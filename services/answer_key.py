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
    if digits and not re.search(r"[a-d]", raw):
        return frozenset(int(d) for d in digits)
    return frozenset("abcd".index(ch) + 1 for ch in re.findall(r"[a-d]", raw)) or None


def grade_choice(solution: str, chosen: Iterable[str]) -> Optional[dict]:
    """{'correct': bool, 'key': [ints], 'chosen': [ints]} or None if ungradable."""
    key = parse_key(solution)
    if key is None:
        return None
    picked = frozenset(i for i in (option_index(c) for c in chosen) if i)
    if not picked:
        return None
    return {"correct": picked == key, "key": sorted(key), "chosen": sorted(picked)}


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


def parse_number(text: str) -> Optional[float]:
    """'2.35', '2.35 m/s', '-4', '3e-3', '5 × 10^3', '5x10^-3' -> float; None if no number."""
    t = (text or "").replace(",", "").replace("−", "-").strip()
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:[x×*]\s*10\s*\^?\s*\{?\s*(-?\d+)\s*\}?)?", t)
    if not m:
        return None
    value = float(m.group(1))
    if m.group(2):
        value *= 10 ** int(m.group(2))
    else:
        e = re.match(r"-?\d+(?:\.\d+)?[eE](-?\d+)", t[m.start():])
        if e:
            value *= 10 ** int(e.group(1))
    return value


def _matches(answer: dict, letters: str, value: Optional[float]) -> bool:
    kind = answer.get("kind")
    if kind == "any_of":
        return any(_matches(a, letters, value) for a in answer.get("alternatives") or [])
    if kind == "letters":
        return bool(letters) and letters == answer.get("letters")
    if value is None:
        return False
    if kind == "integer":
        return abs(value - float(answer["value"])) < 1e-9
    if kind == "range":
        return float(answer["low"]) - 1e-9 <= value <= float(answer["high"]) + 1e-9
    return False


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


def grade_structured(question: dict, choice: Iterable[str] = (), value: Optional[str] = None) -> Optional[dict]:
    """Grade from the question's structured official answer — no model call.

    {'correct', 'key' (option positions, for choice questions), 'answer_text'} or None when the
    question has no structured answer or the student's input does not fit it.
    """
    s = structured_of(question)
    answer = (s or {}).get("answer")
    if not answer:
        return None
    letters = "".join(sorted({c for c in (str(x).strip().strip("()").upper() for x in choice) if c in "ABCD"}))
    if not letters:  # numbered options (1)-(4) map to A-D
        letters = "".join(sorted({"ABCD"[int(x) - 1] for x in (str(c).strip("() ") for c in choice) if x in "1234"}))
    number = parse_number(value) if value is not None else None
    if not letters and number is None:
        return None
    if answer.get("kind") == "letters" and not letters:
        return None
    key = [ord(c) - 64 for c in (answer.get("letters") or "")]
    return {"correct": _matches(answer, letters, number), "key": key,
            "chosen": [ord(c) - 64 for c in letters], "answer_text": answer_text(answer)}
