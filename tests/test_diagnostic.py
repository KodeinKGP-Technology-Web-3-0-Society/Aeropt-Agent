from unittest.mock import patch
from agents.diagnostic import diagnostic_node

@patch("agents.diagnostic.db")
def test_diagnostic_incorrect_with_weak_prereq(mock_db):
    mock_db.read_user_concept_state.return_value = {
        "proficiency_score": 0.5,
        "avg_time_seconds": 60,
        "attempts": 1,
        "correct": 0,
        "struggle_streak": 1,
        "flagged_weak": False
    }
    
    state = {
        "user_id": "test_user",
        "session_id": "test_session",
        "primary_concept_id": "concept_A",
        "has_graded_outcome": True,
        "is_correct": False,
        "error_type": "conceptual",
        "time_taken_sec": 30,
        "student_answer": "42",
        "prereq_concept_states": [
            {"concept_id": "concept_B", "proficiency_score": 0.2, "hop_count": 1} # Weak prereq
        ]
    }
    
    # We also have to mock _groq completion, but we only need to bypass the presentation generation
    # Actually, diagnostic_node generates remediation flashcards. Let's patch _groq in diagnostic
    with patch("agents.diagnostic.groq_complete", return_value=("[]", "llama3")):
        res = diagnostic_node(state)
        
    assert res.get("error") is None
    
    # Verify insert_user_attempt was called with root_cause_concept_id="concept_B"
    mock_db.insert_user_attempt.assert_called_once_with(
        user_id="test_user",
        session_id="test_session",
        concept_id="concept_A",
        content_id=None,
        is_correct=False,
        error_type="conceptual",
        time_taken_sec=30,
        student_answer="42",
        root_cause_concept_id="concept_B",
        # learning ledger (migration 012): where it came from and the score change
        source="chat",
        proficiency_before=0.5,
        proficiency_after=0.35,
        difficulty=None,
        study_session_id=None,
    )
