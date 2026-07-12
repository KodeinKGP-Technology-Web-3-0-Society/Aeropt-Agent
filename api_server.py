"""
JEE Tutor Agent — FastAPI HTTP gateway
=======================================

Exposes the LangGraph tutor pipeline as two HTTP endpoints so that the
Next.js frontend can talk to the agent without touching the agent code.

Endpoints
---------
  POST /api/v1/chat/message
      Request:  { "user_id": str, "session_id": str | null, "query": str }
      Response: { "session_id": str, "response": str | null,
                  "agent_used": str | null,
                  "follow_up_suggestions": list | null,
                  "quiz_steps": list | null,
                  "awaiting_graded_response": bool,
                  "error": str | null }

  POST /api/v1/chat/end-session
      Request:  { "user_id": str, "session_id": str }
      Response: { "ok": bool }

Run with:
  uvicorn api_server:app --host 0.0.0.0 --port 8001 --reload

Design notes (confirmed with user before building)
---------------------------------------------------
1. Carry state persists to `.carry_sessions/` JSON files (no DB schema changes).

2. Memory eviction: idle carry files older than 2 hours are pruned on access.

3. End-session gap: if the frontend never calls /end-session (tab close,
   crash, etc.), _flush_session_summary never runs.  Sessions with < 5
   interactions produce no Supabase summary.  This is accepted for this
   phase; a background sweeper can be added later.

Nothing under agents/, graph/, db/, models/, utils/, or Aeropt-backend-main/
is modified.  main.py is unchanged and continues to work as the CLI entry
point.
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*pydantic.v1.*", category=UserWarning)

import logging
from typing import Any, Dict, List, Optional

# ── config must be imported first — calls load_dotenv() and sets SSL cert ─────
import config  # noqa: F401

# ── FastAPI ────────────────────────────────────────────────────────────────────
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Agent pipeline — same import targets as main.py ───────────────────────────
from db.supabase_client import db
from graph.workflow import tutor_graph
from models.state import TutorState

# ── Session-management helpers — imported directly from main.py ───────────────
# These are module-level pure functions / objects.  Importing them does NOT
# invoke main() because that is guarded by `if __name__ == "__main__"`.
from main import (
    _build_initial_state,
    _flush_session_summary,
    _parse_graded_input,
    _update_carry,
)
from carry_store import get_carry, release_carry, save_carry
from onboarding_diagnostic import grade_placement_answer, start_placement_diagnostic
from sandbox_execute import prepare_sandbox_state_overrides
from utils.presentation import build_chat_payload

logging.basicConfig(
    level=logging.WARNING,
    format="[%(levelname)s %(name)s] %(message)s",
)


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="JEE Tutor Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response schemas ────────────────────────────────────────────────

class ChatMessageRequest(BaseModel):
    user_id: str
    session_id: Optional[str] = None  # null → server creates a new Supabase session
    query: str
    roadmap_node_id: Optional[str] = None
    roadmap_concept_id: Optional[str] = None
    study_stage: Optional[str] = None  # theory | example | practice | pyq
    graded_outcome: Optional[str] = None  # "correct" | "incorrect"
    journey_type: Optional[str] = None
    focus_chapters: Optional[List[str]] = None


class FlashcardsPayload(BaseModel):
    mode: str
    cards: List[Dict[str, Any]]


class GradingUIPayload(BaseModel):
    show_correct_incorrect: bool
    prompt: str


class QuizAssessmentPayload(BaseModel):
    batch_size: int
    require_end_assessment: bool


class ProactiveCardPayload(BaseModel):
    card_type: str
    title: str
    duration_min: Optional[int] = None
    proficiency_pct: Optional[int] = None
    weak_prereq: Optional[str] = None
    preview_text: Optional[str] = None
    image_url: Optional[str] = None
    actions: List[str] = []
    concept_id: Optional[str] = None
    action: Optional[str] = None


class ChatMessageResponse(BaseModel):
    session_id: str
    response: Optional[str] = None
    score_summary: Optional[str] = None
    mistake_brief: Optional[str] = None
    agent_used: Optional[str] = None
    follow_up_suggestions: Optional[List[Dict[str, Any]]] = None
    quiz_steps: Optional[List[Dict[str, Any]]] = None
    awaiting_graded_response: bool = False
    flashcards: Optional[FlashcardsPayload] = None
    grading_ui: Optional[GradingUIPayload] = None
    quiz_assessment: Optional[QuizAssessmentPayload] = None
    card: Optional[ProactiveCardPayload] = None
    proficiency_pct: Optional[int] = None
    error: Optional[str] = None


class SandboxExecuteRequest(BaseModel):
    user_id: str
    session_id: Optional[str] = None
    action: str  # LEARN | PRACTICE | REVISE | PYQ | QUIZ | PREREQ | MOCK_SLICE
    concept_id: str
    concept_name: Optional[str] = None
    roadmap_node_id: Optional[str] = None
    content_type: Optional[str] = None
    reason: Optional[str] = None
    duration_min: Optional[int] = 8
    proficiency_pct: Optional[int] = None
    journey_type: Optional[str] = None
    focus_chapters: Optional[List[str]] = None
    weak_prereq: Optional[str] = None


class EndSessionRequest(BaseModel):
    user_id: str
    session_id: str


class EndSessionResponse(BaseModel):
    ok: bool


class ChatSessionSummary(BaseModel):
    session_id: str
    interaction_count: int = 0
    updated_at: Optional[str] = None
    preview: Optional[str] = None


class ChatSessionListResponse(BaseModel):
    sessions: List[ChatSessionSummary]


class ChatTurnItem(BaseModel):
    turn_id: str
    role: str
    content: str
    turn_index: int
    agent_used: Optional[str] = None


class ChatSessionDetailResponse(BaseModel):
    session_id: str
    interaction_count: int = 0
    turns: List[ChatTurnItem]


# ── Auth schemas ──────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str
    target_exam: str = "JEE_ADVANCED"   # "JEE_ADVANCED" | "JEE_MAINS"


class RegisterResponse(BaseModel):
    user_id: str
    name: str
    email: str
    target_exam: str


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    user_id: str
    name: str
    target_exam: str


class UserResponse(BaseModel):
    user_id: str
    target_exam: str
    preferred_depth: str


class UpdateUserRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None


class UpdateUserResponse(BaseModel):
    ok: bool


class ProbeSpec(BaseModel):
    chapter: str
    concept_id: str
    concept_name: Optional[str] = None
    reason: Optional[str] = None


class OnboardingDiagnosticRequest(BaseModel):
    user_id: str
    action: str  # "start" | "grade"
    question_count: Optional[int] = 5
    probes: Optional[List[ProbeSpec]] = None
    concept_id: Optional[str] = None
    content_id: Optional[str] = None
    is_correct: Optional[bool] = None


class DiagnosticQuestion(BaseModel):
    question_id: str
    concept_id: str
    concept_name: str
    chapter: str
    question_text: str
    content_id: str


class OnboardingDiagnosticStartResponse(BaseModel):
    questions: List[DiagnosticQuestion]


class OnboardingDiagnosticGradeResponse(BaseModel):
    concept_scores: Dict[str, float]
    overall: float


def _resolve_session(user_id: str, session_id: Optional[str]) -> str:
    if session_id:
        return session_id
    session = db.create_session(user_id)
    return session["session_id"]


def _parse_graded_input_request(
    raw_input: str,
    carry: Dict[str, Any],
    graded_outcome: Optional[str],
) -> tuple[str, Optional[Dict[str, Any]], bool]:
    graded_overrides: Optional[Dict[str, Any]] = None
    query = raw_input
    was_graded = False

    outcome = (graded_outcome or "").lower().strip()
    if outcome in {"correct", "incorrect"} and carry.get("awaiting_graded_response"):
        graded_overrides = {
            "has_graded_outcome": True,
            "student_answer": "1" if outcome == "correct" else "0",
            "is_correct": outcome == "correct",
            "error_type": None,
            "time_taken_sec": None,
        }
        query = graded_overrides["student_answer"]
        was_graded = True
        return query, graded_overrides, was_graded

    if (
        raw_input in {"1", "0"}
        and carry.get("content_type_requested") in {"practice", "pyq"}
        and carry.get("awaiting_graded_response")
    ):
        graded_overrides = {
            "has_graded_outcome": True,
            "student_answer": raw_input,
            "is_correct": raw_input == "1",
            "error_type": None,
            "time_taken_sec": None,
        }
        was_graded = True
        return query, graded_overrides, was_graded

    if raw_input.upper().startswith("ANSWER::"):
        graded_overrides = _parse_graded_input(raw_input)
        query = graded_overrides.get("student_answer", raw_input)
        was_graded = True

    return query, graded_overrides, was_graded


def _invoke_tutor(
    *,
    user_id: str,
    session_id: str,
    query: str,
    carry: Dict[str, Any],
    graded_overrides: Optional[Dict[str, Any]] = None,
    roadmap_node_id: Optional[str] = None,
    roadmap_concept_id: Optional[str] = None,
    study_stage: Optional[str] = None,
    state_overrides: Optional[Dict[str, Any]] = None,
    journey_type: Optional[str] = None,
    focus_chapters: Optional[List[str]] = None,
) -> TutorState:
    initial_state = _build_initial_state(
        user_id=user_id,
        session_id=session_id,
        query=query,
        carry=carry,
        graded_overrides=graded_overrides,
        roadmap_node_id=roadmap_node_id,
        roadmap_concept_id=roadmap_concept_id,
        study_stage=study_stage,
        journey_type=journey_type,
        focus_chapters=focus_chapters,
    )
    if state_overrides:
        initial_state.update(state_overrides)  # type: ignore[typeddict-item]
    return tutor_graph.invoke(initial_state)


def _to_chat_response(session_id: str, result: TutorState) -> ChatMessageResponse:
    payload = build_chat_payload(result)
    flashcards = payload.get("flashcards")
    grading_ui = payload.get("grading_ui")
    quiz_assessment = payload.get("quiz_assessment")
    card = payload.get("card")
    return ChatMessageResponse(
        session_id=session_id,
        response=payload.get("response"),
        score_summary=payload.get("score_summary"),
        mistake_brief=payload.get("mistake_brief"),
        agent_used=payload.get("agent_used"),
        follow_up_suggestions=payload.get("follow_up_suggestions"),
        quiz_steps=payload.get("quiz_steps"),
        awaiting_graded_response=bool(payload.get("awaiting_graded_response")),
        flashcards=FlashcardsPayload(**flashcards) if flashcards else None,
        grading_ui=GradingUIPayload(**grading_ui) if grading_ui else None,
        quiz_assessment=QuizAssessmentPayload(**quiz_assessment) if quiz_assessment else None,
        card=ProactiveCardPayload(**card) if card else None,
        proficiency_pct=payload.get("proficiency_pct"),
        error=payload.get("error"),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/api/v1/auth/register", response_model=RegisterResponse)
def register_user(req: RegisterRequest) -> RegisterResponse:
    """Create a new student account — stores email + password as-is in Supabase."""
    if len(req.password) < 6:
        raise HTTPException(status_code=422, detail="Password must be at least 6 characters.")

    existing = db.client.table("users").select("user_id").eq("email", req.email.strip()).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="An account with this email already exists. Please sign in instead.")

    try:
        user = db.register_user(
            name=req.name.strip(),
            target_exam=req.target_exam.upper(),
            preferred_depth="standard",
        )
        db.client.table("users").update({
            "email": req.email.strip(),
            "password_hash": req.password,
        }).eq("user_id", user["user_id"]).execute()

        return RegisterResponse(
            user_id=user["user_id"],
            name=user.get("name", req.name),
            email=req.email.strip(),
            target_exam=user.get("target_exam", req.target_exam),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logging.exception("register_user failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/v1/auth/login", response_model=LoginResponse)
def login_user(req: LoginRequest) -> LoginResponse:
    """Sign in — match email + password directly against the users table."""
    res = (
        db.client.table("users")
        .select("user_id, name, target_exam")
        .eq("email", req.email.strip())
        .eq("password_hash", req.password)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    user = res.data[0]
    return LoginResponse(
        user_id=user["user_id"],
        name=user.get("name", ""),
        target_exam=user.get("target_exam", "JEE_ADVANCED"),
    )


@app.put("/api/v1/auth/user/{user_id}", response_model=UpdateUserResponse)
def update_user(user_id: str, req: UpdateUserRequest) -> UpdateUserResponse:
    """Update a user's name and/or email in Supabase."""
    updates: dict = {}
    if req.name is not None:
        updates["name"] = req.name.strip()
    if req.email is not None:
        # Check the new email isn't already taken by a different account
        if req.email.strip():
            existing = (
                db.client.table("users")
                .select("user_id")
                .eq("email", req.email.strip())
                .neq("user_id", user_id)
                .execute()
            )
            if existing.data:
                raise HTTPException(status_code=409, detail="That email is already in use by another account.")
        updates["email"] = req.email.strip()

    if not updates:
        return UpdateUserResponse(ok=True)

    try:
        db.client.table("users").update(updates).eq("user_id", user_id).execute()
        return UpdateUserResponse(ok=True)
    except Exception as exc:
        logging.exception("update_user failed for %s", user_id)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v1/auth/user/{user_id}", response_model=UserResponse)
