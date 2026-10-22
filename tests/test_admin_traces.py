"""Admin trace API: closed without a token, readable with one."""

from __future__ import annotations

import copy

from fastapi.testclient import TestClient

import api_server
from services import admin_traces


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_a, **_k): return self
    def eq(self, col, val):
        self.rows = [r for r in self.rows if r.get(col) == val]
        return self
    def in_(self, col, vals):
        self.rows = [r for r in self.rows if r.get(col) in vals]
        return self
    def ilike(self, *_a, **_k): return self
    def lt(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def limit(self, n):
        self.rows = self.rows[:n]
        return self
    def execute(self):
        return type("R", (), {"data": self.rows})()


class _FakeDB:
    def __init__(self):
        self.rows = [{
            "trace_id": "t1", "user_id": "u1", "session_id": "s1", "route": "agent_loop",
            "tool_calls": 1, "handoffs": 0, "checker_verdict": "pass", "latency_ms": 900,
            "model_used": "m", "created_at": "2026-10-18T10:00:00+00:00",
            "steps": [{"kind": "tool", "name": "retrieve_content", "ok": True}],
            "metrics": {"query": "what is torque?", "llm_calls": 2, "stop_reason": "answered"},
        }]
        self.client = self

        self.rows.append({
            "trace_id": "t0", "user_id": "u1", "session_id": "s1", "route": "agent_loop",
            "tool_calls": 0, "handoffs": 0, "checker_verdict": "pass", "latency_ms": 500,
            "model_used": "m", "created_at": "2026-10-18T09:00:30+00:00", "steps": [], "metrics": {},
        })
        # chat_turns.created_at has no zone; turn_index does not match the trace's.
        self.turns = [
            {"session_id": "s1", "role": "user", "content": "define work", "created_at": "2026-10-18T09:00:00"},
            {"session_id": "s1", "role": "assistant", "content": "Work is F.d.", "created_at": "2026-10-18T09:00:35"},
            {"session_id": "s1", "role": "user", "content": "what is torque?", "created_at": "2026-10-18T09:59:00"},
            {"session_id": "s1", "role": "assistant", "content": "Torque is r x F.", "created_at": "2026-10-18T10:00:05"},
        ]

    def table(self, name):
        return _Query(copy.deepcopy(self.turns if name == "chat_turns" else self.rows))


def _client(monkeypatch, token="secret"):
    monkeypatch.setattr(admin_traces, "db", _FakeDB())
    if token:
        monkeypatch.setenv("AGENT_ADMIN_TOKEN", token)
    else:
        monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    return TestClient(api_server.app)


def test_closed_when_token_unset(monkeypatch):
    c = _client(monkeypatch, token=None)
    assert c.get("/api/v1/admin/traces").status_code == 404
    assert c.get("/admin/traces").status_code == 404


def test_rejects_wrong_token(monkeypatch):
    c = _client(monkeypatch)
    assert c.get("/api/v1/admin/traces").status_code == 401
    assert c.get("/api/v1/admin/traces", headers={"X-Admin-Token": "nope"}).status_code == 401


def test_list_detail_overview(monkeypatch):
    c = _client(monkeypatch)
    h = {"X-Admin-Token": "secret"}
    assert c.get("/api/v1/admin/traces", headers=h).json()["traces"][0]["trace_id"] == "t1"
    d = c.get("/api/v1/admin/traces/t1", headers=h).json()
    assert d["reply"] == "Torque is r x F."
    assert d["trace"]["steps"][0]["name"] == "retrieve_content"
    assert c.get("/api/v1/admin/traces/missing", headers=h).status_code == 404
    overview = c.get("/api/v1/admin/overview", headers=h).json()
    assert overview["window"] == 2 and overview["with_telemetry"] == 1
    assert overview["avg_llm_calls"] == 2


def test_old_trace_recovers_message_by_time(monkeypatch):
    c = _client(monkeypatch)
    h = {"X-Admin-Token": "secret"}
    listed = {t["trace_id"]: t for t in c.get("/api/v1/admin/traces", headers=h).json()["traces"]}
    assert listed["t0"]["metrics"]["query"] == "define work"
    d = c.get("/api/v1/admin/traces/t0", headers=h).json()
    assert d["reply"] == "Work is F.d."
    assert [t["this_turn"] for t in d["conversation"]] == [True, False, False, False]


def test_page_served(monkeypatch):
    assert "Agent traces" in _client(monkeypatch).get("/admin/traces").text
