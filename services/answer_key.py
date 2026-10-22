"""Read the correct option(s) of a multiple-choice question from its stored solution.

Past-paper solutions end with a key line — "Correct option: 2", "Answer: C",
"Answer: ABD" (multiple correct) — often after a worked solution. When a key
can be read, the answer is graded here, instantly and exactly, with no model
call (POST /api/v1/study/answer). When it cannot (~2% of MCQs), the caller
falls back to the tutor grading it.

Options are compared by position: (1)/(A)/(a) is option 1, and so on.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# "Answer: A, C, D" used to read as just "A" (the lookahead stopped at the first comma), and a
# worked solution's own "answer:" line could override the official one. Structured answers
# (grade_structured) are the primary path now; this parser is the fallback for older items.
_KEY_RE = re.compile(
    r"(?:^|\n)\s*(?:official\s+answer|correct\s+options?|answer|ans)\s*(?:is)?\s*[:.\-]\s*"
    r"((?:\(?\s*(?:[1-4]|[a-d])\s*\)?\s*(?:,|and|&)?\s*){1,4})(?=$|\n|[.;(])",
    re.IGNORECASE,
)


def option_index(label: str) -> Optional[int]:
    """'2' / 'B' / 'b' / '(B)' -> 2."""
    s = (label or "").strip().strip("()").strip().lower()
    if s in {"1", "2", "3", "4"}:
        return int(s)
    if len(s) == 1 and s in "abcd":
        return "abcd".index(s) + 1
    return None


def parse_key(solution: str) -> Optional[frozenset]:
    """The correct option positions, or None when the solution states no key.

    The last key line wins: a worked solution can mention options on the way.
    """
    matches = _KEY_RE.findall(solution or "")
    if not matches:
        return None
    official = re.search(r"official\s+answer", solution or "", re.I)
    raw = (matches[0] if official else matches[-1]).strip().lower()  # the official line wins
    raw = re.sub(r"\band\b", " ", raw)
    digits = re.findall(r"[1-4]", raw)
    letters = re.findall(r"[a-d]", raw)
    # A line that mixes option styles is not an unambiguous key.
    if digits and letters:
        return None
    if digits and not letters:
        return frozenset(int(d) for d in digits)
    return frozenset("abcd".index(ch) + 1 for ch in letters) or None


def grade_choice(solution: str, chosen: Iterable[str]) -> Optional[dict]:
    """Legacy helper. Invalid labels are not dropped, and a mixed key is ungradable."""
    decision = grade_served_question({"solution": solution}, choice=chosen)
    if decision["grade_status"] not in {"correct", "incorrect"}:
        return None
    return {
        "correct": decision["is_correct"],
        "key": decision["key"],
        "chosen": decision["chosen"],
    }


# ── structured answers (local_ingest/structure.py) ───────────────────────────
def structured_of(question: dict) -> Optional[dict]:
    raw = (question or {}).get("structured")
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) else None


_MAX_EXPONENT = 12
_NUMBER = re.compile(
    r"^(?P<signed>[+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    r"(?:[eE](?P<exp>[+-]?\d+)"
    r"|\s*[x×*]\s*10\s*\^?\s*\{?\s*(?P<pow>[+-]?\d+)\s*\}?)?$"
)
_FRACTION = re.compile(r"^(?P<num>[+-]?\d+)\s*/\s*(?P<den>[+-]?\d+)$")
_UNIT_CHARS = re.compile(r"[A-Za-zΩµμ°/^*·.\-]+$")


def parse_number(text: str) -> Optional[float]:
    """A complete numeric token, or None.

    '.5' is 0.5. '1/2' is 0.5. A prefix, a second number, or trailing prose is
    not a number. This never uses eval.
    """
    token = (text or "").strip().replace("−", "-").replace(",", "")
    if not token or re.search(r"\bor\b", token, flags=re.I):
        return None
    fraction = _FRACTION.match(token)
    if fraction:
        denominator = float(fraction.group("den"))
        if denominator == 0:
            return None
        value = float(fraction.group("num")) / denominator
        return value if math_finite(value) else None
    match = _NUMBER.match(token)
    if not match:
        return None
    try:
        value = float(match.group("signed"))
        exponent = match.group("exp")
        if exponent is None:
            exponent = match.group("pow")
        if exponent is not None:
            power = int(exponent)
            if abs(power) > _MAX_EXPONENT:
                return None
            value *= 10 ** power
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math_finite(value) else None


def math_finite(value: float) -> bool:
    return value == value and value not in {float("inf"), float("-inf")}


def _decision(
    status: str,
    *,
    is_correct: Optional[bool] = None,
    reason: Optional[str] = None,
    answer_text: str = "",
    key: Optional[list] = None,
    chosen: Optional[list] = None,
) -> dict:
    return {
        "grade_status": status,
        "is_correct": is_correct,
        "evidence_source": "answer_key" if status in {"correct", "incorrect"} else "none",
        "failure_reason": reason,
        "answer_text": answer_text,
        "key": key or [],
        "chosen": chosen or [],
        "correct": is_correct if status in {"correct", "incorrect"} else None,
    }


def _choice_letters(choice: Iterable[str]) -> dict:
    """Exact option set, or an invalid submission. Tokens are never dropped."""
    tokens = []
    for raw in choice:
        for piece in re.split(r"[\s,;]+", str(raw or "").strip()):
            label = piece.strip().strip("()").strip()
            if label:
                tokens.append(label)
    if not tokens:
        return _decision("invalid_submission", reason="empty_choice")
    families = set()
    letters = []
    for token in tokens:
        upper = token.upper()
        if upper in "ABCD" and len(upper) == 1:
            families.add("letter")
            letters.append(upper)
            continue
        if token in "1234":
            families.add("number")
            letters.append("ABCD"[int(token) - 1])
            continue
        return _decision("invalid_submission", reason="invalid_option_label")
    if len(families) > 1:
        return _decision("invalid_submission", reason="mixed_option_labels")
    return {"letters": "".join(sorted(set(letters)))}


def _numeric_token(value: str, unit: Optional[str]) -> dict:
    text = (value or "").strip()
    if not text or re.search(r"\bor\b", text, flags=re.I):
        return _decision("invalid_submission", reason="ambiguous_number")
    expected = (unit or "").strip()
    if expected:
        pattern = re.compile(rf"^(?P<num>.+?)\s*{re.escape(expected)}\s*$", re.I)
        matched = pattern.match(text.replace("−", "-"))
        if not matched:
            return _decision("invalid_submission", reason="unit_mismatch")
        text = matched.group("num").strip()
    elif _UNIT_CHARS.search(text) and parse_number(text) is None:
        return _decision("invalid_submission", reason="trailing_text")
    number = parse_number(text)
    if number is None:
        return _decision("invalid_submission", reason="not_a_complete_number")
    return {"value": number}


def _key_number(answer: dict, value: float) -> Optional[bool]:
    kind = answer.get("kind")
    tolerance = answer.get("tolerance")
    try:
        slack = float(tolerance) if tolerance is not None else 1e-9
    except (TypeError, ValueError):
        return None
    if not math_finite(slack) or slack < 0:
        return None
    if kind == "integer":
        try:
            target = float(answer["value"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math_finite(target):
            return None
        return abs(value - target) <= slack
    if kind == "range":
        try:
            low = float(answer["low"])
            high = float(answer["high"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math_finite(low) or not math_finite(high) or low > high:
            return None
        return low - slack <= value <= high + slack
    return None


def _matches_key(answer: dict, letters: Optional[str], value: Optional[float]) -> Optional[bool]:
    """True, False, or None when the key itself cannot be checked."""
    if not isinstance(answer, dict):
        return None
    kind = answer.get("kind")
    if kind == "any_of":
        alternatives = answer.get("alternatives") or []
        if not alternatives or any(not isinstance(item, dict) for item in alternatives):
            return None
        checks = [_matches_key(item, letters, value) for item in alternatives]
        if any(item is None for item in checks):
            return None
        return any(checks)
    if kind == "letters":
        expected = answer.get("letters")
        if not isinstance(expected, str) or not expected or any(ch not in "ABCD" for ch in expected):
            return None
        if letters is None:
            return False
        return letters == "".join(sorted(expected))
    if kind in {"integer", "range"}:
        if value is None:
            return False
        return _key_number(answer, value)
    return None


def answer_text(answer: Optional[dict]) -> str:
    if not answer:
        return ""
    kind = answer.get("kind")
    if kind == "any_of":
        return " or ".join(answer_text(a) for a in answer.get("alternatives") or [])
    if kind == "letters":
        return ", ".join(f"({c})" for c in answer["letters"])
    if kind == "integer":
        return f"{answer['value']:g}"
    if kind == "range":
        lo, hi = answer["low"], answer["high"]
        return f"{lo:g}" if lo == hi else f"anything from {lo:g} to {hi:g}"
    return ""


def _grade_answer(answer: dict, choice: Iterable[str], value: Optional[str]) -> dict:
    text = answer_text(answer)
    if not isinstance(answer, dict) or not answer.get("kind"):
        return _decision("ungradable", reason="malformed_key", answer_text=text)
    kind = answer.get("kind")
    unit = answer.get("unit") if isinstance(answer.get("unit"), str) else None
    letters = None
    number = None
    if choice:
        parsed = _choice_letters(choice)
        if "grade_status" in parsed:
            return parsed
        letters = parsed["letters"]
    if value is not None and str(value).strip():
        if letters is not None:
            return _decision("invalid_submission", reason="mixed_choice_and_value", answer_text=text)
        parsed_number = _numeric_token(str(value), unit)
        if "grade_status" in parsed_number:
            parsed_number["answer_text"] = text
            return parsed_number
        number = parsed_number["value"]
    if letters is None and number is None:
        return _decision("invalid_submission", reason="empty_submission", answer_text=text)
    if kind == "letters" and number is not None:
        return _decision("invalid_submission", reason="number_for_choice_question", answer_text=text)
    if kind in {"integer", "range"} and letters is not None:
        return _decision("invalid_submission", reason="choice_for_numeric_question", answer_text=text)
    matched = _matches_key(answer, letters, number)
    if matched is None:
        return _decision("ungradable", reason="malformed_key", answer_text=text)
    positions = [ord(ch) - 64 for ch in (letters or "")]
    key_letters = ""
    if kind == "letters":
        key_letters = "".join(sorted(answer.get("letters") or ""))
    elif kind == "any_of":
        key_letters = "".join(
            sorted(item.get("letters") or "")
            for item in answer.get("alternatives") or []
            if isinstance(item, dict) and item.get("kind") == "letters"
        )
    return _decision(
        "correct" if matched else "incorrect",
        is_correct=matched,
        answer_text=text,
        key=[ord(ch) - 64 for ch in key_letters],
        chosen=positions,
    )


def grade_structured(question: dict, choice: Iterable[str] = (), value: Optional[str] = None) -> Optional[dict]:
    """Grade a structured key. None only when that key is absent.

    A present but unusable key is ungradable. It does not fall through to solution text.
    """
    answer = (structured_of(question) or {}).get("answer")
    if not answer:
        return None
    return _grade_answer(answer, choice, value)


def grade_free_answer(question: dict, text: Optional[str]) -> dict:
    """Grade one typed answer as either options or one complete value."""
    raw = (text or "").strip()
    if not raw:
        return _decision("invalid_submission", reason="empty_submission")
    tokens = [piece for piece in re.split(r"[\s,;]+", raw) if piece]
    if tokens and all(_choice_letters([piece]).get("letters") for piece in tokens):
        together = _choice_letters(tokens)
        if "grade_status" in together:
            return together
        return grade_served_question(question, choice=tokens)
    return grade_served_question(question, value=raw)


def grade_served_question(
    question: dict,
    choice: Iterable[str] = (),
    value: Optional[str] = None,
) -> dict:
    """Grade the server-held question. A malformed structured key blocks the legacy parser."""
    structured = structured_of(question)
    if isinstance(structured, dict) and structured.get("answer"):
        return _grade_answer(structured["answer"], choice, value)
    if choice:
        key = parse_key((question or {}).get("solution") or "")
        if key:
            parsed = _choice_letters(choice)
            if "grade_status" in parsed:
                return parsed
            chosen = frozenset(ord(ch) - 64 for ch in parsed["letters"])
            matched = chosen == key
            return _decision(
                "correct" if matched else "incorrect",
                is_correct=matched,
                answer_text=", ".join(f"({index})" for index in sorted(key)),
                key=sorted(key),
                chosen=sorted(chosen),
            )
    return _decision("ungradable", reason="no_authoritative_key")
