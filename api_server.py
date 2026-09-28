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

# ── config must be imported first — calls load_dotenv() and sets SSL cert ─────
import config  # noqa: F401

# ── FastAPI ────────────────────────────────────────────────────────────────────
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Agent pipeline — same import targets as main.py ───────────────────────────
from db.supabase_client import db
from agents.learning_coach import coach_user
from graph.workflow import tutor_graph
from models.state import TutorState

# ── Session-management helpers — imported from services.turn_service ───────────
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
    """Load the local retrieval index before the first student turn, not during it."""
    if config.local_retrieval():
        try:
            from retrieval import service as retrieval_service

            result = retrieval_service.warm()
            logging.getLogger("aeropt.startup").warning(
                "local retrieval index: %s",
                retrieval_service.status() if result.get("warmed") else result,
            )
        except Exception as exc:  # noqa: BLE001 — a cold index must not stop the server
            logging.getLogger("aeropt.startup").warning("local retrieval warm-up failed: %s", exc)
    yield


app = FastAPI(title="JEE Tutor Agent API", version="1.0.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
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


class ChatSessionListResponse(BaseModel):
    sessions: List[ChatSessionSummary]


class CreateSessionRequest(BaseModel):
    user_id: str


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


def _store_turn_presentation(
    session_id: str,
    carry: Dict[str, Any],
    result: TutorState,
    response: ChatMessageResponse,
) -> None:
    interaction = result.get("interaction_count")
    if not interaction:
        return
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


def _prefetch_next_query(session_id: str, result: TutorState) -> None:
    """Warm GraphRAG for the first follow-up suggestion when Redis is available."""
    follow_ups = result.get("follow_up_suggestions")
    if not follow_ups or not isinstance(follow_ups, list) or not db.redis:
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
        from utils.stream import stream_callback_var
        stream_callback_var.set(sync_cb)
        try:
            stage_overrides: Dict[str, Any] = {}
            
            # Check prefetch cache for next_query fast-forward
            from db.supabase_client import db
            if db.redis:
                try:
                    cache_key = f"prefetch:{session_id}:{query.strip()}"
                    cached_str = db.redis.get(cache_key)
                    if cached_str:
                        cached_data = json.loads(cached_str)
                        stage_overrides.update(cached_data)
                        stage_overrides["_prefetch_fast_forward"] = True
                        logging.info("Used prefetch cache for query: %s", query.strip())
                except Exception:
                    pass
                    
            if req.study_stage in {"theory", "example", "practice", "pyq"}:
                stage_overrides.update({
                    "study_stage": req.study_stage,
                    "content_type_requested": req.study_stage,
                })
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
            # Off until Django exposes POST /classroom/evidence/. A missing
            # route must not be called on every turn.
            if _classroom_evidence_enabled():
                post_turn_evidence(
                    result=result,
                    was_graded=was_graded,
                    study_stage=req.study_stage,
                    token=learner_token,
                )

            def prefetch_bg():
                from utils.stream import stream_callback_var
                stream_callback_var.set(None)
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

    return StreamingResponse(_sse_frames(queue), media_type="text/event-stream")


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
        session = db.create_session(req.user_id)
    except Exception as exc:
        logging.exception("create_session failed for user %s", req.user_id)
        raise HTTPException(status_code=502, detail=f"Could not create session: {exc}") from exc
    session_id = session.get("session_id")
    if not session_id:
        raise HTTPException(status_code=502, detail="Session created without an id.")
    logging.warning("session-create reason=explicit_api user=%s session=%s", req.user_id, session_id)
    return CreateSessionResponse(session_id=session_id)


@app.get("/api/v1/chat/sessions", response_model=ChatSessionListResponse)
def list_chat_sessions(user_id: str, limit: int = 20) -> ChatSessionListResponse:
    """List recent study sessions for a user (newest first)."""
    capped = max(1, min(limit, 50))
    try:
        rows = db.list_user_sessions(user_id, limit=capped)
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


def _classroom_evidence_enabled() -> bool:
    """True only when CLASSROOM_EVIDENCE is explicitly on.

    The helper itself fails open, but the chat path stays quiet until the
    Django evidence route exists.
    """
    return os.environ.get("CLASSROOM_EVIDENCE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
