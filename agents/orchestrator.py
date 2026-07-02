"""
Orchestrator Agent

Responsibilities:
  - Load user profile (target_exam, preferred_depth) from Supabase
  - Load / create session, increment interaction_count
  - Load last 1-2 chat turns for downstream context
  - Classify intent:
      doubt | practice_request | quiz_response | follow_up_response |
      prereq_confirmation | graded_response
  - For graded_response already flagged by main.py: fast-path, skip LLM
  - For graded_response classified by LLM but has_graded_outcome=False:
      a practice/PYQ question is still awaiting — insert a reminder and
      route to END (workflow checks has_graded_outcome before Diagnostic)
  - For prereq_confirmation: inject pending prereq concept as primary_concept_id
  - On first turn of a new session: load the prior session summary
  - Flag run_context_manager every CONTEXT_MANAGER_EVERY_N interactions
  - Insert the user's chat turn into chat_turns

Model: llama-3.1-8b-instant (Groq) — fast, cheap, classification only
"""

from __future__ import annotations

import re
from typing import Any, Dict

from groq import Groq

import config
from db.supabase_client import db
from models.state import TutorState
from utils.llm import groq_complete

_groq = Groq(api_key=config.GROQ_API_KEY)

_INTENT_PROMPT = """\
You are an intent classifier for a JEE Physics tutoring system.
Classify the student's message into exactly ONE of these intents:

  doubt               – asking for explanation, concept help, or worked example
  practice_request    – explicitly asking for a single practice problem or PYQ
  quiz_request        – explicitly asking to be quizzed / for a set of questions \
to test themselves ("quiz me on X", "give me a quiz", "test me on X")
  quiz_response       – answering a question from an ongoing quiz
  follow_up_response  – selecting or acknowledging a follow-up suggestion \
("okay", "show me", "skip", "1", "2", "3", etc.)
  prereq_confirmation – student confirms they want to cover a suggested \
prerequisite concept ("yes", "sure", "okay let's do it", etc.)
  graded_response     – student is reporting or asking about the result of a \
practice/PYQ question that is still awaiting their answer \
(ONLY use this when practice_question_pending=True)

Active quiz running:              {active_quiz}
Pending prerequisite suggestion:  {pending_prereq}
Practice/PYQ question pending:    {practice_question_pending}
Last 2 turns:
{context}

Student message: "{query}"

Reply with ONLY the intent label — no punctuation, no explanation."""

_ANSWER_REMINDER = (
    "There is still an unanswered practice question above.\n\n"
    "Please type **1** if your answer was correct, or **0** if it was incorrect."
)

# Quiz-size parsing for on-demand quizzes ("give me 3 quiz questions on X").
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
# Matches "<n> ... question(s)" so we don't grab unrelated numbers like
# "chapter 3" or "JEE 2021".  Falls back to default when nothing matches.
_QUIZ_COUNT_RE = re.compile(
    r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:quiz\s+)?(?:questions?|qs?|problems?)",
    flags=re.IGNORECASE,
)

_QUIZ_DEFAULT_SIZE = 5
_QUIZ_MAX_SIZE = 5


def _parse_quiz_count(query: str) -> int:
    """
    Return the number of questions the student asked for, clamped to 1–5.
    Defaults to 5 when no explicit "<n> questions" phrase is present.
    """
    m = _QUIZ_COUNT_RE.search(query or "")
    if not m:
        return _QUIZ_DEFAULT_SIZE
    token = m.group(1).lower()
    n = int(token) if token.isdigit() else _NUMBER_WORDS.get(token, _QUIZ_DEFAULT_SIZE)
    return max(1, min(_QUIZ_MAX_SIZE, n))


def _classify_intent(
    query: str,
    active_quiz: bool,
    recent_turns: list,
    pending_prereq: str,
    practice_question_pending: bool,
) -> str:
    context_lines = []
    for t in recent_turns:
        role = t.get("role", "")
        content = t.get("content", "")[:200]
        context_lines.append(f"  [{role}]: {content}")
    context = "\n".join(context_lines) if context_lines else "  (none)"

    prompt = _INTENT_PROMPT.format(
        active_quiz=active_quiz,
        pending_prereq=pending_prereq or "(none)",
        practice_question_pending=practice_question_pending,
        context=context,
        query=query,
    )
    raw, _ = groq_complete(
        client=_groq,
        model=config.ORCHESTRATOR_MODEL,
        messages=[{"role": "user", "content": prompt}],
        fallback_models=config.SMALL_MODEL_FALLBACK,
        temperature=0.0,
        max_tokens=10,
    )
    raw = raw.strip().lower()

    valid = {
        "doubt",
        "practice_request",
        "quiz_request",
        "quiz_response",
        "follow_up_response",
        "prereq_confirmation",
        "graded_response",
    }
    # Check longer/more-specific labels first so "quiz_request" is not shadowed
    # by a substring match on "quiz_response" and vice-versa.
    for v in ("quiz_request", "quiz_response", "follow_up_response",
              "prereq_confirmation", "graded_response", "practice_request", "doubt"):
        if v in raw:
            return v
    for v in valid:
        if v in raw:
            return v
    return "doubt"  # safe default


