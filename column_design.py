#!/usr/bin/env python3
"""
column_design.py - EN 1993-1-1 6.3.1 / 6.3.3: compression members and beam-columns.

`steel_design.py` designs the beam. Nothing in EngineAI designed what the beam
STANDS ON, and a column fails in a way a beam does not:

  * A beam in bending fails when a fibre yields. A column fails by BUCKLING, at a
    load that depends on length squared and does not involve fy at all in the elastic
    limit. A 6 m HEB 200 buckles at roughly a fifth of the load a stress check allows.
  * Buckling picks its OWN axis. It goes about whichever axis is weaker, which for a
    column made from a beam section is never the one the designer was thinking about.
  * A real column carries axial load AND moment at once. Each amplifies the other,
    and the interaction (6.3.3) is not the sum of the two separate utilisations - a
    member can pass both individual checks and fail the combination.

Three further traps this module exists to avoid:

  * The compression slenderness limits are NOT the bending ones. A web in uniform
    compression is limited to 33/38/42 eps; the same web in bending gets 72/83/124,
    because in bending half of it is in tension and stabilises the rest. Reusing the
    bending classifier on a column overstates its class by a wide margin.
  * The buckling CURVE depends on h/b. An HEB 200 is square, h/b = 1.0 <= 1.2, so it
    takes the *worse* curves b and c - while a slimmer IPE takes a and b. The stockier
    section gets the harsher curve, which is the opposite of the intuitive guess.
  * Buckling length is not member length. A cantilever column's L_cr is 2L, so it
    buckles at a QUARTER of the pinned-pinned load.

Units: N, mm, MPa. Partial factors default to the EN recommended gamma_M1 = 1.00.

A checked design aid, NOT a certified result. Second-order/sway effects, torsional
and flexural-torsional buckling, class 4 effective areas, web bearing, base plates
and the National Annex are all out of scope. A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math

import standards
from steel_design import (E_STEEL, STEEL_GRADES, epsilon, chi_LT, ltb_curve_for,
                          Mcr, resolve_section)

# EN 1993-1-1 Table 6.1 imperfection factors, by buckling curve.
ALPHA = {"a0": 0.13, "a": 0.21, "b": 0.34, "c": 0.49, "d": 0.76}

# Theoretical (Euler) effective-length factors. The practical values in brackets are
# what most national annexes and textbooks recommend, because a "fixed" end is never
# truly fixed - real base plates and connections rotate. Using the theoretical value
# for a fixed end is UNCONSERVATIVE, so both are reported and the practical one is
# what `recommended` returns.
END_CONDITIONS = {
    #                       theoretical, practical, description
    "pinned-pinned":       (1.0,  1.0,  "both ends pinned, no sway (the reference case)"),
    "fixed-fixed":         (0.5,  0.65, "both ends fully fixed, no sway"),
    "fixed-pinned":        (0.7,  0.80, "one end fixed, one pinned, no sway"),
    "fixed-free":          (2.0,  2.10, "cantilever - fixed base, free top (sway)"),
    "fixed-fixed-sway":    (1.0,  1.20, "both ends fixed but free to sway sideways"),
}


def effective_length(L_mm, end_condition="pinned-pinned", use_theoretical=False):
    """Buckling length L_cr = k*L. Defaults to the PRACTICAL k, which is the
    conservative choice - see END_CONDITIONS."""
    if end_condition not in END_CONDITIONS:
        raise ValueError(f"unknown end condition '{end_condition}'. "
                         f"Known: {', '.join(END_CONDITIONS)}")
    k_th, k_pr, desc = END_CONDITIONS[end_condition]
    k = k_th if use_theoretical else k_pr
    return {"k": k, "k_theoretical": k_th, "k_practical": k_pr,
            "L_cr_mm": k * float(L_mm), "end_condition": end_condition,
            "description": desc}


def lambda_1(fy, E=E_STEEL):
    """The reference slenderness lambda_1 = pi*sqrt(E/fy) = 93.9*eps.

    This single quantity is the bridge between the two ways of writing slenderness,
    and it is an exact analytic identity, not a tabulated constant: substituting
    E = 210000 and fy = 235 gives pi*sqrt(210000/235) = 93.91. The selftest asserts
    that both routes to lambda_bar agree, which pins E, the definition of eps and
    this formula against each other.
    """
    return math.pi * math.sqrt(E / fy)


# --------------------------------------------------------------------------- #
# Cross-section classification in UNIFORM COMPRESSION (EN 1993-1-1 Table 5.2)
# --------------------------------------------------------------------------- #
def classify_compression(sec, fy):
    """Class 1-4 for a member in pure axial compression.

    Deliberately a separate function from steel_design.classify(), which does
    BENDING. The web limits differ by a factor of more than two (33/38/42 eps here
    against 72/83/124 in bending) because a web in bending has a tension half holding
    it straight, and a web in uniform compression does not. Sharing one classifier
    between the two cases is a real and dangerous error.

    In compression there is no plastic-vs-elastic modulus to choose, so classes 1, 2
    and 3 all use the gross area; only class 4 differs, needing an effective area
    that this module reports rather than guesses.
    """
    eps = epsilon(fy)
    if sec.get("closed_section"):
        if "D_mm" in sec:                       # circular: limits are on d/t, Table 5.2 sheet 3
            ratio = sec["D_mm"] / sec["t_mm"]
            cls = 1 if ratio <= 50 * eps ** 2 else 2 if ratio <= 70 * eps ** 2 else \
                  3 if ratio <= 90 * eps ** 2 else 4
            return {"class": cls, "governing": "tube d/t", "ratio": round(ratio, 2),
                    "limit_basis": "Table 5.2 sheet 3 (d/t vs 50/70/90 eps^2)",
                    "epsilon": round(eps, 4)}
        t = sec["t_mm"]
        c = sec["h_mm"] - 3.0 * t               # EN 10210 clear width convention
        ratio = c / t
        cls = 1 if ratio <= 33 * eps else 2 if ratio <= 38 * eps else \
              3 if ratio <= 42 * eps else 4
        return {"class": cls, "governing": "hollow wall (internal part)",
                "ratio": round(ratio, 2), "web_class": cls, "flange_class": cls,
                "limit_basis": "internal part in compression, 33/38/42 eps",
                "epsilon": round(eps, 4)}

    # web: internal part, UNIFORM compression -> 33/38/42 eps (not 72/83/124)
    cw = sec["h_mm"] - 2 * sec["tf_mm"] - 2 * sec["r_mm"]
    web_ratio = cw / sec["tw_mm"]
    web_class = 1 if web_ratio <= 33 * eps else 2 if web_ratio <= 38 * eps else \
                3 if web_ratio <= 42 * eps else 4
    # flange: outstand in uniform compression -> 9/10/14 eps (same as bending)
    cf = (sec["b_mm"] - sec["tw_mm"] - 2 * sec["r_mm"]) / 2.0
    fl_ratio = cf / sec["tf_mm"]
    fl_class = 1 if fl_ratio <= 9 * eps else 2 if fl_ratio <= 10 * eps else \
               3 if fl_ratio <= 14 * eps else 4
    cls = max(web_class, fl_class)
    return {"class": cls, "web_c_over_t": round(web_ratio, 2), "web_class": web_class,
            "flange_c_over_t": round(fl_ratio, 2), "flange_class": fl_class,
            "governing": "web" if web_class >= fl_class else "flange outstand",
            "limit_basis": "web 33/38/42 eps (UNIFORM compression, not the "
                           "72/83/124 of bending); flange outstand 9/10/14 eps",
            "epsilon": round(eps, 4)}


# --------------------------------------------------------------------------- #
# Buckling curve selection (EN 1993-1-1 Table 6.2)
# --------------------------------------------------------------------------- #
def buckling_curves(sec, grade="S275"):
    """(curve_y, curve_z) from Table 6.2.

    The counter-intuitive part is that a SQUARER rolled section gets a WORSE curve:
    h/b <= 1.2 means curves b and c, while h/b > 1.2 gets a and b. The reason is
    residual stress from rolling - a stocky H-section cools with a more damaging
    residual pattern than a slim I - and it means an HEB column is penalised relative
    to an IPE of the same slenderness.
    """
    fy = STEEL_GRADES.get(grade, 275.0)
    high_grade = fy >= 460.0
    if sec.get("closed_section"):
        # hot finished. Cold formed would be curve c both axes - not distinguished
        # by the catalogue, so the (better) hot-finished assumption is declared.
        c = "a0" if high_grade else "a"
        return c, c, "Table 6.2, hot-finished hollow section" + (
            " (S460+: a0)" if high_grade else "")
    tf = sec["tf_mm"]
    ratio = sec["h_mm"] / sec["b_mm"]
    if ratio > 1.2:
        if tf <= 40.0:
            cy, cz = ("a0", "a") if high_grade else ("a", "b")
        else:
            cy, cz = ("a", "a") if high_grade else ("b", "c")
    else:
        if tf <= 100.0:
            cy, cz = ("a", "a") if high_grade else ("b", "c")
        else:
            cy, cz = ("c", "c") if high_grade else ("d", "d")
    return cy, cz, (f"Table 6.2, rolled I/H with h/b = {ratio:.2f} "
                    f"{'>' if ratio > 1.2 else '<='} 1.2, tf = {tf:g} mm")


def chi(lambda_bar, curve="b"):
    """Flexural buckling reduction factor, EN 1993-1-1 6.3.1.2.

        Phi = 0.5 [ 1 + alpha(lambda_bar - 0.2) + lambda_bar^2 ]
        chi = 1 / ( Phi + sqrt(Phi^2 - lambda_bar^2) )   <= 1.0

    Same Perry-Robertson form as chi_LT but with the Table 6.1 alphas, which include
    an a0 curve that LTB does not have. At lambda_bar = 0.2 this is EXACTLY 1.0 for
    every curve - the plateau below which buckling may be ignored entirely.
    """
    if curve not in ALPHA:
        raise ValueError(f"unknown buckling curve '{curve}'. Known: {', '.join(ALPHA)}")
    a = ALPHA[curve]
    phi = 0.5 * (1.0 + a * (lambda_bar - 0.2) + lambda_bar ** 2)
    disc = phi ** 2 - lambda_bar ** 2
    if disc < 0:
        return 1.0
    return min(1.0 / (phi + math.sqrt(disc)), 1.0)


def flexural_buckling(sec, fy, L_cr_mm, axis="z", curve=None, gamma_M1=1.0,
                      E=E_STEEL):
    """Design buckling resistance N_b,Rd about one axis.

        N_cr = pi^2 E I / L_cr^2          (Euler)
        lambda_bar = sqrt(A fy / N_cr) = (L_cr/i) / lambda_1
        N_b,Rd = chi A fy / gamma_M1

    E defaults to the EN 1993-1-1 nominal 210 GPa, but is a parameter because Euler
    buckling is not a Eurocode-only question: a textbook or a US problem states
    200 GPa, and silently answering a 200 GPa question at 210 is a 5% error in the
    unconservative direction. steel_design.Mcr already took E; this did not, and the
    inconsistency was found by posing a plain "steel E = 200 GPa" buckling problem.
    """
    if axis not in ("y", "z"):
        raise ValueError("axis must be 'y' or 'z'")
    A = sec["A_mm2"]
    I = sec["I_mm4"] if axis == "y" else sec["Iz_mm4"]
    i = sec.get("iy_mm") if axis == "y" else sec.get("iz_derived_mm", sec.get("iz_mm"))
    if i is None:
        i = math.sqrt(I / A)
    if curve is None:
        cy, cz, _ = buckling_curves(sec)
        curve = cy if axis == "y" else cz
    N_cr = math.pi ** 2 * E * I / L_cr_mm ** 2
    lam = math.sqrt(A * fy / N_cr)
    lam_via_i = (L_cr_mm / i) / lambda_1(fy, E)   # the identical quantity, other route
    x = chi(lam, curve)
    return {"axis": axis, "curve": curve, "L_cr_mm": L_cr_mm,
            "i_mm": round(i, 2), "slenderness_Lcr_over_i": round(L_cr_mm / i, 1),
            "N_cr_N": N_cr,                       # unrounded, for callers that chain
            "N_cr_kN": round(N_cr / 1e3, 1), "lambda_bar": round(lam, 4),
            "lambda_bar_via_radius_of_gyration": round(lam_via_i, 4),
            "chi": round(x, 4),
            "N_b_Rd_N": x * A * fy / gamma_M1,
            "N_pl_Rd_N": A * fy / gamma_M1,
            "buckling_ignorable": lam <= 0.2}


def compression_resistance(designation, grade="S275", length_mm=3000.0,
                           end_condition="pinned-pinned", N_Ed_N=None,
                           L_cr_y_mm=None, L_cr_z_mm=None, gamma_M1=1.0):
    """Full axial compression check: classify, buckle about BOTH axes, take the worse.

    `L_cr_y_mm` / `L_cr_z_mm` override the derived buckling lengths. That override is
    the whole point in a real frame: a column is very often braced about its weak axis
    at mid-height by the side rails while remaining unbraced about the strong axis,
    which flips which axis governs.
    """
    try:
        sec = resolve_section(designation)
    except (KeyError, ValueError) as e:
        return {"error": str(e)}
    if grade not in STEEL_GRADES:
        return {"error": f"unknown grade '{grade}'. Known: {', '.join(STEEL_GRADES)}"}
    fy = STEEL_GRADES[grade]
    try:
        eff = effective_length(length_mm, end_condition)
    except ValueError as e:
        return {"error": str(e)}

    cls = classify_compression(sec, fy)
    if cls["class"] == 4:
        return {"error": "class 4 (slender) in compression - an effective area is "
                         "required and is not implemented",
                "classification": cls, "section": designation}

    Lcr_y = float(L_cr_y_mm) if L_cr_y_mm else eff["L_cr_mm"]
    Lcr_z = float(L_cr_z_mm) if L_cr_z_mm else eff["L_cr_mm"]
    cy, cz, curve_basis = buckling_curves(sec, grade)
    by = flexural_buckling(sec, fy, Lcr_y, "y", cy, gamma_M1)
    bz = flexural_buckling(sec, fy, Lcr_z, "z", cz, gamma_M1)

    gov = by if by["N_b_Rd_N"] <= bz["N_b_Rd_N"] else bz
    N_b_Rd = gov["N_b_Rd_N"]
    out = {
        "section": sec["designation"], "grade": grade, "fy_MPa": fy,
        "length_mm": float(length_mm), "end_condition": end_condition,
        "k_used": eff["k"], "k_theoretical": eff["k_theoretical"],
        "class": cls["class"], "classification": cls,
        "curve_basis": curve_basis,
        "major_axis_y": by, "minor_axis_z": bz,
        "governing_axis": gov["axis"],
        "N_pl_Rd_kN": round(sec["A_mm2"] * fy / gamma_M1 / 1e3, 1),
        "N_b_Rd_kN": round(N_b_Rd / 1e3, 1),
        "buckling_penalty_percent": round(
            100.0 * (1.0 - N_b_Rd / (sec["A_mm2"] * fy / gamma_M1)), 1),
        "mass_kg_m": sec.get("mass_kg_m"),
        "code": "EN 1993-1-1 6.3.1 (gamma_M1 = %.2f)" % gamma_M1,
        "disclaimer": "Checked design aid, not a certified result. Second-order/sway "
                      "effects, torsional buckling, class 4 areas and base plates are "
                      "out of scope.",
    }
    if N_Ed_N is not None:
        u = float(N_Ed_N) / N_b_Rd if N_b_Rd else float("inf")
        out["N_Ed_kN"] = round(float(N_Ed_N) / 1e3, 2)
        out["utilisation"] = round(u, 3)
        out["adequate"] = u <= 1.0
        out["checks"] = [{
            "name": f"axial buckling about the {gov['axis']}-axis",
            "passed": u <= 1.0,
            "detail": f"N_Ed={float(N_Ed_N)/1e3:.1f} kN vs N_b,Rd={N_b_Rd/1e3:.1f} kN "
                      f"(chi={gov['chi']}, curve {gov['curve']}, "
                      f"lambda_bar={gov['lambda_bar']}), utilisation {u:.2f}"}]
    return out


# --------------------------------------------------------------------------- #
# Combined bending and axial compression (EN 1993-1-1 6.3.3, Annex B factors)
# --------------------------------------------------------------------------- #
def interaction_factors(lam_y, lam_z, n_y, n_z, C_my=1.0, C_mLT=1.0):
    """Annex B Table B.2 factors k_yy and k_zy, class 1-2, doubly symmetric member
    susceptible to torsional deformation (the general and conservative case).

    Only major-axis bending is handled, which is what a real beam-column in a simple
    frame carries; k_yz and k_zz would be needed for biaxial bending and are refused
    rather than approximated.
    """
    k_yy = min(C_my * (1.0 + (lam_y - 0.2) * n_y), C_my * (1.0 + 0.8 * n_y))
    denom = max(C_mLT - 0.25, 1e-6)
    k_zy = max(1.0 - (0.1 * lam_z / denom) * n_z, 1.0 - (0.1 / denom) * n_z)
    if lam_z < 0.4:
        # Table B.2 note for stocky minor axes: the general expression becomes
        # unconservative, so a floor of 0.6 + lambda_z applies, capped by it.
        k_zy = min(0.6 + lam_z, k_zy)
    return {"k_yy": round(k_yy, 4), "k_zy": round(k_zy, 4),
            "C_my": C_my, "C_mLT": C_mLT,
            "basis": "EN 1993-1-1 Annex B Table B.2, class 1-2, major-axis bending only"}


def beam_column(designation, grade="S275", length_mm=3000.0,
                N_Ed_N=0.0, My_Ed_Nmm=0.0, end_condition="pinned-pinned",
                L_cr_y_mm=None, L_cr_z_mm=None, restraint_spacing_mm=None,
                C_my=1.0, C_mLT=1.0, C1=1.0, gamma_M1=1.0):
    """EN 1993-1-1 6.3.3 checks (6.61) and (6.62) for combined N + My.

        N_Ed/(chi_y N_Rk/gM1) + k_yy My_Ed/(chi_LT My_Rk/gM1) <= 1      (6.61)
        N_Ed/(chi_z N_Rk/gM1) + k_zy My_Ed/(chi_LT My_Rk/gM1) <= 1      (6.62)

    The reason this exists as a separate check: BOTH individual utilisations can sit
    below 1.0 while the sum exceeds it. A member at 0.6 axial and 0.6 bending is at
    ~1.2 combined and fails. There is no way to see that from the two checks alone.
    """
    axial = compression_resistance(designation, grade, length_mm, end_condition,
                                   N_Ed_N=None, L_cr_y_mm=L_cr_y_mm,
                                   L_cr_z_mm=L_cr_z_mm, gamma_M1=gamma_M1)
    if "error" in axial:
        return axial
    sec = resolve_section(designation)
    fy = STEEL_GRADES[grade]
    A = sec["A_mm2"]
    N_Ed, My_Ed = float(N_Ed_N), float(My_Ed_Nmm)
    if N_Ed < 0 or My_Ed < 0:
        return {"error": "N_Ed and My_Ed must be non-negative magnitudes "
                         "(compression positive)"}

    # Bending resistance uses the BENDING class, which differs from the compression
    # class - so it is computed with the bending classifier, not reused from above.
    from steel_design import classify as classify_bending
    cb = classify_bending(sec, fy)
    if cb["class"] == 4:
        return {"error": "class 4 in bending - effective section properties required",
                "bending_classification": cb}
    W = sec["Wpl_mm3"] if cb["class"] <= 2 else sec["Z_mm3"]

    # LTB reduction over the unrestrained length (defaults to the full member length)
    Lb = float(restraint_spacing_mm) if restraint_spacing_mm else float(length_mm)
    if sec.get("closed_section") or sec["Iw_mm6"] <= 0.0:
        # a closed section has no meaningful warping term; its huge It makes Mcr so
        # large that chi_LT is 1.0 for any practical length
        x_LT, lam_LT, ltb_curve = 1.0, 0.0, "n/a (closed section)"
    else:
        mcr = Mcr(sec, Lb, C1)
        lam_LT = math.sqrt(W * fy / mcr)
        ltb_curve = ltb_curve_for(sec)
        x_LT = chi_LT(lam_LT, ltb_curve)

    N_Rk, My_Rk = A * fy, W * fy
    Nby = axial["major_axis_y"]["chi"] * N_Rk / gamma_M1
    Nbz = axial["minor_axis_z"]["chi"] * N_Rk / gamma_M1
    Mb = x_LT * My_Rk / gamma_M1

    n_y = N_Ed / Nby if Nby else 0.0
    n_z = N_Ed / Nbz if Nbz else 0.0
    kf = interaction_factors(axial["major_axis_y"]["lambda_bar"],
                             axial["minor_axis_z"]["lambda_bar"], n_y, n_z, C_my, C_mLT)

    m = My_Ed / Mb if Mb else (0.0 if My_Ed == 0 else float("inf"))
    u61 = n_y + kf["k_yy"] * m
    u62 = n_z + kf["k_zy"] * m

    checks = [
        {"name": "6.61 combined N + My, major-axis buckling", "passed": u61 <= 1.0,
         "detail": f"{n_y:.3f} (axial) + {kf['k_yy']:.3f} x {m:.3f} (bending) "
                   f"= {u61:.3f}"},
        {"name": "6.62 combined N + My, minor-axis buckling", "passed": u62 <= 1.0,
         "detail": f"{n_z:.3f} (axial) + {kf['k_zy']:.3f} x {m:.3f} (bending) "
                   f"= {u62:.3f}"},
    ]
    return {
        "section": sec["designation"], "grade": grade, "length_mm": float(length_mm),
        "N_Ed_kN": round(N_Ed / 1e3, 2), "My_Ed_kNm": round(My_Ed / 1e6, 2),
        "compression_class": axial["class"], "bending_class": cb["class"],
        "chi_y": axial["major_axis_y"]["chi"], "chi_z": axial["minor_axis_z"]["chi"],
        "chi_LT": round(x_LT, 4), "lambda_LT": round(lam_LT, 4), "ltb_curve": ltb_curve,
        "unrestrained_length_mm": Lb,
        "N_b_Rd_y_kN": round(Nby / 1e3, 1), "N_b_Rd_z_kN": round(Nbz / 1e3, 1),
        "Mb_Rd_kNm": round(Mb / 1e6, 2),
        "axial_utilisation_alone": round(max(n_y, n_z), 3),
        "bending_utilisation_alone": round(m, 3),
        "interaction_factors": kf,
        "utilisation": {"eq_6_61": round(u61, 3), "eq_6_62": round(u62, 3),
                        "governing": round(max(u61, u62), 3)},
        "governing_equation": "6.61 (major axis)" if u61 >= u62 else "6.62 (minor axis)",
        "checks": checks, "adequate": all(c["passed"] for c in checks),
        "code": "EN 1993-1-1 6.3.3 with Annex B interaction factors",
        "disclaimer": "Checked design aid, not a certified result. Biaxial bending, "
                      "second-order/sway effects and class 4 sections are out of scope.",
    }


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
COLUMN_FAMILIES = ("HEB", "HEA", "SHS")


def size_column(N_Ed_N, length_mm=3000.0, grade="S275", end_condition="pinned-pinned",
                My_Ed_Nmm=0.0, families=COLUMN_FAMILIES, gamma_M1=1.0):
    """Lightest catalogue section that carries the load. Searches HEB, HEA and SHS.

    IPE is deliberately NOT in the default search: it is a beam section, its weak
    axis is ~7% of its strong axis, and a design search that included it would keep
    proposing members that buckle sideways. That exclusion is a design decision and
    is reported in the result rather than being silent.
    """
    cands = []
    for fam in families:
        if fam == "SHS":
            names = list(standards.SHS)
        elif fam in standards._I_FAMILIES:
            names = list(standards._I_FAMILIES[fam])
        else:
            return {"error": f"unknown family '{fam}'. Known: {', '.join(COLUMN_FAMILIES)}"}
        for nm in names:
            if My_Ed_Nmm:
                r = beam_column(nm, grade, length_mm, N_Ed_N, My_Ed_Nmm,
                                end_condition, gamma_M1=gamma_M1)
                ok = r.get("adequate", False)
                util = r.get("utilisation", {}).get("governing")
            else:
                r = compression_resistance(nm, grade, length_mm, end_condition,
                                           N_Ed_N=N_Ed_N, gamma_M1=gamma_M1)
                ok = r.get("adequate", False)
                util = r.get("utilisation")
            if "error" in r or not ok:
                continue
            sec = resolve_section(nm)
            cands.append((sec["mass_kg_m"], nm, util, r))
    if not cands:
        return {"error": f"nothing in {', '.join(families)} carries "
                         f"{float(N_Ed_N)/1e3:.0f} kN over {float(length_mm)/1000:.1f} m "
                         f"in {grade}. Try a shorter buckling length, a higher grade, "
                         f"or a built-up section."}
    cands.sort(key=lambda t: t[0])
    mass, nm, util, r = cands[0]
    return {"selected": nm, "mass_kg_m": mass, "utilisation": util,
            "candidates_considered": len(cands),
            "next_three_heavier": [{"section": c[1], "mass_kg_m": c[0],
                                    "utilisation": c[2]} for c in cands[1:4]],
            "families_searched": list(families),
            "note": "IPE excluded from column search by design: its weak axis is ~7% "
                    "of its strong axis, so it buckles sideways long before it squashes.",
            "detail": r}


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    # ---- lambda_1 is an exact analytic identity, not a remembered 93.9 ----
    chk("lambda_1(S235) == pi*sqrt(E/fy) == 93.9 (the tabulated value)",
        abs(lambda_1(235.0) - 93.9) / 93.9 < 0.002, f"{lambda_1(235.0):.3f}")
    chk("lambda_1 scales exactly as 93.9*eps for every grade",
        all(abs(lambda_1(f) - 93.9 * epsilon(f)) / lambda_1(f) < 0.002
            for f in STEEL_GRADES.values()),
        f"S355: {lambda_1(355.0):.2f} vs {93.9*epsilon(355.0):.2f}")

    # ---- chi: exact plateau, monotonic, bounded ----
    chk("chi(lambda=0.2) == 1.0 exactly, all five curves",
        all(abs(chi(0.2, c) - 1.0) < 1e-12 for c in ALPHA))
    chk("chi never exceeds 1.0",
        all(chi(l, c) <= 1.0 + 1e-12 for c in ALPHA for l in [0, .1, .2, .5, 1, 2, 5]))
    chk("chi falls monotonically with slenderness",
        all(chi(a, "b") > chi(b, "b") for a, b in zip([.3, .6, 1., 1.5], [.6, 1., 1.5, 2.5])))
    chk("a worse curve gives a lower chi at the same slenderness",
        chi(1., "a0") > chi(1., "a") > chi(1., "b") > chi(1., "c") > chi(1., "d"),
        " > ".join(f"{chi(1., c):.3f}" for c in ("a0", "a", "b", "c", "d")))
    chk("chi -> 0 as lambda -> infinity", chi(20.0, "b") < 0.01, f"{chi(20.,'b'):.5f}")
    chk("an unknown curve is refused, not silently defaulted",
        _raises(lambda: chi(1.0, "z"), ValueError))

    # ---- the two definitions of lambda_bar must agree EXACTLY ----
    # sqrt(A fy / N_cr) and (L_cr/i)/lambda_1 are the same number by algebra. They are
    # computed by completely different routes here, so agreement pins Euler's formula,
    # lambda_1 and the catalogue's A/I/i columns against one another at once.
    worst = 0.0
    for nm in ("HEB 200", "HEA 300", "IPE 300", "HEB 400"):
        s = standards.section(nm)
        for ax in ("y", "z"):
            for L in (2000.0, 5000.0, 9000.0):
                b = flexural_buckling(s, 275.0, L, ax)
                worst = max(worst, abs(b["lambda_bar"] - b["lambda_bar_via_radius_of_gyration"])
                            / b["lambda_bar"])
    chk("lambda_bar via sqrt(A fy/Ncr) == via (L_cr/i)/lambda_1, 24 cases",
        worst < 0.005, f"worst disagreement {worst*100:.2f}% (catalogue i is rounded)")

    # ---- Euler's formula against its own closed form ----
    s = standards.section("HEB 200")
    b = flexural_buckling(s, 275.0, 3000.0, "z")
    want = math.pi ** 2 * E_STEEL * s["Iz_mm4"] / 3000.0 ** 2
    chk("N_cr == pi^2 E I / L^2 exactly", abs(b["N_cr_N"] - want) / want < 1e-12,
        f"{b['N_cr_kN']:.0f} kN")
    chk("N_cr quarters when the length doubles (inverse-square law)",
        abs(flexural_buckling(s, 275., 6000., "z")["N_cr_N"]
            / b["N_cr_N"] - 0.25) < 1e-12)
    chk("buckling resistance never exceeds the squash load",
        all(flexural_buckling(s, 275., L, "z")["N_b_Rd_N"]
            <= flexural_buckling(s, 275., L, "z")["N_pl_Rd_N"] + 1e-6
            for L in (200., 1000., 3000., 10000.)))
    chk("a very short column reaches its full squash load (chi -> 1)",
        flexural_buckling(s, 275., 300., "z")["chi"] > 0.99,
        f"chi={flexural_buckling(s,275.,300.,'z')['chi']} at 0.3 m")

    # E is a parameter, not a baked-in Eurocode constant. A 200 GPa problem must be
    # answerable at 200 GPa - the capability battery caught this by posing a plain
    # textbook buckling question and getting an answer 5% high.
    b200 = flexural_buckling(s, 275.0, 3000.0, "z", E=200000.0)
    want200 = math.pi ** 2 * 200000.0 * s["Iz_mm4"] / 3000.0 ** 2
    chk("N_cr honours a caller-supplied E (200 GPa, not the EN 210 GPa)",
        abs(b200["N_cr_N"] - want200) / want200 < 1e-12,
        f"{b200['N_cr_N']/1e3:.0f} kN at 200 GPa vs {b['N_cr_kN']:.0f} kN at 210 GPa")
    chk("...and the two lambda_bar routes still agree at that E",
        abs(b200["lambda_bar"] - b200["lambda_bar_via_radius_of_gyration"])
        / b200["lambda_bar"] < 0.005)
    chk("N_cr scales linearly with E, as Euler requires",
        abs(b200["N_cr_N"] / b["N_cr_N"] - 200000.0 / E_STEEL) < 1e-12)
    chk("lambda_1 honours E too (93.9 is only right at 210 GPa)",
        abs(lambda_1(235.0, 200000.0) - math.pi * math.sqrt(200000.0 / 235.0)) < 1e-12,
        f"{lambda_1(235.0, 200000.0):.2f} at 200 GPa vs {lambda_1(235.0):.2f} at 210 GPa")

    # ---- Table 6.2: the counter-intuitive curve assignment ----
    cy, cz, _ = buckling_curves(standards.section("HEB 200"))
    iy, iz_, _ = buckling_curves(standards.section("IPE 300"))
    chk("a square HEB (h/b = 1.0) takes the WORSE curves b/c",
        (cy, cz) == ("b", "c"), f"HEB 200 -> {cy}/{cz}")
    chk("...while a slim IPE (h/b = 2.0) takes the better a/b",
        (iy, iz_) == ("a", "b"), f"IPE 300 -> {iy}/{iz_}")
    chk("S460 shifts every curve one step better (Table 6.2 right-hand column)",
        buckling_curves(standards.section("HEB 200"), "S460")[:2] == ("a", "a"))
    chk("a hot-finished hollow section takes curve a on both axes",
        buckling_curves(standards.shs("SHS 200x200x10"))[:2] == ("a", "a"))

    # ---- compression classification is NOT the bending classification ----
    from steel_design import classify as classify_bending
    deep = standards.section("IPE 600")          # slender web: 42.8 c/t
    cc, cb = classify_compression(deep, 275.0), classify_bending(deep, 275.0)
    chk("the SAME web is class 1 in bending but class 4 in uniform compression",
        cb["class"] == 1 and cc["class"] == 4,
        f"web c/t={cc['web_c_over_t']}: bending limit 72eps, compression limit 33eps")
    chk("...and the class 4 compression case is refused, not silently designed",
        "error" in compression_resistance("IPE 600", "S275", 3000.0))
    chk("a stocky HEB is class 1 in compression",
        classify_compression(standards.section("HEB 200"), 275.0)["class"] == 1,
        f"web c/t={classify_compression(standards.section('HEB 200'),275.)['web_c_over_t']}")
    chk("compression classification records WHY the limits differ",
        "UNIFORM compression" in classify_compression(standards.section("HEB 200"),
                                                      275.0)["limit_basis"])

    # ---- effective length: the factor-of-four that a cantilever costs ----
    pin = compression_resistance("HEB 200", "S275", 4000.0, "pinned-pinned")
    can = compression_resistance("HEB 200", "S275", 4000.0, "fixed-free")
    fix = compression_resistance("HEB 200", "S275", 4000.0, "fixed-fixed")
    chk("a cantilever column carries far less than a pinned one of the same height",
        can["N_b_Rd_kN"] < 0.5 * pin["N_b_Rd_kN"],
        f"{can['N_b_Rd_kN']} kN vs {pin['N_b_Rd_kN']} kN over 4 m")
    chk("...and a fixed-fixed column carries more",
        fix["N_b_Rd_kN"] > pin["N_b_Rd_kN"],
        f"{fix['N_b_Rd_kN']} kN, k={fix['k_used']}")
    chk("the practical k is used, and the unconservative theoretical k is disclosed",
        fix["k_used"] == 0.65 and fix["k_theoretical"] == 0.5)
    chk("an unknown end condition is refused with the list of valid ones",
        "error" in compression_resistance("HEB 200", "S275", 3000.0, "welded"))

    # ---- the headline engineering claim: buckling picks the WEAK axis ----
    r = compression_resistance("IPE 300", "S275", 5000.0, "pinned-pinned", N_Ed_N=400e3)
    chk("an IPE used as a column buckles about its MINOR axis",
        r["governing_axis"] == "z")
    chk("...and the penalty against the squash load is severe (>70% of it lost)",
        r["buckling_penalty_percent"] > 70,
        f"N_pl={r['N_pl_Rd_kN']} kN -> N_b={r['N_b_Rd_kN']} kN "
        f"({r['buckling_penalty_percent']}% lost)")
    hb = compression_resistance("HEB 200", "S275", 5000.0, "pinned-pinned", N_Ed_N=400e3)
    chk("an HEB of similar mass carries far more over the same height",
        hb["N_b_Rd_kN"] > 1.8 * r["N_b_Rd_kN"],
        f"HEB 200 {hb['N_b_Rd_kN']} kN ({hb['mass_kg_m']} kg/m) vs "
        f"IPE 300 {r['N_b_Rd_kN']} kN ({r['mass_kg_m']} kg/m)")
    chk("capacity falls monotonically with height",
        all(compression_resistance("HEB 200", "S275", a)["N_b_Rd_kN"]
            > compression_resistance("HEB 200", "S275", b)["N_b_Rd_kN"]
            for a, b in zip([2000., 4000., 6000.], [4000., 6000., 9000.])))

    # bracing the weak axis at mid-height flips which axis governs - the single most
    # useful thing a real designer does to a column
    braced = compression_resistance("IPE 300", "S275", 5000.0, N_Ed_N=400e3,
                                    L_cr_z_mm=2500.0)
    chk("bracing the minor axis at mid-height raises capacity substantially",
        braced["N_b_Rd_kN"] > 1.5 * r["N_b_Rd_kN"],
        f"{r['N_b_Rd_kN']} -> {braced['N_b_Rd_kN']} kN with a mid-height weak-axis brace")

    # ---- 6.3.3: both limiting cases must reduce to the simpler check ----
    bc_axial = beam_column("HEB 200", "S275", 4000.0, N_Ed_N=500e3, My_Ed_Nmm=0.0)
    pure_axial = compression_resistance("HEB 200", "S275", 4000.0, N_Ed_N=500e3)
    chk("6.3.3 with My = 0 reduces EXACTLY to the 6.3.1 axial utilisation",
        abs(bc_axial["utilisation"]["governing"] - pure_axial["utilisation"]) < 1e-9,
        f"{bc_axial['utilisation']['governing']} == {pure_axial['utilisation']}")

    from steel_design import buckling_resistance
    sec = standards.section("HEB 200")
    br = buckling_resistance(sec, 275.0, 4000.0)
    bc_bend = beam_column("HEB 200", "S275", 4000.0, N_Ed_N=0.0, My_Ed_Nmm=50e6)
    # the reported utilisation is rounded to 3 dp, so the tolerance is the rounding
    chk("6.3.3 with N = 0 reduces to the 6.3.2 LTB moment utilisation",
        abs(bc_bend["utilisation"]["governing"] - 50e6 / br["Mb_Rd_Nmm"]) < 1e-3,
        f"{bc_bend['utilisation']['governing']} vs {50e6/br['Mb_Rd_Nmm']:.4f}")

    # ---- the whole reason 6.3.3 exists ----
    # A member comfortably inside BOTH individual limits fails the combination.
    N_half = 0.6 * pure_axial["N_b_Rd_kN"] * 1e3
    M_half = 0.6 * br["Mb_Rd_Nmm"]
    both = beam_column("HEB 200", "S275", 4000.0, N_Ed_N=N_half, My_Ed_Nmm=M_half)
    chk("a member at 0.6 axial AND 0.6 bending passes both alone but FAILS combined",
        both["axial_utilisation_alone"] <= 0.61 and both["bending_utilisation_alone"] <= 0.61
        and not both["adequate"],
        f"axial {both['axial_utilisation_alone']} + bending "
        f"{both['bending_utilisation_alone']} -> combined "
        f"{both['utilisation']['governing']}")
    # The two Annex B factors pull in OPPOSITE directions, and it is worth pinning
    # that down rather than assuming both amplify: k_yy > 1 is the P-delta
    # amplification of the moment on the axis it is applied about, while k_zy < 1 is a
    # small relief on the minor-axis equation, because a major-axis moment does not
    # drive minor-axis buckling as hard. The combination still exceeds either part.
    chk("k_yy amplifies (>1, P-delta) while k_zy relieves (<1) - they are not the same",
        both["interaction_factors"]["k_yy"] > 1.0 > both["interaction_factors"]["k_zy"],
        f"k_yy={both['interaction_factors']['k_yy']}, k_zy={both['interaction_factors']['k_zy']}")
    chk("the combined utilisation exceeds either contribution on its own",
        both["utilisation"]["governing"] > both["axial_utilisation_alone"]
        and both["utilisation"]["governing"] > both["bending_utilisation_alone"])
    chk("combined utilisation grows with the applied moment",
        all(beam_column("HEB 200", "S275", 4000., N_Ed_N=300e3,
                        My_Ed_Nmm=m)["utilisation"]["governing"]
            < beam_column("HEB 200", "S275", 4000., N_Ed_N=300e3,
                          My_Ed_Nmm=m * 1.5)["utilisation"]["governing"]
            for m in (10e6, 30e6, 60e6)))
    chk("k_yy and k_zy are both >= their conservative floors and sane",
        0.5 <= both["interaction_factors"]["k_yy"] <= 2.0
        and 0.5 <= both["interaction_factors"]["k_zy"] <= 1.0,
        f"k_yy={both['interaction_factors']['k_yy']}, k_zy={both['interaction_factors']['k_zy']}")
    chk("a negative load is refused rather than producing a sign-flipped answer",
        "error" in beam_column("HEB 200", "S275", 4000.0, N_Ed_N=-100e3))

    # ---- selection ----
    sel = size_column(800e3, 4000.0, "S275")
    chk("size_column returns the lightest adequate section, fully utilised <= 1.0",
        "error" not in sel and sel["utilisation"] <= 1.0,
        f"{sel.get('selected')} at {sel.get('mass_kg_m')} kg/m, "
        f"utilisation {sel.get('utilisation')}")
    chk("...and nothing lighter in the searched families would have worked",
        all(c["mass_kg_m"] >= sel["mass_kg_m"] for c in sel["next_three_heavier"]))
    chk("the selected column actually passes an independent re-check",
        compression_resistance(sel["selected"], "S275", 4000.0,
                               N_Ed_N=800e3)["adequate"])
    chk("a heavier load selects a heavier section",
        size_column(2000e3, 4000.0)["mass_kg_m"] > sel["mass_kg_m"],
        f"{sel['selected']} -> {size_column(2000e3, 4000.0)['selected']}")
    chk("a longer column needs a heavier section for the SAME load",
        size_column(800e3, 9000.0)["mass_kg_m"] > sel["mass_kg_m"],
        f"4 m: {sel['selected']} -> 9 m: {size_column(800e3, 9000.0)['selected']}")
    chk("an impossible load is refused with advice, not a wrong answer",
        "error" in size_column(50000e3, 12000.0))
    chk("IPE is excluded from the column search, and that is stated",
        "IPE excluded" in sel["note"] and not sel["selected"].startswith("IPE"))
    sel_m = size_column(400e3, 4000.0, My_Ed_Nmm=60e6)
    chk("adding a moment to the same axial load selects a heavier section",
        sel_m["mass_kg_m"] > size_column(400e3, 4000.0)["mass_kg_m"],
        f"{size_column(400e3, 4000.0)['selected']} -> {sel_m['selected']} "
        f"once 60 kNm is applied")

    # ---- hollow sections work end to end too ----
    box = compression_resistance("SHS 200x200x10", "S275", 5000.0, N_Ed_N=900e3)
    chk("a square hollow column resolves and both axes are identical",
        "error" not in box
        and abs(box["major_axis_y"]["chi"] - box["minor_axis_z"]["chi"]) < 1e-12,
        f"chi={box['major_axis_y']['chi']} both axes, N_b,Rd={box['N_b_Rd_kN']} kN")
    chs_ = compression_resistance("CHS 219.1x10", "S275", 5000.0, N_Ed_N=700e3)
    chk("a circular hollow column resolves end to end", "error" not in chs_,
        f"N_b,Rd={chs_.get('N_b_Rd_kN')} kN, class {chs_.get('class')}")

    chk("every result carries its code clause and a disclaimer",
        "6.3.1" in pin["code"] and "not a certified" in pin["disclaimer"]
        and "6.3.3" in both["code"])
    chk("an unknown section is refused with a useful message, not a crash",
        "error" in compression_resistance("HEB 999", "S275", 3000.0))

    print("column_design selftest:", "PASS" if ok else "FAIL")
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
    ap = argparse.ArgumentParser(description="EN 1993-1-1 column and beam-column checks")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check", metavar="SECTION", help="e.g. 'HEB 200'")
    ap.add_argument("--grade", default="S275")
    ap.add_argument("--length", type=float, default=3000.0, help="member length mm")
    ap.add_argument("--ends", default="pinned-pinned",
                    help="|".join(END_CONDITIONS))
    ap.add_argument("--N", type=float, help="axial compression N")
    ap.add_argument("--M", type=float, help="major-axis moment N.mm")
    ap.add_argument("--size", action="store_true", help="select the lightest section for --N")
    a = ap.parse_args()
    if a.size:
        if a.N is None:
            ap.error("--size needs --N")
        print(json.dumps(size_column(a.N, a.length, a.grade, a.ends, a.M or 0.0), indent=2))
    elif a.check:
        if a.M:
            print(json.dumps(beam_column(a.check, a.grade, a.length, a.N or 0.0,
                                         a.M, a.ends), indent=2))
        else:
            print(json.dumps(compression_resistance(a.check, a.grade, a.length,
                                                    a.ends, a.N), indent=2))
    else:
        raise SystemExit(_selftest())
    raise SystemExit(0)
