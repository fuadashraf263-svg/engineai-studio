#!/usr/bin/env python3
"""
battery.py - the 100-task capability battery, run against EngineAI ONLY.

The one rule that makes this a real test rather than theatre:

    THE ANSWER MUST COME FROM ENGINEAI.

The closed-form anchor in each task is the ANSWER KEY, not the answer. If EngineAI
has no entry point for a task, the harness does NOT quietly compute it and score a
pass - it records NO_TOOL. A battery that fills its own gaps measures the harness
author's arithmetic, not the system under test.

Statuses:
    PASS     EngineAI produced the number and it matches the anchor
    FAIL     EngineAI produced a number and it does not match
    NO_TOOL  EngineAI has no entry point for this - the capability gap
    BLOCKED  external solver or subject matter absent from the machine
    ERROR    EngineAI raised while attempting it (also a finding)

Confidence is declared BEFORE the run, per task, and is compared against outcome to
produce the calibration curve and ECE (task 94). Declaring it afterwards would make
the number meaningless.

Usage:  python battery.py [--section N] [--json out.json] [--quiet]
"""
from __future__ import annotations
import argparse, importlib, json, math, os, shutil, subprocess, sys, time, traceback

TOL = 0.02              # 2% relative, the battery's own stated FEA tolerance
TASKS = []


class NoTool(Exception):
    """EngineAI has no entry point for this task."""


class Blocked(Exception):
    """An external solver or the subject matter is absent from this machine."""


def task(n, title, tag, tier, entry, conf):
    """entry = the EngineAI call this task exercises, or '-' if none exists.
    conf   = pre-declared confidence that EngineAI will get this right."""
    def deco(f):
        TASKS.append(dict(n=n, title=title, tag=tag, tier=tier, entry=entry,
                          conf=conf, fn=f))
        return f
    return deco


def rel(got, want):
    if want == 0:
        return abs(got)
    return abs(got - want) / abs(want)


def _imp(name):
    try:
        return importlib.import_module(name)
    except Exception as e:
        raise NoTool(f"module '{name}' unavailable: {type(e).__name__}") from e


E_S = 200000.0          # MPa - the battery specifies steel at 200 GPa throughout
G_S = 79000.0

# =========================================================================== #
# 1 - BEAMS & DEFLECTION
# =========================================================================== #
@task(1, "Cantilever, tip point load: delta and sigma", "beam_design", 1,
      "structural.rectangle + cantilever_point", 0.97)
def t1():
    s = _imp("structural")
    sec = s.rectangle(20, 40)
    r = s.cantilever_point(1000.0, 500.0, E_S, sec["I"])
    d_want = 1000.0 * 500.0 ** 3 / (3 * E_S * sec["I"])
    sig = r["M"] / sec["Z"]
    sig_want = 1000.0 * 500.0 / sec["Z"]
    return [("delta_mm", r["defl"], d_want), ("sigma_MPa", sig, sig_want)]


@task(2, "Cantilever UDL: delta and M", "beam_design", 1,
      "structural.cantilever_udl", 0.97)
def t2():
    s = _imp("structural")
    sec = s.rectangle(20, 40)
    r = s.cantilever_udl(5.0, 600.0, E_S, sec["I"])
    return [("delta_mm", r["defl"], 5.0 * 600.0 ** 4 / (8 * E_S * sec["I"])),
            ("M_Nmm", r["M"], 5.0 * 600.0 ** 2 / 2)]


@task(3, "Simply supported, central point load", "beam_design", 1,
      "structural.simply_point", 0.97)
def t3():
    s = _imp("structural")
    sec = s.rectangle(30, 50)
    r = s.simply_point(2000.0, 800.0, E_S, sec["I"])
    return [("delta_mm", r["defl"], 2000.0 * 800.0 ** 3 / (48 * E_S * sec["I"])),
            ("M_Nmm", r["M"], 2000.0 * 800.0 / 4)]


@task(4, "Simply supported UDL", "beam_design", 1, "structural.simply_udl", 0.97)
def t4():
    s = _imp("structural")
    sec = s.rectangle(30, 50)
    r = s.simply_udl(8.0, 1000.0, E_S, sec["I"])
    return [("delta_mm", r["defl"], 5 * 8.0 * 1000.0 ** 4 / (384 * E_S * sec["I"])),
            ("M_Nmm", r["M"], 8.0 * 1000.0 ** 2 / 8)]


@task(5, "Fixed-fixed, central point load", "analytic", 1,
      "mechanics.fixed_fixed_point", 0.95)
def t5():
    m, s = _imp("mechanics"), _imp("structural")
    sec = s.rectangle(30, 50)
    r = m.fixed_fixed_point(3000.0, 700.0, E_S, sec["I"])
    return [("delta_mm", r["defl"], 3000.0 * 700.0 ** 3 / (192 * E_S * sec["I"])),
            ("M_end_Nmm", r["M_end"], 3000.0 * 700.0 / 8.0)]


@task(6, "Hollow tube vs equal-mass solid rod: stiffness ratio", "structural", 1,
      "structural.tube + solid_circle", 0.95)
def t6():
    s = _imp("structural")
    tu = s.tube(40.0, 30.0)
    d_eq = math.sqrt(40.0 ** 2 - 30.0 ** 2)      # equal area => equal mass
    so = s.solid_circle(d_eq)
    # exact: (do^2+di^2)/(do^2-di^2) since do^4-di^4 factorises
    return [("I_ratio", tu["I"] / so["I"], (40.0 ** 2 + 30.0 ** 2) / (40.0 ** 2 - 30.0 ** 2))]


@task(7, "I-beam cantilever: sigma and delta", "structural", 1,
      "structural.ibeam + cantilever_point", 0.95)
def t7():
    s = _imp("structural")
    sec = s.ibeam(50.0, 100.0, 6.0, 8.0)
    r = s.cantilever_point(5000.0, 1000.0, E_S, sec["I"])
    return [("delta_mm", r["defl"], 5000.0 * 1000.0 ** 3 / (3 * E_S * sec["I"])),
            ("sigma_MPa", r["M"] / sec["c"] * sec["c"] / sec["Z"],
             5000.0 * 1000.0 / sec["Z"])]


@task(8, "FEA<->analytic: CalculiX cantilever, 3x mesh refinement", "CalculiX", 2,
      "fea.mesh_convergence", 0.85)
def t8():
    if not shutil.which("ccx"):
        raise Blocked("ccx not on PATH")
    f = _imp("fea")
    mc = f.mesh_convergence(1000.0, 500.0, 20.0, 40.0,
                            refinements=((8, 1, 2), (16, 2, 4), (32, 2, 8)))
    if "error" in mc:
        raise NoTool(mc["error"])
    return [("converged_within_2pc_of_theory",
             abs(mc["final_error_vs_timoshenko_percent"]), 0.0)
            if False else
            ("final_error_vs_timoshenko_pc",
             1.0 if abs(mc["final_error_vs_timoshenko_percent"]) < 2.0 else 0.0, 1.0),
            ("monotonic_convergence", 1.0 if mc["monotonic"] else 0.0, 1.0),
            ("last_refinement_change_under_2pc",
             1.0 if mc["last_refinement_change_percent"] < 2.0 else 0.0, 1.0)]


@task(9, "Superposition: tip load + UDL on one cantilever", "analytic", 1,
      "structural.cantilever_point + cantilever_udl", 0.95)
def t9():
    s = _imp("structural")
    sec = s.rectangle(20, 40)
    a = s.cantilever_point(1000.0, 500.0, E_S, sec["I"])["defl"]
    b = s.cantilever_udl(5.0, 500.0, E_S, sec["I"])["defl"]
    want = (1000.0 * 500.0 ** 3 / (3 * E_S * sec["I"])
            + 5.0 * 500.0 ** 4 / (8 * E_S * sec["I"]))
    return [("delta_sum_mm", a + b, want)]


@task(10, "Solve max cantilever span for delta <= L/250", "structural", 1,
      "structural.solid_circle + cantilever_point (harness bisection)", 0.55)
def t10():
    s = _imp("structural")
    sec = s.solid_circle(25.0)
    # EngineAI has no solver that inverts a serviceability constraint; the search
    # loop below is harness-side, so this is a PARTIAL capability at best.
    lo, hi = 10.0, 2000.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if s.cantilever_point(500.0, mid, E_S, sec["I"])["defl"] <= mid / 250.0:
            lo = mid
        else:
            hi = mid
    want = math.sqrt(3 * E_S * sec["I"] / (250.0 * 500.0))
    return [("L_max_mm", lo, want)]


@task(11, "Overhanging beam: reactions and max moment", "analytic", 1,
      "mechanics.overhang_point", 0.93)
def t11():
    m, s = _imp("mechanics"), _imp("structural")
    sec = s.rectangle(30, 50)
    P, L, a = 2000.0, 1000.0, 400.0
    r = m.overhang_point(P, L, a, E_S, sec["I"])
    return [("R2_N", r["R2"], P * (L + a) / L),
            ("R1_N_uplift", r["R1"], -P * a / L),
            ("M_at_support_Nmm", r["M_at_support"], -P * a),
            ("sum_F_residual", r["static_check_sum_F"], 0.0),
            ("sum_M_residual", r["static_check_sum_M_about_R1"], 0.0)]


@task(12, "Off-centre point load on simply-supported beam", "analytic", 1,
      "mechanics.simply_point_offset", 0.93)
def t12():
    m, s = _imp("mechanics"), _imp("structural")
    sec = s.rectangle(30, 50)
    P, a, b = 2000.0, 300.0, 700.0
    L = a + b
    r = m.simply_point_offset(P, a, b, E_S, sec["I"])
    return [("R1_N", r["R1"], P * b / L), ("R2_N", r["R2"], P * a / L),
            ("M_max_Nmm", r["M_max"], P * a * b / L),
            ("delta_at_load_mm", r["defl_at_load"],
             P * a ** 2 * b ** 2 / (3 * L * E_S * sec["I"]))]


# =========================================================================== #
# 2 - BUCKLING, TORSION, PRESSURE, STRESS CONCENTRATION
# =========================================================================== #
@task(13, "Euler buckling, pinned-pinned rod", "analytic", 1,
      "column_design.flexural_buckling", 0.80)
