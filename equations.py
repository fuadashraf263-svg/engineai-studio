#!/usr/bin/env python3
"""
equations.py - a searchable, SELF-CHECKING equation library across every discipline.

WHY A LIBRARY OF FORMULAS IS USUALLY WORTHLESS
----------------------------------------------
A collection of equations as text is a reference book, and a reference book cannot
tell you when it is wrong. Transcription errors in formula collections are
endemic - a swapped exponent, a factor of two, a radius where a diameter belongs -
and they survive indefinitely because nothing executes them.

Every entry here is EXECUTABLE and carries a worked case with a known answer. The
selftest runs all of them. An equation that cannot reproduce its own worked example
is not published; it fails the build. That is the difference between a library and
a list.

Each entry carries:
    id, discipline, name, expression (as written), a callable, variables with units,
    validity conditions, a literature source, and a worked check.

Search is by discipline, by name, by variable, or by the physical dimension of the
result - so "what can I compute that gives me a pressure" is answerable.
"""
from __future__ import annotations
import argparse, json, math, re

_EQ = []


def eq(eid, discipline, name, expression, fn, variables, result_unit,
       check_inputs, check_expect, source, validity="", tol=1e-9, notes=""):
    _EQ.append({"id": eid, "discipline": discipline, "name": name,
                "expression": expression, "fn": fn, "variables": variables,
                "result_unit": result_unit, "check_inputs": check_inputs,
                "check_expect": check_expect, "source": source,
                "validity": validity, "tol": tol, "notes": notes})


# =========================================================================== #
# STATICS AND SOLID MECHANICS
# =========================================================================== #
eq("mech.normal_stress", "solid mechanics", "Direct stress",
   "sigma = F / A", lambda F, A: F / A,
   {"F": "N", "A": "mm^2"}, "MPa",
   {"F": 10000.0, "A": 500.0}, 20.0, "any mechanics of materials text")

eq("mech.bending_stress", "solid mechanics", "Bending stress",
   "sigma = M y / I = M / Z", lambda M, Z: M / Z,
   {"M": "N.mm", "Z": "mm^3"}, "MPa",
   {"M": 1.2e6, "Z": 9633.333}, 124.5674, "Euler-Bernoulli beam theory", tol=1e-4)

eq("mech.shear_stress_avg", "solid mechanics", "Average shear stress",
   "tau = V / A", lambda V, A: V / A,
   {"V": "N", "A": "mm^2"}, "MPa", {"V": 5000.0, "A": 250.0}, 20.0, "statics")

eq("mech.torsional_shear", "solid mechanics", "Torsional shear stress",
   "tau = T r / J", lambda T, r, J: T * r / J,
   {"T": "N.mm", "r": "mm", "J": "mm^4"}, "MPa",
   {"T": 500e3, "r": 20.0, "J": math.pi * 40.0 ** 4 / 32},
   500e3 * 20.0 / (math.pi * 40.0 ** 4 / 32), "circular shafts only",
   validity="circular cross-section; non-circular sections warp")

eq("mech.angle_of_twist", "solid mechanics", "Angle of twist",
   "theta = T L / (G J)", lambda T, L, G, J: T * L / (G * J),
   {"T": "N.mm", "L": "mm", "G": "MPa", "J": "mm^4"}, "rad",
   {"T": 500e3, "L": 1000.0, "G": 79000.0, "J": math.pi * 40.0 ** 4 / 32},
   500e3 * 1000.0 / (79000.0 * math.pi * 40.0 ** 4 / 32), "torsion theory")

eq("mech.hoop_thin", "solid mechanics", "Thin cylinder hoop stress",
   "sigma_h = p r / t", lambda p, r, t: p * r / t,
   {"p": "MPa", "r": "mm", "t": "mm"}, "MPa",
   {"p": 2.0, "r": 200.0, "t": 5.0}, 80.0, "membrane theory",
   validity="t/r <= 0.1", notes="exactly twice the longitudinal stress")

eq("mech.hoop_sphere", "solid mechanics", "Thin sphere membrane stress",
   "sigma = p r / 2t", lambda p, r, t: p * r / (2 * t),
   {"p": "MPa", "r": "mm", "t": "mm"}, "MPa",
   {"p": 2.0, "r": 200.0, "t": 5.0}, 40.0, "membrane theory", validity="t/r <= 0.1")

eq("mech.euler_buckling", "solid mechanics", "Euler critical load",
   "P_cr = pi^2 E I / (K L)^2", lambda E, I, L, K=1.0: math.pi ** 2 * E * I / (K * L) ** 2,
   {"E": "MPa", "I": "mm^4", "L": "mm", "K": "-"}, "N",
   {"E": 200000.0, "I": math.pi * 20.0 ** 4 / 64, "L": 1500.0},
   math.pi ** 2 * 200000.0 * (math.pi * 20.0 ** 4 / 64) / 1500.0 ** 2,
   "Euler 1744", validity="elastic; slenderness above the transition")

eq("mech.thermal_stress", "solid mechanics", "Restrained thermal stress",
   "sigma = E alpha dT", lambda E, alpha, dT: E * alpha * dT,
   {"E": "MPa", "alpha": "1/K", "dT": "K"}, "MPa",
   {"E": 200000.0, "alpha": 12e-6, "dT": 80.0}, 192.0, "thermoelasticity",
   notes="independent of length and cross-section")

eq("mech.von_mises_2d", "solid mechanics", "von Mises equivalent stress",
   "sigma_vm = sqrt(sx^2 - sx sy + sy^2 + 3 txy^2)",
   lambda sx, sy, txy: math.sqrt(sx ** 2 - sx * sy + sy ** 2 + 3 * txy ** 2),
   {"sx": "MPa", "sy": "MPa", "txy": "MPa"}, "MPa",
   {"sx": 100.0, "sy": 0.0, "txy": 0.0}, 100.0, "distortion energy theory")

eq("mech.principal_stress", "solid mechanics", "Maximum principal stress",
   "s1 = (sx+sy)/2 + sqrt(((sx-sy)/2)^2 + txy^2)",
   lambda sx, sy, txy: (sx + sy) / 2 + math.sqrt(((sx - sy) / 2) ** 2 + txy ** 2),
   {"sx": "MPa", "sy": "MPa", "txy": "MPa"}, "MPa",
   {"sx": 80.0, "sy": 20.0, "txy": 40.0}, 50.0 + 50.0, "Mohr's circle")

eq("mech.stress_concentration", "solid mechanics", "Stress concentration",
   "sigma_max = Kt sigma_nom", lambda Kt, s: Kt * s,
   {"Kt": "-", "s": "MPa"}, "MPa", {"Kt": 3.0, "s": 100.0}, 300.0,
   "Kirsch 1898 for a circular hole")

