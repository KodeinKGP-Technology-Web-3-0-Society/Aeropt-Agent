"""Hybrid retrieval: graph scope → BM25 + dense → fusion → novelty → diversity.

Order matters and each stage earns its place:

1. **Scope by concept.** The syllabus graph narrows ~10k chunks to a few hundred
   that actually belong to the concept, its neighbours in the chapter and its
   prerequisites. Precision comes from here, not from the embedding.
2. **BM25 (FTS5) and dense similarity, independently.** Lexical search catches the
   exact phrasing a physics question lives or dies by ("angle of repose", "μ_k");
   dense search catches the paraphrase. Neither alone is reliable.
3. **Reciprocal-rank fusion.** Scores from two different scales are never added;
   ranks are. One strong signal can promote an item the other missed.
4. **Novelty.** Questions the student has already been served are removed, by
   content_id *and* by fingerprint, so a reworded duplicate cannot slip through.
5. **Diversity (MMR).** Five paraphrases of one paragraph teach nothing that one
   does, so near-identical survivors are suppressed.

Everything is local: one SQLite query plus a couple of numpy dot products.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from retrieval import embedder, store

TYPE_FAMILIES: dict[str, tuple[str, ...]] = {
    "theory": ("Theory",),
    "example": ("Solved Example",),
    "practice": ("Practice Question",),
    "pyq": ("PYQ",),
    "question": ("Practice Question", "PYQ", "Solved Example"),
    "any": ("Theory", "Solved Example", "Practice Question", "PYQ"),
}

# What to fall back to when the asked-for kind has nothing left to give.
CASCADE: dict[str, tuple[str, ...]] = {
    "theory": ("theory", "example"),
    "example": ("example", "practice", "pyq"),
    "practice": ("practice", "pyq", "example"),
    "pyq": ("pyq", "practice", "example"),
    "any": ("any",),
}

EXAM_ALLOWED: dict[str, tuple[str, ...]] = {
    "JEE_ADVANCED": ("Jee Advanced", "Jee main and Advanced"),
    "JEE_MAINS": ("Jee main", "Jee main and Advanced"),
}

MAX_CANDIDATES = 600
RRF_K = 10
MMR_LAMBDA = 0.78
FTS_LIMIT = 120


@dataclass
class Hit:
    content_id: str
    score: float
    dense: float
    lexical: float
    rank_dense: int | None
    rank_lexical: int | None
    row: dict[str, Any]
    seen_before: bool = False

    def as_chunk(self) -> dict[str, Any]:
        """The shape the rest of the agent already expects from a content row."""
        row = self.row
        return {
            "content_id": row["content_id"],
            "content_type": row["content_type"],
            "subject": row["subject"],
            "chapter": row["chapter"],
            "unit": row["unit"],
            "core_text": row["core_text"],
            "solution": row["solution"],
            "difficulty": row["difficulty"],
            "question_type": row["question_type"],
            "target_exam": row["target_exam"],
            "primary_concept": row["concept_id"],
            "primary_concept_id": row["concept_id"],
            "secondary_concepts": _loads(row["secondary"]),
            "source_book": row["source_book"],
            "source_ref": row["source_ref"],
            "license": row["license"],
            "heading": row["heading"],
            "fingerprint": row["fingerprint"],
            "in_syllabus": bool(row["in_syllabus"]),
            "has_solution": bool(row["has_solution"]),
            "citation": f"{row['source_book']} — {row['source_ref']}".strip(" —"),
            "retrieval_score": round(self.score, 4),
            "seen_before": self.seen_before,
        }


@dataclass
class SearchResult:
    hits: list[Hit] = field(default_factory=list)
    content_type_used: str = ""
    scope_used: str = ""
    candidates: int = 0
    excluded_seen: int = 0
    pool_exhausted: bool = False
    fts_terms: str = ""

    @property
    def chunks(self) -> list[dict[str, Any]]:
        return [h.as_chunk() for h in self.hits]


def _loads(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [s.strip() for s in str(raw).split(",") if s.strip()]


def _fts_query(text: str) -> str:
    """FTS5 MATCH string: content words OR-ed, quoted so operators can't leak in."""
    words = re.findall(r"[A-Za-z][A-Za-z\-']{2,}|\d+(?:\.\d+)?", text or "")
    stop = {
        "the", "and", "for", "what", "why", "how", "does", "did", "can", "will",
        "with", "this", "that", "are", "was", "were", "explain", "please", "give",
        "want", "need", "tell", "about", "from", "you", "your", "its", "have",
        "any", "some", "one", "into", "out", "not", "but", "there", "then",
    }
    terms = [w.lower() for w in words if w.lower() not in stop][:14]
    uniq: list[str] = []
    for term in terms:
        if term not in uniq:
            uniq.append(term)
    return " OR ".join(f'"{t}"' for t in uniq)


