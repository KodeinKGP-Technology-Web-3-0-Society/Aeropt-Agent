"""Official exam papers -> verified questions for the corpus.

    .venv/bin/python -m local_ingest.exams                 # every paper in catalog.py
    .venv/bin/python -m local_ingest.exams --years 2025 2026
    .venv/bin/python -m local_ingest.exams --assemble-only  # re-join from the cache, no Cursor calls
    .venv/bin/python -m local_ingest.exams --classify-only  # file unclassified questions under chapters

Writes .local-run/sources/exams/jee_advanced.jsonl (read by corpus.official_exam_items)
and report.json, which lists every dropped question and why. Then `make index`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from local_ingest.exams import assemble, catalog, classify, extract, keys, keys_vision
from local_ingest.extract.fetch import fetch

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".local-run" / "sources" / "exams"


def run_paper(paper: catalog.Paper, *, assemble_only: bool) -> tuple[list, list, dict]:
    work = OUT / "jee_advanced" / paper.slug
    paper_sha = fetch(paper.paper_url, work / "paper.pdf")
    key_sha = fetch(paper.key_fetch_url, work / "key.pdf")
    key_blocks = keys.parse(keys.text_layer(work / "key.pdf"))

    if assemble_only:
        cached = json.loads((work / "physics_pages.json").read_text())
        results, log = cached["results"], cached["log"]
    else:
        results, log = extract.physics_pages(work / "paper.pdf", work)
        (work / "physics_pages.json").write_text(json.dumps({"results": results, "log": log}, ensure_ascii=False))

    questions, sections = extract.stitch(results)
    # Physics answers the key's text layer did not give (scanned, scrambled, or written
    # into the blank) are read from the key's page images — gaps only.
    index, score = assemble.physics_block(key_blocks, questions) if key_blocks else (-1, 0.0)
    keyed = key_blocks[index].entries if index >= 0 and score >= assemble.MIN_IDENTITY else {}
    missing = [n for n in questions if n not in keyed or keyed[n].kind == "unreadable"]
    if missing and (not assemble_only or (work / "key" / "vision").exists()):
        pages = keys_vision.physics_pages_from_text(work / "key.pdf", key_blocks, questions)
        key_blocks = keys_vision.fill(key_blocks, keys_vision.read(work / "key.pdf", work / "key", pages), questions)
        log = log + [f"key gaps read from images: {len(missing)} ({'text-located' if pages else 'thumbnail-located'} pages)"]
    kept, dropped = assemble.join(paper, questions, sections, key_blocks)
    for item in kept:
        item["paper_sha256"], item["key_sha256"] = paper_sha, key_sha
    meta = {
        "paper": paper.slug, "blocks": log, "extracted": len(questions), "kept": len(kept),
        "dropped": len(dropped), "paper_sha256": paper_sha, "key_sha256": key_sha,
    }
    return kept, dropped, meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, nargs="*")
    ap.add_argument("--assemble-only", action="store_true")
    ap.add_argument("--classify-only", action="store_true")
    args = ap.parse_args(argv)
    target = OUT / "jee_advanced.jsonl"
    if args.classify_only:
        print(f"{classify.apply(target, OUT / 'jee_advanced')} questions filed under a chapter")
        return 0

    all_kept, all_dropped, metas = [], [], []
    for paper in catalog.select(set(args.years or [])):
        started = time.time()
        try:
            kept, dropped, meta = run_paper(paper, assemble_only=args.assemble_only)
        except Exception as exc:  # one bad paper must not lose the others
            print(f"{paper.slug}: FAILED — {exc}", file=sys.stderr)
            metas.append({"paper": paper.slug, "error": str(exc)})
            continue
        all_kept += kept
        all_dropped += dropped
        metas.append(meta)
        print(f"{paper.slug}: {meta['kept']} kept, {meta['dropped']} dropped of {meta['extracted']} "
              f"extracted ({time.time() - started:.0f}s) {meta['blocks']}")

    OUT.mkdir(parents=True, exist_ok=True)
    # Papers not re-run this time keep their previous output.
    previous = []
    if target.exists():
        rerun = {m["paper"] for m in metas if "error" not in m}
        previous = [r for r in map(json.loads, target.read_text().splitlines())
                    if f"jee_adv_{r['year']}_p{r['paper']}" not in rerun]
    rows = sorted(previous + all_kept, key=lambda r: r["id"])
    target.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    try:
        filed = classify.apply(target, OUT / "jee_advanced")
        print(f"{filed} questions filed under a chapter")
    except Exception as exc:  # classification only files questions; never lose the run over it
        print(f"chapter classification skipped: {exc}", file=sys.stderr)
    (OUT / "report.json").write_text(json.dumps({"papers": metas, "dropped": all_dropped}, indent=1, ensure_ascii=False))
    print(f"\n{len(rows)} verified questions in {target.relative_to(ROOT)}; "
          f"{len(all_dropped)} dropped this run (reasons in report.json)")
    return 0 if not any("error" in m for m in metas) else 1


if __name__ == "__main__":
    sys.exit(main())
