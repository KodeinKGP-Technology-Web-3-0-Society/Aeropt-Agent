"""Live progress of the extraction runs, read from the on-disk caches.

    .venv/bin/python -m local_ingest.extract.status
    watch -n 30 make extract-status        # refresh every 30 s
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMS = ROOT / ".local-run" / "sources" / "exams"
NCERT = ROOT / ".local-run" / "sources" / "ncert"


def _lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def _pages(folder: Path) -> int:
    return sum(1 for f in (folder / "vision").glob("page-*.json")) if (folder / "vision").exists() else 0


def main() -> None:
    running = subprocess.run(["pgrep", "-af", "python -m local_ingest"], capture_output=True, text=True).stdout
    jobs = sorted({l.split(" -m ", 1)[1].split(" 2>")[0] for l in running.splitlines()
                   if len(l.split()) > 1 and l.split()[1].endswith("python") and "extract.status" not in l})
    workers = subprocess.run(["pgrep", "-fc", "vision_once.mjs"], capture_output=True, text=True).stdout.strip() or "0"
    print(f"running: {', '.join(jobs) or 'nothing'}   |   Cursor calls in flight: {workers}\n")

    questions = _lines(EXAMS / "jee_advanced.jsonl")
    solutions = {r["id"]: r for r in _lines(EXAMS / "jee_advanced_solutions.jsonl")}
    by_year: dict[int, int] = {}
    for q in questions:
        by_year[q["year"]] = by_year.get(q["year"], 0) + 1
    solved = sum(1 for q in questions if solutions.get(q["id"], {}).get("solution"))
    print(f"JEE Advanced: {len(questions)} verified questions "
          f"({', '.join(f'{y}: {n}' for y, n in sorted(by_year.items()))})")
    print(f"              {solved} with a verified worked solution, "
          f"{sum(1 for q in questions if q['id'] in solutions and not solutions[q['id']].get('solution'))} failed verification\n")
    print("  pages read per paper (questions + key images):")
    for folder in sorted((EXAMS / "jee_advanced").glob("jee_adv_*")):
        print(f"    {folder.name}: {_pages(folder)}" + (f" + key {_pages(folder / 'key')}" if (folder / "key").exists() else ""))

    records = _lines(NCERT / "ncert.jsonl")
    done = {r["chapter_code"] for r in records}
    print(f"\nNCERT: {len(records)} records from {len(done)} of 28 chapters")
    total = read = 0
    for pdf in sorted((NCERT / "pdf").glob("*.pdf")):
        pages = int(subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout.split("Pages:")[1].split()[0])
        got = _pages(NCERT / pdf.stem)
        total, read = total + pages, read + min(got, pages)
        if 0 < got < pages:
            print(f"    {pdf.stem}: {got}/{pages} pages (in progress)")
    print(f"  pages read: {read} of {total}")


if __name__ == "__main__":
    main()