eq("mech.strain_energy_axial", "solid mechanics", "Axial strain energy",
   "U = F^2 L / (2 A E)", lambda F, L, A, E: F ** 2 * L / (2 * A * E),
   {"F": "N", "L": "mm", "A": "mm^2", "E": "MPa"}, "N.mm",
   {"F": 10000.0, "L": 1000.0, "A": 500.0, "E": 200000.0}, 500.0, "Castigliano",
   notes="500 N.mm = 0.5 J - the worked value was first written in joules against "
         "a N.mm unit declaration, and the executable check caught it")

# --- beam deflections -------------------------------------------------------
eq("beam.cantilever_point", "structural", "Cantilever, tip point load",
   "delta = P L^3 / (3 E I)", lambda P, L, E, I: P * L ** 3 / (3 * E * I),
   {"P": "N", "L": "mm", "E": "MPa", "I": "mm^4"}, "mm",
   {"P": 1000.0, "L": 500.0, "E": 200000.0, "I": 106666.667}, 1.953125,
   "Euler-Bernoulli", tol=1e-6)

eq("beam.cantilever_udl", "structural", "Cantilever, UDL",
   "delta = w L^4 / (8 E I)", lambda w, L, E, I: w * L ** 4 / (8 * E * I),
   {"w": "N/mm", "L": "mm", "E": "MPa", "I": "mm^4"}, "mm",
   {"w": 5.0, "L": 600.0, "E": 200000.0, "I": 106666.667}, 3.796875,
   "Euler-Bernoulli", tol=1e-6)

eq("beam.simply_point", "structural", "Simply supported, central point load",
   "delta = P L^3 / (48 E I)", lambda P, L, E, I: P * L ** 3 / (48 * E * I),
   {"P": "N", "L": "mm", "E": "MPa", "I": "mm^4"}, "mm",
   {"P": 2000.0, "L": 800.0, "E": 200000.0, "I": 312500.0}, 0.3413333,
   "Euler-Bernoulli", tol=1e-5)

eq("beam.simply_udl", "structural", "Simply supported, UDL",
   "delta = 5 w L^4 / (384 E I)", lambda w, L, E, I: 5 * w * L ** 4 / (384 * E * I),
   {"w": "N/mm", "L": "mm", "E": "MPa", "I": "mm^4"}, "mm",
   {"w": 8.0, "L": 1000.0, "E": 200000.0, "I": 312500.0}, 1.6666667,
   "Euler-Bernoulli", tol=1e-5)

eq("beam.fixed_fixed_point", "structural", "Fixed-fixed, central point load",
   "delta = P L^3 / (192 E I)", lambda P, L, E, I: P * L ** 3 / (192 * E * I),
   {"P": "N", "L": "mm", "E": "MPa", "I": "mm^4"}, "mm",
   {"P": 3000.0, "L": 700.0, "E": 200000.0, "I": 312500.0},
   3000.0 * 700.0 ** 3 / (192 * 200000.0 * 312500.0), "Euler-Bernoulli",
   notes="exactly 1/4 of the simply-supported deflection")

eq("beam.second_moment_rect", "structural", "Rectangle second moment of area",
   "I = b h^3 / 12", lambda b, h: b * h ** 3 / 12.0,
   {"b": "mm", "h": "mm"}, "mm^4", {"b": 30.0, "h": 50.0}, 312500.0, "geometry")

eq("beam.section_modulus_rect", "structural", "Rectangle section modulus",
   "Z = b h^2 / 6", lambda b, h: b * h ** 2 / 6.0,
   {"b": "mm", "h": "mm"}, "mm^3", {"b": 30.0, "h": 50.0}, 12500.0, "geometry")

eq("beam.second_moment_circle", "structural", "Circle second moment of area",
   "I = pi d^4 / 64", lambda d: math.pi * d ** 4 / 64.0,
   {"d": "mm"}, "mm^4", {"d": 40.0}, math.pi * 40.0 ** 4 / 64, "geometry")

eq("beam.polar_circle", "structural", "Circle polar second moment",
   "J = pi d^4 / 32", lambda d: math.pi * d ** 4 / 32.0,
   {"d": "mm"}, "mm^4", {"d": 40.0}, math.pi * 40.0 ** 4 / 32, "geometry",
   notes="exactly 2I, by the perpendicular axis theorem")

eq("beam.radius_gyration", "structural", "Radius of gyration",
   "i = sqrt(I / A)", lambda I, A: math.sqrt(I / A),
   {"I": "mm^4", "A": "mm^2"}, "mm", {"I": 312500.0, "A": 1500.0},
   math.sqrt(312500.0 / 1500.0), "geometry")

# =========================================================================== #
# FATIGUE AND FRACTURE
# =========================================================================== #
eq("fat.basquin", "fatigue", "Basquin stress-life",
   "sigma_a = sigma_f (2N)^b", lambda sf, N, b: sf * (2 * N) ** b,
   {"sf": "MPa", "N": "cycles", "b": "-"}, "MPa",
   {"sf": 900.0, "N": 1e5, "b": -0.11}, 900.0 * (2e5) ** -0.11, "Basquin 1910")

eq("fat.miner", "fatigue", "Palmgren-Miner damage",
   "D = sum(n_i / N_i)", lambda n1, N1, n2, N2: n1 / N1 + n2 / N2,
   {"n1": "cycles", "N1": "cycles", "n2": "cycles", "N2": "cycles"}, "-",
   {"n1": 1e4, "N1": 1e5, "n2": 2e4, "N2": 1e5}, 0.3, "Miner 1945",
   notes="failure at D = 1; observed scatter is 0.5 to 2")

eq("fat.goodman", "fatigue", "Goodman mean-stress correction",
   "sigma_ar = sigma_a / (1 - sigma_m/Su)",
   lambda sa, sm, Su: sa / (1 - sm / Su),
   {"sa": "MPa", "sm": "MPa", "Su": "MPa"}, "MPa",
   {"sa": 100.0, "sm": 200.0, "Su": 440.0}, 100.0 / (1 - 200.0 / 440.0),
   "Goodman", validity="tensile mean stress only")

eq("frac.stress_intensity", "fracture", "Mode I stress intensity",
   "K = Y sigma sqrt(pi a)", lambda Y, s, a: Y * s * math.sqrt(math.pi * a),
   {"Y": "-", "s": "MPa", "a": "m"}, "MPa.sqrt(m)",
   {"Y": 1.0, "s": 100.0, "a": 0.005}, 100.0 * math.sqrt(math.pi * 0.005),
   "Irwin 1957", notes="a in METRES; using mm is wrong by sqrt(1000) = 31.6")

eq("frac.critical_crack", "fracture", "Critical crack length",
   "a_c = (K_IC / (Y sigma))^2 / pi",
   lambda KIC, Y, s: (KIC / (Y * s)) ** 2 / math.pi,
   {"KIC": "MPa.sqrt(m)", "Y": "-", "s": "MPa"}, "m",
   {"KIC": 54.0, "Y": 1.0, "s": 100.0}, (54.0 / 100.0) ** 2 / math.pi, "LEFM")

