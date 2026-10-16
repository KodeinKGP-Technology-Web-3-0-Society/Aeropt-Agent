"""One NCERT chapter -> theory sections, worked examples and exercises.

Every page is read as an image: NCERT's text layer is fine for prose but loses every
symbol-font glyph (ρ prints as "r", Ω as "W", ∝ as "µ"), and almost every page has one.
The page prompt returns blocks in reading order; `stitch` joins them across pages;
`validate` drops what a student should not be shown, with the reason.
"""

from __future__ import annotations

import re
from typing import Any

from utils.latex_guidelines import find_latex_issues

# n1's prompt showed sample values ("3.4 OHM'S LAW", "Example 3.1"), and on some pages the
# model copied them instead of reading the page. The prompt now shows placeholders; the n1
# cache is kept, and every block is checked against the page's own text layer (`grounded`).
PROMPT_VERSION = "n1"
RETRY_VERSION = "n2"  # pages that fail the text-layer check are re-read once with the placeholder prompt
CHEM_PROMPT_VERSION = "nc1"
CHEM_RETRY_VERSION = "nc2"


def get_prompt(subject: str = "Physics") -> str:
    subj = "Chemistry" if subject.lower() == "chemistry" else "Physics"
    if subj == "Chemistry":
        formula_desc = (
            "All mathematics, chemical formulas and reaction equations in LaTeX inside $...$ (inline) or $$...$$ (display) "
            r"(e.g. $\text{H}_2\text{O}$, $\text{CH}_4$, $\Delta H$, chemical equations). Keep equation/reaction numbers like (3.3) after the equation."
        )
        example_desc = (
            '{"kind": "example", "number": "<example or problem number as printed, e.g. 1.1>", "question": "<the example/problem question>", "solution": "<its printed solution, if on this page>", "continues_on_next_page": <bool>}'
        )
    else:
        formula_desc = (
            "All mathematics in LaTeX inside $...$ (inline) or $$...$$ (display). Keep equation numbers like (3.3) after the equation."
        )
        example_desc = (
            '{"kind": "example", "number": "<example number as printed>", "question": "<the example\'s question>", "solution": "<its printed solution, if on this page>", "continues_on_next_page": <bool>}'
        )

    return f"""You are transcribing one page of an NCERT {subj} textbook into JSON.

Rules:
- Copy the text exactly, in reading order. Do not summarise, solve, correct, or add anything.
- {formula_desc}
- Ignore running headers, page numbers and the "Reprint" footer.
- Sidebars and boxes: give their text as a "box" block; set "biography": true for a scientist's biography.

Return only this JSON object:
{{
  "blocks": [
    {{"kind": "heading", "number": "<section number as printed>", "title": "<section title as printed>"}},
    {{"kind": "text", "text": "<paragraph(s) of explanation>"}},
    {{"kind": "continuation", "part": "text" | "example_question" | "example_solution" | "exercise", "text": "<text at the top of the page that continues what the previous page ended with; 'part' says which>"}},
    {{"kind": "figure", "number": "<figure number as printed>", "caption": "<printed caption>", "description": "<what the drawing shows: every label, value, direction and arrangement>"}},
    {example_desc},
    {{"kind": "exercise", "number": "<exercise number as printed>", "text": "<exercise question>"}},
    {{"kind": "box", "text": "<sidebar text>", "biography": <bool>}},
    {{"kind": "summary", "text": "<SUMMARY section text>"}},
    {{"kind": "points_to_ponder", "text": "<POINTS TO PONDER text>"}}
  ]
}}
Use only the block kinds shown. A page may contain any number of blocks."""


PROMPT = get_prompt("Physics")


_FIG_REF = re.compile(r"\bFig(?:ure)?s?\.?\s*(\d+\.\d+)", re.I)
MIN_GROUNDED = 0.6  # share of a block's words that must be printed on its pages


def _words(text: str) -> list[str]:
    text = re.sub(r"\$[^$]*\$|\\[a-zA-Z]+", " ", text or "")  # maths is garbled in the text layer
    return [w for w in re.findall(r"[a-z]{4,}", text.lower())]


