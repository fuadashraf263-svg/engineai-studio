#!/usr/bin/env python3
"""
geotech.py -- GEOTECHNICS, the gap under 24 questions of the Civil bank.

research_knowledge.py already holds twelve geotechnical laws, so EngineAI can
QUOTE Terzaghi and Rankine. It could not COMPUTE them: every geotechnical
question came back EXPLAINED (the model reading a verified law) rather than
COMPUTED (a solver returning a number). This closes that.

WHY THIS IS VERIFIABLE
----------------------
Bearing-capacity factors are the rare case where the "right answer" is
published to four figures in every textbook and code. Nq and Nc have closed
forms that have not changed since Prandtl and Reissner:

    Nq = e^(pi tan phi) tan^2(45 + phi/2)
    Nc = (Nq - 1) cot phi            (-> pi + 2 = 5.14 as phi -> 0)

So the solver can be checked against the tables rather than against itself.
selftest() does exactly that at phi = 0, 20, 30 and 40 degrees.

THE N-GAMMA TRAP
----------------
N_gamma has NO agreed closed form. Terzaghi's own value comes off a chart;
Meyerhof, Hansen and Vesic each published a different expression, and at
phi = 30 they give 15.67, 15.07 and 22.40 - a 49% spread on the same soil.
Software that prints one number and calls it "the" bearing capacity is hiding
that. Every function here takes an explicit `ngamma` choice, records which one
it used in the result, and bearing_capacity() can return all three side by
side so the spread is visible instead of assumed away.

The same care applies to Nq: Terzaghi's original Nq is NOT the Prandtl Nq
above (he used a different failure surface and got 22.5 rather than 18.4 at
phi = 30). Both are implemented and named.

HONEST SCOPE
------------
  * Drained, homogeneous soil. Layered profiles, partial saturation and
    cyclic/seismic loading are not modelled.
  * Rankine earth pressure assumes a SMOOTH vertical wall. Wall friction
    lowers active thrust and raises passive; use Coulomb when delta != 0.
    Passive pressure from Rankine is unconservative for rough walls - the
    tool warns rather than silently returning it.
  * Bearing capacity is ULTIMATE. Divide by a factor of safety (typically
    2.5-3) for allowable, which this tool does not choose for you.
  * Terzaghi's superposition of the three terms is itself an approximation;
    it is slightly conservative and universally used.
  * 1D consolidation is Terzaghi's theory: small strain, constant cv, no
    secondary compression. Creep-dominated soils will settle more.

Run:  conda activate eng && cd ~/engineai
      python geotech.py --selftest
      python geotech.py --bearing --phi 30 --c 0 --gamma 18 --B 2 --Df 1.5
      python geotech.py --earth --phi 32 --gamma 17 --H 4 --c 5
"""

from __future__ import annotations

import argparse
import math
import sys

TOL_PCT = 0.5        # against published tables, which are themselves rounded
                     # to 2 decimals; 0.5% is the honest bar here.

G_W = 9.81           # unit weight of water, kN/m^3

NGAMMA_METHODS = ("vesic", "meyerhof", "hansen", "ec7")


# ------------------------------------------------------------ soil indices --

def void_ratio(n: float) -> float:
    """Void ratio e from porosity n."""
    if not 0 <= n < 1:
        raise ValueError(f"porosity {n} must be in [0, 1)")
    return n / (1.0 - n)


def porosity(e: float) -> float:
    """Porosity n from void ratio e."""
    return e / (1.0 + e)


def submerged_unit_weight(gamma_sat: float, gamma_w: float = G_W) -> float:
    """Buoyant (effective) unit weight gamma' = gamma_sat - gamma_w [kN/m^3]."""
    return gamma_sat - gamma_w


def effective_stress(sigma_total: float, u: float) -> float:
    """Terzaghi's principle: sigma' = sigma - u. The single most load-bearing
    idea in the subject, and the one most often dropped in a hurry."""
    return sigma_total - u


