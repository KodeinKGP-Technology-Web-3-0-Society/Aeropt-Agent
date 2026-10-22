"""The registered suggestion tool consumes the follow-up service's list contract."""

from importlib import import_module
from unittest.mock import patch

from agents.runtime.toolbelt import ToolContext, Toolbelt
from mcp_tools.learner_tools import suggest_next_material

suggestions_module = import_module("mcp_tools.generate_suggestions")

def test_suggestions_are_returned_and_staged_for_the_next_turn():
    ctx = ToolContext(user_id="u1", session_id="s1", state={"primary_concept_id": "c1"})
    suggestion = {"content_id": "th_1", "title": "Torque", "content_type": "theory", "difficulty": 2}
    belt = Toolbelt([suggest_next_material.tool_spec], ctx)
    with patch.object(suggestions_module, "generate_suggestions", return_value=[suggestion]):
        result = belt.call("suggest_next_material", {})
    assert result.ok
    assert result.data["suggestions"] == [suggestion]
    assert ctx.updates["follow_up_suggestions"] == [suggestion]


def test_empty_suggestion_list_is_a_valid_result():
    ctx = ToolContext(user_id="u1", session_id="s1", state={"primary_concept_id": "c1"})
    belt = Toolbelt([suggest_next_material.tool_spec], ctx)
    with patch.object(suggestions_module, "generate_suggestions", return_value=[]):
        result = belt.call("suggest_next_material", {})
    assert result.ok
    assert result.data == {"suggestions": []}
