"""
LangGraph workflow — wires all 9 agents into the correct execution order.

Intent routing after Orchestrator:
  doubt / practice_request  → Concept Resolver → GraphRAG Retriever → Solver | Socratic
                              → Diagnostic (if graded) | Follow Up → Context Manager
  quiz_response             → Diagnostic → Quiz | Follow Up → Context Manager
  graded_response           → Diagnostic → Follow Up (prereq surfacing or suggestions)
                              → Context Manager
  prereq_confirmation       → GraphRAG Retriever → Solver | Socratic
                              → Diagnostic | Follow Up → Context Manager
  follow_up_response        → Follow Up → Context Manager   (FIXED: was → END)

Special routes:
  • trigger_quiz = True after Diagnostic → Quiz instead of Follow Up
  • error in state            → END immediately
"""

from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import StateGraph, END

from models.state import TutorState
from agents.orchestrator import orchestrator_node
from agents.concept_resolver import concept_resolver_node
from agents.graphrag_retriever import graphrag_retriever_node
from agents.solver import solver_node
from agents.socratic import socratic_node
from agents.diagnostic import diagnostic_node
from agents.follow_up import follow_up_node
from agents.quiz import quiz_node
from agents.context_manager import context_manager_node


# ── Routing functions ─────────────────────────────────────────────────────────

_PRACTICE_TYPES = {"practice", "pyq"}


def route_after_orchestrator(state: TutorState) -> str:
    if state.get("error"):
        return "end"
    intent = state.get("intent", "doubt")

    if intent == "follow_up_response":
        return "follow_up"

    if intent in {"quiz_response", "graded_response"}:
        # If has_graded_outcome is False the Orchestrator already wrote a
        # reminder message and set response in state — route straight to END.
        if not state.get("has_graded_outcome"):
            return "end"
        return "diagnostic"

    if intent == "prereq_confirmation":
        # Orchestrator already injected primary_concept_id from pending prereq;
        # skip Concept Resolver and go straight to retrieval.
        return "graphrag_retriever"

    # doubt | practice_request | quiz_request → full pipeline.
    # quiz_request rides the same path; quiz_batch_mode (set by Orchestrator)
    # diverts the retriever's output to the Quiz agent in route_to_responder.
    return "concept_resolver"


def route_to_responder(state: TutorState) -> str:
    if state.get("error"):
        return "end"
    # On-demand quiz: build a batch of questions instead of a single answer.
    if state.get("quiz_batch_mode"):
        return "quiz"
    if state.get("use_socratic"):
        return "socratic"
    return "solver"


def route_after_response(state: TutorState) -> str:
    """After Solver or Socratic emits a response, decide post-processing path."""
    if state.get("error"):
        return "end"
    if state.get("has_graded_outcome"):
        return "diagnostic"
    # Bug 2 / Bug 1 fix: when a practice or PYQ question was just served the
    # student has not answered yet — Follow-Up suggestions here are premature
    # and confusing.  Skip Follow-Up entirely; still honour the Context Manager
    # schedule if it is due.
    if state.get("content_type_requested") in _PRACTICE_TYPES:
        return "context_manager" if state.get("run_context_manager") else "end"
    return "follow_up"


def route_after_diagnostic(state: TutorState) -> str:
    if state.get("error"):
        return "end"
    # An in-progress on-demand quiz batch must advance to its next question via
    # Follow-Up — never divert into the struggle-triggered micro-quiz mid-batch.
    if state.get("quiz_batch_mode"):
        return "follow_up"
    if state.get("trigger_quiz"):
        return "quiz"
    return "follow_up"


def route_after_quiz_or_followup(state: TutorState) -> str:
    """After quiz or follow-up, optionally run Context Manager."""
    if state.get("run_context_manager"):
        return "context_manager"
    return "end"


# ── Node wrappers (add error short-circuit guard) ──────────────────────────────

def _guard(node_fn):
    """Skip the node if there's already an error in state."""
    def wrapped(state: TutorState) -> Dict[str, Any]:
        if state.get("error"):
            return {}
        return node_fn(state)
    wrapped.__name__ = node_fn.__name__
    return wrapped


# ── Build the graph ───────────────────────────────────────────────────────────

def build_graph() -> Any:
    workflow = StateGraph(TutorState)

    # Register nodes
    workflow.add_node("orchestrator",       _guard(orchestrator_node))
    workflow.add_node("concept_resolver",   _guard(concept_resolver_node))
    workflow.add_node("graphrag_retriever", _guard(graphrag_retriever_node))
    workflow.add_node("solver",             _guard(solver_node))
    workflow.add_node("socratic",           _guard(socratic_node))
    workflow.add_node("diagnostic",         _guard(diagnostic_node))
    workflow.add_node("follow_up",          _guard(follow_up_node))
    workflow.add_node("quiz",               _guard(quiz_node))
    workflow.add_node("context_manager",    _guard(context_manager_node))

    # Entry point
    workflow.set_entry_point("orchestrator")

    # ── Orchestrator routing ──────────────────────────────────────────────────
    workflow.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {
            "concept_resolver":   "concept_resolver",
            "diagnostic":         "diagnostic",      # quiz_response / graded_response
            "follow_up":          "follow_up",       # follow_up_response
            "graphrag_retriever": "graphrag_retriever",  # prereq_confirmation
            "end":                END,
        },
    )

    # ── Concept → Retriever → Responder ──────────────────────────────────────
    workflow.add_edge("concept_resolver", "graphrag_retriever")

    workflow.add_conditional_edges(
        "graphrag_retriever",
        route_to_responder,
        {
            "solver":   "solver",
            "socratic": "socratic",
            "quiz":     "quiz",        # on-demand quiz batch (quiz_batch_mode)
            "end":      END,
        },
    )

    # ── After response: Diagnostic, FollowUp, or direct to ContextManager ───────
    _post_response_map = {
        "diagnostic":      "diagnostic",
        "follow_up":       "follow_up",
        "context_manager": "context_manager",   # practice/PYQ: skip Follow-Up
        "end":             END,
    }
    workflow.add_conditional_edges("solver",   route_after_response, _post_response_map)
    workflow.add_conditional_edges("socratic", route_after_response, _post_response_map)

    # ── After Diagnostic: Quiz or FollowUp ────────────────────────────────────
    workflow.add_conditional_edges(
        "diagnostic",
        route_after_diagnostic,
        {
            "quiz":      "quiz",
            "follow_up": "follow_up",
            "end":       END,
        },
    )

    # ── After Quiz / FollowUp: optionally run Context Manager ─────────────────
    workflow.add_conditional_edges(
        "quiz",
        route_after_quiz_or_followup,
        {
            "context_manager": "context_manager",
            "end":             END,
        },
    )

    workflow.add_conditional_edges(
        "follow_up",
        route_after_quiz_or_followup,
        {
            "context_manager": "context_manager",
            "end":             END,
        },
    )

    workflow.add_edge("context_manager", END)

    return workflow.compile()


# Compiled graph — import and call `.invoke(state)` from main.py
tutor_graph = build_graph()
