"""Answer keys the text layer cannot give: scanned (2015, 2016), font-scrambled (2022), or
printed into the blank (2023's numeric answers).

Still separate from question extraction: this reads only the key document, and the prompt
asks for the printed answer boxes and the first words of each question above them —
transcription, never solving. Those first words feed the same identity check as text
keys, so a vision-read answer is joined only to the question it was printed under.

It fills gaps; it never overrides an answer the text layer gave.
"""

from __future__ import annotations

from pathlib import Path

from local_ingest.exams import assemble, extract, keys
from local_ingest.extract import pdf as pdfs
from local_ingest.extract import reader

VERSION = "k1"
PROMPT = """This is one page of an official JEE Advanced answer-key document: the question paper with the official answer printed with each question (e.g. "Ans for Q.1: (B)", "Answer: A, C", or a number written into the blank).

Transcribe only what is printed. Do not solve anything and do not guess an answer that is not printed.
Return only JSON: {"answers": [{"qno": <int>, "answer": "<the printed answer exactly, e.g. (A), (C) or 2.50 to 2.60>", "question_start": "<the first 20 or so words of that question as printed>"}]}
One entry per question on this page that has a printed answer; [] if none."""

# 2022 prints a value and the accepted range ("2.33  Range (2.30 - 2.35)"); k1 kept the value,
# so a student's 2.32 would be marked wrong. Pages whose k1 answers are single numbers are read
# once more asking for the range; it replaces the value only if it contains it.
RANGE_VERSION = "k1r"
RANGE_PROMPT = PROMPT + """
If a question shows both a single value and an accepted range (e.g. "2.33  Range (2.30 - 2.35)"), give the range as the answer, written "2.30 to 2.35"."""


def _point(entry: keys.KeyEntry) -> float | None:
    if entry.kind == "integer" or (entry.kind == "range" and entry.low == entry.high):
        return entry.value if entry.kind == "integer" else entry.low
    return None


def physics_pages_from_text(key_pdf: Path, key_blocks: list[keys.KeyBlock], questions: dict) -> list[int]:
    """The key's Physics pages, from its own text layer: the numbering-restart block whose
    question text matches ours. Thumbnails guess subject order from convention (2023 is
    printed Maths-Physics-Chemistry and was read as Physics-first), so text wins."""
    try:
        page_blocks = extract.blocks(key_pdf)
    except RuntimeError:
        return []
    index, score = assemble.physics_block(key_blocks, questions) if key_blocks else (-1, 0.0)
    if index < 0 or score < assemble.MIN_IDENTITY or len(page_blocks) != len(key_blocks):
        return []
    return page_blocks[index]


def read(key_pdf: Path, work: Path, pages: list[int] | None = None) -> dict[int, list[keys.KeyEntry]]:
    """Every answer printed on the key's Physics-range pages, as candidates per question
    number. Numbers repeat per subject (2023: Q.12 is in Maths and in Physics) and a
    thumbnail can mislabel a page, so the caller picks the candidate whose printed
    question matches its own stem."""
    if not pages:
        texts = pdfs.page_texts(key_pdf)
        by_page = extract.subjects_by_vision(key_pdf, work, len(texts))
        pages = sorted(p for p, subjects in by_page.items() if "Physics" in subjects)
        # A thumbnail misses the small "PART I: PHYSICS" banner on a section's first page
        # (2015 P2 page 2 held Q.1-3). An unlabelled page just before is read too; its
        # answers still have to match our stems to be used.
        if pages and pages[0] > 1 and not by_page.get(pages[0] - 1):
            pages.insert(0, pages[0] - 1)
    candidates: dict[int, list[keys.KeyEntry]] = {}
    if not pages:
        return candidates
    results = reader.read_pages(key_pdf, list(range(pages[0], pages[-1] + 1)), work, prompt=PROMPT, version=VERSION)
    numeric = [r["_page"] for r in results
               if any(_point(keys.normalise(str(a.get("answer") or ""))) is not None for a in r.get("answers") or [])]
    ranges: dict[tuple[int, int], keys.KeyEntry] = {}
    try:
        for r in reader.read_pages(key_pdf, numeric, work, prompt=RANGE_PROMPT, version=RANGE_VERSION) if numeric else []:
            for a in r.get("answers") or []:
                e = keys.normalise(str(a.get("answer") or ""))
                if e.kind == "range" and e.low < e.high and str(a.get("qno", "")).isdigit():
                    ranges[(r["_page"], int(a["qno"]))] = e
    except reader.VisionError:
        pass  # the printed values stand
    for page in results:
        for row in page.get("answers") or []:
            try:
                n = int(row["qno"])
            except (KeyError, TypeError, ValueError):
                continue
            entry = keys.normalise(str(row.get("answer") or ""))
            wider = ranges.get((page["_page"], n))
            value = _point(entry)
            if wider is not None and value is not None and wider.low <= value <= wider.high:
                entry = wider
            entry.qno = n
            entry.context = str(row.get("question_start") or "")
            if entry.kind != "unreadable":
                candidates.setdefault(n, []).append(entry)
    return candidates


def fill(blocks: list[keys.KeyBlock], candidates: dict[int, list[keys.KeyEntry]], questions: dict) -> list[keys.KeyBlock]:
    """Add vision-read answers for Physics questions the text-layer key left unanswered —
    for each, only the candidate whose printed question matches the extracted stem."""
    chosen = keys.KeyBlock(subject="Physics")
    for n, q in questions.items():
        scored = [(keys.similarity(e.context, q["stem"]), e) for e in candidates.get(n, [])]
        scored = [(s, e) for s, e in scored if s >= assemble.MIN_IDENTITY]
        if scored:
            chosen.entries[n] = max(scored, key=lambda t: t[0])[1]
            chosen.order.append(n)
    if not chosen.entries:
        return blocks
    index, score = assemble.physics_block(blocks, questions) if blocks else (-1, 0.0)
    if index < 0 or score < assemble.MIN_IDENTITY:
        return blocks + [chosen]  # no usable text key: the vision answers stand alone
    target = blocks[index]
    for n, entry in chosen.entries.items():
        if n not in target.entries or target.entries[n].kind == "unreadable":
            target.entries[n] = entry
            if n not in target.order:
                target.order.append(n)
    return blocks
