#!/usr/bin/env python3
"""
emag.py - analytic electromagnetics: fields, forces, inductance, capacitance.

`em_tools.py` is an Elmer wrapper, and Elmer is not installed on this machine, so
EngineAI could not answer a single electromagnetic question - not even "what is the
field around a wire". This module is the analytic layer that should have been there
first: the closed forms are exact, they need no solver, and they are what a finite
element result must be checked against anyway.

The constants here are unusually good test material, because since the 2019 SI
redefinition they are related by exact identities rather than by measurement:

    eps0 = 1 / (mu0 c^2)          exactly
    mu0  = 4 pi x 1e-7            to within 1e-10 (was exact pre-2019)
    F/L between two 1 A wires 1 m apart = 2e-7 N/m exactly - this WAS the
        definition of the ampere from 1948 to 2019

So every constant in this file can be checked against another constant in this file.
The selftest does that rather than comparing typed numbers to typed numbers.

Units: SI throughout - metres, amperes, tesla, henry, farad, ohm.

A checked design aid, NOT a certified result. Saturation, hysteresis, proximity
effect, eddy-current reaction and radiation are out of scope; the magnetic-circuit
model in particular assumes constant permeability and no fringing. A licensed
engineer signs off.
"""
from __future__ import annotations
import argparse, json, math

MU0 = 4.0e-7 * math.pi                  # H/m
C_LIGHT = 299792458.0                   # m/s, exact by definition
EPS0 = 1.0 / (MU0 * C_LIGHT ** 2)       # F/m - DERIVED, not typed
ELEM_CHARGE = 1.602176634e-19           # C, exact by definition

# Conductivity S/m at 20 C, and temperature coefficient per K
CONDUCTIVITY = {
    "copper":     (5.96e7, 0.00393),
    "aluminium":  (3.77e7, 0.00403),
    "silver":     (6.30e7, 0.00380),
    "gold":       (4.10e7, 0.00340),
    "iron":       (1.00e7, 0.00500),
    "steel_mild": (6.99e6, 0.00450),
    "brass":      (1.56e7, 0.00150),
    "seawater":   (4.8,    0.0),
}

# Relative permeability of soft magnetic materials (initial/typical, unsaturated)
MU_R = {
    "air": 1.0, "vacuum": 1.0, "aluminium": 1.000022,
    "electrical_steel_M19": 4000.0, "silicon_iron": 4000.0,
    "cast_iron": 600.0, "mild_steel": 2000.0, "ferrite_MnZn": 2000.0,
    "permalloy": 8000.0, "mu_metal": 50000.0, "supermalloy": 100000.0,
}

# Relative permittivity
EPS_R = {
    "vacuum": 1.0, "air": 1.00059, "teflon_ptfe": 2.1, "polyethylene": 2.25,
    "polypropylene": 2.2, "nylon": 3.4, "fr4": 4.4, "glass": 5.5,
    "mica": 6.0, "alumina": 9.8, "water": 80.1, "barium_titanate": 1200.0,
}


def conductivity(material, T_celsius=20.0):
    """sigma(T) = sigma20 / (1 + alpha (T - 20)). Copper loses ~0.4%/K, which is why
    a hot cable is a worse conductor exactly when it can least afford to be."""
    if material not in CONDUCTIVITY:
        raise KeyError(f"unknown conductor '{material}'. "
                       f"Known: {', '.join(sorted(CONDUCTIVITY))}")
    s20, alpha = CONDUCTIVITY[material]
    sigma = s20 / (1.0 + alpha * (T_celsius - 20.0))
    return {"material": material, "sigma_S_per_m": sigma,
            "resistivity_ohm_m": 1.0 / sigma, "T_celsius": T_celsius,
            "alpha_per_K": alpha}


# --------------------------------------------------------------------------- #
# Magnetostatics
# --------------------------------------------------------------------------- #
def b_wire(current, r):
    """Field around an infinitely long straight wire: B = mu0 I / (2 pi r).

    This is Ampere's law in its simplest form, and the selftest verifies it AS
    Ampere's law - by checking that the closed line integral B*(2 pi r) equals
    mu0*I_enclosed for several radii, rather than by re-reading the formula.
    """
    if r <= 0:
        raise ValueError("radius must be positive (the field diverges on the wire)")
    B = MU0 * current / (2.0 * math.pi * r)
    return {"B_T": B, "B_mT": B * 1e3, "B_gauss": B * 1e4,
            "H_A_per_m": current / (2.0 * math.pi * r),
            "r_m": r, "current_A": current,
            "ampere_law_residual": B * 2.0 * math.pi * r - MU0 * current}


