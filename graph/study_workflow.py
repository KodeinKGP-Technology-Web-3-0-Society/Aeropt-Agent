"""Graph wiring for the Study Room.

A separate LangGraph from the Main Tutor. The flow is simpler:
bootstrap → teacher_agent → [evaluation_agent?] → context_manager → END

The Teacher Agent decides phase transitions. If it completes the session,
the evaluation agent runs before ending.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from langgraph.graph import END, StateGraph

from agents.context_manager import context_manager_node
from agents.session_bootstrap import session_bootstrap_node
from agents.teacher_agent import teacher_agent_node
from agents.evaluation_agent import evaluation_agent_node
from models.study_state import StudyRoomState

logger = logging.getLogger("jee_tutor.study_workflow")


def study_bootstrap_node(state: StudyRoomState) -> Dict[str, Any]:
    """Adapt the existing bootstrap node for the study room state."""
    # We can reuse the existing session_bootstrap_node by casting the state
    from models.state import TutorState
    tutor_state: TutorState = dict(state)  # type: ignore
    
    # Run bootstrap
    updates = session_bootstrap_node(tutor_state)
    
    # Map back any relevant fields
    return updates


def route_after_teacher(state: StudyRoomState) -> str:
    if state.get("error") or state.get("cancelled"):
        return "end"
        
    # If the phase transitioned to evaluation, run the evaluation agent
    if state.get("phase_transition") == "evaluation" or state.get("current_phase") == "evaluation":
        return "evaluation"
        
    # Otherwise run context manager if due
    return "context_manager" if state.get("run_context_manager") else "end"


def route_after_evaluation(state: StudyRoomState) -> str:
    if state.get("error"):
        return "end"
    return "context_manager" if state.get("run_context_manager") else "end"


def _guard(node_fn):
    """Skip a node when the turn has already failed."""

    def wrapped(state: StudyRoomState) -> Dict[str, Any]:
        if state.get("error"):
            return {}
        return node_fn(state)

    wrapped.__name__ = node_fn.__name__
    return wrapped


def build_study_graph() -> Any:
    workflow = StateGraph(StudyRoomState)

    workflow.add_node("bootstrap", study_bootstrap_node)
    workflow.add_node("teacher_agent", _guard(teacher_agent_node))
    workflow.add_node("evaluation_agent", _guard(evaluation_agent_node))
    workflow.add_node("context_manager", _guard(context_manager_node))

    workflow.set_entry_point("bootstrap")

    workflow.add_edge("bootstrap", "teacher_agent")

    workflow.add_conditional_edges(
        "teacher_agent",
        route_after_teacher,
        {
            "evaluation": "evaluation_agent",
            "context_manager": "context_manager",
            "end": END,
        },
    )
    
    workflow.add_conditional_edges(
        "evaluation_agent",
        route_after_evaluation,
        {
            "context_manager": "context_manager",
            "end": END,
        },
    )

    workflow.add_edge("context_manager", END)

    return workflow.compile()


#: Compiled graph — `study_graph.invoke(state)`
study_graph = build_study_graph()