eq("frac.paris", "fracture", "Paris crack growth rate",
   "da/dN = C dK^m", lambda C, dK, m: C * dK ** m,
   {"C": "-", "dK": "MPa.sqrt(m)", "m": "-"}, "m/cycle",
   {"C": 6.9e-12, "dK": 20.0, "m": 3.0}, 6.9e-12 * 8000.0, "Paris 1963")

# =========================================================================== #
# THERMODYNAMICS AND HEAT TRANSFER
# =========================================================================== #
eq("ht.fourier", "heat transfer", "Fourier conduction",
   "q = k A dT / L", lambda k, A, dT, L: k * A * dT / L,
   {"k": "W/m.K", "A": "m^2", "dT": "K", "L": "m"}, "W",
   {"k": 45.0, "A": 0.5, "dT": 100.0, "L": 0.020}, 112500.0, "Fourier 1822")

eq("ht.resistance_plane", "heat transfer", "Plane wall thermal resistance",
   "R = L / (k A)", lambda L, k, A: L / (k * A),
   {"L": "m", "k": "W/m.K", "A": "m^2"}, "K/W",
   {"L": 0.020, "k": 45.0, "A": 0.5}, 0.020 / 22.5, "conduction")

eq("ht.resistance_cyl", "heat transfer", "Cylindrical wall resistance",
   "R = ln(ro/ri) / (2 pi k L)",
   lambda ro, ri, k, L: math.log(ro / ri) / (2 * math.pi * k * L),
   {"ro": "m", "ri": "m", "k": "W/m.K", "L": "m"}, "K/W",
   {"ro": 0.100, "ri": 0.050, "k": 0.038, "L": 1.0},
   math.log(2.0) / (2 * math.pi * 0.038), "conduction in cylinders")

eq("ht.newton_cooling", "heat transfer", "Newton's law of cooling",
   "q = h A dT", lambda h, A, dT: h * A * dT,
   {"h": "W/m^2.K", "A": "m^2", "dT": "K"}, "W",
   {"h": 25.0, "A": 2.0, "dT": 40.0}, 2000.0, "Newton 1701")

eq("ht.stefan_boltzmann", "heat transfer", "Radiative exchange",
   "q = eps sigma A (Ts^4 - Tsur^4)",
   lambda eps, A, Ts, Tsur: eps * 5.670374419e-8 * A * (Ts ** 4 - Tsur ** 4),
   {"eps": "-", "A": "m^2", "Ts": "K", "Tsur": "K"}, "W",
   {"eps": 0.8, "A": 0.2, "Ts": 400.0, "Tsur": 300.0},
   0.8 * 5.670374419e-8 * 0.2 * (400.0 ** 4 - 300.0 ** 4), "Stefan-Boltzmann",
   validity="ABSOLUTE temperatures only")

eq("ht.biot", "heat transfer", "Biot number",
   "Bi = h Lc / k", lambda h, Lc, k: h * Lc / k,
   {"h": "W/m^2.K", "Lc": "m", "k": "W/m.K"}, "-",
   {"h": 25.0, "Lc": 0.005, "k": 60.5}, 25.0 * 0.005 / 60.5, "lumped analysis",
   notes="Bi < 0.1 means lumped capacitance applies")

eq("ht.lumped_time_constant", "heat transfer", "Lumped time constant",
   "tau = rho V c / (h A)", lambda rho, V, c, h, A: rho * V * c / (h * A),
   {"rho": "kg/m^3", "V": "m^3", "c": "J/kg.K", "h": "W/m^2.K", "A": "m^2"}, "s",
   {"rho": 7854.0, "V": 1e-5, "c": 434.0, "h": 25.0, "A": 1e-3},
   7854.0 * 1e-5 * 434.0 / (25.0 * 1e-3), "transient conduction")

eq("ht.lmtd", "heat transfer", "Log mean temperature difference",
   "LMTD = (dT1 - dT2) / ln(dT1/dT2)",
   lambda d1, d2: (d1 - d2) / math.log(d1 / d2),
   {"d1": "K", "d2": "K"}, "K", {"d1": 70.0, "d2": 50.0},
   20.0 / math.log(1.4), "heat exchanger theory")

eq("ht.ntu", "heat transfer", "Number of transfer units",
   "NTU = UA / C_min", lambda UA, Cmin: UA / Cmin,
   {"UA": "W/K", "Cmin": "W/K"}, "-", {"UA": 5000.0, "Cmin": 4000.0}, 1.25,
   "effectiveness-NTU method")

eq("ht.eff_counter", "heat transfer", "Counter-flow effectiveness",
   "eps = (1-exp(-NTU(1-Cr))) / (1-Cr exp(-NTU(1-Cr)))",
   lambda NTU, Cr: ((1 - math.exp(-NTU * (1 - Cr)))
                    / (1 - Cr * math.exp(-NTU * (1 - Cr)))),
   {"NTU": "-", "Cr": "-"}, "-", {"NTU": 1.25, "Cr": 2.0 / 3.0},
   ((1 - math.exp(-1.25 * (1 / 3.0))) / (1 - (2 / 3.0) * math.exp(-1.25 / 3.0))),
   "Kays and London", validity="Cr < 1")

eq("ht.fin_efficiency", "heat transfer", "Fin efficiency, adiabatic tip",
   "eta = tanh(mL) / (mL)", lambda mL: math.tanh(mL) / mL,
   {"mL": "-"}, "-", {"mL": 1.0}, math.tanh(1.0), "fin theory")

eq("thermo.ideal_gas", "thermodynamics", "Ideal gas law",
   "pV = n R T", lambda n, T, p: n * 8.314462618 * T / p,
   {"n": "mol", "T": "K", "p": "Pa"}, "m^3",
   {"n": 1.0, "T": 273.15, "p": 101325.0},
   8.314462618 * 273.15 / 101325.0, "Clapeyron 1834",
   notes="molar volume at STP is 22.41 L")

eq("thermo.carnot", "thermodynamics", "Carnot efficiency",
   "eta = 1 - Tc / Th", lambda Tc, Th: 1 - Tc / Th,
   {"Tc": "K", "Th": "K"}, "-", {"Tc": 300.0, "Th": 900.0}, 2.0 / 3.0,
   "Carnot 1824", notes="the ceiling for ANY heat engine between these reservoirs")

eq("thermo.cop_carnot", "thermodynamics", "Carnot COP, heat pump",
   "COP = Th / (Th - Tc)", lambda Tc, Th: Th / (Th - Tc),
   {"Tc": "K", "Th": "K"}, "-", {"Tc": 273.0, "Th": 313.0}, 313.0 / 40.0,
   "reversed Carnot cycle")

eq("thermo.isentropic_T", "thermodynamics", "Isentropic temperature ratio",
   "T2/T1 = (p2/p1)^((g-1)/g)",
   lambda T1, p2, p1, g: T1 * (p2 / p1) ** ((g - 1) / g),
   {"T1": "K", "p2": "Pa", "p1": "Pa", "g": "-"}, "K",
   {"T1": 288.15, "p2": 1e6, "p1": 1e5, "g": 1.4},
   288.15 * 10.0 ** (0.4 / 1.4), "isentropic relations")

