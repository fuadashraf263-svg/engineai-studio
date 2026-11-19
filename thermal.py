#!/usr/bin/env python3
"""
thermal.py - heat transfer: conduction, convection, radiation, fins, transients.

EngineAI had NO thermal capability at all. The `ht` and CoolProp libraries were
installed and unreachable, which is the worst of both worlds: the dependency cost
was paid and no question could be answered. This module is the missing layer.

Heat transfer earns its own module rather than living inside fluids because its
governing question is different. Fluids asks "what pressure do I need"; thermal
asks "how hot does it get", and the answer decides whether an electronic component
survives, whether a wall meets building regulations, and whether a shaft still has
its modulus.

Where a correlation is EMPIRICAL its validity range is carried with it and checked,
because a Nusselt correlation used outside its Rayleigh range is not approximately
right - it is arbitrary. `nusselt_*` functions return the range they are valid over
and say when they have been pushed outside it.

Verification approach: analytic cases have exact anchors (a plane wall must give
q = kA dT/L to machine precision); series resistances must sum; the fin equation
must tend to the infinite-fin and insulated-tip limits; effectiveness-NTU must agree
with LMTD on the same exchanger, which is the strongest check available because the
two methods are algebraically independent.

Units: W, m, K (SI - NOT the mm basis the structural modules use, because every
thermal property in the world is tabulated in W/m.K). Temperatures in K unless the
name says degC.

A checked design aid, NOT a certified result. A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math

SIGMA = 5.670374419e-8      # Stefan-Boltzmann, W/m^2.K^4 (CODATA 2018, exact-ish)
G_ACC = 9.80665

# Thermal conductivity W/m.K at ~300 K, and volumetric properties for transients.
#   k [W/m.K], rho [kg/m^3], cp [J/kg.K]
MATERIALS = {
    "copper":        (401.0, 8933.0, 385.0),
    "aluminium":     (237.0, 2702.0, 903.0),
    "carbon_steel":  (60.5, 7854.0, 434.0),
    "stainless_304": (14.9, 7900.0, 477.0),
    "steel_generic": (45.0, 7850.0, 470.0),
    "glass":         (1.4, 2500.0, 750.0),
    "concrete":      (1.4, 2300.0, 880.0),
    "brick":         (0.72, 1920.0, 835.0),
    "water":         (0.613, 997.0, 4179.0),
    "air":           (0.0263, 1.1614, 1007.0),
    "mineral_wool":  (0.038, 100.0, 835.0),
    "polystyrene":   (0.035, 25.0, 1210.0),
    "pvc":           (0.19, 1380.0, 1000.0),
    "wood_oak":      (0.17, 750.0, 2385.0),
}

# Total hemispherical emissivity at ~300 K
EMISSIVITY = {
    "polished_aluminium": 0.04, "anodised_aluminium": 0.82,
    "polished_copper": 0.03, "oxidised_copper": 0.78,
    "polished_steel": 0.07, "oxidised_steel": 0.79, "stainless_polished": 0.17,
    "black_paint": 0.98, "white_paint": 0.90, "concrete": 0.88,
    "brick_red": 0.93, "glass": 0.90, "water": 0.96, "human_skin": 0.95,
}


def material(name):
    if name not in MATERIALS:
        raise KeyError(f"unknown material '{name}'. Known: {', '.join(sorted(MATERIALS))}")
    k, rho, cp = MATERIALS[name]
    return {"name": name, "k_W_mK": k, "rho_kg_m3": rho, "cp_J_kgK": cp,
            "alpha_m2_s": k / (rho * cp),          # thermal diffusivity
            "effusivity": math.sqrt(k * rho * cp)}  # governs contact temperature


# --------------------------------------------------------------------------- #
# Conduction
# --------------------------------------------------------------------------- #
def plane_wall(k, area, thickness, dT=None, q=None):
    """Steady conduction through a plane wall: q = k A dT / L.

    Give dT to get q, or q to get dT. R = L/(kA) in K/W.
    """
    if thickness <= 0 or area <= 0 or k <= 0:
        raise ValueError("k, area and thickness must all be positive")
    R = thickness / (k * area)
    if dT is not None:
        q = dT / R
    elif q is not None:
        dT = q * R
    else:
        raise ValueError("give either dT or q")
    return {"q_W": q, "dT_K": dT, "R_K_per_W": R,
            "flux_W_m2": q / area, "U_W_m2K": 1.0 / (R * area)}


def cylindrical_wall(k, r_inner, r_outer, length, dT=None, q=None):
    """Radial conduction through a pipe wall: R = ln(ro/ri) / (2 pi k L).

    The logarithm is the whole point: area grows with radius, so a thick pipe
    insulates far less than a plane wall of the same thickness would suggest.
    """
    if not 0 < r_inner < r_outer or length <= 0 or k <= 0:
        raise ValueError("need 0 < r_inner < r_outer, positive length and k")
    R = math.log(r_outer / r_inner) / (2.0 * math.pi * k * length)
    if dT is not None:
        q = dT / R
    elif q is not None:
        dT = q * R
    else:
        raise ValueError("give either dT or q")
    return {"q_W": q, "dT_K": dT, "R_K_per_W": R,
            "q_per_metre_W_m": q / length}


def critical_insulation_radius(k_insulation, h_outer):
    """r_crit = k/h. Below this radius, ADDING insulation to a pipe or wire
    INCREASES heat loss, because the extra surface area outweighs the extra
    conduction resistance. A genuine and widely missed result - it is why thin
    wires are sometimes cooled by their own sheathing."""
    if h_outer <= 0 or k_insulation <= 0:
        raise ValueError("k and h must be positive")
    return {"r_critical_m": k_insulation / h_outer,
            "note": "below r_crit, adding insulation INCREASES heat loss"}


def resistance_network(resistances, dT=None, q=None, mode="series"):
    """Combine thermal resistances. Series adds; parallel adds reciprocals.
    This is the junction-to-ambient calculation every electronics designer does."""
    rs = [float(r) for r in resistances]
    if not rs or any(r <= 0 for r in rs):
        raise ValueError("every resistance must be positive")
    if mode == "series":
        R = sum(rs)
    elif mode == "parallel":
        R = 1.0 / sum(1.0 / r for r in rs)
    else:
        raise ValueError("mode must be 'series' or 'parallel'")
    if dT is not None:
        q = dT / R
    elif q is not None:
        dT = q * R
    else:
        raise ValueError("give either dT or q")
    return {"R_total_K_per_W": R, "q_W": q, "dT_K": dT, "mode": mode,
            "individual_dT_K": [q * r for r in rs] if mode == "series" else None}


def composite_wall(layers, area, dT=None, q=None, h_inner=None, h_outer=None):
    """Multi-layer wall. `layers` = [(k, thickness), ...] or [(name, thickness), ...].
    Optional surface film coefficients bracket the solid layers."""
    rs, names = [], []
    if h_inner:
        rs.append(1.0 / (h_inner * area)); names.append("inner film")
    for k, t in layers:
        kk = material(k)["k_W_mK"] if isinstance(k, str) else float(k)
        rs.append(t / (kk * area))
        names.append(f"{k} {t*1000:g} mm")
    if h_outer:
        rs.append(1.0 / (h_outer * area)); names.append("outer film")
    net = resistance_network(rs, dT=dT, q=q, mode="series")
    net["layers"] = names
    net["U_W_m2K"] = 1.0 / (net["R_total_K_per_W"] * area)
    net["layer_dT_K"] = dict(zip(names, net["individual_dT_K"]))
    return net


# --------------------------------------------------------------------------- #
# Convection correlations (empirical - validity ranges are carried and checked)
# --------------------------------------------------------------------------- #
def nusselt_flat_plate_laminar(Re, Pr):
    """Laminar forced convection, isothermal flat plate, AVERAGE Nu = 0.664 Re^0.5 Pr^(1/3).
    Valid Re < 5e5, Pr >= 0.6."""
    Nu = 0.664 * Re ** 0.5 * Pr ** (1.0 / 3.0)
    return {"Nu": Nu, "valid": Re < 5e5 and Pr >= 0.6,
            "range": "Re < 5e5, Pr >= 0.6 (laminar)",
            "correlation": "average, 0.664 Re^0.5 Pr^(1/3)"}


def nusselt_dittus_boelter(Re, Pr, heating=True):
    """Turbulent pipe flow: Nu = 0.023 Re^0.8 Pr^n, n = 0.4 heating / 0.3 cooling.
    Valid Re > 10000, 0.6 < Pr < 160, L/D > 10."""
    n = 0.4 if heating else 0.3
    Nu = 0.023 * Re ** 0.8 * Pr ** n
    return {"Nu": Nu, "valid": Re > 1e4 and 0.6 < Pr < 160,
            "range": "Re > 1e4, 0.6 < Pr < 160, L/D > 10",
            "correlation": f"Dittus-Boelter, n={n} ({'heating' if heating else 'cooling'})"}


def nusselt_vertical_plate(Ra, Pr=0.7):
    """Natural convection on a vertical plate, Churchill-Chu, valid all Ra:

        Nu = { 0.825 + 0.387 Ra^(1/6) / [1 + (0.492/Pr)^(9/16)]^(8/27) }^2
    """
    Nu = (0.825 + 0.387 * Ra ** (1.0 / 6.0)
          / (1.0 + (0.492 / Pr) ** (9.0 / 16.0)) ** (8.0 / 27.0)) ** 2
    return {"Nu": Nu, "valid": True, "range": "all Ra (Churchill-Chu)",
            "correlation": "Churchill-Chu vertical plate"}


def rayleigh(beta, dT, L, nu, alpha):
    """Ra = g beta dT L^3 / (nu alpha). For an ideal gas beta = 1/T."""
    return G_ACC * beta * dT * L ** 3 / (nu * alpha)


def convection(h, area, dT=None, q=None):
    """Newton's law of cooling: q = h A dT. R = 1/(hA)."""
    if h <= 0 or area <= 0:
        raise ValueError("h and area must be positive")
    R = 1.0 / (h * area)
    if dT is not None:
        q = dT / R
    elif q is not None:
        dT = q * R
    else:
        raise ValueError("give either dT or q")
    return {"q_W": q, "dT_K": dT, "R_K_per_W": R, "h_W_m2K": h}


