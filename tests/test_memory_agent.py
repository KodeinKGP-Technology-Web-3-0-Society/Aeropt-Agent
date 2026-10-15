"""Phase 1 Memory Agent. No network, no live model, no graph wiring."""

from __future__ import annotations

import inspect
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import agents.memory_agent as memory_agent
from agents.memory_agent import (
    apply_delayed_feedback,
    build_episode,
    format_episode_context,
    invalidate_session,
    invalidate_turn,
    memory_commit_node,
    memory_recall_node,
    retrieve_relevant_episodes,
)


class FakeDB:
    def __init__(self):
        self.calls: List[str] = []
        self.upserts: List[Dict[str, Any]] = []
        self.closed: List[Dict[str, Any]] = []
        self.candidates: List[Dict[str, Any]] = []
        self.fail = False

    def fetch_episode_candidates(self, user_id, concept_id=None):
        self.calls.append(f"fetch:{user_id}:{concept_id}")
        if self.fail:
            raise RuntimeError("db down")
        return list(self.candidates)

    def upsert_episode(self, row):
        self.calls.append("upsert")
        if self.fail:
            raise RuntimeError("db down")
        self.upserts.append(dict(row))
        return dict(row)

    def close_pending_episode(self, **kwargs):
        self.calls.append("close")
        if self.fail:
            raise RuntimeError("db down")
        self.closed.append(kwargs)
        return "ep-closed"

    def invalidate_session(self, session_id):
        self.calls.append(f"invalidate-session:{session_id}")
        if self.fail:
            raise RuntimeError("db down")

    def invalidate_turn(self, session_id, assistant_turn_index):
        self.calls.append(f"invalidate-turn:{session_id}:{assistant_turn_index}")
        if self.fail:
            raise RuntimeError("db down")


def _patch_db(monkeypatch, fake: Optional[FakeDB] = None) -> FakeDB:
    fake = fake or FakeDB()
    monkeypatch.setattr(memory_agent, "db", fake)
    return fake


def _patch_embed(monkeypatch, vector=None, model="test-model", fail=False):
    def encode_one(_text):
        if fail:
            raise RuntimeError("embed down")
        return list(vector if vector is not None else [1.0, 0.0])

    def model_name():
        if fail:
            raise RuntimeError("embed down")
        return model

    monkeypatch.setattr("retrieval.embedder.encode_one", encode_one)
    monkeypatch.setattr("retrieval.embedder.model_name", model_name)


def _state(**overrides) -> Dict[str, Any]:
    base = {
        "user_id": "user-a",
        "session_id": "sess-1",
        "interaction_count": 3,
        "query": "How should I handle torque signs?",
        "response": "Keep the right-hand rule and redraw the force.",
        "primary_concept_id": "torque",
        "agent_trace_path": ["tool:retrieve_content", "handoff:worked_solution"],
        "checker_verdict": "pass",
    }
    base.update(overrides)
    return base


def _episode(**overrides) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    base = {
        "episode_id": "ep",
        "user_id": "user-a",
        "concept_id": "torque",
        "task_type": "doubt",
        "observed_outcome": "taught",
        "feedback_status": "pending",
        "feedback_outcome": None,
        "checker_verdict": None,
        "actions": ["tool:search"],
        "query_excerpt": "torque signs",
        "created_at": now,
        "embedding": [1.0, 0.0],
        "embed_model": "test-model",
    }
    base.update(overrides)
    return base


def test_turn_index_and_skip_rules():
    row = build_episode(_state(), kind="chat")
    assert row is not None
    assert row["assistant_turn_index"] == 6

    assert build_episode(_state(response="  "), kind="chat") is None
    assert build_episode(_state(response=""), kind="chat") is None
    assert build_episode(_state(user_id=""), kind="chat") is None
    assert build_episode(_state(session_id=""), kind="chat") is None
    assert build_episode(_state(interaction_count=None), kind="chat") is None
    assert build_episode(_state(interaction_count=0), kind="chat") is None
    assert build_episode(_state(interaction_count="nope"), kind="chat") is None
    assert build_episode(_state(cancelled=True), kind="study") is None

    physics, fallback = memory_agent._skip_replies()
    assert physics and fallback
    assert build_episode(_state(response=physics), kind="chat") is None
    assert build_episode(_state(response=fallback), kind="chat") is None
    assert build_episode(_state(response="Well done!"), kind="graded") is not None