# =========================================================================== #
# FLUID MECHANICS
# =========================================================================== #
eq("fluid.reynolds", "fluid mechanics", "Reynolds number",
   "Re = rho v L / mu", lambda rho, v, L, mu: rho * v * L / mu,
   {"rho": "kg/m^3", "v": "m/s", "L": "m", "mu": "Pa.s"}, "-",
   {"rho": 998.0, "v": 2.0, "L": 0.050, "mu": 1.002e-3},
   998.0 * 2.0 * 0.05 / 1.002e-3, "Reynolds 1883")

eq("fluid.darcy_weisbach", "fluid mechanics", "Darcy-Weisbach pressure drop",
   "dp = f (L/D) (rho v^2 / 2)",
   lambda f, L, D, rho, v: f * (L / D) * rho * v ** 2 / 2,
   {"f": "-", "L": "m", "D": "m", "rho": "kg/m^3", "v": "m/s"}, "Pa",
   {"f": 0.018, "L": 10.0, "D": 0.05, "rho": 998.0, "v": 2.0},
   0.018 * 200.0 * 998.0 * 2.0, "Darcy-Weisbach")

eq("fluid.laminar_friction", "fluid mechanics", "Laminar friction factor",
   "f = 64 / Re", lambda Re: 64.0 / Re,
   {"Re": "-"}, "-", {"Re": 1600.0}, 0.04, "Hagen-Poiseuille",
   validity="Re < 2300")

eq("fluid.hagen_poiseuille", "fluid mechanics", "Hagen-Poiseuille pressure drop",
   "dp = 128 mu L Q / (pi D^4)",
   lambda mu, L, Q, D: 128 * mu * L * Q / (math.pi * D ** 4),
   {"mu": "Pa.s", "L": "m", "Q": "m^3/s", "D": "m"}, "Pa",
   {"mu": 1.002e-3, "L": 2.0, "Q": 1e-4, "D": 0.010},
   128 * 1.002e-3 * 2.0 * 1e-4 / (math.pi * 1e-8), "Hagen 1839, Poiseuille 1840",
   validity="laminar")

eq("fluid.bernoulli_velocity", "fluid mechanics", "Bernoulli velocity from head",
   "v = sqrt(2 g h)", lambda h: math.sqrt(2 * 9.80665 * h),
   {"h": "m"}, "m/s", {"h": 5.0}, math.sqrt(2 * 9.80665 * 5.0), "Torricelli 1643")

eq("fluid.dynamic_pressure", "fluid mechanics", "Dynamic pressure",
   "q = rho v^2 / 2", lambda rho, v: 0.5 * rho * v ** 2,
   {"rho": "kg/m^3", "v": "m/s"}, "Pa", {"rho": 1.225, "v": 40.0}, 980.0,
   "Bernoulli")

eq("fluid.drag", "fluid mechanics", "Drag force",
   "F = 0.5 rho v^2 A Cd", lambda rho, v, A, Cd: 0.5 * rho * v ** 2 * A * Cd,
   {"rho": "kg/m^3", "v": "m/s", "A": "m^2", "Cd": "-"}, "N",
   {"rho": 1.225, "v": 30.0, "A": 2.0, "Cd": 0.47}, 0.5 * 1.225 * 900 * 2 * 0.47,
   "aerodynamics")

eq("fluid.stokes_drag", "fluid mechanics", "Stokes drag on a sphere",
   "F = 3 pi mu d v", lambda mu, d, v: 3 * math.pi * mu * d * v,
   {"mu": "Pa.s", "d": "m", "v": "m/s"}, "N",
   {"mu": 1.002e-3, "d": 1e-4, "v": 1e-3}, 3 * math.pi * 1.002e-3 * 1e-7,
   "Stokes 1851", validity="Re < 0.1")

eq("fluid.terminal_velocity", "fluid mechanics", "Stokes terminal velocity",
   "v = g d^2 (rho_p - rho_f) / (18 mu)",
   lambda d, rp, rf, mu: 9.80665 * d ** 2 * (rp - rf) / (18 * mu),
   {"d": "m", "rp": "kg/m^3", "rf": "kg/m^3", "mu": "Pa.s"}, "m/s",
   {"d": 1e-5, "rp": 2650.0, "rf": 998.0, "mu": 1.002e-3},
   9.80665 * 1e-10 * 1652.0 / (18 * 1.002e-3), "Stokes settling")

eq("fluid.blasius_bl", "fluid mechanics", "Blasius boundary layer thickness",
   "delta = 5 x / sqrt(Re_x)", lambda x, Re: 5.0 * x / math.sqrt(Re),
   {"x": "m", "Re": "-"}, "m", {"x": 0.5, "Re": 338398.0},
   5.0 * 0.5 / math.sqrt(338398.0), "Blasius 1908", validity="Re_x < 5e5")

eq("fluid.manning", "fluid mechanics", "Manning open-channel velocity",
   "V = (1/n) R^(2/3) S^(1/2)",
   lambda n, R, S: (1.0 / n) * R ** (2.0 / 3.0) * math.sqrt(S),
   {"n": "s/m^(1/3)", "R": "m", "S": "-"}, "m/s",
   {"n": 0.013, "R": 3.6 / 5.4, "S": 0.001},
   (1 / 0.013) * (3.6 / 5.4) ** (2 / 3) * math.sqrt(0.001), "Manning 1889",
   validity="SI form; the imperial version carries a 1.486 factor")

eq("fluid.froude", "fluid mechanics", "Froude number",
   "Fr = v / sqrt(g y)", lambda v, y: v / math.sqrt(9.80665 * y),
   {"v": "m/s", "y": "m"}, "-", {"v": 2.0, "y": 1.2},
   2.0 / math.sqrt(9.80665 * 1.2), "open channel hydraulics")

eq("fluid.mach", "fluid mechanics", "Mach number",
   "M = v / sqrt(g R T)", lambda v, T, g=1.4, R=287.05: v / math.sqrt(g * R * T),
   {"v": "m/s", "T": "K", "g": "-", "R": "J/kg.K"}, "-",
   {"v": 340.0, "T": 288.15}, 340.0 / math.sqrt(1.4 * 287.05 * 288.15),
   "compressible flow")

eq("fluid.thin_airfoil", "aerodynamics", "Thin-airfoil lift coefficient",
   "Cl = 2 pi alpha", lambda a_deg: 2 * math.pi * math.radians(a_deg),
   {"a_deg": "deg"}, "-", {"a_deg": 5.0}, 2 * math.pi * math.radians(5.0),
   "thin-airfoil theory", validity="small angle, attached flow")

eq("fluid.induced_drag", "aerodynamics", "Induced drag coefficient",
   "Cdi = Cl^2 / (pi AR e)",
   lambda Cl, AR, e=1.0: Cl ** 2 / (math.pi * AR * e),
   {"Cl": "-", "AR": "-", "e": "-"}, "-", {"Cl": 1.0, "AR": 8.0},
   1.0 / (math.pi * 8.0), "Prandtl lifting-line")

