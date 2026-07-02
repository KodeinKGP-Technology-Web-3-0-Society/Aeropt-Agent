"""
Concept Resolver Agent

Responsibilities:
  - Load the flat list of 33 concepts from Supabase (closed-set classification)
  - Optionally use last 1-2 turns if the query is ambiguous ("explain that again")
  - Map the query to primary_concept_id + secondary_concept_ids
  - Infer content_type_requested (theory | practice | pyq | example)

Model: llama-3.1-8b-instant (Groq) — fast; 33-item closed-set fits trivially in context
Design note (Section 6): no embeddings here.  A full 33-item list costs <500 tokens.
Embeddings add a failure mode (nearest-neighbour error) without adding accuracy at this scale.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from groq import Groq

import config
from db.supabase_client import db
from models.state import TutorState
from utils.llm import groq_complete

_groq = Groq(api_key=config.GROQ_API_KEY)

_RESOLVER_PROMPT = """\
You are a concept classifier for JEE Physics.

Here are the 33 concepts in this system (concept_id | concept_name):
{concepts_list}

Classify the student's query into:
  - primary_concept_id: the single best matching concept_id from the list above
  - secondary_concept_ids: ONLY concepts explicitly mentioned alongside the primary.
    Leave this as [] in most cases. Do NOT add related or prerequisite concepts —
    those are handled separately by the graph. Only add a secondary concept if the
    student's query clearly and explicitly references two concepts at once.
  - content_type_requested: one of [theory, practice, pyq, example]
    • theory   — asking for explanation / definition / formula
    • practice — asking for problems to solve
    • pyq      — asking for past JEE papers / PYQs
    • example  — asking for a worked example or solved problem

Use the last 2 turns (below) ONLY if the query is ambiguous (e.g. "that", "same thing", "again").
When using context to resolve ambiguity, keep secondary_concept_ids as [].

Last 2 turns:
{context}

Student query: "{query}"

Respond with ONLY valid JSON — no markdown, no prose:
{{
  "primary_concept_id": "<concept_id>",
  "secondary_concept_ids": ["<concept_id>", ...],
  "content_type_requested": "<theory|practice|pyq|example>"
}}"""


def _format_concepts(concepts: List[Dict[str, Any]]) -> str:
    lines = [
        f"  {c['concept_id']} | {c['concept_name']} ({c.get('chapter', '')})"
        for c in concepts
    ]
    return "\n".join(lines)


def _format_recent_turns(turns: List[Dict[str, Any]]) -> str:
    if not turns:
        return "  (none)"
    lines = []
    for t in turns:
        role = t.get("role", "")
        content = t.get("content", "")[:300]
        lines.append(f"  [{role}]: {content}")
    return "\n".join(lines)


def _extract_json(text: str) -> Dict[str, Any]:
    """Pull JSON from model output even if it's wrapped in markdown fences."""
    text = text.strip()
    # Strip ```json ... ``` or ``` ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def concept_resolver_node(state: TutorState) -> Dict[str, Any]:
    query = state["query"]
    recent_turns = state.get("recent_turns", [])

    try:
        concepts = db.read_concepts()
        if not concepts:
            return {"error": "No concepts found in Supabase. Ingestion may not have run."}

        # Build concept ID set for validation
        valid_ids = {c["concept_id"] for c in concepts}

        prompt = _RESOLVER_PROMPT.format(
            concepts_list=_format_concepts(concepts),
            context=_format_recent_turns(recent_turns),
            query=query,
        )

        raw, _ = groq_complete(
            client=_groq,
            model=config.CONCEPT_RESOLVER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            fallback_models=config.SMALL_MODEL_FALLBACK,
            temperature=0.0,
            max_tokens=150,
        )

        parsed = _extract_json(raw)

        primary = parsed.get("primary_concept_id", "")
        secondaries = [
            cid
            for cid in parsed.get("secondary_concept_ids", [])
            if cid in valid_ids and cid != primary
        ]
        content_type = parsed.get("content_type_requested", "theory")

        # Fallback: if primary not in taxonomy, use first concept as default
        if primary not in valid_ids and concepts:
            primary = concepts[0]["concept_id"]

        return {
            "primary_concept_id": primary,
            "secondary_concept_ids": secondaries,
            "content_type_requested": content_type,
            "error": None,
        }

    except Exception as exc:
        return {"error": f"Concept Resolver failed: {exc}"}
