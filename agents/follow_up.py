"""
Follow-Up Agent

Fires in three distinct situations:

1. Normal follow-up (after Solver/Socratic resolved a doubt):
   Pure filtering/selection — NO LLM.  Surfaces 2-3 unseen, appropriately
   difficult next problems from Neo4j + Supabase.

2. Incorrect graded response (student typed "0" after practice/PYQ):
   Identifies the weakest prerequisite from prereq_concept_states
   (sorted by hop_count ASC, proficiency_score ASC).
   Surfaces it to the student and sets pending_prereq_concept_id in state.

3. follow_up_response intent (student selected a suggestion):
   Fetches the selected content from Supabase and returns it as a response.
   If the selected content is practice/PYQ, attaches the graded-reply prompt.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import config
from db.supabase_client import db
from db.neo4j_client import graph_db
from models.state import TutorState
# Image-URL construction (incl. comma-separated image_filename handling) lives
# in solver.py so Solver, Follow-Up and Quiz stay in lock-step.
from agents.solver import _render_math_text, _image_urls

# Reuse the same separator / reply prompt that Solver uses
_SEPARATOR = (
    "\n\n"
    "─────────────── ATTEMPT THE QUESTION ABOVE BEFORE SCROLLING ───────────────"
    "\n\n"
)
_GRADED_REPLY_PROMPT = (
    "\n\n---\n"
    "Reply **1** if your answer was correct, **0** if it was incorrect."
)

# ── Content-type normaliser ────────────────────────────────────────────────────
# Supabase stores content_type in its raw ingested form ("PYQ", "Practice
# Question", "Solved Example", etc.).  All internal logic uses lowercase
# canonical names: "pyq" | "practice" | "example" | "theory".
_CTYPE_ALIASES: Dict[str, str] = {
    "pyq": "pyq",
    "past year question": "pyq",
    "past year paper": "pyq",
    "practice": "practice",
    "practice question": "practice",
    "example": "example",
    "solved example": "example",
    "worked example": "example",
    "theory": "theory",
}

def _normalize_ctype(raw: str) -> str:
    """Return a canonical lowercase content_type from any raw DB value."""
    key = (raw or "").lower().strip()
    return _CTYPE_ALIASES.get(key, key)


# ── Helper: format a practice/PYQ content item ────────────────────────────────

def _extract_chunk_formulae(c: Dict[str, Any]) -> Optional[str]:
    """
    Return KaTeX-ready block-math string for formulae in a single chunk,
    or None if the chunk has no formulae.  Each expression is wrapped in
    $$...$$ so react-markdown + rehype-katex renders it correctly.
    """
    exprs: List[str] = []
    import re as _re
    seen: set = set()
    for field in ("latex_formulae_core", "latex_formulae_solution"):
        raw = (c.get(field) or "").strip()
        # Split on pipe, literal "\n", and real newlines; strip stray bullets.
        for expr in _re.split(r"\|+|\\n|\n", raw):
            e = expr.strip().lstrip("•-*").strip()
            if e and e not in seen:
                seen.add(e)
                exprs.append(e)
    if not exprs:
        return None
    return "\n\n".join(f"$$\n{e}\n$$" for e in exprs)


def _format_practice_item(c: Dict[str, Any]) -> str:
    question = (c.get("core_text") or "").strip()
    solution = (c.get("solution") or "").strip()
    diff = c.get("difficulty")
    diff_tag = f" (difficulty {diff}/5)" if diff else ""
    imgs = _image_urls(c)
    formulae_block = _extract_chunk_formulae(c)

    parts: List[str] = []
    parts.append(question if question else "*(Question text not available.)*")
    if imgs["core"]:
        parts.append(f"\n\n![Diagram]({imgs['core']})")
    parts.append(_SEPARATOR)
    if solution:
        parts.append(f"**Solution{diff_tag}:**\n\n{_render_math_text(solution)}")
    else:
        parts.append("*(Solution not available.)*")
    if imgs["solution"]:
        parts.append(f"\n\n![Solution diagram]({imgs['solution']})")
    if formulae_block:
        parts.append(f"\n\n**Key Formulae:**\n\n{formulae_block}")
    parts.append(_GRADED_REPLY_PROMPT)
    return "".join(parts)


# ── Helper: surface the weakest prerequisite ─────────────────────────────────

def _surface_weak_prereq(state: TutorState) -> Optional[Dict[str, Any]]:
    """
    After an incorrect graded attempt, find the weakest prerequisite concept
    and surface it to the student.

    Priority:
      1. hop_count ASC   (direct prereqs first)
      2. proficiency_score ASC  (lower score = more in need)

    Returns a state-update dict or None if no weak prereq found.
    """
    prereqs: List[Dict[str, Any]] = state.get("prereq_concept_states") or []
    if not prereqs:
        return None

    weak = [
        p for p in prereqs
        if p.get("flagged_weak") or p.get("proficiency_score", 0.5) < config.WEAK_THRESHOLD
    ]
    if not weak:
        return None

    # Sort: hop_count ASC, proficiency_score ASC
    weak.sort(key=lambda p: (p.get("hop_count", 1), p.get("proficiency_score", 0.5)))
    top = weak[0]

    concept_name = top.get("concept_name") or top.get("concept_id", "that concept")
    concept_id = top.get("concept_id", "")

    message = (
        f"Don't worry — it happens! It looks like **{concept_name}** "
        f"might be a gap that's making this harder.\n\n"
        f"Would you like to cover **{concept_name}** first before trying again? "
        f"Reply **yes** to start, or just ask your next question to continue."
    )

    db.insert_chat_turn(
        session_id=state["session_id"],
        role="assistant",
        content=message,
        turn_index=(state.get("interaction_count", 1)) * 2,
        agent_used="follow_up",
        concepts_referenced=[concept_id] if concept_id else None,
    )

    return {
        "response": message,
        "agent_used": "follow_up",
        "pending_prereq_concept_id": concept_id,
        "follow_up_suggestions": [],
        "error": None,
    }


# ── Helper: handle follow_up_response (student selected a suggestion) ─────────

def _parse_selection_index(query: str) -> int:
    """Return 0-based index of the student's suggestion choice (default 0)."""
    q = query.lower()
    if any(w in q for w in ["2", "second", "two"]):
        return 1
    if any(w in q for w in ["3", "third", "three"]):
        return 2
    return 0  # default / "1" / "first" / "yes" / "okay"


