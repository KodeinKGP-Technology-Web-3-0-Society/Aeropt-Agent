"""Copy the local retrieval index, keeping only sources cleared for a public site.

    python deploy/public_index.py .local-run/index /tmp/stage/.local-run/index
    python deploy/public_index.py SRC DST --include ncert --include hcverma   # once permitted

`chunks.license` decides. Cleared by default: OpenStax (CC BY-NC-SA, fine while the
site is free and cites it), official JEE Advanced papers, JEE-Bench past papers,
and Aeropt's own reference notes. Held back until permission exists: NCERT and
H.C. Verma. Never shipped: the coaching test papers (unknown provenance).

Rows are deleted from `chunks`, `chunk_concepts` and `chunks_fts` (whose rowid is
the chunk rowid). Their vectors are zeroed rather than removed, so every other
chunk's `vec_row` stays valid. The source index is never modified.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import numpy as np

CLEARED = (
    "CC BY-NC-SA",
    "official exam paper",
    "past exam paper",
    "original (Aeropt)",
)
OPTIONAL = {
    "ncert": "NCERT textbook",
    "hcverma": "H.C. Verma",
}


def _allowed(license_text: str, prefixes: tuple[str, ...]) -> bool:
    return any((license_text or "").startswith(p) for p in prefixes)


def build(src: Path, dst: Path, include: list[str]) -> dict:
    prefixes = CLEARED + tuple(OPTIONAL[name] for name in include)
    dst.mkdir(parents=True, exist_ok=True)
    out_db = dst / "corpus.sqlite3"
    out_db.unlink(missing_ok=True)

    source = sqlite3.connect(f"file:{src / 'corpus.sqlite3'}?mode=ro", uri=True)
    target = sqlite3.connect(out_db)
    source.backup(target)
    source.close()

    rows = target.execute("SELECT rowid, content_id, vec_row, license, source_book FROM chunks").fetchall()
    dropped = [r for r in rows if not _allowed(r[3], prefixes)]
    kept_sources = sorted({r[4] for r in rows if _allowed(r[3], prefixes)})

    with target:
        target.executemany("DELETE FROM chunks_fts WHERE rowid = ?", [(r[0],) for r in dropped])
        target.executemany("DELETE FROM chunk_concepts WHERE content_id = ?", [(r[1],) for r in dropped])
        target.executemany("DELETE FROM chunks WHERE rowid = ?", [(r[0],) for r in dropped])
        target.execute("UPDATE meta SET value = ? WHERE key = 'chunks'", (json.dumps(len(rows) - len(dropped)),))
        target.execute("UPDATE meta SET value = ? WHERE key = 'sources'", (json.dumps(kept_sources),))
    target.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
    target.commit()
    target.execute("VACUUM")
    target.close()

    vectors = np.load(src / "vectors.npy")
    drop_rows = [int(r[2]) for r in dropped if r[2] is not None]
    if drop_rows:
        vectors[drop_rows] = 0.0
    np.save(dst / "vectors.npy", vectors)
    shutil.copy2(src / "concept_vectors.npy", dst / "concept_vectors.npy")

    by_license: dict[str, int] = {}
    for r in dropped:
        by_license[r[3]] = by_license.get(r[3], 0) + 1
    return {"kept": len(rows) - len(dropped), "dropped": len(dropped), "dropped_by_license": by_license}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--include", action="append", default=[], choices=sorted(OPTIONAL),
                    help="also ship this source (only with permission to publish it)")
    args = ap.parse_args(argv)
    if not (args.src / "corpus.sqlite3").exists():
        print(f"No index at {args.src}; run `make index` first.", file=sys.stderr)
        return 1
    if args.src.resolve() == args.dst.resolve():
        print("Refusing to filter the index in place.", file=sys.stderr)
        return 1
    print(json.dumps(build(args.src, args.dst, args.include), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
