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
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.WARNING,
    format="[%(levelname)s %(name)s] %(message)s",
)
logging.getLogger("jee_tutor.llm").setLevel(logging.WARNING)
logging.getLogger("jee_tutor.solver").setLevel(logging.WARNING)

from db.supabase_client import db
from graph.workflow import tutor_graph
from models.state import TutorState
from agents.context_manager import context_manager_node


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_graded_input(raw: str) -> Dict[str, Any]:
    """
    Parse legacy ANSWER::<answer>::<correct|incorrect>::<error_type>::<seconds>
    Returns dict with has_graded_outcome=True and populated fields.
    """
    parts = raw[len("ANSWER::"):].split("::")
    student_answer = parts[0] if len(parts) > 0 else ""
    correctness = parts[1].lower() if len(parts) > 1 else "incorrect"
    error_type = parts[2].lower() if len(parts) > 2 else None
    try:
        time_taken = int(parts[3]) if len(parts) > 3 else None
    except ValueError:
        time_taken = None

    is_correct = correctness == "correct"
    return {
        "has_graded_outcome": True,
        "student_answer": student_answer,
        "is_correct": is_correct,
        "error_type": error_type if not is_correct else None,
        "time_taken_sec": time_taken,
    }


# ── Cross-turn carry state ────────────────────────────────────────────────────
#
# LangGraph starts fresh each invocation.  A small set of fields must survive
# from one turn to the next so that:
#   - Diagnostic knows which concept was being practised (primary_concept_id)
#   - Follow-Up can surface weak prereqs (prereq_concept_states)
#   - Orchestrator can inject the pending prereq on confirmation (pending_prereq_concept_id)
#   - Follow-Up can show the previously offered suggestions (follow_up_suggestions)
#
# retrieved_chunks / prereq_concept_states / target_concept_state are intentionally
# NOT in this list — they must only survive while a practice/PYQ question is still
# awaiting a graded answer (awaiting_graded_response=True). Carrying them on every
# turn bleeds stale retrieval context into unrelated new queries (Bug 5).
#
_CARRY_KEYS = [
    "primary_concept_id",
    "secondary_concept_ids",
    "content_type_requested",
    "prior_session_summary",   # loaded once; keep it for all subsequent turns
    "seen_follow_up_ids",      # session-scoped dedup for follow-up suggestions
]


_CTYPE_ALIASES: Dict[str, str] = {
    "pyq": "pyq",
    "past year question": "pyq",
    "past year paper": "pyq",
    "practice": "practice",
    "practice question": "practice",
    "example": "example",
    "solved example": "example",
    "worked example": "example",
    "theory": "theory",
}

def _normalize_content_type(raw: str) -> str:
    """Return a canonical lowercase content_type from any raw DB or LLM value."""
    key = (raw or "").lower().strip()
    return _CTYPE_ALIASES.get(key, key)


def _update_carry(
    carry: Dict[str, Any],
    result: TutorState,
    was_graded: bool = False,
) -> None:
    """
    Update carry state from the completed turn's result.

    was_graded=True  →  the student just submitted a "1"/"0" (or ANSWER::) answer,
                        so the practice/PYQ cycle is complete; clear the awaiting
                        flag and all chunk carry-state.
    """
    for key in _CARRY_KEYS:
        val = result.get(key)  # type: ignore[call-overload]
        if val is not None:
            # Always normalise content_type so "PYQ" / "Practice Question" etc.
            # stored by raw Supabase reads don't break the "1"/"0" detection gate.
            if key == "content_type_requested":
                carry[key] = _normalize_content_type(val)
            else:
                carry[key] = val

    # ── awaiting_graded_response lifecycle ────────────────────────────────────
    # Solver / Follow-Up selection explicitly set True (practice/PYQ served) or
    # False (theory/example served).  A graded turn submission normally clears
    # it — UNLESS the result explicitly re-arms it (an on-demand quiz batch
    # advancing to its next question sets awaiting=True even on a graded turn).
    new_awaiting = result.get("awaiting_graded_response")  # type: ignore[call-overload]
    if new_awaiting is True:
        carry["awaiting_graded_response"] = True
    elif was_graded:
        carry.pop("awaiting_graded_response", None)
    elif new_awaiting is False:
        carry.pop("awaiting_graded_response", None)
    # new_awaiting is None → no change (reminder turn keeps the flag alive)

    # ── retrieved_chunks / prereq / target_state — only while awaiting ────────
    # These are only needed so that the subsequent Diagnostic turn can reference
    # the content that was just shown.  Carrying them beyond that poisons new
    # unrelated queries with stale retrieval context (Bug 5).
    if carry.get("awaiting_graded_response"):
        # Update from fresh retrieval if present; otherwise keep the existing
        # values so Diagnostic still has them on the "1"/"0" turn.
        for k in (
            "retrieved_chunks",
            "prereq_concept_states",
            "secondary_concept_states",
            "target_concept_state",
        ):
            v = result.get(k)  # type: ignore[call-overload]
            if v is not None:
                carry[k] = v
    else:
        carry.pop("retrieved_chunks", None)
        carry.pop("prereq_concept_states", None)
        carry.pop("secondary_concept_states", None)
        carry.pop("target_concept_state", None)

    # ── on-demand quiz batch lifecycle ────────────────────────────────────────
    # Keep the question set + cursor alive across the per-question 1/0 grading,
    # and point retrieved_chunks at the question being graded next so Diagnostic
    # scores the correct content_id.  Evict everything when the batch finishes.
    new_quiz_mode = result.get("quiz_batch_mode")  # type: ignore[call-overload]
    if new_quiz_mode is True:
        carry["quiz_batch_mode"] = True
        new_chunks = result.get("quiz_batch_chunks")  # type: ignore[call-overload]
        if new_chunks is not None:
            carry["quiz_batch_chunks"] = new_chunks
        new_idx = result.get("quiz_batch_index")  # type: ignore[call-overload]
        if new_idx is not None:
            carry["quiz_batch_index"] = new_idx
        q_chunks = carry.get("quiz_batch_chunks") or []
        q_idx = carry.get("quiz_batch_index", 0)
        if 0 <= q_idx < len(q_chunks):
            carry["retrieved_chunks"] = [q_chunks[q_idx]]
    elif new_quiz_mode is False:
        carry.pop("quiz_batch_mode", None)
        carry.pop("quiz_batch_index", None)
        carry.pop("quiz_batch_chunks", None)

    # ── pending_prereq_concept_id ─────────────────────────────────────────────
    new_pending = result.get("pending_prereq_concept_id")  # type: ignore[call-overload]
    if new_pending:
        carry["pending_prereq_concept_id"] = new_pending
    else:
        carry.pop("pending_prereq_concept_id", None)

    # ── follow_up_suggestions — carry so follow_up_response can resolve choice ─
    suggestions = result.get("follow_up_suggestions")  # type: ignore[call-overload]
    if suggestions:
        carry["follow_up_suggestions"] = suggestions
    else:
        carry.pop("follow_up_suggestions", None)


