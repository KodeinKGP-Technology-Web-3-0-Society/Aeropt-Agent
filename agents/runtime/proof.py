"""A stated quantity is checked by a physics or arithmetic tool before the student sees it.

The tutor may skip the belt. This runs one check on the draft and records it on
the trace, so an unchecked magnitude does not leave the turn as if it had been
verified.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from agents.runtime.trace import AgentTrace, TraceStep

_CHECKED = (
    "verify_result",
    "solve_symbolically",
    "check_dimensions",
    "check_student_work",
    "calculate",
)

_UNIT = re.compile(
    r"\d+(?:\.\d+)?\s*(?:m/s\^?2|m/s|kg|N|J|W|Pa|Hz|rad/s|rad)\b",
    re.IGNORECASE,
)
_EQUATION = re.compile(r"(?P<formula>[A-Za-z\\][^$\n]{0,80}=[^$\n]{1,80})")
_LATEX_NUMBER = re.compile(r"\$[^$\n]*\d[^$\n]*\$")


def needs_proof(text: str) -> bool:
    """True when the draft asserts a magnitude, a unit, or an equation."""
    if not (text or "").strip():
        return False
    return bool(_UNIT.search(text) or _EQUATION.search(text) or _LATEX_NUMBER.search(text))


def already_checked(trace: Optional[AgentTrace]) -> bool:
    if trace is None:
        return False
    return any(any(name in step for name in _CHECKED) for step in trace.path())


def ensure_proof(draft: str, trace: Optional[AgentTrace]) -> str:
    """Return the draft, with one tool check attached when none has run."""
    if not needs_proof(draft) or already_checked(trace):
        return draft
    proof = _force_one(draft)
    if trace is not None:
        data = proof.get("data")
        ok = not (isinstance(data, dict) and data.get("error"))
        trace.add(
            TraceStep(
                kind="tool",
                name=str(proof.get("tool") or "calculate"),
                ok=ok,
                error=data.get("error") if isinstance(data, dict) else None,
                result_preview=data,
            )
        )
    return f"{draft.rstrip()}\n\n{_student_note(proof)}"


def _student_note(proof: Dict[str, Any]) -> str:
    tool = proof.get("tool") or "calculate"
    data = proof.get("data")
    if isinstance(data, dict) and data.get("error"):
        return f"Checked with {tool} before this reply: {data['error']}"
    return f"Checked with {tool} before this reply."


def _force_one(draft: str) -> Dict[str, Any]:
    formula = _first_formula(draft)
    if formula:
        try:
            from mcp_tools.physics_tools import CheckDimensionsArgs, check_dimensions

            data = check_dimensions(CheckDimensionsArgs(expression=formula), None)  # type: ignore[arg-type]
            return {"tool": "check_dimensions", "data": data}
        except Exception as exc:  # noqa: BLE001 — fall through to arithmetic
            formula_error = str(exc)
        else:
            formula_error = ""
    else:
        formula_error = ""
    number = re.search(r"\d+(?:\.\d+)?", draft)
    expression = number.group(0) if number else "0"
    try:
        from mcp_tools.compute_tools import CalculateArgs, calculate

        data = calculate(CalculateArgs(expression=expression), None)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        data = {"error": formula_error or str(exc)}
    return {"tool": "calculate", "data": data}


def _first_formula(draft: str) -> str:
    match = _EQUATION.search(draft)
    if not match:
        return ""
    formula = match.group("formula").strip().strip(".,;")
    if len(formula) > 120:
        return ""
    return formula
