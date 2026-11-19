#!/usr/bin/env python3
"""
gdt.py - EngineAI GD&T annotation layer on the CAD-native plate drawing.

Adds a machine-readable tolerance spec (limit dimensions, hole position tolerance,
datums A/B/C, general tolerance, title block) to the plate drawing, renders it as
an annotated DXF, and emits a JSON sidecar. Verified by parsing the callouts back
out and checking they are present and internally consistent (limits = nominal +/-
tol; positions reference defined datums). GD&T is a design SPEC, not a computed
result - these checks prove the callouts are complete and consistent, not that the
tolerances are 'correct' for any given application.
"""
from __future__ import annotations
import argparse, json, os, tempfile
import ezdxf
import cad_parts
from drawings_cad import _hole_centres


def gdt_spec(L=120.0, W=80.0, T=10.0, hole_d=8.0, margin=12.0,
             lin_tol=0.1, pos_tol=0.2, material="AL 6061-T6"):
    def lim(nom, t):
        return {"nominal": nom, "plus": t, "minus": t, "limits": [round(nom - t, 6), round(nom + t, 6)]}
    return {
        "part": "mounting_plate", "material": material, "units": "mm",
        "datums": {"A": "top face", "B": "left edge", "C": "front edge"},
        "general_linear_tol": lin_tol,
        "dimensions": {"L": lim(L, lin_tol), "W": lim(W, lin_tol), "T": lim(T, lin_tol),
                       "hole_dia": lim(hole_d, lin_tol)},
        "hole_position": {"characteristic": "position", "value": pos_tol,
                          "modifier": "MMC", "datum_refs": ["A", "B", "C"]},
        "note": "UNCERTIFIED - dimensioning aid, not a released drawing",
    }


def _fcf_text(hp):
    return f"POS {hp['value']:g} ({hp['modifier'][0]}) | " + " | ".join(hp["datum_refs"])


def _txt(msp, s, x, y, h=4.0):
    t = msp.add_text(s, height=h); t.set_placement((x, y)); return t


def annotated_dxf(path, spec, L=120.0, W=80.0, hole_d=8.0, margin=12.0):
    doc = ezdxf.new(); msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (L, 0), (L, W), (0, W), (0, 0)], close=True)
    for cx, cy in _hole_centres(L, W, margin):
        msp.add_circle((cx, cy), hole_d / 2)
    dl, dw = spec["dimensions"]["L"], spec["dimensions"]["W"]
    dh = spec["dimensions"]["hole_dia"]
    _txt(msp, f"{dl['nominal']:g} +/-{dl['plus']:g}", L / 2 - 10, -10)
    _txt(msp, f"{dw['nominal']:g} +/-{dw['plus']:g}", L + 4, W / 2)
    _txt(msp, f"4x dia {dh['nominal']:g} +/-{dh['plus']:g}", 0, W + 6)
    _txt(msp, _fcf_text(spec["hole_position"]), 0, W + 14)
    for name, (dx, dy) in zip(["A", "B", "C"], [(L / 2, W + 2), (-8, W / 2), (L / 2, -18)]):
        _txt(msp, f"[{name}]", dx, dy)
    _txt(msp, f"{spec['part']}  MAT:{spec['material']}  GEN TOL +/-{spec['general_linear_tol']:g}  {spec['note']}", 0, -26, 3.5)
    doc.saveas(path); return path


def make_gdt_drawing(L=120.0, W=80.0, T=10.0, hole_d=8.0, margin=12.0, outdir=None, **kw):
    outdir = outdir or tempfile.mkdtemp(); os.makedirs(outdir, exist_ok=True)
    spec = gdt_spec(L, W, T, hole_d, margin, **kw)
    dxf = annotated_dxf(os.path.join(outdir, "plate_gdt.dxf"), spec, L, W, hole_d, margin)
    jpath = os.path.join(outdir, "plate_gdt.json")
    with open(jpath, "w") as f:
        json.dump(spec, f, indent=2)
    return {"tool": "plate_gdt", "dxf": dxf, "json": jpath, "spec": spec}


def _dxf_texts(path):
    return [e.dxf.text for e in ezdxf.readfile(path).modelspace() if e.dxftype() == "TEXT"]


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    L, W, T, hd, mg, lt, pt = 120.0, 80.0, 10.0, 8.0, 12.0, 0.1, 0.2
    d = make_gdt_drawing(L, W, T, hd, mg, lin_tol=lt, pos_tol=pt)
    spec = d["spec"]; texts = " || ".join(_dxf_texts(d["dxf"]))
    chk("L limits == nominal +/- tol", spec["dimensions"]["L"]["limits"] == [round(L - lt, 6), round(L + lt, 6)],
        str(spec["dimensions"]["L"]["limits"]))
    chk("datums A/B/C in drawing", all(f"[{x}]" in texts for x in ("A", "B", "C")))
    fcf = _fcf_text(spec["hole_position"])
    chk("position FCF present", fcf in texts, fcf)
    chk("toleranced overall dim", f"{L:g} +/-{lt:g}" in texts)
    chk("position refs are defined datums", set(spec["hole_position"]["datum_refs"]) <= set(spec["datums"]))
    chk("JSON sidecar valid", json.load(open(d["json"]))["part"] == "mounting_plate")
    wp, params = cad_parts.build("mounting_plate", L=L, W=W, T=T, hole_d=hd, margin=mg)
    v = cad_parts._volume(wp); ve = cad_parts.mounting_plate_volume(**params)
    chk("geometry still matches 3D model", abs(v - ve) / ve < 1e-6, f"{v:.1f} mm^3")
    print("gdt selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI GD&T annotation layer")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--make", action="store_true"); ap.add_argument("--out", default="./out/drawings")
    a = ap.parse_args()
    if a.make:
        d = make_gdt_drawing(outdir=a.out); print(json.dumps({k: d[k] for k in ("tool", "dxf", "json")}, indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
