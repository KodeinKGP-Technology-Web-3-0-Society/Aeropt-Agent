"""Gemini tool-call signatures survive live history, failures, and replay."""

from copy import deepcopy
import concurrent.futures
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openai.types.chat import ChatCompletionMessage
from pydantic import BaseModel
import pytest

from agents.runtime.loop import _compact_past_tool_messages, _serialise_assistant, run_agent_loop
from agents.runtime.toolbelt import SCOPE_READ, ToolContext, ToolSpec, Toolbelt
from evals.cassettes import Cassette, compute_request_hash, deserialize_message, serialize_message


def _signed_payload(count=1):
    calls = []
    for i in range(count):
        call = {
            "id": f"call_{i}", "type": "function",
            "function": {"name": "echo", "arguments": '{"value":"x"}', "provider_field": "kept"},
            "provider_trace": {"ordinal": i},
        }
        if i == 0:
            call["extra_content"] = {"google": {"thought_signature": "opaque-test-signature/=="}}
        calls.append(call)
    return {
        "role": "assistant", "content": None, "tool_calls": calls,
        "extra_content": {"provider_state": {"opaque": ["unchanged", 1, None]}},
    }


@pytest.mark.parametrize("representation", ["sdk", "namespace", "dict"])
def test_complete_tool_calls_survive_serialization(representation):
    raw = _signed_payload(2)
    if representation == "sdk":
        message = ChatCompletionMessage.model_validate(raw)
    elif representation == "namespace":
        message = SimpleNamespace(**{**raw, "tool_calls": [
            SimpleNamespace(**{**call, "function": SimpleNamespace(**call["function"])})
            for call in raw["tool_calls"]
        ]})
    else:
        message = raw
    result = _serialise_assistant(message)
    assert result["tool_calls"] == raw["tool_calls"]
    assert result["extra_content"] == raw["extra_content"]
    assert result["content"] == ""
    json.dumps(result)  # Request history must remain JSON-compatible.


def test_unsigned_messages_keep_existing_request_and_cassette_shapes():
    message = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
        id="call_0", function=SimpleNamespace(name="echo", arguments="{}"),
    )])
    expected = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_0", "type": "function", "function": {"name": "echo", "arguments": "{}"}},
    ]}
    assert _serialise_assistant(message) == expected
    assert serialize_message(message, "test") == {**expected, "model": "test"}
    plain = ChatCompletionMessage(role="assistant", content="Done")
    assert _serialise_assistant(plain) == {"role": "assistant", "content": "Done"}
    assert serialize_message(plain, "test") == {
        "role": "assistant", "content": "Done", "tool_calls": None, "model": "test",
    }


def test_signature_survives_cassette_disk_round_trip(tmp_path):
    original = ChatCompletionMessage.model_validate(_signed_payload(2))
    path = tmp_path / "signed.json"
    cassette = Cassette("signed", mode="record", filepath=path)
    cassette.set("response", serialize_message(original, "test"))
    cassette.save()
    restored = deserialize_message(Cassette("signed", filepath=path).get("response"))
    before = _serialise_assistant(original)
    after = _serialise_assistant(restored)
    assert after == before
    assert compute_request_hash("test", [after]) == compute_request_hash("test", [before])


class _Args(BaseModel):
    value: str


@pytest.mark.parametrize("count", [1, 2], ids=["sequential", "parallel"])
@pytest.mark.parametrize("outcome", ["success", "failure", "timeout"])
def test_next_model_request_retains_signature_after_tool_outcome(count, outcome):
    signed = ChatCompletionMessage.model_validate(_signed_payload(count))
    requests = []

    def handler(args, ctx):
        if outcome == "failure":
            raise RuntimeError("test tool failed")
        return {"echo": args.value}

    def chat(**kwargs):
        requests.append(deepcopy(kwargs["messages"]))
        message = signed if len(requests) == 1 else ChatCompletionMessage(role="assistant", content="Recovered")
        return message, "test"

    belt = Toolbelt([ToolSpec("echo", "Echo", _Args, handler, frozenset({SCOPE_READ}))],
                    ToolContext(user_id="test", session_id="test"))
    with patch("agents.runtime.loop.chat_completion", side_effect=chat):
        if outcome == "timeout":
            future = Mock()
            future.result.side_effect = concurrent.futures.TimeoutError()
            with patch("agents.runtime.toolbelt._TOOL_POOL.submit", return_value=future):
                result = run_agent_loop(system_prompt="Test", user_message="Test", toolbelt=belt,
                                        model="test", max_steps=2, client=object())
        else:
            result = run_agent_loop(system_prompt="Test", user_message="Test", toolbelt=belt,
                                    model="test", max_steps=2, client=object())
    assert result.ok
    assert len(requests) == 2
    assistant = next(message for message in requests[1] if message["role"] == "assistant")
    assert assistant["tool_calls"] == _signed_payload(count)["tool_calls"]
    observations = [json.loads(message["content"]) for message in requests[1] if message["role"] == "tool"]
    assert len(observations) == count
    assert all(item["ok"] == (outcome == "success") for item in observations)
    if outcome == "timeout":
        assert all("timed out" in item["error"] for item in observations)


def test_compaction_retains_prior_signed_assistant_calls():
    signed = _serialise_assistant(ChatCompletionMessage.model_validate(_signed_payload()))
    messages = [
        {"role": "user", "content": "Test"}, signed,
        {"role": "tool", "tool_call_id": "call_0", "content": json.dumps({
            "ok": True, "data": {"chunks": [{"content_id": "chunk-1", "text": "x" * 1000}]},
        })},
        {"role": "assistant", "content": "Continue"},
    ]
    compacted = _compact_past_tool_messages(messages)
    assert json.loads(compacted[2]["content"])["data"]["compacted"]
    assert compacted[1] == signed
    assert messages[1] == signed
