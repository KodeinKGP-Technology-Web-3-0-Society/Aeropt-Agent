"""General chat history: list by kind, rename, soft delete, ownership."""

from __future__ import annotations

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
    assert client.delete("/api/v1/chat/sessions/s1", params={"user_id": "u1"}).status_code == 200
    assert client.get("/api/v1/chat/sessions/s1", params={"user_id": "u1"}).status_code == 404
    assert client.get("/api/v1/chat/sessions", params={"user_id": "u1"}).json()["sessions"] == []
