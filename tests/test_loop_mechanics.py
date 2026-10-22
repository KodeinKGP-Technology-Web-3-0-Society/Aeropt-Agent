"""Tests for Phase 4 loop mechanics: per-tool timeout, circuit breaker, LLM-call cap, and parallel reads."""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import BaseModel, Field

from agents.runtime.budget import TurnBudget
from agents.runtime.loop import run_agent_loop
from agents.runtime.toolbelt import (
    SCOPE_READ,
    SCOPE_WRITE_PROGRESS,
    ToolContext,
    ToolResult,
    ToolSpec,
    Toolbelt,
)


class _Args(BaseModel):
    value: str = Field(default="x")


def _slow_tool(args: _Args, ctx: ToolContext):
    time.sleep(1.5)
    return {"done": True}


def _failing_tool(args: _Args, ctx: ToolContext):
    raise RuntimeError("unstable service error")


def _fast_read_1(args: _Args, ctx: ToolContext):
    time.sleep(0.05)
    return {"read": 1, "val": args.value}


def _fast_read_2(args: _Args, ctx: ToolContext):
    time.sleep(0.05)
    return {"read": 2, "val": args.value}


def _write_tool(args: _Args, ctx: ToolContext):
    return {"written": args.value}


SLOW = ToolSpec("slow", "Slow tool.", _Args, _slow_tool, frozenset({SCOPE_READ}), timeout_s=0.2)
FAILING = ToolSpec("failing", "Failing tool.", _Args, _failing_tool, frozenset({SCOPE_READ}))
READ1 = ToolSpec("read1", "Read 1.", _Args, _fast_read_1, frozenset({SCOPE_READ}))
READ2 = ToolSpec("read2", "Read 2.", _Args, _fast_read_2, frozenset({SCOPE_READ}))
WRITE = ToolSpec("write", "Write.", _Args, _write_tool, frozenset({SCOPE_WRITE_PROGRESS}))


def _msg(content=None, tool_calls=None):
    calls = None
    if tool_calls:
        calls = [
            SimpleNamespace(
                id=f"call_{i}",
                function=SimpleNamespace(name=name, arguments=args),
            )
            for i, (name, args) in enumerate(tool_calls)
        ]
    return SimpleNamespace(content=content, tool_calls=calls)


