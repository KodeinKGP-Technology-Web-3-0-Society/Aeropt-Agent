"""Scorer and harness for the hard-problem correctness set. No model calls."""

from __future__ import annotations

import json

import pytest

from evals.correctness.runner import HostedRetrievalBlocked, block_hosted, percentile, summarize_items
from evals.correctness.score import Expectation, grade_response, load_problems, recompute
from evals.correctness.traces import is_local_url, summarize_turns
from evals.scenarios import load_all_scenarios


def test_set_size_and_ids_stay_out_of_the_behaviour_suite():
    problems = load_problems()
    assert 25 <= len(problems) <= 40
    ids = [p.id for p in problems]
    assert len(ids) == len(set(ids))
    behaviour = {s.id for s in load_all_scenarios()}
    assert behaviour.isdisjoint(ids)
    assert "cosh_integral_n1" in ids


def test_authored_keys_match_sympy():
    for problem in load_problems():
        if not problem.verify:
            continue
        got = recompute(problem.verify)
        assert problem.expect.value is not None
        assert got == pytest.approx(problem.expect.value, rel=0, abs=1e-6)


def test_student_message_does_not_carry_the_key_block():
    problem = next(p for p in load_problems() if p.id == "cosh_integral_n1")
    message = problem.student_message()
    assert "Solve this and give the final answer." in message
    assert "verify" not in message
    assert "-5.77" not in message


def test_cosh_trap_accepts_the_value_and_the_closed_form():
    expect = next(p.expect for p in load_problems() if p.id == "cosh_integral_n1")
    right = grade_response(r"The integral is \boxed{-5.774}", expect)
    closed = grade_response(r"\boxed{-\sinh(\pi)/2}", expect)
    wrong = grade_response(r"\boxed{\pi/2}", expect)
    assert right.status == "pass"
    assert closed.status == "pass"
    assert wrong.status == "wrong"
    assert wrong.numeric == pytest.approx(1.5708, abs=1e-3)


def test_limit_trap_rejects_one_half():
    expect = next(p.expect for p in load_problems() if p.id == "limit_tan_minus_x")
    assert grade_response(r"\boxed{1/3}", expect).status == "pass"
    assert grade_response(r"\boxed{1/2}", expect).status == "wrong"


def test_option_letters_match_as_a_set():
    expect = Expectation(kind="letters", letters="CD")
    assert grade_response(r"\boxed{(C), (D)}", expect).status == "pass"
    assert grade_response(r"\boxed{D, C}", expect).status == "pass"
    assert grade_response(r"\boxed{C}", expect).status == "wrong"


def test_withheld_answer_is_unparsed_not_wrong():
    expect = Expectation(kind="integer", value=6, abs_tol=0.05, rel_tol=0)
    grade = grade_response("Try drawing the free-body diagram first.", expect)
    assert grade.status == "unparsed"


def test_answer_line_is_enough_when_nothing_is_boxed():
    expect = Expectation(kind="integer", value=6, abs_tol=0.05, rel_tol=0)
    grade = grade_response("After the algebra, the final answer is 6.", expect)
    assert grade.status == "pass"
    assert grade.method == "answer_line"


def test_summary_counts_forced_stops_and_latency():
    items = [
        {
            "status": "pass",
            "subject": "physics",
            "latency_ms": 100,
            "stop_reason": "answered",
            "checker_verdict": "pass",
            "fallback": False,
            "retrieve_called": True,
            "retrieve_empty": True,
        },
        {
            "status": "wrong",
            "subject": "physics",
            "latency_ms": 900,
            "stop_reason": "time_budget",
            "checker_verdict": "skipped:budget",
            "fallback": True,
            "retrieve_called": False,
            "retrieve_empty": False,
        },
    ]
    summary = summarize_items(items)
    assert summary["pass"] == 1
    assert summary["pass_rate"] == 0.5
    assert summary["forced_close"] == 1
    assert summary["checker_skipped"] == 1
    assert summary["fallback_turns"] == 1
    assert summary["retrieve_empty"] == 1
    assert percentile([100, 900], 50) == 500


def test_transcript_scan_separates_eval_sessions(tmp_path):
    from evals.correctness.traces import summarize_transcripts

    real = tmp_path / "sess" / "1.jsonl"
    real.parent.mkdir()
    real.write_text(
        json.dumps(
            {
                "step": 5,
                "messages": [
                    {"role": "assistant", "tool_calls": [{"function": {"name": "retrieve_content"}}]},
                    {"role": "tool", "name": "retrieve_content", "content": '{"chunks_found": 0}'},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "eval_sess_01").mkdir()
    (tmp_path / "eval_sess_01" / "1.jsonl").write_text(
        json.dumps({"step": 1, "messages": []}) + "\n", encoding="utf-8"
    )
    report = summarize_transcripts(tmp_path)
    assert report["real_files"] == 1
    assert report["eval_files"] == 1
    assert report["reached_closing_pass"] == 1
    assert report["retrieve_chunks_found"] == {"0": 1}


def test_trace_summary_and_local_url_guard():
    summary = summarize_turns(
        [
            {"stop_reason": "max_steps", "checker_verdict": "skipped:no_claims"},
            {"stop_reason": "call_cap", "checker_verdict": "pass", "fallback": True},
        ]
    )
    assert summary["forced_close"] == 2
    assert summary["checker_skipped"] == 1
    assert summary["fallback_turns"] == 1
    assert is_local_url("postgresql://postgres@127.0.0.1:54322/postgres")
    assert not is_local_url("postgresql://postgres@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres")


def test_replay_rescores_a_saved_reply(tmp_path):
    from evals.correctness.runner import run_replay

    problem = next(p for p in load_problems() if p.id == "series_k_over_2k")
    path = tmp_path / "run.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "id": problem.id,
                        "subject": problem.subject,
                        "response": r"\boxed{2}",
                        "stop_reason": "answered",
                        "checker_verdict": "pass",
                        "latency_ms": 10,
                        "fallback": False,
                        "retrieve_called": False,
                        "retrieve_empty": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    payload = run_replay(path)
    assert payload["items"][0]["status"] == "pass"


def test_hosted_retrieval_cannot_be_queried():
    import retrieval.store as store

    with block_hosted():
        with pytest.raises(HostedRetrievalBlocked):
            store.query("select 1")
        assert store.index_exists() is False
