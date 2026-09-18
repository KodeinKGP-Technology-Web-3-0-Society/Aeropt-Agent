"""Safe evaluation of a physics expression, with free variables.

`mcp_tools/compute_tools.py` deliberately has no variables — it is a calculator, and
its docstring explains why. The tools that check dimensions, probe limiting cases or
compare a student's algebra need the same whitelisted evaluator *with* symbols bound
to values, so the whitelist and the AST walk live here and both consumers share them.
One whitelist is the point: two copies drift, and a security-relevant list that has
drifted is worse than no list.

Nothing here uses `eval`. An expression is parsed to an AST, every node type is
checked against a whitelist, and only whitelisted functions can be called. There is
no attribute access, no subscripting, no assignment, no comprehension, no lambda.
"""

from __future__ import annotations

import ast
import math
import random
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Callable, Iterable, Mapping

# ── the whitelist ─────────────────────────────────────────────────────────────

FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "sqrt": math.sqrt,
    "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "exp": math.exp,
    "log": math.log,
    "ln": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "cot": lambda x: 1.0 / math.tan(x),
    "sec": lambda x: 1.0 / math.cos(x),
    "csc": lambda x: 1.0 / math.sin(x),
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

#: Mathematical constants only. Physics constants like `g` stay *symbols* here, so a
#: dimension check can reason about them; `compute_tools` binds g=9.81 for its own
#: no-variables contract by passing it in as an extra constant.
CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
}

#: Trig and friends take a plain number and return one. Used by the dimension walker
#: to insist their argument is dimensionless.
DIMENSIONLESS_FUNCTIONS = frozenset(
    {
        "exp", "log", "ln", "log10", "log2",
        "sin", "cos", "tan", "cot", "sec", "csc",
        "asin", "acos", "atan", "atan2",
        "sinh", "cosh", "tanh",
        "degrees", "radians", "factorial", "comb", "perm",
    }
)

_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a ** b,
    ast.Mod: lambda a, b: a % b,
    ast.FloorDiv: lambda a, b: a // b,
}

MAX_POW_EXPONENT = 1_000
MAX_FACTORIAL = 500
MAX_LENGTH = 800


class ExpressionError(ValueError):
    """Raised for anything the evaluator will not accept. Message is student-safe."""


# ── LaTeX in, plain syntax out ────────────────────────────────────────────────

_GREEK = (
    "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa "
    "lambda mu nu xi pi rho sigma tau upsilon phi varphi chi psi omega "
    "Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega"
).split()

_LATEX_FUNCS = (
    "sqrt sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh "
    "exp ln log max min"
).split()


def from_latex(text: str) -> str:
    r"""Turn the LaTeX the agent writes everywhere into plain evaluator syntax.

    The rest of this service speaks LaTeX (see `utils/latex_guidelines.py`), so a tool
    that only accepted `m*g*sin(theta)` would be a tool the agent gets wrong half the
    time. `\frac{v^2}{2g}` and `v**2/(2*g)` both work.

    Implicit multiplication is only inserted where it is unambiguous — after a digit
    or a closing bracket. `mg` stays one symbol called `mg`, because guessing that it
    means `m*g` would silently change a student's equation.
    """
    import re

    s = (text or "").strip()
    s = s.replace("$$", " ").replace("$", " ")
    s = re.sub(r"\\(left|right|!|,|;|:|quad|qquad|displaystyle|text|mathrm|mathbf)\b", " ", s)
    s = s.replace("\\{", "(").replace("\\}", ")")

    # Separate adjacent macros so "\sin\theta" does not collapse into "sintheta",
    # and a macro glued to the symbol before it ("I\omega") into "Iomega".
    s = re.sub(r"(\\[a-zA-Z]+)(?=\\)", r"\1 ", s)
    s = re.sub(r"(?<=[A-Za-z0-9)])(?=\\[a-zA-Z])", " ", s)

    # \frac{a}{b} and \dfrac/\tfrac -> (a)/(b); innermost first so nesting works.
    frac = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
    for _ in range(8):
        s, n = frac.subn(r"((\1)/(\2))", s)
        if not n:
            break

    # \sqrt[n]{x} -> (x)**(1/n), \sqrt{x} -> sqrt(x)
    s = re.sub(r"\\sqrt\s*\[([^\]]*)\]\s*\{([^{}]*)\}", r"((\2)**(1/(\1)))", s)
    s = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"sqrt(\1)", s)

    for name in _LATEX_FUNCS:
        s = re.sub(rf"\\{name}(?![a-zA-Z])", name, s)
    s = s.replace("arcsin", "asin").replace("arccos", "acos").replace("arctan", "atan")

    for name in _GREEK:
        s = re.sub(rf"\\{name}(?![a-zA-Z])", name, s)

    s = s.replace("\\times", "*").replace("\\cdot", "*").replace("\\div", "/")
    s = s.replace("\\pm", "+").replace("\\approx", "=").replace("\\to", " ")
    s = re.sub(r"\\[a-zA-Z]+", " ", s)          # anything left: drop the macro
    s = s.replace("{", "(").replace("}", ")")
    s = s.replace("^", "**")

    # A function with no brackets: "\sin\theta" arrives as "sin theta" and means
    # sin(theta), never sin*theta.
    names = "|".join(sorted(FUNCTIONS, key=len, reverse=True))
    s = re.sub(
        rf"\b({names})\s+([A-Za-z_][A-Za-z_0-9]*|\d+(?:\.\d+)?)(?!\s*\()",
        r"\1(\2)",
        s,
    )

    # Unambiguous implicit multiplication only.
    s = re.sub(r"(\d)\s*([A-Za-z(])", r"\1*\2", s)
    s = re.sub(r"(\))\s*([A-Za-z0-9(])", r"\1*\2", s)
    # Two names separated by whitespace ("m g", "2 a s") can only be a product: no
    # valid expression puts two identifiers side by side. A function name on the left
    # is excluded — it was handled above.
    ident = r"[A-Za-z_][A-Za-z_0-9]*"
    for _ in range(4):
        s, n = re.subn(
            rf"\b({ident}|\d)\s+({ident}|\d|\()",
            lambda m: (
                m.group(0)
                if m.group(1) in FUNCTIONS
                else f"{m.group(1)}*{m.group(2)}"
            ),
            s,
        )
        if not n:
            break
    s = re.sub(r"\*\*\s*\*", "**", s)
    return re.sub(r"\s+", " ", s).strip()


