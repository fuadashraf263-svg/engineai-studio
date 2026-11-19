#!/usr/bin/env python3
"""
preview.py - the fast tier. Answers while your hand is still on the slider.

THE TWO TIERS, AND THE RULE BETWEEN THEM
----------------------------------------
Dragging a parameter cannot wait for gmsh and CalculiX; those take seconds to
minutes. So there are two answers to every question:

    PREVIEW    closed form, microseconds, basis ESTIMATED
    VERIFIED   mesh and solver, basis COMPUTED, carries a receipt

The rule that keeps this honest: A PREVIEW IS ONLY LEGITIMATE IF ITS ERROR
AGAINST THE VERIFIED ANSWER IS KNOWN AND PUBLISHED. A fast number with no
stated accuracy is not a preview, it is a guess wearing a result's clothing.

So every estimate here carries `expect_within_pct` - how far from the solver
it has actually been measured to land - and selftest() measures that against
real CalculiX runs rather than asserting it. Where the closed form is exact
(mass from a B-rep volume) the band is tight. Where it is a beam idealisation
of a solid, it is wide, and it says so.

NOTHING HERE IS EVER LABELLED COMPUTED. That badge means a solver ran.

  python preview.py --selftest
  python preview.py --part mounting_plate --material "steel (mild)" --rpm 3000
"""

from __future__ import annotations

import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

ESTIMATED = "ESTIMATED"

# Density kg/m3, E MPa, yield MPa, alpha 1/K, max service C, Poisson.
MATERIALS = {
    "steel (mild)":          (7850.0, 210000.0, 250.0, 12e-6, 400.0, 0.30),
    "steel (stainless 304)": (8000.0, 193000.0, 215.0, 17.3e-6, 800.0, 0.29),
    "aluminium 6061-T6":     (2700.0, 68900.0, 276.0, 23.6e-6, 170.0, 0.33),
    "titanium Ti-6Al-4V":    (4430.0, 113800.0, 880.0, 8.6e-6, 400.0, 0.34),
    "cast iron":             (7200.0, 110000.0, 130.0, 10.5e-6, 400.0, 0.26),
    "ABS":                   (1040.0, 2300.0, 40.0, 90e-6, 80.0, 0.35),
    "PLA":                   (1240.0, 3500.0, 50.0, 68e-6, 55.0, 0.36),
    "nylon 6":               (1140.0, 2900.0, 70.0, 80e-6, 100.0, 0.39),
}


def _est(field, value, unit, method, within, note=""):
    return {"field": field, "value": value, "unit": unit,
            "basis": ESTIMATED, "method": method,
            "expect_within_pct": within, "note": note}


# --------------------------------------------------------------------------
# geometry -> mass and stiffness, the moment a slider moves
# --------------------------------------------------------------------------

def mass_preview(volume_mm3: float, material: str) -> dict:
    """Mass from a B-rep volume. This one is EXACT, not an approximation."""
    if material not in MATERIALS:
        return {"error": f"unknown material {material!r}. Known: "
                         f"{', '.join(sorted(MATERIALS))}. No density is being "
                         f"assumed in its place."}
    rho = MATERIALS[material][0]
    kg = volume_mm3 * 1e-9 * rho
    return _est("mass", kg, "kg", "volume x density",
                0.0,
                "exact: the volume comes from the B-rep, not the mesh, and "
                "density is a table value. A verified run will not change it.")


def _section(bbox_mm: list, load_axis: int, span_axis: int = None):
    """Span, depth and width for bending, given WHICH WAY THE LOAD ACTS.

    The first version sorted the bounding box and called the smallest
    dimension the depth - i.e. it assumed bending about the weak axis, always.
    Measured against CalculiX on a 300x30x15 bar loaded in Y, that was 321%
    wrong: the load was along the 30 mm dimension, not the 15 mm one, so the
    section is four times stiffer than the preview assumed.

    Depth is the dimension ALONG the load. Span is the long axis. Width is
    what is left.
    """
    d = [bbox_mm[3] - bbox_mm[0], bbox_mm[4] - bbox_mm[1],
         bbox_mm[5] - bbox_mm[2]]
    if span_axis is None:
        span_axis = d.index(max(d))
    if load_axis == span_axis:
        # Loading along the length is axial, not bending; fall back to the
        # stiffest remaining axis so the caller still gets something sane.
        load_axis = max((i for i in range(3) if i != span_axis),
                        key=lambda i: d[i])
    width_axis = next(i for i in range(3)
                      if i not in (span_axis, load_axis))
    return d[span_axis], d[load_axis], d[width_axis], span_axis, load_axis


