"""Study Room tools: session management, practice, and evaluation.

These tools power the Teacher Agent's structured learning flow. They are
separate from the Main Tutor's tools because the Study Room has its own
session model (study_sessions, study_session_events) and its own concept
of "phase" that the general tutor does not.

All tools register with the shared @tool decorator and follow the same
three rules: never raise, degrade gracefully, return structured results.
"""

from __future__ import annotations

import logging
import re
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import config
from pydantic import BaseModel, Field

from agents.runtime.toolbelt import (
    SCOPE_READ,
    SCOPE_WRITE_PROGRESS,
    ToolContext,
    tool,
)
from db.supabase_client import db

logger = logging.getLogger("jee_tutor.study_tools")

_VALID_PHASES = ("learn", "examples", "practice", "evaluation", "completed")
_PHASE_ORDER = {p: i for i, p in enumerate(_VALID_PHASES)}


# ── Study context (read) ─────────────────────────────────────────────────────


class StudyContextArgs(BaseModel):
    study_session_id: Optional[str] = Field(
        default=None,
        description="The study session to load. Omit to use the one in the current turn.",
    )


@tool(
    name="get_study_context",
    description=(
        "Load the current study session: concept, phase, questions attempted, "
        "performance so far, and past sessions on this concept. Call at the "
        "start of each turn to know where the student is."
    ),
    args_model=StudyContextArgs,
    scopes=(SCOPE_READ,),
)
def get_study_context(args: StudyContextArgs, ctx: ToolContext) -> Dict[str, Any]:
    ssid = args.study_session_id or ctx.state.get("study_session_id") or ""
    if not ssid:
        return {"error": "No study session. Start one first."}

    session = db.read_study_session(ssid)
    if not session:
        return {"error": f"Study session {ssid} not found."}

    user_id = ctx.user_id
    concept_id = session.get("concept_id", "")

    out: Dict[str, Any] = {
        "study_session_id": ssid,
        "concept_id": concept_id,
        "concept_name": session.get("concept_name"),
        "chapter": session.get("chapter"),
        "current_phase": session.get("current_phase", "learn"),
        "difficulty_level": session.get("difficulty_level", 1),
        "questions_served": session.get("questions_served", 0),
        "questions_correct": session.get("questions_correct", 0),
    }

    # Correct rate
    served = out["questions_served"]
    if served > 0:
        out["correct_rate"] = round(out["questions_correct"] / served, 2)
    else:
        out["correct_rate"] = None

    # Prior sessions on this concept
    prior = db.read_study_sessions_for_concept(user_id, concept_id)
    prior = [s for s in prior if s.get("study_session_id") != ssid]
    if prior:
        out["prior_sessions"] = [
            {
                "outcome": s.get("outcome"),
                "difficulty_reached": s.get("difficulty_level"),
                "correct_rate": (
                    round(s["questions_correct"] / s["questions_served"], 2)
                    if s.get("questions_served")
                    else None
                ),
                "when": s.get("started_at"),
            }
            for s in prior[:3]
        ]
        # Start difficulty from where they left off
        last_completed = next(
            (s for s in prior if s.get("outcome") in ("mastered", "needs_review")),
            None,
        )
        if last_completed:
            out["recommended_difficulty"] = last_completed.get("difficulty_level", 1)

    # Existing concept state from user_concept_state
    try:
        cs = db.read_user_concept_state(user_id, concept_id)
        if cs:
            out["concept_proficiency"] = round(
                float(cs.get("proficiency_score") or 0.5), 2
            )
            out["concept_attempts"] = cs.get("attempts", 0)
            out["concept_correct"] = cs.get("correct", 0)
            out["struggle_streak"] = cs.get("struggle_streak", 0)
    except Exception:
        pass

    # Existing readiness score
    readiness = db.read_concept_readiness(user_id, concept_id)
    if readiness:
        out["current_readiness"] = round(float(readiness.get("score", 0)), 1)

    # Learner memories about this concept
    memories = db.read_learner_memories(
        user_id, query=concept_id, subject_key=concept_id
    )
    if memories:
        out["learner_memories"] = [
            {"kind": m.get("kind"), "fact": m.get("fact")}
            for m in memories[:4]
        ]

    return out


# ── Phase transition ──────────────────────────────────────────────────────────