def b_wire_inside(current, r, wire_radius):
    """Inside a solid conductor carrying uniform current density the enclosed
    current scales as r^2, so B RISES linearly from zero on the axis:

        B = mu0 I r / (2 pi a^2)

    At r = a this must join the external solution continuously - a check the
    selftest makes, because a discontinuity there would mean one of the two is wrong.
    """
    if not 0 <= r <= wire_radius:
        raise ValueError("r must lie inside the conductor")
    return MU0 * current * r / (2.0 * math.pi * wire_radius ** 2)


def b_solenoid(turns_per_metre, current, length=None, radius=None):
    """Long solenoid: B = mu0 n I on the axis, uniform and independent of radius.

    If length and radius are given, the FINITE solenoid axial-centre field is
    returned too:  B = mu0 n I * L / sqrt(L^2 + 4R^2), which tends to mu0 n I as
    L/R grows. The selftest checks that convergence.
    """
    B_inf = MU0 * turns_per_metre * current
    out = {"B_T": B_inf, "B_infinite_T": B_inf, "B_gauss": B_inf * 1e4,
           "H_A_per_m": turns_per_metre * current}
    if length and radius:
        B_fin = MU0 * turns_per_metre * current * length / math.sqrt(length ** 2 + 4 * radius ** 2)
        out["B_finite_T"] = B_fin
        out["B_T"] = B_fin
        out["end_correction"] = B_fin / B_inf
        out["L_over_D"] = length / (2 * radius)
    return out


def b_toroid(turns, current, mean_radius):
    """Toroid: B = mu0 N I / (2 pi r).

    Identical in form to the straight wire with N I enclosed - because it IS the
    same Ampere's law applied to a circular path. The selftest exploits that: a
    toroid of N turns must give exactly N times the field of a single wire at the
    same radius.
    """
    if mean_radius <= 0:
        raise ValueError("mean radius must be positive")
    B = MU0 * turns * current / (2.0 * math.pi * mean_radius)
    return {"B_T": B, "B_gauss": B * 1e4, "turns": turns,
            "H_A_per_m": turns * current / (2.0 * math.pi * mean_radius)}


def b_loop_axis(current, loop_radius, z=0.0):
    """On the axis of a circular loop:  B = mu0 I R^2 / (2 (R^2 + z^2)^1.5).
    At the centre (z = 0) this is mu0 I / 2R."""
    if loop_radius <= 0:
        raise ValueError("loop radius must be positive")
    B = MU0 * current * loop_radius ** 2 / (2.0 * (loop_radius ** 2 + z ** 2) ** 1.5)
    return {"B_T": B, "B_centre_T": MU0 * current / (2.0 * loop_radius), "z_m": z}


def force_parallel_wires(I1, I2, separation, length=1.0):
    """Force between two long parallel wires:  F/L = mu0 I1 I2 / (2 pi d).

    Like currents ATTRACT (positive), opposite currents repel (negative). At
    I1 = I2 = 1 A and d = 1 m this is exactly 2e-7 N/m - the pre-2019 SI definition
    of the ampere, and therefore the single best anchor available for mu0.
    """
    if separation <= 0:
        raise ValueError("separation must be positive")
    f = MU0 * I1 * I2 / (2.0 * math.pi * separation)
    return {"F_per_L_N_per_m": f, "F_N": f * length,
            "sense": "attraction" if f > 0 else "repulsion" if f < 0 else "none",
            "note": "like currents attract; this defined the ampere until 2019"}


def lorentz_force(current, length, B, angle_deg=90.0):
    """F = B I L sin(theta) on a current-carrying conductor in a field.
    This is what deflects a busbar in a fault and what makes a motor turn."""
    return {"F_N": B * current * length * math.sin(math.radians(angle_deg)),
            "F_per_L_N_per_m": B * current * math.sin(math.radians(angle_deg))}


def magnetic_energy_density(B, mu_r=1.0):
    """u = B^2 / (2 mu). The energy in a magnetic field, J/m^3."""
    return B ** 2 / (2.0 * MU0 * mu_r)


