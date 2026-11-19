#!/usr/bin/env python3
"""
steel_design.py - EN 1993-1-1 steel member checks: the failure modes bending alone misses.

`structural.py` answers "is the bending stress below yield". That is not enough to
design a steel beam, and the gap is dangerous rather than merely incomplete:

  * SHEAR governs short deep beams. Bending stress can be trivial while the web yields.
  * LATERAL-TORSIONAL BUCKLING governs long unrestrained beams. The beam fails by
    twisting sideways at a moment far below its in-plane capacity - an I-section is
    stiff about y and weak about z, and that asymmetry is exactly what LTB exploits.
  * CROSS-SECTION CLASS decides whether the plastic or only the elastic modulus may be
    used. Assuming Wpl on a slender section overstates capacity by the shape factor.

Every formula here is implemented from the EN 1993-1-1 definitions and then verified
against something independent - a limiting case with a known closed form, an exact
analytic value, or a physical monotonicity that must hold. Where a constant could have
been misremembered, there is a test that would fail if it were.

Units: N, mm, MPa throughout. Partial factors default to the EN recommended
gamma_M0 = gamma_M1 = 1.00.

A checked design aid, NOT a certified result. Buckling of the web, bearing, fatigue,
connections, second-order effects and the National Annex are all out of scope.
A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math

E_STEEL = 210000.0      # MPa, EN 1993-1-1 3.2.6
NU_STEEL = 0.3
G_STEEL = E_STEEL / (2.0 * (1.0 + NU_STEEL))    # 80769 MPa - derived, not typed in

# EN 1993-1-1 Table 6.3 imperfection factors, by buckling curve
ALPHA_LT = {"a": 0.21, "b": 0.34, "c": 0.49, "d": 0.76}

# EN 10025 hot-rolled structural steels: nominal yield by grade (t <= 40 mm)
STEEL_GRADES = {"S235": 235.0, "S275": 275.0, "S355": 355.0, "S420": 420.0, "S460": 460.0}


def epsilon(fy):
    """Material factor eps = sqrt(235/fy). Every slenderness limit scales with it."""
    return math.sqrt(235.0 / fy)


# --------------------------------------------------------------------------- #
# Cross-section classification (EN 1993-1-1 Table 5.2), rolled I in bending
# --------------------------------------------------------------------------- #
def classify(sec, fy):
    """Class 1-4 in major-axis bending, EN 1993-1-1 Table 5.2.

    Class 1/2 may use the plastic modulus; class 3 only the elastic; class 4 is
    slender and needs effective properties (not implemented - reported, not guessed).

    Two different limit sets apply. An I-section's flange is an OUTSTAND in
    compression (limits 9/10/14 eps); a hollow section's flange is an INTERNAL part
    with both edges supported, which is far more stable (limits 33/38/42 eps). Using
    the outstand limits on a box would wrongly condemn it.
    """
    eps = epsilon(fy)
    if sec.get("closed_section"):
        t = sec["t_mm"]
        c = sec["h_mm"] - 3.0 * t          # EN 10210 convention: clear width, c = b - 3t
        web_ratio = c / t
        web_class = 1 if web_ratio <= 72 * eps else 2 if web_ratio <= 83 * eps else \
                    3 if web_ratio <= 124 * eps else 4
        fl_ratio = c / t                    # internal compression part
        fl_class = 1 if fl_ratio <= 33 * eps else 2 if fl_ratio <= 38 * eps else \
                   3 if fl_ratio <= 42 * eps else 4
    else:
        # web in bending: clear depth between root fillets
        cw = sec["h_mm"] - 2 * sec["tf_mm"] - 2 * sec["r_mm"]
        web_ratio = cw / sec["tw_mm"]
        web_class = 1 if web_ratio <= 72 * eps else 2 if web_ratio <= 83 * eps else \
                    3 if web_ratio <= 124 * eps else 4
        # flange outstand in compression
        cf = (sec["b_mm"] - sec["tw_mm"] - 2 * sec["r_mm"]) / 2.0
        fl_ratio = cf / sec["tf_mm"]
        fl_class = 1 if fl_ratio <= 9 * eps else 2 if fl_ratio <= 10 * eps else \
                   3 if fl_ratio <= 14 * eps else 4
    cls = max(web_class, fl_class)
    return {"class": cls, "web_c_over_t": round(web_ratio, 2), "web_class": web_class,
            "flange_c_over_t": round(fl_ratio, 2), "flange_class": fl_class,
            "epsilon": round(eps, 4),
            "modulus_allowed": "plastic (Wpl)" if cls <= 2 else
                               "elastic (Wel)" if cls == 3 else "effective (class 4)"}


def moment_resistance(sec, fy, gamma_M0=1.0):
    """In-plane bending resistance Mc,Rd, using the modulus the class permits."""
    c = classify(sec, fy)
    W = sec["Wpl_mm3"] if c["class"] <= 2 else sec["Z_mm3"]
    if c["class"] == 4:
        return {"error": "class 4 (slender) section - effective width properties required",
                "classification": c}
    return {"Mc_Rd_Nmm": W * fy / gamma_M0, "W_used_mm3": W,
            "basis": c["modulus_allowed"], "classification": c}


# --------------------------------------------------------------------------- #
# Shear (EN 1993-1-1 6.2.6)
# --------------------------------------------------------------------------- #
def shear_area(sec, eta=1.0):
    """Av per EN 1993-1-1 6.2.6(3). Different rule per section type:
      rolled I, load parallel to web:  Av = A - 2*b*tf + (tw + 2r)*tf, min eta*hw*tw
      rectangular/square hollow:       Av = A*h/(b+h)  - the two webs carry the shear
      circular hollow:                 Av = 2A/pi
    """
    A = sec["A_mm2"]
    if sec.get("closed_section"):
        if "D_mm" in sec:                       # circular
            return 2.0 * A / math.pi
        h, b = sec["h_mm"], sec["b_mm"]         # rectangular/square
        return A * h / (b + h)
    Av = A - 2 * sec["b_mm"] * sec["tf_mm"] + (sec["tw_mm"] + 2 * sec["r_mm"]) * sec["tf_mm"]
    hw = sec["h_mm"] - 2 * sec["tf_mm"]
    return max(Av, eta * hw * sec["tw_mm"])


def shear_resistance(sec, fy, gamma_M0=1.0):
    """Plastic shear resistance. The 1/sqrt(3) is the von Mises shear yield: under
    pure shear the equivalent stress is sqrt(3)*tau, so yielding starts at fy/sqrt(3)."""
    Av = shear_area(sec)
    return {"Av_mm2": round(Av, 1), "tau_y_MPa": round(fy / math.sqrt(3.0), 2),
            "Vpl_Rd_N": Av * fy / math.sqrt(3.0) / gamma_M0}


def shear_moment_interaction(V_Ed, Vpl_Rd):
    """EN 1993-1-1 6.2.8: below half the shear capacity, bending is unaffected.
    Above it, the web's yield is reduced by (1-rho) with rho = (2V/Vpl - 1)^2."""
    if Vpl_Rd <= 0:
        return {"rho": 0.0, "reduces_moment": False}
    ratio = V_Ed / Vpl_Rd
    rho = 0.0 if ratio <= 0.5 else (2.0 * ratio - 1.0) ** 2
    return {"V_over_Vpl": round(ratio, 4), "rho": round(rho, 5),
            "reduces_moment": rho > 0.0}


