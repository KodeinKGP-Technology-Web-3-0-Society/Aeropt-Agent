"""
Neo4j read-only client for the JEE Tutor Agent.

The graph is populated offline during ingestion; this module NEVER writes to it.
All traversals stay within the Concept → REQUIRES and Content → COVERS subgraph.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase, Driver
from neo4j.exceptions import ServiceUnavailable, SessionExpired

import config

# ── Target-exam normalisation ─────────────────────────────────────────────────
_EXAM_ALLOWLIST: Dict[str, List[str]] = {
    "JEE_ADVANCED": ["Jee Advanced", "Jee main and Advanced"],
    "JEE_MAINS":    ["Jee main",     "Jee main and Advanced"],
}

def _allowed_exams(target_exam: str) -> List[str]:
    return _EXAM_ALLOWLIST.get(target_exam.upper(), list(_EXAM_ALLOWLIST["JEE_ADVANCED"]))


# ── Content-type normalisation ────────────────────────────────────────────────
# Concept Resolver outputs: theory | example | practice | pyq
# Neo4j Content nodes store: Theory | Solved Example | Practice Question | PYQ
_CONTENT_TYPE_MAP: Dict[str, List[str]] = {
    "theory":   ["Theory"],
    "example":  ["Solved Example"],
    "practice": ["Practice Question"],
    "pyq":      ["PYQ"],
}

def _allowed_types(content_type: str) -> List[str]:
    """Map resolver label → actual DB values. Returns all types if label unknown."""
    return _CONTENT_TYPE_MAP.get(content_type.lower(), list(_CONTENT_TYPE_MAP.keys()))


class Neo4jClient:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self.driver: Driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            # Connection resilience — Neo4j Aura drops idle connections after ~30 min.
            # liveness_check_timeout ensures the driver tests the connection before
            # reusing it from the pool, avoiding "defunct connection" errors.
            max_connection_lifetime=1200,       # retire connections after 20 min
            max_connection_pool_size=10,
            connection_acquisition_timeout=30,
            liveness_check_timeout=10,
            # Suppress DEPRECATION notifications (db.index.vector.queryNodes).
            notifications_disabled_classifications=["DEPRECATION"],
        )

    def close(self) -> None:
        self.driver.close()

    def _run(
        self, cypher: str, params: Optional[Dict[str, Any]] = None, retries: int = 3
    ) -> List[Dict[str, Any]]:
        """Execute a read query with automatic retry on transient connection errors."""
        params = params or {}
        last_exc: Exception = RuntimeError("No attempts made")
        for attempt in range(retries):
            try:
                with self.driver.session() as session:
                    result = session.run(cypher, **params)
                    return [dict(r) for r in result]
            except (ServiceUnavailable, SessionExpired, OSError) as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1))   # 1.5s, 3s back-off
        raise last_exc

    # ─────────────────────── Concept / REQUIRES ───────────────────────────────

    def get_prerequisite_concepts(
        self,
        concept_id: str,
        max_depth: int = 2,
    ) -> List[Dict[str, Any]]:
        """
        Follow OUTGOING REQUIRES edges: (target)-[:REQUIRES*1..N]->(prereq)
        = what the target concept depends on (its actual prerequisites).

        Returns [{concept_id, hop_count}] sorted closest first.
        2 hops covers direct prereqs + one level deeper — the pedagogically
        meaningful range without pulling in overly foundational concepts.
        """
        cypher = """
        MATCH path = (target:Concept {concept_id: $concept_id})
                     -[:REQUIRES*1..%d]->
                     (prereq:Concept)
        WITH prereq, min(length(path)) AS hop_count
        RETURN prereq.concept_id AS concept_id, hop_count
        ORDER BY hop_count ASC
        """ % max_depth
        return self._run(cypher, {"concept_id": concept_id})

    def get_requires_chain_for_quiz(
        self,
        concept_id: str,
        max_depth: int = 2,
    ) -> List[Dict[str, Any]]:
        """Leaf-first ordering of prerequisites for quiz step decomposition."""
        cypher = """
        MATCH path = (target:Concept {concept_id: $concept_id})
                     -[:REQUIRES*1..%d]->
                     (prereq:Concept)
        WITH prereq, min(length(path)) AS depth
        RETURN prereq.concept_id AS concept_id, depth
        ORDER BY depth DESC
        """ % max_depth
        return self._run(cypher, {"concept_id": concept_id})

    # ─────────────────────── Content / COVERS ─────────────────────────────────

    def get_content_ids_for_concept(
        self,
        concept_id: str,
        content_type: Optional[str] = None,
        difficulty_max: Optional[int] = None,
        target_exam: Optional[str] = None,
        limit: int = 50,
        ignore_content_type: bool = False,
    ) -> List[str]:
        """
        Return content_ids of Content nodes that COVER this concept.

        content_type is normalised from resolver labels (e.g. "example") to exact
        DB values (e.g. "Solved Example") via _CONTENT_TYPE_MAP.

        Set ignore_content_type=True for the fallback pass when the typed filter
        returns 0 candidates — retrieves all content types for the concept.
        """
        filters = ["c.concept_id = $concept_id"]
        params: Dict[str, Any] = {"concept_id": concept_id, "limit": limit}

        if content_type and not ignore_content_type:
            allowed_types = _allowed_types(content_type)
            filters.append("content.content_type IN $allowed_types")
            params["allowed_types"] = allowed_types

        if difficulty_max is not None:
            filters.append("content.difficulty <= $difficulty_max")
            params["difficulty_max"] = difficulty_max

        if target_exam:
            allowed_exams = _allowed_exams(target_exam)
            filters.append(
                "(content.target_exam IN $allowed_exams OR content.target_exam IS NULL)"
            )
            params["allowed_exams"] = allowed_exams

        where_clause = " AND ".join(filters)
        cypher = f"""
        MATCH (content:Content)-[:COVERS]->(c:Concept)
        WHERE {where_clause}
        RETURN content.content_id AS content_id
        LIMIT $limit
        """
        rows = self._run(cypher, params)
        return [r["content_id"] for r in rows]

    def get_content_ids_for_follow_up(
        self,
        concept_id: str,
        seen_ids: List[str],
        difficulty_max: int = 3,
        limit: int = 10,
    ) -> List[str]:
        """Content for Follow-Up: same concept, unseen, capped difficulty."""
        cypher = """
        MATCH (content:Content)-[:COVERS]->(c:Concept {concept_id: $concept_id})
        WHERE NOT content.content_id IN $seen_ids
          AND (content.difficulty IS NULL OR content.difficulty <= $difficulty_max)
        RETURN content.content_id AS content_id
        LIMIT $limit
        """
        rows = self._run(cypher, {
            "concept_id": concept_id,
            "seen_ids": seen_ids,
            "difficulty_max": difficulty_max,
            "limit": limit,
        })
        return [r["content_id"] for r in rows]

    # ─────────────────────── Vector similarity search ─────────────────────────

    def vector_search(
        self,
        query_vector: List[float],
        top_k: int = config.NEO4J_VECTOR_TOP_K,
        candidate_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search over content_vector_index, scoped to candidate_ids.

        Key design decision:
        When candidate_ids is provided, we must set fetch_k >= total content
        node count (639) so that ALL candidates are present in the YIELD result
        before the WHERE filter is applied.  The previous approach of
        fetch_k = top_k * 4 = 20 caused candidates ranked below position 20
        globally to be silently dropped, producing retrieved_chunks = [].

        fetch_k = CONTENT_CORPUS_SIZE (700 > 639 total nodes) guarantees no
        candidate is missed.  At 639 nodes this is negligible overhead.

        NOTE: db.index.vector.queryNodes is deprecated (replaced by SEARCH in
        Neo4j 5.25+).  Deferring migration until Aura upgrades the instance.
        """
        if candidate_ids:
            # Fetch full corpus so no graph-filtered candidate is missed.
            fetch_k = config.NEO4J_CONTENT_CORPUS_SIZE
            cypher = """
            CALL db.index.vector.queryNodes($index_name, $fetch_k, $query_vector)
            YIELD node, score
            WHERE node.content_id IN $candidate_ids
            RETURN node.content_id AS content_id, score
            ORDER BY score DESC
            LIMIT $top_k
            """
            return self._run(cypher, {
                "index_name": config.NEO4J_VECTOR_INDEX,
                "fetch_k": fetch_k,
                "query_vector": query_vector,
                "candidate_ids": candidate_ids,
                "top_k": top_k,
            })
        else:
            # No candidate filter — standard top-k global search.
            cypher = """
            CALL db.index.vector.queryNodes($index_name, $top_k, $query_vector)
            YIELD node, score
            RETURN node.content_id AS content_id, score
            ORDER BY score DESC
            """
            return self._run(cypher, {
                "index_name": config.NEO4J_VECTOR_INDEX,
                "top_k": top_k,
                "query_vector": query_vector,
            })

    # ─────────────────────── Health check ─────────────────────────────────────

    def ping(self) -> bool:
        try:
            self._run("RETURN 1")
            return True
        except Exception:
            return False


# ── Singleton ─────────────────────────────────────────────────────────────────
graph_db = Neo4jClient(config.NEO4J_URI, config.NEO4J_USER, config.NEO4J_PASSWORD)
