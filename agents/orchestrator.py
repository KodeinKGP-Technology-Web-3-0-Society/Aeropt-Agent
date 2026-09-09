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
    "Use the **Correct** or **Incorrect** buttons below to self-grade your attempt."
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

_GREETING_RE = re.compile(
    r"^(?:"
    r"hi|hello|hey|hiya|howdy|yo|sup|"
    r"good\s+(?:morning|afternoon|evening)|"
    r"thanks|thank\s+you|thx|"
    r"ok(?:ay)?|cool|great"
    r")[!.?\s]*$",
    re.IGNORECASE,
)

_WELCOME_MESSAGE = (
    "Hi! I'm your JEE Physics tutor. Ask me to explain a concept, walk through an "
    "example, or give you a practice question — for example, "
    "*\"Explain Newton's second law\"* or *\"Give me a PYQ on rotational motion.\"*"
)


_BRIEFING_RE = re.compile(
    r"\b(end session|end my session|session summary|mentor briefing|brief my mentor)\b",
    re.IGNORECASE,
)


def _resolve_active_agent(
    state: TutorState,
    intent: str,
    *,
    focus_chapters: list,
    interaction_count: int,
) -> str:
    """Map intent + session context → product agent."""
    query = state.get("query") or ""
    if _BRIEFING_RE.search(query):
        return "mentor_briefing"

    sandbox = (state.get("sandbox_action") or "").upper()
    if sandbox in {"PRACTICE", "PYQ", "QUIZ"}:
        return "practice_coach"
    if sandbox in {"LEARN", "REVISE"}:
        return "tutor"

    study_stage = (state.get("study_stage") or "").lower()
    roadmap_ctx = bool(state.get("roadmap_node_id") or state.get("roadmap_concept_id"))
    if roadmap_ctx and interaction_count == 1 and study_stage in ("theory", "example", ""):
        from mcp_tools.get_syllabus_status import GetSyllabusStatusArgs, get_syllabus_status

        status = get_syllabus_status(
            GetSyllabusStatusArgs(
                user_id=state["user_id"],
                focus_chapters=focus_chapters,
                roadmap_concept_id=state.get("roadmap_concept_id"),
            )
        )
        if status.get("coverage_gap") or status.get("unfinished_lessons", 0) >= 3:
            return "syllabus"

    if intent == "practice_request":
        return "practice_generator"
    if intent == "practice_submission":
        return "practice_analyzer"
    if intent == "quiz_request":
        return "practice_coach"
    if intent == "prereq_confirmation":
        return "tutor"
    if intent == "doubt":
        return "doubt_resolver"
    return "doubt_resolver"


def _is_greeting(query: str) -> bool:
    return bool(_GREETING_RE.match((query or "").strip()))


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


from typing import Tuple

