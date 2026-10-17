"""Scenario loading, execution, and trajectory assertions for eval harness."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("jee_tutor.evals.scenarios")

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


@dataclass
class TurnAssertion:
    tools_called: Optional[List[str]] = None
    tools_not_called: Optional[List[str]] = None
    tools_order: Optional[List[str]] = None
    stop_reason: Optional[str] = None
    checker_verdict: Optional[str] = None
    final_text_regex: Optional[List[str]] = None
    final_text_not_regex: Optional[List[str]] = None
    no_boxed_solution: Optional[bool] = None
    has_boxed_solution: Optional[bool] = None
    max_duration_s: Optional[float] = None


@dataclass
class ScenarioTurn:
    user_message: str
    assertions: TurnAssertion
    setup_state: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Scenario:
    id: str
    description: str
    turns: List[ScenarioTurn]
    tags: List[str] = field(default_factory=list)


@dataclass
class TurnResult:
    user_message: str
    assistant_response: str
    tools_called: List[str]
    stop_reason: str
    checker_verdict: Optional[str]
    passed: bool
    failures: List[str]


@dataclass
class ScenarioResult:
    scenario_id: str
    passed: bool
    turn_results: List[TurnResult]
    error: Optional[str] = None


def load_scenario(filepath: Path) -> Scenario:
    """Load a scenario from a YAML file."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    turns = []
    for t in data.get("turns", []):
        raw_assertions = t.get("assertions", {})
        assertions = TurnAssertion(
            tools_called=raw_assertions.get("tools_called"),
            tools_not_called=raw_assertions.get("tools_not_called"),
            tools_order=raw_assertions.get("tools_order"),
            stop_reason=raw_assertions.get("stop_reason"),
            checker_verdict=raw_assertions.get("checker_verdict"),
            final_text_regex=raw_assertions.get("final_text_regex"),
            final_text_not_regex=raw_assertions.get("final_text_not_regex"),
            no_boxed_solution=raw_assertions.get("no_boxed_solution"),
            has_boxed_solution=raw_assertions.get("has_boxed_solution"),
            max_duration_s=raw_assertions.get("max_duration_s"),
        )
        turns.append(
            ScenarioTurn(
                user_message=t.get("user_message", ""),
                assertions=assertions,
                setup_state=t.get("setup_state", {}),
            )
        )

    return Scenario(
        id=data.get("id", filepath.stem),
        description=data.get("description", ""),
        turns=turns,
        tags=data.get("tags", []),
    )


def load_all_scenarios(directory: Optional[Path] = None) -> List[Scenario]:
    target_dir = directory or SCENARIOS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    scenarios = []
    for p in sorted(target_dir.glob("*.yaml")):
        try:
            scenarios.append(load_scenario(p))
        except Exception as exc:
            logger.error("Failed to load scenario %s: %s", p, exc)
    return scenarios


def check_turn_assertions(
    response_text: str,
    tools_called: List[str],
    stop_reason: str,
    checker_verdict: Optional[str],
    assertions: TurnAssertion,
) -> List[str]:
    """Verify trajectory assertions for one turn and return any failure descriptions."""
    failures: List[str] = []

    # 1. Tools called
    if assertions.tools_called:
        for expected_tool in assertions.tools_called:
            if expected_tool not in tools_called:
                failures.append(f"Expected tool '{expected_tool}' was not called (called: {tools_called})")

    # 2. Tools NOT called
    if assertions.tools_not_called:
        for forbidden_tool in assertions.tools_not_called:
            if forbidden_tool in tools_called:
                failures.append(f"Forbidden tool '{forbidden_tool}' was called")

    # 3. Tools order
    if assertions.tools_order:
        last_idx = -1
        for tool in assertions.tools_order:
            try:
                idx = tools_called.index(tool, last_idx + 1)
                last_idx = idx
            except ValueError:
                failures.append(f"Tool order mismatch: '{tool}' not found in order in {tools_called}")
                break

    # 4. Stop reason
    if assertions.stop_reason:
        if stop_reason != assertions.stop_reason:
            failures.append(f"Expected stop_reason='{assertions.stop_reason}', got '{stop_reason}'")

    # 5. Checker verdict
    if assertions.checker_verdict:
        if checker_verdict != assertions.checker_verdict:
            failures.append(f"Expected checker_verdict='{assertions.checker_verdict}', got '{checker_verdict}'")

    # 6. Regex checks on final text
    if assertions.final_text_regex:
        for pattern in assertions.final_text_regex:
            try:
                matched = bool(re.search(pattern, response_text, re.IGNORECASE))
            except re.error:
                matched = pattern.lower() in response_text.lower()
            if not matched:
                failures.append(f"Final text did not match required pattern: r'{pattern}'")

    if assertions.final_text_not_regex:
        for pattern in assertions.final_text_not_regex:
            try:
                matched = bool(re.search(pattern, response_text, re.IGNORECASE))
            except re.error:
                matched = pattern.lower() in response_text.lower()
            if matched:
                failures.append(f"Final text matched forbidden pattern: r'{pattern}'")

    # 7. Boxed solution checks
    has_boxed = r"\boxed{" in response_text
    if assertions.no_boxed_solution is True and has_boxed:
        failures.append("Revealed boxed solution on an unearned or premature attempt")
    if assertions.has_boxed_solution is True and not has_boxed:
        failures.append("Did not reveal boxed solution when requested/earned")

    return failures
