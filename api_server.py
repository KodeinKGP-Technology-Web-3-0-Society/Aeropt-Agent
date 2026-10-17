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

Design notes
------------
1. Ephemeral carry still lives in `.carry_sessions/` JSON (quiz cursor,
   awaiting_graded_response, retrieved chunks). Idle files older than 2 hours
   are pruned on access.

2. Rich turn presentation (slides, flashcards, grading_ui, structured, …) is
   persisted on `chat_turns.presentation` (jsonb). See
   `db/migrations/001_chat_turns_presentation.sql`. Carry keeps a copy during
   the live session as a read fallback only.

3. End-session gap: if the frontend never calls /end-session (tab close,
   crash, etc.), _flush_session_summary never runs.  Sessions with < 5
   interactions produce no Supabase summary.  Presentation still survives in
   `chat_turns.presentation` after carry release.

4. Shared carry (Redis / networked store) is only needed if multiple agent
   hosts must share quiz cursor / awaiting_graded_response mid-session.
   Presentation history does not require shared carry.

"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*pydantic.v1.*", category=UserWarning)

import logging
from contextlib import asynccontextmanager
import threading
from typing import Any, Dict, List, Optional
import asyncio
import os
import json
import time

# ── config must be imported first — calls load_dotenv() and sets SSL cert ─────
import config  # noqa: F401

# ── FastAPI ────────────────────────────────────────────────────────────────────
from fastapi import FastAPI, Header, HTTPException, File, Form, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Agent pipeline — same import targets as main.py ───────────────────────────
from db.supabase_client import db
from agents.learning_coach import coach_user
from graph.workflow import tutor_graph
from models.state import TutorState