def t13():
    cd, s = _imp("column_design"), _imp("structural")
    sec = s.solid_circle(20.0)
    fake = {"A_mm2": sec["A"], "I_mm4": sec["I"], "Iz_mm4": sec["I"],
            "iy_mm": math.sqrt(sec["I"] / sec["A"]),
            "iz_derived_mm": math.sqrt(sec["I"] / sec["A"])}
    # the battery states steel at 200 GPa; EngineAI must be able to answer AT 200 GPa
    r = cd.flexural_buckling(fake, 275.0, 1500.0, "z", "a", E=E_S)
    dflt = cd.flexural_buckling(fake, 275.0, 1500.0, "z", "a")
    return [("N_cr_N_at_200GPa", r["N_cr_N"],
             math.pi ** 2 * E_S * sec["I"] / 1500.0 ** 2),
            ("N_cr_N_at_EN_default_210GPa", dflt["N_cr_N"],
             math.pi ** 2 * cd.E_STEEL * sec["I"] / 1500.0 ** 2)]


@task(14, "Effective length: Pcr scales as 1/K^2", "analytic", 1,
      "column_design.effective_length", 0.90)
def t14():
    cd = _imp("column_design")
    k_free = cd.effective_length(1500.0, "fixed-free", use_theoretical=True)["k"]
    k_fix = cd.effective_length(1500.0, "fixed-fixed", use_theoretical=True)["k"]
    return [("k_fixed_free", k_free, 2.0), ("k_fixed_fixed", k_fix, 0.5),
            ("Pcr_ratio_free_to_fix", (k_fix / k_free) ** 2, (0.5 / 2.0) ** 2)]


@task(15, "Slenderness transition: Euler stress == yield", "analytic", 1,
      "column_design.lambda_1", 0.70)
def t15():
    cd = _imp("column_design")
    fy = 275.0
    lam1 = cd.lambda_1(fy)                       # = pi sqrt(E/fy), the transition
    want = math.pi * math.sqrt(cd.E_STEEL / fy)
    # transition L for d=20 rod: L/i = lambda_1  =>  L = lambda_1 * i
    i = 20.0 / 4.0
    return [("lambda_1", lam1, want), ("L_transition_mm", lam1 * i, want * i)]


@task(16, "Circular shaft torsion: tau and twist", "analytic", 1,
      "mechanics.shaft_torsion", 0.95)
def t16():
    m = _imp("mechanics")
    T, d, L, G = 500e3, 40.0, 1000.0, 79000.0
    r = m.shaft_torsion(T, d, L, G)
    J = math.pi * d ** 4 / 32.0
    return [("J_mm4", r["J_mm4"], J), ("tau_max_MPa", r["tau_max_MPa"], T * 20.0 / J),
            ("theta_rad", r["theta_rad"], T * L / (G * J))]


@task(17, "Hollow shaft torsion and mass saving", "analytic", 1,
      "mechanics.shaft_compare_hollow", 0.93)
def t17():
    m = _imp("mechanics")
    r = m.shaft_compare_hollow(500e3, 40.0, 1000.0, 79000.0, 30.0)
    J_h = math.pi * (40.0 ** 4 - 30.0 ** 4) / 32.0
    J_s = math.pi * 40.0 ** 4 / 32.0
    return [("tau_hollow_MPa", r["hollow"]["tau_max_MPa"], 500e3 * 20.0 / J_h),
            ("stress_increase_pc", r["stress_increase_percent"],
             100.0 * (J_s / J_h - 1.0)),
            ("mass_saving_pc", r["mass_saving_percent"],
             100.0 * (1.0 - (40.0 ** 2 - 30.0 ** 2) / 40.0 ** 2))]


@task(18, "Thin-walled pressure vessel hoop and longitudinal stress", "analytic", 1,
      "mechanics.thin_cylinder", 0.95)
def t18():
    m = _imp("mechanics")
    r = m.thin_cylinder(2.0, 200.0, 5.0)
    return [("hoop_MPa", r["hoop_MPa"], 2.0 * 200.0 / 5.0),
            ("longitudinal_MPa", r["longitudinal_MPa"], 2.0 * 200.0 / (2 * 5.0)),
            ("hoop_over_long", r["hoop_MPa"] / r["longitudinal_MPa"], 2.0)]


@task(19, "Stress concentration at a hole, Kt ~ 3", "analytic", 1,
      "mechanics.kt_circular_hole + stress_concentration", 0.93)
def t19():
    m = _imp("mechanics")
    kt = m.kt_circular_hole(0.0)
    r = m.stress_concentration(100.0, kt, sigma_y=250.0)
    return [("Kt_infinite_plate", kt, 3.0),
            ("sigma_peak_MPa", r["sigma_peak_MPa"], 300.0),
            ("Kt_elliptical_circle", m.kt_elliptical_hole(5.0, 5.0), 3.0),
            ("Kt_inglis_semicircular", m.kt_notch_inglis(5.0, 5.0), 3.0)]


@task(20, "Thermal stress in a fully constrained bar", "analytic", 1,
      "fatigue.alpha + mechanics.thermal_stress", 0.93)
def t20():
    m, f = _imp("mechanics"), _imp("fatigue")
    a = f.alpha("AISI_1018_steel")
    E = f.props("AISI_1018_steel")["E"]
    r = m.thermal_stress(E, a, 80.0, sigma_y=370.0)
    return [("sigma_magnitude_MPa", r["magnitude_MPa"], E * a * 80.0),
            ("is_compression", 1.0 if r["sense"] == "compression" else 0.0, 1.0)]


# =========================================================================== #
# 3 - CAD, PARTS & DRAWINGS
# =========================================================================== #
@task(21, "mounting_plate 150x100x12, 4xD10: volume", "draw_part", 1,
      "cad_parts.mounting_plate_volume + build", 0.93)
def t21():
    cp = _imp("cad_parts")
    v = cp.mounting_plate_volume(L=150.0, W=100.0, T=12.0, hole_d=10.0, margin=20.0)
    want = 150.0 * 100.0 * 12.0 - 4 * math.pi / 4 * 10.0 ** 2 * 12.0
    return [("volume_mm3", v, want)]


@task(22, "l_bracket a=80 b=60 t=8 w=70: volume", "draw_part", 1,
      "cad_parts.l_bracket_volume", 0.93)
def t22():
    cp = _imp("cad_parts")
    v = cp.l_bracket_volume(a=80.0, b=60.0, t=8.0, width=70.0)
    want = (80.0 * 8.0 + (60.0 - 8.0) * 8.0) * 70.0
    return [("volume_mm3", v, want)]


@task(23, "Stepped shaft d1=30 l1=60 d2=18 l2=40: volume", "draw_part", 1,
      "cad_parts.shaft_volume", 0.93)
def t23():
    cp = _imp("cad_parts")
    v = cp.shaft_volume(d1=30.0, l1=60.0, d2=18.0, l2=40.0)
    want = math.pi / 4 * (30.0 ** 2 * 60.0 + 18.0 ** 2 * 40.0)
    return [("volume_mm3", v, want)]


@task(24, "Flange od=120 id=50 t=15, 6xD11 on bc=90: volume", "draw_part", 1,
      "cad_parts.flange_volume", 0.93)
def t24():
    cp = _imp("cad_parts")
    v = cp.flange_volume(od=120.0, idia=50.0, t=15.0, bc=90.0, n=6.0, bolt_d=11.0)
    want = math.pi / 4 * (120.0 ** 2 - 50.0 ** 2) * 15.0 - 6 * math.pi / 4 * 11.0 ** 2 * 15.0
    return [("volume_mm3", v, want)]


@task(25, "U-channel w=70 h=50 t=6 L=250: volume", "draw_part", 1,
      "cad_parts.u_channel_volume", 0.93)
def t25():
    cp = _imp("cad_parts")
    v = cp.u_channel_volume(w=70.0, h=50.0, t=6.0, length=250.0)
    want = (70.0 * 50.0 - (70.0 - 2 * 6.0) * (50.0 - 6.0)) * 250.0
    return [("volume_mm3", v, want)]


@task(26, "design_part AISI 1018, 4000 N, FoS 2.5, and t-1 must fail", "design_part", 1,
      "design_part.design_part", 0.90)
def t26():
    dp, s = _imp("design_part"), _imp("structural")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = dp.design_part(load_N=4000.0, span_mm=300.0, width_mm=50.0,
                           material="AISI_1018_steel", target_fos=2.5,
                           name="battery26", outdir=d)
    res = r["result"]
    t = res["sized_thickness_mm"]
    # "sizing is tight": one millimetre thinner must violate something. Checked with
    # EngineAI's OWN beam functions, not with harness arithmetic.
    thin = s.rectangle(50.0, t - 1.0)
    d_thin = s.cantilever_point(4000.0, 300.0, res["section"].get("E_MPa", 200000.0),
                                thin["I"])["defl"]
    sig_thin = 4000.0 * 300.0 / thin["Z"]
    fails = (d_thin > res["deflection_allowable_mm"]) or \
            (res["yield_MPa"] / sig_thin < 2.5)
    return [("verified_flag", 1.0 if r["verified"] else 0.0, 1.0),
            ("fos_meets_target", 1.0 if res["factor_of_safety"] >= 2.5 else 0.0, 1.0),
            ("all_checks_passed",
             1.0 if all(c["passed"] for c in r["checks"]) else 0.0, 1.0),
            ("t_minus_1mm_FAILS", 1.0 if fails else 0.0, 1.0)]


@task(27, "Same part in AL 6061: compare thickness and mass", "design_part", 1,
      "design_part.design_part x2", 0.88)
def t27():
    dp = _imp("design_part")
    import tempfile
    out = {}
    with tempfile.TemporaryDirectory() as d:
        for mat in ("AISI_1018_steel", "AL_6061_T6"):
            out[mat] = dp.design_part(load_N=4000.0, span_mm=300.0, width_mm=50.0,
                                      material=mat, target_fos=2.5,
                                      name="b27_" + mat, outdir=d)
    ts = out["AISI_1018_steel"]["result"]["sized_thickness_mm"]
    ta = out["AL_6061_T6"]["result"]["sized_thickness_mm"]
    ms = out["AISI_1018_steel"]["result"]["mass_kg"]
    ma = out["AL_6061_T6"]["result"]["mass_kg"]
    # Aluminium has ~1/3 the modulus, so a deflection-governed part must come out
    # THICKER - and yet, at ~1/3 the density, still lighter. Both must hold.
    return [("al_thicker_than_steel", 1.0 if ta > ts else 0.0, 1.0),
            ("al_still_lighter", 1.0 if ma < ms else 0.0, 1.0)]


@task(28, "Parametric library part measured from its own B-rep", "cad", 2,
      "drawings_dim.measure_solid", 0.90)
def t28():
    dd, cp = _imp("drawings_dim"), _imp("cad_parts")
    wp, _p = cp.build("u_channel", w=70.0, h=50.0, t=6.0, length=250.0)
    m = dd.measure_solid(wp.val())
    # the channel is extruded along Z in this model, so compare the SET of extents
    # rather than assuming which axis is which
    got = sorted([m["xlen"], m["ylen"], m["zlen"]])
    return [("extent_min_mm", got[0], 50.0), ("extent_mid_mm", got[1], 70.0),
            ("extent_max_mm", got[2], 250.0),
            ("no_spurious_holes", float(m["hole_count"]), 0.0)]


