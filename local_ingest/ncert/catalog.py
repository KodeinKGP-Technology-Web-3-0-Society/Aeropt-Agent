"""NCERT Physics (rationalised, Class XI and XII) — scope data only.

Each chapter PDF is fetched from ncert.nic.in/textbook/pdf/<code>.pdf (the index page
fails TLS from here; the PDFs do not). `chapter_id` is the existing syllabus chapter the
NCERT chapter's text joins, so NCERT passages attach to the same concepts as OpenStax;
`unit` widens concept mapping to the whole JEE unit (NCERT's "Laws of Motion" spans two
OpenStax chapters). Titles were checked against each PDF's first page (2026-10-03).

Licence: © NCERT. Fine for local development; a commercial product needs NCERT's permission.
"""

from __future__ import annotations

from dataclasses import dataclass

LICENSE = "NCERT textbook (© NCERT); local development use"
BASE_URL = "https://ncert.nic.in/textbook/pdf"


@dataclass(frozen=True)
class Chapter:
    code: str          # keph101, kech101
    number: int        # NCERT chapter number
    title: str
    chapter_id: str    # syllabus chapter (local_ingest/syllabus.py ids)
    unit: str          # JEE unit (local_ingest/jee_syllabus.py)
    subject: str = "Physics"

    @property
    def book(self) -> str:
        cls = "XI" if self.code.startswith("ke") else "XII"
        if self.subject == "Mathematics" and cls == "XI":
            return f"NCERT {self.subject} Class {cls}"
        part = "I" if self.code[4] == "1" else "II"
        return f"NCERT {self.subject} Class {cls} Part {part}"

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.code}.pdf"


PHYSICS_CHAPTERS: list[Chapter] = [
    Chapter("keph101", 1, "Units and Measurement", "units_and_measurement", "Physics and Measurement", "Physics"),
    Chapter("keph102", 2, "Motion in a Straight Line", "motion_along_a_straight_line", "Kinematics", "Physics"),
    Chapter("keph103", 3, "Motion in a Plane", "motion_in_two_and_three_dimensions", "Kinematics", "Physics"),
    Chapter("keph104", 4, "Laws of Motion", "newton_s_laws_of_motion", "Laws of Motion", "Physics"),
    Chapter("keph105", 5, "Work, Energy and Power", "work_and_kinetic_energy", "Work, Energy and Power", "Physics"),
    Chapter("keph106", 6, "Systems of Particles and Rotational Motion", "fixed_axis_rotation", "Rotational Motion", "Physics"),
    Chapter("keph107", 7, "Gravitation", "gravitation", "Gravitation", "Physics"),
    Chapter("keph201", 8, "Mechanical Properties of Solids", "static_equilibrium_and_elasticity", "Properties of Solids and Liquids", "Physics"),
    Chapter("keph202", 9, "Mechanical Properties of Fluids", "fluid_mechanics", "Properties of Solids and Liquids", "Physics"),
    Chapter("keph203", 10, "Thermal Properties of Matter", "temperature_and_heat", "Properties of Solids and Liquids", "Physics"),
    Chapter("keph204", 11, "Thermodynamics", "the_first_law_of_thermodynamics", "Thermodynamics", "Physics"),
    Chapter("keph205", 12, "Kinetic Theory", "the_kinetic_theory_of_gases", "Kinetic Theory of Gases", "Physics"),
    Chapter("keph206", 13, "Oscillations", "oscillations", "Oscillations and Waves", "Physics"),
    Chapter("keph207", 14, "Waves", "waves", "Oscillations and Waves", "Physics"),
    Chapter("leph101", 1, "Electric Charges and Fields", "electric_charges_and_fields", "Electrostatics", "Physics"),
    Chapter("leph102", 2, "Electrostatic Potential and Capacitance", "electric_potential", "Electrostatics", "Physics"),
    Chapter("leph103", 3, "Current Electricity", "current_and_resistance", "Current Electricity", "Physics"),
    Chapter("leph104", 4, "Moving Charges and Magnetism", "magnetic_forces_and_fields", "Magnetic Effects of Current and Magnetism", "Physics"),
    Chapter("leph105", 5, "Magnetism and Matter", "sources_of_magnetic_fields", "Magnetic Effects of Current and Magnetism", "Physics"),
    Chapter("leph106", 6, "Electromagnetic Induction", "electromagnetic_induction", "Electromagnetic Induction and Alternating Currents", "Physics"),
    Chapter("leph107", 7, "Alternating Current", "alternating_current_circuits", "Electromagnetic Induction and Alternating Currents", "Physics"),
    Chapter("leph108", 8, "Electromagnetic Waves", "electromagnetic_waves", "Electromagnetic Waves", "Physics"),
    Chapter("leph201", 9, "Ray Optics and Optical Instruments", "geometric_optics_and_image_formation", "Optics", "Physics"),
    Chapter("leph202", 10, "Wave Optics", "interference", "Optics", "Physics"),
    Chapter("leph203", 11, "Dual Nature of Radiation and Matter", "photons_and_matter_waves", "Dual Nature of Matter and Radiation", "Physics"),
    Chapter("leph204", 12, "Atoms", "atomic_structure", "Atoms and Nuclei", "Physics"),
    Chapter("leph205", 13, "Nuclei", "nuclear_physics", "Atoms and Nuclei", "Physics"),
    Chapter("leph206", 14, "Semiconductor Electronics: Materials, Devices and Simple Circuits", "condensed_matter_physics", "Electronic Devices", "Physics"),
]

