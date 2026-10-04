"""Questions from the clean (answer-free) question paper, one page image at a time.

Only the question paper is read here, never the key document, and the prompt asks for
transcription, not solving — so an extracted question cannot carry an answer in.

Which pages are Physics: the text layer shows where question numbering restarts at
Q.1 (one block per subject, in an order that changes by year). The first page of
each block is read by the vision model, which names the subject; only the Physics
block's pages are read in full.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from local_ingest.extract import pdf as pdfs
from local_ingest.extract import reader

PROMPT_VERSION = "q1"

PROMPT = """You are transcribing one page of an official JEE Advanced question paper into JSON.

Rules:
- Copy the text exactly. Do not solve, simplify, correct, or add anything. Never include an answer.
- All mathematics in LaTeX inside $...$. Units and numbers exactly as printed.
- Ignore running headers, footers and page numbers.

Return only this JSON object:
{
  "subject": "Physics" | "Chemistry" | "Mathematics",   // the subject of the questions on this page; judge by content if no heading is printed
  "sections": [ {"section": <int>, "question_count": <int>, "answer_type": "single_correct" | "multiple_correct" | "integer" | "numeric" | "matching" | "paragraph"} ],   // only SECTION headers printed on this page
  "continuation": null | {"qno": <int>, "stem_tail": "<text>", "options": {"A": "...", ...} | null, "figure": "<description>" | null},   // text at the top of the page that belongs to a question begun on an earlier page
  "paragraphs": [ {"applies_to": [<qno>, ...], "text": "<paragraph text>"} ],   // shared passages for paragraph-type questions
  "questions": [
    {"qno": <int>, "section": <int or null>, "stem": "<full question text>",
     "options": {"A": "...", "B": "...", "C": "...", "D": "..."} | null,
     "figure": null | "<precise description of the figure: every label, value, direction and arrangement shown>",
     "continues_on_next_page": <bool>}
  ]   // every question that STARTS on this page, including any List-I / List-II tables written out in full
}"""


# Older papers: subjects found from headings or thumbnails, so a boundary page can hold
# two subjects (2017 numbers 1-54 straight through). Each question names its subject.
PROMPT_VERSION_MIXED = "q2"
PROMPT_MIXED = PROMPT.replace(
    '{"qno": <int>, "section": <int or null>,',
    '{"qno": <int>, "subject": "Physics" | "Chemistry" | "Mathematics", "section": <int or null>,',
)

_HEADING = re.compile(r"(?im)^\s*(?:PART\s*[-–]?\s*(?:[IV]+|\d)\s*[:\-–]?\s*)?(PHYSICS|CHEMISTRY|MATHEMATICS)\s*$")


def subject_headings(texts: list[str]) -> dict[str, int]:
    """First page index (0-based) on which each subject's heading line is printed."""
    found: dict[str, int] = {}
    for i, text in enumerate(texts):
        for m in _HEADING.finditer(text):
            found.setdefault(m.group(1).capitalize(), i)
    return found


THUMBS_PROMPT = """These images are the pages of one JEE Advanced question paper, in order (image 1 is page 1).
For every page, say which subject's questions appear on it.
Return only JSON: {"pages": [{"page": 1, "subjects": ["Physics"]}, ...]} — subjects from "Physics", "Chemistry", "Mathematics"; an empty list for cover or instruction-only pages."""


def subjects_by_vision(pdf: Path, work: Path, count: int) -> dict[int, list[str]]:
    """Scanned paper: one call over low-resolution thumbnails of every page."""
    thumbs = [pdfs.render(pdf, p, work / "thumbs", dpi=45) for p in range(1, count + 1)]
    reply = reader.ask(THUMBS_PROMPT, thumbs, cache_dir=work / "vision", version="t1", name="thumbs")
    return {int(r["page"]): [str(s).capitalize() for s in r.get("subjects") or []]
            for r in (reply or {}).get("pages", []) if isinstance(r, dict) and "page" in r}


def page_questions(pdf: Path) -> list[list[int]]:
    """Question numbers found on each page's text layer (empty list: none or no text)."""
    return [[int(n) for n in re.findall(r"(?m)^\s*Q\.?\s*(\d{1,2})\b", page)] for page in pdfs.page_texts(pdf)]


def blocks(pdf: Path) -> list[list[int]]:
    """1-based page numbers of each subject block (numbering restarts at Q.1)."""
    per_page = page_questions(pdf)
    starts = [i for i, qs in enumerate(per_page) if 1 in qs]
    if not starts:
        raise RuntimeError(f"{pdf.name}: no question numbers in the text layer (scanned paper?)")
    result = []
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(per_page)
        # A section-instructions page with no question on it opens the block.
        first = start - 1 if start > 0 and not per_page[start - 1] and k > 0 else start
        if k > 0 and first <= starts[k - 1]:
            first = start
        result.append([p + 1 for p in range(first, end) if p < len(per_page)])
    return result


def read_page(pdf: Path, page: int, work: Path) -> dict[str, Any]:
    return reader.read_pages(pdf, [page], work, prompt=PROMPT, version=PROMPT_VERSION)[0]