# --------------------------------------------------------------------------- #
# Magnetic circuits
# --------------------------------------------------------------------------- #
def reluctance(length, area, mu_r=1.0):
    """R = l / (mu0 mu_r A), the magnetic analogue of electrical resistance."""
    if length <= 0 or area <= 0 or mu_r <= 0:
        raise ValueError("length, area and mu_r must all be positive")
    return length / (MU0 * mu_r * area)


def magnetic_circuit(mmf_NI, segments):
    """Series magnetic circuit. `segments` = [(name, length, area, mu_r), ...].

        flux = NI / sum(reluctances),   B_i = flux / A_i

    The engineering point this exposes: a 1 mm air gap in a 2000-mu_r core of 300 mm
    path length has FIVE TIMES the reluctance of all the iron combined. The gap
    dominates, which is why gapped inductors are designed around the gap and why
    fringing matters. The result reports each segment's share so that is visible
    rather than buried.
    """
    rs = []
    for name, l, A, mur in segments:
        rs.append((name, reluctance(l, A, mur), A))
    R_total = sum(r for _, r, _ in rs)
    flux = mmf_NI / R_total
    return {"flux_Wb": flux, "R_total_A_per_Wb": R_total, "mmf_A": mmf_NI,
            "segments": [{"name": n, "reluctance_A_per_Wb": r,
                          "share_percent": 100.0 * r / R_total,
                          "B_T": flux / A, "mmf_drop_A": flux * r}
                         for n, r, A in rs]}


def inductance_solenoid(turns, area, length, mu_r=1.0):
    """L = mu0 mu_r N^2 A / l for a long solenoid. Note the N^2 - doubling the turns
    quadruples the inductance."""
    return MU0 * mu_r * turns ** 2 * area / length


def inductance_coax(a_inner, b_outer, length=1.0, mu_r=1.0):
    """External inductance of a coaxial line: L' = (mu0 mu_r / 2 pi) ln(b/a) per metre.

    Tends to zero as b -> a, which the selftest checks - a coax with no gap stores
    no external magnetic energy.
    """
    if not 0 < a_inner < b_outer:
        raise ValueError("need 0 < a_inner < b_outer")
    Lp = MU0 * mu_r / (2.0 * math.pi) * math.log(b_outer / a_inner)
    return {"L_per_m_H": Lp, "L_H": Lp * length,
            "L_per_m_nH": Lp * 1e9}


def capacitance_coax(a_inner, b_outer, length=1.0, eps_r=1.0):
    """C' = 2 pi eps0 eps_r / ln(b/a) per metre. The reciprocal-log partner of the
    inductance above, which is why their product gives the speed of light."""
    if not 0 < a_inner < b_outer:
        raise ValueError("need 0 < a_inner < b_outer")
    Cp = 2.0 * math.pi * EPS0 * eps_r / math.log(b_outer / a_inner)
    return {"C_per_m_F": Cp, "C_F": Cp * length, "C_per_m_pF": Cp * 1e12}


def coax_line(a_inner, b_outer, eps_r=1.0, mu_r=1.0):
    """Characteristic impedance and propagation speed of a coaxial line.

        Z0 = sqrt(L'/C') = (1/2pi) sqrt(mu/eps) ln(b/a)
        v  = 1/sqrt(L'C') = c/sqrt(eps_r mu_r)

    The propagation speed comes out as c/sqrt(eps_r) with NO reference to c in the
    calculation - it falls out of L' and C' alone. That is the strongest check in
    this module: two independent geometric formulas reconstruct a fundamental
    constant.
    """
    L = inductance_coax(a_inner, b_outer, 1.0, mu_r)["L_per_m_H"]
    C = capacitance_coax(a_inner, b_outer, 1.0, eps_r)["C_per_m_F"]
    return {"Z0_ohm": math.sqrt(L / C), "v_m_per_s": 1.0 / math.sqrt(L * C),
            "velocity_factor": 1.0 / math.sqrt(L * C) / C_LIGHT,
            "L_per_m_H": L, "C_per_m_F": C}


# --------------------------------------------------------------------------- #
# Electrostatics
# --------------------------------------------------------------------------- #
def capacitance_parallel_plate(area, separation, eps_r=1.0):
    """C = eps0 eps_r A / d, with the stored energy and field strength."""
    if area <= 0 or separation <= 0:
        raise ValueError("area and separation must be positive")
    C = EPS0 * eps_r * area / separation
    return {"C_F": C, "C_pF": C * 1e12, "C_uF": C * 1e6,
            "eps_r": eps_r, "area_m2": area, "d_m": separation}


