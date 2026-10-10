"""Which syllabus chapter each verified question belongs to.

The corpus maps an item to its nearest concept by embedding. Unscoped, over ~955
concepts, that failed on exam stems (rolling disks filed under Stefan-Boltzmann), so
each paper's questions are classified into one of the fixed in-syllabus chapters in a
single text-only call, and the corpus then maps concepts within that chapter.

This is metadata, not content: a wrong chapter misfiles a question but cannot change
its text or its answer. An answer outside the fixed list is ignored, and the corpus
falls back to the old unscoped mapping for that question.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from local_ingest.extract import reader
from retrieval import store

VERSION = "c1"


def chapters() -> list[tuple[str, str, str]]:
    conn = sqlite3.connect(store.CORPUS_DB)
    rows = conn.execute(
        "SELECT chapter_id, title, jee_unit FROM chapters WHERE in_syllabus = 1 "
        "AND volume <> 'JEE syllabus' ORDER BY CAST(ord AS INTEGER)"
    ).fetchall()
    conn.close()
    return rows


def classify(rows: list[dict[str, Any]], cache_dir: Path) -> dict[str, str]:
    """{question id: chapter_id} for one paper's questions."""
    options = chapters()
    valid = {cid for cid, _t, _u in options}
    listing = "\n".join(f"{cid}: {title} ({unit})" for cid, title, unit in options)
    questions = "\n\n".join(
        f"[{r['id']}] {(r.get('paragraph') or '')[:600]} {r['stem'][:900]}" for r in rows
    )
    prompt = (
        "Classify each JEE Advanced physics question into the ONE chapter whose physics it "
        "mainly tests. Choose only chapter ids from this list:\n\n" + listing +
        "\n\nQuestions:\n\n" + questions +
        '\n\nReturn only JSON: {"<question id>": "<chapter_id>", ...} with every question id. Do not solve anything.'
    )
    reply = reader.ask(prompt, None, cache_dir=cache_dir, version=VERSION, name="chapters")
    return {qid: cid for qid, cid in (reply or {}).items() if cid in valid}


def apply(target: Path, cache_root: Path) -> int:
    rows = [json.loads(line) for line in target.read_text().splitlines() if line.strip()]
    by_paper: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_paper.setdefault(f"jee_adv_{r['year']}_p{r['paper']}", []).append(r)
    changed = 0
    for slug, paper_rows in by_paper.items():
        todo = [r for r in paper_rows if not r.get("chapter_id")]
        if not todo:
            continue
        found = classify(sorted(todo, key=lambda r: r["id"]), cache_root / slug / "vision")
        for r in todo:
            if r["id"] in found:
                r["chapter_id"] = found[r["id"]]
                changed += 1
    target.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return changed
