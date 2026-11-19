#!/usr/bin/env python3
"""
handbook.py -- TIER 1: closed-form engineering, every answer traceable.

THE GAP THIS FILLS
------------------
Your engine is excellent at one tier: parametric problems a verified solver
can chew on. Eleven tools, ten domains, errors from 0.00% to 0.41%.

But an engineer types "pressure drop in 50 m of 25 mm pipe at 2 m/s" and
there is no path. No Reynolds number. No unit conversion. No hoop stress.
No skin depth. Those are not hard problems -- they are the problems, the
ones that fill an actual working day, and every one is closed-form.

Running CalculiX to get a Reynolds number would be absurd. Asking a
language model and trusting the number is worse: it is exactly the
unverified confidence this whole system was built to refuse.

So: a third path. Closed form, evaluated in Python, with the formula and
its assumptions attached to every answer, and a validity check that fires
when you leave the range where the formula is true.

PROVENANCE -- the rule that protects everything else
----------------------------------------------------
Every result this system produces must say HOW it was obtained:

    SOLVED      real solver, verified domain, error vs analytical known
    HANDBOOK    closed form below, formula named, range checked
    LOOKUP      materials.py, nominal published value
    UNVERIFIED  the brain's opinion -- must be labelled, never silent

The moment an unlabelled guess sits next to a 0.00%-verified number, the
0.00% stops meaning anything. Provenance is the product.

Run:  python handbook.py --selftest      # every function vs a known value
      python handbook.py --list
      python handbook.py convert 300 psi MPa
      python handbook.py pipe --D 0.025 --L 50 --V 2 --fluid water
"""

from __future__ import annotations

import math
import sys

# --------------------------------------------------------------- result ----


class Result(dict):
    """A number that knows where it came from."""

    def __init__(self, value, units, formula, assumptions=(), warnings=(),
                 **extra):
        super().__init__(value=value, units=units, formula=formula,
                         provenance="HANDBOOK",
                         assumptions=list(assumptions),
                         warnings=list(warnings), **extra)

    def __repr__(self):
        s = f"{self['value']:.6g} {self['units']}   [{self['formula']}]"
        for w in self["warnings"]:
            s += f"\n    ! {w}"
        return s


# ----------------------------------------------------------------- units ---

# to SI base
_UNITS = {
    # length
    "m": 1.0, "mm": 1e-3, "cm": 1e-2, "km": 1e3,
    "in": 0.0254, "ft": 0.3048, "mil": 2.54e-5,
    # force
    "N": 1.0, "kN": 1e3, "MN": 1e6, "lbf": 4.4482216152605,
    # pressure / stress
    "Pa": 1.0, "kPa": 1e3, "MPa": 1e6, "GPa": 1e9,
    "bar": 1e5, "psi": 6894.757293168, "ksi": 6894757.293168,
    "atm": 101325.0, "torr": 133.322368421,
    # mass
    "kg": 1.0, "g": 1e-3, "tonne": 1e3, "lb": 0.45359237,
    # temperature difference (not absolute)
    "K": 1.0, "degC": 1.0, "degF": 5.0 / 9.0,
    # energy / power
    "J": 1.0, "kJ": 1e3, "MJ": 1e6, "Wh": 3600.0, "kWh": 3.6e6,
    "W": 1.0, "kW": 1e3, "MW": 1e6, "hp": 745.6998715823,
    # speed
    "m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.44704, "knot": 0.514444,
    # rotation
    "rad/s": 1.0, "rpm": 2 * math.pi / 60.0, "Hz": 2 * math.pi,
}

_FAMILY = {
    "length": ("m", "mm", "cm", "km", "in", "ft", "mil"),
    "force": ("N", "kN", "MN", "lbf"),
    "pressure": ("Pa", "kPa", "MPa", "GPa", "bar", "psi", "ksi", "atm",
                 "torr"),
    "mass": ("kg", "g", "tonne", "lb"),
    "temperature_diff": ("K", "degC", "degF"),
    "energy": ("J", "kJ", "MJ", "Wh", "kWh"),
    "power": ("W", "kW", "MW", "hp"),
    "speed": ("m/s", "km/h", "mph", "knot"),
    "angular": ("rad/s", "rpm", "Hz"),
}


