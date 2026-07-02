"""
Shared LangGraph state for the JEE Tutor Agent pipeline.

Every node receives the full TutorState and returns a dict of keys to update.
total=False means every key is optional from a Python perspective — nodes only
need to populate the keys they own.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict


class TutorState(TypedDict, total=False):
    # ── Core identifiers ──────────────────────────────────────────────────────
    user_id: str
    session_id: str
    query: str

    # ── User profile (Orchestrator reads) ─────────────────────────────────────
    target_exam: str        # e.g. "JEE_ADVANCED"
    preferred_depth: str    # "standard" | "deep" | "quick"

    # ── Session state (Orchestrator reads/writes) ─────────────────────────────
    interaction_count: int
    active_quiz: bool
    recent_turns: List[Dict[str, Any]]   # last 1–2 chat_turns rows

    # ── Orchestrator classification ───────────────────────────────────────────
    # doubt | practice_request | quiz_response | follow_up_response
    intent: str

    # ── Concept Resolver output ───────────────────────────────────────────────
    primary_concept_id: str
    secondary_concept_ids: List[str]
    # theory | practice | pyq | example
    content_type_requested: str

    # ── GraphRAG Retriever output ─────────────────────────────────────────────
    retrieved_chunks: List[Dict[str, Any]]          # [{content_id, core_text, difficulty, ...}]
    prereq_concept_states: List[Dict[str, Any]]     # [{concept_id, proficiency_score, hop_count ...}]
    secondary_concept_states: List[Dict[str, Any]]  # [{concept_id, concept_name, proficiency_score, ...}]
    target_concept_state: Optional[Dict[str, Any]]  # user_concept_state row for primary concept

    # ── Socratic / Solver routing ─────────────────────────────────────────────
    use_socratic: bool
    socratic_turn_count: int  # loaded from chat_turns by retriever

    # ── Main response ─────────────────────────────────────────────────────────
    # Set by Solver or Socratic; never set by both in the same turn.
    response: str
    agent_used: str    # "solver" | "socratic"
    model_used: str    # actual Groq model that generated the response

    # ── Graded outcome (supplied by caller before graph invocation) ───────────
    # Set to True when the student submitted a numeric / multi-choice answer.
    has_graded_outcome: bool
    student_answer: Optional[str]
    is_correct: Optional[bool]
    error_type: Optional[str]          # conceptual | procedural | calculation | misread
    time_taken_sec: Optional[int]

    # ── Diagnostic output ────────────────────────────────────────────────────
    trigger_quiz: bool   # True when struggle_streak >= STRUGGLE_STREAK_QUIZ_TRIGGER

    # ── Follow-Up output ─────────────────────────────────────────────────────
    follow_up_suggestions: List[Dict[str, Any]]  # [{content_id, title, difficulty}, ...]

    # ── Quiz output ───────────────────────────────────────────────────────────
    quiz_steps: List[Dict[str, Any]]  # [{step_num, question, hint, answer, concept_id}, ...]

    # ── On-demand quiz batch (intent=quiz_request) ────────────────────────────
    # Set when the student explicitly asks for a quiz.  A fixed set of practice/
    # PYQ questions is shown at once (Q1..QN → separator → A1..AN); the student
    # then self-grades each one with 1/0 sequentially.  quiz_batch_index points
    # at the question being graded next; quiz_batch_chunks holds the question
    # chunks so Diagnostic can grade the correct content_id each turn.
    # All three are carried in main.py carry-state and evicted when the batch
    # completes (quiz_batch_mode set back to False by Follow-Up).
    quiz_batch_mode: bool
    quiz_batch_index: int
    quiz_batch_chunks: List[Dict[str, Any]]
    quiz_batch_size: int   # how many questions the student asked for (1–5, default 5)

    # ── Prereq confirmation flow ──────────────────────────────────────────────
    # Set by Follow-Up when it surfaces a weak prereq after an incorrect attempt.
    # Persisted in main.py carry-state across turns so the Orchestrator can read
    # it when the student replies with prereq_confirmation intent.
    pending_prereq_concept_id: Optional[str]

    # ── Practice / PYQ answer-pending flag ───────────────────────────────────
    # Set to True by Solver (or Follow-Up selection) when a practice or PYQ
    # question is shown to the student and no graded response has arrived yet.
    # Cleared in main.py carry-state once the student submits "1" or "0".
    # Used by the Orchestrator to detect ambiguous follow-up messages and route
    # them to a reminder instead of the full doubt pipeline (Bug 3).
    awaiting_graded_response: bool

    # ── Cross-session memory ──────────────────────────────────────────────────
    # Loaded by Orchestrator on the very first turn of a new session only.
    # Injected into Solver / Socratic prompts for continuity.
    prior_session_summary: Optional[str]

    # ── Session-scoped follow-up deduplication ────────────────────────────────
    # Content IDs that have already been surfaced as follow-up suggestions in
    # this session.  Persisted in carry state so each new suggestion set
    # excludes previously shown items (even if not yet in user_attempts).
    seen_follow_up_ids: List[str]

    # ── Flags ─────────────────────────────────────────────────────────────────
    run_context_manager: bool   # True when interaction_count % 5 == 0
    error: Optional[str]        # set on any unrecoverable node failure