def get_user(user_id: str) -> UserResponse:
    """Validate a user_id — used as a fallback check."""
    user = db.read_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return UserResponse(
        user_id=user_id,
        target_exam=user.get("target_exam", "JEE_ADVANCED"),
        preferred_depth=user.get("preferred_depth", "standard"),
    )


@app.post("/api/v1/onboarding/diagnostic")
def onboarding_diagnostic(req: OnboardingDiagnosticRequest):
    """
    Placement diagnostic for onboarding.

    action=start  → batch of practice questions across diverse concepts
    action=grade  → self-grade one answer; updates user_concept_state via Diagnostic agent
    """
    action = (req.action or "").lower().strip()
    if action == "start":
        probe_payload = [p.model_dump() for p in req.probes] if req.probes else None
        result = start_placement_diagnostic(
            req.user_id,
            req.question_count or 5,
            probes=probe_payload,
        )
        return OnboardingDiagnosticStartResponse(questions=result.get("questions", []))
    if action == "grade":
        if not req.concept_id:
            raise HTTPException(status_code=422, detail="concept_id is required for grade.")
        if req.is_correct is None:
            raise HTTPException(status_code=422, detail="is_correct is required for grade.")
        result = grade_placement_answer(
            user_id=req.user_id,
            concept_id=req.concept_id,
            content_id=req.content_id or "",
            is_correct=bool(req.is_correct),
        )
        return OnboardingDiagnosticGradeResponse(
            concept_scores=result.get("concept_scores", {}),
            overall=result.get("overall", 0.5),
        )
    raise HTTPException(status_code=422, detail="action must be 'start' or 'grade'.")


