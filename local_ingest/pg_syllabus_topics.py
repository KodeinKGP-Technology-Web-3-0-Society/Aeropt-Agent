"""Make every JEE syllabus topic a studyable concept — straight in Postgres.

The passage search is Postgres full text (`public.content.fts`) and everything is written
through the push's open cursor (one transaction with the rest of the corpus):

  1. a concept with the same name/alias in the topic's unit *is* the topic;
  2. otherwise create a concept (source='syllabus') and embed it.

Either way, attach (role='syllabus') the passages whose text matches the topic's
phrases. A section title that only names the methods is not the methods: "Methods
of integration" must also pick up substitution, parts and partial fractions.

Idempotent: what a previous run added is removed first. Existing concepts, their
primary/secondary links and their vectors are never modified.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from local_ingest.jee_topics import JEE_TOPICS, SOURCE, get_topics
from local_ingest.syllabus import slug

PASSAGES_PER_TOPIC = 25   # links to the best-matching passages; enough to teach and practise from
MIN_MATERIAL = 3          # fewer linked passages than this = no real course material
# Words that sit between the terms a heading actually uses ("modulus and the conjugate
# of a complex number") without being the phrase as written.
_STOP = frozenset("a an the of to in for on by and or its from with as at into".split())

# Exercise instruction lines, orphan later-parts of a paper, and handbook page furniture
# match the topic words and then outrank the passage that teaches it.
PASSAGE_SKIP = """
  and not (content_type = 'Theory' and length(core_text) < 400 and core_text ~* 'exercise')
  and not (content_type = 'PYQ' and length(core_text) < 120)
  and not (
    source_book ilike 'ALLEN%%'
    and (length(core_text) < 180
         or core_text ~* 'due care and diligence|shall not hold any responsibility')
  )