def _scope_concepts(
    concept_id: str,
    *,
    secondary: Sequence[str] = (),
    chapter_concepts: Sequence[str] = (),
    prereq_concepts: Sequence[str] = (),
    widen: int = 0,
) -> tuple[list[str], str]:
    """Concept ids allowed at this widening level, and a label for the trace."""
    if widen == 0:
        ids = [concept_id, *secondary]
        return [c for c in dict.fromkeys(ids) if c], "concept"
    if widen == 1:
        ids = [concept_id, *secondary, *prereq_concepts]
        return [c for c in dict.fromkeys(ids) if c], "concept+prerequisites"
    ids = [concept_id, *secondary, *prereq_concepts, *chapter_concepts]
    return [c for c in dict.fromkeys(ids) if c], "chapter"


def _fetch_candidates(
    concept_ids: Sequence[str],
    types: Sequence[str],
    *,
    target_exam: str = "",
    require_solution: bool = False,
    in_syllabus_only: bool = True,
    limit: int = MAX_CANDIDATES,
) -> list[dict[str, Any]]:
    if not concept_ids or not types:
        return []
    conn = store.corpus()
    marks_c = ",".join("?" * len(concept_ids))
    marks_t = ",".join("?" * len(types))
    params: list[Any] = [*concept_ids, *types]
    sql = f"""
        SELECT c.*, cc.role AS concept_role
        FROM chunk_concepts cc
        JOIN chunks c ON c.content_id = cc.content_id
        WHERE cc.concept_id IN ({marks_c})
          AND c.content_type IN ({marks_t})
    """
    if require_solution:
        sql += " AND c.has_solution = 1"
    if in_syllabus_only:
        sql += " AND c.in_syllabus = 1"
    exams = EXAM_ALLOWED.get((target_exam or "").upper())
    if exams:
        marks_e = ",".join("?" * len(exams))
        sql += f" AND c.target_exam IN ({marks_e})"
        params.extend(exams)
    # A primary-concept chunk is about the concept; a secondary one merely touches it.
    sql += """
        GROUP BY c.content_id
        ORDER BY MIN(CASE cc.role WHEN 'primary' THEN 0 ELSE 1 END), c.difficulty, c.content_id
        LIMIT ?
    """
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params)]


def _lexical_ranks(query: str, allowed: Sequence[str]) -> tuple[dict[str, float], str]:
    """BM25 over the candidate set only.

    Ranking the whole corpus first and intersecting afterwards silently drops any
    candidate outside the global top-N — the exact failure the Neo4j client had to
    work around with a full-corpus fetch_k. Restricting the MATCH to the candidates
    means a chunk competes against its real rivals, not against all 10,000.
    """
    match = _fts_query(query)
    if not match or not allowed:
        return {}, ""
    conn = store.corpus()
    ids = list(allowed)
    scored: list[tuple[str, float]] = []
    # SQLite's parameter ceiling is well above this, but chunk anyway to stay safe.
    for start in range(0, len(ids), 400):
        batch = ids[start : start + 400]
        marks = ",".join("?" * len(batch))
        try:
            rows = conn.execute(
                f"""
                SELECT c.content_id AS content_id, bm25(chunks_fts, 4.0, 2.0, 1.0) AS score
                FROM chunks_fts
                JOIN chunks c ON c.rowid = chunks_fts.rowid
                WHERE chunks_fts MATCH ? AND c.content_id IN ({marks})
                ORDER BY score
                """,
                (match, *batch),
            ).fetchall()
        except Exception:  # noqa: BLE001 — a malformed MATCH must not fail the turn
            return {}, match
        # bm25() returns negative numbers, smaller = better.
        scored.extend((r["content_id"], -float(r["score"])) for r in rows)
    scored.sort(key=lambda kv: -kv[1])
    return dict(scored[:FTS_LIMIT]), match