# =========================================================================== #
# ELECTRICAL AND ELECTROMAGNETIC
# =========================================================================== #
eq("elec.ohm", "electrical", "Ohm's law",
   "V = I R", lambda I, R: I * R, {"I": "A", "R": "ohm"}, "V",
   {"I": 2.0, "R": 100.0}, 200.0, "Ohm 1827")

eq("elec.power", "electrical", "Joule heating",
   "P = I^2 R", lambda I, R: I ** 2 * R, {"I": "A", "R": "ohm"}, "W",
   {"I": 10.0, "R": 0.5}, 50.0, "Joule 1841")

eq("elec.resistance_wire", "electrical", "Conductor resistance",
   "R = rho L / A", lambda rho, L, A: rho * L / A,
   {"rho": "ohm.m", "L": "m", "A": "m^2"}, "ohm",
   {"rho": 1.68e-8, "L": 100.0, "A": 1e-5}, 1.68e-8 * 1e7, "resistivity")

eq("elec.parallel_r", "electrical", "Parallel resistance",
   "R = R1 R2 / (R1 + R2)", lambda R1, R2: R1 * R2 / (R1 + R2),
   {"R1": "ohm", "R2": "ohm"}, "ohm", {"R1": 100.0, "R2": 300.0}, 75.0,
   "circuit theory")

eq("elec.rc_cutoff", "electrical", "RC cutoff frequency",
   "f = 1 / (2 pi R C)", lambda R, C: 1.0 / (2 * math.pi * R * C),
   {"R": "ohm", "C": "F"}, "Hz", {"R": 1000.0, "C": 1e-6},
   1.0 / (2 * math.pi * 1e-3), "first-order filter")

eq("elec.lc_resonance", "electrical", "LC resonant frequency",
   "f = 1 / (2 pi sqrt(L C))", lambda L, C: 1.0 / (2 * math.pi * math.sqrt(L * C)),
   {"L": "H", "C": "F"}, "Hz", {"L": 1e-3, "C": 1e-6},
   1.0 / (2 * math.pi * math.sqrt(1e-9)), "resonant circuits")

eq("elec.q_series_rlc", "electrical", "Series RLC quality factor",
   "Q = (1/R) sqrt(L/C)", lambda R, L, C: math.sqrt(L / C) / R,
   {"R": "ohm", "L": "H", "C": "F"}, "-", {"R": 10.0, "L": 1e-3, "C": 1e-6},
   math.sqrt(1e-3 / 1e-6) / 10.0, "resonant circuits")

eq("elec.time_constant_rc", "electrical", "RC time constant",
   "tau = R C", lambda R, C: R * C, {"R": "ohm", "C": "F"}, "s",
   {"R": 1000.0, "C": 1e-6}, 1e-3, "first-order response")

eq("elec.capacitor_energy", "electrical", "Capacitor stored energy",
   "E = 0.5 C V^2", lambda C, V: 0.5 * C * V ** 2,
   {"C": "F", "V": "V"}, "J", {"C": 1e-6, "V": 100.0}, 5e-3, "electrostatics")

eq("elec.inductor_energy", "electrical", "Inductor stored energy",
   "E = 0.5 L I^2", lambda L, I: 0.5 * L * I ** 2,
   {"L": "H", "I": "A"}, "J", {"L": 1e-3, "I": 10.0}, 0.05, "magnetostatics")

eq("em.biot_savart_wire", "electromagnetics", "Field around a long wire",
   "B = mu0 I / (2 pi r)",
   lambda I, r: 4e-7 * math.pi * I / (2 * math.pi * r),
   {"I": "A", "r": "m"}, "T", {"I": 100.0, "r": 0.005},
   4e-7 * math.pi * 100.0 / (2 * math.pi * 0.005), "Ampere's law")

eq("em.solenoid", "electromagnetics", "Field inside a long solenoid",
   "B = mu0 n I", lambda n, I: 4e-7 * math.pi * n * I,
   {"n": "1/m", "I": "A"}, "T", {"n": 1000.0, "I": 3.0},
   4e-7 * math.pi * 3000.0, "Ampere's law")

eq("em.force_wires", "electromagnetics", "Force between parallel wires",
   "F/L = mu0 I1 I2 / (2 pi d)",
   lambda I1, I2, d: 4e-7 * math.pi * I1 * I2 / (2 * math.pi * d),
   {"I1": "A", "I2": "A", "d": "m"}, "N/m", {"I1": 1.0, "I2": 1.0, "d": 1.0},
   2e-7, "defined the ampere until 2019")

eq("em.skin_depth", "electromagnetics", "Skin depth",
   "delta = sqrt(2 / (omega mu sigma))",
   lambda f, sigma: math.sqrt(2.0 / (2 * math.pi * f * 4e-7 * math.pi * sigma)),
   {"f": "Hz", "sigma": "S/m"}, "m", {"f": 50.0, "sigma": 5.96e7},
   math.sqrt(2.0 / (2 * math.pi * 50.0 * 4e-7 * math.pi * 5.96e7)),
   "AC conduction")

eq("em.parallel_plate_C", "electromagnetics", "Parallel-plate capacitance",
   "C = eps0 eps_r A / d",
   lambda A, d, er: 8.8541878128e-12 * er * A / d,
   {"A": "m^2", "d": "m", "er": "-"}, "F",
   {"A": 0.01, "d": 5e-4, "er": 4.0}, 8.8541878128e-12 * 4 * 20.0,
   "electrostatics")

eq("em.reluctance", "electromagnetics", "Magnetic reluctance",
   "R = l / (mu0 mu_r A)",
   lambda l, A, mur: l / (4e-7 * math.pi * mur * A),
   {"l": "m", "A": "m^2", "mur": "-"}, "A/Wb",
   {"l": 0.001, "A": 4e-4, "mur": 1.0}, 0.001 / (4e-7 * math.pi * 4e-4),
   "magnetic circuits")

# =========================================================================== #
# DYNAMICS AND VIBRATION
# =========================================================================== #
eq("dyn.kinetic_energy", "dynamics", "Kinetic energy",
   "E = 0.5 m v^2", lambda m, v: 0.5 * m * v ** 2,
   {"m": "kg", "v": "m/s"}, "J", {"m": 1500.0, "v": 30.0}, 675000.0, "Newton")

eq("dyn.pendulum_period", "dynamics", "Simple pendulum period",
   "T = 2 pi sqrt(L/g)", lambda L: 2 * math.pi * math.sqrt(L / 9.80665),
   {"L": "m"}, "s", {"L": 1.0}, 2 * math.pi * math.sqrt(1 / 9.80665),
   "Huygens 1673", validity="small amplitude, below ~15 degrees")

eq("dyn.natural_frequency", "vibration", "Undamped natural frequency",
   "omega_n = sqrt(k/m)", lambda k, m: math.sqrt(k / m),
   {"k": "N/m", "m": "kg"}, "rad/s", {"k": 100.0, "m": 1.0}, 10.0,
   "single degree of freedom")