def vertical_effective_stress(z: float, gamma: float, z_w: float = 0.0,
                              gamma_sat: float = None,
                              gamma_w: float = G_W) -> dict:
    """sigma_v' at depth z with the water table at depth z_w.

    Above the water table the soil is at `gamma`; below it at `gamma_sat`
    (defaulting to `gamma` when the caller has only one number). Returns the
    total stress and pore pressure too, because a settlement that comes out
    wrong is almost always a pore-pressure error rather than an arithmetic one.
    """
    if z < 0:
        raise ValueError("depth must be non-negative")
    gs = gamma if gamma_sat is None else gamma_sat
    z_w = max(0.0, min(z_w, z)) if z_w < z else z_w
    dry = min(z, z_w)
    wet = max(0.0, z - z_w)
    sigma = gamma * dry + gs * wet
    u = gamma_w * wet
    return {"depth_m": z, "sigma_total_kPa": sigma, "u_kPa": u,
            "sigma_eff_kPa": sigma - u}


def critical_hydraulic_gradient(Gs: float = 2.65, e: float = None,
                                gamma_sat: float = None,
                                gamma_w: float = G_W) -> dict:
    """Gradient at which effective stress reaches zero and sand boils.

    Computed BOTH ways when the inputs allow it - (Gs-1)/(1+e) and
    gamma'/gamma_w must agree, and disagreement means the soil parameters
    supplied are mutually inconsistent rather than that the formula is wrong.
    """
    out = {}
    if e is not None:
        out["i_cr_from_index"] = (Gs - 1.0) / (1.0 + e)
        gamma_sat_derived = (Gs + e) / (1.0 + e) * gamma_w
        out["gamma_sat_derived_kNm3"] = gamma_sat_derived
        out["i_cr_from_weights"] = (gamma_sat_derived - gamma_w) / gamma_w
    if gamma_sat is not None:
        out["i_cr_from_weights"] = (gamma_sat - gamma_w) / gamma_w
    if not out:
        raise ValueError("give either void ratio e or gamma_sat")
    vals = [v for k, v in out.items() if k.startswith("i_cr")]
    out["i_cr"] = vals[0]
    out["agrees"] = max(vals) - min(vals) < 1e-9 if len(vals) > 1 else None
    return out


# ------------------------------------------------------- lateral pressure --

def rankine_ka(phi_deg: float, beta_deg: float = 0.0) -> float:
    """Rankine active coefficient. beta is the backfill slope from horizontal.

    beta = 0 reduces to (1 - sin phi)/(1 + sin phi) = tan^2(45 - phi/2).
    """
    phi = math.radians(phi_deg)
    if beta_deg == 0.0:
        return (1 - math.sin(phi)) / (1 + math.sin(phi))
    b = math.radians(beta_deg)
    if abs(beta_deg) > abs(phi_deg):
        raise ValueError(f"backfill slope {beta_deg} exceeds phi {phi_deg}: "
                         "the slope is already unstable, Rankine does not apply")
    root = math.sqrt(math.cos(b) ** 2 - math.cos(phi) ** 2)
    return math.cos(b) * (math.cos(b) - root) / (math.cos(b) + root)


def rankine_kp(phi_deg: float, beta_deg: float = 0.0) -> float:
    """Rankine passive coefficient. For a smooth wall Kp = 1/Ka."""
    phi = math.radians(phi_deg)
    if beta_deg == 0.0:
        return (1 + math.sin(phi)) / (1 - math.sin(phi))
    b = math.radians(beta_deg)
    root = math.sqrt(math.cos(b) ** 2 - math.cos(phi) ** 2)
    return math.cos(b) * (math.cos(b) + root) / (math.cos(b) - root)


def jaky_k0(phi_deg: float, ocr: float = 1.0) -> float:
    """At-rest coefficient. K0 = 1 - sin phi, times OCR^sin phi if overconsolidated."""
    s = math.sin(math.radians(phi_deg))
    return (1.0 - s) * (ocr ** s if ocr != 1.0 else 1.0)


def tension_crack_depth(c: float, gamma: float, phi_deg: float) -> float:
    """Depth over which Rankine active pressure is negative, z0 = 2c/(gamma sqrt(Ka)).

    Soil cannot sustain that tension: it cracks. Ignoring the crack
    overestimates wall stability, and a crack that fills with water reverses
    the saving entirely.
    """
    if c <= 0:
        return 0.0
    return 2.0 * c / (gamma * math.sqrt(rankine_ka(phi_deg)))


