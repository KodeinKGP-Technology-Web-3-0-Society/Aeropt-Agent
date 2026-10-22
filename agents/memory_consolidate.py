"""Between-session consolidation of episodic memory into one learner fact.

This is not a tutor-graph node. A cron or an operator runs it per user. The
model may suggest one fact. This module checks the kind, the length, the
concept, and the user, then writes learner_memory itself.

A single episode, or a set of episodes with no repeated correct or incorrect
outcome, is never sent to the model. Doing nothing is a normal result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence

from db.supabase_client import db
from mcp_tools.learner_tools import _MEMORY_KINDS

logger = logging.getLogger("jee_tutor.memory_consolidate")

MAX_FACT_CHARS = 300
MIN_CLUSTER = 2
_KIND_FOR_OUTCOME = {"incorrect": "misconception", "correct": "strength"}
_RESULTS = ("saved", "duplicate", "skipped", "rejected")


def consolidate_for_user(
    user_id: str,
    *,
    dry_run: bool = False,
    complete: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Propose at most one fact for this user. Never raises."""
    try:
        return _consolidate_for_user(str(user_id or ""), dry_run=dry_run, complete=complete)
    except Exception:
        logger.debug("consolidation failed", exc_info=True)
        return {"user_id": str(user_id or ""), "saved": False, "reason": "failed_open"}


def run_pending(
    *,
    dry_run: bool = False,
    complete: Optional[Callable[[str], str]] = None,
) -> List[Dict[str, Any]]:
    """Run consolidate_for_user once per user who still has open episodes."""
    try:
        user_ids = db.list_users_with_open_episodes()
    except Exception:
        logger.debug("could not list users for consolidation", exc_info=True)
        return []
    return [
        consolidate_for_user(user_id, dry_run=dry_run, complete=complete)
        for user_id in user_ids
        if user_id
    ]


def eligible_clusters(episodes: Sequence[Dict[str, Any]], user_id: str) -> List[Dict[str, Any]]:
    """Repeated same-concept outcomes for this user only.

    Taught, unknown, and pending episodes do not count. A concept needs two
    incorrect episodes (misconception) or two correct ones (strength). The
    larger incorrect group wins when both exist.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        if str(episode.get("user_id") or user_id) != str(user_id):
            continue
        concept_id = str(episode.get("concept_id") or "").strip()
        if not concept_id:
            continue
        grouped.setdefault(concept_id, []).append(episode)

    clusters: List[Dict[str, Any]] = []
    for concept_id, rows in grouped.items():
        by_outcome: Dict[str, List[Dict[str, Any]]] = {"incorrect": [], "correct": []}
        for row in _distinct_evidence(rows):
            if str(row.get("evidence_source") or "") == "student_self_report":
                continue
            outcome = _outcome_class(row)
            if outcome:
                by_outcome[outcome].append(row)
        chosen_outcome = ""
        chosen_rows: List[Dict[str, Any]] = []
        incorrect = by_outcome["incorrect"]
        if len(incorrect) >= MIN_CLUSTER and _same_mistake(incorrect):
            chosen_outcome = "incorrect"
            chosen_rows = incorrect
        elif len(by_outcome["correct"]) >= MIN_CLUSTER and _verified(by_outcome["correct"]):
            chosen_outcome = "correct"
            chosen_rows = by_outcome["correct"]
        if not chosen_rows:
            continue
        clusters.append(
            {
                "user_id": str(user_id),
                "concept_id": concept_id,
                "kind": _KIND_FOR_OUTCOME[chosen_outcome],
                "outcome": chosen_outcome,
                "episodes": chosen_rows[:6],
            }
        )
    clusters.sort(key=lambda item: (-len(item["episodes"]), item["concept_id"]))
    return clusters


def _distinct_evidence(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One grade linked from two episode rows counts once."""
    seen = set()
    unique: List[Dict[str, Any]] = []
    for row in rows:
        attempt = row.get("attempt_id") or row.get("feedback_attempt_id")
        key = f"attempt:{attempt}" if attempt else f"episode:{row.get('episode_id')}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _same_mistake(rows: Sequence[Dict[str, Any]]) -> bool:
    texts = []
    for row in rows:
        text = " ".join(str(row.get("student_response") or "").lower().split())
        if len(text) < 12:
            return False
        texts.append(text)
    return len(set(texts)) == 1