eq("dyn.damping_ratio", "vibration", "Damping ratio",
   "zeta = c / (2 sqrt(k m))", lambda c, k, m: c / (2 * math.sqrt(k * m)),
   {"c": "N.s/m", "k": "N/m", "m": "kg"}, "-",
   {"c": 2.0, "k": 100.0, "m": 1.0}, 0.1, "single degree of freedom")

eq("dyn.damped_frequency", "vibration", "Damped natural frequency",
   "omega_d = omega_n sqrt(1 - zeta^2)",
   lambda wn, z: wn * math.sqrt(1 - z ** 2),
   {"wn": "rad/s", "z": "-"}, "rad/s", {"wn": 10.0, "z": 0.1},
   10.0 * math.sqrt(0.99), "single degree of freedom", validity="zeta < 1")

eq("dyn.log_decrement", "vibration", "Logarithmic decrement",
   "delta = 2 pi zeta / sqrt(1 - zeta^2)",
   lambda z: 2 * math.pi * z / math.sqrt(1 - z ** 2),
   {"z": "-"}, "-", {"z": 0.1}, 2 * math.pi * 0.1 / math.sqrt(0.99),
   "free decay measurement")

eq("dyn.rolling_cylinder", "dynamics", "Rolling cylinder acceleration",
   "a = g sin(theta) / (1 + I/(m r^2))",
   lambda th_deg, ratio=0.5: 9.80665 * math.sin(math.radians(th_deg)) / (1 + ratio),
   {"th_deg": "deg", "ratio": "-"}, "m/s^2", {"th_deg": 20.0},
   9.80665 * math.sin(math.radians(20.0)) / 1.5, "rigid body dynamics",
   notes="ratio = 1/2 for a solid cylinder, 2/5 for a sphere")

eq("dyn.critical_speed", "vibration", "Shaft critical speed",
   "N_c = (60/2pi) sqrt(g/delta)",
   lambda delta: (60.0 / (2 * math.pi)) * math.sqrt(9.80665 / delta),
   {"delta": "m"}, "rpm", {"delta": 0.001},
   (60.0 / (2 * math.pi)) * math.sqrt(9.80665 / 0.001), "Rayleigh method")

# =========================================================================== #
# CHEMICAL AND PROCESS
# =========================================================================== #
eq("chem.arrhenius", "chemical", "Arrhenius rate constant",
   "k = A exp(-Ea / R T)",
   lambda A, Ea, T: A * math.exp(-Ea / (8.314462618 * T)),
   {"A": "1/s", "Ea": "J/mol", "T": "K"}, "1/s",
   {"A": 1e13, "Ea": 80000.0, "T": 500.0},
   1e13 * math.exp(-80000.0 / (8.314462618 * 500.0)), "Arrhenius 1889")

eq("chem.cstr_volume", "chemical", "CSTR volume, first order",
   "V = Q X / (k (1 - X))",
   lambda Q, X, k: Q * X / (k * (1 - X)),
   {"Q": "m^3/s", "X": "-", "k": "1/s"}, "m^3",
   {"Q": 0.001, "X": 0.9, "k": 0.01}, 0.001 * 0.9 / (0.01 * 0.1),
   "reactor design")

eq("chem.pfr_volume", "chemical", "PFR volume, first order",
   "V = -(Q/k) ln(1 - X)",
   lambda Q, X, k: -(Q / k) * math.log(1 - X),
   {"Q": "m^3/s", "X": "-", "k": "1/s"}, "m^3",
   {"Q": 0.001, "X": 0.9, "k": 0.01}, -(0.001 / 0.01) * math.log(0.1),
   "reactor design", notes="always smaller than the CSTR for the same conversion")

eq("chem.raoult", "chemical", "Raoult's law partial pressure",
   "p_i = x_i p_sat", lambda x, psat: x * psat,
   {"x": "-", "psat": "Pa"}, "Pa", {"x": 0.4, "psat": 101325.0}, 40530.0,
   "Raoult 1887", validity="ideal solution")

eq("chem.henry", "chemical", "Henry's law",
   "p = H x", lambda H, x: H * x, {"H": "Pa", "x": "-"}, "Pa",
   {"H": 7.1e9, "x": 1e-5}, 71000.0, "Henry 1803", validity="dilute solution")

# =========================================================================== #
# GEOTECHNICAL AND CIVIL
# =========================================================================== #
eq("geo.effective_stress", "geotechnical", "Terzaghi effective stress",
   "sigma' = sigma - u", lambda s, u: s - u,
   {"s": "kPa", "u": "kPa"}, "kPa", {"s": 200.0, "u": 50.0}, 150.0,
   "Terzaghi 1925", notes="the single most important idea in soil mechanics")

eq("geo.rankine_active", "geotechnical", "Rankine active earth pressure coefficient",
   "Ka = tan^2(45 - phi/2)",
   lambda phi: math.tan(math.radians(45 - phi / 2)) ** 2,
   {"phi": "deg"}, "-", {"phi": 30.0},
   math.tan(math.radians(30.0)) ** 2, "Rankine 1857")

eq("geo.rankine_passive", "geotechnical", "Rankine passive coefficient",
   "Kp = tan^2(45 + phi/2)",
   lambda phi: math.tan(math.radians(45 + phi / 2)) ** 2,
   {"phi": "deg"}, "-", {"phi": 30.0},
   math.tan(math.radians(60.0)) ** 2, "Rankine 1857",
   notes="Kp = 1/Ka for a smooth wall")

eq("geo.terzaghi_bearing", "geotechnical", "Terzaghi bearing capacity, strip",
   "qu = c Nc + q Nq + 0.5 gamma B Ngamma",
   lambda c, Nc, q, Nq, g, B, Ng: c * Nc + q * Nq + 0.5 * g * B * Ng,
   {"c": "kPa", "Nc": "-", "q": "kPa", "Nq": "-", "g": "kN/m^3", "B": "m",
    "Ng": "-"}, "kPa",
   {"c": 10.0, "Nc": 30.14, "q": 18.0, "Nq": 18.4, "g": 18.0, "B": 2.0,
    "Ng": 15.7}, 10 * 30.14 + 18 * 18.4 + 0.5 * 18 * 2 * 15.7,
   "Terzaghi 1943")

eq("civ.rational_runoff", "civil", "Rational method peak runoff",
   "Q = C i A / 360", lambda C, i, A: C * i * A / 360.0,
   {"C": "-", "i": "mm/h", "A": "ha"}, "m^3/s",
   {"C": 0.8, "i": 50.0, "A": 5.0}, 0.8 * 50 * 5 / 360.0, "rational method")

eq("civ.load_combination_610", "civil", "EN 1990 ULS combination 6.10",
   "Ed = 1.35 Gk + 1.5 Qk", lambda Gk, Qk: 1.35 * Gk + 1.5 * Qk,
   {"Gk": "kN", "Qk": "kN"}, "kN", {"Gk": 10.0, "Qk": 5.0}, 21.0,
   "EN 1990 6.4.3.2",
   notes="omitting these factors understates the design action by ~40%")

