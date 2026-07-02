"""Quick test — run to confirm the deprecation warning is gone."""
from db.neo4j_client import graph_db

with graph_db.driver.session() as s:
    s.run(
        "CALL db.index.vector.queryNodes($idx, 1, $vec) YIELD node RETURN node.content_id LIMIT 1",
        idx="content_vector_index",
        vec=[0.0] * 3072,
    )

print("Done — no deprecation warning should appear above this line.")
graph_db.close()
