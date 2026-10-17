"""The Background Learning Coach: the same agent, between sessions.

Not a separate system and not a notification rule engine. It is the Learning
Agent looking at a student's records after something happened, with the same
judgement it uses in conversation and a narrower set of things it may do.

The design point worth protecting: **doing nothing is the normal outcome.** The
version of this that fires a notification after three wrong answers is the
version students learn to ignore, and it is also the version that cannot tell a
bad evening from a real misunderstanding. So the coach gets the whole picture
and decides, and the plumbing here does not second-guess it in either
direction — there is no threshold that forces it to act, and no gate that stops
it when it should.

What it may do: read anything about its learner, write a durable memory, and
schedule one review. What it may not do: speak to the student. A coach run
produces state and an internal note, never a message. Anything student-facing
goes through a live turn, where the student can answer back.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from openai import OpenAI

import config
from agents.runtime.loop import run_agent_loop
from agents.runtime.toolbelt import (
    SCOPE_READ,
    SCOPE_SCHEDULE,
    SCOPE_WRITE_MEMORY,
    ToolContext,
    Toolbelt,
    registry,
)
from agents.runtime.trace import AgentTrace
from db.supabase_client import db
from utils.skills import load_skill

logger = logging.getLogger("jee_tutor.learning_coach")

_groq = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)

#: The coach may learn and remember, and may schedule. It may not compose
#: anything a student will read, and it may not grade an attempt that did not
#: happen — so no `compose` and no `write:progress`.
COACH_SCOPES = frozenset({SCOPE_READ, SCOPE_WRITE_MEMORY, SCOPE_SCHEDULE})

#: It is looking at history, not answering someone. A slow run costs nothing.
_COACH_TIME_BUDGET_S = 90.0


def _import_tools() -> None:
    import mcp_tools.compute_tools      # noqa: F401
    import mcp_tools.curriculum_tools   # noqa: F401
    import mcp_tools.learner_tools      # noqa: F401


def _format_memory(memories: List[Dict[str, Any]]) -> str:
    if not memories:
        return "Nothing remembered about this student yet."
    return "\n".join(
        f"- [{m.get('kind')}] {m.get('fact')} (confidence {float(m.get('confidence') or 0.6):.1f})"
        for m in memories
    )


def _gather_activity(user_id: str) -> str:
    """Assemble the record the coach reasons over.

    Deliberately plain: counts and outcomes, not a narrative. The coach's job is
    to read the pattern, and pre-digesting it into "the student is struggling"
    would be making its decision for it.
    """
    lines: List[str] = []

    try:
        weak = db.read_weak_user_concept_states(user_id) or []
        if weak:
            lines.append("Concepts currently flagged weak:")
            for w in weak[:6]:
                lines.append(
                    f"  - {w.get('concept_id')} "
                    f"(proficiency {float(w.get('proficiency_score') or 0):.2f}, "
                    f"{w.get('struggle_streak') or 0} wrong in a row)"
                )
        else:
            lines.append("No concepts are currently flagged weak.")
    except Exception:
        logger.debug("weak concept read failed", exc_info=True)

    try:
        states = db.read_all_user_concept_states(user_id) or []
        attempted = [s for s in states if (s.get("attempts") or 0) > 0]
        mastered = [
            s
            for s in attempted
            if float(s.get("proficiency_score") or 0) >= config.MASTERY_THRESHOLD
        ]
        lines.append(
            f"Overall: {len(attempted)} of {len(states)} concepts attempted, "
            f"{len(mastered)} at mastery."
        )

        error_totals: Dict[str, int] = {}
        for s in attempted:
            for kind, count in (s.get("error_type_dist") or {}).items():
                error_totals[kind] = error_totals.get(kind, 0) + int(count or 0)
        if any(error_totals.values()):
            spread = ", ".join(
                f"{k}: {v}" for k, v in sorted(error_totals.items(), key=lambda kv: -kv[1]) if v
            )
            lines.append(f"Mistakes by type, across all concepts: {spread}")
    except Exception:
        logger.debug("concept state read failed", exc_info=True)

    try:
        due = db.read_due_reviews(user_id) or []
        if due:
            lines.append(f"Reviews already scheduled and now due: {len(due)}")
            for r in due[:4]:
                lines.append(f"  - {r.get('concept_id')}: {r.get('reason') or 'no reason recorded'}")
    except Exception:
        logger.debug("due review read failed", exc_info=True)

    try:
        sessions = db.list_user_sessions(user_id, limit=3) or []
        if sessions:
            lines.append(f"Recent sessions: {len(sessions)}")
            for s in sessions:
                summary = (s.get("session_summary") or "").strip()
                if summary:
                    lines.append(f"  - {summary[:300]}")
    except Exception:
        logger.debug("session list read failed", exc_info=True)

    return "\n".join(lines) if lines else "No activity on record."


def run_learning_coach(
    user_id: str,
    *,
    trigger: str = "scheduled check",
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one coach pass over a student's records.

    Returns what it decided, for logging and for the caller. Never raises:
    background work failing must not affect anything the student is doing.
    """
    _import_tools()
    started = time.time()

    trace = AgentTrace(
        user_id=user_id,
        session_id=session_id or "",
        route="coach",
    )

    try:
        memories = db.read_learner_memories(user_id, limit=10) or []
    except Exception:
        memories = []

    skill = load_skill("learning_coach") or {}
    prompt = skill.get("system_prompt") or ""
    if not prompt:
        return {"ok": False, "error": "skills/learning_coach.yaml is missing."}

    prompt = (
        prompt.replace("{{trigger}}", trigger)
        .replace("{{learner_memory}}", _format_memory(memories))
        .replace("{{activity}}", _gather_activity(user_id))
    )

    ctx = ToolContext(
        user_id=user_id,
        session_id=session_id or "",
        # Marks writes as coach-authored rather than tutor-authored, so a
        # memory's provenance says which one formed the belief.
        state={"_coach_run": True, "recalled_memories": memories},
        trace=trace,
    )
    toolbelt = Toolbelt(registry().values(), ctx, allowed_scopes=COACH_SCOPES)

    result = run_agent_loop(
        system_prompt=prompt,
        user_message=(
            "Review this student's progress and decide what, if anything, to do. "
            "Doing nothing is a valid answer."
        ),
        toolbelt=toolbelt,
        model=config.LEARNING_COACH_MODEL,
        fallback_models=config.LEARNING_AGENT_FALLBACK_MODELS,
        max_steps=config.LEARNING_COACH_MAX_STEPS,
        time_budget_s=_COACH_TIME_BUDGET_S,
        temperature=0.2,
        max_tokens=1200,
        client=_groq,
        trace=trace,
        label="coach",
        lane="background",
    )

    actions = [s.name for s in trace.steps if s.kind == "tool" and s.ok]

    # `acted` must mean "something changed", not "a write tool was called". When
    # migration 006 has not been applied the memory and review tools return
    # {saved: False} / {scheduled: False} rather than failing — a successful call
    # that persisted nothing. Reporting that as action would make a coach that
    # cannot write anything look like one doing its job.
    def _persisted(step) -> bool:
        preview = step.result_preview
        if not isinstance(preview, dict):
            return False
        return bool(preview.get("saved") or preview.get("scheduled"))

    write_steps = [
        s
        for s in trace.steps
        if s.kind == "tool"
        and s.ok
        and s.name in {"remember_about_learner", "schedule_review"}
    ]
    acted = any(_persisted(s) for s in write_steps)
    attempted_but_not_persisted = bool(write_steps) and not acted

    if attempted_but_not_persisted:
        logger.warning(
            "Coach decided to act for %s but nothing persisted — learner memory "
            "is not enabled (apply migration 006).",
            user_id,
        )

    logger.info(
        "coach run user=%s trigger=%s actions=%s acted=%s %dms",
        user_id,
        trigger,
        actions or "(none)",
        acted,
        int((time.time() - started) * 1000),
    )
    trace.persist()

    return {
        "ok": result.ok,
        "acted": acted,
        "actions": actions,
        "memory_unavailable": attempted_but_not_persisted,
        "note": result.text,
        "trace_path": trace.path(),
        "stop_reason": result.stop_reason,
        "error": result.error,
    }


def coach_user(user_id: str, **kwargs: Any) -> Dict[str, Any]:
    """Entry point that never raises, for callers running this in the background."""
    try:
        return run_learning_coach(user_id, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Learning coach failed for %s", user_id)
        return {"ok": False, "acted": False, "actions": [], "error": str(exc)}
