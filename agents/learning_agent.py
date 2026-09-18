"""The Main Tutor: one autonomous agent that owns the student's whole turn.

This replaces the orchestrator's closed intent enum and the hand-wired
conditional edges in `graph/workflow.py`. There is no routing table here. The
agent is given the student's message, what is known about them, a toolbelt and a
budget, and it decides the rest.

What the platform still guarantees, because these are not judgement calls:

  * identity and scoping — a turn only ever touches its own learner's data;
  * determinism where it matters — retrieval order and proficiency maths are
    tools, not things a model improvises;
  * bounds — steps, wall-clock, and a forced close so an overrun still answers;
  * review — every substantial reply passes the Answer Checker first;
  * a trace — the path an autonomous agent took is recorded, since it can no
    longer be read off a diagram.

Everything above that line is the agent's call.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from groq import Groq

import config
from agents.answer_checker import VERDICT_SKIPPED, check_answer, check_answer_streaming
from agents.runtime.loop import run_agent_loop
from agents.runtime.toolbelt import TUTOR_SCOPES, ToolContext, Toolbelt, registry
from agents.runtime.trace import AgentTrace
from db.supabase_client import db
from models.state import TutorState
from utils.skills import load_skill
from utils.stream import stream_callback_var

logger = logging.getLogger("jee_tutor.learning_agent")

_groq = Groq(api_key=config.GROQ_API_KEY)

#: The tutor's reply when it accepts an expert's draft verbatim.
_USE_DRAFT_TOKEN = "USE_DRAFT"

_FALLBACK_REPLY = (
    "I hit a problem working through that one. Could you send it again, or "
    "rephrase it slightly? If you can include your own working, I can show you "
    "exactly where it goes off track."
)


def _import_tools() -> None:
    """Import every tool module so the registry is populated.

    Registration happens at import time via the @tool decorator, so a tool
    module that nothing imports is a tool the agent silently cannot use. This is
    the one place that list lives.
    """
    import mcp_tools.compute_tools      # noqa: F401
    import mcp_tools.curriculum_tools   # noqa: F401
    import mcp_tools.learner_tools      # noqa: F401
    import mcp_tools.physics_tools      # noqa: F401
    import mcp_tools.symbolic_tools     # noqa: F401
    import mcp_tools.handoff            # noqa: F401



#: Trace signature -> the capability the UI roster panel should highlight.
#: The roster is a product concept with a fixed vocabulary; this reports which
#: of those the turn actually exercised, rather than the old classifier's guess
#: about what the turn was going to be.
_ROSTER_BY_EXPERT = {
    "practice_set": "practice_coach",
    "quiz_builder": "practice_coach",
    "error_locator": "doubt_resolver",
    "alt_explainer": "doubt_resolver",
    "comparer": "doubt_resolver",
    "verifier": "tutor",
    "worked_solution": "tutor",
    "socratic_guide": "tutor",
}


def _derive_active_agent(trace: AgentTrace) -> str:
    """Name the capability this turn exercised, for the roster panel."""
    for step in reversed(trace.steps):
        if step.kind == "handoff" and step.name in _ROSTER_BY_EXPERT:
            return _ROSTER_BY_EXPERT[step.name]
    for step in trace.steps:
        if step.kind == "tool" and step.name == "get_syllabus_status":
            return "syllabus"
    return "tutor"


def _format_memory(memories: List[Dict[str, Any]]) -> str:
    if not memories:
        return (
            "Nothing remembered about this student yet. Treat this as a first "
            "meeting and pay attention to what works for them."
        )
    lines = []
    for m in memories:
        scope = f" (on {m['subject_key']})" if m.get("subject_key") else ""
        confidence = float(m.get("confidence") or 0.6)
        hedge = "" if confidence >= 0.75 else " — only seen once, treat as tentative"
        lines.append(f"- [{m.get('kind')}]{scope} {m.get('fact')}{hedge}")
    return "\n".join(lines)


def _format_roadmap_hint(state: TutorState) -> str:
    stage = state.get("study_stage")
    concept = state.get("roadmap_concept_id")
    if not stage and not concept:
        return "Context:            open conversation, no set lesson"
    bits = []
    if concept:
        bits.append(f"working through {concept}")
    if stage:
        bits.append(f"at the '{stage}' stage")
    return "Context:            " + ", ".join(bits) + " from their study plan"


def _format_pending_suggestions(state: TutorState) -> str:
    """What was on offer at the end of the last turn.

    Without this the agent cannot act on "the second one" or "yes, that one" —
    it would have to guess which item the student meant, which is precisely the
    fuzzy keyword matching this architecture exists to remove.
    """
    suggestions = state.get("follow_up_suggestions") or []
    if not suggestions:
        return "Nothing was offered — they are starting from their own question."
    lines = [
        "You offered these. If they pick one, open it by content_id rather than "
        "searching again:"
    ]
    for i, s in enumerate(suggestions[:4], start=1):
        title = s.get("title") or s.get("content_id") or "(untitled)"
        ctype = s.get("content_type") or "material"
        lines.append(f"  {i}. {title} [{ctype}, content_id={s.get('content_id')}]")
    return "\n".join(lines)


def _build_system_prompt(state: TutorState, memories: List[Dict[str, Any]]) -> str:
    skill = load_skill("learning_agent") or {}
    prompt = skill.get("system_prompt") or ""
    if not prompt:
        raise RuntimeError(
            "skills/learning_agent.yaml is missing or has no system_prompt — the "
            "Main Tutor cannot run without its instructions."
        )

    summary = state.get("session_summary") or state.get("prior_session_summary")
    if summary:
        label = (
            "Earlier in this session" if state.get("session_summary") else "Last session"
        )
        session_text = f"{label}: {summary}"
    else:
        session_text = "This is the start of the conversation."

    replacements = {
        "{{target_exam}}": str(state.get("target_exam") or "JEE_ADVANCED"),
        "{{preferred_depth}}": str(state.get("preferred_depth") or "standard"),
        "{{interaction_count}}": str(state.get("interaction_count") or 1),
        "{{roadmap_hint}}": _format_roadmap_hint(state),
        "{{learner_memory}}": _format_memory(memories),
        "{{session_summary}}": session_text,
        "{{pending_suggestions}}": _format_pending_suggestions(state),
    }
    for token, value in replacements.items():
        prompt = prompt.replace(token, value)
    return prompt


def _priming_messages(state: TutorState) -> List[Dict[str, Any]]:
    """Recent conversation, as real turns rather than a summary blob."""
    messages: List[Dict[str, Any]] = []
    for turn in (state.get("recent_turns") or [])[-4:]:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        content = (turn.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content[:1500]})
    return messages


def _resolve_final_text(loop_text: str, ctx: ToolContext) -> str:
    """Turn the loop's last message into the reply, honouring USE_DRAFT."""
    text = (loop_text or "").strip()
    draft = (ctx.state.get("draft_response") or "").strip()

    if not text and draft:
        # The agent handed off, then produced nothing of its own. The draft is
        # the only real answer available, so send it rather than an empty turn.
        logger.info("Agent produced no text after a handoff — sending the draft.")
        return draft

    # Accept the token whether it is the whole reply or wrapped in a little prose.
    stripped = text.strip("*_` \n.")
    if draft and (stripped == _USE_DRAFT_TOKEN or text.upper().startswith(_USE_DRAFT_TOKEN)):
        return draft

    if draft and _USE_DRAFT_TOKEN in text.upper() and len(text) < len(draft) * 0.5:
        # It meant to accept the draft but added commentary around the token.
        return draft

    return text


