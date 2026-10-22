"""Tests for Phase 6 latency optimizations."""

import time
from unittest.mock import MagicMock, patch

import pytest
from agents.answer_checker import CheckResult, check_answer, has_claims_to_check
from agents.learning_agent import _resolve_final_text
from agents.runtime.toolbelt import ToolContext
from api_server import _PREFETCH_CACHE, _prefetch_next_query


def test_has_claims_to_check_distinguishes_analytical_from_prose():
    # True for physical units, equations, formulas, exam labels
    assert has_claims_to_check("The speed is $v = 10$ m/s.") is True
    assert has_claims_to_check("Applying Newton's second law: $F = ma$.") is True
    assert has_claims_to_check("As asked in JEE Advanced 2016 Paper 1, consider...") is True
    assert has_claims_to_check(r"The result is \boxed{42\text{ N}}.") is True

    # False for plain prose and non-claim responses
    assert has_claims_to_check("That is a great perspective on studying consistently.") is False
    assert has_claims_to_check("Could you tell me what concept you would like to explore today?") is False
    assert has_claims_to_check("Let us break this down step by step together.") is False


def test_check_answer_skips_llm_for_prose_draft():
    prose_draft = (
        "Consistency is key when preparing for competitive examinations. "
        "Reviewing the fundamental principles daily and practicing with focused attention "
        "will yield steady progress over time. Keep going!"
    ) * 3
    assert len(prose_draft) > 180

    with patch("agents.answer_checker.complete_text") as mock_complete:
        res = check_answer(query="How to study?", draft=prose_draft, state={})
        assert res.verdict == "skipped"
        assert "No analytical or exam claims" in res.issues[0]
        # complete_text was never called!
        mock_complete.assert_not_called()


def test_resolve_final_text_detects_high_similarity():
    draft = "A cylinder of mass M and radius R rolls without slipping down an inclined plane of angle theta."
    # Slightly rephrased text with >= 90% character similarity
    copied_text = "A cylinder of mass M and radius R rolls without slipping down an inclined plane of angle theta"
    ctx = ToolContext(
        user_id="u",
        session_id="s",
        state={"draft_response": draft},
    )

    resolved = _resolve_final_text(copied_text, ctx)
    assert resolved == draft


def test_in_process_prefetch_cache():
    _PREFETCH_CACHE.clear()
    state = {
        "follow_up_suggestions": [{"text": "photoelectric effect"}],
        "user_id": "u1",
        "session_id": "s1",
    }
    with patch("agents.concept_resolver.concept_resolver_node", return_value={"primary_concept_id": "c1"}), \
         patch("services.personalization.personalize_for_state", return_value={}), \
         patch("retrieval.bridge.retrieve_for_state", return_value={"retrieved_chunks": [{"id": "chk1"}]}):
        _prefetch_next_query("s1", state)

    cache_key = "prefetch:s1:photoelectric effect"
    assert cache_key in _PREFETCH_CACHE
    ts, val = _PREFETCH_CACHE[cache_key]
    assert val["primary_concept_id"] == "c1"
    assert val["retrieved_chunks"] == [{"id": "chk1"}]
