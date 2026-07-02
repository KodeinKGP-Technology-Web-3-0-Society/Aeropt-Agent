"""Run once to create the vector index on Content nodes."""
from db.neo4j_client import graph_db

cypher = (
    "CREATE VECTOR INDEX content_vector_index IF NOT EXISTS "
    "FOR (c:Content) ON (c.embedding) "
    "OPTIONS {indexConfig: {"
    "`vector.dimensions`: 3072, "
    "`vector.similarity_function`: 'cosine'"
    "}}"
)

with graph_db.driver.session() as s:
    s.run(cypher)
    print("Vector index created (or already exists).")

graph_db.close()
