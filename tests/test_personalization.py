"""Session bootstrap: the deterministic setup that runs before the agent.

Originally this covered the Orchestrator's turn-1 crash (it called a
`read_prior_structured_memory` method that no longer existed, and the broad
`except Exception` swallowed it into a generic error every first turn). The
Orchestrator is gone, but that regression is worth keeping a test on: the same
class of bug — a DB read that fails silently and degrades the whole turn — is
exactly what the bootstrap's own try/except could hide again.
"""

from __future__ import annotations

from unittest.mock import patch

from agents.session_bootstrap import session_bootstrap_node

_STATE = {
    "user_id": "test_user",
    "session_id": "session-123",
    "query": "Explain Newton's Laws",
}


def _mock_db(mock_db, *, prior_summary=None):
    mock_db.read_user.return_value = {
        "target_exam": "JEE_MAIN",
        "preferred_depth": "standard",
    }
    mock_db.read_session.return_value = {
        "session_id": "session-123",
        "interaction_count": 0,
    }
    mock_db.read_user_learning_profile.return_value = {"weak_chapters": []}
    mock_db.read_recent_chat_turns.return_value = []
    mock_db.read_session_summary.return_value = None
    mock_db.read_prior_session_summary.return_value = prior_summary
    return mock_db


@patch("agents.session_bootstrap.db")
def test_turn_one_loads_the_previous_session_summary(mock_db):
    _mock_db(mock_db, prior_summary="Prior summary.")
    result = session_bootstrap_node({**_STATE})
    assert result.get("error") is None
    assert result.get("prior_session_summary") == "Prior summary."
    assert result.get("interaction_count") == 1


@patch("agents.session_bootstrap.db")
def test_turn_one_with_no_previous_session(mock_db):
    _mock_db(mock_db, prior_summary=None)
    result = session_bootstrap_node({**_STATE})
    assert result.get("error") is None
    assert result.get("prior_session_summary") is None


@patch("agents.session_bootstrap.db")
def test_a_failing_summary_read_does_not_fail_the_turn(mock_db):
    """A summary is a nice-to-have. Losing it must not cost the student their turn."""
    _mock_db(mock_db)
    mock_db.read_session_summary.side_effect = RuntimeError("supabase down")
    result = session_bootstrap_node({**_STATE})
    assert result.get("error") is None
    assert result.get("interaction_count") == 1


@patch("agents.session_bootstrap.db")
def test_unknown_user_is_reported_not_swallowed(mock_db):
    _mock_db(mock_db)
    mock_db.read_user.return_value = None
    result = session_bootstrap_node({**_STATE})
    assert "not found" in (result.get("error") or "")


@patch("agents.session_bootstrap.db")
def test_the_student_turn_is_recorded_before_the_agent_runs(mock_db):
    """A turn that fails mid-flight must still leave the conversation intact."""
    _mock_db(mock_db)
    session_bootstrap_node({**_STATE})
    mock_db.insert_chat_turn.assert_called_once()
    kwargs = mock_db.insert_chat_turn.call_args.kwargs
    assert kwargs["role"] == "user"
    assert kwargs["content"] == "Explain Newton's Laws"


@patch("agents.session_bootstrap.db")
def test_summariser_is_flagged_on_the_fifth_turn(mock_db):
    import config

    _mock_db(mock_db)
    n = config.CONTEXT_MANAGER_EVERY_N
    mock_db.read_session.return_value = {
        "session_id": "session-123",
        "interaction_count": n - 1,
    }
    result = session_bootstrap_node({**_STATE})
    assert result["interaction_count"] == n
    assert result["run_context_manager"] is True
