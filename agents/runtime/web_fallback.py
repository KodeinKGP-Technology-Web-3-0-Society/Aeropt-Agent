"""Background official-web search for one tutor turn.

The fetch runs beside the tool loop. Passages are attached only when they are
already finished before the next model call. A search that is still running
when the reply is ready is ignored — the loop does not wait on the network.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future
from typing import Any, Callable

from retrieval.official_web import format_for_tutor, host_allowed, pack_is_weak, search_official

logger = logging.getLogger("aeropt.official_web")

SearchFn = Callable[[str], list[dict[str, str]]]


def _spawn(fn: Callable[..., Any], *args: Any) -> Future:
    future: Future = Future()

    def run() -> None:
        try:
            result = fn(*args)
        except Exception as exc:  # noqa: BLE001 — a failed fetch must not kill the turn
            future.set_exception(exc)
        else:
            future.set_result(result)

    threading.Thread(target=run, name="official-web", daemon=True).start()
    return future


class WebFallback:
    """One allowlisted search, started at most once until a strong pack voids it."""

    def __init__(self, search: SearchFn | None = None) -> None:
        self._search = search or search_official
        self._lock = threading.Lock()
        self._search_future: Future | None = None
        self._probe_started = False
        self._void = False
        self.attached = False
        self.ignored_late = False
        self.passages: list[dict[str, str]] = []

    def schedule_probe(self, query: str, state: dict[str, Any]) -> None:
        """If a fast corpus probe is weak, start the fetch during the tutor's first call.

        Returns immediately. A strong probe does not open a web connection.
        """
        query = (query or "").strip()
        if not query:
            return
        try:
            from retrieval import store

            if not store.database_url():
                return
        except Exception:
            return
        with self._lock:
            if self._probe_started or self.ignored_late:
                return
            self._probe_started = True
        _spawn(self._probe, query, _snapshot(state))

    def _probe(self, query: str, snap: dict[str, Any]) -> None:
        pack = None
        try:
            from retrieval import service, store

            if not store.database_url() or not store.index_exists():
                return
            pack = service.retrieve(
                query,
                user_id=str(snap.get("user_id") or ""),
                content_type=str(snap.get("content_type") or ""),
                concept_id=str(snap.get("concept_id") or ""),
                target_exam=str(snap.get("target_exam") or ""),
                state=dict(snap.get("state") or {}),
                recent_turns=list(snap.get("recent_turns") or []),
                mark=False,
            )
        except Exception:
            logger.debug("official-web probe failed", exc_info=True)
            return
        if pack is not None and pack_is_weak(pack):
            self.note_weak(query)

    def note_weak(self, query: str, *, override: bool = False) -> None:
        """Start (or keep) the fetch. Does not wait.

        A strong pack voids a speculative probe. ``override`` is only for the
        retrieval tool, whose pack is the one the tutor will actually use.
        """
        query = (query or "").strip()
        if not query:
            return
        with self._lock:
            if self.ignored_late or self.attached:
                return
            if self._void and not override:
                return
            if self._search_future is not None and not self._void:
                return
            self._void = False
            self._search_future = _spawn(self._search, query)

    def note_strong(self) -> None:
        """A strong pack needs no web context. Drop an unattached fetch."""
        with self._lock:
            if self.attached:
                return
            self._void = True

    def take_for_model(self) -> list[dict[str, str]] | None:
        """Passages if the fetch has already finished. Never blocks.

        Returns None when the search is still running, was voided by a strong
        pack, or was ignored because the reply was already ready.
        """
        with self._lock:
            if self._void or self.ignored_late or self.attached:
                return None
            future = self._search_future
            if future is None or not future.done():
                return None
            self.attached = True
        try:
            raw = future.result() or []
        except Exception:
            logger.debug("official-web search failed", exc_info=True)
            raw = []
        passages = [
            p
            for p in raw
            if isinstance(p, dict) and p.get("text") and host_allowed(str(p.get("url") or ""))
        ]
        with self._lock:
            if self._void or self.ignored_late:
                self.attached = False
                self.passages = []
                return None
            self.passages = passages
        return passages

    def ignore_if_unattached(self) -> None:
        """The reply is ready. A fetch that has not been shown to the tutor is dropped."""
        with self._lock:
            if not self.attached:
                self.ignored_late = True


def _snapshot(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": state.get("user_id") or "",
        "content_type": state.get("content_type_requested") or "",
        "concept_id": state.get("primary_concept_id") or "",
        "target_exam": state.get("target_exam") or "",
        "recent_turns": list(state.get("recent_turns") or []),
        "state": {
            "primary_concept_id": state.get("primary_concept_id") or "",
            "recent_turns": list(state.get("recent_turns") or []),
            "seen_follow_up_ids": list(state.get("seen_follow_up_ids") or []),
            "retrieved_chunks": list(state.get("retrieved_chunks") or []),
        },
    }


def attach_ready_passages(ctx: Any, messages: list[dict[str, Any]]) -> bool:
    """Append finished passages to the turn. No-op if the fetch is still running."""
    fallback = getattr(ctx, "web_fallback", None)
    if fallback is None:
        return False
    passages = fallback.take_for_model()
    if not passages:
        return False
    state = getattr(ctx, "state", None)
    if isinstance(state, dict):
        state["web_passages"] = passages
    messages.append({"role": "system", "content": format_for_tutor(passages)})
    logger.info("official-web fallback attached %d passage(s)", len(passages))
    return True
