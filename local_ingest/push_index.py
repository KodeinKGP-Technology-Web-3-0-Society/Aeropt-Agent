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

MIGRATION = ROOT / "db" / "migrations" / "019_retrieval_pgvector.sql"
INTEGRITY = ROOT / "db" / "migrations" / "023_retrieval_integrity.sql"
READABLE = ROOT / "db" / "migrations" / "024_retrieval_readable.sql"
SOURCES = ROOT / "db" / "migrations" / "025_sources_solutions.sql"
PROVENANCE = ROOT / "db" / "migrations" / "026_provenance_rights_issues.sql"
INTEGRITY_CONSTRAINTS = sorted(set(re.findall(r"'(\w+_(?:fk|chk))'", INTEGRITY.read_text()))) + ["content_source_fk"]
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
    cur.execute(INTEGRITY.read_text())
    cur.execute(READABLE.read_text())
    cur.execute(SOURCES.read_text())
    cur.execute(PROVENANCE.read_text())
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
    " source_ref, page_number, license, heading, core_text, primary_concept, primary_concept_id,"
    " secondary_concepts, difficulty, question_type, target_exam, in_syllabus, has_solution, is_question,"
    " fingerprint, token_len, structured,"
    " embedding, embed_model, text_hash, synced_at) values %s"
    " on conflict (content_id) do update set content_type=excluded.content_type, subject=excluded.subject,"
    " chapter=excluded.chapter, chapter_id=excluded.chapter_id, unit=excluded.unit,"
    " source_book=excluded.source_book, source_ref=excluded.source_ref, page_number=excluded.page_number,"
    " license=excluded.license, heading=excluded.heading, core_text=excluded.core_text,"
    " primary_concept=excluded.primary_concept,"
    " primary_concept_id=excluded.primary_concept_id, secondary_concepts=excluded.secondary_concepts,"
    " difficulty=excluded.difficulty, question_type=excluded.question_type, target_exam=excluded.target_exam,"
    " in_syllabus=excluded.in_syllabus, has_solution=excluded.has_solution, is_question=excluded.is_question,"
    " fingerprint=excluded.fingerprint, token_len=excluded.token_len, structured=excluded.structured,"
    " embedding=excluded.embedding, embed_model=excluded.embed_model, text_hash=excluded.text_hash,"
    " synced_at=excluded.synced_at"
)
CONTENT_TEMPLATE = "(" + ",".join(["%s"] * 23) + ",%s::jsonb,%s::vector,%s,%s,now())"


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
        it["heading"], it["core_text"], it["concept_id"], it["concept_id"],
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


# source_book prefix -> (family, kind). First match wins; anything else is "other".
SOURCE_FAMILIES = (
    ("NCERT", "ncert", "book"), ("H.C. Verma", "hc-verma", "book"), ("OpenStax", "openstax", "book"),
    ("ALLEN", "allen-handbook", "book"), ("Aeropt", "aeropt-notes", "notes"),
    ("Coaching test paper", "coaching-papers", "exam"), ("JEE Official Exam", "jee-challenger", "exam"),
    ("JEE Advanced", "jee-advanced-official", "exam"), ("JEE Adv ", "jee-advanced-official", "exam"),
)


def source_row(name: str) -> tuple[str, str, str, str]:
    family, kind = next(((f, k) for prefix, f, k in SOURCE_FAMILIES if name.startswith(prefix)), ("other", "book"))
    return (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:80], name, family, kind)


def answer_for(it: dict) -> dict | None:
    """The structured official answer: the source's own, else the key line of the solution text.

    The key line is read only for questions that have options, where "Answer: C" means option C;
    on a numeric question the same words would name a number.
    """
    st = it.get("structured")
    given = st.get("answer") if isinstance(st, dict) else None
    if given:
        return given
    if not (isinstance(st, dict) and st.get("options")):
        return None
    from services.answer_key import parse_key

    key = parse_key(it.get("solution") or "")
    return {"kind": "letters", "letters": "".join("ABCD"[i - 1] for i in sorted(key))} if key else None


