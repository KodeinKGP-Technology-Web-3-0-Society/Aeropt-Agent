"""
Concept-context adapter for tutor tools and API prefetch.

Responsibilities:
  - Map the user's query to primary_concept_id + secondary_concept_ids + content_type
  - Delegate matching to mcp_tools.resolve_concept_id, which combines the
    Postgres concept cache, aliases, embeddings and material voting and returns
    an explicit in_scope flag.

Off-syllabus handling (fixed 2026-09-11 — was the #1 cause of hallucinated answers):
  When the query doesn't match any concept this tutor tracks (in_scope=False), or the
  resolver otherwise can't pick a concept, primary_concept_id is left empty ("").
  Previously this fell back to "the first concept in the Supabase table", which meant
  *any* off-syllabus or unrecognized question (a different subject, a typo, small talk)
  was silently answered as if it were about that arbitrary first concept — GraphRAG would
  retrieve real chunks for the wrong topic and the Solver would confidently "explain" the
  actual question using them. Leaving primary_concept_id empty lets the retrieval gate and
  Solver (see agents/solver.py's grounding_note) tell the student plainly that this is
  outside the syllabus this tutor covers, instead of guessing.

The active resolver uses the retrieval Postgres concept cache, aliases and query
embeddings. The CLI and HTTP server prepare that index and embedding model at
startup so cold initialization does not consume the first tool call's deadline.
"""

from __future__ import annotations

from typing import Any, Dict

from mcp_tools.resolve_concept_id import ResolveConceptIdArgs, resolve_concept_id
from models.state import TutorState
from retrieval import store
from services.classroom_rooms import prerequisite_ids


def concept_resolver_node(state: TutorState) -> Dict[str, Any]:
    query = state["query"]
    recent_turns = state.get("recent_turns", [])
    roadmap_concept_id = state.get("roadmap_concept_id") or None
    study_stage = state.get("study_stage") or None

    locked = (state.get("locked_concept_id") or "").strip()
    if locked:
        # The room fixed the concept. Stay on it, and keep its prerequisites
        # in scope. Do not re-resolve the student's words onto another topic.
        return {
            "primary_concept_id": locked,
            "secondary_concept_ids": prerequisite_ids(locked),
            "content_type_requested": study_stage or "theory",
            "error": None,
        }

    # Fail loudly here (not silently) if the taxonomy itself is missing/empty — that's
    # a data/ingestion problem, distinct from "this one query is off-syllabus" below.
    # Use the same cached catalogue as resolve_concept_id. Fetching the complete
    # REST catalogue here added a redundant network request on every resolution;
    # those rows were never used by the actual Postgres-backed resolver.
    concepts = store.all_concepts()
    if not concepts:
        return {"error": "No concepts found in the retrieval index. Ingestion may not have run."}

    result = resolve_concept_id(
        ResolveConceptIdArgs(
            query=query,
            recent_turns=recent_turns,
            roadmap_concept_id=roadmap_concept_id,
            study_stage=study_stage,
        )
    )

    if result.get("error"):
        return {"error": f"Concept Resolver failed: {result['error']}"}

    primary = result.get("primary_concept_id") or ""
    if result.get("in_scope") is False:
        # Doesn't match anything this tutor covers — stay unresolved, don't guess.
        primary = ""

    return {
        "primary_concept_id": primary,
        "secondary_concept_ids": result.get("secondary_concept_ids") or [],
        "content_type_requested": result.get("content_type_requested") or "theory",
        "error": None,
    }
