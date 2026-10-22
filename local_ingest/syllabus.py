"""Derive the syllabus graph — units, chapters, concepts, prerequisites — from the sources.

Nothing here is typed in by hand. The book's own structure supplies it:

  collections/*.collection.xml   volume -> unit -> chapter -> module reading order
  <glossary><definition>         concept atoms, each with a textbook definition
  <section><title>               concept atoms for sections the glossary skips
  <link document="mXXXXX"/>      prerequisite edges (a section citing an earlier one)

Prerequisite direction is `concept REQUIRES earlier_concept`, and an edge is only
kept when the target sits earlier in reading order. That single rule makes the
graph acyclic by construction, so no cycle-breaking heuristic is needed.

JEE scope comes from `jee_syllabus.py` (alias match on chapter titles), so every
concept carries whether it is examinable — the tutor can then be honest about
scope instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from local_ingest.cnxml import parse_module
from local_ingest.jee_syllabus import OUT_OF_SCOPE_HINTS, alias_index

ROOT = Path(__file__).resolve().parents[1]
OPENSTAX = ROOT / ".local-run" / "sources" / "openstax"

COL_NS = "http://cnx.rice.edu/collxml"
MD_NS = "http://cnx.rice.edu/mdml"

_STOP_TITLES = {
    "introduction", "chapter review", "key terms", "summary", "key equations",
    "conceptual questions", "problems", "additional problems", "challenge problems",
    "answer key", "preface", "review", "problems & exercises",
}


def slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:limit].strip("_")


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


@dataclass
class Chapter:
    chapter_id: str
    title: str
    volume: str
    unit: str
    jee_unit: str
    in_syllabus: bool
    order: int
    module_ids: list[str] = field(default_factory=list)
    subject: str = "Physics"
    # Titles of the source chapters merged into this one ("Linear Programming" inside Algebra).
    former_titles: list[str] = field(default_factory=list)


@dataclass
class Concept:
    concept_id: str
    name: str
    definition: str
    chapter_id: str
    chapter: str
    unit: str
    in_syllabus: bool
    module_id: str
    order: int
    aliases: list[str] = field(default_factory=list)
    source: str = "glossary"
    subject: str = "Physics"


def _collection_tree(path: Path) -> tuple[str, list[tuple[str, str, list[str]]]]:
    """(volume title, [(unit, chapter, [module ids in order])])."""
    root = ET.parse(path).getroot()
    vol_title = ""
    for el in root.iter(f"{{{MD_NS}}}title"):
        vol_title = (el.text or "").strip()
        break

    out: list[tuple[str, str, list[str]]] = []

    def title_of(sub: ET.Element) -> str:
        t = sub.find(f"{{{MD_NS}}}title")
        return (t.text or "").strip() if t is not None else ""

    def modules_of(sub: ET.Element) -> list[str]:
        content = sub.find(f"{{{COL_NS}}}content")
        if content is None:
            return []
        return [
            m.get("document", "")
            for m in content.findall(f"{{{COL_NS}}}module")
            if m.get("document")
        ]

    def walk(sub: ET.Element, unit: str) -> None:
        content = sub.find(f"{{{COL_NS}}}content")
        if content is None:
            return
        children = content.findall(f"{{{COL_NS}}}subcollection")
        if not children:
            return
        for child in children:
            title = title_of(child)
            grandkids = child.find(f"{{{COL_NS}}}content")
            has_sub = grandkids is not None and grandkids.findall(f"{{{COL_NS}}}subcollection")
            if has_sub:
                walk(child, title or unit)
            else:
                out.append((unit or vol_title, title, modules_of(child)))

    top = root.find(f"{{{COL_NS}}}content")
    if top is not None:
        for sub in top.findall(f"{{{COL_NS}}}subcollection"):
            title = title_of(sub)
            inner = sub.find(f"{{{COL_NS}}}content")
            if inner is not None and inner.findall(f"{{{COL_NS}}}subcollection"):
                walk(sub, title)
            else:
                out.append((vol_title, title, modules_of(sub)))
    return vol_title, out


def _jee_scope(chapter_title: str, unit_title: str) -> tuple[str, bool]:
    """Map an OpenStax chapter onto a JEE unit by whole-word alias match.

    Token-sequence matching, not substring: "dynamics" must not claim
    "The First Law of Thermodynamics", which is exactly what a naive `in` test did
    (it put both thermodynamics chapters under Laws of Motion). Longest alias wins,
    so a specific chapter name beats a one-word alias.
    """
    # Physics aliases only: across all subjects "Vectors" is the maths unit and
    # "Atomic Structure" the chemistry one.
    index = alias_index("Physics")
    key_tokens = norm(chapter_title).split()
    if not key_tokens:
        return "", False
    key = " ".join(key_tokens)
    if key in index:
        return index[key][1], True

    best_unit = ""
    best_len = 0
    for alias, (_, unit) in index.items():
        alias_tokens = alias.split()
        if not alias_tokens:
            continue
        if len(alias_tokens) == 1:
            # A single word only counts as an exact chapter name, never inside one.
            matched = key_tokens == alias_tokens
        else:
            matched = _contains_sequence(key_tokens, alias_tokens) or _contains_sequence(
                alias_tokens, key_tokens
            )
        if matched and len(alias_tokens) > best_len:
            best_unit, best_len = unit, len(alias_tokens)
    if best_unit:
        return best_unit, True

    lowered = chapter_title.lower()
    if any(hint in lowered for hint in OUT_OF_SCOPE_HINTS):
        return "", False
    return "", False


def _contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def build_chapters() -> list[Chapter]:
    chapters: list[Chapter] = []
    order = 0
    for col in sorted((OPENSTAX / "collections").glob("*.xml")):
        volume, entries = _collection_tree(col)
        for unit, chapter_title, module_ids in entries:
            if not chapter_title or not module_ids:
                continue
            jee_unit, in_syllabus = _jee_scope(chapter_title, unit)
            order += 1
            chapters.append(
                Chapter(
                    chapter_id=slug(chapter_title),
                    title=chapter_title,
                    volume=volume,
                    unit=unit,
                    jee_unit=jee_unit,
                    in_syllabus=in_syllabus,
                    order=order,
                    module_ids=module_ids,
                )
            )
    from local_ingest.ncert import catalog

    CHEM_BRANCH = {
        "Some Basic Concepts in Chemistry": "Physical Chemistry",
        "Atomic Structure": "Physical Chemistry",
        "Chemical Thermodynamics": "Physical Chemistry",
        "Equilibrium": "Physical Chemistry",
        "Redox Reactions and Electrochemistry": "Physical Chemistry",
        "Solutions": "Physical Chemistry",
        "Chemical Kinetics": "Physical Chemistry",
        "Classification of Elements and Periodicity in Properties": "Inorganic Chemistry",
        "Chemical Bonding and Molecular Structure": "Inorganic Chemistry",
        "P-Block Elements": "Inorganic Chemistry",
        "d- and f-Block Elements": "Inorganic Chemistry",
        "Coordination Compounds": "Inorganic Chemistry",
        "Some Basic Principles of Organic Chemistry": "Organic Chemistry",
        "Purification and Characterisation of Organic Compounds": "Organic Chemistry",
        "Hydrocarbons": "Organic Chemistry",
        "Organic Compounds Containing Halogens": "Organic Chemistry",
        "Organic Compounds Containing Oxygen": "Organic Chemistry",
        "Organic Compounds Containing Nitrogen": "Organic Chemistry",
        "Biomolecules": "Organic Chemistry",
        "Principles Related to Practical Chemistry": "Physical Chemistry",
    }

    for c in catalog.CHEMISTRY_CHAPTERS:
        order += 1
        branch = CHEM_BRANCH.get(c.unit, "Physical Chemistry")
        chapters.append(
            Chapter(
                chapter_id=c.chapter_id,
                title=c.title,
                volume=c.book,
                unit=branch,
                jee_unit=c.unit,
                in_syllabus=True,
                order=order,
                module_ids=[],
                subject="Chemistry",
            )
        )

    MATH_BRANCH = {
        "Sets, Relations and Functions": "Algebra",
        "Complex Numbers and Quadratic Equations": "Algebra",
        "Permutations and Combinations": "Algebra",
        "Binomial Theorem and its Simple Applications": "Algebra",
        "Sequence and Series": "Algebra",
        "Matrices and Determinants": "Algebra",
        "Algebra": "Algebra",
        "Trigonometry": "Trigonometry",
        "Coordinate Geometry": "Coordinate Geometry",
        "Limit, Continuity and Differentiability": "Differential Calculus",
        "Application of Derivatives": "Differential Calculus",
        "Differential Calculus": "Differential Calculus",
        "Integral Calculus": "Integral Calculus",
        "Differential Equations": "Integral Calculus",
        "Vector Algebra": "Vectors and 3D Geometry",
        "Three Dimensional Geometry": "Vectors and 3D Geometry",
        "Statistics and Probability": "Statistics and Probability",
    }

    for c in catalog.MATHEMATICS_CHAPTERS:
        order += 1
        branch = MATH_BRANCH.get(c.unit, "Algebra")
        chapters.append(
            Chapter(
                chapter_id=c.chapter_id,
                title=c.title,
                volume=c.book,
                unit=branch,
                jee_unit=c.unit,
                in_syllabus=True,
                order=order,
                module_ids=[],
                subject="Mathematics",
            )
        )
    # On the JEE Advanced syllabus, and no longer a chapter of the rationalised NCERT books.
    for chapter_id, title, unit in (
        ("solid_state", "Solid State", "Solid State"),
        ("surface_chemistry", "Surface Chemistry", "Surface Chemistry"),
        ("hydrogen", "Hydrogen", "Hydrogen"),
        ("s_block_elements", "s-Block Elements", "s-Block Elements"),
        ("p_block_elements", "p-Block Elements", "P-Block Elements"),
        ("isolation_of_metals", "Isolation of Metals", "Isolation of Metals"),
        ("qualitative_analysis", "Qualitative Analysis", "Principles Related to Practical Chemistry"),
        ("environmental_chemistry", "Environmental Chemistry", "Environmental Chemistry"),
        ("polymers", "Polymers", "Polymers"),
        ("chemistry_in_everyday_life", "Chemistry in Everyday Life", "Chemistry in Everyday Life"),
    ):
        order += 1
        chapters.append(
            Chapter(
                chapter_id=chapter_id, title=title, volume="JEE Advanced syllabus",
                unit=unit, jee_unit=unit, in_syllabus=True, order=order,
                module_ids=[], subject="Chemistry",
            )
        )
    return chapters



def build_concepts(
    chapters: list[Chapter],
) -> tuple[list[Concept], list[dict[str, str]], dict[str, Any]]:
    """Concepts from glossary definitions, plus section titles the glossary misses."""
    module_chapter: dict[str, Chapter] = {}
    module_order: dict[str, int] = {}
    ordinal = 0
    for ch in chapters:
        for mid in ch.module_ids:
            module_chapter[mid] = ch
            ordinal += 1
            module_order[mid] = ordinal

    parsed: dict[str, Any] = {}
    by_name: dict[str, Concept] = {}
    concepts: list[Concept] = []

    def add(
        name: str, definition: str, mid: str, ch: Chapter, source: str
    ) -> Concept | None:
        clean = re.sub(r"\s+", " ", name or "").strip(" .:;")
        if not clean or len(clean) < 3 or norm(clean) in _STOP_TITLES:
            return None
        key = norm(clean)
        existing = by_name.get(key)
        if existing:
            if definition and len(definition) > len(existing.definition):
                existing.definition = definition
            return existing
        cid = slug(clean)
        if not cid:
            return None
        n = 2
        base = cid
        while any(c.concept_id == cid for c in concepts):
            cid = f"{base}_{n}"
            n += 1
        concept = Concept(
            concept_id=cid,
            name=clean,
            definition=definition,
            chapter_id=ch.chapter_id,
            chapter=ch.title,
            unit=ch.jee_unit or ch.unit,
            in_syllabus=ch.in_syllabus,
            module_id=mid,
            order=module_order.get(mid, 0),
            source=source,
        )
        by_name[key] = concept
        concepts.append(concept)
        return concept

    module_concepts: dict[str, list[str]] = {}

    for ch in chapters:
        for mid in ch.module_ids:
            path = OPENSTAX / "modules" / f"{mid}.cnxml"
            if not path.exists():
                continue
            mod = parse_module(path)
            parsed[mid] = mod
            ids: list[str] = []
            for entry in mod.glossary:
                c = add(entry["term"], entry["meaning"], mid, ch, "glossary")
                if c:
                    ids.append(c.concept_id)
            if not ids:
                # No glossary in this module — the section titles are the atoms.
                for sec in mod.sections:
                    c = add(sec["title"], sec["text"][:400], mid, ch, "section")
                    if c:
                        ids.append(c.concept_id)
                        break
            # A module's own title is a useful alias for everything it teaches.
            for cid in ids:
                concept = next(c for c in concepts if c.concept_id == cid)
                if mod.title and mod.title not in concept.aliases:
                    concept.aliases.append(mod.title)
            module_concepts[mid] = ids

    # ── Prerequisite edges from cross-references, earlier-in-book only ─────────
    edges: set[tuple[str, str]] = set()
    for mid, mod in parsed.items():
        here = module_concepts.get(mid, [])
        if not here:
            continue
        for target in mod.links:
            if module_order.get(target, 10**9) >= module_order.get(mid, 0):
                continue  # forward reference: not a prerequisite
            for src in here:
                for dst in module_concepts.get(target, [])[:3]:
                    if src != dst:
                        edges.add((src, dst))

    # Within a chapter, later concepts depend on the chapter's opening concept.
    for ch in chapters:
        chain = [cid for mid in ch.module_ids for cid in module_concepts.get(mid, [])]
        if len(chain) > 1:
            root_id = chain[0]
            for cid in chain[1:]:
                if cid != root_id:
                    edges.add((cid, root_id))

    requires = [{"concept_id": a, "requires": b} for a, b in sorted(edges)]
    return concepts, requires, parsed


def build() -> dict[str, Any]:
    """Physics from OpenStax, plus every NCERT-only subject (chemistry) from NCERT's headings."""
    from local_ingest import ncert_syllabus

    chapters = build_chapters()
    concepts, requires, parsed = build_concepts(chapters)
    other = ncert_syllabus.build()
    known = {ch.chapter_id for ch in chapters}  # the NCERT catalog's chapters are already listed above
    chapters += [ch for ch in other["chapters"] if ch.chapter_id not in known]
    concepts += other["concepts"]
    covered = {c.chapter_id for c in concepts}
    for ch in chapters:
        if ch.subject != "Chemistry" or ch.chapter_id in covered:
            continue
        concepts.append(Concept(
            concept_id=f"chem_{ch.chapter_id}", name=ch.title, definition=ch.title,
            chapter_id=ch.chapter_id, chapter=ch.title, unit=ch.jee_unit or ch.unit,
            in_syllabus=True, module_id="", order=ch.order, source="chapter", subject="Chemistry",
        ))
        covered.add(ch.chapter_id)
    requires += other.get("requires", [])
    # NCERT reuses chapter titles across classes ("Probability" in XI and XII). Two chapters
    # a student cannot tell apart are named by class; concepts carry the title, so follow it.
    seen: dict[tuple[str, str], int] = {}
    for ch in chapters:
        seen[(ch.subject, norm(ch.title))] = seen.get((ch.subject, norm(ch.title)), 0) + 1
    renamed: dict[str, str] = {}
    for ch in chapters:
        grade = re.search(r"\bClass\s+(XII|XI)\b", ch.volume or "")
        if seen[(ch.subject, norm(ch.title))] > 1 and grade:
            ch.title = renamed[ch.chapter_id] = f"{ch.title} (Class {grade.group(1)})"
    for c in concepts:
        c.chapter = renamed.get(c.chapter_id, c.chapter)
    return {
        "chapters": chapters,
        "concepts": concepts,
        "requires": requires,
        "modules": parsed,
    }