class LoopMechanicsTests(unittest.TestCase):
    def setUp(self):
        self.ctx = ToolContext(user_id="u1", session_id="s1")

    def test_per_tool_timeout(self):
        """Tool exceeding its timeout_s returns structured failure without hanging."""
        belt = Toolbelt([SLOW], self.ctx)
        t0 = time.time()
        result = belt.call("slow", '{"value": "test"}')
        duration = time.time() - t0

        self.assertFalse(result.ok)
        self.assertIn("timed out after 0s", result.error)
        self.assertLess(duration, 1.0)

    def test_circuit_breaker_trips_after_two_failures(self):
        """After two failures, tool is removed from schemas and third call rejected immediately."""
        belt = Toolbelt([FAILING, READ1], self.ctx)

        # Initially both in schemas
        schema_names = [s["function"]["name"] for s in belt.schemas()]
        self.assertIn("failing", schema_names)
        self.assertIn("read1", schema_names)

        # 1st failure
        res1 = belt.call("failing", "{}")
        self.assertFalse(res1.ok)
        self.assertEqual(belt._failures.get("failing"), 1)
        self.assertIn("failing", [s["function"]["name"] for s in belt.schemas()])

        # 2nd failure
        res2 = belt.call("failing", "{}")
        self.assertFalse(res2.ok)
        self.assertEqual(belt._failures.get("failing"), 2)

        # Now circuit breaker is tripped: excluded from schemas
        self.assertNotIn("failing", [s["function"]["name"] for s in belt.schemas()])

        # 3rd call rejected immediately
        res3 = belt.call("failing", "{}")
        self.assertFalse(res3.ok)
        self.assertIn("failed repeatedly this turn", res3.error)

    def test_llm_call_cap_triggers_forced_close(self):
        """When budget.max_llm_calls is exhausted, loop force-closes with stop_reason='call_cap'."""
        budget = TurnBudget(deadline=time.time() + 30.0, max_llm_calls=2)
        belt = Toolbelt([READ1], self.ctx)

        # Model tries to keep looping tool calls
        replies = [
            (_msg(tool_calls=[("read1", '{"value": "1"}')]), "test-model"),
            (_msg(tool_calls=[("read1", '{"value": "2"}')]), "test-model"),
            (_msg("closing answer"), "test-model"),
        ]

        def fake_chat(*args, **kwargs):
            budget.record_call()
            return replies.pop(0)

        with patch("agents.runtime.loop.chat_completion", side_effect=fake_chat):
            res = run_agent_loop(
                system_prompt="prompt",
                user_message="question",
                toolbelt=belt,
                budget=budget,
                model="test-model",
            )

        self.assertEqual(res.stop_reason, "call_cap")

    def test_parallel_reads_execution(self):
        """Multiple non-consequential read calls execute in parallel and preserve message order."""
        belt = Toolbelt([READ1, READ2], self.ctx)

        replies = [
            (
                _msg(tool_calls=[("read1", '{"value": "A"}'), ("read2", '{"value": "B"}')]),
                "test-model",
            ),
            (_msg("Done parallel reads."), "test-model"),
        ]

        t0 = time.time()
        with patch("agents.runtime.loop.chat_completion", side_effect=replies) as chat_mock:
            res = run_agent_loop(
                system_prompt="prompt",
                user_message="question",
                toolbelt=belt,
                model="test-model",
            )
        duration = time.time() - t0

        self.assertTrue(res.ok)
        self.assertEqual(res.tool_calls_made, ["read1", "read2"])

        # Tool messages sent to second call must preserve order
        second_call_messages = chat_mock.call_args_list[1].kwargs["messages"]
        tool_msgs = [m for m in second_call_messages if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 2)
        self.assertEqual(tool_msgs[0]["name"], "read1")
        self.assertEqual(tool_msgs[1]["name"], "read2")
        self.assertIn('"read": 1', tool_msgs[0]["content"])
        self.assertIn('"read": 2', tool_msgs[1]["content"])

    def test_explicit_max_tool_rounds(self):
        """Setting max_tool_rounds=1 forces tools=None (final_pass) after 1 round."""
        belt = Toolbelt([READ1], self.ctx)

        replies = [
            (_msg(tool_calls=[("read1", '{"value": "round1"}')]), "test-model"),
            (_msg("Final text after max tool rounds."), "test-model"),
        ]

        with patch("agents.runtime.loop.chat_completion", side_effect=replies) as chat_mock:
            res = run_agent_loop(
                system_prompt="prompt",
                user_message="question",
                toolbelt=belt,
                model="test-model",
                max_steps=6,
                max_tool_rounds=1,
            )

        self.assertTrue(res.ok)
        # The second call must have received tools=None because steps >= max_tool_rounds
        second_call_tools = chat_mock.call_args_list[1].kwargs.get("tools")
        self.assertIsNone(second_call_tools)

    def test_require_tool_sends_ungrounded_answer_back_once(self):
        """Answering without the required tool earns one nudge; the draft is discarded."""
        belt = Toolbelt([READ1], self.ctx)

        replies = [
            (_msg("Answer from memory."), "test-model"),
            (_msg(tool_calls=[("read1", '{"value": "q"}')]), "test-model"),
            (_msg("Grounded answer."), "test-model"),
        ]

        with patch("agents.runtime.loop.chat_completion", side_effect=replies):
            res = run_agent_loop(
                system_prompt="prompt",
                user_message="question",
                toolbelt=belt,
                model="test-model",
                require_tool="read1",
            )

        self.assertEqual(res.text, "Grounded answer.")
        self.assertEqual(res.tool_calls_made, ["read1"])
        contents = [str(m.get("content")) for m in res.messages]
        self.assertTrue(any("`read1`" in c for c in contents))
        self.assertNotIn("Answer from memory.", contents)

    def test_require_tool_nudges_only_once(self):
        """A model that still refuses after the nudge gets its answer through."""
        belt = Toolbelt([READ1], self.ctx)

        replies = [(_msg("Answer from memory."), "test-model"), (_msg("Still from memory."), "test-model")]

        with patch("agents.runtime.loop.chat_completion", side_effect=replies):
            res = run_agent_loop(
                system_prompt="prompt",
                user_message="question",
                toolbelt=belt,
                model="test-model",
                require_tool="read1",
            )

        self.assertEqual(res.text, "Still from memory.")
        self.assertEqual(res.stop_reason, "answered")


if __name__ == "__main__":
    unittest.main()
