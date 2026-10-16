"""The graph client: the Neo4j-shaped method surface, answered from the retrieval Postgres.

Four call sites outside the retriever talk to the graph directly — follow-up
suggestions, `generate_suggestions`, `search_knowledge_graph` and quiz step
decomposition. They keep their method calls; the data comes from the Supabase
retrieval database through retrieval/store.py (the Aura graph is no longer used).

`vector_search` embeds with retrieval.embedder (the corpus's model). Pass `query_text`;
a vector of another width is refused instead of returning silent nonsense.

    from retrieval.local_graph import graph
    graph.get_content_ids_for_concept("torque", content_type="practice")
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from retrieval import embedder, search, store

_CONTENT_TYPES = search.TYPE_FAMILIES


class LocalGraphClient:
    """Read-only, in-process, same method names as `db.neo4j_client.Neo4jClient`."""

    # ── Concept / REQUIRES ────────────────────────────────────────────────────
    def get_prerequisite_concepts(self, concept_id: str, max_depth: int = 2) -> List[Dict[str, Any]]:
        return [
            {"concept_id": row["concept_id"], "hop_count": row["hop_count"]}
            for row in store.prerequisites(concept_id, max_depth=max_depth)
        ]

    def get_requires_chain_for_quiz(self, concept_id: str, max_depth: int = 2) -> List[Dict[str, Any]]:
        rows = store.prerequisites(concept_id, max_depth=max_depth)
        rows.sort(key=lambda r: -r["hop_count"])  # leaf-first, as the quiz expects
        return [{"concept_id": r["concept_id"], "depth": r["hop_count"]} for r in rows]

    # ── Content / COVERS ──────────────────────────────────────────────────────
    def get_content_ids_for_concept(
        self,
        concept_id: str,
        content_type: Optional[str] = None,
        difficulty_max: Optional[int] = None,
        target_exam: Optional[str] = None,
        limit: int = 50,
        ignore_content_type: bool = False,
    ) -> List[str]:
        types = (
            tuple(_CONTENT_TYPES["any"])
            if ignore_content_type or not content_type
            else tuple(_CONTENT_TYPES.get(content_type.lower(), _CONTENT_TYPES["any"]))
        )
        rows = search._fetch_candidates(
            [concept_id],
            types,
            target_exam=target_exam or "",
            in_syllabus_only=False,
            limit=limit,
        )
        if difficulty_max is not None:
            rows = [r for r in rows if (r["difficulty"] or 0) <= difficulty_max]
        return [r["content_id"] for r in rows[:limit]]

    def get_content_ids_for_follow_up(
        self,
        concept_id: str,
        seen_ids: Sequence[str],
        difficulty_max: int = 3,
        limit: int = 10,
    ) -> List[str]:
        rows = store.query(
            """
            select cc.content_id from public.chunk_concepts cc
            join public.content c on c.content_id = cc.content_id
            where cc.concept_id = %(c)s
              and (c.difficulty is null or c.difficulty <= %(d)s)
              and not (cc.content_id = any(%(seen)s))
            group by cc.content_id, c.difficulty, c.content_type
            order by c.difficulty asc nulls first, c.content_type asc
            limit %(n)s
            """,
            {"c": concept_id, "d": difficulty_max, "seen": list(seen_ids or []), "n": limit},
        )
        return [r["content_id"] for r in rows]

    # ── Vector similarity ─────────────────────────────────────────────────────
    def vector_search(
        self,
        query_vector: Optional[Sequence[float]] = None,
        top_k: int = 5,
        candidate_ids: Optional[Sequence[str]] = None,
        query_text: str = "",
    ) -> List[Dict[str, Any]]:
        if query_text:
            qvec = embedder.encode_one(query_text)
        elif query_vector is not None and len(query_vector) == embedder.DIM:
            vec = np.asarray(query_vector, dtype=np.float32)
            qvec = vec / (float(np.linalg.norm(vec)) or 1.0)
        else:
            raise embedder.EmbeddingMismatch(
                f"vector_search needs query_text, or a {embedder.DIM}-d vector from retrieval.embedder "
                f"(got {0 if query_vector is None else len(query_vector)}-d)."
            )
        params = {"qv": embedder.to_pg(qvec), "ids": list(candidate_ids or []), "k": top_k}
        if candidate_ids:
            # Exact over the candidates: an ANN index walk followed by the id filter would drop rows.
            sql = """with pool as materialized (
                         select content_id, embedding from public.content
                         where content_id = any(%(ids)s) and embedding is not null)
                     select content_id, 1 - (embedding <=> %(qv)s::vector) as score from pool
                     order by 2 desc limit %(k)s"""
        else:
            sql = """select content_id, 1 - (embedding <=> %(qv)s::vector) as score from public.content
                     where embedding is not null order by embedding <=> %(qv)s::vector limit %(k)s"""
        rows = store.query(sql, params)
        return [{"content_id": r["content_id"], "score": float(r["score"])} for r in rows]

    def ping(self) -> bool:
        return store.index_exists()

    def close(self) -> None:  # parity with the driver-backed client
        return None


graph = LocalGraphClient()


def graph_client():
    """The graph client. One backend: the retrieval Postgres."""
    return graph
