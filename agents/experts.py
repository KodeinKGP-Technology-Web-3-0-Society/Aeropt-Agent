"""Temporary expert subagents: one narrow job, then gone.

Two kinds, because handing off has two distinct purposes.

**Composers** produce student-facing material. They are the existing, heavily
tested generators — the solver with its LaTeX rules and figure handling, the
Socratic questioner, the quiz and practice builders. Handing off to one returns
a *draft*, which the Main Tutor may accept verbatim. That verbatim path matters:
re-typing a composed answer through another model is how careful formatting,
worked steps and image references quietly get mangled.

**Analysts** answer a question the tutor has about its own work — check this
arithmetic independently, find the first wrong step, try a different framing.
They run their own small reasoning loop with read-only tools and report back.
They never speak to the student.

Every expert, of both kinds, is constrained the same way:

  * one task, stated by the tutor, with only the context that task needs;
  * read-only tools — no memory writes, no progress writes, no scheduling;
  * no handoffs of its own (`depth >= 1` removes `consult_expert`);
  * no channel to the student — output goes back to the tutor, always.

Those are structural, not prompt requests: the scope set and the depth check in
`agents/runtime/toolbelt.py` enforce them.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import config
from agents.runtime.toolbelt import EXPERT_SCOPES, ToolContext, Toolbelt, registry
from agents.runtime.trace import TraceStep

logger = logging.getLogger("jee_tutor.experts")

#: Analysts get a tight budget — they answer one question, they do not explore.
_ANALYST_MAX_STEPS = 3
_ANALYST_TIME_BUDGET_S = 20.0

#: How much of a composed draft the tutor sees when deciding whether to accept it.
_DRAFT_FULL_UNDER = 1400
_DRAFT_HEAD = 900
_DRAFT_TAIL = 400


@dataclass(frozen=True)
class Expert:
    """One narrow role the Main Tutor can hand off to."""

    name: str
    #: Shown to the tutor in the consult_expert tool description.
    purpose: str
    kind: str                       # "composer" | "analyst"
    #: Composer: the node to run. Analyst: None.
    node: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    #: Analyst: system prompt. Composer: unused.
    system_prompt: str = ""
    model: str = ""
    fallback_models: tuple = ()
    #: Composer only: content_type to force before running the node.
    content_type: Optional[str] = None


def _solver_node(state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.solver import solver_node

    return solver_node(state)  # type: ignore[arg-type]


def _socratic_node(state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.socratic import socratic_node

    return socratic_node(state)  # type: ignore[arg-type]


def _quiz_node(state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.quiz import quiz_builder_node

    return quiz_builder_node(state)  # type: ignore[arg-type]


def _practice_node(state: Dict[str, Any]) -> Dict[str, Any]:
    from agents.practice_generator import practice_set_node

    return practice_set_node(state)  # type: ignore[arg-type]


_VERIFIER_PROMPT = """\
You independently check physics and mathematics work. You are not teaching \
anyone — you are the second pair of eyes.

Work the problem yourself before looking for agreement with what you were given. \
Use the calculate tool for every arithmetic step; do not do arithmetic in your \
head. Check units and orders of magnitude as well as the numbers.

Report back in this shape, and nothing else:

VERDICT: correct | incorrect | cannot_determine
WHAT_I_GOT: <your own result, with units>
DISAGREEMENT: <if incorrect, the exact step where it first goes wrong and why; \
otherwise "none">
CONFIDENCE: high | medium | low

If you cannot check something because information is missing, say \
cannot_determine and name what is missing. Do not guess to be helpful."""

_ERROR_LOCATOR_PROMPT = """\
You find the FIRST place a student's work goes wrong, and nothing beyond that.

Read their steps in order. Recompute each one with the calculate tool. Stop at \
the first step that does not follow from the one before it.

Report back in this shape, and nothing else:

FIRST_ERROR_STEP: <quote the step, or "none found">
WHY_ITS_WRONG: <one or two sentences>
ERROR_KIND: conceptual | procedural | calculation | misread
WHAT_IT_SHOULD_BE: <the corrected step>
EVERYTHING_BEFORE: correct | also_has_issues

Everything after the first error usually follows from it — do not list those as \
separate mistakes. If the work is actually correct, say "none found" and say so \
clearly."""

_ALT_EXPLAINER_PROMPT = """\
You produce a different explanation of an idea the student did not get the \
first time.

Different means genuinely different: change the angle of attack, not the \
wording. A concrete everyday case instead of the general rule. A physical \
picture instead of algebra. Working backwards from the answer. A limiting case \
that makes the behaviour obvious.

Report back:

APPROACH: <one line naming the angle you took and why it might land better>
EXPLANATION: <the explanation itself, under 200 words>
CHECK: <one question that would reveal whether it landed>

Do not restate the original explanation more slowly. That is what already \
failed."""

_COMPARER_PROMPT = """\
You compare two or more concepts, methods or solutions for a student who has \
confused them.

