"""Curriculum tools: find the concept, fetch the grounding material.

These wrap the concept-context adapter and `retrieval/bridge.py`
unchanged. That is the point of the wrapper: retrieval order, the content-type
cascade, the vector search and the prerequisite walk are deterministic
engineering, and a model must not be able to improvise them. The agent decides
*whether* to retrieve and *what for*; the pipeline still decides *how*.

Tool results are deliberately compact. Full chunk text stays in turn state for
whichever expert composes the answer — dropping five complete content chunks
into the reasoning loop's context would crowd out the reasoning itself and cost
a fortune per turn.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from agents.runtime.toolbelt import SCOPE_READ, ToolContext, tool

_PREVIEW_CHARS = 220


class ResolveConceptArgs(BaseModel):
    query: str = Field(
        ...,
        description=(
            "What the student is asking about, in your own words if that is "
            "clearer than their raw phrasing. Include the topic, not the whole "
            "conversation."
        ),
    )
    content_type_hint: Optional[str] = Field(
        default=None,
        description=(
            "What kind of material this calls for, if you already know: "
            "'theory', 'example', 'practice' or 'pyq' (past-year question). "
            "Leave empty to let the resolver infer it."
        ),
    )


class RetrieveContentArgs(BaseModel):
    concept_id: Optional[str] = Field(
        default=None,
        description="Concept to retrieve for, from resolve_concept (omit to reuse turn concept).",
    )
    content_type: str = Field(
        default="theory",
        description="'theory', 'example', 'practice', or 'pyq' (past-year question).",
    )
    exam: Optional[str] = Field(
        default=None,
        description="'JEE_ADVANCED' or 'JEE_MAINS' to override the student's target exam.",
    )
    query: Optional[str] = Field(
        default=None,
        description="Search text or official reference (omit to use student message).",
    )
    avoid_repeats: Optional[bool] = Field(
        default=None,
        description="Exclude previously seen items (defaults to true for practice and pyq).",
    )


def _chunk_preview(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """A chunk summarised down to what the agent needs to reason about it."""
    text = (chunk.get("core_text") or "").strip()
    preview: Dict[str, Any] = {
        "content_id": chunk.get("content_id"),
        "content_type": chunk.get("content_type"),
        "difficulty": chunk.get("difficulty"),
        "has_solution": bool(chunk.get("solution")),
        "preview": text[:_PREVIEW_CHARS] + ("…" if len(text) > _PREVIEW_CHARS else ""),
    }
    # The citation is what makes a claim checkable. Quote material that has one and
    # the student can go and read the source; quote something with none and you are
    # speaking from memory, which is exactly the distinction to keep visible.
    if chunk.get("citation"):
        preview["cite"] = chunk["citation"]
    if chunk.get("seen_before"):
        preview["already_seen_by_student"] = True
    return preview


_TEACHING_MODE_ADVICE = {
    "socratic": (
        "Their prerequisites are solid but this concept is not landing yet — the "
        "kind of gap a good question usually closes faster than an explanation."
    ),
    "prereq_first": (
        "A prerequisite underneath this is weak, so teaching the concept directly "
        "may not stick until that gap is addressed."
    ),
    "practice": (
        "They look comfortable with this — a question to attempt will tell you "
        "more than another explanation would."
    ),
    "direct": (
        "A straight, well-grounded explanation looks like the right move here."
    ),
}


def _teaching_suggestion(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Turn the personalisation heuristic into advice rather than a decision."""
    mode = result.get("teaching_mode")
    if not mode:
        return None
    payload: Dict[str, Any] = {
        "suggested_approach": mode,
        "why": _TEACHING_MODE_ADVICE.get(mode, ""),
        "note": (
            "This is a heuristic from their proficiency numbers, not an "
            "instruction. Override it whenever the conversation tells you "
            "something it cannot see."
        ),
    }
    if result.get("difficulty_target") is not None:
        payload["difficulty_target_1_to_5"] = result["difficulty_target"]
    turns = result.get("socratic_turn_count") or 0
    if turns >= 2:
        payload["caution"] = (
            f"You have already asked {turns} guiding questions on this concept "
            "without getting there. Another one risks frustrating them — "
            "consider just explaining it."
        )
    return payload


