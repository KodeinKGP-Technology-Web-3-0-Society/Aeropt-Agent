"""NCERT Physics chapters -> validated theory, worked examples and exercises.

    .venv/bin/python -m local_ingest.ncert --chapters leph103      # one chapter
    .venv/bin/python -m local_ingest.ncert                         # all 28 (~625 pages)
    .venv/bin/python -m local_ingest.ncert --estimate              # Cursor calls still needed
    .venv/bin/python -m local_ingest.ncert --assemble-only         # re-stitch from the cache

Writes .local-run/sources/ncert/ncert.jsonl (read by corpus.ncert_items) and
report.json. Then `make index`. Design: EXTRACTION.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from local_ingest.extract import pdf as pdfs
from local_ingest.extract import reader
from local_ingest.extract.fetch import fetch
from local_ingest.extract.report import Report, merge_jsonl
from local_ingest.ncert import catalog, chapter

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".local-run" / "sources" / "ncert"


def run_chapter(ch: catalog.Chapter, report: Report, *, assemble_only: bool) -> list[dict]:
    work = OUT / ch.code
    pdf = OUT / "pdf" / f"{ch.code}.pdf"
    sha = fetch(ch.url, pdf)
    pages = list(range(1, len(pdfs.page_texts(pdf)) + 1))
    if assemble_only and reader.pending(pdf, pages, work, prompt=chapter.PROMPT, version=chapter.PROMPT_VERSION):
        raise RuntimeError("not fully extracted yet; run without --assemble-only")
    results = reader.read_pages(pdf, pages, work, prompt=chapter.PROMPT, version=chapter.PROMPT_VERSION)
    texts = {i + 1: t for i, t in enumerate(pdfs.page_texts(pdf))}  # the check against invention
    stitched = chapter.stitch(results, ch.title, texts)
    # A page with a block that is not printed on it was not really read: read it once more
    # (placeholder prompt, so nothing can be copied from the instructions) and check again.
    flagged = sorted({page for page, reason in stitched["rejected"]
                      if "not read from the page" in reason or "not printed on page" in reason})
    if flagged and (not assemble_only or not reader.pending(pdf, flagged, work, prompt=chapter.PROMPT,
                                                              version=chapter.RETRY_VERSION)):
        by_page = {}
        for page in flagged:  # a failed re-read keeps the first reading; block checks still apply
            try:
                by_page[page] = reader.read_pages(pdf, [page], work, prompt=chapter.PROMPT,
                                                  version=chapter.RETRY_VERSION)[0]
            except reader.VisionError as exc:
                report.drop(ch.code, f"page {page}", f"re-read failed ({str(exc)[:80]}); first reading kept, block-checked")
        results = [by_page.get(r["_page"], r) for r in results]
        stitched = chapter.stitch(results, ch.title, texts)
    records = chapter.validate(ch, stitched, report, ch.code, texts)
    for r in records:
        r["sha256"] = sha
        r["read_by"] = f"{reader.MODEL} / prompt {chapter.PROMPT_VERSION}"
    report.documents.append({
        "document": ch.code, "pages": len(pages), "kept": len(records), "reread": len(flagged),
        "sections": len(stitched["sections"]), "examples": len(stitched["examples"]),
        "exercises": len(stitched["exercises"]), "figures": len(stitched["figures"]),
    })
    return records


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chapters", nargs="*")
    ap.add_argument("--assemble-only", action="store_true")
    ap.add_argument("--estimate", action="store_true")
    args = ap.parse_args(argv)
    chosen = catalog.select(set(args.chapters or []))

    if args.estimate:
        total = 0
        for ch in chosen:
            pdf = OUT / "pdf" / f"{ch.code}.pdf"
            fetch(ch.url, pdf)
            pages = list(range(1, len(pdfs.page_texts(pdf)) + 1))
            total += reader.pending(pdf, pages, OUT / ch.code, prompt=chapter.PROMPT, version=chapter.PROMPT_VERSION)
        minutes = total * 45 / reader.CONCURRENCY / 60
        print(f"{total} pages to read ≈ {minutes:.0f} min at {reader.CONCURRENCY} parallel Cursor calls")
        return 0

    report = Report()
    rows: list[dict] = []
    done: set[str] = set()
    for ch in chosen:
        started = time.time()
        try:
            records = run_chapter(ch, report, assemble_only=args.assemble_only)
        except Exception as exc:  # one bad chapter must not lose the others
            print(f"{ch.code}: FAILED — {exc}", file=sys.stderr)
            report.documents.append({"document": ch.code, "error": str(exc)})
            continue
        rows += records
        done.add(ch.code)
        d = report.documents[-1]
        print(f"{ch.code} {ch.title}: {d['kept']} records ({d['sections']} sections, {d['examples']} examples, "
              f"{d['exercises']} exercises, {d['figures']} figures) {time.time() - started:.0f}s")

    merged = merge_jsonl(OUT / "ncert.jsonl", rows, replaced=lambda r: r["chapter_code"] in done)
    report.write(OUT / "report.json")
    print(f"\n{len(merged)} NCERT records in {(OUT / 'ncert.jsonl').relative_to(ROOT)}; "
          f"{len(report.dropped)} dropped this run (reasons in report.json)")
    return 0 if all("error" not in d for d in report.documents) else 1


if __name__ == "__main__":
    sys.exit(main())
