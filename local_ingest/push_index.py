"""Build the retrieval corpus straight into the Supabase retrieval Postgres — no SQLite.

    make push ALLOW_HOSTED=1          # the deployed retrieval project
    make push-dry ALLOW_HOSTED=1      # diff only, rolls back

    sources -> syllabus (physics: OpenStax, chemistry: NCERT headings) -> items
            -> concept mapping -> embeddings (retrieval/embedder.py, the only model) -> upsert

The target is RETRIEVAL_DATABASE_URL. This is the only writer of the retrieval corpus; the
agent only reads it (retrieval/store.py).

  --dry-run         build + embed + diff against the database, write nothing
  --prune           delete rows of the pushed subject(s) that this build no longer produces
  --allow-hosted    required when the URL is not localhost
  --subject X       push only this subject's items (default: every subject the build yields)
  --skip-topics     skip the JEE syllabus-topic pass (physics)
  --fetch           download missing OpenStax modules first

Rows are written only when their hash changed (text, metadata, vector *and* model id), so a
re-push of an unchanged corpus writes nothing and does not churn the HNSW index, while a
model change rewrites every row. Subjects not in the build are never pruned.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local", override=True)

from retrieval.embedder import DIM, MODEL_ID, to_pg as vec_literal  # noqa: E402 — the one embedding contract

MIGRATION = ROOT / "db" / "migrations" / "017_retrieval_pgvector.sql"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def row_hash(payload: dict, vec: np.ndarray, model: str) -> str:
    h = hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode())
    h.update(model.encode())
    h.update(np.asarray(vec, dtype=np.float32).tobytes())
    return h.hexdigest()


def connect(url: str, allow_hosted: bool):
    import psycopg2

    host = (urlparse(url).hostname or "").lower()
    if host not in LOCAL_HOSTS and not allow_hosted:
        raise SystemExit(
            f"Refusing to write {host!r}: not localhost. Re-run with --allow-hosted once you have "
            "reviewed a --dry-run against it."
        )
    conn = psycopg2.connect(url, connect_timeout=15)
    print(f"target: {host} ({'local' if host in LOCAL_HOSTS else 'HOSTED'})")
    return conn


def ensure_schema(cur) -> None:
    cur.execute(MIGRATION.read_text())
    # `add column if not exists` keeps a pre-existing column of another dimension; fail loudly.
    for table in ("content", "concepts"):
        cur.execute(
            "select format_type(atttypid, atttypmod) from pg_attribute"
            " where attrelid = %s::regclass and attname = 'embedding'",
            (f"public.{table}",),
        )
        found = cur.fetchone()[0]
        if found != f"vector({DIM})":
            raise SystemExit(
                f"public.{table}.embedding is {found}, expected vector({DIM}). Embedding spaces must not be "
                "mixed — move the old column aside before pushing."
            )


def upsert_chapters(cur, chapters) -> None:
    from psycopg2.extras import execute_values

    execute_values(
        cur,
        "insert into public.chapters (chapter_id, subject, title, volume, unit, jee_unit, in_syllabus, ord) values %s"
        " on conflict (chapter_id) do update set subject=excluded.subject, title=excluded.title,"
        " volume=excluded.volume, unit=excluded.unit, jee_unit=excluded.jee_unit,"
        " in_syllabus=excluded.in_syllabus, ord=excluded.ord",
        chapters,
    )


CONCEPT_SQL = (
    "insert into public.concepts (concept_id, concept_name, chapter, subject, chapter_id, unit, definition,"
    " in_syllabus, ord, aliases, source, embedding, embed_model, text_hash) values %s"
    " on conflict (concept_id) do update set concept_name=excluded.concept_name, chapter=excluded.chapter,"
    " subject=excluded.subject, chapter_id=excluded.chapter_id, unit=excluded.unit,"
    " definition=excluded.definition, in_syllabus=excluded.in_syllabus, ord=excluded.ord,"
    " aliases=excluded.aliases, source=excluded.source, embedding=excluded.embedding,"
    " embed_model=excluded.embed_model, text_hash=excluded.text_hash"
)
CONCEPT_TEMPLATE = "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::vector,%s,%s)"


def concept_tuple(d: dict, vec: np.ndarray, model: str) -> tuple:
    h = row_hash({k: d[k] for k in ("concept_id", "concept_name", "chapter", "chapter_id", "unit", "definition",
                                    "in_syllabus", "ord", "aliases", "source", "subject")}, vec, model)
    return (d["concept_id"], d["concept_name"], d["chapter"], d["subject"], d["chapter_id"], d["unit"],
            d["definition"], d["in_syllabus"], d["ord"], json.dumps(d["aliases"], ensure_ascii=False),
            d["source"], vec_literal(vec), model, h)


def upsert_concepts(cur, rows: list[tuple]) -> int:
    """Write only concepts whose hash differs. Returns rows written."""
    from psycopg2.extras import execute_values

    cur.execute("select concept_id, text_hash from public.concepts")
    have = dict(cur.fetchall())
    todo = [r for r in rows if have.get(r[0]) != r[-1]]
    if todo:
        execute_values(cur, CONCEPT_SQL, todo, template=CONCEPT_TEMPLATE, page_size=200)
    return len(todo)


CONTENT_SQL = (
    "insert into public.content (content_id, content_type, subject, chapter, chapter_id, unit, source_book,"
    " source_ref, page_number, license, heading, core_text, solution, primary_concept, primary_concept_id,"
    " secondary_concepts, difficulty, question_type, target_exam, in_syllabus, has_solution, is_question,"
    " fingerprint, token_len, structured, has_diagram, has_image_core, has_image_solution, reviewed,"
    " embedding, embed_model, text_hash, synced_at) values %s"
    " on conflict (content_id) do update set content_type=excluded.content_type, subject=excluded.subject,"
    " chapter=excluded.chapter, chapter_id=excluded.chapter_id, unit=excluded.unit,"
    " source_book=excluded.source_book, source_ref=excluded.source_ref, page_number=excluded.page_number,"
    " license=excluded.license, heading=excluded.heading, core_text=excluded.core_text,"
    " solution=excluded.solution, primary_concept=excluded.primary_concept,"
    " primary_concept_id=excluded.primary_concept_id, secondary_concepts=excluded.secondary_concepts,"
    " difficulty=excluded.difficulty, question_type=excluded.question_type, target_exam=excluded.target_exam,"
    " in_syllabus=excluded.in_syllabus, has_solution=excluded.has_solution, is_question=excluded.is_question,"
    " fingerprint=excluded.fingerprint, token_len=excluded.token_len, structured=excluded.structured,"
    " embedding=excluded.embedding, embed_model=excluded.embed_model, text_hash=excluded.text_hash,"
    " synced_at=excluded.synced_at"
)
CONTENT_TEMPLATE = "(" + ",".join(["%s"] * 24) + ",%s::jsonb,false,false,false,true,%s::vector,%s,%s,now())"


def content_tuple(it: dict, vec: np.ndarray, model: str) -> tuple:
    payload = {k: it.get(k) for k in (
        "content_id", "content_type", "subject", "chapter", "chapter_id", "unit", "source_book", "source_ref",
        "license", "heading", "core_text", "solution", "concept_id", "secondary_concepts", "difficulty",
        "question_type", "target_exam", "in_syllabus", "has_solution", "is_question", "fingerprint",
        "token_len", "structured")}
    structured = json.dumps(it["structured"], ensure_ascii=False) if it.get("structured") else None
    return (
        it["content_id"], it["content_type"], it["subject"], it["chapter"], it["chapter_id"], it["unit"],
        it["source_book"], it["source_ref"], (it["source_ref"] or "")[:120] or None, it["license"],
        it["heading"], it["core_text"], it["solution"] or None, it["concept_id"], it["concept_id"],
        ", ".join(it["secondary_concepts"]) or None, it["difficulty"], it["question_type"], it["target_exam"],
        bool(it["in_syllabus"]), bool(it["has_solution"]), bool(it["is_question"]), it["fingerprint"],
        it["token_len"], structured, vec_literal(vec), model, row_hash(payload, vec, model),
    )


def upsert_content(cur, items, vecs, model: str) -> tuple[int, int]:
    from psycopg2.extras import execute_values

    cur.execute("select content_id, text_hash from public.content")
    have = dict(cur.fetchall())
    todo = []
    for it, v in zip(items, vecs):
        t = content_tuple(it, v, model)
        if have.get(it["content_id"]) != t[-1]:
            todo.append(t)
    for start in range(0, len(todo), 500):
        execute_values(cur, CONTENT_SQL, todo[start:start + 500], template=CONTENT_TEMPLATE, page_size=500)
    return len(todo), len(items) - len(todo)


def replace_links(cur, items) -> int:
    """Primary + secondary links for the pushed items (syllabus-topic links are rebuilt separately)."""
    from psycopg2.extras import execute_values

    ids = [it["content_id"] for it in items]
    cur.execute(
        "delete from public.chunk_concepts where role in ('primary','secondary') and content_id = any(%s)", (ids,)
    )
    links = []
    for it in items:
        if it["concept_id"]:
            links.append((it["content_id"], it["concept_id"], "primary"))
        links.extend((it["content_id"], s, "secondary") for s in it["secondary_concepts"] if s)
    execute_values(
        cur, "insert into public.chunk_concepts (content_id, concept_id, role) values %s"
        " on conflict (content_id, concept_id) do nothing", links, page_size=1000,
    )
    return len(links)


def replace_edges(cur, syllabus) -> int:
    from psycopg2.extras import execute_values

    valid = {c.concept_id for c in syllabus["concepts"]}
    edges = sorted({(e["concept_id"], e["requires"]) for e in syllabus["requires"]
                    if e["concept_id"] in valid and e["requires"] in valid})
    cur.execute("delete from public.concept_edges where concept_id = any(%s)", (sorted(valid),))
    if edges:
        execute_values(cur, "insert into public.concept_edges (concept_id, requires) values %s", edges, page_size=1000)
    return len(edges)


def prune(cur, subjects: list[str], items, concept_ids: set[str]) -> dict[str, int]:
    keep = [it["content_id"] for it in items]
    cur.execute(
        "delete from public.content where subject = any(%s) and not (content_id = any(%s))", (subjects, keep)
    )
    gone_content = cur.rowcount
    cur.execute(
        "delete from public.concepts where subject = any(%s) and not (concept_id = any(%s))",
        (subjects, sorted(concept_ids)),
    )
    gone_concepts = cur.rowcount
    cur.execute("delete from public.chunk_concepts where content_id not in (select content_id from public.content)")
    return {"content": gone_content, "concepts": gone_concepts}


def finalize(cur, items, n_concepts: int) -> None:
    """Record what the corpus was built with, then make the model CHECK constraints binding."""
    from psycopg2.extras import execute_values

    meta = {
        "embed_model": MODEL_ID,
        "embed_dim": str(DIM),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "content_rows": str(len(items)),
        "concept_rows": str(n_concepts),
        "subjects": json.dumps(dict(Counter(it["subject"] for it in items))),
    }
    execute_values(cur, "insert into public.retrieval_meta (key, value) values %s on conflict (key) do update"
                   " set value = excluded.value, updated_at = now()", list(meta.items()))
    for table in ("content", "concepts"):
        cur.execute("savepoint v")
        try:
            cur.execute(f"alter table public.{table} validate constraint {table}_embed_model_chk")
            cur.execute("release savepoint v")
        except Exception as exc:  # noqa: BLE001 — rows outside this build still lack a model tag
            cur.execute("rollback to savepoint v")
            print(f"  {table}: model constraint not validated yet ({str(exc).splitlines()[0][:120]}); "
                  "re-run with --prune to drop rows this build no longer produces")


def verify(cur) -> None:
    from retrieval import embedder

    cur.execute("select subject, count(*), count(embedding), count(*) filter (where embed_model = %s)"
                " from public.content group by 1 order by 1", (MODEL_ID,))
    for subject, n, embedded, tagged in cur.fetchall():
        print(f"  content  {subject or '(none)':<10} {n:6} rows, {embedded:6} embedded, {tagged:6} tagged {MODEL_ID}")
    cur.execute("select subject, count(*), count(*) filter (where embed_model = %s) from public.concepts"
                " group by 1 order by 1", (MODEL_ID,))
    for subject, n, tagged in cur.fetchall():
        print(f"  concepts {subject or '(none)':<10} {n:6} rows, {tagged:6} tagged")
    for q in ("why does a spinning skater speed up when she pulls her arms in", "aufbau principle electron filling"):
        cur.execute("select concept_name, subject from public.concepts order by embedding <=> %s::vector limit 3",
                    (vec_literal(embedder.encode_one(q)),))
        print(f"  probe {q[:40]!r} ->", cur.fetchall())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--allow-hosted", action="store_true")
    ap.add_argument("--subject")
    ap.add_argument("--skip-topics", action="store_true")
    ap.add_argument("--fetch", action="store_true")
    args = ap.parse_args()

    url = os.getenv("RETRIEVAL_DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("Set RETRIEVAL_DATABASE_URL to the Postgres connection string of the retrieval database.")

    from local_ingest import corpus as corpus_builder
    from local_ingest import syllabus as syllabus_builder
    from retrieval import embedder

    if args.fetch:
        from local_ingest.fetch_openstax import fetch_all

        fetch_all()

    t0 = time.time()
    embedder.snapshot_static_model()
    syllabus = syllabus_builder.build()
    print(f"syllabus: {len(syllabus['chapters'])} chapters, {len(syllabus['concepts'])} concepts "
          f"({time.time() - t0:.1f}s)")
    items, item_vecs, concept_vecs = corpus_builder.build_items(syllabus)
    if args.subject:
        keep = [i for i, it in enumerate(items) if it["subject"].lower() == args.subject.lower()]
        items, item_vecs = [items[i] for i in keep], item_vecs[keep]
    if item_vecs.shape[1] != DIM or concept_vecs.shape[1] != DIM:
        raise SystemExit(f"embedding dimension {item_vecs.shape[1]} != {DIM}")
    model = embedder.model_name()
    subjects = sorted({it["subject"] for it in items})
    print(f"items: {len(items)} {dict(Counter(it['subject'] for it in items))} embedded ({time.time() - t0:.1f}s)")

    conn = connect(url, args.allow_hosted)
    try:
        cur = conn.cursor()
        ensure_schema(cur)
        concepts = syllabus["concepts"]
        chapter_rows = [(c.chapter_id, c.subject, c.title, c.volume, c.unit, c.jee_unit, bool(c.in_syllabus), c.order)
                        for c in syllabus["chapters"]]
        concept_dicts = [{
            "concept_id": c.concept_id, "concept_name": c.name, "chapter": c.chapter, "chapter_id": c.chapter_id,
            "unit": c.unit, "definition": c.definition, "in_syllabus": bool(c.in_syllabus), "ord": c.order,
            "aliases": c.aliases, "source": c.source, "subject": c.subject,
        } for c in concepts]

        upsert_chapters(cur, chapter_rows)
        c_written = upsert_concepts(cur, [concept_tuple(d, v, model) for d, v in zip(concept_dicts, concept_vecs)])
        written, unchanged = upsert_content(cur, items, item_vecs, model)
        links = replace_links(cur, items)
        edges = replace_edges(cur, syllabus)
        print(f"concepts written {c_written}/{len(concepts)}; content written {written}, unchanged {unchanged}; "
              f"links {links}, edges {edges}")

        concept_ids = {c.concept_id for c in concepts}
        if not args.skip_topics and (not args.subject or args.subject.lower() == "physics"):
            from local_ingest import pg_syllabus_topics

            physics = [c for c in concepts if c.subject == "Physics"]
            res = pg_syllabus_topics.apply(cur, physics, [c for c in syllabus["chapters"] if c.subject == "Physics"],
                                           [it for it in items if it["subject"] == "Physics"], embedder.encode)
            from psycopg2.extras import execute_values

            if res["chapters"]:
                upsert_chapters(cur, [(c["chapter_id"], c["subject"], c["title"], c["volume"], c["unit"],
                                       c["jee_unit"], c["in_syllabus"], c["ord"]) for c in res["chapters"]])
            if res["concepts"]:
                upsert_concepts(cur, [concept_tuple(c, c["embedding"], model) for c in res["concepts"]])
            if res["links"]:
                execute_values(cur, "insert into public.chunk_concepts (content_id, concept_id, role) values %s"
                               " on conflict (content_id, concept_id) do nothing",
                               [(a, b, "syllabus") for a, b in res["links"]], page_size=1000)
            execute_values(cur, "insert into public.syllabus_topics (subject, unit, unit_ord, topic, topic_ord,"
                           " concept_id, chapter_id, created, material) values %s", res["topic_rows"])
            concept_ids |= {c["concept_id"] for c in res["concepts"]}

        if args.prune:
            print("pruned:", prune(cur, subjects, items, concept_ids))
        finalize(cur, items, len(concept_ids))
        if args.dry_run:
            conn.rollback()
            print("dry run: rolled back, nothing written")
        else:
            conn.commit()
            cur.execute("analyze public.content")
            cur.execute("analyze public.concepts")
            conn.commit()
            verify(cur)
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"done in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