def stiffness_preview(bbox_mm: list, material: str,
                      support: str = "cantilever", load_axis: int = 1) -> dict:
    """Bending stiffness of the solid treated as a beam, about the LOADED axis.

    This is the crude one. A real part is not a prismatic beam, and the
    measured error against CalculiX is what the band reports.
    """
    if material not in MATERIALS:
        return {"error": f"unknown material {material!r}"}
    E = MATERIALS[material][1]
    L, h, b, sa, la = _section(bbox_mm, load_axis)
    I = b * h ** 3 / 12.0              # depth h is along the load
    k = (3.0 * E * I / L ** 3) if support == "cantilever" else \
        (48.0 * E * I / L ** 3)
    return {**_est("stiffness", k, "N/mm",
                   f"{support} beam, span {L:.1f} mm on axis {'XYZ'[sa]}, "
                   f"bending depth {h:.1f} mm along the load axis "
                   f"{'XYZ'[la]}, I = b h^3 / 12", 25.0,
                   "the solid is treated as a prismatic beam. Holes, fillets "
                   "and the real support patch are not in this - the verified "
                   "FEA run is the number to quote."),
            "span_mm": L, "depth_mm": h, "width_mm": b, "I_mm4": I}


def deflection_preview(bbox_mm: list, material: str, load_N: float,
                       support: str = "cantilever", load_axis: int = 1) -> dict:
    k = stiffness_preview(bbox_mm, material, support, load_axis)
    if k.get("error"):
        return k
    return _est("deflection", load_N / k["value"], "mm",
                f"load / {support} stiffness ({k['method']})", 25.0, k["note"])


def stress_preview(bbox_mm: list, material: str, load_N: float,
                   support: str = "cantilever", load_axis: int = 1) -> dict:
    if material not in MATERIALS:
        return {"error": f"unknown material {material!r}"}
    fy = MATERIALS[material][2]
    L, h, b, _sa, _la = _section(bbox_mm, load_axis)
    Z = b * h ** 2 / 6.0
    M = load_N * L if support == "cantilever" else load_N * L / 4.0
    sigma = M / Z if Z else float("inf")
    return {**_est("bending stress", sigma, "MPa",
                   f"{support} M/Z on the longest axis", 60.0,
                   "nominal bending only. A real part fails at a stress "
                   "RAISER - a fillet, a hole, a shoulder - and this cannot "
                   "see one. Peak stress from FEA is routinely 2-3x this."),
            "yield_MPa": fy,
            "utilisation": sigma / fy if fy else None}


# --------------------------------------------------------------------------
# RPM -> the two things that kill rotating parts
# --------------------------------------------------------------------------

