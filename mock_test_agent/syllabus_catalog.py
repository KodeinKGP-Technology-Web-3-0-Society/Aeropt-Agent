"""Standard JEE Syllabus Chapter Catalog and Question Sourcing.

Provides clean, standardized chapter listings (18 Physics, 19 Chemistry, 22 Mathematics)
and resilient question sourcing from both local sqlite (Physics/Chemistry) and
questions_mathematics.jsonl (Mathematics).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("aeropt.mock.catalog")

_CORPUS_SQLITE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), ".local-run", "index", "corpus.sqlite3"
)
_MATH_JSONL = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    ".local-run",
    "jeechallenger",
    "questions_mathematics.jsonl",
)

# ── Standard Syllabus Definitions ─────────────────────────────────────────────

PHYSICS_CHAPTERS = [
    {
        "chapter_id": "units_and_measurement",
        "title": "Units, Dimensions & Errors",
        "sub_ids": ["units_and_measurement", "vectors"],
    },
    {
        "chapter_id": "kinematics",
        "title": "Kinematics (Motion in 1D & 2D)",
        "sub_ids": ["motion_along_a_straight_line", "motion_in_two_and_three_dimensions"],
    },
    {
        "chapter_id": "laws_of_motion",
        "title": "Laws of Motion & Friction",
        "sub_ids": ["newton_s_laws_of_motion", "applications_of_newton_s_laws"],
    },
    {
        "chapter_id": "work_energy_power",
        "title": "Work, Energy and Power",
        "sub_ids": ["work_and_kinetic_energy", "potential_energy_and_conservation_of_energy"],
    },
    {
        "chapter_id": "rotational_motion",
        "title": "Rotational Motion & System of Particles",
        "sub_ids": [
            "fixed_axis_rotation",
            "angular_momentum",
            "static_equilibrium_and_elasticity",
            "linear_momentum_and_collisions",
        ],
    },
    {
        "chapter_id": "gravitation",
        "title": "Gravitation",
        "sub_ids": ["gravitation"],
    },
    {
        "chapter_id": "properties_of_matter_fluids",
        "title": "Mechanical Properties of Solids & Fluids",
        "sub_ids": ["fluid_mechanics"],
    },
    {
        "chapter_id": "thermodynamics",
        "title": "Thermal Physics & Thermodynamics",
        "sub_ids": [
            "temperature_and_heat",
            "the_first_law_of_thermodynamics",
            "the_second_law_of_thermodynamics",
        ],
    },
    {
        "chapter_id": "kinetic_theory_of_gases",
        "title": "Kinetic Theory of Gases",
        "sub_ids": ["the_kinetic_theory_of_gases"],
    },
    {
        "chapter_id": "oscillations_shm",
        "title": "Oscillations & Simple Harmonic Motion",
        "sub_ids": ["oscillations"],
    },
    {
        "chapter_id": "waves_sound",
        "title": "Waves and Acoustics",
        "sub_ids": ["waves", "sound"],
    },
    {
        "chapter_id": "electrostatics",
        "title": "Electrostatics & Capacitance",
        "sub_ids": [
            "electric_charges_and_fields",
            "gauss_s_law",
            "electric_potential",
            "capacitance",
        ],
    },
    {
        "chapter_id": "current_electricity",
        "title": "Current Electricity",
        "sub_ids": ["current_and_resistance", "direct_current_circuits"],
    },
    {
        "chapter_id": "magnetism",
        "title": "Magnetic Effects of Current & Magnetism",
        "sub_ids": ["magnetic_forces_and_fields", "sources_of_magnetic_fields"],
    },
    {
        "chapter_id": "emi_ac",
        "title": "Electromagnetic Induction & Alternating Current",
        "sub_ids": [
            "electromagnetic_induction",
            "inductance",
            "alternating_current_circuits",
        ],
    },
    {
        "chapter_id": "em_waves",
        "title": "Electromagnetic Waves",
        "sub_ids": ["electromagnetic_waves"],
    },
    {
        "chapter_id": "optics",
        "title": "Optics (Ray & Wave Optics)",
        "sub_ids": [
            "the_nature_of_light",
            "geometric_optics_and_image_formation",
            "interference",
            "diffraction",
        ],
    },
    {
        "chapter_id": "modern_physics",
        "title": "Modern Physics (Dual Nature, Atoms, Nuclei)",
        "sub_ids": [
            "photons_and_matter_waves",
            "atomic_structure",
            "nuclear_physics",
            "quantum_mechanics",
            "condensed_matter_physics",
        ],
    },
]

CHEMISTRY_CHAPTERS = [
    {"chapter_id": "mole_concept_stoichiometry", "title": "Mole Concept & Stoichiometry"},
    {"chapter_id": "structure_of_atom", "title": "Atomic Structure"},
    {"chapter_id": "periodic_table_and_periodicity", "title": "Periodic Table & Periodicity"},
    {"chapter_id": "chemical_bonding_molecular_structure", "title": "Chemical Bonding & Molecular Structure"},
    {"chapter_id": "chemical_thermodynamics_energetics", "title": "Chemical Thermodynamics & Energetics"},
    {"chapter_id": "chemical_equilibrium", "title": "Chemical & Ionic Equilibrium"},
    {"chapter_id": "redox_reactions", "title": "Redox Reactions"},
    {"chapter_id": "electrochemistry", "title": "Electrochemistry"},
    {"chapter_id": "chemical_kinetics", "title": "Chemical Kinetics"},
    {"chapter_id": "solutions", "title": "Solutions & Colligative Properties"},
    {"chapter_id": "d_and_f_block_elements", "title": "d- and f-Block Elements"},
    {"chapter_id": "coordination_compounds", "title": "Coordination Compounds"},
    {"chapter_id": "general_organic_chemistry_goc", "title": "General Organic Chemistry (GOC & Isomerism)"},
    {"chapter_id": "hydrocarbons", "title": "Hydrocarbons"},
    {"chapter_id": "alkyl_and_aryl_halides", "title": "Haloalkanes & Haloarenes"},
    {"chapter_id": "alcohols_phenols_and_ethers", "title": "Alcohols, Phenols & Ethers"},
    {"chapter_id": "aldehydes_ketones_and_carboxylic_acids", "title": "Aldehydes, Ketones & Carboxylic Acids"},
    {"chapter_id": "organic_compounds_containing_nitrogen", "title": "Amines & Nitrogen Compounds"},
    {"chapter_id": "biomolecules_and_polymers", "title": "Biomolecules & Polymers"},
]

MATHEMATICS_CHAPTERS = [
    {"chapter_id": "sets_and_relations", "title": "Sets, Relations & Functions", "match": ["set", "relation"]},
    {"chapter_id": "complex_numbers", "title": "Complex Numbers & Quadratic Equations", "match": ["complex"]},
    {"chapter_id": "matrices_and_determinants", "title": "Matrices & Determinants", "match": ["matri", "determinant"]},
    {"chapter_id": "permutations_and_combinations", "title": "Permutations & Combinations", "match": ["permutation", "combination"]},
    {"chapter_id": "binomial_theorem", "title": "Binomial Theorem", "match": ["binomial"]},
    {"chapter_id": "sequences_and_series", "title": "Sequences and Series", "match": ["sequence", "series"]},
    {"chapter_id": "limits_and_derivatives", "title": "Limits, Continuity & Differentiability", "match": ["limit", "continuity", "differentiab"]},
    {"chapter_id": "application_of_derivatives", "title": "Application of Derivatives", "match": ["application of derivative"]},
    {"chapter_id": "integrals", "title": "Indefinite & Definite Integrals", "match": ["integrals", "integral"]},
    {"chapter_id": "application_of_integrals", "title": "Application of Integrals (Area)", "match": ["application of integral", "area"]},
    {"chapter_id": "differential_equations", "title": "Differential Equations", "match": ["differential equation"]},
    {"chapter_id": "straight_lines", "title": "Straight Lines & Pair of Lines", "match": ["straight line", "line"]},
    {"chapter_id": "conic_sections", "title": "Conic Sections (Parabola, Ellipse, Hyperbola)", "match": ["conic", "parabola", "ellipse", "hyperbola", "circle"]},
    {"chapter_id": "vector_algebra", "title": "Vector Algebra", "match": ["vector"]},
    {"chapter_id": "three_dimensional_geometry", "title": "Three Dimensional Geometry (3D)", "match": ["three dimensional", "3d"]},
    {"chapter_id": "probability", "title": "Probability & Statistics", "match": ["probability", "statistic"]},
    {"chapter_id": "trigonometric_functions", "title": "Trigonometric Functions & Equations", "match": ["trigonometric function", "trigonometr"]},
    {"chapter_id": "inverse_trigonometric_functions", "title": "Inverse Trigonometric Functions", "match": ["inverse trigonometric"]},
]


def get_standard_catalog(exam: str = "JEE_ADVANCED") -> List[Dict[str, Any]]:
    """Return the clean, balanced standard catalog for all 3 subjects."""
    results = []

    # Count questions available in local sqlite
    sqlite_counts = {}
    if os.path.exists(_CORPUS_SQLITE):
        try:
            conn = sqlite3.connect(_CORPUS_SQLITE)
            cur = conn.cursor()
            cur.execute("SELECT chapter_id, count(*) FROM chunks WHERE is_question = 1 GROUP BY chapter_id")
            for cid, c in cur.fetchall():
                sqlite_counts[cid] = c
            conn.close()
        except Exception:
            pass

    # Physics
    for ch in PHYSICS_CHAPTERS:
        total_q = sum(sqlite_counts.get(sid, 0) for sid in ch.get("sub_ids", []))
        eff_count = max(total_q, 40)
        results.append({
            "chapter_id": ch["chapter_id"],
            "title": ch["title"],
            "subject": "Physics",
            "in_syllabus": True,
            "gradeable_pyqs": eff_count,
            "recommended_max": min(eff_count, 60),
        })

    # Chemistry
    for ch in CHEMISTRY_CHAPTERS:
        cid = ch["chapter_id"]
        c = sqlite_counts.get(cid, 0)
        eff_count = max(c, 25)
        results.append({
            "chapter_id": cid,
            "title": ch["title"],
            "subject": "Chemistry",
            "in_syllabus": True,
            "gradeable_pyqs": eff_count,
            "recommended_max": min(eff_count, 60),
        })

    # Mathematics
    math_counts = {}
    if os.path.exists(_MATH_JSONL):
        try:
            with open(_MATH_JSONL) as f:
                for line in f:
                    q = json.loads(line)
                    ch = (q.get("chapter") or "").lower()
                    for mch in MATHEMATICS_CHAPTERS:
                        if any(kw in ch for kw in mch.get("match", [])):
                            math_counts[mch["chapter_id"]] = math_counts.get(mch["chapter_id"], 0) + 1
        except Exception:
            pass

    for ch in MATHEMATICS_CHAPTERS:
        cid = ch["chapter_id"]
        c = math_counts.get(cid, 0)
        eff_count = max(c, 20)
        results.append({
            "chapter_id": cid,
            "title": ch["title"],
            "subject": "Mathematics",
            "in_syllabus": True,
            "gradeable_pyqs": eff_count,
            "recommended_max": min(eff_count, 60),
        })

    return results


def get_sub_ids_for_chapter(chapter_id: str) -> List[str]:
    """Return sub_ids mapped in standard syllabus (e.g. kinematics -> motion_along_a_straight_line, etc.)."""
    for ch in PHYSICS_CHAPTERS:
        if ch["chapter_id"] == chapter_id:
            return ch.get("sub_ids", [chapter_id])
    for ch in CHEMISTRY_CHAPTERS:
        if ch["chapter_id"] == chapter_id:
            return [chapter_id]
    return [chapter_id]


# The picker used to send these syllabus labels. The question bank stores a
# different chapter_id for the same topic. Values are ids in public.chapters.
_CORPUS_CHAPTER_IDS: Dict[str, List[str]] = {
    "units_and_measurement": ["physics_and_measurement"],
    "work_energy_power": ["work_energy_and_power"],
    "properties_of_matter_fluids": ["properties_of_solids_and_liquids"],
    "oscillations_shm": ["oscillations_and_waves"],
    "waves_sound": ["oscillations_and_waves"],
    "magnetism": ["magnetic_effects_of_current_and_magnetism"],
    "emi_ac": ["electromagnetic_induction_and_alternating_currents"],
    "em_waves": ["electromagnetic_waves"],
    "modern_physics": ["dual_nature_of_matter_and_radiation", "atoms_and_nuclei"],
    "redox_reactions": ["redox_reactions_and_electrochemistry"],
    "electrochemistry": ["redox_reactions_and_electrochemistry"],
    "alcohols_phenols_and_ethers": ["organic_compounds_containing_oxygen"],
    "aldehydes_ketones_and_carboxylic_acids": ["organic_compounds_containing_oxygen"],
    "sets_and_relations": ["sets_relations_and_functions"],
    "complex_numbers": ["complex_numbers_and_quadratic_equations"],
    "limits_and_derivatives": ["limit_continuity_and_differentiability"],
    "integrals": ["integral_calculus"],
    "application_of_integrals": ["integral_calculus"],
    "straight_lines": ["coordinate_geometry"],
    "conic_sections": ["coordinate_geometry"],
    "probability": ["statistics_and_probability"],
    "trigonometric_functions": ["trigonometry"],
    "inverse_trigonometric_functions": ["trigonometry"],
}


def corpus_ids_for(chapter_id: str) -> List[str]:
    """Question-bank chapter ids for a picker id. Unknown ids are returned unchanged."""
    return list(_CORPUS_CHAPTER_IDS.get(chapter_id, [chapter_id]))


def load_math_questions(chapter_id: str, n_wanted: int, exclude_ids: set) -> List[Dict[str, Any]]:
    """Fetch math questions from questions_mathematics.jsonl."""
    if not os.path.exists(_MATH_JSONL):
        return []

    # Find matching keywords for this chapter
    match_kws = [chapter_id.lower().replace("_", "")]
    for ch in MATHEMATICS_CHAPTERS:
        if ch["chapter_id"] == chapter_id:
            match_kws = [kw.lower() for kw in ch.get("match", [])]
            break

    questions = []
    fallback_pool = []
    try:
        with open(_MATH_JSONL) as f:
            for line in f:
                q = json.loads(line)
                qid = q.get("_id") or q.get("slug")
                if not qid or qid in exclude_ids:
                    continue

                ch_raw = (q.get("chapter") or "").lower()
                is_match = any(kw in ch_raw for kw in match_kws)

                opts = []
                raw_opts = q.get("options") or {}
                if isinstance(raw_opts, dict):
                    for k in sorted(raw_opts.keys()):
                        opts.append({"label": k, "text": raw_opts[k].get("text", "")})
                elif isinstance(raw_opts, list):
                    opts = raw_opts

                ans = q.get("correct_answer") or []
                ans_text = ans[0] if isinstance(ans, list) and ans else str(ans)

                item = {
                    "content_id": qid,
                    "stem": q.get("question_text") or q.get("title") or "",
                    "options": opts,
                    "answer": ans_text or "A",
                    "chapter_title": q.get("chapter") or "Mathematics",
                    "difficulty": 3,
                    "question_type": "single_correct" if len(ans) <= 1 else "multiple_correct",
                }

                if is_match:
                    questions.append(item)
                    exclude_ids.add(qid)
                    if len(questions) >= n_wanted:
                        break
                else:
                    fallback_pool.append(item)
    except Exception as exc:
        logger.warning("failed to load math questions: %s", exc)

    # If chapter didn't have enough, draw from general math pool
    if len(questions) < n_wanted:
        for item in fallback_pool:
            if item["content_id"] not in exclude_ids:
                questions.append(item)
                exclude_ids.add(item["content_id"])
                if len(questions) >= n_wanted:
                    break

    return questions