def sync_sources_solutions(cur, items) -> tuple[int, int]:
    """Fill sources and the private solutions table from this build, and point every item at its source."""
    from psycopg2.extras import Json, execute_values

    rows = sorted({source_row(it["source_book"]) for it in items if it.get("source_book")})
    execute_values(cur, "insert into public.sources (source_id, name, family, kind) values %s"
                   " on conflict (source_id) do update set name=excluded.name, family=excluded.family,"
                   " kind=excluded.kind", rows)
    cur.execute("update public.content c set source_id = s.source_id from public.sources s"
                " where s.name = c.source_book and c.source_id is distinct from s.source_id")
    # The licence text each source's items carry: evidence for a later rights decision, not one.
    cur.execute("update public.sources s set license = x.license from ("
                " select source_id, mode() within group (order by license) as license"
                " from public.content where license is not null group by 1) x"
                " where x.source_id = s.source_id and s.license is distinct from x.license")

    sols = []
    for it in items:
        if not it.get("is_question"):
            continue
        answer = answer_for(it)
        text = (it.get("solution") or "").strip() or None
        if text or answer:
            sols.append((it["content_id"], "worked" if text else "answer", text, Json(answer) if answer else None))
    ids = [it["content_id"] for it in items if it.get("is_question")]
    cur.execute("delete from knowledge_private.solutions where question_id = any(%s)"
                " and not (question_id = any(%s))", (ids, [r[0] for r in sols] or [""]))
    for start in range(0, len(sols), 1000):
        execute_values(cur, "insert into knowledge_private.solutions (question_id, kind, worked_solution, answer)"
                       " values %s on conflict (question_id) do update set kind=excluded.kind,"
                       " worked_solution=excluded.worked_solution, answer=excluded.answer",
                       sols[start:start + 1000])
    detach_fts_from_solution(cur)
    # Every solution is in the private table now; the copy on content is no longer read.
    cur.execute("alter table public.content drop column if exists solution")
    refresh_fts(cur)
    return len(rows), len(sols)


# Keyword search weights: heading A, body B, and the first 2,000 characters of the solution at the
# lowest weight D (a question is still found by words that only its worked solution uses).
_FTS_SQL = (
    "setweight(to_tsvector('english', coalesce({c}.heading, '')), 'A') ||"
    " setweight(to_tsvector('english', coalesce({c}.core_text, '')), 'B') ||"
    " setweight(to_tsvector('english', left(coalesce(sol.worked_solution, ''), 2000)), 'D')"
)


def detach_fts_from_solution(cur) -> None:
    """`fts` was generated from content.solution, which pins the column. Make it an ordinary column the
    build fills from the private solutions table (same weights), so that column can go."""
    cur.execute("select attgenerated from pg_attribute where attrelid = 'public.content'::regclass and attname = 'fts'")
    row = cur.fetchone()
    if row and row[0] == "s":
        cur.execute("alter table public.content drop column fts")  # its GIN index goes with it
        cur.execute("alter table public.content add column fts tsvector")


def refresh_fts(cur) -> int:
    """Recompute the full-text vector wherever it differs, then (re)create its index."""
    expr = _FTS_SQL.format(c="c2")
    cur.execute(
        "update public.content c set fts = n.v from ("
        f" select c2.content_id, ({expr}) as v from public.content c2"
        " left join knowledge_private.solutions sol on sol.question_id = c2.content_id) n"
        " where n.content_id = c.content_id and c.fts is distinct from n.v"
    )
    changed = cur.rowcount
    cur.execute("create index if not exists ix_content_fts on public.content using gin (fts)")
    return changed