def merge_to_jee_chapters(syllabus: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, str]:
    """Make each JEE unit one chapter: the 44 OpenStax physics chapters become 20, NCERT's
    two Relations and Functions chapters become "Sets, Relations and Functions".

    Runs after concept mapping, so an item is still matched against the concepts of the
    narrow chapter it came from; only the label it is filed under changes. A unit with a
    single NCERT chapter keeps that chapter's id; a merged unit, or one built on OpenStax
    chapters, gets the unit's slug, prefixed with the subject if another subject uses it. A
    chapter outside the JEE syllabus (no unit: Relativity, Particle Physics) is dropped
    with its concepts — it holds no items. Mutates `syllabus` and `items`; returns the
    old -> new chapter id map.
    """
    chapters: list[Chapter] = syllabus["chapters"]
    groups: dict[tuple[str, str], list[Chapter]] = {}
    for ch in chapters:
        if ch.in_syllabus and ch.jee_unit:
            groups.setdefault((ch.subject, ch.jee_unit), []).append(ch)
    dropped = {ch.chapter_id for ch in chapters if not (ch.in_syllabus and ch.jee_unit)}
    if any(it["chapter_id"] in dropped for it in items):
        raise SystemExit("an item sits in a chapter outside the JEE syllabus; refusing to drop it")

    other_ids = {ch.chapter_id for ch in chapters}
    remap: dict[str, str] = {}
    merged: list[Chapter] = []
    for (subject, unit), members in sorted(groups.items(), key=lambda kv: min(c.order for c in kv[1])):
        members.sort(key=lambda c: c.order)
        openstax = any(c.volume.startswith("University Physics") for c in members)
        if len(members) == 1 and not openstax:  # an OpenStax id would keep naming the book's chapter
            new_id = members[0].chapter_id
        else:
            new_id = slug(unit)
            if new_id in other_ids - {c.chapter_id for c in members}:
                new_id = slug(f"{subject}_{unit}")
        volumes = [v for v in dict.fromkeys(c.volume for c in members) if not v.startswith("University Physics")]
        merged.append(Chapter(
            chapter_id=new_id, title=unit, volume=" · ".join(volumes) or f"JEE {subject}",
            unit=members[0].unit, jee_unit=unit, in_syllabus=True, order=members[0].order,
            module_ids=[m for c in members for m in c.module_ids], subject=subject,
            former_titles=[c.title for c in members if c.title != unit],
        ))
        for c in members:
            remap[c.chapter_id] = new_id

    title = {ch.chapter_id: ch.title for ch in merged}
    syllabus["chapters"] = merged
    syllabus["concepts"] = [c for c in syllabus["concepts"] if c.chapter_id not in dropped]
    for c in syllabus["concepts"]:
        c.chapter_id = remap.get(c.chapter_id, c.chapter_id)
        c.chapter = title.get(c.chapter_id, c.chapter)
    kept = {c.concept_id for c in syllabus["concepts"]}
    syllabus["requires"] = [e for e in syllabus["requires"] if e["concept_id"] in kept and e["requires"] in kept]
    for it in items:
        it["chapter_id"] = remap.get(it["chapter_id"], it["chapter_id"])
        it["chapter"] = title.get(it["chapter_id"], it["chapter"])
    return remap


if __name__ == "__main__":
    data = build()
    chapters: list[Chapter] = data["chapters"]
    concepts: list[Concept] = data["concepts"]
    print(f"chapters {len(chapters)} ({sum(1 for c in chapters if c.in_syllabus)} in JEE scope)")
    print(f"concepts {len(concepts)} ({sum(1 for c in concepts if c.in_syllabus)} in scope)")
    print(f"requires {len(data['requires'])}")
    print("\nout-of-scope chapters:")
    for ch in chapters:
        if not ch.in_syllabus:
            print(f"  {ch.volume[-1]} | {ch.unit:34} | {ch.title}")
    print("\nfirst 8 in-scope chapters:")
    for ch in chapters[:8]:
        print(f"  {ch.order:3} {ch.title:44} -> {ch.jee_unit or '(out)'} ({len(ch.module_ids)} modules)")