def _mmr(
    order: list[int],
    vectors: np.ndarray,
    top_k: int,
    lambda_: float = MMR_LAMBDA,
    base_scores: np.ndarray | None = None,
) -> list[int]:
    """Maximal marginal relevance over the fused ranking."""
    if len(order) <= 1:
        return order[:top_k]
    chosen: list[int] = []
    pool = list(order)
    scores = base_scores if base_scores is not None else np.linspace(1.0, 0.0, len(order))
    # Relevance and cosine similarity must live on the same scale, or lambda stops
    # meaning anything: raw RRF scores sit around 0.1 while similarities reach 0.9,
    # and the diversity term silently wins every comparison.
    scores = np.asarray(scores, dtype=np.float32)
    span = float(scores.max() - scores.min()) if scores.size else 0.0
    scores = (scores - scores.min()) / span if span > 1e-9 else np.ones_like(scores)
    score_of = {idx: float(scores[i]) for i, idx in enumerate(order)}
    while pool and len(chosen) < top_k:
        if not chosen:
            best = pool.pop(0)
            chosen.append(best)
            continue
        chosen_mat = vectors[chosen]
        best_idx, best_val = None, -1e9
        for idx in pool:
            sim = float(np.max(chosen_mat @ vectors[idx]))
            val = lambda_ * score_of[idx] - (1.0 - lambda_) * sim
            if val > best_val:
                best_idx, best_val = idx, val
        chosen.append(best_idx)  # type: ignore[arg-type]
        pool.remove(best_idx)  # type: ignore[arg-type]
    return chosen


def search(
    query: str,
    *,
    concept_id: str,
    content_type: str = "theory",
    secondary_concepts: Sequence[str] = (),
    chapter_concepts: Sequence[str] = (),
    prereq_concepts: Sequence[str] = (),
    target_exam: str = "",
    top_k: int = 5,
    exclude_ids: Iterable[str] = (),
    exclude_fingerprints: Iterable[str] = (),
    difficulty_target: int | None = None,
    require_solution: bool | None = None,
    in_syllabus_only: bool = True,
    diversify: bool = True,
) -> SearchResult:
    families = CASCADE.get(content_type, (content_type,))
    exclude_ids = set(exclude_ids)
    exclude_fps = {fp for fp in exclude_fingerprints if fp}
    is_question_request = content_type in {"practice", "pyq", "example", "question"}
    if require_solution is None:
        # A question with no answer key cannot be graded, so it is only a fallback.
        require_solution = content_type in {"practice", "pyq"}

    best: SearchResult | None = None
    min_candidates = max(top_k * 4, 12)

    for family in families:
        types = TYPE_FAMILIES.get(family, TYPE_FAMILIES["any"])
        for solution_required in ([True, False] if require_solution else [False]):
            # Widen the *candidate union*, not the ranking. A 9-item concept pool
            # cannot contain an inclined-plane friction problem just because the
            # concept is "friction"; the chapter pool can. Ranking then decides,
            # with primary-concept chunks already favoured.
            rows: list[dict[str, Any]] = []
            seen_rows: set[str] = set()
            scope_label = "concept"
            for widen in (0, 1, 2):
                ids, label = _scope_concepts(
                    concept_id,
                    secondary=secondary_concepts,
                    chapter_concepts=chapter_concepts,
                    prereq_concepts=prereq_concepts,
                    widen=widen,
                )
                if not ids:
                    continue
                for row in _fetch_candidates(
                    ids,
                    types,
                    target_exam=target_exam,
                    require_solution=solution_required,
                    in_syllabus_only=in_syllabus_only,
                ):
                    if row["content_id"] in seen_rows:
                        continue
                    seen_rows.add(row["content_id"])
                    rows.append(row)
                if rows:
                    scope_label = label if widen else "concept"
                usable = [
                    r for r in rows
                    if not (is_question_request and r["content_id"] in exclude_ids)
                ]
                if len(usable) < min_candidates:
                    continue
                # Having *enough* candidates is not the same as having candidates at
                # the right level. Stopping here with only easy questions is how an
                # Advanced request ends up pitched at Main.
                if difficulty_target:
                    on_band = sum(
                        1 for r in usable
                        if r["difficulty"] and abs(int(r["difficulty"]) - int(difficulty_target)) <= 1
                    )
                    if on_band < min(6, top_k * 2):
                        continue
                break
            if not rows:
                continue
            result = _rank(
                query,
                rows,
                top_k=top_k,
                exclude_ids=exclude_ids,
                exclude_fps=exclude_fps,
                difficulty_target=difficulty_target,
                diversify=diversify,
                drop_seen=is_question_request,
                # "practise this concept" means this concept's questions first. Without
                # this, a chapter neighbour that scores marginally higher is served
                # while the concept's own questions sit unseen forever.
                concept_first=concept_id if is_question_request else "",
            )
            result.content_type_used = family
            result.scope_used = scope_label
            if result.hits:
                return result
            if best is None or result.excluded_seen > best.excluded_seen:
                best = result
    return best or SearchResult()


