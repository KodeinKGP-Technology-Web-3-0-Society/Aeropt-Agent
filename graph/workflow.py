"""Graph wiring for the autonomous Learning Agent.

What this graph is *not* any more: a flowchart. The previous version encoded the
tutor's behaviour as conditional edges between nine agent nodes, switched on a
closed seven-value intent enum. Every genuinely new situation — a compound
request, a cross-concept comparison, "why did you mark that wrong?" — had to be
squeezed into one of those seven values, and the edges decided the rest. That is
a workflow wearing an agent's clothes.

What is left here is the small amount of structure that genuinely *is* fixed:

    bootstrap ──> [structured input?] ──> fast path ──┐
                           │                          ├──> context manager ──> END
                           └──> learning agent ───────┘

One routing decision, and it turns on the *shape of the input*, not on a guess
about its meaning. A Correct/Incorrect button click is a structured payload with
one correct handling and no ambiguity to reason about, so it takes a
deterministic path with no agent call at all — it is also most of the traffic,
so this is where the latency budget is won.

Everything that arrives as open-ended text goes to the agent, which decides for
itself what it needs. There is no intent enum to fall outside of.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from langgraph.graph import END, StateGraph

from agents.diagnostic import diagnostic_node
from agents.context_manager import context_manager_node
from agents.follow_up import follow_up_node
from agents.learning_agent import learning_agent_node
from agents.session_bootstrap import session_bootstrap_node
from models.state import TutorState

logger = logging.getLogger("jee_tutor.workflow")


# ── The one routing decision ──────────────────────────────────────────────────


def is_structured_input(state: TutorState) -> bool:
    """True when this turn has exactly one correct handling and no ambiguity.

    Today that means a graded outcome: the student pressed Correct or Incorrect
    on a practice question. `services/turn_service.parse_turn_input` sets
    `has_graded_outcome` for that case, from the button payload rather than from
    reading the text.

    Keep this test structural. The moment it starts inspecting *what the student
    said*, it has become an intent classifier again, and the whole problem comes
    back.
    """
    return bool(state.get("has_graded_outcome"))


def route_from_bootstrap(state: TutorState) -> str:
    if state.get("error"):
        return "end"
    if is_structured_input(state):
        logger.debug("fast path: structured graded outcome")
        return "graded_fast_path"
    return "learning_agent"


def route_after_turn(state: TutorState) -> str:
    """Run the session summariser if this turn is due for one."""
    if state.get("error"):
        return "end"
    return "context_manager" if state.get("run_context_manager") else "end"


# ── Fast path ─────────────────────────────────────────────────────────────────


def graded_fast_path_node(state: TutorState) -> Dict[str, Any]:
    """Handle a Correct/Incorrect button click without any agent reasoning.

    Two deterministic steps, in order: record the attempt (fixed proficiency
    maths), then decide what to put in front of the student next (a weak
    prerequisite, the next quiz question, or fresh suggestions). Both already
    existed and are unchanged — this path is a latency optimisation, not new
    behaviour, and it must not drift away from what the agent would do.
    """
    updates: Dict[str, Any] = {}

    graded = diagnostic_node(state)
    if graded.get("error"):
        # Grading failed, but the student still pressed a button and is waiting.
        # Fall through to follow-up so they get a reply, and keep the reason.
        logger.warning("Fast-path grading failed: %s", graded["error"])
        graded.pop("error", None)
    updates.update(graded)

    merged: Dict[str, Any] = {**state, **updates}
    follow_up = follow_up_node(merged)  # type: ignore[arg-type]
    if follow_up.get("error"):
        logger.warning("Fast-path follow-up failed: %s", follow_up["error"])
        follow_up.pop("error", None)
    updates.update(follow_up)

    updates.setdefault("agent_used", "graded_fast_path")
    updates["error"] = None
    return updates


# ── Node guard ────────────────────────────────────────────────────────────────


def _guard(node_fn):
    """Skip a node when the turn has already failed."""

    def wrapped(state: TutorState) -> Dict[str, Any]:
        if state.get("error"):
            return {}
        return node_fn(state)

    wrapped.__name__ = node_fn.__name__
    return wrapped


# ── Build ─────────────────────────────────────────────────────────────────────


def build_graph() -> Any:
    workflow = StateGraph(TutorState)

    workflow.add_node("bootstrap", session_bootstrap_node)
    workflow.add_node("graded_fast_path", _guard(graded_fast_path_node))
    workflow.add_node("learning_agent", _guard(learning_agent_node))
    workflow.add_node("context_manager", _guard(context_manager_node))

    workflow.set_entry_point("bootstrap")

    workflow.add_conditional_edges(
        "bootstrap",
        route_from_bootstrap,
        {
            "graded_fast_path": "graded_fast_path",
            "learning_agent": "learning_agent",
            "end": END,
        },
    )

    post_turn = {"context_manager": "context_manager", "end": END}
    workflow.add_conditional_edges("graded_fast_path", route_after_turn, post_turn)
    workflow.add_conditional_edges("learning_agent", route_after_turn, post_turn)

    workflow.add_edge("context_manager", END)

    return workflow.compile()


#: Compiled graph — `tutor_graph.invoke(state)` from the entry points.
tutor_graph = build_graph()
