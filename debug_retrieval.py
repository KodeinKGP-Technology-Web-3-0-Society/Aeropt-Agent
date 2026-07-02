"""
Step-by-step retrieval diagnostic.
Run: python debug_retrieval.py
"""
import sys
from db.supabase_client import db
from db.neo4j_client import graph_db, _allowed_types, _allowed_exams
from agents.graphrag_retriever import _embed_query

SEP = "─" * 60

# ── CONFIG — edit these to match your test query ──────────────────────────────
TEST_QUERY = "provide me a solved example based on Newton's second law"
USER_ID    = None   # set to your user UUID if you want concept-state info
TARGET_EXAM = "JEE_ADVANCED"

print(SEP)
print("STEP 0 — Exact target_exam values in Neo4j Content nodes")
print(SEP)
with graph_db.driver.session() as s:
    r = s.run("MATCH (c:Content) RETURN DISTINCT c.target_exam AS te, count(*) AS n ORDER BY n DESC")
    for row in r:
        print(f"  {row['te']!r:40s}  n={row['n']}")

print()
print(SEP)
print("STEP 0b — Exact content_type values in Neo4j Content nodes")
print(SEP)
with graph_db.driver.session() as s:
    r = s.run("MATCH (c:Content) RETURN DISTINCT c.content_type AS ct, count(*) AS n ORDER BY n DESC")
    for row in r:
        print(f"  {row['ct']!r:40s}  n={row['n']}")

print()
print(SEP)
print("STEP 1 — Concept Resolver (manual test)")
print(SEP)
from agents.concept_resolver import concept_resolver_node
from models.state import TutorState

state: TutorState = {
    "user_id": USER_ID or "00000000-0000-0000-0000-000000000000",
    "session_id": "00000000-0000-0000-0000-000000000000",
    "query": TEST_QUERY,
    "recent_turns": [],
    "target_exam": TARGET_EXAM,
    "preferred_depth": "standard",
    "interaction_count": 1,
    "active_quiz": False,
}

result = concept_resolver_node(state)
primary_id   = result.get("primary_concept_id", "")
secondaries  = result.get("secondary_concept_ids", [])
content_type = result.get("content_type_requested", "")
print(f"  primary_concept_id:     {primary_id!r}")
print(f"  secondary_concept_ids:  {secondaries}")
print(f"  content_type_requested: {content_type!r}")
print(f"  allowed_types mapping:  {_allowed_types(content_type)}")

print()
print(SEP)
print("STEP 2 — Neo4j COVERS traversal (no filters)")
print(SEP)
with graph_db.driver.session() as s:
    r = s.run(
        "MATCH (content:Content)-[:COVERS]->(c:Concept {concept_id: $cid}) "
        "RETURN content.content_id AS cid, content.content_type AS ct, "
        "content.target_exam AS te LIMIT 10",
        cid=primary_id,
    )
    rows = [dict(row) for row in r]
if not rows:
    print(f"  !! COVERS returned 0 results for concept_id={primary_id!r}")
    print("     Check the Concept node exists in Neo4j:")
    with graph_db.driver.session() as s:
        r2 = s.run("MATCH (c:Concept) RETURN c.concept_id LIMIT 10")
        print("     Sample concept_ids in graph:", [row["c.concept_id"] for row in r2])
else:
    for row in rows:
        print(f"  {row['cid']:30s}  type={row['ct']!r:20s}  exam={row['te']!r}")

print()
print(SEP)
print("STEP 3 — content_type filter applied")
print(SEP)
allowed_types = _allowed_types(content_type)
print(f"  Filtering for: {allowed_types}")
with graph_db.driver.session() as s:
    r = s.run(
        "MATCH (content:Content)-[:COVERS]->(c:Concept {concept_id: $cid}) "
        "WHERE content.content_type IN $types "
        "RETURN content.content_id AS cid, content.content_type AS ct LIMIT 10",
        cid=primary_id, types=allowed_types,
    )
    type_filtered = [dict(row) for row in r]
if not type_filtered:
    print(f"  !! 0 results after content_type filter — checking what types exist for this concept:")
    with graph_db.driver.session() as s:
        r2 = s.run(
            "MATCH (content:Content)-[:COVERS]->(c:Concept {concept_id: $cid}) "
            "RETURN DISTINCT content.content_type AS ct, count(*) AS n",
            cid=primary_id,
        )
        for row in r2:
            print(f"     {row['ct']!r}: {row['n']}")
else:
    print(f"  {len(type_filtered)} results after content_type filter:")
    for row in type_filtered[:5]:
        print(f"    {row['cid']:30s}  type={row['ct']!r}")

print()
print(SEP)
print("STEP 4 — target_exam filter applied")
print(SEP)
allowed_exams = _allowed_exams(TARGET_EXAM)
print(f"  Filtering for: {allowed_exams}")
with graph_db.driver.session() as s:
    r = s.run(
        "MATCH (content:Content)-[:COVERS]->(c:Concept {concept_id: $cid}) "
        "WHERE content.content_type IN $types "
        "AND (content.target_exam IN $exams OR content.target_exam IS NULL) "
        "RETURN content.content_id AS cid, content.target_exam AS te LIMIT 10",
        cid=primary_id, types=allowed_types, exams=allowed_exams,
    )
    exam_filtered = [dict(row) for row in r]
if not exam_filtered:
    print("  !! 0 results after target_exam filter — checking what exam values exist for this concept:")
    with graph_db.driver.session() as s:
        r2 = s.run(
            "MATCH (content:Content)-[:COVERS]->(c:Concept {concept_id: $cid}) "
            "RETURN DISTINCT content.target_exam AS te, count(*) AS n",
            cid=primary_id,
        )
        for row in r2:
            print(f"     {row['te']!r}: {row['n']}")
else:
    print(f"  {len(exam_filtered)} results after target_exam filter:")
    for row in exam_filtered[:5]:
        print(f"    {row['cid']:30s}  exam={row['te']!r}")

print()
print(SEP)
print("STEP 5 — get_content_ids_for_concept (full pipeline call)")
print(SEP)
candidate_ids = graph_db.get_content_ids_for_concept(
    primary_id,
    content_type=content_type,
    target_exam=TARGET_EXAM,
    limit=30,
)
print(f"  candidate_ids returned: {len(candidate_ids)}")
print(f"  first 5: {candidate_ids[:5]}")

print()
print(SEP)
print("STEP 6 — Vector search")
print(SEP)
query_vector = _embed_query(TEST_QUERY)
print(f"  query_vector dim: {len(query_vector)}")
vector_results = graph_db.vector_search(
    query_vector=query_vector,
    top_k=5,
    candidate_ids=candidate_ids if candidate_ids else None,
)
print(f"  vector search returned: {len(vector_results)} results")
for r in vector_results:
    print(f"    {r['content_id']:30s}  score={r['score']:.4f}")

print()
print(SEP)
print("STEP 7 — Supabase content fetch")
print(SEP)
top_ids = [r["content_id"] for r in vector_results]
chunks = db.read_content_by_ids(top_ids)
print(f"  read_content_by_ids({top_ids}) → {len(chunks)} rows")
for c in chunks:
    print(f"    {c['content_id']:30s}  type={c.get('content_type')!r}  text_len={len(c.get('core_text') or '')}")

print()
print(SEP)
print("DIAGNOSTIC COMPLETE")
print(SEP)
graph_db.close()
