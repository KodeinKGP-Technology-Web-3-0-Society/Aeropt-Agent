"""
MCP Tool: Generate Suggestions
Encapsulates the Follow-Up Agent logic for generating next-best study recommendations.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import json
import re

import config
from db.supabase_client import db
from retrieval.local_graph import graph_client  # backend-aware: Neo4j when hosted, the local index when not
from utils.llm import groq_complete
from utils.presentation import filter_picker_suggestions
from openai import OpenAI

_groq = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)


class GenerateSuggestionsArgs(BaseModel):
    user_id: str = Field(..., description="The user's UUID.")
    primary_concept_id: str = Field(..., description="The main concept ID currently being studied.")
    proficiency: float = Field(default=0.5, description="The student's current proficiency score (0.0 to 1.0).")
    seen_follow_up_ids: List[str] = Field(default_factory=list, description="IDs of content already suggested/clicked in this session.")


_CTYPE_ALIASES: Dict[str, str] = {
    "pyq": "pyq",
    "past year question": "pyq",
    "past year paper": "pyq",
    "practice": "practice",
    "practice question": "practice",
    "example": "example",
    "solved example": "example",
    "worked example": "example",
    "theory": "theory",
}

def _normalize_ctype(raw: str) -> str:
    key = (raw or "").lower().strip()
    return _CTYPE_ALIASES.get(key, key)


def generate_suggestions(args: GenerateSuggestionsArgs) -> List[Dict[str, Any]]:
    """
    Queries the knowledge graph for unseen content, filters by difficulty, 
    and uses an LLM to select the 3 most pedagogically sound next steps.
    """
    try:
        if not args.primary_concept_id:
            return []

        # 1. Get content IDs already seen by this user
        seen_ids = db.read_seen_content_ids(args.user_id)
        all_excluded = set(seen_ids) | set(args.seen_follow_up_ids)

        # 2. Difficulty ceiling based on proficiency
        difficulty_max = max(1, min(4, int(args.proficiency * 4) + 1))

        # 3. Neo4j candidate search
        candidate_ids = graph_client().get_content_ids_for_follow_up(
            concept_id=args.primary_concept_id,
            seen_ids=list(all_excluded),
            difficulty_max=difficulty_max,
            limit=10,
        )

        if not candidate_ids:
            candidate_ids = graph_client().get_content_ids_for_follow_up(
                concept_id=args.primary_concept_id,
                seen_ids=list(all_excluded),
                difficulty_max=5,
                limit=10,
            )

        # 4. Supabase fetch full metadata
        candidates = db.read_content_by_ids(candidate_ids[:10])
        if not candidates:
            return []

        # 5. LLM Reasoning
        concept_name = candidates[0].get("concept_name", args.primary_concept_id)
        
        candidates_brief = []
        for c in candidates:
            ctype = _normalize_ctype(c.get("content_type", ""))
            if ctype not in {"theory", "example"}:
                continue
            candidates_brief.append({
                "content_id": c["content_id"],
                "content_type": ctype,
                "difficulty": c.get("difficulty", 3),
                "preview": (c.get("core_text") or "")[:150]
            })

        if not candidates_brief:
            return []

        prompt = f"""\
You are a JEE Physics tutor. The student is studying {concept_name} (proficiency: {args.proficiency:.2f}/1.0).

Candidate resources available:
{candidates_brief}

Select the 3 best candidates to show the student next. 
Rules:
- If proficiency < 0.5, prioritize theory and easier examples.
- If proficiency >= 0.5, prioritize harder examples.
- Provide a brief (1-sentence) encouraging reason why this is a good next step.

Respond ONLY with a JSON array:
[
  {{"content_id": "...", "reason": "..."}}
]"""
        
        raw, _ = groq_complete(
            client=_groq,
            model=getattr(config, "FOLLOW_UP_MODEL", config.ORCHESTRATOR_MODEL),
            messages=[{"role": "user", "content": prompt}],
            fallback_models=config.SMALL_MODEL_FALLBACK,
            temperature=0.1,
            max_tokens=400,
        )
        
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                first_newline = cleaned.find("\n")
                cleaned = cleaned[first_newline + 1:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            selected_items = json.loads(cleaned.strip())
        except Exception:
            selected_items = [{"content_id": c["content_id"], "reason": "Next step"} for c in candidates_brief[:3]]

        # Re-attach full DB rows
        suggestions = []
        cand_map = {c["content_id"]: c for c in candidates}
        
        for item in selected_items[:3]:
            cid = item.get("content_id")
            if cid in cand_map:
                c = cand_map[cid]
                suggestion = dict(c)
                suggestion["content_type"] = _normalize_ctype(c.get("content_type", ""))
                suggestion["preview"] = item.get("reason", "Recommended next step.")
                suggestions.append(suggestion)

        suggestions = filter_picker_suggestions(suggestions)
        return suggestions

    except Exception as exc:
        import logging
        logging.exception("generate_suggestions tool failed.")
        return []
