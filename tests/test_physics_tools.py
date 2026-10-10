"""Behaviour tests for the physics reasoning tools.

These assert the properties that make the tools worth trusting:

* a wrong formula is reported wrong, and a right one is not reported wrong;
* a claimed answer that does not satisfy its equation is caught, with a counterexample;
* a student's first broken step is identified — and correct working is left alone,
  which is the harder half (a checker that flags everything is useless);
* units are converted only between quantities that measure the same thing.
"""

from __future__ import annotations

import pytest

from agents.runtime.toolbelt import ToolContext
from mcp_tools.physics_tools import (
    CheckDimensionsArgs,
    CheckStudentWorkArgs,
    ConvertUnitsArgs,
    VerifyResultArgs,
    check_dimensions,
    check_student_work,
    convert_units,
    verify_result,
)
from utils import dimensions as dims
from utils.mathexpr import ExpressionError, evaluate, from_latex, parse


@pytest.fixture()
def ctx() -> ToolContext:
    return ToolContext(user_id="test-user", session_id="test-session", state={})


# ── the LaTeX the agent actually writes has to parse ───────────────────────────
@pytest.mark.parametrize(
    "latex,plain",
    [
        (r"\frac{v^2}{2g}", "((v**2)/(2*g))"),
        (r"\sqrt{2 g h}", "sqrt(2*g*h)"),
        (r"m g \sin\theta", "m*g*sin(theta)"),
        (r"\mu_k m g", "mu_k*m*g"),
        (r"\frac{1}{2}I\omega^2", "((1)/(2))*I*omega**2"),
        (r"2 \pi \sqrt{\frac{l}{g}}", "2*pi*sqrt(((l)/(g)))"),
    ],
)
def test_latex_becomes_evaluable(latex: str, plain: str) -> None:
    assert from_latex(latex) == plain
    parse(latex)  # must not raise


def test_evaluator_refuses_anything_but_maths() -> None:
    for hostile in [
        "__import__('os')",
        "().__class__",
        "open('/etc/passwd')",
        "[x for x in range(3)]",
        "lambda: 1",
        "a if b else c",
    ]:
        with pytest.raises(ExpressionError):
            parse(hostile)


def test_evaluator_binds_variables() -> None:
    assert evaluate(r"\frac{1}{2} m v^2", {"m": 2, "v": 3}) == pytest.approx(9.0)


# ── dimensions ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "formula,expect,consistent",
    [
        (r"v = \sqrt{2 g h}", "velocity", True),
        (r"v = \sqrt{2 g h^2}", "velocity", False),
        (r"T = 2\pi\sqrt{\frac{l}{g}}", "time", True),
        (r"F = \frac{m v^2}{r}", "force", True),
        (r"F = \frac{m v}{r}", "force", False),
        (r"E = \frac{1}{2} m v^2 + m g h", "energy", True),
        (r"P = \frac{W}{t}", "power", True),
        (r"\omega = \sqrt{\frac{k}{m}}", None, True),
    ],
)
def test_dimensional_consistency(ctx, formula, expect, consistent) -> None:
    result = check_dimensions(CheckDimensionsArgs(expression=formula, expect=expect), ctx)
    assert result.get("consistent") is consistent, result


def test_dimension_mismatch_names_both_sides(ctx) -> None:
    result = check_dimensions(CheckDimensionsArgs(expression="F = m*v"), ctx)
    assert result["consistent"] is False
    assert "M L T^-2" in result["verdict"] and "M L T^-1" in result["verdict"]


def test_ambiguous_symbol_is_inferred_when_only_one_reading_balances(ctx) -> None:
    """`T = 2*pi*sqrt(l/g)` must not need a note explaining that T is a period."""
    result = check_dimensions(
        CheckDimensionsArgs(expression=r"T = 2\pi\sqrt{\frac{l}{g}}"), ctx
    )
    assert result["consistent"] is True
    assert result["read_ambiguous_symbols_as"]["T"].startswith("period")


def test_ambiguous_symbol_refuses_to_guess_when_undecidable(ctx) -> None:
    result = check_dimensions(CheckDimensionsArgs(expression="T"), ctx)
    assert result["ok"] is False
    assert "ambiguous" in result["problem"].lower()


def test_expression_dimension_is_reported_with_its_common_name(ctx) -> None:
    result = check_dimensions(CheckDimensionsArgs(expression="m*v"), ctx)
    assert result["dimension"] == "M L T^-1"
    assert "momentum" in result["quantity"]


