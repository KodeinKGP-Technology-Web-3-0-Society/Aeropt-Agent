"""Map JEE/NCERT text onto the 33 PrepWise concept_ids.

Be conservative: skip rather than attach a question to the wrong concept.
The solver will hallucinate less from a miss than from a wrong PYQ.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# Resolver / Neo4j exam allowlists (db/neo4j_client.py)
EXAM_MAIN = "Jee main"
EXAM_ADV = "Jee Advanced"
EXAM_BOTH = "Jee main and Advanced"

TYPE_THEORY = "Theory"
TYPE_EXAMPLE = "Solved Example"
TYPE_PRACTICE = "Practice Question"
TYPE_PYQ = "PYQ"

CHAPTERS = (
    "Laws of Motion",
    "Rotational Motion",
    "Work Energy & Power",
)

# topic/subtopic strings from Grass-G / eQOURSE JEE datasets → our chapter
_TOPIC_CHAPTER = {
    "newton's laws of motion": "Laws of Motion",
    "newtons laws of motion": "Laws of Motion",
    "laws of motion": "Laws of Motion",
    "friction": "Laws of Motion",
    "work power & energy": "Work Energy & Power",
    "work power and energy": "Work Energy & Power",
    "work energy & power": "Work Energy & Power",
    "work, energy and power": "Work Energy & Power",
    "work energy and power": "Work Energy & Power",
    "rigid body dynamics": "Rotational Motion",
    "rotational motion": "Rotational Motion",
    "rotational dynamics": "Rotational Motion",
    "system of particles": "Rotational Motion",
    "systems of particles": "Rotational Motion",
    "systems of particles and rotational motion": "Rotational Motion",
}

_OFF_SYLLABUS_TOPIC = (
    "thermodynamic",
    "kinetic theory",
    "ideal gas",
    "electrostat",
    "current electricity",
    "magnet",
    "emi",
    "optics",
    "wave optics",
    "ray optics",
    "modern physics",
    "dual nature",
    "semiconductor",
    "electronic",
    "fluid",
    "surface tension",
    "viscosity",
    "shm",
    "simple harmonic",
    "oscillation",
    "sound wave",
    "string wave",
    "gravitation",
    "satellite",
    "projectile",
    "kinematics",
    "relative motion",  # often 1D/2D kinematics, not NLM
    "center of mass",
    "centre of mass",
    "collision",
    "capacitance",
    "heat",
    "calorimetr",
    "elasticity",
    "young",
)

# (concept_id, weight, phrases)
_CONCEPT_PHRASES: List[Tuple[str, int, Tuple[str, ...]]] = [
    ("newtons_1st_law", 5, ("newton's first law", "newtons first law", "law of inertia", "first law of motion")),
    ("newtons_2nd_law_f__ma", 5, ("newton's second law", "newtons second law", "second law of motion", "f = ma", "f=ma")),
    ("newtons_3rd_law", 5, ("newton's third law", "newtons third law", "third law of motion", "action and reaction", "action-reaction")),
    ("free_body_diagram", 4, ("free body diagram", "free-body diagram", "fbd")),
    ("normal_force", 4, ("normal force", "normal reaction", "normal contact force")),
    ("tension", 4, ("tension", "string is taut", "massless string")),
    ("pulley_systems", 5, ("pulley", "atwood", "two blocks connected by a string")),
    ("wedge_problems", 5, ("wedge", "movable wedge", "block on a wedge")),
    ("static_friction", 4, ("static friction", "impending motion", "coefficient of static friction", "μs", "\\mu_s", "µs")),
    ("kinetic_friction", 4, ("kinetic friction", "sliding friction", "coefficient of kinetic friction", "μk", "\\mu_k", "µk")),
    ("limiting_friction", 5, ("limiting friction", "maximum static friction", "μ n", "\\mu n")),
    ("constraint_motion", 4, ("constraint", "inextensible", "related acceleration", "constraint relation")),
    ("pseudo_force___non-inertial_frames", 6, ("pseudo force", "fictitious force", "non-inertial", "non inertial", "accelerating frame")),
    ("work_by_constant_force", 4, ("constant force", "work done by a constant", "w = fs", "w=fs")),
    ("work_by_variable_force", 5, ("variable force", "f dx", "\\int f", "work done by a variable")),
    ("kinetic_energy", 3, ("kinetic energy", "translational ke", "1/2 mv", "\\frac{1}{2}mv")),
    ("work-energy_theorem", 5, ("work-energy theorem", "work energy theorem", "work–energy theorem", "we theorem")),
    ("gravitational_pe", 4, ("gravitational potential", "mgh", "gravitational pe")),
    ("spring_pe_hookes_law", 5, ("hooke's law", "hookes law", "spring potential", "1/2 kx", "\\frac{1}{2}kx", "elastic potential")),
    ("conservation_of_mechanical_energy", 5, ("conservation of mechanical energy", "mechanical energy is conserved", "conservative force field")),
    ("conservative_vs_non-conservative_forces", 5, ("conservative force", "non-conservative", "nonconservative", "path independent")),
    ("power", 4, ("instantaneous power", "average power", "p = fv", "p=f.v", "power delivered")),
    ("angular_kinematics", 4, ("angular velocity", "angular acceleration", "ω =", "\\omega", "angular kinematics", "θ = ωt")),
    ("torque", 4, ("torque", "moment of force", "τ =", "\\tau =")),
    ("couple", 5, ("couple", "two equal and opposite forces", "moment of a couple")),
    ("moment_of_inertia", 4, ("moment of inertia", "mi of", "i =", "rotational inertia")),
    ("parallel_axis_theorem", 6, ("parallel axis", "parallel-axis", "i = i_cm + md", "steiner")),
    ("perpendicular_axis_theorem", 6, ("perpendicular axis", "perpendicular-axis", "i_z = i_x + i_y")),
    ("angular_momentum", 4, ("angular momentum", "l = iω", "l = r × p", "\\vec{l}")),
    ("conservation_of_angular_momentum", 6, ("conservation of angular momentum", "angular momentum is conserved")),
    ("rotational_ke", 5, ("rotational kinetic", "1/2 iω", "\\frac{1}{2}i\\omega", "rotational ke")),
    ("rolling_motion_pure_rolling", 5, ("pure rolling", "rolling without slipping", "v = rω", "v=r\\omega")),
    ("rolling_with_slipping", 6, ("rolling with slipping", "rolling with sliding", "kinetic friction rolling")),
]

_CHAPTER_HINTS = {
    "Laws of Motion": (
        "newton", "friction", "tension", "pulley", "wedge", "fbd", "pseudo",
        "normal force", "inertia", "constraint", "atwood",
    ),
    "Rotational Motion": (
        "torque", "angular", "moment of inertia", "rolling", "rigid body",
        "couple", "rotational", "ω", "omega",
    ),
    "Work Energy & Power": (
        "work-energy", "work energy", "kinetic energy", "potential energy",
        "hooke", "power delivered", "conservative", "mgh",
    ),
}


def _norm(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text)


def chapter_from_topic(topic: str, subtopic: str = "") -> Optional[str]:
    blob = _norm(f"{topic} {subtopic}")
    if not blob.strip():
        return None
    if any(bad in blob for bad in _OFF_SYLLABUS_TOPIC):
        # friction is a topic by itself; don't treat "friction" in fluids as NLM
        if "friction" in blob and "fluid" not in blob and "visc" not in blob:
            return "Laws of Motion"
        return None
    for key, chapter in _TOPIC_CHAPTER.items():
        if key in blob:
            return chapter
    return None


def score_concepts(text: str) -> List[Tuple[str, int]]:
    blob = _norm(text)
    scores: dict[str, int] = {}
    for concept_id, weight, phrases in _CONCEPT_PHRASES:
        hits = sum(1 for p in phrases if p in blob)
        if hits:
            scores[concept_id] = scores.get(concept_id, 0) + weight * hits
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def infer_chapter_from_text(text: str) -> Optional[str]:
    blob = _norm(text)
    if any(bad in blob for bad in _OFF_SYLLABUS_TOPIC) and not any(
        h in blob for h in ("newton", "torque", "moment of inertia", "work-energy", "pure rolling")
    ):
        return None
    counts = {
        chapter: sum(1 for h in hints if h in blob)
        for chapter, hints in _CHAPTER_HINTS.items()
    }
    best = max(counts, key=counts.get)
    return best if counts[best] >= 1 else None


def map_item(
    *,
    topic: str = "",
    subtopic: str = "",
    question: str = "",
    solution: str = "",
    min_score: int = 3,
    allow_chapter_fallback: bool = False,
) -> Optional[Tuple[str, List[str], str]]:
    """Return (primary_concept_id, secondary_ids, chapter) or None."""
    chapter = chapter_from_topic(topic, subtopic)
    blob = f"{topic} {subtopic} {question} {solution}"
    if chapter is None:
        chapter = infer_chapter_from_text(blob)
    if chapter is None:
        return None
    ranked = score_concepts(blob)
    ranked = [(cid, sc) for cid, sc in ranked if _concept_chapter(cid) == chapter or sc >= 6]
    if not ranked or ranked[0][1] < min_score:
        if allow_chapter_fallback:
            core = _CHAPTER_CORE.get(chapter)
            if core and chapter_from_topic(topic, subtopic):
                return core, [], chapter
        return None
    primary = ranked[0][0]
    secondary = [cid for cid, sc in ranked[1:4] if sc >= min_score and cid != primary]
    return primary, secondary, _concept_chapter(primary) or chapter


def _concept_chapter(concept_id: str) -> Optional[str]:
    return _CONCEPT_CHAPTER.get(concept_id)


_CONCEPT_CHAPTER = {
    "constraint_motion": "Laws of Motion",
    "free_body_diagram": "Laws of Motion",
    "kinetic_friction": "Laws of Motion",
    "limiting_friction": "Laws of Motion",
    "newtons_1st_law": "Laws of Motion",
    "newtons_2nd_law_f__ma": "Laws of Motion",
    "newtons_3rd_law": "Laws of Motion",
    "normal_force": "Laws of Motion",
    "pseudo_force___non-inertial_frames": "Laws of Motion",
    "pulley_systems": "Laws of Motion",
    "static_friction": "Laws of Motion",
    "tension": "Laws of Motion",
    "wedge_problems": "Laws of Motion",
    "angular_kinematics": "Rotational Motion",
    "angular_momentum": "Rotational Motion",
    "conservation_of_angular_momentum": "Rotational Motion",
    "couple": "Rotational Motion",
    "moment_of_inertia": "Rotational Motion",
    "parallel_axis_theorem": "Rotational Motion",
    "perpendicular_axis_theorem": "Rotational Motion",
    "rolling_motion_pure_rolling": "Rotational Motion",
    "rolling_with_slipping": "Rotational Motion",
    "rotational_ke": "Rotational Motion",
    "torque": "Rotational Motion",
    "conservation_of_mechanical_energy": "Work Energy & Power",
    "conservative_vs_non-conservative_forces": "Work Energy & Power",
    "gravitational_pe": "Work Energy & Power",
    "kinetic_energy": "Work Energy & Power",
    "power": "Work Energy & Power",
    "spring_pe_hookes_law": "Work Energy & Power",
    "work-energy_theorem": "Work Energy & Power",
    "work_by_constant_force": "Work Energy & Power",
    "work_by_variable_force": "Work Energy & Power",
}

_CHAPTER_CORE = {
    "Laws of Motion": "newtons_2nd_law_f__ma",
    "Rotational Motion": "moment_of_inertia",
    "Work Energy & Power": "work-energy_theorem",
}


def difficulty_from_label(label: str, *, exam: str) -> int:
    key = (label or "").strip().lower()
    table = {"easy": 2, "moderate": 3, "medium": 3, "tough": 4, "hard": 4, "difficult": 4}
    if key in table:
        return table[key]
    if exam == EXAM_ADV:
        return 5
    if exam == EXAM_MAIN:
        return 3
    return 2


def clean_text(text: str) -> str:
    text = (text or "").replace("\x00", "")
    text = text.replace("Reprint 2026-27", " ")
    text = re.sub(r"(?m)^(PHYSICS|LAWS OF MOTION|WORK, ENERGY AND POWER|SYSTEMS OF PARTICLES AND ROTATIONAL MOTION)\s+\d+\s*$", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
