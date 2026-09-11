"""The handoff tool: how the Main Tutor delegates.

This is the one tool that creates another agent, so it is also the one that has
to be hardest to misuse. Two protections, both structural:

  * it requires the `compose` scope, which only the Main Tutor is granted — an
    expert's toolbelt is built with read scope alone, so this tool is not merely
    discouraged for them, it is absent;
  * it refuses outright at depth >= 1, so even a mis-scoped caller cannot start
    a chain of experts spawning experts.

The tool description is written for the model that has to choose: it says when
delegating is worth the latency and when answering directly is better, because
an agent that hands off reflexively is as badly calibrated as one that never
does.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from agents.runtime.toolbelt import SCOPE_COMPOSE, ToolContext, tool
from agents.experts import EXPERTS, expert_catalogue, run_expert


class ConsultExpertArgs(BaseModel):
    expert: str = Field(
        ...,
        description=(
            "Which expert to consult. Composers write what the student reads: "
            "worked_solution, socratic_guide, practice_set, quiz_builder. "
            "Analysts report back to you privately: verifier, error_locator, "
            "alt_explainer, comparer."
        ),
    )
    task: str = Field(
        ...,
        description=(
            "The single job for this expert, stated plainly and completely. The "
            "expert sees only this and the material you pass — it cannot see the "
            "conversation, so do not refer to 'the question above'."
        ),
    )
    material: str = Field(
        default="",
        description=(
            "Only for analysts: the specific work to examine — the student's steps, "
            "the calculation to re-check, the two things to compare. Composers read "
            "the retrieved material directly and ignore this."
        ),
    )


@tool(
    name="consult_expert",
    description=(
        "Hand one narrow job to a temporary expert, which does it and disappears. "
        "Use a composer when the student needs something written for them. Use an "
        "analyst when you want a finding you do not already have: an independent "
        "check on a result a student would be misled by, the first wrong step in "
        "their work, a different angle after an explanation has already failed. "
        "Do not consult an expert to confirm something you are already confident "
        "about — it costs the student waiting time and buys nothing.\n\n"
        + expert_catalogue()
    ),
    args_model=ConsultExpertArgs,
    scopes=(SCOPE_COMPOSE,),
)
def consult_expert(args: ConsultExpertArgs, ctx: ToolContext) -> Dict[str, Any]:
    if ctx.depth >= 1:
        return {
            "error": (
                "You are already an expert working on one narrow task. You cannot "
                "delegate further — finish the job with the tools you have and "
                "report back."
            )
        }

    name = (args.expert or "").strip()
    if name not in EXPERTS:
        return {
            "error": (
                f"No expert called '{name}'. Available: {', '.join(sorted(EXPERTS))}."
            )
        }

    task = (args.task or "").strip()
    if len(task) < 10:
        return {
            "error": (
                "The task is too vague for an expert that cannot see the "
                "conversation. State the whole job in one self-contained "
                "instruction."
            )
        }

    return run_expert(name, task, ctx, material=args.material or "")
