"""The reasoning loop: decide -> act -> observe -> repeat.

This is what replaces the hand-wired conditional edges. There is no route table
here and no intent enum. The model is given a goal, a toolbelt and a budget, and
it chooses what to do each step until it has an answer.

The loop's job is not to make decisions — it is to make the agent's decisions
*safe and bounded*:

  * a step budget, so a confused model cannot loop forever;
  * a wall-clock budget, so a slow tool cannot hang a student's turn;
  * a forced close, so running out of budget still produces a real answer
    instead of an empty reply;
  * a full trace, so an autonomous path is still auditable afterwards.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openai import OpenAI

import config
from agents.runtime.toolbelt import SCOPE_READ, Toolbelt
from agents.runtime.trace import AgentTrace, TraceStep
from utils.llm import groq_chat

logger = logging.getLogger("jee_tutor.loop")

#: Hard ceiling on reasoning steps per turn. Generous enough for
#: retrieve -> verify -> compose, tight enough to bound cost and latency.
DEFAULT_MAX_STEPS = 6

#: Wall-clock budget for one agent turn, in seconds. A student is waiting.
DEFAULT_TIME_BUDGET_S = 45.0

_FORCED_CLOSE_NUDGE = (
    "You have used your full tool budget for this turn. Do not request any more "
    "tools. Answer the student now with what you already have. If something "
    "remains unverified, say so plainly rather than guessing."
)

_GROUND_FIRST_NUDGE = (
    "Before you answer: this is a substantive course question and you have not "
    "called `{tool}`. Call it now and build your answer on what it returns. If it "
    "returns nothing useful, answer from your own knowledge."
)


@dataclass
class LoopResult:
    """Outcome of one agent loop."""

    text: str
    model_used: str = ""
    steps_taken: int = 0
    stop_reason: str = "answered"       # answered | max_steps | time_budget | llm_error
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls_made: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and self.error is None


def _serialise_assistant(message: Any) -> Dict[str, Any]:
    """Convert an SDK assistant message or namespace back into request-shaped JSON."""
    payload: Dict[str, Any] = {
        "role": getattr(message, "role", "assistant") or "assistant",
        "content": getattr(message, "content", None) or "",
    }
    calls = getattr(message, "tool_calls", None)
    if calls:
        tool_calls_payload = []
        for i, c in enumerate(calls):
            item = {
                "id": getattr(c, "id", f"call_{i}"),
                "type": getattr(c, "type", "function") or "function",
                "function": {
                    "name": getattr(c.function, "name", ""),
                    "arguments": getattr(c.function, "arguments", ""),
                },
            }
            extra_content = getattr(c, "extra_content", None)
            if extra_content:
                item["extra_content"] = extra_content
            tool_calls_payload.append(item)
        payload["tool_calls"] = tool_calls_payload
    return payload


from agents.runtime.budget import (
    BudgetExhausted,
    TurnBudget,
    current_budget_var,
)


def _compact_past_tool_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Transform earlier tool messages on later steps to keep context compact.

    After the model has processed a retrieval or large tool result, earlier steps
    compact those messages: keeping cited items plus IDs instead of raw chunks.
    """
    last_assistant_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            last_assistant_idx = i
            break

    if last_assistant_idx <= 0:
        return messages

    recent_texts = [
        str(m.get("content") or "")
        for m in messages[last_assistant_idx:]
        if m.get("role") == "assistant"
    ]
    combined_recent_text = " ".join(recent_texts)

    compacted: List[Dict[str, Any]] = []
    for i, msg in enumerate(messages):
        if i < last_assistant_idx and msg.get("role") == "tool":
            raw_content = msg.get("content") or ""
            try:
                parsed = json.loads(raw_content)
                if isinstance(parsed, dict) and parsed.get("ok"):
                    data = parsed.get("data")
                    if isinstance(data, dict) and "chunks" in data:
                        chunks = data.get("chunks") or []
                        cited = []
                        other_ids = []
                        for c in chunks:
                            cid = str(c.get("content_id") or c.get("id") or "")
                            if cid and cid in combined_recent_text:
                                cited.append(c)
                            elif cid:
                                other_ids.append(cid)
                        compact_data = {
                            "concept_id": data.get("concept_id"),
                            "compacted": True,
                            "cited_chunks": cited if cited else chunks[:1],
                            "available_content_ids": other_ids[:10],
                            "note": "Earlier retrieval pack compacted. Call open_material(id) for full text.",
                        }
                        msg = {**msg, "content": json.dumps({"ok": True, "data": compact_data}, default=str)}
                    elif len(raw_content) > 800:
                        compact_data = {
                            "compacted": True,
                            "summary": str(data)[:250] + "...",
                        }
                        msg = {**msg, "content": json.dumps({"ok": True, "data": compact_data}, default=str)}
            except Exception:
                pass
        compacted.append(msg)
    return compacted