class TransitionPhaseArgs(BaseModel):
    target_phase: str = Field(
        ...,
        description=(
            "The phase to move to: 'examples' or 'practice'. Must be later than "
            "the current phase."
        ),
    )
    phase_summary: Optional[str] = Field(
        default=None,
        description="A brief summary of what was covered in the phase just ending.",
    )


@tool(
    name="transition_phase",
    description=(
        "Move the study session to the next phase. Call when the student has "
        "demonstrated sufficient understanding of the current phase.\n"
        "Phase order: learn → examples → practice. Forward only; skipping "
        "examples is allowed for a student who has studied this before. To end "
        "the session use complete_study_session instead."
    ),
    args_model=TransitionPhaseArgs,
    scopes=(SCOPE_WRITE_PROGRESS,),
)
def transition_phase(args: TransitionPhaseArgs, ctx: ToolContext) -> Dict[str, Any]:
    ssid = ctx.state.get("study_session_id") or ""
    if not ssid:
        return {"error": "No study session to transition."}

    target = args.target_phase.lower().strip()
    if target not in _VALID_PHASES:
        return {"error": f"'{target}' is not a valid phase. Use one of: {', '.join(_VALID_PHASES)}."}

    current = ctx.state.get("current_phase", "learn")
    if target in ("evaluation", "completed"):
        return {"error": "End the session with complete_study_session, which records the outcome."}
    if _PHASE_ORDER.get(target, 0) <= _PHASE_ORDER.get(current, 0):
        return {"error": f"Cannot move from '{current}' to '{target}'. Phases only go forward."}

    move_phase(ctx, target, args.phase_summary)
    return {"transitioned": True, "from": current, "to": target}


def move_phase(ctx: ToolContext, target: str, summary: Optional[str] = None) -> None:
    """Record the phase change everywhere it lives: events, row, turn state."""
    ssid = ctx.state.get("study_session_id") or ""
    current = ctx.state.get("current_phase", "learn")
    db.insert_study_event(
        ssid, ctx.user_id, "phase_end",
        concept_id=ctx.state.get("concept_id"),
        payload={"phase": current, "summary": summary},
    )
    db.update_study_session_phase(ssid, target, summary)  # also resets step/turns
    db.insert_study_event(
        ssid, ctx.user_id, "phase_start",
        concept_id=ctx.state.get("concept_id"),
        payload={"phase": target},
    )
    ctx.stage(current_phase=target, phase_transition=target, phase_step=0, phase_turns=0)
    ctx.state.update(ctx.updates)


# ── Lesson outline (pacing) ───────────────────────────────────────────────────


class LessonPlanArgs(BaseModel):
    parts: List[str] = Field(
        ...,
        min_length=1,
        max_length=4,
        description=(
            "Short titles (3-6 words) for the parts of the CURRENT phase, in order. "
            "Learn: 2-3 ideas (1-2 for a returning student). Examples: 1-2 worked "
            "examples. The student sees this outline and ticks through it."
        ),
    )


@tool(
    name="set_lesson_plan",
    description=(
        "Commit to an outline for the current phase (Learn or Examples). Call it on "
        "the first turn of each of those phases, before teaching. The outline is "
        "shown to the student, so keep titles short and plain."
    ),
    args_model=LessonPlanArgs,
    scopes=(SCOPE_WRITE_PROGRESS,),
)
def set_lesson_plan(args: LessonPlanArgs, ctx: ToolContext) -> Dict[str, Any]:
    ssid = ctx.state.get("study_session_id") or ""
    phase = ctx.state.get("current_phase", "learn")
    if phase not in ("learn", "examples"):
        return {"error": "Outlines are for the Learn and Examples phases only."}
    plan = dict(ctx.state.get("lesson_plan") or {})
    if plan.get(phase):
        # Already committed. Re-setting it reset progress to part 1 every turn,
        # so the outline never moved past the first part.
        step = int(ctx.state.get("phase_step") or 0)
        return {
            "ok": True,
            "already_set": True,
            "parts": plan[phase],
            "current_part": min(step + 1, len(plan[phase])),
            "note": "The outline is fixed for this phase; keep going with the current part.",
        }
    parts = [" ".join(p.split())[:60] for p in args.parts if p and p.strip()][:4]
    plan[phase] = parts
    db.update_study_session_fields(ssid, {"lesson_plan": plan, "phase_step": 0})
    ctx.stage(lesson_plan=plan, phase_step=0)
    ctx.state.update(ctx.updates)
    return {"ok": True, "phase": phase, "parts": parts, "current_part": 1}


