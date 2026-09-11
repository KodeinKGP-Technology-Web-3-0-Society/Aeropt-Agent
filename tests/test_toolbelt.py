"""Toolbelt: validation, scoping and failure containment.

These are the guarantees the whole autonomous design rests on. If a tool can
raise into the loop, one bad argument ends a student's turn. If scopes are
advisory rather than enforced, an expert subagent can rewrite a learner's
memory. Both are tested here rather than trusted to a prompt.
"""

from __future__ import annotations

import json
import unittest

from pydantic import BaseModel, Field

from agents.runtime.toolbelt import (
    EXPERT_SCOPES,
    SCOPE_READ,
    SCOPE_WRITE_MEMORY,
    TUTOR_SCOPES,
    ToolContext,
    ToolSpec,
    Toolbelt,
)


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


if __name__ == "__main__":
    unittest.main()