CHEMISTRY_CHAPTERS: list[Chapter] = [
    # Class XI Part 1
    Chapter("kech101", 1, "Some Basic Concepts of Chemistry", "mole_concept_stoichiometry", "Some Basic Concepts in Chemistry", "Chemistry"),
    Chapter("kech102", 2, "Structure of Atom", "structure_of_atom", "Atomic Structure", "Chemistry"),
    Chapter("kech103", 3, "Classification of Elements and Periodicity in Properties", "periodic_table_and_periodicity", "Classification of Elements and Periodicity in Properties", "Chemistry"),
    Chapter("kech104", 4, "Chemical Bonding and Molecular Structure", "chemical_bonding_molecular_structure", "Chemical Bonding and Molecular Structure", "Chemistry"),
    Chapter("kech105", 5, "Thermodynamics", "chemical_thermodynamics_energetics", "Chemical Thermodynamics", "Chemistry"),
    Chapter("kech106", 6, "Equilibrium", "chemical_equilibrium", "Equilibrium", "Chemistry"),
    # Class XI Part 2
    Chapter("kech201", 7, "Redox Reactions", "redox_reactions", "Redox Reactions and Electrochemistry", "Chemistry"),
    Chapter("kech202", 8, "Organic Chemistry – Some Basic Principles and Techniques", "general_organic_chemistry_goc", "Some Basic Principles of Organic Chemistry", "Chemistry"),
    Chapter("kech203", 9, "Hydrocarbons", "hydrocarbons", "Hydrocarbons", "Chemistry"),
    # Class XII Part 1
    Chapter("lech101", 1, "Solutions", "solutions", "Solutions", "Chemistry"),
    Chapter("lech102", 2, "Electrochemistry", "electrochemistry", "Redox Reactions and Electrochemistry", "Chemistry"),
    Chapter("lech103", 3, "Chemical Kinetics", "chemical_kinetics", "Chemical Kinetics", "Chemistry"),
    Chapter("lech104", 4, "The d- and f-Block Elements", "d_and_f_block_elements", "d- and f-Block Elements", "Chemistry"),
    Chapter("lech105", 5, "Coordination Compounds", "coordination_compounds", "Coordination Compounds", "Chemistry"),
    # Class XII Part 2
    Chapter("lech201", 6, "Haloalkanes and Haloarenes", "alkyl_and_aryl_halides", "Organic Compounds Containing Halogens", "Chemistry"),
    Chapter("lech202", 7, "Alcohols, Phenols and Ethers", "alcohols_phenols_and_ethers", "Organic Compounds Containing Oxygen", "Chemistry"),
    Chapter("lech203", 8, "Aldehydes, Ketones and Carboxylic Acids", "aldehydes_ketones_and_carboxylic_acids", "Organic Compounds Containing Oxygen", "Chemistry"),
    Chapter("lech204", 9, "Amines", "organic_compounds_containing_nitrogen", "Organic Compounds Containing Nitrogen", "Chemistry"),
    Chapter("lech205", 10, "Biomolecules", "biomolecules_and_polymers", "Biomolecules", "Chemistry"),
]

