"""Grading contract: answer shapes, evidence, and persistence status."""

from services.answer_key import grade_free_answer, grade_served_question, parse_number
from services.grading import display_percent, stored_score, student_message, update_proficiency, GradeResult


def test_complete_numbers_and_rejected_prefixes():
    assert parse_number(".5") == 0.5
    assert parse_number("1/2") == 0.5
    assert parse_number("-4") == -4
    assert parse_number("3e-3") == 0.003
    assert parse_number("5 x 10^3") == 5000
    assert parse_number("12abc") is None
    assert parse_number("1.2 or 9") is None
    assert parse_number("not 5 but 10") is None


def test_invalid_labels_are_not_dropped():
    question = {"structured": {"answer": {"kind": "letters", "letters": "A"}}}
    mixed = grade_served_question(question, choice=["A", "Z"])
    assert mixed["grade_status"] == "invalid_submission"
    assert mixed["correct"] is None
    styles = grade_served_question(question, choice=["A", "2"])
    assert styles["grade_status"] == "invalid_submission"
    exact = grade_served_question({"structured": {"answer": {"kind": "letters", "letters": "AC"}}}, choice=["1", "3"])
    assert exact["correct"] is True


def test_malformed_structured_key_does_not_use_solution_text():
    question = {
        "solution": "Answer: A",
        "structured": {"answer": {"kind": "mystery", "value": 1}},
    }
    decision = grade_free_answer(question, "A")
    assert decision["grade_status"] == "ungradable"
    assert decision["correct"] is None


def test_long_worked_solution_is_ungradable():
    decision = grade_free_answer(
        {"solution": "Integrate both sides, then the speed is 4 m/s."},
        "B",
    )
    assert decision["grade_status"] == "ungradable"


def test_zero_score_stays_zero_and_cold_start_is_not_a_percent():
    assert stored_score(0) == 0.0
    assert stored_score(0.0) == 0.0
    assert display_percent(0.0) == 0
    assert stored_score(None) is None
    assert stored_score(float("nan")) is None


def test_slow_correct_can_reduce_a_high_score():
    assert round(update_proficiency(0.5, True, None, 0, 0), 4) == 0.65
    assert round(update_proficiency(0.5, False, None, 0, 0), 4) == 0.35
    assert round(update_proficiency(0.5, True, 30, 10, 2), 4) == 0.56
    assert round(update_proficiency(0.9, True, 30, 10, 2), 4) == 0.84
    assert round(update_proficiency(0.0, False, None, 0, 0), 4) == 0.0
    assert update_proficiency(0.9, True, -5, 10, 2) == update_proficiency(0.9, True, None, 10, 2)


def test_self_report_text_is_not_a_verified_grade():
    result = GradeResult(
        grade_status="self_reported",
        evidence_source="student_self_report",
        persistence_status="committed",
        is_correct=True,
        attempt_id="a",
        proficiency_before=0.5,
        proficiency_after=0.65,
        proficiency_pct=65,
    )
    text = student_message(result)
    assert "You marked this correct" in text
    assert result.graded is False
    assert result.recorded is True


def test_checked_but_unsaved_does_not_show_a_score_change():
    result = GradeResult(
        grade_status="correct",
        evidence_source="answer_key",
        persistence_status="failed",
        is_correct=True,
        failure_reason="persistence_unavailable",
        proficiency_before=0.5,
        proficiency_after=0.65,
        proficiency_pct=65,
    )
    assert result.proficiency_pct is None
    assert "not saved" in student_message(result).lower() or "could not be saved" in student_message(result).lower()
    assert result.graded is True
    assert result.recorded is False