def _rank(
    query: str,
    rows: list[dict[str, Any]],
    *,
    top_k: int,
    exclude_ids: set[str],
    exclude_fps: set[str],
    difficulty_target: int | None,
    diversify: bool,
    drop_seen: bool,
    concept_first: str = "",
) -> SearchResult:
    ids = [r["content_id"] for r in rows]
    lexical, match_terms = _lexical_ranks(query, ids)

    vecs = store.vectors()
    rowvecs = vecs[[int(r["vec_row"]) for r in rows]]
    qvec = embedder.encode_one(query)
    dense = rowvecs @ qvec

    dense_order = np.argsort(-dense)
    rank_dense = {ids[i]: pos for pos, i in enumerate(dense_order)}
    lex_sorted = sorted(lexical.items(), key=lambda kv: -kv[1])
    rank_lexical = {cid: pos for pos, (cid, _s) in enumerate(lex_sorted)}

    fused: list[tuple[float, int]] = []
    for i, cid in enumerate(ids):
        score = 0.0
        if cid in rank_dense:
            score += 1.0 / (RRF_K + rank_dense[cid])
        if cid in rank_lexical:
            score += 1.0 / (RRF_K + rank_lexical[cid])
        # Chunks that are *about* the concept beat chunks that merely mention it.
        if rows[i].get("concept_role") == "primary":
            score *= 1.15
        if difficulty_target and rows[i].get("difficulty"):
            # 0.12 per rung was too gentle to move the mean: fusion scores differ by
            # more than that between neighbouring ranks, so an easy-but-well-matched
            # question still beat a correctly-pitched one. A JEE Advanced student was
            # getting the same difficulty mix as a Main student (2.80 vs 2.68).
            gap = abs(int(rows[i]["difficulty"]) - int(difficulty_target))
            score *= max(0.30, 1.0 - 0.22 * gap)
        fused.append((score, i))

    if concept_first:
        fused.sort(key=lambda t: (rows[t[1]]["concept_id"] != concept_first, -t[0]))
    else:
        fused.sort(key=lambda t: -t[0])
    excluded = 0
    kept: list[int] = []
    kept_scores: list[float] = []
    for score, i in fused:
        cid = ids[i]
        fp = rows[i].get("fingerprint")
        if cid in exclude_ids or (fp and fp in exclude_fps):
            excluded += 1
            if drop_seen:
                continue
        kept.append(i)
        kept_scores.append(score)

    if not kept:
        return SearchResult(excluded_seen=excluded, candidates=len(rows), fts_terms=match_terms,
                            pool_exhausted=excluded > 0)

    # Pitch before polish. A score multiplier could not express "an Advanced student
    # should not be handed the chapter's easiest question": fusion gaps between
    # neighbouring ranks are larger than any sane per-rung penalty, so the band is
    # applied as a partition — keep on-band items when there are enough of them, and
    # fall back to the rest rather than returning nothing.
    if difficulty_target:
        on_band = [
            (i, sc) for i, sc in zip(kept, kept_scores)
            if rows[i]["difficulty"] and abs(int(rows[i]["difficulty"]) - int(difficulty_target)) <= 1
        ]
        if len(on_band) >= top_k:
            kept = [i for i, _ in on_band]
            kept_scores = [sc for _, sc in on_band]

    shortlist = kept[: max(top_k * 6, 24)]
    scores_arr = np.asarray(kept_scores[: len(shortlist)], dtype=np.float32)
    chosen = (
        _mmr(shortlist, vecs[[int(rows[i]["vec_row"]) for i in range(len(rows))]], top_k,
             base_scores=scores_arr)
        if diversify and len(shortlist) > 1
        else shortlist[:top_k]
    )

    hits = [
        Hit(
            content_id=ids[i],
            score=float(dict(zip(kept, kept_scores)).get(i, 0.0)),
            dense=float(dense[i]),
            lexical=float(lexical.get(ids[i], 0.0)),
            rank_dense=rank_dense.get(ids[i]),
            rank_lexical=rank_lexical.get(ids[i]),
            row=rows[i],
            seen_before=ids[i] in exclude_ids or bool(rows[i].get("fingerprint") in exclude_fps),
        )
        for i in chosen
    ]
    return SearchResult(
        hits=hits,
        candidates=len(rows),
        excluded_seen=excluded,
        fts_terms=match_terms,
        pool_exhausted=bool(excluded and len(hits) < top_k),
    )


