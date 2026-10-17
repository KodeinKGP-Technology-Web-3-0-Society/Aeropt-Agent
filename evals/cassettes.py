"""Cassette transport hook for offline deterministic replay and live recording.

Keyed by sha256(model, messages, tools, temperature).
- In 'replay' mode, returns the recorded response without touching network or keys.
  If a request hash is missing, raises KeyError to fail fast.
- In 'record' mode, executes calls live and records (hash -> response) pairs to disk.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

logger = logging.getLogger("jee_tutor.evals.cassette")

CASSETTES_DIR = Path(__file__).resolve().parent / "cassettes"


def compute_request_hash(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: Optional[float] = None,
    response_format: Optional[Dict[str, Any]] = None,
) -> str:
    """Compute deterministic sha256 hash of the LLM request payload."""
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "temperature": round(float(temperature), 4) if temperature is not None else 0.0,
        "response_format": response_format,
    }
    dumped = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def serialize_message(message: Any, model: str = "") -> Dict[str, Any]:
    """Serialize an assistant message object or namespace into JSON-compatible dict."""
    content = getattr(message, "content", None) or ""
    tool_calls_raw = getattr(message, "tool_calls", None) or []
    tool_calls = []
    for i, c in enumerate(tool_calls_raw):
        fn = getattr(c, "function", None)
        fn_name = getattr(fn, "name", "") if fn else ""
        fn_args = getattr(fn, "arguments", "") if fn else ""
        call_id = getattr(c, "id", f"call_{i}")
        tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": fn_name,
                    "arguments": fn_args,
                },
            }
        )

    return {
        "role": getattr(message, "role", "assistant") or "assistant",
        "content": content,
        "tool_calls": tool_calls if tool_calls else None,
        "model": model,
    }


def deserialize_message(data: Dict[str, Any]) -> Any:
    """Reconstruct a message object compatible with loop.py and answer_checker.py."""
    content = data.get("content") or ""
    raw_calls = data.get("tool_calls")
    calls = None
    if raw_calls:
        calls = [
            SimpleNamespace(
                id=c.get("id", f"call_{i}"),
                type=c.get("type", "function"),
                function=SimpleNamespace(
                    name=c["function"]["name"],
                    arguments=c["function"]["arguments"],
                ),
            )
            for i, c in enumerate(raw_calls)
        ]
    return SimpleNamespace(
        role=data.get("role", "assistant"),
        content=content,
        tool_calls=calls,
    )


class Cassette:
    """Stores request_hash -> response mappings for a scenario."""

    def __init__(self, name: str, mode: str = "replay", filepath: Optional[Path] = None):
        self.name = name
        self.mode = mode  # 'replay' | 'record' | 'live'
        self.filepath = filepath or (CASSETTES_DIR / f"{name}.json")
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.dirty = False
        if self.filepath.exists():
            self.load()

    def load(self) -> None:
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                self.entries = json.load(f)
        except Exception as exc:
            logger.warning("Could not load cassette %s: %s", self.filepath, exc)
            self.entries = {}

    def save(self) -> None:
        if not self.dirty:
            return
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, indent=2, sort_keys=True, ensure_ascii=False)
        self.dirty = False

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.entries.get(key)

    def set(self, key: str, value: Dict[str, Any]) -> None:
        self.entries[key] = value
        self.dirty = True


_active_cassette_var: contextvars.ContextVar[Optional[Cassette]] = contextvars.ContextVar(
    "active_cassette", default=None
)


def get_active_cassette() -> Optional[Cassette]:
    return _active_cassette_var.get()


class CassetteContext:
    """Context manager to activate a cassette during a scenario run."""

    def __init__(self, cassette: Optional[Cassette]):
        self.cassette = cassette
        self._token = None

    def __enter__(self) -> Optional[Cassette]:
        self._token = _active_cassette_var.set(self.cassette)
        return self.cassette

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.cassette and self.cassette.dirty:
            self.cassette.save()
        if self._token:
            _active_cassette_var.reset(self._token)
