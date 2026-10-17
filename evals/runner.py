"""Eval runner for Aeropt tutor agent: replay, live, record, and judge tiers.

Usage:
    python -m evals.runner                       # Replay all scenarios offline (< 30s)
    python -m evals.runner --scenario <id>       # Replay one scenario
    python -m evals.runner --mode record         # Run live & record cassettes (local DB only)
    python -m evals.runner --rerecord <id>       # Re-record a single scenario
    python -m evals.runner --mode judge          # Run LLM judge calibration suite
    python -m evals.runner --mode live           # Run live without recording (local DB only)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from evals.cassettes import Cassette, CassetteContext
from evals.fakes import InMemoryDatabase, patch_environment
from evals.scenarios import (
    Scenario,
    ScenarioResult,
    TurnResult,
    check_turn_assertions,
    load_all_scenarios,
    load_scenario,
    SCENARIOS_DIR,
)

logger = logging.getLogger("jee_tutor.evals.runner")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def guard_against_hosted() -> None:
    """Refuse to run live or record against hosted Supabase to protect production data."""
    supabase_url = getattr(config, "SUPABASE_URL", "") or os.getenv("SUPABASE_URL", "")
    parsed = urlparse(supabase_url)
    hostname = (parsed.hostname or "").lower()

    if "supabase.co" in hostname and not os.getenv("ALLOW_HOSTED"):
        sys.stderr.write(
            "\n[FATAL] Evals live/record mode refused: points at hosted Supabase instance "
            f"({hostname}).\nEvals only run against local databases (localhost/127.0.0.1). "
            "Set ALLOW_HOSTED=1 only if you explicitly intend to run against hosted data.\n\n"
        )
        sys.exit(1)


def run_single_turn(
    user_message: str,
    session_id: str,
    user_id: str,
    recent_turns: List[dict],
    interaction_count: int,
    setup_state: Optional[dict] = None,
    carry: Optional[dict] = None,
) -> dict:
    """Execute one turn through learning_agent_turn."""
    from agents.learning_agent import learning_agent_turn

    state = {
        "user_id": user_id,
        "session_id": session_id,
        "query": user_message,
        "recent_turns": recent_turns,
        "interaction_count": interaction_count,
        "target_exam": "JEE_ADVANCED",
        "preferred_depth": "standard",
        "carry": dict(carry or {}),
    }
    if carry and "problem_state" in carry:
        state["problem_state"] = carry["problem_state"]
    if setup_state:
        state.update(setup_state)

    return learning_agent_turn(state)


def run_scenario(
    scenario: Scenario,
    mode: str = "replay",
    in_memory_db: Optional[InMemoryDatabase] = None,
    with_judge: bool = False,
) -> ScenarioResult:
    """Execute all turns of a scenario and verify assertions."""
    cassette = Cassette(scenario.id, mode=mode)
    turn_results: List[TurnResult] = []
    recent_turns: List[dict] = []
    carry: Dict[str, Any] = {}
    user_id = f"eval_user_{scenario.id}"
    session_id = f"eval_sess_{scenario.id}"
    scenario_passed = True

    with CassetteContext(cassette):
        with patch_environment(in_memory_db):
            for turn_idx, turn in enumerate(scenario.turns):
                user_msg = turn.user_message

                # Injected tool failure test (scenario 09)
                tool_patch = None
                if "tool_failure" in scenario.tags:
                    from unittest.mock import patch
                    from agents.runtime.toolbelt import Toolbelt
                    orig_call = Toolbelt.call

                    def _failing_call(self, name, args):
                        if name == "retrieve_content":
                            raise RuntimeError("Retrieval service temporarily unavailable")
                        return orig_call(self, name, args)

                    tool_patch = patch.object(Toolbelt, "call", _failing_call)
                    tool_patch.start()

                # Injected slow model / forced close test (scenario 10)
                budget_patch = None
                if "f3" in scenario.tags or "slow_model" in scenario.tags:
                    from unittest.mock import patch
                    from agents.runtime.budget import TurnBudget
                    # Set 0.0s budget to trigger forced close immediately
                    budget_patch = patch("config.LEARNING_AGENT_TIME_BUDGET_S", 0.0)
                    budget_patch.start()

                try:
                    result = run_single_turn(
                        user_message=user_msg,
                        session_id=session_id,
                        user_id=user_id,
                        recent_turns=list(recent_turns),
                        interaction_count=turn_idx + 1,
                        setup_state=turn.setup_state,
                        carry=carry,
                    )
                    from services.turn_service import update_carry

                    update_carry(carry, result)
                except Exception as exc:
                    return ScenarioResult(
                        scenario_id=scenario.id,
                        passed=False,
                        turn_results=turn_results,
                        error=f"Exception during turn {turn_idx + 1}: {exc}",
                    )
                finally:
                    if tool_patch:
                        tool_patch.stop()
                    if budget_patch:
                        budget_patch.stop()

                resp_text = result.get("response") or ""
                trace_path = result.get("agent_trace_path") or []
                tools_called = [
                    p[len("tool:"):] for p in trace_path if p.startswith("tool:")
                ]
                stop_reason = result.get("agent_stop_reason") or "answered"
                checker_verdict = result.get("checker_verdict")

                failures = check_turn_assertions(
                    response_text=resp_text,
                    tools_called=tools_called,
                    stop_reason=stop_reason,
                    checker_verdict=checker_verdict,
                    assertions=turn.assertions,
                )

                turn_passed = len(failures) == 0
                if not turn_passed:
                    scenario_passed = False

                turn_results.append(
                    TurnResult(
                        user_message=user_msg,
                        assistant_response=resp_text,
                        tools_called=tools_called,
                        stop_reason=stop_reason,
                        checker_verdict=checker_verdict,
                        passed=turn_passed,
                        failures=failures,
                    )
                )

                recent_turns.append({"role": "user", "content": user_msg})
                recent_turns.append({"role": "assistant", "content": resp_text})

    return ScenarioResult(
        scenario_id=scenario.id,
        passed=scenario_passed,
        turn_results=turn_results,
    )


def run_suite(
    scenarios: List[Scenario],
    mode: str = "replay",
    with_judge: bool = False,
) -> bool:
    """Run all given scenarios, print report table, and return True if all passed."""
    if mode in ("live", "record"):
        guard_against_hosted()

    print(f"\n================================================================================")
    print(f" AEROPT EVAL HARNESS — Mode: {mode.upper()} ({len(scenarios)} scenarios)")
    print(f"================================================================================")
    print(f"{'Scenario ID':<36} {'Turns':<8} {'Status':<8} {'Time':<8} {'Details'}")
    print(f"{'-'*36} {'-'*8} {'-'*8} {'-'*8} {'-'*16}")

    passed_count = 0
    total_time_start = time.time()

    for sc in scenarios:
        t0 = time.time()
        res = run_scenario(sc, mode=mode, with_judge=with_judge)
        duration = time.time() - t0

        status_str = "PASS" if res.passed else "FAIL"
        if res.passed:
            passed_count += 1
            details = "All assertions passed"
        else:
            if res.error:
                details = f"ERROR: {res.error[:36]}"
            else:
                first_fail = next(
                    (f for tr in res.turn_results for f in tr.failures), "Assertion failed"
                )
                details = f"FAIL: {first_fail[:40]}"

        turns_str = f"{len(res.turn_results)}/{len(sc.turns)}"
        time_str = f"{duration:.2f}s"
        print(f"{sc.id:<36} {turns_str:<8} {status_str:<8} {time_str:<8} {details}")

    total_duration = time.time() - total_time_start
    pct = (passed_count / len(scenarios) * 100) if scenarios else 0.0

    print(f"{'-'*80}")
    print(f"RESULTS: {passed_count}/{len(scenarios)} passed ({pct:.1f}%) in {total_duration:.2f}s\n")
    return passed_count == len(scenarios)


def main() -> None:
    parser = argparse.ArgumentParser(description="Aeropt Tutor Agent Eval Harness")
    parser.add_argument(
        "--mode",
        choices=["replay", "record", "live", "judge"],
        default="replay",
        help="Replay recorded cassettes (default), record live cassettes, run live, or judge calibration.",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Run only the specified scenario id.",
    )
    parser.add_argument(
        "--rerecord",
        type=str,
        default=None,
        help="Re-record live cassette for a single scenario id.",
    )
    parser.add_argument(
        "--with-judge",
        action="store_true",
        help="Also run LLM judge on scenario responses.",
    )

    args = parser.parse_args()

    # Mode: Judge calibration
    if args.mode == "judge":
        from evals.judges.judge import evaluate_calibration

        guard_against_hosted()
        print("\nEvaluating LLM Judge across 10 calibration examples...")
        calib_res = evaluate_calibration()
        print(f"Exact Agreement Rate: {calib_res['exact_agreement_rate'] * 100:.1f}%")
        print(f"Within-1 Agreement Rate: {calib_res['within_one_agreement_rate'] * 100:.1f}%\n")
        for d in calib_res["details"]:
            print(f"  [{d['id']}]: Expected={d['expected']}, Predicted={d['predicted']} (Diff={d['diff']})")
        sys.exit(0 if calib_res["within_one_agreement_rate"] >= 0.8 else 1)

    # Rerecord single scenario
    if args.rerecord:
        target_path = SCENARIOS_DIR / f"{args.rerecord}.yaml"
        if not target_path.exists():
            print(f"Error: Scenario '{args.rerecord}' not found in {SCENARIOS_DIR}")
            sys.exit(1)
        sc = load_scenario(target_path)
        guard_against_hosted()
        print(f"Re-recording cassette for {sc.id}...")
        res = run_scenario(sc, mode="record")
        print(f"Done. Passed={res.passed}")
        sys.exit(0 if res.passed else 1)

    # Load scenarios
    all_scenarios = load_all_scenarios()
    if args.scenario:
        filtered = [s for s in all_scenarios if s.id == args.scenario]
        if not filtered:
            print(f"Error: Scenario '{args.scenario}' not found.")
            sys.exit(1)
        scenarios = filtered
    else:
        scenarios = all_scenarios

    success = run_suite(scenarios, mode=args.mode, with_judge=args.with_judge)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