# ── Session-management helpers — imported from services.turn_service ───────────
from services.auth import AgentAuthMiddleware, check_production_config
from services.classroom_evidence import post_turn_evidence, resolve_learner_token
from services.turn_service import (
    build_initial_state as _build_initial_state,
    clear_stale_practice_carry,
    flush_session_summary as _flush_session_summary,
    parse_legacy_answer as _parse_graded_input,
    parse_turn_input as _parse_graded_input_request,
    resolve_session as _turn_resolve_session,
    update_carry as _update_carry,
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

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Check the retrieval database and its embedding contract before the first student turn."""
    from retrieval import embedder
    from retrieval import service as retrieval_service
    from retrieval import store as retrieval_store

    production = os.getenv("AEROPT_ENV", "").strip().lower() == "production"
    log = logging.getLogger("aeropt.startup")
    try:
        contract = retrieval_store.verify_embedding_contract()
        result = retrieval_service.warm()
        log.warning("retrieval database: %s %s", contract, retrieval_service.status() if result.get("warmed") else result)
    except embedder.EmbeddingMismatch:
        raise  # vectors from another model rank noise; never serve on them
    except Exception as exc:  # noqa: BLE001
        if production:
            raise RuntimeError(f"Retrieval database unavailable: {exc}") from exc
    # Model drift check against provider availability (cached 1h)
    try:
        from utils.groq_pool import check_model_drift
        missing = check_model_drift()
        if missing:
            log.error("Model drift detected: %s missing from provider", ", ".join(missing))
    except Exception as exc:
        log.warning("Model drift check skipped: %s", exc)

    # Episodic memory is not a process-wide sweeper. Recall and commit run as
    # graph nodes (agents/memory_agent.py) and fail open on their own.
    try:
        from agents.memory_status import log_episodic_status

        log_episodic_status(log)
    except Exception:
        log.warning("episodic memory status check failed")
    yield
    from services import durable_state

    durable_state.drain()


check_production_config()

app = FastAPI(title="JEE Tutor Agent API", version="1.0.0", lifespan=_lifespan)

# Added before CORS so CORS wraps it: a 401/429 must still carry CORS headers,
# or the browser hides the status and the frontend cannot refresh the token.
app.add_middleware(AgentAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ALLOWED_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://10.145.27.236:3000",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
@app.get("/api/v1/health")
def health() -> Dict[str, str]:
    """Liveness probe for local CI/CD. Does not touch Neo4j or Supabase."""
    return {"status": "ok", "service": "aeropt-agent"}


# ── Request / Response schemas ────────────────────────────────────────────────

class ChatMessageRequest(BaseModel):
    # Django is the auth authority. This is the Supabase UUID from Django login
    # (`supabase_user_id`), not a separately registered agent account.
    user_id: str
    session_id: Optional[str] = None  # null → server creates a new Supabase session
    query: str
    roadmap_node_id: Optional[str] = None
    roadmap_concept_id: Optional[str] = None
    study_stage: Optional[str] = None  # theory | example | practice | pyq
    graded_outcome: Optional[str] = None  # "correct" | "incorrect"
    journey_type: Optional[str] = None
    focus_chapters: Optional[List[str]] = None
    flagged_step_id: Optional[str] = None
    # Learner's Django JWT. Used only to post classroom evidence. The chat
    # reply still returns when this is missing.
    classroom_token: Optional[str] = None
    # Room shape. lesson | doubt | assess. Absent means the ordinary tutor turn.
    surface: Optional[str] = None
    student_work: Optional[str] = None
    assess_action: Optional[str] = None  # build | grade
    submissions: Optional[List[Dict[str, Any]]] = None


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


class StudySlideItem(BaseModel):
    id: str
    type: str
    title: str
    body_md: str
    image_url: Optional[str] = None


class SlidesPayload(BaseModel):
    mode: str
    slides: List[StudySlideItem]
    current_index: Optional[int] = None


class StructuredLessonPayload(BaseModel):
    filling_gap: str = ""
    solution_steps: List[str] = []
    insight: str = ""


class TurnPresentationPayload(BaseModel):
    slides: Optional[SlidesPayload] = None
    flashcards: Optional[FlashcardsPayload] = None
    grading_ui: Optional[GradingUIPayload] = None
    quiz_assessment: Optional[QuizAssessmentPayload] = None
    card: Optional[ProactiveCardPayload] = None
    mistake_brief: Optional[str] = None
    score_summary: Optional[str] = None
    structured: Optional[StructuredLessonPayload] = None
    proficiency_pct: Optional[int] = None
    awaiting_graded_response: bool = False
    follow_up_suggestions: Optional[List[Any]] = None
    quiz_steps: Optional[List[Any]] = None
    primary_concept_id: Optional[str] = None
    content_type_requested: Optional[str] = None
    practice_question: Optional[Dict[str, Any]] = None
    practice_batch: Optional[List[Dict[str, Any]]] = None
    active_agent: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None


class ChatMessageResponse(BaseModel):
    session_id: str
    response: Optional[str] = None
    score_summary: Optional[str] = None
    mistake_brief: Optional[str] = None
    agent_used: Optional[str] = None
    follow_up_suggestions: Optional[List[Any]] = None
    quiz_steps: Optional[List[Any]] = None
    awaiting_graded_response: bool = False
    flashcards: Optional[FlashcardsPayload] = None
    grading_ui: Optional[GradingUIPayload] = None
    quiz_assessment: Optional[QuizAssessmentPayload] = None
    card: Optional[ProactiveCardPayload] = None
    slides: Optional[SlidesPayload] = None
    structured: Optional[StructuredLessonPayload] = None
    proficiency_pct: Optional[int] = None
    primary_concept_id: Optional[str] = None
    content_type_requested: Optional[str] = None
    practice_question: Optional[Dict[str, Any]] = None
    practice_batch: Optional[List[Dict[str, Any]]] = None
    active_agent: Optional[str] = None
    agent_signals: Optional[Dict[str, Any]] = None
    steps: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


class AgentCardPayload(BaseModel):
    id: str
    name: str
    description: str
    status: str
    signal: str
    signal_type: str


class AgentStatusResponse(BaseModel):
    agents: List[AgentCardPayload]
    active_agent: Optional[str] = None


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


class CoachRunRequest(BaseModel):
    user_id: str
    #: What prompted this look — a finished session, a due review, a nightly
    #: sweep. The coach weighs the trigger, so say something real.
    trigger: str = "scheduled check"
    session_id: Optional[str] = None


class CoachRunResponse(BaseModel):
    ok: bool
    #: False is the common, healthy outcome: nothing warranted acting on.
    acted: bool
    actions: List[str] = []
    note: Optional[str] = None
    trace_path: List[str] = []
    error: Optional[str] = None


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
    title: Optional[str] = None
    kind: Optional[str] = None


class ChatSessionListResponse(BaseModel):
    sessions: List[ChatSessionSummary]


class CreateSessionRequest(BaseModel):
    user_id: str
    kind: Optional[str] = None  # "chat" for the general chat page; None for room sessions


class UpdateSessionRequest(BaseModel):
    user_id: str
    title: Optional[str] = None


class CreateSessionResponse(BaseModel):
    session_id: str


class ChatTurnItem(BaseModel):
    turn_id: str
    role: str
    content: str
    turn_index: int
    agent_used: Optional[str] = None
    presentation: Optional[TurnPresentationPayload] = None


class ChatSessionDetailResponse(BaseModel):
    session_id: str
    interaction_count: int = 0
    turns: List[ChatTurnItem]


class ConceptProficiencyResponse(BaseModel):
    concept_id: str
    proficiency_pct: Optional[int] = None
    proficiency_score: Optional[float] = None
    flagged_weak: Optional[bool] = None


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
    image_url: Optional[str] = None
    solution_text: Optional[str] = None
    solution_image_url: Optional[str] = None


class OnboardingDiagnosticStartResponse(BaseModel):
    questions: List[DiagnosticQuestion]


class OnboardingDiagnosticGradeResponse(BaseModel):
    concept_scores: Dict[str, float]
    overall: float


def _resolve_session(
    user_id: str,
    session_id: Optional[str],
    *,
    create_reason: str = "free",
    roadmap_node_id: Optional[str] = None,
) -> str:
    try:
        return _turn_resolve_session(
            user_id=user_id,
            session_id=session_id,
            create_reason=create_reason,
            roadmap_node_id=roadmap_node_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")


def _presentation_blob(response: ChatMessageResponse) -> Dict[str, Any]:
    return {
        "slides": response.slides.model_dump() if response.slides else None,
        "flashcards": response.flashcards.model_dump() if response.flashcards else None,
        "grading_ui": response.grading_ui.model_dump() if response.grading_ui else None,
        "quiz_assessment": (
            response.quiz_assessment.model_dump() if response.quiz_assessment else None
        ),
        "card": response.card.model_dump() if response.card else None,
        "mistake_brief": response.mistake_brief,
        "score_summary": response.score_summary,
        "structured": response.structured.model_dump() if response.structured else None,
        "proficiency_pct": response.proficiency_pct,
        "awaiting_graded_response": response.awaiting_graded_response,
        "follow_up_suggestions": response.follow_up_suggestions,
        "quiz_steps": response.quiz_steps,
        "primary_concept_id": response.primary_concept_id,
        "content_type_requested": response.content_type_requested,
        "practice_question": response.practice_question,
        "practice_batch": response.practice_batch,
        "active_agent": response.active_agent,
        "steps": response.steps,
    }


def _ledger_tutor_turn(result: Dict[str, Any], session_id: str, *, source: str) -> None:
    """One learning_events row per tutor turn: what the student worked on, where.

    Graded attempts write their own rows; this is the rest — questions asked,
    lessons, doubts — so progress shows practice the grader never saw."""
    concept = result.get("primary_concept_id") or result.get("concept_id")
    query = (result.get("query") or "").strip()
    if not concept and not query:
        return
    db.insert_learning_event(
        result.get("user_id") or "",
        "tutor_turn",
        source=source if source in ("chat", "study", "practice", "assess", "lesson", "doubt") else "chat",
        concept_id=concept or None,
        session_id=session_id,
        study_session_id=result.get("study_session_id"),
        payload={
            "query": query[:200],
            "phase": result.get("current_phase"),
            "tools": list(result.get("tools_used") or [])[:12],
        },
    )


def _store_turn_presentation(
    session_id: str,
    carry: Dict[str, Any],
    result: TutorState,
    response: ChatMessageResponse,
) -> None:
    interaction = result.get("interaction_count")
    if not interaction:
        return
    _ledger_tutor_turn(result, session_id, source=result.get("classroom_surface") or "chat")
    turn_index = int(interaction) * 2
    blob = _presentation_blob(response)
    by_turn = carry.setdefault("presentation_by_turn", {})
    by_turn[str(turn_index)] = blob
    try:
        updated = db.update_chat_turn_presentation(session_id, turn_index, blob)
        if not updated:
            # The chat_turns constraint is handled inside insert_chat_turn now
            # (see SupabaseClient._AGENT_USED_COMPAT) — this used to keep its own
            # copy of the allowed list, which drifted from the labels in use.
            agent_for_db = result.get("agent_used") or "learning_agent"
            db.insert_chat_turn(
                session_id=session_id,
                role="assistant",
                content=result.get("response") or "",
                turn_index=turn_index,
                agent_used=agent_for_db,
                presentation=blob,
            )
    except Exception:
        logging.exception(
            "Failed to persist presentation to Supabase "
            "(session=%s turn_index=%s)",
            session_id,
            turn_index,
        )




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


def _persist_turn_side_effects(
    *,
    session_id: str,
    carry: Dict[str, Any],
    result: TutorState,
    was_graded: bool,
    chat_response: ChatMessageResponse,
) -> None:
    """Carry + presentation persistence after the graph (including Diagnostic/Follow-Up)."""
    _update_carry(carry, result, was_graded=was_graded)
    _store_turn_presentation(session_id, carry, result, chat_response)
    save_carry(session_id, carry)


_PREFETCH_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _prefetch_next_query(session_id: str, result: TutorState) -> None:
    """Warm GraphRAG for the first follow-up suggestion (in-process or Redis)."""
    follow_ups = result.get("follow_up_suggestions")
    if not follow_ups or not isinstance(follow_ups, list):
        return
    next_query = follow_ups[0]
    if isinstance(next_query, dict):
        next_query = next_query.get("text", "")
    next_query = (next_query or "").strip()
    if not next_query:
        return
    try:
        from agents.concept_resolver import concept_resolver_node
        from agents.planner import planner_node
        from agents.graphrag_retriever import graphrag_retriever_node

        p_state = dict(result)
        p_state["query"] = next_query
        p_state["content_type_requested"] = "theory"
        p_state.update(concept_resolver_node(p_state))
        p_state.update(planner_node(p_state))
        p_state.update(graphrag_retriever_node(p_state))
        cache_key = f"prefetch:{session_id}:{next_query}"
        cache_val = {
            "retrieved_chunks": p_state.get("retrieved_chunks", []),
            "primary_concept_id": p_state.get("primary_concept_id"),
            "secondary_concept_ids": p_state.get("secondary_concept_ids", []),
            "target_concept_state": p_state.get("target_concept_state"),
            "prereq_concept_states": p_state.get("prereq_concept_states"),
            "secondary_concept_states": p_state.get("secondary_concept_states"),
            "difficulty_target": p_state.get("difficulty_target"),
            "difficulty_min": p_state.get("difficulty_min"),
            "difficulty_max": p_state.get("difficulty_max"),
            "retrieval_mode": p_state.get("retrieval_mode"),
        }
        _PREFETCH_CACHE[cache_key] = (time.time(), cache_val)
        if db.redis:
            db.redis.setex(cache_key, 120, json.dumps(cache_val))
        logging.info("Prefetched for next query: %s", next_query)
    except Exception:
        logging.exception("Prefetch failed for session %s", session_id)


def _attach_agent_signals(result: TutorState, user_id: str, focus_chapters: Optional[List[str]] = None) -> TutorState:
    """Lightweight agent roster snapshot for the frontend panel."""
    if result.get("agent_signals"):
        return result
    try:
        from mcp_tools.get_agent_signals import GetAgentSignalsArgs, get_agent_signals

        snapshot = get_agent_signals(
            GetAgentSignalsArgs(
                user_id=user_id,
                session_id=result.get("session_id"),
                focus_chapters=focus_chapters or [],
                roadmap_concept_id=result.get("roadmap_concept_id"),
                active_agent=result.get("active_agent"),
                primary_concept_id=result.get("primary_concept_id"),
            )
        )
        result = dict(result)
        result["agent_signals"] = snapshot
    except Exception:
        pass
    return result


_STUDENT_FACING_ERROR = (
    "Something went wrong generating a response. Please try again — if it keeps "
    "happening, start a new session."
)


def _student_facing_error(raw: Optional[str]) -> Optional[str]:
    """
    Turn an internal failure into a message safe to show a student.

    Every agent node's exception handler (and the two outer try/except blocks
    in this file) returns/raises the raw Python exception text, e.g.
    "GraphRAG Retriever failed: 'NoneType' object has no attribute 'get'" —
    that string used to flow straight into ChatMessageResponse.error and from
    there into the chat UI verbatim (Aeropt-frontend/ChatWorkspace.tsx throws
    it as-is). The raw detail is still logged server-side; students just get a
    generic, actionable message instead of a stack-trace fragment.
    """
    if not raw:
        return None
    logging.error("[student-facing error suppressed] %s", raw)
    return _STUDENT_FACING_ERROR


def _to_chat_response(session_id: str, result: TutorState) -> ChatMessageResponse:
    payload = build_chat_payload(result)
    flashcards = payload.get("flashcards")
    grading_ui = payload.get("grading_ui")
    quiz_assessment = payload.get("quiz_assessment")
    card = payload.get("card")
    slides = payload.get("slides")
    structured = payload.get("structured")
    practice_batch = payload.get("practice_batch")
    if not isinstance(practice_batch, list):
        practice_batch = None
    practice_question = payload.get("practice_question")
    if not practice_question and practice_batch and len(practice_batch) == 1:
        practice_question = practice_batch[0]
        
    steps = payload.get("steps")

    return ChatMessageResponse(
        session_id=session_id,
        response=payload.get("response"),
        score_summary=payload.get("score_summary"),
        mistake_brief=payload.get("mistake_brief"),
        agent_used=payload.get("agent_used"),
        follow_up_suggestions=payload.get("follow_up_suggestions"),
        quiz_steps=payload.get("quiz_steps"),
        awaiting_graded_response=bool(payload.get("awaiting_graded_response")),
        flashcards=FlashcardsPayload(**flashcards) if flashcards and flashcards.get("mode") else None,
        grading_ui=GradingUIPayload(**grading_ui) if grading_ui else None,
        quiz_assessment=QuizAssessmentPayload(**quiz_assessment) if quiz_assessment else None,
        card=ProactiveCardPayload(**card) if card and card.get("card_type") else None,
        slides=SlidesPayload(**slides) if slides and slides.get("mode") else None,
        structured=StructuredLessonPayload(**structured) if structured else None,
        proficiency_pct=payload.get("proficiency_pct"),
        primary_concept_id=payload.get("primary_concept_id"),
        content_type_requested=payload.get("content_type_requested"),
        practice_question=practice_question,
        practice_batch=practice_batch,
        active_agent=result.get("active_agent") or payload.get("active_agent"),
        agent_signals=result.get("agent_signals") or payload.get("agent_signals"),
        steps=steps,
        error=_student_facing_error(payload.get("error")),
    )





# ── Endpoints ─────────────────────────────────────────────────────────────────
# Auth lives on Django (:8000 /api/v1/auth/). The agent does not register or
# log in students; tutoring payloads carry user_id (Django-linked Supabase UUID).

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


@app.get("/api/v1/agents/status", response_model=AgentStatusResponse)
def agents_status(
    user_id: str,
    session_id: Optional[str] = None,
    roadmap_concept_id: Optional[str] = None,
    active_agent: Optional[str] = None,
    primary_concept_id: Optional[str] = None,
    focus_chapters: Optional[str] = None,
) -> AgentStatusResponse:
    """Return the five product-agent cards with live status signals."""
    from mcp_tools.get_agent_signals import GetAgentSignalsArgs, get_agent_signals

    chapters = [c.strip() for c in (focus_chapters or "").split(",") if c.strip()]
    payload = get_agent_signals(
        GetAgentSignalsArgs(
            user_id=user_id,
            session_id=session_id,
            focus_chapters=chapters,
            roadmap_concept_id=roadmap_concept_id,
            active_agent=active_agent,
            primary_concept_id=primary_concept_id,
        )
    )
    agents = [AgentCardPayload(**a) for a in payload.get("agents", [])]
    return AgentStatusResponse(agents=agents, active_agent=payload.get("active_agent"))


# A turn can be silent for a long time: nothing streams until the answer checker has
# passed, and on a slower model provider a tool-using turn runs for a minute or more.
# Without traffic, the browser client's stall timer (and any idle-timeout proxy in
# front of this service) reads that silence as a dead connection. An SSE comment line
# is ignored by every client and resets all of those timers.
SSE_KEEPALIVE_S = float(os.environ.get("SSE_KEEPALIVE_S", "15"))

# Every proxy between here and the browser must pass events through as they are
# written. Without `no-transform`, Next's dev proxy (the /agent-api rewrite)
# gzip-compressed the stream and buffered it whole: status events and tokens
# all arrived with the final answer, so the student watched "Starting…" for the
# entire turn. X-Accel-Buffering does the same for nginx in front of a deploy.
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


async def _sse_frames(queue: "asyncio.Queue", keepalive_s: float = SSE_KEEPALIVE_S):
    """Turn the worker's message queue into SSE frames, ending after the result."""
    while True:
        try:
            msg = await asyncio.wait_for(queue.get(), timeout=keepalive_s)
        except asyncio.TimeoutError:
            yield ": keepalive\n\n"
            continue
        if msg["type"] == "token":
            yield f"event: token\ndata: {json.dumps(msg['data'])}\n\n"
        elif msg["type"] == "status":
            yield f"event: status\ndata: {json.dumps(msg['data'])}\n\n"
        elif msg["type"] == "result":
            yield f"event: result\ndata: {msg['data']}\n\n"
            return


