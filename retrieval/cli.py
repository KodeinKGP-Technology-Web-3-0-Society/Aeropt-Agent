"""Inspect the local retrieval pipeline from the shell.

    .venv/bin/python -m retrieval.cli "why does a rolling ball not slip"
    .venv/bin/python -m retrieval.cli --type practice --user u1 "friction questions"
    .venv/bin/python -m retrieval.cli --status
    .venv/bin/python -m retrieval.cli --bench
    .venv/bin/python -m retrieval.cli --forget u1

Prints the gate decision, the resolution and every chunk with its citation, which is
how you check grounding quality without reading the agent's transcript.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BENCH_QUERIES = [
    "what is the work energy theorem",
    "explain rolling without slipping",
    "moment of inertia of a rod about its end",
    "give me a practice question on friction",
    "jee advanced pyq on the photoelectric effect",
    "derive bernoulli's equation",
    "why is centrifugal force a pseudo force",
    "young's double slit fringe width",
]


def show(pack: dict, full: bool = False) -> None:
    decision = pack["decision"]
    print(f"gate      {decision['action']}  ({decision['reason']})")
    concept = pack.get("concept") or {}
    if concept:
        print(
            f"concept   {concept.get('concept_id') or '-'}  "
            f"[{concept.get('chapter') or '-'} / {concept.get('unit') or '-'}]  "
            f"strength={concept.get('grounding_strength')}  via {concept.get('matched_by')}"
        )
        if concept.get("alternatives"):
            alts = ", ".join(f"{a['name']} ({a['score']})" for a in concept["alternatives"])
            print(f"          alternatives: {alts}")
    print(
        f"result    {pack['confidence']}  {len(pack['chunks'])} chunks  "
        f"{pack.get('content_type_used') or pack['content_type']}/{pack.get('scope_used') or '-'}  "
        f"{pack['took_ms']} ms  candidates={pack.get('candidates_considered', 0)}"
    )
    novelty = pack.get("novelty") or {}
    if novelty.get("enforced"):
        print(
            f"novelty   filtered={novelty['filtered_already_seen']}  "
            f"unseen_left_on_concept={novelty.get('concept_unseen_remaining')}"
            f"/{novelty.get('concept_question_total')}  "
            f"borrowed={novelty.get('borrowed_from_neighbours')}"
        )
    for note in pack.get("notes") or []:
        print(f"note      {note}")
    for i, chunk in enumerate(pack["chunks"], start=1):
        body = chunk["core_text"] if full else chunk["core_text"][:220].replace("\n", " ")
        print(f"\n  [{i}] {chunk['content_id']}  {chunk['content_type']}  "
              f"diff={chunk['difficulty']}  score={chunk['retrieval_score']}")
        print(f"      cite: {chunk['citation']}")
        print(f"      {body}")
        if full and chunk.get("solution"):
            print(f"      SOLUTION: {chunk['solution'][:800]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="inspect local retrieval")
    ap.add_argument("query", nargs="*", help="the student's message")
    ap.add_argument("--type", default="", help="theory | example | practice | pyq")
    ap.add_argument("--user", default="", help="user id, to apply the served-question ledger")
    ap.add_argument("--concept", default="", help="force a concept_id")
    ap.add_argument("--exam", default="", help="JEE_ADVANCED | JEE_MAINS")
    ap.add_argument("-k", "--top-k", type=int, default=5)
    ap.add_argument("--full", action="store_true", help="print whole chunks and solutions")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--status", action="store_true", help="index metadata")
    ap.add_argument("--bench", action="store_true", help="time a standard query set")
    ap.add_argument("--forget", default="", help="clear the served ledger for a user")
    args = ap.parse_args()

    from retrieval import service, store

    if args.status:
        print(json.dumps(service.status(), indent=2))
        return 0

    if args.forget:
        print(f"cleared {service.forget(args.forget)} served rows for {args.forget}")
        return 0

    if args.bench:
        if not store.index_exists():
            print("index not built; run: python -m local_ingest.build_index")
            return 1
        service.retrieve("warm up", user_id="", mark=False)
        timings = []
        for query in BENCH_QUERIES:
            started = time.perf_counter()
            pack = service.retrieve(query, user_id="", mark=False)
            elapsed = (time.perf_counter() - started) * 1000
            timings.append(elapsed)
            print(f"{elapsed:7.2f} ms  {pack['confidence']:10} {len(pack['chunks'])} chunks  {query}")
        timings.sort()
        print(f"\nmedian {timings[len(timings) // 2]:.2f} ms   max {timings[-1]:.2f} ms")
        return 0

    query = " ".join(args.query).strip()
    if not query:
        ap.error("pass a query, or --status / --bench / --forget")

    pack = service.retrieve(
        query,
        user_id=args.user,
        content_type=args.type,
        concept_id=args.concept,
        target_exam=args.exam,
        top_k=args.top_k,
        mark=bool(args.user),
    )
    if args.json:
        print(json.dumps(pack, indent=2)[:20000])
    else:
        show(pack, full=args.full)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