class CompletePartArgs(BaseModel):
    summary: Optional[str] = Field(default=None, description="One line on what the student now has.")


@tool(
    name="complete_lesson_part",
    description=(
        "Tick off the current part of the outline. Call it when the student has "
        "answered that part's check (right, or right after one re-explanation), or "
        "has asked to move on. Finishing the last Learn part moves the session to "
        "Examples automatically; finishing the last example tells you to start "
        "Practice."
    ),
    args_model=CompletePartArgs,
    scopes=(SCOPE_WRITE_PROGRESS,),
)
def complete_lesson_part(args: CompletePartArgs, ctx: ToolContext) -> Dict[str, Any]:
    ssid = ctx.state.get("study_session_id") or ""
    phase = ctx.state.get("current_phase", "learn")
    parts = list((ctx.state.get("lesson_plan") or {}).get(phase) or [])
    step = int(ctx.state.get("phase_step") or 0) + 1
    if parts:
        step = min(step, len(parts))
    db.update_study_session_fields(ssid, {"phase_step": step})
    ctx.stage(phase_step=step)
    ctx.state.update(ctx.updates)

    if parts and step < len(parts):
        return {"ok": True, "completed": step, "of": len(parts), "next_part": parts[step]}

    if phase == "learn":
        move_phase(ctx, "examples", args.summary)
        return {
            "ok": True,
            "phase_complete": True,
            "now_in": "examples",
            "instruction": "Learn is done. In THIS reply, confirm their last answer in one "
            "line, then call set_lesson_plan for the examples and start the first one.",
        }
    if phase == "examples":
        move_phase(ctx, "practice", args.summary)
        return {
            "ok": True,
            "phase_complete": True,
            "now_in": "practice",
            "instruction": "Examples are done. In THIS reply, call serve_practice_question "
            "and introduce the first question in one or two lines.",
        }
    return {"ok": True, "completed": step}


# ── Serve practice question ──────────────────────────────────────────────────


class ServePracticeArgs(BaseModel):
    difficulty: Optional[int] = Field(
        default=None,
        description="Override difficulty (1-5). Omit to use the session's current level.",
    )
    content_type: str = Field(
        default="practice",
        description="'practice' for textbook questions, 'pyq' for past-year questions.",
    )


