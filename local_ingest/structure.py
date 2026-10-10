"""The structured form of a question — what the practice pane renders and the grader checks.

    {"stem": str, "figure": str, "options": [{"label": "A", "text": str}] | None,
     "parts": [{"label": "a", "text": str}] | None,   # sub-questions of a multi-part problem
     "qtype": str, "answer": {"kind": "letters"|"integer"|"range"|"any_of", ...} | None,
     "built_by": "source" | "split" | "architect"}

Flattening a question into one string and re-parsing it on screen broke real questions:
"[Figure: Four option graphs labeled (A), (B), (C), and (D) …]" was split into options
",", ",", ", and". So structure is built here, once, from the source's own fields where it
has them (official papers, coaching sets), by a maths-safe split of inline options otherwise,
and by the question architect (local_ingest/architect.py) for what neither can do — and
every structure is checked: it may not add, drop or change a number or a word of substance.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from utils.latex_guidelines import find_latex_issues

ROOT = Path(__file__).resolve().parents[1]
ARCHITECT = ROOT / ".local-run" / "sources" / "architect" / "questions.jsonl"

_FIGURE = re.compile(r"\[Figure[^\]]*?:\s*((?:[^\[\]]|\[[^\[\]]*\])*)\]", re.S)
_MATH = re.compile(r"\$\$[\s\S]*?\$\$|(?<!\\)\$[^$\n]*?(?<!\\)\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]")
# Options are (A)-(D) or (1)-(4). Lower-case (a), (b), … are the parts of a multi-part question
# (OpenStax, NCERT): splitting those into "options" made a 4-part problem look like an MCQ.
_LABEL_SETS = (("A", "B", "C", "D"), ("1", "2", "3", "4"))


def answer_dict(raw: str) -> dict[str, Any] | None:
    """An official answer as structure, via the key parser used at ingest."""
    from local_ingest.exams import keys

    entry = keys.normalise(raw or "")

    def one(e) -> dict[str, Any] | None:
        if e.kind == "letters":
            return {"kind": "letters", "letters": e.letters}
        if e.kind == "integer":
            return {"kind": "integer", "value": e.value}
        if e.kind == "range":
            return {"kind": "range", "low": e.low, "high": e.high}
        return None

    if entry.kind == "any_of":
        alts = [one(a) for a in entry.alternatives]
        return {"kind": "any_of", "alternatives": alts} if all(alts) else None
    return one(entry)


def split_figure(text: str) -> tuple[str, str]:
    """('text without figure descriptions', 'the descriptions'), brackets inside kept intact."""
    figures = [m.group(1).strip() for m in _FIGURE.finditer(text or "")]
    return _FIGURE.sub(" ", text or "").strip(), "\n".join(f for f in figures if f)


def split_inline(text: str) -> tuple[str, list[dict[str, str]]] | None:
    """Stem and options for "… ? (A) … (B) … (C) … (D) …", markers outside maths only."""
    masked = list(text)
    for m in _MATH.finditer(text):
        for i in range(m.start(), m.end()):
            masked[i] = " "
    plain = "".join(masked)
    for labels in _LABEL_SETS:
        hits, pos = [], 0
        for label in labels:
            m = re.search(r"(^|[\s.:;?,)])\(\s*" + re.escape(label) + r"\s*\)", plain[pos:])
            if not m:
                break
            start = pos + m.start() + len(m.group(1))
            hits.append((label, start, pos + m.end()))
            pos = pos + m.end()
        if len(hits) == 4:
            stem = text[: hits[0][1]].strip()
            options = [{"label": "ABCD"[i], "text": text[h[2]: hits[i + 1][1] if i < 3 else len(text)].strip()}
                       for i, h in enumerate(hits)]
            if stem and all(o["text"] for o in options):
                return stem, options
    return None


def split_parts(text: str) -> tuple[str, list[dict[str, str]]] | None:
    """Lead-in and parts for "… (a) … (b) … (c) …" — consecutive labels from (a), outside maths."""
    masked = list(text)
    for m in _MATH.finditer(text):
        for i in range(m.start(), m.end()):
            masked[i] = " "
    plain = "".join(masked)
    hits, pos = [], 0
    for label in "abcdefgh":
        m = re.search(r"(^|[\s.:;?,)])\(\s*" + label + r"\s*\)", plain[pos:])
        if not m:
            break
        hits.append((label, pos + m.start() + len(m.group(1)), pos + m.end()))
        pos = pos + m.end()
    if len(hits) < 2:
        return None
    lead = text[: hits[0][1]].strip()
    parts = [{"label": h[0], "text": text[h[2]: hits[i + 1][1] if i + 1 < len(hits) else len(text)].strip()}
             for i, h in enumerate(hits)]
    return (lead, parts) if all(p["text"] for p in parts) else None


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text or "")


def faithful(original: str, built: dict[str, Any]) -> str | None:
    """None if the structure keeps the question intact; else why it does not."""
    rebuilt = " ".join([built.get("stem", ""), built.get("figure", "")] +
                       [o.get("text", "") for o in built.get("options") or []] +
                       [p.get("text", "") for p in built.get("parts") or []])
    original = re.sub(r"\(\s*[1-4A-Da-h]\s*\)", " ", original or "")  # option / part labels are not data
    original = re.sub(r"^\s*Q\.?\s*\d+\s*", "", original)  # nor is the paper's question number
    missing = [n for n in _numbers(original) if n not in _numbers(rebuilt)]
    added = [n for n in _numbers(rebuilt) if n not in _numbers(original)]
    if missing or added:
        return f"numbers changed (missing {missing[:3]}, added {added[:3]})"
    words = lambda t: set(re.findall(r"[a-z]{4,}", re.sub(r"\\[a-zA-Z]+", " ", t or "").lower()))
    lost = words(original) - words(rebuilt)
    if len(lost) > max(2, 0.05 * len(words(original))):
        return f"words lost: {sorted(lost)[:5]}"
    for part in [built.get("stem", "")] + [o.get("text", "") for o in built.get("options") or []]:
        if find_latex_issues(part):
            return "maths would not render"
    if re.search(r"(^|\s)\(\s*[A-D]\s*\)\s", built.get("stem", "")) and built.get("options"):
        return "option markers left in the stem"
    return None


def _architect_cache() -> dict[str, dict[str, Any]]:
    if not ARCHITECT.exists():
        return {}
    out = {}
    for line in ARCHITECT.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["content_id"]] = row
    return out


def text_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def attach(items: list[dict[str, Any]]) -> dict[str, int]:
    """Give every question item a validated `structured` field. Returns counts by method."""
    cache = _architect_cache()
    counts: dict[str, int] = {}
    for it in items:
        if not it.get("is_question"):
            continue
        # Candidates in order of trust: the source's own fields, the architect's validated
        # layout, the rule-based split. The first that keeps the question intact is used.
        tries: list[tuple[dict[str, Any], str]] = []
        if it.get("structured"):
            tries.append((dict(it["structured"]), "source"))
        cached = cache.get(it["content_id"])
        if cached and cached.get("text_hash") == text_hash(it["core_text"]) and cached.get("structured"):
            tries.append((dict(cached["structured"]), "architect"))
        stem, figure = split_figure(it["core_text"])
        inline = split_inline(stem)
        parts = None if inline else split_parts(stem)
        if inline:
            tries.append(({"stem": inline[0], "figure": figure, "options": inline[1]}, "split"))
        elif parts:
            tries.append(({"stem": parts[0], "figure": figure, "options": None, "parts": parts[1]}, "split"))
        else:
            tries.append(({"stem": stem, "figure": figure, "options": None}, "split"))
        source = it.get("structured") or {}
        chosen, method, problems = None, "plain", []
        for built, how in tries:
            built.setdefault("qtype", source.get("qtype") or it.get("question_type") or "")
            built["answer"] = built.get("answer") or source.get("answer") or it.get("answer_struct")
            problem = faithful(it["core_text"], built)
            if problem is None:
                chosen, method = built, how
                break
            problems.append(f"{how}: {problem}")
        if chosen is None:
            # Plain question rather than a structure that misstates it.
            chosen = {"stem": stem, "figure": figure, "options": None,
                      "qtype": source.get("qtype") or it.get("question_type") or "",
                      "answer": source.get("answer") or it.get("answer_struct"), "rejected": "; ".join(problems)}
        chosen["built_by"] = method
        it["structured"] = chosen
        counts[method] = counts.get(method, 0) + 1
    return counts
