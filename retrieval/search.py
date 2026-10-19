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

Storage is the Supabase retrieval Postgres (retrieval/store.py): stages 1–2 are one SQL
query per widening step (pgvector cosine + Postgres full text, scored in the database);
fusion, novelty and MMR run here over that candidate list. Full rows and vectors are fetched
only for the final shortlist.
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

# Same exclusions as local_ingest.pg_syllabus_topics.PASSAGE_SKIP. Kept here so a theory
# request does not wait for the next ingest to stop seeing them.
_PASSAGE_SKIP = """
not (c.content_type = 'Theory' and length(c.core_text) < 400 and c.core_text ~* 'exercise')
and not (c.content_type = 'PYQ' and length(c.core_text) < 120)
and not (
  c.source_book ilike 'ALLEN%%'
  and (length(c.core_text) < 180
       or c.core_text ~* 'due care and diligence|shall not hold any responsibility')
)
"""

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
            "structured": _loads(row["structured"]) if "structured" in row.keys() and row["structured"] else None,
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


def _terms(text: str) -> str:
    """Content words of the query, for `public.or_tsquery` (which ORs them)."""
    match = _fts_query(text)
    return " ".join(t.strip('"') for t in match.split(" OR ")) if match else ""


def _fetch_candidates(
    concept_ids: Sequence[str],
    types: Sequence[str],
    *,
    target_exam: str = "",
    require_solution: bool = False,
    in_syllabus_only: bool = True,
    limit: int = MAX_CANDIDATES,
    query: str = "",
    qvec: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    """Candidates linked to the concepts, with `dense` (cosine) and `lexical` (full-text) scores.

    Light rows only — no text or vectors — so a 600-row pool is one small round trip. The
    lexical score is computed over the candidates only: ranking the whole corpus first and
    intersecting afterwards silently drops any candidate outside the global top-N.
    """
    if not concept_ids or not types:
        return []
    params: dict[str, Any] = {
        "ids": list(concept_ids), "types": list(types), "limit": limit,
        "qv": embedder.to_pg(qvec) if qvec is not None else None, "terms": _terms(query),
    }
    where = ["cc.concept_id = any(%(ids)s)", "c.content_type = any(%(types)s)", _PASSAGE_SKIP]
    if require_solution:
        where.append("c.has_solution")
    if in_syllabus_only:
        where.append("coalesce(c.in_syllabus, true)")
    exams = EXAM_ALLOWED.get((target_exam or "").upper())
    if exams:
        where.append("c.target_exam = any(%(exams)s)")
        params["exams"] = list(exams)
    dense = "1 - (c.embedding <=> %(qv)s::vector)" if qvec is not None else "0.0"
    lexical = (
        "case when c.fts @@ public.or_tsquery(%(terms)s)"
        " then ts_rank_cd(c.fts, public.or_tsquery(%(terms)s), 1) else 0 end"
        if params["terms"] else "0.0"
    )
    # A primary-concept chunk is about the concept; a secondary one merely touches it.
    sql = f"""
        select c.content_id, c.content_type, c.subject, c.chapter_id, c.chapter, c.unit,
               c.primary_concept_id as concept_id, c.difficulty, c.license, c.fingerprint,
               c.target_exam, coalesce(c.has_solution, false)::int as has_solution,
               case min(case cc.role when 'primary' then 0 else 1 end) when 0 then 'primary' else 'secondary' end
                 as concept_role,
               {dense} as dense,
               max({lexical}) as lexical
        from public.chunk_concepts cc join public.content c on c.content_id = cc.content_id
        where {' and '.join(where)}
        group by c.content_id
        order by min(case cc.role when 'primary' then 0 else 1 end), c.difficulty, c.content_id
        limit %(limit)s
    """
    return store.query(sql, params)


def _lexical_ranks(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    """content_id -> full-text score for the candidates that matched, best FTS_LIMIT."""
    scored = sorted(((r["content_id"], float(r["lexical"] or 0.0)) for r in rows if r.get("lexical")),
                    key=lambda kv: -kv[1])
    return dict(scored[:FTS_LIMIT])


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
    qvec = embedder.encode_one(query)

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
                    query=query,
                    qvec=qvec,
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


# Source authority, as a ranking preference rather than a filter: an official paper with
# its official key beats a coaching test-paper question on the same concept, but a
# concept with no official question still gets the coaching one.
SOURCE_WEIGHT = {
    "official exam paper (jeeadv.ac.in) with official final answer key": 1.30,
    "past exam paper": 1.30,          # JEE-Bench: official papers, official answers
    "coaching test paper": 0.85,
    # NCERT is what JEE is set from (and uses the JEE sign conventions); OpenStax is 1.0.
    "NCERT textbook (© NCERT); local development use": 1.20,
    # Verified against the book (local_ingest/hcverma.py); JEE-level worked examples.
    "H.C. Verma, Concepts of Physics (Bharati Bhawan); sponsor material": 1.10,
}


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
    lexical = _lexical_ranks(rows)
    match_terms = _fts_query(query)
    dense = np.asarray([float(r.get("dense") or 0.0) for r in rows], dtype=np.float32)

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
        score *= SOURCE_WEIGHT.get(rows[i].get("license") or "", 1.0)
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
    # Full text and vectors for the shortlist only (one query), then diversify over it.
    full = {r["content_id"]: r for r in store.chunks_by_ids([ids[i] for i in shortlist])}
    for i in shortlist:
        if ids[i] in full:
            rows[i] = {**full[ids[i]], "concept_role": rows[i].get("concept_role"), "dense": rows[i].get("dense")}
    if diversify and len(shortlist) > 1:
        vec_of = store.content_vectors([ids[i] for i in shortlist])
        mat = np.stack([vec_of.get(ids[i], np.zeros(embedder.DIM, dtype=np.float32)) for i in shortlist])
        picked = _mmr(list(range(len(shortlist))), mat, top_k, base_scores=scores_arr)
        chosen = [shortlist[p] for p in picked]
    else:
        chosen = shortlist[:top_k]
    # Theory leads with NCERT when NCERT has a strong candidate: it is what JEE is set from
    # and uses JEE notation and sign conventions. A weight could not do this — OpenStax
    # passages titled exactly like the query out-scored NCERT's section on the same idea.
    if chosen and rows[chosen[0]].get("content_type") == "Theory":
        ncert = [i for i in shortlist if str(rows[i].get("license") or "").startswith("NCERT")
                 and rows[i].get("content_type") == "Theory"]
        if ncert and not str(rows[chosen[0]].get("license") or "").startswith("NCERT"):
            lead = ncert[0]
            chosen = [lead] + [i for i in chosen if i != lead][: top_k - 1]

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
    retrieve-then-resolve path: one query — the HNSW nearest neighbours and the
    full-text top matches, unioned and scored in the database.
    """
    n = top_k * 4
    terms = _terms(query)
    syllabus = "and coalesce(in_syllabus, true)" if in_syllabus_only else ""
    lexical_cte = (
        f"""select content_id, ts_rank_cd(fts, q, 1) as lex
            from public.content, public.or_tsquery(%(terms)s) q
            where q is not null and fts @@ q {syllabus} order by 2 desc limit %(n)s"""
        if terms else "select null::text as content_id, null::float4 as lex where false"
    )
    rows = store.query(
        f"""
        with d as (
            select content_id from public.content
            where embedding is not null {syllabus}
            order by embedding <=> %(qv)s::vector limit %(n)s
        ),
        l as ({lexical_cte})
        select c.content_id, c.primary_concept_id as concept_id, c.chapter,
               coalesce(c.in_syllabus, true) as in_syllabus,
               1 - (c.embedding <=> %(qv)s::vector) as dense, l.lex
        from (select content_id from d union select content_id from l) u
        join public.content c using (content_id)
        left join l using (content_id)
        """,
        {"qv": embedder.to_pg(embedder.encode_one(query)), "terms": terms, "n": n},
    )
    rank_dense = {r["content_id"]: pos for pos, r in enumerate(sorted(rows, key=lambda r: -float(r["dense"] or 0)))}
    lexed = sorted((r for r in rows if r["lex"] is not None), key=lambda r: -float(r["lex"]))
    rank_lex = {r["content_id"]: pos for pos, r in enumerate(lexed)}
    scored = []
    for r in rows:
        if in_syllabus_only and not r["in_syllabus"]:
            continue
        cid = r["content_id"]
        score = 1.0 / (RRF_K + rank_dense[cid]) + (1.0 / (RRF_K + rank_lex[cid]) if cid in rank_lex else 0.0)
        scored.append({"content_id": cid, "concept_id": r["concept_id"], "chapter": r["chapter"],
                       "score": score, "dense": float(r["dense"] or 0.0)})
    scored.sort(key=lambda r: -r["score"])
    return scored[:top_k]
