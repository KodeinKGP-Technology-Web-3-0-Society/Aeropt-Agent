"""Doubt Resolver product agent — triages free-form doubts and delegates."""

from __future__ import annotations

import re
from typing import Any, Dict

from agents.concept_resolver import concept_resolver_node
from models.state import TutorState

_EXPLAIN_RE = re.compile(
    r"\b(explain|what is|how does|why does|tell me about|help me understand)\b",
    re.IGNORECASE,
)
_PRACTICE_SANDBOX = frozenset({"PRACTICE", "PYQ", "QUIZ"})


def _choose_delegation(state: TutorState) -> str:
    intent = state.get("intent", "doubt")
    query = state.get("query") or ""
    sandbox = (state.get("sandbox_action") or "").upper()

    if intent in ("practice_request", "quiz_request") or sandbox in _PRACTICE_SANDBOX:
        return "practice_coach"
    if state.get("has_graded_outcome") and state.get("is_correct") is False:
        return "tutor"
    if _EXPLAIN_RE.search(query) or intent == "doubt":
        return "tutor"
    return "tutor"


def doubt_resolver_agent_node(state: TutorState) -> Dict[str, Any]:
    resolver_out = concept_resolver_node(state)
    updates: Dict[str, Any] = {
        "active_agent": "doubt_resolver",
        "agent_used": "doubt_resolver",
    }
    updates.update(resolver_out)

    if resolver_out.get("response"):
        return updates

    target = _choose_delegation({**state, **resolver_out})
    updates["active_agent"] = target
    updates["delegation_target"] = target
    if target == "practice_coach" and not updates.get("content_type_requested"):
        updates["content_type_requested"] = "practice"
    return updates