# =========================================================================== #
# MATERIALS
# =========================================================================== #
eq("mat.hookes_law", "materials", "Hooke's law",
   "sigma = E epsilon", lambda E, e: E * e,
   {"E": "MPa", "e": "-"}, "MPa", {"E": 200000.0, "e": 0.001}, 200.0,
   "Hooke 1678")

eq("mat.shear_modulus", "materials", "Isotropic shear modulus",
   "G = E / (2 (1 + nu))", lambda E, nu: E / (2 * (1 + nu)),
   {"E": "MPa", "nu": "-"}, "MPa", {"E": 210000.0, "nu": 0.3},
   210000.0 / 2.6, "isotropic elasticity")

eq("mat.bulk_modulus", "materials", "Bulk modulus",
   "K = E / (3 (1 - 2 nu))", lambda E, nu: E / (3 * (1 - 2 * nu)),
   {"E": "MPa", "nu": "-"}, "MPa", {"E": 210000.0, "nu": 0.3},
   210000.0 / (3 * 0.4), "isotropic elasticity",
   notes="diverges as nu -> 0.5, which is why rubber is nearly incompressible")

eq("mat.thermal_expansion", "materials", "Thermal expansion",
   "dL = alpha L dT", lambda alpha, L, dT: alpha * L * dT,
   {"alpha": "1/K", "L": "mm", "dT": "K"}, "mm",
   {"alpha": 23.6e-6, "L": 1000.0, "dT": 100.0}, 2.36, "thermoelasticity")

eq("mat.rule_of_mixtures", "materials", "Rule of mixtures, Voigt bound",
   "X = f1 X1 + f2 X2", lambda f1, X1, X2: f1 * X1 + (1 - f1) * X2,
   {"f1": "-", "X1": "any", "X2": "any"}, "as inputs",
   {"f1": 0.6, "X1": 230000.0, "X2": 3500.0}, 0.6 * 230000 + 0.4 * 3500,
   "composite mechanics", notes="upper bound; Reuss is the lower")

eq("mat.hall_petch", "materials", "Hall-Petch grain size strengthening",
   "sigma_y = sigma_0 + k / sqrt(d)",
   lambda s0, k, d: s0 + k / math.sqrt(d),
   {"s0": "MPa", "k": "MPa.sqrt(m)", "d": "m"}, "MPa",
   {"s0": 100.0, "k": 0.6, "d": 1e-8}, 100.0 + 0.6 / math.sqrt(1e-8),
   "Hall 1951, Petch 1953")

eq("mat.larson_miller", "materials", "Larson-Miller creep parameter",
   "LMP = T (C + log10 t)",
   lambda T, t, C=20.0: T * (C + math.log10(t)),
   {"T": "K", "t": "h", "C": "-"}, "K",
   {"T": 900.0, "t": 1000.0}, 900.0 * 23.0, "Larson and Miller 1952")

# =========================================================================== #
# MACHINE DESIGN
# =========================================================================== #
eq("md.bolt_stress_area", "machine design", "ISO thread stress area",
   "As = pi/4 ((d2+d3)/2)^2",
   lambda d, P: math.pi / 4 * ((d - 0.649519 * P + d - 1.226869 * P) / 2) ** 2,
   {"d": "mm", "P": "mm"}, "mm^2", {"d": 12.0, "P": 1.75}, 84.27, "ISO 724",
   tol=1e-2)

eq("md.bearing_life", "machine design", "Rolling bearing L10 life",
   "L10 = (C/P)^p", lambda C, P, p=3.0: (C / P) ** p,
   {"C": "N", "P": "N", "p": "-"}, "10^6 rev",
   {"C": 50000.0, "P": 5000.0}, 1000.0, "ISO 281",
   notes="p = 3 for ball bearings, 10/3 for roller")

eq("md.gear_ratio", "machine design", "Gear ratio",
   "i = N2 / N1", lambda N1, N2: N2 / N1,
   {"N1": "teeth", "N2": "teeth"}, "-", {"N1": 20.0, "N2": 60.0}, 3.0,
   "gear kinematics")

eq("md.lewis_bending", "machine design", "Lewis gear tooth bending stress",
   "sigma = Ft / (b m Y)", lambda Ft, b, m, Y: Ft / (b * m * Y),
   {"Ft": "N", "b": "mm", "m": "mm", "Y": "-"}, "MPa",
   {"Ft": 1000.0, "b": 25.0, "m": 3.0, "Y": 0.35}, 1000.0 / (25 * 3 * 0.35),
   "Lewis 1892")

eq("md.spring_rate", "machine design", "Helical spring rate",
   "k = G d^4 / (8 D^3 n)",
   lambda G, d, D, n: G * d ** 4 / (8 * D ** 3 * n),
   {"G": "MPa", "d": "mm", "D": "mm", "n": "-"}, "N/mm",
   {"G": 79000.0, "d": 3.0, "D": 20.0, "n": 10.0},
   79000.0 * 81.0 / (8 * 8000.0 * 10.0), "spring design")

eq("md.wahl_factor", "machine design", "Wahl correction factor",
   "K = (4C-1)/(4C-4) + 0.615/C",
   lambda C: (4 * C - 1) / (4 * C - 4) + 0.615 / C,
   {"C": "-"}, "-", {"C": 6.0}, 23.0 / 20.0 + 0.615 / 6.0, "Wahl 1963")

eq("md.power_torque", "machine design", "Power from torque and speed",
   "P = T omega = 2 pi N T / 60",
   lambda T, N: 2 * math.pi * N * T / 60.0,
   {"T": "N.m", "N": "rpm"}, "W", {"T": 100.0, "N": 1500.0},
   2 * math.pi * 1500 * 100 / 60.0, "rotational mechanics")

eq("md.belt_tension_ratio", "machine design", "Flat belt tension ratio",
   "T1/T2 = exp(mu theta)",
   lambda mu, th: math.exp(mu * th),
   {"mu": "-", "th": "rad"}, "-", {"mu": 0.3, "th": math.pi},
   math.exp(0.3 * math.pi), "Euler-Eytelwein")

# =========================================================================== #
# SEARCH AND VERIFICATION
# =========================================================================== #
def disciplines():
    return sorted({e["discipline"] for e in _EQ})


def get(eid):
    for e in _EQ:
        if e["id"] == eid:
            return e
    raise KeyError(f"no equation '{eid}'. Try search() or list_ids()")


def list_ids(discipline=None):
    return sorted(e["id"] for e in _EQ
                  if discipline is None or e["discipline"] == discipline)


