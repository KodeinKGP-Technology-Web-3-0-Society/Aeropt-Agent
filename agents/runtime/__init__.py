"""Runtime primitives for the autonomous Learning Agent.

This package holds the machinery that makes the agent agentic rather than
scripted: a typed toolbelt the model calls directly, and a reasoning loop that
lets it decide, per turn, which tools to call and in what order.

Nothing in here knows anything about JEE Physics. Pedagogy lives in the agent
prompts (`skills/`) and the tools (`mcp_tools/`); this package only supplies the
decide -> act -> observe machinery and the guardrails around it.
"""

from agents.runtime.toolbelt import (
    Toolbelt,
    ToolContext,
    ToolResult,
    ToolSpec,
    tool,
)
from agents.runtime.trace import AgentTrace, TraceStep

__all__ = [
    "Toolbelt",
    "ToolContext",
    "ToolResult",
    "ToolSpec",
    "tool",
    "AgentTrace",
    "TraceStep",
]
