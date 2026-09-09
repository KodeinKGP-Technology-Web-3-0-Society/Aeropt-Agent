"""
Canonical MCP tool registry — agents import from here; future MCP HTTP server
exposes the same functions.
"""

from mcp_tools.resolve_concept_id import ResolveConceptIdArgs, resolve_concept_id
from mcp_tools.search_knowledge_graph import SearchKnowledgeGraphArgs, search_knowledge_graph
from mcp_tools.evaluate_student_proficiency import (
    EvaluateProficiencyArgs,
    evaluate_student_proficiency,
)
from mcp_tools.generate_suggestions import GenerateSuggestionsArgs, generate_suggestions
from mcp_tools.get_syllabus_status import GetSyllabusStatusArgs, get_syllabus_status
from mcp_tools.get_agent_signals import GetAgentSignalsArgs, get_agent_signals
from mcp_tools.build_mentor_briefing import BuildMentorBriefingArgs, build_mentor_briefing

AGENT_TOOLS = {
    "tutor": ["search_knowledge_graph", "resolve_concept_id"],
    "syllabus": ["get_syllabus_status", "resolve_concept_id"],
    "doubt_resolver": ["resolve_concept_id"],
    "practice_coach": [
        "search_knowledge_graph",
        "evaluate_student_proficiency",
        "generate_suggestions",
    ],
    "mentor_briefing": ["build_mentor_briefing", "get_agent_signals"],
}

__all__ = [
    "AGENT_TOOLS",
    "ResolveConceptIdArgs",
    "resolve_concept_id",
    "SearchKnowledgeGraphArgs",
    "search_knowledge_graph",
    "EvaluateProficiencyArgs",
    "evaluate_student_proficiency",
    "GenerateSuggestionsArgs",
    "generate_suggestions",
    "GetSyllabusStatusArgs",
    "get_syllabus_status",
    "GetAgentSignalsArgs",
    "get_agent_signals",
    "BuildMentorBriefingArgs",
    "build_mentor_briefing",
]
