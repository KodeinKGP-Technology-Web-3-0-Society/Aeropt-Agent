"""Memory Agent. No network and no live model.

Phase 2 checks graph routing and that speakers only append episode_context.
"""

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


def test_null_embedding_is_zero_and_skipped_cosine_is_kept(monkeypatch):
    fake = _patch_db(monkeypatch)
    _patch_embed(monkeypatch, vector=[1.0, 0.0], model="test-model")

    fake.candidates = [
        _episode(episode_id="null-other", concept_id="friction", embedding=None, embed_model=None)
    ]
    assert retrieve_relevant_episodes("user-a", "torque", "torque") == []

    fake.candidates = [
        _episode(episode_id="null-same", concept_id="torque", embedding=None, embed_model=None)
    ]
    assert [row["episode_id"] for row in retrieve_relevant_episodes("user-a", "torque", "torque")] == ["null-same"]

    fake.candidates = [
        _episode(episode_id="other-model", concept_id="friction", embed_model="other-model", embedding=[0.0, 1.0])
    ]
    assert [row["episode_id"] for row in retrieve_relevant_episodes("user-a", "torque", "torque")] == ["other-model"]

    fake.candidates = [
        _episode(episode_id="short", concept_id="friction", embedding=[1.0])
    ]
    assert [row["episode_id"] for row in retrieve_relevant_episodes("user-a", "torque", "torque")] == ["short"]

    _patch_embed(monkeypatch, fail=True)
    fake.candidates = [
        _episode(episode_id="uncompared", concept_id="friction", embedding=[0.0, 1.0]),
        _episode(episode_id="also", concept_id="optics", embedding=None),
    ]
    found = {row["episode_id"] for row in retrieve_relevant_episodes("user-a", "torque", "torque")}
    assert found == {"uncompared", "also"}


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


def test_delayed_feedback_refreshes_retrieval_and_keeps_the_old_vector_on_embed_failure(monkeypatch):
    from db.supabase_client import SupabaseClient
    from tests.test_episodic_memory import FakeSupabase, _row, _seed, _stored

    fake = FakeSupabase()
    _seed(
        fake,
        _row(
            episode_id="ep-old",
            embedding=[0.2, 0.3],
            embed_model="old-model",
            retrieval_text="feedback=pending",
            surface="chat",
            task_type="doubt",
            observed_outcome="taught",
            actions=["tool:search"],
            query_excerpt="sign of torque",
        ),
    )
    client = SupabaseClient.__new__(SupabaseClient)
    client.client = fake
    client._memory_enabled = None
    client._episodes_enabled = True
    client._agent_used_is_narrow = False
    client.redis = None
    monkeypatch.setattr(memory_agent, "db", client)
    _patch_embed(monkeypatch, vector=[0.4, 0.6], model="refreshed-model")

    state = _state(has_graded_outcome=True, is_correct=False, attempt_id="attempt-a", interaction_count=4)
    apply_delayed_feedback(state)
    stored = _stored(fake, "ep-old")
    assert stored["feedback_status"] == "next_attempt"
    assert stored["feedback_outcome"] == "incorrect"
    assert stored["feedback_attempt_id"] == "attempt-a"
    assert "feedback=incorrect" in stored["retrieval_text"]
    assert stored["embedding"] == [0.4, 0.6]
    assert stored["embed_model"] == "refreshed-model"

    other = _row(
        episode_id="ep-next",
        session_id="sess-2",
        assistant_turn_index=4,
        created_at="2026-01-01T00:00:00+00:00",
        embedding=[9.0, 9.0],
        embed_model="old-model",
        retrieval_text="feedback=pending",
    )
    _seed(fake, other)
    apply_delayed_feedback(state)
    assert _stored(fake, "ep-next")["feedback_status"] == "pending"
    assert _stored(fake, "ep-old")["feedback_outcome"] == "incorrect"

    _patch_embed(monkeypatch, fail=True)
    apply_delayed_feedback(_state(
        has_graded_outcome=True,
        is_correct=True,
        attempt_id="attempt-b",
        interaction_count=5,
    ))
    nxt = _stored(fake, "ep-next")
    assert nxt["feedback_status"] == "next_attempt"
    assert nxt["feedback_outcome"] == "correct"
    assert nxt["embedding"] == [9.0, 9.0]
    assert nxt["embed_model"] == "old-model"
    assert "feedback=correct" in nxt["retrieval_text"]

    def _boom(*_args, **_kwargs):
        raise RuntimeError("retrieval write failed")

    monkeypatch.setattr(client, "update_episode_retrieval", _boom)
    apply_delayed_feedback(_state(
        has_graded_outcome=True,
        is_correct=False,
        attempt_id="attempt-b",
        interaction_count=5,
    ))
    assert _stored(fake, "ep-next")["feedback_status"] == "next_attempt"


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


