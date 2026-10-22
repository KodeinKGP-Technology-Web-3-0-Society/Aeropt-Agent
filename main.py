"""
JEE Tutor Agent — CLI entry point

Usage:
  python main.py                          # interactive chat (creates new session)
  python main.py --user-id <uuid>         # resume as existing user
  python main.py --session-id <uuid>      # resume specific session
  python main.py --register               # register a new user first

Graded answers (practice / PYQ questions):
  After the tutor shows a practice question, simply type:
    1   → you got it correct
    0   → you got it incorrect
  The system detects this automatically and routes to the Diagnostic agent.

Legacy graded format (quiz steps):
  ANSWER::<your answer>::<correct|incorrect>::<error_type>::<seconds>
"""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*pydantic.v1.*", category=UserWarning)

import argparse
import logging
import sys
import time
from typing import Any, Dict

logging.basicConfig(
    level=logging.WARNING,
    format="[%(levelname)s %(name)s] %(message)s",
)
logging.getLogger("jee_tutor.llm").setLevel(logging.WARNING)
logging.getLogger("jee_tutor.solver").setLevel(logging.WARNING)

from db.supabase_client import db
from graph.workflow import tutor_graph
from models.state import TutorState
from services.turn_service import (
    build_initial_state as _build_initial_state,
    flush_session_summary as _flush_session_summary,
    parse_turn_input,
    persist_assistant_reply as _persist_assistant_reply,
    resolve_session,
    update_carry as _update_carry,
)


def _print_response(result: TutorState) -> None:
    """Pretty-print the graph result to stdout."""
    print()
    if result.get("error"):
        print(f"[ERROR] {result['error']}")
        return

    # Main response (Solver, Socratic, or Follow-Up prereq/selection message)
    response = result.get("response")
    if response:
        agent = result.get("agent_used", "tutor")
        print(f"─── [{agent.upper()}] ──────────────────────────────────────────")
        print(response)
        print()

    # Quiz steps (if quiz was just generated)
    quiz_steps = result.get("quiz_steps")
    if quiz_steps:
        print("─── [QUIZ STARTED] ─────────────────────────────────────────")
        for step in quiz_steps:
            print(f"\nStep {step['step_num']} [{step.get('concept_id', '')}]")
            print(f"  Q: {step.get('question', '')}")
            print(f"  Hint: {step.get('hint', '')}")
        print()
        print("Submit your answer as: ANSWER::<your answer>::<correct|incorrect>::<error_type>::<seconds>")
        print()

    # Follow-up suggestions
    suggestions = result.get("follow_up_suggestions")
    if suggestions:
        print("─── [FOLLOW-UP SUGGESTIONS] ────────────────────────────────")
        for i, s in enumerate(suggestions, 1):
            ctype = s.get("content_type", "")
            diff = s.get("difficulty", "?")
            preview = s.get("preview", "")[:120]
            print(f"  {i}. [{ctype.upper()} | difficulty={diff}] {preview}...")
        print()
        print("  Reply with 1, 2, or 3 to see that content, or 'skip' to continue.")
        print()


def _register_interactive() -> str:
    print("\n── New User Registration ────────────────────────────────────")
    name = input("Your name: ").strip() or "Student"
    print("Target exam options:")
    print("  1. JEE_ADVANCED")
    print("  2. JEE_MAINS")
    choice = input("Enter 1 or 2 [1]: ").strip()
    target_exam = "JEE_MAINS" if choice == "2" else "JEE_ADVANCED"

    user = db.register_user(
        name=name,
        target_exam=target_exam,
        preferred_depth="standard",
    )
    uid = user["user_id"]
    print(f"\nRegistered! Your user_id: {uid}")
    print("Save this for future sessions.\n")
    return uid


def _prepare_retrieval() -> bool:
    """Pay database/model cold-start costs before any learner turn is timed."""
    from retrieval import service, store

    print("\nPreparing course retrieval and the embedding model (first start may take a moment)...", flush=True)
    started = time.monotonic()
    try:
        store.verify_embedding_contract()
        result = service.warm()
        if not result.get("warmed"):
            raise RuntimeError(result.get("reason") or "The retrieval index is not ready.")
    except Exception as exc:
        logging.getLogger("jee_tutor.cli").error("Retrieval initialization failed: %s", exc)
        print("[ERROR] Check RETRIEVAL_DATABASE_URL, the populated course index, and the embedding model, then restart.")
        return False
    print(f"Course retrieval ready ({time.monotonic() - started:.1f}s).")
    return True


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="JEE Tutor Agent CLI")
    parser.add_argument("--user-id", help="Existing user UUID")
    parser.add_argument("--session-id", help="Existing session UUID to resume")
    parser.add_argument("--register", action="store_true", help="Register a new user")
    args = parser.parse_args()

    if not _prepare_retrieval():
        sys.exit(1)

    # ── User setup ────────────────────────────────────────────────────────────
    if args.register:
        user_id = _register_interactive()
    elif args.user_id:
        user_id = args.user_id
    else:
        stored = input("Enter your user_id (or press Enter to register): ").strip()
        if stored:
            user_id = stored
        else:
            user_id = _register_interactive()

    # ── Session setup ─────────────────────────────────────────────────────────
    try:
        session_id = resolve_session(user_id=user_id, session_id=args.session_id, create_reason="cli")
        if args.session_id:
            print(f"Resuming session {session_id}")
        else:
            print(f"\nNew session started: {session_id}")
    except KeyError:
        print(f"[ERROR] Session {args.session_id} not found.")
        sys.exit(1)

    print("\nJEE Physics Tutor ready. Type your question (or 'quit' to exit).")
    print("After a practice question, reply  1  (correct) or  0  (incorrect).")
    print("─" * 60)

    # Cross-turn carry state (see services/turn_service.py)
    carry: Dict[str, Any] = {}

    # ── Chat loop ─────────────────────────────────────────────────────────────
    try:
        while True:
            try:
                raw_input = input("\nYou: ").strip()
            except EOFError:
                break

            if not raw_input:
                continue
            if raw_input.lower() in {"quit", "exit", "bye"}:
                break

            # ── Parse input ────────────────────────────────────────────────────
            query, graded_overrides, was_graded = parse_turn_input(
                raw_input=raw_input,
                carry=carry,
            )

            initial_state = _build_initial_state(
                user_id=user_id,
                session_id=session_id,
                query=query,
                carry=carry,
                graded_overrides=graded_overrides,
            )

            try:
                result: TutorState = tutor_graph.invoke(initial_state)
                _print_response(result)
                _update_carry(carry, result, was_graded=was_graded)
                _persist_assistant_reply(session_id, result)
            except Exception as exc:
                print(f"\n[SYSTEM ERROR] {exc}")
                print("Check your .env and DB connections.")

    except KeyboardInterrupt:
        print("\nGoodbye!")

    finally:
        # Improvement 4: guarantee a summary flush on every session exit
        print("\nSaving session summary…")
        _flush_session_summary(session_id, user_id)


if __name__ == "__main__":
    main()
