#!/usr/bin/env python3
"""Upload all retrieval chunks and concepts from corpus.sqlite3 into Supabase.

Usage:
  # Using PostgreSQL Connection URI (Fastest, recommended):
  .venv/bin/python deploy/upload_to_supabase.py --db-url "postgresql://postgres.xxx:PASSWORD@aws-0-xx.pooler.supabase.com:6543/postgres"

  # Or using Supabase REST API (via project URL + service role key):
  .venv/bin/python deploy/upload_to_supabase.py --url "https://xxx.supabase.co" --key "eyJhbG..."

  # Or targeting local Supabase:
  .venv/bin/python deploy/upload_to_supabase.py --local
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, List

ROOT = Path(__file__).resolve().parents[1]
INDEX_DIR = ROOT / ".local-run" / "index"
CORPUS_DB = INDEX_DIR / "corpus.sqlite3"


def check_sqlite_exists():
    if not CORPUS_DB.exists():
        print(f"Error: Could not find {CORPUS_DB}. Run 'make index' first.", file=sys.stderr)
        sys.exit(1)


def upload_via_postgres(db_url: str, truncate: bool = False):
    """Upload via psycopg2 with fast batch execution."""
    import psycopg2
    from psycopg2.extras import execute_batch

    check_sqlite_exists()
    print(f"Connecting to Postgres database...")
    try:
        conn = psycopg2.connect(db_url)
    except Exception as exc:
        print(f"Failed to connect to Postgres: {exc}", file=sys.stderr)
        sys.exit(1)

    sqlite_conn = sqlite3.connect(CORPUS_DB)
    sqlite_conn.row_factory = sqlite3.Row

    cur = conn.cursor()

    # 1. Upload Concepts
    concepts = sqlite_conn.execute("SELECT concept_id, name, chapter, unit FROM concepts").fetchall()
    print(f"Found {len(concepts)} concepts in SQLite.")

    if truncate:
        print("Truncating public.content and public.concepts...")
        cur.execute("TRUNCATE public.content CASCADE;")
        cur.execute("TRUNCATE public.concepts CASCADE;")

    print("Uploading concepts into public.concepts...")
    concept_query = """
        INSERT INTO public.concepts (concept_id, concept_name, chapter, subject)
        VALUES (%s, %s, %s, 'Physics')
        ON CONFLICT (concept_id) DO UPDATE SET
            concept_name = EXCLUDED.concept_name,
            chapter = EXCLUDED.chapter;
    """
    concept_rows = [(c["concept_id"], c["name"], c["chapter"]) for c in concepts]
    execute_batch(cur, concept_query, concept_rows, page_size=200)
    conn.commit()
    print(f"Successfully upserted {len(concepts)} concepts.")

    # 2. Upload Content Chunks
    chunks = sqlite_conn.execute("SELECT * FROM chunks").fetchall()
    total_chunks = len(chunks)
    print(f"Found {total_chunks} chunks in SQLite. Uploading to public.content...")

    content_query = """
        INSERT INTO public.content (
            content_id, content_type, subject, chapter, source_book, page_number, core_text,
            primary_concept, secondary_concepts, solution, difficulty, question_type,
            has_diagram, has_image_core, has_image_solution, reviewed, target_exam,
            primary_concept_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (content_id) DO UPDATE SET
            content_type = EXCLUDED.content_type,
            chapter = EXCLUDED.chapter,
            core_text = EXCLUDED.core_text,
            solution = EXCLUDED.solution,
            difficulty = EXCLUDED.difficulty,
            target_exam = EXCLUDED.target_exam,
            primary_concept_id = EXCLUDED.primary_concept_id;
    """

    content_rows = []
    for c in chunks:
        sec = c["secondary"]
        if sec:
            try:
                sec_list = json.loads(sec)
                sec_str = ", ".join(sec_list) if isinstance(sec_list, list) else str(sec)
            except Exception:
                sec_str = str(sec)
        else:
            sec_str = None

        content_rows.append((
            c["content_id"],
            c["content_type"],
            c["subject"] or "Physics",
            c["chapter"],
            c["source_book"],
            (c["source_ref"] or "")[:120] or None,
            c["core_text"],
            c["concept_id"],
            sec_str,
            c["solution"] or None,
            c["difficulty"],
            c["question_type"],
            False,
            False,
            False,
            True,
            c["target_exam"],
            c["concept_id"],
        ))

    # Batch execute in chunks of 500
    batch_size = 500
    for i in range(0, total_chunks, batch_size):
        batch = content_rows[i:i + batch_size]
        execute_batch(cur, content_query, batch, page_size=200)
        conn.commit()
        done = min(i + batch_size, total_chunks)
        print(f"  Uploaded {done}/{total_chunks} chunks ({(done / total_chunks) * 100:.1f}%)...")

    cur.close()
    conn.close()
    sqlite_conn.close()
    print(f"\nCompleted! All {total_chunks} chunks are live in Supabase public.content.")


def upload_via_rest(url: str, key: str):
    """Upload via Supabase Python client using REST."""
    from supabase import create_client

    check_sqlite_exists()
    print(f"Connecting to Supabase at {url}...")
    sb = create_client(url, key)

    sqlite_conn = sqlite3.connect(CORPUS_DB)
    sqlite_conn.row_factory = sqlite3.Row

    # 1. Concepts
    concepts = sqlite_conn.execute("SELECT concept_id, name, chapter FROM concepts").fetchall()
    print(f"Upserting {len(concepts)} concepts...")
    concept_payload = [
        {"concept_id": c["concept_id"], "concept_name": c["name"], "chapter": c["chapter"], "subject": "Physics"}
        for c in concepts
    ]
    for i in range(0, len(concept_payload), 200):
        sb.table("concepts").upsert(concept_payload[i:i+200]).execute()
    print("Concepts uploaded successfully.")

    # 2. Chunks
    chunks = sqlite_conn.execute("SELECT * FROM chunks").fetchall()
    total = len(chunks)
    print(f"Upserting {total} content chunks...")

    chunk_payload = []
    for c in chunks:
        sec = c["secondary"]
        sec_str = None
        if sec:
            try:
                s = json.loads(sec)
                sec_str = ", ".join(s) if isinstance(s, list) else str(sec)
            except Exception:
                sec_str = str(sec)

        chunk_payload.append({
            "content_id": c["content_id"],
            "content_type": c["content_type"],
            "subject": c["subject"] or "Physics",
            "chapter": c["chapter"],
            "source_book": c["source_book"],
            "page_number": (c["source_ref"] or "")[:120] or None,
            "core_text": c["core_text"],
            "primary_concept": c["concept_id"],
            "secondary_concepts": sec_str,
            "solution": c["solution"] or None,
            "difficulty": c["difficulty"],
            "question_type": c["question_type"],
            "reviewed": True,
            "target_exam": c["target_exam"],
            "primary_concept_id": c["concept_id"],
        })

    for i in range(0, total, 200):
        batch = chunk_payload[i:i+200]
        sb.table("content").upsert(batch).execute()
        done = min(i + 200, total)
        print(f"  Uploaded {done}/{total} chunks ({(done/total)*100:.1f}%)...")

    sqlite_conn.close()
    print(f"\nCompleted! All {total} chunks uploaded to Supabase.")


def main():
    parser = argparse.ArgumentParser(description="Upload SQLite retrieval corpus to Supabase.")
    parser.add_argument("--db-url", type=str, help="PostgreSQL connection string (e.g. postgresql://postgres:pw@host:port/postgres)")
    parser.add_argument("--url", type=str, help="Supabase Project URL (e.g. https://xyz.supabase.co)")
    parser.add_argument("--key", type=str, help="Supabase Service Role Key")
    parser.add_argument("--local", action="store_true", help="Upload to local Supabase at 127.0.0.1:54322")
    parser.add_argument("--truncate", action="store_true", help="Truncate content and concepts tables before upload")

    args = parser.parse_args()

    if args.local:
        db_url = os.getenv(
            "LOCAL_PG_URL",
            "postgresql://postgres.your-tenant-id:your-super-secret-and-long-postgres-password@127.0.0.1:54322/postgres"
        )
        upload_via_postgres(db_url, truncate=args.truncate)
    elif args.db_url:
        upload_via_postgres(args.db_url, truncate=args.truncate)
    elif args.url and args.key:
        upload_via_rest(args.url, args.key)
    else:
        print("Please provide either --db-url, --local, or --url and --key.\nRun with -h for help.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
