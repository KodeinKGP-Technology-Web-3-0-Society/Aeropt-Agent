"""
Shared LangGraph state for the tutor pipeline.

Every node receives the full TutorState and returns a dict of keys to update.
total=False means every key is optional from a Python perspective — nodes only
need to populate the keys they own.

**Anything a node returns that is not declared here is silently discarded.**
LangGraph builds its channels from this TypedDict, so an undeclared key does not
raise — it just never lands, and the bug shows up much later as a field that is
mysteriously always empty. If a tool stages a value, declare it here.

This state is now *conversational*, not procedural. Under the old graph it also
carried the routing machinery — which of seven intents this turn was, which
branch the edges should take, which facade agent was notionally active. The
agent decides all of that per turn now, so those fields are gone: `intent`,
`secondary_intents`, `confidence` and `active_agent` no longer exist. Per-call
data belongs in the tool argument and result schemas in `mcp_tools/`, not here.
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
    journey_type: Optional[str]   # fresh_start | mid_journey | revision_dropper
    focus_chapters: List[str]     # weak chapters from onboarding placement
    grade_level: Optional[str]
    daily_goal_minutes: Optional[int]

    # ── Session state (Orchestrator reads/writes) ─────────────────────────────
    interaction_count: int
    active_quiz: bool
    recent_turns: List[Dict[str, Any]]   # last 1–2 chat_turns rows

    # Rolling summary of THIS session so far (the Context Manager's output).
    # Distinct from prior_session_summary, which is the previous session's.
    session_summary: Optional[str]

    # ── Roadmap hint (Phase 2) ────────────────────────────────────────────────
    roadmap_node_id: Optional[str]     # UUID from Django PathNode
    roadmap_concept_id: Optional[str]    # concept_id hint when continuing from roadmap
    study_stage: Optional[str]       # theory | example | practice | pyq — roadmap session hint
    # Classroom room. A surface is an input shape, like a button click.
    classroom_surface: Optional[str]   # lesson | doubt | assess
    locked_concept_id: Optional[str]
    classroom_job: Optional[str]
    classroom_max_steps: Optional[int]

    # ── Concept Resolver output ───────────────────────────────────────────────
    primary_concept_id: str
    secondary_concept_ids: List[str]
    # theory | practice | pyq | example
    content_type_requested: str

    # ── GraphRAG Retriever output ─────────────────────────────────────────────
    retrieved_chunks: List[Dict[str, Any]]          # [{content_id, core_text, difficulty, ...}]
    retrieval_confidence: str                       # "ok" | "low"
    prereq_concept_states: List[Dict[str, Any]]     # [{concept_id, proficiency_score, hop_count ...}]
    secondary_concept_states: List[Dict[str, Any]]  # [{concept_id, concept_name, proficiency_score, ...}]
    target_concept_state: Optional[Dict[str, Any]]  # user_concept_state row for primary concept

    # ── Teaching signals (advisory, not routing) ─────────────────────────────
    # Computed by agents/planner.compute_personalization_strategy and surfaced to
    # the agent through retrieve_content as a *recommendation*. Nothing branches
    # on these any more — the agent weighs them against the conversation.
    use_socratic: bool
    socratic_turn_count: int   # guiding questions already asked on this concept
    teaching_mode: Optional[str]       # direct | socratic | prereq_first | practice
    difficulty_target: Optional[float]  # 1.0–5.0
    framing: Optional[str]

    # ── Main response ─────────────────────────────────────────────────────────
    # What the student sees. Written by the Learning Agent (possibly a composer's
    # draft it accepted verbatim) and then passed through the Answer Checker, or
    # by the deterministic graded fast path.
    response: str
    agent_used: str    # "learning_agent" | "graded_fast_path" | "follow_up"
    model_used: str    # the Groq model that produced the final text
    # Which capability the frontend's agent-roster panel should highlight.
    # Derived from what the agent actually did this turn, not from a guess made
    # before it ran. NOTE: this must stay declared — the old Orchestrator
    # returned it without it being in this TypedDict, so LangGraph dropped it
    # every turn and the panel never lit up.
    active_agent: Optional[str]

    # ── Learning Agent turn record ────────────────────────────────────────────
    # A draft staged by a composer expert, before the agent accepts or replaces
    # it. Cleared before the turn returns — it is never the student-facing value.
    draft_response: Optional[str]
    draft_author: Optional[str]
    draft_model: Optional[str]
    # Memories recalled into this turn's prompt, so the Answer Checker can judge
    # the reply against what the tutor claimed to know.
    recalled_memories: List[Dict[str, Any]]
    # Observability: an autonomous path cannot be read off a diagram, so it is
    # recorded. e.g. ["tool:resolve_concept", "tool:retrieve_content",
    # "handoff:worked_solution", "check:answer_checker"].
    agent_trace_path: List[str]
    agent_stop_reason: Optional[str]     # answered | max_steps | time_budget | llm_error
    checker_verdict: Optional[str]       # pass | revised | failed | skipped
    # Consequential actions the agent stopped short of, pending the student's
    # go-ahead. Surfaced to the caller; never acted on silently.
    pending_permissions: List[Dict[str, Any]]

    # ── Graded outcome (supplied by caller before graph invocation) ───────────
    # Set to True when the student submitted a numeric / multi-choice answer.
    has_graded_outcome: bool
    attempt_source: str              # ledger source: chat | practice | assess | onboarding | study
    student_answer: Optional[str]
    is_correct: Optional[bool]
    error_type: Optional[str]          # conceptual | procedural | calculation | misread
    time_taken_sec: Optional[int]

    # ── Diagnostic output ────────────────────────────────────────────────────
    trigger_quiz: bool   # True when struggle_streak >= STRUGGLE_STREAK_QUIZ_TRIGGER
    mistake_brief: Optional[str]
    remediation_flashcards: List[Dict[str, Any]]
    score_summary: Optional[str]

    # ── Sandbox execute (Phase 3) ─────────────────────────────────────────────
    sandbox_action: Optional[str]
    sandbox_card: Optional[Dict[str, Any]]

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
