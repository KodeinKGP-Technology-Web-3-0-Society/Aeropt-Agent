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
from typing import Any, Dict

from openai import OpenAI

import config
from agents.answer_checker import CheckResult, VERDICT_SKIPPED, check_answer_streaming
from agents.runtime.loop import run_agent_loop
from agents.runtime.toolbelt import TUTOR_SCOPES, ToolContext, Toolbelt, registry
from agents.runtime.trace import AgentTrace, TraceStep
from db.supabase_client import db
from models.state import TutorState
from utils.skills import load_skill
from utils.stream import emit_text, stream_callback_var

logger = logging.getLogger("jee_tutor.learning_agent")

_llm_client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)

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
    if not (state.get("classroom_surface") or "").strip():
        return _CHAT_MOVE, _CHAT_REVEAL

    # 1. Options requested (explicit structured flag or state signal)
    query_clean = (query or "").lower().strip()
    if (
        state.get("student_requests_options")
        or state.get("options_requested")
        or query_clean == "give me options"
    ):
        return _OPTIONS_MOVE, 2

    # 2. Problem state driven reveal ladder
    prob_state = dict(state.get("problem_state") or {})
    asked_answer = int(prob_state.get("student_asked_for_answer") or 0)
    if state.get("student_requests_solution") or query_clean == "tell me the answer":
        asked_answer += 1

    attempts = int(prob_state.get("attempts_on_problem") or 0)

    if asked_answer >= 2:
        return "Student explicitly requested answer again. Tell them the full solution.", 4
    if asked_answer == 1:
        return "Student requested help/answer. Escalate: show up to sticking point, ask one question.", 3

    if attempts == 0:
        return "First turn on problem. Confirm understanding or probe. Do not reveal answer.", 0
    elif attempts == 1:
        return "Nudge them based on their attempt.", 1
    elif attempts == 2:
        return "Hint at the core principle they are missing.", 2
    else:
        return "They are stuck. Show solution up to the sticking point and ask a question.", 3


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

    if draft and text and len(draft) >= 60:
        import difflib

        ratio = difflib.SequenceMatcher(None, draft, text).ratio()
        if ratio >= 0.9:
            logger.info("Tutor copied composer draft verbatim (similarity=%.2f) — using draft", ratio)
            return draft

    return text


