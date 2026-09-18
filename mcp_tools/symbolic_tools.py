"""Symbolic algebra: solve, rearrange, differentiate, integrate, expand, approximate.

JEE physics is algebra with numbers bolted on at the end. A tutor that can only compute
`64 * 102` has to do every rearrangement in its head, which is exactly where a confident
wrong answer comes from — and it is the one kind of mistake a student cannot catch,
because they are the one still learning the algebra.

Safety: expressions are parsed and whitelisted by `utils.mathexpr` **before** sympy sees
them, so sympy's parser is never handed a raw string. That matters — `sympify` on
untrusted text is an execution risk, and the whitelist here allows only numbers, names,
arithmetic and a fixed set of maths functions.

Output is LaTeX, because everything student-facing in this service is LaTeX and a result
the agent has to re-transcribe is a result it can mis-transcribe.
"""

from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from agents.runtime.toolbelt import SCOPE_READ, ToolContext, tool
from utils.mathexpr import ExpressionError, free_symbols, from_latex, parse, split_relation

#: Operations the agent can ask for. Anything not here is not reachable.
OPERATIONS = (
    "solve",
    "simplify",
    "expand",
    "factor",
    "differentiate",
    "integrate",
    "limit",
    "approximate",
    "substitute",
)

_TIMEOUT_NOTE = (
    "Symbolic work can be slow on a hard expression. If this returns nothing useful, "
    "fall back to reasoning it out and checking the result with verify_result."
)


def _sympy():
    """Imported lazily so the rest of the service does not pay for it at start-up."""
    import sympy

    return sympy


def _to_sympy(expression: str):
    """Validate with our own whitelist, then hand the *parsed tree* to sympy.

    sympy never sees the original string, so its parser cannot be used as an attack
    surface: every node has already been checked against `utils.mathexpr`'s whitelist.
    """
    sympy = _sympy()
    tree = parse(expression)
    return _convert(tree.body, sympy), sorted(free_symbols(tree))


_SYMPY_FUNCTIONS = {
    "sqrt": "sqrt", "cbrt": "cbrt", "exp": "exp", "log": "log", "ln": "log",
    "sin": "sin", "cos": "cos", "tan": "tan", "cot": "cot", "sec": "sec", "csc": "csc",
    "asin": "asin", "acos": "acos", "atan": "atan", "atan2": "atan2",
    "sinh": "sinh", "cosh": "cosh", "tanh": "tanh", "abs": "Abs",
    "floor": "floor", "ceil": "ceiling", "factorial": "factorial",
    "min": "Min", "max": "Max", "log10": None, "log2": None,
    "degrees": None, "radians": None, "hypot": None, "round": None, "comb": None,
    "perm": None, "sum": None,
}


def _convert(node: ast.AST, sympy):
    if isinstance(node, ast.Constant):
        return sympy.Rational(str(node.value)) if isinstance(node.value, int) else sympy.Float(node.value)
    if isinstance(node, ast.Name):
        if node.id == "pi":
            return sympy.pi
        if node.id == "e":
            return sympy.E
        if node.id == "inf":
            return sympy.oo
        if node.id == "tau":
            return 2 * sympy.pi
        # positive=True is the right default for physics: masses, lengths, times and
        # coefficients of friction are positive, and it lets sympy simplify sqrt(m**2).
        return sympy.Symbol(node.id, positive=True)
    if isinstance(node, ast.UnaryOp):
        value = _convert(node.operand, sympy)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left, right = _convert(node.left, sympy), _convert(node.right, sympy)
        op = type(node.op)
        if op is ast.Add:
            return left + right
        if op is ast.Sub:
            return left - right
        if op is ast.Mult:
            return left * right
        if op is ast.Div:
            return left / right
        if op is ast.Pow:
            return left ** right
        if op is ast.Mod:
            return sympy.Mod(left, right)
        if op is ast.FloorDiv:
            return sympy.floor(left / right)
    if isinstance(node, ast.Call):
        name = node.func.id  # type: ignore[union-attr]
        mapped = _SYMPY_FUNCTIONS.get(name)
        args = [_convert(a, sympy) for a in node.args]
        if mapped is None:
            if name == "log10":
                return sympy.log(args[0], 10)
            if name == "log2":
                return sympy.log(args[0], 2)
            if name == "degrees":
                return args[0] * 180 / sympy.pi
            if name == "radians":
                return args[0] * sympy.pi / 180
            if name == "hypot":
                return sympy.sqrt(sum(a ** 2 for a in args))
            if name == "round":
                return args[0]
            raise ExpressionError(f"'{name}' is not available symbolically.")
        if mapped == "cbrt":
            return args[0] ** sympy.Rational(1, 3)
        return getattr(sympy, mapped)(*args)
    raise ExpressionError(f"Unsupported expression element: {type(node).__name__}")


