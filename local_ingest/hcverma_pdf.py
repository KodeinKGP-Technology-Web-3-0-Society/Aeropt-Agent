"""H.C. Verma chapters that arrived as PDF pages only (no extracted CSV) -> verified records.

    .venv/bin/python -m local_ingest.hcverma_pdf              # read + verify all chapters below
    .venv/bin/python -m local_ingest.hcverma_pdf --estimate   # Cursor calls still needed
    make index                                                # fold the records in

The pages are read by the same Cursor page reader as NCERT (local_ingest/ncert/chapter.py:
stitch + grounding), with a prompt for this book. Every block is checked against the PDF's
own text layer, so a transcription the model invented is set aside with its reason.

What these PDFs hold: each chapter's theory and its "Worked Out Examples"; they stop where
"Questions for Short Answer" begins (one file also carries the end of the chapter before
it, exercises included). Short-answer and objective lists are not taken: their answers are
not printed here. Pages are assigned to the book's chapter by its printed "CHAPTER nn"
marker, not by folder, and ids carry that number, so a page held by two files is one record.

  theory    a section, kept when >= 85 % of its words are printed on its pages
  example   question + the book's printed solution (solution_status "book")
  exercise  question only (EXERCISES section)
  any row   maths must render; a question that cites a figure must carry its description

Writes .local-run/sources/hcverma/hcverma_pdf.jsonl (read by corpus.hcverma_items beside
hcverma.jsonl) and report_pdf.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from local_ingest.extract import pdf as pdfs
from local_ingest.extract import reader
from local_ingest.extract.report import Report
from local_ingest.ncert import chapter as ncert
from utils.latex_guidelines import find_latex_issues

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".local-run" / "CLASS XII&Xi" / "CLASS XII"
OUT = ROOT / ".local-run" / "sources" / "hcverma"
VERSION = "h1"
RETRY_VERSION = "h2"  # pages whose blocks fail the text-layer check are read once more
MIN_THEORY_GROUNDED = 0.85

FOLDERS = [
    "Wave Optics", "Ray Optics & Optical Instruments", "Electromagnetic Induction", "Alternating Current",
    "Electromagnetic Waves", "Dual Nature of Radiation, Matter & Atomic Physics", "Semiconductor Electronics",
    "Nuclei & Nuclear Physics",
]

# The book's chapter number -> (its title, syllabus chapter, JEE unit).
CHAPTERS: dict[int, tuple[str, str, str]] = {
    17: ("Light Waves", "interference", "Optics"),
    18: ("Geometrical Optics", "geometric_optics_and_image_formation", "Optics"),
    19: ("Optical Instruments", "geometric_optics_and_image_formation", "Optics"),
    20: ("Dispersion and Spectra", "the_nature_of_light", "Optics"),
    38: ("Electromagnetic Induction", "electromagnetic_induction", "Electromagnetic Induction and Alternating Currents"),
    39: ("Alternating Current", "alternating_current_circuits", "Electromagnetic Induction and Alternating Currents"),
    40: ("Electromagnetic Waves", "electromagnetic_waves", "Electromagnetic Waves"),
    42: ("Photoelectric Effect and Wave-Particle Duality", "photons_and_matter_waves", "Dual Nature of Matter and Radiation"),
    43: ("Bohr's Model and Physics of the Atom", "atomic_structure", "Atoms and Nuclei"),
    44: ("X-rays", "atomic_structure", "Atoms and Nuclei"),
    45: ("Semiconductors and Semiconductor Devices", "condensed_matter_physics", "Electronic Devices"),
    46: ("The Nucleus", "nuclear_physics", "Atoms and Nuclei"),
}

PROMPT = """You are transcribing one page of H.C. Verma's "Concepts of Physics" into JSON.

Rules:
- Copy the text exactly, in reading order. Do not summarise, solve, correct, or add anything.
- All mathematics in LaTeX inside $...$ (inline) or $$...$$ (display). Keep equation numbers like (<n>.<m>) after the equation.
- Ignore running headers ("Concepts of Physics", the chapter title), page numbers and footers.
- Under "Worked Out Examples", each numbered problem is an "example": its question, and its printed "Solution" as the solution.
- Under "EXERCISES", each numbered problem is an "exercise".
- Do not transcribe "QUESTIONS FOR SHORT ANSWER", "OBJECTIVE I", "OBJECTIVE II" or "ANSWERS": leave those parts of the page out.

