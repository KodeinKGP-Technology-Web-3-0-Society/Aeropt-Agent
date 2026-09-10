"""
Concept Resolver Agent

Responsibilities:
  - Map the user's query to primary_concept_id + secondary_concept_ids + content_type
  - Delegate the actual classification to mcp_tools.resolve_concept_id, which already
    combines a fast lexical pre-check with an LLM fallback that outputs an explicit
    in_scope flag — this used to be a second, independent, hand-rolled implementation
    here that had no in_scope concept at all.

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

Model: llama-3.1-8b-instant (Groq) — fast; 33-item closed-set fits trivially in context
Design note (Section 6): no embeddings here.  A full 33-item list costs <500 tokens.
Embeddings add a failure mode (nearest-neighbour error) without adding accuracy at this scale.
"""

from __future__ import annotations

from typing import Any, Dict

from db.supabase_client import db
from mcp_tools.resolve_concept_id import ResolveConceptIdArgs, resolve_concept_id
from models.state import TutorState


def concept_resolver_node(state: TutorState) -> Dict[str, Any]:
    query = state["query"]
    recent_turns = state.get("recent_turns", [])
    roadmap_concept_id = state.get("roadmap_concept_id") or None
    study_stage = state.get("study_stage") or None

    # Fail loudly here (not silently) if the taxonomy itself is missing/empty — that's
    # a data/ingestion problem, distinct from "this one query is off-syllabus" below.
    concepts = db.read_concepts()
    if not concepts:
        return {"error": "No concepts found in Supabase. Ingestion may not have run."}

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
