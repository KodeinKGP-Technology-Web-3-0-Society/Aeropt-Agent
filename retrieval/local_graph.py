"""A Neo4j-shaped client backed by the local index.

Four call sites outside the retriever talk to the graph directly — follow-up
suggestions, `generate_suggestions`, `search_knowledge_graph` and quiz step
decomposition. With `RETRIEVAL_BACKEND=local` there is no Neo4j running, so rather
than teach each of them a second data access style, this object answers the same
method calls from SQLite.

`vector_search` is the one method whose contract genuinely differs: the hosted one
takes a 3072-d Gemini vector, this one embeds locally. Pass `query_text` and it does
the right thing; pass a vector of the wrong width and it says so instead of returning
silent nonsense.

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
        marks = ",".join("?" * len(seen_ids)) if seen_ids else ""
        sql = """
            SELECT cc.content_id AS content_id, c.difficulty AS difficulty,
                   c.content_type AS content_type
            FROM chunk_concepts cc
            JOIN chunks c ON c.content_id = cc.content_id
            WHERE cc.concept_id = ?
              AND (c.difficulty IS NULL OR c.difficulty <= ?)
        """
        params: List[Any] = [concept_id, difficulty_max]
        if seen_ids:
            sql += f" AND cc.content_id NOT IN ({marks})"
            params.extend(seen_ids)
        sql += " GROUP BY cc.content_id ORDER BY c.difficulty ASC, c.content_type ASC LIMIT ?"
        params.append(limit)
        return [r["content_id"] for r in store.corpus().execute(sql, params)]

    # ── Vector similarity ─────────────────────────────────────────────────────
    def vector_search(
        self,
        query_vector: Optional[Sequence[float]] = None,
        top_k: int = 5,
        candidate_ids: Optional[Sequence[str]] = None,
        query_text: str = "",
    ) -> List[Dict[str, Any]]:
        dim = int(store.vectors().shape[1])
        if query_text:
            qvec = embedder.encode_one(query_text)
        elif query_vector is not None and len(query_vector) == dim:
            vec = np.asarray(query_vector, dtype=np.float32)
            norm = float(np.linalg.norm(vec)) or 1.0
            qvec = vec / norm
        else:
            raise ValueError(
                f"local vector_search needs query_text, or a {dim}-d vector from "
                f"retrieval.embedder (got {0 if query_vector is None else len(query_vector)}-d). "
                "Hosted Gemini vectors are not comparable with the local index."
            )

        conn = store.corpus()
        if candidate_ids:
            ids = list(candidate_ids)
            rows: List[Dict[str, Any]] = []
            for start in range(0, len(ids), 400):
                batch = ids[start : start + 400]
                marks = ",".join("?" * len(batch))
                rows.extend(
                    dict(r)
                    for r in conn.execute(
                        f"SELECT content_id, vec_row FROM chunks WHERE content_id IN ({marks})",
                        tuple(batch),
                    )
                )
        else:
            rows = [dict(r) for r in conn.execute("SELECT content_id, vec_row FROM chunks")]
        if not rows:
            return []
        mat = store.vectors()[[int(r["vec_row"]) for r in rows]]
        scores = mat @ qvec
        order = np.argsort(-scores)[:top_k]
        return [{"content_id": rows[i]["content_id"], "score": float(scores[i])} for i in order]

    def ping(self) -> bool:
        return store.index_exists()

    def close(self) -> None:  # parity with the driver-backed client
        return None


graph = LocalGraphClient()


def graph_client():
    """The graph client for the active backend."""
    import config

    if config.local_retrieval():
        return graph
    from db.neo4j_client import graph_db

    return graph_db
