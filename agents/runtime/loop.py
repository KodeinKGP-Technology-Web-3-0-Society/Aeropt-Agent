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

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from groq import Groq

import config
from agents.runtime.toolbelt import Toolbelt
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
    """Convert an SDK assistant message back into request-shaped JSON."""
    payload: Dict[str, Any] = {
        "role": "assistant",
        "content": getattr(message, "content", None) or "",
    }
    calls = getattr(message, "tool_calls", None)
    if calls:
        payload["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {
                    "name": c.function.name,
                    "arguments": c.function.arguments,
                },
            }
            for c in calls
        ]
    return payload


def run_agent_loop(
    *,
    system_prompt: str,
    user_message: str,
    toolbelt: Toolbelt,
    model: str,
    fallback_models: Optional[List[str]] = None,
    priming_messages: Optional[List[Dict[str, Any]]] = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    time_budget_s: float = DEFAULT_TIME_BUDGET_S,
    temperature: float = 0.3,
    max_tokens: int = 1800,
    client: Optional[Groq] = None,
    trace: Optional[AgentTrace] = None,
    label: str = "agent",
) -> LoopResult:
    """Run one bounded reasoning loop and return the agent's final text.

    `priming_messages` are inserted between the system prompt and the student's
    message — recalled memory, session summary, retrieved evidence the caller
    already has. They are context, not instructions.
    """
    groq = client or Groq(api_key=config.GROQ_API_KEY)
    trace = trace or toolbelt.ctx.trace
    deadline = time.time() + time_budget_s

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if priming_messages:
        messages.extend(priming_messages)
    messages.append({"role": "user", "content": user_message})

    schemas = toolbelt.schemas()
    tool_names_called: List[str] = []
    model_used = model
    steps = 0

    while steps < max_steps:
        out_of_time = time.time() >= deadline
        final_pass = out_of_time or steps == max_steps - 1

        if out_of_time:
            logger.warning("[%s] time budget exhausted after %d steps", label, steps)

        step_started = time.time()
        try:
            message, model_used = groq_chat(
                client=groq,
                model=model,
                messages=messages,
                fallback_models=fallback_models,
                # On the closing pass, take tools away entirely so the model has
                # no option but to produce the student's answer.
                tools=None if final_pass else schemas,
                tool_choice=None if final_pass else "auto",
                temperature=temperature,
                max_tokens=max_tokens,
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
        trace.model_used = model_used
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
            )
        )

        if not calls:
            return LoopResult(
                text=text,
                model_used=model_used,
                steps_taken=steps,
                stop_reason="max_steps" if final_pass and steps >= max_steps else "answered",
                messages=messages,
                tool_calls_made=tool_names_called,
            )

        # Act on every call the model asked for, then observe.
        messages.append(_serialise_assistant(message))
        for call in calls:
            name = call.function.name
            tool_names_called.append(name)
            result = toolbelt.call(name, call.function.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": name,
                    "content": result.to_json(),
                }
            )

        if time.time() >= deadline:
            messages.append({"role": "system", "content": _FORCED_CLOSE_NUDGE})

    # Budget spent while still mid-reasoning: force one closing answer.
    return _forced_close(
        groq=groq,
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
    )


def _forced_close(
    *,
    groq: Groq,
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
) -> LoopResult:
    """One last tool-free call so a budget overrun still answers the student."""
    messages = messages + [{"role": "system", "content": _FORCED_CLOSE_NUDGE}]
    started = time.time()
    try:
        message, model_used = groq_chat(
            client=groq,
            model=model,
            messages=messages,
            fallback_models=fallback_models,
            temperature=temperature,
            max_tokens=max_tokens,
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
        return LoopResult(
            text=text,
            model_used=model_used,
            steps_taken=steps,
            stop_reason="max_steps",
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
        return LoopResult(
            text="",
            model_used=model_used,
            steps_taken=steps,
            stop_reason="llm_error",
            messages=messages,
            tool_calls_made=tool_names_called,
            error=str(exc),
        )
