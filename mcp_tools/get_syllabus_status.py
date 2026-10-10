"""
MCP Tool: Get Syllabus Status
Aggregates roadmap alignment signals from Supabase concept states.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

import config
from db.supabase_client import db


class GetSyllabusStatusArgs(BaseModel):
    user_id: str = Field(..., description="Supabase user UUID.")
    focus_chapters: List[str] = Field(default_factory=list, description="Placement focus chapters.")
    roadmap_concept_id: Optional[str] = Field(default=None, description="Active roadmap concept, if any.")


def get_syllabus_status(args: GetSyllabusStatusArgs) -> Dict[str, Any]:
    """
    Return unfinished lesson count, weak chapters, and the suggested next concept.
    """
    try:
        states = db.read_all_user_concept_states(args.user_id)
        concepts = {c["concept_id"]: c for c in db.read_concepts()}

        focus = {ch.lower() for ch in (args.focus_chapters or []) if ch}
        unfinished = 0
        weak_chapters: List[str] = []
        next_concept_id: Optional[str] = args.roadmap_concept_id
        next_concept_name: Optional[str] = None

        for st in states:
            score = float(st.get("proficiency_score") or 0.5)
            chapter = (st.get("chapter") or "").strip()
            cold = bool(st.get("is_cold_start", True))
            flagged = bool(st.get("flagged_weak", False))

            in_focus = not focus or chapter.lower() in focus
            if in_focus and (cold or score < config.MASTERY_THRESHOLD):
                unfinished += 1
            if flagged and chapter and chapter not in weak_chapters:
                weak_chapters.append(chapter)

        if next_concept_id:
            next_concept_name = concepts.get(next_concept_id, {}).get("concept_name")
        else:
            # Lowest proficiency in focus (or globally) that is not mastered
            candidates = [
                st for st in states
                if (not focus or (st.get("chapter") or "").lower() in focus)
                and float(st.get("proficiency_score") or 0.5) < config.MASTERY_THRESHOLD
            ]
            candidates.sort(key=lambda s: float(s.get("proficiency_score") or 0.5))
            if candidates:
                next_concept_id = candidates[0]["concept_id"]
                next_concept_name = concepts.get(next_concept_id, {}).get("concept_name")

        return {
            "unfinished_lessons": unfinished,
            "weak_chapters": weak_chapters[:5],
            "next_concept_id": next_concept_id,
            "next_concept_name": next_concept_name,
            "coverage_gap": unfinished >= 3,
        }
    except Exception as exc:
        return {
            "unfinished_lessons": 0,
            "weak_chapters": [],
            "next_concept_id": None,
            "next_concept_name": None,
            "coverage_gap": False,
            "error": str(exc),
        }