# --------------------------------------------------------------------------- #
# Lateral-torsional buckling (EN 1993-1-1 6.3.2)
# --------------------------------------------------------------------------- #
def Mcr(sec, L, C1=1.0, E=E_STEEL, G=G_STEEL):
    """Elastic critical moment of a doubly symmetric section, load at the shear
    centre, ends free to warp (k = kw = 1):

        Mcr = C1 * (pi^2 E Iz / L^2) * sqrt( Iw/Iz + L^2 G It / (pi^2 E Iz) )

    The two terms under the root are the warping and St Venant torsional
    contributions; the selftest checks that killing either one reproduces its own
    classical closed form, which is what makes this more than a remembered formula.
    """
    Iz, Iw, It = sec["Iz_mm4"], sec["Iw_mm6"], sec["It_mm4"]
    base = math.pi ** 2 * E * Iz / L ** 2
    return C1 * base * math.sqrt(Iw / Iz + L ** 2 * G * It / (math.pi ** 2 * E * Iz))


def chi_LT(lambda_LT, curve="b"):
    """LTB reduction factor, EN 1993-1-1 6.3.2.2 (general case).

        phi = 0.5 [ 1 + alpha(lambda - 0.2) + lambda^2 ]
        chi = 1 / ( phi + sqrt(phi^2 - lambda^2) )   <= 1.0

    At lambda = 0.2 this returns EXACTLY 1.0 for every curve - phi = 0.52 and
    sqrt(0.52^2 - 0.2^2) = 0.48. The selftest asserts that, which pins the plateau
    constant and the leading 0.5 independently of any table.
    """
    a = ALPHA_LT[curve]
    phi = 0.5 * (1.0 + a * (lambda_LT - 0.2) + lambda_LT ** 2)
    disc = phi ** 2 - lambda_LT ** 2
    if disc < 0:
        return 1.0
    return min(1.0 / (phi + math.sqrt(disc)), 1.0)