def run_agent_loop(
    *,
    system_prompt: str,
    user_message: str,
    toolbelt: Toolbelt,
    model: str,
    fallback_models: Optional[List[str]] = None,
    priming_messages: Optional[List[Dict[str, Any]]] = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_tool_rounds: Optional[int] = None,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
    budget: Optional[TurnBudget] = None,
    temperature: float = 0.3,
    max_tokens: int = 1800,
    client: Optional[OpenAI] = None,
    trace: Optional[AgentTrace] = None,
    label: str = "agent",
    lane: str = "live",
    require_tool: Optional[str] = None,
) -> LoopResult:
    """Run one bounded reasoning loop and return the agent's final text.

    `priming_messages` are inserted between the system prompt and the student's
    message — recalled memory, session summary, retrieved evidence the caller
    already has. They are context, not instructions.

    `require_tool`: if the model tries to answer without having called this tool,
    it is sent back once to call it. The draft it wrote is discarded.
    """
    if label == "coach" and lane == "live":
        lane = "background"
    llm_client = client or OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    trace = trace or toolbelt.ctx.trace
    budget = budget or getattr(toolbelt.ctx, "budget", None)
    if budget is None:
        budget = TurnBudget.from_duration(time_budget_s)
    deadline = budget.deadline
    budget_token = current_budget_var.set(budget)

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if priming_messages:
        messages.extend(priming_messages)
    messages.append({"role": "user", "content": user_message})

    import hashlib
    import json
    import os
    from pathlib import Path

    schemas = toolbelt.schemas()
    toolset_version = hashlib.sha1(json.dumps(schemas, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    if trace:
        trace.toolset_version = toolset_version

    tool_names_called: List[str] = []
    model_used = model
    steps = 0
    max_step_input_chars = 0
    nudged = False

    from services import progress

    def _record_transcript_step():
        if os.getenv("AEROPT_TRACE_TRANSCRIPTS") == "1":
            try:
                sid = getattr(trace, "session_id", None) or "session"
                tidx = getattr(trace, "turn_index", None) or 0
                out_dir = Path("var/transcripts") / str(sid)
                out_dir.mkdir(parents=True, exist_ok=True)
                out_file = out_dir / f"{tidx}.jsonl"
                with open(out_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"step": steps, "messages": messages}, default=str) + "\n")
            except Exception:
                pass

    try:
        while steps < max_steps:
            step_chars = sum(len(str(m.get("content") or "")) for m in messages)
            if step_chars > max_step_input_chars:
                max_step_input_chars = step_chars
            if trace:
                trace.max_step_input_chars = max(trace.max_step_input_chars, max_step_input_chars)

            _record_transcript_step()

            if progress.is_cancelled():
                res = LoopResult(
                    text="",
                    model_used=model_used,
                    steps_taken=steps,
                    stop_reason="cancelled",
                    messages=messages,
                    tool_calls_made=tool_names_called,
                )
                if trace:
                    trace.stop_reason = res.stop_reason
                return res
            out_of_time = budget.is_exhausted() or time.time() >= deadline
            if out_of_time:
                is_cap = budget and budget.llm_calls >= budget.max_llm_calls
                stop_r = "call_cap" if is_cap else "time_budget"
                logger.warning("[%s] budget exhausted after %d steps (%s)", label, steps, stop_r)
                return _forced_close(
                    client=llm_client,
                    messages=messages,
                    model=model,
                    fallback_models=fallback_models,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    steps=steps,
                    model_used=model_used,
                    tool_names_called=tool_names_called,
                    trace=trace,
                    label=label,
                    depth=toolbelt.ctx.depth,
                    stop_reason=stop_r,
                    reserve_s=budget.close_reserve_s,
                    lane=lane,
                )

            tool_rounds_limit = max_tool_rounds if max_tool_rounds is not None else max_steps - 1
            final_pass = steps >= tool_rounds_limit

            step_started = time.time()
            messages_to_send = _compact_past_tool_messages(messages)
            progress.report("stage", "thinking", "start", step=steps)
            try:
                message, model_used = groq_chat(
                    client=llm_client,
                    model=model,
                    messages=messages_to_send,
                    fallback_models=fallback_models,
                    # On the closing pass, take tools away entirely so the model has
                    # no option but to produce the student's answer.
                    tools=None if final_pass else schemas,
                    tool_choice=None if final_pass else "auto",
                    temperature=temperature,
                    max_tokens=max_tokens,
                    budget=budget,
                    reserve_s=budget.close_reserve_s,
                    lane=lane,
                )
            except BudgetExhausted as exc:
                is_cap = "call cap" in str(exc).lower() or (budget and budget.llm_calls >= budget.max_llm_calls)
                stop_r = "call_cap" if is_cap else "time_budget"
                logger.warning("[%s] budget exhausted during step (%s): %s", label, stop_r, exc)
                return _forced_close(
                    client=llm_client,
                    messages=messages,
                    model=model,
                    fallback_models=fallback_models,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    steps=steps,
                    model_used=model_used,
                    tool_names_called=tool_names_called,
                    trace=trace,
                    label=label,
                    depth=toolbelt.ctx.depth,
                    stop_reason=stop_r,
                    reserve_s=budget.close_reserve_s,
                    lane=lane,
                )
            except Exception as exc:  # noqa: BLE001 — the whole chain failed
                logger.exception("[%s] LLM chain failed", label)
                trace.add(
                    TraceStep(
                        kind="llm",
                        name=label,
                        ok=False,
                        error=str(exc)[:300],
                        duration_ms=int((time.time() - step_started) * 1000),
                        depth=toolbelt.ctx.depth,
                    )
                )
                return LoopResult(
                    text="",
                    model_used=model_used,
                    steps_taken=steps,
                    stop_reason="llm_error",
                    messages=messages,
                    tool_calls_made=tool_names_called,
                    error=str(exc),
                )

            steps += 1
            progress.report("stage", "thinking", "done", step=steps)
            trace.model_used = model_used
            if message is None:
                logger.warning("[%s] call dropped (key exhaustion on priority lane)", label)
                return LoopResult(
                    text="",
                    model_used=model_used,
                    steps_taken=steps,
                    stop_reason="key_exhaustion",
                    messages=messages,
                    tool_calls_made=tool_names_called,
                )
            calls = getattr(message, "tool_calls", None)
            text = (getattr(message, "content", None) or "").strip()

            trace.add(
                TraceStep(
                    kind="llm",
                    name=f"{label}:step{steps}",
                    ok=True,
                    result_preview=(
                        f"{len(calls)} tool call(s)" if calls else f"{len(text)} chars"
                    ),
                    duration_ms=int((time.time() - step_started) * 1000),
                    depth=toolbelt.ctx.depth,
                    text=text or None,
                )
            )

            if (
                not calls
                and require_tool
                and not nudged
                and not final_pass
                and require_tool in toolbelt
                and require_tool not in tool_names_called
            ):
                nudged = True
                trace.note(f"nudge: answered without {require_tool}, sent back to ground first", depth=toolbelt.ctx.depth)
                messages.append({"role": "system", "content": _GROUND_FIRST_NUDGE.format(tool=require_tool)})
                continue

            if not calls:
                stop_r = "max_steps" if final_pass and steps >= max_steps else "answered"
                if trace:
                    trace.stop_reason = stop_r
                return LoopResult(
                    text=text,
                    model_used=model_used,
                    steps_taken=steps,
                    stop_reason=stop_r,
                    messages=messages,
                    tool_calls_made=tool_names_called,
                )

            # Act on every call the model asked for, then observe.
            messages.append(_serialise_assistant(message))

            def _is_parallel_read(call_item) -> bool:
                c_name = getattr(call_item.function, "name", "")
                c_spec = toolbelt.get_spec(c_name) if hasattr(toolbelt, "get_spec") else None
                if c_spec is None:
                    return False
                return c_spec.scopes <= frozenset({SCOPE_READ}) and not c_spec.consequential

            if len(calls) > 1 and all(_is_parallel_read(c) for c in calls):
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(calls))) as executor:
                    futures = [
                        executor.submit(toolbelt.call, c.function.name, c.function.arguments)
                        for c in calls
                    ]
                    results = [f.result() for f in futures]
                for c, result in zip(calls, results):
                    name = c.function.name
                    tool_names_called.append(name)
                    max_chars = (
                        toolbelt.get_max_result_chars(name)
                        if hasattr(toolbelt, "get_max_result_chars")
                        else 2500
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": c.id,
                            "name": name,
                            "content": result.to_json(max_chars=max_chars),
                        }
                    )
            else:
                for call in calls:
                    name = call.function.name
                    tool_names_called.append(name)
                    result = toolbelt.call(name, call.function.arguments)
                    max_chars = (
                        toolbelt.get_max_result_chars(name)
                        if hasattr(toolbelt, "get_max_result_chars")
                        else 2500
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": name,
                            "content": result.to_json(max_chars=max_chars),
                        }
                    )

            if time.time() >= deadline:
                messages.append({"role": "system", "content": _FORCED_CLOSE_NUDGE})

        # Budget spent while still mid-reasoning: force one closing answer.
        return _forced_close(
            client=llm_client,
            messages=messages,
            model=model,
            fallback_models=fallback_models,
            temperature=temperature,
            max_tokens=max_tokens,
            steps=steps,
            model_used=model_used,
            tool_names_called=tool_names_called,
            trace=trace,
            label=label,
            depth=toolbelt.ctx.depth,
            stop_reason="time_budget" if budget.is_exhausted() or time.time() >= deadline else "max_steps",
            reserve_s=budget.close_reserve_s,
            lane=lane,
        )
    finally:
        current_budget_var.reset(budget_token)


