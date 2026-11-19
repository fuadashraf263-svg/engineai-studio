#!/usr/bin/env python3
"""
fatigue.py - fatigue life, mean-stress correction, and linear-elastic fracture.

Fatigue causes more real in-service failures than static overload, and EngineAI
could not see it at all. A part that passes every static check in this repository
can still break after two million cycles at a third of its yield stress, and
nothing in the system would have said so.

Three separate things live here because they answer three different questions:

  * HOW LONG will it last at a given stress amplitude?      Basquin / S-N
  * Does a MEAN stress make it worse?                        Goodman / Gerber
  * Will an EXISTING CRACK run?                              LEFM, K_IC, Paris

The third is not a refinement of the first two. Once a crack exists, stress-life is
the wrong model entirely - a component with a 5 mm crack fails at a stress the S-N
curve says is safe forever. Choosing between the two models is the engineering
judgement, and `assess()` states which one governs rather than averaging them.

Also here: coefficients of thermal expansion and rule-of-mixtures, because
mechanics.thermal_stress needs alpha and nothing in the repository carried it.

Units: MPa, mm, cycles. K in MPa*sqrt(m) - the conventional unit, which means the
crack length must be converted to metres inside the K expression. That conversion
is the single easiest place to be wrong by a factor of 31.6, so it is done once,
here, and the selftest checks it against a hand-computed value.

A checked design aid, NOT a certified result. Variable-amplitude sequence effects,
crack closure, environmental attack, weld residual stress and the statistical
scatter that dominates real fatigue data are all out of scope. A licensed engineer
signs off.
"""
from __future__ import annotations
import argparse, json, math

# --------------------------------------------------------------------------- #
# Material fatigue and fracture data, with provenance.
#   Su          ultimate tensile strength, MPa
#   Sy          yield, MPa
#   Se          fully reversed endurance limit at 1e6-1e7 cycles, MPa (polished,
#               unnotched laboratory specimen - NOT a design value on its own)
#   sigma_f     Basquin coefficient, MPa
#   b           Basquin exponent (negative)
#   K_IC        plane-strain fracture toughness, MPa*sqrt(m)
#   alpha       coefficient of thermal expansion, 1/K
#   E           Young's modulus, MPa
# --------------------------------------------------------------------------- #
FATIGUE_DATA = {
    "AISI_1018_steel": dict(Su=440.0, Sy=370.0, Se=220.0, sigma_f=900.0, b=-0.11,
                            K_IC=54.0, alpha=11.7e-6, E=200000.0,
                            source="ASM Handbook Vol.19 / MatWeb 1018"),
    "AISI_4340_steel": dict(Su=1280.0, Sy=1180.0, Se=580.0, sigma_f=1758.0, b=-0.0977,
                            K_IC=50.0, alpha=12.3e-6, E=205000.0,
                            source="ASM Handbook Vol.19, quenched and tempered"),
    "AL_6061_T6": dict(Su=310.0, Sy=276.0, Se=96.5, sigma_f=525.0, b=-0.11,
                       K_IC=29.0, alpha=23.6e-6, E=68900.0,
                       source="MatWeb 6061-T6 / MIL-HDBK-5"),
    "AL_7075_T6": dict(Su=572.0, Sy=503.0, Se=159.0, sigma_f=886.0, b=-0.12,
                       K_IC=24.0, alpha=23.4e-6, E=71700.0,
                       source="MIL-HDBK-5 7075-T6"),
    "Ti_6Al_4V": dict(Su=950.0, Sy=880.0, Se=510.0, sigma_f=2030.0, b=-0.104,
                      K_IC=75.0, alpha=8.6e-6, E=113800.0,
                      source="ASM Ti-6Al-4V annealed"),
    "stainless_304": dict(Su=505.0, Sy=215.0, Se=240.0, sigma_f=1000.0, b=-0.12,
                          K_IC=112.0, alpha=17.3e-6, E=193000.0,
                          source="ASM Handbook, annealed"),
    "cast_iron_grey": dict(Su=240.0, Sy=None, Se=100.0, sigma_f=400.0, b=-0.10,
                           K_IC=12.0, alpha=10.8e-6, E=100000.0,
                           source="ASM Handbook, class 30 grey iron"),
    "copper_C11000": dict(Su=220.0, Sy=69.0, Se=76.0, sigma_f=380.0, b=-0.12,
                          K_IC=None, alpha=17.0e-6, E=117000.0,
                          source="ASM Copper, annealed"),
    "concrete_C30": dict(Su=3.0, Sy=None, Se=1.2, sigma_f=None, b=None,
                         K_IC=1.0, alpha=10.0e-6, E=33000.0,
                         source="EN 1992-1-1, tensile values"),
}