def ltb_curve_for(sec):
    """EN 1993-1-1 Table 6.4, rolled sections: h/b <= 2 -> curve a, else curve b.
    Hollow sections take curve a - though in practice their torsional stiffness makes
    Mcr so large that chi_LT reaches 1.0 and the curve choice stops mattering."""
    if sec.get("closed_section"):
        return "a"
    return "a" if sec["h_mm"] / sec["b_mm"] <= 2.0 else "b"


def resolve_section(designation):
    """Look a section up across every catalogue family: IPE, SHS, or CHS.
    Accepts 'IPE 300', 'SHS 200x200x10', or 'CHS 219.1x10'."""
    import standards
    d = str(designation).strip()
    low = d.lower()
    if low.startswith("chs"):
        nums = low.replace("chs", "").replace("*", "x").strip().split("x")
        if len(nums) != 2:
            raise KeyError(f"CHS needs 'CHS <outer_dia>x<wall>', got '{designation}'")
        return standards.chs(float(nums[0]), float(nums[1]))
    if low.startswith("shs"):
        return standards.shs(d)
    return standards.section(d)


def buckling_resistance(sec, fy, L, C1=1.0, gamma_M1=1.0):
    """Design buckling resistance moment Mb,Rd for an unrestrained length L."""
    c = classify(sec, fy)
    if c["class"] == 4:
        return {"error": "class 4 section - out of scope", "classification": c}
    W = sec["Wpl_mm3"] if c["class"] <= 2 else sec["Z_mm3"]
    mcr = Mcr(sec, L, C1)
    lam = math.sqrt(W * fy / mcr)
    curve = ltb_curve_for(sec)
    chi = chi_LT(lam, curve)
    return {"Mcr_Nmm": mcr, "lambda_LT": round(lam, 4), "curve": curve,
            "chi_LT": round(chi, 4), "W_used_mm3": W,
            "Mb_Rd_Nmm": chi * W * fy / gamma_M1,
            "Mpl_Rd_Nmm": W * fy / gamma_M1,
            "ltb_governs": chi < 0.999, "classification": c}


