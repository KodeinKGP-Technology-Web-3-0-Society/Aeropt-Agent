"""Create content_vector_index on the connected Neo4j.

Refuses Aura / hosted URIs so a local rebuild cannot drop the deployed index.
"""
from __future__ import annotations

import config
from local_ingest.guard import assert_local_neo4j
from db.neo4j_client import graph_db

assert_local_neo4j(config.NEO4J_URI)

DIM = 3072
cypher = (
    "CREATE VECTOR INDEX content_vector_index IF NOT EXISTS "
    "FOR (c:Content) ON (c.embedding) "
    "OPTIONS {indexConfig: {"
    f"`vector.dimensions`: {DIM}, "
    "`vector.similarity_function`: 'cosine'"
    "}}"
)

with graph_db.driver.session() as s:
    s.run("DROP INDEX content_vector_index IF EXISTS")
    s.run(cypher)
    print(f"Vector index created at {DIM} dimensions on {config.NEO4J_URI}.")

graph_db.close()
