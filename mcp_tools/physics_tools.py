"""Physics reasoning tools: dimensions, answer verification, and checking a student's work.

These exist because a tutor that is confidently wrong is worse than one that says it is
unsure, and the three cheapest ways to be *certain* about physics without a full CAS are:

1. **Dimensions.** `v = \\sqrt{2 g h^2}` is wrong and you can prove it in microseconds.
2. **Substitution.** A claimed answer either satisfies the governing equation or it does
   not; put it back in and evaluate.
3. **Limiting cases.** Every physicist checks what happens as `\\mu \\to 0` or
   `m_2 \\to \\infty`. A tutor that does this catches its own algebra slips.

Equivalence is decided by evaluating at many random points rather than by symbolic
manipulation. Two expressions that agree at two dozen unrelated points are equal in
practice — and unlike a CAS this needs no dependency, never times out on a hard
simplification, and hands back a concrete counterexample when it fails, which is the
part a student can actually learn from.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from agents.runtime.toolbelt import SCOPE_READ, ToolContext, tool
from utils import dimensions as dims
from utils.mathexpr import (
    ExpressionError,
    evaluate,
    free_symbols,
    from_latex,
    parse,
    physical_probe_points,
    probe_points,
    split_relation,
)

#: Two values count as equal when they agree to this relative tolerance.
_REL_TOL = 1e-7
#: A probe point is unusable if an expression blows up there (sqrt of a negative,
#: division by zero, tan at its pole). At least this many must survive.
_MIN_USABLE_PROBES = 6


# ── shared helpers ────────────────────────────────────────────────────────────

def _dimension_overrides(raw: Optional[Dict[str, str]]) -> dict[str, dims.Dim]:
    if not raw:
        return {}
    out: dict[str, dims.Dim] = {}
    for symbol, meaning in raw.items():
        out[symbol] = dims.resolve_quantity(meaning)
    return out


def _presentable_failure(
    symbols: List[str], fails_at: "Callable[[Dict[str, float]], Optional[Dict[str, Any]]]"
) -> Optional[Dict[str, Any]]:
    """Re-find the failure at values a student would recognise, for the explanation.

    The wide random probes are what decide whether the algebra is wrong; they range
    over mu=2.4 and theta=3.7 rad because that is good at exposing differences. Neither
    belongs in a sentence shown to a student, so once something *is* known to be wrong,
    the failing case is looked for again among physically plausible values.
    """
    for point in physical_probe_points(symbols):
        found = fails_at(point.values)
        if found:
            return {"at": point.describe(), **found}
    return None


def _close(a: float, b: float) -> bool:
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return math.isclose(a, b, rel_tol=_REL_TOL, abs_tol=1e-12)


def _infer_ambiguous(
    left_text: str,
    right_text: str,
    overrides: dict[str, dims.Dim],
) -> Optional[tuple[dict[str, str], dims.Dim]]:
    """Pick the meaning of an ambiguous symbol that makes the equation consistent.

    `T = 2\\pi\\sqrt{l/g}` should not need a note explaining that T is a period. The
    right-hand side is unambiguously a time, so only one meaning of T balances the
    equation — that is deduction, not guessing, and the meaning used is reported.
    Refusing is still the answer when several meanings work, or none.
    """
    import itertools

    ambiguous = sorted(
        (dims.unknown_symbols(left_text, overrides)[1])
        | (dims.unknown_symbols(right_text, overrides)[1])
    )
    if not ambiguous or len(ambiguous) > 2:
        return None
    options = [list(dims.AMBIGUOUS_SYMBOLS[name].items()) for name in ambiguous]
    if math.prod(len(o) for o in options) > 12:
        return None

    consistent: list[tuple[dict[str, str], dims.Dim]] = []
    for combination in itertools.product(*options):
        table = dict(overrides)
        labels: dict[str, str] = {}
        for name, (label, dim) in zip(ambiguous, combination):
            table[name] = dim
            labels[name] = label
        try:
            left = dims.dimension_of(left_text, table)
            right = dims.dimension_of(right_text, table)
        except (dims.DimensionError, ExpressionError):
            continue
        if left == right:
            consistent.append((labels, left))
    return consistent[0] if len(consistent) == 1 else None


def _dimension_report(
    expression: str, overrides: dict[str, dims.Dim]
) -> Dict[str, Any]:
    """Dimension of one expression, or why it could not be determined."""
    try:
        dim = dims.dimension_of(expression, overrides)
    except dims.DimensionError as exc:
        unknown, ambiguous = dims.unknown_symbols(expression, overrides)
        report: Dict[str, Any] = {"ok": False, "problem": str(exc)}
        if unknown:
            report["unknown_symbols"] = sorted(unknown)
        if ambiguous:
            report["ambiguous_symbols"] = {
                name: sorted(dims.AMBIGUOUS_SYMBOLS[name]) for name in sorted(ambiguous)
            }
        # Every symbol was known, so the failure is not missing information — it is a
        # term that cannot be added to the term beside it. That is a proof the
        # expression is wrong, and it must be reported as one rather than as doubt.
        report["inconsistent"] = not unknown and not ambiguous
        return report
    except ExpressionError as exc:
        return {"ok": False, "problem": str(exc)}
    return {"ok": True, "dimension": str(dim), "quantity": dim.named() or "no common name"}


# ── 1. dimensions ─────────────────────────────────────────────────────────────

class CheckDimensionsArgs(BaseModel):
    expression: str = Field(
        ...,
        description="LaTeX or plain expression/equation to check (e.g. 'v = \\sqrt{2*g*h}'). Write products explicitly.",
    )
    expect: Optional[str] = Field(
        default=None,
        description="Expected quantity name ('velocity', 'energy') or unit ('m/s', 'N*m').",
    )
    symbol_meanings: Optional[Dict[str, str]] = Field(
        default=None,
        description="Meanings for ambiguous symbols as {symbol: quantity}, e.g. {'T': 'temperature'}.",
    )


@tool(
    name="check_dimensions",
    description=(
        "Check whether a formula is dimensionally consistent and report its dimensions. "
        "Catches dimensional errors and unbalanced equations before showing formulas to students."
    ),
    args_model=CheckDimensionsArgs,
    scopes=(SCOPE_READ,),
)
def check_dimensions(args: CheckDimensionsArgs, ctx: ToolContext) -> Dict[str, Any]:
    overrides = _dimension_overrides(args.symbol_meanings)
    text = (args.expression or "").strip()
    if not text:
        return {"error": "Nothing to check."}

    relation = split_relation(text)
    payload: Dict[str, Any] = {"input": text}

    if relation:
        left_text, right_text = relation
        left = _dimension_report(left_text, overrides)
        right = _dimension_report(right_text, overrides)

        if not (left["ok"] and right["ok"]):
            inferred = _infer_ambiguous(left_text, right_text, overrides)
            if inferred:
                labels, dim = inferred
                overrides = {
                    **overrides,
                    **{name: dims.AMBIGUOUS_SYMBOLS[name][label] for name, label in labels.items()},
                }
                left = _dimension_report(left_text, overrides)
                right = _dimension_report(right_text, overrides)
                payload["read_ambiguous_symbols_as"] = labels
                payload["inference_note"] = (
                    "Only one reading of "
                    + ", ".join(labels)
                    + " makes the equation balance, so that is the one used. Say so if "
                    "you meant something else."
                )
        payload["left"] = {"expression": left_text, **left}
        payload["right"] = {"expression": right_text, **right}
        if not (left["ok"] and right["ok"]):
            broken = [
                (side, report)
                for side, report in (("left", left), ("right", right))
                if report.get("inconsistent")
            ]
            if broken:
                side, report = broken[0]
                payload["consistent"] = False
                payload["verdict"] = (
                    f"NOT consistent: the {side} side is not even internally "
                    f"consistent — {report['problem']} This line cannot be right. It is "
                    "usually a term that lost or gained a factor."
                )
                return payload
            payload["consistent"] = None
            payload["note"] = (
                "Could not determine both sides — see the problem reported above. "
                "Supply symbol_meanings for anything unknown or ambiguous and retry."
            )
            return payload
        payload["consistent"] = left["dimension"] == right["dimension"]
        if payload["consistent"]:
            payload["dimension"] = left["dimension"]
            payload["quantity"] = left["quantity"]
            payload["verdict"] = (
                "Dimensionally consistent. That rules out a units error, not an "
                "algebra error — a wrong coefficient is dimensionally fine."
            )
        else:
            payload["verdict"] = (
                f"NOT consistent: the left side is {left['dimension']} and the right "
                f"side is {right['dimension']}. This equation cannot be correct as "
                "written. Do not show it to the student — find the missing or extra "
                "factor first."
            )
        return payload

    report = _dimension_report(text, overrides)
    payload.update(report)
    if not report["ok"]:
        if report.get("inconsistent"):
            payload["consistent"] = False
            payload["verdict"] = (
                f"NOT consistent: {report['problem']} This expression cannot be right "
                "as written."
            )
        return payload

    if args.expect:
        try:
            wanted = dims.resolve_quantity(args.expect)
        except dims.DimensionError as exc:
            payload["expect_problem"] = str(exc)
            return payload
        payload["expected"] = {"as_given": args.expect, "dimension": str(wanted)}
        payload["matches_expected"] = report["dimension"] == str(wanted)
        payload["verdict"] = (
            f"Dimensions match {args.expect}."
            if payload["matches_expected"]
            else (
                f"MISMATCH: this is {report['dimension']}, but {args.expect} is "
                f"{wanted}. The expression is wrong, or it is not the quantity you "
                "think it is."
            )
        )
    return payload


# ── 2. verify a result ────────────────────────────────────────────────────────

class VerifyResultArgs(BaseModel):
    answer: str = Field(
        ...,
        description="The result to verify as 'unknown = expression' (e.g. 'a = g*(sin(theta) - mu*cos(theta))').",
    )
    relation: Optional[str] = Field(
        default=None,
        description="Optional governing equation to substitute the answer back into and compare sides.",
    )
    expect_quantity: Optional[str] = Field(
        default=None,
        description="Expected physical quantity name or unit ('acceleration', 'energy', 'm/s').",
    )
    limiting_cases: Optional[Dict[str, str]] = Field(
        default=None,
        description="Limits to test as {'symbol -> value': 'expected'}, e.g. {'mu -> 0': 'g*sin(theta)'}.",
    )
    symbol_meanings: Optional[Dict[str, str]] = Field(
        default=None,
        description="Meanings for ambiguous symbols, as in check_dimensions.",
    )


def _limit_value(expr: str, symbol: str, target: str, others: Dict[str, float]) -> Optional[float]:
    """Evaluate `expr` with one symbol pushed to a limit and the rest held fixed."""
    if target in {"inf", "infinity", "oo"}:
        value = 1e7
    elif target in {"-inf", "-infinity"}:
        value = -1e7
    else:
        try:
            value = float(evaluate(target))
        except ExpressionError:
            return None
        if value == 0:
            value = 1e-9
    bindings = dict(others)
    bindings[symbol] = value
    try:
        return float(evaluate(expr, bindings))
    except (ExpressionError, ValueError, ZeroDivisionError, OverflowError):
        return None


@tool(
    name="verify_result",
    description=(
        "Verify a derived physics result: check governing equation satisfaction, dimensions, "
        "and limiting cases (e.g. friction -> 0). Use before presenting algebraic results to students."
    ),
    args_model=VerifyResultArgs,
    scopes=(SCOPE_READ,),
)
def verify_result(args: VerifyResultArgs, ctx: ToolContext) -> Dict[str, Any]:
    answer = split_relation(args.answer or "")
    if not answer:
        return {
            "error": (
                "Write the answer as 'unknown = expression', e.g. "
                "'a = g*(sin(theta) - mu*cos(theta))'."
            )
        }
    unknown, expression = answer
    unknown = unknown.strip()
    overrides = _dimension_overrides(args.symbol_meanings)
    payload: Dict[str, Any] = {"unknown": unknown, "expression": expression, "checks": {}}

    try:
        tree = parse(expression)
    except ExpressionError as exc:
        return {"error": str(exc)}
    symbols = sorted(free_symbols(tree))
    payload["depends_on"] = symbols

    # (a) dimensions
    dim_report = _dimension_report(expression, overrides)
    check: Dict[str, Any] = dict(dim_report)
    if dim_report["ok"] and args.expect_quantity:
        try:
            wanted = dims.resolve_quantity(args.expect_quantity)
            check["expected"] = str(wanted)
            check["matches_expected"] = dim_report["dimension"] == str(wanted)
        except dims.DimensionError as exc:
            check["expected_problem"] = str(exc)
    payload["checks"]["dimensions"] = check

    # (b) substitute the answer back into the governing equation
    if args.relation:
        payload["checks"]["satisfies_relation"] = _substitution_check(
            args.relation, unknown, expression, symbols
        )

    # (c) limiting cases
    payload["checks"]["limits"] = _limit_checks(expression, symbols, args.limiting_cases)

    failures = []
    dim_check = payload["checks"]["dimensions"]
    if dim_check.get("ok") is False:
        failures.append("dimensions could not be determined")
    elif dim_check.get("matches_expected") is False:
        failures.append(
            f"dimensions are {dim_check['dimension']}, not {args.expect_quantity}"
        )
    sat = payload["checks"].get("satisfies_relation") or {}
    if sat.get("satisfies") is False:
        failures.append("the answer does not satisfy the governing equation")
    broken_limits = [
        name for name, info in payload["checks"]["limits"].items()
        if isinstance(info, dict) and info.get("matches_expected") is False
    ]
    if broken_limits:
        failures.append("limiting case(s) disagree: " + ", ".join(broken_limits))

    payload["verdict"] = (
        "Every check passed. Dimensions are right, it satisfies the relation where one "
        "was given, and the limits behave. Safe to show, with the usual caveat that "
        "these checks cannot catch a wrong problem setup."
        if not failures
        else "DO NOT show this yet — " + "; ".join(failures) + "."
    )
    payload["passed"] = not failures
    return payload


def _substitution_check(
    relation: str, unknown: str, expression: str, symbols: List[str]
) -> Dict[str, Any]:
    """Put the answer back into the equation and compare both sides numerically."""
    parts = split_relation(relation)
    if not parts:
        return {"error": "The relation needs exactly one '='."}
    left_text, right_text = parts
    try:
        left = parse(left_text)
        right = parse(right_text)
    except ExpressionError as exc:
        return {"error": str(exc)}

    needed = sorted((free_symbols(left) | free_symbols(right) | set(symbols)) - {unknown})
    points = probe_points(needed)
    compared = mismatched = 0
    example: Dict[str, Any] = {}
    for point in points:
        bindings = dict(point.values)
        try:
            bindings[unknown] = float(evaluate(expression, bindings))
            lhs = float(evaluate(left, bindings))
            rhs = float(evaluate(right, bindings))
        except (ExpressionError, ValueError, ZeroDivisionError, OverflowError):
            continue
        if not (math.isfinite(lhs) and math.isfinite(rhs)):
            continue
        compared += 1
        if not _close(lhs, rhs):
            mismatched += 1
            if not example:
                example = {
                    "at": point.describe(),
                    f"{unknown}": round(bindings[unknown], 6),
                    "left_side": round(lhs, 6),
                    "right_side": round(rhs, 6),
                }
    if compared < _MIN_USABLE_PROBES:
        return {
            "satisfies": None,
            "note": (
                "Could not test this numerically — the expressions were undefined at "
                "most sample points. Check the relation, or verify by hand."
            ),
        }
    if mismatched:
        def fails_at(values: Dict[str, float]) -> Optional[Dict[str, Any]]:
            bindings = dict(values)
            try:
                bindings[unknown] = float(evaluate(expression, bindings))
                lhs_v = float(evaluate(left, bindings))
                rhs_v = float(evaluate(right, bindings))
            except (ExpressionError, ValueError, ZeroDivisionError, OverflowError):
                return None
            if not (math.isfinite(lhs_v) and math.isfinite(rhs_v)) or _close(lhs_v, rhs_v):
                return None
            return {
                f"{unknown}": round(bindings[unknown], 6),
                "left_side": round(lhs_v, 6),
                "right_side": round(rhs_v, 6),
            }

        example = _presentable_failure(needed, fails_at) or example
        return {
            "satisfies": False,
            "points_tested": compared,
            "points_disagreeing": mismatched,
            "counterexample": example,
            "note": (
                "Substituting the answer into the relation does not balance it, so the "
                "answer is wrong. The counterexample above is a concrete case to work "
                "through."
            ),
        }
    return {
        "satisfies": True,
        "points_tested": compared,
        "note": "The answer satisfies the relation at every point tested.",
    }


def _limit_checks(
    expression: str, symbols: List[str], cases: Optional[Dict[str, str]]
) -> Dict[str, Any]:
    baseline = {name: 1.0 + 0.1 * i for i, name in enumerate(symbols)}
    results: Dict[str, Any] = {}

    if cases:
        for when, expect in cases.items():
            text = from_latex(when).replace("->", ">").replace("=>", ">")
            if ">" not in text:
                results[when] = {"error": "Write a limit as 'symbol -> value'."}
                continue
            symbol, target = [p.strip() for p in text.split(">", 1)]
            others = {k: v for k, v in baseline.items() if k != symbol}
            got = _limit_value(expression, symbol, target, others)
            entry: Dict[str, Any] = {
                "value": None if got is None else round(got, 6),
                "others_held_at": {k: round(v, 3) for k, v in others.items()},
            }
            if got is None:
                entry["note"] = "Undefined in this limit — that may itself be the answer."
            if (expect or "").strip():
                want = _limit_value(f"({expect})", symbol, target, others)
                if want is None:
                    try:
                        want = float(evaluate(expect, others))
                    except (ExpressionError, ValueError, ZeroDivisionError):
                        want = None
                entry["expected"] = None if want is None else round(want, 6)
                entry["matches_expected"] = (
                    got is not None and want is not None and _close(got, want)
                )
            results[when] = entry
        return results

    # Nothing specified: probe each symbol at zero and at large, which is where a
    # dropped term or a flipped sign usually shows itself.
    for symbol in symbols:
        others = {k: v for k, v in baseline.items() if k != symbol}
        for target, label in (("0", f"{symbol} -> 0"), ("inf", f"{symbol} -> inf")):
            got = _limit_value(expression, symbol, target, others)
            results[label] = {
                "value": None if got is None else round(got, 6),
                "others_held_at": {k: round(v, 3) for k, v in others.items()},
            }
    results["how_to_read_this"] = (
        "These are numeric values, not symbolic limits. Compare each against what the "
        "physics says should happen — friction vanishing, a mass becoming infinite — "
        "and pass limiting_cases with an `expect` to have it checked properly."
    )
    return results


# ── 3. check a student's working ──────────────────────────────────────────────

class CheckStudentWorkArgs(BaseModel):
    steps: List[str] = Field(
        ...,
        description=(
            "The student's working, one step per item, in order — each an equation or "
            "an expression, LaTeX or plain. Example: ['m*a = m*g*sin(theta) - f', "
            "'a = g*sin(theta) - f/m', 'a = g*(sin(theta) - mu)']. Transcribe what "
            "they wrote, do not tidy it up: the point is to find where it breaks."
        ),
    )
    symbol_meanings: Optional[Dict[str, str]] = Field(
        default=None,
        description="Meanings for ambiguous symbols, as in check_dimensions.",
    )


@tool(
    name="check_student_work",
    description=(
        "Find the first step in a student's working that does not follow from the one "
        "before it, with a concrete counterexample. Checks each step for dimensional "
        "consistency too. Use it the moment a student shows their work and asks why "
        "their answer is wrong — it locates the break instead of re-deriving the whole "
        "problem and hoping they spot the difference. Telling a student exactly which "
        "line broke, and a set of numbers that proves it, teaches far more than a "
        "correct solution does."
    ),
    args_model=CheckStudentWorkArgs,
    scopes=(SCOPE_READ,),
)
def check_student_work(args: CheckStudentWorkArgs, ctx: ToolContext) -> Dict[str, Any]:
    raw_steps = [s for s in (args.steps or []) if (s or "").strip()]
    if len(raw_steps) < 2:
        return {
            "error": (
                "Give at least two steps — with one there is nothing to check it "
                "against. For a single claimed result use verify_result instead."
            )
        }
    overrides = _dimension_overrides(args.symbol_meanings)

    parsed: List[Dict[str, Any]] = []
    for index, text in enumerate(raw_steps, start=1):
        entry: Dict[str, Any] = {"step": index, "as_written": text}
        relation = split_relation(text)
        try:
            if relation:
                entry["kind"] = "equation"
                entry["left"] = relation[0]
                entry["right"] = relation[1]
                parse(relation[0])
                parse(relation[1])
            else:
                entry["kind"] = "expression"
                entry["expression"] = from_latex(text)
                parse(entry["expression"])
        except ExpressionError as exc:
            entry["kind"] = "unparsed"
            entry["problem"] = str(exc)
        parsed.append(entry)

    # Dimensional consistency, step by step — a step can be wrong on its own.
    dimension_problems = []
    for entry in parsed:
        if entry["kind"] == "equation":
            left = _dimension_report(entry["left"], overrides)
            right = _dimension_report(entry["right"], overrides)
            if left.get("ok") and right.get("ok") and left["dimension"] != right["dimension"]:
                dimension_problems.append(
                    {
                        "step": entry["step"],
                        "as_written": entry["as_written"],
                        "left": left["dimension"],
                        "right": right["dimension"],
                        "why": (
                            f"the left side is {left['dimension']} and the right side is "
                            f"{right['dimension']} — this line cannot be right whatever "
                            "the algebra"
                        ),
                    }
                )
    first_break: Optional[Dict[str, Any]] = None
    transitions: List[Dict[str, Any]] = []

    for previous, current in zip(parsed, parsed[1:]):
        result = _compare_steps(previous, current)
        transitions.append(result)
        if first_break is None and result.get("follows") is False:
            first_break = result

    payload: Dict[str, Any] = {
        "steps_checked": len(parsed),
        "transitions": transitions,
        "dimension_problems": dimension_problems,
    }
    if any(e["kind"] == "unparsed" for e in parsed):
        payload["unparsed_steps"] = [
            {"step": e["step"], "as_written": e["as_written"], "problem": e["problem"]}
            for e in parsed
            if e["kind"] == "unparsed"
        ]

    if dimension_problems:
        earliest = min(p["step"] for p in dimension_problems)
        payload["first_problem_at_step"] = earliest
        payload["verdict"] = (
            f"Step {earliest} is not dimensionally consistent — "
            + next(p["why"] for p in dimension_problems if p["step"] == earliest)
            + ". Start there: it is the kind of mistake a student can learn to catch "
            "for themselves."
        )
    elif first_break:
        payload["first_problem_at_step"] = first_break["to_step"]
        payload["verdict"] = (
            f"Their algebra breaks going from step {first_break['from_step']} to step "
            f"{first_break['to_step']}. Everything before that is sound — say so, then "
            "work on that one line."
        )
    else:
        payload["first_problem_at_step"] = None
        payload["verdict"] = (
            "Every step follows from the one before it, and each is dimensionally "
            "consistent. If their final answer is still wrong, the error is in the "
            "setup — the first equation itself, or a misread of the question — not in "
            "the algebra."
        )
    return payload


def _compare_steps(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Does `current` follow from `previous`? Decided by evaluating at random points."""
    base = {
        "from_step": previous["step"],
        "to_step": current["step"],
    }
    if previous["kind"] == "unparsed" or current["kind"] == "unparsed":
        return {**base, "follows": None, "note": "A step could not be parsed."}

    if previous["kind"] == "expression" and current["kind"] == "expression":
        return {**base, **_expressions_equal(previous["expression"], current["expression"])}

    if previous["kind"] == "equation" and current["kind"] == "equation":
        return {**base, **_equations_equivalent(previous, current)}

    return {
        **base,
        "follows": None,
        "note": (
            "One step is an equation and the other an expression, so there is nothing "
            "to compare directly."
        ),
    }


