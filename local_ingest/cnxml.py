"""Parse one OpenStax CNXML module into retrieval-ready blocks.

Structure-driven, not pattern-driven: every block the corpus builder needs is an
actual CNXML element (`section`, `example`, `exercise`, `definition`), so nothing
here depends on a chapter's prose conventions. That is what makes the corpus
extend to the whole book without per-chapter tables.

Output per module:
    module_id, title, links (cross-referenced module ids)
    sections   -> [{title, text, path}]              teaching prose + equations
    examples   -> [{title, question, solution}]      worked examples
    exercises  -> [{kind, question, solution}]        practice / conceptual
    glossary   -> [{term, meaning}]                  concept definitions
    key_concepts -> str                              chapter summary prose
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from local_ingest.mathml import inline, mathml_to_latex

CNXML_NS = "http://cnx.rice.edu/cnxml"
MD_NS = "http://cnx.rice.edu/mdml"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"

_DROP_TAGS = {"media", "image", "iframe", "annotation", "labelless", "cite-title"}
_SKIP_CLASSES = {"splash"}


def _tag(el: ET.Element) -> str:
    return el.tag.split("}")[-1]


def _clean(text: str) -> str:
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _escape_prose_dollars(text: str) -> str:
    r"""Escape `$` coming from the source text.

    The only unescaped `$` in a chunk should be one *this* module wrote around a
    converted formula. A `$` in the prose is a price ("costs $0.0900 per kWh") and
    would otherwise open a math span that swallows the rest of the paragraph.
    """
    return (text or "").replace("$", r"\$")


def _text_of(el: ET.Element, *, keep_titles: bool = True) -> str:
    """Flatten an element's prose, turning MathML into inline LaTeX."""
    out: list[str] = []

    def walk(node: ET.Element) -> None:
        tag = _tag(node)
        if tag == "media" and (node.get("alt") or "").strip():
            # The figure itself cannot be shown, but its accessibility text can:
            # it usually carries the values ("F1 = 10 N points down…") that a
            # "shown below" problem is unanswerable without.
            out.append("\n[Figure: " + _escape_prose_dollars(" ".join(node.get("alt").split())) + "]\n")
            if node.tail:
                out.append(_escape_prose_dollars(node.tail))
            return
        if tag in _DROP_TAGS:
            if node.tail:
                out.append(_escape_prose_dollars(node.tail))
            return
        if node.tag == f"{{{MATHML_NS}}}math":
            out.append(" " + inline(mathml_to_latex(node)) + " ")
            if node.tail:
                out.append(_escape_prose_dollars(node.tail))
            return
        if tag == "newline":
            out.append("\n")
            if node.tail:
                out.append(_escape_prose_dollars(node.tail))
            return
        if tag == "title" and not keep_titles:
            if node.tail:
                out.append(_escape_prose_dollars(node.tail))
            return

        block = tag in {"para", "item", "title", "caption", "equation", "list", "note", "table"}
        if block:
            out.append("\n")
        if node.text:
            out.append(_escape_prose_dollars(node.text))
        for child in node:
            walk(child)
        if block:
            out.append("\n")
        if node.tail:
            out.append(_escape_prose_dollars(node.tail))

    walk(el)
    return _clean("".join(out))


_CONTAINERS = {"exercise", "example", "section", "content"}
_LINK_NOUN = {"figure": "the figure", "equation": "the equation", "table": "the table", "example": "the example"}


def _figure_text(fig: ET.Element) -> str:
    media = fig.find(f".//{{{CNXML_NS}}}media")
    alt = " ".join(((media.get("alt") if media is not None else "") or "").split())
    if alt:
        return alt
    caption = fig.find(f"{{{CNXML_NS}}}caption")
    return " ".join("".join(caption.itertext()).split()) if caption is not None else ""


def _name_empty_links(root: ET.Element) -> None:
    """Give `<link target-id=…/>` the words the rendered book shows instead.

    The book renders an empty link as "Figure 5.12"; flattened, it left "shown in ."
    — and a worked example whose setup lives in a figure outside it lost that setup
    entirely. A figure outside the block being read is described inline.
    """
    by_id = {el.get("id"): el for el in root.iter() if el.get("id")}
    parent = {child: el for el in root.iter() for child in el}

    def container(el: ET.Element) -> ET.Element | None:
        while el is not None and _tag(el) not in _CONTAINERS:
            el = parent.get(el)
        return el

    for link in root.iter(f"{{{CNXML_NS}}}link"):
        if (link.text or "").strip() or len(link) or link.get("document"):
            continue
        target = by_id.get(link.get("target-id") or "")
        if target is None:
            continue
        noun = _LINK_NOUN.get(_tag(target))
        if not noun:
            continue
        link.text = noun
        if _tag(target) != "figure":
            continue
        home = container(link)
        inside = home is not None and any(el is target for el in home.iter())
        description = _figure_text(target)
        if not inside and description:
            link.text = f"{noun} [Figure: {description}]"


@dataclass
class Module:
    module_id: str
    title: str
    css_class: str = ""
    abstract: str = ""
    links: list[str] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    examples: list[dict[str, Any]] = field(default_factory=list)
    exercises: list[dict[str, Any]] = field(default_factory=list)
    glossary: list[dict[str, str]] = field(default_factory=list)
    key_concepts: str = ""