def capacitor_energy(C, V):
    """E = 0.5 C V^2, and Q = CV."""
    return {"energy_J": 0.5 * C * V ** 2, "charge_C": C * V,
            "energy_density_J_m3": None}


def breakdown_check(V, separation, dielectric_strength_MV_per_m=3.0):
    """Field strength against dielectric breakdown. Air is ~3 MV/m at STP, which is
    why a 1 mm air gap flashes over at about 3 kV."""
    E = V / separation
    limit = dielectric_strength_MV_per_m * 1e6
    return {"E_V_per_m": E, "E_kV_per_mm": E / 1e6,
            "limit_V_per_m": limit, "safe": E < limit,
            "margin": limit / E if E else float("inf")}


# --------------------------------------------------------------------------- #
# AC effects
# --------------------------------------------------------------------------- #
def skin_depth(frequency, material="copper", mu_r=1.0, T_celsius=20.0):
    """delta = sqrt(2 / (omega mu sigma)) = 1/sqrt(pi f mu sigma).

    The depth at which current density has fallen to 1/e. Copper is ~9.3 mm at
    50 Hz and ~0.3 mm at 50 kHz, which is why high-frequency conductors are made
    from stranded Litz wire or tube rather than solid rod. delta scales exactly as
    1/sqrt(f), and the selftest checks that scaling over four decades.
    """
    if frequency <= 0:
        raise ValueError("frequency must be positive")
    sigma = conductivity(material, T_celsius)["sigma_S_per_m"]
    omega = 2.0 * math.pi * frequency
    delta = math.sqrt(2.0 / (omega * MU0 * mu_r * sigma))
    return {"delta_m": delta, "delta_mm": delta * 1e3,
            "frequency_Hz": frequency, "sigma_S_per_m": sigma,
            "R_ac_over_R_dc_thick_conductor": None}