def _expressions_equal(first: str, second: str) -> Dict[str, Any]:
    symbols = sorted(free_symbols(parse(first)) | free_symbols(parse(second)))
    points = probe_points(symbols)
    compared = 0
    for point in points:
        try:
            a = float(evaluate(first, point.values))
            b = float(evaluate(second, point.values))
        except (ExpressionError, ValueError, ZeroDivisionError, OverflowError):
            continue
        if not (math.isfinite(a) and math.isfinite(b)):
            continue
        compared += 1
        if not _close(a, b):
            def fails_at(values: Dict[str, float]) -> Optional[Dict[str, Any]]:
                try:
                    x = float(evaluate(first, values))
                    y = float(evaluate(second, values))
                except (ExpressionError, ValueError, ZeroDivisionError, OverflowError):
                    return None
                if not (math.isfinite(x) and math.isfinite(y)) or _close(x, y):
                    return None
                return {"step_before": round(x, 6), "step_after": round(y, 6)}

            nicer = _presentable_failure(symbols, fails_at)
            return {
                "follows": False,
                "points_tested": compared,
                "counterexample": nicer or {
                    "at": point.describe(),
                    "step_before": round(a, 6),
                    "step_after": round(b, 6),
                },
                "why": (
                    "The two expressions are not equal — at the values above they give "
                    "different numbers, so this is not a valid rearrangement."
                ),
            }
    if compared < _MIN_USABLE_PROBES:
        return {"follows": None, "note": "Undefined at most sample points; check by hand."}
    return {"follows": True, "points_tested": compared, "why": "Algebraically equal."}