@app.post("/api/v1/chat/message", response_model=ChatMessageResponse)
def chat_message(req: ChatMessageRequest) -> ChatMessageResponse:
    """Process one student message through the LangGraph pipeline."""
    user_id = req.user_id
    raw_input = req.query.strip()
    session_id = _resolve_session(user_id, req.session_id)
    carry = get_carry(session_id)

    query, graded_overrides, was_graded = _parse_graded_input_request(
        raw_input, carry, req.graded_outcome
    )

    try:
        stage_overrides: Dict[str, Any] = {}
        if req.study_stage in {"theory", "example", "practice", "pyq"}:
            stage_overrides = {
                "study_stage": req.study_stage,
                "intent": "doubt",
                "content_type_requested": req.study_stage,
            }
        result = _invoke_tutor(
            user_id=user_id,
            session_id=session_id,
            query=query,
            carry=carry,
            graded_overrides=graded_overrides,
            roadmap_node_id=req.roadmap_node_id,
            roadmap_concept_id=req.roadmap_concept_id,
            study_stage=req.study_stage,
            state_overrides=stage_overrides or None,
            journey_type=req.journey_type,
            focus_chapters=req.focus_chapters,
        )
    except Exception as exc:
        logging.exception("tutor_graph.invoke failed for session %s", session_id)
        return ChatMessageResponse(session_id=session_id, error=str(exc))

    _update_carry(carry, result, was_graded=was_graded)
    save_carry(session_id, carry)
    return _to_chat_response(session_id, result)


