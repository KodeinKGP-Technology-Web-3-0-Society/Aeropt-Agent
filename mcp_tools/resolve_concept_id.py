"""
MCP Tool: Resolve Concept ID
Encapsulates the Concept Resolver Agent logic as a standalone tool.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import json
import logging
import re

import config
from db.supabase_client import db
from utils.llm import groq_complete
from openai import OpenAI

_groq = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)


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


def _salvage_fields(text: str) -> Optional[Dict[str, Any]]:
    """Recover the fields we need from JSON that did not finish.

    A truncated reply still usually contains the concept id, which is the only
    field that really matters — and returning it beats reporting the topic as
    off-syllabus, which is what a parse failure degrades into.
    """
    m = re.search(r'"primary_concept_id"\s*:\s*"([A-Za-z0-9_\-]+)', text)
    if not m:
        return None
    in_scope = not re.search(r'"in_scope"\s*:\s*false', text, flags=re.IGNORECASE)
    ctype = "theory"
    ct = re.search(r'"content_type_requested"\s*:\s*"(theory|practice|pyq|example)"', text)
    if ct:
        ctype = ct.group(1)
    return {
        "in_scope": in_scope,
        "primary_concept_id": m.group(1),
        "secondary_concept_ids": [],
        "content_type_requested": ctype,
    }


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Wrapped in prose: take the outermost braces.
    braces = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if braces:
        try:
            return json.loads(braces.group(0))
        except json.JSONDecodeError:
            pass

    salvaged = _salvage_fields(text)
    if salvaged:
        logging.warning(
            "resolve_concept_id: recovered %s from malformed JSON.",
            salvaged["primary_concept_id"],
        )
        return salvaged
    raise json.JSONDecodeError("Could not parse resolver output", text, 0)


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
        if config.supabase_retrieval():
            from retrieval.supabase_retriever import resolve_concept
            return resolve_concept(args)
        if config.local_retrieval():
            return _resolve_local(args)
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
            # Was 100, which truncated the reply mid-string for any concept with
            # a long id (e.g. "newtons_2nd_law_f__ma"). The JSON then failed to
            # parse and the whole query was reported as off-syllabus — a silent
            # total failure for those concepts. 400 leaves ample headroom for
            # the fixed four-field response.
            max_tokens=400,
            min_chars=5,
        )
        
        parsed = _extract_json(raw)

        in_scope = parsed.get("in_scope", True)
        primary = parsed.get("primary_concept_id") or ""
        if isinstance(primary, str) and primary.lower() in {"null", "none", ""}:
            primary = ""

        # A salvaged id from a truncated reply is a prefix of the real one. If
        # exactly one concept starts with it there is no ambiguity to resolve,
        # so completing it beats discarding the answer.
        if primary and primary not in valid_ids:
            matches = [cid for cid in valid_ids if cid.startswith(primary)]
            if len(matches) == 1:
                logging.warning(
                    "resolve_concept_id: completed truncated id %r -> %r.",
                    primary,
                    matches[0],
                )
                primary = matches[0]

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
