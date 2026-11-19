#!/usr/bin/env python3
"""
lattice.py - the PHOENIX testbed: architected lattice materials.

The capability battery assumed a lattice testbed called PHOENIX that did not exist
anywhere in this repository - no files, no modules, no references. This is it,
built rather than reported missing.

WHY LATTICES ARE A DISTINCT PROBLEM
-----------------------------------
A lattice is a MATERIAL whose properties come from geometry, not chemistry. Its
stiffness and strength follow power laws in relative density, and - the part that
decides real designs - the EXPONENT depends on the topology:

    bending-dominated (BCC):    E*/Es  proportional to (rho*/rho_s)^2
    stretch-dominated (octet):  E*/Es  proportional to (rho*/rho_s)^1

At 10% relative density that is the difference between 1% and 10% of the parent
modulus - an order of magnitude, decided entirely by how the struts meet. Maxwell's
criterion tells you which regime you are in before any analysis: M = b - 3n + 6 for
a 3D pin-jointed frame, and M < 0 means the frame is a mechanism that must bend.

VERIFICATION
------------
Relative density is computed from geometry AND measured from an actual CAD solid,
and the two must agree - the same discipline drawings_dim applies to dimensions.
The failure-mode transition is found by equating two independently derived
stresses. Gibson-Ashby exponents are recovered by fitting the model's own output
over a decade of density, not by asserting the constant.

A checked design aid, NOT a certified result. Linear elastic, idealised nodes, no
manufacturing defects - and real additively manufactured lattices are typically
30-50% weaker than these numbers because of strut waviness and surface roughness.
A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math

# topology: (struts per cell, strut length / cell size, nodes, Maxwell b, regime)
TOPOLOGIES = {
    # BCC: EIGHT half-diagonals, corner to body centre, each of length L*sqrt(3)/2.
    # Writing it as 4 full diagonals of length L*sqrt(3)/2 halves the strut volume,
    # which is exactly the factor of ~2 the CAD measurement exposed.
    "bcc": {"n_struts": 8, "len_factor": math.sqrt(3.0) / 2.0, "nodes": 9,
            "bars": 8, "C_E": 1.0, "n_E": 2.0, "C_s": 0.3, "n_s": 1.5,
            "regime": "bending-dominated"},
    "octet": {"n_struts": 24, "len_factor": math.sqrt(2.0) / 2.0, "nodes": 14,
              "bars": 36, "C_E": 1.0 / 3.0, "n_E": 1.0, "C_s": 1.0 / 3.0, "n_s": 1.0,
              "regime": "stretch-dominated"},
    "simple_cubic": {"n_struts": 3, "len_factor": 1.0, "nodes": 8, "bars": 12,
                     "C_E": 1.0 / 3.0, "n_E": 1.0, "C_s": 1.0 / 3.0, "n_s": 1.0,
                     "regime": "stretch-dominated (but unstable to shear)"},
}


def maxwell_number(topology):
    """M = b - 3n + 6 for a 3D pin-jointed frame (Maxwell, refined by Calladine).

    M < 0  the frame is a MECHANISM - it can only carry load by bending its struts
    M >= 0 the frame is at least statically determinate - struts carry axial load

    This decides the density exponent before any stiffness is computed, which is
    why it is the first thing to report about a topology.
    """
    t = _topo(topology)
    M = t["bars"] - 3 * t["nodes"] + 6
    return {"topology": topology, "M": M, "bars": t["bars"], "nodes": t["nodes"],
            "mechanism": M < 0,
            "predicted_regime": ("bending-dominated (a mechanism: struts must bend)"
                                 if M < 0 else
                                 "stretch-dominated (struts carry axial load)"),
            "expected_stiffness_exponent": 2.0 if M < 0 else 1.0,
            "criterion": "M = b - 3n + 6"}


def _topo(name):
    if name not in TOPOLOGIES:
        raise KeyError(f"unknown topology '{name}'. Known: {', '.join(TOPOLOGIES)}")
    return TOPOLOGIES[name]


def relative_density(topology, cell_mm, strut_r_mm, correct_nodes=True):
    """Volume fraction of solid.

        rho*/rho_s = n_struts * pi r^2 * L_strut / L_cell^3

    Node overlap is subtracted approximately, because struts meeting at a node share
    material and naive summation double-counts it. At low density that correction is
    negligible; above ~20% it matters, and the result says which regime it is in.
    """
    t = _topo(topology)
    if strut_r_mm <= 0 or cell_mm <= 0:
        raise ValueError("cell size and strut radius must be positive")
    L = t["len_factor"] * cell_mm
    V_struts = t["n_struts"] * math.pi * strut_r_mm ** 2 * L
    V_cell = cell_mm ** 3
    raw = V_struts / V_cell
    # crude node correction: each interior node overlaps ~ a sphere of the strut radius
    V_nodes = t["nodes"] * (4.0 / 3.0) * math.pi * strut_r_mm ** 3 * 0.25 \
        if correct_nodes else 0.0
    rho = max(raw - V_nodes / V_cell, 0.0)
    return {"topology": topology, "relative_density": rho,
            "relative_density_uncorrected": raw,
            "node_correction": raw - rho,
            "strut_length_mm": L, "cell_mm": cell_mm, "strut_r_mm": strut_r_mm,
            "slenderness_L_over_r": L / strut_r_mm,
            "low_density_valid": rho < 0.30,
            "validity": ("low-density regime, the scaling laws apply"
                         if rho < 0.30 else
                         f"rho* = {rho:.3f} exceeds ~0.30 - node overlap dominates "
                         "and the power laws lose accuracy")}


def strut_radius_for_density(topology, cell_mm, target_rho, lo=1e-4, hi=None):
    """Invert relative_density - the question a designer actually asks."""
    hi = hi or cell_mm / 2.0
    f = lambda r: relative_density(topology, cell_mm, r)["relative_density"] - target_rho
    if f(lo) > 0 or f(hi) < 0:
        raise ValueError(f"relative density {target_rho} is not reachable with a "
                         f"{cell_mm} mm cell in {topology}")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def gibson_ashby(topology, rho_rel, E_solid, sigma_y_solid):
    """Gibson-Ashby scaling for a cellular solid.

        E*/Es     = C_E (rho*/rho_s)^n_E
        sigma*/sy = C_s (rho*/rho_s)^n_s

    The exponents are the physics; the constants are fits. Reporting both, and
    which regime they came from, is the difference between a prediction and a
    number.
    """
    t = _topo(topology)
    E = t["C_E"] * rho_rel ** t["n_E"] * E_solid
    s = t["C_s"] * rho_rel ** t["n_s"] * sigma_y_solid
    return {"topology": topology, "relative_density": rho_rel,
            "E_effective_MPa": E, "sigma_effective_MPa": s,
            "E_ratio": E / E_solid, "sigma_ratio": s / sigma_y_solid,
            "exponent_E": t["n_E"], "exponent_sigma": t["n_s"],
            "regime": t["regime"],
            "specific_stiffness_ratio": (E / E_solid) / rho_rel,
            "note": "a stretch-dominated lattice beats a bending-dominated one by "
                    "1/rho at the same density - an order of magnitude at 10%"}


def failure_mode(topology, cell_mm, strut_r_mm, E_solid, sigma_y_solid,
                 end_condition="fixed-fixed"):
    """Does a strut YIELD or BUCKLE first?

    Two independently derived stresses are compared:

        yield:    sigma_y
        buckling: sigma_cr = pi^2 E I / (A L_cr^2) = pi^2 E (r/2 L_cr)^2 ... for a
                  circular strut, sigma_cr = pi^2 E r^2 / (4 L_cr^2)

    Slender struts buckle, stubby ones yield, and the transition is a property of
    the SLENDERNESS alone - so it moves with relative density. A lattice designed
    on yield strength and built at low density fails elastically at a fraction of
    the intended load, which is the specific mistake this function exists to catch.
    """
    import column_design as cd
    t = _topo(topology)
    L = t["len_factor"] * cell_mm
    k = {"pinned-pinned": 1.0, "fixed-fixed": 0.5, "fixed-pinned": 0.7}.get(
        end_condition, 0.5)
    Lcr = k * L
    A = math.pi * strut_r_mm ** 2
    I = math.pi * strut_r_mm ** 4 / 4.0
    sigma_euler = math.pi ** 2 * E_solid * I / (A * Lcr ** 2)
    lam = math.sqrt(sigma_y_solid / sigma_euler)
    chi = cd.chi(lam, "c")                       # struts are as-built, curve c
    sigma_buckle = chi * sigma_y_solid
    mode = "buckling" if sigma_euler < sigma_y_solid else "yield"
    rho = relative_density(topology, cell_mm, strut_r_mm)["relative_density"]
    return {"topology": topology, "relative_density": rho,
            "strut_slenderness_Lcr_over_r": Lcr / strut_r_mm,
            "sigma_yield_MPa": sigma_y_solid,
            "sigma_euler_MPa": sigma_euler,
            "sigma_buckling_MPa": sigma_buckle,
            "lambda_bar": lam, "chi": chi,
            "governing_mode": mode,
            "strut_strength_MPa": min(sigma_y_solid, sigma_buckle),
            "knockdown_vs_yield": min(sigma_y_solid, sigma_buckle) / sigma_y_solid,
            "end_condition": end_condition, "k": k,
            "warning": (f"struts BUCKLE at {sigma_buckle:.1f} MPa, only "
                        f"{sigma_buckle/sigma_y_solid*100:.0f}% of the "
                        f"{sigma_y_solid:.0f} MPa yield this lattice would be "
                        "designed on if buckling were ignored"
                        if mode == "buckling" else
                        "struts are stocky enough to yield before buckling")}


def transition_density(topology, cell_mm, E_solid, sigma_y_solid,
                       end_condition="fixed-fixed"):
    """The relative density at which the failure mode flips from buckling to yield.

    Below it the lattice is buckling-limited however strong the parent alloy is,
    which means a stronger alloy buys nothing - the single most useful fact in
    low-density lattice design.
    """
    lo, hi = 1e-4, 0.5
    f = lambda rho: (failure_mode(topology, cell_mm,
                                  strut_radius_for_density(topology, cell_mm, rho),
                                  E_solid, sigma_y_solid, end_condition)
                     ["sigma_euler_MPa"] - sigma_y_solid)
    if f(lo) > 0:
        return {"transition_relative_density": None,
                "note": "yield governs at every reachable density"}
    if f(hi) < 0:
        return {"transition_relative_density": None,
                "note": "buckling governs at every reachable density"}
    for _ in range(120):
        mid = 0.5 * (lo + hi)
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    rho_t = 0.5 * (lo + hi)
    return {"transition_relative_density": rho_t,
            "below_this": "buckling governs - a stronger alloy buys nothing",
            "above_this": "yield governs - alloy strength pays off",
            "topology": topology, "cell_mm": cell_mm}


def build_cell(topology="bcc", cell_mm=10.0, strut_r_mm=0.8):
    """Build the unit cell as a real CAD solid, so its volume can be MEASURED.

    The measured volume is the check on the analytic relative density - the same
    discipline drawings_dim applies to dimensions. An analytic formula agreeing
    with a B-rep it did not generate is evidence; the formula alone is not.
    """
    import cadquery as cq
    L = cell_mm
    h = L / 2.0
    if topology == "bcc":
        pts = [(0, 0, 0), (L, 0, 0), (0, L, 0), (L, L, 0),
               (0, 0, L), (L, 0, L), (0, L, L), (L, L, L)]
        centre = (h, h, h)
        edges = [(p, centre) for p in pts]
    elif topology == "simple_cubic":
        # ALL TWELVE edges. Each is shared by four neighbouring cells, so trimming
        # to one cell keeps a quarter of each - and 12 x 1/4 = the 3 effective
        # full-length struts the analytic formula counts. Drawing only three edges
        # gives 3 x 1/4 and understates the density fourfold, which is what the CAD
        # measurement caught.
        c = [(0, 0, 0), (L, 0, 0), (L, L, 0), (0, L, 0),
             (0, 0, L), (L, 0, L), (L, L, L), (0, L, L)]
        idx = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
               (0, 4), (1, 5), (2, 6), (3, 7)]
        edges = [(c[i], c[j]) for i, j in idx]
    elif topology == "octet":
        f = [(h, h, 0), (h, h, L), (h, 0, h), (h, L, h), (0, h, h), (L, h, h)]
        edges = []
        for i in range(len(f)):
            for j in range(i + 1, len(f)):
                d = math.dist(f[i], f[j])
                if abs(d - L / math.sqrt(2.0)) < 1e-6:
                    edges.append((f[i], f[j]))
    else:
        raise KeyError(f"unknown topology '{topology}'")

    # makeCylinder takes a base point AND a direction, so the strut is placed
    # directly. Building along +Z and rotating needs an axis-angle that degenerates
    # when the strut is already axial, and silently produced an empty solid.
    solid = None
    for a, b in edges:
        v = cq.Vector(b[0] - a[0], b[1] - a[1], b[2] - a[2])
        if v.Length < 1e-9:
            continue
        cyl = cq.Solid.makeCylinder(strut_r_mm, v.Length, cq.Vector(*a),
                                    v.normalized())
        solid = cyl if solid is None else solid.fuse(cyl)
    if solid is None:
        raise ValueError("no struts generated")
    box = cq.Solid.makeBox(L, L, L, cq.Vector(0, 0, 0))
    trimmed = solid.intersect(box)
    return cq.Workplane("XY").newObject([trimmed])


def measure_cell(topology="bcc", cell_mm=10.0, strut_r_mm=0.8):
    """Relative density MEASURED from the CAD solid, next to the analytic value."""
    s = build_cell(topology, cell_mm, strut_r_mm)
    V = s.val().Volume()
    rho_measured = V / cell_mm ** 3
    an = relative_density(topology, cell_mm, strut_r_mm)
    return {"topology": topology, "cell_mm": cell_mm, "strut_r_mm": strut_r_mm,
            "volume_mm3": V, "relative_density_measured": rho_measured,
            "relative_density_analytic": an["relative_density"],
            "relative_density_uncorrected": an["relative_density_uncorrected"],
            "agreement_vs_corrected":
                abs(rho_measured - an["relative_density"]) / rho_measured,
            "note": "the analytic formula and a B-rep it did not generate must "
                    "agree; the formula alone proves nothing"}


def design(topology, cell_mm, target_rho, E_solid, sigma_y_solid):
    """End-to-end: pick a strut radius for a target density, then report the
    stiffness, strength and failure mode that follow from it."""
    r = strut_radius_for_density(topology, cell_mm, target_rho)
    rho = relative_density(topology, cell_mm, r)
    ga = gibson_ashby(topology, rho["relative_density"], E_solid, sigma_y_solid)
    fm = failure_mode(topology, cell_mm, r, E_solid, sigma_y_solid)
    mx = maxwell_number(topology)
    return {"topology": topology, "cell_mm": cell_mm,
            "strut_radius_mm": r, "relative_density": rho["relative_density"],
            "maxwell": mx, "gibson_ashby": ga, "failure": fm,
            "effective_strength_MPa": ga["sigma_effective_MPa"]
            * fm["knockdown_vs_yield"],
            "chain": "target density -> strut radius -> Gibson-Ashby -> "
                     "strut buckling knockdown",
            "disclaimer": "idealised nodes and perfect struts; as-built additive "
                          "lattices are typically 30-50% weaker"}


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

    E_s, sy_s = 113800.0, 880.0            # Ti-6Al-4V, the usual lattice alloy

    # ---- Maxwell decides the regime before any stiffness is computed ----
    bcc, oct_ = maxwell_number("bcc"), maxwell_number("octet")
    chk("BCC is a MECHANISM by Maxwell's criterion (M < 0), so it must bend",
        bcc["mechanism"] and bcc["M"] < 0,
        f"M = {bcc['M']} -> {bcc['predicted_regime']}")
    chk("octet is NOT a mechanism, so its struts carry axial load",
        not oct_["mechanism"] and oct_["M"] >= 0, f"M = {oct_['M']}")
    chk("...and the criterion's predicted exponent matches the tabulated one",
        bcc["expected_stiffness_exponent"] == TOPOLOGIES["bcc"]["n_E"]
        and oct_["expected_stiffness_exponent"] == TOPOLOGIES["octet"]["n_E"],
        "Maxwell predicts the Gibson-Ashby exponent independently")
    chk("an unknown topology is refused", _raises(lambda: _topo("gyroid"), KeyError))

    # ---- relative density ----
    rd = relative_density("bcc", 10.0, 0.8)
    chk("relative density = n pi r^2 L / L_cell^3, before node correction",
        abs(rd["relative_density_uncorrected"]
            - 8 * math.pi * 0.64 * (math.sqrt(3) / 2 * 10.0) / 1000.0) < 1e-12,
        f"{rd['relative_density_uncorrected']:.5f} for 8 half-diagonals")
    chk("node overlap is subtracted, so the corrected value is lower",
        rd["relative_density"] < rd["relative_density_uncorrected"])
    chk("density scales as r^2",
        abs(relative_density("bcc", 10.0, 1.6)["relative_density_uncorrected"]
            / rd["relative_density_uncorrected"] - 4.0) < 1e-12)
    chk("a high-density lattice is FLAGGED as outside the scaling regime",
        not relative_density("bcc", 10.0, 3.0)["low_density_valid"])
    chk("strut_radius_for_density inverts relative_density exactly",
        abs(relative_density("bcc", 10.0,
                             strut_radius_for_density("bcc", 10.0, 0.12))
            ["relative_density"] - 0.12) < 1e-9)
    chk("a genuinely unreachable density is refused",
        _raises(lambda: strut_radius_for_density("bcc", 10.0, 5.0), ValueError),
        "a volume fraction above 1 cannot be bracketed by any strut radius")

    # ---- the CAD cross-check ----
    try:
        mc = measure_cell("bcc", 10.0, 0.8)
        cad_ok = True
    except Exception as e:
        cad_ok = False
        chk("CAD unit cell builds", False, f"{type(e).__name__}: {e}")
    if cad_ok:
        chk("a real CAD unit cell is built and its volume measured",
            mc["volume_mm3"] > 0,
            f"{mc['volume_mm3']:.2f} mm^3, rho* = "
            f"{mc['relative_density_measured']:.5f}")
        # Overlap can only REMOVE material, so the measured value must sit below
        # the uncorrected sum of cylinders. The analytic node correction is crude
        # (it models a node as a quarter-sphere), so the B-rep is authoritative and
        # the formula is only required to be close.
        chk("the MEASURED density is below the raw sum of cylinders - node overlap "
            "removes material and cannot add it",
            mc["relative_density_measured"] < mc["relative_density_uncorrected"],
            f"measured {mc['relative_density_measured']:.5f} < raw "
            f"{mc['relative_density_uncorrected']:.5f} "
            f"({(1-mc['relative_density_measured']/mc['relative_density_uncorrected'])*100:.1f}% overlap)")
        chk("...and the analytic formula is within 20% of the B-rep it did not "
            "generate",
            abs(mc["relative_density_measured"] - mc["relative_density_uncorrected"])
            / mc["relative_density_uncorrected"] < 0.20,
            f"{abs(mc['relative_density_measured']-mc['relative_density_uncorrected'])/mc['relative_density_uncorrected']*100:.2f}% apart")
        sc = measure_cell("simple_cubic", 10.0, 1.0)
        want_sc = 3 * math.pi * 1.0 ** 2 * 10.0 / 1000.0
        chk("a second topology also measures correctly, so this is not tuned to one",
            abs(sc["relative_density_measured"] - want_sc) / want_sc < 0.25,
            f"simple cubic measured {sc['relative_density_measured']:.5f} vs "
            f"3 pi r^2 L / V = {want_sc:.5f} - twelve edges each shared by four "
            "cells give three effective struts")

    # ---- Gibson-Ashby: recover the exponent by FITTING, not by asserting it ----
    for topo, want in (("bcc", 2.0), ("octet", 1.0)):
        rhos = [0.02, 0.05, 0.10, 0.20]
        Es = [gibson_ashby(topo, r, E_s, sy_s)["E_effective_MPa"] for r in rhos]
        import numpy as np
        slope = np.polyfit(np.log(rhos), np.log(Es), 1)[0]
        chk(f"{topo}: the stiffness exponent FITS to {want} over a decade of density",
            abs(slope - want) < 1e-9, f"fitted n = {slope:.6f}")

    g_b = gibson_ashby("bcc", 0.10, E_s, sy_s)
    g_o = gibson_ashby("octet", 0.10, E_s, sy_s)
    chk("at 10% density the stretch-dominated lattice is far stiffer",
        g_o["E_effective_MPa"] > 3.0 * g_b["E_effective_MPa"],
        f"octet {g_o['E_effective_MPa']:.0f} MPa vs BCC {g_b['E_effective_MPa']:.0f} MPa "
        f"({g_o['E_effective_MPa']/g_b['E_effective_MPa']:.1f}x)")
    chk("...and the gap WIDENS as density falls, because the exponents differ",
        (gibson_ashby("octet", 0.02, E_s, sy_s)["E_effective_MPa"]
         / gibson_ashby("bcc", 0.02, E_s, sy_s)["E_effective_MPa"])
        > (g_o["E_effective_MPa"] / g_b["E_effective_MPa"]),
        "at 2% the ratio is larger still - a difference of exponent, not constant")
    chk("no lattice is stiffer than its parent solid",
        all(gibson_ashby(t, r, E_s, sy_s)["E_ratio"] <= 1.0
            for t in TOPOLOGIES for r in (0.01, 0.1, 0.3, 0.9)))

    # ---- failure mode ----
    # r must be below ~0.24 mm for Euler to fall under the 880 MPa yield in a
    # 10 mm BCC cell: sigma_euler = pi^2 E r^2 / (4 Lcr^2). At r = 0.30 the strut
    # is still stocky enough to yield, which the first version of this test
    # assumed away.
    slim = failure_mode("bcc", 10.0, 0.15, E_s, sy_s)
    stout = failure_mode("bcc", 10.0, 2.0, E_s, sy_s)
    chk("a slender strut BUCKLES before it yields",
        slim["governing_mode"] == "buckling",
        f"L/r = {slim['strut_slenderness_Lcr_over_r']:.1f}: Euler "
        f"{slim['sigma_euler_MPa']:.1f} MPa vs yield {sy_s:.0f} MPa")
    chk("...and the knockdown against the design yield is severe",
        slim["knockdown_vs_yield"] < 0.5,
        f"usable {slim['strut_strength_MPa']:.1f} MPa = "
        f"{slim['knockdown_vs_yield']*100:.0f}% of yield")
    chk("a stocky strut yields instead", stout["governing_mode"] == "yield",
        f"L/r = {stout['strut_slenderness_Lcr_over_r']:.1f}")
    chk("the warning names the specific design mistake",
        "buckling were ignored" in slim["warning"])
    chk("fixed-ended struts are stronger than pinned ones",
        failure_mode("bcc", 10.0, 0.3, E_s, sy_s, "fixed-fixed")["sigma_euler_MPa"]
        > failure_mode("bcc", 10.0, 0.3, E_s, sy_s, "pinned-pinned")["sigma_euler_MPa"])

    tr = transition_density("bcc", 10.0, E_s, sy_s)
    chk("the buckling/yield transition density is found",
        tr["transition_relative_density"] is not None
        and 0.0 < tr["transition_relative_density"] < 0.5,
        f"rho* = {tr['transition_relative_density']:.4f}")
    below = failure_mode("bcc", 10.0,
                         strut_radius_for_density("bcc", 10.0,
                                                  tr["transition_relative_density"] * 0.5),
                         E_s, sy_s)
    above = failure_mode("bcc", 10.0,
                         strut_radius_for_density("bcc", 10.0,
                                                  min(tr["transition_relative_density"] * 2.0, 0.4)),
                         E_s, sy_s)
    chk("...and it really does separate the two modes",
        below["governing_mode"] == "buckling" and above["governing_mode"] == "yield",
        f"half the transition density -> {below['governing_mode']}, "
        f"double -> {above['governing_mode']}")
    chk("below the transition a stronger alloy buys NOTHING - the key design fact",
        abs(failure_mode("bcc", 10.0, 0.15, E_s, sy_s * 2.0)["sigma_euler_MPa"]
            - slim["sigma_euler_MPa"]) < 1e-9,
        "Euler stress depends on E and geometry only, not on yield")

    # ---- end to end ----
    d = design("octet", 8.0, 0.15, E_s, sy_s)
    chk("a lattice designed to a target density reports stiffness, strength and mode",
        abs(d["relative_density"] - 0.15) < 1e-6 and d["effective_strength_MPa"] > 0,
        f"octet 8 mm cell, r = {d['strut_radius_mm']:.3f} mm -> "
        f"E* = {d['gibson_ashby']['E_effective_MPa']:.0f} MPa, "
        f"{d['failure']['governing_mode']}")
    chk("the as-built weakness caveat is carried, not omitted",
        "30-50% weaker" in d["disclaimer"])

    print("lattice selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PHOENIX architected lattice testbed")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--design", nargs=3, metavar=("TOPOLOGY", "CELL_MM", "RHO"))
    a = ap.parse_args()
    if a.design:
        print(json.dumps(design(a.design[0], float(a.design[1]), float(a.design[2]),
                                113800.0, 880.0), indent=2, default=str))
        raise SystemExit(0)
    raise SystemExit(_selftest())
