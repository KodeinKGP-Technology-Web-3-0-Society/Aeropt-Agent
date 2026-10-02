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

_KEY_RE = re.compile(
    r"(?:^|\n)\s*(?:correct\s+options?|answer|ans)\s*(?:is)?\s*[:.\-]\s*\(?\s*([1-4]|[a-d]{1,4})\s*\)?\s*(?=$|\n|[.,;])",
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
    raw = matches[-1].strip().lower()
    if raw.isdigit():
        return frozenset({int(raw)})
    return frozenset("abcd".index(ch) + 1 for ch in raw if ch in "abcd") or None


def grade_choice(solution: str, chosen: Iterable[str]) -> Optional[dict]:
    """{'correct': bool, 'key': [ints], 'chosen': [ints]} or None if ungradable."""
    key = parse_key(solution)
    if key is None:
        return None
    picked = frozenset(i for i in (option_index(c) for c in chosen) if i)
    if not picked:
        return None
    return {"correct": picked == key, "key": sorted(key), "chosen": sorted(picked)}
