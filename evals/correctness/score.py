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


def _brace_group(text: str, i: int) -> tuple[Optional[str], int]:
    """``text[i]`` is ``{``. Return the inside and the index after the closing brace."""
    if i >= len(text) or text[i] != "{":
        return None, i
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1 : j], j + 1
    return None, i


def _convert_latex(text: str) -> Optional[str]:
    """LaTeX math to a sympy string. Nested ``\\frac`` stays a quotient."""
    raw = (text or "").strip().strip("$").strip()
    if not raw:
        return None
    out: list[str] = []
    i = 0
    while i < len(raw):
        frac = re.match(r"\\[dt]?frac\b", raw[i:])
        if frac:
            j = i + frac.end()
            while j < len(raw) and raw[j].isspace():
                j += 1
            num, j = _brace_group(raw, j)
            while j < len(raw) and raw[j].isspace():
                j += 1
            den, j = _brace_group(raw, j)
            if num is None or den is None:
                return None
            num_s, den_s = _convert_latex(num), _convert_latex(den)
            if num_s is None or den_s is None:
                return None
            out.append(f"(({num_s})/({den_s}))")
            i = j
            continue
        out.append(raw[i])
        i += 1
    s = "".join(out)
    s = re.sub(r"\\(left|right|mathbf|mathrm|text|displaystyle|,|;|:)\b", "", s)
    s = re.sub(r"\\(sinh|cosh|sin|cos|tan|log|ln|exp)\b", lambda m: "log" if m.group(1) == "ln" else m.group(1), s)
    s = re.sub(r"\\(pi|infty)\b", lambda m: "pi" if m.group(1) == "pi" else "oo", s)
    s = s.replace("\\times", "*").replace("\\cdot", "*")
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    s = s.replace("{", "(").replace("}", ")").replace("^", "**")
    s = re.sub(r"\be\b", "E", s)
    s = re.sub(r"\s+", "", s)
    if not s or re.search(r"[^0-9A-Za-z_+\-*/().,]", s):
        return None
    return s


def _finite(value: float) -> Optional[float]:
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _try_eval(snippet: str) -> Optional[float]:
    """Evaluate a closed form. Returns None when the snippet is not a number."""
    raw = (snippet or "").strip().strip("$").strip()
    if not raw or len(raw) > 400:
        return None
    if "\\" in raw or "frac" in raw or "^" in raw:
        plain = _convert_latex(raw)
        if not plain:
            return None
        try:
            import sympy as sp

            expr = sp.sympify(plain, locals={"E": sp.E, "pi": sp.pi, "oo": sp.oo})
        except (TypeError, ValueError, SyntaxError):
            return None
        if getattr(expr, "free_symbols", None):
            return None
        try:
            return _finite(float(sp.N(expr)))
        except (TypeError, ValueError):
            return None

    from utils.mathexpr import ExpressionError, evaluate, free_symbols, parse

    plain = re.sub(r"\s+", "", raw)
    if not plain or re.search(r"[^0-9A-Za-z_+\-*/().,]", plain):
        return None
    try:
        tree = parse(plain)
    except ExpressionError:
        return None
    if free_symbols(tree):
        return None
    try:
        return _finite(float(evaluate(tree)))
    except (ExpressionError, OverflowError, ValueError, ZeroDivisionError):
        return None


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


def _math_spans(snippet: str) -> list[tuple[int, str]]:
    """(end_index, body) for $...$, \\(...\\) and \\[...\\]."""
    text = snippet or ""
    found: list[tuple[int, str]] = []
    for pattern in (r"\$([^$]+)\$", r"\\\((.+?)\\\)", r"\\\[(.+?)\\\]"):
        for match in re.finditer(pattern, text, flags=re.S):
            body = match.group(1).strip()
            if body:
                found.append((match.end(), body))
    found.sort(key=lambda item: item[0])
    return found


