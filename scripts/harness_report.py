"""Harness report for Aeropt tutor agent telemetry.

Reads `agent_trace` from the local database only (refuses hosted Supabase to protect
production data) and prints key performance metrics for the last N turns:
- p50/p95 latency
- LLM calls per turn
- Input tokens per turn
- Checker verdict mix
- Stop reason mix
- Tool error rate
- Repeated-call rate
- Forced-close rate
- Cached-token share

Usage:
    python -m scripts.harness_report [--limit 50] [--allow-hosted]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def guard_against_hosted(db_url: str, allow_hosted: bool = False) -> None:
    """Refuse to read or report from hosted database unless allow_hosted is True."""
    if allow_hosted:
        return
    host = (urlparse(db_url).hostname or "").lower()
    if host and host not in LOCAL_HOSTS:
        raise SystemExit(
            f"Refusing to run harness report against hosted host {host!r}. "
            "Telemetry reports run against the local database only. "
            "Pass --allow-hosted if you explicitly intend to read hosted traces."
        )


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    d = k - f
    return sorted_vals[f] + d * (sorted_vals[c] - sorted_vals[f])


def compute_report_metrics(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute all 9 harness metrics from a list of trace row dicts."""
    total_turns = len(traces)
    if total_turns == 0:
        return {
            "total_turns": 0,
            "latency_p50_ms": 0.0,
            "latency_p95_ms": 0.0,
            "llm_calls_p50": 0.0,
            "llm_calls_p95": 0.0,
            "input_tokens_p50": 0.0,
            "input_tokens_p95": 0.0,
            "cached_token_share": 0.0,
            "checker_verdict_mix": {},
            "stop_reason_mix": {},
            "tool_error_rate": 0.0,
            "repeated_call_rate": 0.0,
            "forced_close_rate": 0.0,
        }

    latencies: List[float] = []
    llm_calls_list: List[float] = []
    input_tokens_list: List[float] = []
    total_cached = 0
    total_input = 0

    checker_counts: Counter = Counter()
    stop_reason_counts: Counter = Counter()

    total_tool_steps = 0
    total_tool_errors = 0
    total_repeated_calls = 0
    forced_close_turns = 0

    for t in traces:
        if t.get("latency_ms") is not None:
            latencies.append(float(t["latency_ms"]))

        metrics = t.get("metrics") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}

        calls = metrics.get("llm_calls")
        if calls is None:
            calls = int(t.get("tool_calls") or 0) + 1
        llm_calls_list.append(float(calls))

        inp_tokens = int(metrics.get("input_tokens") or 0)
        out_tokens = int(metrics.get("output_tokens") or 0)
        cached = int(metrics.get("cached_tokens") or 0)

        input_tokens_list.append(float(inp_tokens))
        total_input += inp_tokens
        total_cached += cached

        verdict = (t.get("checker_verdict") or "none").strip().lower()
        checker_counts[verdict] += 1

        stop_r = (metrics.get("stop_reason") or "answered").strip()
        stop_reason_counts[stop_r] += 1
        if stop_r in ("max_steps", "time_budget"):
            forced_close_turns += 1

        steps = t.get("steps") or []
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except Exception:
                steps = []

        seen_tool_signatures = set()
        for step in steps:
            if step.get("kind") == "tool":
                total_tool_steps += 1
                if not step.get("ok", True) or step.get("error"):
                    total_tool_errors += 1
                sig = (step.get("name"), json.dumps(step.get("args") or {}, sort_keys=True))
                if sig in seen_tool_signatures:
                    total_repeated_calls += 1
                else:
                    seen_tool_signatures.add(sig)

    cached_share = (total_cached / total_input) if total_input > 0 else 0.0
    tool_err_rate = (total_tool_errors / total_tool_steps) if total_tool_steps > 0 else 0.0
    repeated_rate = (total_repeated_calls / total_tool_steps) if total_tool_steps > 0 else 0.0
    forced_close_rate = forced_close_turns / total_turns

    return {
        "total_turns": total_turns,
        "latency_p50_ms": _percentile(latencies, 50),
        "latency_p95_ms": _percentile(latencies, 95),
        "llm_calls_p50": _percentile(llm_calls_list, 50),
        "llm_calls_p95": _percentile(llm_calls_list, 95),
        "input_tokens_p50": _percentile(input_tokens_list, 50),
        "input_tokens_p95": _percentile(input_tokens_list, 95),
        "cached_token_share": cached_share,
        "checker_verdict_mix": dict(checker_counts),
        "stop_reason_mix": dict(stop_reason_counts),
        "tool_error_rate": tool_err_rate,
        "repeated_call_rate": repeated_rate,
        "forced_close_rate": forced_close_rate,
    }


def fetch_traces_from_db(limit: int = 50, allow_hosted: bool = False) -> List[Dict[str, Any]]:
    """Fetch the latest N traces from the configured database."""
    # Try direct postgres connection or Supabase client
    db_url = getattr(config, "RETRIEVAL_DATABASE_URL", "") or os.getenv("RETRIEVAL_DATABASE_URL", "")
    guard_against_hosted(db_url, allow_hosted)

    try:
        from db.supabase_client import db

        res = (
            db.client.table("agent_trace")
            .select("user_id, session_id, latency_ms, tool_calls, checker_verdict, steps, metrics, created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as exc:
        print(f"[harness_report] Could not query agent_trace: {exc}", file=sys.stderr)
        return []


def format_report(metrics: Dict[str, Any]) -> str:
    lines = [
        "=" * 60,
        f" AEROPT HARNESS REPORT (Last {metrics['total_turns']} Turns)",
        "=" * 60,
        f"Latency p50 / p95:          {metrics['latency_p50_ms']:.0f} ms / {metrics['latency_p95_ms']:.0f} ms",
        f"LLM Calls per turn p50/p95: {metrics['llm_calls_p50']:.1f} / {metrics['llm_calls_p95']:.1f}",
        f"Input tokens p50 / p95:     {metrics['input_tokens_p50']:.0f} / {metrics['input_tokens_p95']:.0f}",
        f"Cached token share:         {metrics['cached_token_share']:.1%}",
        f"Tool error rate:            {metrics['tool_error_rate']:.1%}",
        f"Repeated tool call rate:    {metrics['repeated_call_rate']:.1%}",
        f"Forced close rate:          {metrics['forced_close_rate']:.1%}",
        "-" * 60,
        "Checker Verdicts:",
    ]
    for verdict, count in sorted(metrics.get("checker_verdict_mix", {}).items()):
        pct = (count / metrics["total_turns"]) if metrics["total_turns"] > 0 else 0
        lines.append(f"  - {verdict:12s}: {count:3d} ({pct:.1%})")

    lines.append("Stop Reasons:")
    for reason, count in sorted(metrics.get("stop_reason_mix", {}).items()):
        pct = (count / metrics["total_turns"]) if metrics["total_turns"] > 0 else 0
        lines.append(f"  - {reason:12s}: {count:3d} ({pct:.1%})")

    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Aeropt Agent Harness Report")
    parser.add_argument("--limit", type=int, default=50, help="Number of recent turns to analyze (default: 50)")
    parser.add_argument("--allow-hosted", action="store_true", help="Allow running against hosted DB")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of formatted text")
    args = parser.parse_args()

    traces = fetch_traces_from_db(limit=args.limit, allow_hosted=args.allow_hosted)
    metrics = compute_report_metrics(traces)

    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print(format_report(metrics))


if __name__ == "__main__":
    main()