def earth_pressure(H: float, gamma: float, phi_deg: float, c: float = 0.0,
                   q: float = 0.0, beta_deg: float = 0.0,
                   state: str = "active") -> dict:
    """Total thrust per metre run on a smooth vertical wall of height H.

    Includes cohesion and a uniform surcharge q. For the active case the
    tension crack is located and the negative block is DISCARDED rather than
    allowed to cancel positive pressure lower down, which is the standard
    conservative treatment.
    """
    if state not in ("active", "passive"):
        raise ValueError("state must be 'active' or 'passive'")
    if H <= 0:
        raise ValueError("wall height must be positive")
    K = rankine_ka(phi_deg, beta_deg) if state == "active" else \
        rankine_kp(phi_deg, beta_deg)
    sK = math.sqrt(K)
    sign = -1.0 if state == "active" else 1.0

    def sigma(z):
        return K * (gamma * z + q) + sign * 2.0 * c * sK

    z0 = 0.0
    if state == "active" and c > 0:
        # depth where sigma crosses zero, allowing for surcharge
        z0 = (2.0 * c * sK - K * q) / (K * gamma) if K > 0 else 0.0
        z0 = min(max(z0, 0.0), H)

    # trapezoidal resultant over the contributing depth
    s_top, s_bot = sigma(z0), sigma(H)
    P = 0.5 * (s_top + s_bot) * (H - z0)
    # centroid of the trapezoid, measured from the base
    if (s_top + s_bot) > 0:
        y = (H - z0) * (2 * s_top + s_bot) / (3 * (s_top + s_bot))
    else:
        y = 0.0

    out = {"state": state, "K": K, "H_m": H, "thrust_kN_per_m": P,
           "sigma_top_kPa": s_top, "sigma_base_kPa": s_bot,
           "tension_crack_m": z0, "lever_arm_above_base_m": y,
           "overturning_moment_kNm_per_m": P * y,
           "wall": "smooth vertical (Rankine)"}
    if state == "passive":
        out["warning"] = ("Rankine passive ignores wall friction and is "
                          "UNCONSERVATIVE for a rough wall; use Coulomb or a "
                          "log-spiral solution when delta > 0.")
    return out


# ------------------------------------------------------- bearing capacity --

def n_factors(phi_deg: float, ngamma: str = "vesic",
              nq: str = "prandtl") -> dict:
    """Bearing-capacity factors, with the source of each one named.

    nq = "prandtl"  : Nq = e^(pi tan phi) tan^2(45 + phi/2)   (modern codes)
    nq = "terzaghi" : Terzaghi's original, from his own failure surface
    """
    if phi_deg < 0:
        raise ValueError("friction angle cannot be negative")
    phi = math.radians(phi_deg)
    t = math.tan(phi)

    if nq == "prandtl":
        Nq = math.exp(math.pi * t) * math.tan(math.pi / 4 + phi / 2) ** 2
    elif nq == "terzaghi":
        a = math.exp((0.75 * math.pi - phi / 2) * t)
        Nq = a ** 2 / (2 * math.cos(math.pi / 4 + phi / 2) ** 2)
    else:
        raise ValueError(f"unknown Nq basis {nq!r}")

    # cot(phi) diverges at phi = 0; the limit of (Nq-1)cot(phi) is pi+2.
    Nc = (math.pi + 2) if phi_deg == 0 else (Nq - 1.0) / t

    if ngamma == "vesic":
        Ng = 2.0 * (Nq + 1.0) * t
    elif ngamma == "meyerhof":
        Ng = (Nq - 1.0) * math.tan(1.4 * phi)
    elif ngamma == "hansen":
        Ng = 1.5 * (Nq - 1.0) * t
    elif ngamma == "ec7":
        Ng = 2.0 * (Nq - 1.0) * t
    else:
        raise ValueError(f"unknown N_gamma method {ngamma!r}, "
                         f"expected one of {NGAMMA_METHODS}")

    return {"phi_deg": phi_deg, "Nq": Nq, "Nc": Nc, "Ngamma": Ng,
            "Nq_basis": nq, "Ngamma_method": ngamma}