def _blank_spans(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for i in range(start, min(end, len(chars))):
            chars[i] = " "
    return "".join(chars)


def _superscript_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    i = 0
    while i < len(text):
        if text[i] == "^" and i + 1 < len(text) and text[i + 1] == "{":
            _, after = _brace_group(text, i + 1)
            if after > i + 1:
                spans.append((i, after))
                i = after
                continue
        i += 1
    return spans


def _rhs(body: str) -> str:
    if body.count("=") == 1:
        return body.split("=", 1)[1]
    return body


def _emphasized_numbers(text: str) -> list[float]:
    """Numbers the reply stresses: markdown bold or ``\\mathbf``."""
    found = []
    for span in re.findall(r"\*\*([^*]+)\*\*", text or ""):
        value = _try_eval(span)
        if value is None:
            nums = _plain_numbers(span)
            value = nums[-1] if nums else None
        if value is not None:
            found.append(value)
    for span in re.findall(r"\\mathbf\s*\{([^{}]*)\}", text or ""):
        value = _try_eval(span)
        if value is None:
            nums = _plain_numbers(span)
            value = nums[-1] if nums else None
        if value is not None:
            found.append(value)
    return found


def _plain_numbers(text: str) -> list[float]:
    """Numbers that are not a superscript and not the 10 in ``10^{-1}``."""
    raw = re.sub(r"\b[A-Za-z]\(([-+]?\d+(?:\.\d+)?)\)", " ", text or "")
    # 10^{-1} is a unit scale, not the integer the student was asked to fill in.
    raw = re.sub(r"10\s*\^\{[^{}]*\}", " ", raw)
    raw = re.sub(r"10\s*\^[+-]?\d+", " ", raw)
    blanked = _blank_spans(raw, _superscript_spans(raw))
    return _numbers(blanked)


def interpret(snippet: str) -> tuple[str, Any]:
    """('numeric', float) | ('letters', 'AC') | ('none', None)."""
    text = snippet or ""
    letters = _option_letters(text)
    # A boxed expression such as 1/3 or -\sinh(\pi)/2 is one claim. The digits
    # inside it are not separate answers.
    if len(text) <= 180 and not re.search(r"[A-Za-z]{4,}", re.sub(r"\\[a-zA-Z]+", "", text)):
        value = _try_eval(_rhs(text))
        if value is not None:
            return "numeric", value
    emphasized = _emphasized_numbers(text)
    if emphasized:
        return "numeric", emphasized[-1]
    plain = _plain_numbers(text)
    if plain and not (letters and not re.search(r"\d", text)):
        return "numeric", plain[-1]
    spans = _math_spans(text)
    if spans:
        value = _try_eval(_rhs(spans[-1][1]))
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


def _closing_display(text: str) -> Optional[str]:
    """The last ``$$...$$`` when the reply ends on it."""
    matches = list(re.finditer(r"\$\$([\s\S]+?)\$\$", text or ""))
    if not matches:
        return None
    last = matches[-1]
    if len(text) - last.end() > 40:
        return None
    return last.group(1).strip()


def _grade_from(method: str, kind: str, payload: Any, snippet: str, expect: Expectation) -> Grade:
    ok = _matches(expect, kind, payload)
    return Grade(
        status="pass" if ok else "wrong",
        method=method,
        extracted=(snippet or "")[:240],
        numeric=float(payload) if kind == "numeric" else None,
        letters=payload if kind == "letters" else "",
        detail="matched" if ok else "final claim disagrees with the key",
    )


def _last_line(response: str) -> str:
    lines = [line for line in (response or "").splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def grade_response(response: str, expect: Expectation) -> Grade:
    """Grade the claim on the last line. Unparsed is not a wrong answer."""
    text = response or ""
    want_numeric = expect.kind in {"numeric", "integer", "range"}
    boxed = boxed_snippets(text)
    if boxed:
        kind, payload = interpret(boxed[-1])
        if (want_numeric and kind == "numeric") or (expect.kind == "letters" and kind == "letters"):
            return _grade_from("boxed", kind, payload, boxed[-1], expect)

    last = _last_line(text)
    if want_numeric and last:
        display = _closing_display(last)
        if display and not re.search(r"\\approx|≈", last):
            value = _try_eval(_rhs(display))
            if value is not None:
                return _grade_from("answer_line", "numeric", value, display, expect)
        emphasized = _emphasized_numbers(last)
        if emphasized:
            return _grade_from("bold", "numeric", emphasized[-1], last[:240], expect)
        if re.search(r"answer|\\approx|≈|=", last, re.IGNORECASE):
            plain = _plain_numbers(last)
            if plain:
                return _grade_from("answer_line", "numeric", plain[-1], last[:240], expect)
    elif expect.kind == "letters" and last:
        kind, payload = interpret(last)
        if kind == "letters":
            return _grade_from("answer_line", kind, payload, last[:240], expect)

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
