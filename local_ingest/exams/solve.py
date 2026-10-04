"""Worked solutions for verified questions — kept only when they reach the official answer.

JEE publishes answers, never worked solutions, and coaching solutions are their
publishers' copyright. So solutions are written here, under one rule: the model is
NOT shown the official answer (it cannot reason backwards to it), and a solution is
kept only if its final answer matches the official key — the same letters, or a
number inside the key's accepted range. A miss gets one more attempt on a second
model; a second miss leaves the question with its official answer and no worked
solution. An unverified solution is never stored.

    .venv/bin/python -m local_ingest.exams.solve            # every question without a solution
    .venv/bin/python -m local_ingest.exams.solve --limit 5  # a sample
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from local_ingest.exams import keys
from local_ingest.extract import reader

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / ".local-run" / "sources" / "exams" / "jee_advanced.jsonl"
SOLUTIONS = ROOT / ".local-run" / "sources" / "exams" / "jee_advanced_solutions.jsonl"
CACHE = ROOT / ".local-run" / "sources" / "exams" / "solve_cache"
VERSION = "s1"
MODELS = [m for m in os.getenv("SOLVE_MODELS", "auto,grok-4.6").split(",") if m]

_FORMAT = {
    "single_correct": 'exactly one letter, e.g. "B"',
    "matching": 'exactly one letter, e.g. "B"',
    "multiple_correct": 'every correct letter, e.g. "AC"',
    "integer": "a number, e.g. 5",
    "numeric": "a number, e.g. 2.35",
}


def prompt(q: dict[str, Any]) -> str:
    parts = [f"JEE Advanced {q['year']} Paper {q['paper']}, Physics Q{q['qno']} ({q['qtype'].replace('_', ' ')})."]
    if q.get("paragraph"):
        parts.append("Passage:\n" + q["paragraph"])
    parts.append("Question:\n" + q["stem"])
    if q.get("figure"):
        parts.append("The figure shows: " + q["figure"])
    if q.get("options"):
        parts.append("Options:\n" + "\n".join(f"({k}) {v}" for k, v in q["options"].items()))
    parts.append(
        "Solve this as a careful physics teacher writing for a JEE student. Show the reasoning step "
        "by step: principles used, equations in LaTeX ($...$ inline, $$...$$ display), and the "
        "arithmetic. For option questions, say briefly why each option is right or wrong.\n"
        "Return only JSON: {\"solution\": \"<the worked solution in Markdown>\", \"final\": \"<"
        + _FORMAT.get(q["qtype"], "the answer") + ">\"}"
    )
    return "\n\n".join(parts)


def matches(final: str, q: dict[str, Any]) -> bool:
    """Does the model's final answer equal the official key?"""
    entry = keys.normalise(q["answer_raw"])
    candidates = entry.alternatives if entry.kind == "any_of" else [entry]
    compact = re.sub(r"[\s(),.&]", "", (final or "").upper())
    if re.fullmatch(r"[A-D]+", compact):  # "AC", "(A), (C)"
        letters = "".join(sorted(set(compact)))
    else:  # "A and C": standalone letters only, so the D in "AND" does not count
        letters = "".join(sorted(set(re.findall(r"(?<![A-Z])[A-D](?![A-Z])", (final or "").upper()))))
    number = re.search(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?", (final or "").replace(",", ""))
    value = float(number.group()) if number else None
    for e in candidates:
        if e.kind == "letters" and letters and letters == e.letters:
            return True
        if value is None:
            continue
        if e.kind == "integer" and abs(value - e.value) < 1e-9:
            return True
        if e.kind == "range" and e.low - 1e-9 <= value <= e.high + 1e-9:
            return True
    return False


def solve_one(q: dict[str, Any]) -> dict[str, Any]:
    attempts = []
    for model in MODELS:
        try:
            reply = reader.ask(prompt(q), None, cache_dir=CACHE, version=f"{VERSION}:{model}", name=q["id"],
                               model=model)
        except reader.VisionError as exc:
            attempts.append({"model": model, "error": str(exc)[:200]})
            continue
        final = str((reply or {}).get("final", ""))
        solution = str((reply or {}).get("solution", "")).strip()
        ok = bool(solution) and matches(final, q)
        attempts.append({"model": model, "final": final, "matched": ok})
        if ok:
            return {"id": q["id"], "solution": solution, "final": final, "model": model,
                    "verified_against": "official final answer key", "attempts": attempts}
    return {"id": q["id"], "solution": "", "attempts": attempts}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    questions = [json.loads(l) for l in QUESTIONS.read_text().splitlines() if l.strip()]
    done = {}
    if SOLUTIONS.exists():
        done = {r["id"]: r for r in map(json.loads, SOLUTIONS.read_text().splitlines())}
    todo = [q for q in questions if not done.get(q["id"], {}).get("solution")]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} questions to solve with {MODELS}")
    with ThreadPoolExecutor(reader.CONCURRENCY) as pool:
        for result in pool.map(solve_one, todo):
            done[result["id"]] = result
            mark = "ok " if result["solution"] else "-- "
            print(mark + result["id"] + "  " + " ".join(
                f"{a['model']}:{a.get('final', 'ERR')}{'✓' if a.get('matched') else '✗'}" for a in result["attempts"]))
            SOLUTIONS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in done.values()))
    verified = sum(1 for r in done.values() if r.get("solution"))
    print(f"\n{verified} of {len(questions)} questions have a verified worked solution")
    return 0


if __name__ == "__main__":
    sys.exit(main())