def grounded(text: str, page_text: str) -> float:
    """Share of the block's words that the page's own text layer also has. The text layer
    loses symbols, not words, so a transcription the model invented scores low."""
    words = _words(text)
    if not words:
        return 1.0
    have = set(re.findall(r"[a-z]{4,}", (page_text or "").lower()))
    return sum(w in have for w in words) / len(words)


def _printed(number: str, page_text: str) -> bool:
    return bool(number) and re.search(r"(?<![\d.])" + re.escape(number) + r"(?![\d])", page_text or "") is not None


def stitch(pages: list[dict[str, Any]], chapter_title: str, page_texts: dict[int, str] | None = None) -> dict[str, Any]:
    """Page results -> {"sections": [...], "examples": [...], "exercises": [...], "figures": {...}}.

    With `page_texts` (page number -> text layer), a heading, example or exercise whose
    number or title is not printed on its page is set aside in "rejected" — that is what a
    copied prompt example looks like — and its text is not attached to anything."""
    rejected: list[tuple[int, str]] = []

    def checkable(n: int) -> bool:
        # A page whose text layer has almost no words (keph107 is blank) cannot be checked.
        return page_texts is not None and len(re.findall(r"[a-z]{4,}", (page_texts.get(n) or "").lower())) >= 30

    def on_page(n: int, number: str = "", title: str = "") -> bool:
        if not checkable(n):
            return True
        text = page_texts[n]
        if number and not _printed(number, text):
            return False
        return not title or grounded(title, text) >= 0.8

    def invented(n: int, text: str) -> bool:
        """A block whose words are mostly not printed on its page was not read from it."""
        return checkable(n) and len(_words(text)) >= 8 and grounded(text, page_texts[n]) < MIN_GROUNDED
    sections: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    exercises: list[dict[str, Any]] = []
    figures: dict[str, str] = {}
    current = {"number": "", "title": chapter_title, "parts": [], "pages": []}
    sections.append(current)
    last: dict[str, Any] | None = None  # the record a "continuation" block extends

    def open_section(number: str, title: str, page: int) -> dict[str, Any]:
        section = {"number": number, "title": title, "parts": [], "pages": [page]}
        sections.append(section)
        return section

    for page in pages:
        n = page["_page"]
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("kind")
            text = str(block.get("text") or "").strip()
            body = " ".join(str(block.get(k) or "") for k in ("text", "question", "solution", "title"))
            if kind != "figure" and invented(n, body):
                rejected.append((n, f"{kind} block on page {n} is mostly not printed there "
                                    f"({grounded(body, page_texts[n]):.0%} of its words) — not read from the page"))
                continue
            if kind == "heading":
                title = str(block.get("title") or "").strip()
                if title and not on_page(n, str(block.get("number") or ""), title):
                    rejected.append((n, f"heading {block.get('number')} {title!r} is not printed on page {n}"))
                    continue
                if title:
                    current = open_section(str(block.get("number") or ""), title, n)
                    last = current
            elif kind == "text":
                current["parts"].append(text)
                current["pages"].append(n)
                last = current
            elif kind == "continuation":
                if last is None:
                    continue
                part = block.get("part")
                if "solution" in last and last.get("open"):
                    # The reader says which part runs on; without that, a solution that
                    # has started keeps going, and otherwise it is still the question.
                    if part == "example_solution" or (part != "example_question" and last["solution"]):
                        key = "solution"
                    else:
                        key = "question"
                    last[key] = (last[key] + "\n" + text).strip()
                    last["pages"].append(n)
                elif "question" in last:
                    last["question"] = (last["question"] + "\n" + text).strip()
                else:  # a section running onto this page: record the page it is printed on
                    last["parts"].append(text)
                    last["pages"].append(n)
            elif kind == "figure":
                number = str(block.get("number") or "").strip()
                caption = str(block.get("caption") or "").strip()
                description = str(block.get("description") or "").strip()
                if number and (caption or description):
                    figures[number] = f"Figure {number}: {caption} — {description}".strip(" —")
                current["parts"].append(f"[Figure {number}: {caption}. {description}]")
            elif kind == "example":
                number = str(block.get("number") or "").strip()
                if not on_page(n, number):
                    rejected.append((n, f"Example {number} is not printed on page {n}"))
                    last = None
                    continue
                prior = next((e for e in examples if e["number"] == number and e.get("open")), None)
                if prior:  # the same example restarting on the next page
                    prior["solution"] = (prior["solution"] + "\n" + str(block.get("solution") or "")).strip()
                    prior["open"] = bool(block.get("continues_on_next_page"))
                    prior["pages"].append(n)
                    last = prior
                    continue
                last = {
                    "number": number,
                    "question": str(block.get("question") or "").strip(),
                    "solution": str(block.get("solution") or "").strip(),
                    "open": bool(block.get("continues_on_next_page")),
                    "section": current["title"],
                    "pages": [n],
                }
                examples.append(last)
            elif kind == "exercise":
                if not on_page(n, str(block.get("number") or "").strip()):
                    rejected.append((n, f"Exercise {block.get('number')} is not printed on page {n}"))
                    last = None
                    continue
                last = {"number": str(block.get("number") or "").strip(), "question": text, "pages": [n]}
                exercises.append(last)
            elif kind in {"summary", "points_to_ponder"}:
                title = "Summary" if kind == "summary" else "Points to ponder"
                if current["title"] != title:
                    current = open_section("", title, n)
                current["parts"].append(text)
                last = current
            elif kind == "box" and not block.get("biography") and text:
                current["parts"].append(text)
                last = current
    for e in examples:
        e.pop("open", None)
    return {"sections": [s for s in sections if any(p.strip() for p in s["parts"])],
            "examples": examples, "exercises": exercises, "figures": figures, "rejected": rejected}