# --------------------------------------------------------------------------- #
# Radiation
# --------------------------------------------------------------------------- #
def radiation(area, T_surface, T_surroundings, emissivity=1.0):
    """Net radiative exchange with large surroundings:

        q = eps sigma A (Ts^4 - Tsur^4)

    Temperatures MUST be absolute. Using Celsius here is the classic error and
    produces an answer wrong by orders of magnitude, so negative inputs are refused.
    """
    if T_surface < 0 or T_surroundings < 0:
        raise ValueError("temperatures must be ABSOLUTE (K) - a negative value "
                         "means Celsius was passed by mistake")
    if not 0 <= emissivity <= 1:
        raise ValueError("emissivity must be between 0 and 1")
    q = emissivity * SIGMA * area * (T_surface ** 4 - T_surroundings ** 4)
    # linearised radiative coefficient, useful for building resistance networks
    Tm = (T_surface + T_surroundings) / 2.0
    h_r = 4.0 * emissivity * SIGMA * Tm ** 3
    return {"q_W": q, "flux_W_m2": q / area, "h_radiative_W_m2K": h_r,
            "R_K_per_W": (1.0 / (h_r * area)) if h_r else float("inf"),
            "emissivity": emissivity}


def view_factor_parallel_plates(W, L, distance):
    """View factor between two identical, directly opposed parallel rectangles.
    Reduces to 1.0 as the plates become infinitely close - the selftest checks it."""
    X, Y = W / distance, L / distance
    x1 = math.sqrt(1 + X ** 2)
    y1 = math.sqrt(1 + Y ** 2)
    t1 = math.log(x1 ** 2 * y1 ** 2 / (1 + X ** 2 + Y ** 2))
    t2 = Y * x1 * math.atan(Y / x1)
    t3 = X * y1 * math.atan(X / y1)
    t4 = X * math.atan(X)
    t5 = Y * math.atan(Y)
    return (2.0 / (math.pi * X * Y)) * (0.5 * t1 + t2 + t3 - t4 - t5)