def bearing_capacity(phi_deg: float, c: float, gamma: float, B: float,
                     Df: float, shape: str = "strip",
                     ngamma: str = "vesic", nq: str = "prandtl",
                     gamma_below: float = None, water_depth: float = None,
                     compare: bool = False) -> dict:
    """Terzaghi ultimate bearing capacity q_ult [kPa].

        strip    q_ult = c Nc      + q Nq + 0.5 gamma B Ngamma
        square   q_ult = 1.3 c Nc  + q Nq + 0.4 gamma B Ngamma
        circular q_ult = 1.3 c Nc  + q Nq + 0.3 gamma B Ngamma

    with q = gamma Df the surcharge at founding level.

    `compare=True` returns q_ult under all three N_gamma conventions, because
    a single number here implies a precision the state of the art does not have.
    """
    shapes = {"strip": (1.0, 0.5), "square": (1.3, 0.4),
              "circular": (1.3, 0.3), "rectangular": (1.3, 0.4)}
    if shape not in shapes:
        raise ValueError(f"unknown shape {shape!r}, expected one of {sorted(shapes)}")
    if B <= 0 or Df < 0:
        raise ValueError("B must be positive and Df non-negative")
    sc, sg = shapes[shape]

    gb = gamma if gamma_below is None else gamma_below
    q_surcharge = gamma * Df

    # Water at or above founding level replaces gamma with gamma' in the
    # self-weight term. Getting this wrong roughly halves the third term, and
    # it is the single most common bearing-capacity error in practice.
    gamma_eff = gb
    water_note = "no water table given; gamma used as supplied"
    if water_depth is not None:
        if water_depth <= Df:
            gamma_eff = submerged_unit_weight(gb)
            water_note = (f"water table at {water_depth} m is at or above "
                          f"founding level {Df} m: buoyant unit weight used "
                          f"in the 0.5 gamma B N_gamma term")
        elif water_depth < Df + B:
            d = water_depth - Df
            gamma_eff = submerged_unit_weight(gb) + (d / B) * (gb - submerged_unit_weight(gb))
            water_note = (f"water table {d:.3g} m below founding level, within "
                          f"the failure zone B = {B} m: unit weight interpolated")
        else:
            water_note = (f"water table at {water_depth} m is deeper than "
                          f"Df + B = {Df + B} m and does not affect q_ult")

    def q_ult_for(method):
        N = n_factors(phi_deg, ngamma=method, nq=nq)
        return (sc * c * N["Nc"] + q_surcharge * N["Nq"]
                + sg * gamma_eff * B * N["Ngamma"]), N

    q_ult, N = q_ult_for(ngamma)
    out = {"q_ult_kPa": q_ult, "shape": shape, "B_m": B, "Df_m": Df,
           "surcharge_q_kPa": q_surcharge, "gamma_used_kNm3": gamma_eff,
           "water_note": water_note,
           "terms_kPa": {"cohesion": sc * c * N["Nc"],
                         "surcharge": q_surcharge * N["Nq"],
                         "self_weight": sg * gamma_eff * B * N["Ngamma"]},
           **N}
    if compare:
        spread = {m: q_ult_for(m)[0] for m in NGAMMA_METHODS}
        lo, hi = min(spread.values()), max(spread.values())
        out["ngamma_comparison_kPa"] = spread
        out["ngamma_spread_pct"] = (hi - lo) / lo * 100 if lo > 0 else 0.0
    return out


def allowable_bearing(q_ult: float, fos: float = 3.0, q_surcharge: float = 0.0,
                      net: bool = True) -> dict:
    """Allowable bearing pressure. NET applies the factor to (q_ult - q), which
    is the correct form: the surcharge was already there before the footing."""
    if fos <= 1:
        raise ValueError("factor of safety must exceed 1")
    if net:
        return {"q_all_kPa": q_surcharge + (q_ult - q_surcharge) / fos,
                "basis": f"net, FoS = {fos}"}
    return {"q_all_kPa": q_ult / fos, "basis": f"gross, FoS = {fos}"}


# ---------------------------------------------------------- consolidation --

