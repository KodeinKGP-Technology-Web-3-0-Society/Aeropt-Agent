"""Reference notes for JEE syllabus topics no downloaded source teaches.

The corpus is otherwise derived from sources (local_ingest/corpus.py). These are the
exception, and the only authored teaching text in the index, for two reasons the
build report made visible:

1. Topics with no passage at all (`syllabus_topics.material = 0`): lab experiments,
   Wheatstone/metre bridge, capillarity, rectifiers... With nothing retrieved the
   tutor answers from recall, and recall is where wrong formulas and wrong sign
   rules come from.
2. A convention clash. OpenStax geometric optics uses the "real-is-positive"
   convention (1/d_o + 1/d_i = 1/f for mirrors *and* lenses, concave f > 0). JEE and
   NCERT use the Cartesian convention (1/v - 1/u = 1/f for lenses, concave f < 0).
   Quoting the textbook verbatim to a JEE student gives wrong signs.

Each note is standard NCERT-level physics, stated once, with the conditions under
which it holds. Each title is the syllabus topic's own name (jee_topics.py), which is
how syllabus_topics.py attaches it to that topic. Keep notes short and exact: they
are cited to students as "Aeropt reference notes", so an error here is an error
the tutor repeats with a citation behind it. Change one only with a source in hand.

Each entry: (chapter_id, title, text). chapter_id is the OpenStax chapter the
physics lives in; the syllabus topic is linked by title even when its unit differs.
"""

from __future__ import annotations

REFERENCE_BOOK = "Aeropt JEE reference notes"
REFERENCE_LICENSE = "original (Aeropt); standard NCERT-level results"

