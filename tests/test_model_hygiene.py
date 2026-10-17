"""Tests for Phase 7: Model-access hygiene (priority lanes, model drift guard, and type hints)."""

from unittest.mock import MagicMock, patch

import pytest
from utils.groq_pool import (
    GeminiKeyPool,
    check_model_drift,
    clear_pruned_models,
    get_pruned_models,
)
from utils.llm import _run_chain, groq_chat, groq_complete


class DummyModel:
    def __init__(self, id: str):
        self.id = id


def test_priority_lane_cool_keys():
    """When cool keys are available, both live and background lanes get clients."""
    pool = GeminiKeyPool(["key1", "key2"])
    
    live_clients = list(pool.iter_clients(override=None, model="model-a", lane="live"))
    assert len(live_clients) == 2

    bg_clients = list(pool.iter_clients(override=None, model="model-a", lane="background"))
    assert len(bg_clients) == 2


def test_priority_lane_hot_keys_drops_background():
    """When all keys are hot, background calls are dropped while live calls fallback."""
    pool = GeminiKeyPool(["key1", "key2"])
    # Mark both keys as rate limited
    pool.mark_limited(0, "model-a", seconds=60.0)
    pool.mark_limited(1, "model-a", seconds=60.0)

    # Background lane drops immediately (yields nothing)
    bg_clients = list(pool.iter_clients(override=None, model="model-a", lane="background"))
    assert len(bg_clients) == 0

    # Live lane yields fallback keys so student is not dropped
    live_clients = list(pool.iter_clients(override=None, model="model-a", lane="live"))
    assert len(live_clients) == 2


def test_run_chain_drops_background_when_keys_exhausted():
    """_run_chain returns (None, model) for background calls when all keys are hot."""
    pool = GeminiKeyPool(["key1"])
    pool.mark_limited(0, "model-a", seconds=60.0)

    with patch("utils.groq_pool.get_pool", return_value=pool):
        # Background lane returns (None, model)
        msg, model_used = _run_chain(
            client=MagicMock(),
            model="model-a",
            messages=[{"role": "user", "content": "hello"}],
            lane="background",
        )
        assert msg is None
        assert model_used == "model-a"


def test_check_model_drift_detects_and_prunes_missing_models():
    """check_model_drift detects missing models and registers them in get_pruned_models()."""
    clear_pruned_models()
    try:
        mock_client = MagicMock()
        mock_client.models.list.return_value = MagicMock(
            data=[DummyModel("model-valid-1"), DummyModel("model-valid-2")]
        )

        configured = ["model-valid-1", "model-missing-x", "model-valid-2"]
        missing = check_model_drift(client=mock_client, configured_models=configured)

        assert missing == ["model-missing-x"]
        assert "model-missing-x" in get_pruned_models()

        # In _run_chain, model-missing-x is pruned from fallback chain
        with patch("utils.llm._pick_client") as mock_pick:
            mock_client_inst = MagicMock()
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content="ok", tool_calls=None))]
            mock_client_inst.chat.completions.create.return_value = mock_resp
            mock_pick.return_value = (None, mock_client_inst)

            _run_chain(
                client=mock_client_inst,
                model="model-valid-1",
                messages=[{"role": "user", "content": "hi"}],
                fallback_models=["model-missing-x", "model-valid-2"],
            )

            # Check that completions.create was called with model-valid-1
            assert mock_client_inst.chat.completions.create.call_args[1]["model"] == "model-valid-1"
    finally:
        clear_pruned_models()


def test_check_model_drift_fails_open_on_provider_error():
    """If provider models.list() errors, check_model_drift logs warning and returns empty."""
    clear_pruned_models()
    try:
        mock_client = MagicMock()
        mock_client.models.list.side_effect = RuntimeError("API unavailable")

        missing = check_model_drift(client=mock_client, configured_models=["model-a"])
        assert missing == []
    finally:
        clear_pruned_models()