MATHEMATICS_CHAPTERS: list[Chapter] = [
    # Class XI
    Chapter("kemh101", 1, "Sets", "sets", "Sets, Relations and Functions", "Mathematics"),
    Chapter("kemh102", 2, "Relations and Functions", "relations_and_functions_11", "Sets, Relations and Functions", "Mathematics"),
    Chapter("kemh103", 3, "Trigonometric Functions", "trigonometric_functions", "Trigonometry", "Mathematics"),
    Chapter("kemh104", 4, "Complex Numbers and Quadratic Equations", "complex_numbers_and_quadratic_equations", "Complex Numbers and Quadratic Equations", "Mathematics"),
    Chapter("kemh105", 5, "Linear Inequalities", "linear_inequalities", "Algebra", "Mathematics"),
    Chapter("kemh106", 6, "Permutations and Combinations", "permutations_and_combinations", "Permutations and Combinations", "Mathematics"),
    Chapter("kemh107", 7, "Binomial Theorem", "binomial_theorem", "Binomial Theorem and its Simple Applications", "Mathematics"),
    Chapter("kemh108", 8, "Sequences and Series", "sequences_and_series", "Sequence and Series", "Mathematics"),
    Chapter("kemh109", 9, "Straight Lines", "straight_lines", "Coordinate Geometry", "Mathematics"),
    Chapter("kemh110", 10, "Conic Sections", "conic_sections", "Coordinate Geometry", "Mathematics"),
    Chapter("kemh111", 11, "Introduction to Three Dimensional Geometry", "intro_three_dimensional_geometry", "Three Dimensional Geometry", "Mathematics"),
    Chapter("kemh112", 12, "Limits and Derivatives", "limits_and_derivatives", "Limit, Continuity and Differentiability", "Mathematics"),
    Chapter("kemh113", 13, "Statistics", "statistics", "Statistics and Probability", "Mathematics"),
    Chapter("kemh114", 14, "Probability", "probability_11", "Statistics and Probability", "Mathematics"),
    # Class XII Part 1
    Chapter("lemh101", 1, "Relations and Functions", "relations_and_functions_12", "Sets, Relations and Functions", "Mathematics"),
    Chapter("lemh102", 2, "Inverse Trigonometric Functions", "inverse_trigonometric_functions", "Trigonometry", "Mathematics"),
    Chapter("lemh103", 3, "Matrices", "matrices", "Matrices and Determinants", "Mathematics"),
    Chapter("lemh104", 4, "Determinants", "determinants", "Matrices and Determinants", "Mathematics"),
    Chapter("lemh105", 5, "Continuity and Differentiability", "continuity_and_differentiability", "Limit, Continuity and Differentiability", "Mathematics"),
    Chapter("lemh106", 6, "Application of Derivatives", "application_of_derivatives", "Application of Derivatives", "Mathematics"),
    # Class XII Part 2
    Chapter("lemh201", 7, "Integrals", "integrals", "Integral Calculus", "Mathematics"),
    Chapter("lemh202", 8, "Application of Integrals", "application_of_integrals", "Integral Calculus", "Mathematics"),
    Chapter("lemh203", 9, "Differential Equations", "differential_equations", "Differential Equations", "Mathematics"),
    Chapter("lemh204", 10, "Vector Algebra", "vector_algebra", "Vector Algebra", "Mathematics"),
    Chapter("lemh205", 11, "Three Dimensional Geometry", "three_dimensional_geometry", "Three Dimensional Geometry", "Mathematics"),
    Chapter("lemh206", 12, "Linear Programming", "linear_programming", "Algebra", "Mathematics"),
    Chapter("lemh207", 13, "Probability", "probability_12", "Statistics and Probability", "Mathematics"),
]


CHAPTERS: list[Chapter] = PHYSICS_CHAPTERS + CHEMISTRY_CHAPTERS + MATHEMATICS_CHAPTERS


def select(codes: set[str] | None = None, subject: str | None = None) -> list[Chapter]:
    chs = CHAPTERS
    if subject:
        s = subject.strip().lower()
        chs = [c for c in chs if c.subject.lower() == s]
    return [c for c in chs if not codes or c.code in codes]

