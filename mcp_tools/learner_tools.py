"""Learner tools: who this student is, and what just happened to them.

Split deliberately into three capability bands, because they carry very
different risk:

  read      — `get_learner_context`, `get_progress`: free to call, always safe.
  progress  — `record_attempt`: mutates proficiency. The maths is NOT the
              model's to improvise, so this wraps the existing diagnostic
              pipeline bit-for-bit and only lets the agent decide *when* to
              call it and with what outcome.
  memory    — `remember_about_learner`: writes a durable fact about a person.
              Cheap to write, expensive to be wrong about, and invisible to the
              student. Guarded by scope, prompt guidance, and a confidence field
              the agent has to commit to.

An expert subagent is scoped to `read` only, so none of the writes below are
reachable from one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from agents.runtime.toolbelt import (
    SCOPE_READ,
    SCOPE_SCHEDULE,
    SCOPE_WRITE_MEMORY,
    SCOPE_WRITE_PROGRESS,
    ToolContext,
    tool,
)
from db.supabase_client import db

logger = logging.getLogger("jee_tutor.learner_tools")

_MEMORY_KINDS = ("preference", "misconception", "strength", "context", "goal")


# ── Reads ─────────────────────────────────────────────────────────────────────


class LearnerContextArgs(BaseModel):
    concept_id: Optional[str] = Field(
        default=None,
        description=(
            "Narrow the recall to what is known about this concept. Omit for the "
            "general picture of the student."
        ),
    )
    include_weak_concepts: bool = Field(
        default=True,
        description="Include the concepts this student is currently struggling with.",
    )


@tool(
    name="get_learner_context",
    description=(
        "Recall what is known about this student: remembered facts about how they "
        "learn, their current goals, which concepts they are weak on, and how "
        "recent attempts have gone. Call this when the way you teach should depend "
        "on who you are teaching — a first explanation, a repeated mistake, or a "
        "choice about difficulty. Skip it for a purely factual one-off question."
    ),
    args_model=LearnerContextArgs,
    scopes=(SCOPE_READ,),
)
def get_learner_context(args: LearnerContextArgs, ctx: ToolContext) -> Dict[str, Any]:
    user_id = ctx.user_id
    out: Dict[str, Any] = {}

    memories = db.read_learner_memories(user_id, subject_key=args.concept_id) or []
    if not memories and args.concept_id:
        memories = db.read_learner_memories(user_id) or []

    out["remembered_facts"] = [
        {
            "kind": m.get("kind"),
            "fact": m.get("fact"),
            "about": m.get("subject_key"),
            "confidence": round(float(m.get("confidence") or 0.6), 2),
        }
        for m in memories
    ]
    if memories:
        db.bump_learner_memory_hits([m["memory_id"] for m in memories if m.get("memory_id")])
    elif not db.memory_enabled():
        out["memory_status"] = (
            "Durable memory is not enabled on this deployment, so you have no "
            "remembered facts about this student. Work from this session only."
        )

    try:
        user = db.read_user(user_id) or {}
        out["target_exam"] = user.get("target_exam")
        out["preferred_depth"] = user.get("preferred_depth")
    except Exception:
        pass

    if args.include_weak_concepts:
        try:
            weak = db.read_weak_user_concept_states(user_id) or []
            out["weak_concepts"] = [
                {
                    "concept_id": w.get("concept_id"),
                    "chapter": w.get("chapter"),
                    "proficiency": round(float(w.get("proficiency_score") or 0.0), 2),
                    "consecutive_wrong": w.get("struggle_streak", 0),
                }
                for w in weak[:6]
            ]
        except Exception:
            out["weak_concepts"] = []

    if args.concept_id:
        try:
            cs = db.read_user_concept_state(user_id, args.concept_id)
            if cs:
                attempts = cs.get("attempts") or 0
                out["this_concept"] = {
                    "concept_id": args.concept_id,
                    "proficiency": round(float(cs.get("proficiency_score") or 0.5), 2),
                    "attempts": attempts,
                    "correct": cs.get("correct") or 0,
                    "consecutive_wrong": cs.get("struggle_streak") or 0,
                    "never_attempted": bool(cs.get("is_cold_start", True)),
                    "error_pattern": cs.get("error_type_dist") or {},
                }
        except Exception:
            pass

    # Attempt history is per-concept in the schema, so this only applies when we
    # have a concept in hand — there is no "recent attempts across everything"
    # read, and inventing one here would mean a second query pattern to maintain.
    if args.concept_id:
        try:
            recent = db.read_recent_user_attempts(user_id, args.concept_id, n=5) or []
            out["recent_attempts_on_this_concept"] = [
                {
                    "correct": a.get("is_correct"),
                    "error_type": a.get("error_type"),
                    "time_taken_sec": a.get("time_taken_sec"),
                }
                for a in recent
            ]
        except Exception:
            logger.debug("recent attempts lookup failed", exc_info=True)

    due = db.read_due_reviews(user_id) or []
    if due:
        out["reviews_due_now"] = [
            {"concept_id": r.get("concept_id"), "reason": r.get("reason")} for r in due[:4]
        ]

    return out


# ── Progress write ────────────────────────────────────────────────────────────


class RecordAttemptArgs(BaseModel):
    is_correct: bool = Field(
        ..., description="Whether the student's attempt was correct."
    )
    concept_id: Optional[str] = Field(
        default=None,
        description="Concept attempted. Omit to use the concept resolved this turn.",
    )
    student_answer: Optional[str] = Field(
        default=None, description="What the student actually answered."
    )
    error_type: Optional[str] = Field(
        default=None,
        description=(
            "If wrong and you can tell why: 'conceptual' (misunderstood the idea), "
            "'procedural' (right idea, wrong method), 'calculation' (arithmetic or "
            "algebra slip), or 'misread' (misread the question). Leave empty and it "
            "will be classified for you."
        ),
    )
    time_taken_sec: Optional[int] = Field(
        default=None, description="Seconds the student spent, if known."
    )


@tool(
    name="record_attempt",
    description=(
        "Record the outcome of a question the student attempted, updating their "
        "proficiency on that concept. Call this exactly once per graded attempt, "
        "and only for a real attempt the student made — never to 'correct the "
        "record' or to log your own worked example. The proficiency maths is "
        "fixed; you supply the outcome, not the score."
    ),
    args_model=RecordAttemptArgs,
    scopes=(SCOPE_WRITE_PROGRESS,),
)
def record_attempt(args: RecordAttemptArgs, ctx: ToolContext) -> Dict[str, Any]:
    from agents.diagnostic import diagnostic_node

    concept_id = args.concept_id or ctx.state.get("primary_concept_id") or ""
    if not concept_id:
        return {
            "error": (
                "No concept to record against. Resolve the concept first, or pass "
                "concept_id."
            )
        }

    state = dict(ctx.state)
    state.update(
        {
            "primary_concept_id": concept_id,
            "has_graded_outcome": True,
            "is_correct": args.is_correct,
            "student_answer": args.student_answer,
            "error_type": args.error_type,
            "time_taken_sec": args.time_taken_sec,
        }
    )

    result = diagnostic_node(state)  # type: ignore[arg-type]
    if result.get("error"):
        return {"error": result["error"]}

    ctx.stage(
        has_graded_outcome=True,
        is_correct=args.is_correct,
        trigger_quiz=bool(result.get("trigger_quiz")),
        mistake_brief=result.get("mistake_brief"),
        score_summary=result.get("score_summary"),
        remediation_flashcards=result.get("remediation_flashcards") or [],
        awaiting_graded_response=False,
    )
    ctx.state.update(ctx.updates)

    payload: Dict[str, Any] = {
        "recorded": True,
        "concept_id": concept_id,
        "is_correct": args.is_correct,
        "score_summary": result.get("score_summary"),
        "mistake_brief": result.get("mistake_brief"),
    }
    if result.get("trigger_quiz"):
        payload["signal"] = (
            "This student has now missed several in a row on this concept. "
            "Consider slowing down and checking the underlying idea before moving on."
        )
    return payload


# ── Memory write ──────────────────────────────────────────────────────────────


class RememberArgs(BaseModel):
    kind: str = Field(
        ...,
        description=(
            "'preference' (how they like to learn), 'misconception' (a specific "
            "wrong belief they hold), 'strength' (something they reliably do well), "
            "'context' (a durable fact about their situation), or 'goal' (what they "
            "are working towards)."
        ),
    )
    fact: str = Field(
        ...,
        description=(
            "One sentence, written about the student, that would still be useful "
            "weeks from now. Good: 'Loses sign conventions when expanding brackets.' "
            "Bad: 'Asked about projectile motion today.'"
        ),
    )
    evidence: str = Field(
        default="",
        description="What in this session makes you believe it. Keep it short.",
    )
    concept_id: Optional[str] = Field(
        default=None,
        description="The concept this is about, if it is specific to one.",
    )
    confidence: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description=(
            "How sure you are, 0 to 1. One observation is around 0.5; a pattern you "
            "have now seen repeat is 0.8 or above."
        ),
    )


@tool(
    name="remember_about_learner",
    description=(
        "Save one durable fact about how this student learns, so a future session "
        "starts knowing it. Save only what will still matter weeks from now: a "
        "recurring mistake, a preference that demonstrably helps them, a goal they "
        "stated. Do NOT save what happened this session (that is already "
        "summarised), anything you only suspect once, or anything you would not be "
        "comfortable showing the student."
    ),
    args_model=RememberArgs,
    scopes=(SCOPE_WRITE_MEMORY,),
)
def remember_about_learner(args: RememberArgs, ctx: ToolContext) -> Dict[str, Any]:
    kind = (args.kind or "").strip().lower()
    if kind not in _MEMORY_KINDS:
        return {
            "error": (
                f"'{args.kind}' is not a memory kind. Use one of: "
                f"{', '.join(_MEMORY_KINDS)}."
            )
        }

    fact = (args.fact or "").strip()
    if len(fact) < 10:
        return {"error": "The fact is too short to be useful later. Write a full sentence."}
    if len(fact) > 300:
        return {"error": "Keep the fact to one sentence (300 characters or fewer)."}

    row = db.insert_learner_memory(
        user_id=ctx.user_id,
        kind=kind,
        fact=fact,
        evidence=(args.evidence or "").strip() or None,
        subject_key=args.concept_id,
        confidence=args.confidence,
        source="coach" if ctx.state.get("_coach_run") else "tutor",
        session_id=ctx.session_id,
    )
    if row is None:
        return {
            "saved": False,
            "note": (
                "Durable memory is not enabled on this deployment, so nothing was "
                "stored. Carry on with the turn; do not tell the student about this."
            ),
        }
    return {"saved": True, "kind": kind, "fact": fact}


# ── Review scheduling ─────────────────────────────────────────────────────────


class ScheduleReviewArgs(BaseModel):
    concept_id: str = Field(..., description="The concept to bring back for review.")
    in_days: int = Field(
        ...,
        ge=1,
        le=60,
        description=(
            "How many days from now. Shaky-but-understood is around 3; solid but "
            "worth consolidating is 7 to 14."
        ),
    )
    reason: str = Field(
        default="",
        description="One short phrase on why this needs revisiting. The student may see it.",
    )


@tool(
    name="schedule_review",
    description=(
        "Bring a concept back for review in a few days. Use it when the student got "
        "there with effort, or corrected a real misunderstanding — the cases where "
        "understanding is likely to fade. Do not schedule a review for something "
        "they answered comfortably, and do not schedule several in one turn."
    ),
    args_model=ScheduleReviewArgs,
    scopes=(SCOPE_SCHEDULE,),
)
def schedule_review(args: ScheduleReviewArgs, ctx: ToolContext) -> Dict[str, Any]:
    due = datetime.now(timezone.utc) + timedelta(days=args.in_days)
    row = db.upsert_review(
        user_id=ctx.user_id,
        concept_id=args.concept_id,
        due_at=due.isoformat(),
        reason=(args.reason or "").strip() or None,
        created_by="coach" if ctx.state.get("_coach_run") else "tutor",
        session_id=ctx.session_id,
    )
    if row is None:
        return {
            "scheduled": False,
            "note": (
                "Review scheduling is not enabled on this deployment. Do not promise "
                "the student a reminder."
            ),
        }
    return {
        "scheduled": True,
        "concept_id": args.concept_id,
        "due_in_days": args.in_days,
        "rescheduled_existing": bool(row.get("rescheduled")),
    }


# ── Suggestions ───────────────────────────────────────────────────────────────


class SuggestNextArgs(BaseModel):
    concept_id: Optional[str] = Field(
        default=None,
        description="Concept to suggest from. Omit to use this turn's concept.",
    )


@tool(
    name="suggest_next_material",
    description=(
        "Get concrete next items from the syllabus the student could do after this "
        "— sized to their current proficiency and excluding what they have already "
        "seen this session. Use it when the student is ready to move on, not when "
        "they are mid-question."
    ),
    args_model=SuggestNextArgs,
    scopes=(SCOPE_READ,),
)
def suggest_next_material(args: SuggestNextArgs, ctx: ToolContext) -> Dict[str, Any]:
    from mcp_tools.generate_suggestions import GenerateSuggestionsArgs, generate_suggestions

    concept_id = args.concept_id or ctx.state.get("primary_concept_id") or ""
    if not concept_id:
        return {"error": "No concept to suggest from. Resolve a concept first."}

    target = ctx.state.get("target_concept_state") or {}
    proficiency = float(target.get("proficiency_score") or 0.5)

    result = generate_suggestions(
        GenerateSuggestionsArgs(
            user_id=ctx.user_id,
            primary_concept_id=concept_id,
            proficiency=proficiency,
            seen_follow_up_ids=list(ctx.state.get("seen_follow_up_ids") or []),
        )
    )
    suggestions: List[Dict[str, Any]] = result.get("suggestions") or []
    if suggestions:
        ctx.stage(follow_up_suggestions=suggestions)
        ctx.state.update(ctx.updates)
    return {
        "suggestions": [
            {
                "content_id": s.get("content_id"),
                "title": s.get("title"),
                "content_type": s.get("content_type"),
                "difficulty": s.get("difficulty"),
            }
            for s in suggestions[:4]
        ]
    }
