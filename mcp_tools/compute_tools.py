"""Exact computation tools.

A language model doing arithmetic in its head is the single easiest source of a
wrong physics answer, and the one that most damages a tutor's credibility with a
student who then checks it. So the agent gets a real evaluator and is told, in
its prompt, to use it whenever a number matters.

This is a stdlib-only evaluator on a whitelisted AST — no `eval`, no imports, no
attribute access, no name binding. It computes; it cannot do anything else.

Symbolic algebra (factor, solve, differentiate) is deliberately NOT here: it
needs a CAS, and adding one is a dependency decision rather than a code one. The
`symbolic_math` slot in the architecture stays unconnected until then, and the
agent's prompt tells it to reason symbolically itself and verify numerically
with this tool.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any, Dict

from pydantic import BaseModel, Field

from agents.runtime.toolbelt import SCOPE_READ, ToolContext, tool

# ── Whitelisted operators ─────────────────────────────────────────────────────

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

#: Functions and constants a physics calculation legitimately needs.
_NAMES: Dict[str, Any] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
    "g": 9.81,          # standard gravity, the constant JEE problems assume
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "sqrt": math.sqrt,
    "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "exp": math.exp,
    "log": math.log,        # log(x) natural; log(x, base) supported
    "log10": math.log10,
    "log2": math.log2,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "degrees": math.degrees,
    "radians": math.radians,
    "hypot": math.hypot,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
    "comb": math.comb,
    "perm": math.perm,
}

#: Guard against a hang from something like 9**9**9.
_MAX_POW_EXPONENT = 1_000
_MAX_FACTORIAL = 500


class CalculateArgs(BaseModel):
    expression: str = Field(
        ...,
        description=(
            "A single arithmetic expression in Python syntax, e.g. '64 * 102' or "
            "'sqrt(2 * 9.81 * 20)' or 'sin(radians(30))'. No variables, no "
            "assignment, no imports."
        ),
    )
    purpose: str = Field(
        default="",
        description=(
            "One short phrase saying what this number is for, e.g. 'final velocity' "
            "or 'checking the student's step 3'. Recorded in the turn trace."
        ),
    )


def _guard_node(node: ast.AST) -> None:
    """Reject computations that would hang before we start evaluating them."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        exp = node.right
        if isinstance(exp, ast.Constant) and isinstance(exp.value, (int, float)):
            if abs(exp.value) > _MAX_POW_EXPONENT:
                raise ValueError(
                    f"Exponent {exp.value} is too large to evaluate safely "
                    f"(limit {_MAX_POW_EXPONENT})."
                )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "factorial" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                if arg.value > _MAX_FACTORIAL:
                    raise ValueError(
                        f"factorial({arg.value}) is too large (limit {_MAX_FACTORIAL})."
                    )


def _eval(node: ast.AST) -> Any:
    _guard_node(node)

    if isinstance(node, ast.Expression):
        return _eval(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex)):
            return node.value
        raise ValueError(f"Unsupported literal: {node.value!r}")

    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_eval(node.left), _eval(node.right))

    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return op(_eval(node.operand))

    if isinstance(node, ast.Name):
        if node.id in _NAMES:
            value = _NAMES[node.id]
            if callable(value):
                raise ValueError(f"'{node.id}' is a function — call it, e.g. {node.id}(x).")
            return value
        raise ValueError(
            f"Unknown name '{node.id}'. Substitute the number directly; "
            "this calculator has no variables."
        )

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only direct calls to built-in maths functions are allowed.")
        fn = _NAMES.get(node.func.id)
        if fn is None or not callable(fn):
            raise ValueError(f"Unknown function '{node.func.id}'.")
        if node.keywords:
            raise ValueError("Keyword arguments are not supported.")
        return fn(*[_eval(a) for a in node.args])

    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval(e) for e in node.elts]

    raise ValueError(f"Unsupported expression element: {type(node).__name__}")


def evaluate_expression(expression: str) -> Dict[str, Any]:
    """Evaluate one arithmetic expression exactly. Raises ValueError on bad input."""
    expr = (expression or "").strip()
    if not expr:
        raise ValueError("Empty expression.")
    if len(expr) > 500:
        raise ValueError("Expression is too long (limit 500 characters).")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Could not parse expression: {exc.msg}") from exc

    value = _eval(tree)

    result: Dict[str, Any] = {"expression": expr, "value": value}
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            result["note"] = "Result is not a finite number — check the inputs."
        else:
            result["rounded_3sf"] = float(f"{value:.3g}")
            result["rounded_2dp"] = round(value, 2)
    return result


@tool(
    name="calculate",
    description=(
        "Evaluate an arithmetic expression exactly. Use this for EVERY number you "
        "are about to show a student or use in a check — arithmetic done in your "
        "head is the most common way a physics answer goes wrong. Supports the "
        "usual operators plus sqrt, exp, log, trig, degrees/radians, factorial and "
        "the constants pi, e and g (9.81). No variables: substitute the numbers."
    ),
    args_model=CalculateArgs,
    scopes=(SCOPE_READ,),
)
def calculate(args: CalculateArgs, ctx: ToolContext) -> Dict[str, Any]:
    return evaluate_expression(args.expression)