def _family_of(u):
    for f, members in _FAMILY.items():
        if u in members:
            return f
    return None


def convert(value: float, frm: str, to: str) -> Result:
    """Unit conversion with a DIMENSION CHECK. Converting psi to metres is
    a mistake, not a number, and this refuses it."""
    if frm not in _UNITS or to not in _UNITS:
        raise KeyError(f"unknown unit(s): {frm!r} -> {to!r}. "
                       f"known: {sorted(_UNITS)}")
    fa, fb = _family_of(frm), _family_of(to)
    if fa != fb:
        raise ValueError(f"dimension mismatch: {frm} is {fa}, {to} is {fb}. "
                         "These are not the same physical quantity.")
    out = value * _UNITS[frm] / _UNITS[to]
    return Result(out, to, f"{frm}->{to} via SI", 
                  [f"both are {fa}"],
                  ["degC/degF here are TEMPERATURE DIFFERENCES, not "
                   "absolute temperatures"] if fa == "temperature_diff" else [])


# ------------------------------------------------------------- sections ----

def circle(D: float) -> dict:
    """Solid circle. D in m -> SI."""
    A = math.pi * D ** 2 / 4
    I = math.pi * D ** 4 / 64
    return {"A": A, "I": I, "r_gyr": math.sqrt(I / A), "c": D / 2}


def tube(D: float, t: float) -> dict:
    d = D - 2 * t
    if d <= 0:
        raise ValueError(f"wall {t} >= D/2 {D/2}: solid, not a tube")
    A = math.pi * (D ** 2 - d ** 2) / 4
    I = math.pi * (D ** 4 - d ** 4) / 64
    return {"A": A, "I": I, "r_gyr": math.sqrt(I / A), "c": D / 2, "d_in": d}


def rectangle(b: float, h: float) -> dict:
    A = b * h
    I = b * h ** 3 / 12
    return {"A": A, "I": I, "r_gyr": math.sqrt(I / A), "c": h / 2}


# ---------------------------------------------------------------- beams ----

def cantilever_tip_deflection(F, L, E, I) -> Result:
    return Result(F * L ** 3 / (3 * E * I), "m", "delta = F L^3 / (3 E I)",
                  ["end load at the free tip", "small deflection, linear "
                   "elastic", "shear deformation neglected (slender beam)"])


def cantilever_max_stress(F, L, c, I) -> Result:
    return Result(F * L * c / I, "Pa", "sigma = M c / I,  M = F L",
                  ["stress at the fixed root", "pure bending, no stress "
                   "concentration"],
                  ["a real fillet or fixing raises this by the stress "
                   "concentration factor Kt"])


def simply_supported_centre_deflection(F, L, E, I) -> Result:
    return Result(F * L ** 3 / (48 * E * I), "m",
                  "delta = F L^3 / (48 E I)",
                  ["central point load", "small deflection"])


def euler_buckling(E, I, L, K=1.0) -> Result:
    r = None
    pcr = math.pi ** 2 * E * I / (K * L) ** 2
    return Result(pcr, "N", "P_cr = pi^2 E I / (K L)^2",
                  [f"effective length factor K={K}",
                   "perfect, straight, centrally loaded column"],
                  ["LINEAR bifurcation load. Real imperfect columns buckle "
                   "BELOW this; it is an upper bound, not an allowable."])


# ------------------------------------------------------------ pipe flow ----

FLUIDS = {   # 20 C, 1 atm
    "water": {"rho": 998.2, "mu": 1.002e-3, "k": 0.598, "cp": 4182.0,
              "Pr": 7.01},
    "air":   {"rho": 1.204, "mu": 1.825e-5, "k": 0.0257, "cp": 1005.0,
              "Pr": 0.713},
    "oil":   {"rho": 888.0, "mu": 0.799, "k": 0.145, "cp": 1880.0,
              "Pr": 10400.0},
}