# --------------------------------------------------------------------------- #
# Fins
# --------------------------------------------------------------------------- #
def fin_rectangular(k, h, thickness, width, length, T_base, T_inf, tip="adiabatic"):
    """Straight rectangular fin.

        m = sqrt(hP/kA)
        adiabatic tip:  q = M tanh(mL),  T_tip = T_inf + theta_b/cosh(mL)
        infinite fin:   q = M            (the mL -> infinity limit)
        efficiency:     eta = q / (h P L theta_b)   = tanh(mL)/(mL) for adiabatic

    The adiabatic-tip solution must tend to the infinite-fin solution as mL grows,
    and eta must tend to 1 as mL -> 0. Both are asserted rather than assumed.
    """
    A_c = thickness * width
    P = 2.0 * (thickness + width)
    if min(k, h, thickness, width, length) <= 0:
        raise ValueError("all geometry and properties must be positive")
    m = math.sqrt(h * P / (k * A_c))
    theta_b = T_base - T_inf
    M = math.sqrt(h * P * k * A_c) * theta_b
    mL = m * length
    if tip == "adiabatic":
        q = M * math.tanh(mL)
        T_tip = T_inf + theta_b / math.cosh(mL)
    elif tip == "infinite":
        q = M
        T_tip = T_inf
    elif tip == "convective":
        hmk = h / (m * k)
        q = M * (math.tanh(mL) + hmk) / (1.0 + hmk * math.tanh(mL))
        T_tip = T_inf + theta_b * (1.0 / (math.cosh(mL) + hmk * math.sinh(mL)))
    else:
        raise ValueError("tip must be 'adiabatic', 'infinite' or 'convective'")
    q_max = h * P * length * theta_b
    return {"q_W": q, "m_per_m": m, "mL": mL, "T_tip_K": T_tip,
            "efficiency": q / q_max if q_max else 0.0,
            "effectiveness": q / (h * A_c * theta_b) if theta_b else 0.0,
            "R_fin_K_per_W": theta_b / q if q else float("inf"),
            "tip_condition": tip,
            "note": ("a fin is only worth adding if effectiveness > 2; "
                     "below that it barely beats the bare surface")}


# --------------------------------------------------------------------------- #
# Transients
# --------------------------------------------------------------------------- #
def biot(h, L_char, k):
    """Bi = h L / k. Below 0.1 the body is nearly isothermal and lumped
    capacitance applies; above it, internal gradients matter and the lumped model
    is simply wrong."""
    return h * L_char / k


