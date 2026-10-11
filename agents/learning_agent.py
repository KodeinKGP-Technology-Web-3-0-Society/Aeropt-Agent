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
import re
import time
from typing import Any, Dict, List, Optional

from openai import OpenAI

import config
from agents.answer_checker import VERDICT_SKIPPED, check_answer, check_answer_streaming
from agents.runtime.loop import run_agent_loop
from agents.runtime.toolbelt import TUTOR_SCOPES, ToolContext, Toolbelt, registry
from agents.runtime.trace import AgentTrace, TraceStep
from db.supabase_client import db
from models.state import TutorState
from utils.skills import load_skill
from utils.stream import emit_text, stream_callback_var

logger = logging.getLogger("jee_tutor.learning_agent")

_groq = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)

#: The tutor's reply when it accepts an expert's draft verbatim.
_USE_DRAFT_TOKEN = "USE_DRAFT"

_FALLBACK_REPLY = (
    "I hit a problem working through that one. Could you send it again, or "
    "rephrase it slightly? If you can include your own working, I can show you "
    "exactly where it goes off track."
)

# Another school subject. This is not a guess about a physics question; it is
# the syllabus boundary. The tutor still owns the reply, and it does not retrieve.
_OUTSIDE_PHYSICS = re.compile(
    r"\b(sn1|sn2|carbocation|organic chemistry|alkyl halide|nucleophilic substitution|"
    r"grignard|benzene|stoichiometry)\b",
    re.IGNORECASE,
)
_PHYSICS_ONLY_REPLY = (
    "I only teach physics for JEE. I won't invent a chemistry mechanism or a "
    "reaction scheme. Ask a physics problem — rotation, electrostatics, or the "
    "step you're stuck on — and I'll work that properly."
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
    import mcp_tools.diagram_tools      # noqa: F401



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


_OPTIONS_PHRASES = ("option", "choices", "multiple choice", "mcq", "hint", "clue")

_OPTIONS_MOVE = (
    "Student asked for options or a hint. Do not probe again and do not give the answer. "
    "Offer 3-4 labelled choices (A, B, C, D) to the question you just asked: one correct, "
    "the others plausible slips (inverted ratio, wrong quantity). Ask which they pick and why."
)


# General chat (the /chat page) sends no room surface. There is no lesson plan
# to pace, so the tutor reads what the student is asking for and picks the mode
# itself. One fixed mode failed both ways: always-Socratic dodged plain doubts,
# always-direct solved a question the student had only asked to see.
_CHAT_MOVE = (
    "General chat. Read what the student wants from THIS message and the conversation, "
    "then choose the mode yourself:\n"
    "  - They ask for a question or practice ('give me a question', 'a PYQ on X'): give "
    "ONLY the question (and its diagram/options). No solution, no hints, no final answer. "
    "End by inviting their attempt, and say they can ask for a hint or the solution.\n"
    "  - They ask for a question AND its solution ('solve', 'with solution', 'explain this "
    "problem'): give both, the question first, then the worked solution.\n"
    "  - They are attempting a question already on the table (an answer, working, or a "
    "partial idea): do not solve it for them. Say what is right, then guide the next step "
    "Socratically: one hint or one question at a time.\n"
    "  - They are stuck on that question ('hint', 'stuck', 'I don't know'): one hint that "
    "names the principle, not the steps. After a second hint, or when they ask for it, "
    "give the full solution.\n"
    "  - They ask for options on a question already on the table: 3-4 labelled choices "
    "(A-D), one correct, and nothing else.\n"
    "  - They ask a conceptual doubt ('why', 'what is', 'explain', 'difference between'): "
    "answer it directly and completely. Do not turn it into a quiz; ask one short check "
    "question only when a common misconception is worth testing, never on two replies in a row.\n"
    "When unsure which they want, prefer the lighter reveal: a question can always be "
    "followed by its solution, but a solution cannot be taken back."
)
_CHAT_REVEAL = "your choice, set by the mode you picked above (question only = 0, hint = 2, explanation or requested solution = 4)"


def _decide_move_and_reveal(query: str, state: TutorState) -> tuple[str, int | str]:
    query_lower = query.lower()
    if not (state.get("classroom_surface") or "").strip():
        return _CHAT_MOVE, _CHAT_REVEAL
    if any(ph in query_lower for ph in _OPTIONS_PHRASES) and len(query_lower.split()) <= 12:
        return _OPTIONS_MOVE, 2
    escalate = any(ph in query_lower for ph in ["give up", "show me", "tell me", "don't know", "answer", "stuck"])
    
    reveal = 0
    move = "Withhold answer. Probe for their thoughts or confirm success."
    
    recent_turns = state.get("recent_turns") or []
    prior_escalations = sum(
        1 for t in recent_turns 
        if t.get("role") == "user" and any(ph in (t.get("content") or "").lower() for ph in ["give up", "show me", "tell me", "don't know", "answer", "stuck"])
    )

    if escalate:
        if prior_escalations >= 1:
            reveal = 4
            move = "Student explicitly requested answer again. Tell them the full solution."
        else:
            reveal = 3
            move = "Student requested help/answer. Escalate: show up to sticking point, ask one question."
    else:
        turn = int(state.get("interaction_count") or 1)
        if turn == 1:
            reveal = 0
            move = "First turn. Confirm understanding or probe. Do not reveal answer."
        elif turn == 2:
            reveal = 1
            move = "Nudge them based on their attempt."
        elif turn == 3:
            reveal = 2
            move = "Hint at the core principle they are missing."
        else:
            reveal = 3
            move = "They are stuck. Show solution up to the sticking point and ask a question."
            
    return move, reveal


def _with_progress(memory_text: str, user_id: str, session_id: str = "") -> str:
    """Memories say who the student is; the digest says how they are doing —
    weak and mastered concepts, mistake pattern, reviews due — so a question
    in chat is answered knowing what the student has already practised. The
    cross-room digest says what they were just doing in the Study Room."""
    from services.cross_room import recent_activity
    from services.progress_report import progress_digest

    digest = progress_digest(user_id) if user_id else ""
    if digest:
        memory_text = f"{memory_text}\n\nProgress so far (from their practice record):\n{digest}"
    activity = recent_activity(user_id, session_id)
    if activity:
        memory_text = f"{memory_text}\n\n{activity}"
    return memory_text


def _build_system_prompt(state: TutorState, memories: List[Dict[str, Any]], scaffolding_move: str, scaffolding_reveal: int | str) -> str:
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
        "{{learner_memory}}": _with_progress(_format_memory(memories), state.get("user_id") or "", state.get("session_id") or ""),
        "{{session_summary}}": session_text,
        "{{pending_suggestions}}": _format_pending_suggestions(state),
        "{{scaffolding_move}}": scaffolding_move,
        "{{scaffolding_reveal}}": str(scaffolding_reveal),
    }
    for token, value in replacements.items():
        prompt = prompt.replace(token, value)
    job = (state.get("classroom_job") or "").strip()
    if job:
        prompt = f"{prompt}\n\n{job}"
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
    # The doubt room already ran check_student_work before this loop. Record it
    # on the trace so the turn shows the check that the student is about to hear.
    if "Working check:" in (state.get("classroom_job") or ""):
        trace.add(
            TraceStep(
                kind="tool",
                name="check_student_work",
                ok=True,
                result_preview="checked the handed working before the reply",
            )
        )

    if _OUTSIDE_PHYSICS.search(query):
        trace.note("outside physics syllabus")
        trace.checker_verdict = "skipped"
        trace.persist()
        return {
            "response": _PHYSICS_ONLY_REPLY,
            "agent_used": "learning_agent",
            "error": None,
            "agent_trace_path": trace.path(),
            "primary_concept_id": "",
        }

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

    move, reveal = _decide_move_and_reveal(query, working_state)
    trace.note(f"move={move}, reveal={reveal}")

    try:
        system_prompt = _build_system_prompt(state, memories, move, reveal)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return {"error": str(exc)}

    from services import progress

    progress.report("stage", "thinking")
    result = run_agent_loop(
        system_prompt=system_prompt,
        user_message=query,
        toolbelt=toolbelt,
        model=config.LEARNING_AGENT_MODEL,
        fallback_models=config.LEARNING_AGENT_FALLBACK_MODELS,
        priming_messages=_priming_messages(state),
        max_steps=int(state.get("classroom_max_steps") or config.LEARNING_AGENT_MAX_STEPS),
        time_budget_s=config.LEARNING_AGENT_TIME_BUDGET_S,
        temperature=config.LEARNING_AGENT_TEMPERATURE,
        client=_groq,
        trace=trace,
        label="tutor",
    )

    answer = _resolve_final_text(result.text, ctx)

    if answer:
        from agents.runtime.provenance import check_exam_claims, render_inline_tikz, strip_foreign_images

        answer, tikz_drawn, tikz_dropped = render_inline_tikz(answer)
        if tikz_drawn or tikz_dropped:
            trace.note(f"provenance: inline TikZ drawn {tikz_drawn}, removed {tikz_dropped}")
        answer, dropped = strip_foreign_images(answer)
        chunks = list(ctx.updates.get("retrieved_chunks") or []) + list(state.get("retrieved_chunks") or [])
        history = [t.get("content") or "" for t in (state.get("recent_turns") or [])]
        answer, unverified = check_exam_claims(answer, chunks, history)
        if dropped or unverified:
            trace.note(f"provenance: dropped {dropped} image(s); unverified exam claims {unverified}")

    if answer:
        from agents.runtime.proof import ensure_proof

        answer = ensure_proof(answer, trace)

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
    
    progress.report("stage", "thinking", "done")
    progress.report("stage", "review")
    checked = check_answer_streaming(
        query=query,
        draft=answer,
        state=ctx.state,
        trace=trace,
        on_token=cb,
    )
    
    progress.report("stage", "review", "done")
    if cb and checked.verdict != "revise" and checked.answer:
        # An approved draft arrives whole; a revision already streamed above.
        emit_text(checked.answer, cb)

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
