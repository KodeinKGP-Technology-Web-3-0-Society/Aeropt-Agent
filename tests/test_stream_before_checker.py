"""The student sees the draft while the checker runs; a revision replaces it."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import agents.learning_agent as la
from agents.answer_checker import VERDICT_PASS, VERDICT_REVISED, CheckResult
from utils.stream import stream_callback_var, stream_replace_var

DRAFT = "Torque is $\\tau = rF\\sin\\theta$, so it doubles when the force doubles at a fixed angle."


def _run(monkeypatch, checker):
    shown, replaced = [], []
    checker_started = threading.Event()
    streamed_before_checker = []

    monkeypatch.setattr(la.db, "read_learner_memories", lambda *a, **k: [])
    monkeypatch.setattr(la, "run_agent_loop", lambda **k: SimpleNamespace(
        text=DRAFT, model_used="m", steps_taken=1, stop_reason="answered", error=None, messages=[], tool_calls_made=[]))
    monkeypatch.setattr("agents.answer_checker.has_claims_to_check", lambda _a: True)

    def slow_checker(**_kwargs):
        checker_started.set()
        # give the streaming thread time to deliver the draft first
        for _ in range(100):
            if "fixed angle" in "".join(shown):
                break
            threading.Event().wait(0.02)
        streamed_before_checker.append("".join(shown))
        return checker(_kwargs)

    monkeypatch.setattr(la, "check_answer_streaming", slow_checker)

    t1 = stream_callback_var.set(shown.append)
    t2 = stream_replace_var.set(replaced.append)
    try:
        out = la.learning_agent_turn({"user_id": "u1", "session_id": "s1", "query": "what is torque?", "interaction_count": 1})
    finally:
        stream_callback_var.reset(t1)
        stream_replace_var.reset(t2)
    return out, "".join(shown), replaced, streamed_before_checker[0]


def test_passed_draft_is_streamed_once_before_checker_returns(monkeypatch):
    out, shown, replaced, early = _run(monkeypatch, lambda kw: CheckResult(kw["draft"], VERDICT_PASS, []))
    assert early.strip() == shown.strip() != ""  # already on screen when the checker returned
    assert replaced == []
    assert out["response"].strip() == shown.strip()


def test_revision_replaces_the_streamed_draft(monkeypatch):
    out, shown, replaced, early = _run(
        monkeypatch, lambda kw: CheckResult("Torque is r x F sin(theta). Corrected.", VERDICT_REVISED, ["fix"])
    )
    assert early.strip() != ""
    assert len(replaced) == 1 and "Corrected" in replaced[0]
    assert out["response"] == replaced[0]
