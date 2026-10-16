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
    Chapter("kech101", 1, "Some Basic Concepts of Chemistry", "mole_concept_stoichiometry", "Physical Chemistry", "Chemistry"),
    Chapter("kech102", 2, "Structure of Atom", "structure_of_atom", "Physical Chemistry", "Chemistry"),
    Chapter("kech103", 3, "Classification of Elements and Periodicity in Properties", "periodic_table_and_periodicity", "Inorganic Chemistry", "Chemistry"),
    Chapter("kech104", 4, "Chemical Bonding and Molecular Structure", "chemical_bonding_molecular_structure", "Inorganic Chemistry", "Chemistry"),
    Chapter("kech105", 5, "Thermodynamics", "chemical_thermodynamics_energetics", "Physical Chemistry", "Chemistry"),
    Chapter("kech106", 6, "Equilibrium", "chemical_equilibrium", "Physical Chemistry", "Chemistry"),
    # Class XI Part 2
    Chapter("kech201", 7, "Redox Reactions", "redox_reactions", "Physical Chemistry", "Chemistry"),
    Chapter("kech202", 8, "Organic Chemistry – Some Basic Principles and Techniques", "general_organic_chemistry_goc", "Organic Chemistry", "Chemistry"),
    Chapter("kech203", 9, "Hydrocarbons", "hydrocarbons", "Organic Chemistry", "Chemistry"),
    # Class XII Part 1
    Chapter("lech101", 1, "Solutions", "solutions", "Physical Chemistry", "Chemistry"),
    Chapter("lech102", 2, "Electrochemistry", "electrochemistry", "Physical Chemistry", "Chemistry"),
    Chapter("lech103", 3, "Chemical Kinetics", "chemical_kinetics", "Physical Chemistry", "Chemistry"),
    Chapter("lech104", 4, "The d- and f-Block Elements", "d_and_f_block_elements", "Inorganic Chemistry", "Chemistry"),
    Chapter("lech105", 5, "Coordination Compounds", "coordination_compounds", "Inorganic Chemistry", "Chemistry"),
    # Class XII Part 2
    Chapter("lech201", 6, "Haloalkanes and Haloarenes", "alkyl_and_aryl_halides", "Organic Chemistry", "Chemistry"),
    Chapter("lech202", 7, "Alcohols, Phenols and Ethers", "alcohols_phenols_and_ethers", "Organic Chemistry", "Chemistry"),
    Chapter("lech203", 8, "Aldehydes, Ketones and Carboxylic Acids", "aldehydes_ketones_and_carboxylic_acids", "Organic Chemistry", "Chemistry"),
    Chapter("lech204", 9, "Amines", "organic_compounds_containing_nitrogen", "Organic Chemistry", "Chemistry"),
    Chapter("lech205", 10, "Biomolecules", "biomolecules_and_polymers", "Organic Chemistry", "Chemistry"),
]


CHAPTERS: list[Chapter] = PHYSICS_CHAPTERS + CHEMISTRY_CHAPTERS


def select(codes: set[str] | None = None, subject: str | None = None) -> list[Chapter]:
    chs = CHAPTERS
    if subject:
        s = subject.strip().lower()
        chs = [c for c in chs if c.subject.lower() == s]
    return [c for c in chs if not codes or c.code in codes]

