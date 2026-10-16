"""The question architect: an LLM that lays a question out for the practice pane.

    .venv/bin/python -m local_ingest.architect            # questions the rules could not structure
    .venv/bin/python -m local_ingest.architect --limit 20
    make push                                             # picks up the cached results

local_ingest/structure.py structures almost every question by rule (the source's own fields, or
a maths-safe split). What is left — a stem with options run together, a figure description
tangled with the options, broken LaTeX — goes to a model, once, offline. It may re-arrange and
repair formatting only: its output must pass structure.faithful() (every number kept, no words
added or lost beyond formatting, all maths renders) or it is discarded and the question stays
as plain text. Results are cached by content_id + text hash, so a question is never sent twice
and a changed question is sent again.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from local_ingest import structure
from local_ingest.extract import reader
from retrieval import store

VERSION = "a1"
CACHE = structure.ARCHITECT.parent / "cache"

PROMPT = """You lay out a physics exam question for a student's screen. You do not change it.

Rules:
- Keep every word, number, unit and symbol of the question. Do not solve it, shorten it, or add anything.
- Separate: the question itself ("stem"); any figure description ("figure", text that describes a drawing); the answer options if it is multiple choice ("options", labelled A-D in order); the sub-questions if it has parts (a), (b), … ("parts").
- Write all mathematics as LaTeX inside $...$. Fix broken LaTeX only so it renders; do not change what it says.
- If it has no options, "options" is null. If it has no parts, "parts" is null.

Question:
<<<
{text}
>>>

Return only JSON: {{"stem": "...", "figure": "...", "options": [{{"label": "A", "text": "..."}}, ...] or null, "parts": [{{"label": "a", "text": "..."}}, ...] or null}}"""


def candidates(limit: int | None) -> list[dict]:
    rows = [r for r in store.query("select content_id, core_text, structured::text as structured from public.content"
                                   " where is_question and structured is not null order by content_id")
            if json.loads(r["structured"]).get("built_by") == "plain"]
    return rows[:limit] if limit else rows


def architect(row: dict) -> dict:
    text = row["core_text"]
    try:
        reply = reader.ask(PROMPT.format(text=text), None, cache_dir=CACHE, version=VERSION, name=row["content_id"])
    except reader.VisionError as exc:
        return {"content_id": row["content_id"], "error": str(exc)[:200]}
    built = {"stem": str(reply.get("stem") or "").strip(), "figure": str(reply.get("figure") or "").strip(),
             "options": reply.get("options") or None, "parts": reply.get("parts") or None}
    problem = structure.faithful(text, built)
    out = {"content_id": row["content_id"], "text_hash": structure.text_hash(text)}
    if problem:
        out["rejected"] = problem
    else:
        out["structured"] = built
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    rows = candidates(args.limit)
    print(f"{len(rows)} questions for the architect ({reader.CONCURRENCY} parallel)")
    existing = {}
    if structure.ARCHITECT.exists():
        existing = {r["content_id"]: r for r in map(json.loads, structure.ARCHITECT.read_text().splitlines()) if r}
    with ThreadPoolExecutor(reader.CONCURRENCY) as pool:
        for result in pool.map(architect, rows):
            existing[result["content_id"]] = result
            print(("ok   " if "structured" in result else "kept plain  ") + result["content_id"]
                  + (f"  ({result.get('rejected') or result.get('error')})" if "structured" not in result else ""))
    structure.ARCHITECT.parent.mkdir(parents=True, exist_ok=True)
    structure.ARCHITECT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in existing.values()))
    ok = sum(1 for r in existing.values() if "structured" in r)
    print(f"\n{ok} structured by the architect and validated; the rest stay plain. Run `make push`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
