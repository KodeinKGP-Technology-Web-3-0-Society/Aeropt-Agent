"""
F3 regression: catches Groq model-catalog drift (models silently decommissioned
on this account) before it reaches production traffic instead of after.

Skipped by default — needs a real network call and a real GROQ_API_KEY, so it
doesn't belong in the normal fast/offline `pytest tests/` run. Opt in with:

    RUN_LIVE_GROQ_CHECKS=1 python -m pytest tests/test_model_health.py -q

Recommended: run this on a schedule (e.g. nightly CI) against the real key, not
just when someone happens to notice degraded responses.
"""

from __future__ import annotations

import os
import unittest

import config

_SKIP_REASON = "set RUN_LIVE_GROQ_CHECKS=1 to run live Groq model-availability checks"


@unittest.skipUnless(os.environ.get("RUN_LIVE_GROQ_CHECKS"), _SKIP_REASON)
class TestConfiguredModelsAreLive(unittest.TestCase):
    def test_every_configured_model_is_available(self):
        from groq import Groq

        client = Groq(api_key=config.GROQ_API_KEY)

        configured = {
            config.ORCHESTRATOR_MODEL,
            config.CONCEPT_RESOLVER_MODEL,
            config.DIAGNOSTIC_MODEL,
            config.CONTEXT_MANAGER_MODEL,
            config.SOLVER_MODEL,
            config.SOCRATIC_MODEL,
            config.QUIZ_MODEL,
            *config.SOLVER_FALLBACK_MODELS,
            *config.QUIZ_FALLBACK_MODELS,
            *config.SOCRATIC_FALLBACK_MODELS,
            *config.SMALL_MODEL_FALLBACK,
        }

        dead = []
        for model in sorted(configured):
            try:
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                )
            except Exception as exc:  # noqa: BLE001 — we want to catch and report, not raise
                dead.append(f"{model}: {exc}")

        self.assertEqual(dead, [], "Dead/unavailable models in config.py:\n" + "\n".join(dead))


if __name__ == "__main__":
    unittest.main()