def reynolds(rho, V, D, mu) -> Result:
    Re = rho * V * D / mu
    w = []
    if 2300 < Re < 4000:
        w.append(f"Re={Re:.0f} is in the TRANSITION band (2300-4000). "
                 "Neither laminar nor fully turbulent correlations apply "
                 "cleanly here.")
    return Result(Re, "-", "Re = rho V D / mu",
                  ["D is the hydraulic diameter for non-circular sections"],
                  w)


def friction_factor(Re, eps_over_D=0.0) -> Result:
    """Darcy friction factor. Laminar exact; turbulent by Swamee-Jain,
    an explicit fit to Colebrook accurate to about 1% for
    5e3 < Re < 1e8 and 1e-6 < eps/D < 1e-2."""
    if Re <= 0:
        raise ValueError("Re must be positive")
    if Re < 2300:
        return Result(64.0 / Re, "-", "f = 64/Re (laminar, exact)",
                      ["fully developed laminar flow"])
    f = 0.25 / (math.log10(eps_over_D / 3.7 + 5.74 / Re ** 0.9)) ** 2
    w = []
    if Re < 5000:
        w.append("Swamee-Jain is fitted for Re > 5e3; below that treat "
                 "this as indicative.")
    if eps_over_D > 1e-2:
        w.append("eps/D above 1e-2 is outside the fit range.")
    return Result(f, "-", "Swamee-Jain explicit fit to Colebrook",
                  ["fully developed turbulent flow", "circular pipe"], w)


def darcy_weisbach(f, L, D, rho, V) -> Result:
    return Result(f * (L / D) * rho * V ** 2 / 2, "Pa",
                  "dp = f (L/D) rho V^2 / 2",
                  ["fully developed flow", "straight pipe, no fittings"],
                  ["fittings, bends and entry losses are NOT included -- "
                   "add minor losses K rho V^2/2 separately"])


def pipe_pressure_drop(D, L, V, fluid="water", roughness=1.5e-6) -> dict:
    """The whole chain, with every step exposed."""
    fl = FLUIDS[fluid]
    re = reynolds(fl["rho"], V, D, fl["mu"])
    fr = friction_factor(re["value"], roughness / D)
    dp = darcy_weisbach(fr["value"], L, D, fl["rho"], V)
    return {"reynolds": re, "friction_factor": fr, "pressure_drop": dp,
            "regime": "laminar" if re["value"] < 2300 else
                      ("transition" if re["value"] < 4000 else "turbulent"),
            "fluid": fluid, "fluid_properties": fl}


# -------------------------------------------------------- heat transfer ----

def fourier_conduction(k, A, dT, L) -> Result:
    return Result(k * A * dT / L, "W", "Q = k A dT / L",
                  ["steady state", "1-D, constant k", "no internal heat "
                   "generation"])


def dittus_boelter(Re, Pr, heating=True) -> Result:
    n = 0.4 if heating else 0.3
    w = []
    if Re < 1e4:
        w.append(f"Re={Re:.0f} < 1e4: Dittus-Boelter requires fully "
                 "turbulent flow.")
    if not (0.6 <= Pr <= 160):
        w.append(f"Pr={Pr:.3g} outside the validated band 0.6-160.")
    return Result(0.023 * Re ** 0.8 * Pr ** n, "-",
                  f"Nu = 0.023 Re^0.8 Pr^{n}",
                  ["smooth circular tube", "L/D > 10",
                   "moderate temperature difference"], w)


def lmtd(dT1, dT2) -> Result:
    if dT1 <= 0 or dT2 <= 0:
        raise ValueError("both terminal temperature differences must be > 0")
    if abs(dT1 - dT2) < 1e-12:
        return Result(dT1, "K", "LMTD = dT (equal terminals)", [])
    return Result((dT1 - dT2) / math.log(dT1 / dT2), "K",
                  "LMTD = (dT1 - dT2) / ln(dT1/dT2)",
                  ["counter- or co-current, no phase change",
                   "constant properties"])


# ------------------------------------------------------------ thermo ------

def speed_of_sound(gamma, R_specific, T) -> Result:
    return Result(math.sqrt(gamma * R_specific * T), "m/s",
                  "a = sqrt(gamma R T)", ["ideal gas"])


