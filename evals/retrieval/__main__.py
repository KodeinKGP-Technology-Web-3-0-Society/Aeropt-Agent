"""Retrieval precision: does the tutor get material from the right chapter? No LLM calls.

    .venv/bin/python -m evals.retrieval              # summary + every miss
    .venv/bin/python -m evals.retrieval --json out.json

The correctness runs solve with retrieval off, so they cannot show what off-topic
material does to an answer. This measures the material itself, for the same problems:

  subject      the resolved concept is in the problem's subject
  chapter      the resolved concept is in one of the labelled chapters
  chunk prec.  share of returned chunks from a labelled chapter
  false ok     confidence "ok" although the concept is in the wrong subject — the case
               where the tutor and the Answer Checker are told to trust wrong material

Read-only: retrieval runs with mark=False, so nothing is written to the novelty ledger.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent


def run() -> dict:
    from retrieval import service, store

    labels = yaml.safe_load((HERE / "labels.yaml").read_text())["labels"]
    problems = {p["id"]: p for p in yaml.safe_load(
        (ROOT / "evals" / "correctness" / "problems.yaml").read_text())["problems"]}
    concepts = {c["concept_id"]: c for c in store.all_concepts()}

    rows = []
    for pid, want in labels.items():
        pack = service.retrieve(problems[pid]["prompt"], forced=True, mark=False)
        cid = (pack.get("concept") or {}).get("concept_id") or ""
        got = concepts.get(cid, {})
        chunks = pack.get("chunks") or []
        # A chunk carries its primary concept, not its chapter id.
        chunk_chapters = [concepts.get(c.get("primary_concept_id") or "", {}).get("chapter_id") for c in chunks]
        in_ch = [ch in want["chapters"] for ch in chunk_chapters]
        rows.append({
            "id": pid,
            "concept": cid,
            "concept_subject": got.get("subject"),
            "concept_chapter": got.get("chapter_id"),
            "confidence": pack.get("confidence"),
            "subject_ok": got.get("subject") == want["subject"],
            "chapter_ok": got.get("chapter_id") in want["chapters"],
            "chunks": len(chunks),
            "chunk_precision": (sum(in_ch) / len(in_ch)) if in_ch else None,
            "chunk_chapters": chunk_chapters,
        })

    n = len(rows)
    with_chunks = [r for r in rows if r["chunk_precision"] is not None]
    summary = {
        "n": n,
        "subject_acc": sum(r["subject_ok"] for r in rows) / n,
        "chapter_acc": sum(r["chapter_ok"] for r in rows) / n,
        "chunk_precision": (sum(r["chunk_precision"] for r in with_chunks) / len(with_chunks)) if with_chunks else 0.0,
        "false_ok": sum(1 for r in rows if not r["subject_ok"] and r["confidence"] == "ok"),
        "no_material": n - len(with_chunks),
    }
    return {"summary": summary, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    args = ap.parse_args()
    result = run()
    s = result["summary"]
    print(f"retrieval eval: {s['n']} problems | subject {s['subject_acc']:.0%} | chapter {s['chapter_acc']:.0%} | "
          f"chunk precision {s['chunk_precision']:.0%} | false 'ok' {s['false_ok']} | no material {s['no_material']}")
    for r in result["rows"]:
        if not r["chapter_ok"] or (r["chunk_precision"] or 0) < 1:
            flag = "WRONG SUBJECT" if not r["subject_ok"] else ("wrong chapter" if not r["chapter_ok"] else "mixed chunks")
            print(f"  {flag:13} {r['id'][:44]:44} -> {r['concept'][:40]:40} conf={r['confidence']} "
                  f"chunks={r['chunk_chapters']}")
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
