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
SUBJECTS = ("Chemistry",)          # NCERT subjects whose concepts come from here
ORDER_BASE = {"Chemistry": 1000}   # chapter/concept `ord` after physics

_NUMBERED = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?\.?\s+(.+)$")
_SKIP = {"summary", "answers to some intext questions", "exercises", "intext questions"}
_SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with", "into", "its"}


def _pretty(title: str) -> str:
    """'DISCOVERY OF SUB-ATOMIC PARTICLES' -> 'Discovery of Sub-atomic Particles'. Mixed case kept."""
    title = re.sub(r"\s+", " ", title).strip(" :*")
    letters = [ch for ch in title if ch.isalpha()]
    if not letters or not all(ch.isupper() for ch in letters):
        return title
    words = title.lower().split(" ")
    return " ".join(w if (i and w in _SMALL) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def _key(heading: str) -> str:
    """Heading text without its number, normalised — how examples refer to theory sections."""
    m = _NUMBERED.match(heading.strip())
    return norm(m.group(4) if m else heading)


@lru_cache(maxsize=1)
def _derive() -> dict[str, Any]:
    if not NCERT_JSONL.exists():
        return {"chapters": [], "concepts": [], "hints": {}}
    rows = [json.loads(line) for line in NCERT_JSONL.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if r.get("subject") in SUBJECTS]

    chapters: list[Chapter] = []
    concepts: list[Concept] = []
    hints: dict[str, str] = {}
    taken: set[str] = set()
    by_code: dict[str, list[dict]] = {}
    for r in rows:
        by_code.setdefault(r["chapter_code"], []).append(r)

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
                current = Concept(
                    concept_id=cid, name=name, definition=re.sub(r"\s+", " ", r.get("text") or "")[:400],
                    chapter_id=chapter.chapter_id, chapter=chapter.title, unit=chapter.unit, in_syllabus=True,
                    module_id=chapter.chapter_id, order=base * 10 + len(concepts), aliases=[],
                    source="ncert_section", subject=subject,
                )
                concepts.append(current)
            elif current is not None and heading and norm(heading) not in _SKIP and not _NUMBERED.match(heading):
                # An unnumbered heading ("Aufbau Principle") names something taught in the current section.
                alias = _pretty(re.sub(r"^(\(?[a-z0-9ivx]+[.)]\s*)", "", heading, flags=re.I))
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
    return {"chapters": chapters, "concepts": concepts, "hints": hints}


def build() -> dict[str, Any]:
    """{"chapters": [...], "concepts": [...]} for the NCERT-only subjects."""
    d = _derive()
    return {"chapters": list(d["chapters"]), "concepts": list(d["concepts"])}


def section_hint(row_id: str) -> str:
    """The concept name a row's heading belongs to ('' when it has none, e.g. exercises)."""
    return _derive()["hints"].get(row_id, "")


if __name__ == "__main__":
    data = build()
    print(f"chapters {len(data['chapters'])}, concepts {len(data['concepts'])}, hinted rows {len(_derive()['hints'])}")
    for c in data["concepts"][:12]:
        print(f"  {c.concept_id:50} {c.chapter[:28]:30} aliases={c.aliases[:3]}")
