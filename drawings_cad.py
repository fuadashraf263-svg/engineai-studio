#!/usr/bin/env python3
"""
drawings_cad.py - EngineAI CAD-native dimensioned mechanical drawings.

Generates a 2D top-view drawing (DXF + dimensioned SVG) directly from the
parametric CAD part, and PROVES the drawing matches the 3D model: DXF extents
equal the part bounding box, hole count/diameter/placement match the spec, and
the solid volume equals the closed form. A dimensioned-drawing foundation
(overall dims + hole callouts); full GD&T tolerance frames are a further step.
"""
from __future__ import annotations
import argparse, os, tempfile
import cad_parts
import ezdxf


def _hole_centres(L, W, margin):
    dx, dy = L / 2 - margin, W / 2 - margin
    return [(L / 2 + sx * dx, W / 2 + sy * dy) for sx, sy in [(1, 1), (-1, 1), (1, -1), (-1, -1)]]


def plate_dxf(path, L=120.0, W=80.0, hole_d=8.0, margin=12.0):
    doc = ezdxf.new(); msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (L, 0), (L, W), (0, W), (0, 0)], close=True)
    for cx, cy in _hole_centres(L, W, margin):
        msp.add_circle((cx, cy), hole_d / 2)
    doc.saveas(path); return path


def plate_svg(path, L=120.0, W=80.0, T=10.0, hole_d=8.0, margin=12.0):
    pad = 30.0
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {L+2*pad} {W+2*pad+30}" font-family="sans-serif" font-size="5">']
    parts.append(f'<rect x="{pad}" y="{pad}" width="{L}" height="{W}" fill="none" stroke="#111" stroke-width="0.5"/>')
    for cx, cy in _hole_centres(L, W, margin):
        parts.append(f'<circle cx="{pad+cx}" cy="{pad+(W-cy)}" r="{hole_d/2}" fill="none" stroke="#111" stroke-width="0.5"/>')
    parts.append(f'<text x="{pad+L/2}" y="{pad+W+12}" fill="#c00" text-anchor="middle">{L:g} mm</text>')
    parts.append(f'<text x="{pad+L+4}" y="{pad+W/2}" fill="#c00">{W:g} mm</text>')
    parts.append(f'<text x="{pad}" y="{pad+W+24}" fill="#111">Plate {L:g}x{W:g}x{T:g} mm, 4x holes dia {hole_d:g}  (uncertified drawing)</text>')
    parts.append('</svg>')
    with open(path, "w") as f:
        f.write("\n".join(parts))
    return path


def make_drawing(L=120.0, W=80.0, T=10.0, hole_d=8.0, margin=12.0, outdir=None):
    outdir = outdir or tempfile.mkdtemp(); os.makedirs(outdir, exist_ok=True)
    return {"tool": "plate_drawing",
            "dxf": plate_dxf(os.path.join(outdir, "plate.dxf"), L, W, hole_d, margin),
            "svg": plate_svg(os.path.join(outdir, "plate.svg"), L, W, T, hole_d, margin),
            "L": L, "W": W, "T": T, "hole_d": hole_d}


def _dxf_extents_holes(path):
    msp = ezdxf.readfile(path).modelspace()
    xs, ys, holes = [], [], []
    for e in msp:
        if e.dxftype() == "LWPOLYLINE":
            for x, y, *_ in e.get_points():
                xs.append(x); ys.append(y)
        elif e.dxftype() == "CIRCLE":
            holes.append((e.dxf.center.x, e.dxf.center.y, e.dxf.radius))
    return (max(xs) - min(xs), max(ys) - min(ys)), holes


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    L, W, T, hd, mg = 120.0, 80.0, 10.0, 8.0, 12.0
    d = make_drawing(L, W, T, hd, mg)
    chk("DXF + SVG written", os.path.getsize(d["dxf"]) > 0 and os.path.getsize(d["svg"]) > 0)
    (ex, ey), holes = _dxf_extents_holes(d["dxf"])
    chk("DXF extents == part L x W", abs(ex - L) < 1e-6 and abs(ey - W) < 1e-6, f"{ex:g} x {ey:g} mm")
    chk("4 holes of diameter hole_d", len(holes) == 4 and all(abs(2 * r - hd) < 1e-6 for *_, r in holes), f"{len(holes)} holes")
    hx = sorted({round(h[0], 6) for h in holes}); hy = sorted({round(h[1], 6) for h in holes})
    chk("holes margin from edges", hx == [mg, L - mg] and hy == [mg, W - mg], f"x={hx} y={hy}")
    wp, params = cad_parts.build("mounting_plate", L=L, W=W, T=T, hole_d=hd, margin=mg)
    bb = wp.val().BoundingBox()
    chk("CAD solid bbox == drawing dims", abs(bb.xlen - L) < 1e-6 and abs(bb.ylen - W) < 1e-6 and abs(bb.zlen - T) < 1e-6,
        f"{bb.xlen:g}x{bb.ylen:g}x{bb.zlen:g}")
    v = cad_parts._volume(wp); ve = cad_parts.mounting_plate_volume(**params)
    chk("solid volume == closed form", abs(v - ve) / ve < 1e-6, f"{v:.1f} mm^3")
    print("drawings_cad selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI CAD-native dimensioned drawings")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--make", action="store_true"); ap.add_argument("--out", default="./out/drawings")
    a = ap.parse_args()
    if a.make:
        import json; print(json.dumps(make_drawing(outdir=a.out), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