@app.post("/api/v1/chat/message")
async def chat_message(
    req: ChatMessageRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Process one student message through the LangGraph pipeline with SSE streaming."""
    user_id = req.user_id
    learner_token = resolve_learner_token(req.classroom_token, authorization)
    raw_input = req.query.strip()
    create_reason = "roadmap" if req.roadmap_node_id else "free"
    # _resolve_session and get_carry are DB calls but short, running in main thread for simplicity before stream starts
    try:
        session_id = _resolve_session(
            user_id,
            req.session_id,
            create_reason=create_reason,
            roadmap_node_id=req.roadmap_node_id,
        )
        carry = get_carry(session_id)
        query, graded_overrides, was_graded = _parse_graded_input_request(
            raw_input, carry, req.graded_outcome
        )
        if req.flagged_step_id:
            step_text = "(Step text unavailable)"
            by_turn = carry.get("presentation_by_turn", {})
            if by_turn:
                latest_turn = max(by_turn.keys(), key=int)
                steps = by_turn[latest_turn].get("steps", [])
                for s in steps:
                    if s.get("step_id") == req.flagged_step_id:
                        step_text = s.get("text", "")
                        break
            query = f"I didn't understand this step of the previous explanation: '{step_text}' — explain just this step differently, in more detail."

        if not was_graded:
            clear_stale_practice_carry(carry)
            save_carry(session_id, carry)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    streamed_parts: List[str] = []

    def sync_cb(token: str):
        streamed_parts.append(token)
        loop.call_soon_threadsafe(queue.put_nowait, {"type": "token", "data": token})

    def sync_worker():
        from services import progress
        from utils.stream import stream_callback_var
        stream_callback_var.set(sync_cb)
        # Tool and stage events ("Reading your course material…") as SSE `status`
        # frames, the same feed the Study Room shows while the tutor works.
        progress.install(
            lambda event: loop.call_soon_threadsafe(queue.put_nowait, {"type": "status", "data": event})
        )
        try:
            stage_overrides: Dict[str, Any] = {}
            
            # Check prefetch cache for next_query fast-forward (Redis or in-process)
            cache_key = f"prefetch:{session_id}:{query.strip()}"
            cached_data = None
            from db.supabase_client import db
            if db.redis:
                try:
                    cached_str = db.redis.get(cache_key)
                    if cached_str:
                        cached_data = json.loads(cached_str)
                except Exception:
                    pass
            if not cached_data and cache_key in _PREFETCH_CACHE:
                ts, val = _PREFETCH_CACHE[cache_key]
                if time.time() - ts < 120:
                    cached_data = val
                else:
                    _PREFETCH_CACHE.pop(cache_key, None)

            if cached_data:
                stage_overrides.update(cached_data)
                stage_overrides["_prefetch_fast_forward"] = True
                logging.info("Used prefetch cache for query: %s", query.strip())
                    
            if req.study_stage in {"theory", "example", "practice", "pyq"}:
                stage_overrides.update({
                    "study_stage": req.study_stage,
                    "content_type_requested": req.study_stage,
                })
            from services.classroom_rooms import handoff_events, prepare_room

            room = prepare_room(
                surface=req.surface,
                concept_id=req.roadmap_concept_id,
                student_work=req.student_work,
                assess_action=req.assess_action,
                submissions=req.submissions,
                user_id=user_id,
                session_id=session_id,
                token=learner_token,
            )
            if room:
                stage_overrides.update(room["state_overrides"])
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
            final_session_id = result.get("session_id") or session_id

            result = _attach_agent_signals(result, user_id, req.focus_chapters)

            # If the solver streamed tokens but the state response is None,
            # inject the accumulated stream so the frontend always gets real text.
            if not result.get("response") and streamed_parts:
                result["response"] = "".join(streamed_parts)

            chat_response = _to_chat_response(final_session_id, result)
            _persist_turn_side_effects(
                session_id=final_session_id,
                carry=carry,
                result=result,
                was_graded=was_graded,
                chat_response=chat_response,
            )
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "result", "data": chat_response.model_dump_json()},
            )
            # After the reply is queued. A down Django process is logged and
            # does not replace the chat reply.
            post_turn_evidence(
                result=result,
                was_graded=was_graded,
                study_stage=req.study_stage,
                token=learner_token,
            )
            if (req.surface or "").strip().lower() == "doubt":
                from services.classroom_evidence import post_events

                post_events(
                    handoff_events(
                        req.roadmap_concept_id or "",
                        result.get("agent_trace_path"),
                    ),
                    token=learner_token,
                )

            def prefetch_bg():
                from services import progress
                from utils.stream import stream_callback_var
                stream_callback_var.set(None)
                progress.install(None)
                try:
                    _prefetch_next_query(final_session_id, result)
                except Exception:
                    logging.exception("Prefetch failed for session %s", final_session_id)

            loop.call_soon_threadsafe(lambda: asyncio.create_task(asyncio.to_thread(prefetch_bg)))
        except Exception as exc:
            logging.exception("tutor_graph.invoke failed for session %s", session_id)
            err = ChatMessageResponse(session_id=session_id, error=_student_facing_error(str(exc)))
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "result", "data": err.model_dump_json()})

    asyncio.create_task(asyncio.to_thread(sync_worker))

    return StreamingResponse(_sse_frames(queue), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/v1/sandbox/execute", response_model=ChatMessageResponse)
def sandbox_execute(req: SandboxExecuteRequest) -> ChatMessageResponse:
    """Run a structured sandbox action without a free-form student query."""
    session_id = _resolve_session(
        req.user_id,
        req.session_id,
        create_reason="sandbox",
        roadmap_node_id=req.roadmap_node_id,
    )
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
        session_id = result.get("session_id") or session_id
    except Exception as exc:
        logging.exception("sandbox_execute failed for session %s", session_id)
        return ChatMessageResponse(session_id=session_id, error=_student_facing_error(str(exc)))

    _update_carry(carry, result, was_graded=False)
    chat_response = _to_chat_response(session_id, result)
    _store_turn_presentation(session_id, carry, result, chat_response)
    save_carry(session_id, carry)
    return chat_response


@app.get(
    "/api/v1/concepts/{concept_id}/proficiency",
    response_model=ConceptProficiencyResponse,
)
def get_concept_proficiency(concept_id: str, user_id: str) -> ConceptProficiencyResponse:
    """Durable mastery for the Live proficiency panel (Supabase user_concept_state)."""
    try:
        row = db.read_user_concept_state(user_id, concept_id)
    except Exception:
        logging.warning(
            "concept-proficiency-miss user=%s concept=%s",
            user_id,
            concept_id,
        )
        return ConceptProficiencyResponse(concept_id=concept_id)

    if not row:
        return ConceptProficiencyResponse(concept_id=concept_id)

    score = float(row.get("proficiency_score") or 0.5)
    return ConceptProficiencyResponse(
        concept_id=concept_id,
        proficiency_pct=int(round(score * 100)),
        proficiency_score=score,
        flagged_weak=bool(row.get("flagged_weak")),
    )


@app.post("/api/v1/chat/sessions", response_model=CreateSessionResponse)
def create_chat_session(req: CreateSessionRequest) -> CreateSessionResponse:
    """Create an empty study session so the client can land on /chat/<id> before the first message."""
    try:
        session = db.create_session(req.user_id, kind=(req.kind or "").strip() or None)
    except Exception as exc:
        logging.exception("create_session failed for user %s", req.user_id)
        raise HTTPException(status_code=502, detail=f"Could not create session: {exc}") from exc
    session_id = session.get("session_id")
    if not session_id:
        raise HTTPException(status_code=502, detail="Session created without an id.")
    logging.warning("session-create reason=explicit_api user=%s session=%s", req.user_id, session_id)
    return CreateSessionResponse(session_id=session_id)


@app.get("/api/v1/chat/sessions", response_model=ChatSessionListResponse)
def list_chat_sessions(user_id: str, limit: int = 20, kind: Optional[str] = None) -> ChatSessionListResponse:
    """List recent sessions for a user (newest first); `kind=chat` lists only
    the general chat page's conversations."""
    capped = max(1, min(limit, 100))
    try:
        rows = db.list_user_sessions(user_id, limit=capped, kind=(kind or "").strip() or None)
    except Exception as exc:
        logging.exception("list_user_sessions failed for user %s", user_id)
        raise HTTPException(status_code=502, detail=f"Could not list sessions: {exc}") from exc

    session_ids = [row["session_id"] for row in rows if row.get("session_id")]
    previews = db.first_user_turn_previews(session_ids)
    summaries: List[ChatSessionSummary] = []
    for row in rows:
        session_id = row["session_id"]
        summaries.append(
            ChatSessionSummary(
                session_id=session_id,
                interaction_count=row.get("interaction_count") or 0,
                updated_at=row.get("updated_at"),
                preview=previews.get(session_id),
                title=row.get("title"),
                kind=row.get("kind"),
            )
        )
    return ChatSessionListResponse(sessions=summaries)


@app.get("/api/v1/chat/sessions/{session_id}", response_model=ChatSessionDetailResponse)
def get_chat_session(session_id: str, user_id: str) -> ChatSessionDetailResponse:
    """Return full message history for a session owned by the user."""
    session = _owned_session(session_id, user_id)
    turns = db.read_all_chat_turns(session_id)
    carry = get_carry(session_id)
    presentation_by_turn = carry.get("presentation_by_turn") or {}
    turn_items: List[ChatTurnItem] = []
    for t in turns:
        turn_index = t.get("turn_index") or 0
        presentation = None
        if (t.get("role") or "") == "assistant":
            # Prefer durable Supabase presentation; fall back to live carry.
            raw_pres = t.get("presentation")
            if not raw_pres:
                raw_pres = presentation_by_turn.get(str(turn_index))
                if raw_pres:
                    logging.warning(
                        "presentation-miss-db session=%s turn_index=%s; using carry",
                        session_id,
                        turn_index,
                    )
                else:
                    logging.warning(
                        "presentation-miss session=%s turn_index=%s",
                        session_id,
                        turn_index,
                    )
            if raw_pres:
                presentation = TurnPresentationPayload(**raw_pres)
        turn_items.append(
            ChatTurnItem(
                turn_id=t.get("turn_id") or str(turn_index),
                role=t.get("role") or "assistant",
                content=t.get("content") or "",
                turn_index=turn_index,
                agent_used=t.get("agent_used"),
                presentation=presentation,
            )
        )
    return ChatSessionDetailResponse(
        session_id=session_id,
        interaction_count=session.get("interaction_count") or 0,
        turns=turn_items,
    )


def _owned_session(session_id: str, user_id: str) -> Dict[str, Any]:
    try:
        session = db.read_session(session_id)
    except Exception:  # malformed id or no row: same answer as not yours
        session = None
    if not session or session.get("user_id") != user_id or db.is_session_deleted(session_id):
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


@app.patch("/api/v1/chat/sessions/{session_id}")
def rename_chat_session(session_id: str, req: UpdateSessionRequest) -> Dict[str, Any]:
    """Rename a chat. An empty title clears it, so the list shows the first question again."""
    _owned_session(session_id, req.user_id)
    title = " ".join((req.title or "").split())[:120] or None
    try:
        db.update_session_title(session_id, title)
    except Exception as exc:
        logging.exception("rename failed for session %s", session_id)
        raise HTTPException(status_code=502, detail="Could not rename this chat.") from exc
    return {"ok": True, "session_id": session_id, "title": title}


@app.delete("/api/v1/chat/sessions/{session_id}")
def delete_chat_session(session_id: str, user_id: str) -> Dict[str, Any]:
    """Delete a chat: hidden from history for good. Learner memory and practice records stay."""
    _owned_session(session_id, user_id)
    try:
        db.delete_session(session_id)
    except Exception as exc:
        logging.exception("delete failed for session %s", session_id)
        raise HTTPException(status_code=502, detail="Could not delete this chat.") from exc
    try:
        from agents.memory_agent import invalidate_session

        invalidate_session(session_id)
    except Exception:
        logging.debug("episode invalidation failed for session %s", session_id, exc_info=True)
    release_carry(session_id)
    return {"ok": True, "session_id": session_id}


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

    # A finished session is the natural moment for the Background Learning Coach
    # to look. It runs detached so ending a session stays instant — the student
    # is closing the tab and must not wait on background reasoning.
    threading.Thread(
        target=coach_user,
        args=(req.user_id,),
        kwargs={"trigger": "student ended a session", "session_id": req.session_id},
        daemon=True,
    ).start()

    return EndSessionResponse(ok=True)


@app.post("/api/v1/coach/run", response_model=CoachRunResponse)
def run_coach(req: CoachRunRequest) -> CoachRunResponse:
    """Run the Background Learning Coach over one student's records.

    The same Learning Agent, looking at what has happened between sessions and
    deciding whether anything deserves a response. `acted: false` is the normal
    result and is not an error — a student working steadily needs nothing.

    Nothing here reaches the student. The coach can remember something and can
    schedule a review; anything student-facing happens in a live turn, where
    they can answer back.

    Synchronous, for a caller that wants the decision (a cron sweep, an admin
    tool). Session-end triggers its own detached run instead.
    """
    result = coach_user(
        req.user_id, trigger=req.trigger, session_id=req.session_id
    )
    return CoachRunResponse(
        ok=bool(result.get("ok")),
        acted=bool(result.get("acted")),
        actions=result.get("actions") or [],
        note=result.get("note"),
        trace_path=result.get("trace_path") or [],
        error=result.get("error"),
    )


# ── Classroom day specialists ─────────────────────────────────────────────────
# Pure JSON. No chat, no stream, no model. Django stores the field it asked for.


@app.post("/api/v1/classroom/plan")
def classroom_plan(body: Dict[str, Any]) -> Dict[str, Any]:
    """Week plan from the catalog Django sent."""
    from services.classroom_specialists import load_learner, plan_specialist

    return plan_specialist(load_learner(body))


@app.post("/api/v1/classroom/daily")
def classroom_daily(body: Dict[str, Any]) -> Dict[str, Any]:
    """One today block for for_date. Skips concept ids the learner already decided."""
    from services.classroom_specialists import daily_specialist, load_learner

    return daily_specialist(load_learner(body))


@app.post("/api/v1/classroom/revise")
def classroom_revise(body: Dict[str, Any]) -> Dict[str, Any]:
    """Revision queue from weak trackers, else the home revise list."""
    from services.classroom_specialists import load_learner, revise_specialist

    return revise_specialist(load_learner(body))


@app.post("/api/v1/classroom/readiness")
def classroom_readiness(body: Dict[str, Any]) -> Dict[str, Any]:
    """Mean mastery of attempted trackers."""
    from services.classroom_specialists import load_learner, readiness_specialist

    return readiness_specialist(load_learner(body))


class AssessBuildRequest(BaseModel):
    concept_id: str = ""


class AssessSubmission(BaseModel):
    item_id: str = ""
    answer: str = ""


class AssessGradeRequest(BaseModel):
    concept_id: str = ""
    answers: List[AssessSubmission] = []
    user_id: str = ""
    session_id: str = ""
    classroom_token: Optional[str] = None


@app.post("/api/v1/classroom/assess/build")
def classroom_assess_build(body: AssessBuildRequest) -> Dict[str, Any]:
    """Retrieved practice items for one concept. The tutor still presents them in chat."""
    from services.classroom_rooms import build_assessment

    concept_id = (body.concept_id or "").strip()
    if not concept_id:
        raise HTTPException(status_code=400, detail="concept_id is required")
    return build_assessment(concept_id)


@app.post("/api/v1/classroom/assess/grade")
def classroom_assess_grade(
    body: AssessGradeRequest,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Grade submitted answers against the stored solution, then the proficiency maths."""
    from services.classroom_rooms import grade_submissions

    if not body.answers:
        raise HTTPException(status_code=400, detail="answers must be a non-empty list")
    token = resolve_learner_token(body.classroom_token, authorization)
    submissions = [{"item_id": row.item_id, "answer": row.answer} for row in body.answers]
    return grade_submissions(
        (body.concept_id or "").strip(),
        submissions,
        user_id=body.user_id,
        session_id=body.session_id,
        token=token,
    )


# ── Study Room (Phase 4) ──────────────────────────────────────────────────────

class StudyStartRequest(BaseModel):
    user_id: str
    # A concept id or whatever the student typed ("LC oscillation"); resolved
    # against the local concept index before a session is created.
    concept_id: str
    # Reopen the newest unfinished session on this concept instead of starting
    # over. A page reload, or React mounting the room twice, must not fork the
    # session — it used to create two empty sessions on every visit.
    force_new: bool = False
    # Open this exact session (from the history panel) instead of the newest one.
    study_session_id: Optional[str] = None
    # "practice": skip Learn / Examples and go straight to questions.
    mode: str = "learn"
    # "pyq": JEE past-year questions only.
    question_source: str = "any"


class StudyMessageRequest(BaseModel):
    user_id: str
    session_id: str
    study_session_id: str
    query: str


# In-flight study turns, by study_session_id, so Stop can reach the one running.
_ACTIVE_STUDY_TURNS: Dict[str, threading.Event] = {}
_ACTIVE_STUDY_GUARD = threading.Lock()


class StudyStopRequest(BaseModel):
    user_id: str
    study_session_id: str


_STUDY_START_LOCKS: Dict[str, threading.Lock] = {}
_STUDY_START_GUARD = threading.Lock()
_RESUME_WINDOW_DAYS = 14


def _study_start_lock(key: str) -> threading.Lock:
    with _STUDY_START_GUARD:
        return _STUDY_START_LOCKS.setdefault(key, threading.Lock())


def _starting_difficulty(user_id: str, concept_id: str) -> int:
    """Where practice should start: the level reached last time, else from proficiency."""
    try:
        for s in db.read_study_sessions_for_concept(user_id, concept_id):
            if int(s.get("questions_served") or 0) > 0:
                return max(1, int(s.get("difficulty_level") or 1) - (0 if s.get("outcome") == "mastered" else 1))
        cs = db.read_user_concept_state(user_id, concept_id) or {}
        if int(cs.get("attempts") or 0) == 0:
            return 1
        prof = float(cs.get("proficiency_score") or 0)
        return 1 if prof < 0.4 else 2 if prof < 0.65 else 3 if prof < 0.8 else 4
    except Exception:
        return 1


def _study_transcript(session_id: str) -> List[Dict[str, Any]]:
    """Student-visible turns of a study session, oldest first."""
    try:
        rows = db.read_all_chat_turns(session_id)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("agent_used") == "diagnostic":
            continue
        content = (r.get("content") or "").strip()
        if not content:
            continue
        if r.get("role") == "user" and out and out[-1]["role"] == "user":
            # The earlier message never got a reply (a failed turn). Showing a run
            # of unanswered student bubbles reads as the tutor ignoring them.
            out.pop()
        item: Dict[str, Any] = {"role": r.get("role"), "content": content}
        pres = r.get("presentation") or {}
        if isinstance(pres, dict) and pres.get("practice_question"):
            item["practice_question"] = pres["practice_question"]
        out.append(item)
    if out and out[-1]["role"] == "user":
        out.pop()  # same: the last message's turn failed; the composer is right there
    return out


def _has_tutor_reply(session_id: str) -> bool:
    try:
        return any(
            r.get("role") == "assistant" and r.get("agent_used") != "diagnostic"
            for r in db.read_all_chat_turns(session_id)
        )
    except Exception:
        return False


def _study_state_payload(session: Dict[str, Any], runtime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The study-session numbers the UI renders (phase stepper, score strip)."""
    from services.study_context import practice_question_view

    served = int(session.get("questions_served") or 0)
    correct = int(session.get("questions_correct") or 0)
    phase = session.get("current_phase") or "learn"
    return {
        "study_session_id": session.get("study_session_id"),
        "concept_id": session.get("concept_id"),
        "concept_name": session.get("concept_name") or session.get("concept_id"),
        "chapter": session.get("chapter"),
        "current_phase": phase,
        "difficulty_level": int(session.get("difficulty_level") or 1),
        "questions_served": served,
        "questions_correct": correct,
        "outcome": session.get("outcome"),
        "evaluation_summary": session.get("evaluation_summary"),
        "mode": session.get("mode") or "learn",
        "question_source": session.get("question_source") or "any",
        "lesson_plan": session.get("lesson_plan") or {},
        "phase_step": int(session.get("phase_step") or 0),
        "phase_turns": int(session.get("phase_turns") or 0),
        "started_at": session.get("started_at"),
        "practice_question": (
            practice_question_view((runtime or {}).get("current_question"))
            if phase == "practice" else None
        ),
    }


@app.post("/api/v1/study/start")
def study_start(body: StudyStartRequest) -> Dict[str, Any]:
    """Open a Study Room session: resolve the topic, resume or create, and say
    what the tutor already knows about this student for it."""
    from datetime import datetime, timezone

    from services.study_context import (
        build_learner_snapshot,
        public_snapshot,
        resolve_study_concept,
        session_runtime,
    )

    user_id = body.user_id.strip()
    raw_topic = body.concept_id.strip()
    if not user_id or not raw_topic:
        raise HTTPException(status_code=400, detail="user_id and concept_id required")
    try:
        known_user = db.read_user(user_id)
    except Exception:  # a malformed id is a client error, not a 500
        known_user = None
    if not known_user:
        raise HTTPException(status_code=404, detail="User not found")

    study_session: Optional[Dict[str, Any]] = None
    resumed = False
    if body.study_session_id:
        picked = db.read_study_session(body.study_session_id.strip())
        if not picked or picked.get("user_id") != user_id:
            raise HTTPException(status_code=404, detail="Study session not found")
        study_session, resumed = picked, True
        raw_topic = picked.get("concept_id") or raw_topic

    resolved = resolve_study_concept(raw_topic)
    concept_id = resolved["concept_id"]

    with _study_start_lock(f"{user_id}:{concept_id}"):
        mode = "practice" if body.mode == "practice" else "learn"
        source = "pyq" if body.question_source == "pyq" else "any"
        if study_session is None and not body.force_new:
            for s in db.read_study_sessions_for_concept(user_id, concept_id):
                if s.get("current_phase") in ("completed", "evaluation") or s.get("outcome"):
                    continue
                # A practice request only resumes a practice session with the same
                # question source; it never drops the student into a lesson.
                if mode == "practice" and (
                    (s.get("mode") or "learn") != "practice" or (s.get("question_source") or "any") != source
                ):
                    break
                started = s.get("started_at")
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(started).replace("Z", "+00:00"))).days
                except (TypeError, ValueError):
                    age = 0
                # A session with no tutor reply has nothing to resume — it is a
                # failed start, or one from before replies were stored — and
                # reopening it shows the student a column of their own messages.
                if (
                    age <= _RESUME_WINDOW_DAYS
                    and s.get("agent_session_id")
                    and _has_tutor_reply(s["agent_session_id"])
                ):
                    study_session, resumed = s, True
                break  # only the newest unfinished one is a resume candidate

        if study_session is None:
            session_id = _turn_resolve_session(user_id, None)
            study_session = db.create_study_session(
                user_id=user_id,
                concept_id=concept_id,
                concept_name=resolved.get("concept_name"),
                chapter=resolved.get("chapter"),
                agent_session_id=session_id,
                mode=mode,
                question_source=source,
                difficulty_level=_starting_difficulty(user_id, concept_id) if mode == "practice" else 1,
            )
            if not study_session:
                raise HTTPException(status_code=500, detail="Failed to create study session")
            if mode == "practice":
                db.insert_study_event(
                    study_session["study_session_id"], user_id, "phase_start",
                    concept_id=concept_id, payload={"phase": "practice", "mode": "practice", "source": source},
                )

    # Sessions created before topics were resolved stored the raw id as the
    # name and no chapter; repair them so the teacher and the header say
    # "LC circuit · Inductance" rather than "lc_circuit".
    if resumed and (
        not study_session.get("chapter")
        or study_session.get("concept_name") in (None, "", concept_id)
    ):
        fix = {"concept_name": resolved.get("concept_name"), "chapter": resolved.get("chapter")}
        fix = {k: v for k, v in fix.items() if v}
        if fix:
            try:
                db.client.table("study_sessions").update(fix).eq(
                    "study_session_id", study_session["study_session_id"]
                ).execute()
                study_session = {**study_session, **fix}
            except Exception:
                logging.debug("study session name repair failed", exc_info=True)

    session_id = study_session["agent_session_id"]
    snapshot = build_learner_snapshot(
        user_id,
        concept_id,
        exclude_study_session_id=study_session["study_session_id"],
        concept_name=resolved.get("concept_name"),
    )
    runtime = session_runtime(study_session["study_session_id"]) if resumed else None
    return {
        **_study_state_payload(study_session, runtime),
        "session_id": session_id,
        "resumed": resumed,
        "resolved_from": raw_topic,
        "in_syllabus": resolved.get("in_syllabus", True),
        "definition": resolved.get("definition"),
        "alternatives": resolved.get("alternatives") or [],
        "learner": public_snapshot(snapshot),
        "readiness_score": snapshot.get("readiness"),
        "transcript": _study_transcript(session_id) if resumed else [],
    }