# Paris-law constants: da/dN = C (dK)^m, with da/dN in m/cycle and dK in MPa*sqrt(m)
PARIS = {
    "ferritic_pearlitic_steel": (6.9e-12, 3.0, "BS 7910 mean, R < 0.5"),
    "martensitic_steel":        (1.36e-10, 2.25, "Barsom & Rolfe"),
    "austenitic_steel":         (5.6e-12, 3.25, "BS 7910"),
    "aluminium_alloy":          (1.0e-11, 3.0, "typical 2000/7000 series"),
}

# EN 1993-1-9 Table 8.x detail categories: the FAT class is the stress range in MPa
# giving 2e6 cycles. Slope m = 3 above the constant-amplitude limit.
FAT_CLASS = {
    "plain_rolled_plate": 160, "machined_plate_edge": 140,
    "transverse_butt_weld_ground": 112, "transverse_butt_weld_as_welded": 80,
    "longitudinal_fillet_weld": 100, "cruciform_full_pen": 71,
    "cruciform_partial_pen": 36, "fillet_weld_toe_transverse": 80,
    "cover_plate_end": 50, "bolted_connection_net_section": 112,
    "stud_shear_connector": 80,
}


def props(material):
    if material not in FATIGUE_DATA:
        raise KeyError(f"unknown material '{material}'. "
                       f"Known: {', '.join(sorted(FATIGUE_DATA))}")
    return dict(FATIGUE_DATA[material])


# --------------------------------------------------------------------------- #
# Stress-life
# --------------------------------------------------------------------------- #
def basquin_life(stress_amplitude, sigma_f, b):
    """Basquin: sigma_a = sigma_f (2N)^b, so N = 0.5 (sigma_a/sigma_f)^(1/b).

    2N is REVERSALS, N is cycles. Reporting one as the other is a factor-of-two
    error in life, and this function is explicit about which it returns.
    """
    if stress_amplitude <= 0 or sigma_f <= 0 or b >= 0:
        raise ValueError("need positive stresses and a NEGATIVE Basquin exponent b")
    reversals = (stress_amplitude / sigma_f) ** (1.0 / b)
    return {"N_cycles": reversals / 2.0, "reversals_2N": reversals,
            "stress_amplitude_MPa": stress_amplitude}


def basquin_stress(N_cycles, sigma_f, b):
    """The inverse: what amplitude gives this life? sigma_a = sigma_f (2N)^b."""
    if N_cycles <= 0:
        raise ValueError("life must be positive")
    return sigma_f * (2.0 * N_cycles) ** b


def sn_life(material, stress_amplitude):
    """Life from the material's own Basquin constants, with the endurance limit
    applied: below Se a steel is conventionally treated as having infinite life.
    Aluminium has no true endurance limit, which the result states."""
    p = props(material)
    if p["sigma_f"] is None or p["b"] is None:
        raise ValueError(f"{material} has no Basquin constants in the database")
    ferrous = "steel" in material or "iron" in material
    if p["Se"] and stress_amplitude <= p["Se"] and ferrous:
        return {"N_cycles": float("inf"), "infinite_life": True,
                "Se_MPa": p["Se"], "material": material,
                "note": "below the endurance limit - ferrous alloys show a true "
                        "fatigue limit, so this is conventionally infinite life"}
    r = basquin_life(stress_amplitude, p["sigma_f"], p["b"])
    r.update({"material": material, "infinite_life": False, "Se_MPa": p["Se"],
              "note": ("aluminium has NO true endurance limit - it will eventually "
                       "fail at any stress amplitude" if "AL_" in material else "")})
    return r


def miner(damage_blocks):
    """Palmgren-Miner linear damage: D = sum(n_i / N_i), failure at D = 1.

    `damage_blocks` = [(n_applied, N_allowable), ...]. Linear accumulation ignores
    sequence effects, which real spectra show can move failure between D = 0.5 and
    D = 2 - so a computed D of 0.9 is not a 10% margin.
    """
    total = 0.0
    parts = []
    for n, N in damage_blocks:
        if N <= 0:
            raise ValueError("allowable life must be positive")
        d = n / N
        total += d
        parts.append({"n": n, "N": N, "damage": d})
    return {"cumulative_damage_D": total, "fails": total >= 1.0,
            "remaining_fraction": max(0.0, 1.0 - total), "blocks": parts,
            "note": "linear accumulation ignores sequence effects; observed failure "
                    "ranges from D ~ 0.5 to D ~ 2, so D = 0.9 is not a 10% margin"}