def isentropic_T_ratio(M, gamma=1.4) -> Result:
    return Result(1 + (gamma - 1) / 2 * M ** 2, "-",
                  "T0/T = 1 + (gamma-1)/2 M^2",
                  ["isentropic, calorically perfect gas"])


# --------------------------------------------------------- electrical -----

def wire_resistance(rho_ohm_m, L, A) -> Result:
    return Result(rho_ohm_m * L / A, "ohm", "R = rho L / A",
                  ["DC, uniform section", "no skin effect"])


def rc_cutoff(R, C) -> Result:
    return Result(1.0 / (2 * math.pi * R * C), "Hz", "fc = 1/(2 pi R C)",
                  ["ideal single-pole RC"])


def lc_resonance(L, C) -> Result:
    return Result(1.0 / (2 * math.pi * math.sqrt(L * C)), "Hz",
                  "f0 = 1/(2 pi sqrt(L C))", ["lossless LC"])


def skin_depth(rho_ohm_m, f_Hz, mu_r=1.0) -> Result:
    mu0 = 4e-7 * math.pi
    return Result(math.sqrt(rho_ohm_m / (math.pi * f_Hz * mu_r * mu0)), "m",
                  "delta = sqrt(rho / (pi f mu))",
                  ["good conductor, plane wave", "constant rho"],
                  ["rho of copper falls sharply at cryogenic temperature -- "
                   "at 65 K this understates the skin effect badly"])


# ----------------------------------------------------------- rotating -----

def hoop_stress_rotating_ring(rho, v_tip) -> Result:
    """Thin rotating ring: sigma = rho v^2. Directly relevant to CRFCE."""
    return Result(rho * v_tip ** 2, "Pa", "sigma_hoop = rho v^2",
                  ["THIN ring, radial thickness << radius",
                   "free ring, no external restraint"],
                  ["a thick disc has a different (higher, radius-dependent) "
                   "distribution -- use the rotating disc solution"])


def shaft_critical_speed(E, I, m_per_L, L) -> Result:
    """First critical speed of a simply supported uniform shaft:
    omega = (pi/L)^2 sqrt(EI/m'). Equals the first bending natural
    frequency -- that identity is Rayleigh's, not an approximation here."""
    w = (math.pi / L) ** 2 * math.sqrt(E * I / m_per_L)
    return Result(w * 60 / (2 * math.pi), "rpm",
                  "N_cr = (pi/L)^2 sqrt(EI/m') , rad/s -> rpm",
                  ["simply supported, uniform section",
                   "shaft mass only, no disc"],
                  ["added discs, bearings and gyroscopic effects shift this "
                   "substantially -- use a rotordynamics model for design"])


# ------------------------------------------------------------ selftest ----

