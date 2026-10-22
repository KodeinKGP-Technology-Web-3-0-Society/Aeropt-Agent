"""Bounded concurrency for tutor turns.

A tutor turn is a long synchronous graph run. Left unbounded, a burst of students
queues threads without limit and every turn gets slower together, until the proxy
times them all out. The gate caps how many turns run at once, lets a few wait a
short while, and turns the rest away with a 503 and a Retry-After so the browser
can say "busy, try again" instead of hanging.

State is per process. The agent runs as one worker (see the Dockerfile), so this
is the whole picture; a second instance would have its own gate.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

MAX_ACTIVE = int(os.getenv("TUTOR_MAX_CONCURRENT_TURNS", "12"))
MAX_WAITING = int(os.getenv("TUTOR_MAX_QUEUED_TURNS", "12"))
QUEUE_WAIT_S = float(os.getenv("TUTOR_QUEUE_WAIT_S", "15"))


class TurnGate:
    """Admit up to `max_active` turns, hold up to `max_waiting` for `wait_s`."""

    def __init__(self, max_active: int = MAX_ACTIVE, max_waiting: int = MAX_WAITING, wait_s: float = QUEUE_WAIT_S):
        self.max_active = max(1, max_active)
        self.max_waiting = max(0, max_waiting)
        self.wait_s = wait_s
        self.active = 0
        self.waiting = 0
        self.rejected = 0
        self._sem: Optional[asyncio.Semaphore] = None
        self._pool = ThreadPoolExecutor(max_workers=self.max_active, thread_name_prefix="tutor-turn")

    def _semaphore(self) -> asyncio.Semaphore:
        # Created lazily so it binds to the running loop, not the import-time one.
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_active)
        return self._sem

    async def acquire(self) -> bool:
        """True when a slot was granted; False when the server is too busy."""
        sem = self._semaphore()
        if sem.locked() and self.waiting >= self.max_waiting:
            self.rejected += 1
            return False
        self.waiting += 1
        try:
            await asyncio.wait_for(sem.acquire(), timeout=self.wait_s)
        except asyncio.TimeoutError:
            self.rejected += 1
            return False
        finally:
            self.waiting -= 1
        self.active += 1
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)
        self._semaphore().release()

    def run(self, fn: Callable[[], Any]) -> "asyncio.Future[Any]":
        """Run `fn` on the turn pool, keeping the caller's contextvars."""
        ctx = contextvars.copy_context()
        return asyncio.get_running_loop().run_in_executor(self._pool, ctx.run, fn)

    def stats(self) -> Dict[str, int]:
        return {
            "active": self.active,
            "waiting": self.waiting,
            "max_active": self.max_active,
            "rejected": self.rejected,
        }

    @property
    def retry_after_s(self) -> int:
        return max(5, int(self.wait_s))


gate = TurnGate()