_EPISODE_BLOCK = (
    "Past experiences (this student, not course material):\n"
    "- 2026-10-01 torque: chat, taught."
)


def _guarded_fn(app, name):
    """The function a guarded graph node actually calls."""
    fn = app.builder.nodes[name].runnable.func
    if fn.__closure__:
        return fn.__closure__[0].cell_contents
    return fn


def _targets(app, source: str):
    return {edge.target for edge in app.get_graph().edges if edge.source == source}


def _reachable(app, start: str):
    outgoing = {}
    for edge in app.get_graph().edges:
        outgoing.setdefault(edge.source, set()).add(edge.target)
    seen = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(outgoing.get(node, ()))
    return seen


def _route_fn(app, node: str):
    spec = next(iter(app.builder.branches[node].values()))
    return spec.path.func


def _patch_progress(monkeypatch):
    monkeypatch.setattr(
        "services.progress_report.progress_digest",
        lambda user_id, tz_name=None: "weak: torque",
    )
    monkeypatch.setattr(
        "services.cross_room.recent_activity",
        lambda user_id, session_id="": "Just now in Study Room: torque",
    )


def test_states_accept_episode_context_and_grade_metadata():
    from models.state import TutorState
    from models.study_state import StudyRoomState

    tutor: TutorState = {"episode_context": _EPISODE_BLOCK, "attempt_id": "attempt-1"}
    study: StudyRoomState = {
        "episode_context": _EPISODE_BLOCK,
        "last_grade": {
            "is_correct": False,
            "content_id": "item-1",
            "concept_id": "torque",
            "attempt_id": "attempt-9",
            "proficiency_before": 0.4,
            "proficiency_after": 0.33,
        },
    }
    assert tutor["episode_context"] == _EPISODE_BLOCK
    assert tutor["attempt_id"] == "attempt-1"
    assert study["episode_context"] == _EPISODE_BLOCK
    assert study["last_grade"]["attempt_id"] == "attempt-9"

    def _ann(cls, name: str) -> str:
        value = cls.__annotations__[name]
        return getattr(value, "__forward_arg__", value)

    assert _ann(TutorState, "episode_context") == "Optional[str]"
    assert _ann(TutorState, "attempt_id") == "Optional[str]"
    assert _ann(StudyRoomState, "episode_context") == "Optional[str]"
    assert "Dict" in _ann(StudyRoomState, "last_grade")
    for banned in ("current_episode_id", "recalled_episodes", "memory_action", "proficiency_before", "proficiency_after"):
        assert banned not in TutorState.__annotations__
    assert "attempt_id" not in StudyRoomState.__annotations__