def lumped_capacitance(h, area, volume, rho, cp, T0, T_inf, t=None, k=None):
    """Newtonian cooling: T(t) = T_inf + (T0 - T_inf) exp(-t/tau), tau = rho V cp/(hA).

    If k is supplied the Biot number is computed and the result says plainly whether
    the model is applicable - a lumped answer at Bi = 5 is not approximately right.
    """
    tau = rho * volume * cp / (h * area)
    out = {"tau_s": tau, "time_constant_min": tau / 60.0}
    if k is not None:
        L_c = volume / area
        Bi = biot(h, L_c, k)
        out["Biot"] = Bi
        out["L_char_m"] = L_c
        out["lumped_valid"] = Bi < 0.1
        out["validity"] = ("Bi < 0.1, lumped capacitance applies" if Bi < 0.1
                           else f"Bi = {Bi:.3f} >= 0.1 - internal gradients matter, "
                                "this answer is NOT reliable")
    if t is not None:
        out["T_K"] = T_inf + (T0 - T_inf) * math.exp(-t / tau)
        out["t_s"] = t
        out["fraction_remaining"] = math.exp(-t / tau)
    return out


def time_to_reach(h, area, volume, rho, cp, T0, T_inf, T_target):
    """Invert the lumped solution for the time to reach a target temperature."""
    tau = rho * volume * cp / (h * area)
    num = (T_target - T_inf) / (T0 - T_inf)
    if num <= 0:
        raise ValueError("target temperature is unreachable - it lies beyond T_inf")
    return {"t_s": -tau * math.log(num), "tau_s": tau}


def semi_infinite_step(k, rho, cp, T_i, T_s, x, t):
    """Semi-infinite solid, surface suddenly held at T_s:

        (T(x,t) - T_s) / (T_i - T_s) = erf( x / (2 sqrt(alpha t)) )

    The classic model for ground temperature, quenching and fire exposure. At x = 0
    it must return T_s exactly, and as x -> infinity it must return T_i.
    """
    alpha = k / (rho * cp)
    eta = x / (2.0 * math.sqrt(alpha * t)) if t > 0 else float("inf")
    T = T_s + (T_i - T_s) * math.erf(eta)
    q_surface = k * (T_s - T_i) / math.sqrt(math.pi * alpha * t) if t > 0 else float("inf")
    return {"T_K": T, "eta": eta, "alpha_m2_s": alpha,
            "penetration_depth_m": 3.6 * math.sqrt(alpha * t),
            "surface_flux_W_m2": q_surface}


def contact_temperature(mat_a, T_a, mat_b, T_b):
    """Two semi-infinite bodies brought into contact instantly settle at

        Ts = (T_a sqrt(k rho c)_a + T_b sqrt(k rho c)_b) / (sum of effusivities)

    weighted by thermal EFFUSIVITY, not conductivity. This is why metal at 20 C
    feels cold and wood at 20 C does not: identical temperature, very different
    effusivity, so the skin contact temperature differs by tens of degrees.
    """
    a = material(mat_a) if isinstance(mat_a, str) else mat_a
    b = material(mat_b) if isinstance(mat_b, str) else mat_b
    ea, eb = a["effusivity"], b["effusivity"]
    Ts = (T_a * ea + T_b * eb) / (ea + eb)
    return {"T_contact_K": Ts, "effusivity_a": ea, "effusivity_b": eb,
            "closer_to": a.get("name", "a") if ea > eb else b.get("name", "b"),
            "note": "weighted by sqrt(k rho c), not by conductivity or by mass"}


# --------------------------------------------------------------------------- #
# Heat exchangers
# --------------------------------------------------------------------------- #
def lmtd(T_hot_in, T_hot_out, T_cold_in, T_cold_out, flow="counter"):
    """Log mean temperature difference.

        counter-flow: dT1 = Th,in - Tc,out ; dT2 = Th,out - Tc,in
        parallel:     dT1 = Th,in - Tc,in  ; dT2 = Th,out - Tc,out
        LMTD = (dT1 - dT2)/ln(dT1/dT2), and -> dT1 when the two are equal
    """
    if flow == "counter":
        d1, d2 = T_hot_in - T_cold_out, T_hot_out - T_cold_in
    elif flow == "parallel":
        d1, d2 = T_hot_in - T_cold_in, T_hot_out - T_cold_out
    else:
        raise ValueError("flow must be 'counter' or 'parallel'")
    if d1 <= 0 or d2 <= 0:
        raise ValueError(f"temperature cross: dT1={d1:.2f}, dT2={d2:.2f} - the "
                         "stated outlet temperatures are thermodynamically impossible "
                         "for this flow arrangement")
    if abs(d1 - d2) < 1e-12:
        return {"LMTD_K": d1, "dT1": d1, "dT2": d2, "flow": flow,
                "note": "equal terminal differences - LMTD equals either"}
    return {"LMTD_K": (d1 - d2) / math.log(d1 / d2), "dT1": d1, "dT2": d2, "flow": flow}


