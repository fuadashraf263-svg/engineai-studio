#!/usr/bin/env python3
"""
design_part.py - EngineAI end-to-end "design a part" workflow.

requirement (load, span, width, material, target FoS)
    -> size the section     (structural + materials_db: solve min thickness)
    -> build the 3D solid   (cadquery)
    -> project a drawing    (drawings_project: true HLR multi-view sheet)
    -> BOM + mass           (materials_db density)
    -> ONE verified report  (JSON + Markdown + tamper-evident receipt)

Checked design aid, not a certified stamp - a licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, os, tempfile

import cadquery as cq

import materials_db
import structural
import drawings_project
import receipt


# Serviceability: strength alone does not make a usable part. A bar can satisfy its
# stress factor of safety and still sag visibly - deflection grows with span^3 while
# bending moment grows only with span, so on long spans deflection governs. L/250 is
# the conventional general-purpose limit for beams carrying no brittle finish
# (Eurocode EN 1990 Annex A1.4 / ISO 4356 give this order of magnitude; tighter limits
# such as L/360 apply under plaster and L/500 under machinery). Callers can override.
DEFAULT_DEFLECTION_RATIO = 250.0


def _size_thickness(load_N, span_mm, width_mm, E, yield_mpa, target_fos, beam,
                    deflection_ratio=DEFAULT_DEFLECTION_RATIO, t_max=1000.0, step=1.0):
    """Smallest thickness satisfying BOTH the strength FoS and the deflection limit."""
    allow = (span_mm / deflection_ratio) if deflection_ratio else float("inf")
    T = step
    while T <= t_max:
        rep = structural.design_check("rectangle", {"b": width_mm, "h": T}, beam,
                                      load_N, span_mm, E, yield_mpa, target_fos)
        if rep["factor_of_safety"] >= target_fos and rep["deflection_mm"] <= allow:
            return T, rep
        T += step
    raise RuntimeError(f"could not meet FoS {target_fos} and deflection <= span/{deflection_ratio:g} "
                       f"({allow:.2f} mm) within {t_max} mm thickness")


def design_part(load_N=2000.0, span_mm=300.0, width_mm=40.0, material="AISI_1018_steel",
                target_fos=2.0, beam="cantilever_point", name="designed_bar", outdir=None,
                deflection_ratio=DEFAULT_DEFLECTION_RATIO):
    if material not in materials_db.MATERIALS:
        raise KeyError(f"unknown material '{material}'. Known: {', '.join(materials_db.MATERIALS)}")
    mat = materials_db.MATERIALS[material]
    # A brittle material has no meaningful yield point, so a yield-based factor of
    # safety is not defined for it. Refuse rather than silently substituting tensile
    # strength - that would quietly halve the real margin on a cast iron part.
    if mat.get("brittle") or "yield" not in mat:
        raise ValueError(
            f"'{material}' is brittle (no yield point); this workflow sizes on yield. "
            f"Brittle materials need an ultimate-strength design with a larger safety "
            f"factor and a stress-concentration assessment - out of scope here.")
    outdir = outdir or tempfile.mkdtemp(); os.makedirs(outdir, exist_ok=True)
    yield_mpa = mat["yield"]["value"]
    E = mat["E"]["value"] * 1000.0
    density = mat["density"]["value"]

    T, rep = _size_thickness(load_N, span_mm, width_mm, E, yield_mpa, target_fos, beam,
                             deflection_ratio=deflection_ratio)
    defl_allow = (span_mm / deflection_ratio) if deflection_ratio else float("inf")

    wp = cq.Workplane("XY").box(span_mm, width_mm, T, centered=(False, False, False))
    bb = wp.val().BoundingBox()
    bbox = (bb.xlen, bb.ylen, bb.zlen)
    step_path = os.path.join(outdir, name + ".step")
    cq.exporters.export(wp, step_path)

    sheet = drawings_project.sheet_from_shape(wp.val().wrapped, name, material, outdir, bbox)

    vol_mm3 = span_mm * width_mm * T
    mass_kg = density * vol_mm3 * 1e-9
    bom = sheet["bom"]; bom[0]["mass_kg"] = round(mass_kg, 4)

    checks = []
    recheck = structural.design_check("rectangle", {"b": width_mm, "h": T}, beam,
                                      load_N, span_mm, E, yield_mpa, target_fos)
    checks.append({"name": "sized part meets target FoS", "passed": recheck["factor_of_safety"] >= target_fos,
                   "detail": f"FoS={recheck['factor_of_safety']} >= {target_fos}"})
    checks.append({"name": f"deflection within serviceability limit span/{deflection_ratio:g}",
                   "passed": recheck["deflection_mm"] <= defl_allow,
                   "detail": f"{recheck['deflection_mm']:.3f} mm <= {defl_allow:.3f} mm"})
    checks.append({"name": "CAD solid bbox == sized dimensions",
                   "passed": abs(bb.xlen - span_mm) < 1e-6 and abs(bb.ylen - width_mm) < 1e-6 and abs(bb.zlen - T) < 1e-6,
                   "detail": f"{bb.xlen:.1f} x {bb.ylen:.1f} x {bb.zlen:.1f} mm"})
    tv = sheet["views"]["top"]
    checks.append({"name": "drawing top view == plan of solid",
                   "passed": abs(tv["w"] - span_mm) < 0.5 and abs(tv["h"] - width_mm) < 0.5,
                   "detail": f"{tv['w']:.1f} x {tv['h']:.1f} mm"})
    all_ok = all(c["passed"] for c in checks)

    report = {
        "requirement": {"load_N": load_N, "span_mm": span_mm, "width_mm": width_mm,
                        "material": material, "target_fos": target_fos, "beam": beam,
                        "deflection_limit": f"span/{deflection_ratio:g}"},
        "result": {
            "sized_thickness_mm": T,
            "section": {"b_mm": width_mm, "h_mm": T, "Z_mm3": recheck["Z_mm3"], "I_mm4": recheck["I_mm4"]},
            "bending_moment_Nmm": recheck["M_Nmm"], "bending_stress_MPa": recheck["sigma_MPa"],
            "deflection_mm": recheck["deflection_mm"],
            "deflection_allowable_mm": round(defl_allow, 4),
            "governed_by": ("deflection" if recheck["factor_of_safety"] > target_fos * 1.05 else "strength"),
            "yield_MPa": yield_mpa,
            "factor_of_safety": recheck["factor_of_safety"], "mass_kg": round(mass_kg, 4),
        },
        "bom": bom,
        "files": {"step": step_path, "drawing_svg": sheet["svg"], "drawing_dxf": sheet["dxf"]},
        "checks": checks, "verified": all_ok,
        "sources": [f"materials_db:{material}:{mat['yield']['source']}",
                    f"materials_db:{material}:{mat['E']['source']}"],
        "disclaimer": "Checked design aid, not a certified result. A licensed engineer signs off.",
    }
    rec = receipt.make_receipt(
        question=f"design {material} bar for {load_N:g} N at {span_mm:g} mm span, FoS>={target_fos:g}",
        method="design_part", inputs=report["requirement"], result=report["result"], checks=checks)
    report["receipt"] = rec
    report["content_hash"] = rec["content_hash"]
    report["created_utc"] = rec["created_utc"]

    json_path = os.path.join(outdir, name + "_report.json")
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)
    md_path = os.path.join(outdir, name + "_report.md")
    with open(md_path, "w") as f:
        f.write(_markdown(report))
    report["files"]["report_json"] = json_path
    report["files"]["report_md"] = md_path
    return report


def _markdown(r):
    req, res = r["requirement"], r["result"]
    lines = [
        f"# EngineAI design report - {req['material']} cantilever bar", "",
        f"**Verified:** {'YES' if r['verified'] else 'NO'}    ",
        f"**Content hash:** `{r.get('content_hash','')[:16]}...`", "",
        "## Requirement",
        f"- Load: {req['load_N']:g} N ({req['beam']})",
        f"- Span: {req['span_mm']:g} mm, width: {req['width_mm']:g} mm",
        f"- Material: {req['material']}, target factor of safety: {req['target_fos']:g}", "",
        "## Result",
        f"- Sized thickness: **{res['sized_thickness_mm']:g} mm**",
        f"- Section modulus Z: {res['section']['Z_mm3']:.1f} mm^3, I: {res['section']['I_mm4']:.1f} mm^4",
        f"- Bending moment: {res['bending_moment_Nmm']:.0f} N.mm, bending stress: {res['bending_stress_MPa']:.2f} MPa",
        f"- Deflection: {res['deflection_mm']:.3f} mm "
        f"(limit {res['deflection_allowable_mm']:.3f} mm = {req['deflection_limit']})",
        f"- Governed by: **{res['governed_by']}**",
        f"- Yield: {res['yield_MPa']:g} MPa -> **factor of safety {res['factor_of_safety']:.2f}**",
        f"- Mass: {res['mass_kg']:.3f} kg", "",
        "## Verification",
    ]
    for c in r["checks"]:
        lines.append(f"- [{'PASS' if c['passed'] else 'FAIL'}] {c['name']} ({c['detail']})")
    lines += ["", "## Files",
              f"- STEP: `{os.path.basename(r['files']['step'])}`",
              f"- Drawing: `{os.path.basename(r['files']['drawing_svg'])}` / `{os.path.basename(r['files']['drawing_dxf'])}`",
              "", f"> {r['disclaimer']}"]
    return "\n".join(lines)


def _selftest():
    ok = True

    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    tmp = tempfile.mkdtemp()
    r = design_part(load_N=2000, span_mm=300, width_mm=40, material="AISI_1018_steel",
                    target_fos=2.0, beam="cantilever_point", outdir=tmp)
    res = r["result"]
    chk("workflow reports verified=True", r["verified"] is True)
    chk("sized part actually meets target FoS", res["factor_of_safety"] >= 2.0, f"FoS={res['factor_of_safety']}")
    thinner = structural.design_check("rectangle", {"b": 40, "h": res["sized_thickness_mm"] - 1.0},
                                      "cantilever_point", 2000, 300, 200000.0, 370.0, 2.0)
    chk("a smaller thickness would FAIL (sizing is tight, not arbitrary)",
        thinner["factor_of_safety"] < 2.0 or thinner["deflection_mm"] > 300 / 250.0,
        f"T-1={res['sized_thickness_mm']-1:g} mm -> FoS={thinner['factor_of_safety']}, "
        f"sag={thinner['deflection_mm']:.3f} mm (limit {300/250:.3f})")
    chk("STEP + SVG + DXF + JSON + MD all written",
        all(os.path.getsize(p) > 0 for p in [r["files"]["step"], r["files"]["drawing_svg"],
            r["files"]["drawing_dxf"], r["files"]["report_json"], r["files"]["report_md"]]))
    chk("BOM carries mass", r["bom"][0].get("mass_kg", 0) > 0, f"{r['bom'][0].get('mass_kg')} kg")
    chk("receipt verifies (tamper-evident)", receipt.verify_receipt(r["receipt"])["ok"])
    chk("cites material sources", any("materials_db" in s for s in r["sources"]))

    brittle_refused = False
    try:
        design_part(load_N=2000, span_mm=300, width_mm=40, material="grey_iron_G4000",
                    target_fos=2.0, outdir=tmp, name="brittle")
    except ValueError as e:
        brittle_refused = "brittle" in str(e)
    chk("a brittle material is REFUSED, not silently sized on tensile strength",
        brittle_refused)
    r2 = design_part(load_N=6000, span_mm=300, width_mm=40, material="AISI_1018_steel",
                     target_fos=2.0, outdir=tmp, name="heavy")
    chk("heavier load -> thicker section", r2["result"]["sized_thickness_mm"] > res["sized_thickness_mm"],
        f"{res['sized_thickness_mm']:g} -> {r2['result']['sized_thickness_mm']:g} mm")

    # Serviceability: on a long span, deflection (~L^3) governs long before stress (~L).
    # Strength-only sizing used to "verify" a bar that sagged far past any usable limit.
    r3 = design_part(load_N=2000, span_mm=1500, width_mm=40, material="AISI_1018_steel",
                     target_fos=2.0, outdir=tmp, name="longspan")
    res3 = r3["result"]
    chk("long span: deflection is respected, not just stress",
        res3["deflection_mm"] <= res3["deflection_allowable_mm"],
        f"{res3['deflection_mm']:.2f} mm <= {res3['deflection_allowable_mm']:.2f} mm")
    chk("long span is reported as deflection-governed", res3["governed_by"] == "deflection",
        f"FoS={res3['factor_of_safety']} (well above target -> stress was not binding)")
    # h=50 is the section a strength-only sizer would have accepted on this span:
    # it clears the FoS but sags ~4.5x past the serviceability limit.
    so = structural.design_check("rectangle", {"b": 40, "h": 50}, "cantilever_point",
                                 2000, 1500, 200000.0, 370.0, 2.0)
    chk("the old strength-only answer would have failed serviceability",
        so["factor_of_safety"] >= 2.0 and so["deflection_mm"] > 1500 / 250.0,
        f"FoS={so['factor_of_safety']} passes, but sag {so['deflection_mm']:.1f} mm "
        f"> {1500/250:.1f} mm limit -> now rejected")
    print("design_part selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI end-to-end design workflow")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--load", type=float, default=2000); ap.add_argument("--span", type=float, default=300)
    ap.add_argument("--width", type=float, default=40); ap.add_argument("--material", default="AISI_1018_steel")
    ap.add_argument("--fos", type=float, default=2.0); ap.add_argument("--beam", default="cantilever_point")
    ap.add_argument("--out", default="./out/design"); ap.add_argument("--name", default="designed_bar")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(_selftest())
    r = design_part(a.load, a.span, a.width, a.material, a.fos, a.beam, a.name, a.out)
    print(_markdown(r)); print("\nJSON:", r["files"]["report_json"])