def orchestrator_node(state: TutorState) -> Dict[str, Any]:
    user_id = state["user_id"]
    session_id = state["session_id"]
    query = state["query"]

    try:
        # 1. Load user profile
        user = db.read_user(user_id)
        if not user:
            return {"error": f"User {user_id} not found. Please register first."}
        target_exam = user.get("target_exam", "JEE_ADVANCED")
        preferred_depth = user.get("preferred_depth", "standard")

        # 2. Load session
        session = db.read_session(session_id)
        if not session:
            session = db.create_session(user_id)
        interaction_count = (session.get("interaction_count") or 0) + 1
        active_quiz = session.get("active_quiz", False)

        # 3. Load last 2 chat turns for context
        recent_turns = db.read_recent_chat_turns(session_id, n=2)

        # 4. Intent classification ────────────────────────────────────────────
        pending_prereq = state.get("pending_prereq_concept_id", "") or ""
        practice_question_pending = bool(state.get("awaiting_graded_response"))

        if state.get("has_graded_outcome") and state.get("is_correct") is not None:
            # "1" / "0" detected deterministically in main.py — skip LLM
            intent = "graded_response"
        else:
            intent = _classify_intent(
                query, active_quiz, recent_turns, pending_prereq, practice_question_pending
            )

        # 5. Handle special intents ───────────────────────────────────────────
        extra_updates: Dict[str, Any] = {}

        # quiz_request: flag on-demand quiz batch mode.  Concept Resolver and
        # GraphRAG Retriever then run as usual; the Quiz agent builds the batch.
        if intent == "quiz_request":
            extra_updates["quiz_batch_mode"] = True
            extra_updates["quiz_batch_size"] = _parse_quiz_count(query)

        # prereq_confirmation: wire pending prereq as the new primary concept
        if intent == "prereq_confirmation" and pending_prereq:
            extra_updates["primary_concept_id"] = pending_prereq
            extra_updates["content_type_requested"] = "theory"
            extra_updates["secondary_concept_ids"] = []
            extra_updates["pending_prereq_concept_id"] = None

        # graded_response without has_graded_outcome: LLM detected an ambiguous
        # answer-related message while a practice question was still pending.
        # Show a reminder and route to END (workflow checks has_graded_outcome).
        if intent == "graded_response" and not state.get("has_graded_outcome"):
            db.insert_chat_turn(
                session_id=session_id,
                role="assistant",
                content=_ANSWER_REMINDER,
                turn_index=interaction_count * 2,
                agent_used="orchestrator",
            )
            extra_updates["response"] = _ANSWER_REMINDER
            extra_updates["agent_used"] = "orchestrator"
            # Keep the awaiting flag so it persists in carry state
            extra_updates["awaiting_graded_response"] = True

        # 6. Update interaction_count
        db.update_session_interaction_count(session_id, interaction_count)

        # 7. Insert the user's turn
        db.insert_chat_turn(
            session_id=session_id,
            role="user",
            content=query,
            turn_index=interaction_count * 2 - 1,  # odd indices = user
            agent_used="orchestrator",
        )

        # 8. Context Manager trigger
        run_context_manager = (
            interaction_count % config.CONTEXT_MANAGER_EVERY_N == 0
        )

        # 9. Cross-session memory: load prior summary on the very first turn only
        prior_session_summary = state.get("prior_session_summary")
        if interaction_count == 1 and prior_session_summary is None:
            prior_session_summary = db.read_prior_session_summary(
                user_id, exclude_session_id=session_id
            )

        result: Dict[str, Any] = {
            "target_exam": target_exam,
            "preferred_depth": preferred_depth,
            "interaction_count": interaction_count,
            "active_quiz": active_quiz,
            "recent_turns": recent_turns,
            "intent": intent,
            "run_context_manager": run_context_manager,
            "prior_session_summary": prior_session_summary,
            "error": None,
        }
        result.update(extra_updates)
        return result

    except Exception as exc:
        return {"error": f"Orchestrator failed: {exc}"}