def split_relation(text: str) -> tuple[str, str] | None:
    """Split `lhs = rhs` into its two sides, or None when there is no single `=`."""
    plain = from_latex(text)
    parts = [p for p in plain.split("=") if p.strip()]
    if plain.count("=") != 1 or len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


# ── parse + validate ──────────────────────────────────────────────────────────

def parse(expression: str, *, latex_ok: bool = True) -> ast.Expression:
    """Parse one expression into a validated AST. Raises ExpressionError."""
    text = (expression or "").strip()
    if not text:
        raise ExpressionError("Empty expression.")
    if len(text) > MAX_LENGTH:
        raise ExpressionError(f"Expression is too long (limit {MAX_LENGTH} characters).")
    if latex_ok and ("\\" in text or "^" in text or "$" in text or "{" in text):
        text = from_latex(text)
    if "=" in text:
        raise ExpressionError(
            "This takes one expression, not an equation. Split it at the '=' and pass "
            "each side, or use a tool that accepts a relation."
        )
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Could not parse {text!r}: {exc.msg}") from exc
    _validate(tree.body)
    return tree


def _validate(node: ast.AST) -> None:
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ExpressionError(f"Unsupported literal: {node.value!r}")
        return
    if isinstance(node, ast.Name):
        return
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BIN_OPS:
            raise ExpressionError(f"Unsupported operator: {type(node.op).__name__}")
        if isinstance(node.op, ast.Pow):
            exp = node.right
            if isinstance(exp, ast.Constant) and isinstance(exp.value, (int, float)):
                if abs(exp.value) > MAX_POW_EXPONENT:
                    raise ExpressionError(f"Exponent {exp.value} is too large to evaluate.")
        _validate(node.left)
        _validate(node.right)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.UAdd, ast.USub)):
            raise ExpressionError(f"Unsupported unary operator: {type(node.op).__name__}")
        _validate(node.operand)
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ExpressionError("Only direct calls to built-in maths functions are allowed.")
        if node.func.id not in FUNCTIONS:
            raise ExpressionError(f"Unknown function '{node.func.id}'.")
        if node.keywords:
            raise ExpressionError("Keyword arguments are not supported.")
        if node.func.id == "factorial" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                if arg.value > MAX_FACTORIAL:
                    raise ExpressionError(f"factorial({arg.value}) is too large.")
        for arg in node.args:
            _validate(arg)
        return
    if isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            _validate(element)
        return
    raise ExpressionError(f"Unsupported expression element: {type(node).__name__}")


def free_symbols(tree: ast.Expression | ast.AST) -> set[str]:
    """Names that are neither a constant nor a function — the variables."""
    node = tree.body if isinstance(tree, ast.Expression) else tree
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id not in CONSTANTS and sub.id not in FUNCTIONS:
            found.add(sub.id)
    return found


# ── evaluate ──────────────────────────────────────────────────────────────────