def _is_real(expression, sympy) -> bool:
    """Whether a result is real, counting infinity as real enough.

    `oo.is_real` is False in sympy — infinity is not a real *number* — so testing
    `is_real` alone rejects every divergent limit, which is most of the interesting
    ones in physics. Extended-real is the property that matters here.
    """
    try:
        if expression.has(sympy.I) or expression.has(sympy.nan):
            return False
        if getattr(expression, "is_extended_real", None) is False:
            return False
    except AttributeError:
        return True
    return True


def _numeric_trend(expression: str, symbol: str, target_text: str) -> Dict[str, Any]:
    """Watch the expression numerically as the symbol approaches the target.

    sympy's answer at a point where an expression stops being real is direction-
    dependent and, with positivity assumptions in play, not always the branch a
    physicist means (`v \to c` in a relativistic factor is from below). Sampling settles
    it, and the samples are worth showing a student in their own right.
    """
    from utils.mathexpr import evaluate

    others = {name: 1.0 for name in free_symbols(parse(expression)) if name != symbol}
    try:
        base = 0.0 if target_text in {"0", "0.0"} else float(evaluate(target_text, others))
    except (ExpressionError, ValueError, ZeroDivisionError):
        return {}

    samples: Dict[str, float] = {}
    for side, sign in (("from below", -1.0), ("from above", 1.0)):
        values = []
        for epsilon in (1e-2, 1e-3, 1e-4):
            point = dict(others)
            point[symbol] = base + sign * epsilon * (abs(base) if base else 1.0)
            try:
                value = float(evaluate(expression, point))
            except (ExpressionError, ValueError, ZeroDivisionError, OverflowError):
                values = []
                break
            if value != value:  # NaN
                values = []
                break
            values.append(value)
        if values:
            samples[side] = values[-1]
    if not samples:
        return {}
    return {
        "numeric_check": {
            side: float(f"{value:.6g}") for side, value in samples.items()
        },
        "other_symbols_held_at": 1.0,
    }


def _render(expression, sympy) -> Dict[str, Any]:
    """A result in the three forms a caller might want."""
    return {
        "latex": sympy.latex(expression),
        "plain": str(expression),
        "is_number": bool(getattr(expression, "is_number", False)),
    }