def test_trig_argument_must_be_dimensionless(ctx) -> None:
    result = check_dimensions(CheckDimensionsArgs(expression=r"\sin(m*g)"), ctx)
    assert result["ok"] is False
    assert "dimensionless" in result["problem"]


def test_sqrt_gives_a_half_power_exactly() -> None:
    assert str(dims.dimension_of("sqrt(m)")) == "M^1/2"


# ── verifying a result ────────────────────────────────────────────────────────
def test_correct_result_passes_every_check(ctx) -> None:
    result = verify_result(
        VerifyResultArgs(
            answer="a = g*(sin(theta) - mu*cos(theta))",
            relation="m*a = m*g*sin(theta) - mu*m*g*cos(theta)",
            expect_quantity="acceleration",
            limiting_cases={"mu -> 0": "g*sin(theta)"},
        ),
        ctx,
    )
    assert result["passed"] is True
    assert result["checks"]["satisfies_relation"]["satisfies"] is True
    assert result["checks"]["limits"]["mu -> 0"]["matches_expected"] is True


def test_sign_slip_is_caught_with_a_counterexample(ctx) -> None:
    result = verify_result(
        VerifyResultArgs(
            answer="a = g*(sin(theta) + mu*cos(theta))",
            relation="m*a = m*g*sin(theta) - mu*m*g*cos(theta)",
        ),
        ctx,
    )
    assert result["passed"] is False
    satisfies = result["checks"]["satisfies_relation"]
    assert satisfies["satisfies"] is False
    assert "at" in satisfies["counterexample"]


def test_wrong_dimension_fails_even_when_the_algebra_is_self_consistent(ctx) -> None:
    result = verify_result(
        VerifyResultArgs(answer="v = 2*g*h", expect_quantity="velocity"), ctx
    )
    assert result["passed"] is False
    assert result["checks"]["dimensions"]["matches_expected"] is False


def test_limits_are_probed_when_none_are_given(ctx) -> None:
    result = verify_result(VerifyResultArgs(answer="a = g*sin(theta)"), ctx)
    limits = result["checks"]["limits"]
    assert "theta -> 0" in limits and "g -> inf" in limits


# ── checking a student's working ──────────────────────────────────────────────
def test_correct_working_is_not_flagged(ctx) -> None:
    result = check_student_work(
        CheckStudentWorkArgs(
            steps=[
                r"m a = m g \sin\theta - \mu m g \cos\theta",
                r"a = g \sin\theta - \mu g \cos\theta",
                r"a = g (\sin\theta - \mu \cos\theta)",
            ]
        ),
        ctx,
    )
    assert result["first_problem_at_step"] is None, result


def test_dividing_through_by_a_variable_is_allowed(ctx) -> None:
    """`(1/2) m v^2 = m g h` -> `v^2 = 2 g h` divides by m/2 and is correct."""
    result = check_student_work(
        CheckStudentWorkArgs(
            steps=[r"\frac{1}{2} m v^2 = m g h", r"v^2 = 2 g h", r"v = \sqrt{2 g h}"]
        ),
        ctx,
    )
    assert result["first_problem_at_step"] is None, result


@pytest.mark.parametrize(
    "steps,broken_step",
    [
        # forgot to divide the friction term by m
        ([r"m a = m g \sin\theta - \mu m g \cos\theta",
          r"a = g \sin\theta - \mu m g \cos\theta"], 2),
        # square-rooted only part of the right-hand side
        ([r"\frac{1}{2} m v^2 = m g h", r"v^2 = 2 g h", r"v = 2 \sqrt{g h}"], 3),
        # lost the square
        (["(a + b)**2", "a**2 + 2*a*b + b**2", "a**2 + b**2"], 3),
        # dimensionally impossible line
        ([r"F = m a", r"F = m v"], 2),
    ],
)
def test_first_broken_step_is_found(ctx, steps, broken_step) -> None:
    result = check_student_work(CheckStudentWorkArgs(steps=steps), ctx)
    assert result["first_problem_at_step"] == broken_step, result


def test_a_break_comes_with_numbers_that_prove_it(ctx) -> None:
    result = check_student_work(
        CheckStudentWorkArgs(steps=["(a + b)**2", "a**2 + b**2"]), ctx
    )
    failing = [t for t in result["transitions"] if t.get("follows") is False]
    assert failing, result
    counterexample = failing[0]["counterexample"]
    assert "at" in counterexample
    assert counterexample["step_before"] != counterexample["step_after"]


def test_one_step_is_rejected_as_nothing_to_check(ctx) -> None:
    result = check_student_work(CheckStudentWorkArgs(steps=["v = u + a*t"]), ctx)
    assert "error" in result


