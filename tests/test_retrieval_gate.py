import pytest
from unittest.mock import patch
from agents.retrieval_gate import should_skip_retrieval, is_meta_repair_turn

def test_missing_primary_concept():
    state = {}
    assert should_skip_retrieval(state) is True

def test_prefetch_cache_hit():
    state = {"primary_concept_id": "concept_1", "_prefetch_fast_forward": True}
    assert should_skip_retrieval(state) is True

@patch("agents.retrieval_gate.is_meta_repair_turn", return_value=True)
def test_meta_turn_skips_retrieval(mock_meta):
    state = {
        "primary_concept_id": "concept_1",
        "intent": "doubt",
        "query": "why is this wrong?",
        "recent_turns": []
    }
    assert should_skip_retrieval(state) is True

@patch("agents.retrieval_gate.is_meta_repair_turn", return_value=False)
def test_normal_doubt_turn_retrieves(mock_meta):
    state = {
        "primary_concept_id": "concept_1",
        "intent": "doubt",
        "query": "explain torque",
        "recent_turns": []
    }
    assert should_skip_retrieval(state) is False
