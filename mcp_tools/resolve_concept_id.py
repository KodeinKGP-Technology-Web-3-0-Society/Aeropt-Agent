"""
MCP Tool: Resolve Concept ID
Encapsulates the Concept Resolver Agent logic as a standalone tool.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import json
import re

import config
from db.supabase_client import db
from utils.llm import groq_complete
from groq import Groq

_groq = Groq(api_key=config.GROQ_API_KEY)


class ResolveConceptIdArgs(BaseModel):
    query: str = Field(..., description="The student's raw query.")
    recent_turns: List[Dict[str, Any]] = Field(default_factory=list, description="Last few chat turns for context resolution.")
    roadmap_concept_id: Optional[str] = Field(default=None, description="Pre-selected concept ID from UI, bypasses LLM if provided.")
    study_stage: Optional[str] = Field(default=None, description="Pre-selected study stage from UI.")


_RESOLVER_PROMPT = """\
You are a concept classifier for JEE Physics.

Here are the concepts in this system (concept_id | concept_name | chapter):
{concepts_list}

Classify the student's query into:
  - in_scope: true if the query is about JEE Physics — including a named chapter
    (e.g. "rotational motion", "laws of motion"), a concept below, problem-solving,
    formulas, or exam prep. false ONLY if it is clearly unrelated (food, celebrities,
    general trivia, other subjects). "I want to learn X" is in_scope when X is a
    chapter or concept below.
  - primary_concept_id: the single best matching concept_id from the list.
    If the student names a CHAPTER rather than one concept, pick the first
    (foundational) concept of that chapter. Never return null when in_scope is true.
  - secondary_concept_ids: ONLY extra concepts explicitly named alongside the primary.
  - content_type_requested: theory | practice | pyq | example.
    "learn", "teach me", "explain" → theory.

Use the last 2 turns (below) ONLY if the query is ambiguous.

Last 2 turns:
{context}

Student query: "{query}"

Respond with ONLY valid JSON — no markdown, no prose:
{{
  "in_scope": true,
  "primary_concept_id": "<concept_id>",
  "secondary_concept_ids": [],
  "content_type_requested": "theory"
}}"""


def _format_concepts(concepts: List[Dict[str, Any]]) -> str:
    return "\n".join(
        [
            f"  {c['concept_id']} | {c['concept_name']} | {c.get('chapter', '')}"
            for c in concepts
        ]
    )


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


def _format_recent_turns(turns: List[Dict[str, Any]]) -> str:
    if not turns:
        return "  (none)"
    lines = []
    for t in turns:
        lines.append(f"  [{t.get('role', '')}]: {t.get('content', '')[:300]}")
    return "\n".join(lines)


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def resolve_concept_id(args: ResolveConceptIdArgs) -> Dict[str, Any]:
    """
    Maps a natural language query to the exact taxonomy concept IDs and content type.
    """
    try:
        concepts = db.read_concepts()
        if not concepts:
            raise ValueError("No concepts found in Supabase.")

        valid_ids = {c["concept_id"] for c in concepts}

        # Fast Path for Roadmap Navigation
        if args.roadmap_concept_id and args.roadmap_concept_id in valid_ids:
            valid_stages = {"theory", "example", "practice", "pyq"}
            content_type = args.study_stage if args.study_stage in valid_stages else "theory"
            
            if not args.study_stage:
                lowered = args.query.lower()
                if any(word in lowered for word in ("practice", "problem", "question")):
                    content_type = "practice"
                elif any(word in lowered for word in ("pyq", "past year")):
                    content_type = "pyq"
                elif any(word in lowered for word in ("example", "worked", "solve")):
                    content_type = "example"

            return {
                "in_scope": True,
                "primary_concept_id": args.roadmap_concept_id,
                "secondary_concept_ids": [],
                "content_type_requested": content_type,
            }

        lexical = lexical_resolve(args.query, concepts)
        if lexical and lexical.get("primary_concept_id") in valid_ids:
            return lexical

        # LLM Classification
        prompt = _RESOLVER_PROMPT.format(
            concepts_list=_format_concepts(concepts),
            context=_format_recent_turns(args.recent_turns),
            query=args.query,
        )

        raw, _ = groq_complete(
            client=_groq,
            model=config.CONCEPT_RESOLVER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            fallback_models=config.SMALL_MODEL_FALLBACK,
            temperature=0.0,
            max_tokens=100,
            min_chars=5,
        )
        
        parsed = _extract_json(raw)

        in_scope = parsed.get("in_scope", True)
        primary = parsed.get("primary_concept_id") or ""
        if isinstance(primary, str) and primary.lower() in {"null", "none", ""}:
            primary = ""

        if primary in valid_ids and in_scope is not False:
            sec = parsed.get("secondary_concept_ids", [])
            parsed["secondary_concept_ids"] = [sid for sid in sec if sid in valid_ids and sid != primary]
            parsed["primary_concept_id"] = primary
            parsed["in_scope"] = True
            if not parsed.get("content_type_requested"):
                parsed["content_type_requested"] = infer_content_type(args.query)
            return parsed

        if lexical and lexical.get("primary_concept_id") in valid_ids:
            return lexical

        return {
            "in_scope": False,
            "primary_concept_id": None,
            "secondary_concept_ids": [],
            "content_type_requested": "theory",
        }

    except Exception as exc:
        import logging
        logging.exception("resolve_concept_id tool failed.")
        return {
            "in_scope": False,
            "primary_concept_id": None,
            "secondary_concept_ids": [],
            "content_type_requested": "theory",
            "error": str(exc)
        }
