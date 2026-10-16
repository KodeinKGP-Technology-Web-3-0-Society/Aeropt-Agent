"""
Shared state for the Study Room pipeline.

Analogous to TutorState but for the structured learning flow:
Learn → Examples → Practice → Evaluation.

Every node receives the full StudyRoomState and returns a dict of keys to update.
total=False means every key is optional — nodes only need to populate the keys they own.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict


class StudyRoomState(TypedDict, total=False):
    # ── Core identifiers ──────────────────────────────────────────────────────
    user_id: str
    session_id: str              # agent chat session (for chat_turns)
    study_session_id: str        # study_sessions row
    query: str

    # ── Study session context ─────────────────────────────────────────────────
    concept_id: str
    concept_name: str
    chapter: str
    current_phase: str           # learn | examples | practice | evaluation | completed
    difficulty_level: int        # 1-5, adapted by the teacher agent
    questions_served: int
    questions_correct: int
    lesson_plan: Dict[str, List[str]]   # migration 011: outline per phase
    phase_step: int                     # parts of the current phase completed
    phase_turns: int                    # student messages in the current phase
    study_mode: str                     # learn | practice (migration 014)
    question_source: str                # any | pyq

    # ── User profile ──────────────────────────────────────────────────────────
    target_exam: str             # JEE_ADVANCED | JEE_MAINS
    preferred_depth: str         # standard | deep | quick

    # ── Session state ─────────────────────────────────────────────────────────
    interaction_count: int
    recent_turns: List[Dict[str, Any]]
    session_summary: Optional[str]

    # ── Retrieval output (reused from existing pipeline) ──────────────────────
    retrieved_chunks: List[Dict[str, Any]]
    retrieval_confidence: str    # ok | low | unresolved | skipped
    primary_concept_id: str      # set by concept resolution

    # ── Practice phase ────────────────────────────────────────────────────────
    current_question: Optional[Dict[str, Any]]
    awaiting_answer: bool
    practice_streak: int         # consecutive correct answers
    wrong_streak: int            # consecutive wrong answers
    served_content_ids: List[str]  # content_ids already served this session
    last_answered: Optional[Dict[str, Any]]  # the question just graded, for "explain it"
    # This turn's practice grade, staged by grade_practice_answer for commit.
    # Transient. Not carried. Keys: is_correct, content_id, concept_id,
    # attempt_id, proficiency_before, proficiency_after.
    last_grade: Optional[Dict[str, Any]]

    # ── Memory ────────────────────────────────────────────────────────────────
    recalled_memories: List[Dict[str, Any]]
    # Episodic block for this turn only. Written by memory_recall_node and read
    # by the Teacher Agent. Not carried across turns.
    episode_context: Optional[str]
    prior_study_sessions: List[Dict[str, Any]]  # past sessions on this concept
    learner_snapshot: Dict[str, Any]            # services/study_context.py
    prior_session_summary: Optional[str]

    # ── Agent output ──────────────────────────────────────────────────────────
    response: str
    agent_used: str              # teacher_agent | evaluation_agent
    model_used: str
    phase_transition: Optional[str]  # set when phase changes (e.g. "examples")
    active_agent: Optional[str]      # for UI roster panel compatibility
    tools_used: List[str]            # tool names the teacher called this turn
    agent_trace_path: Optional[str]
    pending_permissions: List[Dict[str, Any]]

    # ── Evaluation output ─────────────────────────────────────────────────────
    evaluation_summary: Optional[str]
    readiness_score: Optional[float]
    readiness_delta: Optional[float]  # change from previous score
    outcome: Optional[str]            # mastered | needs_review | incomplete

    # ── Flags ─────────────────────────────────────────────────────────────────
    run_context_manager: bool
    error: Optional[str]
    cancelled: bool                  # the student pressed Stop
