"""Toolbelt: validation, scoping and failure containment.

These are the guarantees the whole autonomous design rests on. If a tool can
raise into the loop, one bad argument ends a student's turn. If scopes are
advisory rather than enforced, an expert subagent can rewrite a learner's
memory. Both are tested here rather than trusted to a prompt.
"""

from __future__ import annotations

import json
import threading
import unittest

from pydantic import BaseModel, Field

from agents.runtime.toolbelt import (
    EXPERT_SCOPES,
    SCOPE_READ,
    SCOPE_WRITE_MEMORY,
    TUTOR_SCOPES,
    ToolContext,
    ToolSpec,
    ToolResult,
    Toolbelt,
    surface_for_state,
)
from agents.runtime.budget import TurnBudget, current_budget_var
from services import progress


class _Args(BaseModel):
    name: str = Field(..., description="A required string.")
    count: int = Field(default=1, description="An optional int.")


def _ok(args: _Args, ctx: ToolContext):
    return {"echo": args.name, "count": args.count}


def _boom(args: _Args, ctx: ToolContext):
    raise RuntimeError("the database fell over")


def _stages(args: _Args, ctx: ToolContext):
    ctx.stage(primary_concept_id=args.name)
    return {"staged": True}


READ_TOOL = ToolSpec("read_tool", "A read tool.", _Args, _ok, frozenset({SCOPE_READ}))
MEMORY_TOOL = ToolSpec(
    "memory_tool", "Writes memory.", _Args, _ok, frozenset({SCOPE_WRITE_MEMORY})
)
BROKEN_TOOL = ToolSpec("broken", "Always fails.", _Args, _boom, frozenset({SCOPE_READ}))
STAGING_TOOL = ToolSpec("stager", "Stages state.", _Args, _stages, frozenset({SCOPE_READ}))
CONSEQUENTIAL_TOOL = ToolSpec(
    "publish",
    "Publishes something the learner should approve first.",
    _Args,
    _ok,
    frozenset({SCOPE_READ}),
    consequential=True,
)


def _belt(specs, scopes=TUTOR_SCOPES, **ctx_kwargs):
    ctx = ToolContext(user_id="u1", session_id="s1", **ctx_kwargs)
    return Toolbelt(specs, ctx, allowed_scopes=scopes), ctx


