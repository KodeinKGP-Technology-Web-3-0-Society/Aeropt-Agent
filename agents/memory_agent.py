"""Memory Agent: deterministic episodic recall and commit.

This module owns episodic memory. tutor_graph and study_graph call
memory_recall_node and memory_commit_node. Neither node calls a model.
Background consolidation lives in agents/memory_consolidate.py and is not
on the request path.

Every public function fails open. A missing table or a failed embed must not
change the tutoring turn.
"""

from __future__ import annotations

import ast
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from db.supabase_client import db


logger = logging.getLogger("jee_tutor.memory_agent")

_HEADING = "Past experiences (this student, not course material):"
_MAX_ACTIONS = 12
_MAX_EXCERPT = 300
_MAX_APPROACH = 180
_MAX_RETRIEVAL = 800
_MAX_LINE = 280
_MAX_BLOCK = 900
_MAX_RECALL = 3
_COSINE_FLOOR = 0.25
_RECENCY_DAYS = 14.0
_GRADED_ACTIONS = ["node:diagnostic", "node:follow_up"]
_CLASSROOM_SURFACES = {"lesson", "doubt", "assess"}

_SKIP_REPLIES: Optional[Tuple[str, ...]] = None


def memory_recall_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``{"episode_context": block}`` or ``{}``. Never sets ``error``."""
    try:
        if not isinstance(state, dict):
            return {}
        episodes = retrieve_relevant_episodes(
            str(state.get("user_id") or ""),
            str(state.get("query") or ""),
            _concept_from_state(state),
        )
        block = format_episode_context(episodes)
        if not block:
            return {}
        return {"episode_context": block}
    except Exception:
        logger.debug("memory recall failed", exc_info=True)
        return {}


def memory_commit_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Close an older episode when this turn is a grade, then upsert this turn.

    Returns ``{}``. Nothing is written back onto graph state.
    """
    try:
        if not isinstance(state, dict):
            return {}
        if _grade_view(state) is not None:
            apply_delayed_feedback(state)
        row = build_episode(state, kind=_kind_from_state(state))
        if not row:
            return {}
        vector, model = _embed_text(row.get("retrieval_text") or "")
        row["embedding"] = vector
        row["embed_model"] = model
        db.upsert_episode(row)
    except Exception:
        logger.debug("memory commit failed", exc_info=True)
    return {}


def format_episode_context(episodes: Sequence[Dict[str, Any]]) -> str:
    """Prompt block of at most three episodes, or ``""``."""
    try:
        if not episodes:
            return ""
        lines: List[str] = []
        for episode in list(episodes)[:_MAX_RECALL]:
            if not isinstance(episode, dict):
                continue
            line = _episode_line(episode)
            if len(line) > _MAX_LINE:
                line = line[:_MAX_LINE]
            lines.append(line)
        if not lines:
            return ""
        block = _HEADING + "\n" + "\n".join(lines)
        while len(block) > _MAX_BLOCK and lines:
            lines.pop()
            block = (_HEADING + "\n" + "\n".join(lines)) if lines else ""
        if not lines:
            return ""
        if len(block) > _MAX_BLOCK:
            block = block[:_MAX_BLOCK]
        return block
    except Exception:
        logger.debug("episode format failed", exc_info=True)
        return ""


def build_episode(state: Dict[str, Any], *, kind: str) -> Optional[Dict[str, Any]]:
    """One upsert payload, or None when this turn must not be remembered."""
    try:
        return _build_episode(state, kind)
    except Exception:
        logger.debug("episode build failed", exc_info=True)
        return None