def consolidation_settlement(H: float, e0: float, sigma0: float, dsigma: float,
                             Cc: float, Cs: float = None,
                             sigma_pc: float = None) -> dict:
    """Primary consolidation settlement of a layer of thickness H [m -> m].

    Three cases, and picking the wrong one is the usual source of a settlement
    estimate that is out by a factor of five:
      normally consolidated       sigma_pc <= sigma0
      overconsolidated, staying   sigma0 + dsigma <= sigma_pc   (uses Cs)
      overconsolidated, crossing  two terms, Cs then Cc
    """
    if sigma0 <= 0:
        raise ValueError("initial effective stress must be positive")
    if dsigma < 0:
        raise ValueError("this computes settlement, not heave")
    sigma1 = sigma0 + dsigma
    m = H / (1.0 + e0)

    if sigma_pc is None or sigma_pc <= sigma0:
        case = "normally consolidated"
        S = m * Cc * math.log10(sigma1 / sigma0)
    elif Cs is None:
        raise ValueError("overconsolidated soil needs a recompression index Cs")
    elif sigma1 <= sigma_pc:
        case = "overconsolidated, load stays below preconsolidation"
        S = m * Cs * math.log10(sigma1 / sigma0)
    else:
        case = "overconsolidated, load crosses preconsolidation"
        S = m * (Cs * math.log10(sigma_pc / sigma0)
                 + Cc * math.log10(sigma1 / sigma_pc))
    return {"settlement_m": S, "settlement_mm": S * 1000.0, "case": case,
            "sigma0_kPa": sigma0, "sigma_final_kPa": sigma1,
            "OCR": (sigma_pc / sigma0) if sigma_pc else 1.0}


def time_factor(U_pct: float) -> float:
    """Tv for a given average degree of consolidation, Terzaghi's series fit."""
    if not 0 <= U_pct < 100:
        raise ValueError("U must be in [0, 100)")
    if U_pct < 60:
        return math.pi / 4.0 * (U_pct / 100.0) ** 2
    return 1.781 - 0.933 * math.log10(100.0 - U_pct)


def consolidation_time(U_pct: float, H_layer: float, cv: float,
                       drainage: str = "double") -> dict:
    """Time to reach U% consolidation. cv in m^2/year, H in m, result in years.

    The drainage path is squared, so calling a doubly-drained layer singly
    drained overestimates the time by a factor of FOUR. It is asked for
    explicitly rather than guessed.
    """
    if drainage not in ("single", "double"):
        raise ValueError("drainage must be 'single' or 'double'")
    Hd = H_layer / 2.0 if drainage == "double" else H_layer
    Tv = time_factor(U_pct)
    return {"t_years": Tv * Hd ** 2 / cv, "Tv": Tv, "H_drainage_m": Hd,
            "drainage": drainage}


def darcy_discharge(k: float, i: float, A: float) -> float:
    """Seepage discharge q = k i A. Units follow k and A (SI: m/s, m^2 -> m^3/s)."""
    return k * i * A


# ------------------------------------------------------------------ tests --

# Published bearing-capacity factors. Prandtl Nq and Nc are identical in every
# modern reference; the four N_gamma columns are each author's own.
#
# The phi=20 Hansen entry was written here as 3.93 on the first pass and the
# selftest rejected it. 3.93 is the EUROCODE 7 value, 2(Nq-1)tan phi, which had
# been transcribed into Hansen's column; Hansen's own 1.5(Nq-1)tan phi gives
# 2.95. Two conventions differing by a third is exactly the confusion this
# module is built to prevent, so EC7 is now carried as a named method of its
# own rather than quietly deleted.
TABLE = {
    #  phi:  Nq,      Nc,      vesic,   meyerhof, hansen,  ec7
    0:      (1.00,    5.14,    0.00,    0.00,     0.00,    0.00),
    20:     (6.40,    14.83,   5.39,    2.87,     2.95,    3.93),
    30:     (18.40,   30.14,   22.40,   15.67,    15.07,   20.09),
    40:     (64.20,   75.31,   109.41,  93.69,    79.54,   106.05),
}