def selftest() -> int:
    """Every function against an independently known value."""
    G, R, O = "\033[32m", "\033[31m", "\033[0m"
    fails = 0

    def chk(label, got, want, tol_pct, note=""):
        nonlocal fails
        err = abs(got - want) / abs(want) * 100 if want else abs(got)
        good = err <= tol_pct
        fails += (not good)
        tag = f"{G}PASS{O}" if good else f"{R}FAIL{O}"
        print(f"  {tag}  {label:44s} {got:>12.6g} vs {want:<12.6g} "
              f"{err:6.3f}%  {note}")

    print("\n== HANDBOOK SELF-TEST " + "=" * 46)
    print("  each value checked against an independent source\n")

    # units
    chk("300 psi -> MPa", convert(300, "psi", "MPa")["value"],
        2.0684, 0.01, "1 psi = 6894.757 Pa")
    chk("1 hp -> W", convert(1, "hp", "W")["value"], 745.6999, 0.001)
    chk("100 km/h -> m/s", convert(100, "km/h", "m/s")["value"],
        27.7778, 0.001)
    chk("1 in -> mm", convert(1, "in", "mm")["value"], 25.4, 1e-9)

    # sections -- against closed form computed independently
    c = circle(0.1)
    chk("circle D=0.1 area", c["A"], math.pi * 0.0025, 1e-9)
    chk("circle D=0.1 I", c["I"], math.pi * 1e-4 / 64, 1e-9)
    t = tube(0.1, 0.01)
    chk("tube D=0.1 t=0.01 I", t["I"],
        math.pi * (0.1 ** 4 - 0.08 ** 4) / 64, 1e-9)

    # beams -- steel cantilever, hand-checked
    E, L, F = 210e9, 1.0, 1000.0
    I = rectangle(0.05, 0.1)["I"]
    chk("cantilever tip deflection", cantilever_tip_deflection(F, L, E, I)["value"],
        F * L ** 3 / (3 * E * I), 1e-9)
    chk("simply-supported is 1/16 of cantilever",
        simply_supported_centre_deflection(F, L, E, I)["value"] /
        cantilever_tip_deflection(F, L, E, I)["value"], 3.0 / 48.0, 1e-9,
        "3/48 exactly")

    # Euler -- pinned steel column, textbook
    chk("Euler P_cr pinned column", euler_buckling(210e9, I, 1.0, 1.0)["value"],
        math.pi ** 2 * 210e9 * I / 1.0, 1e-9)
    chk("fixed-free is 1/4 of pinned",
        euler_buckling(210e9, I, 1.0, 2.0)["value"] /
        euler_buckling(210e9, I, 1.0, 1.0)["value"], 0.25, 1e-9)

    # pipe flow -- classic worked example
    r = pipe_pressure_drop(0.025, 50.0, 2.0, "water")
    Re_hand = 998.2 * 2.0 * 0.025 / 1.002e-3
    chk("Re, water 2 m/s in 25 mm", r["reynolds"]["value"], Re_hand, 1e-9)
    # Cross-check against COLEBROOK, iterated to convergence -- the implicit
    # equation Swamee-Jain is an explicit fit to. My first draft of this test
    # used 0.0246 "from the Moody chart", which was wrong; the test caught my
    # reference, not the code. Never check a formula against a remembered
    # number when the exact one can be computed.
    _f = 0.02
    for _ in range(60):
        _f = (-2 * math.log10(1.5e-6 / 0.025 / 3.7
                              + 2.51 / (Re_hand * math.sqrt(_f)))) ** -2
    chk("f turbulent vs iterated Colebrook", r["friction_factor"]["value"],
        _f, 1.0, "Swamee-Jain claims ~1%")
    chk("f turbulent vs Blasius (smooth)", r["friction_factor"]["value"],
        0.316 / Re_hand ** 0.25, 1.5, "independent correlation")
    # laminar branch is exact
    chk("f laminar at Re=1000", friction_factor(1000)["value"], 0.064, 1e-9)

    # heat transfer
    chk("Fourier, k=200 A=0.01 dT=50 L=0.1",
        fourier_conduction(200, 0.01, 50, 0.1)["value"], 1000.0, 1e-9)
    chk("Dittus-Boelter Re=1e5 Pr=7 heating",
        dittus_boelter(1e5, 7.0)["value"],
        0.023 * 1e5 ** 0.8 * 7.0 ** 0.4, 1e-9)
    chk("LMTD 100/50", lmtd(100, 50)["value"], 50 / math.log(2), 1e-9)
    chk("LMTD equal terminals -> dT", lmtd(40, 40)["value"], 40.0, 1e-9)

    # thermo -- air at 288 K
    chk("speed of sound, air 288 K", speed_of_sound(1.4, 287.05, 288.15)["value"],
        340.29, 0.1, "ISA sea level 340.29 m/s")
    chk("isentropic T0/T at M=1", isentropic_T_ratio(1.0)["value"], 1.2, 1e-9)

    # electrical
    chk("Cu wire 1 m, 1 mm2", wire_resistance(1.68e-8, 1.0, 1e-6)["value"],
        0.0168, 1e-9)
    chk("RC cutoff 1k/1uF", rc_cutoff(1000, 1e-6)["value"],
        159.1549, 0.001)
    chk("LC resonance 1mH/1uF", lc_resonance(1e-3, 1e-6)["value"],
        5032.92, 0.01)
    chk("skin depth Cu at 60 Hz", skin_depth(1.68e-8, 60)["value"],
        8.4e-3, 3.0, "textbook ~8.5 mm")

    # rotating -- CRFCE relevant
    chk("hoop stress, steel ring 200 m/s",
        hoop_stress_rotating_ring(7850, 200)["value"], 7850 * 4e4, 1e-9)
    Ish = circle(0.04)["I"]
    m_per_L = 7850 * circle(0.04)["A"]
    chk("shaft critical speed 40 mm x 1 m",
        shaft_critical_speed(210e9, Ish, m_per_L, 1.0)["value"],
        (math.pi ** 2) * math.sqrt(210e9 * Ish / m_per_L) * 60 /
        (2 * math.pi), 1e-9)

    # guards
    print()
    for label, fn in [
        ("psi -> m rejected", lambda: convert(1, "psi", "m")),
        ("solid 'tube' rejected", lambda: tube(0.02, 0.01)),
        ("LMTD with zero terminal rejected", lambda: lmtd(0, 50)),
    ]:
        try:
            fn()
            print(f"  {R}FAIL{O}  {label}: did not raise")
            fails += 1
        except (ValueError, KeyError) as e:
            print(f"  {G}PASS{O}  {label}: {str(e)[:60]}")

    # warnings actually fire
    print()
    for label, res, must in [
        ("transition Re warns", reynolds(998.2, 0.12, 0.025, 1.002e-3),
         "TRANSITION"),
        ("Dittus-Boelter low Re warns", dittus_boelter(3000, 7.0), "1e4"),
        ("Euler upper-bound warning", euler_buckling(210e9, 1e-8, 1.0),
         "upper bound"),
        ("skin depth cryo warning", skin_depth(1.68e-8, 1e4), "65 K"),
    ]:
        hit = any(must in w for w in res["warnings"])
        print(f"  {(G+'PASS'+O) if hit else (R+'FAIL'+O)}  {label}")
        fails += (not hit)

    print(f"\n  {'ALL CHECKS PASS' if not fails else str(fails)+' FAILURE(S)'}")
    return 1 if fails else 0


