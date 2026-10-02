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
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Generator

from openai import (
    OpenAI,
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
MIN_CLAMPED_MAX_TOKENS = 512   # never clamp a request down below this


def _is_capacity_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "over capacity" in msg
        or "capacity" in msg
        or "503" in msg
        or "service unavailable" in msg
        or "temporarily unavailable" in msg
    )


def _is_model_unavailable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "decommissioned" in msg
        or "model_not_found" in msg
        or "does not exist" in msg
        or "do not have access" in msg
    )


_TPM_LIMIT_RE = re.compile(r"tokens per minute.{0,40}?limit\s+(\d+)", re.IGNORECASE)


def _tpm_limit_from_error(exc: Exception) -> Optional[int]:
    """
    Parse a Groq 413 "Request too large ... on tokens per minute (TPM):
    Limit 8000[, Requested 8505]" error into the numeric TPM limit, or None if
    this isn't that error at all (a different 413, or no limit mentioned).
    """
    msg = str(exc)
    if "413" not in msg or "tokens per minute" not in msg.lower():
        return None
    m = _TPM_LIMIT_RE.search(msg)
    return int(m.group(1)) if m else None


_DEAD_KEY_MARKERS = (
    "api key not valid",
    "valid api key",
    "api_key_invalid",
    "api key expired",
    "permission_denied",
    "has been suspended",
    "service_disabled",
    "billing",
)


def _key_failure(exc: Exception) -> Optional[str]:
    """'limited' / 'dead' when the failure belongs to the key, not the model.

    A 429 (RESOURCE_EXHAUSTED) means this key's quota is spent — another key can
    serve the same request right now. A 400/401/403 naming the key means Google
    will never accept it. Either way the request itself is fine, so it should
    move to the next key rather than burn a model retry or fail the turn.
    """
    if isinstance(exc, RateLimitError):
        return "limited"
    msg = str(exc).lower()
    if "resource_exhausted" in msg or "429" in msg[:40]:
        return "limited"
    status = getattr(exc, "status_code", None)
    if status in (400, 401, 403) and any(m in msg for m in _DEAD_KEY_MARKERS):
        return "dead"
    return None


_RETRY_HINT_RE = re.compile(r"retry in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:([\d.]+)s)?", re.IGNORECASE)
_RETRY_DELAY_RE = re.compile(r"retryDelay'?\"?\s*:\s*'?\"?(\d+)s")


def _retry_seconds(exc: Exception) -> Optional[float]:
    """Google's own wait: "Please retry in 11h10m14s" or retryDelay: '40214s'."""
    msg = str(exc)
    m = _RETRY_DELAY_RE.search(msg)
    if m:
        return float(m.group(1))
    m = _RETRY_HINT_RE.search(msg)
    if m and any(m.groups()):
        h, mi, se = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mi * 60 + se
    return None


def is_quota_exhausted(exc: Exception) -> bool:
    """A spent daily quota (not a per-minute burst)."""
    secs = _retry_seconds(exc)
    return "quota" in str(exc).lower() and (secs or 0) > 600


def _note_key_failure(pool, key_idx: Optional[int], kind: str, exc: Exception, model: str = "") -> None:
    if pool is None or key_idx is None:
        return
    if kind == "dead":
        pool.mark_dead(key_idx, str(exc))
    else:
        pool.mark_limited(key_idx, model, _retry_seconds(exc))


def _pick_client(pool, override_client: OpenAI, model: str) -> Tuple[Optional[int], OpenAI]:
    """Return (key_index_or_None, client) for one attempt against `model`."""
    if pool is None:
        return None, override_client
    for idx, client in pool.iter_clients(override=override_client, model=model):
        return idx, client
    return None, override_client


