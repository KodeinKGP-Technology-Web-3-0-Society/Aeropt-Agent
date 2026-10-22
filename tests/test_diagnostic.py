from unittest.mock import patch
from agents.diagnostic import diagnostic_node

@patch("services.grading.db")
def test_diagnostic_self_report_does_not_invent_a_root_cause(mock_db):
    mock_db.commit_graded_attempt.return_value = {
        "status": "committed",
        "attempt_id": "attempt-1",
        "grade_status": "self_reported",
        "evidence_source": "student_self_report",
        "is_correct": False,
        "proficiency_before": 0.5,
        "proficiency_after": 0.35,
        "struggle_streak": 2,
        "was_cold_start": False,
    }
    state = {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "session_id": "00000000-0000-0000-0000-000000000002",
        "primary_concept_id": "concept_A",
        "has_graded_outcome": True,
        "evidence_source": "student_self_report",
        "question_instance_id": "q-1",
        "graded_content_id": "item-1",
        "is_correct": False,
        "error_type": "conceptual",
        "time_taken_sec": 30,
        "student_answer": "0",
        "prereq_concept_states": [
            {"concept_id": "concept_B", "proficiency_score": 0.2, "hop_count": 1}
        ],
    }
    res = diagnostic_node(state)
    assert res.get("error") is None
    assert res["attempt_recorded"] is True
    assert "concept_B" not in res["response"]
    request = mock_db.commit_graded_attempt.call_args.args[0]
    assert request["evidence_source"] == "student_self_report"
    assert request["grade_status"] == "self_reported"
    assert request["error_type"] == "conceptual"
    assert "root_cause" not in request