def test_chat_graded_and_study_row_shapes():
    chat = build_episode(_state(), kind="chat")
    assert chat is not None
    assert chat["surface"] == "chat"
    assert chat["route"] == "agent_loop"
    assert chat["task_type"] == "doubt"
    assert chat["observed_outcome"] == "taught"
    assert "feedback_status" not in chat
    assert "feedback_outcome" not in chat
    assert chat["expert_used"] == "worked_solution"

    theory = build_episode(_state(content_type_requested="theory"), kind="chat")
    assert theory is not None and theory["task_type"] == "explain"
    lesson = build_episode(_state(classroom_surface="lesson"), kind="chat")
    assert lesson is not None and lesson["surface"] == "lesson" and lesson["task_type"] == "explain"
    assess = build_episode(_state(classroom_surface="assess"), kind="chat")
    assert assess is not None and assess["surface"] == "assess" and assess["task_type"] == "practice"

    graded = build_episode(
        _state(has_graded_outcome=True, is_correct=False, attempt_id="attempt-1", agent_trace_path=[]),
        kind="graded",
    )
    assert graded is not None
    assert graded["surface"] == "graded"
    assert graded["route"] == "graded_fast_path"
    assert graded["task_type"] == "grade"
    assert graded["observed_outcome"] == "incorrect"
    assert graded["feedback_status"] == "next_attempt"
    assert graded["feedback_outcome"] == "incorrect"
    assert graded["actions"] == ["node:diagnostic", "node:follow_up"]
    assert graded["attempt_id"] == "attempt-1"

    study = build_episode(_state(study_session_id="study-1", agent_trace_path=[], tools_used=["serve_practice_question"]), kind="study")
    assert study is not None
    assert study["surface"] == "study"
    assert study["route"] == "teacher"
    assert study["task_type"] == "study_phase"
    assert study["observed_outcome"] == "taught"
    assert study["actions"] == ["serve_practice_question"]
    assert "feedback_status" not in study

    closed = build_episode(
        _state(
            study_session_id="study-1",
            last_grade={
                "is_correct": True,
                "attempt_id": "attempt-9",
                "content_id": "item-1",
                "concept_id": "torque",
                "proficiency_before": 0.4,
                "proficiency_after": 0.55,
            },
        ),
        kind="study",
    )
    assert closed is not None
    assert closed["task_type"] == "grade"
    assert closed["observed_outcome"] == "correct"
    assert closed["feedback_status"] == "next_attempt"
    assert closed["feedback_outcome"] == "correct"
    assert closed["attempt_id"] == "attempt-9"
    assert closed["content_id"] == "item-1"
    assert closed["concept_id"] == "torque"
    assert closed["proficiency_before"] == 0.4
    assert closed["proficiency_after"] == 0.55


def test_concept_priority_and_clips():
    row = build_episode(
        _state(primary_concept_id="", roadmap_concept_id="roadmap-torque", concept_id="study-torque"),
        kind="chat",
    )
    assert row is not None and row["concept_id"] == "roadmap-torque"
    from_grade = build_episode(
        _state(
            primary_concept_id="",
            roadmap_concept_id="",
            concept_id="",
            last_grade={"is_correct": False, "concept_id": "from-grade"},
        ),
        kind="study",
    )
    assert from_grade is not None and from_grade["concept_id"] == "from-grade"

    long_query = "q" * 500
    clipped = build_episode(_state(query=long_query), kind="chat")
    assert clipped is not None
    assert len(clipped["query_excerpt"]) == 300
    assert len(clipped["retrieval_text"]) <= 800

    huge_actions = [f"tool:{'x' * 80}" for _ in range(20)]
    wide = build_episode(_state(agent_trace_path=huge_actions), kind="chat")
    assert wide is not None
    assert len(wide["actions"]) == 12
    assert len(wide["retrieval_text"]) <= 800
    assert wide["retrieval_text"].startswith("surface=chat concept=torque task=doubt")


def test_commit_embeds_and_closes_before_upsert(monkeypatch):
    fake = _patch_db(monkeypatch)
    _patch_embed(monkeypatch, vector=[0.2, 0.8], model="test-model")
    state = _state(has_graded_outcome=True, is_correct=True, attempt_id="attempt-2")
    assert memory_commit_node(state) == {}
    assert fake.calls[:2] == ["close", "upsert"]
    assert fake.closed[0]["concept_id"] == "torque"
    assert fake.closed[0]["exclude_turn_index"] == 6
    assert fake.closed[0]["is_correct"] is True
    saved = fake.upserts[0]
    assert saved["embedding"] == [0.2, 0.8]
    assert saved["embed_model"] == "test-model"
    assert saved["feedback_status"] == "next_attempt"
    assert "error" not in memory_commit_node(state)


def test_embed_failure_still_upserts_and_missing_concept_skips_feedback(monkeypatch):
    fake = _patch_db(monkeypatch)
    _patch_embed(monkeypatch, fail=True)
    assert memory_commit_node(_state()) == {}
    assert fake.calls == ["upsert"]
    assert fake.upserts[0]["embedding"] is None
    assert fake.upserts[0]["embed_model"] is None

    fake.calls.clear()
    fake.upserts.clear()
    graded = _state(
        has_graded_outcome=True,
        is_correct=False,
        primary_concept_id="",
        roadmap_concept_id="",
        concept_id="",
    )
    assert memory_commit_node(graded) == {}
    assert "close" not in fake.calls
    assert fake.calls == ["upsert"]