def global_probe(query: str, top_k: int = 20, in_syllabus_only: bool = True) -> list[dict[str, Any]]:
    """Corpus-wide lexical+dense probe, used to resolve a query by what it matches.

    Concept-name similarity alone cannot resolve "block sliding down a rough incline,
    angle of repose": no concept is named that. The chunks that match it are all
    friction chunks, so the material itself votes for the concept. This is the
    retrieve-then-resolve path, and it costs one FTS query plus one 10k x 256 dot
    product — about a millisecond.
    """
    conn = store.corpus()
    match = _fts_query(query)
    lexical: dict[str, float] = {}
    if match:
        try:
            rows = conn.execute(
                """
                SELECT c.content_id AS content_id, bm25(chunks_fts, 4.0, 2.0, 1.0) AS score
                FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid
                WHERE chunks_fts MATCH ? AND (c.in_syllabus = 1 OR ? = 0)
                ORDER BY score LIMIT ?
                """,
                (match, int(in_syllabus_only), top_k * 4),
            ).fetchall()
            lexical = {r["content_id"]: -float(r["score"]) for r in rows}
        except Exception:  # noqa: BLE001
            lexical = {}

    qvec = embedder.encode_one(query)
    sims = store.vectors() @ qvec
    dense_rows = np.argsort(-sims)[: top_k * 4]
    dense_ids = conn.execute(
        f"SELECT content_id, concept_id, chapter, vec_row, in_syllabus FROM chunks "
        f"WHERE vec_row IN ({','.join('?' * len(dense_rows))})",
        tuple(int(r) for r in dense_rows),
    ).fetchall()
    by_id = {r["content_id"]: dict(r) for r in dense_ids}

    if lexical:
        marks = ",".join("?" * len(lexical))
        for row in conn.execute(
            f"SELECT content_id, concept_id, chapter, vec_row, in_syllabus FROM chunks "
            f"WHERE content_id IN ({marks})",
            tuple(lexical),
        ):
            by_id.setdefault(row["content_id"], dict(row))

    rank_dense = {
        by_id[cid]["content_id"]: pos
        for pos, cid in enumerate(
            sorted(
                (r["content_id"] for r in by_id.values() if r["vec_row"] is not None),
                key=lambda cid: -float(sims[int(by_id[cid]["vec_row"])]),
            )
        )
    }
    rank_lex = {
        cid: pos for pos, (cid, _s) in enumerate(sorted(lexical.items(), key=lambda kv: -kv[1]))
    }
    scored = []
    for cid, row in by_id.items():
        if in_syllabus_only and not row["in_syllabus"]:
            continue
        score = 0.0
        if cid in rank_dense:
            score += 1.0 / (RRF_K + rank_dense[cid])
        if cid in rank_lex:
            score += 1.0 / (RRF_K + rank_lex[cid])
        scored.append(
            {
                "content_id": cid,
                "concept_id": row["concept_id"],
                "chapter": row["chapter"],
                "score": score,
                "dense": float(sims[int(row["vec_row"])]) if row["vec_row"] is not None else 0.0,
            }
        )
    scored.sort(key=lambda r: -r["score"])
    return scored[:top_k]