Return only this JSON object:
{
  "blocks": [
    {"kind": "heading", "number": "<section number as printed>", "title": "<section title as printed>"},
    {"kind": "text", "text": "<paragraph(s) of explanation>"},
    {"kind": "continuation", "part": "text" | "example_question" | "example_solution" | "exercise", "text": "<text at the top of the page that continues what the previous page ended with; 'part' says which>"},
    {"kind": "figure", "number": "<figure number exactly as printed>", "caption": "<printed caption, if any>", "description": "<what the drawing shows: every label, value, direction and arrangement>"},
    {"kind": "example", "number": "<example number as printed>", "question": "<the problem>", "solution": "<its printed solution, if on this page>", "continues_on_next_page": <bool>},
    {"kind": "exercise", "number": "<exercise number as printed>", "text": "<exercise question>"}
  ]
}
Use only the block kinds shown. A page may contain any number of blocks."""

_CHAPTER_MARK = re.compile(r"(?m)^\s*CHAPTER\s+(\d{1,2})\s*$")
# "Figure 39.1", "figure (39-W2)", "figure 39-E3"
_FIG_REF = re.compile(r"\bfig(?:ure)?s?\.?\s*\(?\s*(\d{1,2}\s*[.\-–]\s*[WE]?\d{1,2})", re.I)
_FIG_WORDS = re.compile(r"\b(figure|fig\.|shown|diagram)\b", re.I)


def _layer(text: str) -> str:
    """The page's text layer plus the words that justified setting hyphenated across lines
    ("tur-" / "bines"), so a faithful transcription is not marked as unprinted."""
    joined = re.findall(r"([A-Za-z]+)-\s*\n\s*([a-z]+)", text or "")
    return (text or "") + "\n" + " ".join(a + b for a, b in joined)


def _tidy_figures(text: str) -> str:
    """"Figure 38-W6: Figure 38-W6 — …" and "[Figure 18.16: . …" (an empty or repeated caption)."""
    text = re.sub(r"(Figure ([\w.\-]+)): (?:Figure \2)?\s*[—.]\s*", r"\1: ", text or "")
    return text


def _fig_key(ref: str) -> str:
    return re.sub(r"\s+", "", ref).replace("–", "-").upper()


def page_chapters(files: list[Path]) -> dict[tuple[Path, int], int]:
    """(file, page) -> the book chapter it belongs to. Pages before a file's first CHAPTER
    marker end the chapter before it; a file with no marker continues the previous file."""
    owner: dict[tuple[Path, int], int] = {}
    previous: int | None = None
    for pdf in files:
        texts = pdfs.page_texts(pdf)
        marks = [(i + 1, int(m.group(1))) for i, t in enumerate(texts) for m in [_CHAPTER_MARK.search(t)] if m]
        current = (marks[0][1] - 1) if marks and marks[0][0] > 1 else previous
        for page in range(1, len(texts) + 1):
            for at, number in marks:
                if at == page:
                    current = number
            if current is not None:
                owner[(pdf, page)] = current
        previous = current
    return owner


def _read(pdf: Path, pages: list[int], work: Path, report: Report, doc: str) -> list[dict[str, Any]]:
    try:
        return reader.read_pages(pdf, pages, work, prompt=PROMPT, version=VERSION)
    except reader.VisionError:
        results = []
        for page in pages:  # one failed page must not lose the rest
            try:
                results += reader.read_pages(pdf, [page], work, prompt=PROMPT, version=VERSION)
            except reader.VisionError as exc:
                report.drop(doc, f"page {page}", f"could not be read ({str(exc)[:80]})")
        return results


def chapter_records(number: int, parts: list[tuple[Path, list[dict[str, Any]], dict[int, str]]],
                    report: Report, flagged: set[tuple[Path, int]] | None = None) -> list[dict[str, Any]]:
    """Stitch one book chapter's pages (possibly from several files) and keep what passes.
    Pages holding a block that failed the text-layer check are added to `flagged`."""
    title, chapter_id, unit = CHAPTERS[number]
    doc = f"HCV ch {number}"
    results, texts, where, origin = [], {}, {}, {}
    flagged = flagged if flagged is not None else set()
    offset = 0
    for pdf, pages, page_texts in parts:  # renumber so pages from two files never collide
        for r in pages:
            r = {**r, "_page": r["_page"] + offset}
            results.append(r)
            where[r["_page"]] = f"{pdf.parent.name}/{pdf.name} p.{r['_page'] - offset}"
            origin[r["_page"]] = (pdf, r["_page"] - offset)
        texts.update({p + offset: t for p, t in page_texts.items()})
        offset += 1000
    stitched = ncert.stitch(results, title, texts)
    for page, reason in stitched["rejected"]:
        report.drop(doc, where.get(page, page), reason)
        if page in origin and ("not read from the page" in reason or "not printed on page" in reason):
            flagged.add(origin[page])
    figures = {_fig_key(k): _tidy_figures(v) for k, v in stitched["figures"].items()}
    base = {"chapter": f"Ch. {number} {title}", "chapter_id": chapter_id, "unit": unit, "concept_hint": ""}
    records: list[dict[str, Any]] = []

    def text_of(pages: list[int]) -> str:
        return "\n".join(texts.get(p, "") for p in sorted(set(pages)))

    def source(pages: list[int]) -> dict[str, str]:
        first = where.get(min(pages), "") if pages else ""
        return {"page": first.rsplit("p.", 1)[-1], "source_pdf": first.rsplit(" p.", 1)[0]}

    for i, section in enumerate(stitched["sections"]):
        body = _tidy_figures("\n\n".join(p for p in section["parts"] if p.strip()))
        ref = f"section {section['number'] or section['title']}"
        prose = re.sub(r"\[Figure[^\]]*\]", " ", body)  # descriptions are the reader's words, not the book's
        share = ncert.grounded(prose, text_of(section["pages"]))
        if len(body) < 80:
            report.drop(doc, ref, "too little text to teach from")
        elif share < MIN_THEORY_GROUNDED:
            report.drop(doc, ref, f"only {share:.0%} of its words are printed on its pages")
            flagged.update(origin[p] for p in section["pages"] if p in origin)
        elif find_latex_issues(body):
            report.drop(doc, ref, f"maths would not render: {find_latex_issues(body)[:2]}")
        else:
            heading = f"{section['number']} {section['title']}".strip()
            records.append({**base, **source(section["pages"]), "id": f"HCV-C{number}-TH-{i:03d}", "kind": "theory",
                            "question": f"{heading}\n\n{body}" if section["number"] else body, "figure": "",
                            "solution": "", "solution_status": "verbatim"})

    for kind, items in (("example", stitched["examples"]), ("exercise", stitched["exercises"])):
        for item in items:
            ref = f"{kind} {item['number']}"
            question, solution = item["question"].strip(), (item.get("solution") or "").strip()
            question = re.sub(r"^\s*" + re.escape(item["number"].strip(".")) + r"\.\s+", "", question)  # "8. A square loop…"
            refs = sorted({_fig_key(r) for r in _FIG_REF.findall(question)})
            found = [figures[r] for r in refs if r in figures]
            if len(question) < 25:
                report.drop(doc, ref, "question text too short to be whole")
            elif kind == "example" and len(solution) < 20:
                report.drop(doc, ref, "worked example without its solution")
            elif len(found) < len(refs) or (_FIG_WORDS.search(question) and not found):
                report.drop(doc, ref, "refers to a figure that was not described")
            elif find_latex_issues(question) or find_latex_issues(solution):
                report.drop(doc, ref, f"maths would not render: {(find_latex_issues(question) + find_latex_issues(solution))[:2]}")
            else:
                records.append({**base, **source(item["pages"]),
                                "id": f"HCV-C{number}-{'SE' if kind == 'example' else 'EX'}-{item['number'].strip('.').zfill(3)}",
                                "kind": kind, "question": question, "figure": "\n".join(found),
                                "solution": solution if kind == "example" else "",
                                "solution_status": "book" if kind == "example" else ""})
    report.documents.append({"document": doc, "pages": len(results), "kept": len(records),
                             "sections": len(stitched["sections"]), "examples": len(stitched["examples"]),
                             "exercises": len(stitched["exercises"]), "figures": len(figures)})
    return records


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--estimate", action="store_true")
    args = ap.parse_args(argv)

    report = Report()
    by_chapter: dict[int, list[tuple[Path, list[dict[str, Any]], dict[int, str]]]] = {}
    total = 0
    for folder in FOLDERS:
        files = sorted((SOURCE / folder).glob("pdf*.pdf"))
        owner = page_chapters(files)
        for pdf in files:
            work = OUT / "pdf_reads" / re.sub(r"\W+", "_", folder).strip("_").lower() / pdf.stem
            pages = sorted(p for (f, p) in owner if f == pdf)
            unmapped = [p for (f, p), n in owner.items() if f == pdf and n not in CHAPTERS]
            pages = [p for p in pages if p not in unmapped]
            if args.estimate:
                total += reader.pending(pdf, pages, work, prompt=PROMPT, version=VERSION)
                continue
            texts = {i + 1: _layer(t) for i, t in enumerate(pdfs.page_texts(pdf))}
            results = _read(pdf, pages, work, report, f"{folder}/{pdf.name}")
            for number in sorted({owner[(pdf, r['_page'])] for r in results}):
                mine = [r for r in results if owner[(pdf, r["_page"])] == number]
                by_chapter.setdefault(number, []).append((pdf, mine, texts))
            print(f"{folder}/{pdf.name}: {len(results)} pages read", flush=True)
    if args.estimate:
        print(f"{total} pages to read ≈ {total * 45 / reader.CONCURRENCY / 60:.0f} min at {reader.CONCURRENCY} parallel calls")
        return 0

    # Pages with a block the text layer does not back are read once more; per chapter, the
    # reading that yields more checked records is kept (both are held to the same checks).
    flagged: set[tuple[Path, int]] = set()
    for number in sorted(by_chapter):
        chapter_records(number, by_chapter[number], Report(), flagged)
    reread: dict[tuple[Path, int], dict[str, Any]] = {}
    for pdf in sorted({f for f, _ in flagged}):
        pages = sorted(p for f, p in flagged if f == pdf)
        work = OUT / "pdf_reads" / re.sub(r"\W+", "_", pdf.parent.name).strip("_").lower() / pdf.stem
        for page in pages:
            try:
                reread[(pdf, page)] = reader.read_pages(pdf, [page], work, prompt=PROMPT, version=RETRY_VERSION)[0]
            except reader.VisionError as exc:
                report.drop(f"{pdf.parent.name}/{pdf.name}", f"page {page}", f"re-read failed ({str(exc)[:80]})")
    print(f"{len(flagged)} pages re-read once ({len(reread)} succeeded)", flush=True)

    rows: dict[str, dict[str, Any]] = {}
    for number in sorted(by_chapter):
        first = by_chapter[number]
        second = [(pdf, [reread.get((pdf, r["_page"]), r) for r in mine], texts) for pdf, mine, texts in first]
        trial_a, trial_b = Report(), Report()
        a, b = chapter_records(number, first, trial_a), chapter_records(number, second, trial_b)
        chosen, trial = (b, trial_b) if len(b) > len(a) else (a, trial_a)
        report.documents += trial.documents
        report.dropped += trial.dropped
        for record in chosen:
            rows.setdefault(record["id"], record)  # a page held by two files is read twice; keep one
        d = report.documents[-1]
        print(f"ch {number} {CHAPTERS[number][0]}: {d['kept']} kept ({d['sections']} sections, "
              f"{d['examples']} examples, {d['exercises']} exercises, {d['figures']} figures)")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "hcverma_pdf.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows.values()))
    report.write(OUT / "report_pdf.json")
    print(f"\n{len(rows)} records -> {(OUT / 'hcverma_pdf.jsonl').relative_to(ROOT)}; "
          f"{len(report.dropped)} dropped (reasons in report_pdf.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
