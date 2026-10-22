"""Map a student's words onto the syllabus — over ~1000 concepts, in a millisecond.

The hosted resolver lists every concept in an LLM prompt. That was defensible at
33 concepts and is impossible at 1016: the prompt alone would dwarf the turn, and
the model would still pick by vibes. Here the closed set is matched directly:

  lexical  concept name / alias / chapter appearing in the query (exact, high trust)
  dense    query embedding against precomputed concept embeddings (recall)
  graph    the chapter's other concepts, for "teach me rotational motion"

`in_scope` is a real decision, not a formality. A query that matches nothing above
threshold comes back unresolved so the tutor can say "that is outside the syllabus
I cover" — the single most effective anti-hallucination move in the pipeline,
because an unresolved query never gets fake material attached to it.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from retrieval import embedder, store

# Calibrated on 2026-09-17 against this corpus (15 in-syllabus, 12 off-syllabus
# queries — see tests/test_retrieval_local.py):
#
#   concept-name similarity alone does NOT separate the two. Physics queries ran
#   0.37-0.88; other-subject queries ran 0.16-0.42, overlapping badly ("what is the
#   derivative of sin x" scored 0.42, "moment of inertia of a rod" only 0.37).
#   Similarity to the best-matching *chunk* separates far better: 0.51-0.89 for
#   physics, 0.22-0.52 for the rest.
#
# So the result is graded rather than binary. `strong` means the query is
# demonstrably about course material; `weak` means something matched but may not be
# what they asked; `none` means say so plainly. Only `strong` may be presented to a
# student as course material — that grading is what keeps an off-syllabus question
# from being answered with confident, irrelevant physics.
MIN_DENSE = 0.30
STRONG_DENSE = 0.42
PROBE_STRONG = 0.50
# 0.36 -> 0.40 (2026-10-04): NCERT's cricket-ball exercise put "who won the cricket world cup"
# at 0.379. Measured over 17 physics / 13 off-topic queries: lowest physics 0.412 ("why is the
# sky blue"), highest everyday off-topic 0.405 (sport, cooking). Narrow — this guard separates
# physics from everyday chat, not from chemistry/biology (photosynthesis scores 0.62).
PROBE_MIN = 0.40
AMBIGUOUS_MARGIN = 0.04

_lock = threading.Lock()
_cache: dict[str, Any] = {}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


_LATEX_COMMAND = re.compile(r"\\[A-Za-z]+")


def _plain(text: str) -> str:
    """The query without LaTeX command names. Name matching runs on this: "\\text{M}"
    read as the word "text" and matched a chemistry title ("Reactions with \\text{PCl}_5")."""
    return _LATEX_COMMAND.sub(" ", text or "")


def _lexicon() -> dict[str, Any]:
    """Alias → concept lookups, rebuilt whenever the store reloads its concept cache."""
    generation = store.cache_generation()  # outside our lock: the store never calls back in here
    with _lock:
        if _cache and _cache.get("generation") == generation:
            return _cache
        _cache.clear()
        concepts = store.all_concepts()
        by_id = {c["concept_id"]: c for c in concepts}
        # rank 0 = the concept's own name, rank 1 = a section/module title that
        # teaches it. A name match must win, or "photoelectric effect" resolves to
        # whichever glossary term happens to sit in the module of that title.
        alias_to_id: dict[str, tuple[str, int]] = {}
        for c in concepts:
            try:
                titles = json.loads(c["aliases"] or "[]")
            except json.JSONDecodeError:
                titles = []
            ranked = [(c["name"], 0), (c["concept_id"].replace("_", " "), 0)]
            ranked += [(t, 1) for t in titles]
            for name, rank in ranked:
                key = _norm(str(name))
                if len(key) < 4:
                    continue
                prior = alias_to_id.get(key)
                if prior is None or rank < prior[1]:
                    alias_to_id[key] = (c["concept_id"], rank)
        chapters: dict[str, list[str]] = {}
        units: dict[str, list[str]] = {}
        for c in concepts:
            chapters.setdefault(_norm(c["chapter"]), []).append(c["concept_id"])
            if c["unit"]:
                units.setdefault(_norm(c["unit"]), []).append(c["concept_id"])
        linked = {
            row["concept_id"]
            for row in store.query("select distinct concept_id from public.chunk_concepts")
        }
        _cache.update(
            {
                "concepts": concepts,
                "by_id": by_id,
                "alias_to_id": alias_to_id,
                "chapters": chapters,
                "units": units,
                "linked": linked,
                "rows": {c["concept_id"]: int(c["vec_row"]) for c in concepts},
                "vectors": store.concept_vectors(),  # same generation as `rows`
                "generation": generation,
            }
        )
        return _cache


def reset() -> None:
    with _lock:
        _cache.clear()


@dataclass
class Resolution:
    in_scope: bool
    concept_id: str = ""
    concept_name: str = ""
    chapter: str = ""
    unit: str = ""
    in_syllabus: bool = True
    confidence: float = 0.0
    how: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)
    chapter_concepts: list[str] = field(default_factory=list)
    ambiguous: bool = False
    strength: str = "strong"  # strong | weak | none
    evidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "in_scope": self.in_scope,
            "concept_id": self.concept_id,
            "concept_name": self.concept_name,
            "chapter": self.chapter,
            "unit": self.unit,
            "in_jee_syllabus": self.in_syllabus,
            "confidence": round(self.confidence, 3),
            "matched_by": self.how,
            "grounding_strength": self.strength,
            "alternatives": [
                {"concept_id": c["concept_id"], "name": c["name"], "score": round(c["score"], 3)}
                for c in self.candidates[1:4]
            ],
        }


def _lexical_hits(query: str, lex: dict[str, Any]) -> list[tuple[str, str]]:
    """Concepts named outright in the query, with the text that named them: own name
    first, then longest match."""
    qn = " " + _norm(query) + " "
    hits: list[tuple[str, int, str]] = []
    for alias, (cid, rank) in lex["alias_to_id"].items():
        if f" {alias} " in qn:
            hits.append((cid, rank, alias))
    hits.sort(key=lambda h: (h[1], -len(h[2])))
    return [(cid, alias) for cid, _rank, alias in hits]


_PHRASE_STOP = {
    "give", "me", "a", "an", "the", "on", "of", "for", "to", "in", "and", "or",
    "question", "questions", "problem", "problems", "please", "some", "jee",
    "advanced", "mains", "main", "pyq", "past", "year", "previous",
}


def _contained_name(query: str, lex: dict[str, Any], sims: np.ndarray) -> str:
    """Concept whose name contains a multi-word phrase from the query.

    "circular motion" is not the concept's name — that is "Uniform circular
    motion" — so an exact alias miss used to fall through to the nearest
    meaning, which is simple harmonic motion.
    """
    words = [w for w in _norm(query).split() if w not in _PHRASE_STOP and len(w) > 1]
    if len(words) < 2:
        return ""
    labels: list[tuple[str, str, int]] = []
    for c in lex["concepts"]:
        texts = [c["name"]]
        try:
            texts.extend(json.loads(c.get("aliases") or "[]"))
        except json.JSONDecodeError:
            pass
        row = int(c["vec_row"])
        for text in texts:
            key = _norm(str(text))
            if key:
                labels.append((c["concept_id"], key, row))
    for length in range(min(len(words), 6), 1, -1):
        found: list[tuple[int, float, str]] = []
        for start in range(len(words) - length + 1):
            phrase = " ".join(words[start : start + length])
            if len(phrase) < 8:
                continue
            pat = re.compile(rf"(^| ){re.escape(phrase)}( |$)")
            for cid, name, row in labels:
                if pat.search(name):
                    # The tightest label wins. "Uniform and Nonuniform Circular
                    # Motion" beats a long comparison title that merely mentions it.
                    extra = len(name.split()) - length
                    # And the phrase must be most of the label's content words.
                    # "passing through" inside "line passing through two points" names
                    # a maths topic no more than the physics problem it came from;
                    # "circular motion" is most of "uniform and nonuniform circular motion".
                    content = [w for w in name.split() if w not in _PHRASE_STOP]
                    if len(content) - length > length:
                        continue
                    dense = float(sims[row]) if 0 <= row < len(sims) else 0.0
                    found.append((extra, -dense, cid))
        if found:
            found.sort()
            return found[0][2]
    return ""


def _chapter_hit(query: str, lex: dict[str, Any]) -> tuple[str, list[str]]:
    qn = " " + _norm(query) + " "
    best_name, best_ids, best_len = "", [], 0
    for source in ("chapters", "units"):
        for name, ids in lex[source].items():
            if len(name) >= 5 and f" {name} " in qn and len(name) > best_len:
                best_name, best_ids, best_len = name, ids, len(name)
    return best_name, best_ids


def _prefer_grounded(candidates: list[dict[str, Any]], linked: set[str]) -> dict[str, Any]:
    """Nearest concept that has passages, when it is nearly as close as an empty one."""
    best = candidates[0]
    if best["concept_id"] in linked or not linked:
        return best
    alt = next((c for c in candidates if c["concept_id"] in linked), None)
    if alt is not None and best["score"] - alt["score"] <= 0.08:
        return alt
    return best


def resolve(
    query: str,
    *,
    prefer_concept_id: str = "",
    recent_turns: list[dict[str, Any]] | None = None,
    top_k: int = 8,
) -> Resolution:
    lex = _lexicon()
    if not lex["concepts"]:
        return Resolution(in_scope=False, how="the local index is empty", strength="none")

    text = (query or "").strip()
    if not text:
        return Resolution(in_scope=False, how="empty query", strength="none")

    # A short pronoun-ish turn leans on the concept the conversation is already on.
    words = re.findall(r"[a-zA-Z]+", text)
    if prefer_concept_id and len(words) <= 3:
        concept = lex["by_id"].get(prefer_concept_id)
        if concept:
            return _from_concept(concept, 0.7, "carried over from the current topic", lex)

    # Context only supplements the query; it never replaces it, or a follow-up would
    # keep resolving to whatever was discussed three turns ago.
    context = ""
    for turn in (recent_turns or [])[-2:]:
        if turn.get("role") == "user":
            context += " " + str(turn.get("content") or "")[:200]
    probe_text = text if len(words) > 3 else (text + context)

    qvec = embedder.encode_one(probe_text)
    sims = lex["vectors"] @ qvec
    order = np.argsort(-sims)[: max(top_k, 8)]
    concepts = lex["concepts"]
    candidates = [
        {
            "concept_id": concepts[i]["concept_id"],
            "name": concepts[i]["name"],
            "chapter": concepts[i]["chapter"],
            "unit": concepts[i]["unit"],
            "in_syllabus": bool(concepts[i]["in_syllabus"]),
            "score": float(sims[i]),
        }
        for i in order
    ]

    # 1. The query names a concept outright — the most trustworthy signal there is.
    # Every named concept is tried in order: "half deflection method galvanometer
    # resistance" names "resistance" first, and rejecting that one-word match must
    # fall through to the galvanometer topic it also names, not to a vote.
    plain = _plain(text)
    for cid, alias in _lexical_hits(plain, lex):
        concept = lex["by_id"][cid]
        dense = float(sims[lex["rows"][cid]])
        # A one-word name is a trap: "gauss law problems" contains the magnetic-field
        # unit "gauss", and "power dissipated in a resistor" contains mechanics'
        # "power". Keep a one-word match only while meaning agrees with it. The test is
        # on the words that matched, not the concept's name: a one-word alias ("range")
        # of a long-named concept passed it and filed a physics error-analysis problem
        # under "Functions: domain, codomain and range".
        if " " in alias or best_dense(sims) - dense <= 0.05:
            res = _from_concept(
                concept, max(0.6, min(1.0, 0.55 + dense)), "the query names this concept", lex
            )
            res.candidates = _promote(candidates, cid, concept, dense)
            res.evidence = dense
            return res

    # 2. The query names a chapter or unit: "teach me rotational motion".
    chapter_name, chapter_ids = _chapter_hit(plain, lex)
    if chapter_ids:
        ranked = sorted(chapter_ids, key=lambda cid: lex["by_id"][cid]["ord"])
        best_in_chapter = max(chapter_ids, key=lambda cid: float(sims[lex["rows"][cid]]))
        dense = float(sims[lex["rows"][best_in_chapter]])
        pick = best_in_chapter if dense >= MIN_DENSE else ranked[0]
        concept = lex["by_id"][pick]
        res = _from_concept(
            concept,
            max(0.55, min(1.0, 0.5 + dense)),
            f"the query names the chapter '{chapter_name}'",
            lex,
        )
        res.chapter_concepts = ranked
        res.candidates = _promote(candidates, pick, concept, dense)
        res.evidence = dense
        return res

    # 2b. The query uses the topic's words without its full name ("circular motion"
    # inside "Uniform circular motion"). A name contains that phrase; meaning
    # search must not override it.
    contained = _contained_name(plain, lex, sims)
    if contained:
        concept = lex["by_id"][contained]
        dense = float(sims[lex["rows"][contained]])
        res = _from_concept(
            concept,
            max(0.6, min(1.0, 0.55 + max(dense, 0.0))),
            "the query names this concept",
            lex,
        )
        res.candidates = _promote(candidates, contained, concept, dense)
        res.evidence = dense
        return res

    # 3. Let the material vote: the chunks that match the query point at the concept
    #    they teach, which resolves phrasings no concept name contains ("block
    #    sliding down a rough incline, angle of repose").
    voted = _vote_from_chunks(text, lex, sims)
    if voted is not None:
        return voted

    # 4. Nothing matched the corpus. Fall back to the nearest concept by name only,
    #    and mark it weak — the tutor must not present this as course material.
    #    An unlinked glossary term ("baryon") must not win when a concept that
    #    actually has passages is almost as close.
    best = _prefer_grounded(candidates, lex.get("linked") or set())
    if best["score"] < MIN_DENSE:
        return Resolution(
            in_scope=False,
            confidence=float(best["score"]),
            how="nothing in the syllabus is close to this",
            candidates=candidates,
            strength="none",
            evidence=float(best["score"]),
        )

    concept = lex["by_id"][best["concept_id"]]
    margin = best["score"] - (candidates[1]["score"] if len(candidates) > 1 else 0.0)
    res = _from_concept(concept, best["score"], "nearest concept by meaning", lex)
    res.candidates = candidates
    res.ambiguous = margin < AMBIGUOUS_MARGIN and best["score"] < STRONG_DENSE
    res.strength = "weak"
    res.evidence = float(best["score"])
    return res


def _vote_from_chunks(
    query: str, lex: dict[str, Any], sims: np.ndarray, *, min_votes: float = 0.12
) -> "Resolution | None":
    """Resolve by which concept the best-matching material belongs to."""
    from retrieval import search  # local import: search imports nothing from here

    probe = search.global_probe(query, top_k=16)
    if not probe:
        return None
    # The material may only vote when it is genuinely close to the query. Without
    # this, "best pizza in delhi" wins a vote from whichever chunk happens to share
    # a word with it, and an off-syllabus question gets real physics attached —
    # the exact failure this pipeline exists to prevent.
    probe_top = max(row["dense"] for row in probe)
    if probe_top < PROBE_MIN:
        return None
    tally: dict[str, float] = {}
    for rank, row in enumerate(probe):
        cid = row["concept_id"]
        if cid not in lex["by_id"]:
            continue
        tally[cid] = tally.get(cid, 0.0) + 1.0 / (4 + rank)
    if not tally:
        return None
    winner, weight = max(tally.items(), key=lambda kv: kv[1])
    if weight < min_votes:
        return None
    concept = lex["by_id"][winner]
    dense = float(sims[lex["rows"][winner]])
    confidence = min(1.0, 0.45 + weight + max(0.0, dense) * 0.3)
    res = _from_concept(concept, confidence, "the matching material belongs to this concept", lex)
    res.strength = "strong" if probe_top >= PROBE_STRONG else "weak"
    res.evidence = probe_top
    res.candidates = [
        {
            "concept_id": cid,
            "name": lex["by_id"][cid]["name"],
            "chapter": lex["by_id"][cid]["chapter"],
            "unit": lex["by_id"][cid]["unit"],
            "in_syllabus": bool(lex["by_id"][cid]["in_syllabus"]),
            "score": w,
        }
        for cid, w in sorted(tally.items(), key=lambda kv: -kv[1])[:5]
    ]
    return res


def best_dense(sims: np.ndarray) -> float:
    return float(np.max(sims)) if sims.size else 0.0


def _promote(
    candidates: list[dict[str, Any]], cid: str, concept: dict[str, Any], dense: float
) -> list[dict[str, Any]]:
    head = {
        "concept_id": cid,
        "name": concept["name"],
        "chapter": concept["chapter"],
        "unit": concept["unit"],
        "in_syllabus": bool(concept["in_syllabus"]),
        "score": dense,
    }
    rest = [c for c in candidates if c["concept_id"] != cid]
    return [head, *rest]


def _from_concept(
    concept: dict[str, Any], confidence: float, how: str, lex: dict[str, Any]
) -> Resolution:
    return Resolution(
        in_scope=True,
        concept_id=concept["concept_id"],
        concept_name=concept["name"],
        chapter=concept["chapter"],
        unit=concept["unit"] or "",
        in_syllabus=bool(concept["in_syllabus"]),
        confidence=confidence,
        how=how,
        chapter_concepts=sorted(
            lex["chapters"].get(_norm(concept["chapter"]), []),
            key=lambda cid: lex["by_id"][cid]["ord"],
        ),
    )
