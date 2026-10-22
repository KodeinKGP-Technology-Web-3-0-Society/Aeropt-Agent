"""Concept resolution allows a DB reconnect while respecting the live turn cap."""

from unittest.mock import Mock, patch

from agents.runtime.budget import TurnBudget
from agents.runtime.toolbelt import ToolContext, Toolbelt
from mcp_tools.curriculum_tools import resolve_concept


def test_concept_resolution_can_wait_for_a_database_reconnect():
    # Postgres allows ten seconds to establish its pool. The old inherited
    # eight-second dispatch deadline could abandon that connection prematurely.
    result = {"in_scope": True, "concept_id": "newtons_2nd_law"}
    future = Mock()

    def finish_after_reconnect(timeout):
        if timeout < 10:
            raise TimeoutError("connection is still being established")
        return result

    future.result.side_effect = finish_after_reconnect
    ctx = ToolContext(user_id="u", session_id="s")
    with patch("agents.runtime.toolbelt._TOOL_POOL.submit", return_value=future):
        observed = Toolbelt([resolve_concept.tool_spec], ctx).call("resolve_concept", {"query": "Newton's laws"})
    assert observed.ok
    assert observed.data == result


def test_concept_resolution_never_waits_past_the_remaining_turn_allowance():
    budget = TurnBudget.from_duration(1)
    ctx = ToolContext(user_id="u", session_id="s", budget=budget)
    future = Mock()
    future.result.return_value = {"in_scope": False}
    with patch("agents.runtime.toolbelt._TOOL_POOL.submit", return_value=future):
        Toolbelt([resolve_concept.tool_spec], ctx).call("resolve_concept", {"query": "Newton's laws"})
    assert 0 < future.result.call_args.kwargs["timeout"] <= 1