class SchemaTests(unittest.TestCase):
    def test_schema_is_valid_function_calling_shape(self):
        schema = READ_TOOL.schema()
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "read_tool")
        params = schema["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertIn("name", params["properties"])
        self.assertEqual(params["required"], ["name"])

    def test_schema_carries_no_refs(self):
        """$ref-bearing schemas are rejected by some providers."""
        self.assertNotIn("$defs", json.dumps(READ_TOOL.schema()))


class ScopingTests(unittest.TestCase):
    def test_tutor_gets_the_memory_tool(self):
        belt, _ = _belt([READ_TOOL, MEMORY_TOOL], TUTOR_SCOPES)
        self.assertIn("memory_tool", belt)

    def test_expert_cannot_even_see_the_memory_tool(self):
        belt, _ = _belt([READ_TOOL, MEMORY_TOOL], EXPERT_SCOPES)
        self.assertIn("read_tool", belt)
        self.assertNotIn("memory_tool", belt)
        self.assertNotIn("memory_tool", json.dumps(belt.schemas()))

    def test_calling_a_withheld_tool_fails_without_running_it(self):
        belt, _ = _belt([MEMORY_TOOL], EXPERT_SCOPES)
        result = belt.call("memory_tool", {"name": "x"})
        self.assertFalse(result.ok)
        self.assertIn("No tool named", result.error)


class ValidationTests(unittest.TestCase):
    def test_missing_required_argument_is_reported_not_raised(self):
        belt, _ = _belt([READ_TOOL])
        result = belt.call("read_tool", {"count": 2})
        self.assertFalse(result.ok)
        self.assertIn("name", result.error)

    def test_malformed_json_is_reported_not_raised(self):
        belt, _ = _belt([READ_TOOL])
        result = belt.call("read_tool", "{not json at all")
        self.assertFalse(result.ok)
        self.assertIn("JSON", result.error)

    def test_json_string_arguments_are_accepted(self):
        belt, _ = _belt([READ_TOOL])
        result = belt.call("read_tool", '{"name": "hello", "count": 3}')
        self.assertTrue(result.ok)
        self.assertEqual(result.data["echo"], "hello")

    def test_unknown_tool_name_lists_what_is_available(self):
        belt, _ = _belt([READ_TOOL])
        result = belt.call("no_such_tool", {})
        self.assertFalse(result.ok)
        self.assertIn("read_tool", result.error)


class FailureContainmentTests(unittest.TestCase):
    def test_handler_error_is_not_cached_as_a_success(self):
        calls = []

        def missing(args, ctx):
            calls.append(args.name)
            return {"error": "Material not found"}

        spec = ToolSpec("missing", "Missing material.", _Args, missing)
        belt, ctx = _belt([spec])
        for _ in range(3):
            self.assertFalse(belt.call("missing", {"name": "x"}).ok)
        self.assertEqual(calls, ["x", "x"])
        self.assertTrue(all(not step.ok for step in ctx.trace.steps))

    def test_handler_can_return_a_tool_result(self):
        spec = ToolSpec("structured", "Structured failure.", _Args,
                        lambda args, ctx: ToolResult(ok=False, error="Unavailable"))
        belt, _ = _belt([spec])
        result = belt.call("structured", {"name": "x"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Unavailable")

    def test_tool_worker_keeps_budget_reporter_and_cancellation_context(self):
        budget = TurnBudget.from_duration(10)
        cancelled = threading.Event()
        events = []

        def check_context(args, ctx):
            progress.report("stage", "thinking")
            cancelled.set()
            return {"same_budget": current_budget_var.get() is budget,
                    "cancelled": progress.is_cancelled()}

        spec = ToolSpec("context", "Inspect turn context.", _Args, check_context)
        belt, _ = _belt([spec], budget=budget)
        budget_token = current_budget_var.set(budget)
        reporter_token = progress.install(events.append)
        cancel_token = progress.install_cancel(cancelled)
        try:
            result = belt.call("context", {"name": "x"})
        finally:
            progress.reset_cancel(cancel_token)
            progress.reset(reporter_token)
            current_budget_var.reset(budget_token)
        self.assertEqual(result.data, {"same_budget": True, "cancelled": True})
        self.assertTrue(any(event["kind"] == "stage" for event in events))

    def test_expired_turn_does_not_start_a_tool(self):
        calls = []
        spec = ToolSpec("expensive", "Must not run.", _Args,
                        lambda args, ctx: calls.append(args.name))
        belt, _ = _belt([spec], budget=TurnBudget.from_duration(0))
        result = belt.call("expensive", {"name": "x"})
        self.assertFalse(result.ok)
        self.assertIn("budget exhausted", result.error)
        self.assertEqual(calls, [])

    def test_a_raising_tool_never_escapes_the_belt(self):
        belt, _ = _belt([BROKEN_TOOL])
        result = belt.call("broken", {"name": "x"})
        self.assertFalse(result.ok)
        self.assertIn("the database fell over", result.error)

    def test_every_outcome_serialises_for_the_model(self):
        belt, _ = _belt([READ_TOOL, BROKEN_TOOL])
        for name in ("read_tool", "broken", "missing"):
            with self.subTest(tool=name):
                payload = json.loads(belt.call(name, {"name": "x"}).to_json())
                self.assertIn("ok", payload)


class PermissionTests(unittest.TestCase):
    def test_consequential_tool_asks_instead_of_acting(self):
        belt, ctx = _belt([CONSEQUENTIAL_TOOL])
        result = belt.call("publish", {"name": "report"})
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.permission_required)
        self.assertEqual(len(ctx.pending_permissions), 1)
        self.assertIn("NOT performed", result.to_json())

    def test_granted_permission_lets_it_run(self):
        belt, ctx = _belt([CONSEQUENTIAL_TOOL], granted_permissions=["publish"])
        result = belt.call("publish", {"name": "report"})
        self.assertTrue(result.ok)
        self.assertEqual(ctx.pending_permissions, [])


class ContextTests(unittest.TestCase):
    def test_tools_stage_updates_rather_than_mutating_shared_state(self):
        belt, ctx = _belt([STAGING_TOOL])
        belt.call("stager", {"name": "KIN_01"})
        self.assertEqual(ctx.updates["primary_concept_id"], "KIN_01")

    def test_a_child_context_is_one_level_deeper_and_isolated(self):
        _, ctx = _belt([READ_TOOL])
        ctx.stage(a=1)
        child = ctx.child()
        self.assertEqual(child.depth, 1)
        self.assertEqual(child.updates, {})
        self.assertIs(child.trace, ctx.trace)

    def test_every_call_lands_in_the_trace(self):
        belt, ctx = _belt([READ_TOOL, BROKEN_TOOL])
        belt.call("read_tool", {"name": "x"})
        belt.call("broken", {"name": "x"})
        self.assertEqual(ctx.trace.tool_calls, 2)
        self.assertEqual(ctx.trace.path(), ["tool:read_tool", "tool:broken"])
        self.assertFalse(ctx.trace.steps[1].ok)


class StudySurfaceTests(unittest.TestCase):
    def test_current_phase_selects_practice_over_stale_stage(self):
        self.assertEqual(surface_for_state({"study_session_id": "study-1",
                                           "current_phase": "practice",
                                           "study_stage": "theory"}), "study_practice")

    def test_real_teacher_can_plan_lessons_and_grade_practice(self):
        from agents.teacher_agent import _import_study_tools, _TEACHER_TOOLS
        from agents.runtime.toolbelt import registry

        _import_study_tools()
        specs = [s for name, s in registry().items() if name in _TEACHER_TOOLS]
        learn, _ = _belt(specs, state={"study_session_id": "study-1", "current_phase": "learn"})
        self.assertIn("set_lesson_plan", learn)
        self.assertIn("complete_lesson_part", learn)
        self.assertIn("schedule_review", learn)
        practice, _ = _belt(specs, state={"study_session_id": "study-1", "current_phase": "practice"})
        self.assertIn("serve_practice_question", practice)
        self.assertIn("grade_practice_answer", practice)
        self.assertIn("schedule_review", practice)


if __name__ == "__main__":
    unittest.main()


class RepeatedReadTests(unittest.TestCase):
    """An identical repeated read wastes a step the student is waiting on."""

    def setUp(self):
        self.calls = []

        def _counting(args: _Args, ctx: ToolContext):
            self.calls.append(args.name)
            return {"echo": args.name}

        self.read = ToolSpec(
            "counting_read", "A read.", _Args, _counting, frozenset({SCOPE_READ})
        )
        self.write = ToolSpec(
            "counting_write",
            "A write.",
            _Args,
            _counting,
            frozenset({SCOPE_WRITE_MEMORY}),
        )

    def test_an_identical_read_is_served_from_cache_and_flagged(self):
        belt, _ = _belt([self.read])
        first = belt.call("counting_read", {"name": "x"})
        second = belt.call("counting_read", {"name": "x"})
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(second.ok)
        self.assertIn("repeated_call", second.data)
        self.assertEqual(second.data["echo"], first.data["echo"])

    def test_different_arguments_are_a_different_call(self):
        belt, _ = _belt([self.read])
        belt.call("counting_read", {"name": "x"})
        belt.call("counting_read", {"name": "y"})
        self.assertEqual(self.calls, ["x", "y"])

    def test_a_write_always_runs_even_when_repeated(self):
        belt, _ = _belt([self.write])
        belt.call("counting_write", {"name": "x"})
        belt.call("counting_write", {"name": "x"})
        self.assertEqual(len(self.calls), 2)

    def test_repeats_are_still_traced_as_real_calls(self):
        belt, ctx = _belt([self.read])
        belt.call("counting_read", {"name": "x"})
        belt.call("counting_read", {"name": "x"})
        self.assertEqual(ctx.trace.tool_calls, 2)
