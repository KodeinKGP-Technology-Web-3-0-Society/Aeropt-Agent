"""Build the local retrieval index end to end.

    .venv/bin/python -m local_ingest.build_index [--no-postgres] [--fetch]

Stages: fetch sources → derive syllabus → build items → map concepts → embed →
write SQLite + vectors → mirror into local Postgres → verify.

Refuses any non-local database (see local_ingest/guard.py): this never touches
Aura or a hosted Supabase project.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local", override=True)

from local_ingest import corpus as corpus_builder
from local_ingest import syllabus as syllabus_builder
from local_ingest.guard import assert_local_supabase
from retrieval import embedder, store

PG = {
    "host": os.getenv("LOCAL_PG_HOST", "127.0.0.1"),
    "port": int(os.getenv("LOCAL_PG_PORT", "54322")),
    "user": os.getenv("LOCAL_PG_USER", "postgres"),
    "password": os.getenv("LOCAL_PG_PASSWORD", "postgres"),
    "dbname": os.getenv("LOCAL_PG_DB", "postgres"),
}


def write_sqlite(syllabus, items, item_vecs, concept_vecs) -> None:
    store.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    if store.CORPUS_DB.exists():
        store.CORPUS_DB.unlink()
    for suffix in ("-wal", "-shm"):
        side = Path(str(store.CORPUS_DB) + suffix)
        if side.exists():
            side.unlink()
    store.reset_caches()

    conn = sqlite3.connect(store.CORPUS_DB)
    conn.executescript(store.SCHEMA)
    chapters = syllabus["chapters"]
    concepts = syllabus["concepts"]

    with conn:
        conn.executemany(
            "INSERT INTO chapters (chapter_id, title, volume, unit, jee_unit, in_syllabus, ord)"
            " VALUES (?,?,?,?,?,?,?)",
            [
                (c.chapter_id, c.title, c.volume, c.unit, c.jee_unit, int(c.in_syllabus), c.order)
                for c in chapters
            ],
        )
        conn.executemany(
            "INSERT INTO concepts (concept_id, name, definition, chapter_id, chapter, unit,"
            " in_syllabus, ord, aliases, source, vec_row) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    c.concept_id, c.name, c.definition, c.chapter_id, c.chapter, c.unit,
                    int(c.in_syllabus), c.order, json.dumps(c.aliases), c.source, i,
                )
                for i, c in enumerate(concepts)
            ],
        )
        valid = {c.concept_id for c in concepts}
        conn.executemany(
            "INSERT OR IGNORE INTO concept_edges (concept_id, requires) VALUES (?,?)",
            [
                (e["concept_id"], e["requires"])
                for e in syllabus["requires"]
                if e["concept_id"] in valid and e["requires"] in valid
            ],
        )
        rows = []
        fts_rows = []
        links = []
        concept_name = {c.concept_id: c.name for c in concepts}
        for i, it in enumerate(items):
            rows.append(
                (
                    it["content_id"], i, it["content_type"], it["subject"], it["chapter_id"],
                    it["chapter"], it["unit"], it["concept_id"], json.dumps(it["secondary_concepts"]),
                    it["source_book"], it["source_ref"], it["license"], it["difficulty"],
                    it["question_type"], it["target_exam"], int(bool(it["in_syllabus"])),
                    int(bool(it["has_solution"])), int(bool(it["is_question"])), it["heading"],
                    it["core_text"], it["solution"], it["fingerprint"], it["token_len"],
                )
            )
            fts_rows.append(
                (
                    i + 1,
                    it["core_text"] + "\n" + it["solution"][:2000],
                    it["heading"],
                    f"{concept_name.get(it['concept_id'], '')} {it['chapter']} {it['unit']}",
                )
            )
            links.append((it["content_id"], it["concept_id"], "primary"))
            for sec in it["secondary_concepts"]:
                links.append((it["content_id"], sec, "secondary"))
        conn.executemany(
            "INSERT INTO chunks (content_id, vec_row, content_type, subject, chapter_id, chapter,"
            " unit, concept_id, secondary, source_book, source_ref, license, difficulty,"
            " question_type, target_exam, in_syllabus, has_solution, is_question, heading,"
            " core_text, solution, fingerprint, token_len)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.executemany(
            "INSERT INTO chunks_fts (rowid, body, heading, concept) VALUES (?,?,?,?)", fts_rows
        )
        conn.executemany(
            "INSERT OR IGNORE INTO chunk_concepts (content_id, concept_id, role) VALUES (?,?,?)",
            [link for link in links if link[1]],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
            [
                ("embed_model", json.dumps(embedder.model_name())),
                ("embed_backend", json.dumps(embedder.BACKEND)),
                ("dimension", json.dumps(int(item_vecs.shape[1]))),
                ("chunks", json.dumps(len(items))),
                ("concepts", json.dumps(len(concepts))),
                ("chapters", json.dumps(len(chapters))),
                ("built_at", json.dumps(time.strftime("%Y-%m-%dT%H:%M:%S"))),
                ("sources", json.dumps(sorted({it["source_book"] for it in items}))),
            ],
        )
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
    conn.commit()
    conn.close()

    np.save(store.VECTORS, item_vecs)
    np.save(store.CONCEPT_VECTORS, concept_vecs)
    print(f"index: {store.CORPUS_DB} ({store.CORPUS_DB.stat().st_size / 1e6:.1f} MB), "
          f"vectors {item_vecs.shape}, concept vectors {concept_vecs.shape}")


def mirror_postgres(syllabus, items) -> None:
    """Keep public.concepts / public.content in step so the existing tools still read."""
    assert_local_supabase(os.getenv("SUPABASE_URL", "http://127.0.0.1:54321"))
    import psycopg2
    from psycopg2.extras import execute_batch

    conn = psycopg2.connect(**PG)
    cur = conn.cursor()
    cur.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.content, public.concepts TO service_role;"
    )
    cur.execute("TRUNCATE public.content;")
    cur.execute("TRUNCATE public.concepts;")
    execute_batch(
        cur,
        "INSERT INTO public.concepts (concept_id, concept_name, chapter, subject)"
        " VALUES (%s, %s, %s, %s)",
        [(c.concept_id, c.name, c.chapter, "Physics") for c in syllabus["concepts"]],
        page_size=200,
    )
    execute_batch(
        cur,
        """
        INSERT INTO public.content (
            content_id, content_type, subject, chapter, source_book, page_number, core_text,
            primary_concept, secondary_concepts, solution, difficulty, question_type,
            has_diagram, has_image_core, has_image_solution, reviewed, target_exam,
            primary_concept_id
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        [
            (
                it["content_id"], it["content_type"], it["subject"], it["chapter"],
                it["source_book"], it["source_ref"][:120] or None, it["core_text"],
                it["concept_id"], ", ".join(it["secondary_concepts"]) or None,
                it["solution"] or None, it["difficulty"], it["question_type"],
                False, False, False, True, it["target_exam"], it["concept_id"],
            )
            for it in items
        ],
        page_size=100,
    )
    conn.commit()
    cur.close()
    conn.close()
    print(f"postgres {PG['host']}:{PG['port']}: {len(syllabus['concepts'])} concepts, {len(items)} content rows")


