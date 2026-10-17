"""Unit tests for Phase 5 pedagogical policy driven by explicit problem state, not keywords."""

import pytest
from agents.learning_agent import _decide_move_and_reveal
from agents.runtime.toolbelt import ToolContext
from mcp_tools.learner_tools import TurnSignalsArgs, turn_signals


LESSON_SURFACE = {"classroom_surface": "lesson"}


def test_turn_5_first_attempt_no_reveal():
    """Turn 5 of session, but first attempt on a new problem must withhold the answer (reveal=0)."""
    state = {
        **LESSON_SURFACE,
        "interaction_count": 5,
        "problem_state": {
            "problem_key": "prob_friction_1",
            "attempts_on_problem": 0,
            "hints_given": 0,
            "student_asked_for_answer": 0,
        },
    }
    move, reveal = _decide_move_and_reveal(
        "A block slides down an incline of angle 30 degrees. How do I find its acceleration if friction coefficient is 0.2?",
        state,
    )
    assert reveal == 0
    assert "probe" in move.lower() or "withhold" in move.lower() or "first turn" in move.lower()


def test_what_does_the_answer_mean_no_escalation():
    """'What does the answer mean?' contains the word 'answer' but must NOT escalate reveal level."""
    state = {
        **LESSON_SURFACE,
        "interaction_count": 2,
        "problem_state": {
            "problem_key": "prob_escape_1",
            "attempts_on_problem": 0,
            "hints_given": 0,
            "student_asked_for_answer": 0,
        },
    }
    move, reveal = _decide_move_and_reveal(
        "What does the answer 11.2 km/s for escape velocity actually mean physically?",
        state,
    )
    assert reveal == 0
    assert "full solution" not in move.lower()


def test_explicit_answer_requests_escalate_ladder():
    """One request for answer escalates to sticking point (reveal=3), two requests to full solution (reveal=4)."""
    # 1st request
    state_1 = {
        **LESSON_SURFACE,
        "problem_state": {
            "problem_key": "prob_disk_1",
            "attempts_on_problem": 1,
            "hints_given": 1,
            "student_asked_for_answer": 1,
        },
    }
    move_1, reveal_1 = _decide_move_and_reveal("Please tell me the answer directly", state_1)
    assert reveal_1 == 3
    assert "sticking point" in move_1.lower()

    # 2nd request
    state_2 = {
        **LESSON_SURFACE,
        "problem_state": {
            "problem_key": "prob_disk_1",
            "attempts_on_problem": 2,
            "hints_given": 1,
            "student_asked_for_answer": 2,
        },
    }
    move_2, reveal_2 = _decide_move_and_reveal("I really need the full answer right now", state_2)
    assert reveal_2 == 4
    assert "full solution" in move_2.lower()


def test_reveal_ladder_by_attempts_on_problem():
    """Attempts on the current problem systematically advance through the reveal ladder."""
    for attempts, expected_reveal in [(0, 0), (1, 1), (2, 2), (3, 3), (4, 3)]:
        state = {
            **LESSON_SURFACE,
            "problem_state": {
                "problem_key": "prob_123",
                "attempts_on_problem": attempts,
                "hints_given": 0,
                "student_asked_for_answer": 0,
            },
        }
        _, reveal = _decide_move_and_reveal("working on problem", state)
        assert reveal == expected_reveal


def test_options_request_gives_choices():
    """Structured student_requests_options sets reveal=2 with choices move."""
    state = {
        **LESSON_SURFACE,
        "student_requests_options": True,
        "problem_state": {
            "problem_key": "prob_mcq_1",
            "attempts_on_problem": 1,
            "hints_given": 0,
            "student_asked_for_answer": 0,
        },
    }
    move, reveal = _decide_move_and_reveal("options please", state)
    assert reveal == 2
    assert "A, B, C, D" in move


def test_turn_signals_tool_updates_problem_state():
    """turn_signals tool updates problem_state and resets counters on a new problem_key."""
    ctx = ToolContext(
        user_id="user_1",
        session_id="sess_1",
        state={
            "problem_state": {
                "problem_key": "prob_old",
                "attempts_on_problem": 2,
                "hints_given": 1,
                "student_asked_for_answer": 1,
            }
        },
    )

    # 1. New problem signal resets counters
    res = turn_signals(TurnSignalsArgs(problem_key="prob_new"), ctx)
    assert res["ok"] is True
    p_state = res["problem_state"]
    assert p_state["problem_key"] == "prob_new"
    assert p_state["attempts_on_problem"] == 0
    assert p_state["hints_given"] == 0
    assert p_state["student_asked_for_answer"] == 0

    # 2. Student asks for solution
    res2 = turn_signals(TurnSignalsArgs(student_requests_solution=True), ctx)
    assert res2["problem_state"]["student_asked_for_answer"] == 1
