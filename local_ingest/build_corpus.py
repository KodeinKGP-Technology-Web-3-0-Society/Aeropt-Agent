"""SUPERSEDED by `local_ingest/corpus.py` — the NCERT/3-chapter corpus builder.

Kept because it is the only parser for the locally cached NCERT text extracts. Its
`SECTION_CONCEPTS` table and `_gap_theory_cards()` are exactly the hand-extraction the
current pipeline removes: see `local_ingest/syllabus.py` and `local_ingest/corpus.py`.

Build the local retrieval corpus from NCERT + in-syllabus PYQs with solutions."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from local_ingest.concept_map import (
    EXAM_ADV,
    EXAM_BOTH,
    EXAM_MAIN,
    TYPE_EXAMPLE,
    TYPE_PYQ,
    TYPE_THEORY,
    clean_text,
    difficulty_from_label,
    map_item,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / ".local-run" / "sources"
SYLLABUS = ROOT / ".local-run" / "aura_readonly"

NCERT_FILES = {
    "Laws of Motion": SOURCES / "ncert" / "keph104.raw.txt",
    "Work Energy & Power": SOURCES / "ncert" / "keph105.raw.txt",
    "Rotational Motion": SOURCES / "ncert" / "keph106.raw.txt",
}

# NCERT section number → concept_ids (rationalised Class 11 Physics Part I)
SECTION_CONCEPTS = {
    "4.3": ["newtons_1st_law"],
    "4.4": ["newtons_1st_law"],
    "4.5": ["newtons_2nd_law_f__ma"],
    "4.6": ["newtons_3rd_law"],
    "4.8": ["free_body_diagram", "normal_force", "tension"],
    "4.9": ["normal_force", "tension"],
    "4.9.1": ["static_friction", "kinetic_friction", "limiting_friction"],
    "4.11": ["free_body_diagram", "constraint_motion", "pulley_systems"],
    "5.2": ["kinetic_energy", "work-energy_theorem"],
    "5.3": ["work_by_constant_force"],
    "5.4": ["kinetic_energy"],
    "5.5": ["work_by_variable_force"],
    "5.6": ["work-energy_theorem"],
    "5.7": ["gravitational_pe", "conservative_vs_non-conservative_forces"],
    "5.8": ["conservation_of_mechanical_energy"],
    "5.9": ["spring_pe_hookes_law"],
    "5.10": ["power"],
    "6.6": ["angular_kinematics"],
    "6.7": ["torque", "angular_momentum"],
    "6.8": ["couple", "torque"],
    "6.9": ["moment_of_inertia", "parallel_axis_theorem", "perpendicular_axis_theorem"],
    "6.10": ["angular_kinematics"],
    "6.11": ["torque", "rotational_ke", "rolling_motion_pure_rolling"],
    "6.12": ["angular_momentum", "conservation_of_angular_momentum"],
}

CHAPTER_OF_SECTION = {
    "4": "Laws of Motion",
    "5": "Work Energy & Power",
    "6": "Rotational Motion",
}

HEADER_RE = re.compile(
    r"(?m)^((?:\d+\.\d+)(?:\.\d+)?)\s+([A-Z][A-Za-z0-9 ,/'’\-\(\)]+)$"
)
EXAMPLE_RE = re.compile(r"(?m)^Example\s+(\d+\.\d+)\b")
SUMMARY_RE = re.compile(r"(?m)^SUMMARY\b")


def load_syllabus() -> tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    concepts: List[Dict[str, str]] = []
    with (SYLLABUS / "concepts.csv").open() as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
    # header then "id", "name", "chapter", "subject"
    import csv
    from io import StringIO
    reader = csv.DictReader(StringIO("\n".join(lines)), skipinitialspace=True)
    for row in reader:
        concepts.append(
            {
                "concept_id": row["concept_id"].strip().strip('"'),
                "concept_name": row["name"].strip().strip('"'),
                "chapter": row["chapter"].strip().strip('"'),
                "subject": row["subject"].strip().strip('"') or "Physics",
            }
        )
    requires: List[Dict[str, str]] = []
    with (SYLLABUS / "requires.csv").open() as fh:
        reader = csv.DictReader(fh, skipinitialspace=True)
        for row in reader:
            requires.append(
                {
                    "concept_id": row["concept_id"].strip().strip('"'),
                    "requires": row["requires"].strip().strip('"'),
                }
            )
    return concepts, requires


def _split_markers(text: str) -> List[tuple[str, str, int]]:
    """Section starts for known NCERT headings, plus examples and summary."""
    markers: List[tuple[str, str, int]] = []
    for key in SECTION_CONCEPTS:
        best = None
        best_score = -1.0
        for m in re.finditer(rf"(?m)^{re.escape(key)}\s+", text):
            nl = text.find("\n", m.start())
            first = text[m.start() : nl if nl != -1 else m.start() + 80]
            if len(first) > 96:
                continue  # exercise sentence, not a heading
            letters = [c for c in first if c.isalpha()]
            if len(letters) < 3:
                continue
            score = sum(c.isupper() for c in letters) / len(letters)
            # "4.9.1 Friction" is mixed-case but is a real heading.
            if score < 0.45 and not first.lower().endswith("friction"):
                continue
            if score > best_score:
                best_score = score
                best = m.start()
        if best is not None:
            markers.append(("section", key, best))
    for m in EXAMPLE_RE.finditer(text):
        markers.append(("example", m.group(1), m.start()))
    sm = SUMMARY_RE.search(text)
    if sm:
        markers.append(("summary", "SUMMARY", sm.start()))
    markers.sort(key=lambda x: x[2])
    return markers


def parse_ncert_chapter(chapter: str, path: Path) -> List[Dict[str, Any]]:
    src_chapter = chapter
    text = path.read_text(encoding="utf-8", errors="replace")
    # Drop the TOC duplicate at the top: first real section is ALL CAPS after intro
    markers = _split_markers(text)
    items: List[Dict[str, Any]] = []
    for i, (kind, key, start) in enumerate(markers):
        end = markers[i + 1][2] if i + 1 < len(markers) else len(text)
        body = clean_text(text[start:end])
        if kind == "section":
            concepts = SECTION_CONCEPTS.get(key)
            if not concepts or len(body) < 280:
                continue
            # Skip TOC-only stubs
            if body.count("\n") < 4:
                continue
            items.append(
                _item(
                    content_id=f"theory_ncert_{key.replace('.', '_')}",
                    content_type=TYPE_THEORY,
                    chapter=chapter,
                    primary=concepts[0],
                    secondary=concepts[1:],
                    core_text=body[:6000],
                    solution="",
                    difficulty=1,
                    exam=EXAM_BOTH,
                    source_book="NCERT Class 11 Physics",
                    page_number=key,
                    question_type="theory",
                )
            )
        elif kind == "example":
            if len(body) < 80:
                continue
            q, sol = _split_answer(body)
            if len(sol) < 20:
                continue
            mapped = map_item(
                topic=src_chapter, question=q, solution=sol, min_score=3
            )
            if not mapped or mapped[2] != src_chapter:
                continue
            primary, secondary, _ = mapped
            items.append(
                _item(
                    content_id=f"example_ncert_{key.replace('.', '_')}",
                    content_type=TYPE_EXAMPLE,
                    chapter=chapter,
                    primary=primary,
                    secondary=secondary,
                    core_text=q[:4000],
                    solution=sol[:6000],
                    difficulty=2,
                    exam=EXAM_BOTH,
                    source_book="NCERT Class 11 Physics",
                    page_number=key,
                    question_type="worked_example",
                )
            )
        elif kind == "summary":
            items.extend(_summary_cards(chapter, body))
    return items


def _concepts_near_example(ex_id: str) -> tuple[str, List[str]]:
    chapter_no = ex_id.split(".")[0]
    # last section in that chapter
    keys = [k for k in SECTION_CONCEPTS if k.startswith(chapter_no + ".")]
    if not keys:
        return "", []
    concepts = SECTION_CONCEPTS[keys[-1]]
    return concepts[0], concepts[1:]


def _split_answer(body: str) -> tuple[str, str]:
    m = re.search(r"\bAnswer\b", body)
    if not m:
        return body, ""
    return clean_text(body[: m.start()]), clean_text(body[m.end() :])


def _summary_cards(chapter: str, body: str) -> List[Dict[str, Any]]:
    cards = []
    mapped = map_item(topic=chapter, question=body, min_score=2)
    if not mapped or len(body) < 200:
        return cards
    primary, secondary, chapter = mapped
    cards.append(
        _item(
            content_id=f"theory_ncert_summary_{chapter.split()[0].lower()}",
            content_type=TYPE_THEORY,
            chapter=chapter,
            primary=primary,
            secondary=secondary,
            core_text=body[:5000],
            solution="",
            difficulty=1,
            exam=EXAM_BOTH,
            source_book="NCERT Class 11 Physics",
            page_number="summary",
            question_type="theory",
        )
    )
    return cards


def parse_all_ncert() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for chapter, path in NCERT_FILES.items():
        if not path.exists():
            raise SystemExit(f"Missing NCERT extract: {path}")
        items.extend(parse_ncert_chapter(chapter, path))
    items.extend(_gap_theory_cards())
    return items


def _gap_theory_cards() -> List[Dict[str, Any]]:
    """Short textbook statements for syllabus atoms NCERT only sketches."""
    cards = [
        (
            "pseudo_force___non-inertial_frames",
            "Laws of Motion",
            "Pseudo force in a non-inertial frame",
            "In an inertial frame Newton’s second law is F_ext = m a. "
            "If we describe the same particle from a frame that has acceleration a0 "
            "relative to an inertial frame, the frame is non-inertial. In that frame "
            "we may still write m a' = F_ext + F_pseudo provided we introduce the "
            "fictitious (pseudo) force F_pseudo = −m a0. The pseudo force is not due "
            "to any physical agent; it only restores the form of Newton’s second law. "
            "In a frame rotating with angular velocity ω, a particle of mass m also "
            "experiences the Coriolis force −2m ω × v' and the centrifugal force "
            "−m ω × (ω × r'). JEE problems on accelerating lifts, wedges and carts "
            "use F_pseudo = −m a0 in the translating frame of the vehicle.",
        ),
        (
            "wedge_problems",
            "Laws of Motion",
            "Block on an accelerating or movable wedge",
            "A wedge of angle α may be fixed or free to move on a smooth horizontal "
            "surface. For a block on a fixed smooth wedge the acceleration down the "
            "incline is g sin α and the normal reaction is mg cos α. If the wedge "
            "accelerates horizontally with a, it is often easiest to work in the "
            "wedge frame: apply a pseudo force −m a on the block, then resolve "
            "along and perpendicular to the incline. For a movable wedge plus block "
            "(smooth contacts) use constraint: if the wedge moves x_w to the right "
            "and the block slides x_b along the incline, the horizontal and vertical "
            "coordinates are related by the wedge angle, and accelerations are "
            "differentiated from that constraint. Smoothness means no friction; "
            "the only contact force is the normal.",
        ),
        (
            "pulley_systems",
            "Laws of Motion",
            "Ideal pulley and string constraints",
            "An ideal pulley is massless and frictionless, so the tension is the same "
            "on both sides of a light inextensible string that passes over it. For two "
            "masses m1 > m2 in an Atwood machine, a = (m1 − m2)g / (m1 + m2) and "
            "T = 2 m1 m2 g / (m1 + m2). If a string is inextensible, the accelerations "
            "of the two ends along the string are equal in magnitude. For a movable "
            "pulley the string-length constraint typically gives a_block = 2 a_pulley. "
            "Never cancel action–reaction pairs that act on different bodies; draw a "
            "separate FBD for each mass and for the pulley if it has mass.",
        ),
        (
            "constraint_motion",
            "Laws of Motion",
            "Constraint relations for connected bodies",
            "When two or more bodies are connected by a rigid rod or an inextensible "
            "string of length ℓ, their coordinates are not independent. Write the "
            "geometric relation (for example x1 + x2 = constant, or x + 2y = ℓ) and "
            "differentiate twice with respect to time to get the acceleration "
            "constraint. The constraint force (tension or normal) does no virtual "
            "work if the string/rod is inextensible and we use coordinates consistent "
            "with the constraint. Sign convention: choose a positive direction for "
            "each body and keep it when substituting into Newton’s second law.",
        ),
        (
            "rolling_motion_pure_rolling",
            "Rotational Motion",
            "Pure rolling of a rigid body",
            "Pure rolling on a stationary surface means the point of contact is "
            "instantaneously at rest: v_cm = ω R for a disc/sphere/ring of radius R "
            "(velocity of centre of mass parallel to the surface). Then the kinetic "
            "energy is (1/2) M v_cm² + (1/2) I_cm ω² = (1/2) M v_cm² (1 + I_cm / M R²). "
            "For a body starting from rest down an incline of angle θ with static "
            "friction sufficient to prevent slipping, "
            "a_cm = g sinθ / (1 + I_cm/MR²) and "
            "f = Mg sinθ · (I_cm/MR²) / (1 + I_cm/MR²). "
            "The friction is static and does no work in pure rolling on a rigid "
            "surface because the contact point does not slide.",
        ),
        (
            "rolling_with_slipping",
            "Rotational Motion",
            "Rolling with slipping",
            "If |v_cm − ω R| ≠ 0 the contact point slides and kinetic friction acts, "
            "f_k = μ_k N, opposite to the slip velocity of the contact point. Kinetic "
            "friction both decelerates/accelerates v_cm and provides a torque that "
            "changes ω, until v_cm = ω R (pure rolling may be achieved) or the body "
            "stops sliding. Work is done by kinetic friction against sliding, so "
            "mechanical energy is not conserved. Use translation: F_ext = M a_cm and "
            "rotation about CM: τ_cm = I_cm α, with the same f_k in both equations.",
        ),
        (
            "parallel_axis_theorem",
            "Rotational Motion",
            "Parallel-axis theorem",
            "If I_cm is the moment of inertia of a rigid body about an axis through "
            "the centre of mass, the moment of inertia about a parallel axis at "
            "perpendicular distance d is I = I_cm + M d². The axes must be parallel; "
            "the theorem holds for any rigid body, not only laminas. Typical JEE use: "
            "a rod about an end (I_end = ML²/12 + M(L/2)² = ML²/3), or a disc about "
            "a tangent perpendicular to its plane.",
        ),
        (
            "perpendicular_axis_theorem",
            "Rotational Motion",
            "Perpendicular-axis theorem",
            "For a planar lamina in the xy-plane, the moment of inertia about the "
            "z-axis (perpendicular to the plane) equals the sum of the moments about "
            "two perpendicular in-plane axes through the same origin: I_z = I_x + I_y. "
            "This does not apply to 3-D bodies that are not planar. Example: for a "
            "circular disc I_z = (1/2) MR² about its centre, so each diameter has "
            "I_x = I_y = (1/4) MR².",
        ),
        (
            "couple",
            "Rotational Motion",
            "Couple",
            "A couple is a pair of equal, opposite, parallel forces whose lines of "
            "action do not coincide. The net force is zero so the CM does not "
            "accelerate, but the net torque is τ = F × d where d is the perpendicular "
            "separation of the lines of action. The torque of a couple is independent "
            "of the point about which it is calculated. A couple cannot be balanced "
            "by a single force; it is balanced by another couple of equal and "
            "opposite moment.",
        ),
        (
            "static_friction",
            "Laws of Motion",
            "Static friction",
            "Static friction f_s acts between two surfaces that are not sliding. It "
            "opposes impending relative motion and is self-adjusting: 0 ≤ f_s ≤ f_s,max "
            "with f_s,max = μ_s N. The direction is opposite to the tendency to slip. "
            "On a horizontal surface, a force F less than μ_s N produces no acceleration. "
            "μ_s depends on the pair of surfaces, not on the area of contact (to a good "
            "approximation).",
        ),
        (
            "kinetic_friction",
            "Laws of Motion",
            "Kinetic friction",
            "Once sliding starts, kinetic (sliding) friction f_k = μ_k N opposes the "
            "relative velocity of the contact surfaces. μ_k is usually a little smaller "
            "than μ_s. Kinetic friction dissipates mechanical energy as heat, so "
            "mechanical energy is not conserved while it acts.",
        ),
        (
            "limiting_friction",
            "Laws of Motion",
            "Limiting friction",
            "Limiting friction is the maximum static friction f_L = μ_s N, reached when "
            "motion is just about to start. If the applied tangential force exceeds f_L, "
            "the body starts sliding and friction becomes the kinetic value μ_k N. "
            "On an incline the body is on the verge of sliding when tan θ = μ_s "
            "(angle of repose).",
        ),
        (
            "tension",
            "Laws of Motion",
            "Tension in a string",
            "A light inextensible string pulls a body along its length with tension T, "
            "directed away from the body along the string. For a massless string the "
            "tension is the same throughout. A string can only pull, never push; if it "
            "slacks, T = 0. For a body hanging at rest T = mg; in a lift accelerating "
            "upward with a, T = m(g+a).",
        ),
        (
            "conservation_of_angular_momentum",
            "Rotational Motion",
            "Conservation of angular momentum",
            "If the net external torque on a system about a point (or axis) is zero, "
            "the total angular momentum about that point is constant. A spinning ice "
            "skater speeds up when she pulls her arms in because I decreases while Iω "
            "stays constant. Internal torques cancel in pairs when they are central.",
        ),
        (
            "rotational_ke",
            "Rotational Motion",
            "Rotational kinetic energy",
            "A rigid body rotating with angular speed ω about a fixed axis has "
            "rotational kinetic energy K_rot = (1/2) I ω². If the body is also "
            "translating, K = (1/2) M v_cm² + (1/2) I_cm ω². About a fixed axis the "
            "work–energy theorem reads W_τ = ΔK_rot.",
        ),
        (
            "conservative_vs_non-conservative_forces",
            "Work Energy & Power",
            "Conservative and non-conservative forces",
            "A force is conservative if the work between two points is path-independent "
            "(work around every closed loop is zero). Then a potential energy U exists "
            "with F = −∇U, and K+U is conserved if only conservative forces do work. "
            "Gravity and the ideal spring force are conservative. Kinetic friction and "
            "applied motor forces are non-conservative: Δ(K+U) = W_nc.",
        ),
    ]
    items = []
    for cid, chapter, title, text in cards:
        items.append(
            _item(
                content_id=f"theory_card_{cid[:40]}",
                content_type=TYPE_THEORY,
                chapter=chapter,
                primary=cid,
                secondary=[],
                core_text=f"{title}\n\n{text}",
                solution="",
                difficulty=1,
                exam=EXAM_BOTH,
                source_book="NCERT/JEE standard mechanics (gap card)",
                page_number="",
                question_type="theory",
            )
        )
    return items


def parse_hf_pyqs() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    files = [
        (SOURCES / "hf" / "jee_main_physics_train.jsonl", EXAM_MAIN),
        (SOURCES / "hf" / "jee_main_physics_test.jsonl", EXAM_MAIN),
        (SOURCES / "hf" / "jee_adv_physics_train.jsonl", EXAM_ADV),
        (SOURCES / "hf" / "jee_adv_physics_test.jsonl", EXAM_ADV),
    ]
    for path, default_exam in files:
        if not path.exists():
            raise SystemExit(f"Missing PYQ file: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            item = _pyq_from_hf(row, default_exam)
            if item and item["content_id"] not in seen:
                seen.add(item["content_id"])
                items.append(item)
    return items


def _pyq_from_hf(row: Dict[str, Any], default_exam: str) -> Optional[Dict[str, Any]]:
    question = clean_text(row.get("question") or "")
    solution = clean_text(row.get("solution") or "")
    if len(question) < 40 or len(solution) < 40:
        return None
    if question.strip() in {"[IMAGE]", ""}:
        return None
    options = []
    for i, key in enumerate(("option_1", "option_2", "option_3", "option_4"), start=1):
        val = clean_text(row.get(key) or "")
        if val:
            options.append(f"({i}) {val}")
    core = question if not options else question + "\n\n" + "\n".join(options)
    answer_bits = []
    if row.get("correct_option") not in (None, "", 0):
        answer_bits.append(f"Correct option: {row.get('correct_option')}")
    if row.get("numerical_answer"):
        answer_bits.append(f"Numerical answer: {row.get('numerical_answer')}")
    if row.get("answer"):
        answer_bits.append(f"Answer: {row.get('answer')}")
    if answer_bits:
        solution = solution + "\n\n" + "\n".join(answer_bits)
    mapped = map_item(
        topic=str(row.get("topic") or ""),
        subtopic=str(row.get("subtopic") or ""),
        question=core,
        solution=solution,
        min_score=4,
        allow_chapter_fallback=True,
    )
    if not mapped:
        return None
    primary, secondary, chapter = mapped
    exam_label = str(row.get("exam") or "")
    exam = EXAM_ADV if "advanced" in exam_label.lower() else default_exam
    qid = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(row.get("question_id") or "q"))
    qtype = str(row.get("question_type") or "single_correct")
    return _item(
        content_id=f"pyq_{qid}",
        content_type=TYPE_PYQ,
        chapter=chapter,
        primary=primary,
        secondary=secondary,
        core_text=core[:8000],
        solution=solution[:8000],
        difficulty=difficulty_from_label(str(row.get("difficulty") or ""), exam=exam),
        exam=exam,
        source_book=str(row.get("source_paper") or exam_label or "JEE"),
        page_number="",
        question_type=qtype,
        has_diagram=bool(row.get("has_image")),
    )


def _item(
    *,
    content_id: str,
    content_type: str,
    chapter: str,
    primary: str,
    secondary: Iterable[str],
    core_text: str,
    solution: str,
    difficulty: int,
    exam: str,
    source_book: str,
    page_number: str,
    question_type: str,
    has_diagram: bool = False,
) -> Dict[str, Any]:
    secondary = [s for s in secondary if s and s != primary]
    return {
        "content_id": content_id,
        "content_type": content_type,
        "subject": "Physics",
        "chapter": chapter,
        "source_book": source_book,
        "page_number": page_number,
        "core_text": clean_text(core_text),
        "primary_concept": primary,
        "secondary_concepts": ", ".join(secondary),
        "prerequisite_concepts": "",
        "solution": clean_text(solution),
        "difficulty": difficulty,
        "question_type": question_type,
        "has_diagram": has_diagram,
        "has_image_core": has_diagram,
        "has_image_solution": False,
        "target_exam": exam,
        "reviewed": True,
        "primary_concept_id": primary,
        "embed_text": clean_text(core_text + "\n" + (solution[:800] if solution else "")),
    }


def build_corpus() -> tuple[List[Dict[str, str]], List[Dict[str, str]], List[Dict[str, Any]]]:
    concepts, requires = load_syllabus()
    items = parse_all_ncert() + parse_hf_pyqs()
    # drop empty bodies
    items = [it for it in items if len(it["core_text"]) >= 40]
    by_id: Dict[str, Dict[str, Any]] = {}
    for it in items:
        by_id[it["content_id"]] = it
    return concepts, requires, list(by_id.values())