class SolveSymbolicallyArgs(BaseModel):
    operation: str = Field(
        ...,
        description=(
            "What to do: 'solve' (rearrange an equation for one symbol), 'simplify', "
            "'expand', 'factor', 'differentiate', 'integrate', 'limit', 'approximate' "
            "(series expansion — small-angle, binomial), or 'substitute'."
        ),
    )
    expression: str = Field(
        ...,
        description=(
            "The expression or equation, LaTeX or plain: "
            "'m*a = m*g*sin(theta) - mu*m*g*cos(theta)', '\\\\frac{1}{2}mv^2'. Write "
            "products explicitly — 'm*g*h', not 'mgh'."
        ),
    )
    symbol: Optional[str] = Field(
        default=None,
        description=(
            "The symbol to act on: what to solve for, differentiate or integrate with "
            "respect to, or take a limit in. Required for solve, differentiate, "
            "integrate, limit and approximate."
        ),
    )
    at: Optional[str] = Field(
        default=None,
        description=(
            "For 'limit': the point approached, e.g. '0' or 'inf'. For 'approximate': "
            "the point to expand about, default 0. For 'integrate': omit for an "
            "indefinite integral, or give bounds as 'a to b'."
        ),
    )
    order: int = Field(
        default=1,
        description=(
            "For 'differentiate': how many times — 1 unless you specifically want a "
            "second derivative. For 'approximate': how many terms to keep (minimum 2)."
        ),
    )
    substitutions: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "For 'substitute', and applied after any other operation: "
            "{'theta': 'pi/6', 'mu': '0.2'} — values or expressions."
        ),
    )


@tool(
    name="solve_symbolically",
    description=(
        "Do the algebra exactly instead of in your head: rearrange an equation for a "
        "symbol, differentiate, integrate, expand, factor, take a limit, or get a "
        "small-angle/binomial approximation. Returns LaTeX you can put straight in front "
        "of a student. Use it for any rearrangement you are going to show, for deriving "
        "a result rather than recalling it, and for the approximations JEE leans on "
        "(sin(x) ~ x, (1+x)^n ~ 1+nx). Pair it with verify_result when the result "
        "matters — this gives you the algebra, that proves it is the algebra you wanted."
    ),
    args_model=SolveSymbolicallyArgs,
    scopes=(SCOPE_READ,),
)
def solve_symbolically(args: SolveSymbolicallyArgs, ctx: ToolContext) -> Dict[str, Any]:
    operation = (args.operation or "").strip().lower()
    if operation not in OPERATIONS:
        return {
            "error": (
                f"Unknown operation '{args.operation}'. Available: "
                + ", ".join(OPERATIONS)
            )
        }
    sympy = _sympy()

    try:
        payload = _run_operation(operation, args, sympy)
    except ExpressionError as exc:
        return {"error": str(exc)}
    except (NotImplementedError, ValueError, TypeError, ZeroDivisionError) as exc:
        return {
            "error": f"Could not do this symbolically: {str(exc)[:200]}",
            "note": _TIMEOUT_NOTE,
        }
    except RecursionError:
        return {"error": "Expression is too deeply nested.", "note": _TIMEOUT_NOTE}

    # `substitute` has already applied them; every other operation gets the values
    # folded in afterwards so one call can give both the algebra and the number.
    if args.substitutions and operation != "substitute" and "_raw" in payload:
        payload["with_values"] = _apply_substitutions(
            payload["_raw"], args.substitutions, sympy
        )
    payload.pop("_raw", None)
    return payload