def _handle_followup_selection(state: TutorState) -> Dict[str, Any]:
    """
    The student replied to the follow-up suggestions.  Fetch and return the
    selected content item so they actually see it.
    """
    suggestions: List[Dict[str, Any]] = state.get("follow_up_suggestions") or []
    query = state.get("query", "")

    # "skip", "next", "no" → just acknowledge
    skip_words = {"skip", "next", "no", "later", "pass", "nevermind"}
    if any(w in query.lower() for w in skip_words) or not suggestions:
        ack = "No problem! Ask me your next question whenever you're ready."
        db.insert_chat_turn(
            session_id=state["session_id"],
            role="assistant",
            content=ack,
            turn_index=(state.get("interaction_count", 1)) * 2,
            agent_used="follow_up",
        )
        return {
            "response": ack,
            "agent_used": "follow_up",
            "follow_up_suggestions": [],
            "awaiting_graded_response": False,
            "error": None,
        }

    idx = _parse_selection_index(query)
    idx = min(idx, len(suggestions) - 1)
    selected = suggestions[idx]

    content_id = selected.get("content_id")
    primary_concept_id = ""

    # Proficiency states for the chunk's prereq and secondary concepts.
    # These are returned so main.py can carry them into the next graded turn,
    # giving Diagnostic the same data it would have after a GraphRAG retrieval.
    chunk_prereq_states: List[Dict[str, Any]] = []
    chunk_secondary_states: List[Dict[str, Any]] = []
    chunk_target_state: Optional[Dict[str, Any]] = None

    if content_id:
        rows = db.read_content_by_ids([content_id])
        if rows:
            c = rows[0]
            primary_concept_id = c.get("primary_concept_id", "")

            # The suggestion's content_type was already normalised at generation
            # time from the same Supabase row.  Use it as the authoritative
            # source so a DB label mismatch ("Solved Example" vs "Practice
            # Question") doesn't silently skip the practice format.
            suggestion_ctype = _normalize_ctype(selected.get("content_type", ""))
            row_ctype = _normalize_ctype(c.get("content_type", ""))
            # Prefer the suggestion label when it explicitly says practice/pyq;
            # otherwise fall back to the row label.
            ctype = suggestion_ctype if suggestion_ctype in {"practice", "pyq"} else row_ctype

            if ctype in {"practice", "pyq"}:
                response = _format_practice_item(c)
            else:
                text = (c.get("core_text") or "").strip()
                response = f"Here's what we found:\n\n{text}" if text else selected.get("preview", "")
                imgs = _image_urls(c)
                if imgs["core"]:
                    response += f"\n\n![Diagram]({imgs['core']})"

            # ── Build proficiency states from chunk metadata ───────────────
            # This mirrors what GraphRAG Retriever does, so Diagnostic has
            # full data even when no retrieval pass ran for this concept.
            user_id = state.get("user_id", "")
            all_states = db.read_all_user_concept_states(user_id)
            state_map = {s["concept_id"]: s for s in all_states}
            concepts_rows = db.read_concepts()
            name_map = {row["concept_id"]: row["concept_name"] for row in concepts_rows}

            # Target concept state
            if primary_concept_id:
                chunk_target_state = state_map.get(primary_concept_id)

            # Prereq concepts from chunk
            prereq_ids = c.get("prerequisite_concepts") or []
            if isinstance(prereq_ids, list):
                for cid in prereq_ids:
                    s = state_map.get(cid)
                    chunk_prereq_states.append({
                        "concept_id": cid,
                        "concept_name": name_map.get(cid, cid),
                        "proficiency_score": s.get("proficiency_score", 0.5) if s else 0.5,
                        "flagged_weak":      s.get("flagged_weak",      False) if s else False,
                        "is_cold_start":     s.get("is_cold_start",     True)  if s else True,
                        "hop_count": 1,
                    })

            # Secondary concepts from chunk
            secondary_ids = c.get("secondary_concepts") or []
            if isinstance(secondary_ids, list):
                for cid in secondary_ids:
                    s = state_map.get(cid)
                    chunk_secondary_states.append({
                        "concept_id": cid,
                        "concept_name": name_map.get(cid, cid),
                        "proficiency_score": s.get("proficiency_score", 0.5) if s else 0.5,
                        "flagged_weak":      s.get("flagged_weak",      False) if s else False,
                        "is_cold_start":     s.get("is_cold_start",     True)  if s else True,
                    })
        else:
            response = "The selected content could not be loaded. Ask me another question!"
            ctype = ""
    else:
        response = "Got it! Ask me your next question."
        ctype = ""

    db.insert_chat_turn(
        session_id=state["session_id"],
        role="assistant",
        content=response,
        turn_index=(state.get("interaction_count", 1)) * 2,
        agent_used="follow_up",
        concepts_referenced=[primary_concept_id] if primary_concept_id else None,
    )

    # Determine whether the served content needs a graded response
    served_practice = ctype in {"practice", "pyq"}

    # Only the actually-opened item is excluded from future rounds.
    session_seen: List[str] = state.get("seen_follow_up_ids") or []
    new_seen: List[str] = list(set(session_seen) | {content_id}) if content_id else list(session_seen)

    result: Dict[str, Any] = {
        "response": response,
        "agent_used": "follow_up",
        "follow_up_suggestions": [],
        # Signal to main.py whether an unanswered practice/PYQ is now pending
        "awaiting_graded_response": served_practice,
        # Append only the clicked content_id, not all three suggestions
        "seen_follow_up_ids": new_seen,
        # Proficiency data so Diagnostic has context on the next graded turn
        "prereq_concept_states":    chunk_prereq_states,
        "secondary_concept_states": chunk_secondary_states,
        "error": None,
    }
    # Carry forward so main.py can set up carry state for the next graded turn
    if primary_concept_id:
        result["primary_concept_id"] = primary_concept_id
    if ctype:
        result["content_type_requested"] = ctype
    if chunk_target_state is not None:
        result["target_concept_state"] = chunk_target_state
    return result


