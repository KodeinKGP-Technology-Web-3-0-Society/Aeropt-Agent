"""Summary failures must leave the completed tutor reply intact."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents import context_manager


@pytest.mark.parametrize("failure_stage", ["read", "model"])
def test_summary_failure_logs_and_returns_without_writing(monkeypatch, caplog, failure_stage):
    store = SimpleNamespace(
        read_unsummarized_turns=Mock(return_value=[{
            "turn_id": "turn-1", "role": "user", "content": "Explain torque.",
        }]),
        read_session_summary=Mock(return_value=""),
        update_session_summary=Mock(),
        mark_turns_summarized=Mock(),
    )
    completion = Mock(return_value=("A summary", "test-model"))
    failing_call = store.read_unsummarized_turns if failure_stage == "read" else completion
    failing_call.side_effect = RuntimeError("summary unavailable")
    monkeypatch.setattr(context_manager, "db", store)
    monkeypatch.setattr(context_manager, "complete_text", completion)

    with caplog.at_level(logging.WARNING, logger="jee_tutor.context_manager"):
        assert context_manager.context_manager_node({"session_id": "session-1"}) == {}

    assert "Context manager failed; the reply still stands: summary unavailable" in caplog.text
    store.update_session_summary.assert_not_called()
    store.mark_turns_summarized.assert_not_called()
