"""
GraphRAG Retriever

Retrieval order (must not change):
  1. Concept Resolution          (done by Concept Resolver before this node)
  2. Neo4j prerequisite traversal  → what does this concept depend on?
  3. Candidate content filtering   → COVERS graph + exam/type/difficulty filters
  4. Vector search on candidates   → semantic ranking within the candidate set
  5. Supabase text fetch           → full chunk text for top-k results

No LLM chat call — only Gemini embedding API.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from google import genai

import config
from db.supabase_client import db
from db.neo4j_client import graph_db
from models.state import TutorState

_genai_client = genai.Client(api_key=config.GEMINI_API_KEY)


def _embed_query(text: str) -> List[float]:
    result = _genai_client.models.embed_content(
        model=config.EMBEDDING_MODEL,
        contents=text,
    )
    return result.embeddings[0].values


def _compute_prereq_score(prereq_states: List[Dict[str, Any]]) -> float:
    """
    Weighted average prereq proficiency — direct prereqs (hop=1) count more.
    Falls back to 0.5 (neutral) when no prereq data exists.
    """
    if not prereq_states:
        return 0.5
    total_weight = 0.0
    weighted_sum = 0.0
    for s in prereq_states:
        hop = s.get("hop_count", 1)
        weight = 1.0 / hop          # hop-1 prereqs count twice as much as hop-2
        weighted_sum += s.get("proficiency_score", 0.5) * weight
        total_weight += weight
    return weighted_sum / total_weight if total_weight > 0 else 0.5


def _should_use_socratic(
    target_state: Optional[Dict[str, Any]],
    prereq_states: List[Dict[str, Any]],
    socratic_turn_count: int,
) -> bool:
    """
    Section 8 refined routing:
      IF target.is_cold_start == True          → Solver (nothing to draw out yet)
      ELIF prereq_avg > 0.7 AND target < 0.4   → Socratic
      ELIF socratic_turns >= MAX               → force Solver (auto-escalate)
      ELSE                                     → Solver
    """
    if target_state is None:
        return False
    if target_state.get("is_cold_start", True):
        return False

    target_score = target_state.get("proficiency_score", 0.5)
    prereq_score = _compute_prereq_score(prereq_states)

    if prereq_score > config.SOCRATIC_PREREQ_MIN and target_score < config.SOCRATIC_TARGET_MAX:
        if socratic_turn_count >= config.SOCRATIC_MAX_TURNS:
            return False   # auto-escalate
        return True

    return False


def _build_prereq_states(
    prereq_graph: List[Dict[str, Any]],
    user_id: str,
    concept_name_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Enrich graph traversal results with Supabase user_concept_state data.

    Returns list of:
    {
        concept_id:       str,
        concept_name:     str,      # from Supabase concepts table
        proficiency_score: float,
        flagged_weak:     bool,
        is_cold_start:    bool,
        hop_count:        int,      # 1 = direct prereq, 2 = one step removed
    }

    hop_count is used by _compute_prereq_score for weighted averaging and
    can be surfaced to the Solver to explain why certain concepts are flagged.
    """
    results = []
    for p in prereq_graph:
        cid = p["concept_id"]
        hop = p.get("hop_count", 1)
        state = db.read_user_concept_state(user_id, cid)
        entry: Dict[str, Any] = {
            "concept_id": cid,
            "concept_name": concept_name_map.get(cid, cid),
            "proficiency_score": state.get("proficiency_score", 0.5) if state else 0.5,
            "flagged_weak": state.get("flagged_weak", False) if state else False,
            "is_cold_start": state.get("is_cold_start", True) if state else True,
            "hop_count": hop,
        }
        results.append(entry)
    return results


