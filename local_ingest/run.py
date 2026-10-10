"""SUPERSEDED by `local_ingest/build_index.py` — kept for the Neo4j-shaped local path.

This writes the three-chapter NCERT corpus (203 items, 33 hand-mapped concepts) into
local Neo4j + Postgres, using Gemini embeddings one item at a time. The current local
pipeline derives the whole syllabus instead and needs no Neo4j:

    make index          # local_ingest/build_index.py

Use this one only if you specifically want the Neo4j-backed local setup.

Ingest the local GraphRAG corpus into local Neo4j + local Supabase Postgres.

Refuses Aura and supabase.co. Run from Aeropt-agent:

    .venv/bin/python -m local_ingest.run
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / ".env.local", override=True)

from local_ingest.build_corpus import build_corpus
from local_ingest.guard import assert_local_neo4j, assert_local_supabase

PG_HOST = os.getenv("LOCAL_PG_HOST", "127.0.0.1")
PG_PORT = int(os.getenv("LOCAL_PG_PORT", "54322"))
PG_USER = os.getenv("LOCAL_PG_USER", "postgres")
PG_PASSWORD = os.getenv("LOCAL_PG_PASSWORD", "postgres")
PG_DB = os.getenv("LOCAL_PG_DB", "postgres")

OUT_DIR = ROOT / ".local-run"


def embed_texts(texts: list[str], api_key: str) -> list[list[float]]:
    from google import genai

    client = genai.Client(api_key=api_key)
    vectors: list[list[float]] = []
    model = os.getenv("EMBEDDING_MODEL", "gemini-embedding-2")
    for i, text in enumerate(texts, start=1):
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                result = client.models.embed_content(model=model, contents=text[:8000] or "physics")
                values = result.embeddings[0].values
                vectors.append(list(values))
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001 — retry then fail
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
        if last_exc is not None:
            raise SystemExit(f"Embedding failed at item {i}/{len(texts)}: {last_exc}")
        if i % 25 == 0:
            print(f"  embedded {i}/{len(texts)}")
        time.sleep(0.08)
    dims = {len(v) for v in vectors}
    print(f"Embedding dims: {dims}")
    if dims != {3072}:
        print("WARNING: expected 3072-d gemini-embedding-2 vectors.")
    return vectors


def write_postgres(concepts: list[dict], items: list[dict]) -> None:
    import psycopg2
    from psycopg2.extras import execute_batch

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASSWORD, dbname=PG_DB
    )
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.content, public.concepts TO service_role;")
    cur.execute("TRUNCATE public.content;")
    cur.execute("TRUNCATE public.concepts;")
    execute_batch(
        cur,
        """
        INSERT INTO public.concepts (concept_id, concept_name, chapter, subject)
        VALUES (%(concept_id)s, %(concept_name)s, %(chapter)s, %(subject)s)
        """,
        concepts,
    )
    rows = []
    for it in items:
        rows.append(
            {
                "content_id": it["content_id"],
                "content_type": it["content_type"],
                "subject": it["subject"],
                "chapter": it["chapter"],
                "source_book": it["source_book"],
                "page_number": it["page_number"] or None,
                "core_text": it["core_text"],
                "primary_concept": it["primary_concept"],
                "secondary_concepts": it["secondary_concepts"] or None,
                "solution": it["solution"] or None,
                "difficulty": it["difficulty"],
                "question_type": it["question_type"],
                "has_diagram": it["has_diagram"],
                "has_image_core": it["has_image_core"],
                "has_image_solution": it["has_image_solution"],
                "reviewed": True,
                "target_exam": it["target_exam"],
                "primary_concept_id": it["primary_concept_id"],
            }
        )
    execute_batch(
        cur,
        """
        INSERT INTO public.content (
            content_id, content_type, subject, chapter, source_book, page_number,
            core_text, primary_concept, secondary_concepts, solution, difficulty,
            question_type, has_diagram, has_image_core, has_image_solution,
            reviewed, target_exam, primary_concept_id
        ) VALUES (
            %(content_id)s, %(content_type)s, %(subject)s, %(chapter)s, %(source_book)s,
            %(page_number)s, %(core_text)s, %(primary_concept)s, %(secondary_concepts)s,
            %(solution)s, %(difficulty)s, %(question_type)s, %(has_diagram)s,
            %(has_image_core)s, %(has_image_solution)s, %(reviewed)s, %(target_exam)s,
            %(primary_concept_id)s
        )
        """,
        rows,
        page_size=50,
    )
    conn.commit()
    cur.close()
    conn.close()
    print(f"Postgres {PG_HOST}:{PG_PORT} wrote {len(concepts)} concepts and {len(items)} content rows.")


def write_neo4j(
    uri: str,
    user: str,
    password: str,
    concepts: list[dict],
    requires: list[dict],
    items: list[dict],
    vectors: list[list[float]],
) -> None:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(uri, auth=(user, password))
    dim = len(vectors[0]) if vectors else 3072
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
        s.run("DROP INDEX content_vector_index IF EXISTS")
        s.run(
            """
            UNWIND $rows AS row
            MERGE (c:Concept {concept_id: row.concept_id})
            SET c.name = row.concept_name, c.chapter = row.chapter, c.subject = row.subject
            """,
            rows=concepts,
        )
        s.run(
            """
            UNWIND $rows AS row
            MATCH (a:Concept {concept_id: row.concept_id})
            MATCH (b:Concept {concept_id: row.requires})
            MERGE (a)-[:REQUIRES]->(b)
            """,
            rows=requires,
        )
        payload = []
        for it, vec in zip(items, vectors):
            payload.append(
                {
                    "content_id": it["content_id"],
                    "content_type": it["content_type"],
                    "chapter": it["chapter"],
                    "subject": it["subject"],
                    "target_exam": it["target_exam"],
                    "question_type": it["question_type"],
                    "has_diagram": it["has_diagram"],
                    "difficulty": it["difficulty"],
                    "primary": it["primary_concept_id"],
                    "secondary": [x.strip() for x in (it["secondary_concepts"] or "").split(",") if x.strip()],
                    "embedding": vec,
                }
            )
        s.run(
            """
            UNWIND $rows AS row
            MERGE (ct:Content {content_id: row.content_id})
            SET ct.content_type = row.content_type,
                ct.chapter = row.chapter,
                ct.subject = row.subject,
                ct.target_exam = row.target_exam,
                ct.question_type = row.question_type,
                ct.has_diagram = row.has_diagram,
                ct.difficulty = row.difficulty,
                ct.embedding = row.embedding
            WITH ct, row
            MATCH (p:Concept {concept_id: row.primary})
            MERGE (ct)-[:COVERS]->(p)
            WITH ct, row
            FOREACH (sec IN row.secondary |
              MERGE (s:Concept {concept_id: sec})
              MERGE (ct)-[:COVERS]->(s)
            )
            """,
            rows=payload,
        )
        s.run(
            f"""
            CREATE VECTOR INDEX content_vector_index IF NOT EXISTS
            FOR (c:Content) ON (c.embedding)
            OPTIONS {{indexConfig: {{
              `vector.dimensions`: {int(dim)},
              `vector.similarity_function`: 'cosine'
            }}}}
            """
        )
        # Wait until the index is online so retrieval can use it immediately.
        for _ in range(30):
            rec = s.run(
                "SHOW INDEXES YIELD name, state, populationPercent "
                "WHERE name = 'content_vector_index' RETURN state, populationPercent"
            ).single()
            if rec and rec["state"] == "ONLINE" and float(rec["populationPercent"] or 0) >= 100:
                break
            time.sleep(1)
        else:
            print("WARNING: vector index not fully online yet.")
    driver.close()
    print(f"Neo4j {uri} wrote {len(concepts)} concepts, {len(items)} content nodes, dim={dim}.")


def coverage_report(concepts: list[dict], items: list[dict]) -> None:
    by_type = Counter(it["content_type"] for it in items)
    by_exam = Counter(it["target_exam"] for it in items)
    by_chapter = Counter(it["chapter"] for it in items)
    print("\n=== corpus ===")
    print("types ", dict(by_type))
    print("exams ", dict(by_exam))
    print("chapters ", dict(by_chapter))
    covered = defaultdict(lambda: Counter())
    for it in items:
        covered[it["primary_concept_id"]][it["content_type"]] += 1
        for sec in (it["secondary_concepts"] or "").split(","):
            sec = sec.strip()
            if sec:
                covered[sec][it["content_type"]] += 1
    print("\n=== per-concept (primary+secondary) ===")
    missing_theory = []
    missing_pyq = []
    for c in concepts:
        cid = c["concept_id"]
        counts = covered.get(cid, Counter())
        print(f"  {cid:42} T={counts.get('Theory',0):2} E={counts.get('Solved Example',0):2} P={counts.get('PYQ',0):2}")
        if counts.get("Theory", 0) == 0:
            missing_theory.append(cid)
        if counts.get("PYQ", 0) == 0:
            missing_pyq.append(cid)
    print("no theory:", missing_theory)
    print("no pyq   :", missing_pyq)


def smoke_test(uri: str, user: str, password: str) -> None:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as s:
        n = s.run("MATCH (c:Content) RETURN count(c) AS n").single()["n"]
        types = list(
            s.run(
                "MATCH (c:Content) RETURN c.content_type AS t, count(*) AS n ORDER BY n DESC"
            )
        )
        ids = s.run(
            """
            MATCH (content:Content)-[:COVERS]->(c:Concept)
            WHERE c.concept_id = $cid AND content.content_type IN $types
              AND (content.target_exam IN $exams OR content.target_exam IS NULL)
            RETURN content.content_id AS content_id
            LIMIT 5
            """,
            cid="newtons_2nd_law_f__ma",
            types=["Theory"],
            exams=["Jee Advanced", "Jee main and Advanced"],
        )
        print("\n=== smoke ===")
        print("content nodes", n, "types", [(r["t"], r["n"]) for r in types])
        print("theory for newtons_2nd_law_f__ma", [r["content_id"] for r in ids])
    driver.close()

    import psycopg2

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASSWORD, dbname=PG_DB
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT content_id, content_type, left(core_text,80) FROM public.content "
        "WHERE primary_concept_id = 'newtons_2nd_law_f__ma' LIMIT 3"
    )
    print("supabase samples:", cur.fetchall())
    cur.close()
    conn.close()


def main() -> None:
    neo_uri = os.environ["NEO4J_URI"]
    assert_local_neo4j(neo_uri)
    assert_local_supabase(os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321"))
    if PG_PORT not in (54322, 5432):
        # 54322 is the workspace local supabase used by agent .env (API 54321).
        print(f"Using Postgres {PG_HOST}:{PG_PORT}")

    print("Building corpus from NCERT + HuggingFace JEE PYQs...")
    concepts, requires, items = build_corpus()
    coverage_report(concepts, items)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "corpus.json").write_text(
        json.dumps({"concepts": concepts, "requires": requires, "items": items}, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {OUT_DIR / 'corpus.json'} ({len(items)} items)")

    api_key = os.environ.get("GEMINI_API_KEY") or ""
    if not api_key or api_key == "dummy":
        raise SystemExit("GEMINI_API_KEY missing; needed for gemini-embedding-2 (3072-d).")

    print("Embedding with gemini-embedding-2...")
    cache_path = OUT_DIR / "embeddings.json"
    cache: dict = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
    texts = []
    reuse_idx: dict[int, list[float]] = {}
    to_embed: list[tuple[int, str, str]] = []
    for i, it in enumerate(items):
        key = it["content_id"]
        blob = it["embed_text"]
        cached = cache.get(key)
        if cached and cached.get("text") == blob and cached.get("vector"):
            reuse_idx[i] = cached["vector"]
        else:
            to_embed.append((i, key, blob))
    print(f"  cache hit {len(reuse_idx)}, to embed {len(to_embed)}")
    new_vecs = embed_texts([t[2] for t in to_embed], api_key) if to_embed else []
    vectors: list[list[float]] = [[]] * len(items)
    for i, vec in reuse_idx.items():
        vectors[i] = vec
    for (i, key, blob), vec in zip(to_embed, new_vecs):
        vectors[i] = vec
        cache[key] = {"text": blob, "vector": vec}
    cache_path.write_text(json.dumps(cache))
    print(f"  saved embedding cache ({len(cache)} keys)")
    write_postgres(concepts, items)
    write_neo4j(
        neo_uri,
        os.environ["NEO4J_USER"],
        os.environ["NEO4J_PASSWORD"],
        concepts,
        requires,
        items,
        vectors,
    )
    smoke_test(neo_uri, os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
    print("\nLocal ingest complete. Agent .env.local -> local Neo4j + Supabase :54321.")


if __name__ == "__main__":
    main()