@task(29, "Dimension the projected sheet with hole positions and diameter callouts",
      "draw", 2, "drawings_dim.make_dimensioned_sheet", 0.88)
def t29():
    dd = _imp("drawings_dim")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = dd.make_dimensioned_sheet("mounting_plate", outdir=d, L=150.0, W=100.0,
                                      T=12.0, hole_d=10.0, margin=20.0)
        # read INSIDE the context - the directory is deleted on exit, and reading
        # after it returns an empty string that looks like a missing feature
        svg = open(r["files"]["svg"]).read()
    v = r["verification"]
    return [("all_dims_verified_against_solid",
             1.0 if v["all_dimensions_match_solid"] else 0.0, 1.0),
            ("worst_dim_error_mm", v["worst_error_mm"], 0.0),
            ("has_diameter_callout_with_count", 1.0 if "4 x" in svg else 0.0, 1.0),
            ("dimension_count_at_least_8",
             1.0 if r["dimension_count"] >= 8 else 0.0, 1.0)]


@task(30, "GD&T frame (position / flatness) on the flange sheet", "draw", 2,
      "drawings_dim GD&T frames", 0.88)
def t30():
    dd = _imp("drawings_dim")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = dd.make_dimensioned_sheet(
            "flange", outdir=d, od=120.0, idia=50.0, t=15.0, bc=90.0, n=6.0,
            bolt_d=11.0,
            gdt=[{"characteristic": "position", "tolerance": 0.2,
                  "datums": ["A", "B"], "view": "top"},
                 {"characteristic": "flatness", "tolerance": 0.05, "view": "top"}])
    return [("fcf_frames_placed", float(r["gdt_frames"]), 2.0),
            ("sheet_verified",
             1.0 if r["verification"]["all_dimensions_match_solid"] else 0.0, 1.0),
            ("iso1101_declared",
             1.0 if any("1101" in s for s in r["standards"]) else 0.0, 1.0)]


@task(31, "STEP export -> re-import -> bbox and volume round-trip", "cad", 1,
      "cad_parts.build + export", 0.80)
def t31():
    cp = _imp("cad_parts")
    import tempfile
    wp, _params = cp.build("flange", od=120.0, idia=50.0, t=15.0,
                           bc=90.0, n=6.0, bolt_d=11.0)
    v0 = wp.val().Volume()
    bb0 = wp.val().BoundingBox()
    with tempfile.TemporaryDirectory() as d:
        step, _stl = cp.export(wp, os.path.join(d, "b31"))
        import cadquery as cq
        back = cq.importers.importStep(step)
        v1 = back.val().Volume()
        bb1 = back.val().BoundingBox()
    return [("volume_roundtrip_mm3", v1, v0),
            ("bbox_x_mm", bb1.xlen, bb0.xlen), ("bbox_z_mm", bb1.zlen, bb0.zlen)]


@task(32, "Isometric view alongside the three orthographic views", "draw", 2,
      "drawings_project.project_isometric", 0.90)
def t32():
    dpj, cp = _imp("drawings_project"), _imp("cad_parts")
    ic = dpj.isometric_axis_check()
    wp, _p = cp.build("l_bracket")
    iso = dpj.project_isometric(wp.val().wrapped)
    ortho = dpj.project_views(wp.val().wrapped)
    widest = max(v["w"] for v in ortho.values())
    lens = list(ic["projected_lengths"].values())
    return [("all_axes_foreshorten_equally", max(lens) - min(lens), 0.0),
            ("foreshortening_is_sqrt_2_over_3", lens[0], math.sqrt(2.0 / 3.0)),
            ("axes_120_degrees_apart", 1.0 if ic["all_120_apart"] else 0.0, 1.0),
            ("iso_view_has_geometry",
             1.0 if len(iso["visible"]) > 0 and iso["w"] > 0 else 0.0, 1.0),
            ("iso_wider_than_any_orthographic",
             1.0 if iso["w"] > widest else 0.0, 1.0),
            ("orthographic_set_unchanged", float(len(ortho)), 3.0)]


# =========================================================================== #
# 4 - THERMAL
# =========================================================================== #
@task(33, "1D conduction through a wall", "analytic", 1, "thermal.plane_wall", 0.95)
def t33():
    th = _imp("thermal")
    r = th.plane_wall(45.0, 0.5, 0.020, dT=100.0)
    return [("q_W", r["q_W"], 45.0 * 0.5 * 100.0 / 0.020),
            ("R_K_per_W", r["R_K_per_W"], 0.020 / (45.0 * 0.5))]


@task(34, "Composite wall in series", "analytic", 1, "thermal.composite_wall", 0.93)
def t34():
    th = _imp("thermal")
    r = th.composite_wall([("carbon_steel", 0.010), ("mineral_wool", 0.100),
                           ("brick", 0.100)], area=1.0, dT=30.0)
    manual = 0.010 / 60.5 + 0.100 / 0.038 + 0.100 / 0.72
    return [("R_total_K_per_W", r["R_total_K_per_W"], manual),
            ("q_W", r["q_W"], 30.0 / manual),
            ("layer_dT_sum_K", sum(r["layer_dT_K"].values()), 30.0)]


@task(35, "Lumped-capacitance cooling of a sphere", "analytic", 1,
      "thermal.lumped_capacitance", 0.93)
def t35():
    th = _imp("thermal")
    d, h = 0.030, 25.0
    V, A = math.pi * d ** 3 / 6.0, math.pi * d ** 2
    st = th.material("carbon_steel")
    r = th.lumped_capacitance(h, A, V, st["rho_kg_m3"], st["cp_J_kgK"],
                              573.0, 293.0, t=300.0, k=st["k_W_mK"])
    tau = st["rho_kg_m3"] * V * st["cp_J_kgK"] / (h * A)
    return [("Biot", r["Biot"], h * (V / A) / st["k_W_mK"]),
            ("lumped_valid", 1.0 if r["lumped_valid"] else 0.0, 1.0),
            ("T_K", r["T_K"], 293.0 + 280.0 * math.exp(-300.0 / tau))]


@task(36, "Straight rectangular fin: tip T, heat rate, efficiency", "analytic", 1,
      "thermal.fin_rectangular", 0.90)
def t36():
    th = _imp("thermal")
    k, h, t, w, L = 237.0, 25.0, 0.003, 0.100, 0.050
    r = th.fin_rectangular(k, h, t, w, L, 373.0, 293.0)
    P, Ac = 2 * (t + w), t * w
    m_ = math.sqrt(h * P / (k * Ac))
    M = math.sqrt(h * P * k * Ac) * 80.0
    return [("q_W", r["q_W"], M * math.tanh(m_ * L)),
            ("efficiency", r["efficiency"], math.tanh(m_ * L) / (m_ * L)),
            ("T_tip_K", r["T_tip_K"], 293.0 + 80.0 / math.cosh(m_ * L))]


@task(37, "Radiative loss from a plate", "analytic", 1, "thermal.radiation", 0.95)
def t37():
    th = _imp("thermal")
    r = th.radiation(0.2, 400.0, 300.0, 0.8)
    return [("q_W", r["q_W"], 0.8 * 5.670374419e-8 * 0.2 * (400.0 ** 4 - 300.0 ** 4))]


@task(38, "Steady conduction FEA vs the analytic resistance network", "Elmer", 2,
      "fea.thermal_wall + thermal_cross_check", 0.85)
def t38():
    # Elmer is absent and unobtainable here, but steady conduction is a standard
    # CalculiX analysis. Using the solver that IS present is the point.
    f = _imp("fea")
    if not f.ccx_available():
        raise Blocked("neither Elmer nor ccx is available")
    tw = f.thermal_wall(k=45.0, L=20.0, W=100.0, H=100.0, T_hot=100.0, T_cold=0.0)
    if "error" in tw:
        raise NoTool(tw["error"])
    cc = f.thermal_cross_check()
    return [("field_is_linear", 1.0 if tw["linear"] else 0.0, 1.0),
            ("hot_face_K", tw["profile"][0][1], 100.0),
            ("cold_face_K", tw["profile"][-1][1], 0.0),
            ("analytic_vs_fea_agree", 1.0 if cc["agreed"] else 0.0, 1.0),
            ("q_W", cc["fem_q_W"], cc["analytic_q_W"])]


@task(39, "Transient semi-infinite solid, erf solution", "analytic", 1,
      "thermal.semi_infinite_step", 0.93)
def t39():
    th = _imp("thermal")
    k, rho, cp, Ti, Ts, x, t = 1.4, 2300.0, 880.0, 283.0, 373.0, 0.05, 3600.0
    r = th.semi_infinite_step(k, rho, cp, Ti, Ts, x, t)
    al = k / (rho * cp)
    return [("T_K", r["T_K"], Ts + (Ti - Ts) * math.erf(x / (2 * math.sqrt(al * t)))),
            ("alpha_m2_s", r["alpha_m2_s"], al),
            ("T_at_surface_K", th.semi_infinite_step(k, rho, cp, Ti, Ts, 0.0, t)["T_K"],
             Ts)]


@task(40, "Heat-sink meshed in Gmsh, base temperature from the thermal network",
      "Elmer+Gmsh", 2, "deliverables.make_gmsh + fea.heatsink", 0.82)
def t40():
    f, d = _imp("fea"), _imp("deliverables")
    import tempfile
    hs = f.heatsink(power_W=20.0)
    with tempfile.TemporaryDirectory() as td:
        g = d.make_gmsh(os.path.join(td, "hs.geo"), os.path.join(td, "hs.msh"))
        meshed = 1.0 if g["verified"] else 0.0
        nodes = float(g.get("nodes", 0))
    return [("gmsh_meshed_the_heatsink", meshed, 1.0),
            ("mesh_has_real_node_count", 1.0 if nodes > 100 else 0.0, 1.0),
            ("base_temperature_above_ambient",
             1.0 if hs["T_base_C"] > 25.0 else 0.0, 1.0),
            ("fin_efficiency_in_range",
             1.0 if 0.0 < hs["fin_efficiency"] <= 1.0 else 0.0, 1.0),
            ("finned_beats_bare",
             1.0 if hs["T_base_C"] < hs["T_base_if_unfinned_C"] else 0.0, 1.0)]


@task(41, "Contact temperature of two dissimilar bodies", "analytic", 1,
      "thermal.contact_temperature", 0.93)
