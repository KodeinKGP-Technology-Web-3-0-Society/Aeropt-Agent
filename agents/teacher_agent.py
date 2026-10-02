"""The Teacher Agent: owns the Study Room's entire learning flow.

This agent operates similarly to the Main Tutor but follows a structured pedagogical
flow (Learn → Examples → Practice → Evaluation) rather than open-ended tutoring.
It relies on study_tools.py to manage the session state and phase transitions.

Each turn it starts already knowing three things, so none of them costs a tool
call or depends on the model remembering to ask:
  * the conversation so far (real turns, not a summary) — without it the teacher
    could not tell that the student's message answers the question it just asked;
  * a learner snapshot (services/study_context.py) — past sessions, prerequisites
    and how they went, related concepts studied, recent mistakes, memories;
  * the practice question on the table, with its stored solution, so grading is
    against the answer key rather than the model's own working.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

import config
from agents.answer_checker import check_answer
from agents.runtime.loop import run_agent_loop
from agents.runtime.toolbelt import TUTOR_SCOPES, ToolContext, Toolbelt, registry
from agents.runtime.trace import AgentTrace
from db.supabase_client import db
from models.study_state import StudyRoomState
from services.study_context import build_learner_snapshot, format_snapshot
from utils.skills import load_skill

logger = logging.getLogger("jee_tutor.teacher_agent")

# The Study Room's own toolset. Context loaders (get_study_context,
# get_learner_context) are left out because their output is already in the
# prompt; record_attempt is left out because grade_practice_answer records the
# attempt itself and two tools for one attempt double-counts it.
_TEACHER_TOOLS = frozenset({
    "retrieve_content",
    "open_material",
    "serve_practice_question",
    "grade_practice_answer",
    "transition_phase",
    "complete_study_session",
    "check_student_work",
    "calculate",
    "check_dimensions",
    "verify_result",
    "solve_symbolically",
    "convert_units",
    "remember_about_learner",
    "schedule_review",
    "draw_diagram",
    "set_lesson_plan",
    "complete_lesson_part",
})

_HISTORY_TURNS = 10

# "Explain the one I just answered", as opposed to "give me the next one".
_EXPLAIN_RE = re.compile(
    r"\b(explain|explanation|why|how (?:do|did|is|was)|walk me through|solution|solve|working|"
    r"wrong|mistake|doubt|didn'?t (?:get|understand)|don'?t (?:get|understand)|confus)",
    re.IGNORECASE,
)
_NEXT_RE = re.compile(r"\b(next|another|new|more) (?:question|one)\b", re.IGNORECASE)


def _wants_explanation_of_last(state: StudyRoomState, query: str) -> bool:
    """A question was just graded (none open) and the student asks about it."""
    if state.get("current_question") or not (state.get("last_answered") or {}).get("question"):
        return False
    return bool(_EXPLAIN_RE.search(query or "")) and not _NEXT_RE.search(query or "")
# A "Question:" block the model writes itself after serving a question. The pane
# shows the real question verbatim; the model's version paraphrased it, and once
# it no longer saw the text it invented details. Removed whenever it served one.
_RESTATED_QUESTION_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:"
    r"\**[ \t]*(?:practice[ \t]+)?question[ \t]*\d*[ \t]*\**[ \t]*:"   # **Question:** / Question 2:
    r"|#+[ \t]*(?:practice[ \t]+)?question[ \t]*\d*[ \t]*(?=\n)"        # ### Question 2
    r")[^\n]*(?:\n(?![ \t]*\n)[^\n]*)*",
    re.IGNORECASE,
)


# Two or more option markers in one paragraph: "(A) … (B) …" / "(1) … (2) …".
_OPTION_MARK_RE = re.compile(r"(?:^|[\s>*])\(\s*(?:[1-4]|[A-Da-d])\s*\)")


def _strip_restated_question(text: str) -> str:
    stripped = _RESTATED_QUESTION_RE.sub("\n", text or "")
    # An options list written in the same turn the question was served is the
    # model's own (it was seen inventing (A)-(D) unrelated to the real options);
    # the real ones are in the pane.
    stripped = "\n\n".join(
        para for para in re.split(r"\n[ \t]*\n", stripped)
        if len(_OPTION_MARK_RE.findall(para)) < 2
    )
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
    # Never leave the student with nothing.
    return stripped if len(stripped) >= 40 else (text or "")


# Corpus ids (th_m58296_3_1, ex_…, pr_…, pyq_…) are for tools, not students.
_CONTENT_ID_RE = re.compile(r"\s?[\[(](?:th|ex|pr|pyq|def|sum)_[A-Za-z0-9_]+[\])]")
_TURN_CHARS = 2500

TEACHER_MAX_STEPS = 8
TEACHER_TIME_BUDGET_S = 80.0
# Gemini 3 counts its thinking against max_tokens; a full Learn-phase lesson
# was being cut off mid-derivation at the loop's 1800 default.
TEACHER_MAX_TOKENS = 6000


def _import_study_tools() -> None:
    # Ensure study tools are in the registry
    import mcp_tools.study_tools  # noqa: F401
    import mcp_tools.curriculum_tools  # noqa: F401
    import mcp_tools.learner_tools  # noqa: F401
    import mcp_tools.compute_tools  # noqa: F401
    import mcp_tools.symbolic_tools  # noqa: F401
    import mcp_tools.physics_tools  # noqa: F401
    import mcp_tools.diagram_tools  # noqa: F401


def _history(session_id: str, query: str) -> List[Dict[str, Any]]:
    """The last few real turns, oldest first, without the message being answered.

    Bootstrap has already stored the current student message, so the newest row
    is usually this turn's own query — drop it, it is sent as the user message.
    Diagnostic rows are bookkeeping for the context manager, not conversation.
    """
    try:
        rows = db.read_recent_chat_turns(session_id, n=_HISTORY_TURNS + 1)
    except Exception:
        logger.debug("history read failed", exc_info=True)
        return []
    rows = [r for r in rows if r.get("agent_used") != "diagnostic"]
    if rows and rows[-1].get("role") == "user" and (rows[-1].get("content") or "").strip() == query.strip():
        rows = rows[:-1]
    out: List[Dict[str, Any]] = []
    for r in rows[-_HISTORY_TURNS:]:
        content = (r.get("content") or "").strip()
        if not content:
            continue
        role = "assistant" if r.get("role") == "assistant" else "user"
        if len(content) > _TURN_CHARS:
            content = content[:_TURN_CHARS] + " …"
        out.append({"role": role, "content": content})
    # Gemini rejects a conversation that does not alternate cleanly after the
    # system prompt; merge consecutive same-role rows rather than drop them.
    merged: List[Dict[str, Any]] = []
    for m in out:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] += "\n\n" + m["content"]
        else:
            merged.append(dict(m))
    if merged and merged[-1]["role"] == "user":
        merged.append({"role": "assistant", "content": "(no reply was recorded for that message)"})
    return merged


def _learner_block(snapshot: Dict[str, Any], user_id: str) -> str:
    """This concept's history, then the student's overall trajectory."""
    from services.progress_report import progress_digest

    parts = [format_snapshot(snapshot) or "- Nothing on record for this concept yet."]
    digest = progress_digest(user_id)
    if digest:
        parts.append("Across everything they have practised:\n" + digest)
    return "\n".join(parts)


def _mode_block(state: StudyRoomState) -> str:
    if (state.get("study_mode") or "learn") != "practice":
        return "Full lesson: Learn → Examples → Practice → Results."
    source = (
        "JEE past-year questions (PYQs) only — serve_practice_question returns PYQs."
        if state.get("question_source") == "pyq" else "All calibrated questions (textbook practice and PYQs)."
    )
    return (
        "PRACTICE-ONLY session. The student chose to skip theory and examples and go "
        "straight to questions. Do not teach a lesson or set an outline. Serve a "
        "question at once when none is open, keep feedback tight, and teach only "
        "what a doubt or a wrong answer calls for. Continue for up to 10 graded "
        "questions or until they stop, then call complete_study_session.\n"
        f"Question source: {source}"
    )


def phase_turn_budget(state: StudyRoomState) -> int:
    """Student messages a phase may take before the teacher is told to move on."""
    phase = state.get("current_phase") or "learn"
    parts = len((state.get("lesson_plan") or {}).get(phase) or []) or (3 if phase == "learn" else 2)
    return {"learn": 2 * parts + 1, "examples": 2 * parts + 1}.get(phase, 99)


def _pacing_block(state: StudyRoomState) -> str:
    phase = state.get("current_phase") or "learn"
    if phase not in ("learn", "examples"):
        return "Not applicable in this phase."
    parts = list((state.get("lesson_plan") or {}).get(phase) or [])
    step = int(state.get("phase_step") or 0)
    turns = int(state.get("phase_turns") or 0)
    budget = phase_turn_budget(state)
    lines: List[str] = []
    if not parts:
        lines.append(
            f"No outline yet for {phase.upper()}. Call `set_lesson_plan` FIRST this turn "
            "(Learn: 2-3 parts, 1-2 for a returning student; Examples: 1-2 examples)."
        )
    else:
        for i, title in enumerate(parts):
            mark = "✓ done" if i < step else ("▶ CURRENT" if i == step else "○ next")
            lines.append(f"  {i + 1}. {title}  [{mark}]")
    lines.append(f"Student messages in this phase: {turns} (budget {budget}).")
    if turns >= budget:
        lines.append(
            "BUDGET USED: this phase must end THIS turn. Confirm their last answer in one "
            "line, then call `complete_lesson_part` (repeat until the phase moves) and "
            "continue in the new phase in this same reply. Do not ask another check first."
        )
    return "\n".join(lines)


def _question_block(state: StudyRoomState) -> str:
    q = state.get("current_question") or {}
    if not q:
        last = state.get("last_answered") or {}
        lq = last.get("question") or {}
        if lq and state.get("current_phase") == "practice":
            verdict = "CORRECT" if last.get("is_correct") else "WRONG"
            return "\n".join([
                "No question is open. The student has just answered this one — already graded "
                f"({verdict}; their answer: {last.get('student_answer') or '—'}). Do NOT grade it again.",
                "If they ask for the solution or why, explain it fully from the answer key below: "
                "the idea, the working, and (if they were wrong) exactly where their choice goes wrong. "
                "Serve the next question only when they ask for it.",
                "Question:",
                (lq.get("core_text") or "").strip()[:3000],
                "Answer key / solution:",
                (lq.get("solution") or "").strip()[:3000],
            ])
        if state.get("current_phase") == "practice":
            return "No question is open. If the student is ready, serve one with `serve_practice_question`."
        return "No practice question is open."
    lines = [
        "A practice question is OPEN and waiting for the student's answer.",
        f"content_id: {q.get('content_id')}   type: {q.get('question_type') or q.get('content_type')}"
        f"   difficulty: {q.get('difficulty')}/5",
        "Question (the student can see this in the practice pane):",
        (q.get("core_text") or "").strip()[:3000],
    ]
    solution = (q.get("solution") or "").strip()
    if solution:
        lines += [
            "Answer key (HIDDEN from the student — grade against this; reveal it only "
            "after they have attempted, or if they ask to see the solution):",
            solution[:3000],
        ]
    else:
        lines.append("No stored answer key — work the problem yourself and verify with tools before grading.")
    return "\n".join(lines)


def teacher_agent_node(state: StudyRoomState) -> Dict[str, Any]:
    _import_study_tools()

    user_id = state.get("user_id") or ""
    session_id = state.get("session_id") or ""
    query = state.get("query") or ""

    if not user_id or not session_id or not query:
        return {"error": "Missing core identifiers for teacher agent."}

    trace = AgentTrace(user_id=user_id, session_id=session_id)
    working_state: Dict[str, Any] = dict(state)
    ctx = ToolContext(user_id=user_id, session_id=session_id, state=working_state, trace=trace)

    # Explaining the question just answered is not the moment for a new one: the
    # practice rule "serve when none is open" won out over the prompt otherwise.
    explaining_last = _wants_explanation_of_last(state, query)
    allowed_tools = _TEACHER_TOOLS - {"serve_practice_question", "grade_practice_answer"} if explaining_last else _TEACHER_TOOLS
    toolbelt = Toolbelt(
        specs=[s for s in registry().values() if s.name in allowed_tools],
        ctx=ctx,
        allowed_scopes=TUTOR_SCOPES,
    )

    # Load the agent skill prompt
    skill = load_skill("teacher_agent")
    if not skill:
        logger.error("teacher_agent skill not found.")
        return {"error": "Missing teacher_agent skill prompt."}

    from services import progress

    concept_id = state.get("concept_id") or ""
    progress.report("stage", "context")
    snapshot = state.get("learner_snapshot") or build_learner_snapshot(
        user_id,
        concept_id,
        exclude_study_session_id=state.get("study_session_id"),
        concept_name=state.get("concept_name"),
    )
    history = _history(session_id, query)

    # Format the prompt with current state
    replacements = {
        "{{target_exam}}": state.get("target_exam") or "JEE_ADVANCED",
        "{{preferred_depth}}": state.get("preferred_depth") or "standard",
        "{{interaction_count}}": str(state.get("interaction_count", 1)),
        "{{concept_name}}": state.get("concept_name") or concept_id or "Unknown Concept",
        "{{concept_id}}": concept_id,
        "{{chapter}}": state.get("chapter") or "Unknown Chapter",
        "{{current_phase}}": state.get("current_phase") or "learn",
        "{{difficulty_level}}": str(state.get("difficulty_level", 1)),
        "{{questions_served}}": str(state.get("questions_served", 0)),
        "{{questions_correct}}": str(state.get("questions_correct", 0)),
        "{{learner_memory}}": _learner_block(snapshot, user_id),
        "{{session_summary}}": state.get("session_summary")
        or ("This is the first message of the session." if not history else "See the conversation below."),
        "{{open_question}}": _question_block(state),
        "{{pacing}}": _pacing_block(state),
        "{{session_mode}}": _mode_block(state),
    }
    prompt = skill.get("system_prompt", "")
    for token, value in replacements.items():
        prompt = prompt.replace(token, str(value))
    if explaining_last:
        prompt += (
            "\n\nTHIS TURN: the student is asking about the question they just answered (shown under "
            "'Practice question on the table'). Explain it fully — the idea, the working from the answer "
            "key, and where their choice goes wrong if it was wrong. Do not serve a new question; end by "
            "telling them they can move to the next one when ready."
        )

    progress.report("stage", "context", "done")
    progress.report("stage", "thinking")
    res = run_agent_loop(
        system_prompt=prompt,
        user_message=query,
        toolbelt=toolbelt,
        model=skill.get("model") or "gemini-3-flash-preview",
        # Without a chain the room failed outright the moment the primary
        # model's daily quota ran out.
        fallback_models=config.LEARNING_AGENT_FALLBACK_MODELS,
        priming_messages=history,
        max_steps=TEACHER_MAX_STEPS,
        time_budget_s=TEACHER_TIME_BUDGET_S,
        max_tokens=TEACHER_MAX_TOKENS,
        temperature=skill.get("temperature", 0.3),
        trace=trace,
        label="teacher_agent",
    )

    if res.stop_reason == "cancelled" or progress.is_cancelled():
        return {"cancelled": True, "response": ""}

    if res.error:
        logger.error("Teacher agent loop failed: %s", res.error)
        return {"error": res.error, "agent_trace_path": trace.path()}

    updates: Dict[str, Any] = dict(ctx.updates)

    progress.report("stage", "thinking", "done")
    # Run the Answer Checker
    progress.report("stage", "review")
    checked_result = check_answer(
        query=query,
        draft=res.text,
        state={**working_state, **updates},
        trace=trace,
    )

    progress.report("stage", "review", "done")
    reply = _CONTENT_ID_RE.sub("", checked_result.answer)
    if "serve_practice_question" in res.tool_calls_made:
        reply = _strip_restated_question(reply)
    updates["response"] = reply
    updates["agent_used"] = "teacher_agent"
    updates["model_used"] = res.model_used
    updates["active_agent"] = "teacher_agent"
    updates["learner_snapshot"] = snapshot
    updates["tools_used"] = list(res.tool_calls_made)

    # Pending permissions if any
    if ctx.pending_permissions:
        updates["pending_permissions"] = ctx.pending_permissions

    try:
        trace.persist()
    except Exception:
        logger.debug("trace persist failed", exc_info=True)

    return updates
