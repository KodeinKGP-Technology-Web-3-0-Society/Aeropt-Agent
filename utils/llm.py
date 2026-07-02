"""
Fault-tolerant Groq completion wrapper.

Behaviour:
  1. Try the primary model up to MAX_RETRIES times with exponential backoff
     for transient errors (503 over-capacity, timeout, connection drop).
  2. If retries are exhausted, cycle through fallback_models in order.
  3. Log every retry and every fallback switch so debugging is trivial.
  4. Raise only when ALL models in the chain have failed.

Usage:
    from utils.llm import groq_complete

    text, model_used = groq_complete(
        client=_groq,
        model=config.SOLVER_MODEL,
        messages=[{"role": "user", "content": prompt}],
        fallback_models=config.SOLVER_FALLBACK_MODELS,
        temperature=0.4,
        max_tokens=1500,
    )
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from groq import (
    Groq,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

logger = logging.getLogger("jee_tutor.llm")

# Errors that are worth retrying on the same model
_TRANSIENT = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)

MAX_RETRIES = 2          # attempts on the same model before moving to fallback
BACKOFF_BASE = 1.5       # seconds — wait = BACKOFF_BASE ** attempt  (1.5s, 2.25s)


def _is_capacity_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "over capacity" in msg
        or "capacity" in msg
        or "503" in msg
        or "service unavailable" in msg
        or "temporarily unavailable" in msg
    )


def _is_decommissioned(exc: Exception) -> bool:
    return "decommissioned" in str(exc).lower()


def groq_complete(
    client: Groq,
    model: str,
    messages: List[Dict[str, Any]],
    fallback_models: Optional[List[str]] = None,
    min_chars: int = 0,
    **kwargs: Any,
) -> Tuple[str, str]:
    """
    Call Groq with retry + automatic model fallback.

    Returns:
        (response_text, model_that_succeeded)
    """
    chain = [model] + (fallback_models or [])
    last_exc: Exception = RuntimeError("No attempts made")

    for model_idx, current_model in enumerate(chain):
        is_fallback = model_idx > 0
        if is_fallback:
            logger.warning(
                "[LLM] Switching to fallback model: %s (after %s failed)",
                current_model,
                chain[model_idx - 1],
            )

        for attempt in range(MAX_RETRIES + 1):
            try:
                completion = client.chat.completions.create(
                    model=current_model,
                    messages=messages,
                    **kwargs,
                )
                text = completion.choices[0].message.content or ""
                if min_chars and len(text.strip()) < min_chars:
                    # Model returned a suspiciously short response (e.g. think-only).
                    # Treat as a soft failure and continue to next attempt / model.
                    logger.warning(
                        "[LLM] %s returned %d chars (min=%d), treating as empty.",
                        current_model, len(text.strip()), min_chars,
                    )
                    last_exc = RuntimeError(f"Response too short: {len(text.strip())} chars")
                    if attempt < MAX_RETRIES:
                        time.sleep(BACKOFF_BASE)
                        continue
                    else:
                        break  # try next model

                if is_fallback or attempt > 0:
                    logger.info(
                        "[LLM] Success — model=%s attempt=%d/%d",
                        current_model,
                        attempt + 1,
                        MAX_RETRIES + 1,
                    )
                return text, current_model

            except Exception as exc:
                last_exc = exc

                if _is_decommissioned(exc):
                    logger.warning("[LLM] %s is decommissioned, skipping.", current_model)
                    break  # skip retries, go to next model

                is_transient = isinstance(exc, _TRANSIENT) or _is_capacity_error(exc)
                if not is_transient:
                    # Non-recoverable (bad request, auth, etc.) — re-raise immediately
                    raise

                if attempt < MAX_RETRIES:
                    wait = BACKOFF_BASE ** (attempt + 1)
                    logger.warning(
                        "[LLM] %s transient error (attempt %d/%d): %s — retrying in %.1fs",
                        current_model,
                        attempt + 1,
                        MAX_RETRIES + 1,
                        str(exc)[:120],
                        wait,
                    )
                    time.sleep(wait)
                else:
                    logger.warning(
                        "[LLM] %s exhausted %d retries: %s",
                        current_model,
                        MAX_RETRIES + 1,
                        str(exc)[:120],
                    )
                    break  # move to next model in chain

    raise RuntimeError(
        f"All LLM models in chain failed. Last error: {last_exc}\n"
        f"Chain tried: {chain}"
    )