def t41():
    th = _imp("thermal")
    r = th.contact_temperature("carbon_steel", 293.0, "wood_oak", 310.0)
    ea = math.sqrt(60.5 * 7854.0 * 434.0)
    eb = math.sqrt(0.17 * 750.0 * 2385.0)
    return [("T_contact_K", r["T_contact_K"], (293.0 * ea + 310.0 * eb) / (ea + eb)),
            ("effusivity_steel", r["effusivity_a"], ea)]


@task(42, "Thermal resistance network, junction to ambient", "analytic", 1,
      "thermal.resistance_network", 0.93)
def t42():
    th = _imp("thermal")
    # junction->case 0.5, case->sink 0.2, sink->ambient 2.0 K/W, 25 W dissipated
    r = th.resistance_network([0.5, 0.2, 2.0], q=25.0, mode="series")
    return [("R_total_K_per_W", r["R_total_K_per_W"], 2.7),
            ("junction_rise_K", r["dT_K"], 25.0 * 2.7),
            ("parallel_of_two_equal",
             th.resistance_network([2.0, 2.0], q=1.0, mode="parallel")["R_total_K_per_W"],
             1.0)]


# =========================================================================== #
# 5 - ELECTROMAGNETICS
# =========================================================================== #
MU0 = 4e-7 * math.pi
EPS0 = 1.0 / (MU0 * 299792458.0 ** 2)


@task(43, "B field around an infinite wire", "em", 1, "emag.b_wire", 0.95)
def t43():
    em = _imp("emag")
    r = em.b_wire(100.0, 0.005)
    return [("B_T", r["B_T"], MU0 * 100.0 / (2 * math.pi * 0.005)),
            ("ampere_law_residual", r["ampere_law_residual"], 0.0)]


@task(44, "B inside a long solenoid", "analytic", 1, "emag.b_solenoid", 0.95)
def t44():
    em = _imp("emag")
    return [("B_T", em.b_solenoid(1000.0, 3.0)["B_T"], MU0 * 1000.0 * 3.0)]


@task(45, "B inside a toroid", "analytic", 1, "emag.b_toroid", 0.95)
def t45():
    em = _imp("emag")
    r = em.b_toroid(500.0, 5.0, 0.050)
    return [("B_T", r["B_T"], MU0 * 500.0 * 5.0 / (2 * math.pi * 0.050)),
            ("N_times_single_wire", r["B_T"] / em.b_wire(5.0, 0.050)["B_T"], 500.0)]


@task(46, "Force per length between parallel wires", "analytic", 1,
      "emag.force_parallel_wires", 0.95)
def t46():
    em = _imp("emag")
    return [("F_per_L_N_per_m", em.force_parallel_wires(200.0, 200.0, 0.010)["F_per_L_N_per_m"],
             MU0 * 200.0 * 200.0 / (2 * math.pi * 0.010)),
            ("SI_ampere_definition_2e-7",
             em.force_parallel_wires(1.0, 1.0, 1.0)["F_per_L_N_per_m"], 2e-7)]


@task(47, "Coaxial inductance per unit length", "analytic", 1,
      "emag.inductance_coax", 0.95)
def t47():
    em = _imp("emag")
    r = em.inductance_coax(0.001, 0.004)
    line = em.coax_line(0.001, 0.004)
    return [("L_H_per_m", r["L_per_m_H"], MU0 / (2 * math.pi) * math.log(4.0)),
            ("c_from_LC_m_per_s", line["v_m_s"] if "v_m_s" in line
             else line["v_m_per_s"], 299792458.0)]


@task(48, "Parallel-plate capacitance", "analytic", 1,
      "emag.capacitance_parallel_plate", 0.95)
def t48():
    em = _imp("emag")
    return [("C_F", em.capacitance_parallel_plate(0.01, 0.0005, 4.0)["C_F"],
             EPS0 * 4.0 * 0.01 / 0.0005)]


@task(49, "Magnetic circuit with an air gap", "analytic", 1,
      "emag.magnetic_circuit", 0.90)
def t49():
    em = _imp("emag")
    r = em.magnetic_circuit(1000.0, [("iron", 0.300, 0.0004, 2000.0),
                                     ("gap", 0.001, 0.0004, 1.0)])
    R_iron = 0.300 / (MU0 * 2000.0 * 0.0004)
    R_gap = 0.001 / (MU0 * 1.0 * 0.0004)
    return [("flux_Wb", r["flux_Wb"], 1000.0 / (R_iron + R_gap)),
            ("R_total", r["R_total_A_per_Wb"], R_iron + R_gap),
            ("mmf_sum_A", sum(s["mmf_drop_A"] for s in r["segments"]), 1000.0)]


@task(50, "Skin depth in copper at 50 Hz and 50 kHz", "analytic", 1,
      "emag.skin_depth", 0.93)
def t50():
    em = _imp("emag")
    sig = em.conductivity("copper")["sigma_S_per_m"]
    d50 = em.skin_depth(50.0)["delta_m"]
    d50k = em.skin_depth(50000.0)["delta_m"]
    return [("skin_depth_50Hz_m", d50,
             math.sqrt(2.0 / (2 * math.pi * 50.0 * MU0 * sig))),
            ("skin_depth_50kHz_m", d50k,
             math.sqrt(2.0 / (2 * math.pi * 50000.0 * MU0 * sig))),
            ("ratio_is_sqrt_1000", d50 / d50k, math.sqrt(1000.0))]


@task(51, "Magnetostatic FEA of a C-core vs the reluctance model", "Elmer", 2,
      "magnetostatics.c_core", 0.82)
def t51():
    ms = _imp("magnetostatics")
    w = ms.wire_field(I=100.0)
    cc = ms.c_core()
    return [("wire_field_follows_1_over_r",
             1.0 if w["follows_one_over_r"] else 0.0, 1.0),
            ("wire_magnitude_vs_analytic",
             1.0 if w["max_error_vs_analytic"] < 0.15 else 0.0, 1.0),
            ("ccore_gap_field_positive",
             1.0 if cc["B_gap_fea_T"] > 0 else 0.0, 1.0),
            ("fea_within_2x_of_reluctance_model",
             1.0 if 0.3 < cc["ratio_fea_over_lumped"] < 1.6 else 0.0, 1.0)]


@task(52, "Harmonic eddy currents: field decay must match the skin depth", "Elmer",
      3, "magnetostatics.eddy_current_slab", 0.82)
def t52():
    ms = _imp("magnetostatics")
    ec = ms.eddy_current_slab(frequency=1000.0)
    hi = ms.eddy_current_slab(frequency=10000.0)
    return [("fitted_skin_depth_m", ec["skin_depth_fitted_m"],
             ec["skin_depth_analytic_m"]),
            ("H_at_one_delta_is_1_over_e", ec["H_at_one_delta_over_H0"],
             math.exp(-1.0)),
            ("delta_scales_as_1_over_sqrt_f",
             ec["skin_depth_analytic_m"] / hi["skin_depth_analytic_m"],
             math.sqrt(10.0)),
            ("loss_rises_with_frequency",
             1.0 if hi["loss_W_per_m2"] > ec["loss_W_per_m2"] else 0.0, 1.0)]


# =========================================================================== #
# 6 - FLUIDS
# =========================================================================== #
@task(53, "Hagen-Poiseuille laminar pipe: Re and dP", "analytic", 1,
      "engineai_fluids.reynolds + poiseuille_dp", 0.95)
def t53():
    ef = _imp("engineai_fluids")
    D, L, Q = 0.010, 2.0, 1.0e-4
    rho, mu = 998.0, 1.002e-3
    U = Q / (math.pi / 4 * D ** 2)
    Re = ef.reynolds(U, D, rho=rho, mu=mu)
    dp = ef.poiseuille_dp(mu, L, Q, D)
    return [("Re", Re, rho * U * D / mu),
            ("dP_Pa", dp, 128 * mu * L * Q / (math.pi * D ** 4))]


@task(54, "Turbulent pipe: Colebrook friction factor and head loss", "analytic", 1,
      "engineai_fluids.f_colebrook + pressure_drop", 0.95)
def t54():
    ef = _imp("engineai_fluids")
    Re, eD = 1.0e5, 0.0002
    f = ef.f_colebrook(Re, eD)
    # Colebrook is implicit; verify by residual, the only honest check
    resid = 1.0 / math.sqrt(f) + 2.0 * math.log10(eD / 3.7 + 2.51 / (Re * math.sqrt(f)))
    dp = ef.pressure_drop(f, 10.0, 0.05, 998.0, 2.0)
    return [("colebrook_residual", resid, 0.0),
            ("dP_Pa", dp, f * 10.0 / 0.05 * 0.5 * 998.0 * 2.0 ** 2)]


@task(55, "Converging nozzle: Bernoulli + continuity", "analytic", 1,
      "fluid_mech.nozzle + bernoulli", 0.95)
def t55():
    fm = _imp("fluid_mech")
    rho = 998.0
    n = fm.nozzle(rho, 0.100, 0.050, v1=2.0)
    b = fm.bernoulli(rho, p1=0.0, v1=2.0, p2=None, v2=8.0)
    return [("v2_m_s", n["v2_ideal_m_s"], 8.0),
            ("dp_Pa", n["dp_Pa"], 0.5 * rho * (64.0 - 4.0)),
            ("bernoulli_agrees_Pa", -b["p2_Pa"], n["dp_Pa"]),
            ("head_residual_Pa", b["head_residual_Pa"], 0.0)]


@task(56, "Sphere drag in the Stokes regime", "analytic", 1,
      "fluid_mech.drag_sphere + terminal_velocity", 0.95)
def t56():
    fm = _imp("fluid_mech")
    rho, mu, d, v = 998.0, 1.002e-3, 1e-4, 1e-3
    r = fm.drag_sphere(rho, mu, d, v)
    Re = rho * v * d / mu
    tv = fm.terminal_velocity(2650.0, rho, mu, 1e-5)
    return [("Cd", r["Cd"], 24.0 / Re),
            ("F_N", r["F_N"], 3.0 * math.pi * mu * d * v),
            ("v_terminal_m_s", tv["v_m_s"],
             9.80665 * 1e-10 * (2650 - 998) / (18 * mu))]


@task(57, "Blasius flat-plate boundary layer", "analytic", 1,
      "fluid_mech.blasius_boundary_layer", 0.95)
def t57():
    fm = _imp("fluid_mech")
    rho, mu, v, x = 1.225, 1.81e-5, 10.0, 0.5
    r = fm.blasius_boundary_layer(rho, mu, v, x)
    Re = rho * v * x / mu
    return [("Re_x", r["Re_x"], Re),
            ("delta_m", r["delta_m"], 5.0 * x / math.sqrt(Re)),
            ("Cf_local", r["Cf_local"], 0.664 / math.sqrt(Re)),
            ("Cd_over_Cf_is_2", r["Cd_average_to_x"] / r["Cf_local"], 2.0)]


