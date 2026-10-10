"""Make every JEE syllabus topic (jee_topics.py) a studyable concept in the index.

Runs after build_index has written the corpus, and on its own against an
existing index:  .venv/bin/python -m local_ingest.syllabus_topics

For each topic, in order:
  1. An existing concept with the same name or alias in the same JEE unit is the
     topic — link to it.
  2. Otherwise create a concept (source='syllabus'), placed in the unit's chapter
     whose passages mention it most, attached (role='syllabus') to the passages
     that match its phrases, and given an embedding so the resolver can find it.
  3. Units the textbook has no chapter for (Experimental Skills) get a chapter.

Every topic is recorded in `syllabus_topics` with how much course material backs
it, so the study page can show the full syllabus in exam order and say plainly
when a topic is taught by the tutor without a textbook section behind it.

Idempotent: everything it added last time is removed first. Existing concepts,
their passage mapping and their vectors are never modified.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter
from typing import Any

import numpy as np

from local_ingest.jee_topics import JEE_TOPICS, SOURCE
from local_ingest.syllabus import slug
from retrieval import embedder, store

PASSAGES_PER_TOPIC = 25   # links to the best-matching passages; enough to teach and practise from
MIN_MATERIAL = 3          # fewer linked passages than this = no real course material

SCHEMA = """
CREATE TABLE IF NOT EXISTS syllabus_topics (
  unit        TEXT,
  unit_ord    INTEGER,
  topic       TEXT,
  topic_ord   INTEGER,
  concept_id  TEXT,
  chapter_id  TEXT,
  created     INTEGER,   -- 1 = concept made for this topic, 0 = existing concept
  material    INTEGER,   -- passages linked to the concept
  PRIMARY KEY (unit_ord, topic_ord)
);
"""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower().replace("’", "'")).strip()


def _phrase_query(phrases: list[str]) -> str:
    """FTS5 OR of exact phrases. Words only, so no operator can leak in."""
    parts = []
    for p in phrases:
        # Tokenise like the index (unicode61): an apostrophe splits, so
        # "maker's" is "maker" "s" there and must be here too.
        words = re.findall(r"[a-z0-9]+", p.lower())
        if words:
            parts.append('"' + " ".join(words) + '"')
    return " OR ".join(dict.fromkeys(parts))


def _reset(conn: sqlite3.Connection) -> int:
    """Remove the previous run. Returns the number of base concept vectors."""
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM chunk_concepts WHERE role = 'syllabus'")
    conn.execute("DELETE FROM concepts WHERE source = 'syllabus'")
    conn.execute("DELETE FROM chapters WHERE volume = 'JEE syllabus'")  # chapters this pass added
    conn.execute("DELETE FROM syllabus_topics")
    row = conn.execute("SELECT value FROM meta WHERE key = 'base_concept_vectors'").fetchone()
    if row:
        return int(row[0])
    base = int(conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0])
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('base_concept_vectors', ?)", (str(base),))
    return base


def apply(verbose: bool = True) -> dict[str, Any]:
    conn = sqlite3.connect(store.CORPUS_DB)
    conn.row_factory = sqlite3.Row
    base = _reset(conn)
    vectors = np.load(store.CONCEPT_VECTORS)[:base]

    concepts = [dict(r) for r in conn.execute("SELECT * FROM concepts")]
    chapters = [dict(r) for r in conn.execute("SELECT * FROM chapters ORDER BY CAST(ord AS INTEGER)")]
    by_unit: dict[str, list[dict[str, Any]]] = {}
    for ch in chapters:
        if str(ch.get("in_syllabus")) in ("1", "True") and ch.get("jee_unit"):
            by_unit.setdefault(ch["jee_unit"], []).append(ch)
    chapter_title = {ch["chapter_id"]: ch["title"] for ch in chapters}

    # Rule 1 lookups. Exact concept NAMES first, aliases only after: every
    # concept carries its OpenStax module title as an alias, so "torque" is an
    # alias of everything in the Torque module ("lever arm" won otherwise).
    by_name: dict[str, list[dict[str, Any]]] = {}
    by_alias: dict[str, list[dict[str, Any]]] = {}
    for c in concepts:
        by_name.setdefault(_norm(c["name"]), []).append(c)
        # "simple harmonic motion (SHM)" is the concept for "Simple harmonic motion".
        bare = re.sub(r"\s*\([^)]*\)\s*$", "", c["name"] or "")
        if bare != c["name"]:
            by_name.setdefault(_norm(bare), []).append(c)
        try:
            aliases = json.loads(c.get("aliases") or "[]")
        except (TypeError, ValueError):
            aliases = []
        for n in aliases:
            by_alias.setdefault(_norm(n), []).append(c)
    names = by_name

    taken = {c["concept_id"] for c in concepts}
    max_ord = max([int(ch.get("ord") or 0) for ch in chapters] or [0])
    new_concepts: list[tuple] = []
    new_texts: list[str] = []
    links: list[tuple[str, str, str]] = []
    rows: list[tuple] = []
    stats = Counter()

    for unit_ord, (unit, topics) in enumerate(JEE_TOPICS, start=1):
        unit_chapters = by_unit.get(unit, [])
        if not unit_chapters:
            # The textbook has no chapter for this unit (Experimental Skills).
            max_ord += 1
            ch = {"chapter_id": slug(unit), "title": unit, "volume": "JEE syllabus", "unit": unit,
                  "jee_unit": unit, "in_syllabus": 1, "ord": max_ord}
            conn.execute(
                "INSERT OR REPLACE INTO chapters (chapter_id, title, volume, unit, jee_unit, in_syllabus, ord)"
                " VALUES (?,?,?,?,?,?,?)",
                (ch["chapter_id"], ch["title"], ch["volume"], ch["unit"], ch["jee_unit"], 1, ch["ord"]),
            )
            unit_chapters = [ch]
            chapter_title[ch["chapter_id"]] = unit
            stats["chapters_added"] += 1
        unit_chapter_ids = [ch["chapter_id"] for ch in unit_chapters]

        for topic_ord, (name, *phrases) in enumerate(topics, start=1):
            # Rule 1: same name (or a listed phrase) as an existing concept in this unit.
            existing = None
            for lookup in (by_name, by_alias):
                for candidate in [name, *phrases]:
                    for c in lookup.get(_norm(candidate), []):
                        if c.get("chapter_id") in unit_chapter_ids:
                            existing = c
                            break
                    if existing:
                        break
                if existing:
                    break
            if not existing:
                # Same name, other unit: OpenStax files impulse and collisions under
                # a chapter the scope map puts in Rotational Motion. Name only —
                # a loose phrase match across units would link the wrong thing.
                for c in names.get(_norm(name), []):
                    if str(c.get("in_syllabus")) in ("1", "True"):
                        existing = c
                        break
            if existing:
                material = conn.execute(
                    "SELECT COUNT(*) FROM chunk_concepts WHERE concept_id = ?", (existing["concept_id"],)
                ).fetchone()[0]
                rows.append((unit, unit_ord, name, topic_ord, existing["concept_id"], existing["chapter_id"], 0, material))
                stats["linked"] += 1
                continue

            # Rule 2: a new concept, attached to the passages that teach it.
            match = _phrase_query([name, *phrases])
            marks = ",".join("?" * len(unit_chapter_ids))
            hits = []
            home: list = []
            if match:
                try:
                    hits = conn.execute(
                        f"""SELECT c.content_id, c.chapter_id FROM chunks_fts
                            JOIN chunks c ON c.rowid = chunks_fts.rowid
                            WHERE chunks_fts MATCH ? AND c.chapter_id IN ({marks}) AND c.in_syllabus = 1
                            ORDER BY bm25(chunks_fts, 4.0, 2.0, 1.0) LIMIT ?""",
                        (match, *unit_chapter_ids, PASSAGES_PER_TOPIC),
                    ).fetchall()
                    home = hits  # the chapter is chosen from the topic's own unit only
                    if len(hits) < MIN_MATERIAL:
                        # The material can live in a chapter mapped to another unit.
                        hits = conn.execute(
                            """SELECT c.content_id, c.chapter_id FROM chunks_fts
                               JOIN chunks c ON c.rowid = chunks_fts.rowid
                               WHERE chunks_fts MATCH ? AND c.in_syllabus = 1
                               ORDER BY bm25(chunks_fts, 4.0, 2.0, 1.0) LIMIT ?""",
                            (match, PASSAGES_PER_TOPIC),
                        ).fetchall() or hits
                except sqlite3.Error:
                    hits = []
            # Placed in its own unit even when its passages live elsewhere:
            # "Vernier calipers" belongs under Experimental Skills.
            chapter_id = (
                Counter(h["chapter_id"] for h in home).most_common(1)[0][0] if home else unit_chapter_ids[0]
            )
            cid = slug(name)
            n = 2
            while cid in taken:
                cid = f"{slug(name)}_{n}"
                n += 1
            taken.add(cid)
            chapter = chapter_title.get(chapter_id, unit)
            ord_row = conn.execute(
                "SELECT MIN(ord) FROM concepts WHERE chapter_id = ?", (chapter_id,)
            ).fetchone()[0]
            definition = f"JEE Main syllabus topic in {unit}: {name}."
            new_concepts.append((
                cid, name, definition, chapter_id, chapter, unit, 1, int(ord_row or max_ord),
                json.dumps([p for p in phrases if _norm(p) != _norm(name)]), "syllabus", base + len(new_texts),
            ))
            new_texts.append(f"{name}. {chapter}. {' ; '.join(phrases[:4])}. {definition}")
            links.extend((h["content_id"], cid, "syllabus") for h in hits)
            rows.append((unit, unit_ord, name, topic_ord, cid, chapter_id, 1, len(hits)))
            stats["created"] += 1
            stats["no_material"] += len(hits) < MIN_MATERIAL

    if new_concepts:
        conn.executemany(
            "INSERT INTO concepts (concept_id, name, definition, chapter_id, chapter, unit, in_syllabus,"
            " ord, aliases, source, vec_row) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            new_concepts,
        )
        conn.executemany("INSERT OR IGNORE INTO chunk_concepts (content_id, concept_id, role) VALUES (?,?,?)", links)
        vectors = np.vstack([vectors, embedder.encode(new_texts).astype(vectors.dtype)])
    conn.executemany(
        "INSERT INTO syllabus_topics (unit, unit_ord, topic, topic_ord, concept_id, chapter_id, created, material)"
        " VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('syllabus_source', ?)", (SOURCE,))
    # Keep the recorded totals true: the index's consistency checks compare them
    # with the concept table and the vector file.
    total = conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('concepts', ?)", (str(total),))
    conn.commit()
    conn.close()
    np.save(store.CONCEPT_VECTORS, vectors)
    store.reset_caches()

    stats["topics"] = len(rows)
    if verbose:
        print(f"syllabus topics: {stats['topics']} — linked to existing {stats['linked']}, "
              f"created {stats['created']} ({stats['no_material']} with no course material), "
              f"chapters added {stats['chapters_added']}")
    return dict(stats)


if __name__ == "__main__":
    apply()
    sys.exit(0)