@app.post("/api/v1/sandbox/execute", response_model=ChatMessageResponse)
def sandbox_execute(req: SandboxExecuteRequest) -> ChatMessageResponse:
    """Run a structured sandbox action without a free-form student query."""
    session_id = _resolve_session(req.user_id, req.session_id)
    carry = get_carry(session_id)

    from sandbox_execute import build_sandbox_card

    card_meta = build_sandbox_card(
        action=req.action,
        concept_id=req.concept_id,
        concept_name=req.concept_name,
        proficiency_pct=req.proficiency_pct,
        weak_prereq=req.weak_prereq,
        duration_min=req.duration_min or 8,
        reason=req.reason,
    )
    query, overrides = prepare_sandbox_state_overrides(
        action=req.action,
        concept_id=req.concept_id,
        concept_name=req.concept_name,
        roadmap_node_id=req.roadmap_node_id,
        content_type=req.content_type,
        card_meta=card_meta,
    )

    try:
        result = _invoke_tutor(
            user_id=req.user_id,
            session_id=session_id,
            query=query,
            carry=carry,
            roadmap_node_id=req.roadmap_node_id,
            roadmap_concept_id=req.concept_id,
            state_overrides=overrides,
            journey_type=req.journey_type,
            focus_chapters=req.focus_chapters,
        )
        result["sandbox_card"] = card_meta
    except Exception as exc:
        logging.exception("sandbox_execute failed for session %s", session_id)
        return ChatMessageResponse(session_id=session_id, error=str(exc))

    _update_carry(carry, result, was_graded=False)
    save_carry(session_id, carry)
    return _to_chat_response(session_id, result)