@task(58, "Thin-airfoil lift Cl = 2 pi alpha", "analytic", 1,
      "fluid_mech.thin_airfoil", 0.95)
def t58():
    fm = _imp("fluid_mech")
    r = fm.thin_airfoil(5.0)
    return [("Cl", r["Cl"], 2 * math.pi * math.radians(5.0)),
            ("slope_per_deg", r["lift_slope_per_deg"], 2 * math.pi * math.pi / 180.0)]


@task(59, "CFD validated against the exact Poiseuille solution", "OpenFOAM", 3,
      "cfd.validate", 0.80)
def t59():
    c = _imp("cfd")
    if not c.foam_available():
        raise Blocked("no OpenFOAM at /opt/openfoam13")
    v = c.validate()
    if v.get("reason"):
        raise NoTool(v["reason"])
    return [("mean_over_max", v["mean_over_max"], 2.0 / 3.0),
            ("ratio_error_under_2pc",
             1.0 if v["ratio_error_percent"] < 2.0 else 0.0, 1.0),
            ("profile_error_under_3pc",
             1.0 if v["profile_max_error_percent"] < 3.0 else 0.0, 1.0),
            ("declared_validated", 1.0 if v["validated"] else 0.0, 1.0)]


@task(60, "Developing laminar channel flow vs entrance length", "OpenFOAM", 3,
      "cfd.poiseuille", 0.78)
def t60():
    c = _imp("cfd")
    if not c.foam_available():
        raise Blocked("no OpenFOAM at /opt/openfoam13")
    r = c.poiseuille()
    if "error" in r:
        raise NoTool(r["error"])
    return [("channel_exceeds_entry_length",
             1.0 if r["length_over_entry"] >= 3.0 else 0.0, 1.0),
            ("outlet_profile_is_developed",
             1.0 if r["profile_max_error_percent"] < 3.0 else 0.0, 1.0),
            ("mass_flow_preserved_within_2pc",
             1.0 if r["mean_velocity_error_percent"] < 2.0 else 0.0, 1.0),
            ("turbulent_case_is_refused",
             1.0 if "error" in c.poiseuille(u_mean=50.0, h=0.1, nu=1e-6) else 0.0,
             1.0)]


# =========================================================================== #
# 7 - MATERIALS & THERMODYNAMICS
# =========================================================================== #
@task(61, "Al-Cu equilibrium from an assessed database", "pycalphad", 2,
      "phase_diagram.equilibrium", 0.75)
def t61():
    pd = _imp("phase_diagram")
    try:
        pd.find_database(["AL", "CU"])
    except KeyError as e:
        raise NoTool(str(e)[:200])
    # 700 K, 20 at% Cu: below the melting range, so a solid assemblage
    eq = pd.equilibrium(["AL", "CU"], 700.0, 0.20)
    return [("mass_balance_residual", eq["mass_balance_residual"], 0.0),
            ("at_least_one_phase", 1.0 if eq["phase_count"] >= 1 else 0.0, 1.0),
            ("phase_rule_applied",
             float(eq["degrees_of_freedom"]), float(3 - eq["phase_count"]))]


@task(62, "Fe-C eutectoid at ~727 C, 0.76 wt% C", "pycalphad", 2, "-", 0.20)
def t62():
    pd = _imp("phase_diagram")
    try:
        pd.find_database(["FE", "C"])
    except KeyError as e:
        raise NoTool("no assessed Fe-C database on this machine. " + str(e)[:180])
    src = pd.find_database(["FE", "C"])
    eq = pd.equilibrium(["FE", "C"], 1000.0, 0.03)
    db = pd._pycalphad().Database(src["path"])
    act = pd.active_phases(db, ["FE", "C"])
    return [("mass_balance_residual", eq["mass_balance_residual"], 0.0),
            ("chose_a_dedicated_binary_assessment",
             1.0 if len(src["extra_elements"]) <= 1 else 0.0, 1.0),
            ("no_unbuildable_phases_in_it",
             float(len(pd.unbuildable_phases(db, ["FE", "C"]))), 0.0),
            ("austenite_ferrite_and_a_carbon_phase_present",
             1.0 if (any("FCC" in p for p in act) and any("BCC" in p for p in act)
                     and any("CEMENTITE" in p or "GRAPHITE" in p for p in act))
             else 0.0, 1.0),
            ("phase_rule_applied", float(eq["degrees_of_freedom"]),
             float(3 - eq["phase_count"]))]


@task(63, "Lever rule at a tie-line", "analytic", 1, "phase_diagram.lever_rule", 0.95)
def t63():
    pd = _imp("phase_diagram")
    lr = pd.lever_rule(0.3, 0.1, 0.6)
    return [("f_beta", lr["f_beta"], 0.4), ("f_alpha", lr["f_alpha"], 0.6),
            ("fractions_sum_to_1", lr["sum"], 1.0),
            ("mass_balance", lr["mass_balance_check"], 0.3)]


@task(64, "Gibbs energy minimisation for a binary", "pycalphad", 2,
      "phase_diagram.equilibrium + scan_temperature", 0.78)
def t64():
    pd = _imp("phase_diagram")
    sysx = next((s for s in pd.available_systems()
                 if "error" not in s and {"AL", "NI"} <= set(s["elements"])), None)
    if sysx is None:
        sysx = next((s for s in pd.available_systems()
                     if "error" not in s and len(s["elements"]) >= 2), None)
    if sysx is None:
        raise NoTool("no loadable assessed database on this machine")
    els = ["AL", "NI"] if {"AL", "NI"} <= set(sysx["elements"]) else sysx["elements"][:2]
    eq = pd.equilibrium(els, 1200.0, 0.4, db_path=sysx["path"])
    sc = pd.scan_temperature(els, 0.4, 800.0, 1800.0, steps=8, db_path=sysx["path"])
    return [("mass_balance_residual", eq["mass_balance_residual"], 0.0),
            ("equilibrium_returns_phases",
             1.0 if eq["phase_count"] >= 1 else 0.0, 1.0),
            ("assemblage_changes_with_T",
             1.0 if sc["transition_count"] >= 1 else 0.0, 1.0),
            ("database_named_in_result",
             1.0 if eq["database"] else 0.0, 1.0)]


@task(65, "Thermal expansion of a 1 m Al bar over 100 K", "analytic", 1,
      "fatigue.alpha + mechanics.thermal_expansion", 0.93)
def t65():
    f, m = _imp("fatigue"), _imp("mechanics")
    a = f.alpha("AL_6061_T6")
    r = m.thermal_expansion(a, 1000.0, 100.0)
    return [("alpha_per_K", a, 23.6e-6), ("dL_mm", r["dL"], a * 1000.0 * 100.0),
            ("strain", r["strain"], a * 100.0)]


@task(66, "Basquin S-N fatigue life", "analytic", 1, "fatigue.sn_life", 0.93)
def t66():
    f = _imp("fatigue")
    p = f.props("AISI_1018_steel")
    r = f.basquin_life(300.0, p["sigma_f"], p["b"])
    return [("N_cycles", r["N_cycles"],
             0.5 * (300.0 / p["sigma_f"]) ** (1.0 / p["b"])),
            ("reversals_are_2N", r["reversals_2N"] / r["N_cycles"], 2.0),
            ("roundtrip_stress_MPa",
             f.basquin_stress(r["N_cycles"], p["sigma_f"], p["b"]), 300.0)]


@task(67, "Critical crack length from K_IC", "analytic", 1,
      "fatigue.critical_crack + stress_intensity", 0.93)
def t67():
    f = _imp("fatigue")
    K = f.stress_intensity(100.0, 5.0)
    ac = f.critical_crack(100.0, 54.0)
    return [("K_MPa_sqrt_m", K["K_MPa_sqrt_m"], 100.0 * math.sqrt(math.pi * 0.005)),
            ("a_critical_mm", ac["a_critical_mm"],
             (54.0 / 100.0) ** 2 / math.pi * 1000.0),
            ("inverse_consistency",
             f.stress_intensity(100.0, ac["a_critical_mm"])["K_MPa_sqrt_m"], 54.0)]


@task(68, "Rule-of-mixtures density and modulus", "analytic", 1,
      "fatigue.rule_of_mixtures + composite", 0.93)
def t68():
    f = _imp("fatigue")
    c = f.composite([0.6, 0.4], [230000.0, 3500.0], [1800.0, 1200.0])
    return [("E_voigt_MPa", c["E_voigt_upper_MPa"], 0.6 * 230000 + 0.4 * 3500),
            ("E_reuss_MPa", c["E_reuss_lower_MPa"],
             1.0 / (0.6 / 230000 + 0.4 / 3500)),
            ("density", c["density_kg_m3"], 0.6 * 1800 + 0.4 * 1200)]


# =========================================================================== #
# 8 - CIRCUITS
# =========================================================================== #
@task(69, "RC low-pass: fc and the -3 dB point via ngspice AC sweep", "ngspice", 2,
      "circuits.rc_lowpass_fc + ngspice_ac_mag", 0.92)
def t69():
    c = _imp("circuits")
    fc = c.rc_lowpass_fc(1000.0, 1e-6)
    want = 1.0 / (2 * math.pi * 1000.0 * 1e-6)
    out = [("fc_Hz", fc, want)]
    if c.ngspice_available():
        mag = c.ngspice_ac_mag(c.rc_lowpass(1000.0, 1e-6, 1.0), fc)
        out.append(("mag_at_fc", mag, 1.0 / math.sqrt(2.0)))
    else:
        raise NoTool("ngspice_available() is False")
    return out


@task(70, "Series RLC: resonance and Q", "ngspice", 2,
      "circuits.series_rlc_f0 + series_rlc_Q", 0.92)
def t70():
    c = _imp("circuits")
    f0 = c.series_rlc_f0(1e-3, 1e-6)
    Q = c.series_rlc_Q(10.0, 1e-3, 1e-6)
    return [("f0_Hz", f0, 1.0 / (2 * math.pi * math.sqrt(1e-3 * 1e-6))),
            ("Q", Q, (1.0 / 10.0) * math.sqrt(1e-3 / 1e-6))]


@task(71, "RC step response: extract tau from a transient", "ngspice", 2,
      "circuits_ext.rc_step + spice_rc_step", 0.90)