def sync_ingest_issues(cur, issues: list[dict]) -> int:
    """Replace the build's drop/rejection log. Kinds this build reports are rewritten; others are kept."""
    from psycopg2.extras import execute_values

    kinds = sorted({i["kind"] for i in issues}) or ["number_table", "duplicate", "structure_rejected"]
    cur.execute("delete from public.ingest_issues where kind = any(%s)", (kinds,))
    if issues:
        execute_values(cur, "insert into public.ingest_issues (kind, content_id, source, detail) values %s",
                       [(i["kind"], i.get("content_id"), i.get("source"), i.get("detail")) for i in issues],
                       page_size=1000)
    return len(issues)


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
            links.append((it["content_id"], it["concept_id"], "primary",
                          it.get("mapping_method") or "embedding", it.get("mapping_score")))
        scores = it.get("secondary_scores") or []
        for n, s in enumerate(it["secondary_concepts"]):
            if s:
                links.append((it["content_id"], s, "secondary", "embedding", scores[n] if n < len(scores) else None))
    execute_values(
        cur, "insert into public.chunk_concepts (content_id, concept_id, role, method, confidence) values %s"
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
    # Chapters are only ever upserted, so a chapter the build no longer produces (the OpenStax
    # chapters merged into JEE units) stayed as an empty row. Drop those nothing points at.
    cur.execute(
        "delete from public.chapters h where subject = any(%s)"
        " and not exists (select 1 from public.content c where c.chapter_id = h.chapter_id)"
        " and not exists (select 1 from public.concepts k where k.chapter_id = h.chapter_id)"
        " and not exists (select 1 from public.syllabus_topics s where s.chapter_id = h.chapter_id)",
        (subjects,),
    )
    return {"content": gone_content, "concepts": gone_concepts, "chapters": cur.rowcount}


def finalize(cur, items, n_concepts: int) -> None:
    """Record what the corpus was built with, then make the model CHECK constraints binding."""
    from psycopg2.extras import execute_values

    # Rows pointing at a concept or chapter that no build produces any more. replace_edges only
    # clears edges whose source it is rewriting, so an edge from a removed concept outlived it.
    cur.execute("delete from public.concept_edges e where e.concept_id = e.requires"
                " or not exists (select 1 from public.concepts k where k.concept_id = e.concept_id)"
                " or not exists (select 1 from public.concepts k where k.concept_id = e.requires)")
    cur.execute("delete from public.chunk_concepts x where not exists"
                " (select 1 from public.concepts k where k.concept_id = x.concept_id)")
    # 023 adds its constraints NOT VALID; with the corpus rewritten, every row must now pass.
    cur.execute("set constraints all immediate")
    cur.execute("select conrelid::regclass::text, conname from pg_constraint"
                " where not convalidated and conname = any(%s)", (INTEGRITY_CONSTRAINTS,))
    for table, name in cur.fetchall():
        cur.execute(f"alter table {table} validate constraint {name}")

    cur.execute("select count(*) from public.content")
    total_content = cur.fetchone()[0]
    cur.execute("select count(*) from public.concepts")
    total_concepts = cur.fetchone()[0]

    meta = {
        "embed_model": MODEL_ID,
        "embed_dim": str(DIM),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "content_rows": str(total_content),
        "concept_rows": str(total_concepts),
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


def purge_unreadable(cur) -> int:
    """Drop passages that match topic words and teach nothing: exercise banners and handbook furniture."""
    where = """
        (content_type = 'Theory' and length(core_text) < 400 and core_text ~* 'exercise')
        or (source_book ilike 'ALLEN%%' and (
              length(core_text) < 180
              or core_text ~* 'due care and diligence|shall not hold any responsibility'))
    """
    cur.execute(f"delete from public.chunk_concepts where content_id in (select content_id from public.content where {where})")
    cur.execute(f"delete from public.content where {where}")
    return cur.rowcount


def relink_topics(url: str, *, allow_hosted: bool, subject: str, dry_run: bool) -> int:
    """Upsert authored notes and rebuild syllabus-topic links for one subject, or all three.

    Does not re-embed the corpus. Textbook rows stay; only note rows and role='syllabus' links move.
    """
    from local_ingest.corpus import reference_items
    from local_ingest.reference_notes import REFERENCE_BOOK
    from local_ingest.syllabus import Chapter, Concept
    from retrieval import embedder

    conn = connect(url, allow_hosted)
    try:
        cur = conn.cursor()
        cur.execute("set statement_timeout = '180s'")
        ensure_schema(cur)
        removed = purge_unreadable(cur)
        print(f"removed unreadable passages: {removed}")

        cur.execute(
            "select chapter_id, title, coalesce(volume,''), coalesce(unit,''), coalesce(jee_unit,''),"
            " coalesce(in_syllabus, true), coalesce(ord, 0), coalesce(subject, 'Physics')"
            " from public.chapters"
        )
        chapters = [
            Chapter(chapter_id=r[0], title=r[1], volume=r[2], unit=r[3], jee_unit=r[4],
                    in_syllabus=bool(r[5]), order=int(r[6]), subject=r[7])
            for r in cur.fetchall()
        ]
        lookup = {c.chapter_id: c for c in chapters}
        notes = reference_items(lookup)
        cur.execute("select content_id from public.content where source_book = %s", (REFERENCE_BOOK,))
        have = {row[0] for row in cur.fetchall()}
        fresh = [note for note in notes if note["content_id"] not in have]
        embedder.snapshot_static_model()
        if fresh:
            vecs = embedder.encode([f"{n['heading']}\n{n['core_text'][:1200]}" for n in fresh])
            written, _unchanged = upsert_content(cur, fresh, vecs, embedder.model_name())
            print(f"reference notes inserted: {written}")
        else:
            print("reference notes inserted: 0")

        subjects = [subject.capitalize()] if subject else ["Physics", "Chemistry", "Mathematics"]
        from local_ingest import pg_syllabus_topics
        from psycopg2.extras import execute_values

        for subj in subjects:
            cur.execute(
                "select concept_id, concept_name, coalesce(definition,''), chapter_id, coalesce(chapter,''),"
                " coalesce(unit,''), coalesce(in_syllabus, true), coalesce(ord, 0), aliases, coalesce(source,''),"
                " subject from public.concepts where subject = %s and coalesce(source,'') <> 'syllabus'",
                (subj,),
            )
            concepts = []
            for row in cur.fetchall():
                aliases = row[8] if isinstance(row[8], list) else json.loads(row[8] or "[]")
                concepts.append(Concept(
                    concept_id=row[0], name=row[1], definition=row[2], chapter_id=row[3], chapter=row[4],
                    unit=row[5], in_syllabus=bool(row[6]), module_id="", order=int(row[7]),
                    aliases=aliases, source=row[9] or "glossary", subject=row[10],
                ))
            cur.execute(
                "select content_id, coalesce(primary_concept_id, '') from public.content where subject = %s",
                (subj,),
            )
            content_rows = cur.fetchall()
            ids = [r[0] for r in content_rows]
            secondary: dict[str, list[str]] = {}
            if ids:
                cur.execute(
                    "select content_id, concept_id from public.chunk_concepts"
                    " where role = 'secondary' and content_id = any(%s)",
                    (ids,),
                )
                for content_id, concept_id in cur.fetchall():
                    secondary.setdefault(content_id, []).append(concept_id)
            items = [
                {"content_id": content_id, "concept_id": concept_id, "secondary_concepts": secondary.get(content_id, [])}
                for content_id, concept_id in content_rows
            ]
            subj_chapters = [c for c in chapters if c.subject == subj]
            res = pg_syllabus_topics.apply(
                cur, concepts, subj_chapters, items, embedder.encode, subject=subj,
            )
            if res["chapters"]:
                upsert_chapters(cur, [(c["chapter_id"], c["subject"], c["title"], c["volume"], c["unit"],
                                       c["jee_unit"], c["in_syllabus"], c["ord"]) for c in res["chapters"]])
            if res["concepts"]:
                upsert_concepts(cur, [concept_tuple(c, c["embedding"], embedder.model_name()) for c in res["concepts"]])
            if res["links"]:
                execute_values(
                    cur, "insert into public.chunk_concepts (content_id, concept_id, role) values %s"
                    " on conflict (content_id, concept_id) do nothing",
                    [(a, b, "syllabus") for a, b in res["links"]], page_size=1000,
                )
            execute_values(
                cur, "insert into public.syllabus_topics (subject, unit, unit_ord, topic, topic_ord,"
                " concept_id, chapter_id, created, material) values %s", res["topic_rows"],
            )
            thin = [f"{row[3]} ({row[8]})" for row in res["topic_rows"] if row[8] < 3]
            print(f"  still under 3 passages: {thin or 'none'}")

        if dry_run:
            conn.rollback()
            print("dry run: rolled back, nothing written")
        else:
            conn.commit()
        return 0
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--allow-hosted", action="store_true")
    ap.add_argument("--subject")
    ap.add_argument("--skip-topics", action="store_true")
    ap.add_argument("--relink", action="store_true",
                    help="re-attach syllabus topics and add reference notes, without rebuilding the corpus")
    ap.add_argument("--fetch", action="store_true")
    args = ap.parse_args()

    url = os.getenv("RETRIEVAL_DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("Set RETRIEVAL_DATABASE_URL to the Postgres connection string of the retrieval database.")

    from local_ingest import corpus as corpus_builder
    from local_ingest import syllabus as syllabus_builder
    from retrieval import embedder

    if args.relink:
        return relink_topics(url, allow_hosted=args.allow_hosted, subject=args.subject or "", dry_run=args.dry_run)

    if args.fetch:
        from local_ingest.fetch_openstax import fetch_all

        fetch_all()

    t0 = time.time()
    embedder.snapshot_static_model()
    syllabus = syllabus_builder.build()
    print(f"syllabus: {len(syllabus['chapters'])} chapters, {len(syllabus['concepts'])} concepts "
          f"({time.time() - t0:.1f}s)")
    items, item_vecs, concept_vecs = corpus_builder.build_items(syllabus)
    syllabus["requires"] = corpus_builder.refine_edges(syllabus, concept_vecs)
    # After mapping: items were matched within their narrow source chapter, now filed by JEE unit.
    before = [c.concept_id for c in syllabus["concepts"]]
    remap = syllabus_builder.merge_to_jee_chapters(syllabus, items)
    kept = {c.concept_id for c in syllabus["concepts"]}
    concept_vecs = concept_vecs[[i for i, cid in enumerate(before) if cid in kept]]
    print(f"chapters: {len(set(remap))} -> {len(syllabus['chapters'])} (one per JEE unit); "
          f"concepts dropped with out-of-syllabus chapters: {len(before) - len(kept)}")
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
        n_src, n_sol = sync_sources_solutions(cur, items)
        print(f"sources {n_src}, solutions {n_sol}")
        links = replace_links(cur, items)
        edges = replace_edges(cur, syllabus)
        n_issues = sync_ingest_issues(cur, corpus_builder.build_issues())
        print(f"concepts written {c_written}/{len(concepts)}; content written {written}, unchanged {unchanged}; "
              f"links {links}, edges {edges}, build issues logged {n_issues}")

        concept_ids = {c.concept_id for c in concepts}
        if not args.skip_topics:
            from local_ingest import pg_syllabus_topics
            from psycopg2.extras import execute_values

            target_subjects = [args.subject.capitalize()] if args.subject else subjects
            for subj in target_subjects:
                subj_concepts = [c for c in concepts if c.subject == subj]
                subj_chapters = [c for c in syllabus["chapters"] if c.subject == subj]
                subj_items = [it for it in items if it["subject"] == subj]
                res = pg_syllabus_topics.apply(cur, subj_concepts, subj_chapters,
                                               subj_items, embedder.encode, subject=subj)

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

        # Links the syllabus-topic pass added are matched on the topic's words.
        cur.execute("update public.chunk_concepts set method = 'syllabus_text' where role = 'syllabus' and method is null")

        if args.prune:
            print("pruned:", prune(cur, subjects, items, concept_ids))
        finalize(cur, items, len(concept_ids))
        # The foreign keys are deferred to commit; check them now, so a dry run that would
        # leave a dangling reference fails here instead of only the real push failing.
        cur.execute("set constraints all immediate")
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