@app.get("/api/v1/chat/sessions", response_model=ChatSessionListResponse)
def list_chat_sessions(user_id: str, limit: int = 20) -> ChatSessionListResponse:
    """List recent study sessions for a user (newest first)."""
    capped = max(1, min(limit, 50))
    rows = db.list_user_sessions(user_id, limit=capped)
    summaries: List[ChatSessionSummary] = []
    for row in rows:
        session_id = row["session_id"]
        summaries.append(
            ChatSessionSummary(
                session_id=session_id,
                interaction_count=row.get("interaction_count") or 0,
                updated_at=row.get("updated_at"),
                preview=db.first_user_turn_preview(session_id),
            )
        )
    return ChatSessionListResponse(sessions=summaries)


@app.get("/api/v1/chat/sessions/{session_id}", response_model=ChatSessionDetailResponse)
def get_chat_session(session_id: str, user_id: str) -> ChatSessionDetailResponse:
    """Return full message history for a session owned by the user."""
    session = db.read_session(session_id)
    if not session or session.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Session not found.")
    turns = db.read_all_chat_turns(session_id)
    return ChatSessionDetailResponse(
        session_id=session_id,
        interaction_count=session.get("interaction_count") or 0,
        turns=[
            ChatTurnItem(
                turn_id=t.get("turn_id") or str(t.get("turn_index", 0)),
                role=t.get("role") or "assistant",
                content=t.get("content") or "",
                turn_index=t.get("turn_index") or 0,
                agent_used=t.get("agent_used"),
            )
            for t in turns
        ],
    )


@app.post("/api/v1/chat/end-session", response_model=EndSessionResponse)
def end_session(req: EndSessionRequest) -> EndSessionResponse:
    """
    Flush the Supabase session summary and release in-memory carry state.

    This is the HTTP equivalent of main.py's `finally` block.  The frontend
    should call this when the user explicitly ends the session.

    Gap accepted for this phase: if the client never calls this endpoint
    (tab close, crash, network drop), the summary flush does not happen for
    that session.  See module docstring, note 3.
    """
    _flush_session_summary(session_id=req.session_id, user_id=req.user_id)
    release_carry(req.session_id)
    return EndSessionResponse(ok=True)
