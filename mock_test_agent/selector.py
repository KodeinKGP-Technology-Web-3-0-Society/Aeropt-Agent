"""Deterministic question selection.

Proficiency decides how many questions each band receives. Empty bands give
their share to the bands that still have questions. One concept cannot take
the whole paper when others in the same band have questions.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

DEFAULT_WEIGHTS = {"weak": 0.50, "medium": 0.30, "strong": 0.20}
# When the student explicitly includes unlearned chapters, keep that share bounded.
UNLEARNED_SHARE = 0.25
CONCEPT_CAP = 0.40


def redistribute(n: int, weights: Dict[str, float], present: Iterable[str]) -> Dict[str, int]:
    """Largest-remainder split of `n` across bands that actually have questions."""
    if n <= 0:
        return {}
    active = {k: float(weights.get(k) or 0) for k in present if float(weights.get(k) or 0) > 0}
    if not active:
        return {}
    total = sum(active.values())
    raw = {k: n * w / total for k, w in active.items()}
    floors = {k: int(raw[k]) for k in raw}
    left = n - sum(floors.values())
    order = sorted(raw, key=lambda k: (raw[k] - floors[k], raw[k]), reverse=True)
    for i in range(max(0, left)):
        floors[order[i % len(order)]] += 1
    return {k: v for k, v in floors.items() if v > 0}


def even_shares(n: int, keys: Sequence[str]) -> Dict[str, int]:
    if n <= 0 or not keys:
        return {}
    base, extra = divmod(n, len(keys))
    return {key: base + (1 if i < extra else 0) for i, key in enumerate(keys)}


def difficulty_target(band: str, exam_target: int) -> int:
    if band == "weak":
        return max(1, exam_target - 1)
    if band == "strong":
        return min(5, exam_target + 1)
    if band == "unlearned":
        return max(1, exam_target - 1)
    return exam_target


def _rank(content_id: str, difficulty: int, target: int, seen: Set[str], seed: str) -> tuple:
    digest = hashlib.sha256(f"{seed}|{content_id}".encode()).hexdigest()
    return (
        1 if content_id in seen else 0,
        abs(int(difficulty or 3) - target),
        digest,
    )


def extra_concepts(question: Dict[str, Any]) -> List[str]:
    raw = question.get("secondary_concepts")
    primary = str(question.get("concept_id") or "")
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw]
    else:
        parts = [p.strip() for p in str(raw or "").split(",")]
    return [p for p in parts if p and p != primary]


def within_learned(question: Dict[str, Any], learned_ids: Set[str]) -> bool:
    primary = str(question.get("concept_id") or "")
    if primary not in learned_ids:
        return False
    return all(cid in learned_ids for cid in extra_concepts(question))


def _cap(n_band: int, n_concepts: int) -> int:
    if n_concepts <= 1:
        return n_band
    return max(1, int(round(n_band * CONCEPT_CAP + 0.0001)))


def pick_band(
    pool: List[Dict[str, Any]],
    n: int,
    *,
    band: str,
    exam_target: int,
    seen: Set[str],
    seed: str,
    used: Set[str],
) -> List[Dict[str, Any]]:
    """Up to `n` questions, round-robin across concepts, unseen first."""
    fresh = [q for q in pool if q.get("content_id") not in used]
    if n <= 0 or not fresh:
        return []
    target = difficulty_target(band, exam_target)
    by_concept: Dict[str, List[Dict[str, Any]]] = {}
    for q in fresh:
        by_concept.setdefault(str(q.get("concept_id") or "unknown"), []).append(q)
    for cid in by_concept:
        by_concept[cid].sort(
            key=lambda q: _rank(str(q["content_id"]), int(q.get("difficulty") or 3), target, seen, seed)
        )
    cap = _cap(n, len(by_concept))
    taken: List[Dict[str, Any]] = []
    taken_per: Dict[str, int] = {cid: 0 for cid in by_concept}
    concepts = sorted(by_concept)
    while len(taken) < n:
        progressed = False
        for cid in concepts:
            if len(taken) >= n or taken_per[cid] >= cap:
                continue
            bucket = by_concept[cid]
            if not bucket:
                continue
            taken.append(bucket.pop(0))
            taken_per[cid] += 1
            progressed = True
        if not progressed:
            break
    for q in taken:
        q["previously_seen"] = q["content_id"] in seen
        q["band"] = band
    return taken


def allocate_and_pick(
    questions: List[Dict[str, Any]],
    learned: Dict[str, Dict[str, Any]],
    n: int,
    *,
    coverage: str,
    exam_target: int,
    seen: Set[str],
    seed: str,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Pick `n` questions.

    coverage "learned" drops anything outside the learned set, including a
    question whose extra concepts are unlearned.
    coverage "all" reserves a bounded share for concepts with no learned row.
    """
    weights = dict(weights or DEFAULT_WEIGHTS)
    learned_ids = set(learned)
    chosen_pool: List[Dict[str, Any]] = []
    unlearned_pool: List[Dict[str, Any]] = []
    for q in questions:
        cid = str(q.get("concept_id") or "")
        if coverage == "learned":
            if within_learned(q, learned_ids):
                chosen_pool.append(q)
        elif cid in learned_ids and within_learned(q, learned_ids):
            chosen_pool.append(q)
        else:
            unlearned_pool.append(q)

    if coverage == "learned":
        unlearned_n = 0
    elif unlearned_pool and chosen_pool:
        unlearned_n = min(len(unlearned_pool), max(1, int(round(n * UNLEARNED_SHARE))))
        unlearned_n = min(unlearned_n, n)
    elif unlearned_pool and not chosen_pool:
        unlearned_n = n
    else:
        unlearned_n = 0
    learned_n = n - unlearned_n

    by_band: Dict[str, List[Dict[str, Any]]] = {"weak": [], "medium": [], "strong": []}
    for q in chosen_pool:
        info = learned.get(str(q.get("concept_id") or ""))
        if not info:
            continue
        by_band[info["band"]].append(q)
    present = [b for b in ("weak", "medium", "strong") if by_band[b]]
    counts = redistribute(learned_n, weights, present)

    used: Set[str] = set()
    picked: List[Dict[str, Any]] = []
    for band in ("weak", "medium", "strong"):
        picked.extend(pick_band(
            by_band[band], counts.get(band, 0),
            band=band, exam_target=exam_target, seen=seen, seed=seed, used=used,
        ))
        used.update(q["content_id"] for q in picked)

    # If a band's questions ran out, fill from the other learned bands.
    short = learned_n - len(picked)
    if short > 0:
        rest = [q for band in ("weak", "medium", "strong") for q in by_band[band]]
        picked.extend(pick_band(
            rest, short, band="medium", exam_target=exam_target, seen=seen, seed=seed, used=used,
        ))
        used.update(q["content_id"] for q in picked)

    if unlearned_n:
        extra = pick_band(
            unlearned_pool, unlearned_n,
            band="unlearned", exam_target=exam_target, seen=seen, seed=seed, used=used,
        )
        picked.extend(extra)

    return {
        "questions": picked,
        "allocation": counts,
        "unlearned": unlearned_n if any(q.get("band") == "unlearned" for q in picked) else 0,
        "contains_unlearned": any(q.get("band") == "unlearned" for q in picked),
    }