def learning_agent_turn(state: TutorState) -> Dict[str, Any]:
    """Run one full autonomous turn and return the state updates for it.

    Returns a dict shaped like any other node's output: `response`, `agent_used`
    and whatever tools staged along the way.
    """
    _import_tools()
    started = time.time()

    from utils.llm import UsageMeter, usage_meter
    from utils.skills import get_skill_version

    meter = UsageMeter()
    u_token = usage_meter.set(meter)

    user_id = state["user_id"]
    session_id = state["session_id"]
    query = (state.get("query") or "").strip()

    trace = AgentTrace(
        user_id=user_id,
        session_id=session_id,
        turn_index=state.get("interaction_count"),
        route="agent_loop",
        prompt_version=get_skill_version("learning_agent"),
    )

    def _finalize_trace():
        trace.llm_calls = meter.llm_calls
        trace.input_tokens = meter.prompt_tokens
        trace.output_tokens = meter.completion_tokens
        trace.cached_tokens = meter.cached_tokens
        trace.persist()

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
        _finalize_trace()
        usage_meter.reset(u_token)
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

    from agents.runtime.budget import TurnBudget

    budget = TurnBudget.from_duration(config.LEARNING_AGENT_TIME_BUDGET_S)

    working_state: Dict[str, Any] = dict(state)
    working_state["recalled_memories"] = memories

    prob_state = dict(
        working_state.get("problem_state")
        or (working_state.get("carry") or {}).get("problem_state")
        or {
            "problem_key": "",
            "attempts_on_problem": 0,
            "hints_given": 0,
            "student_asked_for_answer": 0,
        }
    )
    if working_state.get("note_problem"):
        new_key = str(working_state.get("note_problem"))
        if new_key != prob_state.get("problem_key"):
            prob_state = {
                "problem_key": new_key,
                "attempts_on_problem": 0,
                "hints_given": 0,
                "student_asked_for_answer": 0,
            }
    if working_state.get("student_requests_solution"):
        prob_state["student_asked_for_answer"] = int(prob_state.get("student_asked_for_answer") or 0) + 1

    working_state["problem_state"] = prob_state

    ctx = ToolContext(
        user_id=user_id,
        session_id=session_id,
        state=working_state,
        trace=trace,
        budget=budget,
    )
    toolbelt = Toolbelt(registry().values(), ctx, allowed_scopes=TUTOR_SCOPES)

    move, reveal = _decide_move_and_reveal(query, working_state)
    trace.note(f"move={move}, reveal={reveal}")

    from agents.runtime import context

    skill = load_skill("learning_agent") or {}
    stable_prompt = skill.get("stable") or skill.get("system_prompt")
    if not stable_prompt:
        logger.error("learning_agent skill prompt missing")
        return {"error": "Missing learning_agent skill prompt."}

    sections = context.build(
        state=working_state,
        stable_system_prompt=stable_prompt,
        memories=memories,
        scaffolding_move=move,
        scaffolding_reveal=reveal,
    )

    from services import progress

    progress.report("stage", "thinking")
    priming = [sections.dynamic_message(), *sections.recent_turns]
    result = run_agent_loop(
        system_prompt=sections.stable_system_prompt,
        user_message=query,
        toolbelt=toolbelt,
        model=config.LEARNING_AGENT_MODEL,
        fallback_models=config.LEARNING_AGENT_FALLBACK_MODELS,
        priming_messages=priming,
        max_steps=int(state.get("classroom_max_steps") or config.LEARNING_AGENT_MAX_STEPS),
        time_budget_s=config.LEARNING_AGENT_TIME_BUDGET_S,
        temperature=config.LEARNING_AGENT_TEMPERATURE,
        client=_llm_client,
        trace=trace,
        label="tutor",
    )

    if result.stop_reason == "cancelled":
        _finalize_trace()
        usage_meter.reset(u_token)
        return {
            "cancelled": True,
            "response": "",
            "error": None,
            "agent_stop_reason": "cancelled",
            "agent_trace_path": trace.path(),
        }

    answer = _resolve_final_text(result.text, ctx)

    if answer:
        from agents.runtime.proof import ensure_proof

        answer = ensure_proof(answer, trace, ctx.state)

    if not answer:
        logger.error(
            "Agent turn produced no answer (stop_reason=%s, error=%s)",
            result.stop_reason,
            result.error,
        )
        trace.checker_verdict = VERDICT_SKIPPED
        _finalize_trace()
        usage_meter.reset(u_token)
        return {
            "response": _FALLBACK_REPLY,
            "agent_used": "learning_agent",
            "model_used": result.model_used,
            "error": None,
            "problem_state": prob_state,
            **ctx.updates,
        }

    # Fresh review before the student sees any of it.
    cb = stream_callback_var.get()
    
    progress.report("stage", "thinking", "done")
    progress.report("stage", "review")

    from agents.answer_checker import has_claims_to_check

    remaining = budget.remaining()
    if remaining <= 0:
        logger.warning("Turn budget exhausted before answer checker; skipping review.")
        checked = CheckResult(answer, "skipped:budget", ["Turn budget exhausted."])
        trace.checker_verdict = "skipped:budget"
        trace.add(TraceStep(kind="check", name="answer_checker", ok=True, result_preview="skipped:budget"))
    elif not has_claims_to_check(answer):
        logger.info("Draft has no analytical or exam claims; skipping checker review.")
        checked = CheckResult(answer, VERDICT_SKIPPED, ["No analytical or exam claims to review."])
        trace.checker_verdict = VERDICT_SKIPPED
        trace.add(TraceStep(kind="check", name="answer_checker", ok=True, result_preview="skipped:no_claims"))
    else:
        checker_timeout = min(15.0, remaining)
        checked = check_answer_streaming(
            query=query,
            draft=answer,
            state=ctx.state,
            trace=trace,
            timeout=checker_timeout,
        )
    
    progress.report("stage", "review", "done")

    # One single release gate on whatever text is final (draft or revision)
    from agents.runtime.release import release

    final_answer = release(checked.answer, ctx)
    checked.answer = final_answer

    if cb and final_answer:
        emit_text(final_answer, cb)

    updates: Dict[str, Any] = dict(ctx.updates)
    updates.pop("draft_response", None)
    updates.pop("draft_author", None)
    updates.pop("draft_model", None)

    final_prob_state = dict(ctx.state.get("problem_state") or prob_state)
    if final_prob_state.get("problem_key") and not ctx.state.get("graded_this_turn"):
        final_prob_state["attempts_on_problem"] = int(final_prob_state.get("attempts_on_problem") or 0) + 1

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
            "problem_state": final_prob_state,
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
    _finalize_trace()
    usage_meter.reset(u_token)
    return updates


def learning_agent_node(state: TutorState) -> Dict[str, Any]:
    """Graph-node wrapper. Never raises into the graph."""
    try:
        return learning_agent_turn(state)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Learning agent turn failed")
        return {"error": f"Learning agent failed: {exc}"}
