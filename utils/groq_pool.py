"""
Round-robin Gemini API keys with per-key cooldown after a 429.

Every LLM call in the agent goes through utils/llm.py, which asks this pool for
a client per attempt. Keys rotate round-robin; a key that 429s cools down for
COOLDOWN_SEC, and a key Google rejects outright (revoked, mistyped, project
disabled) is retired for the life of the process, so one bad key in the list
costs one failed request instead of failing every call that lands on it.

Also tracks a local per-(key, model) sliding window so we skip a key that is
already near the free-tier RPM cap instead of waiting for Google to 429.

Keys come from GEMINI_KEYS (comma or semicolon separated) or GEMINI_API_KEY.
They live in .env only — never as a default in source.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Iterable, List, Optional, Tuple

from openai import OpenAI

logger = logging.getLogger("jee_tutor.llm")

SARVAM_OPENAI_BASE_URL = "https://api.sarvam.ai/v1"
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

COOLDOWN_SEC = 65.0
# A cooldown longer than this means a spent daily quota, not a per-minute burst:
# such a key is skipped for that model entirely, so a fallback model is tried at
# once instead of after twelve doomed requests.
LONG_COOLDOWN_SEC = 600.0
RPM_WINDOW_SEC = 60.0
# Free-tier quotas: rotate a little before limit
RPM_SOFT_CAP = 8


def load_sarvam_api_keys(env: Optional[Dict[str, str]] = None) -> List[str]:
    """Read SARVAM_KEYS (comma/semicolon list) or SARVAM_API_KEY, de-duplicated."""
    src = env if env is not None else os.environ
    raw = (src.get("SARVAM_KEYS") or "").strip() or (src.get("SARVAM_API_KEY") or "").strip()
    keys: List[str] = []
    for part in raw.replace(";", ",").split(","):
        key = part.strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def load_gemini_api_keys(env: Optional[Dict[str, str]] = None) -> List[str]:
    """Read GEMINI_KEYS (comma/semicolon list) or GEMINI_API_KEY, de-duplicated."""
    src = env if env is not None else os.environ
    raw = (src.get("GEMINI_KEYS") or "").strip() or (src.get("GEMINI_API_KEY") or "").strip()
    keys: List[str] = []
    for part in raw.replace(";", ",").split(","):
        key = part.strip()
        if key and key not in keys:
            keys.append(key)
    return keys


import httpx


def _default_client(api_key: str) -> OpenAI:
    # Use Gemini endpoint by default; allow Sarvam if explicitly configured or key matches
    base_url = GEMINI_OPENAI_BASE_URL
    if os.getenv("LLM_PROVIDER", "").strip().lower() == "sarvam" or (api_key.startswith("sk_") and not api_key.startswith("AIza")):
        base_url = os.getenv("SARVAM_BASE_URL", SARVAM_OPENAI_BASE_URL)
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
        timeout=httpx.Timeout(30.0, connect=5.0),
    )


class GeminiKeyPool:
    def __init__(self, keys: List[str], client_factory: Callable[[str], OpenAI] = _default_client):
        keys = list(dict.fromkeys(k for k in keys if k))
        if not keys:
            raise ValueError("At least one API key is required (set GEMINI_KEYS or GEMINI_API_KEY in .env).")
        self._keys = keys
        self._clients = [client_factory(k) for k in keys]
        self._lock = threading.Lock()
        self._rr = 0
        # Per (key, model): Gemini quotas are per model, so a key whose daily
        # gemini-3-flash quota is spent can still serve a fallback model.
        self._cooldown_until: Dict[Tuple[int, str], float] = {}
        self._dead: List[bool] = [False] * len(keys)
        self._hits: Dict[Tuple[int, str], Deque[float]] = {}
        logger.info("[LLM] Gemini key pool size=%d", len(keys))

    @property
    def size(self) -> int:
        return len(self._clients)

    @property
    def live_count(self) -> int:
        with self._lock:
            return sum(1 for d in self._dead if not d)

    def mark_limited(self, key_idx: int, model: str = "", seconds: Optional[float] = None) -> None:
        """Cool a key for one model. `seconds` comes from Google's retry hint —
        hours for a spent daily quota, about a minute for the per-minute one."""
        wait = max(COOLDOWN_SEC, float(seconds or 0))
        until = time.monotonic() + wait
        with self._lock:
            slot = (key_idx, model)
            self._cooldown_until[slot] = max(self._cooldown_until.get(slot, 0.0), until)
            now = time.monotonic()
            hot = sum(1 for (i, m), t in self._cooldown_until.items() if m == model and t > now)
        logger.warning(
            "[LLM] Gemini key #%d (…%s) rate-limited on %s; cooling for %.0fs (%d/%d keys cooling for this model)",
            key_idx + 1, self._keys[key_idx][-4:], model or "all models", wait, hot, self.size,
        )

    def exhausted(self, model: str) -> bool:
        """Every live key is in a LONG cooldown for this model (a spent daily quota)."""
        now = time.monotonic()
        with self._lock:
            live = [i for i in range(self.size) if not self._dead[i]]
            return bool(live) and all(
                self._cooldown_until.get((i, model), 0.0) - now > LONG_COOLDOWN_SEC for i in live
            )

    def mark_dead(self, key_idx: int, reason: str = "") -> None:
        """Retire a key Google rejected (invalid, revoked, disabled project)."""
        with self._lock:
            if self._dead[key_idx]:
                return
            self._dead[key_idx] = True
            live = sum(1 for d in self._dead if not d)
        logger.error(
            "[LLM] Gemini key #%d (…%s) rejected and retired: %s (%d/%d keys still live)",
            key_idx + 1, self._keys[key_idx][-4:], reason[:120], live, self.size,
        )

    def note_attempt(self, key_idx: int, model: str) -> None:
        now = time.monotonic()
        slot = (key_idx, model)
        with self._lock:
            hits = self._hits.setdefault(slot, deque())
            hits.append(now)
            cutoff = now - RPM_WINDOW_SEC
            while hits and hits[0] < cutoff:
                hits.popleft()

    def iter_clients(
        self,
        override: Optional[OpenAI],
        model: str,
    ) -> Iterable[Tuple[int, OpenAI]]:
        """Live keys in round-robin order: cool ones first, then the hot ones."""
        now = time.monotonic()
        ordered: List[int] = []
        fallback: List[int] = []
        with self._lock:
            start = self._rr % self.size
            self._rr = start + 1
            order = list(range(start, self.size)) + list(range(start))
            for idx in order:
                if self._dead[idx]:
                    continue
                until = self._cooldown_until.get((idx, model), 0.0)
                if until - now > LONG_COOLDOWN_SEC:
                    continue  # quota spent for hours: not even a last resort
                if until > now:
                    fallback.append(idx)
                    continue
                hits = self._hits.get((idx, model), deque())
                cutoff = now - RPM_WINDOW_SEC
                live = sum(1 for t in hits if t >= cutoff)
                if live >= RPM_SOFT_CAP:
                    fallback.append(idx)
                    continue
                ordered.append(idx)

        for idx in ordered or fallback:
            yield idx, self._clients[idx]


SarvamKeyPool = GeminiKeyPool

_pool: Optional[GeminiKeyPool] = None
_pool_lock = threading.Lock()


def get_pool() -> GeminiKeyPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                import config

                keys = getattr(config, "GEMINI_API_KEYS", None) or getattr(config, "LLM_API_KEYS", None)
                _pool = GeminiKeyPool(keys)
    return _pool


def reset_pool_for_tests(pool: Optional[GeminiKeyPool] = None) -> None:
    global _pool
    _pool = pool