@tool(
    name="serve_practice_question",
    description=(
        "Fetch a calibrated practice question for the current concept. "
        "Avoids questions already served in this session. Uses the session's "
        "difficulty level unless overridden. NEVER invent questions — always "
        "use this to get verified ones from the corpus."
    ),
    args_model=ServePracticeArgs,
    scopes=(SCOPE_READ,),
)
def serve_practice_question(args: ServePracticeArgs, ctx: ToolContext) -> Dict[str, Any]:
    from retrieval import service as retrieval_service

    concept_id = ctx.state.get("concept_id") or ctx.state.get("primary_concept_id") or ""
    if not concept_id:
        return {"error": "No concept to serve questions for."}

    if ctx.state.get("graded_this_turn"):
        # The student needs to see the result (and the solution) and ask about it
        # before the screen moves on. They get the next one when they ask for it.
        return {
            "error": "The student has just answered. Do not serve the next question in this turn: "
            "give the result (and the full solution if they were wrong), then tell them they can "
            "ask about it or press Next question when ready."
        }
    open_q = ctx.state.get("current_question")
    if open_q and ctx.state.get("awaiting_answer", True):
        return {
            "error": "A question is already open. Grade the student's attempt first.",
            "open_content_id": open_q.get("content_id"),
        }

    ssid = ctx.state.get("study_session_id") or ""
    difficulty = int(args.difficulty or ctx.state.get("difficulty_level") or 1)
    pyq_only = ctx.state.get("question_source") == "pyq"
    content_type = "pyq" if pyq_only else (args.content_type if args.content_type in ("practice", "pyq") else "practice")
    # A PYQ-only session never falls back to textbook questions.
    other_type = "pyq" if pyq_only or content_type == "practice" else "practice"
    target_exam = ctx.state.get("target_exam") or "JEE_ADVANCED"

    # Never repeat a question inside one session. The carry only lives in this
    # process, so the session's own event log is the source of truth.
    served_ids = set(ctx.state.get("served_content_ids") or [])
    if ssid:
        served_ids |= {
            e.get("content_id")
            for e in db.read_study_events(ssid, "question_served")
            if e.get("content_id")
        }

    question: Optional[Dict[str, Any]] = None
    off_concept: Optional[Dict[str, Any]] = None
    # Widen in steps: the exact level first, then any level, then the other
    # question bank. A question about THIS concept from a wider pass beats an
    # off-concept one from a narrower pass — otherwise practice on LC circuits
    # drifts into RL circuits as soon as one level's bank runs dry.
    for ctype, diff in ((content_type, difficulty), (content_type, None), (other_type, difficulty), (other_type, None)):
        pack = retrieval_service.retrieve(
            f"practice question on {ctx.state.get('concept_name') or concept_id}",
            user_id=ctx.user_id,
            content_type=ctype,
            concept_id=concept_id,
            top_k=6,
            target_exam=target_exam,
            difficulty_target=diff,
            forced=True,
            exclude_seen=True,
            mark=False,  # only the question actually served is marked, below
        )
        chunks = [c for c in (pack.get("chunks") or []) if c.get("content_id") not in served_ids]
        own = [c for c in chunks if c.get("primary_concept_id") == concept_id]
        if own:
            question = own[0]
            break
        if chunks and off_concept is None:
            off_concept = chunks[0]
    if question is None:
        # Off-concept is acceptable only nearby: same chapter, then same JEE unit.
        # PYQs are linked to fine-grained concepts ("lever arm"), so a topic like
        # projectile motion rarely has one of its own — without this bound the
        # fallback served a rolling-cylinder PYQ for projectile motion.
        question = _nearby_question(
            concept_id, served_ids, difficulty,
            ("PYQ",) if pyq_only else ("Practice Question", "PYQ"),
        ) or (off_concept if off_concept and _same_area(off_concept, concept_id) else None)

    revisit = False
    if not question:
        # Never a dead end: when nothing unseen is left nearby, bring back a question the
        # student has already met — one they got wrong first — and say it is a revisit.
        question = _revisit_question(concept_id, ssid, ("PYQ",) if pyq_only else ("Practice Question", "PYQ"))
        revisit = question is not None
    if not question:
        return {"error": "This concept has no questions in the index at all.", "exhausted": True}

    content_id = question.get("content_id", "")
    import uuid as _uuid
    question = dict(question)
    question["question_instance_id"] = str(_uuid.uuid4())
    try:
        retrieval_service.mark_served(ctx.user_id, [question], purpose="study_practice")
    except Exception:
        logger.debug("ledger mark failed", exc_info=True)

    if ssid:
        db.insert_study_event(
            ssid, ctx.user_id, "question_served",
            concept_id=concept_id,
            content_id=content_id,
            payload={
                "difficulty": question.get("difficulty"),
                "content_type": question.get("content_type"),
                "question_type": question.get("question_type"),
                "question_instance_id": question["question_instance_id"],
            },
        )

    new_prob_state = {
        "problem_key": content_id,
        "attempts_on_problem": 0,
        "hints_given": 0,
        "student_asked_for_answer": 0,
    }
    ctx.stage(
        current_question=question,
        awaiting_answer=True,
        served_content_ids=sorted(served_ids | {content_id}),
        problem_state=new_prob_state,
    )
    ctx.state.update(ctx.updates)

    # Only the opening words: given the full text the model kept restating the
    # question in its own words (changing details — "two coins" for "a dime and
    # a quarter"). The full question and its answer key reach it on the
    # student's next turn, in the open-question block, when grading needs them.
    words = (question.get("core_text") or "").split()
    return {
        "content_id": content_id,
        "content_type": question.get("content_type"),
        "question_type": question.get("question_type"),
        "difficulty": question.get("difficulty"),
        "opening_words": " ".join(words[:10]) + (" …" if len(words) > 10 else ""),
        "source": question.get("citation"),
        "revisit": revisit,
        "note": (
            "The student sees the full question in the practice pane. Reply with one or "
            "two lines that set it up (e.g. 'A numerical one on maximum range — watch the "
            "angle.') Do not write the question out."
        ),
    }


