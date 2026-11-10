"""Versioned exam blueprints and verified organizing-institute records.

Only structures and institutes that are named by an official document are marked
verified. A year with no such record is "unconfirmed". Nothing here predicts a
future paper from the organizing institute.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# NTA Information Bulletin, JEE (Main) 2025, Paper 1 (B.E./B.Tech.):
# 20 MCQ + 5 numerical per subject, all compulsory, +4 / −1 in both sections,
# 180 minutes for the full three-subject paper.
# https://cdnbbsr.s3waas.gov.in/s3f8e59f4b2fe7c5705bf878bbd494ccdf/uploads/2024/10/2024102824.pdf
_MAIN_SECTION_A = {
    "id": "single",
    "title": "Section A · Single correct",
    "formats": ["single_correct", "comprehension"],
    "full_count": 20,
    "marks": {"correct": 4, "incorrect": -1, "unattempted": 0},
    "instructions": (
        "Section A. Only one option is correct. "
        "+4 if your choice matches the key, −1 if it does not, 0 if you leave it."
    ),
}
_MAIN_SECTION_B = {
    "id": "numerical",
    "title": "Section B · Numerical",
    "formats": ["numeric", "integer", "numerical"],
    "full_count": 5,
    "marks": {"correct": 4, "incorrect": -1, "unattempted": 0, "numeric_mode": "nearest_integer"},
    "instructions": (
        "Section B. Enter the numerical answer, rounded to the nearest integer. "
        "+4 if it matches the key, −1 if it does not, 0 if you leave it."
    ),
}

# Not an official Advanced paper. Advanced papers change by year, and this
# product does not invent a year's section counts. Marks are the type rules
# already used for Advanced-style questions in this codebase.
_ADVANCED_PRACTICE = [
    {
        "id": "single",
        "title": "Single correct",
        "formats": ["single_correct", "comprehension"],
        "share": 0.4,
        "marks": {"correct": 3, "incorrect": -1, "unattempted": 0},
        "instructions": (
            "Single correct. +3 if your choice matches the key, −1 if it does not, 0 if you leave it. "
            "This is an Advanced-style practice section, not a copy of one official paper."
        ),
    },
    {
        "id": "multiple",
        "title": "One or more correct",
        "formats": ["multiple_correct"],
        "share": 0.3,
        "marks": {"correct": 4, "partial": 1, "incorrect": -2, "unattempted": 0},
        "instructions": (
            "One or more options may be correct. +4 if the set matches, "
            "+1 for each correct option when you choose only correct options but miss some, "
            "−2 if any wrong option is chosen, 0 if you leave it. "
            "Partial credit follows this practice rule, not a claim about one historical paper."
        ),
    },
    {
        "id": "numerical",
        "title": "Numerical",
        "formats": ["numeric", "integer"],
        "share": 0.3,
        "marks": {"correct": 4, "incorrect": 0, "unattempted": 0},
        "instructions": (
            "Numerical or integer answer. +4 if it matches, 0 otherwise. "
            "This practice section does not apply the negative mark some Advanced papers used."
        ),
    },
]

BLUEPRINTS: Dict[str, Dict[str, Any]] = {
    "jee_main_2025": {
        "id": "jee_main_2025",
        "version": 1,
        "exam": "JEE_MAIN",
        "title": "JEE Main 2025 Paper 1",
        "verified": True,
        "full_duration_minutes": 180,
        "full_per_subject": 25,
        "source": "NTA JEE (Main) 2025 Information Bulletin, Paper 1 (B.E./B.Tech).",
        "source_url": "https://cdnbbsr.s3waas.gov.in/s3f8e59f4b2fe7c5705bf878bbd494ccdf/uploads/2024/10/2024102824.pdf",
        "sections": [_MAIN_SECTION_A, _MAIN_SECTION_B],
        "note": "Official bulletin pattern. A one-subject paper is a sectional mock, not the full examination.",
    },
    # JEE Advanced 2024 Paper 1, each subject. Counts and marks are the scheme printed
    # on the paper: Section 1 single correct, Section 2 one-or-more with partial marks,
    # Section 3 non-negative integer, Section 4 matching list.
    # https://www.jeeadv.ac.in/documents/p1_solutions.pdf
    # Organising institute: IIT Madras, https://jeeadv.ac.in/reports/2024.pdf
    "jee_advanced_2024_paper1": {
        "id": "jee_advanced_2024_paper1",
        "version": 1,
        "exam": "JEE_ADVANCED",
        "title": "JEE Advanced 2024 Paper 1",
        "verified": True,
        "paper": 1,
        "full_duration_minutes": 180,
        "full_per_subject": 17,
        "max_marks_per_subject": 60,
        "source": "JEE Advanced 2024 Paper 1 instructions, published by the IITs.",
        "source_url": "https://www.jeeadv.ac.in/documents/p1_solutions.pdf",
        "sections": [
            {
                "id": "single",
                "title": "Section 1 · Single correct",
                "formats": ["single_correct", "comprehension"],
                "full_count": 4,
                "marks": {"correct": 3, "incorrect": -1, "unattempted": 0},
                "instructions": (
                    "Section 1. Only one option is correct. "
                    "+3 if only the correct option is chosen, −1 in every other case, 0 if unanswered."
                ),
            },
            {
                "id": "multiple",
                "title": "Section 2 · One or more correct",
                "formats": ["multiple_correct", "multi_correct"],
                "full_count": 3,
                "marks": {"correct": 4, "partial": 1, "incorrect": -2, "unattempted": 0},
                "instructions": (
                    "Section 2. One or more options may be correct. "
                    "+4 if every correct option is chosen and no wrong option is chosen. "
                    "+3 if all four options are correct and only three are chosen. "
                    "+2 if three or more options are correct and only two correct options are chosen. "
                    "+1 if two or more options are correct and only one correct option is chosen. "
                    "−2 in every other case, 0 if unanswered."
                ),
            },
            {
                "id": "numerical",
                "title": "Section 3 · Non-negative integer",
                "formats": ["integer", "numeric", "numerical"],
                "full_count": 6,
                "marks": {"correct": 4, "incorrect": 0, "unattempted": 0, "numeric_mode": "integer"},
                "instructions": (
                    "Section 3. Enter a non-negative integer. "
                    "+4 if the integer matches the key, 0 otherwise. There is no negative mark."
                ),
            },
            {
                "id": "matching",
                "title": "Section 4 · Matching list",
                "formats": ["matching", "match_list", "matrix_match"],
                "full_count": 4,
                "marks": {"correct": 3, "incorrect": -1, "unattempted": 0},
                "instructions": (
                    "Section 4. Matching list. Choose every correct option. "
                    "+3 if the set matches the key, −1 in every other case, 0 if unanswered. "
                    "There is no partial mark in this section."
                ),
            },
        ],
        "note": (
            "Verified 2024 Paper 1 structure. Questions may come from other years when they fit a section. "
            "This is not a copy of the 2024 paper, and a one-subject paper is a sectional mock."
        ),
    },
    "jee_advanced_practice": {
        "id": "jee_advanced_practice",
        "version": 1,
        "exam": "JEE_ADVANCED",
        "title": "JEE Advanced practice format",
        "verified": False,
        "full_duration_minutes": 180,
        "full_per_subject": 18,
        "source": "AerOpt practice format. Not an official JEE Advanced paper pattern.",
        "source_url": "",
        "sections": _ADVANCED_PRACTICE,
        "note": (
            "Question types and marks follow the practice rules above. "
            "An official paper is available in JEE PYQ Mock, where the paper's own questions are used."
        ),
    },
}

# Named on the face of the official JEE Advanced report for that year.
# Other years stay unconfirmed. Do not infer an institute from a key-file host.
ORGANIZING_IITS: Dict[int, Dict[str, str]] = {
    2014: {
        "iit": "IIT Kharagpur",
        "status": "verified",
        "source": "https://jeeadv.ac.in/reports/2014.pdf",
    },
    2017: {
        "iit": "IIT Madras",
        "status": "verified",
        "source": "https://jeeadv.ac.in/reports/2017.pdf",
    },
    2024: {
        "iit": "IIT Madras",
        "status": "verified",
        "source": "https://jeeadv.ac.in/reports/2024.pdf",
    },
}

SUBJECT_ORDER = ("Physics", "Chemistry", "Mathematics")


def blueprint_for(exam: str, blueprint_id: Optional[str] = None) -> Dict[str, Any]:
    if blueprint_id and blueprint_id in BLUEPRINTS:
        found = BLUEPRINTS[blueprint_id]
        if found["exam"] == exam or exam == "custom":
            return found
    if exam == "JEE_MAIN":
        return BLUEPRINTS["jee_main_2025"]
    return BLUEPRINTS["jee_advanced_practice"]


def pyq_blueprint(exam: str, blueprint_id: Optional[str] = None) -> Dict[str, Any]:
    """The verified blueprint a PYQ mock must follow. Practice formats are refused."""
    if exam == "JEE_MAIN":
        chosen = blueprint_id or "jee_main_2025"
    elif exam == "JEE_ADVANCED":
        chosen = blueprint_id or "jee_advanced_2024_paper1"
    else:
        raise KeyError(f"PYQ mocks are only defined for JEE Main and JEE Advanced, not {exam}")
    found = BLUEPRINTS.get(chosen)
    if not found or found.get("exam") != exam or not found.get("verified"):
        raise KeyError(f"No verified {exam} blueprint named {chosen}")
    return found


def full_question_count(blueprint: Dict[str, Any], n_subjects: int) -> int:
    return int(blueprint["full_per_subject"]) * max(0, n_subjects)


def duration_minutes(blueprint: Dict[str, Any], n_subjects: int, length: str, short_minutes: Optional[int] = None) -> int:
    """Full papers use the bulletin time, scaled by how many subjects were selected."""
    full = int(blueprint["full_duration_minutes"])
    subjects = max(1, min(3, n_subjects or 1))
    if length == "short":
        return int(short_minutes or max(30, round(full * subjects / 3 / 2)))
    if subjects >= 3:
        return full
    return max(30, round(full * subjects / 3))


def section_plan(blueprint: Dict[str, Any], n: int) -> List[Dict[str, Any]]:
    """Exact section counts for one subject. `n` equal to the full count is not scaled."""
    sections = blueprint["sections"]
    full = sum(int(s.get("full_count") or 0) for s in sections)
    if n == full and full:
        return [{**section, "count": int(section["full_count"])} for section in sections if int(section.get("full_count") or 0) > 0]
    return scale_sections(blueprint, n)


def organizing_iit(year: Optional[int]) -> Dict[str, str]:
    if year is None:
        return {"iit": "", "status": "unconfirmed", "source": ""}
    found = ORGANIZING_IITS.get(int(year))
    if found:
        return dict(found)
    return {
        "iit": "",
        "status": "unconfirmed",
        "source": "https://jeeadv.ac.in/reports.html",
    }


def scale_sections(blueprint: Dict[str, Any], n: int) -> List[Dict[str, Any]]:
    """Split `n` questions across the blueprint's sections.

    Verified bulletins use fixed full counts, scaled down in proportion.
    The practice format uses shares. A section with no remaining questions is dropped.
    """
    sections = blueprint["sections"]
    if n <= 0 or not sections:
        return []
    if sections[0].get("full_count"):
        full = sum(int(s["full_count"]) for s in sections)
        raw = [n * int(s["full_count"]) / full for s in sections]
    else:
        raw = [n * float(s.get("share") or 0) for s in sections]
    floors = [int(x) for x in raw]
    left = n - sum(floors)
    order = sorted(range(len(sections)), key=lambda i: (raw[i] - floors[i]), reverse=True)
    for i in range(left):
        floors[order[i % len(order)]] += 1
    out = []
    for section, count in zip(sections, floors):
        if count <= 0:
            continue
        out.append({**section, "count": count})
    return out


def sectional_label(subjects: List[str], blueprint: Dict[str, Any], per_subject: int) -> bool:
    """A full examination is every subject at the bulletin count. Anything else is sectional."""
    if set(subjects) != set(SUBJECT_ORDER):
        return True
    return per_subject != int(blueprint.get("full_per_subject") or 0)


def public_blueprint(blueprint: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": blueprint["id"],
        "version": blueprint["version"],
        "exam": blueprint["exam"],
        "title": blueprint["title"],
        "verified": blueprint["verified"],
        "full_duration_minutes": blueprint["full_duration_minutes"],
        "full_per_subject": blueprint["full_per_subject"],
        "source": blueprint["source"],
        "source_url": blueprint["source_url"],
        "note": blueprint["note"],
        "sections": [
            {
                "id": s["id"],
                "title": s["title"],
                "formats": list(s["formats"]),
                "marks": dict(s["marks"]),
                "instructions": s["instructions"],
                **({"full_count": s["full_count"]} if "full_count" in s else {}),
            }
            for s in blueprint["sections"]
        ],
    }