def test_retrieval_scoring(monkeypatch):
    fake = _patch_db(monkeypatch)
    _patch_embed(monkeypatch, vector=[1.0, 0.0], model="test-model")
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=1)).isoformat()
    old = (now - timedelta(days=400)).isoformat()

    same = _episode(episode_id="same", concept_id="torque", created_at=old)
    other = _episode(episode_id="other", concept_id="friction", created_at=old)
    fake.candidates = [other, same]
    ranked = retrieve_relevant_episodes("user-a", "torque signs", "torque")
    assert [row["episode_id"] for row in ranked][0] == "same"

    fake.candidates = [
        _episode(episode_id="old", created_at=old),
        _episode(episode_id="recent", created_at=recent),
    ]
    ranked = retrieve_relevant_episodes("user-a", "torque", "torque")
    assert [row["episode_id"] for row in ranked][0] == "recent"

    fake.candidates = [
        _episode(episode_id="ok", observed_outcome="correct", feedback_status="next_attempt", feedback_outcome="correct"),
        _episode(episode_id="bad", observed_outcome="incorrect", feedback_status="next_attempt", feedback_outcome="incorrect"),
    ]
    ranked = retrieve_relevant_episodes("user-a", "torque", "torque")
    assert [row["episode_id"] for row in ranked][0] == "bad"
    assert memory_agent._quality_score(fake.candidates[1]) > memory_agent._quality_score(fake.candidates[0])

    fake.candidates = [
        _episode(episode_id="wrong-model", concept_id="friction", embed_model="other-model", embedding=[0.0, 1.0])
    ]
    ranked = retrieve_relevant_episodes("user-a", "torque", "torque")
    assert [row["episode_id"] for row in ranked] == ["wrong-model"]

    fake.candidates = [
        _episode(episode_id="short", concept_id="friction", embedding=[1.0])
    ]
    ranked = retrieve_relevant_episodes("user-a", "torque", "torque")
    assert [row["episode_id"] for row in ranked] == ["short"]

    fake.candidates = [
        _episode(episode_id="orthogonal", concept_id="friction", embedding=[0.0, 1.0])
    ]
    assert retrieve_relevant_episodes("user-a", "torque", "torque") == []

    fake.candidates = [
        _episode(episode_id="kept", concept_id="torque", embedding=[0.0, 1.0])
    ]
    assert [row["episode_id"] for row in retrieve_relevant_episodes("user-a", "torque", "torque")] == ["kept"]

    fake.candidates = [
        _episode(episode_id=f"e{i}", embedding=[1.0, i / 10.0], concept_id="torque")
        for i in range(5)
    ]
    ranked = retrieve_relevant_episodes("user-a", "torque", "torque")
    assert len(ranked) == 3

    fake.candidates = []
    assert retrieve_relevant_episodes("user-a", "torque", None) == []

    _patch_embed(monkeypatch, fail=True)
    fake.candidates = [_episode(episode_id="still", concept_id="torque")]
    assert retrieve_relevant_episodes("user-a", "torque", "torque") == [fake.candidates[0]]


def test_recency_formula_is_bounded():
    now = datetime.now(timezone.utc).isoformat()
    assert math.isclose(memory_agent._recency(now), 1.0, abs_tol=0.01)
    old = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    assert memory_agent._recency(old) < memory_agent._recency(now)


def test_format_and_recall_node(monkeypatch):
    assert format_episode_context([]) == ""
    block = format_episode_context([
        _episode(created_at="2026-10-01T00:00:00+00:00", feedback_outcome="incorrect", observed_outcome="taught"),
    ])
    assert block.startswith("Past experiences (this student, not course material):")
    assert "incorrect" in block
    assert "torque" in block
    assert len(block) <= 900
    assert all(len(line) <= 280 for line in block.splitlines())

    huge = format_episode_context([
        _episode(episode_id=f"e{i}", query_excerpt="q" * 300, actions=["tool:" + ("x" * 40)] * 12)
        for i in range(3)
    ])
    assert len(huge) <= 900
    assert all(len(line) <= 280 for line in huge.splitlines())
    assert "episode_id" not in huge

    fake = _patch_db(monkeypatch)
    _patch_embed(monkeypatch)
    fake.candidates = []
    assert memory_recall_node(_state()) == {}

    fake.candidates = [_episode()]
    recalled = memory_recall_node(_state())
    assert recalled["episode_context"].startswith("Past experiences (this student, not course material):")
    assert "error" not in recalled


def test_public_functions_fail_open(monkeypatch):
    fake = _patch_db(monkeypatch)
    fake.fail = True
    _patch_embed(monkeypatch, fail=True)
    assert memory_recall_node(_state()) == {}
    assert memory_commit_node(_state(has_graded_outcome=True, is_correct=False)) == {}
    assert retrieve_relevant_episodes("user-a", "torque", "torque") == []
    assert format_episode_context(None) == ""  # type: ignore[arg-type]
    apply_delayed_feedback(_state(has_graded_outcome=True, is_correct=False))
    invalidate_session("sess-1")
    invalidate_turn("sess-1", 6)
    assert build_episode(None, kind="chat") is None  # type: ignore[arg-type]


def test_module_does_not_call_a_model_or_the_graphs():
    source = inspect.getsource(memory_agent)
    assert "OpenAI(" not in source
    assert "genai" not in source
    assert "run_agent_loop" not in source
    assert "api_server" not in source
    assert "graph.workflow" not in source
    assert "graph.study_workflow" not in source
    assert "skills/" not in source