def _concept_area(concept_id: str) -> tuple:
    try:
        from retrieval import store

        c = store.concept(concept_id) or {}
        return c.get("chapter_id") or "", c.get("unit") or ""
    except Exception:
        return "", ""


def _same_area(chunk: Dict[str, Any], concept_id: str) -> bool:
    chapter_id, unit = _concept_area(concept_id)
    return bool(chapter_id and chunk.get("chapter_id") == chapter_id) or bool(unit and chunk.get("unit") == unit)


def _revisit_question(concept_id: str, ssid: str, types: tuple) -> Optional[Dict[str, Any]]:
    """A question the student has met before, for when nothing unseen is left: one they got
    wrong in this session first, else the one seen longest ago, else any in the chapter."""
    from retrieval import store

    try:
        served = [e.get("content_id") for e in db.read_study_events(ssid, "question_served")] if ssid else []
        wrong = [e.get("content_id") for e in db.read_study_events(ssid, "question_attempted")
                 if not (e.get("payload") or {}).get("is_correct")] if ssid else []
    except Exception:
        served, wrong = [], []
    last = served[-1] if served else None
    order = [c for c in dict.fromkeys(wrong + served) if c and c != last]
    for cid in order:
        rows = store.chunks_by_ids([cid])
        if rows and rows[0]["content_type"] in types:
            row = dict(rows[0])
            row.setdefault("citation", f"{row.get('source_book')} — {row.get('source_ref')}")
            return row
    return _nearby_question(concept_id, {last} if last else set(), 3, types)


def _nearby_question(concept_id: str, served: set, difficulty: int, types: tuple) -> Optional[Dict[str, Any]]:
    """An unseen question from the concept's chapter, else its JEE unit, nearest the target difficulty."""
    chapter_id, unit = _concept_area(concept_id)
    try:
        from retrieval import store
    except Exception:
        return None
    try:
        name = (store.concept(concept_id) or {}).get("name") or concept_id
    except Exception:
        name = concept_id
    # Words that identify the topic ("projectile"), so a question about it beats
    # another question from the same chapter.
    keys = [w for w in re.findall(r"[a-z]{4,}", name.lower()) if w not in {"motion", "theorem", "laws", "with", "from"}]
    for column, value in (("chapter_id", chapter_id), ("unit", unit)):
        if not value:
            continue
        try:
            rows = store.questions_in_area(column, value, types, difficulty, exclude_ids=served, keywords=keys)
        except Exception:
            return None
        for row in rows:
            row.setdefault("citation", f"{row.get('source_book')} — {row.get('source_ref')}")
            row.setdefault("primary_concept_id", row.get("concept_id"))
            return row
    return None


def _last_grade(
    *,
    is_correct: bool,
    content_id: Optional[str],
    concept_id: Optional[str],
    attempt_id: Any,
    proficiency_before: Any = None,
    proficiency_after: Any = None,
) -> Dict[str, Any]:
    """Grade facts staged onto the turn. This does not close an episode."""
    return {
        "is_correct": bool(is_correct),
        "content_id": content_id or None,
        "concept_id": concept_id or None,
        "attempt_id": str(attempt_id) if attempt_id else None,
        "proficiency_before": proficiency_before,
        "proficiency_after": proficiency_after,
    }


# ── Grade practice answer ────────────────────────────────────────────────────


class GradePracticeArgs(BaseModel):
    is_correct: bool = Field(
        ..., description="Whether the student's answer is correct."
    )
    student_answer: Optional[str] = Field(
        default=None, description="What the student answered."
    )
    submission_id: Optional[str] = Field(
        default=None,
        description="Stable id for this submission. Reuse it on retry. Omit to use the served question instance.",
    )
    error_type: Optional[str] = Field(
        default=None,
        description=(
            "If wrong: 'conceptual', 'procedural', 'calculation', or 'misread'."
        ),
    )