def test_tutor_graph_recalls_only_free_text_and_commits_both_paths(monkeypatch):
    import traceback

    import openai

    real_openai = openai.OpenAI

    def _spy(*args, **kwargs):
        for line in traceback.format_stack():
            if "agents/memory_agent.py" in line.replace("\\", "/"):
                raise AssertionError("Memory Agent constructed a model client")
        return real_openai(*args, **kwargs)

    monkeypatch.setattr(openai, "OpenAI", _spy)

    from graph.workflow import (
        build_graph,
        is_structured_input,
        route_after_turn,
        route_from_bootstrap,
    )
    from services.turn_service import _CARRY_KEYS

    fake = _patch_db(monkeypatch)
    app = build_graph()

    assert {"memory_recall", "memory_commit"} <= set(app.get_graph().nodes)
    assert _guarded_fn(app, "memory_recall") is memory_recall_node
    assert _guarded_fn(app, "memory_commit") is memory_commit_node
    assert fake.calls == []

    assert is_structured_input({"has_graded_outcome": True}) is True
    assert is_structured_input({"query": "correct"}) is False
    assert route_from_bootstrap({"error": "no such user"}) == "end"
    assert route_from_bootstrap({"has_graded_outcome": True}) == "graded_fast_path"
    assert route_from_bootstrap({"query": "explain torque"}) == "memory_recall"
    assert _route_fn(app, "bootstrap") is route_from_bootstrap

    assert _targets(app, "bootstrap") == {"graded_fast_path", "memory_recall", "__end__"}
    assert _targets(app, "memory_recall") == {"learning_agent"}
    assert _targets(app, "learning_agent") == {"memory_commit"}
    assert _targets(app, "graded_fast_path") == {"memory_commit"}
    assert "learning_agent" not in _reachable(app, "graded_fast_path")
    assert "memory_recall" not in _reachable(app, "graded_fast_path")

    assert _targets(app, "memory_commit") == {"context_manager", "__end__"}
    assert _route_fn(app, "memory_commit") is route_after_turn
    assert route_after_turn({"run_context_manager": True}) == "context_manager"
    assert route_after_turn({"run_context_manager": False}) == "end"
    assert route_after_turn({"run_context_manager": True, "error": "boom"}) == "end"
    assert "episode_context" not in _CARRY_KEYS
    assert "attempt_id" not in _CARRY_KEYS
    assert "last_grade" not in _CARRY_KEYS

    workflow_source = inspect.getsource(__import__("graph.workflow", fromlist=["build_graph"]))
    for name in ("fetch_episode_candidates", "upsert_episode", "close_pending_episode"):
        assert name not in workflow_source


def test_study_graph_commits_after_a_live_teacher_turn(monkeypatch):
    from graph.study_workflow import (
        build_study_graph,
        route_after_commit,
        route_after_evaluation,
        route_after_teacher,
    )

    fake = _patch_db(monkeypatch)
    app = build_study_graph()

    assert {"memory_recall", "memory_commit"} <= set(app.get_graph().nodes)
    assert _guarded_fn(app, "memory_recall") is memory_recall_node
    assert _guarded_fn(app, "memory_commit") is memory_commit_node
    assert fake.calls == []

    assert _targets(app, "bootstrap") == {"memory_recall"}
    assert _targets(app, "memory_recall") == {"teacher_agent"}
    assert _targets(app, "teacher_agent") == {"memory_commit", "__end__"}
    assert "evaluation_agent" not in _targets(app, "teacher_agent")
    assert "context_manager" not in _targets(app, "teacher_agent")

    assert route_after_teacher({"cancelled": True, "phase_transition": "evaluation"}) == "end"
    assert route_after_teacher({"error": "boom", "run_context_manager": True}) == "end"
    assert route_after_teacher({"current_phase": "learn"}) == "memory_commit"
    assert _route_fn(app, "teacher_agent") is route_after_teacher

    assert _targets(app, "memory_commit") == {"evaluation_agent", "context_manager", "__end__"}
    assert _route_fn(app, "memory_commit") is route_after_commit
    assert route_after_commit({"phase_transition": "evaluation"}) == "evaluation"
    assert route_after_commit({"current_phase": "evaluation"}) == "evaluation"
    assert route_after_commit({"run_context_manager": True}) == "context_manager"
    assert route_after_commit({}) == "end"
    assert route_after_commit({"error": "boom", "run_context_manager": True}) == "end"
    # Evaluation stays downstream of commit, not a second episode path off the teacher.
    assert "memory_commit" in _reachable(app, "teacher_agent")
    assert _reachable(app, "memory_commit") >= {"evaluation_agent", "context_manager", "__end__"}

    assert _targets(app, "evaluation_agent") == {"context_manager", "__end__"}
    assert _route_fn(app, "evaluation_agent") is route_after_evaluation
    assert route_after_evaluation({"run_context_manager": True}) == "context_manager"
    assert route_after_evaluation({"error": "boom", "run_context_manager": True}) == "end"

    # Cancel and error leave the teacher node for END, so commit is not on that route.
    assert route_after_teacher({"cancelled": True}) != "memory_commit"
    assert route_after_teacher({"error": "boom"}) != "memory_commit"

    study_source = inspect.getsource(__import__("graph.study_workflow", fromlist=["build_study_graph"]))
    for name in ("fetch_episode_candidates", "upsert_episode", "close_pending_episode"):
        assert name not in study_source