def evaluate(
    tree: ast.Expression | str,
    variables: Mapping[str, float] | None = None,
    *,
    extra_constants: Mapping[str, float] | None = None,
) -> float:
    """Evaluate a validated expression with symbols bound to numbers."""
    if isinstance(tree, str):
        tree = parse(tree)
    bindings: dict[str, float] = dict(CONSTANTS)
    if extra_constants:
        bindings.update(extra_constants)
    if variables:
        bindings.update(variables)
    return _eval(tree.body, bindings)


def _eval(node: ast.AST, names: Mapping[str, float]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        if node.id in FUNCTIONS:
            raise ExpressionError(f"'{node.id}' is a function — call it, e.g. {node.id}(x).")
        raise ExpressionError(f"No value given for '{node.id}'.")
    if isinstance(node, ast.BinOp):
        return _BIN_OPS[type(node.op)](_eval(node.left, names), _eval(node.right, names))
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, names)
        return +value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.Call):
        fn = FUNCTIONS[node.func.id]  # type: ignore[union-attr]
        return fn(*[_eval(a, names) for a in node.args])
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval(e, names) for e in node.elts]
    raise ExpressionError(f"Unsupported expression element: {type(node).__name__}")


def rational(value: Any) -> Fraction | None:
    """A constant exponent as an exact fraction, or None when it is not constant."""
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float) and math.isfinite(value):
        frac = Fraction(value).limit_denominator(24)
        return frac if abs(float(frac) - value) < 1e-9 else None
    return None


# ── probing (used for equivalence checks and limiting cases) ───────────────────

@dataclass(frozen=True)
class ProbePoint:
    """One random assignment of values to every free symbol."""

    values: dict[str, float]

    def describe(self) -> str:
        return ", ".join(f"{k}={v:.4g}" for k, v in sorted(self.values.items()))


def probe_points(
    symbols: Iterable[str],
    count: int = 24,
    *,
    seed: int = 20260918,
    low: float = 0.3,
    high: float = 3.7,
) -> list[ProbePoint]:
    """Random positive values for each symbol.

    Positive and away from zero on purpose: physics symbols are usually masses,
    lengths and angles where zero or negative is either meaningless or a special case,
    and probing at 0 would make `1/m` blow up and report a false mismatch. Angles land
    inside (0.3, 3.7) radians, which keeps `tan` away from its pole at pi/2 for most
    draws and still explores past pi/2 for some.
    """
    names = sorted(set(symbols))
    rng = random.Random(seed)
    return [
        ProbePoint({name: rng.uniform(low, high) for name in names}) for _ in range(count)
    ]


#: Plausible ranges for symbols by convention, used when a counterexample is going to
#: be shown to a student. A mismatch at "mu=2.4, theta=3.69 rad" is mathematically
#: valid and pedagogically useless — no coefficient of friction is 2.4, and no incline
#: is 211 degrees. The wide random probes decide *whether* something is wrong; these
#: decide which failing case is worth showing.
PHYSICAL_RANGES: dict[str, tuple[float, float]] = {
    "mu": (0.1, 0.8), "mu_s": (0.2, 0.9), "mu_k": (0.1, 0.7),
    "theta": (0.17, 1.4), "phi": (0.17, 1.4), "alpha": (0.17, 1.4),
    "beta": (0.17, 1.4), "gamma": (0.17, 1.4),
    "g": (9.81, 9.81),
    "m": (0.5, 5.0), "M": (1.0, 10.0), "m_1": (0.5, 5.0), "m_2": (0.5, 5.0),
    "m1": (0.5, 5.0), "m2": (0.5, 5.0),
    "h": (0.5, 20.0), "l": (0.2, 3.0), "L": (0.2, 3.0), "r": (0.1, 2.0),
    "R": (0.1, 2.0), "d": (0.5, 10.0), "x": (0.1, 2.0), "s": (0.5, 20.0),
    "t": (0.1, 5.0), "v": (1.0, 20.0), "u": (1.0, 20.0), "v_0": (1.0, 20.0),
    "a": (0.5, 9.8), "k": (10.0, 500.0), "omega": (0.5, 20.0), "n": (1.0, 4.0),
}
_DEFAULT_PHYSICAL = (0.5, 4.0)


def physical_probe_points(
    symbols: Iterable[str], count: int = 12, *, seed: int = 1729
) -> list[ProbePoint]:
    """Probe values a student would recognise: mu below 1, angles under 80 degrees."""
    names = sorted(set(symbols))
    rng = random.Random(seed)
    points = []
    for _ in range(count):
        values = {}
        for name in names:
            low, high = PHYSICAL_RANGES.get(name, _DEFAULT_PHYSICAL)
            values[name] = low if low == high else rng.uniform(low, high)
        points.append(ProbePoint(values))
    return points
