"""The reasoning loop: budgets, recovery and forced closure.

The loop is what keeps an autonomous agent from being unbounded. Everything
here is a failure mode that would otherwise reach a student: a model that loops
forever, a model that ends a turn silently, a tool error that derails the whole
reply.

`chat_completion` is patched rather than the Groq client, because with a multi-key
pool configured the client argument is ignored in favour of a pooled one.
"""

from __future__ import annotations

import contextvars
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import BaseModel, Field

from agents.runtime.budget import TurnBudget, current_budget_var
from agents.runtime.loop import run_agent_loop
from agents.runtime.toolbelt import SCOPE_READ, ToolContext, ToolSpec, Toolbelt
from services import progress
from utils.llm import UsageMeter, usage_meter


class _Args(BaseModel):
    value: str = Field(default="x")


def _echo(args: _Args, ctx: ToolContext):
    return {"echoed": args.value}


def _boom(args: _Args, ctx: ToolContext):
    raise RuntimeError("tool exploded")


ECHO = ToolSpec("echo", "Echo a value.", _Args, _echo, frozenset({SCOPE_READ}))
BOOM = ToolSpec("boom", "Always fails.", _Args, _boom, frozenset({SCOPE_READ}))


def _msg(content=None, tool_calls=None):
    """A Groq-shaped assistant message."""
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


def _belt(specs=(ECHO,)):
    ctx = ToolContext(user_id="u1", session_id="s1")
    return Toolbelt(list(specs), ctx), ctx


def _run(belt, **kwargs):
    params = dict(
        system_prompt="You are a tutor.",
        user_message="help me",
        toolbelt=belt,
        model="test-model",
        label="test",
    )
    params.update(kwargs)
    return run_agent_loop(**params)


class HappyPathTests(unittest.TestCase):
    def test_a_direct_answer_costs_one_call_and_no_tools(self):
        with patch(
            "agents.runtime.loop.chat_completion",
            return_value=(_msg("Force equals mass times acceleration."), "test-model"),
        ) as chat:
            belt, ctx = _belt()
            result = _run(belt)
        self.assertTrue(result.ok)
        self.assertEqual(result.stop_reason, "answered")
        self.assertEqual(result.steps_taken, 1)
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(ctx.trace.tool_calls, 0)

    def test_a_tool_call_is_executed_and_fed_back(self):
        replies = [
            (_msg(tool_calls=[("echo", '{"value": "hello"}')]), "test-model"),
            (_msg("Here is your answer."), "test-model"),
        ]
        with patch("agents.runtime.loop.chat_completion", side_effect=replies) as chat:
            belt, ctx = _belt()
            result = _run(belt)

        self.assertEqual(result.text, "Here is your answer.")
        self.assertEqual(result.tool_calls_made, ["echo"])
        self.assertEqual(ctx.trace.path(), ["tool:echo"])

        # The tool's output must actually reach the second call.
        second_call_messages = chat.call_args_list[1].kwargs["messages"]
        roles = [m["role"] for m in second_call_messages]
        self.assertIn("tool", roles)
        tool_msg = next(m for m in second_call_messages if m["role"] == "tool")
        self.assertIn("hello", tool_msg["content"])
        self.assertEqual(tool_msg["tool_call_id"], "call_0")

    def test_parallel_tool_calls_all_run(self):
        replies = [
            (
                _msg(tool_calls=[("echo", '{"value": "a"}'), ("echo", '{"value": "b"}')]),
                "test-model",
            ),
            (_msg("Done."), "test-model"),
        ]
        with patch("agents.runtime.loop.chat_completion", side_effect=replies):
            belt, ctx = _belt()
            result = _run(belt)
        self.assertEqual(result.tool_calls_made, ["echo", "echo"])
        self.assertEqual(ctx.trace.tool_calls, 2)

    def test_parallel_reads_keep_turn_context_and_propagate_cancellation(self):
        """Both worker hops preserve one turn and keep local context writes apart."""
        barrier = threading.Barrier(2)
        cancelled = threading.Event()
        budget = TurnBudget.from_duration(10)
        meter = UsageMeter()
        marker = contextvars.ContextVar("parallel_read_marker", default="missing")
        events = []

        def read(args, ctx):
            inherited_marker = marker.get()
            marker.set(args.value)
            # Both reads must start before cancellation is signalled, otherwise
            # the toolbelt correctly refuses the second dispatch.
            barrier.wait(timeout=2)
            cancelled.set()
            progress.report("stage", f"read:{args.value}", "done")
            return {
                "turn_marker": inherited_marker,
                "worker_marker": marker.get(),
                "same_budget": current_budget_var.get() is budget,
                "same_usage_meter": usage_meter.get() is meter,
                "cancelled": progress.is_cancelled(),
            }

        spec = ToolSpec("read", "Read turn context.", _Args, read, frozenset({SCOPE_READ}))
        ctx = ToolContext(user_id="u1", session_id="s1", budget=budget)
        belt = Toolbelt([spec], ctx)
        marker_token = marker.set("turn-1")
        usage_token = usage_meter.set(meter)
        progress_token = progress.install(events.append)
        cancel_token = progress.install_cancel(cancelled)
        try:
            with patch(
                "agents.runtime.loop.chat_completion",
                return_value=(
                    _msg(tool_calls=[("read", '{"value": "a"}'), ("read", '{"value": "b"}')]),
                    "test-model",
                ),
            ) as chat:
                result = _run(belt, budget=budget)
            self.assertEqual(marker.get(), "turn-1")
        finally:
            progress.reset_cancel(cancel_token)
            progress.reset(progress_token)
            usage_meter.reset(usage_token)
            marker.reset(marker_token)

        self.assertEqual(result.stop_reason, "cancelled")
        self.assertEqual(chat.call_count, 1)
        observations = {entry["args"]["value"]: entry["result"] for entry in ctx.full_tool_results}
        self.assertEqual(set(observations), {"a", "b"})
        for value, observation in observations.items():
            self.assertTrue(observation.ok, observation.error)
            self.assertEqual(observation.data, {
                "turn_marker": "turn-1",
                "worker_marker": value,
                "same_budget": True,
                "same_usage_meter": True,
                "cancelled": True,
            })
        self.assertEqual(
            {event["name"] for event in events if event["name"].startswith("read:")},
            {"read:a", "read:b"},
        )


