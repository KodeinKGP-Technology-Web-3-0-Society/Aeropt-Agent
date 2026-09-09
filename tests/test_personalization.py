import pytest
from unittest.mock import patch, MagicMock
from graph.workflow import route_after_orchestrator
from db.supabase_client import SupabaseClient

@patch('agents.orchestrator.db')
def test_task_a_turn_1_regression(mock_db):
    """
    S1 & S2: Test that turn 1 of a new session doesn't crash trying to load
    prior_structured_memory, for both users with and without prior sessions.
    """
    
    # Mock user and concept resolution
    mock_db.read_user.return_value = {"target_exam": "JEE_MAIN"}
    mock_db.read_session.return_value = {"session_id": "session-123"}
    
    # Mock text summary fetch (no crash on read_prior_structured_memory)
    # Scenario S1: >= 1 prior session
    mock_db.read_prior_session_summary.return_value = "Prior summary."
    
    state = {
        "user_id": "test_user",
        "session_id": "session-123",
        "query": "Explain Newton's Laws",
        "interaction_count": 1,
        "chat_history": []
    }
    
    # Actually run the orchestrator node
    from agents.orchestrator import orchestrator_node
    # We must patch _groq to not actually make an LLM call inside orchestrator_node,
    # or patch intent routing. Orchestrator only calls LLM if intent is missing.
    # Wait, orchestrator doesn't call LLM if intent is provided or if _prefetch_next_query is called
    # Let's just mock groq_complete
    with patch("agents.orchestrator.groq_complete", return_value=("{}", "llama3")):
        # Mock focus_chapters
        mock_db.read_user_learning_profile.return_value = {"weak_chapters": []}
        
        # Test S1
        result = orchestrator_node({**state, "intent": "doubt"})
        assert result.get("error") is None
        assert result.get("prior_session_summary") == "Prior summary."
        
        # Test S2: Zero prior sessions
        mock_db.read_prior_session_summary.return_value = None
        result2 = orchestrator_node({**state, "intent": "doubt"})
        assert result2.get("error") is None
        assert result2.get("prior_session_summary") is None
