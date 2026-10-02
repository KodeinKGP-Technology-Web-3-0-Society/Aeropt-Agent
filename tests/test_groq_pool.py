"""Gemini key pool: env parsing, 429 rotation, dead-key retirement, model fallback."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from openai import RateLimitError

from utils.groq_pool import GeminiKeyPool, load_gemini_api_keys, reset_pool_for_tests
from utils.llm import groq_complete


def _rate_limit() -> RateLimitError:
    err = RateLimitError.__new__(RateLimitError)
    Exception.__init__(err, "Error code: 429 - RESOURCE_EXHAUSTED")
    return err


class _BadKey(Exception):
    status_code = 400

    def __init__(self):
        super().__init__("Error code: 400 - API key not valid. Please pass a valid API key.")


def _completion(text: str):
    msg = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=msg, delta=SimpleNamespace(content=None))
    return SimpleNamespace(choices=[choice])


class FakeGroq:
    """Minimal Groq stand-in keyed by api_key."""

    def __init__(self, api_key: str, fail_keys=None, by_model=None):
        self.api_key = api_key
        self._fail_keys = set(fail_keys or [])
        self._by_model = by_model or {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        model = kwargs.get("model", "")
        if model in self._by_model:
            outcome = self._by_model[model]
            if outcome == "429":
                raise _rate_limit()
            return _completion(outcome)
        if self.api_key in self._fail_keys:
            raise _rate_limit()
        if self.api_key.startswith("bad"):
            raise _BadKey()
        return _completion(f"ok:{self.api_key}")


class TestLoadKeys(unittest.TestCase):
    def test_comma_and_semicolon_list(self):
        keys = load_gemini_api_keys({"GEMINI_KEYS": "k1, k2 ; k3"})
        self.assertEqual(keys, ["k1", "k2", "k3"])

    def test_single_key_fallback(self):
        self.assertEqual(load_gemini_api_keys({"GEMINI_API_KEY": "solo"}), ["solo"])

    def test_dedupes(self):
        keys = load_gemini_api_keys({"GEMINI_KEYS": "k1,k2,k1,k3,k2"})
        self.assertEqual(keys, ["k1", "k2", "k3"])

    def test_empty(self):
        self.assertEqual(load_gemini_api_keys({}), [])


class TestKeyRotation(unittest.TestCase):
    def tearDown(self):
        reset_pool_for_tests(None)

    def test_rotates_to_next_key_on_429(self):
        def factory(api_key):
            return FakeGroq(api_key, fail_keys={"k1"})

        pool = GeminiKeyPool(["k1", "k2"], client_factory=factory)
        reset_pool_for_tests(pool)
        text, model = groq_complete(
            client=MagicMock(),
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(text, "ok:k2")
        self.assertEqual(model, "llama-3.1-8b-instant")

    def test_rotates_past_more_keys_than_model_retries(self):
        """Six hot keys then a cool one: rotation must not stop at MAX_RETRIES."""
        hot = {f"k{i}" for i in range(6)}
        pool = GeminiKeyPool(
            [f"k{i}" for i in range(7)],
            client_factory=lambda k: FakeGroq(k, fail_keys=hot),
        )
        reset_pool_for_tests(pool)
        text, _ = groq_complete(
            client=MagicMock(), model="m", messages=[{"role": "user", "content": "hi"}]
        )
        self.assertEqual(text, "ok:k6")

    def test_rejected_key_is_retired_not_fatal(self):
        pool = GeminiKeyPool(["bad1", "good"], client_factory=FakeGroq)
        reset_pool_for_tests(pool)
        for _ in range(3):
            text, _ = groq_complete(
                client=MagicMock(), model="m", messages=[{"role": "user", "content": "hi"}]
            )
            self.assertEqual(text, "ok:good")
        self.assertEqual(pool.live_count, 1)

    def test_next_model_when_every_key_429s(self):
        def factory(api_key):
            return FakeGroq(
                api_key,
                by_model={"hot-model": "429", "cool-model": f"ok:{api_key}"},
            )

        pool = GeminiKeyPool(["k1", "k2"], client_factory=factory)
        reset_pool_for_tests(pool)
        text, model = groq_complete(
            client=MagicMock(),
            model="hot-model",
            messages=[{"role": "user", "content": "hi"}],
            fallback_models=["cool-model"],
        )
        self.assertEqual(model, "cool-model")
        self.assertTrue(text.startswith("ok:"))

    def test_tpm_413_does_not_raise_and_clamps_max_tokens(self):
        """Same-org keys cannot absorb a request bigger than the model TPM cap."""

        class TpmFake(FakeGroq):
            def create(self, **kwargs):
                if kwargs.get("max_tokens", 0) > 4000:
                    raise RuntimeError(
                        "Error code: 413 - Request too large for model "
                        "`qwen/qwen3.8-27b` on tokens per minute (TPM): "
                        "Limit 8000, Requested 8505"
                    )
                return _completion("ok-clamped")

        pool = GeminiKeyPool(["k1", "k2"], client_factory=TpmFake)
        reset_pool_for_tests(pool)
        text, model = groq_complete(
            client=MagicMock(),
            model="qwen/qwen3.8-27b",
            messages=[{"role": "user", "content": "hi"}],
            fallback_models=["openai/gpt-oss-20b"],
            max_tokens=8192,
        )
        self.assertEqual(text, "ok-clamped")
        self.assertEqual(model, "qwen/qwen3.8-27b")

    def test_tpm_413_skips_to_fallback_model(self):
        class Always413ThenOk(FakeGroq):
            def create(self, **kwargs):
                if kwargs.get("model") == "qwen/qwen3.8-27b":
                    raise RuntimeError(
                        "Error code: 413 - Request too large for model "
                        "`qwen/qwen3.8-27b` on tokens per minute (TPM): Limit 8000"
                    )
                return _completion("ok-fallback")

        pool = GeminiKeyPool(["k1", "k2"], client_factory=Always413ThenOk)
        reset_pool_for_tests(pool)
        text, model = groq_complete(
            client=MagicMock(),
            model="qwen/qwen3.8-27b",
            messages=[{"role": "user", "content": "hi"}],
            fallback_models=["openai/gpt-oss-20b"],
            max_tokens=2000,
        )
        self.assertEqual(text, "ok-fallback")
        self.assertEqual(model, "openai/gpt-oss-20b")


if __name__ == "__main__":
    unittest.main()