def effectiveness_ntu(UA, C_hot, C_cold, arrangement="counter"):
    """epsilon-NTU. C_min, C_max, Cr = C_min/C_max, NTU = UA/C_min.

        counter:  eps = (1 - exp(-NTU(1-Cr))) / (1 - Cr exp(-NTU(1-Cr))),  Cr < 1
                  eps = NTU/(1+NTU)                                        Cr = 1
        parallel: eps = (1 - exp(-NTU(1+Cr))) / (1 + Cr)
        Cr = 0 (phase change): eps = 1 - exp(-NTU), for every arrangement
    """
    C_min, C_max = min(C_hot, C_cold), max(C_hot, C_cold)
    if C_min <= 0:
        raise ValueError("heat capacity rates must be positive")
    Cr = C_min / C_max
    NTU = UA / C_min
    if abs(Cr) < 1e-12:
        eps = 1.0 - math.exp(-NTU)
    elif arrangement == "counter":
        if abs(Cr - 1.0) < 1e-12:
            eps = NTU / (1.0 + NTU)
        else:
            e = math.exp(-NTU * (1.0 - Cr))
            eps = (1.0 - e) / (1.0 - Cr * e)
    elif arrangement == "parallel":
        eps = (1.0 - math.exp(-NTU * (1.0 + Cr))) / (1.0 + Cr)
    else:
        raise ValueError("arrangement must be 'counter' or 'parallel'")
    return {"effectiveness": eps, "NTU": NTU, "Cr": Cr,
            "C_min_W_K": C_min, "C_max_W_K": C_max, "arrangement": arrangement}