def _figures_for(text: str, figures: dict[str, str]) -> tuple[list[str], list[str]]:
    refs = sorted(set(_FIG_REF.findall(text)))
    found = [figures[r] for r in refs if r in figures]
    missing = [r for r in refs if r not in figures]
    return found, missing


def validate(chapter, stitched: dict[str, Any], report, doc: str,
             page_texts: dict[int, str] | None = None) -> list[dict[str, Any]]:
    """Records that pass every check; the rest go to the report with their reason."""
    records: list[dict[str, Any]] = []
    figures = stitched["figures"]
    for page, reason in stitched.get("rejected", []):
        report.drop(doc, f"page {page}", reason)

    base = {
        "chapter_code": chapter.code,
        "chapter": chapter.title,
        "chapter_id": chapter.chapter_id,
        "unit": chapter.unit,
        "book": chapter.book,
        "source_url": chapter.url,
        "subject": getattr(chapter, "subject", "Physics"),
    }

    for i, section in enumerate(stitched["sections"]):
        text = "\n\n".join(p for p in section["parts"] if p.strip())
        if len(text) < 80:
            report.drop(doc, f"section {section['number'] or section['title']}", "too little text to teach from")
            continue
        issues = find_latex_issues(text)
        if issues:
            report.drop(doc, f"section {section['number'] or section['title']}", f"maths would not render: {issues[:2]}")
            continue
        records.append({**base, "id": f"ncert_{chapter.code}_s{i:02d}", "kind": "theory",
                        "section": f"{section['number']} {section['title']}".strip(), "text": text,
                        "pages": sorted(set(section["pages"]))})

    for kind, items in (("example", stitched["examples"]), ("exercise", stitched["exercises"])):
        for item in items:
            ref = f"{kind} {item['number']}"
            question = item["question"]
            solution = item.get("solution", "")
            if len(question) < 25:
                report.drop(doc, ref, "question text too short to be whole")
                continue
            if kind == "example" and len(solution) < 20:
                report.drop(doc, ref, "worked example without its solution")
                continue
            found, missing = _figures_for(question, figures)
            if missing:
                report.drop(doc, ref, f"refers to Fig. {', '.join(missing)}, which was not described")
                continue
            body = question + ("\n\n" + "\n".join(f"[{f}]" for f in found) if found else "")
            issues = find_latex_issues(body) + find_latex_issues(solution)
            if issues:
                report.drop(doc, ref, f"maths would not render: {issues[:2]}")
                continue
            records.append({**base, "id": f"ncert_{chapter.code}_{kind[:2]}{item['number'].replace('.', '_')}",
                            "kind": kind, "number": item["number"], "section": item.get("section", ""),
                            "question": body, "solution": solution, "pages": sorted(set(item["pages"]))})
    return records