class RecoveryTests(unittest.TestCase):
    def test_a_failing_tool_does_not_end_the_turn(self):
        replies = [
            (_msg(tool_calls=[("boom", "{}")]), "test-model"),
            (_msg("I could not look that up, but here is what I know."), "test-model"),
        ]
        with patch("agents.runtime.loop.chat_completion", side_effect=replies):
            belt, _ = _belt((ECHO, BOOM))
            result = _run(belt)
        self.assertTrue(result.ok)
        self.assertIn("here is what I know", result.text)

    def test_the_model_is_told_why_a_tool_failed(self):
        replies = [
            (_msg(tool_calls=[("boom", "{}")]), "test-model"),
            (_msg("Recovered."), "test-model"),
        ]
        with patch("agents.runtime.loop.chat_completion", side_effect=replies) as chat:
            belt, _ = _belt((ECHO, BOOM))
            _run(belt)
        tool_msg = next(
            m for m in chat.call_args_list[1].kwargs["messages"] if m["role"] == "tool"
        )
        self.assertIn("tool exploded", tool_msg["content"])

    def test_a_dead_model_chain_returns_an_error_not_an_exception(self):
        with patch(
            "agents.runtime.loop.chat_completion", side_effect=RuntimeError("all models down")
        ):
            belt, _ = _belt()
            result = _run(belt)
        self.assertFalse(result.ok)
        self.assertEqual(result.stop_reason, "llm_error")
        self.assertIn("all models down", result.error)


class BudgetTests(unittest.TestCase):
    def test_a_model_that_only_calls_tools_is_forced_to_answer(self):
        """Without the forced close this is an infinite tool loop.

        The stub mirrors real behaviour: offered tools, it keeps calling them;
        with tools withdrawn, it has no choice but to answer. That withdrawal is
        the mechanism under test.
        """
        calls = {"n": 0}

        def _reply(**kwargs):
            calls["n"] += 1
            if kwargs.get("tools"):
                return _msg(tool_calls=[("echo", "{}")]), "test-model"
            return _msg("Fine, here is the answer."), "test-model"

        with patch("agents.runtime.loop.chat_completion", side_effect=_reply):
            belt, _ = _belt()
            result = _run(belt, max_steps=3)

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "Fine, here is the answer.")
        self.assertEqual(result.stop_reason, "max_steps")
        # Bounded: it cannot spend more calls than the budget allows.
        self.assertLessEqual(calls["n"], 4)

    def test_a_closing_call_that_still_returns_nothing_is_reported(self):
        """The caller must be able to tell an empty turn from a real answer."""
        with patch(
            "agents.runtime.loop.chat_completion",
            side_effect=lambda **kw: (_msg(tool_calls=[("echo", "{}")]), "test-model"),
        ):
            belt, _ = _belt()
            result = _run(belt, max_steps=2)
        self.assertFalse(result.ok)

    def test_tools_are_withdrawn_on_the_closing_pass(self):
        replies = [
            (_msg(tool_calls=[("echo", "{}")]), "test-model"),
            (_msg("Answer."), "test-model"),
        ]
        with patch("agents.runtime.loop.chat_completion", side_effect=replies) as chat:
            belt, _ = _belt()
            _run(belt, max_steps=2)
        self.assertIsNotNone(chat.call_args_list[0].kwargs["tools"])
        self.assertIsNone(chat.call_args_list[1].kwargs["tools"])

    def test_an_exhausted_time_budget_still_produces_an_answer(self):
        with patch(
            "agents.runtime.loop.chat_completion",
            return_value=(_msg("Short answer with what I have."), "test-model"),
        ):
            belt, _ = _belt()
            result = _run(belt, time_budget_s=0.0)
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
