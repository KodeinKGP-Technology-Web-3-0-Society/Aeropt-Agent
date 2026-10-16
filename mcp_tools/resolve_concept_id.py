"""
MCP Tool: Resolve Concept ID
Encapsulates the Concept Resolver Agent logic as a standalone tool.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import logging
import re


class ResolveConceptIdArgs(BaseModel):
    query: str = Field(..., description="The student's raw query.")
    recent_turns: List[Dict[str, Any]] = Field(default_factory=list, description="Last few chat turns for context resolution.")
    roadmap_concept_id: Optional[str] = Field(default=None, description="Pre-selected concept ID from UI, bypasses LLM if provided.")
    study_stage: Optional[str] = Field(default=None, description="Pre-selected study stage from UI.")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def infer_content_type(query: str) -> str:
    lowered = (query or "").lower()
    if any(w in lowered for w in ("pyq", "past year", "jee main", "jee advanced")):
        return "pyq"
    if any(w in lowered for w in ("practice", "problem", "question", "drill", "quiz")):
        return "practice"
    if any(w in lowered for w in ("example", "worked", "solve this")):
        return "example"
    return "theory"


def lexical_resolve(query: str, concepts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Map a study intent to a concept without an LLM.

    Chapter names (e.g. "rotational motion") resolve to the first listed concept
    in that chapter. Named concepts win over chapter matches.
    """
    qn = _norm(query)
    if not qn or not concepts:
        return None

    best_concept: Optional[Dict[str, Any]] = None
    best_len = 0
    for c in concepts:
        name = _norm(c.get("concept_name") or "")
        cid = _norm((c.get("concept_id") or "").replace("_", " "))
        for needle in (name, cid):
            if len(needle) < 4:
                continue
            if needle in qn and len(needle) > best_len:
                best_concept = c
                best_len = len(needle)

    if best_concept:
        return {
            "in_scope": True,
            "primary_concept_id": best_concept["concept_id"],
            "secondary_concept_ids": [],
            "content_type_requested": infer_content_type(query),
        }

    chapters: Dict[str, List[Dict[str, Any]]] = {}
    for c in concepts:
        ch = _norm(c.get("chapter") or "")
        if not ch:
            continue
        chapters.setdefault(ch, []).append(c)

    best_chapter = ""
    for ch in chapters:
        if len(ch) < 4:
            continue
        if ch in qn and len(ch) > len(best_chapter):
            best_chapter = ch

    if best_chapter:
        starter = chapters[best_chapter][0]
        return {
            "in_scope": True,
            "primary_concept_id": starter["concept_id"],
            "secondary_concept_ids": [],
            "content_type_requested": infer_content_type(query),
        }
    return None


def _resolve_local(args: ResolveConceptIdArgs) -> Dict[str, Any]:
    """Local backend: embeddings + aliases + material voting, no LLM, ~1 ms.

    The prompt-based path below lists every concept for the model to choose from.
    That was fine at 33 concepts; the derived syllabus has over a thousand, where the
    list alone would dominate the turn and the choice would get worse, not better.
    """
    from retrieval import resolver
    from retrieval.gate import infer_content_type

    valid_stages = {"theory", "example", "practice", "pyq"}
    if args.roadmap_concept_id:
        from retrieval import store

        if store.concept(args.roadmap_concept_id):
            stage = args.study_stage if args.study_stage in valid_stages else infer_content_type(args.query)
            return {
                "in_scope": True,
                "primary_concept_id": args.roadmap_concept_id,
                "secondary_concept_ids": [],
                "content_type_requested": stage,
            }

    res = resolver.resolve(args.query, recent_turns=args.recent_turns)
    if not res.in_scope:
        return {
            "in_scope": False,
            "primary_concept_id": None,
            "secondary_concept_ids": [],
            "content_type_requested": infer_content_type(args.query),
            "grounding_strength": res.strength,
        }
    stage = args.study_stage if args.study_stage in valid_stages else infer_content_type(args.query)
    return {
        "in_scope": True,
        "primary_concept_id": res.concept_id,
        "secondary_concept_ids": [
            c["concept_id"] for c in res.candidates[1:3] if c.get("concept_id") != res.concept_id
        ],
        "content_type_requested": stage,
        "concept_name": res.concept_name,
        "chapter": res.chapter,
        "in_jee_syllabus": res.in_syllabus,
        "grounding_strength": res.strength,
        "matched_by": res.how,
    }


def resolve_concept_id(args: ResolveConceptIdArgs) -> Dict[str, Any]:
    """
    Maps a natural language query to the exact taxonomy concept IDs and content type.
    """
    try:
        # Embeddings + aliases + material voting over the retrieval Postgres, no LLM call.
        return _resolve_local(args)
    except Exception as exc:
        # NOTE: `import logging` used to live here. A function-local import makes
        # the name local for the WHOLE function, so every earlier logging call in
        # this function raised UnboundLocalError. It is imported at module level.
        logging.exception("resolve_concept_id tool failed.")
        return {
            "in_scope": False,
            "primary_concept_id": None,
            "secondary_concept_ids": [],
            "content_type_requested": "theory",
            "error": str(exc)
        }