# ── Helper: advance an on-demand quiz batch after each graded answer ──────────

def _handle_quiz_advance(state: TutorState) -> Dict[str, Any]:
    """
    Runs after Diagnostic grades one question of an on-demand quiz batch.
    Advances to the next question, or finishes the quiz when all are done.
    Proficiency was already updated by Diagnostic; this only drives the prompt.
    """
    chunks: List[Dict[str, Any]] = state.get("quiz_batch_chunks") or []
    current_index = state.get("quiz_batch_index", 0)
    next_index = current_index + 1
    total = len(chunks)

    if next_index < total:
        msg = (
            f"Recorded. Now reply **1** if your answer to **Question {next_index + 1}** "
            f"was correct, **0** if it was incorrect."
        )
        db.insert_chat_turn(
            session_id=state["session_id"],
            role="assistant",
            content=msg,
            turn_index=(state.get("interaction_count", 1)) * 2,
            agent_used="quiz",
        )
        return {
            "response": msg,
            "agent_used": "quiz",
            "quiz_batch_mode": True,
            "quiz_batch_index": next_index,
            "awaiting_graded_response": True,
            "error": None,
        }

    # All questions graded — close out the batch.
    msg = (
        "That completes the quiz — your proficiency scores have been updated for "
        "each question. Ask me anything next, or request another quiz!"
    )
    db.insert_chat_turn(
        session_id=state["session_id"],
        role="assistant",
        content=msg,
        turn_index=(state.get("interaction_count", 1)) * 2,
        agent_used="quiz",
    )
    return {
        "response": msg,
        "agent_used": "quiz",
        "quiz_batch_mode": False,
        "quiz_batch_index": 0,
        "quiz_batch_chunks": [],
        "awaiting_graded_response": False,
        "follow_up_suggestions": [],
        "error": None,
    }


