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
import logging

logger = logging.getLogger("jee_tutor.retriever")

_genai_client = None


def _get_genai_client():
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _genai_client


def _embed_query(text: str) -> List[float]:
    client = _get_genai_client()
    result = client.models.embed_content(
        model=config.EMBEDDING_MODEL,
        contents=text,
    )
    return result.embeddings[0].values



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
    # The local backend does its own resolution, gating, novelty filtering and
    # hybrid search, and returns this node's exact output shape (retrieval/bridge.py).
    # It is selected by RETRIEVAL_BACKEND=local in .env.local; the deployed service
    # never sets it, so everything below is untouched for Aura.
    if config.supabase_retrieval():
        from retrieval.supabase_retriever import retrieve_for_state

        return retrieve_for_state(dict(state))

    if config.local_retrieval():
        from retrieval.bridge import retrieve_for_state

        return retrieve_for_state(dict(state))

    if not state.get("primary_concept_id"):
        # Concept Resolver couldn't match this query to anything in the syllabus
        # (in_scope=False) — there is nothing to retrieve, and reusing whatever
        # retrieval_confidence/retrieved_chunks was left over from a *previous*,
        # unrelated turn would be misleading. Tag it explicitly so the Solver can
        # tell the student this looks outside what it covers, instead of silently
        # answering ungrounded (see agents/solver.py's grounding_note).
        return {"retrieval_confidence": "unresolved", "retrieved_chunks": []}

    from agents.retrieval_gate import should_skip_retrieval
    if should_skip_retrieval(state):
        return {}

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

        # ── 6/7. Gemini + Neo4j with Timeout (Task 1.3) ────────────────────────
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        
        def run_search():
            q_vec = _embed_query(query)
            return graph_db.vector_search(
                query_vector=q_vec,
                top_k=config.NEO4J_VECTOR_TOP_K,
                candidate_ids=candidate_ids if candidate_ids else None,
            )

        search_timed_out = False
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(run_search)
                vector_results = future.result(timeout=config.RETRIEVAL_TIMEOUT_MS / 1000.0)
        except TimeoutError:
            logger.warning(
                "Vector search exceeded %d ms for concept %s — answering without "
                "retrieved material.",
                config.RETRIEVAL_TIMEOUT_MS,
                primary_concept_id,
            )
            vector_results = []
            search_timed_out = True
        
        # Filter by confidence threshold (Task 1.2)
        strong_results = [r for r in vector_results if r["score"] >= config.RETRIEVAL_CONFIDENCE_THRESHOLD]
        if len(strong_results) < config.RETRIEVAL_CONFIDENCE_MIN_HITS:
            top_content_ids = []
            retrieved_chunks = []
            top_score = vector_results[0]["score"] if vector_results else 0.0
            # A timeout means we never looked, so it says nothing about whether
            # the content exists. Recording it as a content gap filled the table
            # with false misses at score 0.0 and made the signal worthless.
            if not search_timed_out:
                db.insert_content_gap_event(
                    user_id=user_id,
                    session_id=session_id,
                    concept_id=primary_concept_id,
                    query_text=query,
                    top_score=top_score
                )
        else:
            top_content_ids = [r["content_id"] for r in strong_results]
            # ── 8. Supabase: fetch full text for top-k hits ───────────────────────
            retrieved_chunks = db.read_content_by_ids(top_content_ids)
            order_map = {cid: i for i, cid in enumerate(top_content_ids)}
            retrieved_chunks.sort(key=lambda c: order_map.get(c["content_id"], 999))

        # ── 9. Strategy decision ─────────────────────────────────────────────
        socratic_turn_count = db.count_socratic_turns_for_concept(
            session_id, primary_concept_id
        )
        
        from agents.planner import compute_personalization_strategy

        # The learning profile only tunes *how* to teach (velocity, preferred
        # content type). Retrieval works fine without it, so a failure here must
        # not take the retrieved material down with it — which is exactly what
        # happened when the Supabase role was missing SELECT on
        # user_learning_profile: every retrieval returned zero chunks and the
        # tutor answered ungrounded, with no visible cause.
        try:
            learning_profile = db.read_user_learning_profile(user_id)
        except Exception as exc:
            logger.warning(
                "Learning profile unavailable for %s (%s) — continuing with defaults.",
                user_id,
                str(exc)[:160],
            )
            learning_profile = None
        
        strategy = compute_personalization_strategy(
            target_state=target_state or {},
            prereq_states=prereq_states,
            learning_profile=learning_profile,
            content_type=content_type,
            query=query,
            socratic_turn_count=socratic_turn_count
        )
        
        retrieval_confidence = "low" if not retrieved_chunks else "ok"

        return {
            "target_concept_state":    target_state,
            "prereq_concept_states":   prereq_states,
            "secondary_concept_states": secondary_states,
            "retrieved_chunks":        retrieved_chunks,
            "retrieval_confidence":    retrieval_confidence,
            "use_socratic":            strategy.get("use_socratic", False),
            "socratic_turn_count":     socratic_turn_count,
            "teaching_mode":           strategy.get("teaching_mode"),
            "difficulty_target":       strategy.get("difficulty_target"),
            "framing":                 strategy.get("framing"),
            "error": None,
        }

    except Exception as exc:
        return {"error": f"GraphRAG Retriever failed: {exc}"}