def _isolated_unknown(entry: Dict[str, Any]) -> Optional[str]:
    """The symbol sitting alone on one side, e.g. 'a' in `a = g*sin(theta)`."""
    for side, other in ((entry["left"], entry["right"]), (entry["right"], entry["left"])):
        name = side.strip()
        if name.isidentifier() and name not in free_symbols(parse(other)):
            return name
    return None


def _other_side(entry: Dict[str, Any], unknown: str) -> str:
    return entry["right"] if entry["left"].strip() == unknown else entry["left"]


def _equations_equivalent(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Does the later equation say the same thing as the earlier one?

    Random probing alone cannot answer this: at a point where neither equation is
    satisfied, both residuals are just numbers and their ratio says nothing about
    whether the solutions agree. `m*a = m*g - f` and `a = m*g - f` look equally fine
    that way, and the second is a student's most common mistake.

    So the unknown gets substituted instead. When the later step isolates it, putting
    that expression back into the earlier equation must leave zero — that is a proof
    the step preserved the solution, and when it does not, the leftover is a concrete
    counterexample to show the student.
    """
    prev_unknown = _isolated_unknown(previous)
    curr_unknown = _isolated_unknown(current)

    # Both sides solved for the same thing: compare the two expressions directly.
    if prev_unknown and curr_unknown and prev_unknown == curr_unknown:
        result = _expressions_equal(
            _other_side(previous, prev_unknown), _other_side(current, curr_unknown)
        )
        if result.get("follows"):
            result["why"] = f"Both steps give {prev_unknown}, and the two expressions are equal."
        return result

    # The later step isolates the unknown: substitute it into the earlier equation.
    if curr_unknown:
        checked = _residual_after_substitution(previous, curr_unknown, _other_side(current, curr_unknown))
        if checked is not None:
            return checked

    # Only the earlier step isolates it: substitute into the later equation.
    if prev_unknown:
        checked = _residual_after_substitution(current, prev_unknown, _other_side(previous, prev_unknown))
        if checked is not None:
            return checked

    # Neither is solved for anything: compare the residuals as expressions, which
    # catches a term that changed while allowing both sides to be scaled by a constant.
    return _residuals_proportional(previous, current)


def _residual_after_substitution(
    equation: Dict[str, Any], unknown: str, expression: str
) -> Optional[Dict[str, Any]]:
    """Substitute `unknown = expression` into `equation` and require it to balance."""
    residual = f"({equation['left']}) - ({equation['right']})"
    try:
        tree = parse(residual)
        value_tree = parse(expression)
    except ExpressionError:
        return None
    if unknown not in free_symbols(tree):
        return None

    symbols = sorted((free_symbols(tree) | free_symbols(value_tree)) - {unknown})
    compared = 0
    for point in probe_points(symbols):
        bindings = dict(point.values)
        try:
            bindings[unknown] = float(evaluate(value_tree, bindings))
            left_over = float(evaluate(tree, bindings))
        except (ExpressionError, ValueError, ZeroDivisionError, OverflowError):
            continue
        if not math.isfinite(left_over):
            continue
        compared += 1
        scale = max(
            abs(bindings[unknown]),
            *(abs(v) for v in point.values.values()),
            1.0,
        )
        if abs(left_over) > 1e-7 * scale:
            def fails_at(values: Dict[str, float]) -> Optional[Dict[str, Any]]:
                probe = dict(values)
                try:
                    probe[unknown] = float(evaluate(value_tree, probe))
                    residual_v = float(evaluate(tree, probe))
                except (ExpressionError, ValueError, ZeroDivisionError, OverflowError):
                    return None
                if not math.isfinite(residual_v):
                    return None
                limit = 1e-7 * max(abs(probe[unknown]), *(abs(v) for v in values.values()), 1.0)
                if abs(residual_v) <= limit:
                    return None
                return {
                    f"{unknown}_from_later_step": round(probe[unknown], 6),
                    "earlier_equation_left_minus_right": round(residual_v, 6),
                }

            nicer = _presentable_failure(symbols, fails_at)
            return {
                "follows": False,
                "points_tested": compared,
                "counterexample": nicer or {
                    "at": point.describe(),
                    f"{unknown}_from_later_step": round(bindings[unknown], 6),
                    "earlier_equation_left_minus_right": round(left_over, 6),
                },
                "why": (
                    f"Substituting this step's {unknown} back into the previous equation "
                    "does not balance it — the two are not the same equation, so "
                    "something was dropped or mis-divided on this line."
                ),
            }
    if compared < _MIN_USABLE_PROBES:
        return None
    return {
        "follows": True,
        "points_tested": compared,
        "why": (
            f"Substituting this step's {unknown} back into the previous equation leaves "
            "zero, so the rearrangement is sound."
        ),
    }


def _residuals_proportional(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Neither step isolates a symbol, so compare what actually solves each equation.

    Scaling both sides by a variable is legal — `\\frac{1}{2} m v^2 = m g h` becoming
    `v^2 = 2 g h` divided through by `m/2` — so requiring a constant ratio rejects
    correct work. Requiring only a non-zero ratio accepts incorrect work. The question
    that actually matters is whether the two equations have the same solutions, so
    that is what gets tested: solve one numerically and check the other vanishes there.
    """
    first = f"({previous['left']}) - ({previous['right']})"
    second = f"({current['left']}) - ({current['right']})"
    try:
        first_tree, second_tree = parse(first), parse(second)
    except ExpressionError as exc:
        return {"follows": None, "note": str(exc)}

    shared = sorted(free_symbols(first_tree) & free_symbols(second_tree))
    if not shared:
        return {"follows": None, "note": "The two steps share no symbols to compare."}

    # Constant ratio is the cheap, certain case: both sides scaled by a number.
    ratios: List[float] = []
    for point in probe_points(shared + sorted(
        (free_symbols(first_tree) | free_symbols(second_tree)) - set(shared)
    )):
        try:
            a = float(evaluate(first_tree, point.values))
            b = float(evaluate(second_tree, point.values))
        except (ExpressionError, ValueError, ZeroDivisionError, OverflowError):
            continue
        if math.isfinite(a) and math.isfinite(b) and abs(b) > 1e-12:
            ratios.append(a / b)
    if len(ratios) >= _MIN_USABLE_PROBES:
        lo, hi = min(ratios), max(ratios)
        if lo > 0 and abs(hi / lo - 1.0) < 1e-9:
            return {
                "follows": True,
                "points_tested": len(ratios),
                "why": "Both sides were scaled by the same constant, so nothing changed.",
            }

    return _roots_agree(first_tree, second_tree, shared)


def _solve_for(tree, symbol: str, others: Dict[str, float]) -> Optional[float]:
    """A root of `tree` in one symbol, by scanning for a sign change then bisecting."""

    def value_at(x: float) -> Optional[float]:
        bindings = dict(others)
        bindings[symbol] = x
        try:
            out = float(evaluate(tree, bindings))
        except (ExpressionError, ValueError, ZeroDivisionError, OverflowError):
            return None
        return out if math.isfinite(out) else None

    grid = [(-40 + 80 * i / 160) for i in range(161)]
    previous_x = previous_y = None
    for x in grid:
        y = value_at(x)
        if y is None:
            previous_x = previous_y = None
            continue
        if abs(y) < 1e-14:
            return x
        if previous_y is not None and (previous_y < 0) != (y < 0):
            lo, hi = previous_x, x
            for _ in range(80):
                mid = (lo + hi) / 2
                mid_y = value_at(mid)
                if mid_y is None:
                    return None
                if (mid_y < 0) == (value_at(lo) < 0):  # type: ignore[operator]
                    lo = mid
                else:
                    hi = mid
            return (lo + hi) / 2
        previous_x, previous_y = x, y
    return None


def _roots_agree(first_tree, second_tree, shared: List[str]) -> Dict[str, Any]:
    """Solve each equation for a shared symbol and require the other to vanish there."""
    all_symbols = sorted(free_symbols(first_tree) | free_symbols(second_tree))
    checked = 0
    for symbol in shared:
        others_names = [n for n in all_symbols if n != symbol]
        for point in probe_points(others_names, count=6):
            for solve_tree, other_tree, which in (
                (second_tree, first_tree, "this step"),
                (first_tree, second_tree, "the previous step"),
            ):
                root = _solve_for(solve_tree, symbol, point.values)
                if root is None:
                    continue
                bindings = dict(point.values)
                bindings[symbol] = root
                try:
                    residual = float(evaluate(other_tree, bindings))
                except (ExpressionError, ValueError, ZeroDivisionError, OverflowError):
                    continue
                if not math.isfinite(residual):
                    continue
                checked += 1
                scale = max(abs(root), *(abs(v) for v in point.values.values()), 1.0)
                if abs(residual) > 1e-6 * scale:
                    return {
                        "follows": False,
                        "solved_for": symbol,
                        "counterexample": {
                            "at": point.describe(),
                            f"{symbol}_solving_{which.replace(' ', '_')}": round(root, 6),
                            "other_equation_left_minus_right": round(residual, 6),
                        },
                        "why": (
                            f"Solving {which} gives {symbol}={root:.4g}, which does not "
                            "satisfy the other one — so this step changed the equation "
                            "rather than rearranging it."
                        ),
                    }
    if not checked:
        return {
            "follows": None,
            "note": (
                "Could not find a solution to compare against numerically, so this step "
                "is unverified — check it by hand rather than trusting it."
            ),
        }
    return {
        "follows": True,
        "roots_compared": checked,
        "why": (
            "Both equations are solved by the same values, so the step is a valid "
            "rearrangement (including multiplying or dividing through by a variable)."
        ),
    }


# ── 4. units ──────────────────────────────────────────────────────────────────

class ConvertUnitsArgs(BaseModel):
    value: float = Field(..., description="The number to convert, e.g. 30.")
    from_unit: str = Field(
        ...,
        description="The unit it is in: 'km/h', 'eV', 'g/cm**3', 'rpm', 'mmHg'.",
    )
    to_unit: str = Field(
        ...,
        description="The unit wanted: 'm/s', 'J', 'kg/m**3', 'rad/s', 'Pa'.",
    )


@tool(
    name="convert_units",
    description=(
        "Convert a quantity between units, refusing the conversion when the two units "
        "measure different things. JEE problems mix km/h with m/s, grams with "
        "kilograms, eV with joules and cm with metres, and a slip there produces an "
        "answer that is wrong by a factor of a thousand while looking perfectly "
        "reasonable. Use it whenever the question's units differ from the ones you are "
        "calculating in."
    ),
    args_model=ConvertUnitsArgs,
    scopes=(SCOPE_READ,),
)
def convert_units(args: ConvertUnitsArgs, ctx: ToolContext) -> Dict[str, Any]:
    try:
        from_factor, from_dim = dims.parse_unit(args.from_unit)
        to_factor, to_dim = dims.parse_unit(args.to_unit)
    except (dims.DimensionError, ExpressionError) as exc:
        return {"error": str(exc)}

    if from_dim != to_dim:
        return {
            "error": (
                f"Cannot convert {args.from_unit} to {args.to_unit}: the first is "
                f"{from_dim.describe()} and the second is {to_dim.describe()}. These "
                "measure different physical quantities."
            ),
            "from_dimension": str(from_dim),
            "to_dimension": str(to_dim),
        }
    if to_factor == 0:
        return {"error": f"'{args.to_unit}' has no scale to convert into."}

    si = args.value * from_factor
    converted = si / to_factor
    return {
        "value": args.value,
        "from_unit": args.from_unit,
        "to_unit": args.to_unit,
        "converted": converted,
        "rounded_3sf": float(f"{converted:.3g}"),
        "in_si": si,
        "dimension": from_dim.describe(),
        "statement": f"{args.value:g} {args.from_unit} = {converted:.6g} {args.to_unit}",
    }
