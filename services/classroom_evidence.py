"""Post one classroom evidence event after a graded turn.

Django owns the tracker. This module only speaks HTTP. A missing token, an
unset base URL, or a down Django process is logged and swallowed so the
student still receives the chat reply.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aeropt.classroom_evidence")

_TIMEOUT_S = 1.5


def resolve_learner_token(
    classroom_token: Optional[str],
    authorization: Optional[str],
) -> str:
    """Prefer the explicit chat field, then an Authorization bearer header."""
    for raw in (classroom_token, authorization):
        token = _normalize_token(raw)
        if token:
            return token
    return ""


def events_for_turn(
    *,
    result: Dict[str, Any],
    was_graded: bool,
    study_stage: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Build the evidence bodies for this turn. Empty when nothing was graded."""
    concept_id = str(
        result.get("recorded_concept_id")
        or result.get("primary_concept_id")
        or result.get("roadmap_concept_id")
        or ""
    ).strip()
    if not concept_id or result.get("attempt_record_failed"):
        return []

    button = bool(was_graded)
    recorded = bool(result.get("attempt_recorded"))
    if not button and not recorded:
        return []

    stage = study_stage or result.get("study_stage") or result.get("content_type_requested")
    surface = "assess" if stage == "practice" else "lesson"
    specialist = "evaluation" if button else "tutor"
    payload: Dict[str, Any] = {"correct": bool(result.get("is_correct"))}
    mastery = result.get("mastery_pct")
    if isinstance(mastery, int) and not isinstance(mastery, bool) and 0 <= mastery <= 100:
        payload["mastery"] = mastery
    events: List[Dict[str, Any]] = [
        {
            "concept_id": concept_id,
            "surface": surface,
            "specialist": specialist,
            "kind": "attempt",
            "payload": payload,
        }
    ]
    if not button and recorded and "mastery" in payload:
        events.append(
            {
                "concept_id": concept_id,
                "surface": surface,
                "specialist": "tutor",
                "kind": "mastery",
                "payload": {"mastery": payload["mastery"]},
            }
        )
    return events


def post_events(
    events: Optional[List[Dict[str, Any]]],
    token: Optional[str] = None,
    base_url: Optional[str] = None,
) -> None:
    """POST an explicit list of evidence events. Never raises."""
    try:
        if not events:
            return
        token = _normalize_token(token)
        if not token:
            logger.info("classroom evidence skipped: no learner token")
            return
        base = base_url if base_url is not None else os.getenv("DJANGO_API_BASE_URL", "")
        base = base.strip().rstrip("/")
        if not base:
            logger.info("classroom evidence skipped: DJANGO_API_BASE_URL unset")
            return
        url = f"{base}/api/v1/classroom/evidence/"
        for event in events:
            _post_one(url, token, event)
    except Exception:
        logger.exception("classroom evidence failed")


def post_turn_evidence(
    *,
    result: Dict[str, Any],
    was_graded: bool,
    study_stage: Optional[str] = None,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
) -> None:
    """POST each event derived from a graded turn. Never raises."""
    try:
        events = events_for_turn(result=result, was_graded=was_graded, study_stage=study_stage)
        post_events(events, token=token, base_url=base_url)
    except Exception:
        logger.exception("classroom evidence failed")


def _normalize_token(raw: Optional[str]) -> str:
    if not raw:
        return ""
    token = raw.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def _post_one(url: str, token: str, event: Dict[str, Any]) -> None:
    data = json.dumps(event).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.warning(
            "classroom evidence post failed kind=%s concept=%s: %s",
            event.get("kind"),
            event.get("concept_id"),
            exc,
        )
    except Exception:
        logger.exception(
            "classroom evidence post failed kind=%s concept=%s",
            event.get("kind"),
            event.get("concept_id"),
        )