def t71():
    ce = _imp("circuits_ext")
    R, C = 1000.0, 1e-6
    r = ce.rc_step(R, C, 5.0, R * C)
    out = [("tau_s", r["tau_s"], R * C),
           ("v_at_1tau", r["v"], 5.0 * (1 - math.exp(-1))),
           ("waveform_samples", float(len(ce.rc_waveform(R, C, 5.0, 5e-3, n=20))), 21.0)]
    if ce.ngspice_available():
        sim = ce.spice_rc_step(R, C, 5.0, 2e-3)
        if sim:
            out.append(("spice_agrees_V", sim["v"], ce.rc_step(R, C, 5.0, 2e-3)["v"]))
    return out


@task(72, "Inverting op-amp gain -Rf/Rin", "ngspice", 2,
      "circuits_ext.opamp_inverting", 0.95)
def t72():
    ce = _imp("circuits_ext")
    r = ce.opamp_inverting(1000.0, 10000.0, 0.5)
    sat = ce.opamp_inverting(1000.0, 100000.0, 0.5, vsupply=12.0)
    return [("gain", r["gain"], -10.0), ("v_out_V", r["v_out"], -5.0),
            ("clips_at_rail_V", abs(sat["v_out"]), 12.0)]


@task(73, "Half-wave rectifier: waveform and mean", "ngspice", 2,
      "circuits_ext.half_wave_rectifier", 0.93)
def t73():
    ce = _imp("circuits_ext")
    r = ce.half_wave_rectifier(10.0, 0.7)
    fw = ce.full_wave_rectifier(10.0, 0.7, bridge=True)
    return [("v_dc_V", r["v_dc"], 9.3 / math.pi),
            ("v_rms_V", r["v_rms"], 9.3 / 2.0),
            ("form_factor", r["form_factor"], math.pi / 2.0),
            ("fullwave_dc_V", fw["v_dc"], 2.0 * 8.6 / math.pi)]


@task(74, "Common-emitter BJT bias point", "ngspice", 2,
      "circuits_ext.bjt_common_emitter", 0.90)
def t74():
    ce = _imp("circuits_ext")
    q = ce.bjt_common_emitter(12.0, 47000.0, 10000.0, 3300.0, 1000.0, beta=100.0)
    if "error" in q:
        raise NoTool(q["error"])
    return [("forward_active", 1.0 if q["region"] == "forward active" else 0.0, 1.0),
            ("KVL_residual_V",
             12.0 - (q["I_c_A"] * 3300.0 + q["V_ce"] + q["I_e_A"] * 1000.0), 0.0),
            ("Ie_minus_Ic_minus_Ib", q["I_e_A"] - q["I_c_A"] - q["I_b_A"], 0.0),
            ("gm_S", q["gm_S"], q["I_c_A"] / 0.025852)]


@task(75, "Voltage divider under load: Thevenin equivalent", "analytic", 1,
      "circuits_ext.thevenin_divider", 0.95)
def t75():
    ce = _imp("circuits_ext")
    r = ce.thevenin_divider(5.0, 1000.0, 2000.0, r_load=1000.0)
    return [("V_th", r["V_th"], 5.0 * 2000.0 / 3000.0),
            ("R_th", r["R_th"], 1000.0 * 2000.0 / 3000.0),
            ("V_loaded", r["V_loaded"],
             (5.0 * 2 / 3) * 1000.0 / (1000.0 * 2000.0 / 3000.0 + 1000.0))]


# =========================================================================== #
# 9 - MULTIBODY DYNAMICS
# =========================================================================== #
def _mb():
    m = _imp("multibody")
    if not m.chrono_available():
        raise Blocked("PyChrono environment absent - expected at "
                      "~/miniconda3/envs/chrono")
    return m


@task(76, "Simple pendulum period vs 2 pi sqrt(L/g)", "PyChrono", 2,
      "multibody.pendulum", 0.78)
def t76():
    m = _mb()
    p = m.pendulum(length=1.0, theta0_deg=5.0)
    if "error" in p or p.get("period_simulated_s") is None:
        raise NoTool(p.get("error", "no period measured"))
    p4 = m.pendulum(length=4.0, theta0_deg=5.0, t_end=9.0)
    out = [("period_s", p["period_simulated_s"], p["period_analytic_s"]),
           ("within_1_percent", 1.0 if p["error_percent"] < 1.0 else 0.0, 1.0)]
    if p4.get("period_simulated_s"):
        out.append(("quadruple_length_doubles_period",
                    p4["period_simulated_s"] / p["period_simulated_s"], 2.0))
    return out


@task(77, "Spring-mass-damper: damped period and damping ratio", "PyChrono", 2,
      "multibody.spring_mass_damper", 0.78)
def t77():
    m = _mb()
    s = m.spring_mass_damper()
    if "error" in s or not s.get("damped_period_simulated_s"):
        raise NoTool(s.get("error", "no peaks detected"))
    out = [("damped_period_s", s["damped_period_simulated_s"],
            s["damped_period_analytic_s"]),
           ("omega_n_rad_s", s["omega_n_rad_s"], math.sqrt(100.0 / 1.0))]
    if s.get("zeta_measured"):
        out.append(("zeta_from_log_decrement", s["zeta_measured"],
                    s["zeta_analytic"]))
    return out


@task(78, "Free acceleration on an incline is g sin(theta)", "PyChrono", 2,
      "multibody.rolling_cylinder", 0.78)
def t78():
    m = _mb()
    r = m.rolling_cylinder(incline_deg=20.0)
    if "error" in r:
        raise NoTool(r["error"])
    return [("a_simulated", r["a_simulated_free"], r["a_sliding_analytic"]),
            ("within_2_percent",
             1.0 if r["free_body_error_percent"] < 2.0 else 0.0, 1.0)]


@task(79, "Rolling cylinder: a = 2/3 g sin(theta)", "PyChrono", 2,
      "multibody.rolling_cylinder", 0.85)
def t79():
    m = _mb()
    r = m.rolling_cylinder(incline_deg=20.0)
    if "error" in r:
        raise NoTool(r["error"])
    r2 = m.rolling_cylinder(incline_deg=20.0, mass=10.0, radius=0.5)
    return [("rolling_fraction_is_two_thirds", r["rolling_fraction"], 2.0 / 3.0),
            ("independent_of_mass_and_radius",
             r2["rolling_fraction"], r["rolling_fraction"])]


@task(80, "Double pendulum: energy conservation in a chaotic system", "PyChrono", 2,
      "multibody.double_pendulum", 0.80)
def t80():
    m = _mb()
    d = m.double_pendulum(t_end=2.0)
    if "error" in d:
        raise NoTool(d["error"])
    return [("energy_drift_under_2pc",
             1.0 if d["drift_percent"] < 2.0 else 0.0, 1.0),
            ("energy_band_under_5pc",
             1.0 if d["band_percent"] < 5.0 else 0.0, 1.0),
            ("E_final_equals_E_initial", d["E_final_J"], d["E_initial_J"])]


@task(81, "Constraint fidelity: the pendulum stays on its arc", "PyChrono", 2,
      "multibody.pendulum", 0.78)
def t81():
    m = _mb()
    p = m.pendulum(length=1.0, theta0_deg=30.0, t_end=6.0)
    if "error" in p or p.get("period_simulated_s") is None:
        raise NoTool(p.get("error", "no period measured"))
    return [("swing_symmetric_about_vertical",
             1.0 if p["amplitude_symmetric"] else 0.0, 1.0),
            ("multiple_full_cycles_measured",
             1.0 if p["crossings"] >= 2 else 0.0, 1.0),
            ("period_lengthens_at_30_deg_as_theory_requires",
             1.0 if 0.0 < p["error_percent"] < 6.0 else 0.0, 1.0)]


# =========================================================================== #
# 10 - MULTIPHYSICS COUPLING
# =========================================================================== #
@task(82, "Electro-thermal: Joule heating to steady temperature rise", "coupled", 2,
      "multiphysics.joule_heating_coupled", 0.90)
def t82():
    mp = _imp("multiphysics")
    one = mp.joule_heating(10.0, 0.5, 2.0)
    two = mp.joule_heating_coupled(10.0, 0.5, 2.0)
    run = mp.joule_heating_coupled(10.0, 0.5, 6.0)
    return [("P_W_one_way", one["P_W"], 100.0 * 0.5),
            ("dT_K_one_way", one["dT_K"], 100.0),
            ("iterated_equals_closed_form", two["T_celsius"],
             two["T_exact_closed_form"]),
            ("runaway_detected_and_refused",
             1.0 if (run["runaway"] and run["T_celsius"] is None) else 0.0, 1.0)]


@task(83, "Thermo-mechanical: constrained bar, sigma = E alpha dT vs yield",
      "coupled", 1, "multiphysics.thermal_stress_check", 0.93)
def t83():
    mp, f = _imp("multiphysics"), _imp("fatigue")
    r = mp.thermal_stress_check("AISI_1018_steel", 80.0)
    E = f.props("AISI_1018_steel")["E"]
    a = f.alpha("AISI_1018_steel")
    return [("sigma_MPa", r["magnitude_MPa"], E * a * 80.0),
            ("fos", r["fos"], 370.0 / (E * a * 80.0)),
            ("is_compression", 1.0 if r["sense"] == "compression" else 0.0, 1.0)]


@task(84, "One-way FSI: pressure load on a clamped plate", "coupled", 2,
      "multiphysics.wind_on_plate", 0.90)
def t84():
    mp = _imp("multiphysics")
    r = mp.wind_on_plate(40.0, 500.0, 4.0)
    q = 0.5 * 1.225 * 40.0 ** 2
    p = q * 1.2 / 1e6
    D = 210000.0 * 4.0 ** 3 / (12 * (1 - 0.09))
    return [("dynamic_pressure_Pa", r["dynamic_pressure_Pa"], q),
            ("w_max_mm", r["w_max_mm"], p * 500.0 ** 4 / (64 * D)),
            ("sigma_edge_MPa", r["sigma_edge_MPa"],
             3 * p * 500.0 ** 2 / (4 * 4.0 ** 2))]


@task(85, "Magneto-structural: Lorentz force on a beam", "coupled", 2,
      "multiphysics.lorentz_beam_deflection", 0.90)
def t85():
    mp, em = _imp("multiphysics"), _imp("emag")
    r = mp.lorentz_beam_deflection(20000.0, 20000.0, 100.0, 1000.0, 10.0, 50.0)
    f = em.force_parallel_wires(20000.0, 20000.0, 0.1)["F_per_L_N_per_m"]
    fs = mp.fault_force_scaling(200.0, 20000.0, 100.0)
    return [("force_N_per_m", r["force_per_metre_N_m"], f),
            ("udl_N_per_mm", r["udl_N_per_mm"], f / 1000.0),
            ("fault_ratio_is_current_ratio_squared", fs["ratio"], 100.0 ** 2)]


@task(86, "Heat exchanger effectiveness-NTU", "analytic", 1,
      "thermal.heat_exchanger + lmtd", 0.93)
