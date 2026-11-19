#!/usr/bin/env python3
"""research_knowledge.py - the laws the engine should already know.

THE GAP THIS FILLS. research.py can recover a law from data and cannot
recognise it afterwards. Handed the Kepler dataset it returns
T = 6.28319 a^(3/2) mu^(-1/2), which is correct and anonymous - it does not
know it has just rediscovered Kepler's third law, cannot say the exponent 3/2
is the expected one, and cannot tell a genuinely new relationship from a
textbook one. A discovery engine that cannot recognise prior art will announce
Ohm's law as a finding.

So this is a library of named relationships with their exponents, and three
things to do with it:

  identify()      given recovered exponents, name the law - or say plainly
                  that nothing in the library matches, which is the honest
                  precondition for calling something new
  test_law()      check whether a named law HOLDS in supplied data, which is
                  hypothesis testing rather than fitting
  expected()      what the exponent should be, so a recovered 2.97 can be
                  compared against a theoretical 3 rather than admired

WHAT IS AND IS NOT IN HERE. Exponents, constants, variables and validity
ranges - the machine-checkable skeleton of each law. NOT the derivations, not
the clause text of any standard, and not anything copied from a copyrighted
source. A power law is a mathematical fact and the exponent in Kepler's third
law belongs to nobody.

EVERY ENTRY CARRIES ITS VALIDITY RANGE, because that is the part people drop.
The Blasius correlation is not wrong outside 3000 < Re < 100000, it is
inapplicable, and a fit that reports 4% error while operating at Re = 10^7 has
not been tested at all.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field

import numpy as np

__all__ = ["Law", "LAWS", "identify", "test_law", "expected", "by_discipline",
           "search"]


@dataclass
class Law:
    name: str
    discipline: str
    formula: str
    target: str
    exponents: dict              # variable -> exponent
    constant: float | None = None
    constant_note: str = ""
    validity: str = ""
    source: str = ""
    note: str = ""

    def predict(self, data: dict) -> np.ndarray | None:
        if self.constant is None:
            return None
        out = np.full(len(next(iter(data.values()))), float(self.constant))
        for v, e in self.exponents.items():
            if v not in data:
                return None
            out = out * np.asarray(data[v], dtype=float) ** e
        return out

    def __str__(self) -> str:
        return f"{self.name} ({self.discipline}): {self.formula}"


def L(*a, **k) -> Law:
    return Law(*a, **k)


LAWS: list[Law] = [
    # ================= mechanics of materials =================
    L("Euler buckling", "mechanics", "P_cr = pi^2 E I / (K L)^2", "P_cr",
      {"E": 1, "I": 1, "L": -2}, math.pi ** 2, "pi^2 / K^2 for the end condition",
      "slender columns only, above the transition slenderness",
      note="below the transition slenderness the column yields before it "
           "buckles and this overpredicts badly"),
    L("cantilever tip deflection", "mechanics", "d = P L^3 / (3 E I)", "d",
      {"P": 1, "L": 3, "E": -1, "I": -1}, 1 / 3, "1/3",
      "small deflection, linear elastic"),
    L("simply supported central point load", "mechanics",
      "d = P L^3 / (48 E I)", "d", {"P": 1, "L": 3, "E": -1, "I": -1}, 1 / 48,
      "1/48", "small deflection, linear elastic"),
    L("simply supported UDL", "mechanics", "d = 5 w L^4 / (384 E I)", "d",
      {"w": 1, "L": 4, "E": -1, "I": -1}, 5 / 384, "5/384"),
    L("fixed-fixed central point load", "mechanics",
      "d = P L^3 / (192 E I)", "d", {"P": 1, "L": 3, "E": -1, "I": -1},
      1 / 192, "1/192"),
    L("second moment, rectangle", "mechanics", "I = b h^3 / 12", "I",
      {"b": 1, "h": 3}, 1 / 12, "1/12"),
    L("second moment, circle", "mechanics", "I = pi d^4 / 64", "I",
      {"d": 4}, math.pi / 64, "pi/64"),
    L("polar second moment, circle", "mechanics", "J = pi d^4 / 32", "J",
      {"d": 4}, math.pi / 32, "pi/32"),
    L("torsional shear stress", "mechanics", "tau = 16 T / (pi d^3)", "tau",
      {"T": 1, "d": -3}, 16 / math.pi, "16/pi", "solid circular shaft"),
    L("thin cylinder hoop stress", "mechanics", "sigma = p r / t", "sigma",
      {"p": 1, "r": 1, "t": -1}, 1.0, "1", "t/r <= 0.1",
      note="above t/r = 0.1 use Lame thick-wall theory"),
    L("thin sphere stress", "mechanics", "sigma = p r / (2 t)", "sigma",
      {"p": 1, "r": 1, "t": -1}, 0.5, "1/2", "t/r <= 0.1"),
    L("beam first natural frequency", "mechanics",
      "f1 = (pi/2) sqrt(E I / (rho A)) / L^2", "f1",
      {"E": 0.5, "I": 0.5, "rho": -0.5, "A": -0.5, "L": -2},
      math.pi / 2, "pi/2 for simply supported", "Euler-Bernoulli"),
    L("Hall-Petch", "materials", "sigma_y = sigma_0 + k d^(-1/2)", "sigma_y",
      {"d": -0.5}, None, "k is material specific",
      "polycrystalline metals above about 20 nm grain size",
      note="INVERSE SQUARE ROOT of grain size, not inverse linear. Below "
           "about 20 nm the relation inverts - the inverse Hall-Petch regime"),
    L("Griffith fracture", "materials", "sigma_f = sqrt(2 E gamma / (pi a))",
      "sigma_f", {"E": 0.5, "gamma": 0.5, "a": -0.5}, None,
      "sqrt(2/pi)", "brittle, ideally sharp crack"),
    L("stress intensity factor", "materials", "K = Y sigma sqrt(pi a)", "K",
      {"sigma": 1, "a": 0.5}, None, "Y sqrt(pi), geometry dependent"),
    L("Paris law", "materials", "da/dN = C (dK)^m", "dadN", {"dK": 3.0}, None,
      "C material specific; m is typically 2-4 for metals",
      "region II, stable crack growth"),
    L("Basquin", "materials", "sigma_a = A N^b", "sigma_a", {"N": -0.1}, None,
      "b is typically -0.05 to -0.12", "high cycle fatigue"),
    L("Coffin-Manson", "materials", "de_p/2 = e_f (2N)^c", "de_p", {"N": -0.5},
      None, "c is typically -0.5 to -0.7", "low cycle fatigue"),
    L("Arrhenius creep", "materials", "rate = A exp(-Q/(R T))", "rate", {},
      None, "exponential in 1/T, NOT a power law",
      note="this is not a monomial - a power-law engine cannot recover it and "
           "should say so rather than returning a bad fit"),
    L("Larson-Miller", "materials", "LMP = T (C + log t)", "LMP", {}, None,
      "C ~ 20 for many steels", "creep rupture extrapolation"),

    # ================= fluid mechanics =================
    L("Reynolds number", "fluids", "Re = rho v D / mu", "Re",
      {"rho": 1, "v": 1, "D": 1, "mu": -1}, 1.0, "1"),
    L("Darcy-Weisbach", "fluids", "dp = f (L/D) (rho v^2 / 2)", "dp",
      {"f": 1, "L": 1, "D": -1, "rho": 1, "v": 2}, 0.5, "1/2"),
    L("Hagen-Poiseuille", "fluids", "Q = pi dp r^4 / (8 mu L)", "Q",
      {"dp": 1, "r": 4, "mu": -1, "L": -1}, math.pi / 8, "pi/8",
      "laminar, Re < 2300, fully developed",
      note="the r^4 dependence is why halving a pipe radius costs 16x the flow"),
    L("laminar friction factor", "fluids", "f = 64 / Re", "f", {"Re": -1},
      64.0, "64", "Re < 2300"),
    L("Blasius", "fluids", "f = 0.316 Re^(-1/4)", "f", {"Re": -0.25}, 0.316,
      "0.316", "smooth pipe, 3000 < Re < 100000",
      note="outside that band it is not wrong, it is inapplicable"),
    L("Stokes drag", "fluids", "F = 6 pi mu r v", "F",
      {"mu": 1, "r": 1, "v": 1}, 6 * math.pi, "6 pi", "Re < 1"),
    L("drag force", "fluids", "F = 0.5 Cd rho A v^2", "F",
      {"Cd": 1, "rho": 1, "A": 1, "v": 2}, 0.5, "1/2"),
    L("dynamic pressure", "fluids", "q = 0.5 rho v^2", "q",
      {"rho": 1, "v": 2}, 0.5, "1/2"),
    L("Torricelli", "fluids", "v = sqrt(2 g h)", "v", {"h": 0.5}, None,
      "sqrt(2g)", "inviscid, free discharge"),
    L("Froude number", "fluids", "Fr = v / sqrt(g L)", "Fr",
      {"v": 1, "L": -0.5}, None, "1/sqrt(g)"),
    L("Manning", "fluids", "v = (1/n) R^(2/3) S^(1/2)", "v",
      {"R": 2 / 3, "S": 0.5}, None, "1/n", "open channel, turbulent"),
    L("pump affinity, flow", "fluids", "Q2/Q1 = N2/N1", "Q", {"N": 1}, None, ""),
    L("pump affinity, head", "fluids", "H2/H1 = (N2/N1)^2", "H", {"N": 2},
      None, ""),
    L("pump affinity, power", "fluids", "P2/P1 = (N2/N1)^3", "P", {"N": 3},
      None, "", note="power goes as the CUBE of speed - a 10% speed rise is "
                     "a 33% power rise"),
    L("fan/turbine power in wind", "fluids", "P = 0.5 Cp rho A v^3", "P",
      {"rho": 1, "A": 1, "v": 3}, 0.5, "0.5 Cp",
      "Betz limit caps Cp at 16/27 = 0.593"),

    # ================= heat transfer =================
    L("Fourier conduction", "thermal", "q = k A dT / L", "q",
      {"k": 1, "A": 1, "dT": 1, "L": -1}, 1.0, "1"),
    L("Newton cooling", "thermal", "q = h A dT", "q",
      {"h": 1, "A": 1, "dT": 1}, 1.0, "1"),
    L("Stefan-Boltzmann", "thermal", "q = eps sigma A T^4", "q",
      {"eps": 1, "A": 1, "T": 4}, 5.670374419e-8, "Stefan-Boltzmann constant",
      "radiation heat transfer between a surface and its surroundings",
      note="RADIATION, radiated, radiative, emissive power, blackbody, "
           "grey body. The FOURTH power of ABSOLUTE temperature - a common "
           "error is using Celsius"),
    L("thermal diffusion time", "thermal", "tau ~ L^2 / alpha", "tau",
      {"L": 2, "alpha": -1}, None, "order unity, geometry dependent"),
    L("Biot number", "thermal", "Bi = h L / k", "Bi", {"h": 1, "L": 1, "k": -1},
      1.0, "1", note="Bi < 0.1 justifies the lumped capacitance assumption"),
    L("Fourier number", "thermal", "Fo = alpha t / L^2", "Fo",
      {"alpha": 1, "t": 1, "L": -2}, 1.0, "1"),
    L("Dittus-Boelter", "thermal", "Nu = 0.023 Re^0.8 Pr^0.4", "Nu",
      {"Re": 0.8, "Pr": 0.4}, 0.023, "0.023",
      "turbulent pipe flow, Re > 10000, 0.7 < Pr < 160, heating",
      note="the Pr exponent is 0.4 for heating and 0.3 for cooling"),
    L("laminar flat plate Nusselt", "thermal", "Nu = 0.664 Re^0.5 Pr^(1/3)",
      "Nu", {"Re": 0.5, "Pr": 1 / 3}, 0.664, "0.664", "laminar, Re < 5e5"),
    L("natural convection, vertical plate", "thermal",
      "Nu = 0.59 Ra^(1/4)", "Nu", {"Ra": 0.25}, 0.59, "0.59",
      "laminar, 10^4 < Ra < 10^9"),
    L("fin efficiency parameter", "thermal", "m = sqrt(h P / (k A))", "m",
      {"h": 0.5, "P": 0.5, "k": -0.5, "A": -0.5}, 1.0, "1"),
    L("Wien displacement", "thermal", "lambda_max T = 2.898e-3", "lambda_max",
      {"T": -1}, 2.897771955e-3, "Wien constant, m K"),

    # ================= thermodynamics =================
    L("ideal gas", "thermo", "p V = n R T", "p",
      {"n": 1, "T": 1, "V": -1}, 8.314462618, "R"),
    L("Carnot efficiency", "thermo", "eta = 1 - Tc/Th", "eta", {}, None,
      "not a power law", "reversible cycle only",
      note="an upper bound no real engine reaches"),
    L("isentropic relation", "thermo", "T2/T1 = (p2/p1)^((g-1)/g)", "T",
      {"p": 0.2857}, None, "(gamma-1)/gamma = 0.2857 for air"),
    L("speed of sound", "thermo", "a = sqrt(gamma R T)", "a", {"T": 0.5},
      None, "sqrt(gamma R)"),
    L("compression ratio efficiency", "thermo", "eta = 1 - r^(1-gamma)", "eta",
      {}, None, "Otto cycle, gamma = 1.4"),

    # ================= electrical =================
    L("Ohm", "electrical", "V = I R", "V", {"I": 1, "R": 1}, 1.0, "1"),
    L("Joule heating", "electrical", "P = I^2 R", "P", {"I": 2, "R": 1},
      1.0, "1", note="the SQUARE of current - doubling current quadruples heat"),
    L("resistance of a conductor", "electrical", "R = rho L / A", "R",
      {"rho": 1, "L": 1, "A": -1}, 1.0, "1"),
    L("RC time constant", "electrical", "tau = R C", "tau", {"R": 1, "C": 1},
      1.0, "1"),
    L("RC corner frequency", "electrical", "fc = 1/(2 pi R C)", "fc",
      {"R": -1, "C": -1}, 1 / (2 * math.pi), "1/(2 pi)"),
    L("LC resonance", "electrical", "f0 = 1/(2 pi sqrt(L C))", "f0",
      {"L": -0.5, "C": -0.5}, 1 / (2 * math.pi), "1/(2 pi)"),
    L("series RLC damping ratio", "electrical", "zeta = (R/2) sqrt(C/L)",
      "zeta", {"R": 1, "C": 0.5, "L": -0.5}, 0.5, "1/2"),
    L("quality factor", "electrical", "Q = 1/(2 zeta)", "Q", {"zeta": -1},
      0.5, "1/2"),
    L("capacitor energy", "electrical", "E = 0.5 C V^2", "E",
      {"C": 1, "V": 2}, 0.5, "1/2"),
    L("inductor energy", "electrical", "E = 0.5 L I^2", "E",
      {"L": 1, "I": 2}, 0.5, "1/2"),
    L("parallel plate capacitance", "electrical", "C = eps A / d", "C",
      {"A": 1, "d": -1}, 8.8541878128e-12, "epsilon"),
    L("solenoid inductance", "electrical", "L = mu N^2 A / l", "L",
      {"N": 2, "A": 1, "l": -1}, 1.25663706212e-6, "mu"),
    L("Coulomb", "electrical", "F = k q1 q2 / r^2", "F",
      {"q1": 1, "q2": 1, "r": -2}, 8.9875517873681764e9, "1/(4 pi eps0)"),
    L("Biot-Savart, long wire", "electrical", "B = mu0 I / (2 pi r)", "B",
      {"I": 1, "r": -1}, 2e-7, "mu0/(2 pi)"),
    L("transformer turns ratio", "electrical", "V2/V1 = N2/N1", "V",
      {"N": 1}, None, "", "ideal transformer"),
    L("skin depth", "electrical", "delta = sqrt(rho/(pi f mu))", "delta",
      {"rho": 0.5, "f": -0.5}, None, "1/sqrt(pi mu)"),

    # ================= dynamics and vibration =================
    L("Newton second law", "dynamics", "F = m a", "F", {"m": 1, "a": 1},
      1.0, "1"),
    L("kinetic energy", "dynamics", "E = 0.5 m v^2", "E", {"m": 1, "v": 2},
      0.5, "1/2"),
    L("spring-mass natural frequency", "dynamics",
      "wn = sqrt(k/m)", "wn", {"k": 0.5, "m": -0.5}, 1.0, "1"),
    L("simple pendulum", "dynamics", "T = 2 pi sqrt(L/g)", "T", {"L": 0.5},
      None, "2 pi / sqrt(g)", "small angle, below about 10 degrees"),
    L("rolling cylinder acceleration", "dynamics", "a = (2/3) g sin(theta)",
      "a", {}, 2 / 3, "2/3", "solid cylinder, rolling without slipping",
      note="independent of both mass and radius"),
    L("centripetal acceleration", "dynamics", "a = v^2 / r", "a",
      {"v": 2, "r": -1}, 1.0, "1"),
    L("critical damping", "dynamics", "c_c = 2 sqrt(k m)", "c_c",
      {"k": 0.5, "m": 0.5}, 2.0, "2"),
    L("logarithmic decrement", "dynamics", "delta = 2 pi zeta / sqrt(1-z^2)",
      "delta", {}, None, "not a power law", "lightly damped"),

    # ================= orbital and aerospace =================
    L("Kepler third law", "aerospace", "T = 2 pi sqrt(a^3/mu)", "T",
      {"a": 1.5, "mu": -0.5}, 2 * math.pi, "2 pi",
      note="the 3/2 exponent on semi-major axis is the signature"),
    L("vis-viva", "aerospace", "v = sqrt(mu (2/r - 1/a))", "v", {}, None,
      "not a monomial"),
    L("circular orbital velocity", "aerospace", "v = sqrt(mu/r)", "v",
      {"mu": 0.5, "r": -0.5}, 1.0, "1"),
    L("escape velocity", "aerospace", "v = sqrt(2 mu / r)", "v",
      {"mu": 0.5, "r": -0.5}, math.sqrt(2), "sqrt(2)"),
    L("Tsiolkovsky rocket", "aerospace", "dv = Isp g0 ln(m0/mf)", "dv", {},
      None, "logarithmic in mass ratio, not a power law"),
    L("specific impulse", "aerospace", "Isp = T / (mdot g0)", "Isp",
      {"T": 1, "mdot": -1}, 1 / 9.80665, "1/g0"),
    L("lift", "aerospace", "L = 0.5 Cl rho A v^2", "L",
      {"Cl": 1, "rho": 1, "A": 1, "v": 2}, 0.5, "1/2"),
    L("vacuum projectile range", "aerospace", "R = v0^2 sin(2 th)/g", "R",
      {"v0": 2}, 1 / 9.80665, "1/g", "no drag",
      note="independent of mass - Galileo's result"),

    # ================= geometry and sections =================
    L("sphere volume", "geometry", "V = (4/3) pi r^3", "V", {"r": 3},
      4 * math.pi / 3, "4pi/3"),
    L("sphere surface", "geometry", "A = 4 pi r^2", "A", {"r": 2},
      4 * math.pi, "4 pi"),
    L("cylinder volume", "geometry", "V = pi r^2 h", "V", {"r": 2, "h": 1},
      math.pi, "pi"),
    L("ellipse semi-minor axis", "geometry", "b = a sqrt(1-e^2)", "b",
      {"a": 1}, None, "sqrt(1-e^2)"),
    L("square-cube law", "geometry", "A/V ~ 1/L", "ratio", {"L": -1}, None,
      "", note="why small things cool fast and large structures are "
              "governed by their own weight"),

    # ================= chemical and process =================
    L("Arrhenius rate", "chemical", "k = A exp(-Ea/(R T))", "k", {}, None,
      "exponential in 1/T, NOT a power law",
      note="ln k against 1/T is the linear form; a power-law engine will "
           "fit it badly and should report that it is the wrong shape"),
    L("Fick first law", "chemical", "J = -D dC/dx", "J", {"D": 1}, 1.0, "1"),
    L("diffusion length", "chemical", "x ~ sqrt(D t)", "x",
      {"D": 0.5, "t": 0.5}, None, "order unity"),
    L("residence time", "chemical", "tau = V / Q", "tau", {"V": 1, "Q": -1},
      1.0, "1"),
    L("Beer-Lambert", "chemical", "A = eps l c", "A",
      {"l": 1, "c": 1}, None, "epsilon"),

    # ================= civil and geotechnical =================
    L("Terzaghi effective stress", "civil", "sigma' = sigma - u", "sigma_eff",
      {}, None, "not a power law", note="the founding relation of soil mechanics"),
    L("Darcy seepage", "civil", "v = k i", "v", {"k": 1, "i": 1}, 1.0, "1"),
    L("Abrams", "civil", "fc = A / B^(w/c)", "fc", {}, None,
      "exponential in w/c",
      note="often approximated as a power law in w/c, which is why a fitted "
           "exponent varies between mixes"),

    # ================= dimensionless groups =================
    L("Prandtl", "dimensionless", "Pr = cp mu / k", "Pr",
      {"cp": 1, "mu": 1, "k": -1}, 1.0, "1"),
    L("Nusselt", "dimensionless", "Nu = h L / k", "Nu",
      {"h": 1, "L": 1, "k": -1}, 1.0, "1"),
    L("Peclet", "dimensionless", "Pe = Re Pr", "Pe", {"Re": 1, "Pr": 1},
      1.0, "1"),
    L("Mach", "dimensionless", "M = v / a", "M", {"v": 1, "a": -1}, 1.0, "1"),
    L("Weber", "dimensionless", "We = rho v^2 L / sigma", "We",
      {"rho": 1, "v": 2, "L": 1, "sigma": -1}, 1.0, "1"),
    L("Strouhal", "dimensionless", "St = f L / v", "St",
      {"f": 1, "L": 1, "v": -1}, 1.0, "1"),
    L("Rayleigh", "dimensionless", "Ra = Gr Pr", "Ra", {"Gr": 1, "Pr": 1},
      1.0, "1"),
    L("Euler number", "dimensionless", "Eu = dp / (rho v^2)", "Eu",
      {"dp": 1, "rho": -1, "v": -2}, 1.0, "1"),

    # ============ heat transfer correlations, in depth ============
    # These are the workhorses. Each is a fitted correlation with a stated
    # band, and outside the band it is not conservative - it is meaningless.
    L("Sieder-Tate", "thermal", "Nu = 0.027 Re^0.8 Pr^(1/3) (mu/mu_w)^0.14",
      "Nu", {"Re": 0.8, "Pr": 1 / 3}, 0.027, "0.027",
      "turbulent pipe, Re > 10000, 0.7 < Pr < 16700, large property variation",
      note="preferred over Dittus-Boelter when viscosity varies strongly "
           "across the boundary layer, e.g. viscous oils being heated"),
    L("Gnielinski", "thermal",
      "Nu = (f/8)(Re-1000)Pr / (1 + 12.7 sqrt(f/8)(Pr^(2/3)-1))", "Nu", {},
      None, "not a power law - a rational function of f, Re and Pr",
      "3000 < Re < 5e6, 0.5 < Pr < 2000",
      note="the most accurate general internal-flow correlation, and NOT a "
           "monomial, so a power-law engine must decline it"),
    L("Colburn", "thermal", "Nu = 0.023 Re^0.8 Pr^(1/3)", "Nu",
      {"Re": 0.8, "Pr": 1 / 3}, 0.023, "0.023",
      "turbulent pipe, Re > 10000",
      note="the Pr^(1/3) form; Dittus-Boelter uses 0.4 for heating"),
    L("Hausen", "thermal", "Nu = 3.66 + 0.0668 Gz / (1 + 0.04 Gz^(2/3))",
      "Nu", {}, None, "not a power law", "laminar developing pipe flow"),
    L("fully developed laminar pipe, constant T", "thermal", "Nu = 3.66",
      "Nu", {}, 3.66, "3.66 exactly",
      "laminar, fully developed, constant wall temperature",
      note="a CONSTANT - no Reynolds dependence at all once developed, which "
           "surprises people expecting a correlation"),
    L("fully developed laminar pipe, constant q", "thermal", "Nu = 4.36",
      "Nu", {}, 4.36, "4.36 exactly",
      "laminar, fully developed, constant heat flux"),
    L("Hilpert", "thermal", "Nu = C Re^m Pr^(1/3)", "Nu",
      {"Re": 0.618, "Pr": 1 / 3}, 0.193, "C and m depend on the Re band",
      "cross-flow over a cylinder, 4000 < Re < 40000",
      note="C and m are tabulated per decade of Re - a single exponent is an "
           "approximation over one band only"),
    L("Churchill-Bernstein", "thermal",
      "Nu = 0.3 + 0.62 Re^0.5 Pr^(1/3) / [1+(0.4/Pr)^(2/3)]^0.25 * corr",
      "Nu", {}, None, "not a power law",
      "cross-flow cylinder, all Re Pr > 0.2",
      note="the general cylinder correlation; valid over the whole range "
           "where Hilpert needs banding"),
    L("Zhukauskas", "thermal", "Nu = C Re^m Pr^n (Pr/Pr_w)^0.25", "Nu",
      {"Re": 0.6, "Pr": 0.37}, 0.51, "C, m banded by Re",
      "cross-flow cylinder, 0.7 < Pr < 500, 1 < Re < 1e6"),
    L("Whitaker sphere", "thermal",
      "Nu = 2 + (0.4 Re^0.5 + 0.06 Re^(2/3)) Pr^0.4", "Nu", {}, None,
      "not a power law; the leading 2 is pure conduction",
      "sphere, 3.5 < Re < 7.6e4",
      note="Nu -> 2 as Re -> 0, which is the exact conduction limit for a "
           "sphere in a stagnant medium - a correlation that misses that "
           "limit is wrong"),
    L("Churchill-Chu, vertical plate", "thermal",
      "Nu = 0.68 + 0.670 Ra^0.25 / [1+(0.492/Pr)^(9/16)]^(4/9)", "Nu", {},
      None, "not a power law", "natural convection, Ra < 1e9, laminar"),
    L("McAdams turbulent vertical plate", "thermal", "Nu = 0.10 Ra^(1/3)",
      "Nu", {"Ra": 1 / 3}, 0.10, "0.10",
      "natural convection, 1e10 < Ra < 1e13, turbulent",
      note="the Ra^(1/3) exponent marks turbulent free convection; laminar "
           "is Ra^(1/4)"),
    L("horizontal plate, hot face up", "thermal", "Nu = 0.15 Ra^(1/3)", "Nu",
      {"Ra": 1 / 3}, 0.15, "0.15", "1e7 < Ra < 1e11"),
    L("horizontal cylinder natural convection", "thermal",
      "Nu = 0.53 Ra^0.25", "Nu", {"Ra": 0.25}, 0.53, "0.53",
      "1e4 < Ra < 1e9"),
    L("Grashof", "dimensionless", "Gr = g beta dT L^3 / nu^2", "Gr",
      {"beta": 1, "dT": 1, "L": 3, "nu": -2}, 9.80665, "g",
      note="the L^3 makes natural convection extremely sensitive to size"),
    L("Rohsenow nucleate boiling", "thermal",
      "q = mu h_fg (dT cp / (Csf h_fg Pr^n))^3 sqrt(g dRho/sigma)", "q",
      {"dT": 3}, None, "Csf is surface-fluid specific",
      "nucleate pool boiling",
      note="the CUBE of superheat - a small wall temperature rise gives a "
           "large flux increase, which is why boiling is so effective"),
    L("Zuber critical heat flux", "thermal",
      "q_max = 0.149 h_fg rho_v sqrt(sigma g (rho_l-rho_v)/rho_v^2)",
      "q_max", {"sigma": 0.25}, 0.149, "0.149",
      "saturated pool boiling, horizontal surface",
      note="exceeding CHF causes transition to film boiling and a sudden "
           "temperature excursion - the burnout point"),
    L("Nusselt film condensation, vertical", "thermal",
      "h = 0.943 [rho^2 g h_fg k^3 / (mu L dT)]^0.25", "h",
      {"k": 0.75, "L": -0.25, "dT": -0.25, "mu": -0.25}, 0.943, "0.943",
      "laminar film condensation on a vertical plate",
      note="h FALLS with dT, unlike almost every other convection "
           "correlation, because a larger dT thickens the condensate film"),
    L("LMTD", "thermal", "dT_lm = (dT1-dT2)/ln(dT1/dT2)", "dT_lm", {}, None,
      "logarithmic, not a power law", "counter or parallel flow, constant U"),
    L("NTU", "thermal", "NTU = U A / (m_dot cp)_min", "NTU",
      {"U": 1, "A": 1}, 1.0, "1"),
    L("counterflow effectiveness", "thermal",
      "eps = (1-exp(-NTU(1-Cr)))/(1-Cr exp(-NTU(1-Cr)))", "eps", {}, None,
      "exponential, not a power law"),
    L("Colburn j-factor analogy", "thermal", "j = Nu/(Re Pr^(1/3)) = f/2",
      "j", {"Re": -1, "Pr": -1 / 3}, 1.0, "1",
      "turbulent, Pr near 1",
      note="links heat transfer to friction - the Chilton-Colburn analogy, "
           "and why a better heat exchanger usually costs pressure drop"),
    L("fin efficiency, straight fin", "thermal",
      "eta = tanh(mL)/(mL)", "eta", {}, None, "hyperbolic, not a power law"),
    L("critical insulation radius, cylinder", "thermal", "r_cr = k / h",
      "r_cr", {"k": 1, "h": -1}, 1.0, "1",
      note="below this radius adding insulation INCREASES heat loss, because "
           "the added surface area beats the added resistance"),
    L("thermal contact conductance", "thermal", "R = 1/(h_c A)", "R",
      {"h_c": -1, "A": -1}, 1.0, "1"),
    L("Peclet-based entrance length, thermal", "thermal",
      "x_t/D ~ 0.05 Re Pr", "x_t", {"Re": 1, "Pr": 1}, 0.05, "0.05",
      "laminar"),

    # ============ fluid mechanics correlations, in depth ============
    L("Colebrook-White", "fluids",
      "1/sqrt(f) = -2 log10(e/(3.7D) + 2.51/(Re sqrt(f)))", "f", {}, None,
      "implicit - must be solved iteratively",
      "turbulent, Re > 4000, any roughness",
      note="IMPLICIT in f. Swamee-Jain and Haaland are explicit "
           "approximations to it, and that is the only reason they exist"),
    L("Swamee-Jain", "fluids",
      "f = 0.25/[log10(e/(3.7D) + 5.74/Re^0.9)]^2", "f", {}, None,
      "explicit approximation to Colebrook",
      "5000 < Re < 1e8, 1e-6 < e/D < 0.01",
      note="within about 1% of Colebrook over the stated band, and it "
           "needs no iteration"),
    L("Haaland", "fluids",
      "1/sqrt(f) = -1.8 log10[(e/(3.7D))^1.11 + 6.9/Re]", "f", {}, None,
      "explicit approximation to Colebrook", "turbulent"),
    L("von Karman fully rough", "fluids",
      "1/sqrt(f) = -2 log10(e/(3.7D))", "f", {}, None,
      "logarithmic in relative roughness",
      "fully rough turbulent, high Re",
      note="f becomes INDEPENDENT of Reynolds number - the horizontal "
           "asymptotes on the right of the Moody chart"),
    L("Hazen-Williams", "fluids",
      "v = 0.849 C R^0.63 S^0.54", "v", {"R": 0.63, "S": 0.54}, 0.849,
      "0.849 C, SI units",
      "water only, 4 < D < 3500 mm, v < 3 m/s, near 16 C",
      note="WATER ONLY and temperature specific - it has no viscosity term, "
           "so using it for oil or hot water is simply wrong"),
    L("Chezy", "fluids", "v = C sqrt(R S)", "v", {"R": 0.5, "S": 0.5}, None,
      "C is the Chezy coefficient", "open channel"),
    L("Borda-Carnot sudden expansion", "fluids",
      "dp = 0.5 rho (v1-v2)^2", "dp", {"rho": 1}, 0.5, "1/2",
      "sudden enlargement"),
    L("minor loss", "fluids", "dp = K (rho v^2 / 2)", "dp",
      {"K": 1, "rho": 1, "v": 2}, 0.5, "1/2"),
    L("Ergun packed bed", "fluids",
      "dp/L = 150 mu (1-e)^2 v/(e^3 dp^2) + 1.75 rho (1-e) v^2/(e^3 dp)",
      "dp", {}, None, "two terms: viscous plus inertial",
      "packed beds, all Re",
      note="the two terms dominate at low and high Re respectively, so a "
           "single power law fits neither end"),
    L("Kozeny-Carman", "fluids",
      "k = e^3 d^2 / (180 (1-e)^2)", "k", {"d": 2}, 1 / 180, "1/180",
      "laminar flow through a packed bed or soil"),
    L("Stokes settling velocity", "fluids",
      "v = g d^2 (rho_s-rho_f)/(18 mu)", "v", {"d": 2, "mu": -1},
      9.80665 / 18, "g/18", "Re < 1, spherical particle",
      note="the d^2 dependence is why fine particles settle so slowly - "
           "halving diameter quarters the velocity"),
    L("Newton drag regime settling", "fluids",
      "v = 1.74 sqrt(g d (rho_s-rho_f)/rho_f)", "v", {"d": 0.5}, 1.74,
      "1.74", "Re > 1000, inertia dominated"),
    L("Sutherland viscosity", "fluids",
      "mu/mu0 = (T/T0)^1.5 (T0+S)/(T+S)", "mu", {"T": 1.5}, None,
      "S = 110.4 K for air", "gases, moderate pressure",
      note="the T^1.5 is the leading behaviour; the Sutherland constant "
           "corrects it"),
    L("choked mass flow", "fluids",
      "mdot = A p0 sqrt(gamma/(R T0)) (2/(gamma+1))^((gamma+1)/(2(gamma-1)))",
      "mdot", {"A": 1, "p0": 1, "T0": -0.5}, None, "gamma dependent",
      "M = 1 at the throat, p_back/p0 below the critical ratio",
      note="once choked, mass flow no longer responds to downstream "
           "pressure at all"),
    L("critical pressure ratio", "fluids",
      "p*/p0 = (2/(gamma+1))^(gamma/(gamma-1))", "ratio", {}, 0.5283,
      "0.5283 for air, gamma = 1.4", "isentropic nozzle"),
    L("Rayleigh-Plesset bubble", "fluids",
      "R d2R/dt2 + 1.5 (dR/dt)^2 = (p_B - p_inf)/rho", "R", {}, None,
      "a differential equation, not an algebraic law",
      note="SINDy can attack this from a radius time series; the monomial "
           "engine cannot see it at all"),
    L("Womersley number", "dimensionless",
      "Wo = R sqrt(omega rho / mu)", "Wo",
      {"R": 1, "omega": 0.5, "rho": 0.5, "mu": -0.5}, 1.0, "1",
      "pulsatile flow, arterial and reciprocating systems"),
    L("Roshko / Strouhal for a cylinder", "fluids", "St ~ 0.21", "St", {},
      0.21, "0.21, nearly constant", "300 < Re < 2e5",
      note="remarkably constant over three decades of Re, which is why "
           "vortex shedding frequency scales simply with velocity"),
    L("law of the wall", "fluids", "u+ = (1/kappa) ln(y+) + B", "u_plus", {},
      None, "kappa = 0.41, B = 5.0", "y+ > 30, log layer",
      note="logarithmic, not a power law - the reason wall functions exist "
           "in CFD instead of resolving the boundary layer"),
    L("Prandtl mixing length", "fluids", "tau_t = rho l^2 (du/dy)^2",
      "tau_t", {"rho": 1, "l": 2}, 1.0, "1"),
    L("Blasius boundary layer thickness", "fluids",
      "delta = 5.0 x / sqrt(Re_x)", "delta", {"x": 0.5}, 5.0,
      "5.0 / sqrt(rho v / mu)", "laminar flat plate, Re_x < 5e5",
      note="delta grows as sqrt(x) in laminar flow and as x^0.8 in turbulent"),
    L("turbulent boundary layer thickness", "fluids",
      "delta = 0.37 x / Re_x^0.2", "delta", {"x": 0.8}, 0.37, "0.37",
      "turbulent flat plate, 5e5 < Re_x < 1e7"),
    L("skin friction, laminar plate", "fluids", "Cf = 0.664/sqrt(Re_x)",
      "Cf", {"Re_x": -0.5}, 0.664, "0.664", "laminar, Re_x < 5e5"),
    L("skin friction, turbulent plate", "fluids", "Cf = 0.0592 Re_x^(-0.2)",
      "Cf", {"Re_x": -0.2}, 0.0592, "0.0592", "5e5 < Re_x < 1e7"),
    L("NPSH available", "fluids",
      "NPSHa = (p_atm - p_v)/(rho g) + z - h_f", "NPSHa", {}, None,
      "not a power law",
      note="cavitation begins when NPSHa falls to NPSHr, and the margin is "
           "what pump selection is really about"),
    L("specific speed", "fluids", "Ns = N sqrt(Q) / H^0.75", "Ns",
      {"N": 1, "Q": 0.5, "H": -0.75}, 1.0, "1",
      note="dictates impeller type: low Ns radial, high Ns axial"),
    L("Thoma cavitation number", "dimensionless",
      "sigma = (p - p_v)/(0.5 rho v^2)", "sigma",
      {"rho": -1, "v": -2}, 2.0, "2"),
    L("orifice discharge", "fluids", "Q = Cd A sqrt(2 dp / rho)", "Q",
      {"A": 1, "dp": 0.5, "rho": -0.5}, None, "Cd sqrt(2)",
      "sharp-edged orifice, Cd ~ 0.6"),
    L("Venturi flow", "fluids",
      "Q = Cd A2 sqrt(2 dp/(rho(1-(A2/A1)^2)))", "Q",
      {"dp": 0.5, "rho": -0.5}, None, "geometry dependent"),
    L("weir flow, rectangular", "fluids", "Q = (2/3) Cd b sqrt(2g) H^1.5",
      "Q", {"b": 1, "H": 1.5}, None, "(2/3) Cd sqrt(2g)",
      note="the 3/2 power on head is the signature of a weir"),
    L("hydraulic jump depth ratio", "fluids",
      "y2/y1 = 0.5(sqrt(1+8 Fr1^2) - 1)", "ratio", {}, None,
      "not a power law", "Fr1 > 1, supercritical upstream"),
    L("Bernoulli", "fluids", "p + 0.5 rho v^2 + rho g z = const", "p", {},
      None, "not a power law", "inviscid, incompressible, along a streamline",
      note="the assumptions are what people forget - it is not valid across "
           "a pump, a turbine, or any region with losses"),

    # ============ GEOTECHNICAL - previously zero coverage ============
    L("Terzaghi bearing capacity", "geotechnical",
      "qu = c Nc + q Nq + 0.5 gamma B N_gamma", "qu",
      {"B": 1}, None, "bearing capacity factors depend on friction angle",
      "shallow foundation, general shear failure",
      note="three terms - cohesion, surcharge, self weight. The width term "
           "matters only for granular soils"),
    L("Rankine active earth pressure", "geotechnical",
      "Ka = tan^2(45 - phi/2)", "Ka", {}, None, "trigonometric in phi",
      "smooth vertical wall, horizontal backfill, no wall friction"),
    L("Rankine passive earth pressure", "geotechnical",
      "Kp = tan^2(45 + phi/2)", "Kp", {}, None, "trigonometric in phi",
      note="Kp is 1/Ka; passive resistance needs far more movement to "
           "mobilise than active pressure, which is why it is often ignored"),
    L("at-rest earth pressure", "geotechnical", "K0 = 1 - sin(phi)", "K0",
      {}, None, "Jaky", "normally consolidated soil"),
    L("Terzaghi effective stress principle", "geotechnical",
      "sigma' = sigma - u", "sigma_eff", {}, None, "not a power law",
      note="the founding relation of soil mechanics - shear strength "
           "depends on EFFECTIVE stress, which is why pore pressure controls "
           "slope failure"),
    L("Mohr-Coulomb shear strength", "geotechnical",
      "tau = c' + sigma' tan(phi')", "tau", {}, None, "trigonometric",
      "drained conditions"),
    L("Darcy seepage velocity", "geotechnical", "v = k i", "v",
      {"k": 1, "i": 1}, 1.0, "1", "laminar flow through soil"),
    L("Terzaghi 1D consolidation", "geotechnical", "Tv = cv t / H^2", "Tv",
      {"cv": 1, "t": 1, "H": -2}, 1.0, "1",
      note="time to consolidate goes as the SQUARE of drainage path - "
           "doubling layer thickness quadruples the time"),
    L("consolidation settlement", "geotechnical",
      "s = Cc H log10(sigma1/sigma0) / (1+e0)", "s", {"H": 1}, None,
      "logarithmic in stress ratio", "normally consolidated clay"),
    L("SPT to friction angle", "geotechnical",
      "phi ~ sqrt(20 N1_60) + 20", "phi", {"N": 0.5}, None,
      "empirical correlation", "granular soils",
      note="an empirical correlation with wide scatter - never a substitute "
           "for a triaxial test on a real project"),
    L("factor of safety, slope", "geotechnical",
      "FS = resisting moment / driving moment", "FS", {}, None,
      "not a power law", "limit equilibrium"),
    L("critical hydraulic gradient", "geotechnical",
      "i_cr = (Gs-1)/(1+e)", "i_cr", {}, None, "",
      note="above this, quicksand - effective stress reaches zero"),

    # ============ STRUCTURAL - previously zero as a named discipline ======
    L("plastic section modulus, rectangle", "structural", "Zp = b h^2 / 4",
      "Zp", {"b": 1, "h": 2}, 0.25, "1/4"),
    L("elastic section modulus, rectangle", "structural", "Ze = b h^2 / 6",
      "Ze", {"b": 1, "h": 2}, 1 / 6, "1/6",
      note="the shape factor Zp/Ze is 1.5 for a rectangle - the reserve "
           "between first yield and full plasticity"),
    L("moment capacity, plastic", "structural", "Mp = Zp fy", "Mp",
      {"Zp": 1, "fy": 1}, 1.0, "1"),
    L("slenderness ratio", "structural", "lambda = L_eff / r", "lambda",
      {"L_eff": 1, "r": -1}, 1.0, "1"),
    L("radius of gyration", "structural", "r = sqrt(I/A)", "r",
      {"I": 0.5, "A": -0.5}, 1.0, "1"),
    L("Perry-Robertson buckling", "structural",
      "reduction factor from imperfection parameter", "chi", {}, None,
      "not a power law", "EN 1993 uses buckling curves of this form"),
    L("shear capacity, web", "structural", "Vpl = Av fy / sqrt(3)", "Vpl",
      {"Av": 1, "fy": 1}, 1 / math.sqrt(3), "1/sqrt(3)",
      note="the sqrt(3) is von Mises - shear yield is 0.577 of tensile yield"),
    L("von Mises equivalent stress", "structural",
      "sv = sqrt(0.5[(s1-s2)^2+(s2-s3)^2+(s3-s1)^2])", "sv", {}, None,
      "not a power law", "ductile metals"),
    L("Tresca criterion", "structural", "s1 - s3 = fy", "s", {}, None,
      "not a power law",
      note="more conservative than von Mises by up to 15%"),
    L("deflection limit, span/250", "structural", "d_max = L / 250", "d",
      {"L": 1}, 1 / 250, "1/250", "characteristic combination, EN 1990",
      note="a serviceability limit, not strength - a beam can be strong "
           "enough and still fail this"),
    L("second order amplification", "structural",
      "factor = 1/(1 - N/Ncr)", "amp", {}, None, "not a power law",
      note="the reason a member near its buckling load amplifies moments "
           "sharply - at N = 0.5 Ncr the amplification is already 2"),

    # ============ CONTROL - previously zero ============
    L("second order natural frequency", "control", "wn = sqrt(k/m)", "wn",
      {"k": 0.5, "m": -0.5}, 1.0, "1"),
    L("damping ratio from overshoot", "control",
      "zeta = -ln(OS)/sqrt(pi^2+ln^2(OS))", "zeta", {}, None,
      "logarithmic, not a power law"),
    L("percent overshoot", "control", "OS = exp(-zeta pi/sqrt(1-zeta^2))",
      "OS", {}, None, "exponential, NOT a power law",
      "underdamped, zeta < 1",
      note="the monomial engine cannot recover this and should decline it"),
    L("settling time, 2%", "control", "ts = 4/(zeta wn)", "ts",
      {"zeta": -1, "wn": -1}, 4.0, "4"),
    L("rise time approximation", "control", "tr ~ 1.8/wn", "tr",
      {"wn": -1}, 1.8, "1.8", "second order, lightly damped"),
    L("damped natural frequency", "control", "wd = wn sqrt(1-zeta^2)", "wd",
      {"wn": 1}, None, "sqrt(1-zeta^2)"),
    L("Ziegler-Nichols PID", "control",
      "Kp = 0.6 Ku, Ti = 0.5 Tu, Td = 0.125 Tu", "Kp", {"Ku": 1}, 0.6, "0.6",
      "ultimate cycle method",
      note="aggressive tuning - gives about 25% overshoot, which many "
           "processes will not tolerate"),
    L("phase margin to damping", "control", "zeta ~ PM/100", "zeta",
      {"PM": 1}, 0.01, "1/100", "rule of thumb, PM < 70 degrees"),
    L("DC gain of a first order system", "control", "y_ss = K u", "y",
      {"K": 1, "u": 1}, 1.0, "1"),
    L("first order time constant response", "control",
      "y = K(1 - exp(-t/tau))", "y", {}, None, "exponential",
      note="63.2% at one time constant, 95% at three, 99.3% at five"),

    # ============ VIBRATION AND ACOUSTICS - previously zero ============
    L("transmissibility", "vibration",
      "TR = sqrt((1+(2 z r)^2)/((1-r^2)^2+(2 z r)^2))", "TR", {}, None,
      "not a power law",
      note="isolation only begins above r = sqrt(2); below that an isolator "
           "AMPLIFIES the input, which is the classic isolation mistake"),
    L("isolation frequency ratio", "vibration", "r = f / fn", "r",
      {"f": 1, "fn": -1}, 1.0, "1"),
    L("static deflection to natural frequency", "vibration",
      "fn = 15.76/sqrt(delta_mm)", "fn", {"delta": -0.5}, 15.76,
      "15.76 with delta in mm", "single degree of freedom"),
    L("sound pressure level", "acoustics", "SPL = 20 log10(p/p_ref)", "SPL",
      {}, None, "logarithmic, p_ref = 20 uPa"),
    L("sound power level", "acoustics", "LW = 10 log10(W/W_ref)", "LW",
      {}, None, "logarithmic, W_ref = 1 pW"),
    L("inverse square law, sound", "acoustics",
      "SPL2 = SPL1 - 20 log10(r2/r1)", "SPL", {}, None, "logarithmic",
      "free field, point source",
      note="6 dB per doubling of distance in a free field, but only 3 dB "
           "for a line source"),
    L("Sabine reverberation time", "acoustics", "T60 = 0.161 V / A", "T60",
      {"V": 1, "A": -1}, 0.161, "0.161 in SI", "diffuse field"),
    L("coincidence critical frequency", "acoustics",
      "fc = c^2/(1.8 t) sqrt(rho/E)", "fc", {"t": -1}, None, "",
      note="the dip in a panel's sound insulation - thin stiff panels are "
           "worse than mass law predicts near fc"),
    L("mass law, sound insulation", "acoustics",
      "R = 20 log10(m f) - 47", "R", {}, None, "logarithmic in mass and freq",
      note="6 dB per doubling of mass OR frequency"),

    # ============ MANUFACTURING - previously zero ============
    L("Taylor tool life", "manufacturing", "V T^n = C", "T",
      {"V": -1}, None, "n is 0.1-0.2 for HSS, 0.2-0.4 for carbide",
      "steady cutting",
      note="doubling cutting speed can cut tool life by a factor of ten - "
           "the exponent is small so life is very sensitive to speed"),
    L("material removal rate, turning", "manufacturing",
      "MRR = v f d", "MRR", {"v": 1, "f": 1, "d": 1}, 1.0, "1"),
    L("cutting power", "manufacturing", "P = ks MRR", "P",
      {"MRR": 1}, None, "specific cutting force ks"),
    L("surface roughness, turning", "manufacturing",
      "Ra ~ f^2/(32 r)", "Ra", {"f": 2, "r": -1}, 1 / 32, "1/32",
      "theoretical minimum for a round-nose tool",
      note="the SQUARE of feed - halving feed quarters roughness, which is "
           "why finishing passes use small feeds"),
    L("injection moulding cooling time", "manufacturing",
      "t = h^2/(pi^2 alpha) ln(...)", "t", {"h": 2, "alpha": -1}, None,
      "logarithmic prefactor",
      note="the SQUARE of wall thickness dominates cycle time - the single "
           "biggest lever on moulding cost"),
    L("shrinkage allowance", "manufacturing", "L_pattern = L(1+s)", "L",
      {}, None, "s is 1-2% for many polymers"),
    L("weld heat input", "manufacturing", "HI = eta V I / v", "HI",
      {"V": 1, "I": 1, "v": -1}, None, "arc efficiency eta"),
    L("t8/5 cooling time", "manufacturing",
      "t85 = HI^2 / (4 pi k rho c) (1/(500-T0)^2 - 1/(800-T0)^2)", "t85",
      {"HI": 2}, None, "thick plate, 3D heat flow",
      note="controls the microstructure of the heat affected zone; too fast "
           "gives martensite and cracking, which is why preheat exists"),
    L("bend allowance", "manufacturing",
      "BA = (pi/180) A (R + K t)", "BA", {}, None, "K factor 0.33-0.5"),
    L("press force, blanking", "manufacturing", "F = L t tau", "F",
      {"L": 1, "t": 1, "tau": 1}, 1.0, "1"),

    # ============ CORROSION AND ELECTROCHEMISTRY ============
    L("Faraday electrolysis", "corrosion", "m = M I t/(n F)", "m",
      {"I": 1, "t": 1}, 1 / 96485.33212, "1/F", ""),
    L("corrosion rate from current", "corrosion",
      "CR = 3.27 i M/(n rho)", "CR", {"i": 1, "rho": -1}, 3.27,
      "3.27 for mm/year with i in uA/cm2"),
    L("Tafel", "corrosion", "eta = a + b log10(i)", "eta", {}, None,
      "logarithmic in current density", "activation controlled"),
    L("Nernst", "corrosion", "E = E0 + (RT/nF) ln(Q)", "E", {}, None,
      "logarithmic in activity"),
    L("Pilling-Bedworth ratio", "corrosion",
      "PBR = V_oxide/V_metal", "PBR", {}, None, "",
      note="PBR below 1 gives a porous non-protective oxide; above 2 the "
           "oxide spalls. Between 1 and 2 is protective, which is why "
           "aluminium and chromium passivate"),
    L("galvanic series driving force", "corrosion", "dE = E_cathode - E_anode",
      "dE", {}, None, "not a power law",
      note="area ratio matters more than potential - a small anode with a "
           "large cathode corrodes very fast"),

    # ============ OPTICS ============
    L("Snell", "optics", "n1 sin(th1) = n2 sin(th2)", "theta", {}, None,
      "trigonometric"),
    L("critical angle", "optics", "th_c = arcsin(n2/n1)", "theta_c", {},
      None, "trigonometric", "n1 > n2"),
    L("thin lens", "optics", "1/f = 1/u + 1/v", "f", {}, None,
      "reciprocal, not a power law"),
    L("lensmaker", "optics", "1/f = (n-1)(1/R1 - 1/R2)", "f", {}, None, ""),
    L("Rayleigh diffraction limit", "optics",
      "theta = 1.22 lambda / D", "theta", {"lambda": 1, "D": -1}, 1.22,
      "1.22", "circular aperture"),
    L("numerical aperture", "optics", "NA = n sin(theta)", "NA", {}, None,
      "trigonometric"),
    L("fibre acceptance angle", "optics",
      "NA = sqrt(n1^2 - n2^2)", "NA", {}, None, ""),

    # ============ NUCLEAR ============
    L("radioactive decay", "nuclear", "N = N0 exp(-lambda t)", "N", {},
      None, "exponential, not a power law"),
    L("half life", "nuclear", "t_half = ln(2)/lambda", "t_half",
      {"lambda": -1}, math.log(2), "ln 2"),
    L("neutron attenuation", "nuclear",
      "I = I0 exp(-Sigma x)", "I", {}, None, "exponential"),
    L("macroscopic cross section", "nuclear", "Sigma = N sigma", "Sigma",
      {"N": 1, "sigma": 1}, 1.0, "1"),
    L("four factor formula", "nuclear", "k_inf = eta f p epsilon", "k",
      {}, None, "product of four factors", "infinite reactor"),
    L("decay heat, Wigner-Way", "nuclear",
      "P/P0 = 0.0622(t^-0.2 - (t+t0)^-0.2)", "P", {"t": -0.2}, 0.0622,
      "0.0622", "after shutdown",
      note="about 7% of full power immediately after scram, and it is why a "
           "reactor still needs cooling long after it stops"),

    # ============ MARINE ============
    L("metacentric height", "marine", "GM = KB + BM - KG", "GM", {}, None,
      "not a power law",
      note="GM must be positive for stability; too large gives a stiff, "
           "uncomfortable roll"),
    L("BM from waterplane inertia", "marine", "BM = I / V", "BM",
      {"I": 1, "V": -1}, 1.0, "1"),
    L("roll period", "marine", "T = 2 pi k / sqrt(g GM)", "T",
      {"k": 1, "GM": -0.5}, None, "2 pi / sqrt(g)"),
    L("hull speed", "marine", "v = 1.34 sqrt(Lwl_ft)", "v", {"L": 0.5},
      1.34, "1.34 in knots and feet", "displacement hulls",
      note="Fr about 0.4 - the speed at which wave-making resistance rises "
           "steeply"),
    L("Froude scaling of resistance", "marine",
      "Rr_model/Rr_ship = scale^3", "Rr", {"scale": 3}, None, "",
      "residuary resistance only, Froude similarity"),
    L("Admiralty coefficient", "marine",
      "Ac = disp^(2/3) V^3 / P", "Ac", {"disp": 2 / 3, "V": 3, "P": -1},
      1.0, "1"),

    # ============ ENVIRONMENTAL ============
    L("Streeter-Phelps oxygen sag", "environmental",
      "D = (kd L0/(kr-kd))(exp(-kd t)-exp(-kr t)) + D0 exp(-kr t)", "D",
      {}, None, "exponential", "river reach, steady flow"),
    L("BOD first order decay", "environmental",
      "BOD_t = L0(1 - exp(-k t))", "BOD", {}, None, "exponential"),
    L("Gaussian plume, centreline", "environmental",
      "C = Q/(pi u sy sz)", "C", {"Q": 1, "u": -1, "sy": -1, "sz": -1},
      1 / math.pi, "1/pi", "neutral stability, ground level, no reflection"),
    L("settling in a sedimentation tank", "environmental",
      "overflow rate = Q/A", "v_o", {"Q": 1, "A": -1}, 1.0, "1"),
    L("embodied carbon", "environmental", "EC = sum(m_i * ef_i)", "EC",
      {}, None, "linear sum",
      note="the emission factors dominate the answer and vary by a factor "
           "of two between databases, so cite the source"),

    # ============ METROLOGY ============
    L("combined standard uncertainty", "metrology",
      "uc = sqrt(sum(ui^2))", "uc", {}, None, "root sum square",
      "uncorrelated inputs, GUM"),
    L("expanded uncertainty", "metrology", "U = k uc", "U",
      {"uc": 1}, 2.0, "k = 2 for about 95%"),
    L("standard error of the mean", "metrology", "sem = s/sqrt(n)", "sem",
      {"s": 1, "n": -0.5}, 1.0, "1",
      note="quartering the uncertainty needs SIXTEEN times the measurements"),
    L("resolution uncertainty", "metrology", "u = d/(2 sqrt(3))", "u",
      {"d": 1}, 1 / (2 * math.sqrt(3)), "1/(2 sqrt 3)",
      "rectangular distribution over one least count"),

    # ============ POWER AND ENERGY ============
    L("capacity factor", "energy", "CF = E_actual/(P_rated * 8760)", "CF",
      {"E": 1, "P": -1}, 1 / 8760, "1/8760 for a year in hours"),
    L("levelised cost of energy", "energy",
      "LCOE = (CAPEX*CRF + OPEX)/E_annual", "LCOE", {}, None,
      "not a power law"),
    L("capital recovery factor", "energy",
      "CRF = i(1+i)^n/((1+i)^n - 1)", "CRF", {}, None, "compound interest"),
    L("heat rate", "energy", "HR = 3600/eta", "HR", {"eta": -1}, 3600.0,
      "3600 kJ/kWh"),
    L("transmission line loss", "energy", "P_loss = I^2 R", "P",
      {"I": 2, "R": 1}, 1.0, "1",
      note="why transmission is at high voltage - for the same power, "
           "raising V lowers I and losses fall with the SQUARE"),
    L("grid inertia constant", "energy", "H = E_kinetic/S_rated", "H",
      {}, None, "seconds",
      note="falling as synchronous machines are replaced by inverters, "
           "which is the stability problem behind the A7 corpus study"),

    # ============ MATERIALS, expanded ============
    L("Fick second law diffusion depth", "materials",
      "x ~ 2 sqrt(D t)", "x", {"D": 0.5, "t": 0.5}, 2.0, "2",
      note="case depth goes as the SQUARE ROOT of time - doubling depth "
           "costs four times the furnace time"),
    L("Arrhenius diffusivity", "materials", "D = D0 exp(-Q/(R T))", "D",
      {}, None, "exponential in 1/T, NOT a power law"),
    L("lever rule", "materials", "fraction = (C0-Ca)/(Cb-Ca)", "f", {},
      None, "linear interpolation on a tie line"),
    L("Gibbs phase rule", "materials", "F = C - P + 2", "F", {}, None,
      "integer arithmetic"),
    L("rule of mixtures, composite", "materials",
      "Ec = Ef Vf + Em(1-Vf)", "Ec", {}, None, "linear, fibre direction",
      note="the upper bound; transverse modulus follows the inverse rule "
           "and is far lower, which is why laminates need cross-plies"),
    L("inverse rule of mixtures", "materials",
      "1/Et = Vf/Ef + (1-Vf)/Em", "Et", {}, None, "transverse direction"),
    L("Larson-Miller parameter", "materials",
      "LMP = T(C + log t) x 1e-3", "LMP", {}, None, "C about 20 for steels"),
    L("thermal shock resistance", "materials",
      "R = sigma_f k (1-nu)/(E alpha)", "R",
      {"sigma_f": 1, "k": 1, "E": -1, "alpha": -1}, 1.0, "1",
      note="why fused silica survives thermal shock and alumina does not - "
           "low alpha dominates"),
    L("Weibull modulus, brittle strength", "materials",
      "Pf = 1 - exp(-(sigma/sigma0)^m)", "Pf", {}, None,
      "Weibull, not a power law", "brittle ceramics",
      note="m is 5-15 for ceramics and 50+ for metals - low m means huge "
           "scatter and a design stress far below the mean"),
    L("size effect on brittle strength", "materials",
      "sigma1/sigma2 = (V2/V1)^(1/m)", "sigma", {"V": -1 / 12}, None,
      "1/m", "Weibull statistics",
      note="a bigger specimen is weaker, because it contains a bigger flaw"),
]


# --------------------------------------------------------------------------
# using the library
# --------------------------------------------------------------------------

def by_discipline(d: str) -> list:
    return [x for x in LAWS if x.discipline == d]


def search(term: str) -> list:
    """Search every field, not just the name.

    "fatigue" appears in the VALIDITY of Basquin and Coffin-Manson and in the
    name of neither, so a name-only search returns nothing for the most
    obvious query an engineer would type.
    """
    t = term.lower()
    return [x for x in LAWS
            if t in " ".join((x.name, x.formula, x.discipline, x.target,
                              x.validity, x.note, x.constant_note,
                              " ".join(x.exponents))).lower()]


def expected(name: str, variable: str) -> float | None:
    """The exponent theory says a variable should carry."""
    for x in LAWS:
        if x.name.lower() == name.lower():
            return x.exponents.get(variable)
    return None


def identify(exponents: dict, tol: float = 0.06, min_overlap: int = 2) -> list:
    """Name a recovered law, or report that nothing matches.

    Matching is on the EXPONENT PATTERN, not on variable names, because the
    data will call it `length_L_m` and the textbook calls it `L`. Two laws with
    the same exponents on the same number of variables are genuinely the same
    relationship whatever the columns were labelled.

    Returns matches sorted by closeness. An empty list is the honest
    precondition for calling a result new.
    """
    got = sorted(exponents.values(), key=abs, reverse=True)
    out = []
    for law in LAWS:
        if not law.exponents:
            continue
        want = sorted(law.exponents.values(), key=abs, reverse=True)
        if len(want) != len(got) or len(want) < min_overlap:
            continue
        if all(abs(a - b) <= tol for a, b in zip(got, want)):
            err = max(abs(a - b) for a, b in zip(got, want))
            out.append({"law": law, "max_exponent_error": err,
                        "name": law.name, "formula": law.formula})
    return sorted(out, key=lambda d: d["max_exponent_error"])


def test_law(name: str, data: dict, tol: float = 0.05) -> dict:
    """Does a NAMED law hold in this data? Hypothesis testing, not fitting.

    Different from discovery: the exponents are fixed by theory and only the
    constant is free, so the question is whether the data is consistent with
    the law rather than what law best describes the data. A law that fails here
    has been refuted on this data, which is a real result.
    """
    law = next((x for x in LAWS if x.name.lower() == name.lower()), None)
    if law is None:
        return {"ok": False, "reason": f"'{name}' is not in the library"}
    if not law.exponents:
        return {"ok": False, "reason": f"{law.name} is not a power law "
                                       f"({law.constant_note})",
                "formula": law.formula}
    missing = [v for v in law.exponents if v not in data]
    if missing or law.target not in data:
        return {"ok": False,
                "reason": f"data is missing {missing or [law.target]}"}

    y = np.asarray(data[law.target], dtype=float)
    pred_shape = np.ones(len(y))
    for v, e in law.exponents.items():
        pred_shape = pred_shape * np.asarray(data[v], dtype=float) ** e
    m = np.isfinite(y) & np.isfinite(pred_shape) & (pred_shape != 0)
    if m.sum() < 5:
        return {"ok": False, "reason": "too few usable rows"}

    # only the constant is free - the exponents come from theory
    k = float(np.mean(y[m] / pred_shape[m]))
    pred = k * pred_shape
    rel = np.abs(pred[m] - y[m]) / np.maximum(np.abs(y[m]), 1e-300)
    med, worst = float(np.median(rel)), float(np.max(rel))
    holds = med <= tol
    out = {
        "ok": True, "law": law.name, "formula": law.formula,
        "fitted_constant": k, "expected_constant": law.constant,
        "median_rel_err": med, "max_rel_err": worst, "n": int(m.sum()),
        "holds": holds, "validity": law.validity,
        "verdict": ("consistent with the data" if holds else
                    "REFUTED on this data - the theoretical exponents do not "
                    "reproduce it"),
    }
    if law.constant is not None and law.constant != 0:
        ratio = k / law.constant
        out["constant_ratio"] = ratio
        if abs(ratio - 1) > 0.1:
            out["constant_warning"] = (
                f"fitted constant is {ratio:.3g}x the theoretical "
                f"{law.constant_note or law.constant}. The SHAPE may be right "
                f"while a factor is wrong - check units before believing the fit")
    if law.validity:
        out["validity_reminder"] = (
            f"valid only for: {law.validity}. A good fit outside that range "
            f"has not tested the law.")
    return out


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    print("== the library is substantial and well formed ==")
    chk(len(LAWS) >= 90, f"{len(LAWS)} named laws")
    discs = {x.discipline for x in LAWS}
    chk(len(discs) >= 10, f"{len(discs)} disciplines: {sorted(discs)[:6]}")
    chk(all(x.name and x.formula and x.target for x in LAWS),
        "every law has a name, a formula and a target")
    names = [x.name for x in LAWS]
    chk(len(names) == len(set(names)), "no duplicate names")

    print("== identification: it recognises what it rediscovers ==")
    # exactly what research.py returns for the Kepler dataset
    m = identify({"a": 1.5, "mu": -0.5})
    chk(any(d["name"] == "Kepler third law" for d in m),
        f"Kepler is recognised from its exponents ({[d['name'] for d in m][:3]})")
    m2 = identify({"b": 1.0, "h": 3.0})
    chk(any("second moment" in d["name"] for d in m2),
        f"I = bh^3/12 is recognised ({[d['name'] for d in m2][:2]})")
    m3 = identify({"L_H": -0.5, "C_F": -0.5})
    chk(any("LC resonance" in d["name"] for d in m3),
        f"exponents match regardless of column NAMES ({[d['name'] for d in m3][:2]})")

    print("== and says nothing when nothing matches ==")
    chk(identify({"x": 7.3, "y": -11.9}) == [],
        "an unmatched exponent pattern returns empty - the honest "
        "precondition for calling something new")
    chk(identify({"x": 1.0}) == [] or all(len(d["law"].exponents) == 1
                                          for d in identify({"x": 1.0})),
        "a single exponent does not match a multi-variable law")

    print("== expected() gives theory's answer ==")
    chk(expected("Kepler third law", "a") == 1.5, "Kepler's a exponent is 3/2")
    chk(expected("Hagen-Poiseuille", "r") == 4, "Poiseuille is r^4")
    chk(expected("Stefan-Boltzmann", "T") == 4, "Stefan-Boltzmann is T^4")
    chk(expected("Hall-Petch", "d") == -0.5,
        "Hall-Petch is d^(-1/2) - the exponent the 7B model got wrong")
    chk(expected("nonexistent law", "x") is None, "an unknown law returns None")

    print("== test_law: hypothesis testing against real data ==")
    rng = np.random.default_rng(5)
    n = 400
    r = rng.uniform(0.001, 0.02, n)
    dp = rng.uniform(1e3, 1e5, n)
    mu = rng.uniform(1e-3, 5e-3, n)
    Lp = rng.uniform(0.5, 5.0, n)
    Q = math.pi * dp * r ** 4 / (8 * mu * Lp)
    t = test_law("Hagen-Poiseuille", {"dp": dp, "r": r, "mu": mu, "L": Lp,
                                      "Q": Q})
    chk(t["ok"] and t["holds"], f"Poiseuille holds on Poiseuille data "
                                f"({t.get('median_rel_err', 9):.2e})")
    chk(abs(t["constant_ratio"] - 1) < 1e-9,
        f"and the constant matches pi/8 ({t['constant_ratio']:.6f})")
    chk("validity_reminder" in t, "with its validity range attached")
    chk("laminar" in t["validity"], "which names the laminar restriction")

    print("== a law that does NOT hold is refuted, not fitted ==")
    Q_wrong = math.pi * dp * r ** 3 / (8 * mu * Lp)      # r^3, not r^4
    t2 = test_law("Hagen-Poiseuille", {"dp": dp, "r": r, "mu": mu, "L": Lp,
                                       "Q": Q_wrong})
    chk(t2["ok"] and not t2["holds"],
        f"r^3 data refutes the r^4 law ({t2['median_rel_err']:.2f})")
    chk("REFUTED" in t2["verdict"], "and the verdict says refuted")

    print("== non-power-laws are refused, not badly fitted ==")
    t3 = test_law("Arrhenius rate", {"k": [1, 2], "T": [300, 400]})
    chk(not t3["ok"] and "not a power law" in t3["reason"],
        f"Arrhenius is declined as the wrong shape ({t3.get('reason','')})")
    t4 = test_law("Carnot efficiency", {})
    chk(not t4["ok"], "Carnot is likewise not a monomial")

    print("== the Hall-Petch entry carries the correction ==")
    hp = next(x for x in LAWS if x.name == "Hall-Petch")
    chk("INVERSE SQUARE ROOT" in hp.note,
        "the note states the exponent explicitly")
    chk("inverse Hall-Petch" in hp.note,
        "and records that it inverts below ~20 nm")

    print("== validity ranges are present where they matter ==")
    bl = next(x for x in LAWS if x.name == "Blasius")
    chk("3000" in bl.validity and "100000" in bl.validity,
        f"Blasius carries its Reynolds band ({bl.validity})")
    chk("inapplicable" in bl.note,
        "and says that outside it the law is inapplicable, not wrong")
    stokes = next(x for x in LAWS if x.name == "Stokes drag")
    chk("Re < 1" in stokes.validity, "Stokes carries Re < 1")
    with_validity = [x for x in LAWS if x.validity]
    chk(len(with_validity) >= 20,
        f"{len(with_validity)} laws carry an explicit validity range")

    print("== search and browse ==")
    chk(len(search("fatigue")) >= 2, "search finds fatigue laws")
    chk(len(by_discipline("thermal")) >= 8,
        f"{len(by_discipline('thermal'))} thermal laws")
    chk(len(by_discipline("dimensionless")) >= 7,
        f"{len(by_discipline('dimensionless'))} dimensionless groups")

    print("== predict() reproduces the law ==")
    kep = next(x for x in LAWS if x.name == "Kepler third law")
    a_ = np.array([1.5e11]); mu_ = np.array([1.327e20])
    p = kep.predict({"a": a_, "mu": mu_})
    year = 2 * math.pi * math.sqrt(1.5e11 ** 3 / 1.327e20)
    chk(abs(p[0] - year) / year < 1e-9,
        f"Earth's year from Kepler: {p[0]/86400:.1f} days")
    chk(abs(p[0] / 86400 - 365.25) < 3, "which is about 365 days")

    print(f"\nresearch_knowledge.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    q = " ".join(a for a in sys.argv[1:] if not a.startswith("-"))
    if q:
        for law in search(q):
            print(f"  {law}")
            if law.validity:
                print(f"      valid: {law.validity}")
            if law.note:
                print(f"      note : {law.note}")
    else:
        print(f"{len(LAWS)} named laws across "
              f"{len({x.discipline for x in LAWS})} disciplines")
        for d in sorted({x.discipline for x in LAWS}):
            print(f"  {d:16s} {len(by_discipline(d))}")