@tool(
    name="resolve_concept",
    description=(
        "Map what the student is asking about onto the syllabus: returns the "
        "concept id, any related concepts, and whether the topic is in scope at "
        "all. Call this before retrieving material. If it reports the topic is "
        "out of scope, say so honestly rather than answering from general "
        "knowledge as though it were course material."
    ),
    args_model=ResolveConceptArgs,
    scopes=(SCOPE_READ,),
    # A pool reconnect alone may wait up to ten seconds. Allow that recovery
    # within the turn budget; cold model/index setup belongs at CLI/API startup.
    timeout_s=20.0,
)
def resolve_concept(args: ResolveConceptArgs, ctx: ToolContext) -> Dict[str, Any]:
    from agents.concept_resolver import concept_resolver_node

    state = dict(ctx.state)
    state["query"] = args.query
    if args.content_type_hint:
        state["study_stage"] = args.content_type_hint

    result = concept_resolver_node(state)  # type: ignore[arg-type]
    if result.get("error"):
        return {"error": result["error"], "in_scope": None}

    concept_id = result.get("primary_concept_id") or ""
    ctx.stage(
        primary_concept_id=concept_id,
        secondary_concept_ids=result.get("secondary_concept_ids") or [],
        content_type_requested=result.get("content_type_requested") or "theory",
    )
    ctx.state.update(ctx.updates)

    if not concept_id:
        return {
            "in_scope": False,
            "note": (
                "This does not match any concept in the syllabus you cover. Tell "
                "the student that plainly, offer what you can, and do not present "
                "a guess as course material."
            ),
        }

    return {
        "in_scope": True,
        "concept_id": concept_id,
        "related_concept_ids": result.get("secondary_concept_ids") or [],
        "content_type": result.get("content_type_requested") or "theory",
    }


@tool(
    name="retrieve_content",
    description=(
        "Fetch syllabus material (theory, worked examples, practice, pyqs) for a concept. "
        "Returns previews and citations to ground explanations and practice. Full text is attached for composers."
    ),
    args_model=RetrieveContentArgs,
    scopes=(SCOPE_READ,),
    max_result_chars=6000,
    timeout_s=10.0,
)
def retrieve_content(args: RetrieveContentArgs, ctx: ToolContext) -> Dict[str, Any]:
    from retrieval.bridge import retrieve_for_state

    concept_id = args.concept_id or ctx.state.get("primary_concept_id") or ""
    if not concept_id:
        return {
            "error": (
                "No concept to retrieve for. Call resolve_concept first, or pass "
                "concept_id explicitly."
            )
        }

    state = dict(ctx.state)
    state["primary_concept_id"] = concept_id
    state["content_type_requested"] = args.content_type
    if args.query:
        state["query"] = args.query
    # This tool is an explicit agent decision — never let the turn-level
    # skip-retrieval heuristic silently cancel a call the agent chose to make.
    state["_force_retrieval"] = True
    if args.avoid_repeats is not None:
        state["_exclude_seen"] = bool(args.avoid_repeats)
    if args.exam and args.exam.upper() in {"JEE_ADVANCED", "JEE_MAINS"}:
        state["target_exam"] = args.exam.upper()

    result = retrieve_for_state(dict(state))
    if result.get("error"):
        return {"error": result["error"]}

    chunks: List[Dict[str, Any]] = result.get("retrieved_chunks") or []
    confidence = result.get("retrieval_confidence") or "ok"

    # Full chunks live in state for the composing expert; the model sees previews.
    ctx.stage(
        retrieved_chunks=chunks,
        retrieval_confidence=confidence,
        prereq_concept_states=result.get("prereq_concept_states") or [],
        secondary_concept_states=result.get("secondary_concept_states") or [],
        target_concept_state=result.get("target_concept_state"),
        primary_concept_id=concept_id,
        content_type_requested=args.content_type,
        use_socratic=bool(result.get("use_socratic")),
        socratic_turn_count=result.get("socratic_turn_count") or 0,
        teaching_mode=result.get("teaching_mode"),
        difficulty_target=result.get("difficulty_target"),
        framing=result.get("framing"),
    )
    ctx.state.update(ctx.updates)

    target = result.get("target_concept_state") or {}
    weak_prereqs = [
        {
            "concept_id": p.get("concept_id"),
            "concept_name": p.get("concept_name"),
            "proficiency": round(float(p.get("proficiency_score") or 0.5), 2),
        }
        for p in (result.get("prereq_concept_states") or [])
        if float(p.get("proficiency_score") or 0.5) < 0.5
    ][:4]

    payload: Dict[str, Any] = {
        "concept_id": concept_id,
        "content_type": args.content_type,
        "retrieval_confidence": confidence,
        "chunks_found": len(chunks),
        "chunks": [_chunk_preview(c) for c in chunks[:5]],
        "sources": list(dict.fromkeys(result.get("retrieval_citations") or []))[:5],
        "student_proficiency_on_concept": (
            round(float(target.get("proficiency_score")), 2)
            if target.get("proficiency_score") is not None
            else None
        ),
        "concept_is_new_to_student": bool(target.get("is_cold_start", True)) if target else True,
        "weak_prerequisites": weak_prereqs,
    }

    # The personalisation heuristic used to *decide* how to teach, by threshold,
    # with no way to disagree with it. Now it advises: the agent gets the
    # recommendation and the numbers behind it, and weighs them against
    # everything else it knows about this turn.
    suggestion = _teaching_suggestion(result)
    if suggestion:
        payload["teaching_signal"] = suggestion

    novelty = result.get("retrieval_novelty") or {}
    if novelty.get("enforced"):
        payload["novelty"] = {
            "already_seen_filtered_out": novelty.get("filtered_already_seen", 0),
            "pool_exhausted": bool(novelty.get("pool_exhausted")),
        }
    # Notes the retrieval layer wants honoured verbatim — out-of-syllabus, weak
    # grounding, exhausted question pool. They are instructions about honesty, not
    # decoration; the payload carries them so the composing model sees them.
    for note in result.get("retrieval_notes") or []:
        payload.setdefault("guidance", []).append(note)

    if confidence == "low":
        payload["note"] = (
            "Retrieval confidence is low — the material found may not really match "
            "the question. Lean on it lightly and tell the student where you are "
            "less sure."
        )
    elif confidence == "unresolved":
        payload["note"] = (
            "Nothing in the syllabus matched. Do not present an answer as though "
            "it came from the course material."
        )
    elif not chunks:
        payload["note"] = (
            "No material came back for this concept and content type. Try a "
            "different content_type, or answer from your own knowledge while "
            "telling the student it is not drawn from their course material."
        )
    return payload


