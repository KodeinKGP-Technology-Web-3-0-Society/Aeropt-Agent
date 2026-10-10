"""Dimensional analysis over a physics expression.

Why this exists: dimensions catch a whole class of wrong answers instantly and with
certainty. `v = \\sqrt{2 g h^2}` is wrong and you can know that without solving
anything. A tutor that checks dimensions before it speaks is a tutor that stops being
confidently wrong about the kind of mistake students actually make.

Implemented directly rather than with a units library, for two reasons: exponents stay
exact (`Fraction`, so `sqrt` is `^1/2`, not `0.4999999`), and the error messages can
say *which* term disagreed, which is the part a student needs.

Ambiguity is handled by refusing to guess. `T` is period or temperature, `I` is current
or moment of inertia, `P` is power or pressure or momentum — physics reuses letters. A
symbol on that list without an explicit meaning makes the check return
`needs_disambiguation` instead of a verdict, because a dimension check that quietly
picks the wrong meaning is worse than no check at all.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Mapping

from utils.mathexpr import (
    DIMENSIONLESS_FUNCTIONS,
    ExpressionError,
    FUNCTIONS,
    CONSTANTS,
    parse,
    rational,
)

#: Base dimensions, in the order they are stored.
BASE = ("M", "L", "T", "I", "K", "N")


class DimensionError(ExpressionError):
    """A dimensional inconsistency, or a symbol whose dimension is unknown."""


@dataclass(frozen=True)
class Dim:
    """A dimension as exact exponents of mass, length, time, current, temperature, mole."""

    exponents: tuple[Fraction, ...] = (Fraction(0),) * 6

    @classmethod
    def of(cls, M=0, L=0, T=0, I=0, K=0, N=0) -> "Dim":  # noqa: E741 — I is current
        return cls(tuple(Fraction(x) for x in (M, L, T, I, K, N)))

    def __mul__(self, other: "Dim") -> "Dim":
        return Dim(tuple(a + b for a, b in zip(self.exponents, other.exponents)))

    def __truediv__(self, other: "Dim") -> "Dim":
        return Dim(tuple(a - b for a, b in zip(self.exponents, other.exponents)))

    def __pow__(self, power: Fraction | int) -> "Dim":
        factor = Fraction(power)
        return Dim(tuple(a * factor for a in self.exponents))

    @property
    def dimensionless(self) -> bool:
        return all(e == 0 for e in self.exponents)

    def __str__(self) -> str:
        if self.dimensionless:
            return "1 (dimensionless)"
        parts = []
        for name, exp in zip(BASE, self.exponents):
            if exp == 0:
                continue
            parts.append(name if exp == 1 else f"{name}^{exp}")
        return " ".join(parts)

    def latex(self) -> str:
        if self.dimensionless:
            return "1"
        parts = []
        for name, exp in zip(BASE, self.exponents):
            if exp == 0:
                continue
            parts.append(name if exp == 1 else f"{name}^{{{exp}}}")
        return r" \, ".join(parts)

    def named(self) -> str:
        """The physical quantity with these dimensions, when it has a familiar name."""
        return _NAMED.get(self, "")

    def describe(self) -> str:
        if self.dimensionless:
            return str(self)
        name = self.named()
        return f"{self} ({name})" if name else str(self)


DIMENSIONLESS = Dim.of()
MASS = Dim.of(M=1)
LENGTH = Dim.of(L=1)
TIME = Dim.of(T=1)
CURRENT = Dim.of(I=1)
TEMPERATURE = Dim.of(K=1)
AMOUNT = Dim.of(N=1)

AREA = Dim.of(L=2)
VOLUME = Dim.of(L=3)
VELOCITY = Dim.of(L=1, T=-1)
ACCELERATION = Dim.of(L=1, T=-2)
FORCE = Dim.of(M=1, L=1, T=-2)
ENERGY = Dim.of(M=1, L=2, T=-2)
POWER = Dim.of(M=1, L=2, T=-3)
MOMENTUM = Dim.of(M=1, L=1, T=-1)
PRESSURE = Dim.of(M=1, L=-1, T=-2)
DENSITY = Dim.of(M=1, L=-3)
FREQUENCY = Dim.of(T=-1)
ANGULAR_VELOCITY = Dim.of(T=-1)
MOMENT_OF_INERTIA = Dim.of(M=1, L=2)
ANGULAR_MOMENTUM = Dim.of(M=1, L=2, T=-1)
SPRING_CONSTANT = Dim.of(M=1, T=-2)
CHARGE = Dim.of(I=1, T=1)
VOLTAGE = Dim.of(M=1, L=2, T=-3, I=-1)
RESISTANCE = Dim.of(M=1, L=2, T=-3, I=-2)
CAPACITANCE = Dim.of(M=-1, L=-2, T=4, I=2)
INDUCTANCE = Dim.of(M=1, L=2, T=-2, I=-2)
MAGNETIC_FIELD = Dim.of(M=1, T=-2, I=-1)
ELECTRIC_FIELD = Dim.of(M=1, L=1, T=-3, I=-1)
MAGNETIC_FLUX = Dim.of(M=1, L=2, T=-2, I=-1)

_NAMED: dict[Dim, str] = {
    DIMENSIONLESS: "dimensionless",
    MASS: "mass",
    LENGTH: "length",
    TIME: "time",
    CURRENT: "current",
    TEMPERATURE: "temperature",
    AREA: "area",
    VOLUME: "volume",
    VELOCITY: "velocity",
    ACCELERATION: "acceleration",
    FORCE: "force",
    ENERGY: "energy / work / torque",
    POWER: "power",
    MOMENTUM: "momentum / impulse",
    PRESSURE: "pressure / stress / energy density",
    DENSITY: "density",
    FREQUENCY: "frequency / angular velocity / rate",
    MOMENT_OF_INERTIA: "moment of inertia",
    ANGULAR_MOMENTUM: "angular momentum",
    SPRING_CONSTANT: "spring constant / surface tension",
    CHARGE: "charge",
    VOLTAGE: "potential difference / emf",
    RESISTANCE: "resistance",
    CAPACITANCE: "capacitance",
    INDUCTANCE: "inductance",
    MAGNETIC_FIELD: "magnetic field",
    ELECTRIC_FIELD: "electric field",
    MAGNETIC_FLUX: "magnetic flux",
    Dim.of(L=1, T=-3): "jerk",
    Dim.of(M=1, L=-1, T=-1): "dynamic viscosity",
    Dim.of(L=2, T=-1): "kinematic viscosity / diffusivity",
    Dim.of(M=1, L=2, T=-2, K=-1): "heat capacity / entropy",
    Dim.of(L=2, T=-2, K=-1): "specific heat capacity",
    Dim.of(T=-2): "angular acceleration",
}

#: Symbols whose meaning is conventional enough to assume.
SYMBOL_DIMENSIONS: dict[str, Dim] = {
    # mass
    "m": MASS, "M": MASS, "dm": MASS, "mu_mass": MASS,
    # length
    "x": LENGTH, "y": LENGTH, "z": LENGTH, "r": LENGTH, "d": LENGTH, "h": LENGTH,
    "s": LENGTH, "l": LENGTH, "L_len": LENGTH, "R": LENGTH, "b": LENGTH,
    "lambda": LENGTH, "dx": LENGTH, "dr": LENGTH, "u_len": LENGTH,
    # time
    "t": TIME, "dt": TIME, "tau": TIME,
    # kinematics
    "v": VELOCITY, "u": VELOCITY, "c": VELOCITY, "v_0": VELOCITY, "v0": VELOCITY,
    "u_0": VELOCITY, "v_cm": VELOCITY, "dv": VELOCITY,
    "a": ACCELERATION, "g": ACCELERATION, "a_cm": ACCELERATION, "a_c": ACCELERATION,
    # dynamics
    "F": FORCE, "f": FORCE, "N": FORCE, "W_force": FORCE, "T_tension": FORCE,
    "p": MOMENTUM, "J_impulse": MOMENTUM,
    "W": ENERGY, "E_energy": ENERGY, "U": ENERGY, "K": ENERGY, "KE": ENERGY,
    "PE": ENERGY, "Q_heat": ENERGY, "tau_torque": ENERGY,
    # rotation
    "omega": ANGULAR_VELOCITY, "omega_0": ANGULAR_VELOCITY,
    "alpha_ang": Dim.of(T=-2),
    "L_angmom": ANGULAR_MOMENTUM, "I_inertia": MOMENT_OF_INERTIA,
    # angles and ratios — dimensionless
    "theta": DIMENSIONLESS, "phi": DIMENSIONLESS, "alpha": DIMENSIONLESS,
    "beta": DIMENSIONLESS, "gamma": DIMENSIONLESS, "delta": DIMENSIONLESS,
    "mu": DIMENSIONLESS, "mu_s": DIMENSIONLESS, "mu_k": DIMENSIONLESS,
    "e_restitution": DIMENSIONLESS, "n": DIMENSIONLESS, "eta": DIMENSIONLESS,
    "epsilon": DIMENSIONLESS,
    # materials / fluids
    "rho": DENSITY, "sigma_surface": SPRING_CONSTANT, "k": SPRING_CONSTANT,
    "Y": PRESSURE, "eta_visc": Dim.of(M=1, L=-1, T=-1),
    # waves
    "nu": FREQUENCY, "f_freq": FREQUENCY, "k_wave": Dim.of(L=-1),
    # electricity and magnetism
    "q": CHARGE, "Q": CHARGE, "V": VOLTAGE, "emf": VOLTAGE,
    "R_resistance": RESISTANCE, "C": CAPACITANCE, "B": MAGNETIC_FIELD,
    "E_field": ELECTRIC_FIELD, "Phi": MAGNETIC_FLUX, "L_inductance": INDUCTANCE,
}

#: Symbols physics genuinely overloads. The check refuses to pick for you.
AMBIGUOUS_SYMBOLS: dict[str, dict[str, Dim]] = {
    "T": {"period / time": TIME, "temperature": TEMPERATURE, "tension (force)": FORCE},
    "I": {"current": CURRENT, "moment of inertia": MOMENT_OF_INERTIA,
          "intensity (power per area)": Dim.of(M=1, T=-3)},
    "P": {"power": POWER, "pressure": PRESSURE, "momentum": MOMENTUM},
    "E": {"energy": ENERGY, "electric field": ELECTRIC_FIELD},
    "A": {"area": AREA, "amplitude (length)": LENGTH},
    "L": {"length": LENGTH, "angular momentum": ANGULAR_MOMENTUM,
          "inductance": INDUCTANCE},
    "sigma": {"surface charge density": Dim.of(I=1, T=1, L=-2),
              "stress (pressure)": PRESSURE, "surface tension": SPRING_CONSTANT,
              "conductivity": Dim.of(M=-1, L=-3, T=3, I=2)},
}


def dimension_of(
    expression: str | ast.Expression,
    symbol_dimensions: Mapping[str, Dim] | None = None,
) -> Dim:
    """The dimension of one expression. Raises DimensionError on an inconsistency."""
    tree = parse(expression) if isinstance(expression, str) else expression
    table: dict[str, Dim] = dict(SYMBOL_DIMENSIONS)
    if symbol_dimensions:
        table.update(symbol_dimensions)
    return _walk(tree.body, table)


def _walk(node: ast.AST, table: Mapping[str, Dim]) -> Dim:
    if isinstance(node, ast.Constant):
        return DIMENSIONLESS

    if isinstance(node, ast.Name):
        if node.id in table:
            return table[node.id]
        if node.id in CONSTANTS:
            return DIMENSIONLESS
        if node.id in AMBIGUOUS_SYMBOLS:
            meanings = ", ".join(AMBIGUOUS_SYMBOLS[node.id])
            raise DimensionError(
                f"'{node.id}' is ambiguous in physics — it could be {meanings}. "
                f"Say which you mean."
            )
        raise DimensionError(
            f"No dimension known for '{node.id}'. Give it explicitly, e.g. "
            f"{node.id}=velocity."
        )

    if isinstance(node, ast.UnaryOp):
        return _walk(node.operand, table)

    if isinstance(node, ast.BinOp):
        op = type(node.op)
        if op is ast.Mult:
            return _walk(node.left, table) * _walk(node.right, table)
        if op in (ast.Div, ast.FloorDiv):
            return _walk(node.left, table) / _walk(node.right, table)
        if op is ast.Pow:
            base = _walk(node.left, table)
            exponent = _walk(node.right, table)
            if not exponent.dimensionless:
                raise DimensionError(
                    f"An exponent must be dimensionless, but this one is {exponent}."
                )
            if base.dimensionless:
                return DIMENSIONLESS
            power = _constant_exponent(node.right)
            if power is None:
                raise DimensionError(
                    f"Cannot raise {base} to a non-constant power — the result would "
                    "not have a fixed dimension."
                )
            return base ** power
        if op in (ast.Add, ast.Sub):
            left = _walk(node.left, table)
            right = _walk(node.right, table)
            if left != right:
                symbol = "+" if op is ast.Add else "-"
                raise DimensionError(
                    f"Cannot {'add' if op is ast.Add else 'subtract'} {left.describe()} "
                    f"and {right.describe()} — the two sides of '{symbol}' must have the "
                    "same dimensions."
                )
            return left
        if op is ast.Mod:
            return _walk(node.left, table)
        raise DimensionError(f"Unsupported operator for dimensions: {op.__name__}")

    if isinstance(node, ast.Call):
        name = node.func.id  # type: ignore[union-attr]
        args = [_walk(a, table) for a in node.args]
        if name == "sqrt":
            return args[0] ** Fraction(1, 2)
        if name == "cbrt":
            return args[0] ** Fraction(1, 3)
        if name == "hypot":
            if len({*args}) > 1:
                raise DimensionError(
                    "hypot() combines lengths of the same kind, but the arguments are "
                    + " and ".join(a.describe() for a in args)
                )
            return args[0]
        if name in {"abs", "min", "max", "sum", "round", "floor", "ceil"}:
            if len({*args}) > 1:
                raise DimensionError(
                    f"{name}() needs arguments of one dimension, got "
                    + " and ".join(a.describe() for a in args)
                )
            return args[0]
        if name in DIMENSIONLESS_FUNCTIONS:
            for arg, sub in zip(args, node.args):
                if not arg.dimensionless:
                    raise DimensionError(
                        f"{name}() needs a dimensionless argument, but its argument is "
                        f"{arg.describe()}. In physics this usually means a term is "
                        "missing a constant that carries the units."
                    )
            return DIMENSIONLESS
        if name in FUNCTIONS:
            return args[0] if args else DIMENSIONLESS
        raise DimensionError(f"Unknown function '{name}'.")

    raise DimensionError(f"Unsupported expression element: {type(node).__name__}")


def _constant_exponent(node: ast.AST) -> Fraction | None:
    """The exponent as an exact fraction when it is a literal, else None."""
    if isinstance(node, ast.Constant):
        return rational(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _constant_exponent(node.operand)
        return -inner if inner is not None else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        num = _constant_exponent(node.left)
        den = _constant_exponent(node.right)
        if num is not None and den not in (None, 0):
            return num / den  # type: ignore[operator]
    return None


def unknown_symbols(
    expression: str | ast.Expression,
    symbol_dimensions: Mapping[str, Dim] | None = None,
) -> tuple[set[str], set[str]]:
    """(symbols with no known dimension, symbols that are ambiguous)."""
    from utils.mathexpr import free_symbols

    tree = parse(expression) if isinstance(expression, str) else expression
    table = dict(SYMBOL_DIMENSIONS)
    if symbol_dimensions:
        table.update(symbol_dimensions)
    unknown, ambiguous = set(), set()
    for name in free_symbols(tree):
        if name in table:
            continue
        if name in AMBIGUOUS_SYMBOLS:
            ambiguous.add(name)
        else:
            unknown.add(name)
    return unknown, ambiguous


# ── named quantities and units, for the caller to name a dimension ────────────

QUANTITIES: dict[str, Dim] = {
    "dimensionless": DIMENSIONLESS, "ratio": DIMENSIONLESS, "angle": DIMENSIONLESS,
    "mass": MASS, "length": LENGTH, "distance": LENGTH, "displacement": LENGTH,
    "height": LENGTH, "radius": LENGTH, "wavelength": LENGTH, "amplitude": LENGTH,
    "time": TIME, "period": TIME, "current": CURRENT, "temperature": TEMPERATURE,
    "amount": AMOUNT, "area": AREA, "volume": VOLUME,
    "velocity": VELOCITY, "speed": VELOCITY, "acceleration": ACCELERATION,
    "force": FORCE, "tension": FORCE, "weight": FORCE,
    "energy": ENERGY, "work": ENERGY, "torque": ENERGY, "heat": ENERGY,
    "power": POWER, "momentum": MOMENTUM, "impulse": MOMENTUM,
    "pressure": PRESSURE, "stress": PRESSURE, "density": DENSITY,
    "frequency": FREQUENCY, "angular_velocity": ANGULAR_VELOCITY,
    "angular_acceleration": Dim.of(T=-2),
    "moment_of_inertia": MOMENT_OF_INERTIA, "angular_momentum": ANGULAR_MOMENTUM,
    "spring_constant": SPRING_CONSTANT, "surface_tension": SPRING_CONSTANT,
    "charge": CHARGE, "voltage": VOLTAGE, "potential": VOLTAGE, "emf": VOLTAGE,
    "resistance": RESISTANCE, "capacitance": CAPACITANCE, "inductance": INDUCTANCE,
    "magnetic_field": MAGNETIC_FIELD, "electric_field": ELECTRIC_FIELD,
    "magnetic_flux": MAGNETIC_FLUX, "viscosity": Dim.of(M=1, L=-1, T=-1),
}

#: unit name -> (multiplier to SI, dimension). Compound units are parsed as
#: expressions over this table, so "kg*m/s**2" works without being listed.
UNITS: dict[str, tuple[float, Dim]] = {
    "m": (1.0, LENGTH), "km": (1e3, LENGTH), "cm": (1e-2, LENGTH),
    "mm": (1e-3, LENGTH), "um": (1e-6, LENGTH), "nm": (1e-9, LENGTH),
    "angstrom": (1e-10, LENGTH), "inch": (0.0254, LENGTH), "ft": (0.3048, LENGTH),
    "mile": (1609.344, LENGTH), "au": (1.495978707e11, LENGTH),
    "s": (1.0, TIME), "ms": (1e-3, TIME), "us": (1e-6, TIME), "ns": (1e-9, TIME),
    "min": (60.0, TIME), "hour": (3600.0, TIME), "h": (3600.0, TIME),
    "day": (86400.0, TIME), "year": (3.15576e7, TIME),
    "kg": (1.0, MASS), "g": (1e-3, MASS), "mg": (1e-6, MASS),
    "tonne": (1e3, MASS), "amu": (1.66053906660e-27, MASS),
    "N": (1.0, FORCE), "kN": (1e3, FORCE), "dyne": (1e-5, FORCE),
    "kgf": (9.80665, FORCE),
    "J": (1.0, ENERGY), "kJ": (1e3, ENERGY), "erg": (1e-7, ENERGY),
    "eV": (1.602176634e-19, ENERGY), "keV": (1.602176634e-16, ENERGY),
    "MeV": (1.602176634e-13, ENERGY), "cal": (4.184, ENERGY),
    "kcal": (4184.0, ENERGY), "kWh": (3.6e6, ENERGY),
    "W": (1.0, POWER), "kW": (1e3, POWER), "MW": (1e6, POWER), "hp": (745.6999, POWER),
    "Pa": (1.0, PRESSURE), "kPa": (1e3, PRESSURE), "MPa": (1e6, PRESSURE),
    "bar": (1e5, PRESSURE), "atm": (101325.0, PRESSURE),
    "mmHg": (133.322387415, PRESSURE), "torr": (133.322368421, PRESSURE),
    "C": (1.0, CHARGE), "mC": (1e-3, CHARGE), "uC": (1e-6, CHARGE),
    "nC": (1e-9, CHARGE), "elementary_charge": (1.602176634e-19, CHARGE),
    "A": (1.0, CURRENT), "mA": (1e-3, CURRENT), "uA": (1e-6, CURRENT),
    "V": (1.0, VOLTAGE), "mV": (1e-3, VOLTAGE), "kV": (1e3, VOLTAGE),
    "ohm": (1.0, RESISTANCE), "kohm": (1e3, RESISTANCE), "Mohm": (1e6, RESISTANCE),
    "F": (1.0, CAPACITANCE), "uF": (1e-6, CAPACITANCE), "nF": (1e-9, CAPACITANCE),
    "pF": (1e-12, CAPACITANCE),
    "H": (1.0, INDUCTANCE), "mH": (1e-3, INDUCTANCE),
    "T": (1.0, MAGNETIC_FIELD), "mT": (1e-3, MAGNETIC_FIELD),
    "gauss": (1e-4, MAGNETIC_FIELD), "Wb": (1.0, MAGNETIC_FLUX),
    "Hz": (1.0, FREQUENCY), "kHz": (1e3, FREQUENCY), "MHz": (1e6, FREQUENCY),
    "rpm": (1.0 / 60.0, FREQUENCY), "rad": (1.0, DIMENSIONLESS),
    "deg": (0.017453292519943295, DIMENSIONLESS),
    "K": (1.0, TEMPERATURE), "mol": (1.0, AMOUNT),
    "litre": (1e-3, VOLUME), "L": (1e-3, VOLUME), "mL": (1e-6, VOLUME),
    "knot": (0.514444, VELOCITY),
}


def parse_unit(text: str) -> tuple[float, Dim]:
    """`(multiplier to SI, dimension)` for a unit string like 'km/h' or 'kg*m/s**2'."""
    tree = parse(text)
    return _unit_walk(tree.body)


def _unit_walk(node: ast.AST) -> tuple[float, Dim]:
    if isinstance(node, ast.Constant):
        return float(node.value), DIMENSIONLESS
    if isinstance(node, ast.Name):
        if node.id not in UNITS:
            raise DimensionError(
                f"Unknown unit '{node.id}'. Known units include m, km, s, h, kg, g, N, "
                "J, eV, W, Pa, atm, C, A, V, ohm, F, H, T, Hz, rpm, K, mol."
            )
        return UNITS[node.id]
    if isinstance(node, ast.UnaryOp):
        factor, dim = _unit_walk(node.operand)
        return (factor if isinstance(node.op, ast.UAdd) else -factor), dim
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Mult):
            lf, ld = _unit_walk(node.left)
            rf, rd = _unit_walk(node.right)
            return lf * rf, ld * rd
        if isinstance(node.op, ast.Div):
            lf, ld = _unit_walk(node.left)
            rf, rd = _unit_walk(node.right)
            return lf / rf, ld / rd
        if isinstance(node.op, ast.Pow):
            lf, ld = _unit_walk(node.left)
            power = _constant_exponent(node.right)
            if power is None:
                raise DimensionError("A unit exponent must be a constant.")
            return lf ** float(power), ld ** power
    raise DimensionError("A unit must be built from unit names, *, / and powers.")


def resolve_quantity(name: str) -> Dim:
    """Turn 'velocity' or 'm/s' into a dimension."""
    key = (name or "").strip()
    if key in QUANTITIES:
        return QUANTITIES[key]
    lowered = key.lower().replace(" ", "_")
    if lowered in QUANTITIES:
        return QUANTITIES[lowered]
    return parse_unit(key)[1]


def dimension_table(names: Iterable[str]) -> dict[str, Dim]:
    """Caller-supplied overrides: {'T': 'temperature'} -> {'T': Dim}."""
    out: dict[str, Dim] = {}
    for symbol, meaning in dict(names).items():  # type: ignore[arg-type]
        out[symbol] = resolve_quantity(meaning)
    return out