def _run_operation(operation: str, args: SolveSymbolicallyArgs, sympy) -> Dict[str, Any]:
    relation = split_relation(args.expression or "")

    if operation == "solve":
        if not args.symbol:
            return {"error": "Say which symbol to solve for."}
        if relation:
            left, _ = _to_sympy(relation[0])
            right, _ = _to_sympy(relation[1])
            equation = sympy.Eq(left, right)
        else:
            expression, _ = _to_sympy(args.expression)
            equation = sympy.Eq(expression, 0)
        symbol = sympy.Symbol(args.symbol, positive=True)
        roots = sympy.solve(equation, symbol, dict=False)
        if not roots:
            return {
                "operation": "solve",
                "solved_for": args.symbol,
                "solutions": [],
                "note": (
                    "No solution found. Either the symbol does not appear, or the "
                    "equation has no closed form — check the equation, or solve it "
                    "numerically."
                ),
            }
        rendered = [_render(sympy.simplify(r), sympy) for r in roots]
        if _has_trig(equation, sympy) and len(roots) == 1:
            rendered_note = (
                "This is a trigonometric equation, so it has infinitely many roots and "
                "only one came back. In a physics problem the others are often the "
                "interesting part — two launch angles give the same range, for "
                "instance. Check whether a second angle in range also works."
            )
            return {**_solve_payload(args, rendered, roots, sympy), "note": rendered_note}
        return _solve_payload(args, rendered, roots, sympy)

    if operation in {"simplify", "expand", "factor"}:
        target = args.expression
        if relation:
            left, _ = _to_sympy(relation[0])
            right, _ = _to_sympy(relation[1])
            expression = left - right
            note = "An equation was given, so this acts on (left - right)."
        else:
            expression, _ = _to_sympy(target)
            note = ""
        fn = {"simplify": sympy.simplify, "expand": sympy.expand, "factor": sympy.factor}[operation]
        result = fn(expression)
        out = {"operation": operation, "result": sympy.latex(result), **_render(result, sympy), "_raw": result}
        if note:
            out["note"] = note
        return out

    if operation == "differentiate":
        if not args.symbol:
            return {"error": "Say which symbol to differentiate with respect to."}
        expression, _ = _to_sympy(relation[1] if relation else args.expression)
        symbol = sympy.Symbol(args.symbol, positive=True)
        result = sympy.diff(expression, symbol, max(1, args.order if args.order else 1))
        return {
            "operation": "differentiate",
            "with_respect_to": args.symbol,
            "order": max(1, args.order or 1),
            "result": sympy.latex(result),
            **_render(result, sympy),
            "_raw": result,
        }

    if operation == "integrate":
        if not args.symbol:
            return {"error": "Say which symbol to integrate with respect to."}
        expression, _ = _to_sympy(relation[1] if relation else args.expression)
        symbol = sympy.Symbol(args.symbol, positive=True)
        bounds = None
        if args.at and " to " in args.at:
            low_text, high_text = args.at.split(" to ", 1)
            low, _ = _to_sympy(low_text)
            high, _ = _to_sympy(high_text)
            bounds = (low, high)
        result = (
            sympy.integrate(expression, (symbol, bounds[0], bounds[1]))
            if bounds
            else sympy.integrate(expression, symbol)
        )
        out = {
            "operation": "integrate",
            "with_respect_to": args.symbol,
            "definite": bounds is not None,
            "result": sympy.latex(result),
            **_render(result, sympy),
            "_raw": result,
        }
        if bounds is None:
            out["note"] = "Indefinite — remember the constant of integration when you show this."
        return out

    if operation == "limit":
        if not args.symbol:
            return {"error": "Say which symbol goes to the limit."}
        expression, _ = _to_sympy(relation[1] if relation else args.expression)
        symbol = sympy.Symbol(args.symbol, positive=True)
        target_text = (args.at or "0").strip()
        target = sympy.oo if target_text in {"inf", "infinity", "oo"} else _to_sympy(target_text)[0]
        # Direction matters physically. `v -> c` in a relativistic expression means from
        # below; approaching from above puts the expression under a negative square root
        # and sympy correctly answers with something imaginary, which is not the physics
        # anyone is asking about. So take the direction that stays real.
        result = sympy.limit(expression, symbol, target)
        direction = "default"
        if not _is_real(result, sympy):
            for attempt in ("-", "+"):
                try:
                    candidate = sympy.limit(expression, symbol, target, dir=attempt)
                except (NotImplementedError, ValueError, TypeError):
                    continue
                if _is_real(candidate, sympy):
                    result, direction = candidate, (
                        "from below" if attempt == "-" else "from above"
                    )
                    break
        out = {
            "operation": "limit",
            "symbol": args.symbol,
            "approaching": target_text,
            "result": sympy.latex(result),
            **_render(result, sympy),
            "_raw": result,
        }
        if direction != "default":
            out["approached"] = direction
            out["note"] = (
                f"Taken {direction}, because the other side is not physically "
                "meaningful here (the expression stops being real)."
            )
        trend = _numeric_trend(
            relation[1] if relation else args.expression, args.symbol, target_text
        )
        if trend:
            out.update(trend)
            out["note"] = (
                (out.get("note", "") + " ").strip()
                + "The numeric samples show which way it actually goes — trust those "
                "over the symbolic sign if the two disagree, and say which side you "
                "approached from."
            )
        # A divergent symbolic limit whose sign contradicts the samples is sympy's
        # branch choice, not the physics. Saying so is more useful than picking one.
        signed_infinity = result in (sympy.oo, -sympy.oo)
        if signed_infinity and trend:
            symbolic_positive = result == sympy.oo
            numeric_positive = all(v > 0 for v in trend["numeric_check"].values())
            numeric_negative = all(v < 0 for v in trend["numeric_check"].values())
            if (symbolic_positive and numeric_negative) or (
                not symbolic_positive and numeric_positive
            ):
                out["disagreement"] = (
                    f"The symbolic limit says {sympy.latex(result)} but the samples run "
                    "the other way. The samples are the reliable half here — the "
                    "expression diverges, and the sign follows the side you approach "
                    "from. State the direction explicitly."
                )
        if not _is_real(result, sympy):
            out["caution"] = (
                "The symbolic limit is not real, which usually means the expression "
                "stops being defined at this point rather than tending to a value. Do "
                "not show the symbolic form to a student — describe what the numbers do."
            )
        return out

    if operation == "approximate":
        if not args.symbol:
            return {"error": "Say which symbol is small."}
        expression, _ = _to_sympy(relation[1] if relation else args.expression)
        symbol = sympy.Symbol(args.symbol, positive=True)
        about = _to_sympy(args.at)[0] if args.at else 0
        terms = max(2, args.order or 2)
        series = sympy.series(expression, symbol, about, terms).removeO()
        result = sympy.simplify(series)
        return {
            "operation": "approximate",
            "small_symbol": args.symbol,
            "about": str(about),
            "terms": terms,
            "result": sympy.latex(result),
            **_render(result, sympy),
            "_raw": result,
            "note": (
                "A truncated series: valid only while that symbol is small. Say which "
                "approximation you used — students should see where it came from."
            ),
        }

    if operation == "substitute":
        if not args.substitutions:
            return {"error": "Give the substitutions to make."}
        expression, _ = _to_sympy(relation[1] if relation else args.expression)
        substituted = _apply_substitutions(expression, args.substitutions, sympy)
        if "error" in substituted:
            return substituted
        return {"operation": "substitute", "result": substituted["latex"], **substituted}

    return {"error": f"Operation '{operation}' is not implemented."}


