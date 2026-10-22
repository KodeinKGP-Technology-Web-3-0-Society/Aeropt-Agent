"""JEE Advanced questions linked to one concept.

The link is the ingest map in public.chunk_concepts (primary, then secondary).
This does not search, rank, or write questions. Coaching sheets stored as PYQ
are left out: they were not an Advanced paper.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from retrieval.store import ADVANCED_PYQ_LICENSES

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_PAPER = re.compile(r"\bpaper\s*([12])\b", re.IGNORECASE)
_PREVIEW = 180


def _loads(value: Any) -> Optional[dict]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _is_advanced_pyq(row: Dict[str, Any]) -> bool:
    if str(row.get("content_type") or "") != "PYQ":
        return False
    if str(row.get("target_exam") or "") != "Jee Advanced":
        return False
    license_ = str(row.get("license") or "")
    if "coaching" in license_.lower():
        return False
    return license_ in ADVANCED_PYQ_LICENSES


def _when(row: Dict[str, Any]) -> tuple[Optional[int], Optional[str]]:
    blob = " ".join(str(row.get(key) or "") for key in ("source_book", "heading", "source_ref"))
    year = _YEAR.search(blob)
    paper = _PAPER.search(blob)
    return (int(year.group(0)) if year else None, paper.group(1) if paper else None)


def _stem(row: Dict[str, Any]) -> str:
    structured = _loads(row.get("structured"))
    text = ""
    if structured:
        text = str(structured.get("stem") or "").strip()
    if not text:
        text = str(row.get("core_text") or row.get("heading") or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= _PREVIEW:
        return text
    cut = text[:_PREVIEW].rsplit(" ", 1)[0]
    if cut.count("$") % 2:
        cut = cut[: cut.rfind("$")].rstrip()
    return (cut or text[:_PREVIEW]).rstrip(".,;:") + "…"


def _license_rank(license_: str) -> int:
    if license_.startswith("official exam paper"):
        return 0
    if license_ == "past exam paper":
        return 1
    return 2


def shape_pyqs(rows: List[Dict[str, Any]], *, limit: int = 6) -> List[Dict[str, Any]]:
    """Year, paper (when the source names one), and a stem preview.

    An official paper outranks a newer digitized set, then newer years within that.
    """
    shaped = []
    for row in rows:
        if not row.get("content_id") or not _is_advanced_pyq(row):
            continue
        year, paper = _when(row)
        shaped.append({
            "content_id": row["content_id"],
            "year": year,
            "paper": paper,
            "stem": _stem(row),
            "_role": int(row.get("role_rank") or 0),
            "_source": _license_rank(str(row.get("license") or "")),
        })
    shaped.sort(key=lambda item: (
        item["_role"], item["_source"], -(item["year"] or 0), item["paper"] or "9", item["content_id"],
    ))
    return [
        {"content_id": item["content_id"], "year": item["year"], "paper": item["paper"], "stem": item["stem"]}
        for item in shaped[: max(0, limit)]
    ]


def list_concept_pyqs(concept_id: str, *, limit: int = 6) -> List[Dict[str, Any]]:
    from retrieval import store

    concept_id = (concept_id or "").strip()
    if not concept_id:
        return []
    rows = store.linked_pyq_rows(concept_id, limit=max(limit * 8, 48))
    return shape_pyqs(rows, limit=limit)
