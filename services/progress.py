"""Live "what the tutor is doing" events for a streaming turn.

The HTTP handler installs a reporter for the turn; the toolbelt reports each
tool as it starts and finishes, and nodes report their own stages (reviewing
the reply, scoring the session). The handler forwards them to the browser as
SSE `status` frames, so the student sees "Reading your course material…" rather
than three dots for 30 seconds.

A ContextVar rather than a global or the graph state: LangGraph runs nodes in
worker threads with the caller's context copied in, so the reporter follows the
turn without being serialised into state, and two concurrent turns never see
each other's reporter.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("jee_tutor.progress")

Reporter = Callable[[Dict[str, Any]], None]
_reporter: contextvars.ContextVar[Optional[Reporter]] = contextvars.ContextVar("study_progress", default=None)
# Set when the student presses Stop. The loop checks it between model steps and
# the toolbelt before each tool, so a turn sent by mistake ends within a step.
_cancel: contextvars.ContextVar[Optional[threading.Event]] = contextvars.ContextVar("turn_cancel", default=None)


def install_cancel(event: Optional[threading.Event]) -> contextvars.Token:
    return _cancel.set(event)


def reset_cancel(token: contextvars.Token) -> None:
    _cancel.reset(token)


def is_cancelled() -> bool:
    event = _cancel.get()
    return bool(event and event.is_set())

# Student-facing wording per tool. Anything missing falls back to a generic line.
TOOL_LABELS: Dict[str, str] = {
    "retrieve_content": "Reading your course material",
    "open_material": "Opening the full worked example",
    "serve_practice_question": "Picking a question at your level",
    "grade_practice_answer": "Checking your answer against the key",
    "transition_phase": "Moving to the next stage",
    "complete_study_session": "Wrapping up the session",
    "set_lesson_plan": "Planning the lesson",
    "complete_lesson_part": "Marking this part done",
    "check_student_work": "Going through your working",
    "calculate": "Working out the numbers",
    "verify_result": "Double-checking the result",
    "check_dimensions": "Checking the units",
    "solve_symbolically": "Solving the algebra",
    "convert_units": "Converting units",
    "draw_diagram": "Drawing a diagram",
    "remember_about_learner": "Noting how you learn",
    "schedule_review": "Scheduling a review",
    "resolve_concept": "Finding the right topic",
    "record_attempt": "Recording your attempt",
    "get_learner_context": "Recalling your progress",
    "suggest_next_material": "Choosing what to study next",
}

STAGE_LABELS: Dict[str, str] = {
    "context": "Recalling what you've done before",
    "thinking": "Thinking",
    "review": "Reviewing the reply before sending",
    "evaluation": "Scoring your session",
}


def install(reporter: Optional[Reporter]) -> contextvars.Token:
    return _reporter.set(reporter)


def reset(token: contextvars.Token) -> None:
    _reporter.reset(token)


def report(kind: str, name: str, state: str = "start", **extra: Any) -> None:
    """kind: 'tool' | 'stage'; state: 'start' | 'done' | 'error'. Never raises."""
    fn = _reporter.get()
    if fn is None:
        return
    labels = TOOL_LABELS if kind == "tool" else STAGE_LABELS
    event = {
        "kind": kind,
        "name": name,
        "state": state,
        "label": labels.get(name) or ("Using " + name.replace("_", " ") if kind == "tool" else name),
        **extra,
    }
    try:
        fn(event)
    except Exception:
        logger.debug("progress reporter failed", exc_info=True)