def test_learning_agent_appends_episode_context_without_fetching(monkeypatch):
    _patch_progress(monkeypatch)
    from agents.learning_agent import _build_system_prompt

    memories = [{"kind": "preference", "fact": "likes diagrams", "confidence": 0.9}]
    base = {
        "user_id": "user-a",
        "session_id": "sess-1",
        "target_exam": "JEE_ADVANCED",
        "preferred_depth": "standard",
        "interaction_count": 2,
    }

    def prompt(state):
        return _build_system_prompt(state, memories, "Probe.", 0)

    empty = prompt(base)
    assert prompt({**base, "episode_context": ""}) == empty
    assert prompt({**base, "episode_context": "   "}) == empty
    assert prompt({**base, "episode_context": None}) == empty
    assert "likes diagrams" in empty
    assert "weak: torque" in empty
    assert "Progress so far" in empty
    assert "Just now in Study Room: torque" in empty

    filled = prompt({**base, "episode_context": _EPISODE_BLOCK})
    assert _EPISODE_BLOCK in filled
    assert filled.count(_EPISODE_BLOCK) == 1
    assert filled.replace("\n\n" + _EPISODE_BLOCK, "", 1) == empty

    source = inspect.getsource(__import__("agents.learning_agent", fromlist=["_build_system_prompt"]))
    for name in ("fetch_episode_candidates", "upsert_episode", "close_pending_episode"):
        assert name not in source


def test_teacher_agent_appends_episode_context_without_fetching(monkeypatch):
    _patch_progress(monkeypatch)
    from agents.runtime.loop import LoopResult
    from agents.teacher_agent import _learner_block, teacher_agent_node
    import agents.teacher_agent as teacher_agent

    snapshot = {"concept_name": "Torque"}
    empty = _learner_block(snapshot, "user-a", "sess-1")
    assert _learner_block(snapshot, "user-a", "sess-1", "") == empty
    assert _learner_block(snapshot, "user-a", "sess-1", "   ") == empty
    assert "Past experiences" not in empty
    assert "First time studying Torque" in empty
    assert "weak: torque" in empty
    assert "Just now in Study Room: torque" in empty

    filled = _learner_block(snapshot, "user-a", "sess-1", _EPISODE_BLOCK)
    assert _EPISODE_BLOCK in filled
    assert filled.count("Past experiences (this student, not course material):") == 1
    assert filled.replace("\n\n" + _EPISODE_BLOCK, "", 1) == empty

    captured = {}

    def _loop(**kwargs):
        captured["prompt"] = kwargs["system_prompt"]
        return LoopResult(text="", stop_reason="cancelled")

    monkeypatch.setattr(teacher_agent, "run_agent_loop", _loop)
    monkeypatch.setattr(teacher_agent.db, "read_recent_chat_turns", lambda *args, **kwargs: [])

    state = {
        "user_id": "user-a",
        "session_id": "sess-1",
        "query": "explain torque",
        "concept_id": "torque",
        "concept_name": "Torque",
        "learner_snapshot": snapshot,
        "current_phase": "learn",
        "study_mode": "learn",
        "episode_context": _EPISODE_BLOCK,
    }
    result = teacher_agent_node(state)
    assert result.get("cancelled") is True
    with_episode = captured["prompt"]
    assert _EPISODE_BLOCK in with_episode
    assert with_episode.count(_EPISODE_BLOCK) == 1

    captured.clear()
    bare = dict(state)
    bare.pop("episode_context")
    teacher_agent_node(bare)
    without = captured["prompt"]
    assert _EPISODE_BLOCK not in without
    assert with_episode.replace("\n\n" + _EPISODE_BLOCK, "", 1) == without

    source = inspect.getsource(teacher_agent)
    for name in ("fetch_episode_candidates", "upsert_episode", "close_pending_episode"):
        assert name not in source