def mean_stress(sigma_a, sigma_m, Su, Sy=None, criterion="goodman"):
    """Mean-stress correction. A tensile mean stress makes fatigue worse; the
    criteria differ in how much worse.

        Goodman (linear, conservative): sigma_a/Se + sigma_m/Su = 1
        Gerber (parabolic, fits data):  sigma_a/Se + (sigma_m/Su)^2 = 1
        Soderberg (uses yield, most conservative): sigma_a/Se + sigma_m/Sy = 1

    Returns the EQUIVALENT fully reversed amplitude, which can then be used with a
    plain S-N curve. A compressive mean stress is beneficial and is not credited
    here - that is deliberate conservatism, and it is stated.
    """
    if sigma_m < 0:
        return {"sigma_ar_MPa": sigma_a, "criterion": criterion,
                "note": "compressive mean stress is BENEFICIAL; no credit taken "
                        "(deliberately conservative)"}
    if criterion == "goodman":
        denom = 1.0 - sigma_m / Su
    elif criterion == "gerber":
        denom = 1.0 - (sigma_m / Su) ** 2
    elif criterion == "soderberg":
        if not Sy:
            raise ValueError("Soderberg needs a yield strength")
        denom = 1.0 - sigma_m / Sy
    else:
        raise ValueError("criterion must be goodman, gerber or soderberg")
    if denom <= 0:
        raise ValueError(f"mean stress {sigma_m} MPa reaches the static limit - "
                         "the part fails on the first cycle, not by fatigue")
    return {"sigma_ar_MPa": sigma_a / denom, "criterion": criterion,
            "sigma_a_MPa": sigma_a, "sigma_m_MPa": sigma_m,
            "amplification": 1.0 / denom}


def stress_from_range(sigma_max, sigma_min):
    """Convert a max/min pair to amplitude, mean, range and R-ratio."""
    return {"sigma_a_MPa": (sigma_max - sigma_min) / 2.0,
            "sigma_m_MPa": (sigma_max + sigma_min) / 2.0,
            "sigma_range_MPa": sigma_max - sigma_min,
            "R_ratio": sigma_min / sigma_max if sigma_max else float("nan")}


def en1993_fatigue(detail_category, stress_range_MPa, cycles=None):
    """EN 1993-1-9 detail-category curve for welded steel.

        N = 2e6 (dsigma_C / dsigma)^3      above the constant-amplitude limit
        constant-amplitude limit dsigma_D = dsigma_C (2/5)^(1/3)  at 5e6 cycles
        cut-off dsigma_L = dsigma_D (5/100)^(1/5)                 at 1e8 cycles

    The whole point of the detail-category system is that the WELD GEOMETRY, not the
    parent metal, sets the fatigue strength - a FAT 36 cruciform joint in the same
    steel as a FAT 160 plate has a life 87 times shorter at the same stress range.
    """
    if detail_category not in FAT_CLASS:
        raise KeyError(f"unknown detail '{detail_category}'. "
                       f"Known: {', '.join(sorted(FAT_CLASS))}")
    dsC = float(FAT_CLASS[detail_category])
    dsD = dsC * (2.0 / 5.0) ** (1.0 / 3.0)
    dsL = dsD * (5.0 / 100.0) ** (1.0 / 5.0)
    if stress_range_MPa <= dsL:
        N = float("inf")
        regime = "below the cut-off limit - no damage accumulates"
    elif stress_range_MPa <= dsD:
        N = 5e6 * (dsD / stress_range_MPa) ** 5
        regime = "between the CAFL and cut-off, slope m = 5"
    else:
        N = 2e6 * (dsC / stress_range_MPa) ** 3
        regime = "above the constant-amplitude limit, slope m = 3"
    out = {"detail_category_FAT": dsC, "N_cycles": N,
           "CAFL_MPa": dsD, "cutoff_MPa": dsL, "regime": regime,
           "stress_range_MPa": stress_range_MPa,
           "code": "EN 1993-1-9"}
    if cycles:
        out["damage"] = cycles / N if N != float("inf") else 0.0
        out["adequate"] = out["damage"] <= 1.0
    return out


# --------------------------------------------------------------------------- #
# Linear-elastic fracture mechanics
# --------------------------------------------------------------------------- #
def stress_intensity(sigma, crack_length_mm, Y=1.0):
    """K = Y sigma sqrt(pi a), with a in METRES so K comes out in MPa*sqrt(m).

    The mm->m conversion is the classic error here: forgetting it makes K too large
    by sqrt(1000) = 31.6. The selftest checks a hand-computed value.
    """
    if crack_length_mm <= 0:
        raise ValueError("crack length must be positive")
    a_m = crack_length_mm / 1000.0
    return {"K_MPa_sqrt_m": Y * sigma * math.sqrt(math.pi * a_m),
            "a_mm": crack_length_mm, "a_m": a_m, "Y": Y}


