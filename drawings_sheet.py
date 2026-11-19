#!/usr/bin/env python3
"""
drawings_sheet.py - EngineAI multi-view mechanical drawing SHEET (DXF + SVG + BOM).

From a parametric part: three orthographic views (top, front, right) with overall
dimensions, a title block, and a BOM table. Verified: each view's extents equal the
part's projected dimensions, the hole count matches, and the sheet is cross-checked
against the 3D CAD solid's bounding box. A real drawing deliverable - the title
block says UNCERTIFIED; a licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, os, tempfile
import ezdxf
import cad_parts
from drawings_cad import _hole_centres


def plate_views(L, W, T, hole_d, margin):
    r = hole_d / 2
    return {
        "top":   {"w": L, "h": W, "holes": [(x, y, r) for x, y in _hole_centres(L, W, margin)]},
        "front": {"w": L, "h": T, "holes": []},
        "right": {"w": W, "h": T, "holes": []},
    }


def sheet_svg(path, part, params, material, views):
    L, W, T, hd = params["L"], params["W"], params["T"], params["hole_d"]
    pad, gap = 40.0, 60.0
    SW = pad * 2 + L + gap + W + 40
    SH = pad * 2 + W + gap + T + 100
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SW} {SH}" font-family="sans-serif" font-size="6">']
    def rect(x, y, w, h): s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="#111" stroke-width="0.5"/>')
    def txt(x, y, t, col="#111", a="start"): s.append(f'<text x="{x}" y="{y}" fill="{col}" text-anchor="{a}">{t}</text>')
    tx, ty = pad, pad
    rect(tx, ty, L, W); txt(tx, ty - 4, "TOP")
    for cx, cy, rr in views["top"]["holes"]:
        s.append(f'<circle cx="{tx+cx}" cy="{ty+(W-cy)}" r="{rr}" fill="none" stroke="#111" stroke-width="0.5"/>')
    txt(tx + L / 2, ty + W + 12, f"{L:g}", "#c00", "middle"); txt(tx + L + 4, ty + W / 2, f"{W:g}", "#c00")
    fx, fy = pad, ty + W + gap
    rect(fx, fy, L, T); txt(fx, fy - 4, "FRONT"); txt(fx + L + 4, fy + T / 2, f"{T:g}", "#c00")
    rx, ry = tx + L + gap, ty
    rect(rx, ry, W, T); txt(rx, ry - 4, "RIGHT"); txt(rx + W / 2, ry + T + 12, f"{W:g}", "#c00", "middle")
    by = SH - 84
    rect(pad, by, SW - 2 * pad, 64)
    txt(pad + 4, by + 14, f"PART: {part}")
    txt(pad + 4, by + 28, f"MATERIAL: {material}")
    txt(pad + 4, by + 42, f"OVERALL: {L:g} x {W:g} x {T:g} mm    4x holes dia {hd:g}")
    txt(pad + 4, by + 56, "SCALE 1:1   UNCERTIFIED - not a released drawing")
    txt(SW - pad - 4, by + 14, "EngineAI", "#111", "end")
    s.append('</svg>')
    with open(path, "w") as f:
        f.write("\n".join(s))
    return path


def sheet_dxf(path, params):
    L, W, T, hd, mg = params["L"], params["W"], params["T"], params["hole_d"], params["margin"]
    doc = ezdxf.new(); msp = doc.modelspace(); gap = 60
    msp.add_lwpolyline([(0, 0), (L, 0), (L, W), (0, W), (0, 0)], close=True)          # TOP
    for cx, cy in _hole_centres(L, W, mg): msp.add_circle((cx, cy), hd / 2)
    fy = -(T + gap)
    msp.add_lwpolyline([(0, fy), (L, fy), (L, fy + T), (0, fy + T), (0, fy)], close=True)   # FRONT
    rx = L + gap
    msp.add_lwpolyline([(rx, 0), (rx + W, 0), (rx + W, T), (rx, T), (rx, 0)], close=True)   # RIGHT
    doc.saveas(path); return path


def make_sheet(part="mounting_plate", material="AL 6061-T6", outdir=None, **overrides):
    outdir = outdir or tempfile.mkdtemp(); os.makedirs(outdir, exist_ok=True)
    wp, params = cad_parts.build(part, **overrides)
    views = plate_views(params["L"], params["W"], params["T"], params["hole_d"], params["margin"])
    svg = sheet_svg(os.path.join(outdir, part + "_sheet.svg"), part, params, material, views)
    dxf = sheet_dxf(os.path.join(outdir, part + "_sheet.dxf"), params)
    bom = [{"item": 1, "part": part, "qty": 1, "material": material,
            "size_mm": f"{params['L']:g}x{params['W']:g}x{params['T']:g}"}]
    return {"svg": svg, "dxf": dxf, "views": views, "bom": bom, "params": params, "wp": wp}


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    d = make_sheet("mounting_plate", "AL 6061-T6", L=120, W=80, T=10, hole_d=8, margin=12)
    v, p = d["views"], d["params"]
    chk("top view extents == L x W", v["top"]["w"] == p["L"] and v["top"]["h"] == p["W"])
    chk("front view extents == L x T", v["front"]["w"] == p["L"] and v["front"]["h"] == p["T"])
    chk("right view extents == W x T", v["right"]["w"] == p["W"] and v["right"]["h"] == p["T"])
    chk("top view has 4 holes", len(v["top"]["holes"]) == 4)
    bb = d["wp"].val().BoundingBox()
    chk("sheet consistent with 3D solid bbox",
        abs(bb.xlen - p["L"]) < 1e-6 and abs(bb.ylen - p["W"]) < 1e-6 and abs(bb.zlen - p["T"]) < 1e-6)
    chk("BOM lists material + size", d["bom"][0]["material"] == "AL 6061-T6" and "120x80x10" in d["bom"][0]["size_mm"])
    chk("SVG + DXF written", os.path.getsize(d["svg"]) > 0 and os.path.getsize(d["dxf"]) > 0,
        f"{os.path.getsize(d['svg'])}B / {os.path.getsize(d['dxf'])}B")
    msp = ezdxf.readfile(d["dxf"]).modelspace()
    xs = [pt[0] for e in msp if e.dxftype() == "LWPOLYLINE" for pt in e.get_points()]
    chk("DXF contains all view geometry", len([e for e in msp if e.dxftype() == "LWPOLYLINE"]) == 3 and max(xs) > p["L"])
    print("drawings_sheet selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI multi-view drawing sheet")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--make", action="store_true"); ap.add_argument("--out", default="./out/sheets")
    a = ap.parse_args()
    if a.make:
        d = make_sheet(outdir=a.out)
        print(json.dumps({"svg": d["svg"], "dxf": d["dxf"], "bom": d["bom"]}, indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