def _classify_intent(
    query: str,
    active_quiz: bool,
    recent_turns: list,
    pending_prereq: str,
    practice_question_pending: bool,
) -> Tuple[str, list[str], float]:
    context_lines = []
    for t in recent_turns:
        role = t.get("role", "")
        content = t.get("content", "")[:200]
        context_lines.append(f"  [{role}]: {content}")
    context = "\n".join(context_lines) if context_lines else "  (none)"

    from utils.skills import load_skill
    skill = load_skill("orchestrator") or {}

    prompt = skill["system_prompt"]
    prompt = prompt.replace("{{active_quiz}}", str(active_quiz))
    prompt = prompt.replace("{{pending_prereq}}", str(pending_prereq or "(none)"))
    prompt = prompt.replace("{{practice_question_pending}}", str(practice_question_pending))
    prompt = prompt.replace("{{context}}", str(context))
    prompt = prompt.replace("{{query}}", str(query))

    raw, _ = groq_complete(
        client=_groq,
        model=skill.get("model", config.ORCHESTRATOR_MODEL),
        messages=[{"role": "user", "content": prompt}],
        fallback_models=config.SMALL_MODEL_FALLBACK,
        temperature=skill.get("temperature", 0.0),
        max_tokens=60,
    )
    raw = raw.strip()
    
    # Strip potential markdown code block
    if raw.startswith("```"):
        lines = raw.split("\n")
        if len(lines) > 1 and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    primary_intent = "doubt"
    secondary_intents = []
    confidence = 0.0

    import json
    try:
        parsed = json.loads(raw)
        primary_intent = parsed.get("primary_intent", "doubt").lower()
        secondary_intents = parsed.get("secondary_intents", [])
        confidence = float(parsed.get("confidence", 0.0))
    except (json.JSONDecodeError, ValueError, TypeError):
        raw_lower = raw.lower()
        valid = {
            "doubt", "practice_request", "quiz_request", "quiz_response",
            "follow_up_response", "prereq_confirmation", "graded_response"
        }
        for v in ("quiz_request", "quiz_response", "follow_up_response",
                  "prereq_confirmation", "graded_response", "practice_request", "doubt"):
            if v in raw_lower:
                primary_intent = v
                break

    return primary_intent, secondary_intents, confidence


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
        grade_level = user.get("grade_level")
        daily_goal_minutes = user.get("daily_goal_minutes")
        focus_chapters = list(state.get("focus_chapters") or [])
        if not focus_chapters:
            profile = db.read_user_learning_profile(user_id)
            if profile:
                focus_chapters = list(profile.get("weak_chapters") or [])

        # 2. Load session
        session = db.read_session(session_id)
        if not session:
            session = db.create_session(user_id)
            session_id = session["session_id"]
        interaction_count = (session.get("interaction_count") or 0) + 1
        active_quiz = session.get("active_quiz", False)

        # 3. Load last 2 chat turns for context
        recent_turns = db.read_recent_chat_turns(session_id, n=2)

        # 3b. Greetings / small-talk — fast path (skip LLM intent + full pipeline)
        if _is_greeting(query):
            db.update_session_interaction_count(session_id, interaction_count)
            db.insert_chat_turn(
                session_id=session_id,
                role="user",
                content=query,
                turn_index=interaction_count * 2 - 1,
                agent_used="orchestrator",
            )
            db.insert_chat_turn(
                session_id=session_id,
                role="assistant",
                content=_WELCOME_MESSAGE,
                turn_index=interaction_count * 2,
                agent_used="orchestrator",
            )
            return {
                "session_id": session_id,
                "target_exam": target_exam,
                "preferred_depth": preferred_depth,
                "grade_level": grade_level,
                "daily_goal_minutes": daily_goal_minutes,
                "focus_chapters": focus_chapters,
                "interaction_count": interaction_count,
                "active_quiz": active_quiz,
                "recent_turns": recent_turns,
                "intent": "doubt",
                "active_agent": "orchestrator",
                "response": _WELCOME_MESSAGE,
                "agent_used": "orchestrator",
                "run_context_manager": False,
                "prior_session_summary": state.get("prior_session_summary"),
                "prior_structured_memory": state.get("prior_structured_memory"),
                "error": None,
            }

        # 4. Intent classification ────────────────────────────────────────────
        pending_prereq = state.get("pending_prereq_concept_id", "") or ""
        practice_question_pending = bool(state.get("awaiting_graded_response"))
        extra_updates: Dict[str, Any] = {}

        if state.get("has_graded_outcome") and state.get("is_correct") is not None:
            # "1" / "0" detected deterministically in main.py — skip LLM
            intent = "graded_response"
            extra_updates["secondary_intents"] = []
            extra_updates["confidence"] = 1.0
        elif query.startswith("SUBMISSION::"):
            intent = "practice_submission"
            extra_updates["secondary_intents"] = []
            extra_updates["confidence"] = 1.0
        else:
            intent, secondary_intents, confidence = _classify_intent(
                query, active_quiz, recent_turns, pending_prereq, practice_question_pending
            )
            extra_updates["secondary_intents"] = secondary_intents
            extra_updates["confidence"] = confidence

            # Phase 2, Task 2.2: Log compound/ambiguous intents
            if confidence < 0.6 or secondary_intents:
                import logging
                logging.getLogger("orchestrator").warning(
                    f"COMPOUND_INTENT_GAP | Query: {query} | Primary: {intent} | "
                    f"Secondary: {secondary_intents} | Confidence: {confidence}"
                )

        # ── Guard: follow_up_response with no outstanding suggestions ────────────
        # Models like groq/compound-mini over-classify greetings ("hi", "okay",
        # "thanks") as follow_up_response.  Carry is already merged into state by
        # _build_initial_state, so state["follow_up_suggestions"] is authoritative.
        if intent == "follow_up_response":
            if not state.get("follow_up_suggestions") or _is_greeting(query):
                intent = "doubt"

        # 5. Handle special intents ───────────────────────────────────────────
        
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

        # 9. Cross-session memory: load prior text summary
        #    on the very first turn of a new session only.
        prior_session_summary = state.get("prior_session_summary")
        if interaction_count == 1 and prior_session_summary is None:
            prior_session_summary = db.read_prior_session_summary(
                user_id, exclude_session_id=session_id
            )

        active_agent = _resolve_active_agent(
            {**state, **extra_updates, "intent": intent},
            intent,
            focus_chapters=focus_chapters,
            interaction_count=interaction_count,
        )

        result: Dict[str, Any] = {
            "session_id": session_id,
            "target_exam": target_exam,
            "preferred_depth": preferred_depth,
            "grade_level": grade_level,
            "daily_goal_minutes": daily_goal_minutes,
            "focus_chapters": focus_chapters,
            "interaction_count": interaction_count,
            "active_quiz": active_quiz,
            "recent_turns": recent_turns,
            "intent": intent,
            "active_agent": active_agent,
            "run_context_manager": run_context_manager,
            "prior_session_summary": prior_session_summary,
            "error": None,
        }
        result.update(extra_updates)
        return result

    except Exception as exc:
        return {"error": f"Orchestrator failed: {exc}"}