def _has_trig(expression, sympy) -> bool:
    return expression.has(sympy.sin, sympy.cos, sympy.tan, sympy.cot, sympy.sec, sympy.csc)


def _solve_payload(args, rendered, roots, sympy) -> Dict[str, Any]:
    return {
        "operation": "solve",
        "solved_for": args.symbol,
        "solutions": rendered,
        "result": rendered[0]["latex"],
        "_raw": roots[0],
        "note": (
            "Every symbol is assumed positive, which is right for masses, lengths and "
            "coefficients of friction but means a genuinely negative root may be "
            "dropped. Say so if a sign matters here."
            if len(roots) == 1
            else f"{len(roots)} solutions — pick the physical one and say why."
        ),
    }


def _apply_substitutions(expression, substitutions: Dict[str, str], sympy) -> Dict[str, Any]:
    mapping = {}
    for name, value in substitutions.items():
        try:
            mapping[sympy.Symbol(name, positive=True)] = _to_sympy(str(value))[0]
        except ExpressionError as exc:
            return {"error": f"Could not read the value for '{name}': {exc}"}
    substituted = sympy.simplify(expression.subs(mapping))
    out = _render(substituted, sympy)
    try:
        numeric = float(substituted.evalf())
        out["value"] = numeric
        out["rounded_3sf"] = float(f"{numeric:.3g}")
    except (TypeError, ValueError):
        pass
    return out