def test_speaking_skills_explain_episode_context_without_a_new_token():
    from pathlib import Path

    from utils.skills import load_skill

    learning = load_skill("learning_agent")["system_prompt"]
    teacher = load_skill("teacher_agent")["system_prompt"]
    heading = "Past experiences (this student, not course material):"
    for prompt in (learning, teacher):
        assert heading in prompt
        assert "not course" in prompt
        assert "recite" in prompt
        assert "failed" in prompt
        assert "absent" in prompt
        assert "{{episode_context}}" not in prompt
    assert "tool results stay authoritative" in learning
    assert "phase rules" in teacher
    assert "open question" in teacher
    assert "evaluation and progression" in teacher
    assert not (Path("skills") / "memory_agent.yaml").exists()


def test_diagnostic_returns_attempt_id_without_closing(monkeypatch):
    import agents.diagnostic as diagnostic

    class _DiagDB:
        def __init__(self):
            self.calls = []

        def read_user_concept_state(self, user_id, concept_id):
            self.calls.append("read")
            return {
                "proficiency_score": 0.5,
                "avg_time_seconds": 60,
                "attempts": 1,
                "correct": 0,
                "struggle_streak": 0,
                "flagged_weak": False,
            }

        def update_user_concept_state(self, user_id, concept_id, fields):
            self.calls.append("update")
            self.written = fields

        def insert_user_attempt(self, **kwargs):
            self.calls.append("insert")
            return {"attempt_id": "attempt-77"}

        def insert_chat_turn(self, **kwargs):
            self.calls.append("chat")

        def close_pending_episode(self, **kwargs):
            self.calls.append("close")

    fake = _DiagDB()
    monkeypatch.setattr(diagnostic, "db", fake)
    result = diagnostic.diagnostic_node({
        "user_id": "user-a",
        "session_id": "sess-1",
        "primary_concept_id": "concept-a",
        "has_graded_outcome": True,
        "is_correct": True,
        "student_answer": "5",
    })
    assert result.get("error") is None
    assert result["attempt_id"] == "attempt-77"
    assert result["proficiency_before"] == 0.5
    assert result["proficiency_after"] == fake.written["proficiency_score"]
    assert "close" not in fake.calls
    source = inspect.getsource(diagnostic)
    assert "memory_agent" not in source
    assert "close_pending_episode" not in source

    _patch_embed(monkeypatch)
    episode_db = _patch_db(monkeypatch)
    state = _state(
        has_graded_outcome=True,
        is_correct=True,
        primary_concept_id="concept-a",
        attempt_id=result["attempt_id"],
        agent_trace_path=[],
    )
    assert memory_commit_node(state) == {}
    assert len(episode_db.closed) == 1
    assert episode_db.closed[0]["concept_id"] == "concept-a"
    assert episode_db.closed[0]["attempt_id"] == "attempt-77"
    assert episode_db.closed[0]["exclude_turn_index"] == 6

    other = _state(
        has_graded_outcome=True,
        is_correct=False,
        primary_concept_id="concept-b",
        attempt_id="attempt-b",
        agent_trace_path=[],
    )
    memory_commit_node(other)
    assert [row["concept_id"] for row in episode_db.closed] == ["concept-a", "concept-b"]
    assert episode_db.closed[1]["attempt_id"] == "attempt-b"
    assert all(row["concept_id"] != "concept-a" or row["attempt_id"] == "attempt-77" for row in episode_db.closed)


