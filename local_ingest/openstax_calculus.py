"""OpenStax Calculus (CC BY-NC-SA 4.0) for the JEE single-variable chapters.

University Physics stays out of the corpus: it is a US physics sequence and was
excluded on purpose. Calculus Volumes 1 and 2 are the other OpenStax books, and
they teach the techniques NCERT only names — substitution, parts, partial
fractions, standard forms, series, first-order differential equations.

Volumes of revolution, polar coordinates, and second-order equations are not on
the JEE syllabus, so those modules are not imported.

    .venv/bin/python -m local_ingest.openstax_calculus            # download + count
    .venv/bin/python -m local_ingest.openstax_calculus --push --allow-hosted
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
import re

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEST = ROOT / ".local-run" / "sources" / "openstax-calculus"
REPO = "openstax/osbooks-calculus-bundle"
RAW = f"https://raw.githubusercontent.com/{REPO}/main"
LICENSE = "CC BY-NC-SA 4.0"
SOURCE_BOOK = "OpenStax Calculus"
COLLECTIONS = ("calculus-volume-1.collection.xml", "calculus-volume-2.collection.xml")

# Book chapter title -> existing maths chapter_id. Anything else is left out.
_CHAPTER = {
    "Functions and Graphs": "relations_and_functions_11",
    "Limits": "limits_and_derivatives",
    "Derivatives": "continuity_and_differentiability",
    "Applications of Derivatives": "application_of_derivatives",
    "Integration": "integrals",
    "Applications of Integration": "application_of_integrals",
    "Techniques of Integration": "integrals",
    "Introduction to Differential Equations": "differential_equations",
    "Sequences and Series": "sequences_and_series",
    "Power Series": "sequences_and_series",
}

# Not on the JEE Advanced syllabus. Convergence tests, Taylor theory, the logistic
# equation, related rates and the epsilon-delta definition are university calculus.
_SKIP_MODULE = re.compile(
    r"polar|parametric|arc length|surface area|\bvolumes?\b|hydrostatic|"
    r"cent(?:er|re) of mass|hyperbolic|\bmoments?\b|second[- ]order|"
    r"numerical integration|simpson|trapezoid|midpoint rule|answer key|preface|"
    r"newton'?s method|series solutions|numerical methods|"
    r"preview of calculus|precise definition of a limit|alternating series|"
    r"comparison tests|ratio and root|divergence and integral tests|"
    r"properties of power series|logistic equation|related rates|integral test|"
    r"taylor and maclaurin|working with taylor",
    re.I,
)

# Module titles already stored that are outside the Advanced syllabus. A later push
# skips them; this list is what a one-shot delete removes.
OFF_SYLLABUS_MODULES = (
    "A Preview of Calculus",
    "The Precise Definition of a Limit",
    "Alternating Series",
    "Comparison Tests",
    "Ratio and Root Tests",
    "The Divergence and Integral Tests",
    "Properties of Power Series",
    "The Logistic Equation",
    "Related Rates",
    "Taylor and Maclaurin Series",
    "Working with Taylor Series",
)

_UA = {"User-Agent": "aeropt-local-ingest"}


def chapter_for(book_chapter: str, module_title: str) -> str | None:
    """JEE chapter this module belongs in, or None when it should not be imported."""
    cid = _CHAPTER.get((book_chapter or "").strip())
    if not cid or _SKIP_MODULE.search(module_title or ""):
        return None
    return cid


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_to(url: str, path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    last: Exception | None = None
    for attempt in range(4):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_get(url))
            return
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            if attempt == 3:
                raise SystemExit(f"Failed to download {url}: {last}")


def fetch() -> int:
    """Download the two collection files and every module they list. Returns module count."""
    for name in COLLECTIONS:
        # Collections are tiny and name the modules; refresh them so a book update is seen.
        path = DEST / "collections" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_get(f"{RAW}/collections/{name}"))
    ids = sorted({mid for _chapter, mid in module_plan()})
    def one(mid: str) -> None:
        _fetch_to(f"{RAW}/modules/{mid}/index.cnxml", DEST / "modules" / mid / "index.cnxml")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(one, ids))
    return len(ids)


def module_plan() -> list[tuple[str, str]]:
    """(OpenStax chapter title, module id) in reading order."""
    from local_ingest.syllabus import _collection_tree

    plan: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in COLLECTIONS:
        path = DEST / "collections" / name
        if not path.exists():
            continue
        _volume, entries = _collection_tree(path)
        for _unit, chapter, module_ids in entries:
            for mid in module_ids:
                if mid and mid not in seen:
                    seen.add(mid)
                    plan.append((chapter, mid))
    return plan


def calculus_items(chapter_lookup: dict[str, Any]) -> list[dict[str, Any]]:
    """Theory, worked examples, and solved exercises. Empty stems are not stored."""
    if not (DEST / "collections").exists():
        return []
    from local_ingest.cnxml import parse_module
    from local_ingest.corpus import (
        EXAM_BOTH,
        TYPE_EXAMPLE,
        TYPE_PRACTICE,
        TYPE_THEORY,
        _item,
        split_theory,
    )

    items: list[dict[str, Any]] = []
    for book_chapter, module_id in module_plan():
        path = DEST / "modules" / module_id / "index.cnxml"
        if not path.exists():
            continue
        try:
            mod = parse_module(path)
        except ET.ParseError:
            continue
        chapter_id = chapter_for(book_chapter, mod.title)
        if not chapter_id:
            continue
        chapter = chapter_lookup.get(chapter_id)
        if chapter is None:
            continue
        common = dict(
            chapter=chapter,
            source_book=SOURCE_BOOK,
            license_=LICENSE,
            target_exam=EXAM_BOTH,
            module_id=module_id,
            subject="Mathematics",
        )

        n = 0
        blocks: list[tuple[str, str]] = []
        for sec in mod.sections:
            if _SKIP_MODULE.search(sec.get("title") or ""):
                continue
            heading = " · ".join(p for p in sec.get("path") or [] if p) or mod.title
            for part in split_theory(sec["text"]):
                if len(part) >= 180:
                    blocks.append((heading, part))
        if mod.key_concepts and len(mod.key_concepts) >= 180 and not _SKIP_MODULE.search(mod.title):
            blocks.append((f"{mod.title} · Key concepts", mod.key_concepts))
        for heading, part in blocks:
            n += 1
            item = _item(
                content_id=f"osc_{module_id}_s{n}",
                content_type=TYPE_THEORY,
                core_text=part,
                solution="",
                heading=f"OpenStax Calculus · {heading}",
                source_ref=f"{module_id} {heading}",
                difficulty=2,
                question_type="theory",
                **common,
            )
            item["concept_hint"] = heading
            items.append(item)

        for i, ex in enumerate(mod.examples, 1):
            question = (ex.get("question") or "").strip()
            solution = (ex.get("solution") or "").strip()
            if len(question) < 40 or len(solution) < 60:
                continue
            title = ex.get("title") or mod.title
            if _SKIP_MODULE.search(title):
                continue
            item = _item(
                content_id=f"osc_{module_id}_e{i}",
                content_type=TYPE_EXAMPLE,
                core_text=question,
                solution=solution,
                heading=f"OpenStax Calculus · {title}",
                source_ref=f"{module_id} example {i}",
                difficulty=3,
                question_type="worked_example",
                **common,
            )
            item["concept_hint"] = title
            items.append(item)

        for i, ex in enumerate(mod.exercises, 1):
            question = (ex.get("question") or "").strip()
            solution = (ex.get("solution") or "").strip()
            if len(question) < 40 or len(solution) < 80:
                continue
            item = _item(
                content_id=f"osc_{module_id}_q{i}",
                content_type=TYPE_PRACTICE,
                core_text=question,
                solution=solution,
                heading=f"OpenStax Calculus · {mod.title}",
                source_ref=f"{module_id} exercise {i}",
                difficulty=3,
                question_type="exercise",
                **common,
            )
            item["concept_hint"] = mod.title
            items.append(item)
    return items


def _maths_lookup():
    from local_ingest.ncert.catalog import MATHEMATICS_CHAPTERS
    from local_ingest.syllabus import Chapter

    return {
        c.chapter_id: Chapter(
            chapter_id=c.chapter_id,
            title=c.title,
            volume=c.book,
            unit=c.unit,
            jee_unit=c.unit,
            in_syllabus=True,
            order=c.number,
            subject="Mathematics",
        )
        for c in MATHEMATICS_CHAPTERS
    }


def push(*, allow_hosted: bool, dry_run: bool) -> int:
    """Embed the new chunks and upsert them. Does not rebuild the rest of the corpus."""
    import json

    from local_ingest import push_index
    from local_ingest.syllabus import Concept
    from retrieval import embedder

    n = fetch()
    print(f"openstax calculus modules on disk: {n}", flush=True)
    lookup = _maths_lookup()
    items = calculus_items(lookup)
    if not items:
        raise SystemExit("OpenStax Calculus produced no items.")
    by_chapter: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for it in items:
        by_chapter[it["chapter_id"]] = by_chapter.get(it["chapter_id"], 0) + 1
        by_type[it["content_type"]] = by_type.get(it["content_type"], 0) + 1
    print(f"items: {len(items)}  types: {by_type}", flush=True)
    for cid, count in sorted(by_chapter.items(), key=lambda kv: -kv[1]):
        print(f"  {cid}: {count}", flush=True)

    url = os.getenv("RETRIEVAL_DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("Set RETRIEVAL_DATABASE_URL to the Postgres connection string of the retrieval database.")
    embedder.snapshot_static_model()
    conn = push_index.connect(url, allow_hosted)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute("set statement_timeout = '120s'")
        cur.execute(
            "select concept_id, concept_name, coalesce(definition,''), chapter_id, coalesce(chapter,''),"
            " coalesce(unit,''), coalesce(in_syllabus, true), coalesce(ord, 0), aliases, coalesce(source,''),"
            " subject from public.concepts where subject = 'Mathematics' and coalesce(source,'') <> 'syllabus'"
        )
        concepts = []
        for row in cur.fetchall():
            aliases = row[8] if isinstance(row[8], list) else json.loads(row[8] or "[]")
            concepts.append(Concept(
                concept_id=row[0], name=row[1], definition=row[2], chapter_id=row[3], chapter=row[4],
                unit=row[5], in_syllabus=bool(row[6]), module_id="", order=int(row[7]),
                aliases=aliases, source=row[9] or "glossary", subject=row[10],
            ))
        if not concepts:
            raise SystemExit("No mathematics concepts in the retrieval database to attach these chunks to.")
        from local_ingest.corpus import map_concepts

        chapters = list(lookup.values())
        item_vecs, _concept_vecs = map_concepts(items, concepts, chapters)
        from retrieval.embedder import DIM

        if item_vecs.shape[1] != DIM:
            raise SystemExit(f"embedding dimension {item_vecs.shape[1]} != {DIM}")
        model = embedder.model_name()
        written, unchanged = push_index.upsert_content(cur, items, item_vecs, model)
        links = push_index.replace_links(cur, items)
        print(f"upserted {written}, unchanged {unchanged}, links {links}", flush=True)
        if dry_run:
            conn.rollback()
            print("dry run: rolled back, nothing written", flush=True)
            return 0
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Re-attach syllabus topics so the new passages are what a topic search returns.
    push_index.relink_topics(url, allow_hosted=allow_hosted, subject="Mathematics", dry_run=False)
    conn = push_index.connect(url, allow_hosted)
    try:
        cur = conn.cursor()
        cur.execute(
            "insert into public.retrieval_meta (key, value) values ('built_at', to_char(now() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS'))"
            " on conflict (key) do update set value = excluded.value, updated_at = now()"
        )
        conn.commit()
    finally:
        conn.close()
    return 0


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / ".env.local", override=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-hosted", action="store_true")
    args = ap.parse_args()
    if args.push or args.dry_run:
        return push(allow_hosted=args.allow_hosted, dry_run=args.dry_run and not args.push)
    n = fetch()
    items = calculus_items(_maths_lookup())
    print(f"modules {n}, items {len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