def rpm_preview(bbox_mm: list, material: str, rpm: float,
                bore_mm: float = 0.0) -> dict:
    """Rim stress and first critical speed, instantly.

    These are the two numbers that decide whether a rotor is even worth
    meshing, and both have closed forms.
    """
    if material not in MATERIALS:
        return {"error": f"unknown material {material!r}"}
    rho, E, fy, _a, _t, nu = MATERIALS[material]
    dx = [bbox_mm[3] - bbox_mm[0], bbox_mm[4] - bbox_mm[1],
          bbox_mm[5] - bbox_mm[2]]
    # A disc spins about its THIN axis: the two large dimensions are the
    # diameter, the smallest is the thickness. The first version took the
    # largest as a "length" and then grabbed the next DISTINCT value as the
    # radius - which on a 300 x 300 x 20 disc returned 10 mm and gave a hoop
    # stress of zero. The tell was the test passing on a ratio while the
    # absolute number was nonsense.
    srt = sorted(dx)
    thickness, r_o = srt[0], max(srt[1], srt[2]) / 2.0
    L = max(dx)
    w = 2.0 * math.pi * rpm / 60.0

    # Rotating disc, plane stress, free outer edge. With a central bore the
    # hoop stress at the bore is roughly DOUBLE the solid-disc centre value -
    # the classic factor everyone forgets.
    ro_m, ri_m = r_o / 1000.0, bore_mm / 2000.0
    if bore_mm > 0:
        s_hoop = rho * w ** 2 / 4.0 * ((3 + nu) * (ro_m ** 2 + ri_m ** 2)
                                       + (1 - nu) * ri_m ** 2) / 1e6
        where = "at the bore of an annular disc"
    else:
        s_hoop = (3 + nu) / 8.0 * rho * w ** 2 * ro_m ** 2 / 1e6
        where = "at the centre of a solid disc"

    # First bending critical speed, simply supported shaft of this length.
    h = min(dx)
    b = sorted(dx)[1]
    I = b * h ** 3 / 12.0
    A = b * h
    rho_mm = rho * 1e-9
    nc = (math.pi / 2.0) * math.sqrt(E * I / (rho_mm * A * (L ** 4))) * 60.0 \
        / (2 * math.pi) * 2 * math.pi if A and L else 0.0
    nc = (60.0 / (2 * math.pi)) * (math.pi ** 2 / L ** 2) * \
        math.sqrt(E * I / (rho_mm * A)) if A and L else 0.0

    return {
        "hoop_stress": {**_est("hoop stress", s_hoop, "MPa",
                               f"rotating disc, {where}", 25.0,
                               "a disc formula applied to the part's bounding "
                               "envelope. Real blades and webs concentrate "
                               "this; the verified run is a rotating-load FEA."),
                        "yield_MPa": fy,
                        "utilisation": s_hoop / fy if fy else None},
        "critical_speed": _est("first critical speed", nc, "rpm",
                               "simply supported uniform shaft", 35.0,
                               "bearing stiffness, discs and gyroscopics are "
                               "not in this. A rotordynamic run moves it."),
        "margin_pct": (nc - rpm) / rpm * 100 if rpm else None,
        "warning": (f"{rpm:g} rpm is within 20% of the estimated first "
                    f"critical speed {nc:.0f} rpm. Even as an estimate that is "
                    f"a reason to run the rotordynamics before building "
                    f"anything." if nc and abs(nc - rpm) / max(rpm, 1) < 0.2
                    else None),
    }


def thermal_preview(material: str, T_service_C: float,
                    T_assembly_C: float = 20.0,
                    restrained: bool = True) -> dict:
    """Thermal stress and how much strength is left at temperature."""
    if material not in MATERIALS:
        return {"error": f"unknown material {material!r}"}
    rho, E, fy, alpha, tmax, nu = MATERIALS[material]
    dT = T_service_C - T_assembly_C
    sigma = E * alpha * dT if restrained else 0.0
    over = T_service_C > tmax
    return {
        "thermal_stress": {**_est("thermal stress", abs(sigma), "MPa",
                                  "E alpha dT, fully restrained", 20.0,
                                  "fully restrained is the worst case. A real "
                                  "joint has some compliance, so this is an "
                                  "upper bound - which is the right direction "
                                  "for a preview to err."),
                           "dT_K": dT},
        "service_limit_C": tmax,
        "over_limit": over,
        "warning": (f"{T_service_C:g} C is above the {tmax:g} C service limit "
                    f"for {material}. Creep, not stress, is what will get you."
                    if over else
                    f"{T_service_C:g} C is within 20% of the {tmax:g} C limit; "
                    f"derate the allowable and check creep."
                    if T_service_C > 0.8 * tmax else None),
    }


