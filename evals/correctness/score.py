"""Load the correctness set and grade a tutor reply against its key.

A reply passes only when the final claim matches. A number that shows up in the
middle of a derivation does not. Closed forms are evaluated (``-sinh(pi)/2``),
so a right expression and a right decimal are the same result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

PROBLEMS_PATH = Path(__file__).resolve().parent / "problems.yaml"

_BOXED = re.compile(r"\\boxed\s*\{")
_ANSWER_LINE = re.compile(
    r"(?im)^.*(?:final\s+answer|the\s+answer|answer\s+is|answer\s*[:=]).*$"
)
_NUM = re.compile(
    r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*[x×]\s*10\s*\^?\s*\{?\s*[-+]?\d+\s*\}?)?(?:[eE][-+]?\d+)?"
)


@dataclass
class Expectation:
    kind: str
    value: Optional[float] = None
    low: Optional[float] = None
    high: Optional[float] = None
    letters: str = ""
    abs_tol: float = 0.02
    rel_tol: float = 0.01


@dataclass
class Grade:
    status: str  # pass | wrong | unparsed
    method: str
    extracted: str
    numeric: Optional[float]
    letters: str
    detail: str


@dataclass
class Problem:
    id: str
    subject: str
    topic: str
    source: str
    source_id: str
    prompt: str
    expect: Expectation
    verify: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def student_message(self) -> str:
        """What the tutor is asked. The key stays out of this string."""
        return "Solve this and give the final answer.\n\n" + self.prompt.strip()


def _expect(data: dict[str, Any]) -> Expectation:
    kind = str(data.get("kind") or "numeric")
    letters = "".join(sorted(set(re.findall(r"[A-D]", str(data.get("letters") or "").upper()))))
    value = data.get("value")
    return Expectation(
        kind=kind,
        value=float(value) if value is not None else None,
        low=float(data["low"]) if data.get("low") is not None else None,
        high=float(data["high"]) if data.get("high") is not None else None,
        letters=letters,
        abs_tol=float(data.get("abs_tol", 0.05 if kind == "integer" else 0.02)),
        rel_tol=float(data.get("rel_tol", 0.0 if kind == "integer" else 0.01)),
    )


def load_problems(path: Optional[Path] = None) -> list[Problem]:
    target = path or PROBLEMS_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    problems = []
    for row in data.get("problems") or []:
        problems.append(
            Problem(
                id=str(row["id"]),
                subject=str(row.get("subject") or ""),
                topic=str(row.get("topic") or ""),
                source=str(row.get("source") or ""),
                source_id=str(row.get("source_id") or ""),
                prompt=str(row.get("prompt") or ""),
                expect=_expect(row.get("expect") or {}),
                verify=dict(row.get("verify") or {}),
                raw=row,
            )
        )
    return problems


def _balanced_brace(text: str, open_at: int) -> Optional[str]:
    """``open_at`` points at the ``{`` that opens the boxed body."""
    depth = 0
    for i in range(open_at, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1 : i]
    return None


def boxed_snippets(text: str) -> list[str]:
    out = []
    for match in _BOXED.finditer(text or ""):
        brace = match.end() - 1
        body = _balanced_brace(text, brace)
        if body is not None and body.strip():
            out.append(body.strip())
    return out


def answer_lines(text: str) -> list[str]:
    return [m.group(0).strip() for m in _ANSWER_LINE.finditer(text or "")]


def _to_float(token: str) -> Optional[float]:
    token = token.replace("−", "-").replace(",", "").strip()
    sci = re.match(
        r"([-+]?\d+(?:\.\d+)?)\s*[x×]\s*10\s*\^?\s*\{?\s*([-+]?\d+)\s*\}?$",
        token,
    )
    try:
        if sci:
            return float(sci.group(1)) * 10 ** int(sci.group(2))
        return float(token)
    except ValueError:
        return None


def _numbers(text: str) -> list[float]:
    found = []
    for match in _NUM.finditer(text or ""):
        value = _to_float(match.group(0))
        if value is not None:
            found.append(value)
    return found


def _try_eval(snippet: str) -> Optional[float]:
    """Evaluate a closed form. Returns None when the snippet is not a number."""
    from utils.mathexpr import ExpressionError, evaluate, free_symbols, from_latex, parse

    raw = (snippet or "").strip().strip("$").strip()
    if not raw or len(raw) > 400:
        return None
    plain = from_latex(raw)
    plain = plain.replace("^", "**")
    plain = re.sub(r"\s+", "", plain)
    if not plain or re.search(r"[^0-9A-Za-z_+\-*/().,]", plain):
        return None
    try:
        tree = parse(plain)
    except ExpressionError:
        return None
    if free_symbols(tree):
        return None
    try:
        value = float(evaluate(tree))
    except (ExpressionError, OverflowError, ValueError, ZeroDivisionError):
        return None
    if value != value or value in (float("inf"), float("-inf")):  # NaN
        return None
    return value


def _option_letters(snippet: str) -> str:
    """Option letters when the snippet is an option claim, not a word that happens to contain one."""
    text = (snippet or "").strip()
    if not text or len(text) > 80:
        return ""
    marked = re.findall(r"\(([A-Da-d])\)", text)
    if marked:
        return "".join(sorted(set(ch.upper() for ch in marked)))
    flat = re.sub(r"[\s,./+&]|and", "", text, flags=re.I)
    if flat and re.fullmatch(r"[A-Da-d]{1,4}", flat):
        return "".join(sorted(set(flat.upper())))
    words = re.findall(r"\b([A-Da-d])\b", text)
    if words and not re.search(r"\d", text):
        return "".join(sorted(set(ch.upper() for ch in words)))
    return ""


def _math_spans(snippet: str) -> list[str]:
    text = snippet or ""
    spans = re.findall(r"\$([^$]+)\$", text)
    spans += re.findall(r"\\\((.+?)\\\)", text)
    spans += re.findall(r"\\\[(.+?)\\\]", text, flags=re.S)
    return [s.strip() for s in spans if s.strip()]


def interpret(snippet: str) -> tuple[str, Any]:
    """('numeric', float) | ('letters', 'AC') | ('none', None)."""
    letters = _option_letters(snippet)
    candidates = [snippet, *_math_spans(snippet)]
    tail = re.split(r"(?i)(?:answer\s+is|answer\s*[:=]|equals|=)", snippet or "")
    if len(tail) > 1:
        candidates.append(tail[-1])
    value = None
    for candidate in reversed(candidates):
        value = _try_eval(candidate)
        if value is not None:
            break
        nums = _numbers(candidate)
        if nums and not (letters and not re.search(r"\d", candidate)):
            value = nums[-1]
            break
    if value is not None:
        return "numeric", value
    if letters:
        return "letters", letters
    return "none", None


def _tolerance(expect: Expectation) -> float:
    span = abs(expect.value or 0.0)
    tol = expect.abs_tol
    if span and expect.rel_tol:
        tol = max(tol, expect.rel_tol * span)
    return tol


def _matches(expect: Expectation, kind: str, payload: Any) -> bool:
    if expect.kind == "letters":
        return kind == "letters" and payload == expect.letters
    if kind != "numeric":
        return False
    value = float(payload)
    if expect.kind == "range":
        return expect.low is not None and expect.high is not None and (expect.low - 1e-9) <= value <= (expect.high + 1e-9)
    if expect.value is None:
        return False
    return abs(value - expect.value) <= _tolerance(expect)


def grade_response(response: str, expect: Expectation) -> Grade:
    """Grade the final claim of one reply. Unparsed is not a wrong answer."""
    want_numeric = expect.kind in {"numeric", "integer", "range"}
    pools = (("boxed", boxed_snippets(response)), ("answer_line", answer_lines(response)))
    for method, snippets in pools:
        chosen: Optional[tuple[str, Any, str]] = None
        for snippet in snippets:
            kind, payload = interpret(snippet)
            if want_numeric and kind == "numeric":
                chosen = (kind, payload, snippet)
            elif expect.kind == "letters" and kind == "letters":
                chosen = (kind, payload, snippet)
        if chosen:
            kind, payload, snippet = chosen
            ok = _matches(expect, kind, payload)
            return Grade(
                status="pass" if ok else "wrong",
                method=method,
                extracted=snippet[:240],
                numeric=float(payload) if kind == "numeric" else None,
                letters=payload if kind == "letters" else "",
                detail="matched" if ok else "final claim disagrees with the key",
            )

    tail = (response or "")[-600:]
    if re.search(r"answer", tail, re.I):
        kind, payload = interpret(tail)
        if (want_numeric and kind == "numeric") or (expect.kind == "letters" and kind == "letters"):
            ok = _matches(expect, kind, payload)
            return Grade(
                status="pass" if ok else "wrong",
                method="tail",
                extracted=tail.strip()[-240:],
                numeric=float(payload) if kind == "numeric" else None,
                letters=payload if kind == "letters" else "",
                detail="matched from the closing lines" if ok else "closing claim disagrees with the key",
            )

    return Grade(
        status="unparsed",
        method="none",
        extracted="",
        numeric=None,
        letters="",
        detail="no final numeric claim or option letters",
    )


def recompute(verify: dict[str, Any]) -> float:
    """Independent value of an authored ``verify`` block. Used to keep the key honest."""
    import sympy as sp

    op = verify.get("op")
    var_name = str(verify.get("var") or "x")
    var = sp.symbols(var_name)
    names: dict[str, Any] = {
        var_name: var,
        "pi": sp.pi,
        "E": sp.E,
        "oo": sp.oo,
        "inf": sp.oo,
        "ln": sp.log,
        "log": sp.log,
    }
    for name in ("cosh", "cos", "sin", "sinh", "tan", "exp", "binomial", "log"):
        names[name] = getattr(sp, name)
    expr = sp.sympify(str(verify.get("expr")), locals=names)
    if op == "integrate":
        lo = sp.sympify(str(verify["lo"]), locals=names)
        hi = sp.sympify(str(verify["hi"]), locals=names)
        value = sp.integrate(expr, (var, lo, hi))
    elif op == "summation":
        lo = sp.sympify(str(verify["lo"]), locals=names)
        hi = sp.sympify(str(verify["hi"]), locals=names)
        value = sp.summation(expr, (var, lo, hi))
    elif op == "limit":
        point = sp.sympify(str(verify.get("point") or "0"), locals=names)
        value = sp.limit(expr, var, point)
    elif op == "nsolve":
        guess = float(verify.get("guess") or 0.0)
        return float(sp.nsolve(expr, var, guess))
    elif op == "evalf":
        value = expr
    else:
        raise ValueError(f"unknown verify op {op!r}")
    return float(sp.N(value, 30))