def heat_exchanger(UA, C_hot, C_cold, T_hot_in, T_cold_in, arrangement="counter"):
    """Full exchanger solution from inlet conditions alone - no outlet guess needed.

        q_max = C_min (Th,in - Tc,in),  q = eps q_max, then outlets follow.

    The selftest closes the loop by feeding these outlets back into LMTD and
    checking q = UA * LMTD. Those two methods share no algebra, so their agreement
    is real evidence rather than a restatement.
    """
    en = effectiveness_ntu(UA, C_hot, C_cold, arrangement)
    q_max = en["C_min_W_K"] * (T_hot_in - T_cold_in)
    q = en["effectiveness"] * q_max
    return {"q_W": q, "q_max_W": q_max,
            "T_hot_out_K": T_hot_in - q / C_hot,
            "T_cold_out_K": T_cold_in + q / C_cold,
            **en}


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

    # ---- constants ----
    chk("Stefan-Boltzmann constant is 5.6704e-8 W/m^2.K^4",
        abs(SIGMA - 5.670374419e-8) < 1e-16)
    chk("sigma derivable from first principles: 2 pi^5 k^4/(15 c^2 h^3)",
        abs(SIGMA - 2 * math.pi ** 5 * (1.380649e-23) ** 4
            / (15 * (299792458.0) ** 2 * (6.62607015e-34) ** 3)) / SIGMA < 1e-6,
        "pins it against CODATA k, c, h")

    # ---- conduction: exact ----
    w = plane_wall(45.0, 0.5, 0.020, dT=100.0)
    chk("plane wall q = kA dT/L", abs(w["q_W"] - 45.0 * 0.5 * 100.0 / 0.020) < 1e-9,
        f"{w['q_W']:.0f} W")
    chk("...and inverting it returns the same dT",
        abs(plane_wall(45.0, 0.5, 0.020, q=w["q_W"])["dT_K"] - 100.0) < 1e-9)
    chk("resistance R = L/kA", abs(w["R_K_per_W"] - 0.020 / (45.0 * 0.5)) < 1e-15)
    chk("halving thickness doubles the heat flow",
        abs(plane_wall(45., .5, .010, dT=100.)["q_W"] / w["q_W"] - 2.0) < 1e-12)
    chk("zero or negative thickness is refused",
        _raises(lambda: plane_wall(45.0, 0.5, 0.0, dT=100.0), ValueError))

    # series resistance must ADD - checked against an independent single wall
    cw = composite_wall([("carbon_steel", 0.010), ("mineral_wool", 0.100),
                         ("brick", 0.100)], area=1.0, dT=30.0)
    manual = (0.010 / 60.5 + 0.100 / 0.038 + 0.100 / 0.72)
    chk("composite wall resistances add in series",
        abs(cw["R_total_K_per_W"] - manual) / manual < 1e-12,
        f"U = {cw['U_W_m2K']:.4f} W/m^2K")
    chk("the layer temperature drops sum to the total",
        abs(sum(cw["layer_dT_K"].values()) - 30.0) < 1e-9)
    chk("the insulation takes almost all of the temperature drop",
        cw["layer_dT_K"]["mineral_wool 100 mm"] / 30.0 > 0.9,
        f"{cw['layer_dT_K']['mineral_wool 100 mm']/30.0*100:.1f}% of the drop")
    chk("the steel layer takes essentially none of it",
        cw["layer_dT_K"]["carbon_steel 10 mm"] / 30.0 < 0.001)

    cyl = cylindrical_wall(0.038, 0.050, 0.100, 1.0, dT=60.0)
    chk("cylindrical wall R = ln(ro/ri)/(2 pi k L)",
        abs(cyl["R_K_per_W"] - math.log(2.0) / (2 * math.pi * 0.038 * 1.0)) < 1e-15,
        f"{cyl['q_per_metre_W_m']:.2f} W/m")
    crit = critical_insulation_radius(0.038, 10.0)
    chk("critical insulation radius r = k/h",
        abs(crit["r_critical_m"] - 0.0038) < 1e-15,
        "below this, insulating a pipe makes it lose MORE heat")

    # ---- convection ----
    c = convection(25.0, 2.0, dT=40.0)
    chk("Newton cooling q = hA dT", abs(c["q_W"] - 25.0 * 2.0 * 40.0) < 1e-12)
    fp = nusselt_flat_plate_laminar(1e5, 0.7)
    chk("laminar flat plate Nu = 0.664 Re^0.5 Pr^(1/3)",
        abs(fp["Nu"] - 0.664 * math.sqrt(1e5) * 0.7 ** (1 / 3)) < 1e-9,
        f"Nu={fp['Nu']:.1f}")
    chk("...and it is flagged INVALID when pushed turbulent",
        not nusselt_flat_plate_laminar(1e7, 0.7)["valid"],
        "a correlation outside its range is arbitrary, not approximate")
    db = nusselt_dittus_boelter(50000.0, 4.0)
    chk("Dittus-Boelter Nu = 0.023 Re^0.8 Pr^0.4 for heating",
        abs(db["Nu"] - 0.023 * 50000.0 ** 0.8 * 4.0 ** 0.4) < 1e-9, f"Nu={db['Nu']:.1f}")
    chk("cooling uses n=0.3 and gives a lower Nu than heating",
        nusselt_dittus_boelter(50000., 4., heating=False)["Nu"] < db["Nu"])
    chk("Dittus-Boelter is flagged invalid in the laminar range",
        not nusselt_dittus_boelter(1000.0, 4.0)["valid"])
    chk("natural convection Nu rises with Rayleigh number",
        nusselt_vertical_plate(1e6)["Nu"] < nusselt_vertical_plate(1e9)["Nu"],
        f"Ra 1e6 -> Nu {nusselt_vertical_plate(1e6)['Nu']:.1f}, "
        f"Ra 1e9 -> Nu {nusselt_vertical_plate(1e9)['Nu']:.1f}")

    # ---- radiation ----
    r = radiation(0.2, 400.0, 300.0, 0.8)
    chk("radiation q = eps sigma A (Ts^4 - Tsur^4)",
        abs(r["q_W"] - 0.8 * SIGMA * 0.2 * (400.0 ** 4 - 300.0 ** 4)) < 1e-12,
        f"{r['q_W']:.2f} W")
    chk("net radiation is zero at thermal equilibrium",
        abs(radiation(0.2, 300.0, 300.0, 0.8)["q_W"]) < 1e-15)
    chk("radiation reverses sign when the surroundings are hotter",
        radiation(0.2, 300.0, 400.0, 0.8)["q_W"] < 0)
    chk("a CELSIUS temperature is refused, not silently used",
        _raises(lambda: radiation(0.2, -20.0, 300.0), ValueError),
        "the classic order-of-magnitude error")
    chk("emissivity outside 0..1 is refused",
        _raises(lambda: radiation(0.2, 400.0, 300.0, 1.5), ValueError))
    chk("the linearised h_r reproduces the exact flux within a few percent "
        "over a modest dT",
        abs(radiation(1.0, 310.0, 300.0, 0.9)["h_radiative_W_m2K"] * 10.0
            - radiation(1.0, 310.0, 300.0, 0.9)["q_W"])
        / radiation(1.0, 310.0, 300.0, 0.9)["q_W"] < 0.02)
    chk("polished metal radiates far less than black paint",
        EMISSIVITY["polished_aluminium"] < 0.1 < EMISSIVITY["black_paint"])
    vf = view_factor_parallel_plates(1.0, 1.0, 0.001)
    chk("two coincident parallel plates see each other completely (F -> 1)",
        vf > 0.99, f"F={vf:.4f}")
    chk("...and distant plates barely see each other",
        view_factor_parallel_plates(1.0, 1.0, 100.0) < 0.001)

    # ---- fins: must reduce to both known limits ----
    f = fin_rectangular(237.0, 25.0, 0.003, 0.100, 0.050, 373.0, 293.0)
    chk("fin: adiabatic tip q = M tanh(mL)", f["q_W"] > 0 and 0 < f["efficiency"] <= 1,
        f"q={f['q_W']:.2f} W, eta={f['efficiency']:.3f}, mL={f['mL']:.3f}")
    long_fin = fin_rectangular(237.0, 25.0, 0.003, 0.100, 2.0, 373.0, 293.0)
    inf_fin = fin_rectangular(237.0, 25.0, 0.003, 0.100, 2.0, 373.0, 293.0, tip="infinite")
    chk("a long fin's adiabatic-tip solution converges on the infinite-fin limit",
        abs(long_fin["q_W"] - inf_fin["q_W"]) / inf_fin["q_W"] < 1e-6,
        f"mL={long_fin['mL']:.1f}: {long_fin['q_W']:.3f} vs {inf_fin['q_W']:.3f} W")
    short = fin_rectangular(237.0, 25.0, 0.003, 0.100, 1e-5, 373.0, 293.0)
    chk("a vanishingly short fin has efficiency -> 1 (tanh(mL)/mL -> 1)",
        short["efficiency"] > 0.9999, f"eta={short['efficiency']:.6f}")
    chk("fin tip temperature lies between base and ambient",
        293.0 < f["T_tip_K"] < 373.0, f"{f['T_tip_K']:.1f} K")
    chk("a longer fin moves more heat but is less efficient",
        long_fin["q_W"] > f["q_W"] and long_fin["efficiency"] < f["efficiency"])
    chk("a convective tip moves more heat than an adiabatic one",
        fin_rectangular(237., 25., .003, .1, .05, 373., 293., tip="convective")["q_W"]
        > f["q_W"])
    chk("a copper fin outperforms a steel one of identical geometry",
        fin_rectangular(401., 25., .003, .1, .05, 373., 293.)["q_W"]
        > fin_rectangular(60.5, 25., .003, .1, .05, 373., 293.)["q_W"])

    # ---- transients ----
    d, h = 0.030, 25.0
    V = math.pi * d ** 3 / 6.0
    A = math.pi * d ** 2
    st = material("carbon_steel")
    lc = lumped_capacitance(h, A, V, st["rho_kg_m3"], st["cp_J_kgK"],
                            573.0, 293.0, t=300.0, k=st["k_W_mK"])
    chk("sphere Biot number is below 0.1, so lumped capacitance applies",
        lc["lumped_valid"] and lc["Biot"] < 0.1, f"Bi={lc['Biot']:.5f}")
    chk("lumped cooling T(t) = Tinf + (T0-Tinf) exp(-t/tau)",
        abs(lc["T_K"] - (293.0 + 280.0 * math.exp(-300.0 / lc["tau_s"]))) < 1e-9,
        f"{lc['T_K']:.1f} K after 5 min, tau={lc['tau_s']:.0f} s")
    chk("at t = tau exactly 1/e of the excess remains",
        abs(lumped_capacitance(h, A, V, st["rho_kg_m3"], st["cp_J_kgK"], 573., 293.,
                               t=lc["tau_s"])["fraction_remaining"]
            - math.exp(-1)) < 1e-12)
    chk("T -> T_inf as t -> infinity",
        abs(lumped_capacitance(h, A, V, st["rho_kg_m3"], st["cp_J_kgK"], 573., 293.,
                               t=1e9)["T_K"] - 293.0) < 1e-6)
    bad = lumped_capacitance(5000.0, A, V, st["rho_kg_m3"], st["cp_J_kgK"],
                             573.0, 293.0, t=10.0, k=st["k_W_mK"])
    chk("a high-Bi case is FLAGGED as unreliable rather than answered silently",
        not bad["lumped_valid"] and "NOT reliable" in bad["validity"],
        f"Bi={bad['Biot']:.2f}")
    tt = time_to_reach(h, A, V, st["rho_kg_m3"], st["cp_J_kgK"], 573., 293., 373.)
    chk("time_to_reach inverts the cooling curve consistently",
        abs(lumped_capacitance(h, A, V, st["rho_kg_m3"], st["cp_J_kgK"], 573., 293.,
                               t=tt["t_s"])["T_K"] - 373.0) < 1e-6,
        f"{tt['t_s']:.0f} s to fall from 300 to 100 C")
    chk("an unreachable target is refused",
        _raises(lambda: time_to_reach(h, A, V, 7854., 434., 573., 293., 200.),
                ValueError))

    si = semi_infinite_step(1.4, 2300.0, 880.0, 283.0, 373.0, 0.05, 3600.0)
    chk("semi-infinite solid: T at the surface (x=0) is exactly T_s",
        abs(semi_infinite_step(1.4, 2300., 880., 283., 373., 0.0, 3600.)["T_K"]
            - 373.0) < 1e-12)
    chk("...and deep inside it is still the initial temperature",
        abs(semi_infinite_step(1.4, 2300., 880., 283., 373., 100.0, 3600.)["T_K"]
            - 283.0) < 1e-9)
    chk("...and in between it lies between the two",
        283.0 < si["T_K"] < 373.0, f"{si['T_K']:.1f} K at 50 mm after 1 h")
    chk("the heated depth grows as sqrt(t)",
        abs(semi_infinite_step(1.4, 2300., 880., 283., 373., .05, 14400.)["penetration_depth_m"]
            / si["penetration_depth_m"] - 2.0) < 1e-9, "4x the time, 2x the depth")

    ct = contact_temperature("carbon_steel", 293.0, "wood_oak", 310.0)
    chk("skin touching steel and wood at the same temperature: steel wins",
        ct["closer_to"] == "carbon_steel" and ct["T_contact_K"] < 300.0,
        f"contact settles at {ct['T_contact_K']:.1f} K - "
        "why 20 C metal feels cold and 20 C wood does not")
    chk("contact temperature is bounded by the two body temperatures",
        293.0 <= ct["T_contact_K"] <= 310.0)
    same = contact_temperature("copper", 300.0, "copper", 400.0)
    chk("identical materials meet exactly halfway",
        abs(same["T_contact_K"] - 350.0) < 1e-12)

    # ---- heat exchangers: two independent methods must agree ----
    lm = lmtd(400.0, 350.0, 300.0, 330.0, "counter")
    chk("counter-flow LMTD = (dT1-dT2)/ln(dT1/dT2)",
        abs(lm["LMTD_K"] - (70.0 - 50.0) / math.log(70.0 / 50.0)) < 1e-12,
        f"{lm['LMTD_K']:.3f} K")
    chk("LMTD tends to the common value when both terminal dTs are equal",
        abs(lmtd(400., 350., 300., 350.0 - 0.0, "counter")["LMTD_K"] - 50.0) < 1e-6
        if False else abs(lmtd(400., 360., 310., 350., "counter")["LMTD_K"] - 50.0) < 1e-9,
        "equal-dT limit is the removable singularity of the log")
    chk("counter-flow LMTD exceeds parallel-flow for the same terminals",
        lm["LMTD_K"] > lmtd(400., 350., 300., 330., "parallel")["LMTD_K"],
        "which is why counter-flow needs less area")
    chk("a thermodynamically impossible temperature cross is REFUSED",
        _raises(lambda: lmtd(400.0, 310.0, 300.0, 420.0, "parallel"), ValueError))

    # the real check: eps-NTU and LMTD are algebraically independent
    hx = heat_exchanger(5000.0, 4000.0, 6000.0, 400.0, 300.0, "counter")
    back = lmtd(400.0, hx["T_hot_out_K"], 300.0, hx["T_cold_out_K"], "counter")
    q_lmtd = 5000.0 * back["LMTD_K"]
    chk("eps-NTU and LMTD agree on the same exchanger to machine precision",
        abs(hx["q_W"] - q_lmtd) / hx["q_W"] < 1e-9,
        f"eps-NTU {hx['q_W']:.2f} W vs UA*LMTD {q_lmtd:.2f} W - "
        "two independent methods, one answer")
    chk("effectiveness is between 0 and 1", 0 < hx["effectiveness"] < 1,
        f"eps={hx['effectiveness']:.4f}, NTU={hx['NTU']:.3f}")
    chk("energy balances: q = C_hot dT_hot = C_cold dT_cold",
        abs(4000.0 * (400.0 - hx["T_hot_out_K"]) - hx["q_W"]) < 1e-6
        and abs(6000.0 * (hx["T_cold_out_K"] - 300.0) - hx["q_W"]) < 1e-6)
    chk("counter-flow is more effective than parallel at the same UA",
        hx["effectiveness"]
        > heat_exchanger(5000., 4000., 6000., 400., 300., "parallel")["effectiveness"])
    chk("effectiveness -> 1 as UA -> infinity",
        heat_exchanger(1e9, 4000., 6000., 400., 300.)["effectiveness"] > 0.9999)
    chk("effectiveness -> 0 as UA -> 0",
        heat_exchanger(1e-9, 4000., 6000., 400., 300.)["effectiveness"] < 1e-9)
    chk("Cr = 1 uses the removable-singularity form NTU/(1+NTU)",
        abs(effectiveness_ntu(5000.0, 4000.0, 4000.0)["effectiveness"]
            - (5000.0 / 4000.0) / (1 + 5000.0 / 4000.0)) < 1e-12)
    ph = effectiveness_ntu(5000.0, 4000.0, 1e12)     # condensing: Cr -> 0
    chk("a phase-change stream (Cr -> 0) gives eps = 1 - exp(-NTU)",
        abs(ph["effectiveness"] - (1 - math.exp(-5000.0 / 4000.0))) < 1e-6,
        f"eps={ph['effectiveness']:.5f}")

    # ---- materials ----
    chk("copper conducts ~6.6x better than carbon steel",
        6.0 < material("copper")["k_W_mK"] / material("carbon_steel")["k_W_mK"] < 7.0,
        f"{material('copper')['k_W_mK']/material('carbon_steel')['k_W_mK']:.1f}x")
    chk("...and ~27x better than stainless, which is a poor conductor for a metal",
        25 < material("copper")["k_W_mK"] / material("stainless_304")["k_W_mK"] < 28,
        f"{material('copper')['k_W_mK']/material('stainless_304')['k_W_mK']:.1f}x - "
        "stainless is closer to glass than to copper")
    chk("insulation is ~1000x worse than aluminium",
        material("aluminium")["k_W_mK"] / material("mineral_wool")["k_W_mK"] > 1000)
    chk("thermal diffusivity alpha = k/(rho cp)",
        abs(material("copper")["alpha_m2_s"] - 401.0 / (8933.0 * 385.0)) < 1e-18)
    chk("copper diffuses heat far faster than concrete",
        material("copper")["alpha_m2_s"] / material("concrete")["alpha_m2_s"] > 100)
    chk("an unknown material is refused", _raises(lambda: material("unobtainium"), KeyError))

    print("thermal selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="heat transfer")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--wall", nargs=4, type=float, metavar=("k", "A", "L", "dT"))
    ap.add_argument("--hx", nargs=5, type=float, metavar=("UA", "Ch", "Cc", "Thi", "Tci"))
    a = ap.parse_args()
    if a.wall:
        print(json.dumps(plane_wall(a.wall[0], a.wall[1], a.wall[2], dT=a.wall[3]), indent=2))
        raise SystemExit(0)
    if a.hx:
        print(json.dumps(heat_exchanger(*a.hx), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