def selftest() -> int:
    ok = True

    def chk(cond, label):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label)
        ok = ok and bool(cond)

    def close(got, want, pct=TOL_PCT):
        if want == 0:
            return abs(got) < 1e-9
        return abs(got - want) / abs(want) * 100 <= pct

    print("geotech selftest")

    print(" bearing-capacity factors vs published tables")
    for phi, (Nq, Nc, ves, mey, han, ec7) in TABLE.items():
        f = n_factors(phi)
        chk(close(f["Nq"], Nq), f"phi={phi:2d}  Nq {f['Nq']:.2f} vs table {Nq}")
        chk(close(f["Nc"], Nc), f"phi={phi:2d}  Nc {f['Nc']:.2f} vs table {Nc}")
        for m, want in (("vesic", ves), ("meyerhof", mey), ("hansen", han),
                        ("ec7", ec7)):
            g = n_factors(phi, ngamma=m)["Ngamma"]
            chk(close(g, want, 1.0), f"phi={phi:2d}  Ngamma {m:8s} {g:.2f} vs {want}")

    # Terzaghi's own Nq is a DIFFERENT number, and conflating them is the
    # error this module exists to prevent.
    tz = n_factors(30, nq="terzaghi")["Nq"]
    chk(21.0 < tz < 24.0, f"Terzaghi's original Nq at phi=30 is {tz:.2f}, not 18.4")
    chk(abs(tz - n_factors(30)["Nq"]) > 3.0,
        "Terzaghi Nq and Prandtl Nq are held apart, not merged")

    print(" limits and internal consistency")
    chk(close(n_factors(0)["Nc"], math.pi + 2),
        f"Nc -> pi+2 = 5.1416 as phi -> 0 (got {n_factors(0)['Nc']:.4f})")
    chk(close(rankine_kp(35), 1 / rankine_ka(35)),
        "Kp = 1/Ka for a smooth wall")
    chk(close(rankine_ka(30), math.tan(math.radians(30)) ** 0 * 1 / 3, 0.01),
        f"Ka at phi=30 is exactly 1/3 (got {rankine_ka(30):.6f})")
    icr = critical_hydraulic_gradient(Gs=2.65, e=0.65)
    chk(icr["agrees"], f"critical gradient agrees both ways ({icr['i_cr']:.4f})")
    chk(close(icr["i_cr"], 1.0, 5.0), "i_cr is near unity for a typical sand")

    print(" against the audited question bank (data/qbank_csv_*.csv)")
    try:
        import qbank_csv
        bank = {q.id: q for q in qbank_csv.load()}
    except Exception as exc:                                  # noqa: BLE001
        print(f"  skip  bank not loadable: {exc}")
        bank = {}

    def bank_chk(qid, got, label):
        q = bank.get(qid)
        if q is None:
            print(f"  skip  {qid} not in the loaded bank")
            return
        chk(close(got, q.answer_value, 0.1), f"{qid} {label}: {got:.5g} vs bank {q.answer_value:.5g}")

    bank_chk("EQ-000102", void_ratio(0.5), "void ratio from porosity")
    bank_chk("EQ-000103", rankine_ka(38), "Rankine Ka at phi=38")
    bank_chk("EQ-000104",
             earth_pressure(H=3.3, gamma=16, phi_deg=29)["thrust_kN_per_m"],
             "active thrust on a 3.3 m wall")
    bank_chk("EQ-000105", vertical_effective_stress(4.7, 19, z_w=0.0)["sigma_eff_kPa"],
             "effective stress at 4.7 m")
    bank_chk("EQ-000106", darcy_discharge(2.7e-4, 0.6, 4.5e-3) * 1e9,
             "seepage discharge in mm^3/s")

    print(" earth pressure behaviour")
    a = earth_pressure(H=5, gamma=18, phi_deg=30)
    chk(close(a["thrust_kN_per_m"], 0.5 * (1 / 3) * 18 * 25, 0.01),
        f"cohesionless active thrust = 0.5 Ka gamma H^2 ({a['thrust_kN_per_m']:.3f})")
    chk(close(a["lever_arm_above_base_m"], 5 / 3, 0.01),
        f"triangular pressure acts at H/3 ({a['lever_arm_above_base_m']:.4f} m)")
    p = earth_pressure(H=5, gamma=18, phi_deg=30, state="passive")
    chk(p["thrust_kN_per_m"] > 8 * a["thrust_kN_per_m"],
        "passive thrust exceeds active by Kp/Ka = 9")
    chk("warning" in p, "passive result carries the rough-wall warning")

    c_wall = earth_pressure(H=5, gamma=18, phi_deg=30, c=10)
    chk(c_wall["tension_crack_m"] > 0,
        f"cohesion opens a tension crack ({c_wall['tension_crack_m']:.3f} m)")
    chk(c_wall["thrust_kN_per_m"] < a["thrust_kN_per_m"],
        "cohesion reduces active thrust")
    z0 = tension_crack_depth(10, 18, 30)
    chk(close(z0, 2 * 10 / (18 * math.sqrt(1 / 3)), 0.01),
        f"crack depth matches 2c/(gamma sqrt(Ka)) ({z0:.4f} m)")

    chk(close(jaky_k0(30), 0.5, 0.01), f"Jaky K0 at phi=30 is 0.5 ({jaky_k0(30):.4f})")
    chk(jaky_k0(30, ocr=4) > jaky_k0(30), "overconsolidation raises K0")
    chk(rankine_ka(30) < jaky_k0(30) < rankine_kp(30),
        "Ka < K0 < Kp, the ordering that must always hold")

    print(" bearing capacity")
    b = bearing_capacity(phi_deg=0, c=50, gamma=18, B=2, Df=1, compare=True)
    chk(close(b["terms_kPa"]["self_weight"], 0.0),
        "phi = 0 kills the self-weight term")
    chk(close(b["q_ult_kPa"], 50 * 5.14 + 18 * 1 * 1.0, 0.1),
        f"undrained strip q_ult = c Nc + q ({b['q_ult_kPa']:.2f} kPa)")

    b30 = bearing_capacity(phi_deg=30, c=0, gamma=18, B=2, Df=1.5, compare=True)
    chk(b30["ngamma_spread_pct"] > 10,
        f"the N_gamma spread is reported, not hidden ({b30['ngamma_spread_pct']:.1f}%)")
    chk(set(b30["ngamma_comparison_kPa"]) == set(NGAMMA_METHODS),
        "all three N_gamma conventions are returned side by side")

    sq = bearing_capacity(phi_deg=25, c=20, gamma=18, B=2, Df=1, shape="square")
    st = bearing_capacity(phi_deg=25, c=20, gamma=18, B=2, Df=1, shape="strip")
    chk(sq["terms_kPa"]["cohesion"] > st["terms_kPa"]["cohesion"],
        "a square footing gets the 1.3 cohesion shape factor")

    wet = bearing_capacity(phi_deg=32, c=0, gamma=19, B=2, Df=1, water_depth=0.5)
    dry = bearing_capacity(phi_deg=32, c=0, gamma=19, B=2, Df=1, water_depth=99)
    chk(wet["q_ult_kPa"] < dry["q_ult_kPa"],
        "a high water table lowers q_ult")
    chk(close(wet["gamma_used_kNm3"], 19 - G_W),
        f"buoyant unit weight used when water is above founding level "
        f"({wet['gamma_used_kNm3']:.2f})")
    chk("does not affect" in dry["water_note"],
        "a deep water table is explicitly declared harmless")

    al = allowable_bearing(dry["q_ult_kPa"], fos=3, q_surcharge=19.0, net=True)
    chk(al["q_all_kPa"] < dry["q_ult_kPa"], "allowable is below ultimate")
    chk(al["q_all_kPa"] > dry["q_ult_kPa"] / 3,
        "net allowable exceeds a naive gross q_ult/3")

    print(" consolidation")
    nc = consolidation_settlement(H=3, e0=0.8, sigma0=100, dsigma=100, Cc=0.3)
    chk(close(nc["settlement_m"], 3 / 1.8 * 0.3 * math.log10(2), 0.01),
        f"NC settlement matches the closed form ({nc['settlement_mm']:.2f} mm)")
    chk(nc["case"].startswith("normally"), "NC case detected")

    oc = consolidation_settlement(H=3, e0=0.8, sigma0=100, dsigma=50, Cc=0.3,
                                  Cs=0.06, sigma_pc=200)
    chk(oc["settlement_m"] < nc["settlement_m"],
        "an overconsolidated layer settles less under the same geometry")
    chk("stays below" in oc["case"], f"OC-below case detected ({oc['case']})")

    cross = consolidation_settlement(H=3, e0=0.8, sigma0=100, dsigma=200, Cc=0.3,
                                     Cs=0.06, sigma_pc=150)
    two = (3 / 1.8) * (0.06 * math.log10(1.5) + 0.3 * math.log10(300 / 150))
    chk(close(cross["settlement_m"], two, 0.01),
        f"crossing case sums Cs and Cc branches ({cross['settlement_mm']:.2f} mm)")

    chk(close(time_factor(50), 0.197, 2.0), f"Tv(50%) = 0.197 (got {time_factor(50):.4f})")
    chk(close(time_factor(90), 0.848, 2.0), f"Tv(90%) = 0.848 (got {time_factor(90):.4f})")
    d = consolidation_time(90, H_layer=4, cv=2.0, drainage="double")
    s = consolidation_time(90, H_layer=4, cv=2.0, drainage="single")
    chk(close(s["t_years"] / d["t_years"], 4.0, 0.01),
        "single drainage takes exactly 4x as long as double")

    print(" refusals")
    for bad, label in (
        (lambda: n_factors(30, ngamma="made_up"), "an unknown N_gamma method"),
        (lambda: bearing_capacity(30, 0, 18, B=0, Df=1), "a zero footing width"),
        (lambda: earth_pressure(H=-1, gamma=18, phi_deg=30), "a negative wall height"),
        (lambda: rankine_ka(20, beta_deg=30), "a backfill steeper than phi"),
        (lambda: consolidation_settlement(H=3, e0=0.8, sigma0=100, dsigma=50,
                                          Cc=0.3, sigma_pc=200), "OC with no Cs"),
        (lambda: allowable_bearing(500, fos=1.0), "a factor of safety of 1"),
        (lambda: void_ratio(1.0), "porosity of 1"),
    ):
        try:
            bad()
            chk(False, f"refuses {label}")
        except (ValueError, ZeroDivisionError):
            chk(True, f"refuses {label}")

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--bearing", action="store_true")
    p.add_argument("--earth", action="store_true")
    p.add_argument("--settle", action="store_true")
    p.add_argument("--phi", type=float, default=30.0)
    p.add_argument("--c", type=float, default=0.0)
    p.add_argument("--gamma", type=float, default=18.0)
    p.add_argument("--B", type=float, default=2.0)
    p.add_argument("--Df", type=float, default=1.0)
    p.add_argument("--H", type=float, default=4.0)
    p.add_argument("--q", type=float, default=0.0)
    p.add_argument("--shape", default="strip")
    p.add_argument("--ngamma", default="vesic", choices=NGAMMA_METHODS)
    p.add_argument("--water", type=float, default=None)
    p.add_argument("--fos", type=float, default=3.0)
    p.add_argument("--e0", type=float, default=0.8)
    p.add_argument("--sigma0", type=float, default=100.0)
    p.add_argument("--dsigma", type=float, default=100.0)
    p.add_argument("--Cc", type=float, default=0.3)
    a = p.parse_args()

    if a.selftest:
        return selftest()

    def show(d, indent="  "):
        for k, v in d.items():
            if isinstance(v, dict):
                print(f"{indent}{k}:")
                show(v, indent + "  ")
            elif isinstance(v, float):
                print(f"{indent}{k:26s} {v:.6g}")
            else:
                print(f"{indent}{k:26s} {v}")

    if a.bearing:
        r = bearing_capacity(a.phi, a.c, a.gamma, a.B, a.Df, shape=a.shape,
                             ngamma=a.ngamma, water_depth=a.water, compare=True)
        print("Terzaghi bearing capacity")
        show(r)
        show(allowable_bearing(r["q_ult_kPa"], a.fos, r["surcharge_q_kPa"]))
    elif a.earth:
        print("Rankine earth pressure")
        show(earth_pressure(a.H, a.gamma, a.phi, c=a.c, q=a.q, state="active"))
        print("  K0 (Jaky)                  %.6g" % jaky_k0(a.phi))
        show(earth_pressure(a.H, a.gamma, a.phi, c=a.c, q=a.q, state="passive"))
    elif a.settle:
        print("1D consolidation settlement")
        show(consolidation_settlement(a.H, a.e0, a.sigma0, a.dsigma, a.Cc))
    else:
        p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
