"""Dump corpus text as a KaTeX audit fixture for the frontend.

    .venv/bin/python -m local_ingest.latex_fixture [--limit 800] [--out PATH]

Writes what the *student* would actually see: each record is corpus text passed
through `prepare_student_markdown`, the agent's outbound choke point. The frontend
then renders it with the real preprocessor and real KaTeX:

    cd ../Aeropt-frontend && node scripts/katex-audit.mjs fixtures/latex-corpus.json

Keeping the fixture in the frontend repo means a change on either side — a new
MathML construct at ingest, or a regex tweak in markdownMath.ts — is caught by the
same check.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_OUT = ROOT.parent / "Aeropt-frontend" / "fixtures" / "latex-corpus.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=800, help="records to sample")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    from retrieval import store

    if not store.index_exists():
        raise SystemExit("no local index; run python -m local_ingest.build_index first")

    conn = sqlite3.connect(store.CORPUS_DB)
    conn.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT content_id, content_type, core_text, solution FROM chunks "
            "WHERE core_text LIKE '%$%' OR solution LIKE '%$%'"
        )
    ]
    random.seed(args.seed)
    rows = random.sample(rows, min(args.limit, len(rows)))

    from agents.solver import prepare_student_markdown

    records = []
    for row in rows:
        for field in ("core_text", "solution"):
            raw = (row[field] or "").strip()
            if "$" not in raw:
                continue
            records.append(
                {
                    "id": f"{row['content_id']}:{field}",
                    "content_type": row["content_type"],
                    # `stored` is what ingest wrote; `text` is what the student sees
                    # after the agent's outbound pass. Auditing both separates a data
                    # defect from a defect introduced on the way out.
                    "stored": raw[:3000],
                    "text": prepare_student_markdown(raw[:3000]),
                }
            )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(records)} records to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