@tool(
    name="grade_practice_answer",
    description=(
        "Grade the student's answer to the current practice question. Records "
        "the attempt, updates the session stats, and adjusts difficulty.\n"
        "Rules:\n"
        "- 3 correct in a row → increase difficulty\n"
        "- 2 wrong in a row → decrease difficulty\n"
        "Call this exactly once per question attempt."
    ),
    args_model=GradePracticeArgs,
    scopes=(SCOPE_WRITE_PROGRESS,),
)
def grade_practice_answer(args: GradePracticeArgs, ctx: ToolContext) -> Dict[str, Any]:
    ssid = ctx.state.get("study_session_id") or ""
    if not ssid:
        return {"error": "No study session."}

    concept_id = ctx.state.get("concept_id") or ""
    question = ctx.state.get("current_question") or {}
    if not question:
        return {
            "error": "No practice question is open. Serve one before grading — "
            "an answer to your own check question is not a graded attempt."
        }
    from agents.runtime.toolbelt import call_is_live

    if not call_is_live(ctx):
        return {
            "graded": False,
            "recorded": False,
            "persistence_status": "not_attempted",
            "failure_reason": "cancelled",
            "student_message": "That check was stopped, so your progress was not changed.",
        }
    content_id = question.get("content_id", "")
    instance_id = question.get("question_instance_id") or ""
    from services.answer_key import grade_free_answer
    from services.grading import answer_fingerprint, commit_progress

    decision = grade_free_answer(question, args.student_answer)
    authoritative = decision["grade_status"] in {"correct", "incorrect"}
    if not authoritative:
        return {
            "graded": False,
            "recorded": False,
            "grade_status": "ungradable" if decision["grade_status"] == "ungradable" else decision["grade_status"],
            "evidence_source": "model_unverified" if decision["grade_status"] == "ungradable" else "none",
            "persistence_status": "not_attempted",
            "is_correct": None,
            "model_claim": bool(args.is_correct),
            "failure_reason": decision.get("failure_reason") or "no_authoritative_key",
            "student_message": (
                "This answer could not be checked against a key, so your progress was not changed."
                if decision["grade_status"] == "ungradable"
                else "That submission could not be read as an answer, so your progress was not changed."
            ),
        }
    submission_id = (args.submission_id or "").strip() or instance_id
    saved = commit_progress(
        user_id=ctx.user_id,
        session_id=ctx.state.get("session_id") or "",
        concept_id=concept_id,
        content_id=content_id or None,
        submission_id=submission_id or None,
        question_instance_id=instance_id or None,
        student_answer=args.student_answer or "",
        fingerprint=answer_fingerprint({
            "student_answer": args.student_answer or "",
            "content_id": content_id,
            "question_instance_id": instance_id,
        }),
        is_correct=bool(decision["is_correct"]),
        grade_status=decision["grade_status"],
        evidence_source="answer_key",
        error_type=None if decision["is_correct"] else (
            args.error_type if args.error_type in {"conceptual", "procedural", "calculation", "misread"} else None
        ),
        source="study",
        study_session_id=ssid,
        question_difficulty=question.get("difficulty"),
        answer_text=decision.get("answer_text") or "",
    )
    public = saved.to_public()
    if saved.recorded:
        staged = {
            "questions_served": saved.questions_served,
            "questions_correct": saved.questions_correct,
            "difficulty_level": saved.difficulty_level,
            "awaiting_answer": False,
            "current_question": None,
            "last_grade": _last_grade(
                is_correct=bool(saved.is_correct),
                content_id=content_id or None,
                concept_id=concept_id or None,
                attempt_id=saved.attempt_id,
                proficiency_before=saved.proficiency_before,
                proficiency_after=saved.proficiency_after,
            ),
        }
        if saved.questions_served is not None:
            ctx.stage(**{key: value for key, value in staged.items() if value is not None or key in {"current_question", "awaiting_answer"}})
        else:
            ctx.stage(**staged)
        ctx.state["graded_this_turn"] = True
        ctx.state.update(ctx.updates)
    public["student_message"] = saved.student_message
    public["solution_available"] = bool(question.get("solution")) and saved.grade_status == "incorrect"
    return public


# ── Complete study session ────────────────────────────────────────────────────


class CompleteSessionArgs(BaseModel):
    summary: Optional[str] = Field(
        default=None,
        description="A brief summary of the practice phase.",
    )