def t86():
    th = _imp("thermal")
    hx = th.heat_exchanger(5000.0, 4000.0, 6000.0, 400.0, 300.0, "counter")
    back = th.lmtd(400.0, hx["T_hot_out_K"], 300.0, hx["T_cold_out_K"], "counter")
    return [("q_W_eps_NTU", hx["q_W"], 5000.0 * back["LMTD_K"]),
            ("NTU", hx["NTU"], 5000.0 / 4000.0),
            ("Cr", hx["Cr"], 4000.0 / 6000.0),
            ("energy_balance_hot_W", 4000.0 * (400.0 - hx["T_hot_out_K"]), hx["q_W"])]


@task(87, "Coupled shaft: torsion + temperature knockdown + fatigue life",
      "coupled", 2, "multiphysics.shaft_life", 0.85)
def t87():
    mp, m = _imp("multiphysics"), _imp("mechanics")
    r = mp.shaft_life("AISI_1018_steel", torque_Nmm=200e3, moment_Nmm=150e3,
                      diameter_mm=40.0)
    if "error" in r:
        raise NoTool(r["error"])
    cbt = m.combined_bending_torsion(150e3, 200e3, 40.0)
    hot = mp.shaft_life("AISI_1018_steel", 200e3, 150e3, 40.0, T_service=500.0)
    return [("sigma_bending_alternating_MPa", r["sigma_bending_alternating_MPa"],
             cbt["sigma_bending_MPa"]),
            ("tau_torsion_steady_MPa", r["tau_torsion_steady_MPa"],
             cbt["tau_torsion_MPa"]),
            ("Se_derated_below_lab",
             1.0 if r["Se_corrected_MPa"] < r["Se_lab_MPa"] else 0.0, 1.0),
            ("hot_yield_lower",
             1.0 if hot["fy_used_MPa"] < r["fy_used_MPa"] else 0.0, 1.0)]


@task(88, "Busbar chain: current -> Joule -> temperature -> expansion stress",
      "coupled", 3, "multiphysics.busbar_chain", 0.85)
def t88():
    mp, f = _imp("multiphysics"), _imp("fatigue")
    r = mp.busbar_chain(2000.0, 1000.0, 100.0, 10.0, R_thermal=0.05)
    if r.get("runaway"):
        raise NoTool("chain diverged at the electro-thermal link")
    a = f.alpha("copper_C11000")
    free = mp.busbar_chain(2000.0, 1000.0, 100.0, 10.0, 0.05, restraint=0.0)
    return [("free_expansion_mm", r["free_expansion_mm"], a * 1000.0 * r["dT_K"]),
            ("current_density_A_per_mm2", r["current_density_A_per_mm2"], 2.0),
            ("hot_R_exceeds_cold",
             1.0 if r["R_hot_ohm"] > r["R_20_ohm"] else 0.0, 1.0),
            ("unrestrained_has_no_stress", free["sigma_magnitude_MPa"], 0.0),
            ("runaway_case_refused",
             1.0 if mp.busbar_chain(6000.0, 1000.0, 100.0, 10.0,
                                    R_thermal=0.5).get("runaway") else 0.0, 1.0)]


# =========================================================================== #
# 11 - VERIFICATION, CONSENSUS & CALIBRATION
# =========================================================================== #
@task(89, "derive_verified on 10 known-answer problems: pass rate and spread",
      "derive_verified", 1, "derive_offline.run_battery", 0.80)
def t89():
    do = _imp("derive_offline")
    r = do.run_battery(quiet=True)
    adv = do.adversarial(0)
    return [("problems_run", float(r["problems"]), 10.0),
            ("pass_rate", r["pass_rate"], 1.0),
            ("worst_error_under_0.1pc",
             1.0 if r["worst_relative_error"] < 1e-3 else 0.0, 1.0),
            ("methods_are_independent_nonzero_spread",
             1.0 if r["worst_numeric_spread"] > 0 else 0.0, 1.0),
            ("gamed_minority_outvoted", 1.0 if adv["outvoted"] else 0.0, 1.0)]


@task(90, "FEA<->analytic cross-check harness over 5 sections", "CalculiX", 2,
      "fea.cross_check_sections", 0.85)
def t90():
    if not shutil.which("ccx"):
        raise Blocked("ccx not on PATH")
    f = _imp("fea")
    r = f.cross_check_sections(1000.0, 500.0,
                               sections=((20.0, 40.0), (30.0, 50.0), (25.0, 25.0),
                                         (40.0, 20.0), (15.0, 60.0)))
    if "error" in r:
        raise NoTool(r["error"])
    return [("all_five_sections_agree", 1.0 if r["all_agreed"] else 0.0, 1.0),
            ("worst_disagreement_under_5pc",
             1.0 if r["worst_disagreement_percent"] < 5.0 else 0.0, 1.0),
            ("sections_checked", float(len(r["sections"])), 5.0)]


@task(91, "Unit-gate adversarial: right number, wrong unit must be rejected",
      "derive_verified", 1, "units_gate", 0.85)
def t91():
    ug = _imp("units_gate")
    if not hasattr(ug, "has_dimension"):
        raise NoTool(f"units_gate has no has_dimension "
                     f"({', '.join(n for n in dir(ug) if not n.startswith('_'))[:80]})")
    good = ug.has_dimension(100.0, "kN", "N")          # force vs force -> accept
    bad = ug.has_dimension(100.0, "kN", "N*m")         # force vs moment -> reject
    worse = ug.has_dimension(5.0, "m", "mm")           # same dim, diff unit -> accept
    return [("accepts_correct_dimension", 1.0 if good else 0.0, 1.0),
            ("REJECTS_wrong_dimension", 0.0 if bad else 1.0, 1.0),
            ("accepts_same_dim_other_unit", 1.0 if worse else 0.0, 1.0)]


@task(92, "Consensus adversarial: a gamed minority must be outvoted",
      "derive_verified", 1, "consensus", 0.80)
def t92():
    cs = _imp("consensus")
    # The public entry point solve_consensus() needs a live model backend, so the
    # adversarial injection targets the clustering logic it delegates to. Runs are
    # (value, unit) pairs, exactly as solve_consensus assembles them.
    gamed = [(1.9531, "mm"), (1.9530, "mm"), (1.9532, "mm"), (99.0, "mm")]
    r = cs._cluster(gamed, 1e-3)
    # a minority in a DIFFERENT dimension must never join the cluster either
    dim = [(1.9531, "mm"), (1.9530, "mm"), (1.9531, "kg")]
    r2 = cs._cluster(dim, 1e-3)
    # and 12.566 m^2 vs 12.566 ft^2 are numerically identical, physically not
    unitcon = cs._cluster([(12.566, "m**2"), (12.566, "ft**2")], 1e-3)
    return [("majority_agreed", 1.0 if r["agreed"] else 0.0, 1.0),
            ("outlier_excluded", float(r["n_agree"]), 3.0),
            ("agreed_value_is_majority", r["value"], 1.9531),
            ("cross_dimension_not_counted", float(r2["n_agree"]), 2.0),
            ("same_number_different_unit_rejected",
             0.0 if unitcon["agreed"] else 1.0, 1.0)]


@task(93, "Receipt tamper test: a mutated field must break the hash", "meta", 1,
      "receipt", 0.85)
def t93():
    rc = _imp("receipt")
    r = rc.make_receipt(
        question="cantilever tip deflection, P=1000 N, L=500 mm",
        method="Euler-Bernoulli PL^3/3EI",
        inputs={"P_N": 1000.0, "L_mm": 500.0, "E_MPa": 200000.0, "I_mm4": 106666.67},
        result={"value": 1.953125, "unit": "mm"},
        checks=[{"name": "closed form", "passed": True, "detail": "exact"}])
    # NB verify_receipt returns a verdict object; read ["ok"] rather than trusting
    # its truthiness. (The first run of this battery got that wrong and scored a
    # false FAIL against EngineAI - the harness was the bug, not the receipt.)
    clean = rc.verify_receipt(r)["ok"]
    # mutate the ANSWER and nothing else - the hash must notice
    t1 = json.loads(json.dumps(r))
    t1["result"]["value"] = 9.999
    caught_result = not rc.verify_receipt(t1)["ok"]
    # mutate an INPUT - a receipt that only covered the answer would miss this
    t2 = json.loads(json.dumps(r))
    t2["inputs"]["L_mm"] = 5000.0
    caught_input = not rc.verify_receipt(t2)["ok"]
    # flip a check from passed to failed - the classic "claim success" tamper
    t3 = json.loads(json.dumps(r))
    t3["checks"][0]["passed"] = False
    caught_check = not rc.verify_receipt(t3)["ok"]
    falsy = not rc.verify_receipt(t1)      # boolean reading must agree with ["ok"]
    return [("clean_receipt_verifies", 1.0 if clean else 0.0, 1.0),
            ("TAMPERED_RESULT_DETECTED", 1.0 if caught_result else 0.0, 1.0),
            ("TAMPERED_INPUT_DETECTED", 1.0 if caught_input else 0.0, 1.0),
            ("TAMPERED_CHECK_DETECTED", 1.0 if caught_check else 0.0, 1.0),
            ("verdict_is_falsy_when_forged", 1.0 if falsy else 0.0, 1.0)]


# task 94 (calibration/ECE) is computed from this run's own log - see main()


@task(95, "Method diversity: same beam by closed form and by FEA", "coupled", 2,
      "fea.cross_check", 0.85)
def t95():
    if not shutil.which("ccx"):
        raise Blocked("ccx not on PATH")
    f = _imp("fea")
    r = f.cross_check(1000.0, 500.0, 20.0, 40.0)
    if r.get("methods_compared", 0) < 2:
        raise NoTool(r.get("reason", "FEA unavailable"))
    tight = f.cross_check(1000.0, 500.0, 20.0, 40.0, tolerance=1e-9)
    return [("two_independent_methods", float(r["methods_compared"]), 2.0),
            ("they_agree", 1.0 if r["agreed"] else 0.0, 1.0),
            ("disagreement_under_5pc",
             1.0 if r["disagreement_vs_timoshenko_percent"] < 5.0 else 0.0, 1.0),
            ("the_gate_CAN_fail", 0.0 if tight["agreed"] else 1.0, 1.0)]


# =========================================================================== #
# 12 & 13 - PROJECT TESTBEDS
# =========================================================================== #
@task(96, "PHOENIX unit cell: relative density vs strut radius", "PHOENIX", 2,
      "lattice.relative_density + measure_cell", 0.85)