def graphrag_retriever_node(state: TutorState) -> Dict[str, Any]:
    user_id = state["user_id"]
    session_id = state["session_id"]
    primary_concept_id = state.get("primary_concept_id", "")
    secondary_concept_ids = state.get("secondary_concept_ids", [])
    content_type = state.get("content_type_requested", "theory")
    query = state["query"]
    target_exam = state.get("target_exam", "JEE_ADVANCED")

    try:
        # ── 1. Load target concept's user state ───────────────────────────────
        target_state = db.read_user_concept_state(user_id, primary_concept_id)

        # ── 2. Neo4j: prerequisite concepts (OUTGOING REQUIRES, max 2 hops) ──
        prereq_graph = graph_db.get_prerequisite_concepts(primary_concept_id, max_depth=2)

        # ── 3. Build concept_name lookup from Supabase concepts table ─────────
        all_concept_ids = [primary_concept_id] + [p["concept_id"] for p in prereq_graph]
        concepts_rows = db.read_concepts()
        concept_name_map = {c["concept_id"]: c["concept_name"] for c in concepts_rows}

        # ── 4. Enrich prereq states (proficiency + hop_count + name) ─────────
        prereq_states = _build_prereq_states(prereq_graph, user_id, concept_name_map)

        # ── 4b. Secondary concept states (same shape as prereq_states) ────────
        secondary_states: List[Dict[str, Any]] = []
        for sec_id in secondary_concept_ids:
            sec_state = db.read_user_concept_state(user_id, sec_id)
            secondary_states.append({
                "concept_id":       sec_id,
                "concept_name":     concept_name_map.get(sec_id, sec_id),
                "proficiency_score": sec_state.get("proficiency_score", 0.5) if sec_state else 0.5,
                "flagged_weak":     sec_state.get("flagged_weak", False)      if sec_state else False,
                "is_cold_start":    sec_state.get("is_cold_start", True)      if sec_state else True,
            })

        # ── 5. Candidate content_ids — graph traversal with type cascade ────────
        #
        # Content type cascade for "example" requests:
        #   Tier 1: Solved Example
        #   Tier 2: Practice Question  (has a stored solution the Solver can use)
        #   Tier 3: PYQ                (also has stored solution)
        #
        # For all other types, a single pass with type filter, then a fallback
        # pass without type filter if too few candidates are found.
        # Secondary concepts are ONLY added if the primary concept itself yields
        # too few candidates — prevents cross-chapter contamination.

        MIN_PRIMARY_CANDIDATES = 3

        _EXAMPLE_CASCADE = ["example", "practice", "pyq"]

        def _get_for_type(ctype: Optional[str], cid: str, limit: int = 30) -> List[str]:
            return graph_db.get_content_ids_for_concept(
                cid,
                content_type=ctype,
                target_exam=target_exam,
                limit=limit,
                ignore_content_type=(ctype is None),
            )

        candidate_ids: List[str] = []

        if content_type == "example":
            # Cascade through Solved Example → Practice Question → PYQ
            for tier_type in _EXAMPLE_CASCADE:
                candidate_ids.extend(_get_for_type(tier_type, primary_concept_id))
                if len(candidate_ids) >= MIN_PRIMARY_CANDIDATES:
                    break
        else:
            # Pass 1: strict type filter
            candidate_ids = _get_for_type(content_type, primary_concept_id)
            # Pass 2: if still too few, relax type filter
            if len(candidate_ids) < MIN_PRIMARY_CANDIDATES:
                candidate_ids = _get_for_type(None, primary_concept_id)

        # Pass 3: secondary concepts only when primary concept has too few results
        if len(candidate_ids) < MIN_PRIMARY_CANDIDATES and secondary_concept_ids:
            for sec_id in secondary_concept_ids:
                candidate_ids.extend(_get_for_type(content_type, sec_id, limit=15))

        # Deduplicate while preserving order (primary concept hits first)
        seen: set = set()
        candidate_ids = [x for x in candidate_ids if not (x in seen or seen.add(x))]  # type: ignore[func-returns-value]

        # ── 6. Gemini: embed the query ────────────────────────────────────────
        query_vector = _embed_query(query)

        # ── 7. Neo4j: vector search scoped to graph-filtered candidates ───────
        vector_results = graph_db.vector_search(
            query_vector=query_vector,
            top_k=config.NEO4J_VECTOR_TOP_K,
            candidate_ids=candidate_ids if candidate_ids else None,
        )
        top_content_ids = [r["content_id"] for r in vector_results]

        # ── 8. Supabase: fetch full text for top-k hits ───────────────────────
        retrieved_chunks = db.read_content_by_ids(top_content_ids)
        order_map = {cid: i for i, cid in enumerate(top_content_ids)}
        retrieved_chunks.sort(key=lambda c: order_map.get(c["content_id"], 999))

        # ── 9. Socratic routing decision ──────────────────────────────────────
        socratic_turn_count = db.count_socratic_turns_for_concept(
            session_id, primary_concept_id
        )
        use_socratic = _should_use_socratic(target_state, prereq_states, socratic_turn_count)

        return {
            "target_concept_state":    target_state,
            "prereq_concept_states":   prereq_states,
            "secondary_concept_states": secondary_states,
            "retrieved_chunks":        retrieved_chunks,
            "use_socratic":            use_socratic,
            "socratic_turn_count":     socratic_turn_count,
            "error": None,
        }

    except Exception as exc:
        return {"error": f"GraphRAG Retriever failed: {exc}"}