def learning_agent_turn(state: TutorState) -> Dict[str, Any]:
    """Run one full autonomous turn and return the state updates for it.

    Returns a dict shaped like any other node's output: `response`, `agent_used`
    and whatever tools staged along the way.
    """
    _import_tools()
    started = time.time()

    user_id = state["user_id"]
    session_id = state["session_id"]
    query = (state.get("query") or "").strip()

    trace = AgentTrace(
        user_id=user_id,
        session_id=session_id,
        turn_index=state.get("interaction_count"),
        route="agent_loop",
    )

    # Recall before reasoning: the agent should start the turn already knowing
    # who it is talking to, rather than having to spend a tool call to find out.
    try:
        memories = db.read_learner_memories(user_id, limit=8) or []
    except Exception:
        logger.debug("learner memory recall failed", exc_info=True)
        memories = []

    working_state: Dict[str, Any] = dict(state)
    working_state["recalled_memories"] = memories

    ctx = ToolContext(
        user_id=user_id,
        session_id=session_id,
        state=working_state,
        trace=trace,
    )
    toolbelt = Toolbelt(registry().values(), ctx, allowed_scopes=TUTOR_SCOPES)

    try:
        system_prompt = _build_system_prompt(state, memories)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return {"error": str(exc)}

    result = run_agent_loop(
        system_prompt=system_prompt,
        user_message=query,
        toolbelt=toolbelt,
        model=config.LEARNING_AGENT_MODEL,
        fallback_models=config.LEARNING_AGENT_FALLBACK_MODELS,
        priming_messages=_priming_messages(state),
        max_steps=config.LEARNING_AGENT_MAX_STEPS,
        time_budget_s=config.LEARNING_AGENT_TIME_BUDGET_S,
        temperature=config.LEARNING_AGENT_TEMPERATURE,
        client=_groq,
        trace=trace,
        label="tutor",
    )

    answer = _resolve_final_text(result.text, ctx)

    if not answer:
        logger.error(
            "Agent turn produced no answer (stop_reason=%s, error=%s)",
            result.stop_reason,
            result.error,
        )
        trace.checker_verdict = VERDICT_SKIPPED
        trace.persist()
        return {
            "response": _FALLBACK_REPLY,
            "agent_used": "learning_agent",
            "model_used": result.model_used,
            "error": None,
            **ctx.updates,
        }

    # Fresh review before the student sees any of it.
    cb = stream_callback_var.get()
    
    checked = check_answer_streaming(
        query=query,
        draft=answer,
        state=ctx.state,
        trace=trace,
        on_token=cb,
    )
    
    if cb and checked.verdict != "revise" and checked.answer:
        # Fake-stream the draft
        words = checked.answer.split(" ")
        for i, w in enumerate(words):
            cb(w + (" " if i < len(words) - 1 else ""))
            time.sleep(0.01)

    updates: Dict[str, Any] = dict(ctx.updates)
    updates.pop("draft_response", None)
    updates.pop("draft_author", None)
    updates.pop("draft_model", None)

    updates.update(
        {
            "response": checked.answer,
            "agent_used": "learning_agent",
            "model_used": result.model_used,
            "error": None,
            "active_agent": _derive_active_agent(trace),
            "agent_trace_path": trace.path(),
            "agent_stop_reason": result.stop_reason,
            "checker_verdict": checked.verdict,
        }
    )

    if ctx.pending_permissions:
        updates["pending_permissions"] = ctx.pending_permissions

    logger.info(
        "agent turn user=%s session=%s steps=%d path=%s checker=%s %dms",
        user_id,
        session_id,
        result.steps_taken,
        " → ".join(trace.path()) or "(direct answer)",
        checked.verdict,
        int((time.time() - started) * 1000),
    )
    trace.persist()
    return updates


def learning_agent_node(state: TutorState) -> Dict[str, Any]:
    """Graph-node wrapper. Never raises into the graph."""
    try:
        return learning_agent_turn(state)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Learning agent turn failed")
        return {"error": f"Learning agent failed: {exc}"}
