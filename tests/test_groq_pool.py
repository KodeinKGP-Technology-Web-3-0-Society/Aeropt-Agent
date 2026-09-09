"""Groq key pool: env parsing, 429 rotation, model fallback."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from groq import RateLimitError

from utils.groq_pool import GroqKeyPool, load_groq_api_keys, reset_pool_for_tests
from utils.llm import groq_complete


def _rate_limit() -> RateLimitError:
    try:
        return RateLimitError("Rate limit reached")
    except TypeError:
        err = RateLimitError.__new__(RateLimitError)
        Exception.__init__(err, "Rate limit reached")
        return err


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
        return _completion(f"ok:{self.api_key}")


class TestLoadKeys(unittest.TestCase):
    def test_numbered_keys(self):
        keys = load_groq_api_keys(
            {
                "GROQ_API_KEY": "gsk_a",
                "GROQ_API_KEY_2": "gsk_b",
                "GROQ_API_KEY_3": "gsk_c",
            }
        )
        self.assertEqual(keys, ["gsk_a", "gsk_b", "gsk_c"])

    def test_comma_list_wins(self):
        keys = load_groq_api_keys(
            {
                "GROQ_API_KEYS": "gsk_1, gsk_2 ; gsk_3",
                "GROQ_API_KEY": "ignored",
            }
        )
        self.assertEqual(keys, ["gsk_1", "gsk_2", "gsk_3"])

    def test_dedupes(self):
        keys = load_groq_api_keys(
            {"GROQ_API_KEY": "gsk_a", "GROQ_API_KEY_2": "gsk_a", "GROQ_API_KEY_3": "gsk_b"}
        )
        self.assertEqual(keys, ["gsk_a", "gsk_b"])


class TestKeyRotation(unittest.TestCase):
    def tearDown(self):
        reset_pool_for_tests(None)

    def test_rotates_to_next_key_on_429(self):
        def factory(api_key):
            return FakeGroq(api_key, fail_keys={"k1"})

        pool = GroqKeyPool(["k1", "k2"], client_factory=factory)
        reset_pool_for_tests(pool)
        text, model = groq_complete(
            client=MagicMock(),
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(text, "ok:k2")
        self.assertEqual(model, "llama-3.1-8b-instant")

    def test_next_model_when_every_key_429s(self):
        def factory(api_key):
            return FakeGroq(
                api_key,
                by_model={"hot-model": "429", "cool-model": f"ok:{api_key}"},
            )

        pool = GroqKeyPool(["k1", "k2"], client_factory=factory)
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

        pool = GroqKeyPool(["k1", "k2"], client_factory=TpmFake)
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

        pool = GroqKeyPool(["k1", "k2"], client_factory=Always413ThenOk)
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
