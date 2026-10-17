"""Decision trace for one agent turn.

In the old graph you could read a turn's behaviour off the flowchart: the edges
were fixed, so the path was knowable in advance. An autonomous agent chooses its
own path, so the path has to be *recorded* instead. That is what this is.

A trace is cheap, always-on, and never load-bearing: recording must never change
or break the turn it is observing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _clip(value: Any, limit: int = 400) -> Any:
    """Shrink a value to something loggable without dragging whole chunks along."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"…(+{len(value) - limit})"
    if isinstance(value, dict):
        return {k: _clip(v, limit // 2) for k, v in list(value.items())[:12]}
    if isinstance(value, list):
        head = [_clip(v, limit // 2) for v in value[:5]]
        return head + [f"…(+{len(value) - 5} more)"] if len(value) > 5 else head
    return value


@dataclass
class TraceStep:
    """One thing the agent did: a tool call, a handoff, or a verification pass."""

    kind: str                       # tool | handoff | check | llm | note
    name: str
    args: Optional[Dict[str, Any]] = None
    ok: bool = True
    error: Optional[str] = None
    result_preview: Optional[Any] = None
    duration_ms: int = 0
    depth: int = 0                  # 0 = main tutor, 1 = expert subagent

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "kind": self.kind,
            "name": self.name,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "depth": self.depth,
        }
        if self.args:
            out["args"] = _clip(self.args)
        if self.error:
            out["error"] = _clip(self.error, 200)
        if self.result_preview is not None:
            out["result"] = _clip(self.result_preview)
        return out


@dataclass
class AgentTrace:
    """Ordered record of one turn's decisions."""

    user_id: str = ""
    session_id: str = ""
    turn_index: Optional[int] = None
    route: str = "agent_loop"           # fast_path | agent_loop
    model_used: Optional[str] = None
    checker_verdict: Optional[str] = None
    steps: List[TraceStep] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    # Telemetry metrics (migration 020)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    stop_reason: Optional[str] = None
    prompt_version: Optional[str] = None
    toolset_version: Optional[str] = None
    max_step_input_chars: int = 0

    def add(self, step: TraceStep) -> TraceStep:
        self.steps.append(step)
        return step

    def note(self, text: str, *, depth: int = 0) -> None:
        self.add(TraceStep(kind="note", name=text, depth=depth))

    @property
    def tool_calls(self) -> int:
        return sum(1 for s in self.steps if s.kind == "tool")

    @property
    def handoffs(self) -> int:
        return sum(1 for s in self.steps if s.kind == "handoff")

    @property
    def latency_ms(self) -> int:
        return int((time.time() - self.started_at) * 1000)

    @property
    def metrics(self) -> Dict[str, Any]:
        return {
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "stop_reason": self.stop_reason or "",
            "prompt_version": self.prompt_version or "",
            "toolset_version": self.toolset_version or "",
            "max_step_input_chars": self.max_step_input_chars,
        }

    def path(self) -> List[str]:
        """Compact human-readable path, e.g. ['tool:retrieve_content', 'handoff:solver']."""
        return [f"{s.kind}:{s.name}" for s in self.steps if s.kind in ("tool", "handoff", "check")]

    def to_row(self) -> Dict[str, Any]:
        """Shape expected by `agent_trace` (migration 006, 020)."""
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "turn_index": self.turn_index,
            "route": self.route,
            "steps": [s.to_dict() for s in self.steps],
            "tool_calls": self.tool_calls,
            "handoffs": self.handoffs,
            "checker_verdict": self.checker_verdict,
            "latency_ms": self.latency_ms,
            "model_used": self.model_used,
            "metrics": self.metrics,
        }

    def persist(self) -> None:
        """Best-effort write. Telemetry must never break a turn."""
        try:
            from db.supabase_client import db

            if self.user_id and self.session_id:
                db.insert_agent_trace(self.to_row())
        except Exception:
            pass
