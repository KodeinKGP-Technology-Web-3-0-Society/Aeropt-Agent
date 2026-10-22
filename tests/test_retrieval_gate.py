"""Exercise the active retrieval gate and its optional model classifier."""

from unittest.mock import Mock, patch

import pytest

from retrieval import gate


def test_empty_message_skips_retrieval():
    assert gate.decide("").action == "skip"


def test_answer_submission_does_not_fetch_new_material():
    decision = gate.decide("B", state={"awaiting_graded_response": True})
    assert decision.action == "skip"
    assert decision.notes["grading_turn"] is True


def test_repair_turn_reuses_existing_material():
    state = {"retrieved_chunks": [{"content_id": "torque-1"}]}
    assert gate.decide("why is this wrong?", state=state).action == "reuse"
    assert gate.decide("explain torque", state=state).action == "retrieve"


def test_explicit_tool_request_bypasses_skip_and_reuse():
    assert gate.decide("thanks", forced=True).action == "retrieve"


@pytest.mark.parametrize("classification, expected", [(True, "reuse"), (False, "retrieve")])
def test_optional_classifier_controls_ambiguous_turns(monkeypatch, classification, expected):
    monkeypatch.setattr(gate, "GATE_LLM", True)
    classifier = Mock(return_value=classification)
    monkeypatch.setattr(gate, "is_meta_repair_turn", classifier)
    state = {"retrieved_chunks": [{"content_id": "torque-1"}]}
    assert gate.decide("That approach seems unfamiliar", state=state).action == expected
    classifier.assert_called_once()


def test_disabled_classifier_makes_no_model_call(monkeypatch):
    monkeypatch.setattr(gate, "GATE_LLM", False)
    classifier = Mock(side_effect=AssertionError("model must remain optional"))
    monkeypatch.setattr(gate, "is_meta_repair_turn", classifier)
    state = {"retrieved_chunks": [{"content_id": "torque-1"}]}
    assert gate.decide("That approach seems unfamiliar", state=state).action == "retrieve"
    classifier.assert_not_called()


def test_classifier_failure_keeps_retrieval_available(monkeypatch):
    monkeypatch.setattr(gate, "GATE_LLM", True)
    monkeypatch.setattr(gate, "is_meta_repair_turn", Mock(side_effect=RuntimeError("offline")))
    state = {"retrieved_chunks": [{"content_id": "torque-1"}]}
    assert gate.decide("That approach seems unfamiliar", state=state).action == "retrieve"


def test_classifier_preserves_completion_prompt_and_failure_behavior():
    gate._meta_classifier_client.cache_clear()
    with patch("openai.OpenAI"), patch("utils.llm.complete_text", return_value=("TRUE", "model")) as complete:
        assert gate.is_meta_repair_turn("Explain that last line", [{"role": "assistant", "content": "Torque."}])
        prompt = complete.call_args.kwargs["messages"][0]["content"]
        assert "Torque." in prompt
        assert "Explain that last line" in prompt
    with patch("openai.OpenAI"), patch("utils.llm.complete_text", side_effect=RuntimeError("offline")):
        assert gate.is_meta_repair_turn("Explain that last line", []) is False
    gate._meta_classifier_client.cache_clear()