def _verified(rows: Sequence[Dict[str, Any]]) -> bool:
    allowed = {"answer_key", "checker"}
    return all(str(row.get("evidence_source") or "") in allowed for row in rows)


def cluster_key(user_id: str, episode_ids: Sequence[str]) -> str:
    raw = str(user_id) + "|" + ",".join(sorted(str(item) for item in episode_ids))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def validate_proposal(proposal: Any, cluster: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a cleaned fact, or None when the model output must not be stored."""
    if not isinstance(proposal, dict) or proposal.get("save") is not True:
        return None
    kind = str(proposal.get("kind") or "").strip().lower()
    if kind not in _MEMORY_KINDS or kind != cluster.get("kind"):
        return None
    fact = " ".join(str(proposal.get("fact") or "").split())
    if not fact or len(fact) > MAX_FACT_CHARS:
        return None
    concept_id = str(proposal.get("concept_id") or "").strip()
    if concept_id != cluster.get("concept_id"):
        return None
    try:
        confidence = float(proposal.get("confidence") if proposal.get("confidence") is not None else 0.75)
    except (TypeError, ValueError):
        confidence = 0.75
    confidence = max(0.5, min(0.9, confidence))
    return {"kind": kind, "fact": fact, "concept_id": concept_id, "confidence": confidence}


def _consolidate_for_user(
    user_id: str,
    *,
    dry_run: bool,
    complete: Optional[Callable[[str], str]],
) -> Dict[str, Any]:
    base = {"user_id": user_id, "saved": False, "dry_run": dry_run}
    if not user_id:
        return {**base, "reason": "missing_user"}
    if not db.consolidation_schema_ready():
        return {**base, "reason": "schema_not_ready"}

    episodes = db.list_unconsolidated_episodes(user_id) or []
    clusters = eligible_clusters(episodes, user_id)
    if not clusters:
        return {**base, "reason": "no_repeated_outcome"}

    for cluster in clusters:
        episode_ids = [str(row.get("episode_id")) for row in cluster["episodes"] if row.get("episode_id")]
        if len(episode_ids) < MIN_CLUSTER:
            continue
        key = cluster_key(user_id, episode_ids)
        if db.consolidation_seen(user_id, key):
            continue
        prompt = _prompt(cluster)
        try:
            raw = (complete or _default_complete)(prompt)
        except Exception:
            logger.debug("consolidation model failed", exc_info=True)
            return {**base, "reason": "model_unavailable", "concept_id": cluster["concept_id"]}
        proposal = _parse_proposal(raw)
        cleaned = validate_proposal(proposal, cluster)
        if cleaned is None:
            reason = "skipped" if isinstance(proposal, dict) and proposal.get("save") is False else "rejected"
            if not dry_run:
                db.record_consolidation(
                    user_id=user_id,
                    cluster_key=key,
                    episode_ids=episode_ids,
                    result=reason if reason in _RESULTS else "rejected",
                )
            return {
                **base,
                "reason": reason,
                "concept_id": cluster["concept_id"],
                "episode_ids": episode_ids,
            }

        if not dry_run:
            claimed = db.record_consolidation(
                user_id=user_id,
                cluster_key=key,
                episode_ids=episode_ids,
                result="claimed",
                fact=cleaned["fact"],
            )
            if not claimed and db.consolidation_seen(user_id, key):
                return {**base, "reason": "already_considered", "concept_id": cleaned["concept_id"]}
        duplicate = _matching_fact(db.active_learner_facts(user_id) or [], cleaned)
        if duplicate is not None:
            if not dry_run:
                db.mark_episodes_consolidated(user_id, episode_ids, duplicate.get("memory_id"))
                db.record_consolidation(
                    user_id=user_id,
                    cluster_key=key,
                    episode_ids=episode_ids,
                    result="duplicate",
                    memory_id=duplicate.get("memory_id"),
                    fact=cleaned["fact"],
                )
            return {
                **base,
                "reason": "duplicate",
                "concept_id": cleaned["concept_id"],
                "fact": cleaned["fact"],
                "episode_ids": episode_ids,
                "memory_id": duplicate.get("memory_id"),
            }

        if dry_run:
            return {
                **base,
                "reason": "would_save",
                "kind": cleaned["kind"],
                "fact": cleaned["fact"],
                "concept_id": cleaned["concept_id"],
                "episode_ids": episode_ids,
            }

        stored = db.insert_learner_memory(
            user_id,
            cleaned["kind"],
            cleaned["fact"],
            evidence="episodes " + ",".join(episode_ids),
            subject_key=cleaned["concept_id"],
            confidence=cleaned["confidence"],
            source="consolidation",
        )
        if not stored:
            return {**base, "reason": "write_failed", "concept_id": cleaned["concept_id"]}
        memory_id = stored.get("memory_id")
        db.mark_episodes_consolidated(user_id, episode_ids, memory_id)
        db.record_consolidation(
            user_id=user_id,
            cluster_key=key,
            episode_ids=episode_ids,
            result="saved",
            memory_id=memory_id,
            fact=cleaned["fact"],
        )
        return {
            **base,
            "saved": True,
            "reason": "saved",
            "kind": cleaned["kind"],
            "fact": cleaned["fact"],
            "concept_id": cleaned["concept_id"],
            "episode_ids": episode_ids,
            "memory_id": memory_id,
        }

    return {**base, "reason": "already_considered"}


def _outcome_class(row: Dict[str, Any]) -> str:
    feedback = row.get("feedback_outcome")
    if feedback in ("incorrect", "correct"):
        return str(feedback)
    observed = row.get("observed_outcome")
    if observed in ("incorrect", "correct"):
        return str(observed)
    return ""


def _matching_fact(facts: Sequence[Dict[str, Any]], cleaned: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    wanted = _normalise_fact(cleaned["fact"])
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        if str(fact.get("kind") or "") != cleaned["kind"]:
            continue
        subject = str(fact.get("subject_key") or "")
        if subject and subject != cleaned["concept_id"]:
            continue
        if _normalise_fact(str(fact.get("fact") or "")) == wanted:
            return fact
    return None


def _normalise_fact(fact: str) -> str:
    return " ".join(fact.lower().split())


def _prompt(cluster: Dict[str, Any]) -> str:
    lines = []
    for episode in cluster["episodes"]:
        lines.append(
            "concept={concept} outcome={outcome} approach={approach} query={query}".format(
                concept=cluster["concept_id"],
                outcome=_outcome_class(episode) or "unknown",
                approach=str(episode.get("approach") or "")[:180],
                query=str(episode.get("query_excerpt") or "")[:300],
            )
        )
    return (
        "You consolidate one student's repeated tutoring episodes into at most one durable fact.\n"
        "Return a JSON object only.\n"
        'If these episodes do not show a stable learning trait, return {"save": false}.\n'
        "If they do, return "
        '{"save": true, "kind": "%s", "fact": "<one sentence about the student>", '
        '"concept_id": "%s", "confidence": 0.75}.\n'
        "kind must be %s. fact must be at most 300 characters. "
        "Do not mention any other student. Do not invent a preference, goal, or context.\n\n"
        "Episodes:\n%s"
        % (cluster["kind"], cluster["concept_id"], cluster["kind"], "\n".join(lines))
    )


def _parse_proposal(raw: str) -> Optional[Dict[str, Any]]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _default_complete(prompt: str) -> str:
    """One structured model call. Imported here so tests never construct a client."""
    from openai import OpenAI

    import config
    from utils.llm import complete_text

    client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    text, _model = complete_text(
        client=client,
        model=config.GEMINI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        fallback_models=config.FALLBACK_MODELS,
        temperature=0.1,
        max_tokens=400,
        response_format={"type": "json_object"},
    )
    return text


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Consolidate one user's episodes into at most one learner-memory fact."
    )
    parser.add_argument("--user-id", default="", help="Supabase user id. Required unless --pending.")
    parser.add_argument(
        "--pending",
        action="store_true",
        help="Run once for each user who still has an unconsolidated episode.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the fact that would be saved. Write nothing.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.user_id and not args.pending:
        parser.error("pass --user-id or --pending")
    if args.pending:
        results = run_pending(dry_run=args.dry_run)
        print(json.dumps(results, default=str))
        return 0
    result = consolidate_for_user(args.user_id, dry_run=args.dry_run)
    print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
