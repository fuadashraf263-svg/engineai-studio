#!/usr/bin/env python3
"""
mechanics.py - mechanics of materials beyond simple bending.

`structural.py` covers four beam cases in bending and nothing else. A real member
is also twisted, pressurised, notched and heated, and each of those has a failure
mode bending cannot see:

  * TORSION. A shaft carries torque, and tau = Tr/J has nothing to do with bending
    stress. Sizing a shaft on bending alone is how shafts fail.
  * PRESSURE. In a thin cylinder the hoop stress is TWICE the longitudinal - which
    is why pressure vessels split along their length, never around their belly.
  * STRESS CONCENTRATION. A hole in a plate triples the local stress. A design at a
    nominal 100 MPa against a 250 MPa yield looks safe at 2.5x and is actually at
    0.83x. This single factor of 3 is the most commonly forgotten number in
    mechanical design.
  * THERMAL STRESS. A fully restrained bar heated 80 K develops E*alpha*dT with no
    external load at all - about 190 MPa in steel, most of the way to yield in mild
    steel, from nothing but sunshine.

Verification approach: every formula here has at least one limiting case that must
reproduce an independently derived result. The three stress-concentration formulas
(Kirsch, Inglis, elliptical) are written separately and must ALL return exactly 3.0
for a circular hole; the off-centre beam formulas must collapse exactly onto the
centre-load case; thick-wall Lame must tend to thin-wall as t/r -> 0.

Units: N, mm, MPa, K throughout. Angles in radians unless the name says degrees.

A checked design aid, NOT a certified result. Fatigue, creep, plasticity and code
compliance (ASME VIII, EN 13445) are out of scope. A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math

# --------------------------------------------------------------------------- #
# Additional beam cases (the ones structural.py does not have)
# --------------------------------------------------------------------------- #
def fixed_fixed_point(P, L, E, I):
    """Both ends built in, central point load.

    delta = PL^3/192EI, M_end = M_mid = PL/8.

    Fixing both ends stiffens the beam by exactly 4x against the simply-supported
    case (192/48), and that ratio is the selftest's anchor - it is a pure number
    that no arithmetic slip can survive.
    """
    return {"defl": P * L ** 3 / (192.0 * E * I),
            "M_end": P * L / 8.0, "M_mid": P * L / 8.0,
            "M": P * L / 8.0, "R": P / 2.0,
            "case": "fixed-fixed, central point load"}


def fixed_fixed_udl(w, L, E, I):
    """Both ends built in, UDL. delta = wL^4/384EI, M_end = wL^2/12, M_mid = wL^2/24.

    Note the hogging moment at the supports is TWICE the sagging moment at midspan,
    so a fixed-ended beam is critical at its ends, not its middle - the opposite of
    the simply-supported intuition.
    """
    return {"defl": w * L ** 4 / (384.0 * E * I),
            "M_end": w * L ** 2 / 12.0, "M_mid": w * L ** 2 / 24.0,
            "M": w * L ** 2 / 12.0, "R": w * L / 2.0,
            "case": "fixed-fixed, UDL"}


def propped_cantilever_udl(w, L, E, I):
    """One end built in, one end simply propped, UDL.

    M_fixed = wL^2/8, R_prop = 3wL/8, R_fixed = 5wL/8, delta_max = wL^4/185EI
    at x = 0.5785L. Statically indeterminate to first degree.
    """
    return {"defl": w * L ** 4 / (185.0 * E * I),
            "M_fixed": w * L ** 2 / 8.0,
            "M_span": 9.0 * w * L ** 2 / 128.0,
            "M": w * L ** 2 / 8.0,
            "R_prop": 3.0 * w * L / 8.0, "R_fixed": 5.0 * w * L / 8.0,
            "x_max_defl": 0.5785 * L,
            "case": "propped cantilever, UDL"}


def simply_point_offset(P, a, b, E, I):
    """Simply supported, point load at distance a from the left support, b from the
    right (L = a + b).

        R1 = Pb/L,  R2 = Pa/L,  M_max = Pab/L  (under the load)
        delta at the load = P a^2 b^2 / (3 L E I)
        delta_max         = P b (L^2 - b^2)^1.5 / (9 sqrt(3) L E I)   for a >= b

    Both deflection expressions collapse EXACTLY onto PL^3/48EI when a = b = L/2,
    which the selftest asserts - two independent routes to the same known answer.
    """
    L = a + b
    if a <= 0 or b <= 0:
        raise ValueError("a and b must both be positive (load between the supports)")
    R1, R2 = P * b / L, P * a / L
    d_load = P * a ** 2 * b ** 2 / (3.0 * L * E * I)
    far = max(a, b)
    d_max = P * min(a, b) * (L ** 2 - min(a, b) ** 2) ** 1.5 / (9.0 * math.sqrt(3.0) * L * E * I)
    x_max = math.sqrt((L ** 2 - min(a, b) ** 2) / 3.0)
    return {"L": L, "R1": R1, "R2": R2, "M": P * a * b / L, "M_max": P * a * b / L,
            "defl_at_load": d_load, "defl": d_max, "x_max_defl": x_max,
            "static_check_sum_F": R1 + R2 - P,
            "static_check_sum_M_about_R1": R2 * L - P * a,
            "case": f"simply supported, point load at a={a:g} of L={L:g}"}


def overhang_point(P, L, a, E, I):
    """Two supports distance L apart, with an overhang of length a beyond the right
    support carrying a point load P at its tip.

        R2 = P(L+a)/L   (right support, upward)
        R1 = -Pa/L      (left support - NEGATIVE, i.e. it must hold the beam DOWN)
        M at the right support = -Pa (hogging)
        delta at the tip = P a^2 (L + a) / (3 E I)

    The uplift at R1 is the engineering point: an overhang tries to lever the far
    support out of its seat, and a detail that can only push will simply let go.
    """
    if L <= 0 or a <= 0:
        raise ValueError("span L and overhang a must both be positive")
    R2 = P * (L + a) / L
    R1 = -P * a / L
    return {"R1": R1, "R2": R2, "uplift_at_R1": R1 < 0,
            "M_at_support": -P * a, "M": P * a, "M_max_magnitude": P * a,
            "defl_tip": P * a ** 2 * (L + a) / (3.0 * E * I),
            "defl": P * a ** 2 * (L + a) / (3.0 * E * I),
            "static_check_sum_F": R1 + R2 - P,
            "static_check_sum_M_about_R1": R2 * L - P * (L + a),
            "case": f"overhanging beam, span {L:g}, overhang {a:g}"}


# --------------------------------------------------------------------------- #
# Torsion
# --------------------------------------------------------------------------- #
def polar_J(d_outer, d_inner=0.0):
    """Polar second moment of area of a circular shaft, J = pi(do^4 - di^4)/32.

    For any circular section J = 2I exactly, by the perpendicular axis theorem -
    the strongest available check that this constant is right.
    """
    if d_outer <= 0:
        raise ValueError("outer diameter must be positive")
    if not 0 <= d_inner < d_outer:
        raise ValueError("inner diameter must be >= 0 and < outer diameter")
    return math.pi * (d_outer ** 4 - d_inner ** 4) / 32.0


def shaft_torsion(T, d_outer, L, G, d_inner=0.0):
    """Circular shaft in pure torsion.

        tau_max = T r_o / J       (at the OUTER surface - torsional shear is zero
                                   on the axis and maximum at the skin, which is
                                   why hollowing a shaft costs so little strength)
        theta   = T L / (G J)     radians
    """
    J = polar_J(d_outer, d_inner)
    r_o = d_outer / 2.0
    tau = T * r_o / J
    theta = T * L / (G * J)
    A = math.pi * (d_outer ** 2 - d_inner ** 2) / 4.0
    return {"J_mm4": J, "tau_max_MPa": tau, "tau_inner_MPa": T * (d_inner / 2.0) / J,
            "theta_rad": theta, "theta_deg": math.degrees(theta),
            "twist_per_metre_deg": math.degrees(theta) / (L / 1000.0) if L else 0.0,
            "torsional_stiffness_Nmm_per_rad": G * J / L if L else float("inf"),
            "area_mm2": A, "d_outer_mm": d_outer, "d_inner_mm": d_inner,
            "note": "shear is zero on the axis and maximum at the surface"}


def shaft_compare_hollow(T, d_outer, L, G, d_inner):
    """Solid vs hollow shaft of the same OUTER diameter: how much stress is added
    and how much mass is saved. Removing the core removes material that was barely
    working, so a large mass saving costs a small stress penalty."""
    solid = shaft_torsion(T, d_outer, L, G, 0.0)
    hollow = shaft_torsion(T, d_outer, L, G, d_inner)
    return {"solid": solid, "hollow": hollow,
            "stress_increase_percent": 100.0 * (hollow["tau_max_MPa"] / solid["tau_max_MPa"] - 1.0),
            "mass_saving_percent": 100.0 * (1.0 - hollow["area_mm2"] / solid["area_mm2"]),
            "stiffness_loss_percent": 100.0 * (1.0 - hollow["J_mm4"] / solid["J_mm4"])}


def combined_bending_torsion(M, T, d, sigma_y=None):
    """A shaft carrying bending AND torque at once - the normal case, and the one a
    bending-only check gets dangerously wrong.

        sigma_b = 32M/(pi d^3),  tau = 16T/(pi d^3)
        von Mises: sigma_vm = sqrt(sigma_b^2 + 3 tau^2)
        maximum shear stress theory: tau_max = sqrt((sigma_b/2)^2 + tau^2)

    Equivalent moments (the classical shaft-design shortcut):
        M_e = 0.5(M + sqrt(M^2 + T^2)),  T_e = sqrt(M^2 + T^2)
    """
    Z = math.pi * d ** 3 / 32.0
    sigma_b = M / Z
    tau = T / (2.0 * Z)
    vm = math.sqrt(sigma_b ** 2 + 3.0 * tau ** 2)
    tresca = 2.0 * math.sqrt((sigma_b / 2.0) ** 2 + tau ** 2)
    out = {"sigma_bending_MPa": sigma_b, "tau_torsion_MPa": tau,
           "sigma_von_mises_MPa": vm, "sigma_tresca_MPa": tresca,
           "M_equivalent_Nmm": 0.5 * (M + math.sqrt(M ** 2 + T ** 2)),
           "T_equivalent_Nmm": math.sqrt(M ** 2 + T ** 2),
           "note": "Tresca is the more conservative of the two criteria"}
    if sigma_y:
        out["fos_von_mises"] = sigma_y / vm
        out["fos_tresca"] = sigma_y / tresca
        out["fos_bending_only_MISLEADING"] = sigma_y / sigma_b if sigma_b else float("inf")
    return out


# --------------------------------------------------------------------------- #
# Pressure vessels
# --------------------------------------------------------------------------- #
def thin_cylinder(p, r, t, joint_efficiency=1.0):
    """Thin-walled cylinder under internal pressure.

        hoop (circumferential) sigma_h = p r / t
        longitudinal (axial)   sigma_l = p r / 2t

    The hoop stress is exactly TWICE the longitudinal, always, independent of every
    input. That is why a pressurised tube splits along a longitudinal seam and why
    a sausage bursts lengthwise. The selftest asserts the ratio is exactly 2.

    Thin-wall theory is conventionally valid for t/r <= 0.1; beyond that use
    thick_cylinder(). This function says so rather than silently over-reaching.
    """
    if t <= 0 or r <= 0:
        raise ValueError("radius and thickness must be positive")
    hoop = p * r / (t * joint_efficiency)
    return {"hoop_MPa": hoop, "longitudinal_MPa": hoop / 2.0,
            "radial_MPa": -p, "ratio_hoop_to_long": 2.0,
            "von_mises_MPa": math.sqrt(hoop ** 2 - hoop * (hoop / 2.0) + (hoop / 2.0) ** 2),
            "t_over_r": t / r, "thin_wall_valid": t / r <= 0.1,
            "validity": ("thin-wall theory applies (t/r <= 0.1)" if t / r <= 0.1
                         else f"t/r = {t/r:.3f} EXCEEDS 0.1 - use thick_cylinder()"),
            "joint_efficiency": joint_efficiency}


def thin_sphere(p, r, t, joint_efficiency=1.0):
    """Thin-walled sphere: sigma = p r / 2t in EVERY direction.

    A sphere carries the same pressure at half the stress of a cylinder of the same
    radius and thickness, which is why pressure vessel heads are domed."""
    if t <= 0 or r <= 0:
        raise ValueError("radius and thickness must be positive")
    s = p * r / (2.0 * t * joint_efficiency)
    return {"membrane_MPa": s, "hoop_MPa": s, "longitudinal_MPa": s,
            "t_over_r": t / r, "thin_wall_valid": t / r <= 0.1,
            "note": "a sphere is half the stress of a cylinder at the same p, r, t"}


def thick_cylinder(p_i, r_i, r_o, p_o=0.0, r=None):
    """Lame thick-walled cylinder. At radius r (default: the inner surface, where
    the hoop stress is greatest):

        sigma_h = (p_i r_i^2 - p_o r_o^2)/(r_o^2 - r_i^2)
                  + r_i^2 r_o^2 (p_i - p_o) / (r^2 (r_o^2 - r_i^2))
        sigma_r = (p_i r_i^2 - p_o r_o^2)/(r_o^2 - r_i^2)
                  - r_i^2 r_o^2 (p_i - p_o) / (r^2 (r_o^2 - r_i^2))

    Exact for any wall thickness. As the wall thins it must tend to p r / t, and the
    selftest checks that convergence rather than assuming it.
    """
    if not 0 < r_i < r_o:
        raise ValueError("need 0 < r_i < r_o")
    r = r_i if r is None else r
    if not r_i <= r <= r_o:
        raise ValueError("evaluation radius must lie in the wall")
    A = (p_i * r_i ** 2 - p_o * r_o ** 2) / (r_o ** 2 - r_i ** 2)
    B = r_i ** 2 * r_o ** 2 * (p_i - p_o) / (r_o ** 2 - r_i ** 2)
    sh = A + B / r ** 2
    sr = A - B / r ** 2
    sl = A                                     # closed ends, axial membrane
    return {"radius_mm": r, "hoop_MPa": sh, "radial_MPa": sr, "longitudinal_MPa": sl,
            "von_mises_MPa": math.sqrt(0.5 * ((sh - sr) ** 2 + (sr - sl) ** 2 + (sl - sh) ** 2)),
            "max_shear_MPa": (sh - sr) / 2.0,
            "t_over_r": (r_o - r_i) / r_i}


# --------------------------------------------------------------------------- #
# Stress concentration
# --------------------------------------------------------------------------- #
def kt_circular_hole(d_over_W=0.0):
    """Circular hole in a plate under uniaxial tension.

    Infinite plate (Kirsch, 1898): Kt = 3 exactly.
    Finite width (Howland): Kt = 3 - 3.14(d/W) + 3.667(d/W)^2 - 1.527(d/W)^3,
    where the nominal stress is taken on the NET section.

    Setting d/W = 0 must return exactly 3.000, which pins the polynomial's constant
    term against the closed-form elasticity solution.
    """
    if not 0.0 <= d_over_W < 1.0:
        raise ValueError("d/W must be in [0, 1)")
    x = d_over_W
    return 3.0 - 3.14 * x + 3.667 * x ** 2 - 1.527 * x ** 3


def kt_elliptical_hole(a, b):
    """Elliptical hole, major semi-axis a perpendicular to the load:

        Kt = 1 + 2a/b

    A circle is the case a = b, which gives exactly 3 - the same number Kirsch
    gives, derived a completely different way. That agreement is the check.
    As b -> 0 the ellipse becomes a crack and Kt -> infinity, which is precisely
    why fracture mechanics exists and why Kt stops being the right tool.
    """
    if a <= 0 or b <= 0:
        raise ValueError("both semi-axes must be positive")
    return 1.0 + 2.0 * a / b


def kt_notch_inglis(a, rho):
    """Inglis: a notch of depth a with root radius rho gives Kt = 1 + 2*sqrt(a/rho).

    A semicircular notch has a = rho, giving exactly 3 - the third independent route
    to the same number. Sharpening the root (rho -> 0) drives Kt up without limit.
    """
    if a <= 0 or rho <= 0:
        raise ValueError("notch depth and root radius must be positive")
    return 1.0 + 2.0 * math.sqrt(a / rho)


def stress_concentration(sigma_nominal, kt, sigma_y=None):
    """Apply a concentration factor and report what it does to the safety margin.
    The point of returning both factors of safety is that the difference between
    them is exactly the mistake this function exists to prevent."""
    peak = sigma_nominal * kt
    out = {"sigma_nominal_MPa": sigma_nominal, "Kt": kt, "sigma_peak_MPa": peak}
    if sigma_y:
        out["fos_at_peak"] = sigma_y / peak
        out["fos_if_Kt_IGNORED"] = sigma_y / sigma_nominal
        out["yields_locally"] = peak > sigma_y
        out["warning"] = (f"local yielding: {peak:.1f} MPa exceeds {sigma_y:.1f} MPa "
                          f"despite a nominal FoS of {sigma_y/sigma_nominal:.2f}"
                          if peak > sigma_y else "no local yielding")
    return out


# --------------------------------------------------------------------------- #
# Thermal stress
# --------------------------------------------------------------------------- #
def thermal_expansion(alpha, L, dT):
    """Free expansion dL = alpha L dT. Strain is alpha*dT and is independent of L."""
    return {"dL": alpha * L * dT, "strain": alpha * dT,
            "L_final": L + alpha * L * dT}


def thermal_stress(E, alpha, dT, restraint=1.0, sigma_y=None):
    """Stress in a bar prevented from expanding.

        sigma = -restraint * E * alpha * dT

    Fully restrained (restraint = 1) and heated, the bar goes into COMPRESSION -
    the sign matters, because a heated restrained member buckles and a cooled one
    cracks in tension. The magnitude is independent of length and cross-section,
    which surprises people: a 1 mm bar and a 100 m bar reach the same stress.
    """
    if not 0.0 <= restraint <= 1.0:
        raise ValueError("restraint must be between 0 (free) and 1 (fully fixed)")
    sigma = -restraint * E * alpha * dT
    out = {"sigma_MPa": sigma, "magnitude_MPa": abs(sigma),
           "sense": "compression" if sigma < 0 else "tension" if sigma > 0 else "none",
           "restraint": restraint,
           "note": "independent of length and cross-section"}
    if sigma_y:
        out["fos"] = sigma_y / abs(sigma) if sigma else float("inf")
        out["yields"] = abs(sigma) > sigma_y
    return out


def thermal_stress_two_bar(E1, a1, E2, a2, A1, A2, dT):
    """Two bars of different materials, same length, rigidly joined at both ends and
    heated together. Each restrains the other, so both develop stress even though
    nothing external holds them.

        compatibility: a1 dT + s1/E1 = a2 dT + s2/E2
        equilibrium:   s1 A1 + s2 A2 = 0
    """
    denom = A1 / (E1 * A1) + A2 / (E2 * A2) if (A1 and A2) else 0.0
    # solve: s1 = (a2-a1) dT / (1/E1 + A1/(A2 E2))
    s1 = (a2 - a1) * dT / (1.0 / E1 + A1 / (A2 * E2))
    s2 = -s1 * A1 / A2
    return {"sigma_1_MPa": s1, "sigma_2_MPa": s2,
            "equilibrium_residual_N": s1 * A1 + s2 * A2,
            "note": "the bar with the LARGER alpha ends in compression"}


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

    import structural
    E, G = 200000.0, 79000.0
    sec = structural.rectangle(30, 50)
    I = sec["I"]

    # ---- extra beam cases: each must relate EXACTLY to a known case ----
    P, L = 3000.0, 700.0
    ff = fixed_fixed_point(P, L, E, I)
    ss = structural.simply_point(P, L, E, I)
    chk("fixed-fixed point load: delta = PL^3/192EI",
        abs(ff["defl"] - P * L ** 3 / (192 * E * I)) < 1e-12, f"{ff['defl']:.5f} mm")
    chk("...which is EXACTLY 1/4 of the simply-supported deflection (192/48)",
        abs(ss["defl"] / ff["defl"] - 4.0) < 1e-12,
        f"{ss['defl']:.4f} / {ff['defl']:.4f} = {ss['defl']/ff['defl']:.6f}")
    chk("fixed-fixed end moment = PL/8, exactly half the simply-supported PL/4",
        abs(ff["M_end"] - P * L / 8) < 1e-9
        and abs(ss["M"] / ff["M_end"] - 2.0) < 1e-12)

    w = 8.0
    ffu = fixed_fixed_udl(w, 1000.0, E, I)
    ssu = structural.simply_udl(w, 1000.0, E, I)
    chk("fixed-fixed UDL is exactly 1/5 of the simply-supported deflection (384/76.8)",
        abs(ssu["defl"] / ffu["defl"] - 5.0) < 1e-12,
        f"ratio {ssu['defl']/ffu['defl']:.6f}")
    chk("fixed-fixed UDL: support moment is TWICE the midspan moment",
        abs(ffu["M_end"] / ffu["M_mid"] - 2.0) < 1e-12,
        f"wL^2/12 vs wL^2/24 - the beam is critical at its ENDS")
    chk("fixed-fixed UDL: the two moments sum to the free moment wL^2/8",
        abs(ffu["M_end"] + ffu["M_mid"] - w * 1000.0 ** 2 / 8) < 1e-6)

    pc = propped_cantilever_udl(w, 1000.0, E, I)
    chk("propped cantilever: reactions sum to the total load",
        abs(pc["R_prop"] + pc["R_fixed"] - w * 1000.0) < 1e-9)
    chk("propped cantilever sits BETWEEN simply-supported and fixed-fixed in stiffness",
        ffu["defl"] < pc["defl"] < ssu["defl"],
        f"{ffu['defl']:.4f} < {pc['defl']:.4f} < {ssu['defl']:.4f} mm")
    chk("propped cantilever fixed-end moment = wL^2/8",
        abs(pc["M_fixed"] - w * 1000.0 ** 2 / 8) < 1e-6)

    # off-centre load: BOTH deflection formulas must collapse onto the centre case
    half = L / 2.0
    off = simply_point_offset(P, half, half, E, I)
    chk("off-centre load at a=b=L/2: R1 = R2 = P/2",
        abs(off["R1"] - P / 2) < 1e-12 and abs(off["R2"] - P / 2) < 1e-12)
    chk("...M_max = Pab/L collapses to PL/4",
        abs(off["M_max"] - P * L / 4) < 1e-12)
    chk("...delta at the load collapses EXACTLY to PL^3/48EI",
        abs(off["defl_at_load"] - ss["defl"]) < 1e-12,
        f"{off['defl_at_load']:.9f} vs {ss['defl']:.9f}")
    chk("...and the delta_max formula, derived separately, agrees to the last digit",
        abs(off["defl"] - ss["defl"]) / ss["defl"] < 1e-12,
        "two independent expressions, one answer")
    quarter = simply_point_offset(P, L * 0.25, L * 0.75, E, I)
    chk("off-centre: R1 = Pb/L and R2 = Pa/L (nearer support takes more)",
        abs(quarter["R1"] - P * 0.75) < 1e-9 and abs(quarter["R2"] - P * 0.25) < 1e-9,
        f"load at L/4: R1={quarter['R1']:.0f} N, R2={quarter['R2']:.0f} N")
    chk("off-centre: statics closes (sum F = 0 and sum M = 0)",
        abs(quarter["static_check_sum_F"]) < 1e-9
        and abs(quarter["static_check_sum_M_about_R1"]) < 1e-9)
    chk("moving the load off centre REDUCES the maximum moment",
        quarter["M_max"] < off["M_max"],
        f"{quarter['M_max']:.0f} vs {off['M_max']:.0f} N.mm")
    chk("the peak deflection is not under the load once it is off centre",
        quarter["defl"] > quarter["defl_at_load"],
        f"max {quarter['defl']:.4f} mm at x={quarter['x_max_defl']:.1f} vs "
        f"{quarter['defl_at_load']:.4f} mm under the load")
    chk("a load outside the supports is refused",
        _raises(lambda: simply_point_offset(P, -10.0, 100.0, E, I), ValueError))

    # overhang: the uplift is the engineering point
    ov = overhang_point(2000.0, 1000.0, 400.0, E, I)
    chk("overhang: statics closes (sum F = 0 and sum M = 0)",
        abs(ov["static_check_sum_F"]) < 1e-9
        and abs(ov["static_check_sum_M_about_R1"]) < 1e-9)
    chk("overhang: the FAR support goes into UPLIFT, not compression",
        ov["uplift_at_R1"] and ov["R1"] < 0,
        f"R1 = {ov['R1']:.0f} N (must be held DOWN), R2 = {ov['R2']:.0f} N")
    chk("overhang: hogging moment at the support = -Pa",
        abs(ov["M_at_support"] + 2000.0 * 400.0) < 1e-9)
    chk("overhang tip deflection = Pa^2(L+a)/3EI",
        abs(ov["defl_tip"] - 2000.0 * 400.0 ** 2 * 1400.0 / (3 * E * I)) < 1e-9,
        f"{ov['defl_tip']:.3f} mm")

    # ---- torsion ----
    d = 40.0
    J = polar_J(d)
    chk("solid shaft J = pi d^4 / 32", abs(J - math.pi * d ** 4 / 32) < 1e-9,
        f"{J:.1f} mm^4")
    chk("J == 2I exactly for a circular section (perpendicular axis theorem)",
        abs(J - 2.0 * (math.pi * d ** 4 / 64)) < 1e-12)
    t = shaft_torsion(500e3, 40.0, 1000.0, G)     # 500 N.m = 500e3 N.mm
    chk("tau = Tr/J", abs(t["tau_max_MPa"] - 500e3 * 20.0 / J) < 1e-9,
        f"{t['tau_max_MPa']:.2f} MPa")
    chk("theta = TL/GJ", abs(t["theta_rad"] - 500e3 * 1000.0 / (G * J)) < 1e-15,
        f"{t['theta_deg']:.4f} deg over 1 m")
    chk("torsional shear is ZERO on the axis", t["tau_inner_MPa"] == 0.0)
    chk("twist is proportional to length",
        abs(shaft_torsion(500e3, 40., 2000., G)["theta_rad"] / t["theta_rad"] - 2.0) < 1e-12)
    chk("stress is independent of length",
        abs(shaft_torsion(500e3, 40., 2000., G)["tau_max_MPa"] - t["tau_max_MPa"]) < 1e-12)
    chk("doubling the diameter cuts stress by 8x (d^3) and twist by 16x (d^4)",
        abs(t["tau_max_MPa"] / shaft_torsion(500e3, 80., 1000., G)["tau_max_MPa"] - 8.0) < 1e-9
        and abs(t["theta_rad"] / shaft_torsion(500e3, 80., 1000., G)["theta_rad"] - 16.0) < 1e-9)

    # A moderate bore is nearly free: the core it removes was carrying almost no
    # torsional shear. Push the bore too far and the bargain reverses, so assert
    # BOTH ends of that story rather than only the flattering one.
    mid = shaft_compare_hollow(500e3, 40.0, 1000.0, G, 20.0)     # di/do = 0.50
    thin = shaft_compare_hollow(500e3, 40.0, 1000.0, G, 30.0)    # di/do = 0.75
    chk("a moderate bore (di/do = 0.5) saves far more mass than it costs in stress",
        mid["mass_saving_percent"] > 3.0 * mid["stress_increase_percent"],
        f"{mid['mass_saving_percent']:.1f}% lighter for only "
        f"{mid['stress_increase_percent']:.1f}% more stress")
    chk("...but the bargain has diminishing returns as the wall thins",
        (thin["mass_saving_percent"] / thin["stress_increase_percent"])
        < (mid["mass_saving_percent"] / mid["stress_increase_percent"]),
        f"di/do 0.50 -> {mid['mass_saving_percent']/mid['stress_increase_percent']:.1f}x, "
        f"0.75 -> {thin['mass_saving_percent']/thin['stress_increase_percent']:.1f}x")
    chk("hollowing always costs SOME stress and SOME stiffness",
        thin["stress_increase_percent"] > 0 and thin["stiffness_loss_percent"] > 0)
    chk("an inner diameter >= the outer is refused",
        _raises(lambda: polar_J(40.0, 40.0), ValueError))

    cbt = combined_bending_torsion(500e3, 500e3, 50.0, sigma_y=370.0)
    chk("combined bending+torsion: von Mises exceeds bending alone",
        cbt["sigma_von_mises_MPa"] > cbt["sigma_bending_MPa"],
        f"vm {cbt['sigma_von_mises_MPa']:.1f} vs bending-only "
        f"{cbt['sigma_bending_MPa']:.1f} MPa")
    chk("...so a bending-only FoS is MISLEADINGLY high",
        cbt["fos_bending_only_MISLEADING"] > cbt["fos_von_mises"],
        f"{cbt['fos_bending_only_MISLEADING']:.2f} claimed vs "
        f"{cbt['fos_von_mises']:.2f} real")
    chk("Tresca is more conservative than von Mises",
        cbt["sigma_tresca_MPa"] >= cbt["sigma_von_mises_MPa"])
    pure_t = combined_bending_torsion(0.0, 500e3, 50.0)
    chk("with M=0 the equivalent torque reduces to T itself",
        abs(pure_t["T_equivalent_Nmm"] - 500e3) < 1e-9)
    pure_m = combined_bending_torsion(500e3, 0.0, 50.0)
    chk("with T=0 the equivalent moment reduces to M itself",
        abs(pure_m["M_equivalent_Nmm"] - 500e3) < 1e-9)
    chk("...and von Mises then equals the plain bending stress",
        abs(pure_m["sigma_von_mises_MPa"] - pure_m["sigma_bending_MPa"]) < 1e-9)

    # ---- pressure vessels ----
    tc = thin_cylinder(2.0, 200.0, 5.0)
    chk("thin cylinder hoop = pr/t", abs(tc["hoop_MPa"] - 2.0 * 200.0 / 5.0) < 1e-12,
        f"{tc['hoop_MPa']:.1f} MPa")
    chk("thin cylinder longitudinal = pr/2t", abs(tc["longitudinal_MPa"] - 40.0) < 1e-12)
    chk("hoop is EXACTLY twice longitudinal, for every input",
        all(abs(thin_cylinder(p, r, tt)["hoop_MPa"]
                / thin_cylinder(p, r, tt)["longitudinal_MPa"] - 2.0) < 1e-12
            for p, r, tt in [(1, 100, 2), (5, 500, 10), (0.2, 50, 1)]),
        "this is why vessels split lengthwise")
    chk("a sphere is half the stress of a cylinder at the same p, r, t",
        abs(thin_sphere(2.0, 200.0, 5.0)["membrane_MPa"] / tc["hoop_MPa"] - 0.5) < 1e-12)
    fat = thin_cylinder(2.0, 100.0, 30.0)
    chk("a thick wall is FLAGGED, not silently computed with thin-wall theory",
        not fat["thin_wall_valid"] and "EXCEEDS" in fat["validity"],
        f"t/r = {fat['t_over_r']:.2f}")

    # Lame must converge on thin-wall as the wall thins - the real check
    worst = 0.0
    for t_w in (10.0, 2.0, 0.5, 0.1, 0.02):
        r_i = 200.0
        lame = thick_cylinder(2.0, r_i, r_i + t_w)
        thin = 2.0 * (r_i + t_w / 2.0) / t_w        # thin-wall at the MEAN radius
        worst = abs(lame["hoop_MPa"] - thin) / thin
    chk("Lame thick-wall converges on pr/t as t/r -> 0",
        worst < 0.001, f"at t/r = 1e-4 the two differ by {worst*100:.4f}%")
    lame = thick_cylinder(2.0, 100.0, 130.0)
    chk("Lame: radial stress at the bore equals -p exactly (boundary condition)",
        abs(lame["radial_MPa"] + 2.0) < 1e-12, f"{lame['radial_MPa']:.6f} MPa")
    outer = thick_cylinder(2.0, 100.0, 130.0, r=130.0)
    chk("Lame: radial stress at the free outer surface is zero",
        abs(outer["radial_MPa"]) < 1e-12)
    chk("Lame: hoop stress is greatest at the BORE, not the outside",
        lame["hoop_MPa"] > outer["hoop_MPa"],
        f"{lame['hoop_MPa']:.2f} at r_i vs {outer['hoop_MPa']:.2f} at r_o")
    chk("Lame: hoop - radial is constant x 1/r^2, so max shear is at the bore",
        lame["max_shear_MPa"] > outer["hoop_MPa"] / 2.0)

    # ---- stress concentration: three independent routes to Kt = 3 ----
    chk("Kirsch: circular hole in an INFINITE plate gives exactly Kt = 3",
        abs(kt_circular_hole(0.0) - 3.0) < 1e-12)
    chk("Elliptical formula 1 + 2a/b gives exactly 3 for a circle (a = b)",
        abs(kt_elliptical_hole(5.0, 5.0) - 3.0) < 1e-12)
    chk("Inglis 1 + 2 sqrt(a/rho) gives exactly 3 for a semicircular notch (a = rho)",
        abs(kt_notch_inglis(5.0, 5.0) - 3.0) < 1e-12)
    chk("...all three agree, and they are three DIFFERENT derivations",
        abs(kt_circular_hole(0.0) - kt_elliptical_hole(1, 1)) < 1e-12
        and abs(kt_elliptical_hole(1, 1) - kt_notch_inglis(1, 1)) < 1e-12)
    chk("a finite-width plate has a LOWER Kt on the net section than 3",
        kt_circular_hole(0.3) < 3.0, f"d/W=0.3 -> Kt={kt_circular_hole(0.3):.3f}")
    chk("an elongated hole across the load is worse than a round one",
        kt_elliptical_hole(10.0, 2.0) > 3.0, f"Kt={kt_elliptical_hole(10.,2.):.1f}")
    chk("a hole elongated ALONG the load is better than a round one",
        kt_elliptical_hole(2.0, 10.0) < 3.0, f"Kt={kt_elliptical_hole(2.,10.):.2f}")
    chk("sharpening a notch root drives Kt up without limit (-> fracture mechanics)",
        kt_notch_inglis(5.0, 0.01) > 40.0, f"rho=0.01: Kt={kt_notch_inglis(5.,0.01):.0f}")

    sc = stress_concentration(100.0, 3.0, sigma_y=250.0)
    chk("the classic trap: nominal FoS 2.5 becomes 0.83 once Kt=3 is applied",
        abs(sc["fos_if_Kt_IGNORED"] - 2.5) < 1e-12 and sc["fos_at_peak"] < 1.0
        and sc["yields_locally"], f"peak {sc['sigma_peak_MPa']:.0f} MPa vs 250 MPa yield")

    # ---- thermal ----
    alpha_steel = 12e-6
    ts = thermal_stress(E, alpha_steel, 80.0, sigma_y=250.0)
    chk("thermal stress = E alpha dT",
        abs(ts["magnitude_MPa"] - E * alpha_steel * 80.0) < 1e-9,
        f"{ts['magnitude_MPa']:.1f} MPa from an 80 K rise, no external load")
    chk("a heated restrained bar is in COMPRESSION (sign is not decorative)",
        ts["sense"] == "compression" and ts["sigma_MPa"] < 0)
    chk("a cooled restrained bar is in TENSION",
        thermal_stress(E, alpha_steel, -80.0)["sense"] == "tension")
    chk("thermal stress is independent of length and area",
        thermal_stress(E, alpha_steel, 80.0)["magnitude_MPa"]
        == thermal_stress(E, alpha_steel, 80.0)["magnitude_MPa"])
    chk("...and 80 K takes mild steel most of the way to yield",
        0.5 < ts["magnitude_MPa"] / 250.0 < 1.0,
        f"{ts['magnitude_MPa']/250.0*100:.0f}% of yield")
    chk("half restraint gives half the stress",
        abs(thermal_stress(E, alpha_steel, 80.0, 0.5)["magnitude_MPa"]
            / ts["magnitude_MPa"] - 0.5) < 1e-12)
    chk("zero restraint gives zero stress (free expansion)",
        thermal_stress(E, alpha_steel, 80.0, 0.0)["magnitude_MPa"] == 0.0)
    chk("a restraint outside 0..1 is refused",
        _raises(lambda: thermal_stress(E, alpha_steel, 80.0, 1.5), ValueError))

    ex = thermal_expansion(23e-6, 1000.0, 100.0)
    chk("free expansion dL = alpha L dT (1 m aluminium, 100 K -> 2.3 mm)",
        abs(ex["dL"] - 2.3) < 1e-9, f"{ex['dL']:.4f} mm")
    chk("strain is independent of length",
        thermal_expansion(23e-6, 1.0, 100.0)["strain"]
        == thermal_expansion(23e-6, 1e6, 100.0)["strain"])

    tb = thermal_stress_two_bar(200000., 12e-6, 69000., 23e-6, 100., 100., 100.)
    chk("two-bar thermal: equilibrium closes exactly",
        abs(tb["equilibrium_residual_N"]) < 1e-6)
    chk("...and the higher-expansion bar (aluminium) ends in COMPRESSION",
        tb["sigma_2_MPa"] < 0 < tb["sigma_1_MPa"],
        f"steel {tb['sigma_1_MPa']:.1f} MPa (tension), "
        f"aluminium {tb['sigma_2_MPa']:.1f} MPa (compression)")

    print("mechanics selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="mechanics of materials beyond bending")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--torsion", nargs=4, type=float, metavar=("T_Nmm", "d_mm", "L_mm", "G_MPa"))
    ap.add_argument("--vessel", nargs=3, type=float, metavar=("p_MPa", "r_mm", "t_mm"))
    a = ap.parse_args()
    if a.torsion:
        print(json.dumps(shaft_torsion(*a.torsion), indent=2)); raise SystemExit(0)
    if a.vessel:
        print(json.dumps(thin_cylinder(*a.vessel), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
