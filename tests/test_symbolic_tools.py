"""Behaviour tests for the symbolic algebra tool.

The results asserted here are JEE physics a student would be shown, so a regression in
any of them is a regression in what the tutor teaches. Also covered: that sympy is
never handed an unvalidated string, and that results come back as LaTeX, since the rest
of the service speaks LaTeX and re-transcribing is where a correct result turns wrong.
"""

from __future__ import annotations

import pytest

from agents.runtime.toolbelt import ToolContext
from mcp_tools.symbolic_tools import SolveSymbolicallyArgs, solve_symbolically


@pytest.fixture()
def ctx() -> ToolContext:
    return ToolContext(user_id="test-user", session_id="test-session", state={})


def _run(ctx, **kwargs):
    return solve_symbolically(SolveSymbolicallyArgs(**kwargs), ctx)


# ── rearranging ───────────────────────────────────────────────────────────────
def test_solves_the_incline_with_friction(ctx) -> None:
    result = _run(
        ctx,
        operation="solve",
        expression="m*a = m*g*sin(theta) - mu*m*g*cos(theta)",
        symbol="a",
    )
    assert "\\mu \\cos" in result["result"] and "\\sin" in result["result"]
    assert "m" not in result["solutions"][0]["plain"].replace("mu", "")


def test_solves_suvat_for_displacement(ctx) -> None:
    result = _run(ctx, operation="solve", expression="v**2 = u**2 + 2*a*s", symbol="s")
    plain = result["solutions"][0]["plain"].replace(" ", "")
    assert plain in {"(-u**2+v**2)/(2*a)", "(v**2-u**2)/(2*a)"}


def test_solves_pendulum_period_for_length(ctx) -> None:
    result = _run(
        ctx, operation="solve", expression=r"T = 2\pi\sqrt{\frac{l}{g}}", symbol="l"
    )
    plain = result["solutions"][0]["plain"].replace(" ", "")
    assert "T**2" in plain and "pi**2" in plain


def test_projectile_range_returns_both_launch_angles(ctx) -> None:
    """Two angles give the same range, and a student must be shown both."""
    result = _run(
        ctx, operation="solve", expression="R = v**2*sin(2*theta)/g", symbol="theta"
    )
    assert len(result["solutions"]) == 2, result
    assert "pick the physical one" in result["note"]


def test_single_root_trig_solve_warns_about_periodicity(ctx) -> None:
    result = _run(ctx, operation="solve", expression="sin(theta) = 1", symbol="theta")
    if len(result["solutions"]) == 1:
        assert "infinitely many roots" in result["note"]


def test_solve_without_a_symbol_is_refused(ctx) -> None:
    assert "error" in _run(ctx, operation="solve", expression="a = b")


# ── calculus ──────────────────────────────────────────────────────────────────
def test_differentiates_shm_to_velocity_and_acceleration(ctx) -> None:
    velocity = _run(
        ctx, operation="differentiate", expression="A*sin(omega*t)", symbol="t", order=1
    )
    assert velocity["plain"].replace(" ", "") == "A*omega*cos(omega*t)"
    acceleration = _run(
        ctx, operation="differentiate", expression="A*sin(omega*t)", symbol="t", order=2
    )
    assert acceleration["plain"].replace(" ", "") == "-A*omega**2*sin(omega*t)"


def test_definite_integral_gives_spring_energy(ctx) -> None:
    result = _run(ctx, operation="integrate", expression="k*x", symbol="x", at="0 to d")
    assert result["plain"].replace(" ", "") in {"d**2*k/2", "k*d**2/2"}
    assert result["definite"] is True


def test_indefinite_integral_warns_about_the_constant(ctx) -> None:
    result = _run(ctx, operation="integrate", expression="u + a*t", symbol="t")
    assert result["definite"] is False
    assert "constant of integration" in result["note"]


# ── approximations JEE actually uses ──────────────────────────────────────────
def test_small_angle_approximation(ctx) -> None:
    result = _run(
        ctx, operation="approximate", expression="sin(theta)", symbol="theta", order=4
    )
    assert "theta" in result["plain"]
    assert "theta**3/6" in result["plain"].replace(" ", "")