def t96():
    la = _imp("lattice")
    rd = la.relative_density("bcc", 10.0, 0.8)
    mc = la.measure_cell("bcc", 10.0, 0.8)
    want = 8 * math.pi * 0.8 ** 2 * (math.sqrt(3) / 2 * 10.0) / 1000.0
    return [("analytic_relative_density", rd["relative_density_uncorrected"], want),
            ("scales_as_r_squared",
             la.relative_density("bcc", 10.0, 1.6)["relative_density_uncorrected"]
             / rd["relative_density_uncorrected"], 4.0),
            ("CAD_measured_within_20pc_of_formula",
             1.0 if abs(mc["relative_density_measured"]
                        - mc["relative_density_uncorrected"])
             / mc["relative_density_uncorrected"] < 0.20 else 0.0, 1.0),
            ("radius_inverts_density",
             la.relative_density("bcc", 10.0,
                                 la.strut_radius_for_density("bcc", 10.0, 0.12))
             ["relative_density"], 0.12)]


@task(97, "PHOENIX lattice stiffness vs Gibson-Ashby scaling", "PHOENIX", 3,
      "lattice.gibson_ashby + maxwell_number", 0.85)
def t97():
    la = _imp("lattice")
    import numpy as np
    E_s, sy = 113800.0, 880.0
    out = []
    for topo, want in (("bcc", 2.0), ("octet", 1.0)):
        rhos = [0.02, 0.05, 0.10, 0.20]
        Es = [la.gibson_ashby(topo, r, E_s, sy)["E_effective_MPa"] for r in rhos]
        slope = float(np.polyfit(np.log(rhos), np.log(Es), 1)[0])
        out.append((f"{topo}_fitted_exponent", slope, want))
    mx = la.maxwell_number("bcc")
    out.append(("maxwell_predicts_bending_for_bcc",
                1.0 if mx["mechanism"] else 0.0, 1.0))
    out.append(("octet_stiffer_than_bcc_at_10pc",
                1.0 if la.gibson_ashby("octet", 0.10, E_s, sy)["E_effective_MPa"]
                > 3 * la.gibson_ashby("bcc", 0.10, E_s, sy)["E_effective_MPa"]
                else 0.0, 1.0))
    return out


@task(98, "PHOENIX strut buckling vs yield failure mode", "PHOENIX", 2,
      "lattice.failure_mode + transition_density", 0.85)
def t98():
    la = _imp("lattice")
    E_s, sy = 113800.0, 880.0
    slim = la.failure_mode("bcc", 10.0, 0.15, E_s, sy)
    stout = la.failure_mode("bcc", 10.0, 2.0, E_s, sy)
    tr = la.transition_density("bcc", 10.0, E_s, sy)
    return [("slender_strut_buckles",
             1.0 if slim["governing_mode"] == "buckling" else 0.0, 1.0),
            ("stocky_strut_yields",
             1.0 if stout["governing_mode"] == "yield" else 0.0, 1.0),
            ("transition_density_found",
             1.0 if (tr["transition_relative_density"] or 0) > 0 else 0.0, 1.0),
            ("stronger_alloy_does_not_help_a_buckling_strut",
             la.failure_mode("bcc", 10.0, 0.15, E_s, sy * 2)["sigma_euler_MPa"],
             slim["sigma_euler_MPa"])]


@task(99, "CRFCE coil field map and REBCO Ic(B,theta) margin", "CRFCE", 3,
      "superconductor.load_line", 0.80)
def t99():
    sc = _imp("superconductor")
    ll = sc.load_line(NI=50000.0, turns=200)
    return [("peak_field_exceeds_bore",
             1.0 if ll["peak_over_bore"] > 1.0 else 0.0, 1.0),
            ("Ic_at_peak_below_Ic_at_bore",
             1.0 if ll["Ic_at_peak_field_A"] < ll["Ic_if_bore_field_assumed_A"]
             else 0.0, 1.0),
            ("perpendicular_field_is_worst",
             1.0 if sc.critical_current(0.5, 0.0)["Ic_A"]
             < sc.critical_current(0.5, 90.0)["Ic_A"] else 0.0, 1.0),
            ("margin_consistent_with_load_fraction",
             ll["margin_percent"], (1 - ll["load_fraction_I_over_Ic"]) * 100.0)]


@task(100, "CRFCE AC loss (Norris) coupled to cryo temperature margin", "CRFCE", 3,
       "superconductor.norris_strip + cryo_margin + design", 0.80)
def t100():
    sc = _imp("superconductor")
    import numpy as np
    Ic = 400.0
    Fs = [0.005, 0.01, 0.02, 0.05]
    Qs = [sc.norris_strip(f * Ic, Ic)["Q_J_per_cycle_per_m"] for f in Fs]
    slope = float(np.polyfit(np.log(Fs), np.log(Qs), 1)[0])
    d = sc.design()
    cm = d["cryogenics"]
    return [("norris_exponent_is_4", slope, 4.0),
            ("matches_small_signal_limit",
             sc.norris_strip(0.01 * Ic, Ic)["Q_J_per_cycle_per_m"]
             / sc.norris_strip(0.01 * Ic, Ic)["small_signal_limit_J_per_m"], 1.0),
            ("cryo_balance_sums", cm["total_heat_W"],
             sum(cm["contributions_W"].values())),
            ("full_chain_runs", 1.0 if d["ac_loss_total_W"] > 0 else 0.0, 1.0)]


# =========================================================================== #
def run(section=None, quiet=False):
    rows = []
    for t in sorted(TASKS, key=lambda x: x["n"]):
        if section and not _in_section(t["n"], section):
            continue
        row = dict(n=t["n"], title=t["title"], tag=t["tag"], tier=t["tier"],
                   entry=t["entry"], conf=t["conf"])
        t0 = time.time()
        try:
            checks = t["fn"]() or []
            worst, details, ok = 0.0, [], True
            for name, got, want in checks:
                e = rel(got, want)
                worst = max(worst, e)
                ok = ok and e <= TOL
                details.append(f"{name}={got:.6g} vs {want:.6g} ({e*100:.3f}%)")
            row.update(status="PASS" if ok else "FAIL", err=worst,
                       detail="; ".join(details))
        except NoTool as e:
            row.update(status="NO_TOOL", err=None, detail=str(e))
        except Blocked as e:
            row.update(status="BLOCKED", err=None, detail=str(e))
        except Exception as e:
            row.update(status="ERROR", err=None,
                       detail=f"{type(e).__name__}: {e}",
                       tb=traceback.format_exc(limit=3))
        row["ms"] = round((time.time() - t0) * 1000, 1)
        rows.append(row)
        if not quiet:
            m = {"PASS": "PASS   ", "FAIL": "FAIL   ", "NO_TOOL": "NO TOOL",
                 "BLOCKED": "BLOCKED", "ERROR": "ERROR  "}[row["status"]]
            print(f"  {m} {row['n']:>3}  {row['title'][:52]:<52} {row['detail'][:88]}")
    return rows


SECTIONS = {1: range(1, 13), 2: range(13, 21), 3: range(21, 33), 4: range(33, 43),
            5: range(43, 53), 6: range(53, 61), 7: range(61, 69), 8: range(69, 76),
            9: range(76, 82), 10: range(82, 89), 11: range(89, 96),
            12: range(96, 99), 13: range(99, 101)}


def _in_section(n, sec):
    return n in SECTIONS.get(sec, [])


def ece(rows, bins=5):
    """Expected Calibration Error over tasks where EngineAI actually answered.
    NO_TOOL/BLOCKED are excluded: there is no answer to be confident about."""
    pts = [(r["conf"], 1.0 if r["status"] == "PASS" else 0.0)
           for r in rows if r["status"] in ("PASS", "FAIL", "ERROR")]
    if not pts:
        return None, 0, []
    tot, out = len(pts), []
    e = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        b = [p for p in pts if (lo < p[0] <= hi) or (i == 0 and p[0] == 0)]
        if not b:
            continue
        avg_c = sum(p[0] for p in b) / len(b)
        acc = sum(p[1] for p in b) / len(b)
        e += len(b) / tot * abs(avg_c - acc)
        out.append((lo, hi, len(b), avg_c, acc))
    return e, tot, out


def main():
    ap = argparse.ArgumentParser(description="EngineAI 100-task capability battery")
    ap.add_argument("--section", type=int, help="run one section 1-13")
    ap.add_argument("--json", help="write full results here")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    print("=" * 118)
    print("EngineAI 100-task capability battery - EngineAI answers only, no harness fallbacks")
    print("=" * 118)
    rows = run(a.section, a.quiet)

    n = len(rows)
    by = {}
    for r in rows:
        by.setdefault(r["status"], []).append(r)
    print("\n" + "=" * 118)
    print("RESULT")
    print("=" * 118)
    for k in ("PASS", "FAIL", "NO_TOOL", "BLOCKED", "ERROR"):
        v = by.get(k, [])
        if v:
            print(f"  {k:<8} {len(v):>3} / {n}   {sorted(x['n'] for x in v)}")

    answered = len(by.get("PASS", [])) + len(by.get("FAIL", [])) + len(by.get("ERROR", []))
    print(f"\n  EngineAI produced an answer for {answered}/{n} tasks "
          f"({answered/n*100:.0f}%)")
    if answered:
        print(f"  Of those it was correct on {len(by.get('PASS', []))}/{answered} "
              f"({len(by.get('PASS', []))/answered*100:.0f}%)")

    tags = sorted({r["tag"] for r in rows})
    fired = sorted({r["tag"] for r in rows if r["status"] == "PASS"})
    print(f"\n  Capability tags exercised successfully: {len(fired)}/{len(tags)}")
    print(f"    fired : {', '.join(fired)}")
    print(f"    silent: {', '.join(t for t in tags if t not in fired)}")

    for tier in (1, 2, 3):
        tr = [r for r in rows if r["tier"] == tier]
        if tr:
            p = len([r for r in tr if r["status"] == "PASS"])
            print(f"  T{tier}: {p}/{len(tr)} pass ({p/len(tr)*100:.0f}%)")

    e, cnt, binsout = ece(rows)
    print("\n  TASK 94 - CALIBRATION (confidence declared before the run)")
    if e is None:
        print("    no answered tasks, ECE undefined")
    else:
        print(f"    ECE = {e:.3f} over {cnt} answered tasks")
        for lo, hi, k, c, acc in binsout:
            print(f"      conf {lo:.1f}-{hi:.1f}: n={k:<3} mean conf {c:.2f} -> "
                  f"accuracy {acc:.2f}   {'OVERCONFIDENT' if c > acc + 0.05 else 'under' if acc > c + 0.05 else 'ok'}")

    if a.json:
        with open(a.json, "w") as f:
            json.dump(rows, f, indent=2)
        print(f"\n  full log -> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
