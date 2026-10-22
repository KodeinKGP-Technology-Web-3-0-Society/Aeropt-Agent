"""The retrieval entry point: one call, a grounded pack or an honest refusal.

    pack = service.retrieve("give me a pyq on rolling motion", user_id=uid,
                            content_type="pyq", state=turn_state)

What a caller gets back is deliberately shaped to make hallucination awkward:

* every chunk carries `citation` (book + chapter + section, or the exam paper),
  so a claim in the answer can be traced to a line in the corpus;
* `confidence` is `ok` / `low` / `unresolved`, and `low`/`unresolved` come with a
  `note` the composing model is expected to honour — "say you are not sure",
  "this is outside the syllabus";
* `skipped` turns return no material at all rather than stale chunks from an
  unrelated turn, which is what made the old pipeline answer confidently off-topic;
* questions already served to this student are filtered out before ranking, by id
  and by fingerprint, so "give me another one" cannot return the same problem.

Nothing here calls a network service. A cold call is ~15 ms, a warm one ~5 ms.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Sequence

from retrieval import exam_refs, gate, resolver, search, store

logger = logging.getLogger("aeropt.retrieval")

CONTENT_TYPES = {"theory", "example", "practice", "pyq", "question", "any"}

# Most of the question pool (the 4,967 textbook exercises) is labelled for both exams,
# so the exam filter alone does not change what a Main student is asked. The difficulty
# band does: Advanced questions sit a rung higher than Main ones, and this is a soft
# preference in ranking, not a hard filter, so a thin concept still returns something.
EXAM_DIFFICULTY_TARGET = {"JEE_MAINS": 3, "JEE_ADVANCED": 4}

_NOTE_LOW = (
    "Retrieval confidence is low: something matched, but it may not be what the "
    "student asked about. Lean on it lightly, and say where you are unsure. Do not "
    "present it as the course's answer to their question."
)
_NOTE_UNRESOLVED = (
    "Nothing in the syllabus matches this. Say so plainly. You may still help from "
    "general knowledge, but make clear it is not drawn from their course material."
)
_NOTE_OUT_OF_SYLLABUS = (
    "This concept sits outside the JEE syllabus. Answer if they are curious, but "
    "tell them it is beyond what they are examined on."
)
_NOTE_SUBSTITUTED = (
    "No {asked} exists for this concept, so what came back is {got} instead. Say which "
    "you are giving them — a student who asked for a past-year question should not be "
    "handed a textbook exercise as though it were one."
)
_NOTE_NO_MATERIAL = (
    "No material came back for this concept and content type. Either try another "
    "content type or answer from your own knowledge, saying that it is not from "
    "their course material."
)
_NOTE_EXHAUSTED = (
    "The student has already been served every question this concept has. The items "
    "below are the least recently seen — say openly that they are a repeat, or move "
    "to a neighbouring concept or a harder level."
)
_NOTE_NEIGHBOURS = (
    "This concept has no unseen questions left, so these come from neighbouring "
    "concepts in the same chapter. Tell the student that is what you are doing."
)
# Content-level caveats: true of the material itself, whatever the student asked.
_OPTICS_CHAPTER = "Optics"  # the JEE unit; ray and wave optics are one chapter since the merge
_NOTE_OPTICS_CONVENTION = (
    "Sign convention: the OpenStax passages use the real-is-positive convention "
    "(1/d_o + 1/d_i = 1/f for mirrors and lenses, concave mirror f > 0). JEE uses the "
    "Cartesian convention: mirror 1/v + 1/u = 1/f with concave f < 0 and m = -v/u; "
    "lens 1/v - 1/u = 1/f with convex f > 0 and m = v/u; real object u < 0. Teach and "
    "solve in the Cartesian form, converting any formula you quote from these passages."
)
_NOTE_TEST_PAPER = (
    "Items cited as a 'Coaching test paper' are JEE-level questions from coaching "
    "test series, not verified official papers. Do not tell the student they appeared "
    "in JEE, or name a year or shift for them."
)
_NOTE_UNSOLVED = (
    "{n} of these questions have no stored answer. If you use one, solve it fully, "
    "check the numbers with the calculate tool, and never present your result as the "
    "official answer key."
)


_NOTE_EXACT_PAPER = (
    "This is the exact official question the student named ({label}), as printed, with the "
    "official final answer. Serve it as it is; do not substitute or paraphrase it. If the "
    "solution field carries a worked solution, it was checked against the official answer."
)
_NOTE_PAPER_UNAVAILABLE = (
    "The student asked for {label}, but {reason}. Tell them that plainly. Do not present "
    "another question as if it were that one; you may offer a similar official question instead."
)


_NOTE_PAPER_SET = (
    "These are official Physics questions from {label}, as printed, each with its official "
    "final answer. If the student asked for a question from that paper, serve one of these "
    "exactly and name it by its citation (e.g. 'JEE Advanced 2016 Paper 2 Q7'). Do not write "
    "your own question and attribute it to {label}."
)
_NOTE_PAPER_SET_EMPTY = (
    "The student asked for questions from {label}, but none from that paper are in the "
    "verified set. Tell them so plainly. Never write a question yourself and present it as "
    "being from {label}; you may offer an official question from another year instead."
)
_PAPER_SET_SIZE = 5


def _named_paper_set(query: str, started: float, user_id: str = "") -> dict[str, Any] | None:
    """A request for questions from one exam year or paper, with no question number."""
    ref = exam_refs.parse_paper(query)
    if ref is None:
        return None
    rows = store.chunks_by_id_prefix(ref.id_prefix)
    if not rows:
        return {"retrieved": False, "notes": [_NOTE_PAPER_SET_EMPTY.format(label=ref.label)]}
    # Unseen questions first, so "another 2016 question" does not repeat one.
    seen = set(store.served_ids(user_id)) if user_id else set()
    rows = sorted(rows, key=lambda r: r["content_id"] in seen)[:_PAPER_SET_SIZE]
    chunks = [search.Hit(r["content_id"], 1.0, 0.0, 0.0, None, None, r).as_chunk() for r in rows]
    return {
        "decision": {"action": "retrieve", "reason": f"the student asked for {ref.label} questions"},
        "query": query,
        "content_type": "pyq",
        "content_type_used": "pyq",
        "retrieved": True,
        "in_scope": True,
        "confidence": "ok",
        "concept": {"in_scope": True, "concept_id": rows[0]["concept_id"], "matched_by": "named official paper"},
        "chunks": chunks,
        "citations": [c["citation"] for c in chunks],
        "notes": [_NOTE_PAPER_SET.format(label=ref.label)],
        "novelty": {},
        "prerequisites": [],
        "scope_used": "named paper",
        "took_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _named_paper_question(query: str, started: float) -> dict[str, Any] | None:
    """A request for one specific official question ("JEE Advanced 2019 paper 1 Q5")."""
    ref = exam_refs.parse(query)
    if ref is None:
        return None
    rows = store.chunks_by_ids([ref.content_id])
    if not rows:
        return {"retrieved": False, "notes": [_NOTE_PAPER_UNAVAILABLE.format(label=ref.label, reason=ref.unavailable_reason())]}
    row = rows[0]
    chunk = search.Hit(row["content_id"], 1.0, 0.0, 0.0, None, None, row).as_chunk()
    concept = store.concept(row["concept_id"]) or {}
    return {
        "decision": {"action": "retrieve", "reason": f"the student named {ref.label}"},
        "query": query,
        "content_type": "pyq",
        "content_type_used": "pyq",
        "retrieved": True,
        "in_scope": True,
        "confidence": "ok",
        "concept": {
            "in_scope": True, "concept_id": row["concept_id"], "concept_name": concept.get("name", ""),
            "chapter": concept.get("chapter", ""), "unit": concept.get("unit", ""),
            "in_syllabus": True, "matched_by": "named official question",
        },
        "chunks": [chunk],
        "citations": [chunk["citation"]],
        "notes": [_NOTE_EXACT_PAPER.format(label=ref.label)],
        "novelty": {},
        "prerequisites": store.prerequisites(row["concept_id"])[:6] if row["concept_id"] else [],
        "scope_used": "named question",
        "took_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _content_notes(resolution: Any, chunks: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    if resolution.chapter == _OPTICS_CHAPTER or any(c.get("chapter") == _OPTICS_CHAPTER for c in chunks):
        notes.append(_NOTE_OPTICS_CONVENTION)
    if any(c.get("license") == "coaching test paper" for c in chunks):
        notes.append(_NOTE_TEST_PAPER)
    unsolved = sum(1 for c in chunks if c.get("content_type") != "Theory" and not c.get("has_solution"))
    if unsolved:
        notes.append(_NOTE_UNSOLVED.format(n=unsolved))
    return notes


def _confidence(strength: str, chunks: list[dict[str, Any]]) -> str:
    if strength == "none":
        return "unresolved"
    if not chunks:
        return "low"
    return "ok" if strength == "strong" else "low"


def _exam_in_query(query: str) -> str:
    """The exam the student named in this message, if they named one."""
    text = (query or "").lower()
    if re.search(r"\bjee\s+advanced\b|\bjee\s+adv\b", text):
        return "JEE_ADVANCED"
    if re.search(r"\bjee\s+mains?\b", text):
        return "JEE_MAINS"
    return ""


def retrieve(
    query: str,
    *,
    user_id: str = "",
    content_type: str = "",
    concept_id: str = "",
    top_k: int = 5,
    target_exam: str = "",
    state: dict[str, Any] | None = None,
    recent_turns: list[dict[str, Any]] | None = None,
    forced: bool = False,
    exclude_seen: bool | None = None,
    difficulty_target: int | None = None,
    mark: bool = True,
    allow_out_of_syllabus: bool = True,
) -> dict[str, Any]:
    """Resolve, decide, search and ground one turn."""
    started = time.perf_counter()
    state = state or {}
    recent_turns = recent_turns or state.get("recent_turns") or []

    named = _named_paper_question(query, started)
    if named is not None:
        if named.get("retrieved"):
            if mark and user_id:
                store.mark_served(user_id, named["chunks"], purpose="pyq")
            return named
        # The named question is not in the verified set: say so, then retrieve normally.
        unavailable_note = named["notes"][0]
    else:
        unavailable_note = ""
        paper_set = _named_paper_set(query, started, user_id)
        if paper_set is not None:
            if paper_set.get("retrieved"):
                if mark and user_id:
                    store.mark_served(user_id, paper_set["chunks"], purpose="pyq")
                return paper_set
            unavailable_note = paper_set["notes"][0]

    decision = gate.decide(query, recent_turns=recent_turns, state=state, forced=forced)
    requested_type = content_type or decision.content_type
    if requested_type not in CONTENT_TYPES:
        requested_type = "theory"

    pack: dict[str, Any] = {
        "decision": decision.as_dict(),
        "query": query,
        "content_type": requested_type,
        "chunks": [],
        "citations": [],
        "notes": [],
        "took_ms": 0.0,
    }

    if decision.action in {"skip", "clarify"} and not forced:
        pack.update(
            {
                "retrieved": False,
                "confidence": "skipped",
                "reason": decision.reason,
            }
        )
        pack["took_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return pack

    resolution = resolver.resolve(
        query,
        prefer_concept_id=concept_id or str(state.get("primary_concept_id") or ""),
        recent_turns=recent_turns,
    )
    if concept_id and concept_id != resolution.concept_id:
        # An explicit concept_id from the agent or the roadmap wins over inference.
        forced_concept = store.concept(concept_id)
        if forced_concept:
            resolution = resolver.Resolution(
                in_scope=True,
                concept_id=concept_id,
                concept_name=forced_concept["name"],
                chapter=forced_concept["chapter"],
                unit=forced_concept["unit"] or "",
                in_syllabus=bool(forced_concept["in_syllabus"]),
                confidence=1.0,
                how="concept given by the caller",
                strength="strong",
                chapter_concepts=[
                    c["concept_id"]
                    for c in store.all_concepts()
                    if c["chapter_id"] == forced_concept["chapter_id"]
                ],
            )

    pack["concept"] = resolution.as_dict()

    if not resolution.in_scope:
        pack.update(
            {
                "retrieved": False,
                "confidence": "unresolved",
                "in_scope": False,
                "reason": resolution.how,
            }
        )
        pack["notes"].append(_NOTE_UNRESOLVED)
        pack["took_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return pack

    # `reuse` is only honest while the topic has not moved. The gate cannot know
    # that — it never looks at the syllabus — so the check happens here, after
    # resolution: same concept, keep what we have; new concept, retrieve.
    if decision.action == "reuse" and not forced:
        current = str(state.get("primary_concept_id") or "")
        if current and current == resolution.concept_id and state.get("retrieved_chunks"):
            pack.update(
                {
                    "retrieved": False,
                    "confidence": "reused",
                    "in_scope": True,
                    "reason": "the material already on this turn covers the same concept",
                    "chunks": list(state.get("retrieved_chunks") or []),
                }
            )
            pack["took_ms"] = round((time.perf_counter() - started) * 1000, 2)
            return pack

    asked_exam = _exam_in_query(query)
    exam = asked_exam or (target_exam or "")
    # "a JEE Advanced question" means an official paper, not a coaching sheet and
    # not a textbook exercise standing in for one.
    prefer_official = requested_type == "pyq" and exam.upper() == "JEE_ADVANCED"
    is_question = requested_type in {"practice", "pyq", "example", "question"}
    if difficulty_target is None and is_question:
        # The learner's own proficiency target wins when the planner supplied one
        # (bridge passes it through); this is the fallback when nothing else knows.
        difficulty_target = EXAM_DIFFICULTY_TARGET.get(exam.upper())
    if exclude_seen is None:
        exclude_seen = is_question or bool(decision.notes.get("novelty_required"))

    scope_ids = [resolution.concept_id, *resolution.chapter_concepts]
    exclude_ids: set[str] = set()
    exclude_fps: set[str] = set()
    if exclude_seen and user_id:
        served = store.served_ids(user_id)
        exclude_ids = set(served)
        exclude_fps = store.served_fingerprints(user_id)
    # Anything already shown in this session, even before it reached the ledger.
    exclude_ids.update(state.get("seen_follow_up_ids") or [])

    prereqs = store.prerequisites(resolution.concept_id, max_depth=2)
    result = search.search(
        query,
        concept_id=resolution.concept_id,
        content_type=requested_type,
        chapter_concepts=resolution.chapter_concepts,
        prereq_concepts=[p["concept_id"] for p in prereqs],
        target_exam=exam,
        top_k=top_k,
        exclude_ids=exclude_ids,
        exclude_fingerprints=exclude_fps,
        difficulty_target=difficulty_target,
        in_syllabus_only=not allow_out_of_syllabus or resolution.in_syllabus,
        prefer_official=prefer_official,
    )

    exhausted = False
    if not result.hits and exclude_ids and is_question:
        # Everything left was already seen. Rather than pretend the concept has no
        # questions, serve the least recently seen and say so.
        exhausted = True
        result = search.search(
            query,
            concept_id=resolution.concept_id,
            content_type=requested_type,
            chapter_concepts=resolution.chapter_concepts,
            prereq_concepts=[p["concept_id"] for p in prereqs],
            target_exam=exam,
            top_k=top_k,
            difficulty_target=difficulty_target,
            in_syllabus_only=not allow_out_of_syllabus or resolution.in_syllabus,
            prefer_official=prefer_official,
        )
        seen_at = store.served_ids(user_id) if user_id else {}
        result.hits.sort(key=lambda h: seen_at.get(h.content_id, 0.0))

    chunks = result.chunks
    confidence = _confidence(resolution.strength, chunks)

    concept_unseen = concept_total = 0
    if is_question:
        concept_unseen, concept_total = store.unseen_question_count(
            user_id,
            resolution.concept_id,
            search.TYPE_FAMILIES.get(result.content_type_used or requested_type, ()),
        )
    borrowed = bool(
        is_question
        and concept_total
        and not concept_unseen
        and any(c["primary_concept_id"] != resolution.concept_id for c in chunks)
    )

    pack.update(
        {
            "retrieved": bool(chunks),
            "in_scope": True,
            "confidence": confidence,
            "content_type_used": result.content_type_used or requested_type,
            "scope_used": result.scope_used,
            "chunks": chunks,
            "citations": [c["citation"] for c in chunks],
            "candidates_considered": result.candidates,
            "novelty": {
                "filtered_already_seen": result.excluded_seen,
                "enforced": bool(exclude_seen),
                "pool_exhausted": bool(exhausted or result.pool_exhausted),
                "concept_unseen_remaining": concept_unseen,
                "concept_question_total": concept_total,
                "concept_pool_exhausted": bool(is_question and concept_total and not concept_unseen),
                "borrowed_from_neighbours": borrowed,
            },
            "prerequisites": prereqs[:6],
            "difficulty_target": difficulty_target,
            "exam_scope": exam.upper() or "any",
            "fts_terms": result.fts_terms,
        }
    )

    if confidence == "low":
        pack["notes"].append(_NOTE_LOW)
    if not resolution.in_syllabus:
        pack["notes"].append(_NOTE_OUT_OF_SYLLABUS)
    if not chunks:
        pack["notes"].append(_NOTE_NO_MATERIAL)
    used = result.content_type_used or requested_type
    if chunks and used != requested_type:
        labels = {
            "theory": "explanation material",
            "example": "worked example",
            "practice": "practice question",
            "pyq": "past-year question",
            "question": "question",
            "any": "material",
        }
        pack["notes"].append(
            _NOTE_SUBSTITUTED.format(
                asked=labels.get(requested_type, requested_type),
                got=labels.get(used, used),
            )
        )
    pack["notes"].extend(_content_notes(resolution, chunks))
    if exhausted:
        pack["notes"].append(_NOTE_EXHAUSTED)
    elif borrowed:
        pack["notes"].append(_NOTE_NEIGHBOURS)
    if resolution.ambiguous:
        pack["notes"].append(
            "Two concepts matched almost equally: "
            + ", ".join(c["name"] for c in resolution.candidates[:2])
            + ". If the answer depends on which, ask."
        )

    if mark and user_id and chunks:
        # Only questions are logged as served: re-reading a theory chunk is fine,
        # re-attempting a question the student has already seen is not.
        questions = [c for c in chunks if c["content_type"] != "Theory"]
        if questions:
            store.mark_served(user_id, questions, purpose=requested_type)

    if unavailable_note:
        pack["notes"].insert(0, unavailable_note)
    pack["took_ms"] = round((time.perf_counter() - started) * 1000, 2)
    logger.debug(
        "retrieval %s concept=%s type=%s chunks=%d %.1fms",
        confidence,
        resolution.concept_id,
        pack["content_type_used"],
        len(chunks),
        pack["took_ms"],
    )
    return pack


def practice_batch(
    *,
    user_id: str,
    concept_id: str,
    count: int = 3,
    content_type: str = "practice",
    difficulty_target: int | None = None,
    target_exam: str = "",
    query: str = "",
    require_solution: bool = True,
) -> dict[str, Any]:
    """Unseen questions for a concept, hardest constraint first: never a repeat."""
    concept = store.concept(concept_id)
    if not concept:
        return {"error": f"unknown concept_id '{concept_id}'", "questions": []}
    chapter_concepts = [
        c["concept_id"] for c in store.all_concepts() if c["chapter_id"] == concept["chapter_id"]
    ]
    served = store.served_ids(user_id) if user_id else {}
    result = search.search(
        query or concept["name"],
        concept_id=concept_id,
        content_type=content_type,
        chapter_concepts=chapter_concepts,
        prereq_concepts=[p["concept_id"] for p in store.prerequisites(concept_id)],
        target_exam=target_exam,
        top_k=count,
        exclude_ids=set(served),
        exclude_fingerprints=store.served_fingerprints(user_id) if user_id else set(),
        difficulty_target=difficulty_target,
        require_solution=require_solution,
    )
    exhausted = False
    if len(result.hits) < count:
        exhausted = True
    questions = result.chunks
    if user_id and questions:
        store.mark_served(user_id, questions, purpose=f"{content_type}_batch")
    return {
        "concept_id": concept_id,
        "concept_name": concept["name"],
        "questions": questions,
        "count": len(questions),
        "all_unseen": all(not q["seen_before"] for q in questions),
        "pool_exhausted": exhausted,
        "note": _NOTE_EXHAUSTED if exhausted and questions else "",
    }


def mark_served(user_id: str, chunks: Sequence[dict[str, Any]], purpose: str = "served") -> int:
    return store.mark_served(user_id, chunks, purpose=purpose)


def forget(user_id: str, concept_id: str = "") -> int:
    """Clear the served ledger — for a fresh revision cycle, or in tests."""
    return store.forget_served(user_id, concept_id or None)


def warm() -> dict[str, Any]:
    """Load the embedding model, the concept cache and the lexicon, and open the pool, up front.

    Paying that at startup keeps it off the first student's turn.
    """
    if not store.index_exists():
        return {"warmed": False, "reason": "retrieval database unavailable"}
    started = time.perf_counter()
    retrieve("warm up the local retrieval index", user_id="", mark=False)
    return {"warmed": True, "took_ms": round((time.perf_counter() - started) * 1000, 1)}


def status() -> dict[str, Any]:
    if not store.index_exists():
        return {"ready": False, "reason": "retrieval database unavailable (RETRIEVAL_DATABASE_URL; fill with make push)"}
    meta = store.meta()
    return {"ready": True, **meta}