@tool(
    name="complete_study_session",
    description=(
        "End the study session and trigger evaluation. Call this after the "
        "student has finished practicing (usually after 5-10 questions, or "
        "when they ask to stop)."
    ),
    args_model=CompleteSessionArgs,
    scopes=(SCOPE_WRITE_PROGRESS,),
)
def complete_study_session(args: CompleteSessionArgs, ctx: ToolContext) -> Dict[str, Any]:
    ssid = ctx.state.get("study_session_id") or ""
    if not ssid:
        return {"error": "No study session to complete."}

    questions_served = ctx.state.get("questions_served", 0)
    questions_correct = ctx.state.get("questions_correct", 0)
    difficulty = ctx.state.get("difficulty_level", 1)

    # Determine outcome. Difficulty only rises after 3 right in a row, so a
    # 5-question session tops out around level 2 — requiring level 3 (as this
    # used to) made "mastered" unreachable in a normal session.
    rate = questions_correct / questions_served if questions_served else 0.0
    if questions_served == 0:
        outcome = "incomplete"
    elif (
        (questions_served >= config.STUDY_MASTERED_MIN_QUESTIONS and rate >= config.STUDY_MASTERED_RATE)
        or (
            questions_served >= config.STUDY_MASTERED_HARD_QUESTIONS
            and rate >= config.STUDY_MASTERED_HARD_RATE
            and difficulty >= config.STUDY_MASTERED_HARD_DIFFICULTY
        )
    ):
        outcome = "mastered"
    else:
        outcome = "needs_review"

    db.insert_study_event(
        ssid, ctx.user_id, "session_completed",
        concept_id=ctx.state.get("concept_id"),
        payload={
            "outcome": outcome,
            "questions_served": questions_served,
            "questions_correct": questions_correct,
            "max_difficulty": difficulty,
            "summary": args.summary,
        },
    )

    db.complete_study_session(ssid, outcome)
    db.insert_learning_event(
        ctx.user_id, "study_completed", source="study",
        concept_id=ctx.state.get("concept_id"), session_id=ctx.state.get("session_id"),
        study_session_id=ssid, outcome=outcome, difficulty=difficulty,
        payload={"questions_served": questions_served, "questions_correct": questions_correct},
    )
    _close_pending_reviews(ctx)
    _remember_session(ctx, outcome, questions_served, questions_correct, difficulty)

    ctx.stage(current_phase="evaluation", phase_transition="evaluation")
    ctx.state.update(ctx.updates)

    return {
        "completed": True,
        "outcome": outcome,
        "questions_served": questions_served,
        "questions_correct": questions_correct,
        "correct_rate": round(questions_correct / questions_served, 2) if questions_served else 0,
        "difficulty_reached": difficulty,
    }


def _close_pending_reviews(ctx: ToolContext) -> None:
    """Studying a concept that had a review scheduled IS the review. Nothing
    else ever marked reviews done, so they stayed "due" forever."""
    concept_id = ctx.state.get("concept_id") or ""
    if not concept_id:
        return
    try:
        pending = (
            db.client.table("review_schedule")
            .select("review_id")
            .eq("user_id", ctx.user_id)
            .eq("concept_id", concept_id)
            .eq("status", "pending")
            .execute()
            .data
            or []
        )
    except Exception:
        return
    for row in pending:
        db.complete_review(row["review_id"])
        db.insert_learning_event(
            ctx.user_id, "review_done", source="review", concept_id=concept_id,
            study_session_id=ctx.state.get("study_session_id"),
        )