Find the actual distinction that matters — the one that determines which applies \
when — not a list of surface differences.

Report back:

CORE_DIFFERENCE: <one sentence: the distinction that actually matters>
WHEN_EACH_APPLIES: <the deciding condition for each>
EASY_TO_CONFUSE_BECAUSE: <why a student mixes them up>
DISCRIMINATING_EXAMPLE: <one case where picking the wrong one gives a visibly \
wrong answer>"""


EXPERTS: Dict[str, Expert] = {
    # ── Composers ────────────────────────────────────────────────────────────
    "worked_solution": Expert(
        name="worked_solution",
        purpose=(
            "Compose a full worked explanation or solution, grounded in the "
            "retrieved material, with proper formatting and figures. The default "
            "choice when the student needs to be taught something."
        ),
        kind="composer",
        node=_solver_node,
    ),
    "socratic_guide": Expert(
        name="socratic_guide",
        purpose=(
            "Compose a guiding question instead of an answer — for when the "
            "student is close and would learn more from being walked to it than "
            "from being told."
        ),
        kind="composer",
        node=_socratic_node,
    ),
    "practice_set": Expert(
        name="practice_set",
        purpose=(
            "Compose practice questions or past-year questions for the student to "
            "attempt, pitched at their current level."
        ),
        kind="composer",
        node=_practice_node,
        content_type="practice",
    ),
    "quiz_builder": Expert(
        name="quiz_builder",
        purpose=(
            "Compose a set of quiz questions to test the student across a concept, "
            "for when they ask to be quizzed or you want to check retention."
        ),
        kind="composer",
        node=_quiz_node,
    ),
    # ── Analysts ─────────────────────────────────────────────────────────────
    "verifier": Expert(
        name="verifier",
        purpose=(
            "Independently rework a calculation or solution and report whether it "
            "holds up. Use when a wrong number would mislead the student and you "
            "want a second pass before committing."
        ),
        kind="analyst",
        system_prompt=_VERIFIER_PROMPT,
        model=config.SOLVER_MODEL,
        fallback_models=tuple(config.SOLVER_FALLBACK_MODELS),
    ),
    "error_locator": Expert(
        name="error_locator",
        purpose=(
            "Find the first incorrect step in work the student submitted. Use when "
            "they ask why their answer is wrong."
        ),
        kind="analyst",
        system_prompt=_ERROR_LOCATOR_PROMPT,
        model=config.SOLVER_MODEL,
        fallback_models=tuple(config.SOLVER_FALLBACK_MODELS),
    ),
    "alt_explainer": Expert(
        name="alt_explainer",
        purpose=(
            "Produce a genuinely different explanation of an idea the student did "
            "not get the first time. Use after an explanation has already failed."
        ),
        kind="analyst",
        system_prompt=_ALT_EXPLAINER_PROMPT,
        model=config.SOCRATIC_MODEL,
        fallback_models=tuple(config.SOCRATIC_FALLBACK_MODELS),
    ),
    "comparer": Expert(
        name="comparer",
        purpose=(
            "Draw out the distinction between two concepts or methods the student "
            "is conflating. Use for 'what's the difference between X and Y'."
        ),
        kind="analyst",
        system_prompt=_COMPARER_PROMPT,
        model=config.SOLVER_MODEL,
        fallback_models=tuple(config.SOLVER_FALLBACK_MODELS),
    ),
}


def expert_catalogue() -> str:
    """Concise menu for consult_expert schema."""
    composers = [f"{e.name} ({e.purpose.split('.')[0]})" for e in EXPERTS.values() if e.kind == "composer"]
    analysts = [f"{e.name} ({e.purpose.split('.')[0]})" for e in EXPERTS.values() if e.kind == "analyst"]
    return (
        "Composers (draft student text): " + "; ".join(composers) + ".\n"
        "Analysts (private check): " + "; ".join(analysts) + "."
    )


def draft_preview(draft: str) -> str:
    """Enough of a draft for the tutor to judge it without repaying for all of it."""
    if len(draft) <= _DRAFT_FULL_UNDER:
        return draft
    return (
        draft[:_DRAFT_HEAD]
        + f"\n\n…[{len(draft) - _DRAFT_HEAD - _DRAFT_TAIL} characters omitted]…\n\n"
        + draft[-_DRAFT_TAIL:]
    )


def run_expert(
    expert_name: str,
    task: str,
    ctx: ToolContext,
    *,
    material: str = "",
) -> Dict[str, Any]:
    """Run one expert to completion and return its finding or draft.

    Never raises: a failed expert reports the failure so the tutor can carry on
    without it, which is strictly better than losing the student's turn.
    """
    expert = EXPERTS.get(expert_name)
    if expert is None:
        return {
            "error": (
                f"No expert called '{expert_name}'. Available: "
                f"{', '.join(sorted(EXPERTS))}."
            )
        }

    started = time.time()
    child = ctx.child()
    child.state["_expert_task"] = task

    try:
        if expert.kind == "composer":
            result = _run_composer(expert, task, child)
            # A composer's draft is a candidate for the real reply, so it has to
            # outlive the expert that wrote it. Everything else the child staged
            # stays discarded — that isolation is the point of a subagent.
            ctx.updates.update(child.updates)
            ctx.state.update(child.updates)
        else:
            result = _run_analyst(expert, task, material, child)
    except Exception as exc:  # noqa: BLE001 — an expert failing must not end the turn
        logger.exception("Expert %s failed", expert_name)
        result = {
            "error": f"The {expert_name} expert failed ({type(exc).__name__}). "
            "Carry on without it."
        }

    ctx.trace.add(
        TraceStep(
            kind="handoff",
            name=expert_name,
            args={"task": task[:200]},
            ok="error" not in result,
            error=result.get("error"),
            result_preview=result.get("summary") or result.get("finding"),
            duration_ms=int((time.time() - started) * 1000),
            depth=ctx.depth,
        )
    )
    return result


def _run_composer(expert: Expert, task: str, ctx: ToolContext) -> Dict[str, Any]:
    """Run a generator node and stage its draft for verbatim use."""
    state = dict(ctx.state)
    state["query"] = task or state.get("query", "")
    if expert.content_type:
        state.setdefault("content_type_requested", expert.content_type)

    assert expert.node is not None
    out = expert.node(state)

    if out.get("error"):
        return {"error": out["error"]}

    draft = (out.get("response") or "").strip()
    if not draft:
        return {
            "error": (
                f"The {expert.name} expert produced nothing usable. Answer the "
                "student yourself."
            )
        }

    # Staged here, then lifted to the parent by run_expert — this draft is a
    # candidate for the student-facing reply, so it must survive the expert.
    parent_updates = {
        "draft_response": draft,
        "draft_author": expert.name,
        "draft_model": out.get("model_used"),
    }
    for key in (
        "quiz_steps",
        "quiz_batch_mode",
        "quiz_batch_chunks",
        "quiz_batch_index",
        "awaiting_graded_response",
        "practice_payload",
        "generated_practice_batch",
        "sandbox_card",
    ):
        if key in out:
            parent_updates[key] = out[key]

    ctx.state.update(parent_updates)
    ctx.updates.update(parent_updates)

    return {
        "composed_by": expert.name,
        "draft_length_chars": len(draft),
        "draft": draft_preview(draft),
        "summary": f"{expert.name} drafted {len(draft)} chars",
        "final": True,
        "next_step": (
            "This draft is staged as the reply. If it answers the student well, "
            "respond with exactly USE_DRAFT and nothing else — that sends it "
            "unchanged and preserves its formatting. Only write your own reply "
            "instead if the draft is genuinely wrong or off-target."
        ),
    }


def _run_analyst(expert: Expert, task: str, material: str, ctx: ToolContext) -> Dict[str, Any]:
    """Run a narrow read-only reasoning loop and return its report."""
    from agents.runtime.loop import run_agent_loop

    child_budget = None
    time_budget_s = _ANALYST_TIME_BUDGET_S
    if getattr(ctx, "budget", None) is not None:
        child_budget = ctx.budget.child_budget(own_budget_s=_ANALYST_TIME_BUDGET_S, reserve_s=8.0)
        if child_budget.remaining() <= 0:
            return {
                "error": (
                    f"The {expert.name} expert could not be consulted (turn budget exhausted). "
                    "Carry on without it, and do not claim anything was independently verified."
                )
            }
        time_budget_s = child_budget.remaining()

    # Read-only tools, and no consult_expert: an expert cannot spawn an expert.
    specs = [s for name, s in registry().items() if name != "consult_expert"]
    expert_ctx = ToolContext(
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        state=dict(ctx.state),
        trace=ctx.trace,
        depth=ctx.depth + 1,
        granted_permissions=ctx.granted_permissions,
        budget=child_budget,
    )
    belt = Toolbelt(specs, expert_ctx, allowed_scopes=EXPERT_SCOPES)

    user_message = task
    if material.strip():
        user_message = f"{task}\n\n--- Material to work from ---\n{material.strip()}"

    result = run_agent_loop(
        system_prompt=expert.system_prompt,
        user_message=user_message,
        toolbelt=belt,
        model=expert.model or config.SOLVER_MODEL,
        fallback_models=list(expert.fallback_models) or config.SOLVER_FALLBACK_MODELS,
        max_steps=_ANALYST_MAX_STEPS,
        time_budget_s=time_budget_s,
        temperature=0.2,
        max_tokens=900,
        trace=ctx.trace,
        label=f"expert:{expert.name}",
    )

    if not result.ok:
        return {
            "error": (
                f"The {expert.name} expert could not complete its check "
                f"({result.stop_reason}). Carry on without it, and do not claim "
                "anything was independently verified."
            )
        }

    return {
        "expert": expert.name,
        "finding": result.text,
        "summary": result.text[:160],
        "tools_used": result.tool_calls_made,
    }