def test_binomial_approximation(ctx) -> None:
    result = _run(ctx, operation="approximate", expression="(1+x)**n", symbol="x", order=3)
    assert "n*x" in result["plain"].replace(" ", "")


def test_relativistic_energy_reduces_to_classical(ctx) -> None:
    result = _run(
        ctx,
        operation="approximate",
        expression="m*c**2/sqrt(1 - v**2/c**2) - m*c**2",
        symbol="v",
        order=4,
    )
    assert result["plain"].replace(" ", "") == "m*v**2/2"


def test_approximation_says_it_is_only_valid_while_small(ctx) -> None:
    result = _run(ctx, operation="approximate", expression="cos(x)", symbol="x", order=3)
    assert "only while that symbol is small" in result["note"]


# ── limits ────────────────────────────────────────────────────────────────────
def test_standard_limit(ctx) -> None:
    result = _run(ctx, operation="limit", expression="sin(x)/x", symbol="x", at="0")
    assert result["plain"] == "1"
    assert result["numeric_check"]["from below"] == pytest.approx(1.0, abs=1e-3)


def test_field_falls_to_zero_at_infinity(ctx) -> None:
    result = _run(ctx, operation="limit", expression="G*M*m/r**2", symbol="r", at="inf")
    assert result["plain"] == "0"


def test_divergent_limit_is_backed_by_numbers(ctx) -> None:
    """At a point where the expression stops being real, sympy's sign is unreliable."""
    result = _run(
        ctx, operation="limit", expression="m/sqrt(1 - v**2/c**2)", symbol="v", at="c"
    )
    assert result["numeric_check"]["from below"] > 1
    assert result.get("disagreement") or result.get("caution")


# ── algebra with numbers in one call ─────────────────────────────────────────
def test_solve_then_substitute_gives_the_number(ctx) -> None:
    result = _run(
        ctx,
        operation="solve",
        expression="m*a = m*g*sin(theta) - mu*m*g*cos(theta)",
        symbol="a",
        substitutions={"g": "9.81", "theta": "pi/6", "mu": "0.2"},
    )
    assert result["with_values"]["rounded_3sf"] == pytest.approx(3.21, abs=0.01)


def test_simplify_uses_the_pythagorean_identity(ctx) -> None:
    result = _run(ctx, operation="simplify", expression="(sin(x)**2 + cos(x)**2)*m*g")
    assert result["plain"].replace(" ", "") in {"g*m", "m*g"}


def test_factor(ctx) -> None:
    result = _run(ctx, operation="factor", expression="m*g*sin(theta) - mu*m*g*cos(theta)")
    assert "g" in result["plain"] and "m" in result["plain"]


# ── safety and shape ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "hostile",
    [
        "__import__('os').system('true')",
        "().__class__.__bases__",
        "open('/etc/passwd').read()",
        "[i for i in range(9)]",
    ],
)
def test_sympy_never_sees_unvalidated_input(ctx, hostile: str) -> None:
    result = _run(ctx, operation="simplify", expression=hostile)
    assert "error" in result


def test_unknown_operation_lists_what_is_available(ctx) -> None:
    result = _run(ctx, operation="integrate_by_parts", expression="x", symbol="x")
    assert "error" in result and "differentiate" in result["error"]


def test_differentiate_defaults_to_once_not_twice(ctx) -> None:
    """A silent second-derivative default would produce wrong physics constantly."""
    result = _run(ctx, operation="differentiate", expression="x**3", symbol="x")
    assert result["order"] == 1
    assert result["latex"] == "3 x^{2}"


def test_registered_and_schema_is_flat() -> None:
    import json

    from agents.learning_agent import _import_tools
    from agents.runtime.toolbelt import registry

    _import_tools()
    spec = registry()["solve_symbolically"]
    assert "$ref" not in json.dumps(spec.schema())


def test_substitute_alone_returns_the_number(ctx) -> None:
    """A regression: this path had no `_raw` and the post-substitution step crashed."""
    result = _run(
        ctx,
        operation="substitute",
        expression="2*pi*sqrt(l/g)",
        substitutions={"l": "1", "g": "9.81"},
    )
    assert "error" not in result
    assert result["rounded_3sf"] == pytest.approx(2.01, abs=0.01)