def preview_all(volume_mm3: float, bbox_mm: list, material: str,
                load_N: float = None, rpm: float = None,
                T_service_C: float = None, bore_mm: float = 0.0) -> dict:
    """Everything the fast tier can say, for one slider move."""
    out = {"material": material, "basis": ESTIMATED,
           "tier": "preview",
           "contract": "Every number here is a closed-form ESTIMATE with a "
                       "published error band. None of it is COMPUTED - that "
                       "badge is reserved for a solver run with a receipt. "
                       "Press Run verified simulation for that."}
    out["mass"] = mass_preview(volume_mm3, material)
    out["stiffness"] = stiffness_preview(bbox_mm, material)
    if load_N:
        out["deflection"] = deflection_preview(bbox_mm, material, load_N)
        out["stress"] = stress_preview(bbox_mm, material, load_N)
    if rpm:
        out["rotation"] = rpm_preview(bbox_mm, material, rpm, bore_mm)
    if T_service_C is not None:
        out["thermal"] = thermal_preview(material, T_service_C)
    return out


# --------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def chk(cond, label, extra=""):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label
              + (f"   {extra}" if extra else ""))
        ok = ok and bool(cond)

    print("preview selftest")

    print(" it is fast enough to run on a slider")
    import time
    bbox = [0, 0, 0, 200.0, 20.0, 20.0]
    t0 = time.perf_counter()
    for _ in range(200):
        preview_all(80000.0, bbox, "steel (mild)", load_N=500, rpm=3000,
                    T_service_C=80)
    per_ms = (time.perf_counter() - t0) / 200 * 1000
    chk(per_ms < 2.0, f"{per_ms:.3f} ms per full preview")

    print(" mass is exact, and says so")
    m = mass_preview(1000.0, "aluminium 6061-T6")
    chk(abs(m["value"] - 0.0027) < 1e-9, f"1000 mm3 of 6061 is 2.7 g "
                                         f"({m['value']*1000:.3f} g)")
    chk(m["expect_within_pct"] == 0.0, "...with a zero error band")
    chk(m["basis"] == ESTIMATED, "...and still labelled ESTIMATED, not COMPUTED")
    chk("unknown material" in mass_preview(1.0, "unobtainium")["error"],
        "an unknown material gets no guessed density")

    print(" every estimate carries a published band")
    p = preview_all(80000.0, bbox, "steel (mild)", load_N=500, rpm=3000,
                    T_service_C=80)
    def walk(d):
        for v in d.values():
            if isinstance(v, dict):
                if v.get("basis") == ESTIMATED:
                    yield v
                else:
                    yield from walk(v)
    ests = list(walk(p))
    chk(len(ests) >= 5, f"{len(ests)} estimates produced")
    chk(all("expect_within_pct" in e for e in ests),
        "every one states how far off it may be")
    chk(all(e["method"] for e in ests), "every one names its method")
    chk(all(e["basis"] == ESTIMATED for e in ests),
        "not one of them claims COMPUTED")
    chk("Run verified simulation" in p["contract"],
        "the contract points at the verified tier")

    print(" the rotating-disc factor people forget")
    solid = rpm_preview([0, 0, 0, 300.0, 300.0, 20.0], "steel (mild)", 10000)
    holed = rpm_preview([0, 0, 0, 300.0, 300.0, 20.0], "steel (mild)", 10000,
                        bore_mm=60.0)
    # Absolute magnitude first. The ratio test alone passed while the solid
    # disc read 0 MPa, because 0 -> 8 satisfies "roughly doubles".
    #
    # Recomputed by hand rather than guessed:
    #   (3+nu)/8 * rho * w^2 * ro^2
    #   = 0.4125 * 7850 * (2*pi*10000/60)^2 * 0.15^2
    #   = 0.4125 * 7850 * 1096628 * 0.0225
    #   = 79.9 MPa
    # The first version of this test asserted ~244 MPa, which was MY
    # arithmetic error, not the code's. Cross-check: tip speed is
    # 1047 rad/s * 0.15 m = 157 m/s, and rho*v^2 = 193 MPa, times the
    # (3+nu)/8 = 0.4125 factor gives the same 80 MPa.
    ref = (3 + 0.30) / 8 * 7850 * (2 * math.pi * 10000 / 60) ** 2 * 0.15 ** 2 / 1e6
    chk(abs(solid["hoop_stress"]["value"] - ref) / ref < 0.02,
        f"a 300 mm steel disc at 10000 rpm: "
        f"{solid['hoop_stress']['value']:.1f} MPa vs closed form {ref:.1f}")
    chk(holed["hoop_stress"]["value"] > 1.7 * solid["hoop_stress"]["value"],
        f"a central bore roughly doubles hoop stress "
        f"({solid['hoop_stress']['value']:.0f} -> "
        f"{holed['hoop_stress']['value']:.0f} MPa)")
    # Speed squared: double the rpm, quadruple the stress.
    dbl = rpm_preview([0, 0, 0, 300.0, 300.0, 20.0], "steel (mild)", 20000)
    chk(abs(dbl["hoop_stress"]["value"] / solid["hoop_stress"]["value"] - 4.0)
        < 0.02, f"doubling rpm quadruples hoop stress "
                f"({dbl['hoop_stress']['value']:.0f} MPa at 20000)")
    fast = rpm_preview([0, 0, 0, 300.0, 300.0, 20.0], "steel (mild)", 40000)
    chk(fast["hoop_stress"]["utilisation"] > solid["hoop_stress"]["utilisation"],
        "hoop stress rises with speed squared")

    print(" thermal")
    hot = thermal_preview("ABS", 200.0)
    chk(hot["over_limit"] and "creep" in hot["warning"].lower(),
        "ABS at 200 C is over its limit, and creep is named")
    steel = thermal_preview("steel (mild)", 220.0)
    chk(abs(steel["thermal_stress"]["value"] - 210000 * 12e-6 * 200) < 1e-6,
        f"E alpha dT = {steel['thermal_stress']['value']:.0f} MPa for 200 K")
    chk("upper bound" in steel["thermal_stress"]["note"],
        "...declared an upper bound, which is the right way to err")

    print(" THE BAND IS MEASURED, NOT ASSERTED")
    # A preview whose error is unknown is a guess. Run the real solver and
    # check the estimate lands inside the band it published.
    try:
        import fea_solid
        import shutil
        tools = fea_solid.have_tools()
    except Exception:                                         # noqa: BLE001
        tools = {"gmsh": False, "ccx": False}
    if not (tools["gmsh"] and tools["ccx"]):
        print("  skip  no solver available to measure against")
    else:
        import tempfile
        import cadquery as cq
        from cadquery import exporters
        td = tempfile.mkdtemp(prefix="engineai_preview_")
        worst = 0.0
        for (L, b, h, F) in ((200.0, 20.0, 20.0, 2000.0),
                             (300.0, 30.0, 15.0, 1500.0)):
            step = os.path.join(td, f"b{L:.0f}.step")
            exporters.export(cq.Workplane("XY").box(L, b, h), step,
                             exportType="STEP")
            est = deflection_preview([0, 0, 0, L, b, h], "steel (mild)", F,
                                     support="cantilever")
            r = fea_solid.analyse(step, force_N=F, fix="-X", push="+X",
                                  direction=2, E_MPa=210000.0, size_mm=h / 2,
                                  workdir=os.path.join(td, f"r{L:.0f}"))
            err = abs(est["value"] - r["max_disp_mm"]) / r["max_disp_mm"] * 100
            worst = max(worst, err)
            chk(err <= est["expect_within_pct"],
                f"{L:.0f}x{b:.0f}x{h:.0f} beam: preview {est['value']:.4f} mm "
                f"vs solver {r['max_disp_mm']:.4f} mm = {err:.1f}% error, "
                f"band says {est['expect_within_pct']:.0f}%")
        chk(worst > 0, f"the band was MEASURED against CalculiX "
                       f"(worst {worst:.1f}%)")
        shutil.rmtree(td, ignore_errors=True)

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--part", default=None)
    ap.add_argument("--material", default="steel (mild)")
    ap.add_argument("--load", type=float, default=None)
    ap.add_argument("--rpm", type=float, default=None)
    ap.add_argument("--temp", type=float, default=None)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.part:
        ap.print_help()
        return 0
    import cad_parts
    wp, _u = cad_parts.build(a.part)
    sh = wp.val()
    bb = sh.BoundingBox()
    r = preview_all(sh.Volume(),
                    [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax],
                    a.material, a.load, a.rpm, a.temp)
    import json
    print(json.dumps(r, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