def physics_pages(pdf: Path, work: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Read the Physics pages. Returns (page results in order, log lines).

    Three ways to find them, in order: question numbering restarting at Q.1 per subject
    (2018+; one probe call per block — this is how the cached 2023-26 pages were read);
    printed subject headings (2014, 2017: numbered straight through; no model call); and for
    a scanned paper, one call over thumbnails of every page.
    """
    texts = pdfs.page_texts(pdf)
    restarts = sum(1 for qs in page_questions(pdf) if 1 in qs)
    heads = subject_headings(texts)
    if restarts < 2 and "Physics" in heads and len(heads) >= 2:
        start = heads["Physics"]
        after = [i for i in heads.values() if i > start]
        end = min(after) if after else len(texts) - 1  # the next subject's first page may still end Physics
        pages = list(range(start + 1, end + 2))
        log = [f"headings: Physics pages {pages[0]}-{pages[-1]}"]
        return reader.read_pages(pdf, pages, work, prompt=PROMPT_MIXED, version=PROMPT_VERSION_MIXED), log
    if restarts < 2 and not any(re.search(r"(?m)^\s*Q\.?\s*\d", t) for t in texts):
        by_page = subjects_by_vision(pdf, work, len(texts))
        pages = sorted(p for p, subs in by_page.items() if "Physics" in subs)
        if not pages:
            raise RuntimeError(f"{pdf.name}: no Physics pages found in the thumbnails")
        pages = list(range(pages[0], pages[-1] + 1))
        log = [f"thumbnails: Physics pages {pages[0]}-{pages[-1]}"]
        return reader.read_pages(pdf, pages, work, prompt=PROMPT_MIXED, version=PROMPT_VERSION_MIXED), log
    log: list[str] = []
    found = blocks(pdf)
    per_page = page_questions(pdf)
    probes = {}
    with ThreadPoolExecutor(reader.CONCURRENCY) as pool:
        # The first page that has questions on it names the block's subject.
        heads = [next(p for p in pages if per_page[p - 1]) for pages in found]
        for pages, head, result in zip(found, heads, pool.map(lambda p: read_page(pdf, p, work), heads)):
            probes[head] = result
            log.append(f"block pages {pages[0]}-{pages[-1]}: {result.get('subject')}")
        physics = [pages for pages, head in zip(found, heads) if str(probes[head].get("subject", "")).lower() == "physics"]
        if len(physics) != 1:
            raise RuntimeError(f"{pdf.name}: expected one Physics block, found {len(physics)} ({log})")
        pages = physics[0]
        results = list(pool.map(lambda p: probes.get(p) or read_page(pdf, p, work), pages))
    return results, log


def stitch(results: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    """Page results -> ({qno: question}, {section: header}) for one subject block."""
    questions: dict[int, dict[str, Any]] = {}
    sections: dict[int, dict[str, Any]] = {}
    paragraphs: dict[int, str] = {}
    current_section = None
    for page in results:
        for header in page.get("sections") or []:
            if isinstance(header, dict) and header.get("section") is not None:
                sections[int(header["section"])] = header
                current_section = int(header["section"])
        cont = page.get("continuation")
        if isinstance(cont, dict) and cont.get("qno") in questions:
            q = questions[cont["qno"]]
            if cont.get("stem_tail"):
                q["stem"] = (q["stem"] + "\n" + cont["stem_tail"]).strip()
            if isinstance(cont.get("options"), dict):
                q["options"] = {**(q.get("options") or {}), **cont["options"]}
            if cont.get("figure"):
                q["figure"] = "; ".join(x for x in (q.get("figure"), cont["figure"]) if x)
            q["pages"].append(page["_page"])
        for para in page.get("paragraphs") or []:
            for n in para.get("applies_to") or []:
                paragraphs[int(n)] = para.get("text") or ""
        for raw in page.get("questions") or []:
            if raw.get("subject") and str(raw["subject"]).capitalize() != "Physics":
                continue  # a boundary page in a paper numbered straight through
            try:
                n = int(raw["qno"])
            except (KeyError, TypeError, ValueError):
                continue
            section = raw.get("section") if raw.get("section") is not None else current_section
            questions[n] = {
                "qno": n,
                "section": int(section) if section is not None else None,
                "stem": (raw.get("stem") or "").strip(),
                "options": raw.get("options") if isinstance(raw.get("options"), dict) else None,
                "figure": (raw.get("figure") or "").strip() or None,
                "pages": [page["_page"]],
            }
    groups: dict[str, list[int]] = {}
    for n, text in paragraphs.items():
        if n in questions:
            questions[n]["paragraph"] = text
            groups.setdefault(text, []).append(n)
    # A paragraph's figure is drawn once, usually transcribed with its first question;
    # every question on that paragraph needs it.
    for members in groups.values():
        shared = next((questions[n]["figure"] for n in sorted(members) if questions[n].get("figure")), None)
        for n in members:
            questions[n]["figure"] = questions[n].get("figure") or shared
    return questions, sections