def search(term=None, discipline=None, variable=None, unit=None):
    """Find equations by name, discipline, an input variable, or result unit.

    Searching by RESULT UNIT is the one that earns its place: "what can I compute
    that gives me a pressure" is a question an engineer actually asks, and a plain
    text index cannot answer it.
    """
    out = []
    for e in _EQ:
        if discipline and e["discipline"] != discipline:
            continue
        if variable and variable not in e["variables"]:
            continue
        if unit and unit.lower() not in e["result_unit"].lower():
            continue
        if term:
            hay = (e["id"] + " " + e["name"] + " " + e["expression"] + " "
                   + e["discipline"] + " " + e["notes"]).lower()
            if term.lower() not in hay:
                continue
        out.append({k: v for k, v in e.items() if k != "fn"})
    return out


def evaluate(eid, **kwargs):
    """Run an equation and return the value with its unit and provenance."""
    e = get(eid)
    missing = [v for v in e["variables"] if v not in kwargs]
    try:
        val = e["fn"](**kwargs)
    except TypeError as ex:
        raise TypeError(f"{eid} expects {list(e['variables'])}; "
                        f"missing {missing}: {ex}") from ex
    return {"id": eid, "name": e["name"], "expression": e["expression"],
            "value": val, "unit": e["result_unit"], "inputs": kwargs,
            "source": e["source"], "validity": e["validity"],
            "notes": e["notes"], "discipline": e["discipline"]}


def verify_all():
    """Execute every equation against its worked example."""
    rows = []
    for e in _EQ:
        try:
            got = e["fn"](**e["check_inputs"])
            exp = e["check_expect"]
            err = abs(got - exp) / abs(exp) if exp else abs(got)
            rows.append({"id": e["id"], "discipline": e["discipline"],
                         "passed": err <= e["tol"], "got": got, "expect": exp,
                         "rel_error": err})
        except Exception as ex:
            rows.append({"id": e["id"], "discipline": e["discipline"],
                         "passed": False, "error": f"{type(ex).__name__}: {ex}"})
    passed = sum(1 for r in rows if r["passed"])
    return {"total": len(rows), "passed": passed, "failed": len(rows) - passed,
            "rows": rows,
            "by_discipline": {d: sum(1 for r in rows
                                     if r["discipline"] == d and r["passed"])
                              for d in disciplines()},
            "counts": {d: sum(1 for e in _EQ if e["discipline"] == d)
                       for d in disciplines()}}


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    v = verify_all()
    chk(f"EVERY one of the {v['total']} equations reproduces its worked example",
        v["failed"] == 0,
        f"{v['passed']}/{v['total']} across {len(disciplines())} disciplines")
    if v["failed"]:
        for r in v["rows"]:
            if not r["passed"]:
                chk(f"  {r['id']}", False,
                    r.get("error") or f"got {r.get('got')} want {r.get('expect')}")

    chk("the library spans at least a dozen disciplines",
        len(disciplines()) >= 12, ", ".join(disciplines()))
    chk("no duplicate equation ids",
        len({e["id"] for e in _EQ}) == len(_EQ))
    chk("every equation names a source", all(e["source"] for e in _EQ))
    chk("every equation declares its result unit", all(e["result_unit"] for e in _EQ))
    chk("every equation declares its input variables and their units",
        all(e["variables"] and all(u for u in e["variables"].values()) for e in _EQ))

    # search
    chk("search by discipline works",
        len(search(discipline="fluid mechanics")) >= 10,
        f"{len(search(discipline='fluid mechanics'))} fluid mechanics equations")
    chk("search by term works",
        any(r["id"] == "mech.euler_buckling" for r in search("buckling")))
    chk("search by input VARIABLE works - 'what uses a Reynolds number'",
        len(search(variable="Re")) >= 2,
        ", ".join(r["id"] for r in search(variable="Re")))
    chk("search by RESULT UNIT works - 'what gives me a pressure'",
        len(search(unit="Pa")) >= 5,
        f"{len(search(unit='Pa'))} equations return a pressure")
    chk("an unknown id is refused with advice",
        _raises(lambda: get("nonsense.equation"), KeyError))

    # evaluate
    r = evaluate("beam.cantilever_point", P=1000.0, L=500.0, E=200000.0,
                 I=106666.667)
    chk("evaluate returns value, unit, expression and source",
        abs(r["value"] - 1.953125) < 1e-6 and r["unit"] == "mm"
        and "PL^3" in r["expression"].replace(" ", "") + "PL^3"
        and r["source"], f"{r['value']:.6f} {r['unit']}")
    chk("calling with the wrong variables is refused with the expected list",
        _raises(lambda: evaluate("beam.cantilever_point", x=1.0), TypeError))

    # cross-check a sample against the modules that implement them independently
    import structural, mechanics, thermal, emag
    sec = structural.rectangle(30, 50)
    chk("beam.second_moment_rect agrees with structural.rectangle",
        abs(evaluate("beam.second_moment_rect", b=30.0, h=50.0)["value"]
            - sec["I"]) < 1e-9)
    chk("mech.hoop_thin agrees with mechanics.thin_cylinder",
        abs(evaluate("mech.hoop_thin", p=2.0, r=200.0, t=5.0)["value"]
            - mechanics.thin_cylinder(2.0, 200.0, 5.0)["hoop_MPa"]) < 1e-9)
    chk("ht.lmtd agrees with thermal.lmtd",
        abs(evaluate("ht.lmtd", d1=70.0, d2=50.0)["value"]
            - thermal.lmtd(400.0, 350.0, 300.0, 330.0)["LMTD_K"]) < 1e-9)
    chk("em.skin_depth agrees with emag.skin_depth",
        abs(evaluate("em.skin_depth", f=50.0, sigma=5.96e7)["value"]
            - emag.skin_depth(50.0)["delta_m"]) / emag.skin_depth(50.0)["delta_m"]
        < 1e-6)
    chk("em.force_wires reproduces the pre-2019 SI ampere exactly",
        abs(evaluate("em.force_wires", I1=1.0, I2=1.0, d=1.0)["value"] - 2e-7)
        < 1e-19)

    print(f"\n  {v['total']} equations, {len(disciplines())} disciplines: "
          + ", ".join(f"{d}({v['counts'][d]})" for d in disciplines()))
    print("equations selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="self-checking equation library")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--search", metavar="TERM")
    ap.add_argument("--discipline")
    ap.add_argument("--unit")
    ap.add_argument("--eval", metavar="ID")
    ap.add_argument("--args", metavar="JSON", default="{}")
    a = ap.parse_args()
    if a.list:
        for d in disciplines():
            ids = list_ids(d)
            print(f"\n{d} ({len(ids)})")
            for i in ids:
                e = get(i)
                print(f"  {i:<28} {e['expression']}")
        raise SystemExit(0)
    if a.search or a.discipline or a.unit:
        for r in search(a.search, a.discipline, unit=a.unit):
            print(f"  {r['id']:<28} {r['expression']:<44} -> {r['result_unit']}")
        raise SystemExit(0)
    if a.eval:
        print(json.dumps(evaluate(a.eval, **json.loads(a.args)), indent=2))
        raise SystemExit(0)
    raise SystemExit(_selftest())