def report(syllabus, items) -> None:
    concepts = syllabus["concepts"]
    print("\n=== corpus ===")
    print("items            ", len(items))
    print("by type          ", dict(Counter(it["content_type"] for it in items)))
    print("by source        ", dict(Counter(it["source_book"][:28] for it in items).most_common(6)))
    print("questions w/ sol ", sum(1 for it in items if it["is_question"] and it["has_solution"]))
    print("in JEE scope     ", sum(1 for it in items if it["in_syllabus"]))
    print("mean map score   ", round(float(np.mean([it["mapping_score"] for it in items])), 3))
    weak = [it for it in items if it["mapping_score"] < 0.15]
    print("weak mappings    ", len(weak))

    covered = Counter()
    for it in items:
        covered[it["concept_id"]] += 1
        for sec in it["secondary_concepts"]:
            covered[sec] += 1
    in_scope = [c for c in concepts if c.in_syllabus]
    bare = [c for c in in_scope if covered.get(c.concept_id, 0) == 0]
    print(f"concepts in scope {len(in_scope)}, with content {len(in_scope) - len(bare)}, bare {len(bare)}")

    per_unit = Counter()
    q_per_unit = Counter()
    for it in items:
        per_unit[it["unit"] or "(unmapped)"] += 1
        if it["is_question"]:
            q_per_unit[it["unit"] or "(unmapped)"] += 1
    print("\n=== per JEE unit (items / questions) ===")
    for unit, n in sorted(per_unit.items(), key=lambda kv: -kv[1]):
        print(f"  {unit[:46]:48} {n:5} / {q_per_unit.get(unit, 0):5}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-postgres", action="store_true", help="skip the public.content mirror")
    ap.add_argument("--fetch", action="store_true", help="download missing OpenStax modules first")
    args = ap.parse_args()

    if args.fetch:
        from local_ingest.fetch_openstax import fetch_all

        fetch_all()

    t0 = time.time()
    embedder.snapshot_static_model()
    print("deriving syllabus from sources...")
    syllabus = syllabus_builder.build()
    print(f"  chapters {len(syllabus['chapters'])}, concepts {len(syllabus['concepts'])}, "
          f"requires {len(syllabus['requires'])}  ({time.time() - t0:.1f}s)")

    t1 = time.time()
    print("building items + mapping concepts...")
    items, item_vecs, concept_vecs = corpus_builder.build_items(syllabus)
    print(f"  {len(items)} items embedded in {time.time() - t1:.1f}s")

    write_sqlite(syllabus, items, item_vecs, concept_vecs)
    if not args.no_postgres:
        try:
            mirror_postgres(syllabus, items)
        except Exception as exc:  # noqa: BLE001 — the local index is the primary artifact
            print(f"postgres mirror skipped: {str(exc)[:200]}")
    report(syllabus, items)
    print(f"\ndone in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
