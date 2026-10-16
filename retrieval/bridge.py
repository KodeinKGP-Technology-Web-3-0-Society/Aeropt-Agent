"""Adapter: the retrieval service, shaped exactly like the GraphRAG node's output.

`graphrag_retriever_node` returns a specific dict of state updates that the Solver,
the experts and the Answer Checker all read — same keys, same meanings as ever.

What is genuinely new is added, not substituted: `retrieval_citations`,
`retrieval_novelty` and `retrieval_notes` carry the grounding trail and the
"don't repeat this question" signal that the hosted shape has no place for.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aeropt.retrieval.bridge")


def _learner_states(user_id: str, concept_id: str, prereqs: list[dict[str, Any]],
                    secondary_ids: list[str]) -> dict[str, Any]:
    """Proficiency for the concept, its prerequisites and any secondary concepts."""
    try:
        from db.supabase_client import db
    except Exception as exc:  # noqa: BLE001 — retrieval must work without the DB
        logger.warning("learner state unavailable (%s)", str(exc)[:120])
        return {"target": None, "prereq": [], "secondary": []}

    def read(cid: str) -> dict[str, Any]:
        try:
            return db.read_user_concept_state(user_id, cid) or {}
        except Exception:  # noqa: BLE001
            return {}

    target = read(concept_id) if user_id and concept_id else {}
    prereq_states = []
    for p in prereqs:
        state = read(p["concept_id"]) if user_id else {}
        prereq_states.append(
            {
                "concept_id": p["concept_id"],
                "concept_name": p.get("concept_name") or p["concept_id"],
                "proficiency_score": state.get("proficiency_score", 0.5),
                "flagged_weak": state.get("flagged_weak", False),
                "is_cold_start": state.get("is_cold_start", True),
                "hop_count": p.get("hop_count", 1),
            }
        )
    secondary_states = []
    for cid in secondary_ids:
        state = read(cid) if user_id else {}
        secondary_states.append(
            {
                "concept_id": cid,
                "concept_name": cid,
                "proficiency_score": state.get("proficiency_score", 0.5),
                "flagged_weak": state.get("flagged_weak", False),
                "is_cold_start": state.get("is_cold_start", True),
            }
        )
    return {"target": target or None, "prereq": prereq_states, "secondary": secondary_states}


def retrieve_for_state(state: dict[str, Any]) -> dict[str, Any]:
    """Run retrieval for one turn and return GraphRAG-shaped state updates."""
    from retrieval import service, store

    if not store.index_exists():
        return {
            "error": (
                "Retrieval database unavailable: set RETRIEVAL_DATABASE_URL "
                "(and fill it with `make push`)."
            )
        }

    user_id = str(state.get("user_id") or "")
    session_id = str(state.get("session_id") or "")
    query = str(state.get("query") or "")
    content_type = str(state.get("content_type_requested") or "theory")
    concept_id = str(state.get("primary_concept_id") or "")
    target_exam = str(state.get("target_exam") or "")

    pack = service.retrieve(
        query,
        user_id=user_id,
        content_type=content_type,
        concept_id=concept_id,
        target_exam=target_exam,
        state=state,
        recent_turns=list(state.get("recent_turns") or []),
        forced=bool(state.get("_force_retrieval")),
        top_k=int(state.get("retrieval_top_k") or 5),
        exclude_seen=state.get("_exclude_seen"),
        difficulty_target=state.get("difficulty_target"),
    )

    if pack["confidence"] == "skipped":
        # Nothing to ground and nothing to undo: leave the turn's state alone.
        return {"retrieval_decision": pack["decision"]}

    concept = pack.get("concept") or {}
    resolved_id = concept.get("concept_id") or concept_id

    if not pack.get("in_scope", False):
        return {
            "primary_concept_id": "",
            "retrieved_chunks": [],
            "retrieval_confidence": "unresolved",
            "retrieval_notes": pack.get("notes") or [],
            "retrieval_decision": pack["decision"],
            "error": None,
        }

    chunks = pack.get("chunks") or []
    prereqs = pack.get("prerequisites") or []
    secondary_ids = [
        c["concept_id"] for c in (concept.get("alternatives") or [])[:2] if c.get("concept_id")
    ]
    learner = _learner_states(user_id, resolved_id, prereqs, secondary_ids)

    socratic_turns = 0
    if session_id and resolved_id:
        try:
            from db.supabase_client import db

            socratic_turns = db.count_socratic_turns_for_concept(session_id, resolved_id) or 0
        except Exception:  # noqa: BLE001
            socratic_turns = 0

    try:
        from db.supabase_client import db

        learning_profile = db.read_user_learning_profile(user_id) if user_id else None
    except Exception as exc:  # noqa: BLE001 — profile only tunes *how* to teach
        logger.warning("learning profile unavailable (%s)", str(exc)[:120])
        learning_profile = None

    from agents.planner import compute_personalization_strategy

    strategy = compute_personalization_strategy(
        target_state=learner["target"] or {},
        prereq_states=learner["prereq"],
        learning_profile=learning_profile,
        content_type=content_type,
        query=query,
        socratic_turn_count=socratic_turns,
    )

    if pack["confidence"] == "reused":
        chunks = chunks or list(state.get("retrieved_chunks") or [])

    return {
        "primary_concept_id": resolved_id,
        "secondary_concept_ids": secondary_ids,
        "content_type_requested": pack.get("content_type_used") or content_type,
        "target_concept_state": learner["target"],
        "prereq_concept_states": learner["prereq"],
        "secondary_concept_states": learner["secondary"],
        "retrieved_chunks": chunks,
        "retrieval_confidence": "ok" if pack["confidence"] in {"ok", "reused"} else pack["confidence"],
        "retrieval_citations": pack.get("citations") or [],
        "retrieval_notes": pack.get("notes") or [],
        "retrieval_novelty": pack.get("novelty") or {},
        "retrieval_decision": pack["decision"],
        "retrieval_took_ms": pack.get("took_ms"),
        "use_socratic": bool(strategy.get("use_socratic", False)),
        "socratic_turn_count": socratic_turns,
        "teaching_mode": strategy.get("teaching_mode"),
        "difficulty_target": strategy.get("difficulty_target"),
        "framing": strategy.get("framing"),
        "error": None,
    }
