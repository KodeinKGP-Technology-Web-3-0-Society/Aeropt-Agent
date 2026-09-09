"""
Round-robin Groq API keys with per-key cooldown after a 429.

Groq rate limits are per organization, not per key. Extra keys on the same
account share one RPM/RPD budget and will 429 together. Keys from different
Groq orgs are independent and this pool will fail over between them.

Also tracks a local per-(key, model) sliding window so we skip a slot that
is already near the free-tier 30 RPM cap instead of waiting for Groq to 429.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from groq import Groq

logger = logging.getLogger("jee_tutor.llm")

COOLDOWN_SEC = 65.0
RPM_WINDOW_SEC = 60.0
# Stay under Groq free-tier 30 RPM so we rotate before the hard 429.
RPM_SOFT_CAP = 26


def load_groq_api_keys(env: Optional[Dict[str, str]] = None) -> List[str]:
    """Read GROQ_API_KEYS or GROQ_API_KEY / GROQ_API_KEY_2..7."""
    import os

    src = env if env is not None else os.environ
    seen = set()
    keys: List[str] = []

    def add(raw: str) -> None:
        key = (raw or "").strip()
        if key and key not in seen:
            seen.add(key)
            keys.append(key)

    bundled = src.get("GROQ_API_KEYS", "") or ""
    if bundled.strip():
        for part in bundled.replace(";", ",").split(","):
            add(part)
        return keys

    add(src.get("GROQ_API_KEY", "") or "")
    for i in range(2, 8):
        add(src.get(f"GROQ_API_KEY_{i}", "") or "")
    return keys


class GroqKeyPool:
    def __init__(self, keys: List[str], client_factory=Groq):
        if not keys:
            raise ValueError("At least one Groq API key is required.")
        self._keys = list(keys)
        self._clients = [client_factory(api_key=k) for k in keys]
        self._lock = threading.Lock()
        self._rr = 0
        self._cooldown_until: List[float] = [0.0] * len(keys)
        self._hits: Dict[Tuple[int, str], Deque[float]] = {}
        if len(keys) > 1:
            logger.info(
                "[LLM] Groq key pool size=%d (limits are per org; same-account keys share RPM)",
                len(keys),
            )

    @property
    def size(self) -> int:
        return len(self._clients)

    def mark_limited(self, key_idx: int) -> None:
        until = time.monotonic() + COOLDOWN_SEC
        with self._lock:
            self._cooldown_until[key_idx] = max(self._cooldown_until[key_idx], until)
            hot = sum(1 for t in self._cooldown_until if t > time.monotonic())
        logger.warning(
            "[LLM] Groq key #%d rate-limited; cooling for %.0fs (%d/%d keys in cooldown)",
            key_idx + 1,
            COOLDOWN_SEC,
            hot,
            self.size,
        )
        if self.size > 1 and hot >= self.size:
            logger.warning(
                "[LLM] Every Groq key is in cooldown. If they belong to one org "
                "they share one quota — extra keys will not help."
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
        override: Optional[Groq],
        model: str,
    ) -> Iterable[Tuple[int, Groq]]:
        """
        Yield (key_index, client) in rotation, skipping cooled-down / RPM-hot slots.

        A single-key pool keeps using the caller-supplied client so tests and
        existing agent modules stay unchanged.
        """
        if self.size <= 1:
            client = override if override is not None else self._clients[0]
            yield 0, client
            return

        now = time.monotonic()
        ordered: List[int] = []
        fallback: List[int] = []
        with self._lock:
            start = self._rr % self.size
            self._rr = start + 1
            order = list(range(start, self.size)) + list(range(start))
            for idx in order:
                if self._cooldown_until[idx] > now:
                    fallback.append(idx)
                    continue
                hits = self._hits.get((idx, model), deque())
                cutoff = now - RPM_WINDOW_SEC
                live = sum(1 for t in hits if t >= cutoff)
                if live >= RPM_SOFT_CAP:
                    fallback.append(idx)
                    continue
                ordered.append(idx)

        chosen = ordered or fallback
        for idx in chosen:
            yield idx, self._clients[idx]


_pool: Optional[GroqKeyPool] = None


def get_pool() -> GroqKeyPool:
    global _pool
    if _pool is None:
        import config

        _pool = GroqKeyPool(config.GROQ_API_KEYS)
    return _pool


def reset_pool_for_tests(pool: Optional[GroqKeyPool] = None) -> None:
    global _pool
    _pool = pool
