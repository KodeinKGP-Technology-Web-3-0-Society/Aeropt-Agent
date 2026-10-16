"""The JEE PCM (Physics, Chemistry, Mathematics) scope map: units, chapters and aliases.

Encodes the official JEE Main & JEE Advanced curriculum scope aligned with
standard NCERT and ALLEN Career Institute coaching taxonomies.
This scope metadata allows the tutor to validate whether a concept sits within
the student's JEE examination syllabus across Physics, Chemistry, and Mathematics.
"""

from __future__ import annotations

# ==============================================================================
# 1. Physics Units & Chapters (Canonical JEE + NCERT + ALLEN)
# ==============================================================================
JEE_PHYSICS_UNITS: dict[str, list[tuple[str, ...]]] = {
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

# ==============================================================================
# 2. Chemistry Units & Chapters (Physical, Inorganic, Organic - ALLEN Aligned)
# ==============================================================================
JEE_CHEMISTRY_UNITS: dict[str, list[tuple[str, ...]]] = {
    "Physical Chemistry": [
        ("Mole Concept & Stoichiometry", "some basic concepts of chemistry", "mole concept", "stoichiometry",
         "concentration terms", "molarity", "molality", "limiting reagent", "equivalent weight"),
        ("Atomic Structure", "structure of atom", "bohr's model", "quantum numbers", "photoelectric effect in chemistry",
         "electronic configuration", "de broglie wavelength"),
        ("States of Matter", "gaseous state", "liquid state", "ideal gas equation", "real gases",
         "van der waals equation", "kinetic theory of gases chemistry"),
        ("Chemical Thermodynamics & Energetics", "thermodynamics chemistry", "thermochemistry", "enthalpy",
         "hess's law", "entropy chemistry", "gibbs free energy", "spontaneity"),
        ("Chemical Equilibrium", "law of mass action", "le chatelier's principle", "equilibrium constant", "kp and kc"),
        ("Ionic Equilibrium", "acids and bases", "ph calculation", "buffer solutions", "solubility product",
         "common ion effect", "salt hydrolysis"),
        ("Redox Reactions & Electrochemistry", "redox reactions", "electrochemistry", "galvanic cell", "nernst equation",
         "faraday's laws of electrolysis", "kohlrausch's law", "electrode potential"),
        ("Chemical Kinetics", "chemical kinetics", "rate of reaction", "order and molecularity", "arrhenius equation",
         "activation energy", "first order kinetics", "integrated rate laws"),
        ("Solid State", "solid state", "crystal lattices", "unit cells", "packing efficiency", "bragg's law", "defects in solids"),
        ("Solutions", "solutions", "colligative properties", "raoult's law", "van 't hoff factor",
         "elevation in boiling point", "depression in freezing point", "osmotic pressure"),
        ("Surface Chemistry", "surface chemistry", "adsorption", "colloids", "catalysis", "emulsions"),
    ],
    "Inorganic Chemistry": [
        ("Periodic Table and Periodicity", "classification of elements", "periodic properties", "ionization enthalpy",
         "electronegativity", "electron gain enthalpy", "atomic and ionic radii"),
        ("Chemical Bonding & Molecular Structure", "chemical bonding", "vsepr theory", "hybridization chemistry",
         "molecular orbital theory", "mot", "hydrogen bonding", "dipole moment"),
        ("Hydrogen and s-Block Elements", "hydrogen", "s-block elements", "alkali metals", "alkaline earth metals"),
        ("p-Block Elements", "p-block elements", "boron family", "carbon family", "nitrogen family",
         "oxygen family", "halogens", "noble gases", "interhalogen compounds"),
        ("d and f Block Elements", "transition elements", "d-block", "f-block", "lanthanoids", "actinoids",
         "potassium dichromate", "potassium permanganate"),
        ("Coordination Compounds", "coordination chemistry", "coordination compounds", "werner's theory", "cft",
         "crystal field theory", "isomerism in coordination compounds", "ligands"),
        ("Metallurgy and Extraction", "metallurgy", "general principles and processes of isolation of elements",
         "ore extraction", "froth floatation", "refining of metals", "ellíngham diagram"),
        ("Qualitative Inorganic Analysis", "salt analysis", "qualitative analysis", "cation detection", "anion detection"),
    ],
    "Organic Chemistry": [
        ("General Organic Chemistry (GOC)", "goc", "basic principles of organic chemistry", "inductive effect",
         "resonance effect", "hyperconjugation", "electrophilic addition", "carbocation", "carbanion", "free radical"),
        ("Isomerism & Stereochemistry", "isomerism", "stereochemistry", "optical isomerism", "geometrical isomerism",
         "conformational isomerism", "chirality", "r/s configuration", "e/z isomerism"),
        ("Hydrocarbons", "hydrocarbons", "alkanes", "alkenes", "alkynes", "aromatic hydrocarbons", "benzene",
         "friedel-crafts reaction", "markovnikov addition", "ozonolysis"),
        ("Alkyl and Aryl Halides", "haloalkanes and haloarenes", "sn1 and sn2", "nucleophilic substitution",
         "elimination reactions e1 e2", "grignard reagent", "aryl halides"),
        ("Alcohols, Phenols and Ethers", "alcohols phenols and ethers", "alcohols", "phenols", "ethers",
         "williamson synthesis", "reimer-tiemann reaction", "kolbe reaction"),
        ("Aldehydes, Ketones and Carboxylic Acids", "carbonyl compounds", "aldehydes", "ketones", "carboxylic acids",
         "aldol condensation", "cannizzaro reaction", "clemmensen reduction", "esterification"),
        ("Organic Compounds Containing Nitrogen", "amines", "diazonium salts", "gabriel phthalimide synthesis",
         "carbylamine reaction", "hoffmann bromamide degradation"),
        ("Biomolecules and Polymers", "biomolecules", "carbohydrates", "amino acids and proteins", "nucleic acids",
         "polymers", "addition and condensation polymers"),
        ("Practical Organic Chemistry (POC)", "practical organic chemistry", "functional group tests",
         "detection of nitrogen sulfur halogens in organic compounds", "chemistry in everyday life"),
    ],
}

# ==============================================================================
# 3. Mathematics Units & Chapters (Algebra, Calculus, Geometry - ALLEN Aligned)
# ==============================================================================
JEE_MATHEMATICS_UNITS: dict[str, list[tuple[str, ...]]] = {
    "Algebra": [
        ("Quadratic Equations", "quadratic equations and expressions", "quadratic equation", "theory of equations",
         "nature of roots", "location of roots", "maximum and minimum of quadratic"),
        ("Complex Numbers", "complex numbers", "algebra of complex numbers", "modulus and argument", "de moivre's theorem",
         "cube roots of unity", "geometry of complex numbers"),
        ("Sequences and Series", "sequences and series", "arithmetic progression", "ap", "geometric progression",
         "gp", "harmonic progression", "hp", "agp", "special series"),
        ("Binomial Theorem", "binomial theorem", "properties of binomial coefficients", "multinomial theorem"),
        ("Permutations and Combinations", "permutations and combinations", "p and c", "combinatorics",
         "fundamental principle of counting", "circular permutation"),
        ("Matrices and Determinants", "matrices and determinants", "matrices", "determinants", "cramer's rule",
         "inverse of a matrix", "system of linear equations"),
        ("Probability", "probability", "conditional probability", "bayes theorem", "probability distribution",
         "binomial distribution", "independent events"),
        ("Sets, Relations and Functions", "sets relations and functions", "relations and functions", "types of functions",
         "composite functions", "inverse functions", "domain and range"),
    ],
    "Trigonometry": [
        ("Trigonometric Ratios and Identities", "trigonometric ratios and identities", "trigonometry", "compound angles",
         "multiple angles", "transformation formulas"),
        ("Trigonometric Equations", "trigonometric equations", "general solutions of trigonometric equations"),
        ("Inverse Trigonometric Functions", "inverse trigonometric functions", "itf", "properties of inverse trigonometric functions"),
        ("Properties of Triangles", "properties of triangles", "solution of triangles", "sot", "sine rule", "cosine rule",
         "inradius and circumradius"),
    ],
    "Coordinate Geometry": [
        ("Straight Lines", "straight lines and pair of straight lines", "straight lines", "slope of a line",
         "forms of line equations", "family of straight lines", "distance between parallel lines"),
        ("Circles", "circle", "circles", "equation of circle", "tangents and normals to circles", "system of circles",
         "radical axis"),
        ("Parabola", "parabola", "conic sections parabola", "tangents to parabola", "focal chord", "parametric coordinates parabola"),
        ("Ellipse", "ellipse", "conic sections ellipse", "eccentricity of ellipse", "tangents to ellipse"),
        ("Hyperbola", "hyperbola", "conic sections hyperbola", "rectangular hyperbola", "asymptotes of hyperbola"),
    ],
    "Differential Calculus": [
        ("Limits, Continuity and Differentiability", "limits continuity and differentiability", "limits", "continuity",
         "differentiability", "l'hopital's rule", "indeterminate forms"),
        ("Application of Derivatives", "application of derivatives", "aod", "rate of change", "tangents and normals calculus",
         "increasing and decreasing functions", "maxima and minima", "rolle's theorem", "lagrange mean value theorem", "lmvt"),
    ],
    "Integral Calculus": [
        ("Indefinite Integration", "indefinite integration", "indefinite integrals", "integration by substitution",
         "integration by parts", "partial fractions integration"),
        ("Definite Integration", "definite integration", "definite integrals", "properties of definite integrals",
         "leibnitz rule", "fundamental theorem of calculus"),
        ("Area Under Curves", "area under curves", "area of bounded regions", "application of integrals"),
        ("Differential Equations", "differential equations", "order and degree", "variable separable",
         "homogeneous differential equations", "linear differential equations", "bernoulli's equation"),
    ],
    "Vectors and 3D Geometry": [
        ("Vector Algebra", "vectors", "vector algebra", "dot product", "cross product", "scalar triple product",
         "vector triple product", "coplanarity of vectors"),
        ("Three Dimensional Geometry", "3d geometry", "three dimensional geometry", "direction cosines",
         "equation of line in space", "equation of plane", "shortest distance between skew lines"),
    ],
}

# ==============================================================================
# Mapping by Subject
# ==============================================================================
JEE_SUBJECT_UNITS: dict[str, dict[str, list[tuple[str, ...]]]] = {
    "Physics": JEE_PHYSICS_UNITS,
    "Chemistry": JEE_CHEMISTRY_UNITS,
    "Mathematics": JEE_MATHEMATICS_UNITS,
}

# Consolidated units across all subjects for unified scope gating
JEE_UNITS: dict[str, list[tuple[str, ...]]] = {}
for _units in JEE_SUBJECT_UNITS.values():
    JEE_UNITS.update(_units)

# Out-of-scope hints across PCM
OUT_OF_SCOPE_HINTS = (
    "relativity",
    "particle physics and cosmology",
    "cosmology",
    "nuclear physics and its applications",
    "quantum field theory",
    "crystal field splitting calculation for tetrahedral non-standard ligands",
    "multivariable calculus",
    "tensor calculus",
)


def canonical_chapters(subject: str | None = None) -> list[dict[str, object]]:
    """Return all canonical chapters. If subject is provided ('Physics', 'Chemistry', 'Mathematics'),
    filter down to that subject."""
    rows: list[dict[str, object]] = []
    subjects_to_scan = [subject] if (subject and subject in JEE_SUBJECT_UNITS) else list(JEE_SUBJECT_UNITS.keys())

    for subj in subjects_to_scan:
        units_map = JEE_SUBJECT_UNITS[subj]
        for unit, chapters in units_map.items():
            for entry in chapters:
                canonical, *aliases = entry
                rows.append({
                    "subject": subj,
                    "unit": unit,
                    "chapter": canonical,
                    "aliases": list(aliases),
                })
    return rows


def alias_index(subject: str | None = None) -> dict[str, tuple[str, str]]:
    """Normalised alias -> (canonical chapter, unit).
    
    Preserves exact backward compatibility with existing retrieval and gating callers.
    """
    import re

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

    index: dict[str, tuple[str, str]] = {}
    for row in canonical_chapters(subject=subject):
        chapter, unit = str(row["chapter"]), str(row["unit"])
        for name in [chapter, *row["aliases"]]:  # type: ignore[list-item]
            index[norm(str(name))] = (chapter, unit)
    return index


def alias_index_with_subject() -> dict[str, tuple[str, str, str]]:
    """Normalised alias -> (canonical chapter, unit, subject)."""
    import re

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

    index: dict[str, tuple[str, str, str]] = {}
    for row in canonical_chapters():
        chapter, unit, subj = str(row["chapter"]), str(row["unit"]), str(row["subject"])
        for name in [chapter, *row["aliases"]]:  # type: ignore[list-item]
            index[norm(str(name))] = (chapter, unit, subj)
    return index
