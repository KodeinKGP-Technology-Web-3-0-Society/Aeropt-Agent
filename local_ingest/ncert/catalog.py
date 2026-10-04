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
    code: str          # keph101
    number: int        # NCERT chapter number
    title: str
    chapter_id: str    # syllabus chapter (local_ingest/syllabus.py ids)
    unit: str          # JEE unit (local_ingest/jee_syllabus.py)

    @property
    def book(self) -> str:
        cls = "XI" if self.code.startswith("keph") else "XII"
        part = "I" if self.code[4] == "1" else "II"
        return f"NCERT Physics Class {cls} Part {part}"

    @property
    def url(self) -> str:
        return f"{BASE_URL}/{self.code}.pdf"


CHAPTERS: list[Chapter] = [
    Chapter("keph101", 1, "Units and Measurement", "units_and_measurement", "Physics and Measurement"),
    Chapter("keph102", 2, "Motion in a Straight Line", "motion_along_a_straight_line", "Kinematics"),
    Chapter("keph103", 3, "Motion in a Plane", "motion_in_two_and_three_dimensions", "Kinematics"),
    Chapter("keph104", 4, "Laws of Motion", "newton_s_laws_of_motion", "Laws of Motion"),
    Chapter("keph105", 5, "Work, Energy and Power", "work_and_kinetic_energy", "Work, Energy and Power"),
    Chapter("keph106", 6, "Systems of Particles and Rotational Motion", "fixed_axis_rotation", "Rotational Motion"),
    Chapter("keph107", 7, "Gravitation", "gravitation", "Gravitation"),
    Chapter("keph201", 8, "Mechanical Properties of Solids", "static_equilibrium_and_elasticity", "Properties of Solids and Liquids"),
    Chapter("keph202", 9, "Mechanical Properties of Fluids", "fluid_mechanics", "Properties of Solids and Liquids"),
    Chapter("keph203", 10, "Thermal Properties of Matter", "temperature_and_heat", "Properties of Solids and Liquids"),
    Chapter("keph204", 11, "Thermodynamics", "the_first_law_of_thermodynamics", "Thermodynamics"),
    Chapter("keph205", 12, "Kinetic Theory", "the_kinetic_theory_of_gases", "Kinetic Theory of Gases"),
    Chapter("keph206", 13, "Oscillations", "oscillations", "Oscillations and Waves"),
    Chapter("keph207", 14, "Waves", "waves", "Oscillations and Waves"),
    Chapter("leph101", 1, "Electric Charges and Fields", "electric_charges_and_fields", "Electrostatics"),
    Chapter("leph102", 2, "Electrostatic Potential and Capacitance", "electric_potential", "Electrostatics"),
    Chapter("leph103", 3, "Current Electricity", "current_and_resistance", "Current Electricity"),
    Chapter("leph104", 4, "Moving Charges and Magnetism", "magnetic_forces_and_fields", "Magnetic Effects of Current and Magnetism"),
    Chapter("leph105", 5, "Magnetism and Matter", "sources_of_magnetic_fields", "Magnetic Effects of Current and Magnetism"),
    Chapter("leph106", 6, "Electromagnetic Induction", "electromagnetic_induction", "Electromagnetic Induction and Alternating Currents"),
    Chapter("leph107", 7, "Alternating Current", "alternating_current_circuits", "Electromagnetic Induction and Alternating Currents"),
    Chapter("leph108", 8, "Electromagnetic Waves", "electromagnetic_waves", "Electromagnetic Waves"),
    Chapter("leph201", 9, "Ray Optics and Optical Instruments", "geometric_optics_and_image_formation", "Optics"),
    Chapter("leph202", 10, "Wave Optics", "interference", "Optics"),
    Chapter("leph203", 11, "Dual Nature of Radiation and Matter", "photons_and_matter_waves", "Dual Nature of Matter and Radiation"),
    Chapter("leph204", 12, "Atoms", "atomic_structure", "Atoms and Nuclei"),
    Chapter("leph205", 13, "Nuclei", "nuclear_physics", "Atoms and Nuclei"),
    Chapter("leph206", 14, "Semiconductor Electronics: Materials, Devices and Simple Circuits", "condensed_matter_physics", "Electronic Devices"),
]


def select(codes: set[str] | None = None) -> list[Chapter]:
    return [c for c in CHAPTERS if not codes or c.code in codes]