def _split_example(el: ET.Element) -> dict[str, Any]:
    """An OpenStax example is question paras, then Strategy, then Solution/Significance."""
    title = ""
    q_parts: list[str] = []
    sol_parts: list[str] = []
    bucket = "question"
    for child in el:
        tag = _tag(child)
        if tag == "title" and not title:
            title = _clean(_text_of(child))
            continue
        label = ""
        title_el = child.find(f"{{{CNXML_NS}}}title") if tag in {"para", "section"} else None
        if title_el is not None:
            label = (title_el.text or "").strip().lower()
        if label in {"strategy", "solution", "significance", "check your understanding"}:
            bucket = "solution"
        if not title and tag == "para" and title_el is not None and label not in {
            "strategy", "solution", "significance", "check your understanding"
        }:
            title = (title_el.text or "").strip()
        body = _text_of(child, keep_titles=True)
        if not body:
            continue
        (q_parts if bucket == "question" else sol_parts).append(body)
    return {
        "title": _clean(title),
        "question": _clean("\n\n".join(q_parts)),
        "solution": _clean("\n\n".join(sol_parts)),
    }


def parse_module(path: Path) -> Module:
    root = ET.parse(path).getroot()
    mid = path.stem
    title_el = root.find(f"{{{CNXML_NS}}}title")
    md = root.find(f"{{{CNXML_NS}}}metadata")
    abstract = ""
    if md is not None:
        abs_el = md.find(f"{{{MD_NS}}}abstract")
        if abs_el is not None:
            abstract = _clean(_text_of(abs_el))
    mod = Module(
        module_id=mid,
        title=_clean((title_el.text or "") if title_el is not None else mid),
        css_class=root.get("class", ""),
        abstract=abstract,
    )

    content = root.find(f"{{{CNXML_NS}}}content")
    if content is None:
        return mod

    for link in root.iter(f"{{{CNXML_NS}}}link"):
        doc = link.get("document")
        if doc and doc not in mod.links:
            mod.links.append(doc)
    _name_empty_links(root)

    consumed: set[ET.Element] = set()

    for ex in content.iter(f"{{{CNXML_NS}}}example"):
        consumed.add(ex)
        parsed = _split_example(ex)
        if parsed["question"]:
            mod.examples.append(parsed)

    # The enclosing section's class is what distinguishes a numeric problem from a
    # conceptual question ("review-problems" vs "review-conceptual-questions"),
    # so exercises are collected with that context rather than globally.
    section_class: dict[ET.Element, str] = {}

    def label_sections(el: ET.Element, inherited: str) -> None:
        for child in el:
            if _tag(child) != "section":
                continue
            cls = child.get("class") or inherited
            section_class[child] = cls
            label_sections(child, cls)

    label_sections(content, "")

    def collect_exercises(el: ET.Element, cls: str) -> None:
        for child in el:
            tag = _tag(child)
            if tag == "section":
                collect_exercises(child, section_class.get(child, cls))
                continue
            if tag != "exercise":
                collect_exercises(child, cls)
                continue
            consumed.add(child)
            prob = child.find(f"{{{CNXML_NS}}}problem")
            sol = child.find(f"{{{CNXML_NS}}}solution")
            question = _text_of(prob) if prob is not None else ""
            if not question:
                continue
            mod.exercises.append(
                {
                    "kind": child.get("class") or cls,
                    "question": question,
                    "solution": _text_of(sol) if sol is not None else "",
                }
            )

    collect_exercises(content, "")

    # Sections: teaching prose with examples/exercises removed, so a theory chunk
    # never smuggles in a question the practice path is supposed to serve.
    def section_prose(el: ET.Element) -> str:
        parts: list[str] = []
        for child in el:
            if child in consumed or _tag(child) in {"section"}:
                continue
            if child.get("class") in _SKIP_CLASSES:
                continue
            body = _text_of(child)
            if body:
                parts.append(body)
        return _clean("\n\n".join(parts))

    def walk_sections(el: ET.Element, trail: list[str]) -> None:
        for child in el:
            if _tag(child) != "section":
                continue
            st_el = child.find(f"{{{CNXML_NS}}}title")
            st = _clean((st_el.text or "") if st_el is not None else "")
            cls = child.get("class") or ""
            path_now = trail + ([st] if st else [])
            if cls == "key-concepts":
                mod.key_concepts = section_prose(child)
            else:
                prose = section_prose(child)
                # A section that only holds exercises flattens to its own title;
                # that is not teaching prose and must not become a theory chunk.
                if prose and len(prose) >= 160:
                    mod.sections.append({"title": st, "text": prose, "path": path_now, "class": cls})
            walk_sections(child, path_now)

    top_prose = section_prose(content)
    if top_prose:
        mod.sections.append({"title": mod.title, "text": top_prose, "path": [mod.title], "class": "intro"})
    walk_sections(content, [mod.title])

    glossary = root.find(f"{{{CNXML_NS}}}glossary")
    if glossary is not None:
        for defn in glossary.iter(f"{{{CNXML_NS}}}definition"):
            term_el = defn.find(f"{{{CNXML_NS}}}term")
            mean_el = defn.find(f"{{{CNXML_NS}}}meaning")
            term = _clean(_text_of(term_el)) if term_el is not None else ""
            meaning = _clean(_text_of(mean_el)) if mean_el is not None else ""
            if term and meaning:
                mod.glossary.append({"term": term, "meaning": meaning})

    return mod
