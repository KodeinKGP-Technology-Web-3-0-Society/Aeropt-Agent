"""Tests for Phase 3 context diet: result shaping, per-surface tools, compacting, and context builder."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest
from pydantic import BaseModel

import agents.learning_agent as la
from agents.runtime import context
from agents.runtime.loop import _compact_past_tool_messages
from agents.runtime.toolbelt import (
    SURFACE_TOOL_SETS,
    ToolContext,
    ToolResult,
    ToolSpec,
    Toolbelt,
    registry,
    surface_for_state,
)
from utils.skills import load_skill


def test_surface_tool_sets_under_fifteen_and_fifteen_k_chars():
    """Phase 3.2 & 3.3 invariant: every surface has < 15 tools and <= 15k chars total schema."""
    la._import_tools()
    import mcp_tools.study_tools  # noqa: F401

    all_specs = registry()

    for surface, tool_names in SURFACE_TOOL_SETS.items():
        assert len(tool_names) < 15, f"Surface {surface} has {len(tool_names)} tools (must be < 15)"
        schemas = [all_specs[name].schema() for name in tool_names if name in all_specs]
        total_chars = len(json.dumps(schemas))
        assert total_chars <= 15000, f"Surface {surface} schemas total {total_chars} chars (must be <= 15k)"


def test_surface_for_state_routing():
    """Verify state-based surface inference without keyword matching."""
    assert surface_for_state({}) == "chat"
    assert surface_for_state({"classroom_surface": "lesson"}) == "classroom_lesson"
    assert surface_for_state({"classroom_surface": "doubt"}) == "classroom_doubt"
    assert surface_for_state({"classroom_surface": "assess"}) == "classroom_assess"
    assert surface_for_state({"study_session_id": "s1", "study_phase": "explore"}) == "study_learn"
    assert surface_for_state({"study_session_id": "s1", "study_phase": "practice"}) == "study_practice"
    # The Study Room stores the phase as current_phase, not study_phase.
    assert surface_for_state({"study_session_id": "s1", "current_phase": "learn"}) == "study_learn"
    assert surface_for_state({"study_session_id": "s1", "current_phase": "examples"}) == "study_learn"
    assert surface_for_state({"study_session_id": "s1", "current_phase": "practice"}) == "study_practice"
    assert surface_for_state({"is_coach": True}) == "coach"


def test_study_room_keeps_the_tools_the_teacher_is_told_to_call():
    """Learn must be able to set an outline, and practice must be able to serve a question."""
    la._import_tools()
    import mcp_tools.study_tools  # noqa: F401

    learn = Toolbelt(
        registry().values(),
        ToolContext(user_id="u1", session_id="s1", state={"study_session_id": "ss", "current_phase": "learn"}),
    )
    assert "set_lesson_plan" in learn.names
    assert "serve_practice_question" in learn.names

    practice = Toolbelt(
        registry().values(),
        ToolContext(user_id="u1", session_id="s1", state={"study_session_id": "ss", "current_phase": "practice"}),
    )
    assert "serve_practice_question" in practice.names
    assert "grade_practice_answer" in practice.names


def test_toolbelt_surface_filtering():
    """Toolbelt initializes with surface filter when initialized with registry values."""
    la._import_tools()
    ctx = ToolContext(user_id="u1", session_id="s1", state={"classroom_surface": "doubt"})
    belt = Toolbelt(registry().values(), ctx)
    assert len(belt.names) < 15
    assert "solve_symbolically" in belt
    assert "resolve_concept" in belt
    # Study room phase transition should not be in classroom doubt
    assert "transition_phase" not in belt


def test_tool_result_shaping():
    """Phase 3.1: ToolResult shapes chunks and adds open_material note when over budget."""
    chunks = [
        {"content_id": f"c{i}", "title": f"Chunk {i}", "text": "Physics concept text " * 50}
        for i in range(10)
    ]
    raw_payload = {"ok": True, "chunks": chunks, "concept_id": "torque"}
    result = ToolResult(ok=True, data=raw_payload)

    rendered = result.to_json(max_chars=2000)
    parsed = json.loads(rendered)

    assert parsed["ok"] is True
    assert len(parsed["data"]["chunks"]) <= 2
    assert "open_material" in parsed["data"]["more_available"]
    assert len(rendered) <= 2000


def test_compact_past_tool_messages():
    """Phase 3.1: Earlier tool messages are compacted on later steps."""
    chunks = [
        {"content_id": "c_rot_1", "text": "Detailed rotation theory..."},
        {"content_id": "c_rot_2", "text": "Another detailed chunk..."},
    ]
    retrieval_content = json.dumps({"ok": True, "data": {"concept_id": "torque", "chunks": chunks}})

    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "tell me about torque"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "t1", "function": {"name": "retrieve_content"}}]},
        {"role": "tool", "tool_call_id": "t1", "name": "retrieve_content", "content": retrieval_content},
        # Assistant uses chunk c_rot_1 and calls another tool
        {"role": "assistant", "content": "According to c_rot_1, torque is r x F.", "tool_calls": [{"id": "t2", "function": {"name": "solve_symbolically"}}]},
        {"role": "tool", "tool_call_id": "t2", "name": "solve_symbolically", "content": json.dumps({"ok": True, "data": {"latex": "\\tau = I \\alpha"}})},
    ]

    compacted = _compact_past_tool_messages(messages)
    # The first tool message (t1) appeared before the assistant message and should be compacted
    t1_msg = [m for m in compacted if m.get("tool_call_id") == "t1"][0]
    t1_data = json.loads(t1_msg["content"])["data"]
    assert t1_data.get("compacted") is True
    assert any(c.get("content_id") == "c_rot_1" for c in t1_data["cited_chunks"])

    # The most recent tool message (t2) has not yet been answered by an assistant, so it remains uncompacted
    t2_msg = [m for m in compacted if m.get("tool_call_id") == "t2"][0]
    t2_data = json.loads(t2_msg["content"])["data"]
    assert t2_data.get("compacted") is not True


def test_context_builder_caching_and_budget_trimming(monkeypatch):
    """Phase 3.4 & 3.5: Sections priority trimming and cross-room / digest caching."""
    context.clear_context_cache()

    fetch_counts = {"progress": 0, "cross_room": 0}

    def fake_progress(uid):
        fetch_counts["progress"] += 1
        return "Mastered: kinematics. Weak: rotation." * 10

    def fake_cross(uid, sid):
        fetch_counts["cross_room"] += 1
        return "Just completed 5 practice questions in Study Room." * 10

    monkeypatch.setattr("services.progress_report.progress_digest", fake_progress)
    monkeypatch.setattr("services.cross_room.recent_activity", fake_cross)

    state = {
        "user_id": "u_test",
        "session_id": "s_test",
        "target_exam": "JEE_ADVANCED",
        "interaction_count": 3,
        "session_summary": "Discussed friction earlier." * 5,
        "episode_context": "Previous episode on incline plane." * 5,
    }

    # First call: fetches progress and cross_room
    sec1 = context.build(state, stable_system_prompt="Static Tutor Prompt", budget_chars=5000)
    assert fetch_counts["progress"] == 1
    assert fetch_counts["cross_room"] == 1
    assert "Discussed friction earlier" in sec1.dynamic_context_text

    # Second call for same user and session: uses cached values
    sec2 = context.build(state, stable_system_prompt="Static Tutor Prompt", budget_chars=5000)
    assert fetch_counts["progress"] == 1
    assert fetch_counts["cross_room"] == 1

    # Call with very tight budget: trims cross_room and progress_digest first
    sec_tight = context.build(state, stable_system_prompt="Static Tutor Prompt", budget_chars=200)
    # Core target exam remains
    assert "JEE_ADVANCED" in sec_tight.dynamic_context_text
    assert len(sec_tight.dynamic_context_text) < len(sec1.dynamic_context_text)


def test_cache_friendly_skill_split():
    """Phase 3.4: learning_agent.yaml contains stable instructions separate from dynamic template."""
    skill = load_skill("learning_agent")
    assert "stable" in skill
    assert "dynamic" in skill
    assert "{{target_exam}}" not in skill["stable"]
    assert "You are this student's tutor" in skill["stable"]
    assert "{{target_exam}}" in skill["dynamic"]


def test_refusal_filtering_in_recent_turns():
    """Ensure previous assistant refusal messages are stripped from recent_turns."""
    state = {
        "user_id": "u_test",
        "session_id": "s_test",
        "query": "explain me all kind of reactions in organic chemistry with diagrams",
        "recent_turns": [
            {
                "role": "user",
                "content": "explain me all kind of reactions in organic chemistry with diagrams",
            },
            {
                "role": "assistant",
                "content": "I understand you're curious about organic chemistry reactions, but as your physics tutor for JEE, my expertise and syllabus coverage are strictly in physics. I cannot provide organic chemistry mechanisms or reaction schemes.",
            },
        ],
    }
    sec = context.build(state, stable_system_prompt="Static Tutor Prompt")
    # Both the assistant refusal and its triggering user turn should be filtered out
    assert len(sec.recent_turns) == 0