def _remember_session(
    ctx: ToolContext, outcome: str, served: int, correct: int, difficulty: int
) -> None:
    """Leave a durable trace every tutor can read: the chat tutor, the next
    Study Room session on a related concept, the background coach."""
    concept_id = ctx.state.get("concept_id") or ""
    name = ctx.state.get("concept_name") or concept_id
    if not concept_id or served == 0:
        return
    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    if outcome == "mastered":
        kind, conf = "strength", 0.75
        fact = (
            f"Completed a Study Room session on {name} on {today} and mastered it "
            f"({correct}/{served} correct, reached difficulty {difficulty}/5)."
        )
    else:
        kind, conf = "context", 0.6
        fact = (
            f"Studied {name} in the Study Room on {today} but it needs review "
            f"({correct}/{served} correct, difficulty {difficulty}/5)."
        )
    try:
        db.insert_learner_memory(
            ctx.user_id, kind, fact,
            evidence=f"study_session {ctx.state.get('study_session_id')}",
            subject_key=concept_id,
            confidence=conf,
            source="tutor",
            session_id=ctx.state.get("session_id"),
        )
    except Exception:
        logger.debug("session memory write failed", exc_info=True)

    if outcome != "mastered":
        from datetime import timedelta

        due = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        try:
            db.upsert_review(
                ctx.user_id, concept_id, due,
                reason=f"Study Room: {correct}/{served} correct on {name}",
                created_by="tutor",
                session_id=ctx.state.get("session_id"),
            )
            ssid = ctx.state.get("study_session_id")
            if ssid:
                db.insert_study_event(
                    ssid, ctx.user_id, "review_scheduled",
                    concept_id=concept_id, payload={"due_at": due},
                )
        except Exception:
            logger.debug("review scheduling failed", exc_info=True)


# ── Compute readiness score (deterministic, no LLM) ──────────────────────────


def compute_readiness_score(
    user_id: str,
    concept_id: str,
) -> Dict[str, Any]:
    """Compute a 0-100 readiness score for a concept.

    Deterministic formula, not an LLM call:
      base = proficiency * 40
      difficulty_bonus = (max_difficulty / 5) * 20
      recency_factor = exp(-days_since / 14) * 20
      accuracy_factor = correct_rate * 20
    """
    factors: Dict[str, Any] = {}

    # Proficiency from user_concept_state
    from services.grading import measured_score

    proficiency = None
    try:
        cs = db.read_user_concept_state(user_id, concept_id)
        proficiency = measured_score(cs)
        if proficiency is not None:
            factors["proficiency"] = round(proficiency, 2)
            factors["total_attempts"] = cs.get("attempts", 0)
            factors["cold_start"] = False
        elif cs:
            factors["cold_start"] = bool(cs.get("is_cold_start", True))
            factors["total_attempts"] = cs.get("attempts", 0)
    except Exception:
        proficiency = None

    # Best study session on this concept
    max_difficulty = 1
    best_rate = 0.0
    days_since = 30.0  # default: long time ago
    sessions = db.read_study_sessions_for_concept(user_id, concept_id)
    completed = [s for s in sessions if s.get("outcome") in ("mastered", "needs_review")]
    if completed:
        best = completed[0]  # most recent completed
        max_difficulty = max(s.get("difficulty_level", 1) for s in completed)
        served = best.get("questions_served", 0)
        if served > 0:
            best_rate = best.get("questions_correct", 0) / served
        started = best.get("started_at")
        if started:
            try:
                then = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                days_since = max(0, (now - then).total_seconds() / 86400)
            except Exception:
                pass
        factors["sessions_completed"] = len(completed)
        factors["max_difficulty_reached"] = max_difficulty
        factors["best_correct_rate"] = round(best_rate, 2)
        factors["days_since_last_study"] = round(days_since, 1)

    completed_questions = sum(int(s.get("questions_served") or 0) for s in completed)
    if proficiency is None and completed_questions <= 0:
        return {
            "score": None,
            "available": False,
            "confidence": 0.0,
            "factors": {**factors, "reason": "unmeasured"},
        }

    # Formula. A missing proficiency does not become the 0.5 prior.
    base = (0.0 if proficiency is None else proficiency) * 40
    difficulty_bonus = (max_difficulty / 5) * 20
    recency_factor = math.exp(-days_since / 14) * 20
    accuracy_factor = best_rate * 20

    score = base + difficulty_bonus + recency_factor + accuracy_factor
    score = max(0.0, min(100.0, score))

    # Confidence: more data = more confident
    total_questions = sum(s.get("questions_served", 0) for s in completed)
    confidence = min(1.0, total_questions / 20)  # 20 questions = full confidence

    factors["base"] = round(base, 1)
    factors["difficulty_bonus"] = round(difficulty_bonus, 1)
    factors["recency_factor"] = round(recency_factor, 1)
    factors["accuracy_factor"] = round(accuracy_factor, 1)

    return {
        "score": round(score, 1),
        "confidence": round(confidence, 2),
        "factors": factors,
    }