def retrieve_relevant_episodes(
    user_id: str,
    query: str,
    concept_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """At most three episodes for this user, highest score first."""
    try:
        if not user_id:
            return []
        candidates = db.fetch_episode_candidates(user_id, concept_id or None) or []
        if not candidates:
            return []
        query_vec, query_model = _embed_text(query or "")
        ranked: List[Tuple[float, Dict[str, Any]]] = []
        for row in candidates:
            if not isinstance(row, dict):
                continue
            cosine = _cosine_for(row, query_vec, query_model)
            matched = _concepts_match(row.get("concept_id"), concept_id)
            # Numeric cosine only. None means the comparison was skipped
            # (query embed failed, incompatible model, or a length mismatch).
            if cosine is not None and cosine < _COSINE_FLOOR and not matched:
                continue
            ranked.append((_score_episode(row, cosine, matched), row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        improved_at: Dict[Any, str] = {}
        for row in candidates:
            if isinstance(row, dict) and _outcome_label(row) == "correct":
                stamp = str(row.get("created_at") or "")
                concept = row.get("concept_id")
                if stamp > improved_at.get(concept, ""):
                    improved_at[concept] = stamp
        kept: List[Dict[str, Any]] = []
        for row in (item[1] for item in ranked):
            concept = row.get("concept_id")
            stale_failure = (
                _outcome_label(row) == "incorrect"
                and str(row.get("created_at") or "") < improved_at.get(concept, "")
            )
            if stale_failure:
                continue
            kept.append(row)
            if len(kept) >= _MAX_RECALL:
                break
        return kept
    except Exception:
        logger.debug("episode retrieval failed", exc_info=True)
        return []


def apply_delayed_feedback(state: Dict[str, Any]) -> None:
    """Ask the client to close one older pending episode. No-op without a grade."""
    try:
        if not isinstance(state, dict):
            return
        grade = _grade_view(state)
        turn = _assistant_turn_index(state.get("interaction_count"))
        user_id = state.get("user_id")
        if grade is None or turn is None or not user_id or not grade.get("concept_id"):
            return
        if not isinstance(grade.get("is_correct"), bool):
            return
        episode_id = db.close_pending_episode(
            user_id=str(user_id),
            concept_id=str(grade["concept_id"]),
            exclude_turn_index=turn,
            content_id=grade.get("content_id"),
            interaction_id=grade.get("interaction_id"),
            is_correct=grade.get("is_correct"),
            attempt_id=grade.get("attempt_id"),
            proficiency_before=grade.get("proficiency_before"),
            proficiency_after=grade.get("proficiency_after"),
        )
        if episode_id:
            _refresh_closed_retrieval(str(user_id), str(episode_id))
    except Exception:
        logger.debug("delayed feedback failed", exc_info=True)


def invalidate_session(session_id: str) -> None:
    try:
        db.invalidate_session(session_id)
    except Exception:
        logger.debug("episode session invalidation failed", exc_info=True)


def invalidate_turn(session_id: str, assistant_turn_index: int) -> None:
    try:
        db.invalidate_turn(session_id, assistant_turn_index)
    except Exception:
        logger.debug("episode turn invalidation failed", exc_info=True)


def _build_episode(state: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    if kind not in ("chat", "graded", "study"):
        return None
    if not isinstance(state, dict):
        return None
    if state.get("cancelled"):
        return None
    user_id = state.get("user_id")
    session_id = state.get("session_id")
    if not user_id or not session_id:
        return None
    turn = _assistant_turn_index(state.get("interaction_count"))
    if turn is None:
        return None
    response = state.get("response")
    if not isinstance(response, str) or not response.strip():
        return None
    if response.strip() in _skip_replies():
        return None

    surface, route, task_type, outcome, born_closed = _mapping(state, kind)
    if state.get("attempt_record_failed"):
        outcome = "unknown"
        born_closed = False
        assessment_status = "persistence_failed"
    elif outcome == "unknown" and (kind == "graded" or state.get("has_graded_outcome")):
        assessment_status = "failed"
    else:
        assessment_status = "not_requested"
    concept_id = _concept_from_state(state)
    grade = state.get("last_grade") if isinstance(state.get("last_grade"), dict) else None
    if kind == "study" and grade and grade.get("concept_id"):
        concept_id = grade.get("concept_id") or concept_id
    content_id = _content_id(state)
    if kind == "study" and grade and grade.get("content_id"):
        content_id = grade.get("content_id")
    attempt_id = state.get("attempt_id")
    if kind == "study" and grade:
        attempt_id = grade.get("attempt_id") or attempt_id

    actions = _actions(state, kind)
    excerpt = _clip(str(state.get("query") or "").strip(), _MAX_EXCERPT)
    approach = _approach_from_response(response)
    student_response = str(state.get("student_answer") or excerpt or "").strip()
    interaction_id = _interaction_id(str(user_id), concept_id, content_id, str(session_id))
    feedback_outcome = None
    evidence_source = "dialogue" if excerpt else "none"
    row: Dict[str, Any] = {
        "user_id": str(user_id),
        "session_id": str(session_id),
        "assistant_turn_index": turn,
        "surface": surface,
        "route": route,
        "task_type": task_type,
        "observed_outcome": outcome,
        "query_excerpt": excerpt,
        "approach": approach,
        "student_response": _clip(student_response, _MAX_EXCERPT),
        "interaction_id": interaction_id,
        "assessment_status": assessment_status,
        "evidence_source": evidence_source,
        "actions": actions,
        "expert_used": _expert_used(actions),
        "checker_verdict": state.get("checker_verdict"),
        "concept_id": concept_id,
        "content_id": content_id,
    }
    if state.get("study_session_id"):
        row["study_session_id"] = state.get("study_session_id")
    if attempt_id:
        row["attempt_id"] = attempt_id
    if born_closed:
        correct = _as_bool(_grade_flag(state, kind))
        if correct is None:
            outcome = "unknown"
            row["observed_outcome"] = outcome
            row["assessment_status"] = "failed"
            born_closed = False
        else:
            feedback_outcome = "correct" if correct else "incorrect"
            outcome = feedback_outcome
            row["observed_outcome"] = outcome
            row["feedback_status"] = "next_attempt"
            row["feedback_outcome"] = feedback_outcome
            row["assessment_status"] = "completed" if attempt_id else "persistence_failed"
            row["evidence_source"] = str(state.get("evidence_source") or "student_self_report")
            if attempt_id:
                row["feedback_attempt_id"] = attempt_id
            before, after = _proficiency(state, kind)
            if before is not None:
                row["proficiency_before"] = before
            if after is not None:
                row["proficiency_after"] = after
    row["retrieval_text"] = _build_retrieval_text(
        surface=surface,
        concept_id=concept_id,
        task_type=task_type,
        observed_outcome=outcome,
        feedback_outcome=feedback_outcome,
        actions=actions,
        query_excerpt=excerpt,
        approach=approach,
    )
    return row


def _mapping(state: Dict[str, Any], kind: str) -> Tuple[str, str, str, str, bool]:
    """surface, route, task_type, observed_outcome, born_closed."""
    if kind == "graded":
        outcome = _graded_outcome(state.get("is_correct"))
        return "graded", "graded_fast_path", "grade", outcome, outcome != "unknown"
    if kind == "study":
        grade = state.get("last_grade") if isinstance(state.get("last_grade"), dict) else None
        if grade:
            outcome = _graded_outcome(grade.get("is_correct"))
            return "study", "teacher", "grade", outcome, outcome != "unknown"
        return "study", "teacher", "study_phase", "unknown", False

    classroom = state.get("classroom_surface")
    surface = classroom if classroom in _CLASSROOM_SURFACES else "chat"
    grade_like = bool(state.get("has_graded_outcome"))
    if grade_like:
        task = "grade"
        outcome = _graded_outcome(state.get("is_correct"))
        return surface, "agent_loop", task, outcome, outcome != "unknown"
    requested = state.get("content_type_requested")
    if requested in ("theory", "example") or surface == "lesson":
        task = "explain"
    elif surface == "assess" or requested in ("practice", "pyq"):
        task = "practice"
    else:
        task = "doubt"
    return surface, "agent_loop", task, "unknown", False


def _as_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _graded_outcome(value: Any) -> str:
    correct = _as_bool(value)
    if correct is None:
        return "unknown"
    return "correct" if correct else "incorrect"


def _grade_flag(state: Dict[str, Any], kind: str) -> Any:
    if kind == "study":
        grade = state.get("last_grade")
        if isinstance(grade, dict):
            return grade.get("is_correct")
    return state.get("is_correct")


def _interaction_id(
    user_id: str,
    concept_id: Optional[str],
    content_id: Optional[str],
    session_id: str,
) -> Optional[str]:
    import hashlib

    if concept_id and content_id:
        raw = f"{user_id}|{concept_id}|{content_id}"
    elif concept_id:
        raw = f"{user_id}|{session_id}|{concept_id}"
    else:
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _outcome_label(row: Dict[str, Any]) -> str:
    feedback = row.get("feedback_outcome")
    if feedback in ("correct", "incorrect"):
        return str(feedback)
    observed = row.get("observed_outcome")
    if observed in ("correct", "incorrect"):
        return str(observed)
    return "unknown"


def _proficiency(state: Dict[str, Any], kind: str) -> Tuple[Optional[float], Optional[float]]:
    source = state.get("last_grade") if kind == "study" and isinstance(state.get("last_grade"), dict) else state
    if not isinstance(source, dict):
        return None, None
    return source.get("proficiency_before"), source.get("proficiency_after")


def _kind_from_state(state: Dict[str, Any]) -> str:
    if state.get("study_session_id") or state.get("current_phase") or isinstance(state.get("last_grade"), dict):
        return "study"
    if state.get("has_graded_outcome"):
        return "graded"
    return "chat"


def _concept_from_state(state: Dict[str, Any]) -> Optional[str]:
    if not isinstance(state, dict):
        return None
    for key in ("primary_concept_id", "roadmap_concept_id", "concept_id"):
        value = state.get(key)
        if value:
            return str(value)
    grade = state.get("last_grade")
    if isinstance(grade, dict) and grade.get("concept_id"):
        return str(grade["concept_id"])
    return None


def _content_id(state: Dict[str, Any]) -> Optional[str]:
    chunks = state.get("retrieved_chunks") or []
    if chunks and isinstance(chunks[0], dict) and chunks[0].get("content_id"):
        return str(chunks[0]["content_id"])
    return None


def _actions(state: Dict[str, Any], kind: str) -> List[str]:
    path = state.get("agent_trace_path")
    actions = [str(item) for item in path[:_MAX_ACTIONS]] if isinstance(path, list) else []
    if actions:
        return actions
    if kind == "graded":
        return list(_GRADED_ACTIONS)
    if kind == "study":
        tools = state.get("tools_used")
        if isinstance(tools, list):
            return [str(item) for item in tools[:_MAX_ACTIONS]]
    return []


def _expert_used(actions: Sequence[str]) -> Optional[str]:
    for action in actions:
        if action.startswith("handoff:"):
            name = action.split(":", 1)[1].strip()
            return name or None
    return None


def _assistant_turn_index(interaction_count: Any) -> Optional[int]:
    count = _as_count(interaction_count)
    if count is None or count < 1:
        return None
    return count * 2


def _as_count(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _build_retrieval_text(
    *,
    surface: str,
    concept_id: Optional[str],
    task_type: str,
    observed_outcome: str,
    feedback_outcome: Optional[str],
    actions: Sequence[str],
    query_excerpt: str,
    approach: str = "",
) -> str:
    text = (
        f"surface={surface} concept={concept_id or 'unknown'} task={task_type}\n"
        f"outcome={observed_outcome} feedback={feedback_outcome or 'pending'}\n"
        f"actions={', '.join(actions)}\n"
        f"query={query_excerpt}"
    )
    if approach:
        text += f"\napproach={approach}"
    return _clip(text, _MAX_RETRIEVAL)


def _approach_from_response(response: str) -> str:
    """Short clip of the tutor's own reply. No model call."""
    text = " ".join(str(response or "").split())
    if len(text) <= _MAX_APPROACH:
        return text
    clipped = text[:_MAX_APPROACH]
    space = clipped.rfind(" ")
    if space >= 40:
        clipped = clipped[:space]
    return clipped.rstrip(".,;: ")


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def _refresh_closed_retrieval(user_id: str, episode_id: str) -> None:
    """Rewrite retrieval text after feedback, then re-embed it.

    Feedback columns are already stored. A failed embed keeps the previous
    vector. Nothing here is allowed to escape to the turn.
    """
    try:
        row = db.read_episode_retrieval_source(user_id, episode_id)
        if not isinstance(row, dict):
            return
        actions = row.get("actions") or []
        if not isinstance(actions, list):
            actions = []
        text = _build_retrieval_text(
            surface=str(row.get("surface") or "chat"),
            concept_id=row.get("concept_id"),
            task_type=str(row.get("task_type") or "doubt"),
            observed_outcome=str(row.get("observed_outcome") or "unknown"),
            feedback_outcome=row.get("feedback_outcome"),
            actions=[str(item) for item in actions],
            query_excerpt=str(row.get("query_excerpt") or ""),
            approach=str(row.get("approach") or ""),
        )
        vector, model = _embed_text(text)
        if vector is None:
            vector = row.get("embedding")
            model = row.get("embed_model")
        db.update_episode_retrieval(
            user_id=user_id,
            episode_id=episode_id,
            retrieval_text=text,
            embedding=vector,
            embed_model=model,
        )
    except Exception:
        logger.debug("episode retrieval refresh failed", exc_info=True)


def _embed_text(text: str) -> Tuple[Optional[List[float]], Optional[str]]:
    try:
        from retrieval import embedder

        vector = embedder.encode_one(text)
        if vector is None:
            return None, None
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        return [float(item) for item in vector], embedder.model_name()
    except Exception:
        logger.debug("episode embed failed", exc_info=True)
        return None, None


def _embedding_missing(value: Any) -> bool:
    if value is None:
        return True
    if hasattr(value, "tolist"):
        value = value.tolist()
    return isinstance(value, (list, tuple)) and len(value) == 0


def _cosine_for(
    row: Dict[str, Any],
    query_vec: Optional[Sequence[float]],
    query_model: Optional[str],
) -> Optional[float]:
    """Cosine, or None when the comparison must be skipped.

    A missing stored vector is 0.0 once the query itself embedded. A failed
    query embed, a different embed model, or a different vector length stays
    None so the caller does not drop the row for a comparison it could not make.
    """
    if not query_vec or not query_model:
        return None
    if _embedding_missing(row.get("embedding")):
        return 0.0
    if row.get("embed_model") != query_model:
        return None
    return _cosine_similarity(query_vec, row.get("embedding"))


def _cosine_similarity(left: Optional[Sequence[float]], right: Any) -> Optional[float]:
    if not left or right is None:
        return None
    if hasattr(right, "tolist"):
        right = right.tolist()
    if not isinstance(right, (list, tuple)) or len(left) != len(right):
        return None
    try:
        pairs = [(float(a), float(b)) for a, b in zip(left, right)]
    except (TypeError, ValueError):
        return None
    dot = sum(a * b for a, b in pairs)
    left_norm = math.sqrt(sum(a * a for a, _ in pairs))
    right_norm = math.sqrt(sum(b * b for _, b in pairs))
    if left_norm == 0 or right_norm == 0:
        return None
    return dot / (left_norm * right_norm)


def _concepts_match(episode_concept: Any, query_concept: Optional[str]) -> bool:
    return bool(query_concept) and episode_concept == query_concept


def _score_episode(row: Dict[str, Any], cosine: Optional[float], concept_match: bool) -> float:
    cosine_term = cosine if cosine is not None else 0.0
    return (
        0.50 * cosine_term
        + 0.25 * (1.0 if concept_match else 0.0)
        + 0.15 * _recency(row.get("created_at"))
        + 0.10 * _quality_score(row)
    )


def _recency(created_at: Any) -> float:
    created = _parse_time(created_at)
    if created is None:
        return 0.0
    age_days = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 86400.0)
    return math.exp(-age_days / _RECENCY_DAYS)


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _quality_score(row: Dict[str, Any]) -> float:
    feedback = row.get("feedback_outcome")
    observed = row.get("observed_outcome")
    if feedback == "incorrect" or observed == "incorrect":
        return 1.0
    if feedback == "correct" or observed == "correct":
        return 0.7
    if row.get("checker_verdict") == "revised":
        return 0.5
    if row.get("feedback_status") == "pending":
        return 0.2
    return 0.3


def _grade_view(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalized grade facts, or None when this turn is not a grade."""
    grade = state.get("last_grade")
    if isinstance(grade, dict):
        concept = grade.get("concept_id") or _concept_from_state(state)
        return {
            "concept_id": concept,
            "is_correct": grade.get("is_correct"),
            "attempt_id": grade.get("attempt_id"),
            "content_id": grade.get("content_id") or _content_id(state),
            "interaction_id": grade.get("interaction_id"),
            "proficiency_before": grade.get("proficiency_before"),
            "proficiency_after": grade.get("proficiency_after"),
        }
    if state.get("has_graded_outcome"):
        return {
            "concept_id": _concept_from_state(state),
            "is_correct": state.get("is_correct"),
            "attempt_id": state.get("attempt_id"),
            "content_id": _content_id(state),
            "interaction_id": _interaction_id(
                str(state.get("user_id") or ""),
                _concept_from_state(state),
                _content_id(state),
                str(state.get("session_id") or ""),
            ),
            "proficiency_before": state.get("proficiency_before"),
            "proficiency_after": state.get("proficiency_after"),
        }
    return None


def _episode_line(episode: Dict[str, Any]) -> str:
    concept = episode.get("concept_id") or "unknown"
    outcome = episode.get("feedback_outcome") or episode.get("observed_outcome") or "unknown"
    actions = episode.get("actions") or []
    if not isinstance(actions, list):
        actions = []
    action_text = ", ".join(str(item) for item in actions) or "none"
    excerpt = str(episode.get("query_excerpt") or "").strip()
    approach = str(episode.get("approach") or "").strip()
    line = (
        f"- {_when(episode.get('created_at'))} {concept}: {episode.get('task_type') or 'unknown'}, "
        f"outcome {outcome}"
    )
    if approach:
        line += f"; approach: {approach}"
    line += f"; actions: {action_text}. Query: {excerpt}"
    return line


def _when(created_at: Any) -> str:
    text = str(created_at or "")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return "earlier"


def _skip_replies() -> Tuple[str, ...]:
    """Tutor refusal strings, read from source so this module does not import it.

    ``agents.learning_agent`` builds an OpenAI client at import time. Importing
    it here is not a cycle, but it would construct that client and crash when
    no Gemini key is configured. The two replies are adjacent string literals,
    so the AST yields the same values the module binds.
    """
    global _SKIP_REPLIES
    if _SKIP_REPLIES is not None:
        return _SKIP_REPLIES
    try:
        source = (Path(__file__).resolve().parent / "learning_agent.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        found: Dict[str, str] = {}
        for node in tree.body:
            targets: List[Any] = []
            value = None
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
                value = node.value
            if value is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"_PHYSICS_ONLY_REPLY", "_FALLBACK_REPLY"}:
                    parsed = ast.literal_eval(value)
                    if isinstance(parsed, str):
                        found[target.id] = parsed
        _SKIP_REPLIES = (found["_PHYSICS_ONLY_REPLY"], found["_FALLBACK_REPLY"])
    except Exception:
        logger.warning("could not read tutor skip replies", exc_info=True)
        _SKIP_REPLIES = ()
    return _SKIP_REPLIES
