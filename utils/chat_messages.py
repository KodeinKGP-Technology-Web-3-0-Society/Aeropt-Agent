"""Lossless tool-call serialization shared by the live loop and replay transport."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, Dict

from pydantic import BaseModel


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        # SDK models retain provider extensions as Pydantic extra fields. Picking
        # only id/type/function here loses Gemini's opaque thought signature.
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, SimpleNamespace):
        return {key: _json_value(item) for key, item in vars(value).items()
                if not key.startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def serialize_assistant_message(message: Any) -> Dict[str, Any]:
    """Keep complete tool calls, including provider metadata, in request history.

    Preserve the existing content/absent-tool envelope for unsigned responses so
    existing replay request hashes stay valid. Never interpret or create signatures.
    """
    raw = _json_value(message)
    payload = {
        key: value for key, value in raw.items()
        if key not in {"role", "content", "tool_calls", "model", "usage", "annotations"}
        and value is not None
    }
    payload["role"] = raw.get("role") or "assistant"
    payload["content"] = raw.get("content") or ""
    calls = raw.get("tool_calls")
    if calls:
        serialized_calls = []
        for i, call in enumerate(calls):
            call = dict(call)
            call.setdefault("id", f"call_{i}")
            call["type"] = call.get("type") or "function"
            function = dict(call.get("function") or {})
            function.setdefault("name", "")
            function.setdefault("arguments", "")
            call["function"] = function
            serialized_calls.append(call)
        payload["tool_calls"] = serialized_calls
    return payload


def assistant_message_from_dict(data: Dict[str, Any]) -> SimpleNamespace:
    """Restore attribute access for replay without discarding tool-call metadata."""
    payload = serialize_assistant_message(data)
    calls = payload.pop("tool_calls", None)
    restored_calls = None
    if calls:
        restored_calls = []
        for call in calls:
            fields = dict(call)
            fields["function"] = SimpleNamespace(**fields["function"])
            restored_calls.append(SimpleNamespace(**fields))
    return SimpleNamespace(**payload, tool_calls=restored_calls)
