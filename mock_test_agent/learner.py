"""What the student has actually learned, and how strong each concept is.

An unlearned concept is not a weak concept. Cold-start rows and rows with no
attempts stay out of Smart Mock and out of the "learned only" custom option.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

WEAK_BELOW = 0.45
STRONG_FROM = 0.75


def is_learned(row: Dict[str, Any]) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("is_cold_start") in (True, 1, "true", "t"):
        return False
    try:
        attempts = int(row.get("attempts") or 0)
    except (TypeError, ValueError):
        return False
    return attempts > 0 and bool(row.get("concept_id"))


def band_of(score: Any) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        value = 0.5
    if value < WEAK_BELOW:
        return "weak"
    if value < STRONG_FROM:
        return "medium"
    return "strong"


def learned_index(rows: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """concept_id -> {band, score, subject, chapter, attempts} for learned concepts only."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not is_learned(row):
            continue
        cid = str(row["concept_id"])
        try:
            score = float(row.get("proficiency_score") if row.get("proficiency_score") is not None else 0.5)
        except (TypeError, ValueError):
            score = 0.5
        out[cid] = {
            "concept_id": cid,
            "band": band_of(score),
            "score": score,
            "subject": row.get("subject") or "",
            "chapter": row.get("chapter") or row.get("chapter_id") or "",
            "attempts": int(row.get("attempts") or 0),
            "flagged_weak": bool(row.get("flagged_weak")),
        }
    return out


def fingerprint(index: Dict[str, Dict[str, Any]]) -> str:
    parts = [
        f"{cid}:{round(float(info['score']), 2)}:{info['band']}"
        for cid, info in sorted(index.items())
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def load_states(user_id: str) -> List[Dict[str, Any]]:
    """Learner rows from the user database. Empty when the store is unavailable."""
    if not user_id:
        return []
    try:
        from db.supabase_client import db
        rows = db.read_all_user_concept_states(user_id)
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]
