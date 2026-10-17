"""Tests for harness instrumentation: usage tracking, trace v2 metrics, transcripts, and reports."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agents.runtime.trace import AgentTrace, TraceStep
from scripts.harness_report import compute_report_metrics, guard_against_hosted
from utils.llm import UsageMeter, usage_meter
from utils.skills import get_skill_version


def test_usage_meter_contextvar():
    meter = UsageMeter()
    token = usage_meter.set(meter)
    try:
        current = usage_meter.get()
        assert current is meter
        current.llm_calls += 1
        current.prompt_tokens += 150
        current.completion_tokens += 50
        current.cached_tokens += 20
        assert meter.llm_calls == 1
        assert meter.prompt_tokens == 150
        assert meter.completion_tokens == 50
        assert meter.cached_tokens == 20
    finally:
        usage_meter.reset(token)
    assert usage_meter.get() is None


def test_agent_trace_metrics_to_row():
    trace = AgentTrace(
        user_id="test_user",
        session_id="test_sess",
        turn_index=2,
        route="agent_loop",
        prompt_version="abc123def456",
        toolset_version="789xyz123abc",
        llm_calls=3,
        input_tokens=1200,
        output_tokens=350,
        cached_tokens=400,
        stop_reason="answered",
        max_step_input_chars=5400,
    )
    trace.checker_verdict = "pass"
    trace.add(TraceStep(kind="tool", name="retrieve_content", ok=True))

    row = trace.to_row()
    assert "metrics" in row
    m = row["metrics"]
    assert m["llm_calls"] == 3
    assert m["input_tokens"] == 1200
    assert m["output_tokens"] == 350
    assert m["cached_tokens"] == 400
    assert m["stop_reason"] == "answered"
    assert m["prompt_version"] == "abc123def456"
    assert m["toolset_version"] == "789xyz123abc"
    assert m["max_step_input_chars"] == 5400


def test_get_skill_version():
    ver = get_skill_version("learning_agent")
    assert isinstance(ver, str)
    assert len(ver) == 12


def test_compute_report_metrics():
    sample_traces = [
        {
            "latency_ms": 1500,
            "tool_calls": 2,
            "checker_verdict": "pass",
            "metrics": {
                "llm_calls": 3,
                "input_tokens": 1000,
                "output_tokens": 200,
                "cached_tokens": 500,
                "stop_reason": "answered",
            },
            "steps": [
                {"kind": "tool", "name": "retrieve_content", "args": {"q": "photoelectric"}, "ok": True},
                {"kind": "tool", "name": "verify_result", "args": {"v": 5}, "ok": True},
            ],
        },
        {
            "latency_ms": 3500,
            "tool_calls": 1,
            "checker_verdict": "revised",
            "metrics": {
                "llm_calls": 2,
                "input_tokens": 2000,
                "output_tokens": 400,
                "cached_tokens": 1000,
                "stop_reason": "time_budget",
            },
            "steps": [
                {"kind": "tool", "name": "retrieve_content", "args": {"q": "photoelectric"}, "ok": False, "error": "timeout"},
            ],
        },
    ]

    report = compute_report_metrics(sample_traces)
    assert report["total_turns"] == 2
    assert report["forced_close_rate"] == 0.5  # 1 out of 2 had time_budget
    assert report["cached_token_share"] == 1500 / 3000  # 50%
    assert report["checker_verdict_mix"]["pass"] == 1
    assert report["checker_verdict_mix"]["revised"] == 1
    assert report["tool_error_rate"] == 1 / 3  # 1 error out of 3 tool calls


def test_guard_against_hosted():
    with pytest.raises(SystemExit):
        guard_against_hosted("postgresql://user:pass@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres")

    # Should not raise for localhost
    guard_against_hosted("postgresql://user:pass@localhost:5432/postgres")
    guard_against_hosted("postgresql://user:pass@127.0.0.1:5432/postgres")
    # Should not raise when allow_hosted=True
    guard_against_hosted("postgresql://user:pass@supabase.co:5432/postgres", allow_hosted=True)