def _run_chain(
    client: OpenAI,
    model: str,
    messages: List[Dict[str, Any]],
    fallback_models: Optional[List[str]] = None,
    soft_check: Optional[Any] = None,
    **kwargs: Any,
) -> Tuple[Any, str]:
    """
    Call Groq with retry + automatic model fallback; return the raw message.

    `soft_check(message) -> Optional[str]` lets the caller reject an otherwise
    successful response (e.g. a think-only reply with no usable text). Returning
    a reason string treats the attempt as a transient failure: retry, then fall
    back to the next model. Returning None accepts the message.

    Returns:
        (message, model_that_succeeded)

    Key rotation: if utils/groq_pool has a multi-key pool configured
    (GROQ_API_KEYS / GROQ_API_KEY_2..7), each attempt is made against a client
    picked from that pool (round-robin, skipping cooled-down/RPM-hot keys) —
    `client` is only used directly when no multi-key pool is configured, so
    every existing call site keeps working unchanged. A 429 marks that key
    cooled-down and moves to the next one immediately, without the model
    fallback chain being touched — same-account keys share one quota, but
    different-org keys don't, so this is the actual fix for a 429 rather than
    just retrying the same (still-limited) key or downgrading model quality.
    """
    chain = [model] + (fallback_models or [])
    last_exc: Exception = RuntimeError("No attempts made")

    try:
        from utils.groq_pool import get_pool

        pool = get_pool()
    except Exception:
        pool = None

    for model_idx, current_model in enumerate(chain):
        is_fallback = model_idx > 0
        if is_fallback:
            logger.warning(
                "[LLM] Switching to fallback model: %s (after %s failed)",
                current_model,
                chain[model_idx - 1],
            )

        call_kwargs = dict(kwargs)
        attempt = -1
        key_switches = 0
        if pool is not None and pool.exhausted(current_model):
            logger.warning("[LLM] %s: daily quota spent on every key — skipping to fallback.", current_model)
            last_exc = RuntimeError(f"Daily quota exhausted for {current_model} on every key")
            continue

        while attempt < MAX_RETRIES:
            attempt += 1
            key_idx, active_client = _pick_client(pool, client, current_model)
            if pool is not None and key_idx is not None:
                pool.note_attempt(key_idx, current_model)

            try:
                completion = active_client.chat.completions.create(
                    model=current_model,
                    messages=messages,
                    **call_kwargs,
                )
                message = completion.choices[0].message
                reason = soft_check(message) if soft_check else None
                if reason:
                    # Model returned a technically-successful but unusable reply
                    # (e.g. think-only text). Treat as a soft failure and continue
                    # to the next attempt / model.
                    logger.warning("[LLM] %s: %s", current_model, reason)
                    last_exc = RuntimeError(reason)
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
                return message, current_model

            except Exception as exc:
                last_exc = exc

                key_kind = _key_failure(exc)
                if key_kind and pool is not None and key_idx is not None:
                    _note_key_failure(pool, key_idx, key_kind, exc, current_model)
                    key_switches += 1
                    if pool.live_count == 0:
                        break  # nothing left to rotate to
                    if key_switches < pool.size:
                        # Same request, different key: not a model failure, so it
                        # neither waits nor spends one of this model's retries.
                        attempt -= 1
                        continue
                    # Every key is hot. Fall through to the backoff below so the
                    # earliest cooldown has a moment to clear.
                    key_switches = 0

                if _is_model_unavailable(exc):
                    logger.warning("[LLM] %s unavailable, skipping.", current_model)
                    break  # skip retries, go to next model

                tpm_limit = _tpm_limit_from_error(exc)
                if tpm_limit is not None:
                    # Same-org keys share one TPM budget — rotating keys can't help
                    # a request that's simply too large. Try shrinking max_tokens to
                    # fit under the limit on the SAME model/key first; only fall
                    # through to the next model if we're already at/under a size
                    # that still doesn't fit (nothing left to shrink).
                    requested = call_kwargs.get("max_tokens")
                    candidate = max(MIN_CLAMPED_MAX_TOKENS, tpm_limit // 2)
                    if requested is not None and candidate < requested:
                        logger.warning(
                            "[LLM] %s hit TPM limit (%d); clamping max_tokens %d -> %d and retrying.",
                            current_model, tpm_limit, requested, candidate,
                        )
                        call_kwargs["max_tokens"] = candidate
                        continue
                    logger.warning(
                        "[LLM] %s TPM limit (%d) still exceeded after clamping; moving to next model.",
                        current_model, tpm_limit,
                    )
                    break  # move to next model in chain

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


def groq_complete(
    client: OpenAI,
    model: str,
    messages: List[Dict[str, Any]],
    fallback_models: Optional[List[str]] = None,
    min_chars: int = 0,
    **kwargs: Any,
) -> Tuple[str, str]:
    """
    Call Groq for plain text with retry + automatic model fallback.

    Returns:
        (response_text, model_that_succeeded)

    `min_chars` rejects a suspiciously short reply (a think-only response) as a
    soft failure, retrying and then falling back rather than returning nothing.

    Key rotation: if utils/groq_pool has a multi-key pool configured
    (GROQ_API_KEYS / GROQ_API_KEY_2..7), each attempt is made against a client
    picked from that pool (round-robin, skipping cooled-down/RPM-hot keys) —
    `client` is only used directly when no multi-key pool is configured, so
    every existing call site keeps working unchanged. A 429 marks that key
    cooled-down and moves to the next one immediately, without the model
    fallback chain being touched — same-account keys share one quota, but
    different-org keys don't, so this is the actual fix for a 429 rather than
    just retrying the same (still-limited) key or downgrading model quality.
    """

    def _check(message: Any) -> Optional[str]:
        if not min_chars:
            return None
        text = (getattr(message, "content", None) or "").strip()
        if len(text) < min_chars:
            return f"returned {len(text)} chars (min={min_chars}), treating as empty."
        return None

    message, model_used = _run_chain(
        client=client,
        model=model,
        messages=messages,
        fallback_models=fallback_models,
        soft_check=_check,
        **kwargs,
    )
    return (getattr(message, "content", None) or ""), model_used


def groq_chat(
    client: OpenAI,
    model: str,
    messages: List[Dict[str, Any]],
    fallback_models: Optional[List[str]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[str] = None,
    **kwargs: Any,
) -> Tuple[Any, str]:
    """
    Tool-calling counterpart of `groq_complete`: returns the raw assistant
    message so the caller can read `.tool_calls` as well as `.content`.

    Used by the autonomous agent loop (`agents/runtime/loop.py`), which needs to
    see tool calls, not just text. Shares the same retry, key-rotation, TPM
    clamping and model-fallback behaviour as `groq_complete`.

    A reply carrying tool calls but no text is valid here (that is the normal
    shape of a tool-calling turn), so the only soft failure is a reply with
    neither text nor tool calls — nothing the loop could act on.

    Returns:
        (message, model_that_succeeded)
    """

    def _check(message: Any) -> Optional[str]:
        has_text = bool((getattr(message, "content", None) or "").strip())
        has_calls = bool(getattr(message, "tool_calls", None))
        if not has_text and not has_calls:
            return "returned neither text nor tool calls."
        return None

    call_kwargs: Dict[str, Any] = dict(kwargs)
    if tools:
        call_kwargs["tools"] = tools
        call_kwargs["tool_choice"] = tool_choice or "auto"

    return _run_chain(
        client=client,
        model=model,
        messages=messages,
        fallback_models=fallback_models,
        soft_check=_check,
        **call_kwargs,
    )


def groq_stream(
    client: OpenAI,
    model: str,
    messages: List[Dict[str, Any]],
    fallback_models: Optional[List[str]] = None,
    **kwargs: Any,
) -> Generator[Tuple[str, bool], None, str]:
    """
    Call Groq for a stream with retry + automatic model fallback.
    Returns a generator yielding (delta_text, is_final).
    Returns the model_used when the generator finishes.
    """
    chain = [model] + (fallback_models or [])
    last_exc: Exception = RuntimeError("No attempts made")

    try:
        from utils.groq_pool import get_pool
        pool = get_pool()
    except Exception:
        pool = None

    for model_idx, current_model in enumerate(chain):
        is_fallback = model_idx > 0
        if is_fallback:
            logger.warning(
                "[LLM] Switching to fallback model: %s (after %s failed)",
                current_model,
                chain[model_idx - 1],
            )

        call_kwargs = dict(kwargs)
        attempt = -1
        key_switches = 0
        if pool is not None and pool.exhausted(current_model):
            logger.warning("[LLM] %s: daily quota spent on every key — skipping to fallback.", current_model)
            last_exc = RuntimeError(f"Daily quota exhausted for {current_model} on every key")
            continue

        while attempt < MAX_RETRIES:
            attempt += 1
            key_idx, active_client = _pick_client(pool, client, current_model)
            if pool is not None and key_idx is not None:
                pool.note_attempt(key_idx, current_model)

            try:
                completion = active_client.chat.completions.create(
                    model=current_model,
                    messages=messages,
                    stream=True,
                    **call_kwargs,
                )
                
                # If we get here, the connection succeeded. We now yield chunks.
                # If a chunk fails, we catch it inside the generator and trigger retry.
                for chunk in completion:
                    delta = chunk.choices[0].delta.content if chunk.choices and chunk.choices[0].delta else ""
                    if delta:
                        yield (delta, False)
                
                # After the loop, if we didn't crash, we successfully finished!
                yield ("", True)
                if is_fallback or attempt > 0:
                    logger.info(
                        "[LLM] Stream Success — model=%s attempt=%d/%d",
                        current_model,
                        attempt + 1,
                        MAX_RETRIES + 1,
                    )
                return current_model

            except Exception as exc:
                last_exc = exc

                key_kind = _key_failure(exc)
                if key_kind and pool is not None and key_idx is not None:
                    _note_key_failure(pool, key_idx, key_kind, exc, current_model)
                    key_switches += 1
                    if pool.live_count == 0:
                        break  # nothing left to rotate to
                    if key_switches < pool.size:
                        # Same request, different key: not a model failure, so it
                        # neither waits nor spends one of this model's retries.
                        attempt -= 1
                        continue
                    # Every key is hot. Fall through to the backoff below so the
                    # earliest cooldown has a moment to clear.
                    key_switches = 0

                if _is_model_unavailable(exc):
                    logger.warning("[LLM] %s unavailable, skipping.", current_model)
                    break  # skip retries, go to next model

                tpm_limit = _tpm_limit_from_error(exc)
                if tpm_limit is not None:
                    requested = call_kwargs.get("max_tokens")
                    candidate = max(MIN_CLAMPED_MAX_TOKENS, tpm_limit // 2)
                    if requested is not None and candidate < requested:
                        logger.warning(
                            "[LLM] %s hit TPM limit (%d); clamping max_tokens %d -> %d and retrying.",
                            current_model, tpm_limit, requested, candidate,
                        )
                        call_kwargs["max_tokens"] = candidate
                        continue
                    logger.warning(
                        "[LLM] %s TPM limit (%d) still exceeded after clamping; moving to next model.",
                        current_model, tpm_limit,
                    )
                    break  # move to next model in chain

                is_transient = isinstance(exc, _TRANSIENT) or _is_capacity_error(exc)
                if not is_transient:
                    raise

                if attempt < MAX_RETRIES:
                    wait = BACKOFF_BASE ** (attempt + 1)
                    logger.warning(
                        "[LLM] %s transient error in stream (attempt %d/%d): %s — retrying in %.1fs",
                        current_model,
                        attempt + 1,
                        MAX_RETRIES + 1,
                        str(exc)[:120],
                        wait,
                    )
                    time.sleep(wait)
                else:
                    logger.warning(
                        "[LLM] %s exhausted %d retries for stream: %s",
                        current_model,
                        MAX_RETRIES + 1,
                        str(exc)[:120],
                    )
                    break  # move to next model in chain

    raise RuntimeError(
        f"All LLM models in chain failed during streaming. Last error: {last_exc}\n"
        f"Chain tried: {chain}"
    )