NOTES: list[tuple[str, str, str]] = [
    # ── Optics: the JEE sign convention ───────────────────────────────────────
    ("geometric_optics_and_image_formation", "Sign convention for mirrors and lenses (JEE / NCERT)", r"""
JEE and NCERT use the **New Cartesian sign convention**:

- All distances are measured from the pole (mirror) or optical centre (lens).
- Distances in the direction of the incident light are positive; against it, negative. With light travelling left to right, a real object always has $u < 0$.
- Heights above the principal axis are positive, below are negative.

Formulas in this convention:

- Mirror formula: $\frac{1}{v} + \frac{1}{u} = \frac{1}{f}$, with $f = \frac{R}{2}$. A concave mirror has $f < 0$; a convex mirror has $f > 0$. Magnification $m = -\frac{v}{u} = \frac{h'}{h}$.
- Thin lens formula: $\frac{1}{v} - \frac{1}{u} = \frac{1}{f}$. A convex (converging) lens has $f > 0$; a concave (diverging) lens has $f < 0$. Magnification $m = \frac{v}{u}$.
- Lens maker's formula: $\frac{1}{f} = (n - 1)\left(\frac{1}{R_1} - \frac{1}{R_2}\right)$, with $R$ positive when the centre of curvature lies on the outgoing-light side (biconvex: $R_1 > 0$, $R_2 < 0$). For a lens of index $n_2$ in a medium of index $n_1$, replace $n$ by $\frac{n_2}{n_1}$.
- Refraction at a spherical surface: $\frac{n_2}{v} - \frac{n_1}{u} = \frac{n_2 - n_1}{R}$.
- Power: $P = \frac{1}{f}$ with $f$ in metres, in dioptres (D).
- A negative $m$ means an inverted (real) image, a positive $m$ an erect (virtual) image.

**The OpenStax course text uses a different convention.** It writes $\frac{1}{d_o} + \frac{1}{d_i} = \frac{1}{f}$ for both mirrors and lenses, with real objects and real images positive and a concave mirror's $f$ positive. To convert, for a real object, $u = -d_o$; for a lens $v = d_i$ and $f$ is unchanged; for a mirror $v = -d_i$ and $f_{\text{JEE}} = -f_{\text{OpenStax}}$. Magnification comes out the same, $m = -\frac{d_i}{d_o}$. Always give a JEE student the Cartesian form.
"""),
    ("geometric_optics_and_image_formation", "Combination of thin lenses", r"""
Combination of thin lenses (Cartesian convention, $f > 0$ for convex lenses):

- Thin lenses in contact: $\frac{1}{F} = \frac{1}{f_1} + \frac{1}{f_2} + \dots$, so powers add, $P = P_1 + P_2 + \dots$
- Total magnification of a combination is the product, $m = m_1 m_2 \cdots$
- Two thin lenses separated by a distance $d$ along the axis: $\frac{1}{F} = \frac{1}{f_1} + \frac{1}{f_2} - \frac{d}{f_1 f_2}$.
- Step method: the image formed by the first lens is the object for the second, with its distance measured from the second lens. It can be a virtual object ($u > 0$) when the converging rays are intercepted first.
"""),
    ("geometric_optics_and_image_formation", "Refraction through a prism", r"""
Refraction through a prism of refracting angle $A$ and refractive index $n$:

- Geometry: $r_1 + r_2 = A$, and the deviation is $\delta = i + e - A$, where $i$ is the angle of incidence and $e$ the angle of emergence.
- Minimum deviation $\delta_m$ occurs when the ray passes symmetrically: $i = e$, $r_1 = r_2 = \frac{A}{2}$. Then $n = \frac{\sin\left(\frac{A + \delta_m}{2}\right)}{\sin\left(\frac{A}{2}\right)}$.
- Thin prism (small $A$): $\delta = (n - 1)A$, independent of $i$ for small angles.
- Dispersion: $n$ depends on colour, with violet bending most. Angular dispersion is $\delta_v - \delta_r = (n_v - n_r)A$ for a thin prism, and the dispersive power is $\omega = \frac{n_v - n_r}{n - 1}$, where $n$ is the mean (yellow) index.
- There is no emergent ray (total internal reflection at the second face) if $A > 2\theta_c$, where $\sin\theta_c = \frac{1}{n}$.
"""),
    ("geometric_optics_and_image_formation", "Microscope and astronomical telescope (JEE formulas)", r"""
Magnifying power of optical instruments, as JEE uses it. $D = 25\ \text{cm}$ is the least distance of distinct vision, and magnitudes are quoted.

- Simple microscope (magnifier): $m = 1 + \frac{D}{f}$ with the final image at the near point; $m = \frac{D}{f}$ with the image at infinity (relaxed eye).
- Compound microscope: $m = m_o m_e$. With the final image at infinity, $m \approx \frac{L}{f_o}\cdot\frac{D}{f_e}$, where $L$ is the tube length (the distance between the objective's second focal point and the eyepiece's first focal point). With the final image at the near point, $m = \frac{v_o}{|u_o|}\left(1 + \frac{D}{f_e}\right)$.
- Astronomical telescope in normal adjustment (final image at infinity): $m = \frac{f_o}{f_e}$, tube length $f_o + f_e$. With the final image at the near point: $m = \frac{f_o}{f_e}\left(1 + \frac{f_e}{D}\right)$.
- A telescope needs a large-aperture, long-focal-length objective; a microscope needs short focal lengths for both lenses.
"""),
    # ── Measurement ───────────────────────────────────────────────────────────
    ("units_and_measurement", "Least count", r"""
The least count of an instrument is the smallest value it can measure directly. Every reading made with it is uncertain by about one least count.

- Metre scale: $1\ \text{mm}$.
- Vernier calipers: $\text{LC} = 1\ \text{MSD} - 1\ \text{VSD} = \frac{\text{value of 1 MSD}}{N}$ when $N$ vernier divisions equal $N - 1$ main-scale divisions. A typical value is $0.1\ \text{mm}$.
- Screw gauge: $\text{LC} = \frac{\text{pitch}}{\text{number of circular-scale divisions}}$. A typical value is $\frac{0.5\ \text{mm}}{50} = 0.01\ \text{mm}$.
- A smaller least count means a more precise instrument. The absolute error of a single reading is taken as the least count unless stated otherwise.
"""),
    ("units_and_measurement", "Vernier calipers", r"""
Vernier calipers measure lengths, internal and external diameters, and depths.

- If $N$ vernier scale divisions (VSD) coincide with $N - 1$ main scale divisions (MSD), then $1\ \text{VSD} = \frac{N-1}{N}\ \text{MSD}$ and the least count is $\text{LC} = 1\ \text{MSD} - 1\ \text{VSD} = \frac{1\ \text{MSD}}{N}$.
- Reading = main scale reading + (vernier division that coincides $\times$ LC).
- Zero error: with the jaws closed, if the vernier zero lies to the **right** of the main-scale zero, the zero error is positive, $+n \times \text{LC}$ ($n$ = coinciding division). If it lies to the **left**, the zero error is negative, $-(N - n)\times\text{LC}$.
- Corrected reading = observed reading $-$ zero error (with its sign).
- Example: 10 VSD = 9 MSD with 1 MSD = 1 mm gives LC = 0.1 mm.
"""),
    ("units_and_measurement", "Screw gauge", r"""
A screw gauge (micrometer) measures small lengths, such as the diameter of a wire.

- Pitch is the distance the spindle moves along the main (linear) scale in one full rotation of the circular scale.
- Least count: $\text{LC} = \frac{\text{pitch}}{\text{number of divisions on the circular scale}}$.
- Reading = main scale reading + (circular scale division on the reference line $\times$ LC).
- Zero error: with the faces touching, if the circular-scale zero lies **below** the reference line, the zero error is positive, $+n \times \text{LC}$. If it lies **above**, the zero error is negative, $-(N - n)\times\text{LC}$, for $N$ circular divisions.
- Corrected reading = observed reading $-$ zero error (with its sign).
- Backlash error: always turn the screw in one direction when approaching the reading.
"""),
    # ── Mechanics ─────────────────────────────────────────────────────────────
    ("fixed_axis_rotation", "Radius of gyration", r"""
The radius of gyration $k$ of a body about an axis is the distance from the axis at which its whole mass could be concentrated without changing its moment of inertia: $I = Mk^2$, so $k = \sqrt{\frac{I}{M}}$.

$k$ depends on the axis chosen. Common values:

- Thin rod about its centre, perpendicular to its length: $k = \frac{L}{\sqrt{12}}$; about one end: $k = \frac{L}{\sqrt{3}}$
- Ring about its axis: $k = R$
- Disc about its axis: $k = \frac{R}{\sqrt{2}}$
- Solid sphere about a diameter: $k = \sqrt{\frac{2}{5}}\,R$
- Hollow sphere about a diameter: $k = \sqrt{\frac{2}{3}}\,R$

In rolling without slipping, $\text{KE} = \frac{1}{2}Mv^2\left(1 + \frac{k^2}{R^2}\right)$, and down an incline of angle $\theta$, $a = \frac{g\sin\theta}{1 + k^2/R^2}$.
"""),
    ("gravitation", "Variation of g with altitude and depth", r"""
At the Earth's surface, $g = \frac{GM}{R^2}$, treating the Earth as a uniform sphere of radius $R$.

- At height $h$ above the surface: $g_h = g\,\frac{R^2}{(R + h)^2}$, which for $h \ll R$ is approximately $g\left(1 - \frac{2h}{R}\right)$.
- At depth $d$ below the surface (uniform density): $g_d = g\left(1 - \frac{d}{R}\right)$. Inside the Earth $g$ falls linearly with distance from the centre, and $g = 0$ at the centre.
- For small $h$ and $d$, $g$ falls by the same amount at height $h$ as at depth $d = 2h$.
- Effect of rotation at latitude $\lambda$: $g_\lambda = g - \omega^2 R\cos^2\lambda$, so $g$ is least at the equator and unaffected at the poles.
"""),
    ("fluid_mechanics", "Critical velocity", r"""
Critical velocity is the speed of a fluid above which its flow stops being streamline (laminar) and becomes turbulent.

- Reynolds number: $R_e = \frac{\rho v D}{\eta}$, where $\rho$ is the fluid density, $v$ the speed, $D$ the pipe diameter and $\eta$ the viscosity.
- Flow is streamline for $R_e$ below about 1000, turbulent above about 2000, and unsteady in between (NCERT values).
- Critical velocity: $v_c = \frac{R_e\,\eta}{\rho D}$. It is higher for more viscous fluids and lower for denser fluids or wider pipes.
"""),
    ("fluid_mechanics", "Stokes' law and terminal velocity", r"""
Stokes' law: a small sphere of radius $r$ moving slowly at speed $v$ through a fluid of viscosity $\eta$ feels a drag force $F = 6\pi\eta r v$. It holds for streamline flow (low Reynolds number).

Terminal velocity: a sphere of density $\rho$ falling through a fluid of density $\sigma$ stops accelerating when weight = buoyancy + viscous drag:

$\frac{4}{3}\pi r^3\rho g = \frac{4}{3}\pi r^3\sigma g + 6\pi\eta r v_t$, which gives $v_t = \frac{2r^2(\rho - \sigma)g}{9\eta}$.

- $v_t \propto r^2$. If $\rho < \sigma$ (for example an air bubble in water), $v_t$ is negative: the body rises.
- Once terminal velocity is reached, the net force is zero and the speed stays constant.
"""),
    ("fluid_mechanics", "Excess pressure across a curved surface", r"""
Surface tension $T$ makes the pressure on the concave side of a curved liquid surface higher than on the convex side.

- Liquid drop of radius $R$: $P_{\text{in}} - P_{\text{out}} = \frac{2T}{R}$
- Air bubble inside a liquid (one surface): $\frac{2T}{R}$
- Soap bubble in air (two surfaces): $\frac{4T}{R}$
- Pressure inside an air bubble at depth $h$ below a liquid surface: $P_0 + \rho g h + \frac{2T}{R}$.
- Surface energy: increasing the area by $\Delta A$ takes work $W = T\,\Delta A$. Count both surfaces for a soap film: $W = 2T\,\Delta A$.
- Smaller bubbles have the higher excess pressure, so when two bubbles are connected, air flows from the smaller one into the larger.
"""),
    ("fluid_mechanics", "Capillary rise", r"""
Capillary rise: a liquid rises (or falls) in a narrow tube of radius $r$ because of surface tension.

$h = \frac{2T\cos\theta}{\rho g r}$

Here $T$ is the surface tension, $\theta$ the angle of contact and $\rho$ the liquid's density.

- $\theta < 90^\circ$ (water in clean glass, $\theta \approx 0$): the meniscus is concave and the liquid rises.
- $\theta > 90^\circ$ (mercury in glass): the meniscus is convex and the liquid is depressed ($h < 0$).
- $h \propto \frac{1}{r}$ (Jurin's law).
- If the tube is shorter than $h$, the liquid does **not** overflow. It rises to the top and the meniscus flattens, increasing its radius so that $hR$ stays constant (radius of meniscus $R = \frac{r}{\cos\theta}$).
- In a freely falling lift ($g_{\text{eff}} = 0$) the liquid rises to the top of any tube.
"""),
    # ── Heat, thermodynamics, kinetic theory ──────────────────────────────────
    ("the_kinetic_theory_of_gases", "Work done in compressing a gas", r"""
Work done by a gas whose volume changes from $V_1$ to $V_2$: $W = \int_{V_1}^{V_2} P\,dV$, equal to the area under the P-V curve. NCERT and JEE write the first law as $\Delta Q = \Delta U + W$ with $W$ the work done **by** the gas. In compression $W < 0$, and the work done **on** the gas is $-W$.

For $n$ moles of an ideal gas:

- Isobaric: $W = P(V_2 - V_1)$
- Isothermal: $W = nRT\ln\frac{V_2}{V_1}$. In compression, the work done on the gas is $nRT\ln\frac{V_1}{V_2}$ and an equal amount of heat leaves the gas ($\Delta U = 0$).
- Adiabatic: $W = \frac{P_1V_1 - P_2V_2}{\gamma - 1} = \frac{nR(T_1 - T_2)}{\gamma - 1}$. Compression heats the gas, because $\Delta U = -W > 0$.
- Isochoric: $W = 0$.

For the same compression ratio from the same initial state, adiabatic compression needs more work than isothermal compression: the adiabatic curve is steeper, since $PV^\gamma$ is constant rather than $PV$.
"""),
    # ── Waves ─────────────────────────────────────────────────────────────────
    ("waves", "Reflection of waves", r"""
Reflection of waves at a boundary:

- Rigid boundary (fixed end of a string, a denser medium): the reflected wave has a phase change of $\pi$, so a crest returns as a trough (the pulse comes back inverted). The boundary is a displacement node.
- Free boundary (free end of a string, a rarer medium): no phase change, so the pulse comes back upright. The boundary is a displacement antinode.
- The transmitted wave never suffers a phase change.
- At a junction of two strings, with wave speeds $v_1$ (incident side) and $v_2$: $\frac{A_r}{A_i} = \frac{v_2 - v_1}{v_2 + v_1}$ and $\frac{A_t}{A_i} = \frac{2v_2}{v_1 + v_2}$. A negative $A_r$ means the wave is inverted.
- Incident and reflected waves superpose into a standing wave, for example $y = 2A\sin kx\cos\omega t$, with nodes $\frac{\lambda}{2}$ apart.
- Sound in pipes: a closed end is a displacement node (pressure antinode); an open end is a displacement antinode (pressure node).
"""),
    # ── Electrostatics ────────────────────────────────────────────────────────
    ("electric_charges_and_fields", "Torque on a dipole in a uniform field", r"""
An electric dipole of moment $\vec{p} = q\,\vec{d}$ (pointing from $-q$ to $+q$) in a uniform field $\vec{E}$:

- Net force: zero, since the forces on the two charges cancel. In a non-uniform field the net force is not zero.
- Torque: $\vec{\tau} = \vec{p}\times\vec{E}$, magnitude $\tau = pE\sin\theta$. It tends to align $\vec{p}$ with $\vec{E}$.
- Potential energy: $U = -\vec{p}\cdot\vec{E} = -pE\cos\theta$, taking $U = 0$ at $\theta = 90^\circ$.
- Work done by an external agent to rotate it from $\theta_1$ to $\theta_2$: $W = pE(\cos\theta_1 - \cos\theta_2)$.
- Equilibrium: stable at $\theta = 0$ ($U = -pE$), unstable at $\theta = 180^\circ$ ($U = +pE$).
- Small oscillations about $\theta = 0$, for moment of inertia $I$: $\omega = \sqrt{\frac{pE}{I}}$.
"""),
    # ── Current electricity ───────────────────────────────────────────────────
    ("direct_current_circuits", "Wheatstone bridge", r"""
Wheatstone bridge: four resistances $P$ (arm AB), $Q$ (BC), $R$ (AD) and $S$ (DC), a cell across A and C, and a galvanometer across B and D.

- Balance condition (no current through the galvanometer): $\frac{P}{Q} = \frac{R}{S}$.
- At balance, B and D are at the same potential, so the galvanometer branch can be removed or replaced by any resistance without changing the currents. The condition does not depend on the cell's emf or the galvanometer's resistance.
- Interchanging the cell and the galvanometer keeps the bridge balanced.
- The bridge is most sensitive when all four resistances are of the same order.
- When it is not balanced, solve with Kirchhoff's laws.
"""),
    ("direct_current_circuits", "Metre bridge", r"""
The metre bridge (slide wire bridge) is a practical Wheatstone bridge built on a uniform 1 m wire.

- A known resistance $R$ sits in the left gap and the unknown $S$ in the right gap. A jockey slides along the wire to the balance point at length $l$ (in cm) from the left end.
- Since the wire is uniform, resistance is proportional to length, so at balance $\frac{R}{S} = \frac{l}{100 - l}$, giving $S = R\,\frac{100 - l}{l}$.
- The result is most accurate when the balance point is near the middle of the wire. Interchanging $R$ and $S$ and averaging cancels end errors.
- End correction: the resistances of the copper strips and contacts add small lengths $\alpha$ and $\beta$, so $\frac{R}{S} = \frac{l + \alpha}{100 - l + \beta}$.
"""),
    ("direct_current_circuits", "Combination of cells", r"""
Combination of cells, each of emf $\varepsilon$ and internal resistance $r$, driving an external resistance $R$:

- $n$ cells in series: $\varepsilon_{\text{eq}} = n\varepsilon$, $r_{\text{eq}} = nr$, so $I = \frac{n\varepsilon}{R + nr}$. A cell connected the wrong way round subtracts its emf but still adds its internal resistance.
- $m$ identical cells in parallel: $\varepsilon_{\text{eq}} = \varepsilon$, $r_{\text{eq}} = \frac{r}{m}$, so $I = \frac{\varepsilon}{R + r/m}$.
- Mixed grouping ($m$ rows, each of $n$ cells in series): $I = \frac{n\varepsilon}{R + nr/m}$. The current is maximum when $R = \frac{nr}{m}$.
- Two different cells in parallel: $\varepsilon_{\text{eq}} = \frac{\varepsilon_1 r_2 + \varepsilon_2 r_1}{r_1 + r_2}$, $r_{\text{eq}} = \frac{r_1 r_2}{r_1 + r_2}$.
- Terminal voltage: $V = \varepsilon - Ir$ while discharging, $V = \varepsilon + Ir$ while charging.
"""),
    # ── Magnetism ─────────────────────────────────────────────────────────────
    ("magnetic_forces_and_fields", "Torque on a magnetic dipole", r"""
A current loop is a magnetic dipole with moment $\vec{m} = NI\vec{A}$, for $N$ turns, current $I$ and area vector $\vec{A}$ given by the right-hand rule. In a uniform field $\vec{B}$:

- Net force: zero.
- Torque: $\vec{\tau} = \vec{m}\times\vec{B}$, magnitude $\tau = mB\sin\theta$.
- Potential energy: $U = -\vec{m}\cdot\vec{B} = -mB\cos\theta$. Stable equilibrium at $\theta = 0$, unstable at $\theta = 180^\circ$.
- Work to rotate from $\theta_1$ to $\theta_2$: $W = mB(\cos\theta_1 - \cos\theta_2)$.
- A magnetic needle of moment of inertia $I$ oscillates with $T = 2\pi\sqrt{\frac{I}{mB}}$.
- Moving coil galvanometer: in a radial field the coil turns until $NIAB = k\phi$, where $k$ is the torsion constant. Current sensitivity is $\frac{\phi}{I} = \frac{NAB}{k}$.
"""),
    ("sources_of_magnetic_fields", "Effect of temperature on magnetism", r"""
Effect of temperature on magnetic materials, with susceptibility $\chi$:

- Diamagnetic materials: $\chi$ is small and negative, and essentially independent of temperature.
- Paramagnetic materials: Curie's law, $\chi = \frac{C}{T}$ (equivalently $M = C\frac{B_0}{T}$). Thermal agitation disorders the atomic dipoles, so magnetisation falls as temperature rises.
- Ferromagnetic materials: above the Curie temperature $T_c$ they become paramagnetic, with $\chi = \frac{C}{T - T_c}$ for $T > T_c$. Approximate Curie temperatures: iron about 1043 K, nickel about 631 K, cobalt about 1394 K.
- Heating a permanent magnet above its Curie temperature destroys its magnetisation.
"""),
    ("alternating_current_circuits", "Wattless current", r"""
In an AC circuit the average power is $P = V_{\text{rms}}I_{\text{rms}}\cos\phi$, where $\phi$ is the phase difference between voltage and current and $\cos\phi$ is the power factor.

- Resolve the current into $I_{\text{rms}}\cos\phi$, in phase with the voltage, which carries all the power, and $I_{\text{rms}}\sin\phi$, $90^\circ$ out of phase, which carries none. The second part is the **wattless current**.
- In a purely inductive or purely capacitive circuit, $\phi = 90^\circ$, so $P = 0$ and the whole current is wattless.
- A choke coil (large inductance, small resistance) limits current with almost no power loss, which is why it replaces a resistor in AC circuits.
"""),
    # ── Electronic devices ────────────────────────────────────────────────────
    ("condensed_matter_physics", "Diode as a rectifier", r"""
Rectification is converting AC to DC using a p-n junction diode, which conducts only when forward biased.

- Half-wave rectifier (one diode): output appears only during one half cycle, at the same ripple frequency as the input, $f$. $V_{\text{dc}} = \frac{V_m}{\pi}$, $I_{\text{rms}} = \frac{I_m}{2}$, maximum efficiency 40.6%.
- Full-wave rectifier (two diodes with a centre-tapped transformer, or four in a bridge): output in both half cycles, ripple frequency $2f$. $V_{\text{dc}} = \frac{2V_m}{\pi}$, $I_{\text{rms}} = \frac{I_m}{\sqrt{2}}$, maximum efficiency 81.2%.
- Peak inverse voltage across a diode: $V_m$ for half-wave and bridge, $2V_m$ for centre-tap (where $V_m$ is each half of the secondary).
- A capacitor filter in parallel with the load smooths the pulsating output.
- These use an ideal diode. A real silicon diode drops about 0.7 V when it conducts.
"""),
    ("condensed_matter_physics", "Zener diode as a voltage regulator", r"""
A Zener diode is heavily doped and designed to work in **reverse** breakdown, where the voltage across it stays nearly constant at $V_Z$ over a wide range of current.

Voltage regulator: unregulated input $V_{\text{in}}$, then a series resistor $R_s$, then the Zener (reverse biased) in parallel with the load $R_L$.

- Load voltage: $V_L = V_Z$, as long as the Zener is in breakdown.
- Current through $R_s$: $I_s = \frac{V_{\text{in}} - V_Z}{R_s}$; load current $I_L = \frac{V_Z}{R_L}$; Zener current $I_Z = I_s - I_L$.
- Regulation works only while $I_Z > 0$ (and below the rated maximum). If $V_{\text{in}}$ rises, the extra current flows through the Zener and the extra voltage drops across $R_s$.
- In heavily doped junctions the breakdown is by internal field emission (Zener breakdown) and occurs at a sharp, well-defined voltage.
"""),
    ("condensed_matter_physics", "Photodiode", r"""
A photodiode is a p-n junction operated in **reverse bias** to detect light.

- Light with photon energy $h\nu > E_g$ (equivalently $\lambda < \frac{hc}{E_g}$) creates electron-hole pairs near the junction. The junction field separates them, adding to the reverse current.
- The reverse current increases with light intensity, which is how it measures intensity.
- It is used in reverse bias because there the fractional change in current produced by light is much larger than in forward bias, where the majority-carrier current dominates.
- Compare the LED (forward biased, emits light) and the solar cell (no external bias, generates emf).
"""),
    ("condensed_matter_physics", "Logic gates (OR, AND, NOT, NAND, NOR)", r"""
Logic gates have digital inputs $A$, $B$ (0 or 1) and an output $Y$.

- OR: $Y = A + B$, 1 if either input is 1. Inputs (0,0), (0,1), (1,0), (1,1) give 0, 1, 1, 1.
- AND: $Y = A\cdot B$, 1 only if both inputs are 1. Outputs 0, 0, 0, 1.
- NOT: $Y = \overline{A}$, which inverts: 0 gives 1, 1 gives 0.
- NAND: $Y = \overline{A\cdot B}$. Outputs 1, 1, 1, 0.
- NOR: $Y = \overline{A + B}$. Outputs 1, 0, 0, 0.

NAND and NOR are universal gates: any gate can be built from either alone. For example, a NAND with both inputs joined is a NOT gate, and a NAND followed by a NOT is an AND gate.

De Morgan's theorems: $\overline{A + B} = \overline{A}\cdot\overline{B}$ and $\overline{A\cdot B} = \overline{A} + \overline{B}$.
"""),
    # ── Experimental skills ───────────────────────────────────────────────────
    ("static_equilibrium_and_elasticity", "Metre scale: mass by principle of moments", r"""
Experiment: finding an unknown mass with a metre scale, using the principle of moments.

- Balance the metre scale on a knife edge at its centre of gravity (near the 50 cm mark), so its own weight produces no moment.
- Hang the known mass $m_1$ at distance $d_1$ on one side and the unknown mass $m_2$ at distance $d_2$ on the other. Adjust until the scale is horizontal again.
- Principle of moments (clockwise moment = anticlockwise moment): $m_1 d_1 = m_2 d_2$, so $m_2 = \frac{m_1 d_1}{d_2}$.
- If the knife edge is not at the centre of gravity, include the scale's own weight $Mg$ acting at the centre of gravity. The same idea, with one known mass, finds the mass $M$ of the scale itself.
- Repeat for several positions and average.
"""),
    ("static_equilibrium_and_elasticity", "Young's modulus of a metallic wire", r"""
Experiment: Young's modulus of a metallic wire (Searle's method).

- Two identical wires hang from the same support: the reference wire carries a constant load, and the experimental wire carries the load $Mg$. The reference wire cancels the effects of temperature change and of the support yielding.
- Measure the wire's length $L$ with a metre scale, its radius $r$ with a screw gauge at several places (averaged), and the extension $\Delta L$ with the micrometer (spirit-level) arrangement, for increasing and then decreasing loads.
- $Y = \frac{\text{stress}}{\text{strain}} = \frac{Mg/(\pi r^2)}{\Delta L/L} = \frac{MgL}{\pi r^2\,\Delta L}$.
- Plotting load against extension gives a straight line within the elastic limit, and $Y = \frac{L}{\pi r^2}\times\text{slope}$ (load/extension).
- Maximum relative error: $\frac{\Delta Y}{Y} = \frac{\Delta M}{M} + \frac{\Delta L}{L} + 2\frac{\Delta r}{r} + \frac{\Delta(\Delta L)}{\Delta L}$. The radius, being small and squared, dominates.
"""),
    ("fluid_mechanics", "Surface tension by capillary rise", r"""
Experiment: surface tension of water by capillary rise.

- Dip a clean, narrow glass capillary vertically in water. Measure the height $h$ of the meniscus above the water surface, and the tube's internal radius $r$, with a travelling microscope.
- From $h = \frac{2T\cos\theta}{\rho g r}$: $T = \frac{r h \rho g}{2\cos\theta}$. For water in clean glass $\theta \approx 0$, so $T = \frac{r h\rho g}{2}$.
- Correction for the liquid in the meniscus: use $h + \frac{r}{3}$ in place of $h$.
- Precautions: the tube must be clean and grease-free, and wet before reading.
"""),
    ("fluid_mechanics", "Viscosity by terminal velocity", r"""
Experiment: coefficient of viscosity of a liquid (e.g. glycerine) from the terminal velocity of a falling sphere.

- Drop small steel balls of known radius $r$ (screw gauge) and density $\rho$ into a tall jar of the liquid (density $\sigma$). Time each one over a marked distance well below the surface, where it has already reached terminal velocity, which gives $v_t$.
- From Stokes' law, $v_t = \frac{2r^2(\rho - \sigma)g}{9\eta}$, so $\eta = \frac{2r^2(\rho - \sigma)g}{9v_t}$.
- A plot of $v_t$ against $r^2$ is a straight line through the origin with slope $\frac{2(\rho - \sigma)g}{9\eta}$.
- The ball must fall along the axis, far from the walls, at a fixed temperature, since viscosity falls as temperature rises.
"""),
    ("temperature_and_heat", "Specific heat by the method of mixtures", r"""
Experiment: specific heat capacity of a solid (or a liquid) by the method of mixtures.

- Heat the solid (mass $m_s$) to temperature $T_s$, then drop it quickly into water (mass $m_w$) in a calorimeter (mass $m_c$, specific heat $s_c$), both initially at $T_w$. Stir and record the final steady temperature $T$.
- Heat lost = heat gained: $m_s s_s (T_s - T) = (m_w s_w + m_c s_c)(T - T_w)$, so $s_s = \frac{(m_w s_w + m_c s_c)(T - T_w)}{m_s (T_s - T)}$.
- It assumes no heat is exchanged with the surroundings. Transfer the solid quickly and insulate the calorimeter.
"""),
    ("current_and_resistance", "Resistivity using a metre bridge", r"""
Experiment: resistivity of the material of a wire using a metre bridge.

- Put the wire (length $L$) in the right gap and a known resistance $R$ (resistance box) in the left gap. Find the balance length $l$ (cm) from the left end: $S = R\,\frac{100 - l}{l}$.
- Measure the diameter $d$ with a screw gauge at several points: area $A = \frac{\pi d^2}{4}$.
- Resistivity: $\rho = \frac{SA}{L} = \frac{\pi d^2 S}{4L}$.
- Choose $R$ so the balance point falls near the middle of the wire, and repeat with $R$ and $S$ interchanged to cancel end errors.
"""),
    ("current_and_resistance", "Resistance of a wire using Ohm's law", r"""
Experiment: resistance of a given wire using Ohm's law.

- Connect a battery, key, rheostat, ammeter and the wire in series, with a voltmeter in parallel across the wire.
- Vary the rheostat and record pairs of voltmeter reading $V$ and ammeter reading $I$.
- The $V$-$I$ graph is a straight line through the origin for an ohmic conductor at constant temperature. The resistance is the slope, $R = \frac{\Delta V}{\Delta I}$.
- Resistivity follows from $\rho = \frac{RA}{L}$. Keep the current small, so the wire does not heat and change its resistance.
"""),
    ("magnetic_forces_and_fields", "Galvanometer: resistance and figure of merit", r"""
Experiment: resistance of a galvanometer by the half-deflection method, and its figure of merit.

- Connect a cell (emf $E$), a high resistance $R$ (resistance box) and the galvanometer in series, and adjust $R$ for a deflection $\theta$.
- Connect a shunt resistance $S$ across the galvanometer and adjust it until the deflection is $\frac{\theta}{2}$, keeping $R$ fixed. Then $G = \frac{RS}{R - S}$, which is approximately $S$ when $R \gg S$.
- Figure of merit (current per division): $k = \frac{I}{\theta} = \frac{E}{(R + G)\,\theta}$.
- Conversions: an ammeter of range $I$ needs a shunt $S = \frac{I_g G}{I - I_g}$; a voltmeter of range $V$ needs a series resistance $R = \frac{V}{I_g} - G$, where $I_g$ is the full-scale current.
"""),
    ("geometric_optics_and_image_formation", "Focal length of mirrors and lenses by parallax", r"""
Experiment: focal length of a concave mirror or a convex lens by the u-v method, locating images by removing parallax.

- No parallax: when the image and a search pin coincide, they do not shift relative to each other as the eye moves sideways. Then the pin marks the image position.
- Measure the object distance $u$ and the image distance $v$ for several object positions, using the Cartesian sign convention.
- Concave mirror: $\frac{1}{f} = \frac{1}{v} + \frac{1}{u}$, so $f = \frac{uv}{u + v}$ (both $u, v < 0$ for a real image, so $f < 0$).
- Convex lens: $\frac{1}{f} = \frac{1}{v} - \frac{1}{u}$, so $f = \frac{uv}{u - v}$ ($u < 0$, $v > 0$, so $f > 0$).
- A graph of $\frac{1}{v}$ against $\frac{1}{u}$ is a straight line with both intercepts equal to $\frac{1}{f}$ in magnitude. Correct for index error (the bench scale's offset) before using the distances.
- A convex mirror's focal length is found with an auxiliary convex lens, since it forms no real image of a real object.
"""),
    ("geometric_optics_and_image_formation", "Angle of deviation vs angle of incidence for a prism", r"""
Experiment: plotting angle of deviation vs angle of incidence for a prism to find its refractive index.

- For several angles of incidence $i$, trace the emergent ray with pins and measure the deviation $\delta$ between the incident and emergent directions.
- The graph of $\delta$ against $i$ is U-shaped. Its lowest point is the angle of minimum deviation $\delta_m$, where $i = e$.
- With the prism angle $A$ measured: $n = \frac{\sin\left(\frac{A + \delta_m}{2}\right)}{\sin\left(\frac{A}{2}\right)}$.
- Except at the minimum, each value of $\delta$ occurs for two angles of incidence ($i$ and the corresponding $e$, interchanged).
"""),
    ("condensed_matter_physics", "Characteristic curves of a p-n junction diode", r"""
Experiment: I-V characteristic curves of a p-n junction diode.

- Forward bias (p side to +, with a milliammeter): almost no current until the threshold (knee) voltage, about 0.7 V for silicon and 0.2-0.3 V for germanium. Beyond it the current rises steeply (exponentially).
- Reverse bias (with a microammeter): a small, nearly constant reverse saturation current (μA) from minority carriers, until breakdown.
- Dynamic resistance at a point on the curve: $r_d = \frac{\Delta V}{\Delta I}$. It is not constant, so the diode is a non-ohmic device.
"""),
    ("condensed_matter_physics", "Characteristic curves of a Zener diode", r"""
Experiment: characteristic curves of a Zener diode and its reverse breakdown voltage.

- Forward bias: like an ordinary diode, with conduction beyond about 0.7 V (silicon).
- Reverse bias: a negligible current until the Zener (breakdown) voltage $V_Z$. Then the current rises sharply while the voltage stays almost constant at $V_Z$.
- $V_Z$ is read from the knee of the reverse characteristic. That nearly vertical region is what lets a Zener regulate voltage.
- Use a series current-limiting resistor so the reverse current stays below the rated maximum.
"""),
    ("condensed_matter_physics", "Identifying diode, LED, resistor and capacitor", r"""
Experiment: identifying a diode, an LED, a resistor and a capacitor from a mixed collection, with a multimeter (or a battery, resistor and meter).

- Resistor: conducts equally in both directions, with a steady, finite reading that does not change when the leads are reversed.
- Diode: low resistance in one direction (forward) and very high resistance when the leads are reversed (reverse). It emits no light.
- LED: conducts in one direction only, like a diode, and glows when forward biased.
- Capacitor: a reading that starts as a deflection (charging current) and falls steadily to zero (infinite resistance). The kick repeats when the leads are reversed.
"""),
]
