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
    # ── Chemistry: Practical and Laboratory Reference Notes ───────────────────
    ("d_and_f_block_elements", "Standard electrode potential and chemical reactivity of transition elements", r"""
Standard electrode potentials ($E^\circ_{\text{M}^{2+}/\text{M}}$) of 3d transition metals:

- Along the 3d series from Sc to Zn, values generally become less negative, with anomalies at Mn ($-1.18\text{ V}$, half-filled $d^5$), Zn ($-0.76\text{ V}$, completely filled $d^{10}$), and Cu ($+0.34\text{ V}$, positive due to high enthalpy of atomization not balanced by hydration enthalpy).
- $E^\circ_{\text{M}^{3+}/\text{M}^{2+}}$ values: High positive value for $\text{Mn}^{3+}/\text{Mn}^{2+}$ ($+1.57\text{ V}$) reflects the stability of $\text{Mn}^{2+}$ ($d^5$). High positive value for $\text{Co}^{3+}/\text{Co}^{2+}$ ($+1.97\text{ V}$). Negative value for $\text{Cr}^{3+}/\text{Cr}^{2+}$ ($-0.41\text{ V}$) reflects the stable $t_{2g}^3$ configuration of $\text{Cr}^{3+}$.
- Chemical reactivity: Elements with negative $E^\circ$ liberate $\text{H}_2$ from dilute acids, while copper ($E^\circ > 0$) does not dissolve in non-oxidizing acids.
"""),
    ("general_organic_chemistry_goc", "Quantitative analysis: Estimation of carbon and hydrogen", r"""
Liebig's combustion method for quantitative estimation of Carbon and Hydrogen:

- A known mass ($m\text{ g}$) of organic compound is heated with excess pure copper(II) oxide ($\text{CuO}$) in a dry dioxygen atmosphere.
- Carbon is oxidized to $\text{CO}_2$ and absorbed in a pre-weighed U-tube containing concentrated potassium hydroxide ($\text{KOH}$) solution ($\Delta m_{\text{CO}_2}$).
- Hydrogen is oxidized to $\text{H}_2\text{O}$ and absorbed in a pre-weighed U-tube containing anhydrous calcium chloride ($\text{CaCl}_2$) ($\Delta m_{\text{H}_2\text{O}}$).
- Formulas for percentage composition:
  $$\%\text{ C} = \frac{12}{44} \times \frac{\Delta m_{\text{CO}_2}}{m} \times 100$$
  $$\%\text{ H} = \frac{2}{18} \times \frac{\Delta m_{\text{H}_2\text{O}}}{m} \times 100$$
"""),
    ("general_organic_chemistry_goc", "Preparation of inorganic and organic compounds", r"""
Laboratory preparation of inorganic and organic compounds:

1. **Mohr's Salt** (Ferrous ammonium sulphate, $\text{FeSO}_4\cdot(\text{NH}_4)_2\text{SO}_4\cdot 6\text{H}_2\text{O}$): Equimolar mixture of ferrous sulphate and ammonium sulphate dissolved in water containing dilute $\text{H}_2\text{SO}_4$ (to prevent hydrolysis of $\text{Fe}^{2+}$), followed by crystallization. Forms light green octahedral crystals.
2. **Potash Alum** ($\text{K}_2\text{SO}_4\cdot\text{Al}_2(\text{SO}_4)_3\cdot 24\text{H}_2\text{O}$): Equimolar solution of potassium sulphate and aluminium sulphate crystallized into colourless octahedral crystals.
3. **Acetanilide**: Acetylation of aniline with acetic anhydride in the presence of glacial acetic acid or zinc dust:
   $$\text{C}_6\text{H}_5\text{NH}_2 + (\text{CH}_3\text{CO})_2\text{O} \to \text{C}_6\text{H}_5\text{NHCOCH}_3 + \text{CH}_3\text{COOH}$$
   Forms white glistening flakes (m.p. $114^\circ\text{C}$).
4. **Aniline Yellow** ($p$-aminoazobenzene): Prepared by diazotization of aniline with $\text{NaNO}_2 + \text{HCl}$ at $0\text{--}5^\circ\text{C}$ to yield benzenediazonium chloride, followed by coupling with aniline to form diazoaminobenzene, which rearranges on warming with acid to $p$-aminoazobenzene (yellow dye).
"""),
    ("mole_concept_stoichiometry", "Physical chemistry practicals", r"""
Physical chemistry practical laboratory techniques:

1. **Melting Point Determination**: Carried out using a Kjeldahl flask or Thiele tube filled with liquid paraffin or concentrated sulphuric acid. A thin capillary tube sealed at one end containing pulverized dry organic compound is tied to a thermometer. The temperature at which the substance starts melting to complete liquefaction is noted.
2. **Boiling Point Determination**: Using distillation assembly or J-tube / capillary method. The liquid is placed in a small ignition tube with an inverted capillary (air pocket acting as boiling chip). Heated gently until a continuous stream of bubbles escapes, then allowed to cool; the temperature at which liquid just rushes into the capillary marks the boiling point.
"""),
    # ── Mathematics: ALLEN / Kota Coaching High-Yield Theory Notes ────────────
    ("sets", "Wavy Curve Method and Rational Inequalities", r"""
The Wavy Curve Method (Method of Intervals) for solving inequalities $f(x) \gtrless 0$ where $f(x) = \frac{(x-a_1)^{k_1}(x-a_2)^{k_2}\cdots}{(x-b_1)^{m_1}(x-b_2)^{m_2}\cdots}$:

1. Factorize numerator and denominator into linear factors $(x - c_i)$ with positive $x$ coefficient.
2. Mark all roots (zeros and poles) on the real number line in ascending order.
3. Denominator roots $x = b_i$ must always be excluded (open circle, cannot divide by zero).
4. For $x > \text{largest root}$, the expression is always positive (+).
5. Moving right to left across root $c_i$:
   - If exponent of $(x - c_i)$ is **odd**, the sign **changes** ($+ \to -$ or $- \to +$).
   - If exponent is **even**, the sign **remains unchanged** ($+ \to +$ or $- \to -$).
6. Pick intervals marked $(+)$ for $>0$ (or $\ge 0$) and $(-)$ for $<0$ (or $\le 0$). For $\ge 0$ or $\le 0$, include numerator roots where $f(x)=0$.
7. Modulus inequalities: $|f(x)| \le a \iff -a \le f(x) \le a$; $|f(x)| \ge a \iff f(x) \ge a \text{ or } f(x) \le -a$.
"""),
    ("sets", "Logarithms and Logarithmic Inequalities", r"""
Logarithm definitions, identities and inequalities ($y = \log_a x$ with $x > 0, a > 0, a \ne 1$):

- **Fundamental properties**:
  - $\log_a (xy) = \log_a x + \log_a y$ and $\log_a (x/y) = \log_a x - \log_a y$.
  - $\log_a (x^k) = k \log_a x$ and $\log_{a^m} (x) = \frac{1}{m} \log_a x$.
  - Base change: $\log_a x = \frac{\log_b x}{\log_b a} = \frac{\ln x}{\ln a}$.
  - Power of log: $a^{\log_a x} = x$ and $a^{\log_b c} = c^{\log_b a}$.
- **Logarithmic Inequalities**:
  - If base $a > 1$ (strictly increasing): $\log_a f(x) > \log_a g(x) \iff f(x) > g(x) > 0$ (sign of inequality is preserved).
  - If base $0 < a < 1$ (strictly decreasing): $\log_a f(x) > \log_a g(x) \iff 0 < f(x) < g(x)$ (sign of inequality reverses).
- **Characteristic and Mantissa**: For $x > 0$, $\log_{10} x = c + m$ where $c \in \mathbb{Z}$ (characteristic) and $0 \le m < 1$ (mantissa).
  - Number of digits in integer $N$ is $\lfloor\log_{10} N\rfloor + 1$.
  - Number of leading zeros after decimal point before first non-zero digit in $x \in (0, 1)$ is $|\lfloor\log_{10} x\rfloor| - 1$ or $-c - 1$ where $c < 0$.
"""),
    ("complex_numbers_and_quadratic_equations", "Location of roots and sign of quadratic expressions", r"""
Location of roots of quadratic equation $f(x) = ax^2 + bx + c = 0$ ($a > 0, D = b^2 - 4ac$):

1. **Both roots greater than a real number $k$ ($\alpha, \beta > k$):**
   $$D \ge 0,\quad -\frac{b}{2a} > k,\quad a f(k) > 0$$
2. **Both roots less than $k$ ($\alpha, \beta < k$):**
   $$D \ge 0,\quad -\frac{b}{2a} < k,\quad a f(k) > 0$$
3. **A number $k$ lies between the roots ($\alpha < k < \beta$):**
   $$a f(k) < 0 \quad (\text{automatically implies } D > 0)$$
4. **Both roots lie between $k_1$ and $k_2$ ($k_1 < \alpha \le \beta < k_2$):**
   $$D \ge 0,\quad k_1 < -\frac{b}{2a} < k_2,\quad a f(k_1) > 0,\quad a f(k_2) > 0$$
5. **Exactly one root lies in $(k_1, k_2)$:**
   $$f(k_1) f(k_2) < 0$$
6. **Sign and range of $f(x) = ax^2 + bx + c$:**
   - If $a > 0, D < 0 \implies f(x) > 0 \;\forall x \in \mathbb{R}$. Minimum value is $-\frac{D}{4a}$ at $x = -\frac{b}{2a}$.
   - If $a < 0, D < 0 \implies f(x) < 0 \;\forall x \in \mathbb{R}$. Maximum value is $-\frac{D}{4a}$ at $x = -\frac{b}{2a}$.
"""),
    ("complex_numbers_and_quadratic_equations", "De Moivre's theorem and roots of unity", r"""
De Moivre's Theorem and Cube / $n$-th Roots of Unity:

- **De Moivre's Theorem**: For any $n \in \mathbb{R}$, $(\cos\theta + i\sin\theta)^n = \cos(n\theta) + i\sin(n\theta) = e^{i n \theta}$.
- **Cube roots of unity**: Solutions of $z^3 = 1$ are $1, \omega, \omega^2$ where $\omega = e^{i 2\pi/3} = -\frac{1}{2} + i\frac{\sqrt{3}}{2}$ and $\omega^2 = e^{i 4\pi/3} = -\frac{1}{2} - i\frac{\sqrt{3}}{2}$.
  - Properties: $1 + \omega + \omega^2 = 0$, $\omega^3 = 1$, $\omega^{3k+1} = \omega$, $\omega^{3k+2} = \omega^2$.
  - Factoring: $a^3 - b^3 = (a-b)(a-\omega b)(a-\omega^2 b)$, $a^3 + b^3 = (a+b)(a+\omega b)(a+\omega^2 b)$.
  - $a^2 + ab + b^2 = (a - \omega b)(a - \omega^2 b)$, $a^2 + b^2 + c^2 - ab - bc - ca = (a + \omega b + \omega^2 c)(a + \omega^2 b + \omega c)$.
- **$n$-th roots of unity**: Solutions of $z^n = 1$ are $\alpha_k = e^{i \frac{2k\pi}{n}}$ for $k = 0, 1, \dots, n-1$.
  - Sum of all $n$-th roots: $\sum_{k=0}^{n-1} \alpha_k = 0$.
  - Product of all $n$-th roots: $\prod_{k=0}^{n-1} \alpha_k = (-1)^{n-1}$.
  - $z^n - 1 = (z - 1)(z - \alpha_1)(z - \alpha_2)\cdots(z - \alpha_{n-1})$.
"""),
    ("complex_numbers_and_quadratic_equations", "Geometry of complex numbers and locus problems", r"""
Geometry in the Complex Argand Plane:

- **Distance between $z_1$ and $z_2$**: $|z_1 - z_2|$.
- **Section formula**: Point $z$ dividing line segment joining $z_1, z_2$ in ratio $m:n$ is $z = \frac{m z_2 + n z_1}{m + n}$.
- **Equation of a circle**:
  - Center $z_0$, radius $r$: $|z - z_0| = r$, or in general form $z\bar{z} + \bar{a}z + a\bar{z} + b = 0$ ($b \in \mathbb{R}$) with center $-a$ and radius $\sqrt{|a|^2 - b}$.
- **Equation of a straight line**: $\bar{a}z + a\bar{z} + b = 0$ ($b \in \mathbb{R}$).
- **Perpendicular bisector of $z_1$ and $z_2$**: $|z - z_1| = |z - z_2|$.
- **Conic loci**:
  - Ellipse: $|z - z_1| + |z - z_2| = 2a$ (where $2a > |z_1 - z_2|$).
  - Hyperbola: $||z - z_1| - |z - z_2|| = 2a$ (where $2a < |z_1 - z_2|$).
  - Circle (Apollonius circle): $\left|\frac{z - z_1}{z - z_2}\right| = k \ne 1$.
- **Angle between lines**: $\arg\left(\frac{z_3 - z_1}{z_2 - z_1}\right)$ is the angle $\angle z_2 z_1 z_3$.
"""),
    ("sequences_and_series", "Harmonic Progression (HP) and AM-GM-HM inequality", r"""
Harmonic Progression (HP) and Fundamental Inequalities:

- **Harmonic Progression (HP)**: A sequence $a_1, a_2, \dots, a_n$ is in HP if $\frac{1}{a_1}, \frac{1}{a_2}, \dots, \frac{1}{a_n}$ is in AP.
  - $n$-th term: $T_n = \frac{1}{\frac{1}{a} + (n-1)d}$.
  - Harmonic Mean (HM) of $a$ and $b$: $H = \frac{2ab}{a+b}$.
  - Harmonic Mean of $n$ positive numbers: $H = \frac{n}{\sum_{i=1}^n \frac{1}{a_i}}$.
- **AM-GM-HM Inequality**: For positive real numbers $a_1, a_2, \dots, a_n$:
  $$\text{AM} \ge \text{GM} \ge \text{HM}$$
  $$\frac{a_1 + a_2 + \dots + a_n}{n} \ge (a_1 a_2 \cdots a_n)^{1/n} \ge \frac{n}{\frac{1}{a_1} + \frac{1}{a_2} + \dots + \frac{1}{a_n}}$$
  Equality holds if and only if $a_1 = a_2 = \dots = a_n$.
- **Weighted AM-GM Inequality**: For weights $w_i > 0$ with $W = \sum w_i$:
  $$\frac{\sum w_i a_i}{W} \ge \left(\prod a_i^{w_i}\right)^{1/W}$$
- **Cauchy-Schwarz Inequality**: For any real sequences $(a_i)$ and $(b_i)$:
  $$\left(\sum_{i=1}^n a_i b_i\right)^2 \le \left(\sum_{i=1}^n a_i^2\right) \left(\sum_{i=1}^n b_i^2\right)$$
"""),
    ("sequences_and_series", "Method of differences and telescoping series", r"""
Summation of Series via Telescoping Sums and Method of Differences:

1. **Telescoping Method ($V_n - V_{n-1}$)**:
   Express the general term $T_n$ as a difference of two consecutive values of another sequence: $T_n = V_n - V_{n-1}$ (or $V_n - V_{n+1}$).
   Then the sum $S_n = \sum_{r=1}^n T_r = V_n - V_0$ (all intermediate terms cancel).
2. **Partial Fractions in Series**:
   $$T_r = \frac{1}{r(r+1)(r+2)\cdots(r+k)} = \frac{1}{k}\left[\frac{1}{r(r+1)\cdots(r+k-1)} - \frac{1}{(r+1)\cdots(r+k)}\right]$$
3. **Method of Differences**:
   When consecutive differences of terms $T_{r+1} - T_r$ form an AP or GP:
   $$S_n = T_1 + T_2 + T_3 + \dots + T_n$$
   $$S_n = \phantom{T_1 +{}} T_1 + T_2 + \dots + T_{n-1} + T_n$$
   Subtracting gives $0 = T_1 + (T_2 - T_1) + (T_3 - T_2) + \dots + (T_n - T_{n-1}) - T_n \implies T_n = T_1 + \sum_{k=1}^{n-1} \Delta T_k$.
"""),
    ("permutations_and_combinations", "Division and distribution into groups", r"""
Grouping and Distribution of Objects in Combinatorics:

1. **Division into Unequal Groups**:
   - Number of ways to divide $(m + n + p)$ distinct objects into three unequal groups of size $m, n, p$ is:
     $$\frac{(m+n+p)!}{m! \, n! \, p!}$$
   - If distributed to 3 distinct persons: multiply by $3!$.
2. **Division into Equal Groups**:
   - Number of ways to divide $3m$ distinct objects into 3 equal groups of size $m$:
     $$\frac{(3m)!}{(m!)^3 \times 3!}$$
     (divided by $3!$ because the 3 groups have the same size and are unordered).
   - If distributed to 3 distinct persons: $\frac{(3m)!}{(m!)^3 \times 3!} \times 3! = \frac{(3m)!}{(m!)^3}$.
3. **Identical Objects Distribution (Beggar's Method / Stars and Bars)**:
   - Number of non-negative integer solutions to $x_1 + x_2 + \dots + x_r = n$ ($x_i \ge 0$):
     $$\binom{n + r - 1}{r - 1}$$
   - Number of positive integer solutions ($x_i \ge 1$):
     $$\binom{n - 1}{r - 1}$$
"""),
    ("permutations_and_combinations", "Derangements and inclusion-exclusion principle", r"""
Derangements and Principle of Inclusion and Exclusion (PIE):

- **Derangements ($D_n$ or $!n$)**: Number of permutations of $n$ distinct objects such that no object appears in its original position:
  $$D_n = n! \left(1 - \frac{1}{1!} + \frac{1}{2!} - \frac{1}{3!} + \dots + \frac{(-1)^n}{n!}\right) = \left\lfloor \frac{n!}{e} + \frac{1}{2} \right\rfloor$$
  - Recurrence relation: $D_n = (n-1)(D_{n-1} + D_{n-2}) = n D_{n-1} + (-1)^n$.
  - Base values: $D_1 = 0$, $D_2 = 1$, $D_3 = 2$, $D_4 = 9$, $D_5 = 44$, $D_6 = 265$.
- **Partial Derangements**: Number of permutations where exactly $r$ objects out of $n$ occupy their original positions:
  $$\binom{n}{r} D_{n-r}$$
- **Principle of Inclusion and Exclusion (PIE)**:
  $$|A_1 \cup A_2 \cup \dots \cup A_n| = \sum |A_i| - \sum |A_i \cap A_j| + \sum |A_i \cap A_j \cap A_k| - \dots + (-1)^{n-1} |A_1 \cap \dots \cap A_n|$$
"""),
    ("binomial_theorem", "Multinomial theorem and divisibility problems", r"""
Multinomial Theorem and Binomial Divisibility:

- **Multinomial Expansion**: For positive integer $n$:
  $$(x_1 + x_2 + \dots + x_k)^n = \sum_{r_1 + r_2 + \dots + r_k = n} \frac{n!}{r_1! \, r_2! \cdots r_k!} x_1^{r_1} x_2^{r_2} \cdots x_k^{r_k}$$
  - Total number of terms in expansion: $\binom{n + k - 1}{k - 1}$.
  - Greatest coefficient: occurs when all $r_i$ are as nearly equal as possible ($r_i = \lfloor n/k \rfloor$ or $\lceil n/k \rceil$).
- **Divisibility and Remainder Problems**:
  - To find remainder of $a^n$ divided by $b$: express $a = mb + r$ or $a = mb \pm 1$.
  - Expansion: $(1 + x)^n = 1 + nx + \frac{n(n-1)}{2}x^2 + \dots + x^n$.
  - To test divisibility by $x^2$: truncate higher order terms $\mathcal{O}(x^2)$.
"""),
    ("matrices", "Cayley-Hamilton theorem and powers of matrices", r"""
Cayley-Hamilton Theorem and Matrix Powers:

- **Cayley-Hamilton Theorem**: Every square matrix $A$ satisfies its own characteristic equation:
  $$|A - \lambda I| = 0 \implies \lambda^n + c_{n-1}\lambda^{n-1} + \dots + c_1\lambda + c_0 = 0$$
  Substituting $\lambda = A$:
  $$A^n + c_{n-1}A^{n-1} + \dots + c_1 A + c_0 I = O$$
- **Applications**:
  - Expressing high powers $A^m$ ($m > n$) as a polynomial in $A$ of degree at most $n-1$.
  - Calculating inverse: Multiplying by $A^{-1}$:
    $$A^{-1} = -\frac{1}{c_0}\left(A^{n-1} + c_{n-1}A^{n-2} + \dots + c_1 I\right) \quad (\text{if } c_0 = (-1)^n |A| \ne 0)$$
- **Trace and Determinant of $2 \times 2$ matrix**:
  $$A^2 - \text{tr}(A) A + |A| I = O$$
"""),
    ("trigonometric_functions", "Solution of Triangles (Properties of Triangles)", r"""
Properties of Triangles (Solution of Triangles - SOT) for triangle $ABC$ with sides $a, b, c$:

- **Sine Rule**: $\frac{a}{\sin A} = \frac{b}{\sin B} = \frac{c}{\sin C} = 2R$ ($R$ = circumradius).
- **Cosine Rule**: $\cos A = \frac{b^2 + c^2 - a^2}{2bc}$, $\cos B = \frac{c^2 + a^2 - b^2}{2ca}$, $\cos C = \frac{a^2 + b^2 - c^2}{2ab}$.
- **Projection Formulae**: $a = b\cos C + c\cos B$, $b = c\cos A + a\cos C$, $c = a\cos B + b\cos A$.
- **Napier's Analogy (Tangent Rule)**:
  $$\tan\left(\frac{B - C}{2}\right) = \frac{b - c}{b + c}\cot\left(\frac{A}{2}\right)$$
- **Half-Angle Formulas** ($s = \frac{a+b+c}{2}$):
  $$\sin\left(\frac{A}{2}\right) = \sqrt{\frac{(s-b)(s-c)}{bc}},\quad \cos\left(\frac{A}{2}\right) = \sqrt{\frac{s(s-a)}{bc}},\quad \tan\left(\frac{A}{2}\right) = \sqrt{\frac{(s-b)(s-c)}{s(s-a)}} = \frac{r}{s-a}$$
- **Area of Triangle ($\Delta$)**:
  $$\Delta = \frac{1}{2}ab\sin C = \frac{abc}{4R} = rs = \sqrt{s(s-a)(s-b)(s-c)}$$
- **Radii of Associated Circles**:
  - Circumradius: $R = \frac{a}{2\sin A} = \frac{abc}{4\Delta}$.
  - Inradius: $r = \frac{\Delta}{s} = (s-a)\tan(A/2) = 4R\sin(A/2)\sin(B/2)\sin(C/2)$.
  - Exradii (escribed circles): $r_1 = \frac{\Delta}{s-a} = s\tan(A/2) = 4R\sin(A/2)\cos(B/2)\cos(C/2)$ (similarly $r_2, r_3$).
  - Relations: $\frac{1}{r_1} + \frac{1}{r_2} + \frac{1}{r_3} = \frac{1}{r}$, $r_1 + r_2 + r_3 - r = 4R$.
"""),
    ("inverse_trigonometric_functions", "Inverse trigonometric functions and properties", r"""
Inverse Trigonometric Functions (ITF) Identities and Series:

- **Principal Value Branches**:
  - $\sin^{-1}x \in [-\pi/2, \pi/2]$, $\cos^{-1}x \in [0, \pi]$, $\tan^{-1}x \in (-\pi/2, \pi/2)$, $\cot^{-1}x \in (0, \pi)$.
  - $\sin^{-1}(-x) = -\sin^{-1}x$, $\tan^{-1}(-x) = -\tan^{-1}x$.
  - $\cos^{-1}(-x) = \pi - \cos^{-1}x$, $\cot^{-1}(-x) = \pi - \cot^{-1}x$, $\sec^{-1}(-x) = \pi - \sec^{-1}x$.
- **Complementary Angles**:
  $$\sin^{-1}x + \cos^{-1}x = \frac{\pi}{2},\quad \tan^{-1}x + \cot^{-1}x = \frac{\pi}{2},\quad \sec^{-1}x + \csc^{-1}x = \frac{\pi}{2}$$
- **Addition Formulas**:
  $$\tan^{-1}x + \tan^{-1}y = \begin{cases} \tan^{-1}\left(\frac{x+y}{1-xy}\right) & xy < 1 \\ \pi + \tan^{-1}\left(\frac{x+y}{1-xy}\right) & x > 0, y > 0, xy > 1 \\ -\pi + \tan^{-1}\left(\frac{x+y}{1-xy}\right) & x < 0, y < 0, xy > 1 \end{cases}$$
- **Summation of Telescoping Inverse Tangent Series**:
  $$\tan^{-1}\left(\frac{a_{n} - a_{n-1}}{1 + a_n a_{n-1}}\right) = \tan^{-1}a_n - \tan^{-1}a_{n-1}$$
"""),
    ("continuity_and_differentiability", "Method of Differentiation (MOD)", r"""
Advanced Differentiation Methods (Method of Differentiation - MOD):

1. **Logarithmic Differentiation**: For $y = [f(x)]^{g(x)}$:
   $$\ln y = g(x)\ln f(x) \implies \frac{dy}{dx} = [f(x)]^{g(x)}\left[g'(x)\ln f(x) + \frac{g(x)f'(x)}{f(x)}\right]$$
2. **Parametric Differentiation**: If $x = f(t), y = g(t)$:
   $$\frac{dy}{dx} = \frac{g'(t)}{f'(t)},\quad \frac{d^2y}{dx^2} = \frac{d}{dt}\left(\frac{dy}{dx}\right) \frac{dt}{dx} = \frac{f'(t)g''(t) - g'(t)f''(t)}{[f'(t)]^3}$$
   (Crucial: never differentiate $d^2y/dt^2$ by $d^2x/dt^2$).
3. **Differentiation of Determinants**:
   $$\frac{d}{dx} \begin{vmatrix} f_1 & f_2 & f_3 \\ g_1 & g_2 & g_3 \\ h_1 & h_2 & h_3 \end{vmatrix} = \begin{vmatrix} f_1' & f_2' & f_3' \\ g_1 & g_2 & g_3 \\ h_1 & h_2 & h_3 \end{vmatrix} + \begin{vmatrix} f_1 & f_2 & f_3 \\ g_1' & g_2' & g_3' \\ h_1 & h_2 & h_3 \end{vmatrix} + \begin{vmatrix} f_1 & f_2 & f_3 \\ g_1 & g_2 & g_3 \\ h_1' & h_2' & h_3' \end{vmatrix}$$
4. **Differentiation of one function w.r.t another**:
   $$\frac{d(f(x))}{d(g(x))} = \frac{f'(x)}{g'(x)}$$
"""),
    ("integrals", "Leibniz rule of differentiation under integral sign", r"""
Leibniz Integral Rule and Definite Integrals as Limits of Sums:

- **Leibniz Rule (Newton-Leibniz Formula)**:
  $$\frac{d}{dx}\left[\int_{\psi(x)}^{\phi(x)} f(x, t)\, dt\right] = f(x, \phi(x))\cdot \phi'(x) - f(x, \psi(x))\cdot \psi'(x) + \int_{\psi(x)}^{\phi(x)} \frac{\partial f}{\partial x}(x, t)\, dt$$
  For $f(t)$ independent of $x$: $\frac{d}{dx}\int_{\psi(x)}^{\phi(x)} f(t)\, dt = f(\phi(x))\phi'(x) - f(\psi(x))\psi'(x)$.
- **Definite Integral as Limit of a Sum (Riemann Sum)**:
  $$\lim_{n \to \infty} \sum_{r=1}^{pn} \frac{1}{n} f\left(\frac{r}{n}\right) = \int_0^p f(x)\, dx$$
  Steps to convert:
  1. Replace $\frac{r}{n}$ by $x$.
  2. Replace $\frac{1}{n}$ by $dx$.
  3. Lower limit = $\lim_{n \to \infty} \frac{r_{\min}}{n}$.
  4. Upper limit = $\lim_{n \to \infty} \frac{r_{\max}}{n}$.
"""),
    ("straight_lines", "Pair of Straight Lines", r"""
Pair of Straight Lines and Homogenization:

1. **Homogeneous Equation of 2nd Degree**: $ax^2 + 2hxy + by^2 = 0$ represents two lines passing through the origin.
   - Lines are real and distinct if $h^2 > ab$, coincident if $h^2 = ab$, imaginary if $h^2 < ab$.
   - Angle $\theta$ between the lines: $\tan\theta = \left|\frac{2\sqrt{h^2 - ab}}{a + b}\right|$.
   - Lines are perpendicular if $a + b = 0$ (coefficient of $x^2$ + coefficient of $y^2 = 0$).
   - Combined equation of angle bisectors:
     $$\frac{x^2 - y^2}{a - b} = \frac{xy}{h}$$
2. **General Equation of 2nd Degree**: $ax^2 + 2hxy + by^2 + 2gx + 2fy + c = 0$ represents a pair of straight lines iff:
   $$\Delta = \begin{vmatrix} a & h & g \\ h & b & f \\ g & f & c \end{vmatrix} = abc + 2fgh - af^2 - bg^2 - ch^2 = 0 \quad\text{and}\quad h^2 \ge ab$$
   - Point of intersection: solve $\frac{\partial F}{\partial x} = 0$ and $\frac{\partial F}{\partial y} = 0$.
3. **Homogenization**:
   To find equation of pair of lines joining the origin to intersection points of curve $f(x, y) = 0$ and line $lx + my = 1$:
   Multiply terms of degree 1 by $(lx + my)$ and terms of degree 0 by $(lx + my)^2$ to make every term homogeneous of degree 2.
"""),
    ("conic_sections", "Family of circles, common chord and radical axis", r"""
System of Circles, Radical Axis and Orthogonality:

- **Family of Circles**:
  - Through intersections of $S = 0$ and line $L = 0$: $S + \lambda L = 0$ ($\lambda \in \mathbb{R}$).
  - Through intersections of two circles $S_1 = 0$ and $S_2 = 0$: $S_1 + \lambda S_2 = 0$ ($\lambda \ne -1$).
  - Touching line $L = 0$ at $(x_1, y_1)$: $(x - x_1)^2 + (y - y_1)^2 + \lambda L = 0$.
- **Radical Axis**:
  - The locus of a point from which lengths of tangents to two non-concentric circles $S_1 = 0$ and $S_2 = 0$ are equal:
    $$S_1 - S_2 = 0$$
  - Radical axis is always perpendicular to the line joining the centres of the two circles.
  - If the circles intersect, radical axis is their **common chord**.
  - If they touch, it is their **common tangent**.
- **Orthogonality of Two Circles**:
  Two circles $x^2 + y^2 + 2g_1x + 2f_1y + c_1 = 0$ and $x^2 + y^2 + 2g_2x + 2f_2y + c_2 = 0$ cut orthogonally iff:
  $$2g_1 g_2 + 2f_1 f_2 = c_1 + c_2$$
"""),
    ("vector_algebra", "Scalar and vector triple products", r"""
Scalar and Vector Triple Products in 3D Vectors:

- **Scalar Triple Product (Box Product $[\vec{a}\;\vec{b}\;\vec{c}]$)**:
  $$[\vec{a}\;\vec{b}\;\vec{c}] = \vec{a} \cdot (\vec{b} \times \vec{c}) = \begin{vmatrix} a_1 & a_2 & a_3 \\ b_1 & b_2 & b_3 \\ c_1 & c_2 & c_3 \end{vmatrix}$$
  - Represents the volume of parallelepiped with coterminous edges $\vec{a}, \vec{b}, \vec{c}$.
  - Volume of tetrahedron with coterminous edges $\vec{a}, \vec{b}, \vec{c}$ is $\frac{1}{6}|[\vec{a}\;\vec{b}\;\vec{c}]|$.
  - Cyclic permutation invariance: $[\vec{a}\;\vec{b}\;\vec{c}] = [\vec{b}\;\vec{c}\;\vec{a}] = [\vec{c}\;\vec{a}\;\vec{b}] = -[\vec{b}\;\vec{a}\;\vec{c}]$.
  - Condition for coplanarity of three vectors: $[\vec{a}\;\vec{b}\;\vec{c}] = 0$.
- **Vector Triple Product**:
  $$\vec{a} \times (\vec{b} \times \vec{c}) = (\vec{a} \cdot \vec{c})\vec{b} - (\vec{a} \cdot \vec{b})\vec{c} \quad\text{("BAC - CAB rule")}$$
  - $(\vec{a} \times \vec{b}) \times \vec{c} = (\vec{a} \cdot \vec{c})\vec{b} - (\vec{b} \cdot \vec{c})\vec{a}$.
  - Note that $\vec{a} \times (\vec{b} \times \vec{c}) \ne (\vec{a} \times \vec{b}) \times \vec{c}$ in general.
- **Lagrange's Identity**:
  $$(\vec{a} \times \vec{b}) \cdot (\vec{c} \times \vec{d}) = (\vec{a} \cdot \vec{c})(\vec{b} \cdot \vec{d}) - (\vec{a} \cdot \vec{d})(\vec{b} \cdot \vec{c})$$
"""),
    ("probability_12", "Bayes' theorem and total probability", r"""
Law of Total Probability and Bayes' Theorem:

- **Partition of Sample Space**: Events $E_1, E_2, \dots, E_n$ form a partition of $S$ if they are pairwise disjoint ($E_i \cap E_j = \emptyset$ for $i \ne j$), exhaustive ($\bigcup E_i = S$), and $P(E_i) > 0$.
- **Theorem on Total Probability**: For any event $A \subset S$:
  $$P(A) = \sum_{i=1}^n P(E_i) P(A | E_i)$$
- **Bayes' Theorem (Inverse Probability)**:
  Probability of hypothesis $E_k$ given that event $A$ has occurred:
  $$P(E_k | A) = \frac{P(E_k) P(A | E_k)}{\sum_{i=1}^n P(E_i) P(A | E_i)}$$
- **Bernoulli Trials and Binomial Distribution**:
  For $n$ independent trials with probability of success $p$ and failure $q = 1-p$:
  $$P(X = r) = \binom{n}{r} p^r q^{n-r},\quad r = 0, 1, 2, \dots, n$$
  - Mean $\mu = np$, Variance $\sigma^2 = npq$ (note $\text{Var} < \text{Mean}$).
"""),
]