class OpenMaterialArgs(BaseModel):
    content_id: str = Field(
        ...,
        description=(
            "The content_id to open, taken from a suggestion you previously "
            "offered or from a retrieve_content result."
        ),
    )


@tool(
    name="open_material",
    description=(
        "Open one specific piece of material by its content_id and load it as the "
        "grounding for this turn. Use it when the student picks something you "
        "offered them — 'the second one', 'yes show me that' — so they get the "
        "item they actually chose rather than a fresh search."
    ),
    args_model=OpenMaterialArgs,
    scopes=(SCOPE_READ,),
)
def open_material(args: OpenMaterialArgs, ctx: ToolContext) -> Dict[str, Any]:
    from db.supabase_client import db

    content_id = (args.content_id or "").strip()
    if not content_id:
        return {"error": "content_id is required."}

    rows = db.read_content_by_ids([content_id]) or []
    if not rows:
        offered = [
            s.get("content_id")
            for s in (ctx.state.get("follow_up_suggestions") or [])
            if s.get("content_id")
        ]
        return {
            "error": (
                f"No material with content_id '{content_id}'."
                + (f" You offered: {', '.join(offered)}." if offered else "")
            )
        }

    chunk = rows[0]
    concept_id = chunk.get("primary_concept_id") or ctx.state.get("primary_concept_id") or ""
    content_type = chunk.get("content_type") or "theory"

    seen = list(ctx.state.get("seen_follow_up_ids") or [])
    if content_id not in seen:
        seen.append(content_id)

    ctx.stage(
        retrieved_chunks=[chunk],
        retrieval_confidence="ok",
        primary_concept_id=concept_id,
        content_type_requested=content_type,
        seen_follow_up_ids=seen,
    )
    ctx.state.update(ctx.updates)

    return {
        "opened": content_id,
        "content_type": content_type,
        "concept_id": concept_id,
        "preview": _chunk_preview(chunk)["preview"],
        "next_step": (
            "This is now the grounding material for the turn. Present it to the "
            "student — hand off to a composer if it needs working through."
        ),
    }
