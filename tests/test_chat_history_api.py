"""General chat history: list by kind, rename, soft delete, ownership."""

from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

import api_server


class _FakeDB:
    def __init__(self):
        self.sessions = {
            "s1": {"session_id": "s1", "user_id": "u1", "kind": "chat", "title": None, "deleted": False},
            "s2": {"session_id": "s2", "user_id": "u2", "kind": "chat", "title": None, "deleted": False},
        }
        self.listed_kind = "unset"

    def read_session(self, sid):
        return self.sessions.get(sid)

    def is_session_deleted(self, sid):
        return self.sessions[sid]["deleted"]

    def list_user_sessions(self, user_id, limit=20, kind=None):
        self.listed_kind = kind
        return [s for s in self.sessions.values() if s["user_id"] == user_id and not s["deleted"]]

    def first_user_turn_previews(self, ids):
        return {"s1": "what is torque?"}

    def update_session_title(self, sid, title):
        self.sessions[sid]["title"] = title

    def delete_session(self, sid):
        self.sessions[sid]["deleted"] = True


def _client(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(api_server, "db", fake)
    invalidated = []

    def _invalidate(session_id):
        invalidated.append(session_id)

    monkeypatch.setattr("agents.memory_agent.invalidate_session", _invalidate)
    fake.invalidated = invalidated
    return TestClient(api_server.app), fake


def test_list_passes_kind_and_returns_title(monkeypatch):
    client, fake = _client(monkeypatch)
    fake.sessions["s1"]["title"] = "Torque doubts"
    body = client.get("/api/v1/chat/sessions", params={"user_id": "u1", "kind": "chat"}).json()
    assert fake.listed_kind == "chat"
    assert body["sessions"][0]["title"] == "Torque doubts"
    assert body["sessions"][0]["preview"] == "what is torque?"


def test_rename_trims_and_empty_clears(monkeypatch):
    client, fake = _client(monkeypatch)
    r = client.patch("/api/v1/chat/sessions/s1", json={"user_id": "u1", "title": "  Rotational   motion "})
    assert r.json()["title"] == "Rotational motion"
    client.patch("/api/v1/chat/sessions/s1", json={"user_id": "u1", "title": "   "})
    assert fake.sessions["s1"]["title"] is None


def test_cannot_touch_someone_elses_chat(monkeypatch):
    client, fake = _client(monkeypatch)
    assert client.patch("/api/v1/chat/sessions/s2", json={"user_id": "u1", "title": "x"}).status_code == 404
    assert client.delete("/api/v1/chat/sessions/s2", params={"user_id": "u1"}).status_code == 404
    assert fake.sessions["s2"]["deleted"] is False


def test_deleted_chat_is_gone(monkeypatch):
    client, fake = _client(monkeypatch)
    response = client.delete("/api/v1/chat/sessions/s1", params={"user_id": "u1"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "session_id": "s1"}
    assert fake.sessions["s1"]["deleted"] is True
    assert fake.invalidated == ["s1"]
    assert client.get("/api/v1/chat/sessions/s1", params={"user_id": "u1"}).status_code == 404
    assert client.get("/api/v1/chat/sessions", params={"user_id": "u1"}).json()["sessions"] == []


def test_delete_invalidates_only_that_session_and_survives_failure(monkeypatch):
    client, fake = _client(monkeypatch)
    order = []
    original = fake.delete_session

    def _delete(sid):
        order.append(("delete", sid))
        original(sid)

    def _invalidate(sid):
        order.append(("invalidate", sid))
        fake.invalidated.append(sid)

    fake.delete_session = _delete
    monkeypatch.setattr("agents.memory_agent.invalidate_session", _invalidate)
    assert client.delete("/api/v1/chat/sessions/s1", params={"user_id": "u1"}).status_code == 200
    assert order == [("delete", "s1"), ("invalidate", "s1")]
    assert "s2" not in fake.invalidated
    assert fake.sessions["s2"]["deleted"] is False

    def _boom(_sid):
        raise RuntimeError("episodes unavailable")

    fake.sessions["s1"]["deleted"] = False
    monkeypatch.setattr("agents.memory_agent.invalidate_session", _boom)
    response = client.delete("/api/v1/chat/sessions/s1", params={"user_id": "u1"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "session_id": "s1"}

    missed = client.delete("/api/v1/chat/sessions/s2", params={"user_id": "u1"})
    assert missed.status_code == 404
    assert fake.invalidated == ["s1"]

    source = inspect.getsource(api_server.delete_chat_session)
    assert "invalidate_session(session_id)" in source
    assert "db.invalidate_session" not in source
    assert "upsert_episode" not in source
    assert "close_pending_episode" not in source


class _UpdateChain:
    def __init__(self):
        self.payload = None
        self.filters = []

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        return self


def test_discard_stopped_turn_invalidates_that_assistant_index(monkeypatch):
    chain = _UpdateChain()
    fields = {}

    class _Client:
        def table(self, name):
            assert name == "chat_turns"
            return chain

    class _DB:
        client = _Client()

        def update_study_session_fields(self, study_session_id, payload):
            fields["id"] = study_session_id
            fields["payload"] = payload

    notices = []

    def _invalidate_turn(session_id, assistant_turn_index):
        notices.append(("turn", session_id, assistant_turn_index))

    def _invalidate_session(session_id):
        notices.append(("session", session_id))

    monkeypatch.setattr(api_server, "db", _DB())
    monkeypatch.setattr("agents.memory_agent.invalidate_turn", _invalidate_turn)
    monkeypatch.setattr("agents.memory_agent.invalidate_session", _invalidate_session)

    api_server._discard_stopped_turn(
        "study-1",
        "sess-9",
        {"interaction_count": 3},
        {"interaction_count": 3, "phase_turns": 4},
    )
    assert chain.payload == {"content": "", "presentation": {"stopped": True}}
    assert ("session_id", "sess-9") in chain.filters
    assert ("turn_index", 5) in chain.filters
    assert ("role", "user") in chain.filters
    assert notices == [("turn", "sess-9", 6)]
    assert fields == {"id": "study-1", "payload": {"phase_turns": 3}}

    def _boom(*_args):
        raise RuntimeError("episodes unavailable")

    monkeypatch.setattr("agents.memory_agent.invalidate_turn", _boom)
    api_server._discard_stopped_turn(
        "study-1",
        "sess-9",
        {"interaction_count": 2},
        {"phase_turns": 1},
    )
    assert ("turn_index", 3) in chain.filters
    assert fields["payload"] == {"phase_turns": 0}
    assert all(item[0] != "session" for item in notices)

    source = inspect.getsource(api_server._discard_stopped_turn)
    assert "invalidate_turn(session_id, interaction * 2)" in source
    assert "invalidate_session" not in source
    assert "db.invalidate_turn" not in source
