"""HTTP and retrieval helpers — not the agent's live toolbelt.

Student-turn tools register with `@tool` in `compute_tools`, `curriculum_tools`,
`learner_tools`, and `handoff`. `agents/learning_agent.py::_import_tools` is the
one place that list is imported. Do not add a second registry here: the old
`AGENT_TOOLS` map (tutor / syllabus / doubt_resolver / practice_coach /
mentor_briefing) was leftover from the five-facade graph and drifted from what
the Main Tutor could actually call.

The names below are still used by the FastAPI dashboard/status endpoints and by
internal wrappers. They are not model-visible tools.
"""

from mcp_tools.resolve_concept_id import ResolveConceptIdArgs, resolve_concept_id
from mcp_tools.generate_suggestions import GenerateSuggestionsArgs, generate_suggestions
from mcp_tools.get_syllabus_status import GetSyllabusStatusArgs, get_syllabus_status
from mcp_tools.get_agent_signals import GetAgentSignalsArgs, get_agent_signals

__all__ = [
    "ResolveConceptIdArgs",
    "resolve_concept_id",
    "GenerateSuggestionsArgs",
    "generate_suggestions",
    "GetSyllabusStatusArgs",
    "get_syllabus_status",
    "GetAgentSignalsArgs",
    "get_agent_signals",
]
