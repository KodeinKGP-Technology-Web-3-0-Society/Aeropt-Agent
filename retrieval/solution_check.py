"""Does a stored worked solution reach the official answer?

The corpus stores the official final answer in `structured.answer`, and most Advanced
solutions end with a line copied from it ("Correct answer: (A, C)"). That line proves
nothing: the model-written *reasoning above it* may conclude something else. This reads
the conclusion from the body and compares it with the key.

Three outcomes, so a guess never becomes a verdict:
  match     the body's conclusion equals the key
  mismatch  the body states a different conclusion (safe to stop serving the solution)
  unknown   no conclusion could be read, or the key is in a form this does not model
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

MATCH, MISMATCH, UNKNOWN = "match", "mismatch", "unknown"

# Lines the ingest appended from the official key; everything before them is the model's work.
_APPENDED = re.compile(r"\n+\s*(?:Correct answer:|Numerical answer:|Official answer:)", re.I)
_CHECKED_HEADER = re.compile(r"checked against the official key", re.I)

_NUM = r"-?\d+(?:,\d{3})*(?:\.\d+)?(?:\s*[x×]\s*10\^?\{?-?\d+\}?)?"


def _body(solution: str) -> str:
    m = _APPENDED.search(solution)
    return solution[: m.start()] if m else solution


def _parse_answer(raw: Any) -> dict[str, Any] | None:
    """`structured.answer` is a JSON string; numeric ones carry a stringified dict inside."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("kind") == "letters":
        letters = re.findall(r"[A-D]", str(data.get("letters") or "").upper())
        return {"kind": "letters", "letters": set(letters)} if letters else None
    if data.get("kind") == "exact":
        inner: Any = data.get("value")
        if isinstance(inner, str):
            try:
                inner = ast.literal_eval(inner)
            except (ValueError, SyntaxError):
                return None
        if not isinstance(inner, dict):
            return None
        lo, hi, ex = inner.get("min_value"), inner.get("max_value"), inner.get("exact_value")
        if inner.get("is_range") and lo is not None and hi is not None:
            return {"kind": "range", "lo": float(lo), "hi": float(hi)}
        if ex is not None:
            return {"kind": "exact", "value": float(ex)}
    return None


_LETTER_SPAN = re.compile(r"\*\*\(?[A-D]\)?(?:\s*(?:,|and|&)\s*\(?[A-D]\)?)*\)?\*\*", re.I)
_TRIGGER = re.compile(
    r"(?:correct\s+(?:option|statement|answer|choice|pair|match)(?:e?s)?|answer)\s*(?:is|are|:)?\s*[:\-]?\s*([^\n]{0,50})",
    re.I,
)


def _only_letters(phrase: str) -> set[str] | None:
    """The option letters in `phrase`, or None unless the phrase is nothing but letters."""
    flat = re.sub(r"[*()\s.,&]|\band\b", "", phrase, flags=re.I)
    return set(flat.upper()) if flat and re.fullmatch(r"[A-Da-d]+", flat) else None


def _letters_in_body(body: str) -> set[str] | None:
    """Letters the body concludes with: the last "correct ... is/are X" statement, else the
    last bold span made only of option letters. Mid-explanation bold letters are ignored."""
    tail = body[-700:]
    for m in reversed(list(_TRIGGER.finditer(tail))):
        letters = _only_letters(m.group(1))
        if letters:
            return letters
    # "...are **(A)** and **(B)**." : every letter-only bold span in the concluding line.
    last_line = next((ln for ln in reversed(tail.splitlines()) if ln.strip()), "")
    singles = re.findall(r"\*\*\(?([A-D])\)?\*\*", last_line)
    if len(singles) > 1:
        return set(singles)
    spans = _LETTER_SPAN.findall(tail)
    return set(re.findall(r"[A-D]", spans[-1].upper())) if spans else None


def _to_float(text: str) -> float | None:
    text = text.replace(",", "").strip()
    sci = re.match(r"(-?\d+(?:\.\d+)?)\s*[x×]\s*10\^?\{?(-?\d+)\}?", text)
    try:
        return float(sci.group(1)) * 10 ** int(sci.group(2)) if sci else float(re.match(r"-?\d+(?:\.\d+)?", text).group(0))  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        return None


def _number_in_body(body: str) -> float | None:
    tail = body[-500:]
    if re.search(r"\(or\s|\bor\b[^.]{0,30}\d", tail[-160:], re.I):
        return None  # the body hedges between values; there is no single conclusion to compare
    # Subscripts (r_1) and list numbering ("**4. Tension**") are not answers.
    tail = re.sub(r"_\{[^}]*\}|_[A-Za-z0-9]", "", tail)
    tail = re.sub(r"(?m)^\s*(?:\*\*)?\d+\.\s", "", tail)
    tail = re.sub(r"\*\*\d+\.(?=\s)", "**", tail)
    for pat in (r"final\s+answer[^0-9\-]{0,25}(" + _NUM + ")", r"answer\s*(?:is|=|:)[^0-9\-]{0,12}(" + _NUM + ")"):
        found = re.findall(pat, tail, re.I)
        if found:
            return _to_float(found[-1])
    bold = re.findall(r"\*\*\$?\\?\(?\s*(" + _NUM + r")", tail)
    return _to_float(bold[-1]) if bold else None


def check_solution(answer_raw: Any, solution: str | None) -> tuple[str, str]:
    """(outcome, detail) for one stored solution against its official answer."""
    if not solution:
        return UNKNOWN, "no solution"
    if _CHECKED_HEADER.search(solution[:400]):
        return MATCH, "already checked against the key by the ingest"
    key = _parse_answer(answer_raw)
    if key is None:
        return UNKNOWN, "answer key not in a modelled form"
    body = _body(solution)

    if key["kind"] == "letters":
        got = _letters_in_body(body)
        if not got:
            return UNKNOWN, "no letter conclusion in the body"
        return (MATCH, "") if got == key["letters"] else (MISMATCH, f"body says {sorted(got)}, key {sorted(key['letters'])}")

    got_n = _number_in_body(body)
    if got_n is None:
        return UNKNOWN, "no numeric conclusion in the body"
    if key["kind"] == "range":
        pad = max(abs(key["hi"]), abs(key["lo"]), 1e-9) * 0.02
        ok = key["lo"] - pad <= got_n <= key["hi"] + pad
        want = f"{key['lo']}..{key['hi']}"
    else:
        ok = abs(got_n - key["value"]) <= max(abs(key["value"]) * 0.01, 1e-9)
        want = str(key["value"])
    return (MATCH, "") if ok else (MISMATCH, f"body says {got_n}, key {want}")