def _forced_close(
    *,
    client: OpenAI,
    messages: List[Dict[str, Any]],
    model: str,
    fallback_models: Optional[List[str]],
    temperature: float,
    max_tokens: int,
    steps: int,
    model_used: str,
    tool_names_called: List[str],
    trace: AgentTrace,
    label: str,
    depth: int,
    stop_reason: str = "max_steps",
    reserve_s: float = 8.0,
    lane: str = "live",
) -> LoopResult:
    """One last tool-free call so a budget overrun still answers the student."""
    messages = messages + [{"role": "system", "content": _FORCED_CLOSE_NUDGE}]
    started = time.time()
    try:
        message, model_used = groq_chat(
            client=client,
            model=model,
            messages=messages,
            fallback_models=fallback_models,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=reserve_s,
            budget=None,
            lane=lane,
        )
        text = (getattr(message, "content", None) or "").strip()
        trace.add(
            TraceStep(
                kind="llm",
                name=f"{label}:forced_close",
                ok=True,
                result_preview=f"{len(text)} chars",
                duration_ms=int((time.time() - started) * 1000),
                depth=depth,
            )
        )
        if trace:
            trace.stop_reason = stop_reason
        return LoopResult(
            text=text,
            model_used=model_used,
            steps_taken=steps,
            stop_reason=stop_reason,
            messages=messages,
            tool_calls_made=tool_names_called,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] forced close failed", label)
        trace.add(
            TraceStep(
                kind="llm",
                name=f"{label}:forced_close",
                ok=False,
                error=str(exc)[:300],
                duration_ms=int((time.time() - started) * 1000),
                depth=depth,
            )
        )
        if trace:
            trace.stop_reason = "llm_error"
        return LoopResult(
            text="",
            model_used=model_used,
            steps_taken=steps,
            stop_reason="llm_error",
            messages=messages,
            tool_calls_made=tool_names_called,
            error=str(exc),
        )