def critical_crack(sigma, K_IC, Y=1.0):
    """The crack length at which fast fracture occurs: a_c = (K_IC/(Y sigma))^2 / pi.

    Returned in mm. Small a_c is the danger signal - a high-strength alloy with a
    low toughness can have a critical crack shorter than the inspection threshold,
    which means it fails without warning.
    """
    if sigma <= 0 or K_IC <= 0:
        raise ValueError("stress and toughness must be positive")
    a_m = (K_IC / (Y * sigma)) ** 2 / math.pi
    return {"a_critical_mm": a_m * 1000.0, "a_critical_m": a_m,
            "sigma_MPa": sigma, "K_IC": K_IC, "Y": Y}


def fracture_check(sigma, crack_length_mm, material, Y=1.0):
    """Will this crack run at this stress? Compares K to K_IC."""
    p = props(material)
    if p["K_IC"] is None:
        raise ValueError(f"{material} has no K_IC in the database")
    K = stress_intensity(sigma, crack_length_mm, Y)["K_MPa_sqrt_m"]
    ac = critical_crack(sigma, p["K_IC"], Y)
    return {"K_MPa_sqrt_m": K, "K_IC": p["K_IC"], "ratio_K_over_KIC": K / p["K_IC"],
            "fractures": K >= p["K_IC"], "a_critical_mm": ac["a_critical_mm"],
            "a_actual_mm": crack_length_mm,
            "margin_on_crack_length": ac["a_critical_mm"] / crack_length_mm,
            "material": material}


def paris_growth(dK_range, C, m):
    """da/dN = C (dK)^m, metres per cycle."""
    if dK_range <= 0:
        raise ValueError("stress intensity range must be positive")
    return C * dK_range ** m


def cycles_to_failure_paris(sigma_range, a_initial_mm, a_final_mm, C, m, Y=1.0):
    """Integrate Paris' law from an initial to a final crack length.

    For constant Y and m != 2 the integral is closed form:

        N = [a_f^(1-m/2) - a_i^(1-m/2)] / [C (Y dsigma sqrt(pi))^m (1 - m/2)]

    The result is dominated by a_initial: halving the initial flaw size can multiply
    life several-fold, which is why inspection sensitivity matters more than a
    slightly better alloy.
    """
    if m == 2:
        raise ValueError("m = 2 needs the logarithmic form; not implemented")
    ai, af = a_initial_mm / 1000.0, a_final_mm / 1000.0
    if af <= ai:
        raise ValueError("final crack must be longer than the initial one")
    k = C * (Y * sigma_range * math.sqrt(math.pi)) ** m
    p = 1.0 - m / 2.0
    N = (af ** p - ai ** p) / (k * p)
    return {"N_cycles": N, "a_initial_mm": a_initial_mm, "a_final_mm": a_final_mm,
            "C": C, "m": m,
            "note": "life is dominated by the INITIAL flaw size, not the final one"}


def assess(material, sigma_max, sigma_min=0.0, crack_length_mm=None, Y=1.0):
    """The judgement call: stress-life or fracture mechanics?

    With no known crack, S-N governs. With a crack present, LEFM governs and the
    S-N answer is irrelevant - a cracked part can fail at a stress the S-N curve
    calls infinite-life. This function says which model applies rather than
    averaging two answers that describe different failure mechanisms.
    """
    p = props(material)
    s = stress_from_range(sigma_max, sigma_min)
    out = {"material": material, **s}
    corrected = mean_stress(s["sigma_a_MPa"], s["sigma_m_MPa"], p["Su"], p["Sy"])
    out["equivalent_reversed_amplitude_MPa"] = corrected["sigma_ar_MPa"]
    try:
        out["stress_life"] = sn_life(material, corrected["sigma_ar_MPa"])
    except ValueError as e:
        out["stress_life"] = {"error": str(e)}
    if crack_length_mm:
        out["fracture"] = fracture_check(sigma_max, crack_length_mm, material, Y)
        out["governing_model"] = "linear-elastic fracture mechanics"
        out["why"] = ("a crack is present, so stress-life does not apply - "
                      "a cracked part fails at stresses the S-N curve calls safe")
    else:
        out["governing_model"] = "stress-life (S-N)"
        out["why"] = "no crack reported; crack initiation governs"
    return out


# --------------------------------------------------------------------------- #
# Thermal expansion and mixtures (the data mechanics.py needed and lacked)
# --------------------------------------------------------------------------- #
def alpha(material):
    """Coefficient of thermal expansion, 1/K."""
    return props(material)["alpha"]