@app.post("/api/v1/study/message")
async def study_message(body: StudyMessageRequest) -> StreamingResponse:
    """The main chat endpoint for the Study Room. Streams like /chat/message."""
    from graph.study_workflow import study_graph
    from models.study_state import StudyRoomState
    from services.study_context import practice_question_view, session_runtime

    user_id = body.user_id.strip()
    session_id = body.session_id.strip()
    study_session_id = body.study_session_id.strip()
    query = body.query.strip()

    if not user_id or not session_id or not study_session_id or not query:
        raise HTTPException(status_code=400, detail="Missing required fields")

    session = db.read_study_session(study_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found")

    if session.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not your study session")

    carry = get_carry(session_id)

    # Load initial state
    base_state = _build_initial_state(user_id, session_id, query, carry)

    state = StudyRoomState(**base_state)
    state["study_session_id"] = study_session_id
    state["concept_id"] = session["concept_id"]
    state["concept_name"] = session.get("concept_name") or session["concept_id"]
    state["chapter"] = session.get("chapter") or ""
    state["current_phase"] = session.get("current_phase") or "learn"
    state["difficulty_level"] = int(session.get("difficulty_level") or 1)
    state["questions_served"] = int(session.get("questions_served") or 0)
    state["questions_correct"] = int(session.get("questions_correct") or 0)
    state["lesson_plan"] = session.get("lesson_plan") or {}
    state["study_mode"] = session.get("mode") or "learn"
    state["question_source"] = session.get("question_source") or "any"
    state["phase_step"] = int(session.get("phase_step") or 0)
    state["phase_turns"] = int(session.get("phase_turns") or 0) + 1
    db.update_study_session_fields(study_session_id, {"phase_turns": state["phase_turns"]})

    # The prompt tells the teacher to move on once a phase's budget is used; if it
    # still has not two messages later, move it here rather than trust it again.
    from agents.teacher_agent import phase_turn_budget

    if state["current_phase"] in ("learn", "examples") and state["phase_turns"] > phase_turn_budget(state) + 2:
        _advance_study_phase(user_id, state)

    # Open question and streaks come from the session's event log — the carry
    # file is per-process and pruned, so it cannot be trusted across a reload.
    runtime = session_runtime(study_session_id)
    state.update(runtime)  # type: ignore[typeddict-item]

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _emit(kind: str, data: Any) -> None:
        asyncio.run_coroutine_threadsafe(queue.put({"type": kind, "data": data}), loop)

    cancel = threading.Event()
    with _ACTIVE_STUDY_GUARD:
        previous = _ACTIVE_STUDY_TURNS.get(study_session_id)
        if previous:
            previous.set()  # a new message supersedes one still running
        _ACTIVE_STUDY_TURNS[study_session_id] = cancel

    def _run_graph() -> None:
        from services import progress

        from utils.stream import stream_callback_var

        token = progress.install(lambda event: _emit("status", event))
        cancel_token = progress.install_cancel(cancel)
        stream_callback_var.set(lambda text: _emit("token", text))
        try:
            final = study_graph.invoke(state, {"recursion_limit": 25})
        except Exception as exc:
            logging.exception("Study graph failed")
            _emit("result", json.dumps({"error": str(exc)}))
            return
        finally:
            progress.reset(token)
            progress.reset_cancel(cancel_token)
            with _ACTIVE_STUDY_GUARD:
                if _ACTIVE_STUDY_TURNS.get(study_session_id) is cancel:
                    _ACTIVE_STUDY_TURNS.pop(study_session_id, None)
        if cancel.is_set() or final.get("cancelled"):
            _discard_stopped_turn(study_session_id, session_id, final, state)
            _emit("result", json.dumps({"cancelled": True, "study": _study_state_payload(db.read_study_session(study_session_id) or session)}, default=str))
            return
        if final.get("error"):
            _emit("result", json.dumps({"error": final["error"], "student_message": _student_error(final["error"])}))
            return

        fresh = db.read_study_session(study_session_id) or session
        q = final.get("current_question") if final.get("awaiting_answer") else None
        q_view = practice_question_view(q)
        response = final.get("response") or ""

        if int(final.get("interaction_count") or 0) == 1:
            db.insert_learning_event(
                user_id, "study_started", source="study",
                concept_id=state["concept_id"], session_id=session_id,
                study_session_id=study_session_id,
            )
        _ledger_tutor_turn({**final, "user_id": user_id, "query": query}, session_id, source="study")

        # Store the tutor's reply. Without this the next turn's history held
        # only the student's side, and the teacher re-taught from scratch.
        interaction = int(final.get("interaction_count") or 0)
        if interaction and response:
            try:
                db.insert_chat_turn(
                    session_id=session_id,
                    role="assistant",
                    content=response,
                    turn_index=interaction * 2,
                    agent_used="tutor",
                    concepts_referenced=[state["concept_id"]],
                    presentation={"practice_question": q_view} if q_view else None,
                )
            except Exception:
                logging.exception("Failed to store study reply (session=%s)", session_id)

        save_carry(session_id, {
            "practice_streak": final.get("practice_streak", 0),
            "wrong_streak": final.get("wrong_streak", 0),
            "served_content_ids": final.get("served_content_ids", []),
        })

        study = _study_state_payload(fresh)
        study["practice_question"] = q_view
        data = {
            "session_id": session_id,
            "response": response,
            "agent_used": final.get("agent_used", "teacher_agent"),
            "active_agent": final.get("active_agent"),
            "current_phase": study["current_phase"],
            "practice_question": q_view,
            "evaluation_summary": final.get("evaluation_summary"),
            "readiness_score": final.get("readiness_score"),
            "readiness_delta": final.get("readiness_delta"),
            "tools_used": final.get("tools_used") or [],
            "study": study,
        }
        _emit("result", json.dumps(data, default=str))

    threading.Thread(target=_run_graph, daemon=True).start()
    return StreamingResponse(_sse_frames(queue), media_type="text/event-stream", headers=SSE_HEADERS)


def _student_error(error: str) -> Optional[str]:
    """Plain words for the failures a student can act on (or at least understand)."""
    from utils.llm import _retry_seconds

    text = str(error or "")
    if "quota" in text.lower() or "RESOURCE_EXHAUSTED" in text:
        secs = _retry_seconds(Exception(text)) or 0
        if secs > 600:
            hours = max(1, round(secs / 3600))
            return (f"Your tutor has used up today's AI allowance. It resets in about {hours} hour"
                    f"{'s' if hours != 1 else ''} — your progress is saved; come back then.")
        return "Your tutor is getting a lot of requests right now. Wait a minute and send that again."
    return None


def _discard_stopped_turn(study_session_id: str, session_id: str, final: Dict[str, Any], state: Dict[str, Any]) -> None:
    """A stopped turn leaves no trace in the conversation: the student sent it by
    mistake. The message bootstrap stored is removed and the phase's turn count
    put back. (An answer already graded before Stop stays graded — that grade
    happened and the record should say so.)"""
    interaction = int(final.get("interaction_count") or state.get("interaction_count") or 0)
    if interaction:
        # chat_turns is append-only for the service role (no DELETE grant, on
        # purpose), so the message is blanked and marked instead. Transcript and
        # tutor history both skip empty turns.
        try:
            db.client.table("chat_turns").update({"content": "", "presentation": {"stopped": True}}).eq(
                "session_id", session_id
            ).eq("turn_index", interaction * 2 - 1).eq("role", "user").execute()
        except Exception:
            logging.debug("could not blank stopped turn", exc_info=True)
        # The assistant episode, if commit already wrote one, is the even index.
        try:
            from agents.memory_agent import invalidate_turn

            invalidate_turn(session_id, interaction * 2)
        except Exception:
            logging.debug("episode turn invalidation failed", exc_info=True)
    turns = max(0, int(state.get("phase_turns") or 1) - 1)
    db.update_study_session_fields(study_session_id, {"phase_turns": turns})


@app.post("/api/v1/study/stop")
def study_stop(body: StudyStopRequest) -> Dict[str, Any]:
    """Stop the turn running for this study session. It ends at its next step
    boundary (usually within a few seconds) and sends nothing."""
    session = db.read_study_session(body.study_session_id.strip())
    if not session or session.get("user_id") != body.user_id.strip():
        raise HTTPException(status_code=404, detail="Study session not found")
    with _ACTIVE_STUDY_GUARD:
        event = _ACTIVE_STUDY_TURNS.get(session["study_session_id"])
    if event:
        event.set()
    return {"stopped": bool(event)}


@app.get("/api/v1/study/session/{study_session_id}")
def study_session_get(study_session_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """A study session with its transcript, for reopening it from history."""
    from services.study_context import session_runtime

    session = db.read_study_session(study_session_id)
    if not session or (user_id and session.get("user_id") != user_id):
        raise HTTPException(status_code=404, detail="Session not found")
    runtime = session_runtime(study_session_id)
    return {
        **session,
        **_study_state_payload(session, runtime),
        "session_id": session.get("agent_session_id"),
        "transcript": _study_transcript(session.get("agent_session_id") or ""),
    }


class StudyAnswerRequest(BaseModel):
    user_id: str
    study_session_id: str
    # Option labels as shown — ["2"] or ["A", "C"] for multiple correct …
    choice: List[str] = []
    # … or a numerical answer as typed ("2.35", "5 x 10^3"), graded against the official range.
    value: Optional[str] = None


@app.post("/api/v1/study/answer")
def study_answer(body: StudyAnswerRequest) -> Dict[str, Any]:
    """Grade a multiple-choice answer from the stored key — instant, exact, no
    model call. Records the attempt exactly as the tutor's grading would
    (proficiency, streaks, difficulty, ledger). Returns {"graded": false} when the
    question has no readable key; the browser then asks the tutor instead."""
    from agents.runtime.toolbelt import ToolContext
    from agents.runtime.trace import AgentTrace
    from mcp_tools.study_tools import GradePracticeArgs, grade_practice_answer
    from services.answer_key import grade_choice
    from services.study_context import session_runtime

    user_id = body.user_id.strip()
    session = db.read_study_session(body.study_session_id.strip())
    if not session or session.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Study session not found")
    ssid = session["study_session_id"]
    runtime = session_runtime(ssid)
    question = runtime.get("current_question")
    if not question:
        raise HTTPException(status_code=409, detail="No question is open")
    # The structured official answer first (letters or a numeric range); the old text key only
    # for choice questions ingested before structure existed.
    from services.answer_key import grade_structured

    result = grade_structured(question, body.choice, body.value)
    if result is None and body.choice:
        result = grade_choice(question.get("solution") or "", body.choice)
    if result is None:
        return {"graded": False}

    labels = (", ".join(f"({c.strip().strip('()')})" for c in body.choice) if body.choice
              else (body.value or "").strip())
    agent_session = session.get("agent_session_id") or ""
    state: Dict[str, Any] = {
        "user_id": user_id,
        "session_id": agent_session,
        "study_session_id": ssid,
        "concept_id": session["concept_id"],
        "concept_name": session.get("concept_name") or session["concept_id"],
        "current_phase": session.get("current_phase") or "practice",
        "difficulty_level": int(session.get("difficulty_level") or 1),
        "questions_served": int(session.get("questions_served") or 0),
        "questions_correct": int(session.get("questions_correct") or 0),
        "deterministic_grade": True,
        **runtime,
    }
    ctx = ToolContext(user_id=user_id, session_id=agent_session, state=state,
                      trace=AgentTrace(user_id=user_id, session_id=agent_session))
    grade_practice_answer(GradePracticeArgs(is_correct=result["correct"], student_answer=labels), ctx)

    # Put the exchange in the conversation, so "explain it" on the next turn
    # finds it in the tutor's history as well as in its open-question context.
    try:
        sess = db.read_session(agent_session) or {}
        n = int(sess.get("interaction_count") or 0) + 1
        db.update_session_interaction_count(agent_session, n)
        db.insert_chat_turn(session_id=agent_session, role="user", content=f"My answer: {labels}",
                            turn_index=n * 2 - 1, agent_used="student")
        key_text = result.get("answer_text") or ", ".join(f"({k})" for k in result["key"])
        db.insert_chat_turn(
            session_id=agent_session, role="assistant", agent_used="tutor", turn_index=n * 2,
            content=(f"✓ Correct — the answer is {key_text}." if result["correct"]
                     else f"✗ Not quite — you chose {labels}; the correct answer is {key_text}."),
        )
    except Exception:
        logging.debug("could not record the graded answer in the transcript", exc_info=True)

    fresh = db.read_study_session(ssid) or session
    # The stored solution goes back with the grade, so the pane can show it at once instead of
    # waiting ~20 s for the tutor to write one. Every stored solution is verified (official
    # answer, or a worked solution checked against it, or the book's own).
    return {"graded": True, **result, "solution": question.get("solution") or "",
            "study": _study_state_payload(fresh)}


class StudyNextRequest(BaseModel):
    user_id: str
    study_session_id: str


@app.post("/api/v1/study/next")
def study_next(body: StudyNextRequest) -> Dict[str, Any]:
    """Serve the next practice question directly — retrieval only, no model turn.

    "Next question" used to be a chat message the tutor answered by calling
    serve_practice_question (~20 s). This runs the same tool with the same session state, so
    difficulty, the no-repeat ledger and the event log behave exactly as before.
    """
    from agents.runtime.toolbelt import ToolContext
    from agents.runtime.trace import AgentTrace
    from mcp_tools.study_tools import ServePracticeArgs, serve_practice_question
    from services.study_context import session_runtime

    user_id = body.user_id.strip()
    session = db.read_study_session(body.study_session_id.strip())
    if not session or session.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Study session not found")
    ssid = session["study_session_id"]
    runtime = session_runtime(ssid)
    if runtime.get("current_question") and runtime.get("awaiting_answer"):
        fresh = db.read_study_session(ssid) or session
        return {"served": False, "reason": "a question is already open", "study": _study_state_payload(fresh, runtime)}
    agent_session = session.get("agent_session_id") or ""
    state: Dict[str, Any] = {
        "user_id": user_id, "session_id": agent_session, "study_session_id": ssid,
        "concept_id": session["concept_id"], "concept_name": session.get("concept_name") or session["concept_id"],
        "current_phase": session.get("current_phase") or "practice",
        "difficulty_level": int(session.get("difficulty_level") or 1),
        "question_source": session.get("question_source") or "any",
        "target_exam": session.get("target_exam") or "JEE_ADVANCED",
        **runtime, "current_question": None, "awaiting_answer": False,
    }
    ctx = ToolContext(user_id=user_id, session_id=agent_session, state=state,
                      trace=AgentTrace(user_id=user_id, session_id=agent_session))
    result = serve_practice_question(ServePracticeArgs(), ctx)
    fresh = db.read_study_session(ssid) or session
    return {"served": "error" not in result, "revisit": bool(result.get("revisit")),
            "reason": result.get("error"), "study": _study_state_payload(fresh, session_runtime(ssid))}


class StudyAdvanceRequest(BaseModel):
    user_id: str
    study_session_id: str


_NEXT_PHASE = {"learn": "examples", "examples": "practice", "practice": "evaluation"}


def _advance_study_phase(user_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """Move a study session one phase forward without asking the model.

    learn → examples → practice is a plain phase change. practice → evaluation
    closes the session (outcome, memory, review) and runs the evaluation, the
    same path complete_study_session takes from inside a turn.
    """
    from agents.runtime.toolbelt import ToolContext
    from agents.runtime.trace import AgentTrace
    from mcp_tools.study_tools import CompleteSessionArgs, complete_study_session, move_phase

    current = state.get("current_phase") or "learn"
    target = _NEXT_PHASE.get(current)
    if not target:
        return {}
    ctx = ToolContext(
        user_id=user_id,
        session_id=state.get("session_id") or "",
        state=state,
        trace=AgentTrace(user_id=user_id, session_id=state.get("session_id") or ""),
    )
    if target != "evaluation":
        move_phase(ctx, target, "student chose to move on")
        return {}
    complete_study_session(CompleteSessionArgs(summary="student ended practice"), ctx)
    from agents.evaluation_agent import evaluation_agent_node

    return evaluation_agent_node({**state, **ctx.updates}) or {}


@app.post("/api/v1/study/advance")
def study_advance(body: StudyAdvanceRequest) -> Dict[str, Any]:
    """The student's own "skip ahead" / "finish" — deterministic, no model call
    except the results summary when practice ends."""
    session = db.read_study_session(body.study_session_id.strip())
    if not session or session.get("user_id") != body.user_id.strip():
        raise HTTPException(status_code=404, detail="Study session not found")
    phase = session.get("current_phase") or "learn"
    if phase not in _NEXT_PHASE:
        raise HTTPException(status_code=409, detail="This session is already finished")
    state: Dict[str, Any] = {
        "user_id": session["user_id"],
        "session_id": session.get("agent_session_id") or "",
        "study_session_id": session["study_session_id"],
        "concept_id": session["concept_id"],
        "concept_name": session.get("concept_name") or session["concept_id"],
        "current_phase": phase,
        "difficulty_level": int(session.get("difficulty_level") or 1),
        "questions_served": int(session.get("questions_served") or 0),
        "questions_correct": int(session.get("questions_correct") or 0),
        "lesson_plan": session.get("lesson_plan") or {},
    }
    evaluation = _advance_study_phase(body.user_id.strip(), state)
    fresh = db.read_study_session(session["study_session_id"]) or session
    return {
        "study": _study_state_payload(fresh),
        "evaluation_summary": evaluation.get("evaluation_summary"),
        "readiness_score": evaluation.get("readiness_score"),
        "readiness_delta": evaluation.get("readiness_delta"),
    }


@app.get("/api/v1/study/overview/{user_id}")
def study_overview(user_id: str, tz: Optional[str] = None) -> Dict[str, Any]:
    """The /study landing page: up next, mastered / in progress / needs review,
    and what is left to cover in syllabus order."""
    from services.study_overview import build_overview

    try:
        known = db.read_user(user_id)
    except Exception:
        known = None
    if not known:
        raise HTTPException(status_code=404, detail="User not found")
    return build_overview(user_id, tz_name=tz)


class PlanSettingsRequest(BaseModel):
    user_id: str
    study_days: Optional[int] = None
    revision_weeks: Optional[int] = None
    exam_date: Optional[str] = None  # ISO date, or "" to go back to the estimate


def _plan_settings(user_id: str) -> Dict[str, Any]:
    try:
        rows = db.client.table("study_plan_settings").select("*").eq("user_id", user_id).limit(1).execute().data
        return rows[0] if rows else {}
    except Exception:
        return {}


@app.get("/api/v1/plan/{user_id}")
def study_plan(
    user_id: str,
    exam: str = "BOTH",
    target_year: Optional[int] = None,
    daily_minutes: int = 60,
    tz: Optional[str] = None,
) -> Dict[str, Any]:
    """The exam plan. Exam, year and daily minutes come from the student's
    profile (passed by the browser); study days, revision weeks and an exact
    exam date come from study_plan_settings."""
    from datetime import date as _date

    from services.study_plan import build_plan

    try:
        known = db.read_user(user_id)
    except Exception:
        known = None
    if not known:
        raise HTTPException(status_code=404, detail="User not found")
    saved = _plan_settings(user_id)
    exam_date = None
    if saved.get("exam_date"):
        try:
            exam_date = _date.fromisoformat(str(saved["exam_date"])[:10])
        except ValueError:
            exam_date = None
    return build_plan(
        user_id,
        exam=(exam or "BOTH").upper(),
        exam_date=exam_date,
        target_year=target_year,
        daily_minutes=daily_minutes,
        study_days=int(saved.get("study_days") or 6),
        revision_weeks=int(saved["revision_weeks"]) if saved.get("revision_weeks") is not None else 6,
        tz_name=tz,
    )


@app.put("/api/v1/plan/settings")
def save_plan_settings(body: PlanSettingsRequest) -> Dict[str, Any]:
    from datetime import date as _date

    user_id = body.user_id.strip()
    try:
        known = db.read_user(user_id)
    except Exception:
        known = None
    if not known:
        raise HTTPException(status_code=404, detail="User not found")
    row: Dict[str, Any] = {"user_id": user_id, **{k: v for k, v in _plan_settings(user_id).items() if k in ("study_days", "revision_weeks", "exam_date")}}
    if body.study_days is not None:
        row["study_days"] = max(1, min(7, int(body.study_days)))
    if body.revision_weeks is not None:
        row["revision_weeks"] = max(0, min(26, int(body.revision_weeks)))
    if body.exam_date is not None:
        if body.exam_date.strip():
            try:
                row["exam_date"] = _date.fromisoformat(body.exam_date.strip()).isoformat()
            except ValueError:
                raise HTTPException(status_code=400, detail="exam_date must be YYYY-MM-DD")
        else:
            row["exam_date"] = None
    from datetime import datetime as _dt, timezone as _tz

    row["updated_at"] = _dt.now(_tz.utc).isoformat()
    db.client.table("study_plan_settings").upsert(row).execute()
    return {k: row.get(k) for k in ("study_days", "revision_weeks", "exam_date")}


@app.get("/api/v1/progress/{user_id}")
def progress_report(user_id: str, tz: Optional[str] = None, days: int = 30) -> Dict[str, Any]:
    """Everything the /progress page shows, derived from the learning ledger."""
    from services.progress_report import build_report

    try:
        known = db.read_user(user_id)
    except Exception:
        known = None
    if not known:
        raise HTTPException(status_code=404, detail="User not found")
    return build_report(user_id, tz_name=tz, days=max(7, min(int(days), 90)))


@app.get("/api/v1/diagrams/{diagram_id}.svg")
def diagram_svg(diagram_id: str) -> FileResponse:
    """A compiled tutor diagram. Content-addressed, so it never changes."""
    from services.diagrams import diagram_path

    path = diagram_path(diagram_id)
    if not path:
        raise HTTPException(status_code=404, detail="Diagram not found")
    return FileResponse(
        path,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/v1/study/history/{user_id}")
def study_history(user_id: str) -> List[Dict[str, Any]]:
    """Study sessions newest first, each with how many messages it holds, so the
    history panel can leave out sessions that were opened and never used."""
    rows = db.list_user_study_sessions(user_id, limit=40)
    try:
        full = (
            db.client.table("study_sessions")
            .select("study_session_id, agent_session_id")
            .eq("user_id", user_id)
            .in_("study_session_id", [r["study_session_id"] for r in rows])
            .execute()
            .data
            or []
        )
        agent_ids = {r["study_session_id"]: r.get("agent_session_id") for r in full}
        turns = (
            db.client.table("chat_turns")
            .select("session_id")
            .in_("session_id", [a for a in agent_ids.values() if a])
            .neq("agent_used", "diagnostic")
            .execute()
            .data
            or []
        )
        counts: Dict[str, int] = {}
        for t in turns:
            counts[t["session_id"]] = counts.get(t["session_id"], 0) + 1
        for r in rows:
            r["message_count"] = counts.get(agent_ids.get(r["study_session_id"]) or "", 0)
    except Exception:
        logging.debug("study history message counts failed", exc_info=True)
    return rows


# ── Voice API (Sarvam AI: Saaras STT + Bulbul TTS) ───────────────────────────

from services.voice_service import (
    DEFAULT_SPEAKER,
    DEFAULT_TTS_MODEL,
    DEFAULT_STT_MODEL,
    TUTOR_SPEAKERS,
    synthesize_speech as _synthesize_speech,
    transcribe_speech as _transcribe_speech,
)


class VoiceSynthesisRequest(BaseModel):
    text: str
    speaker: Optional[str] = DEFAULT_SPEAKER
    language_code: Optional[str] = "en-IN"
    model: Optional[str] = DEFAULT_TTS_MODEL


class VoiceSynthesisResponse(BaseModel):
    audio_base64: str
    format: str = "wav"
    speaker: str
    cleaned_text: str


class VoiceTranscribeResponse(BaseModel):
    transcript: str
    language_code: str


@app.get("/api/v1/voice/speakers")
def get_voice_speakers() -> Dict[str, Any]:
    """List available Indian voice personas for Bulbul TTS tutor."""
    return {
        "speakers": TUTOR_SPEAKERS,
        "default_speaker": DEFAULT_SPEAKER,
    }


@app.post("/api/v1/voice/transcribe", response_model=VoiceTranscribeResponse)
async def voice_transcribe(
    file: UploadFile = File(...),
    language_code: Optional[str] = Form(None),
) -> VoiceTranscribeResponse:
    """Transcribe spoken JEE doubt audio using Sarvam Saaras STT."""
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty audio file received.")
        filename = file.filename or "doubt_audio.webm"
        content_type = file.content_type or "audio/webm"
        result = _transcribe_speech(
            file_bytes=content,
            filename=filename,
            content_type=content_type,
            language_code=language_code,
        )
        return VoiceTranscribeResponse(**result)
    except HTTPException:
        raise
    except Exception as exc:
        logging.getLogger("aeropt.voice").error("Voice transcription failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Voice transcription failed: {str(exc)}")


@app.post("/api/v1/voice/synthesize", response_model=VoiceSynthesisResponse)
def voice_synthesize(req: VoiceSynthesisRequest) -> VoiceSynthesisResponse:
    """Read aloud tutor explanation or doubt answer using Sarvam Bulbul TTS."""
    try:
        if not req.text or not req.text.strip():
            return VoiceSynthesisResponse(
                audio_base64="",
                format="wav",
                speaker=req.speaker or DEFAULT_SPEAKER,
                cleaned_text="",
            )
        result = _synthesize_speech(
            text=req.text,
            speaker=req.speaker or DEFAULT_SPEAKER,
            language_code=req.language_code or "en-IN",
            model=req.model or DEFAULT_TTS_MODEL,
        )
        return VoiceSynthesisResponse(**result)
    except HTTPException:
        raise
    except Exception as exc:
        logging.getLogger("aeropt.voice").error("Voice synthesis failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Voice synthesis failed: {str(exc)}")