def test_study_grade_stages_last_grade_and_only_commit_closes(monkeypatch):
    import mcp_tools.study_tools as study_tools
    from agents.runtime.toolbelt import ToolContext
    from mcp_tools.study_tools import GradePracticeArgs, grade_practice_answer

    class _StudyDB:
        def __init__(self):
            self.calls = []
            self.attempt = {
                "attempt_id": "attempt-fb",
                "proficiency_before": 0.2,
                "proficiency_after": 0.15,
            }

        def insert_study_event(self, *args, **kwargs):
            self.calls.append("event")

        def update_study_session_stats(self, *args, **kwargs):
            self.calls.append("stats")

        def insert_user_attempt(self, **kwargs):
            self.calls.append("insert")
            return dict(self.attempt)

        def close_pending_episode(self, **kwargs):
            self.calls.append("close")

    store = _StudyDB()
    monkeypatch.setattr(study_tools, "db", store)

    def _ctx():
        return ToolContext(
            user_id="user-a",
            session_id="sess-1",
            state={
                "study_session_id": "study-1",
                "session_id": "sess-1",
                "concept_id": "torque",
                "current_question": {"content_id": "item-1", "difficulty": 2},
                "questions_served": 0,
                "questions_correct": 0,
                "difficulty_level": 1,
                "practice_streak": 0,
                "wrong_streak": 0,
            },
        )

    monkeypatch.setattr(
        "agents.diagnostic.diagnostic_node",
        lambda state: {
            "error": None,
            "attempt_id": "attempt-9",
            "proficiency_before": 0.4,
            "proficiency_after": 0.55,
        },
    )
    ctx = _ctx()
    grade_practice_answer(GradePracticeArgs(is_correct=True, student_answer="5"), ctx)
    assert "insert" not in store.calls
    assert "close" not in store.calls
    grade = ctx.updates["last_grade"]
    assert grade == {
        "is_correct": True,
        "content_id": "item-1",
        "concept_id": "torque",
        "attempt_id": "attempt-9",
        "proficiency_before": 0.4,
        "proficiency_after": 0.55,
    }

    _patch_embed(monkeypatch)
    episode_db = _patch_db(monkeypatch)
    committed = _state(
        study_session_id="study-1",
        response="The torque is r cross F.",
        last_grade=grade,
        agent_trace_path=[],
    )
    assert memory_commit_node(committed) == {}
    assert len(episode_db.closed) == 1
    assert episode_db.closed[0]["attempt_id"] == "attempt-9"
    assert episode_db.closed[0]["concept_id"] == "torque"
    assert episode_db.closed[0]["proficiency_before"] == 0.4
    assert episode_db.closed[0]["proficiency_after"] == 0.55
    saved = build_episode(committed, kind="study")
    assert saved is not None
    assert saved["feedback_status"] == "next_attempt"
    assert saved["attempt_id"] == "attempt-9"
    assert saved["feedback_attempt_id"] == "attempt-9"

    monkeypatch.setattr(
        "agents.diagnostic.diagnostic_node",
        lambda state: {"error": "No concept state"},
    )
    fallback_ctx = _ctx()
    grade_practice_answer(GradePracticeArgs(is_correct=False, student_answer="2"), fallback_ctx)
    assert store.calls.count("insert") == 1
    assert "close" not in store.calls
    assert fallback_ctx.updates["last_grade"]["attempt_id"] == "attempt-fb"
    assert fallback_ctx.updates["last_grade"]["proficiency_before"] == 0.2
    assert fallback_ctx.updates["last_grade"]["is_correct"] is False

    source = inspect.getsource(study_tools)
    assert "close_pending_episode" not in source
    assert "memory_commit" not in source
    assert "memory_agent" not in source
