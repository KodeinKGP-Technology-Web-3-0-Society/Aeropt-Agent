"""Join extracted questions to the official key — and refuse every join it cannot prove.

A question is kept only when all of these hold; otherwise it is dropped and the
reason recorded in the report:

  key        the official key has an answer for it, and it is not a bonus/dropped one
  identity   the key document's own copy of the question matches the extracted stem
             (they are two different documents, so a numbering slip shows up here)
  type       the answer's form fits the section: one letter for single-correct and
             matching, letters for multiple-correct, a number or range for numeric, a
             row-by-row map for matrix-match (whose columns must be printed in the stem)
  complete   an MCQ has all four options; a question that refers to a figure has one
             described
"""

from __future__ import annotations

import re
from typing import Any

from local_ingest.exams import keys

MIN_IDENTITY = 0.40
MIN_FIGURE_CHARS = 40
_FIGURE_WORDS = re.compile(r"\b(figure|shown|diagram|graph|circuit shown|as shown|see fig)", re.I)
_ANSWER_LINE = re.compile(r"(?im)^\s*\**\s*(?:Answers?|Ans)\b.*$")
_TYPES = {"single_correct", "multiple_correct", "integer", "numeric", "matching", "paragraph"}


def _qtype(question: dict[str, Any], sections: dict[int, dict[str, Any]], entry: keys.KeyEntry) -> str:
    header = sections.get(question.get("section") or -1) or {}
    qtype = str(header.get("answer_type") or "")
    if qtype == "paragraph":  # paragraph sections are single-correct or numeric in practice
        qtype = "single_correct" if question.get("options") else "numeric"
    if qtype not in _TYPES:
        qtype = "multiple_correct" if (entry.kind == "letters" and len(entry.letters) > 1) else (
            "single_correct" if question.get("options") else "numeric")
    return qtype


def _fits(qtype: str, entry: keys.KeyEntry) -> bool:
    if entry.kind == "matrix":
        return qtype == "matching"
    options = entry.alternatives if entry.kind == "any_of" else [entry]
    for e in options:
        if qtype in {"single_correct", "matching"} and not (e.kind == "letters" and len(e.letters) == 1):
            return False
        if qtype == "multiple_correct" and e.kind != "letters":
            return False
        if qtype in {"integer", "numeric"} and e.kind not in {"integer", "range"}:
            return False
    return True


def display(entry: keys.KeyEntry) -> str:
    if entry.kind == "matrix":
        # "A → R (also accepted: R, T); B → P, S; …"
        rows = []
        for row, accepted in entry.matches.items():
            first, *others = [", ".join(cols) for cols in accepted]
            rows.append(f"{row} → {first}" + (f" (also accepted: {'; '.join(others)})" if others else ""))
        return "; ".join(rows)
    if entry.kind == "any_of":
        return " or ".join(display(a) for a in entry.alternatives) + " (the final key accepts any of these)"
    if entry.kind == "letters":
        return ", ".join(entry.letters)
    if entry.kind == "integer":
        return str(int(entry.value))
    if entry.kind == "range":
        return f"{entry.low:g}" if entry.low == entry.high else f"any value from {entry.low:g} to {entry.high:g}"
    return entry.raw


def physics_block(key_blocks: list[keys.KeyBlock], questions: dict[int, dict[str, Any]]) -> tuple[int, float]:
    """Which key block is Physics: the one the key labels so (tables), else the one whose
    question texts match ours best."""
    for i, block in enumerate(key_blocks):
        if block.subject == "Physics":
            return i, 1.0
    best, best_score = -1, 0.0
    for i, block in enumerate(key_blocks):
        scores = [
            keys.similarity(block.entries[n].context, q["stem"])
            for n, q in questions.items() if n in block.entries
        ]
        score = sum(scores) / len(scores) if scores else 0.0
        if score > best_score:
            best, best_score = i, score
    return best, best_score


def join(paper, questions, sections, key_blocks) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    index, score = physics_block(key_blocks, questions)
    if index < 0 or score < MIN_IDENTITY:
        return [], [{"paper": paper.slug, "qno": None, "reason": f"no key block matches the physics questions (best {score:.2f})"}]
    block = key_blocks[index]

    # A paper numbered straight through (2017: 1-54) has one key block for all subjects;
    # only the Physics range is expected here.
    lo, hi = (min(questions), max(questions)) if questions else (0, -1)
    expected = sorted(n for n in set(block.order) if lo <= n <= hi)
    for n in expected:
        if n not in questions:
            dropped.append({"paper": paper.slug, "qno": n, "reason": "in the key but not extracted from the paper"})

    for n in sorted(questions):
        q = questions[n]
        # 2014's papers print the answer under each question; it must never reach a stem.
        q["stem"] = _ANSWER_LINE.sub("", q["stem"]).strip()

        def drop(reason: str) -> None:
            dropped.append({"paper": paper.slug, "qno": n, "reason": reason})

        entry = block.entries.get(n)
        if entry is None:
            drop("no answer line in the official key")
            continue
        if entry.kind in {"bonus", "unreadable"}:
            drop(f"official key entry is {entry.kind}: {entry.raw!r}")
            continue
        # A table key (2019) prints no question text, so there is nothing to compare; the
        # structural checks below (answer form per section type) still apply to every row.
        identity = keys.similarity(entry.context, q["stem"]) if entry.context else None
        if identity is not None and identity < MIN_IDENTITY:
            drop(f"key's copy of the question does not match the extracted stem ({identity:.2f})")
            continue
        qtype = _qtype(q, sections, entry)
        if not _fits(qtype, entry):
            drop(f"answer {entry.raw!r} does not fit a {qtype} question")
            continue
        opts = q.get("options") or {}
        if entry.kind == "matrix":
            # No options: both columns are printed in the stem, every label the key uses included.
            labels = set(entry.matches) | {c for alts in entry.matches.values() for cols in alts for c in cols}
            if not all(f"({c})" in q["stem"] for c in labels):
                drop("a matrix-match column entry the key uses is missing from the stem")
                continue
        elif qtype in {"single_correct", "multiple_correct", "matching"} and not all(
            str(opts.get(k) or "").strip() for k in "ABCD"
        ):
            drop("an option is missing or empty")
            continue
        if len(q["stem"]) < 25:
            drop("stem too short to be a whole question")
            continue
        if _FIGURE_WORDS.search(q["stem"] + " " + (q.get("paragraph") or "")) and len(q.get("figure") or "") < MIN_FIGURE_CHARS:
            drop("refers to a figure that was not described")
            continue

        kept.append({
            "id": f"{paper.slug}_phy_q{n:02d}",
            "exam": "JEE Advanced",
            "year": paper.year,
            "paper": paper.paper,
            "subject": "Physics",
            "qno": n,
            "section": q.get("section"),
            "qtype": qtype,
            "paragraph": q.get("paragraph") or "",
            "stem": q["stem"],
            "options": {k: opts[k] for k in "ABCD" if k in opts} if opts else None,
            "figure": q.get("figure") or "",
            "answer": display(entry),
            "answer_raw": entry.raw,
            "answer_kind": entry.kind,
            "key_identity": round(identity, 3) if identity is not None else None,
            "paper_url": paper.paper_url,
            "key_url": paper.key_url,
            "pages": q.get("pages") or [],
        })
    return kept, dropped