"""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower().replace("’", "'")).strip()


def proximity_query(phrase: str) -> str | None:
    """Content words of one phrase, ANDed, for `to_tsquery`.

    `phraseto_tsquery` demands the words in order. A heading such as "The Modulus and
    the Conjugate of a Complex Number" never matches "modulus of complex number" that
    way, because stop-words still occupy positions. Requiring the content words
    anywhere in the passage is enough: the search is already limited to the subject.
    The returned string is only letters, digits and ` & `.
    """
    words = []
    for raw in re.findall(r"[A-Za-z0-9']+", phrase or ""):
        word = re.sub(r"[^A-Za-z0-9]", "", raw)
        if len(word) > 2 and word.lower() not in _STOP:
            words.append(word)
    if len(words) < 2:
        return None
    return " & ".join(words)


def _tsquery(phrases: list[str]):
    """OR of a strict phrase and a looser proximity query, parameters only — no interpolated text."""
    words = [p for p in dict.fromkeys(phrases) if re.search(r"[A-Za-z0-9]", p or "")]
    parts: list[str] = []
    params: list[str] = []
    for phrase in words:
        parts.append("phraseto_tsquery('english', %s)")
        params.append(phrase)
        loose = proximity_query(phrase)
        if loose:
            parts.append("to_tsquery('english', %s)")
            params.append(loose)
    if not parts:
        return None, []
    return " || ".join(parts), params


def _search_passages(cur, phrases: list[str], subject: str, chapter_ids: list[str]):
    """Best passages for these phrases, theory before questions. Home hits stay inside the unit."""
    expr, params = _tsquery(phrases)
    if not expr or not chapter_ids:
        return [], []
    ranked = (
        " order by case content_type when 'Theory' then 0 when 'Solved Example' then 1 else 2 end,"
        " ts_rank_cd(fts, q) desc limit %s"
    )
    home_sql = (
        f"select content_id, chapter_id from public.content, (select {expr} as q) s"
        " where fts @@ q and subject = %s and chapter_id = any(%s) and coalesce(in_syllabus, true)"
        f"{PASSAGE_SKIP}{ranked}"
    )
    cur.execute(home_sql, (*params, subject, chapter_ids, PASSAGES_PER_TOPIC))
    home = cur.fetchall()
    hits = home
    if len(hits) < MIN_MATERIAL:
        wide_sql = (
            f"select content_id, chapter_id from public.content, (select {expr} as q) s"
            " where fts @@ q and subject = %s and coalesce(in_syllabus, true)"
            f"{PASSAGE_SKIP}{ranked}"
        )
        cur.execute(wide_sql, (*params, subject, PASSAGES_PER_TOPIC))
        hits = cur.fetchall() or hits
    return hits, home


def apply(cur, concepts, chapters, items, embed, *, subject: str = "Physics",
          topic_list: list[tuple[str, list[tuple[str, ...]]]] | None = None,
          verbose: bool = True) -> dict[str, Any]:
    """concepts/chapters: the derived syllabus objects; items: this build's items (for material counts);
    embed: texts -> (n, d) float array. Returns stats and the new concept rows to upsert."""
    cur.execute(
        "delete from public.chunk_concepts where role = 'syllabus' and concept_id in "
        "(select concept_id from public.concepts where subject = %s)", (subject,)
    )
    cur.execute("delete from public.concepts where source = 'syllabus' and subject = %s", (subject,))
    cur.execute("delete from public.chapters where volume = 'JEE syllabus' and subject = %s", (subject,))
    cur.execute("delete from public.syllabus_topics where subject = %s", (subject,))

    material_of: Counter[str] = Counter()
    for it in items:
        material_of[it["concept_id"]] += 1
        for sec in it["secondary_concepts"]:
            material_of[sec] += 1

    by_unit: dict[str, list[Any]] = {}
    for ch in chapters:
        if ch.in_syllabus and ch.jee_unit:
            by_unit.setdefault(ch.jee_unit, []).append(ch)
    chapter_title = {ch.chapter_id: ch.title for ch in chapters}
    min_ord: dict[str, int] = {}
    for c in concepts:
        min_ord[c.chapter_id] = min(min_ord.get(c.chapter_id, c.order), c.order)

    by_name: dict[str, list[Any]] = {}
    by_alias: dict[str, list[Any]] = {}
    for c in concepts:
        by_name.setdefault(_norm(c.name), []).append(c)
        bare = re.sub(r"\s*\([^)]*\)\s*$", "", c.name or "")
        if bare != c.name:
            by_name.setdefault(_norm(bare), []).append(c)
        for alias in c.aliases:
            by_alias.setdefault(_norm(alias), []).append(c)

    taken = {c.concept_id for c in concepts}
    max_ord = max([ch.order for ch in chapters] or [0])
    new_chapters: list[dict[str, Any]] = []
    new_concepts: list[dict[str, Any]] = []
    topic_rows: list[tuple] = []
    links: list[tuple[str, str]] = []
    stats: Counter[str] = Counter()

    topics_to_process = topic_list or get_topics(subject)

    for unit_ord, (unit, topics) in enumerate(topics_to_process, start=1):
        unit_chapters = by_unit.get(unit, [])
        if not unit_chapters:
            # A unit no chapter is labelled with may still be a chapter by name: NCERT files
            # "Linear Programming" under Algebra and "Straight Lines" under Coordinate Geometry,
            # and a second, empty chapter of the same name is what a student would then see.
            unit_words = f" {_norm(unit)} "
            unit_chapters = [ch for ch in chapters if ch.in_syllabus and any(
                _norm(t) and f" {_norm(t)} " in unit_words
                for t in [ch.title, *getattr(ch, "former_titles", [])])]
        if not unit_chapters:
            max_ord += 1
            ch = {"chapter_id": slug(f"{subject}_{unit}"), "subject": subject, "title": unit, "volume": "JEE syllabus",
                  "unit": unit, "jee_unit": unit, "in_syllabus": True, "ord": max_ord}
            new_chapters.append(ch)
            chapter_title[ch["chapter_id"]] = unit
            unit_chapter_ids = [ch["chapter_id"]]
            stats["chapters_added"] += 1
        else:
            unit_chapter_ids = [ch.chapter_id for ch in unit_chapters]

        for topic_ord, (name, *phrases) in enumerate(topics, start=1):
            existing = None
            for lookup in (by_name, by_alias):
                for candidate in [name, *phrases]:
                    existing = next((c for c in lookup.get(_norm(candidate), []) if c.chapter_id in unit_chapter_ids), None)
                    if existing:
                        break
                if existing:
                    break
            if not existing:
                existing = next((c for c in by_name.get(_norm(name), []) if c.in_syllabus and getattr(c, "subject", subject) == subject), None)
            hits, home = _search_passages(cur, [name, *phrases], subject, unit_chapter_ids)
            if existing:
                # The section concept already owns its own heading. Phrase hits add the
                # subsections that name the technique ("integration by substitution").
                owned = {
                    it["content_id"]
                    for it in items
                    if it["concept_id"] == existing.concept_id or existing.concept_id in it["secondary_concepts"]
                }
                extra = [h for h in hits if h[0] not in owned]
                links.extend((h[0], existing.concept_id) for h in extra)
                topic_rows.append((subject, unit, unit_ord, name, topic_ord, existing.concept_id,
                                   existing.chapter_id, False, material_of[existing.concept_id] + len(extra)))
                stats["linked"] += 1
                stats["no_material"] += material_of[existing.concept_id] + len(extra) < MIN_MATERIAL
                continue

            chapter_id = Counter(h[1] for h in home).most_common(1)[0][0] if home else unit_chapter_ids[0]
            prefix = f"{subject.lower()[:4]}_" if subject != "Physics" else ""
            raw_cid = f"{prefix}{slug(name)}"
            cid = raw_cid
            n = 2
            while cid in taken:
                cid = f"{raw_cid}_{n}"
                n += 1
            taken.add(cid)
            chapter = chapter_title.get(chapter_id, unit)
            definition = f"JEE Main syllabus topic in {unit}: {name}."
            aliases = [p for p in phrases if _norm(p) != _norm(name)]
            new_concepts.append({
                "concept_id": cid, "concept_name": name, "definition": definition,
                "chapter_id": chapter_id, "chapter": chapter, "unit": unit, "subject": subject,
                "in_syllabus": True, "ord": int(min_ord.get(chapter_id, max_ord)), "aliases": aliases,
                "source": "syllabus",
                "embed_text": f"{name}. {chapter}. {' ; '.join(phrases[:4])}. {definition}",
            })
            links.extend((h[0], cid) for h in hits)
            topic_rows.append((subject, unit, unit_ord, name, topic_ord, cid, chapter_id, True, len(hits)))
            stats["created"] += 1
            stats["no_material"] += len(hits) < MIN_MATERIAL

    if new_concepts:
        vecs = embed([c["embed_text"] for c in new_concepts])
        for c, v in zip(new_concepts, vecs):
            c["embedding"] = v
    stats["topics"] = len(topic_rows)
    if verbose:
        print(f"syllabus topics ({subject}): {stats['topics']} — linked to existing {stats['linked']}, created {stats['created']} "
              f"({stats['no_material']} with no course material), chapters added {stats['chapters_added']}")
    return {"stats": dict(stats), "chapters": new_chapters, "concepts": new_concepts,
            "topic_rows": topic_rows, "links": links, "source": SOURCE}
