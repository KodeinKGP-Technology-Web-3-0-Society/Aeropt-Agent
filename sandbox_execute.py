"""Structured sandbox actions → tutor graph queries."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

_ACTION_INTENTS = {
    "LEARN": "doubt",
    "REVISE": "doubt",
    "PREREQ": "doubt",
    "PRACTICE": "practice_request",
    "PYQ": "practice_request",
    "QUIZ": "quiz_request",
    "MOCK_SLICE": "quiz_request",
}

_CONTENT_TYPES = {
    "LEARN": "theory",
    "REVISE": "theory",
    "PREREQ": "theory",
    "PRACTICE": "practice",
    "PYQ": "pyq",
    "QUIZ": "practice",
    "MOCK_SLICE": "pyq",
}


def action_to_query(action: str, concept_id: str, concept_name: Optional[str] = None) -> str:
    label = concept_name or concept_id.replace("_", " ")
    action = (action or "LEARN").upper()
    if action == "PRACTICE":
        return f"Give me a practice question on {label}"
    if action == "PYQ":
        return f"Give me a past year JEE question on {label}"
    if action == "QUIZ":
        return f"Quiz me on {label}"
    if action == "REVISE":
        return f"Quick revision on {label}"
    if action == "PREREQ":
        return f"Explain the prerequisite concept {label}"
    if action == "MOCK_SLICE":
        return f"Give me a short mixed drill on {label}"
    return f"Explain {label}"


def build_sandbox_card(
    *,
    action: str,
    concept_id: str,
    concept_name: Optional[str] = None,
    proficiency_pct: Optional[int] = None,
    weak_prereq: Optional[str] = None,
    duration_min: int = 8,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    title = concept_name or concept_id.replace("_", " ")
    action = (action or "LEARN").upper()
    card_type = {
        "LEARN": "concept_intro",
        "REVISE": "concept_intro",
        "PREREQ": "prerequisite_alert",
        "PRACTICE": "practice_prompt",
        "PYQ": "practice_prompt",
        "QUIZ": "practice_prompt",
        "MOCK_SLICE": "practice_prompt",
    }.get(action, "concept_intro")

    return {
        "card_type": card_type,
        "title": title,
        "duration_min": duration_min,
        "proficiency_pct": proficiency_pct,
        "weak_prereq": weak_prereq,
        "preview_text": reason or f"{action.replace('_', ' ').title()} · {title}",
        "image_url": None,
        "actions": ["start", "skip", "why"],
        "concept_id": concept_id,
        "action": action,
    }


def prepare_sandbox_state_overrides(
    *,
    action: str,
    concept_id: str,
    concept_name: Optional[str] = None,
    roadmap_node_id: Optional[str] = None,
    content_type: Optional[str] = None,
    card_meta: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    action = (action or "LEARN").upper()
    query = action_to_query(action, concept_id, concept_name)
    overrides: Dict[str, Any] = {
        "roadmap_concept_id": concept_id,
        "roadmap_node_id": roadmap_node_id,
        "content_type_requested": content_type or _CONTENT_TYPES.get(action, "theory"),
        "sandbox_action": action,
        "sandbox_card": card_meta
        or build_sandbox_card(action=action, concept_id=concept_id, concept_name=concept_name),
    }
    return query, overrides