# ── Main node ─────────────────────────────────────────────────────────────────

def follow_up_node(state: TutorState) -> Dict[str, Any]:
    intent = state.get("intent", "")

    # ── Case 0: on-demand quiz batch in progress → advance to next question ────
    if state.get("quiz_batch_mode") and state.get("has_graded_outcome"):
        try:
            return _handle_quiz_advance(state)
        except Exception as exc:
            return {"error": f"Follow-Up Agent failed (quiz advance): {exc}"}

    # ── Case 1: student selected a follow-up suggestion ───────────────────────
    if intent == "follow_up_response":
        try:
            return _handle_followup_selection(state)
        except Exception as exc:
            return {"error": f"Follow-Up Agent failed (selection): {exc}"}

    # ── Case 2: incorrect graded attempt → surface weak prereq ───────────────
    if state.get("has_graded_outcome") and not state.get("is_correct", True):
        try:
            result = _surface_weak_prereq(state)
            if result:
                return result
            # No weak prereqs found — fall through to normal suggestions
        except Exception as exc:
            return {"error": f"Follow-Up Agent failed (prereq surfacing): {exc}"}

    # ── Case 3: normal follow-up suggestions ─────────────────────────────────
    user_id = state["user_id"]
    primary_id = state.get("primary_concept_id", "")
    target_exam = state.get("target_exam", "JEE_ADVANCED")

    try:
        if not primary_id:
            return {"follow_up_suggestions": []}

        # Brief acknowledgement when the student just answered correctly
        ack_response: Optional[str] = None
        if state.get("has_graded_outcome") and state.get("is_correct"):
            ack_response = "Well done! Here are some suggestions for what to try next:"

        # 1. Get content IDs already seen by this user (all-time deduplication)
        #    Also exclude any IDs already shown as follow-up suggestions in this
        #    session so the student never sees the same card twice.
        seen_ids = db.read_seen_content_ids(user_id)
        session_seen: List[str] = state.get("seen_follow_up_ids") or []
        all_excluded = set(seen_ids) | set(session_seen)

        # 2. Difficulty ceiling: match or slightly exceed the student's proficiency
        target_state = state.get("target_concept_state") or {}
        proficiency = target_state.get("proficiency_score", 0.5)
        difficulty_max = max(1, min(4, int(proficiency * 4) + 1))

        # 3. Neo4j: find unseen, appropriately-difficult content for the concept
        candidate_ids = graph_db.get_content_ids_for_follow_up(
            concept_id=primary_id,
            seen_ids=list(all_excluded),
            difficulty_max=difficulty_max,
            limit=10,
        )

        if not candidate_ids:
            candidate_ids = graph_db.get_content_ids_for_follow_up(
                concept_id=primary_id,
                seen_ids=list(all_excluded),
                difficulty_max=5,
                limit=10,
            )

        # 4. Supabase: fetch full metadata for candidates
        candidates = db.read_content_by_ids(candidate_ids[:6])

        # 5. Pick 2-3: prefer practice/pyq content for follow-up
        TYPE_PRIORITY = {"practice": 0, "pyq": 1, "example": 2, "theory": 3}
        candidates.sort(
            key=lambda c: (
                TYPE_PRIORITY.get(c.get("content_type", "theory"), 4),
                c.get("difficulty") or 3,
            )
        )
        suggestions = []
        for c in candidates[:3]:
            # Preserve every field the DB returns so downstream agents and the
            # frontend never lose image, latex, or solution data.
            suggestion = dict(c)
            suggestion["content_type"] = _normalize_ctype(c.get("content_type", ""))
            suggestion["preview"]      = (c.get("core_text") or "")[:150]
            suggestions.append(suggestion)

        # Do NOT add suggestion IDs to seen_follow_up_ids yet — only the item
        # the student actually clicks should be excluded from future rounds.
        # seen_follow_up_ids is updated in _handle_followup_selection instead.
        result: Dict[str, Any] = {
            "follow_up_suggestions": suggestions,
            # The graded cycle is complete — clear the awaiting flag so the
            # frontend no longer shows "Reply 1/0" after this turn.
            "awaiting_graded_response": False,
            "error": None,
        }
        if ack_response:
            result["response"] = ack_response
            result["agent_used"] = "follow_up"

        return result

    except Exception as exc:
        return {"error": f"Follow-Up Agent failed: {exc}"}