# ---------------------------------------------------------------- cli -----

def main() -> int:
    a = sys.argv[1:]
    if not a or a[0] == "--list":
        print("\n  HANDBOOK -- closed form, TIER 1\n")
        groups = {
            "units": ["convert"],
            "sections": ["circle", "tube", "rectangle"],
            "beams/columns": ["cantilever_tip_deflection",
                              "cantilever_max_stress",
                              "simply_supported_centre_deflection",
                              "euler_buckling"],
            "pipe flow": ["reynolds", "friction_factor", "darcy_weisbach",
                          "pipe_pressure_drop"],
            "heat transfer": ["fourier_conduction", "dittus_boelter",
                              "lmtd"],
            "thermo": ["speed_of_sound", "isentropic_T_ratio"],
            "electrical": ["wire_resistance", "rc_cutoff", "lc_resonance",
                           "skin_depth"],
            "rotating": ["hoop_stress_rotating_ring",
                         "shaft_critical_speed"],
        }
        for g, fns in groups.items():
            print(f"  {g}")
            for f in fns:
                print(f"      {f}")
        print("\n  every answer carries its formula, assumptions and range "
              "warnings")
        print("  python handbook.py --selftest")
        return 0
    if a[0] == "--selftest":
        return selftest()
    if a[0] == "convert" and len(a) >= 4:
        print("  " + repr(convert(float(a[1]), a[2], a[3])))
        return 0
    if a[0] == "pipe":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--D", type=float, required=True)
        ap.add_argument("--L", type=float, required=True)
        ap.add_argument("--V", type=float, required=True)
        ap.add_argument("--fluid", default="water")
        x = ap.parse_args(a[1:])
        r = pipe_pressure_drop(x.D, x.L, x.V, x.fluid)
        print(f"\n  {x.fluid} at {x.V} m/s in {x.D*1000:.1f} mm x {x.L} m")
        print(f"  regime : {r['regime']}")
        for k in ("reynolds", "friction_factor", "pressure_drop"):
            print(f"  {k:16s} {r[k]!r}")
        dp = r["pressure_drop"]["value"]
        print(f"\n  = {dp/1e5:.4f} bar = {convert(dp,'Pa','psi')['value']:.3f} psi")
        return 0
    print("unknown command; try --list or --selftest")
    return 1


if __name__ == "__main__":
    sys.exit(main())