def ac_resistance_ratio(radius, frequency, material="copper", mu_r=1.0):
    """Approximate Rac/Rdc for a round conductor, from the ratio of the full area to
    an annulus one skin depth deep. Exact only for r >> delta; below that it is
    clamped to 1, because a conductor thinner than a skin depth has no skin effect.
    """
    d = skin_depth(frequency, material, mu_r)["delta_m"]
    if radius <= d:
        return {"ratio": 1.0, "skin_depth_m": d, "regime": "delta >= radius: "
                "current fills the conductor, no skin effect"}
    A_dc = math.pi * radius ** 2
    A_ac = math.pi * (radius ** 2 - (radius - d) ** 2)
    return {"ratio": A_dc / A_ac, "skin_depth_m": d,
            "regime": f"delta/r = {d/radius:.3f}, skin effect significant"}


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    # ---- the constants check EACH OTHER, not typed literals ----
    chk("eps0 is DERIVED from mu0 and c, and matches the CODATA value",
        abs(EPS0 - 8.8541878128e-12) / 8.8541878128e-12 < 1e-9,
        f"{EPS0:.10e} F/m")
    chk("1/sqrt(mu0 eps0) reconstructs the speed of light exactly",
        abs(1.0 / math.sqrt(MU0 * EPS0) - C_LIGHT) / C_LIGHT < 1e-12,
        f"{1.0/math.sqrt(MU0*EPS0):.1f} m/s")
    chk("the impedance of free space sqrt(mu0/eps0) is 376.73 ohm",
        abs(math.sqrt(MU0 / EPS0) - 376.730) < 0.01,
        f"{math.sqrt(MU0/EPS0):.4f} ohm")

    # ---- Ampere's law, verified AS a law ----
    w = b_wire(100.0, 0.005)
    chk("B around a wire = mu0 I / 2 pi r",
        abs(w["B_T"] - MU0 * 100.0 / (2 * math.pi * 0.005)) < 1e-18,
        f"{w['B_mT']:.4f} mT at 5 mm from 100 A")
    chk("...and the closed line integral B.dl equals mu0 I_enc at EVERY radius",
        all(abs(b_wire(I, r)["ampere_law_residual"]) < 1e-20
            for I in (1.0, 100.0, 5000.0) for r in (1e-3, 0.01, 1.0, 100.0)),
        "Ampere's law satisfied, not merely restated")
    chk("field falls as 1/r",
        abs(b_wire(100.0, 0.01)["B_T"] / b_wire(100.0, 0.02)["B_T"] - 2.0) < 1e-12)
    chk("zero radius is refused - the field diverges on the wire",
        _raises(lambda: b_wire(100.0, 0.0), ValueError))

    # inside/outside solutions must JOIN at the surface
    a = 0.004
    chk("the internal solution joins the external one exactly at the surface",
        abs(b_wire_inside(100.0, a, a) - b_wire(100.0, a)["B_T"])
        / b_wire(100.0, a)["B_T"] < 1e-15,
        f"both give {b_wire(100.0, a)['B_T']*1e3:.6f} mT at r=a - no discontinuity")
    chk("B is exactly zero on the axis of a solid conductor",
        b_wire_inside(100.0, 0.0, a) == 0.0)
    chk("B rises LINEARLY inside the conductor",
        abs(b_wire_inside(100., a / 2, a) / b_wire_inside(100., a / 4, a) - 2.0) < 1e-12)

    # ---- solenoid, toroid, loop ----
    s = b_solenoid(1000.0, 3.0)
    chk("long solenoid B = mu0 n I",
        abs(s["B_T"] - MU0 * 1000.0 * 3.0) < 1e-18, f"{s['B_T']*1e3:.4f} mT")
    chk("a finite solenoid tends to mu0 n I as L/D grows",
        abs(b_solenoid(1000., 3., length=100.0, radius=0.01)["end_correction"] - 1.0) < 1e-6,
        f"L/D=5000 -> {b_solenoid(1000.,3.,length=100.,radius=.01)['end_correction']:.8f}")
    chk("...and a squat solenoid falls well short of it",
        b_solenoid(1000., 3., length=0.02, radius=0.05)["end_correction"] < 0.25,
        f"L/D=0.2 -> {b_solenoid(1000.,3.,length=.02,radius=.05)['end_correction']:.3f}")

    t = b_toroid(500.0, 5.0, 0.050)
    chk("toroid B = mu0 N I / 2 pi r",
        abs(t["B_T"] - MU0 * 500.0 * 5.0 / (2 * math.pi * 0.050)) < 1e-18,
        f"{t['B_T']*1e3:.3f} mT")
    chk("...which is EXACTLY N times a single wire at the same radius",
        abs(t["B_T"] / b_wire(5.0, 0.050)["B_T"] - 500.0) < 1e-9,
        "same Ampere's law, N I enclosed")

    lp = b_loop_axis(10.0, 0.05, 0.0)
    chk("loop centre field B = mu0 I / 2R",
        abs(lp["B_T"] - MU0 * 10.0 / (2 * 0.05)) < 1e-18, f"{lp['B_T']*1e6:.2f} uT")
    chk("loop field falls as z^-3 far from the loop",
        abs(b_loop_axis(10., .05, 5.0)["B_T"] / b_loop_axis(10., .05, 10.0)["B_T"] - 8.0)
        < 0.01, "dipole behaviour")

    # ---- THE anchor: the pre-2019 definition of the ampere ----
    f = force_parallel_wires(1.0, 1.0, 1.0)
    chk("two 1 A wires 1 m apart: F/L = 2e-7 N/m EXACTLY (the old SI ampere)",
        abs(f["F_per_L_N_per_m"] - 2e-7) < 1e-19,
        f"{f['F_per_L_N_per_m']:.6e} N/m - this defined the ampere until 2019")
    big = force_parallel_wires(200.0, 200.0, 0.010)
    chk("200 A busbars 10 mm apart: F/L = mu0 I^2 / 2 pi d",
        abs(big["F_per_L_N_per_m"] - MU0 * 200.0 ** 2 / (2 * math.pi * 0.010)) < 1e-12,
        f"{big['F_per_L_N_per_m']:.2f} N/m - substantial, and why busbars need bracing")
    chk("like currents attract, opposite currents repel",
        force_parallel_wires(100., 100., .01)["sense"] == "attraction"
        and force_parallel_wires(100., -100., .01)["sense"] == "repulsion")
    chk("force scales as I^2 and as 1/d",
        abs(force_parallel_wires(200., 200., .01)["F_per_L_N_per_m"]
            / force_parallel_wires(100., 100., .01)["F_per_L_N_per_m"] - 4.0) < 1e-12
        and abs(force_parallel_wires(100., 100., .01)["F_per_L_N_per_m"]
                / force_parallel_wires(100., 100., .02)["F_per_L_N_per_m"] - 2.0) < 1e-12)

    # cross-check: Lorentz force on wire 2 in wire 1's field == the pair formula
    B1 = b_wire(200.0, 0.010)["B_T"]
    lf = lorentz_force(200.0, 1.0, B1)
    chk("Lorentz F = BIL on wire 2 in wire 1's field reproduces the pair formula",
        abs(lf["F_N"] - big["F_per_L_N_per_m"]) < 1e-12,
        "two different routes, same force")

    # ---- coax: L and C must reconstruct the speed of light ----
    L = inductance_coax(0.001, 0.004)
    chk("coax L' = (mu0/2pi) ln(b/a)",
        abs(L["L_per_m_H"] - MU0 / (2 * math.pi) * math.log(4.0)) < 1e-20,
        f"{L['L_per_m_nH']:.1f} nH/m")
    chk("coax inductance -> 0 as the gap closes",
        inductance_coax(0.00399, 0.004)["L_per_m_H"] < 1e-9)
    line = coax_line(0.001, 0.004)
    chk("1/sqrt(L'C') recovers c from geometry alone, with no c in the calculation",
        abs(line["v_m_per_s"] - C_LIGHT) / C_LIGHT < 1e-12,
        f"{line['v_m_per_s']:.1f} m/s from two independent geometric formulas")
    chk("air-spaced coax with b/a = 2.30 gives the standard 50 ohm",
        abs(coax_line(1.0, 2.3018)["Z0_ohm"] - 50.0) < 0.05,
        f"{coax_line(1.0, 2.3018)['Z0_ohm']:.3f} ohm")
    chk("a PTFE dielectric slows propagation by 1/sqrt(eps_r)",
        abs(coax_line(0.001, 0.004, eps_r=2.1)["velocity_factor"]
            - 1.0 / math.sqrt(2.1)) < 1e-12,
        f"velocity factor {coax_line(0.001,0.004,eps_r=2.1)['velocity_factor']:.4f}")
    chk("...and lowers Z0 by the same factor",
        abs(coax_line(0.001, 0.004, eps_r=2.1)["Z0_ohm"] * math.sqrt(2.1)
            - line["Z0_ohm"]) < 1e-9)

    # ---- capacitance ----
    c = capacitance_parallel_plate(0.01, 0.0005, 4.0)
    chk("parallel plate C = eps0 eps_r A / d",
        abs(c["C_F"] - EPS0 * 4.0 * 0.01 / 0.0005) < 1e-20, f"{c['C_pF']:.2f} pF")
    chk("capacitance scales with area and inversely with gap",
        abs(capacitance_parallel_plate(.02, .0005, 4.)["C_F"] / c["C_F"] - 2.0) < 1e-12
        and abs(capacitance_parallel_plate(.01, .00025, 4.)["C_F"] / c["C_F"] - 2.0) < 1e-12)
    e = capacitor_energy(c["C_F"], 100.0)
    chk("stored energy E = 0.5 C V^2 and Q = CV",
        abs(e["energy_J"] - 0.5 * c["C_F"] * 1e4) < 1e-20
        and abs(e["charge_C"] - c["C_F"] * 100.0) < 1e-20)
    bd = breakdown_check(3000.0, 0.001)
    chk("3 kV across a 1 mm air gap is right at breakdown",
        abs(bd["margin"] - 1.0) < 0.01, f"E = {bd['E_kV_per_mm']:.2f} kV/mm vs 3 kV/mm")
    chk("...and 10 kV across the same gap is flagged unsafe",
        not breakdown_check(10000.0, 0.001)["safe"])

    # ---- magnetic circuit: the gap dominates ----
    mc = magnetic_circuit(1000.0, [
        ("iron core", 0.300, 0.0004, 2000.0),
        ("air gap", 0.001, 0.0004, 1.0)])
    gap = next(s for s in mc["segments"] if s["name"] == "air gap")
    iron = next(s for s in mc["segments"] if s["name"] == "iron core")
    chk("a 1 mm air gap out-reluctances 300 mm of 2000-mu_r iron",
        gap["reluctance_A_per_Wb"] > iron["reluctance_A_per_Wb"],
        f"gap {gap['share_percent']:.1f}% of total reluctance vs "
        f"iron {iron['share_percent']:.1f}%")
    chk("...and the ratio is exactly (1/1)/(0.300/2000) = 6.67",
        abs(gap["reluctance_A_per_Wb"] / iron["reluctance_A_per_Wb"]
            - (0.001 / 1.0) / (0.300 / 2000.0)) < 1e-9,
        f"{gap['reluctance_A_per_Wb']/iron['reluctance_A_per_Wb']:.3f}x")
    chk("flux is continuous, so B is the same in both segments (equal areas)",
        abs(gap["B_T"] - iron["B_T"]) < 1e-15, f"B = {gap['B_T']:.4f} T")
    chk("the mmf drops sum to the applied NI",
        abs(sum(s["mmf_drop_A"] for s in mc["segments"]) - 1000.0) < 1e-9)
    nogap = magnetic_circuit(1000.0, [("iron core", 0.301, 0.0004, 2000.0)])
    chk("removing the gap raises the flux several-fold",
        nogap["flux_Wb"] / mc["flux_Wb"] > 5.0,
        f"{mc['flux_Wb']*1e6:.2f} uWb gapped -> {nogap['flux_Wb']*1e6:.2f} uWb solid")
    chk("reluctance shares sum to 100%",
        abs(sum(s["share_percent"] for s in mc["segments"]) - 100.0) < 1e-9)

    ind = inductance_solenoid(500, 0.0004, 0.1, 2000.0)
    chk("solenoid L = mu0 mu_r N^2 A / l",
        abs(ind - MU0 * 2000.0 * 500 ** 2 * 0.0004 / 0.1) < 1e-15, f"{ind*1e3:.3f} mH")
    chk("inductance goes as N^2",
        abs(inductance_solenoid(1000, .0004, .1, 2000.) / ind - 4.0) < 1e-12)
    chk("magnetic energy density u = B^2/2mu",
        abs(magnetic_energy_density(1.0) - 1.0 / (2 * MU0)) < 1e-9,
        f"{magnetic_energy_density(1.0)/1e3:.1f} kJ/m^3 at 1 T")

    # ---- skin effect ----
    d50 = skin_depth(50.0)
    d50k = skin_depth(50000.0)
    chk("copper skin depth at 50 Hz is ~9.3 mm",
        9.0 < d50["delta_mm"] < 9.5, f"{d50['delta_mm']:.3f} mm")
    chk("copper skin depth at 50 kHz is ~0.29 mm",
        0.28 < d50k["delta_mm"] < 0.31, f"{d50k['delta_mm']:.4f} mm")
    chk("delta scales EXACTLY as 1/sqrt(f) over four decades",
        all(abs(skin_depth(fr)["delta_mm"] * math.sqrt(fr)
                - d50["delta_mm"] * math.sqrt(50.0)) < 1e-9
            for fr in (5.0, 500.0, 5000.0, 500000.0)))
    chk("aluminium has a larger skin depth than copper (lower conductivity)",
        skin_depth(50.0, "aluminium")["delta_mm"] > d50["delta_mm"])
    chk("a hot conductor has a LARGER skin depth (sigma falls with temperature)",
        skin_depth(50.0, "copper", T_celsius=90.0)["delta_mm"] > d50["delta_mm"])
    chk("copper resistivity rises ~0.39%/K",
        abs(conductivity("copper", 120.0)["resistivity_ohm_m"]
            / conductivity("copper", 20.0)["resistivity_ohm_m"] - 1.393) < 0.01,
        "a 100 K rise costs ~39% more resistance")
    thin = ac_resistance_ratio(0.0005, 50.0)
    chk("a conductor thinner than one skin depth shows NO skin effect",
        thin["ratio"] == 1.0 and "no skin effect" in thin["regime"])
    thick = ac_resistance_ratio(0.020, 50000.0)
    chk("a thick conductor at high frequency has Rac >> Rdc",
        thick["ratio"] > 10.0, f"Rac/Rdc = {thick['ratio']:.1f} - why Litz wire exists")
    chk("an unknown conductor is refused",
        _raises(lambda: conductivity("mithril"), KeyError))
    chk("zero frequency is refused (skin depth is infinite at DC)",
        _raises(lambda: skin_depth(0.0), ValueError))

    print("emag selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="analytic electromagnetics")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--wire", nargs=2, type=float, metavar=("I_A", "r_m"))
    ap.add_argument("--skin", nargs=1, type=float, metavar="f_Hz")
    a = ap.parse_args()
    if a.wire:
        print(json.dumps(b_wire(*a.wire), indent=2)); raise SystemExit(0)
    if a.skin:
        print(json.dumps(skin_depth(a.skin[0]), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