def check_beam(designation, grade="S275", span_mm=6000.0, udl_N_per_mm=None,
               point_load_N=None, restraint_spacing_mm=None, C1=1.0):
    """Full simply-supported beam check: classification, bending, shear, LTB.

    Loading is either a UDL or a mid-span point load. `restraint_spacing_mm` is the
    unrestrained length for LTB - it defaults to the full span, which is the
    conservative assumption when nothing restrains the compression flange.
    """
    try:
        sec = resolve_section(designation)
    except (KeyError, ValueError) as e:
        return {"error": str(e)}
    if grade not in STEEL_GRADES:
        return {"error": f"unknown grade '{grade}'. Known: {', '.join(STEEL_GRADES)}"}
    fy = STEEL_GRADES[grade]
    L = span_mm
    Lcr = restraint_spacing_mm if restraint_spacing_mm else span_mm

    if udl_N_per_mm is not None:
        w = float(udl_N_per_mm)
        M_Ed = w * L ** 2 / 8.0
        V_Ed = w * L / 2.0
        load_desc = f"UDL {w:g} N/mm"
        # 5wL^4/384EI
        defl = 5.0 * w * L ** 4 / (384.0 * E_STEEL * sec["I_mm4"])
    elif point_load_N is not None:
        P = float(point_load_N)
        M_Ed = P * L / 4.0
        V_Ed = P / 2.0
        load_desc = f"point load {P:g} N at mid-span"
        defl = P * L ** 3 / (48.0 * E_STEEL * sec["I_mm4"])
    else:
        return {"error": "give either udl_N_per_mm or point_load_N"}

    mres = moment_resistance(sec, fy)
    if "error" in mres:
        return mres
    vres = shear_resistance(sec, fy)
    bres = buckling_resistance(sec, fy, Lcr, C1)
    inter = shear_moment_interaction(V_Ed, vres["Vpl_Rd_N"])

    M_cap = min(mres["Mc_Rd_Nmm"], bres["Mb_Rd_Nmm"])
    governing = "lateral-torsional buckling" if bres["Mb_Rd_Nmm"] < mres["Mc_Rd_Nmm"] \
                else "cross-section bending"

    util_M = M_Ed / M_cap if M_cap else float("inf")
    util_V = V_Ed / vres["Vpl_Rd_N"] if vres["Vpl_Rd_N"] else float("inf")
    defl_limit = L / 250.0

    checks = [
        {"name": "bending", "passed": util_M <= 1.0,
         "detail": f"M_Ed={M_Ed/1e6:.1f} kNm vs {M_cap/1e6:.1f} kNm ({governing}), "
                   f"utilisation {util_M:.2f}"},
        {"name": "shear", "passed": util_V <= 1.0,
         "detail": f"V_Ed={V_Ed/1e3:.1f} kN vs Vpl_Rd={vres['Vpl_Rd_N']/1e3:.1f} kN, "
                   f"utilisation {util_V:.2f}"},
        {"name": "shear does not reduce bending capacity", "passed": not inter["reduces_moment"],
         "detail": f"V/Vpl={inter['V_over_Vpl']:.2f}"
                   + (f" > 0.5, rho={inter['rho']:.3f}" if inter["reduces_moment"] else " <= 0.5")},
        {"name": "deflection within span/250", "passed": defl <= defl_limit,
         "detail": f"{defl:.1f} mm vs {defl_limit:.1f} mm"},
        {"name": f"section is class 1-3 ({grade})", "passed": mres["classification"]["class"] <= 3,
         "detail": f"class {mres['classification']['class']}, {mres['basis']}"},
    ]
    return {
        "section": designation, "grade": grade, "fy_MPa": fy,
        "loading": load_desc, "span_mm": L, "unrestrained_length_mm": Lcr,
        "M_Ed_kNm": round(M_Ed / 1e6, 2), "V_Ed_kN": round(V_Ed / 1e3, 2),
        "Mc_Rd_kNm": round(mres["Mc_Rd_Nmm"] / 1e6, 2),
        "Mb_Rd_kNm": round(bres["Mb_Rd_Nmm"] / 1e6, 2),
        "Vpl_Rd_kN": round(vres["Vpl_Rd_N"] / 1e3, 2),
        "lambda_LT": bres["lambda_LT"], "chi_LT": bres["chi_LT"], "ltb_curve": bres["curve"],
        "governing_bending_mode": governing,
        "deflection_mm": round(defl, 2), "deflection_limit_mm": round(defl_limit, 2),
        "class": mres["classification"]["class"],
        "utilisation": {"bending": round(util_M, 3), "shear": round(util_V, 3)},
        "checks": checks, "adequate": all(c["passed"] for c in checks),
        "code": "EN 1993-1-1 (gamma_M0 = gamma_M1 = 1.00)",
        "disclaimer": "Checked design aid, not a certified result. Web buckling, bearing, "
                      "fatigue, connections and the National Annex are out of scope.",
    }


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    import standards

    # ---- material constants are derived, not typed ----
    chk("G == E/(2(1+nu)) for steel", abs(G_STEEL - 80769.23) < 1.0, f"{G_STEEL:.1f} MPa")
    chk("epsilon(S235) == 1 exactly", abs(epsilon(235.0) - 1.0) < 1e-12)
    chk("epsilon(S355) == sqrt(235/355)", abs(epsilon(355.0) - math.sqrt(235 / 355)) < 1e-12,
        f"{epsilon(355.0):.4f}")

    # ---- chi_LT: the plateau is an EXACT analytic value, for every curve ----
    exact = all(abs(chi_LT(0.2, c) - 1.0) < 1e-12 for c in ALPHA_LT)
    chk("chi_LT(lambda=0.2) == 1.0 exactly, all four curves", exact,
        "pins the 0.2 plateau and the leading 0.5")
    chk("chi_LT never exceeds 1.0",
        all(chi_LT(l, c) <= 1.0 + 1e-12 for c in ALPHA_LT for l in [0.0, 0.1, 0.2, 0.5, 1, 2, 5]))
    chk("chi_LT decreases as slenderness grows",
        all(chi_LT(a, "b") > chi_LT(b, "b") for a, b in zip([0.3, 0.6, 1.0, 1.5], [0.6, 1.0, 1.5, 2.5])))
    chk("a worse buckling curve gives a lower chi at the same slenderness",
        chi_LT(1.0, "a") > chi_LT(1.0, "b") > chi_LT(1.0, "c") > chi_LT(1.0, "d"),
        f"a={chi_LT(1.0,'a'):.3f} b={chi_LT(1.0,'b'):.3f} c={chi_LT(1.0,'c'):.3f} d={chi_LT(1.0,'d'):.3f}")
    chk("chi_LT -> 0 as lambda -> infinity (no capacity when infinitely slender)",
        chi_LT(20.0, "b") < 0.01, f"{chi_LT(20.0,'b'):.5f}")

    # ---- Mcr: each limiting case must reproduce its classical closed form ----
    s = standards.section("IPE 300")
    L = 5000.0
    Iz, Iw, It = s["Iz_mm4"], s["Iw_mm6"], s["It_mm4"]

    warp_only = dict(s); warp_only["It_mm4"] = 0.0
    got = Mcr(warp_only, L)
    want = math.pi ** 2 * E_STEEL / L ** 2 * math.sqrt(Iz * Iw)          # pure warping
    chk("Mcr with It=0 reduces to (pi^2 E/L^2) sqrt(Iz Iw)", abs(got - want) / want < 1e-12,
        f"{got/1e6:.1f} kNm")

    tors_only = dict(s); tors_only["Iw_mm6"] = 0.0
    got = Mcr(tors_only, L)
    want = math.pi / L * math.sqrt(E_STEEL * Iz * G_STEEL * It)          # pure St Venant
    chk("Mcr with Iw=0 reduces to (pi/L) sqrt(E Iz G It)", abs(got - want) / want < 1e-12,
        f"{got/1e6:.1f} kNm")

    chk("Mcr with both terms exceeds either alone (they add under the root)",
        Mcr(s, L) > max(Mcr(warp_only, L), Mcr(tors_only, L)))
    chk("Mcr falls as the unrestrained length grows",
        Mcr(s, 2000) > Mcr(s, 4000) > Mcr(s, 8000) > Mcr(s, 16000),
        f"{Mcr(s,2000)/1e6:.0f} -> {Mcr(s,16000)/1e6:.0f} kNm")
    chk("C1 scales Mcr linearly (moment-shape factor)",
        abs(Mcr(s, L, C1=1.77) - 1.77 * Mcr(s, L)) / Mcr(s, L) < 1e-12)

    # ---- shear ----
    v = shear_resistance(s, 275.0)
    chk("shear yield is fy/sqrt(3) (von Mises)", abs(v["tau_y_MPa"] - 275.0 / math.sqrt(3)) < 0.01,
        f"{v['tau_y_MPa']} MPa")
    frac = v["Av_mm2"] / s["A_mm2"]
    chk("shear area is a sane fraction of gross area for an I-section", 0.25 < frac < 0.70,
        f"Av/A = {frac:.3f}")
    chk("shear resistance scales with yield",
        abs(shear_resistance(s, 355.0)["Vpl_Rd_N"] / v["Vpl_Rd_N"] - 355.0 / 275.0) < 1e-9)

    # interaction must be continuous at the 0.5 threshold and complete at 1.0
    chk("no moment reduction at exactly V = 0.5 Vpl (continuous)",
        shear_moment_interaction(50.0, 100.0)["rho"] == 0.0)
    chk("rho -> 1 as V -> Vpl (web fully used in shear)",
        abs(shear_moment_interaction(100.0, 100.0)["rho"] - 1.0) < 1e-12)
    chk("rho rises smoothly between the two",
        0 < shear_moment_interaction(75.0, 100.0)["rho"] < 1)

    # ---- classification ----
    c = classify(s, 235.0)
    chk("IPE 300 in S235 is class 1 (compact, plastic design allowed)", c["class"] == 1,
        f"web c/t={c['web_c_over_t']}, flange c/t={c['flange_c_over_t']}")
    chk("the same section in a higher grade is no better classified",
        classify(s, 460.0)["class"] >= c["class"],
        f"S235 class {c['class']} -> S460 class {classify(s,460.0)['class']}")
    chk("class 1-2 uses the plastic modulus, class 3 the elastic",
        moment_resistance(s, 235.0)["W_used_mm3"] == s["Wpl_mm3"])

    # ---- the physics that motivates the whole module ----
    fy = 275.0
    # NB 1.5 m is NOT short enough to reach the plateau for an IPE 300 - chi is still
    # ~0.94 there. That is the correct physics, and worth stating: "it's braced" is not
    # the same as "LTB does not apply". Closely restrained means a few hundred mm.
    short = buckling_resistance(s, fy, 500.0)
    mid = buckling_resistance(s, fy, 1500.0)
    long_ = buckling_resistance(s, fy, 12000.0)
    chk("a closely restrained beam reaches its full plastic moment (no LTB penalty)",
        short["chi_LT"] > 0.99, f"chi_LT={short['chi_LT']} at 0.5 m")
    chk("even at 1.5 m there is still a real LTB penalty (bracing is not binary)",
        0.85 < mid["chi_LT"] < 0.99, f"chi_LT={mid['chi_LT']} at 1.5 m")
    chk("an unrestrained long beam is severely reduced by LTB",
        long_["chi_LT"] < 0.5, f"chi_LT={long_['chi_LT']} at 12 m")
    chk("LTB reduction is the whole point: capacity drops with unrestrained length",
        long_["Mb_Rd_Nmm"] < short["Mb_Rd_Nmm"],
        f"{short['Mb_Rd_Nmm']/1e6:.0f} -> {long_['Mb_Rd_Nmm']/1e6:.0f} kNm")
    chk("Mb_Rd never exceeds Mpl_Rd", long_["Mb_Rd_Nmm"] <= long_["Mpl_Rd_Nmm"])

    # ---- end-to-end: the case bending-only design gets WRONG ----
    # 12 m unrestrained IPE 300 that a stress-only check would happily pass
    r = check_beam("IPE 300", "S275", span_mm=12000, udl_N_per_mm=6.0)
    chk("12 m unrestrained IPE 300: LTB governs, not cross-section bending",
        r["governing_bending_mode"] == "lateral-torsional buckling",
        f"Mb_Rd={r['Mb_Rd_kNm']} kNm vs Mc_Rd={r['Mc_Rd_kNm']} kNm")
    chk("...and that reduction is large enough to change the answer",
        r["Mb_Rd_kNm"] < 0.6 * r["Mc_Rd_kNm"],
        f"{r['Mb_Rd_kNm']/r['Mc_Rd_kNm']*100:.0f}% of the in-plane capacity")
    chk("restraining the compression flange recovers capacity",
        check_beam("IPE 300", "S275", span_mm=12000, udl_N_per_mm=6.0,
                   restraint_spacing_mm=2000)["Mb_Rd_kNm"] > r["Mb_Rd_kNm"],
        f"{check_beam('IPE 300','S275',span_mm=12000,udl_N_per_mm=6.0,restraint_spacing_mm=2000)['Mb_Rd_kNm']}"
        f" kNm at 2 m restraint spacing")

    # A short deep beam inverts the usual intuition: the web yields in shear while the
    # bending utilisation is still modest. A stress-only check would call this fine.
    deep = check_beam("IPE 600", "S275", span_mm=800, point_load_N=900000.0)
    chk("short deep beam is shear-critical, not bending-critical",
        deep["utilisation"]["shear"] > deep["utilisation"]["bending"],
        f"shear {deep['utilisation']['shear']:.2f} vs bending {deep['utilisation']['bending']:.2f}")

    # ---- hollow sections: the engineering answer when LTB kills an I-beam ----
    box = standards.shs("SHS 200x200x10")
    b_short = buckling_resistance(box, fy, 1000.0)
    b_long = buckling_resistance(box, fy, 12000.0)
    # "Immune" is a practical claim, not a mathematical one: chi_LT is still slightly
    # below 1, but a ~2% penalty is a different world from the I-section's ~80%.
    chk("a closed section is practically immune to LTB even at 12 m",
        b_long["chi_LT"] > 0.95 and b_long["chi_LT"] > 4 * long_["chi_LT"],
        f"chi_LT={b_long['chi_LT']} vs {long_['chi_LT']} for IPE 300 at the same length")
    chk("...so unlike the I-section, its capacity barely changes with length",
        abs(b_long["Mb_Rd_Nmm"] - b_short["Mb_Rd_Nmm"]) / b_short["Mb_Rd_Nmm"] < 0.05,
        f"{b_short['Mb_Rd_Nmm']/1e6:.0f} -> {b_long['Mb_Rd_Nmm']/1e6:.0f} kNm "
        f"({abs(b_long['Mb_Rd_Nmm']-b_short['Mb_Rd_Nmm'])/b_short['Mb_Rd_Nmm']*100:.1f}% loss, "
        f"against 80% for the IPE)")
    # Swapping to a box fixes the BUCKLING problem - bending utilisation drops from
    # over 1 to 0.75 - but the 12 m span then fails on deflection instead. Worth
    # asserting both halves: solving one limit state exposes the next, and a tool that
    # only reported "LTB fixed" would be quietly misleading.
    ripe = check_beam("IPE 300", "S275", span_mm=12000, udl_N_per_mm=6.0)
    rb = check_beam("SHS 200x200x10", "S275", span_mm=12000, udl_N_per_mm=6.0)
    chk("the 12 m span that FAILED on LTB as an IPE 300 passes bending as an SHS",
        ripe["utilisation"]["bending"] > 1.0 and rb["utilisation"]["bending"] < 1.0,
        f"IPE {ripe['utilisation']['bending']:.2f} -> SHS {rb['utilisation']['bending']:.2f}")
    defl_chk = next(c for c in rb["checks"] if c["name"].startswith("deflection"))
    chk("...but the tool then honestly reports deflection as the new binding limit",
        (not rb["adequate"]) and not defl_chk["passed"],
        defl_chk["detail"])
    chk("hollow section uses the INTERNAL-part flange limits, not outstand",
        classify(box, 275.0)["class"] <= 2, f"class {classify(box,275.0)['class']}")

    ch = check_beam("CHS 219.1x10", "S275", span_mm=8000, udl_N_per_mm=4.0)
    chk("a circular hollow section resolves and checks end to end",
        "error" not in ch and ch["chi_LT"] > 0.99,
        f"CHS 219.1x10: chi_LT={ch['chi_LT']}, bending utilisation {ch['utilisation']['bending']:.2f}")
    chk("an unknown section is refused with a useful message, not a crash",
        "error" in check_beam("IPE 999", "S275", span_mm=1000, udl_N_per_mm=1.0))

    good = check_beam("IPE 400", "S275", span_mm=6000, udl_N_per_mm=10.0,
                      restraint_spacing_mm=1500)
    chk("a sensibly designed beam passes every check", good["adequate"],
        f"bending {good['utilisation']['bending']:.2f}, shear {good['utilisation']['shear']:.2f}, "
        f"sag {good['deflection_mm']} mm")
    chk("every result carries the code reference and a disclaimer",
        "EN 1993-1-1" in good["code"] and "not a certified" in good["disclaimer"])

    # ---- units survive ----
    try:
        from units_gate import has_dimension, HAVE_PINT
        if HAVE_PINT:
            chk("moment has N.m dimensions", has_dimension(good["Mc_Rd_kNm"], "kN*m", "N*m"))
            chk("shear resistance has force dimensions", has_dimension(good["Vpl_Rd_kN"], "kN", "N"))
    except Exception:
        pass

    print("steel_design selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EN 1993-1-1 steel member checks")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check", metavar="SECTION", help="e.g. 'IPE 300'")
    ap.add_argument("--grade", default="S275")
    ap.add_argument("--span", type=float, default=6000.0)
    ap.add_argument("--udl", type=float, help="N/mm")
    ap.add_argument("--point", type=float, help="N at mid-span")
    ap.add_argument("--restraint", type=float, help="unrestrained length mm (default: full span)")
    a = ap.parse_args()
    if a.check:
        print(json.dumps(check_beam(a.check, a.grade, a.span, a.udl, a.point, a.restraint), indent=2))
        raise SystemExit(0)
    raise SystemExit(_selftest())
