"""A stated quantity is checked by a physics tool before the student sees it.

Extracts the final stated result (the last formula, boxed equation, or final
quantity = number unit in the draft) and checks it for dimensional consistency.
Never fabricates a check note on an irrelevant number.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from agents.runtime.trace import AgentTrace, TraceStep

logger = logging.getLogger("jee_tutor.proof")

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


def _extract_boxed(text: str) -> List[str]:
    """Extract contents of \\boxed{...}, handling nested braces."""
    results: List[str] = []
    idx = 0
    while True:
        pos = text.find(r"\boxed{", idx)
        if pos == -1:
            break
        start = pos + len(r"\boxed{")
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        if depth == 0:
            results.append(text[start : i - 1])
        idx = i
    return results


def _clean_latex_expr(expr: str) -> str:
    """Strip \\text{...}, dollar signs and trailing punctuation from a formula."""
    s = re.sub(r"\\text\{([^}]+)\}", r"\1", expr or "")
    return s.strip(" \t\n.,;$")


def _extract_final_target(draft: str) -> Optional[str]:
    """Extract the final stated result: boxed equation, final formula, or quantity=number unit."""
    if not draft:
        return None

    # 1. Boxed answer
    boxed_items = _extract_boxed(draft)
    if boxed_items:
        boxed = _clean_latex_expr(boxed_items[-1])
        if "=" in boxed:
            return boxed
        # If boxed is e.g. "12 N" preceded by "F = \boxed{...}"
        match_var = re.search(r"([A-Za-z\\][A-Za-z0-9_]*)\s*=\s*\\boxed\{", draft)
        if match_var:
            return f"{match_var.group(1)} = {boxed}"

    # 2. Last equation in the draft
    matches = list(_EQUATION.finditer(draft))
    if matches:
        for match in reversed(matches):
            raw = match.group("formula")
            clean = _clean_latex_expr(raw)
            if "=" in clean and len(clean) <= 120:
                parts = clean.split("=", 1)
                lhs, rhs = parts[0].strip(), parts[1].strip()
                if lhs and rhs and not lhs.isdigit():
                    return clean

    # 3. Final stated quantity: e.g. "The force is 12 N." -> "F = 12*N"
    qty_match = re.search(
        r"(?:(?:final\s+)?(?P<name>force|velocity|speed|acceleration|energy|work|power|pressure|torque|period)\s+(?:is|=|equals)\s+|(?P<var>[A-Za-z\\][A-Za-z0-9_]*)\s*=\s*)(?P<val>-?\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z/]+(?:\^?\d+)?)\.?$",
        draft.strip(),
        re.IGNORECASE,
    )
    if qty_match:
        name = (qty_match.group("name") or "").lower()
        var = qty_match.group("var") or ""
        val = qty_match.group("val")
        unit = qty_match.group("unit")
        var_map = {
            "force": "F",
            "velocity": "v",
            "speed": "v",
            "acceleration": "a",
            "energy": "E",
            "work": "W",
            "power": "P",
            "pressure": "P",
            "torque": "tau",
            "period": "T",
        }
        symbol = var or var_map.get(name, "")
        if symbol:
            return f"{symbol} = {val}*{unit}"

    return None


def ensure_proof(
    draft: str,
    trace: Optional[AgentTrace] = None,
    state: Optional[Dict[str, Any]] = None,
) -> str:
    """Verify final formula dimensions when no check tool has run.

    Never appends fake check notes. If the check fails, notifies state['proof_issue']
    so the Answer Checker can repair the answer.
    """
    if not needs_proof(draft) or already_checked(trace):
        return draft

    target = _extract_final_target(draft)
    if not target:
        # Nothing checkable found (e.g. an irrelevant number in a setup sentence)
        if trace is not None:
            trace.note("proof=unverifiable")
        return draft

    try:
        from mcp_tools.physics_tools import CheckDimensionsArgs, check_dimensions

        res = check_dimensions(CheckDimensionsArgs(expression=target), None)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        logger.debug("proof check_dimensions failed: %s", exc)
        res = {"error": str(exc), "consistent": None}

    consistent = res.get("consistent")

    if consistent is True:
        if trace is not None:
            trace.add(
                TraceStep(
                    kind="tool",
                    name="check_dimensions",
                    ok=True,
                    result_preview=res,
                )
            )
            trace.note("proof=passed")
        return draft

    if consistent is False:
        # Dimensional inconsistency detected!
        verdict = res.get("verdict") or res.get("problem") or "inconsistent dimensions"
        if trace is not None:
            trace.add(
                TraceStep(
                    kind="tool",
                    name="check_dimensions",
                    ok=False,
                    error=verdict,
                    result_preview=res,
                )
            )
            trace.note("proof=failed")
        if state is not None:
            state["proof_issue"] = (
                f"Final formula '{target}' is dimensionally inconsistent: {verdict}"
            )
        # Do NOT append anything to the draft
        return draft

    # Could not determine dimensions (unknown symbols or syntax error)
    if trace is not None:
        trace.note("proof=unverifiable")
    return draft