def test_unparseable_step_is_reported_not_swallowed(ctx) -> None:
    result = check_student_work(
        CheckStudentWorkArgs(steps=["v = u + a*t", "v = u + a*t)("]), ctx
    )
    assert result.get("unparsed_steps")


# ── units ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "value,from_unit,to_unit,expected",
    [
        (30, "km/h", "m/s", 8.3333333),
        (1, "eV", "J", 1.602176634e-19),
        (1, "g/cm**3", "kg/m**3", 1000.0),
        (300, "rpm", "Hz", 5.0),
        (760, "mmHg", "atm", 1.0),
        (1, "kWh", "J", 3.6e6),
    ],
)
def test_unit_conversion(ctx, value, from_unit, to_unit, expected) -> None:
    result = convert_units(
        ConvertUnitsArgs(value=value, from_unit=from_unit, to_unit=to_unit), ctx
    )
    assert result["converted"] == pytest.approx(expected, rel=1e-6)


def test_conversion_between_different_quantities_is_refused(ctx) -> None:
    result = convert_units(ConvertUnitsArgs(value=5, from_unit="kg", to_unit="m"), ctx)
    assert "error" in result
    assert "different physical quantities" in result["error"]


def test_unknown_unit_says_what_it_knows(ctx) -> None:
    result = convert_units(ConvertUnitsArgs(value=1, from_unit="furlong", to_unit="m"), ctx)
    assert "Unknown unit" in result["error"]


# ── registration ──────────────────────────────────────────────────────────────
def test_tools_are_registered_for_the_agent() -> None:
    from agents.learning_agent import _import_tools
    from agents.runtime.toolbelt import registry

    _import_tools()
    names = set(registry())
    assert {"check_dimensions", "verify_result", "check_student_work", "convert_units"} <= names


# ── the schema a provider actually receives ───────────────────────────────────
def test_no_tool_schema_carries_a_dangling_reference() -> None:
    """A nested args model must be inlined, not left pointing at deleted $defs.

    Dropping `$defs` while leaving the `$ref` that needs it produced a schema some
    providers reject outright and others accept while never calling the tool — a tool
    that looks registered and is silently unusable.
    """
    import json

    from agents.learning_agent import _import_tools
    from agents.runtime.toolbelt import registry

    _import_tools()
    for name, spec in registry().items():
        rendered = json.dumps(spec.schema())
        assert "$ref" not in rendered, f"{name} schema has an unresolved $ref"
        assert "$defs" not in rendered, f"{name} schema still carries $defs"


def test_nested_models_are_inlined_with_their_fields() -> None:
    from pydantic import BaseModel

    from agents.runtime.toolbelt import SCOPE_READ, ToolSpec

    class Inner(BaseModel):
        amount: float

    class Outer(BaseModel):
        item: Inner

    spec = ToolSpec(
        name="probe",
        description="d",
        args_model=Outer,
        handler=lambda args, ctx: None,
        scopes=frozenset({SCOPE_READ}),
    )
    item = spec.schema()["function"]["parameters"]["properties"]["item"]
    assert item["type"] == "object"
    assert "amount" in item["properties"]


# ── quality of the explanation, not just the verdict ──────────────────────────
def test_internal_mismatch_is_reported_as_wrong_not_as_uncertain(ctx) -> None:
    """`a = g*sin(t) - mu*m*g*cos(t)` subtracts a force from an acceleration.

    Every symbol is known, so this is a proof the line is wrong. Reporting it as
    "could not determine" would waste the one check that was certain.
    """
    result = check_dimensions(
        CheckDimensionsArgs(expression="a = g*sin(theta) - mu*m*g*cos(theta)"), ctx
    )
    assert result["consistent"] is False
    assert "not even internally consistent" in result["verdict"]


def test_counterexamples_use_values_a_student_would_recognise(ctx) -> None:
    """mu must be below 1 and an angle below ~80 degrees, or the example teaches nothing."""
    result = check_student_work(
        CheckStudentWorkArgs(
            steps=[
                "m*a = m*g*sin(theta) - mu*m*g*cos(theta)",
                "a = g*sin(theta) - mu*m*g*cos(theta)",
            ]
        ),
        ctx,
    )
    failing = [t for t in result["transitions"] if t.get("follows") is False]
    assert failing
    at = failing[0]["counterexample"]["at"]
    values = dict(
        part.split("=") for part in at.split(", ")
    )
    assert 0.0 < float(values["mu"]) < 1.0, at
    assert 0.0 < float(values["theta"]) < 1.5, at
    assert float(values["g"]) == pytest.approx(9.81), at
