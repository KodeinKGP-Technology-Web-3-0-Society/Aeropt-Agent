"""Derive chapters + concepts for NCERT-only subjects (chemistry today) from the book itself.

Physics concepts come from OpenStax (glossary + modules, syllabus.py). Chemistry has no
such source, but NCERT's own numbered headings are a concept hierarchy:

    2.1 DISCOVERY OF SUB-ATOMIC PARTICLES      -> concept
    2.1.1 Discovery of Electron                -> concept
    Photoelectric Effect  (unnumbered)         -> alias of the numbered section it sits under

Nothing is typed in: chapters, concept names, definitions (the section's first passage)
and aliases all come from `.local-run/sources/ncert/ncert.jsonl` (local_ingest/ncert).

Every row also gets a `concept_hint` — the concept its heading belongs to — so the
corpus mapper files theory and worked examples by name, and only exercises (which carry
no heading) fall back to similarity, restricted to their own chapter. That restriction is
the point: mapped against the physics concept pool, chemistry passages landed on
"Bohr magneton" and "dimension".
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from local_ingest.syllabus import Chapter, Concept, norm, slug

NCERT_JSONL = Path(__file__).resolve().parents[1] / ".local-run" / "sources" / "ncert" / "ncert.jsonl"
PDF_DIR = Path(__file__).resolve().parents[1] / ".local-run" / "sources" / "ncert" / "pdf"
SUBJECTS = ("Chemistry", "Mathematics")  # NCERT subjects whose concepts come from here
ORDER_BASE = {"Chemistry": 1000, "Mathematics": 2000}   # chapter/concept `ord` after physics

_NUMBERED = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?\.?\s+(.+)$")
_SKIP = {"summary", "answers to some intext questions", "exercises", "intext questions", "introduction", "historical note", "miscellaneous examples", "answers"}
_SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with", "into", "its"}


def _pretty(title: str) -> str:
    """'DISCOVERY OF SUB-ATOMIC PARTICLES' -> 'Discovery of Sub-atomic Particles'. Mixed case kept."""
    title = re.sub(r"\s+", " ", title).strip(" :*")
    letters = [ch for ch in title if ch.isalpha()]
    if not letters or not all(ch.isupper() for ch in letters):
        return title
    words = title.lower().split(" ")
    return " ".join(w if (i and w in _SMALL) else w[:1].upper() + w[1:] for i, w in enumerate(words))


_TRAILING_CLAUSE = re.compile(
    r"\s+(?:Suppose|Let|The notation|The geometric|The process|The given|The word|In this|In everyday|In previous|"
    r"In the|When|If|Consider|We|Sometimes|Recall|To study|For example|Given|Since|There is|"
    r"From|As we|Now consider|Zero Vector|Unit Vector|Events can be|A function)\b.*$",
    flags=re.I
)


def _clean_title(s: str) -> str:
    s = re.sub(r"\s{2,}.*$", "", s).strip()
    s = _TRAILING_CLAUSE.sub("", s).strip(" ,:.-")
    return s


def _key(heading: str) -> str:
    """Heading text without its number, normalised — how examples refer to theory sections."""
    m = _NUMBERED.match(heading.strip())
    return norm(m.group(4) if m else heading)


@lru_cache(maxsize=1)
def _derive() -> dict[str, Any]:
    rows: list[dict] = []
    if NCERT_JSONL.exists():
        rows = [json.loads(line) for line in NCERT_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows = [r for r in rows if r.get("subject") in SUBJECTS]

    chapters: list[Chapter] = []
    concepts: list[Concept] = []
    hints: dict[str, str] = {}
    taken: set[str] = set()
    by_code: dict[str, list[dict]] = {}
    for r in rows:
        by_code.setdefault(r["chapter_code"], []).append(r)

    # 1. Process chapters present in ncert.jsonl
    for ch_index, code in enumerate(sorted(by_code, key=lambda c: (c[0] != "k", c))):  # Class XI, then XII
        ch_rows = by_code[code]
        first = ch_rows[0]
        subject = first["subject"]
        base = ORDER_BASE.get(subject, 2000)
        chapter = Chapter(
            chapter_id=first["chapter_id"], title=first["chapter"], volume=first["book"],
            unit=first["unit"], jee_unit=first["unit"], in_syllabus=True, order=base + ch_index,
            module_ids=[first["chapter_id"]], subject=subject,
        )
        chapters.append(chapter)

        theory = sorted((r for r in ch_rows if r["kind"] == "theory"), key=lambda r: r["id"])
        owner_of_key: dict[str, Concept] = {}
        current: Concept | None = None
        for r in theory:
            heading = (r.get("section") or "").strip()
            m = _NUMBERED.match(heading)
            if m and not norm(m.group(4)) in _SKIP:
                name = _pretty(m.group(4))
                prefix = subject.lower()[:4]
                cid = f"{prefix}_{slug(name)}"
                n = 2
                while cid in taken:
                    cid = f"{prefix}_{slug(name)}_{n}"
                    n += 1
                taken.add(cid)
                unit = chapter.jee_unit or chapter.unit
                current = Concept(
                    concept_id=cid, name=name, definition=re.sub(r"\s+", " ", r.get("text") or "")[:400],
                    chapter_id=chapter.chapter_id, chapter=chapter.title, unit=unit, in_syllabus=True,
                    module_id=chapter.chapter_id, order=base * 10 + len(concepts), aliases=[],
                    source="ncert_section", subject=subject,
                )
                concepts.append(current)
            elif current is not None and heading and norm(heading) not in _SKIP and not _NUMBERED.match(heading):
                # An unnumbered heading names something taught in the current section.
                alias = _pretty(re.sub(r"^(\(?[a-z0-9ivx]+[.)]?\s+)", "", heading, flags=re.I))
                if len(alias) > 3 and alias not in current.aliases:
                    current.aliases.append(alias)
            if current is not None:
                owner_of_key.setdefault(_key(heading), current)
                if norm(heading) not in _SKIP:
                    hints[r["id"]] = current.name

        for r in ch_rows:
            if r["kind"] != "theory" and r.get("section"):
                owner = owner_of_key.get(_key(r["section"]))
                if owner is not None:
                    hints[r["id"]] = owner.name

    # 2. Process chapters from catalog not yet in ncert.jsonl (from their downloaded PDF)
    from local_ingest.ncert import catalog
    from local_ingest.extract import pdf as pdfs

    catalog_chapters = [c for c in catalog.CHAPTERS if c.subject in SUBJECTS and c.code not in by_code]
    for ch_index, ch in enumerate(catalog_chapters):
        base = ORDER_BASE.get(ch.subject, 2000)
        chapter = Chapter(
            chapter_id=ch.chapter_id, title=ch.title, volume=ch.book,
            unit=ch.unit, jee_unit=ch.unit, in_syllabus=True, order=base + ch_index,
            module_ids=[ch.chapter_id], subject=ch.subject,
        )
        chapters.append(chapter)

        pdf_path = PDF_DIR / f"{ch.code}.pdf"
        if not pdf_path.exists():
            continue
        texts = pdfs.page_texts(pdf_path)
        current = None
        for text in texts:
            lines = text.splitlines()
            for l_idx, line in enumerate(lines):
                line = line.strip()
                m = _NUMBERED.match(line)
                if m and m.group(1) == str(ch.number):
                    raw_name = _clean_title(m.group(4))
                    if norm(raw_name) in _SKIP or len(raw_name) < 3 or raw_name.lower().startswith("example"):
                        continue
                    name = _pretty(raw_name)
                    prefix = ch.subject.lower()[:4]
                    cid = f"{prefix}_{slug(name)}"
                    base_cid = cid
                    n = 2
                    while cid in taken:
                        cid = f"{base_cid}_{n}"
                        n += 1
                    taken.add(cid)
                    following = [fl.strip() for fl in lines[l_idx+1:l_idx+6] if fl.strip() and not _NUMBERED.match(fl.strip()) and not fl.lower().startswith("example")]
                    defn = " ".join(following)[:400] or f"NCERT {ch.subject} {ch.title}: {name}."
                    current = Concept(
                        concept_id=cid, name=name, definition=defn,
                        chapter_id=chapter.chapter_id, chapter=chapter.title, unit=chapter.jee_unit or chapter.unit,
                        in_syllabus=True, module_id=chapter.chapter_id, order=base * 10 + len(concepts),
                        aliases=[], source="ncert_section", subject=ch.subject,
                    )
                    concepts.append(current)
                elif current is not None and line and not _NUMBERED.match(line) and len(line) < 60:
                    clean_l = _clean_title(line)
                    if norm(clean_l) not in _SKIP and len(clean_l) > 3 and not clean_l.lower().startswith("example"):
                        if clean_l.isupper() or (clean_l[0].isupper() and not clean_l.endswith(".")):
                            alias = _pretty(clean_l)
                            if alias not in current.aliases and len(alias) < 50:
                                current.aliases.append(alias)

    # ── Concept prerequisite edges for Chemistry & Mathematics ──────────────
    edges: set[tuple[str, str]] = set()
    by_chapter_concepts: dict[str, list[Concept]] = {}
    for c in concepts:
        by_chapter_concepts.setdefault(c.chapter_id, []).append(c)

    # 1. Intra-chapter: later concepts depend on opening concept, plus sequential chain
    for ch_id, ch_concepts in by_chapter_concepts.items():
        if len(ch_concepts) > 1:
            root_id = ch_concepts[0].concept_id
            for c in ch_concepts[1:]:
                edges.add((c.concept_id, root_id))
        for i in range(1, len(ch_concepts)):
            edges.add((ch_concepts[i].concept_id, ch_concepts[i - 1].concept_id))

    # 2. Cross-chapter curriculum prerequisites (strictly earlier chapters only)
    CROSS_CHAPTER_PREREQUISITES: dict[str, list[str]] = {
        # Chemistry prerequisites
        "periodic_table_and_periodicity": ["structure_of_atom"],
        "chemical_bonding_molecular_structure": ["structure_of_atom", "periodic_table_and_periodicity"],
        "chemical_thermodynamics_energetics": ["mole_concept_stoichiometry"],
        "chemical_equilibrium": ["mole_concept_stoichiometry", "chemical_thermodynamics_energetics"],
        "redox_reactions": ["mole_concept_stoichiometry"],
        "solutions": ["mole_concept_stoichiometry", "chemical_equilibrium"],
        "electrochemistry": ["redox_reactions", "chemical_equilibrium", "chemical_thermodynamics_energetics"],
        "chemical_kinetics": ["mole_concept_stoichiometry", "chemical_equilibrium"],
        "d_and_f_block_elements": ["periodic_table_and_periodicity", "structure_of_atom", "redox_reactions"],
        "coordination_compounds": ["chemical_bonding_molecular_structure", "d_and_f_block_elements"],
        "general_organic_chemistry_goc": ["chemical_bonding_molecular_structure"],
        "hydrocarbons": ["general_organic_chemistry_goc"],
        "alkyl_and_aryl_halides": ["hydrocarbons", "general_organic_chemistry_goc"],
        "alcohols_phenols_and_ethers": ["alkyl_and_aryl_halides", "hydrocarbons"],
        "aldehydes_ketones_and_carboxylic_acids": ["alcohols_phenols_and_ethers", "hydrocarbons"],
        "organic_compounds_containing_nitrogen": ["alkyl_and_aryl_halides", "general_organic_chemistry_goc"],
        "biomolecules_and_polymers": ["aldehydes_ketones_and_carboxylic_acids", "general_organic_chemistry_goc"],
        # Mathematics curriculum prerequisites
        "relations_and_functions_11": ["sets"],
        "relations_and_functions_12": ["relations_and_functions_11"],
        "complex_numbers_and_quadratic_equations": ["sets"],
        "linear_inequalities": ["sets"],
        "permutations_and_combinations": ["sets"],
        "binomial_theorem": ["permutations_and_combinations"],
        "sequences_and_series": ["sets"],
        "straight_lines": ["sets"],
        "conic_sections": ["straight_lines"],
        "intro_three_dimensional_geometry": ["straight_lines"],
        "limits_and_derivatives": ["relations_and_functions_11", "trigonometric_functions"],
        "statistics": ["sequences_and_series"],
        "probability_11": ["sets", "permutations_and_combinations"],
        "inverse_trigonometric_functions": ["trigonometric_functions", "relations_and_functions_12"],
        "matrices": ["sets"],
        "determinants": ["matrices"],
        "continuity_and_differentiability": ["limits_and_derivatives", "relations_and_functions_12"],
        "application_of_derivatives": ["continuity_and_differentiability"],
        "integrals": ["continuity_and_differentiability"],
        "application_of_integrals": ["integrals", "conic_sections"],
        "differential_equations": ["integrals", "continuity_and_differentiability"],
        "vector_algebra": ["intro_three_dimensional_geometry"],
        "three_dimensional_geometry": ["vector_algebra", "straight_lines"],
        "linear_programming": ["linear_inequalities"],
        "probability_12": ["probability_11", "permutations_and_combinations"],
    }

    for ch_id, prereq_chs in CROSS_CHAPTER_PREREQUISITES.items():
        curr = by_chapter_concepts.get(ch_id, [])
        if not curr:
            continue
        for p_id in prereq_chs:
            prereqs = by_chapter_concepts.get(p_id, [])
            if not prereqs:
                continue
            for src in curr[:3]:
                for dst in prereqs[:3]:
                    if src.concept_id != dst.concept_id:
                        edges.add((src.concept_id, dst.concept_id))

    requires = [{"concept_id": a, "requires": b} for a, b in sorted(edges)]
    return {"chapters": chapters, "concepts": concepts, "hints": hints, "requires": requires}


def build() -> dict[str, Any]:
    """{"chapters": [...], "concepts": [...], "requires": [...]} for the NCERT-only subjects."""
    d = _derive()
    return {"chapters": list(d["chapters"]), "concepts": list(d["concepts"]), "requires": list(d.get("requires", []))}


def section_hint(row_id: str) -> str:
    """The concept name a row's heading belongs to ('' when it has none, e.g. exercises)."""
    return _derive()["hints"].get(row_id, "")


if __name__ == "__main__":
    data = build()
    print(f"chapters {len(data['chapters'])}, concepts {len(data['concepts'])}, hinted rows {len(_derive()['hints'])}")
    for c in data["concepts"][:12]:
        print(f"  {c.concept_id:50} {c.chapter[:28]:30} aliases={c.aliases[:3]}")
