"""The JEE Physics scope map: units, chapters and their aliases.

This is the one piece of hand-written data in the pipeline, and it is deliberate:
it is *scope metadata*, not content. The official JEE Main/Advanced physics
syllabus is a fixed list of chapter names — a few hundred words — and encoding it
is what lets the tutor say "that is outside your syllabus" honestly instead of
guessing. Everything taught (concepts, definitions, prerequisites, theory,
examples, questions) is derived from the sources, never typed in here.

`aliases` exist because three vocabularies have to meet: OpenStax chapter titles,
the topic labels on the PYQ datasets, and what a student actually types.
"""

from __future__ import annotations

# unit -> [(canonical chapter, aliases...)]
JEE_UNITS: dict[str, list[tuple[str, ...]]] = {
    "Physics and Measurement": [
        ("Units and Measurement", "units and dimensions", "unit and dimension", "dimensional analysis",
         "error analysis", "significant figures", "measurement"),
    ],
    "Kinematics": [
        ("Motion in a Straight Line", "kinematics", "motion in 1d", "rectilinear motion", "motion along a straight line"),
        ("Motion in a Plane", "projectile motion", "motion in two and three dimensions", "relative motion",
         "circular motion", "vectors", "vector"),
    ],
    "Laws of Motion": [
        ("Laws of Motion", "newton's laws of motion", "newtons laws of motion", "friction", "free body diagram",
         "applications of newton's laws", "dynamics"),
    ],
    "Work, Energy and Power": [
        ("Work, Energy and Power", "work power & energy", "work energy and power", "work-energy theorem",
         "potential energy and conservation of energy", "collision", "collisions",
         "work and kinetic energy", "kinetic energy", "power"),
    ],
    "Rotational Motion": [
        ("Centre of Mass and Rotational Motion", "rigid body dynamics", "rotational motion", "centre of mass",
         "center of mass", "system of particles", "linear momentum and collisions", "fixed-axis rotation",
         "angular momentum", "moment of inertia", "rolling motion", "torque"),
    ],
    "Gravitation": [
        ("Gravitation", "gravity", "kepler's laws", "satellite motion", "escape velocity"),
    ],
    "Properties of Solids and Liquids": [
        ("Mechanical Properties of Solids", "elasticity", "mechanical properties of solid", "stress and strain",
         "static equilibrium and elasticity"),
        ("Mechanical Properties of Fluids", "fluid mechanics", "mechanical properties of liquid", "hydrostatics",
         "bernoulli", "viscosity", "surface tension", "fluid statics", "fluid dynamics", "fluid pressure"),
        ("Thermal Properties of Matter", "heat transfer", "calorimetry", "thermal expansion",
         "temperature and heat", "heat and thermal radiation"),
    ],
    "Thermodynamics": [
        ("Thermodynamics", "first law of thermodynamics", "second law of thermodynamics", "heat engines",
         "thermodynamic processes", "entropy", "isobaric process", "thermodynamic"),
    ],
    "Kinetic Theory of Gases": [
        ("Kinetic Theory of Gases", "the kinetic theory of gases", "behaviour of perfect gases", "ideal gas",
         "kinetic theory of gasses", "mean free path"),
    ],
    "Oscillations and Waves": [
        ("Oscillations", "simple harmonic motion", "shm", "oscillation", "damped oscillations", "resonance"),
        ("Waves", "wave on a string", "wave motion", "sound wave", "sound waves", "doppler effect",
         "waves and oscillations", "superposition", "standing waves", "beats", "wave"),
    ],
    "Electrostatics": [
        ("Electric Charges and Fields", "electrostatics", "coulomb's law", "gauss's law", "electric field",
         "electric charges and fields", "electric flux"),
        ("Electrostatic Potential and Capacitance", "capacitance", "capacitor", "electric potential",
         "potential energy of charges", "dielectric"),
    ],
    "Current Electricity": [
        ("Current Electricity", "electric current", "ohm's law", "kirchhoff", "resistivity",
         "direct-current circuits", "wheatstone bridge", "electromotive force", "current and resistance"),
    ],
    "Magnetic Effects of Current and Magnetism": [
        ("Moving Charges and Magnetism", "magnetic effect of current", "magnetic effects of current",
         "moving charges and", "biot-savart", "ampere's law", "magnetic forces and fields", "sources of magnetic fields",
         "magnetics", "magnetism", "lorentz force", "cyclotron"),
        ("Magnetism and Matter", "magnetic properties of matter", "mangetism", "bar magnet", "magnetic dipole"),
    ],
    "Electromagnetic Induction and Alternating Currents": [
        ("Electromagnetic Induction", "emi", "faraday's law", "lenz's law", "inductance", "self inductance",
         "mutual inductance", "eddy currents"),
        ("Alternating Current", "ac", "ac circuit", "alternating-current circuits", "lcr circuit", "transformer",
         "rms value", "resonance in ac"),
    ],
    "Electromagnetic Waves": [
        ("Electromagnetic Waves", "em wave", "e-m waves", "electromagnetic spectrum", "maxwell's equations",
         "displacement current"),
    ],
    "Optics": [
        ("Ray Optics and Optical Instruments", "ray optics", "geometric optics and image formation",
         "reflection", "refraction", "lens", "mirror", "prism", "total internal reflection", "optics",
         "images", "optical instruments", "the nature of light", "light"),
        ("Wave Optics", "wave optics", "interference", "diffraction", "polarization", "young's double slit",
         "huygens principle"),
    ],
    "Dual Nature of Matter and Radiation": [
        ("Dual Nature of Radiation and Matter", "dual nature of matter", "photoelectric effect", "de broglie",
         "photons and matter waves", "photon", "work function"),
    ],
    "Atoms and Nuclei": [
        ("Atoms", "atomic physics", "bohr model", "hydrogen spectrum", "rutherford", "atomic structure",
         "quantum mechanics", "the quantum harmonic oscillator"),
        ("Nuclei", "nuclear physics", "radioactivity", "nuclear fission", "nuclear fusion", "binding energy",
         "mass defect", "half life"),
    ],
    "Electronic Devices": [
        ("Semiconductor Electronics", "electronic devices", "electronic device", "semiconductors", "diode",
         "transistor", "logic gates", "digital electronics", "p-n junction", "condensed matter physics",
         "rectifier"),
    ],
    "Experimental Skills": [
        ("Experimental Skills", "vernier calliper", "screw gauge", "metre bridge", "experimental physics"),
    ],
}

# Chapters a JEE student is never examined on. Kept in the corpus (a curious
# student may ask) but flagged, so retrieval can say "beyond your syllabus"
# rather than pretending either way.
OUT_OF_SCOPE_HINTS = (
    "relativity",
    "particle physics and cosmology",
    "cosmology",
    "nuclear physics and its applications",
)


def canonical_chapters() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for unit, chapters in JEE_UNITS.items():
        for entry in chapters:
            canonical, *aliases = entry
            rows.append({"unit": unit, "chapter": canonical, "aliases": list(aliases)})
    return rows


def alias_index() -> dict[str, tuple[str, str]]:
    """normalised alias -> (canonical chapter, unit)."""
    import re

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

    index: dict[str, tuple[str, str]] = {}
    for row in canonical_chapters():
        chapter, unit = str(row["chapter"]), str(row["unit"])
        for name in [chapter, *row["aliases"]]:  # type: ignore[list-item]
            index[norm(str(name))] = (chapter, unit)
    return index