def _build_initial_state(
    user_id: str,
    session_id: str,
    query: str,
    carry: Dict[str, Any],
    graded_overrides: Optional[Dict[str, Any]] = None,
) -> TutorState:
    state: TutorState = {
        "user_id": user_id,
        "session_id": session_id,
        "query": query,
        # Graded defaults (may be overridden below)
        "has_graded_outcome": False,
        "student_answer": None,
        "is_correct": None,
        "error_type": None,
        "time_taken_sec": None,
        # Other defaults
        "trigger_quiz": False,
        "use_socratic": False,
        "socratic_turn_count": 0,
        "run_context_manager": False,
        "follow_up_suggestions": [],
        "quiz_steps": [],
        "secondary_concept_ids": [],
        "recent_turns": [],
        "prereq_concept_states": [],
        "retrieved_chunks": [],
        "pending_prereq_concept_id": None,
        "prior_session_summary": None,
        "awaiting_graded_response": False,
        "seen_follow_up_ids": [],
        "secondary_concept_states": [],
        "quiz_batch_mode": False,
        "quiz_batch_index": 0,
        "quiz_batch_chunks": [],
        "quiz_batch_size": 5,
    }
    # Merge carry-over fields from previous turn
    state.update(carry)  # type: ignore[typeddict-item]
    # Apply explicit graded overrides (highest priority)
    if graded_overrides:
        state.update(graded_overrides)  # type: ignore[typeddict-item]
    return state


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


def _flush_session_summary(session_id: str, user_id: str) -> None:
    """Force a final Context Manager run on session exit (Improvement 4)."""
    try:
        # Build a minimal state with only the fields context_manager_node needs
        flush_state: TutorState = {   # type: ignore[typeddict-item]
            "session_id": session_id,
            "user_id": user_id,
        }
        context_manager_node(flush_state)
    except Exception:
        pass  # best-effort; never crash on exit


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


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="JEE Tutor Agent CLI")
    parser.add_argument("--user-id", help="Existing user UUID")
    parser.add_argument("--session-id", help="Existing session UUID to resume")
    parser.add_argument("--register", action="store_true", help="Register a new user")
    args = parser.parse_args()

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
    if args.session_id:
        session_id = args.session_id
        print(f"Resuming session {session_id}")
    else:
        session = db.create_session(user_id)
        session_id = session["session_id"]
        print(f"\nNew session started: {session_id}")

    print("\nJEE Physics Tutor ready. Type your question (or 'quit' to exit).")
    print("After a practice question, reply  1  (correct) or  0  (incorrect).")
    print("─" * 60)

    # Cross-turn carry state (see _CARRY_KEYS above)
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
            graded_overrides: Optional[Dict[str, Any]] = None
            query = raw_input

            # Auto-detect "1" / "0" after a practice/PYQ question.
            # Guard on awaiting_graded_response so that selecting a follow-up
            # suggestion by number ("1", "2", "3") is never misread as a grade.
            was_graded = False
            if (
                raw_input.strip() in {"1", "0"}
                and carry.get("content_type_requested") in {"practice", "pyq"}
                and carry.get("awaiting_graded_response")
            ):
                is_correct = raw_input.strip() == "1"
                graded_overrides = {
                    "has_graded_outcome": True,
                    "student_answer": raw_input.strip(),
                    "is_correct": is_correct,
                    "error_type": None,
                    "time_taken_sec": None,
                }
                was_graded = True

            # Legacy ANSWER:: format (quiz steps)
            elif raw_input.upper().startswith("ANSWER::"):
                graded_overrides = _parse_graded_input(raw_input)
                query = graded_overrides.get("student_answer", raw_input)
                was_graded = True

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
