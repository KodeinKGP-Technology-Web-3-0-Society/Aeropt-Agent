"""Session IDs must be owned and paired before summaries, carry or graph access."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import api_server


@pytest.mark.parametrize("session", [None, {"user_id": "other"}, {"user_id": "me", "deleted": True}])
def test_end_session_rejects_missing_foreign_or_deleted_session(monkeypatch, session):
    monkeypatch.setattr(api_server, "db", SimpleNamespace(
        read_session=lambda sid: session,
        is_session_deleted=lambda sid: session.get("deleted", False),
    ))
    flush, release, coach = Mock(), Mock(), Mock()
    monkeypatch.setattr(api_server, "_flush_session_summary", flush)
    monkeypatch.setattr(api_server, "release_carry", release)
    monkeypatch.setattr(api_server, "coach_user", coach)
    with pytest.raises(HTTPException) as exc:
        api_server.end_session(api_server.EndSessionRequest(user_id="me", session_id="chat"))
    assert exc.value.status_code == 404
    flush.assert_not_called()
    release.assert_not_called()
    coach.assert_not_called()


@pytest.mark.parametrize("chat_owner,paired", [("other", True), ("me", False)])
def test_study_message_rejects_foreign_or_unpaired_chat_before_carry(monkeypatch, chat_owner, paired):
    monkeypatch.setattr(api_server, "db", SimpleNamespace(
        read_study_session=lambda sid: {"user_id": "me", "agent_session_id": "chat" if paired else "different-chat"},
        read_session=lambda sid: {"user_id": chat_owner},
        is_session_deleted=lambda sid: False,
    ))
    carry = Mock()
    monkeypatch.setattr(api_server, "get_carry", carry)
    request = api_server.StudyMessageRequest(user_id="me", session_id="chat", study_session_id="study", query="hello")
    coroutine = api_server.study_message(request)
    try:
        with pytest.raises(HTTPException) as exc:
            coroutine.send(None)
        assert exc.value.status_code in {403, 404}
    finally:
        coroutine.close()
    carry.assert_not_called()
