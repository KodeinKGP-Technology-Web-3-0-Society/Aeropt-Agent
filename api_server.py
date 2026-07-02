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
1. Restart gap: uvicorn restart wipes in-memory carry state.  The pipeline
   won't crash — the next request simply starts as a fresh turn.  Accepted
   for local dev.

2. Memory eviction: each session carry entry tracks `last_seen`.  A prune
   pass runs on every inbound request and removes entries idle > 2 hours.
   No background thread required.

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
import time
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

logging.basicConfig(
    level=logging.WARNING,
    format="[%(levelname)s %(name)s] %(message)s",
)


# ── In-memory carry-state store ───────────────────────────────────────────────
#
# Keyed by session_id.  Each entry: {"carry": dict, "last_seen": float}
#
# Why in-memory?  The constraint forbids new Supabase tables or new env vars,
# and LangGraph starts fresh on every .invoke() call.  A per-process dict is
# the only option that requires no schema changes.  It is safe for a
# single-worker local server.
#
# Eviction: _evict_idle_sessions() is called on every inbound request and
# removes entries whose last_seen is older than _SESSION_TTL_SECONDS.  This
# prevents unbounded growth on a long-running server without needing a
# background thread.

_SESSION_TTL_SECONDS: int = 2 * 60 * 60  # evict sessions idle for > 2 hours
_carry_store: Dict[str, Dict[str, Any]] = {}


def _evict_idle_sessions() -> None:
    """Prune sessions idle beyond the TTL.  O(n) but n is tiny for local dev."""
    cutoff = time.monotonic() - _SESSION_TTL_SECONDS
    stale = [sid for sid, e in _carry_store.items() if e["last_seen"] < cutoff]
    for sid in stale:
        _carry_store.pop(sid, None)


def _get_carry(session_id: str) -> Dict[str, Any]:
    """Return (and touch) the carry dict for session_id, creating it if absent."""
    _evict_idle_sessions()
    entry = _carry_store.get(session_id)
    if entry is None:
        entry = {"carry": {}, "last_seen": time.monotonic()}
        _carry_store[session_id] = entry
    else:
        entry["last_seen"] = time.monotonic()
    return entry["carry"]


def _release_carry(session_id: str) -> None:
    """Remove carry entry — called after a clean end-session."""
    _carry_store.pop(session_id, None)


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


class ChatMessageResponse(BaseModel):
    session_id: str
    response: Optional[str] = None
    agent_used: Optional[str] = None
    follow_up_suggestions: Optional[List[Dict[str, Any]]] = None
    quiz_steps: Optional[List[Dict[str, Any]]] = None
    awaiting_graded_response: bool = False
    error: Optional[str] = None


class EndSessionRequest(BaseModel):
    user_id: str
    session_id: str


class EndSessionResponse(BaseModel):
    ok: bool


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


@app.post("/api/v1/chat/message", response_model=ChatMessageResponse)
def chat_message(req: ChatMessageRequest) -> ChatMessageResponse:
    """
    Process one student message through the LangGraph pipeline.

    The implementation mirrors main.py's chat loop exactly:
      1. Create or resume a Supabase session.
      2. Load carry state from the in-memory store.
      3. Detect "1"/"0" graded responses and ANSWER:: legacy format.
      4. Build the initial TutorState via _build_initial_state.
      5. Invoke the compiled tutor_graph synchronously.
      6. Update carry state via _update_carry.
      7. Return the agent response fields as JSON.
    """
    user_id = req.user_id
    raw_input = req.query.strip()

    # ── Session ────────────────────────────────────────────────────────────────
    if req.session_id:
        session_id = req.session_id
    else:
        session = db.create_session(user_id)
        session_id = session["session_id"]

    # ── Carry state ────────────────────────────────────────────────────────────
    carry = _get_carry(session_id)

    # ── Parse input — mirrors main.py chat loop verbatim ──────────────────────
    graded_overrides: Optional[Dict[str, Any]] = None
    query = raw_input
    was_graded = False

    # Auto-detect "1" / "0" after a practice/PYQ question.
    # Guard on awaiting_graded_response so that selecting a follow-up
    # suggestion by number ("1", "2", "3") is never misread as a grade.
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

    # Legacy ANSWER:: format (quiz steps)
    elif raw_input.upper().startswith("ANSWER::"):
        graded_overrides = _parse_graded_input(raw_input)
        query = graded_overrides.get("student_answer", raw_input)
        was_graded = True

    # ── Build state and invoke ─────────────────────────────────────────────────
    initial_state = _build_initial_state(
        user_id=user_id,
        session_id=session_id,
        query=query,
        carry=carry,
        graded_overrides=graded_overrides,
    )

    try:
        result: TutorState = tutor_graph.invoke(initial_state)
    except Exception as exc:
        logging.exception("tutor_graph.invoke failed for session %s", session_id)
        return ChatMessageResponse(session_id=session_id, error=str(exc))

    # ── Update carry for next turn ─────────────────────────────────────────────
    _update_carry(carry, result, was_graded=was_graded)

    # ── Return response ────────────────────────────────────────────────────────
    suggestions = result.get("follow_up_suggestions")
    quiz = result.get("quiz_steps")

    return ChatMessageResponse(
        session_id=session_id,
        response=result.get("response"),
        agent_used=result.get("agent_used"),
        follow_up_suggestions=suggestions if suggestions else None,
        quiz_steps=quiz if quiz else None,
        awaiting_graded_response=bool(result.get("awaiting_graded_response")),
        error=result.get("error"),
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
    _release_carry(req.session_id)
    return EndSessionResponse(ok=True)