def rule_of_mixtures(fractions, values, inverse=False):
    """Voigt (parallel, upper bound):  X = sum(f_i X_i)
    Reuss (series, lower bound):       1/X = sum(f_i / X_i)

    Density always follows Voigt exactly. Modulus follows Voigt along the fibres
    and Reuss across them, and the two bracket the true value - which is why a
    single "composite modulus" number without a direction is meaningless.
    """
    if abs(sum(fractions) - 1.0) > 1e-9:
        raise ValueError(f"volume fractions must sum to 1, got {sum(fractions)}")
    if len(fractions) != len(values):
        raise ValueError("fractions and values must be the same length")
    if inverse:
        return 1.0 / sum(f / v for f, v in zip(fractions, values))
    return sum(f * v for f, v in zip(fractions, values))


def composite(fractions, E_values, rho_values):
    """Voigt/Reuss bounds for a composite, with specific stiffness."""
    Ev = rule_of_mixtures(fractions, E_values)
    Er = rule_of_mixtures(fractions, E_values, inverse=True)
    rho = rule_of_mixtures(fractions, rho_values)
    return {"E_voigt_upper_MPa": Ev, "E_reuss_lower_MPa": Er,
            "density_kg_m3": rho,
            "specific_stiffness_longitudinal": Ev / rho,
            "anisotropy_ratio": Ev / Er,
            "note": "Voigt is along the fibres, Reuss across them; the true "
                    "modulus lies between and depends on direction"}


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

    # ---- data sanity ----
    chk("every material has a source citation",
        all("source" in v and v["source"] for v in FATIGUE_DATA.values()))
    chk("endurance limit is always below ultimate strength",
        all(v["Se"] < v["Su"] for v in FATIGUE_DATA.values()))
    chk("steels have Se/Su in the classical 0.4-0.6 band",
        all(0.35 < FATIGUE_DATA[m]["Se"] / FATIGUE_DATA[m]["Su"] < 0.62
            for m in FATIGUE_DATA if "steel" in m),
        ", ".join(f"{m.split('_')[1]}:{FATIGUE_DATA[m]['Se']/FATIGUE_DATA[m]['Su']:.2f}"
                  for m in FATIGUE_DATA if "steel" in m))
    chk("aluminium ratios are lower than steel, as observed",
        FATIGUE_DATA["AL_6061_T6"]["Se"] / FATIGUE_DATA["AL_6061_T6"]["Su"] < 0.4)
    chk("every Basquin exponent is negative (life falls as stress rises)",
        all(v["b"] < 0 for v in FATIGUE_DATA.values() if v["b"] is not None))
    chk("an unknown material is refused", _raises(lambda: props("adamantium"), KeyError))

    # ---- Basquin round trip ----
    p = props("AISI_1018_steel")
    N = basquin_life(300.0, p["sigma_f"], p["b"])
    chk("Basquin N = 0.5 (sigma_a/sigma_f)^(1/b)",
        abs(N["N_cycles"] - 0.5 * (300.0 / p["sigma_f"]) ** (1 / p["b"])) < 1e-6,
        f"{N['N_cycles']:.0f} cycles at 300 MPa")
    chk("reversals are exactly twice cycles (the classic factor-of-2 trap)",
        abs(N["reversals_2N"] / N["N_cycles"] - 2.0) < 1e-12)
    chk("basquin_stress inverts basquin_life exactly",
        abs(basquin_stress(N["N_cycles"], p["sigma_f"], p["b"]) - 300.0) < 1e-9)
    chk("higher stress gives shorter life, monotonically",
        all(basquin_life(s1, p["sigma_f"], p["b"])["N_cycles"]
            > basquin_life(s2, p["sigma_f"], p["b"])["N_cycles"]
            for s1, s2 in zip([200, 300, 400], [300, 400, 500])))
    chk("a positive Basquin exponent is refused",
        _raises(lambda: basquin_life(300.0, 900.0, 0.11), ValueError))

    chk("a steel below its endurance limit gets infinite life",
        sn_life("AISI_1018_steel", 100.0)["infinite_life"],
        f"100 MPa < Se = {p['Se']} MPa")
    chk("...but ALUMINIUM does not, and the result says so",
        not sn_life("AL_6061_T6", 50.0)["infinite_life"]
        and "NO true endurance limit" in sn_life("AL_6061_T6", 50.0)["note"],
        f"{sn_life('AL_6061_T6', 50.0)['N_cycles']:.3g} cycles at 50 MPa")

    # ---- Miner ----
    m1 = miner([(1e4, 1e5), (2e4, 1e5), (3e4, 1e5)])
    chk("Miner damage sums to 0.6 for 10k+20k+30k out of 100k",
        abs(m1["cumulative_damage_D"] - 0.6) < 1e-12 and not m1["fails"])
    chk("Miner predicts failure at D >= 1",
        miner([(5e4, 1e5), (6e4, 1e5)])["fails"])
    chk("...and the result warns that D = 0.9 is not a 10% margin",
        "not a 10% margin" in m1["note"])

    # ---- mean stress: the three criteria must be ordered ----
    g = mean_stress(100.0, 200.0, 440.0, 370.0, "goodman")
    ge = mean_stress(100.0, 200.0, 440.0, 370.0, "gerber")
    so = mean_stress(100.0, 200.0, 440.0, 370.0, "soderberg")
    chk("Goodman: sigma_ar = sigma_a / (1 - sigma_m/Su)",
        abs(g["sigma_ar_MPa"] - 100.0 / (1 - 200.0 / 440.0)) < 1e-9,
        f"{g['sigma_ar_MPa']:.1f} MPa equivalent reversed")
    chk("the three criteria are ordered: Gerber < Goodman < Soderberg",
        ge["sigma_ar_MPa"] < g["sigma_ar_MPa"] < so["sigma_ar_MPa"],
        f"{ge['sigma_ar_MPa']:.0f} < {g['sigma_ar_MPa']:.0f} < {so['sigma_ar_MPa']:.0f} MPa")
    chk("zero mean stress leaves the amplitude untouched, in all three",
        all(abs(mean_stress(100.0, 0.0, 440.0, 370.0, c)["sigma_ar_MPa"] - 100.0) < 1e-12
            for c in ("goodman", "gerber", "soderberg")))
    chk("a COMPRESSIVE mean stress is not penalised, and the choice is stated",
        mean_stress(100.0, -200.0, 440.0)["sigma_ar_MPa"] == 100.0
        and "BENEFICIAL" in mean_stress(100.0, -200.0, 440.0)["note"])
    chk("a mean stress at the ultimate is refused as a STATIC failure",
        _raises(lambda: mean_stress(100.0, 440.0, 440.0), ValueError))

    sr = stress_from_range(300.0, -100.0)
    chk("amplitude, mean, range and R from max/min",
        sr["sigma_a_MPa"] == 200.0 and sr["sigma_m_MPa"] == 100.0
        and sr["sigma_range_MPa"] == 400.0 and abs(sr["R_ratio"] + 1 / 3) < 1e-12)
    chk("fully reversed loading has R = -1",
        abs(stress_from_range(200.0, -200.0)["R_ratio"] + 1.0) < 1e-12)

    # ---- EN 1993-1-9 ----
    e1 = en1993_fatigue("transverse_butt_weld_as_welded", 80.0)
    chk("a FAT 80 detail reaches exactly 2e6 cycles at an 80 MPa range",
        abs(e1["N_cycles"] - 2e6) / 2e6 < 1e-12,
        "that IS the definition of the detail category")
    chk("the CAFL is FAT x (2/5)^(1/3) at 5e6 cycles",
        abs(e1["CAFL_MPa"] - 80.0 * (0.4) ** (1 / 3)) < 1e-12,
        f"{e1['CAFL_MPa']:.2f} MPa")
    chk("below the cut-off, no damage accumulates at all",
        en1993_fatigue("transverse_butt_weld_as_welded", 20.0)["N_cycles"] == float("inf"))
    # both of these must be ABOVE the 58.9 MPa CAFL for the m = 3 branch to apply
    chk("slope m = 3 above the CAFL: halving the range gives exactly 8x the life",
        abs(en1993_fatigue("transverse_butt_weld_as_welded", 80.0)["N_cycles"]
            / en1993_fatigue("transverse_butt_weld_as_welded", 160.0)["N_cycles"]
            - 8.0) < 1e-9,
        f"160 MPa -> {en1993_fatigue('transverse_butt_weld_as_welded',160.)['N_cycles']:.3g}"
        f", 80 MPa -> 2e6 cycles")
    chk("...and BELOW the CAFL the curve correctly switches to slope m = 5",
        "m = 5" in en1993_fatigue("transverse_butt_weld_as_welded", 40.0)["regime"],
        f"40 MPa is under the {e1['CAFL_MPa']:.1f} MPa CAFL, so the shallower "
        "branch applies - using m = 3 there would understate life")
    good = en1993_fatigue("plain_rolled_plate", 100.0)
    bad = en1993_fatigue("cruciform_partial_pen", 100.0)
    chk("the WELD DETAIL dominates fatigue, not the steel grade",
        good["N_cycles"] / bad["N_cycles"] > 80,
        f"FAT 160 plate lasts {good['N_cycles']/bad['N_cycles']:.0f}x longer than "
        f"a FAT 36 cruciform at the same 100 MPa range - same steel")
    chk("an unknown detail category is refused",
        _raises(lambda: en1993_fatigue("magic_weld", 100.0), KeyError))

    # ---- LEFM: the mm->m conversion, hand-checked ----
    k = stress_intensity(100.0, 5.0)
    hand = 100.0 * math.sqrt(math.pi * 0.005)
    chk("K = Y sigma sqrt(pi a) with a in METRES",
        abs(k["K_MPa_sqrt_m"] - hand) < 1e-12,
        f"{k['K_MPa_sqrt_m']:.4f} MPa*sqrt(m) for 100 MPa and a 5 mm crack")
    chk("...and it is NOT 31.6x larger, which is what mm would have given",
        abs(k["K_MPa_sqrt_m"] - 100.0 * math.sqrt(math.pi * 5.0)) > 1.0,
        "the sqrt(1000) unit trap, checked explicitly")
    chk("K scales as sqrt(a)",
        abs(stress_intensity(100.0, 20.0)["K_MPa_sqrt_m"] / k["K_MPa_sqrt_m"] - 2.0)
        < 1e-12)
    chk("K is linear in stress",
        abs(stress_intensity(200.0, 5.0)["K_MPa_sqrt_m"] / k["K_MPa_sqrt_m"] - 2.0)
        < 1e-12)

    ac = critical_crack(100.0, 54.0)
    chk("critical crack a_c = (K_IC/(Y sigma))^2 / pi",
        abs(ac["a_critical_mm"] - (54.0 / 100.0) ** 2 / math.pi * 1000.0) < 1e-9,
        f"{ac['a_critical_mm']:.2f} mm in 1018 steel at 100 MPa")
    chk("critical_crack and stress_intensity are exact inverses",
        abs(stress_intensity(100.0, ac["a_critical_mm"])["K_MPa_sqrt_m"] - 54.0) < 1e-9)
    chk("a tougher material tolerates a longer crack",
        critical_crack(100.0, 112.0)["a_critical_mm"] > ac["a_critical_mm"])
    chk("critical crack shrinks as the square of stress",
        abs(critical_crack(200.0, 54.0)["a_critical_mm"] / ac["a_critical_mm"] - 0.25)
        < 1e-12)

    # the headline engineering fact: high strength can mean SHORTER tolerable cracks
    soft = critical_crack(0.5 * FATIGUE_DATA["AISI_1018_steel"]["Su"],
                          FATIGUE_DATA["AISI_1018_steel"]["K_IC"])
    hard = critical_crack(0.5 * FATIGUE_DATA["AL_7075_T6"]["Su"],
                          FATIGUE_DATA["AL_7075_T6"]["K_IC"])
    chk("a high-strength alloy worked at the same fraction of Su tolerates a "
        "SHORTER crack than mild steel",
        hard["a_critical_mm"] < soft["a_critical_mm"],
        f"7075-T6 {hard['a_critical_mm']:.2f} mm vs 1018 {soft['a_critical_mm']:.2f} mm "
        "- strength bought at the cost of damage tolerance")

    fc = fracture_check(300.0, 8.0, "AISI_1018_steel")
    chk("fracture_check compares K to K_IC and states the verdict",
        fc["fractures"] == (fc["K_MPa_sqrt_m"] >= fc["K_IC"]),
        f"K = {fc['K_MPa_sqrt_m']:.1f} vs K_IC = {fc['K_IC']}, "
        f"a_c = {fc['a_critical_mm']:.2f} mm")
    chk("a material with no K_IC in the database is refused, not guessed",
        _raises(lambda: fracture_check(100.0, 1.0, "copper_C11000"), ValueError))

    C, m_p, _ = PARIS["ferritic_pearlitic_steel"]
    chk("Paris da/dN = C dK^m",
        abs(paris_growth(20.0, C, m_p) - C * 20.0 ** m_p) < 1e-25,
        f"{paris_growth(20.0, C, m_p)*1e9:.4f} nm/cycle at dK = 20")
    chk("growth rate rises steeply with dK (cube law for m = 3)",
        abs(paris_growth(40.0, C, m_p) / paris_growth(20.0, C, m_p) - 8.0) < 1e-9)
    life = cycles_to_failure_paris(100.0, 1.0, 20.0, C, m_p)
    chk("Paris integration gives a finite, positive life",
        life["N_cycles"] > 0, f"{life['N_cycles']:.3g} cycles from 1 mm to 20 mm")
    small = cycles_to_failure_paris(100.0, 0.5, 20.0, C, m_p)
    chk("halving the INITIAL flaw multiplies life - inspection beats alloy choice",
        small["N_cycles"] / life["N_cycles"] > 1.3,
        f"{life['N_cycles']:.3g} -> {small['N_cycles']:.3g} cycles "
        f"({small['N_cycles']/life['N_cycles']:.2f}x) by finding a 0.5 mm flaw "
        "instead of a 1 mm one")
    chk("doubling the FINAL crack barely changes life at all",
        cycles_to_failure_paris(100.0, 1.0, 40.0, C, m_p)["N_cycles"]
        / life["N_cycles"] < 1.1, "the last millimetres go in a moment")
    chk("a final crack shorter than the initial one is refused",
        _raises(lambda: cycles_to_failure_paris(100., 5., 1., C, m_p), ValueError))

    # ---- the model-choice judgement ----
    clean = assess("AISI_1018_steel", 200.0, 0.0)
    cracked = assess("AISI_1018_steel", 200.0, 0.0, crack_length_mm=15.0)
    chk("with no crack, stress-life governs",
        clean["governing_model"] == "stress-life (S-N)")
    chk("with a crack present, LEFM governs and the code SAYS so",
        cracked["governing_model"] == "linear-elastic fracture mechanics"
        and "does not apply" in cracked["why"])
    chk("mean stress is folded into the equivalent reversed amplitude",
        assess("AISI_1018_steel", 300.0, 100.0)["equivalent_reversed_amplitude_MPa"]
        > 100.0)

    # ---- thermal expansion and mixtures ----
    chk("steel alpha ~ 12e-6 /K", 11e-6 < alpha("AISI_1018_steel") < 13e-6)
    chk("aluminium expands about twice as much as steel",
        1.8 < alpha("AL_6061_T6") / alpha("AISI_1018_steel") < 2.2,
        f"{alpha('AL_6061_T6')/alpha('AISI_1018_steel'):.2f}x")
    chk("titanium expands least of the structural metals here",
        alpha("Ti_6Al_4V") < min(alpha(m) for m in
                                 ("AISI_1018_steel", "AL_6061_T6", "copper_C11000")))
    chk("concrete and steel have nearly the same alpha - which is why reinforced "
        "concrete works at all",
        abs(alpha("concrete_C30") - alpha("AISI_1018_steel")) / alpha("AISI_1018_steel")
        < 0.20,
        f"concrete {alpha('concrete_C30')*1e6:.1f} vs steel "
        f"{alpha('AISI_1018_steel')*1e6:.1f} x1e-6 /K")

    # this closes the loop with mechanics.py, which had no alpha of its own
    import mechanics
    ts = mechanics.thermal_stress(props("AISI_1018_steel")["E"],
                                  alpha("AISI_1018_steel"), 80.0, sigma_y=370.0)
    chk("mechanics.thermal_stress can now be driven from real material data",
        180.0 < ts["magnitude_MPa"] < 200.0,
        f"{ts['magnitude_MPa']:.1f} MPa in restrained 1018 for an 80 K rise "
        f"({ts['magnitude_MPa']/370*100:.0f}% of yield)")

    comp = composite([0.6, 0.4], [230000.0, 3500.0], [1800.0, 1200.0])
    chk("Voigt (parallel) is an upper bound and Reuss (series) a lower one",
        comp["E_reuss_lower_MPa"] < comp["E_voigt_upper_MPa"],
        f"{comp['E_reuss_lower_MPa']:.0f} to {comp['E_voigt_upper_MPa']:.0f} MPa "
        f"- {comp['anisotropy_ratio']:.1f}x anisotropy for 60% carbon fibre")
    chk("density follows Voigt exactly",
        abs(comp["density_kg_m3"] - (0.6 * 1800 + 0.4 * 1200)) < 1e-12)
    chk("a single-component 'mixture' returns that component unchanged",
        abs(rule_of_mixtures([1.0], [200000.0]) - 200000.0) < 1e-9
        and abs(rule_of_mixtures([1.0], [200000.0], inverse=True) - 200000.0) < 1e-9)
    chk("Voigt and Reuss coincide when both phases are identical",
        abs(rule_of_mixtures([0.5, 0.5], [100.0, 100.0])
            - rule_of_mixtures([0.5, 0.5], [100.0, 100.0], inverse=True)) < 1e-12)
    chk("fractions that do not sum to 1 are refused",
        _raises(lambda: rule_of_mixtures([0.6, 0.6], [1.0, 2.0]), ValueError))

    print("fatigue selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="fatigue life and fracture mechanics")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--assess", nargs=3, metavar=("MATERIAL", "SMAX", "SMIN"))
    ap.add_argument("--crack", type=float, help="crack length mm, with --assess")
    a = ap.parse_args()
    if a.assess:
        print(json.dumps(assess(a.assess[0], float(a.assess[1]), float(a.assess[2]),
                                a.crack), indent=2, default=str))
        raise SystemExit(0)
    raise SystemExit(_selftest())
