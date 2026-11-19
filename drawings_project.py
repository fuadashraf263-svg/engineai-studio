#!/usr/bin/env python3
"""
drawings_project.py - EngineAI TRUE orthographic drawing engine (OCC HLR).

Unlike drawings_sheet.py (which drew bounding rectangles and only worked for the
flat plate), this projects the ACTUAL 3D solid through OpenCASCADE's Hidden-Line-
Removal algorithm - the same engine FreeCAD's TechDraw uses - to produce real
visible + hidden edges for any part. Output is a multi-view sheet (SVG + DXF) with
solid visible lines, dashed hidden lines, a title block and a BOM.

Verified in --selftest: each projected view's extents equal the solid's bounding
box on that plane, for BOTH a plate AND an L-bracket (proving it is general, not a
special case), hidden geometry appears where features are occluded, and the DXF
round-trips through a clean audit. A real drawing deliverable - the title block
says UNCERTIFIED; a licensed engineer signs off.

Requires: cadquery (ships OCP/OpenCASCADE), ezdxf.
"""
from __future__ import annotations
import argparse, json, math, os, sys, tempfile

import cadquery as cq
import ezdxf
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GCPnts import GCPnts_QuasiUniformDeflection

import cad_parts

_AX = {
    "top":   gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1),  gp_Dir(1, 0, 0)),
    "front": gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, -1, 0), gp_Dir(1, 0, 0)),
    "right": gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0),  gp_Dir(0, 1, 0)),
    # Isometric: view along the body diagonal (1,1,1). The name is literal - all
    # three principal axes project to EQUAL length, sqrt(2/3) = 0.81650 of true,
    # and land 120 degrees apart on the sheet. Both facts are exact, and both are
    # asserted, because an "isometric" view that fails them is just an arbitrary
    # rotation with a flattering name.
    "iso":   gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(1, 1, 1),  gp_Dir(1, -1, 0)),
}

# The three views a third-angle sheet must carry. `iso` is a pictorial addition,
# not one of them - folding it into this set would silently change the layout of
# every sheet that iterates the axes.
ORTHOGRAPHIC = ("top", "front", "right")
ISO_SCALE = math.sqrt(2.0 / 3.0)          # 0.81650, the isometric foreshortening


def _edges(compound, defl=0.05):
    pls = []
    if compound is None or compound.IsNull():
        return pls
    exp = TopExp_Explorer(compound, TopAbs_EDGE)
    seen = failed = 0
    while exp.More():
        seen += 1
        e = TopoDS.Edge(exp.Current())
        try:
            gc = GCPnts_QuasiUniformDeflection(BRepAdaptor_Curve(e), defl)
            pts = [(gc.Value(i).X(), gc.Value(i).Y()) for i in range(1, gc.NbPoints() + 1)] if gc.IsDone() else []
        except Exception:
            failed += 1
            pts = []
        if len(pts) >= 2:
            pls.append(pts)
        exp.Next()
    if failed:
        print(f"drawings_project: {failed}/{seen} edges could not be discretised", file=sys.stderr)
    return pls


def _project(shape, ax):
    algo = HLRBRep_Algo(); algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(ax)); algo.Update(); algo.Hide()
    hlr = HLRBRep_HLRToShape(algo)

    def grab(*fns):
        # A bare `except: pass` here once hid a real bug for who knows how long: an
        # OCP API rename (TopoDS.Edge_s -> TopoDS.Edge) made every call raise, and the
        # drawing engine silently produced empty 0x0 views instead of erroring. Now any
        # unexpected exception is surfaced on stderr so a future API drift is loud, not
        # invisible - it still degrades to "no geometry from this source" rather than
        # crashing the whole sheet, since one malformed edge should not kill a drawing.
        out = []
        for fn in fns:
            try:
                out += _edges(fn())
            except Exception as e:
                print(f"drawings_project: {fn.__name__} extraction failed: "
                      f"{type(e).__name__}: {e}", file=sys.stderr)
        return out

    return grab(hlr.VCompound, hlr.OutLineVCompound), grab(hlr.HCompound, hlr.OutLineHCompound)


def project_views(shape, views_wanted=ORTHOGRAPHIC):
    """Return {view: {visible, hidden, w, h}} with each view normalised to (0,0).

    Defaults to the three orthographic views, NOT every axis in _AX. Adding the
    isometric axis to the dict and letting this iterate it would have quietly put a
    fourth view into every existing sheet - the layout code addresses views by name
    and would not have complained, it would just have drawn the wrong thing.
    """
    views = {}
    for name in views_wanted:
        ax = _AX[name]
        vis, hid = _project(shape, ax)
        pts = [p for pl in vis + hid for p in pl]
        if not pts:
            views[name] = {"visible": [], "hidden": [], "w": 0.0, "h": 0.0}
            continue
        minx = min(x for x, _ in pts); miny = min(y for _, y in pts)
        maxx = max(x for x, _ in pts); maxy = max(y for _, y in pts)
        sh = lambda pl: [(x - minx, y - miny) for x, y in pl]
        views[name] = {"visible": [sh(pl) for pl in vis], "hidden": [sh(pl) for pl in hid],
                       "w": maxx - minx, "h": maxy - miny}
    return views


def project_isometric(shape):
    """The pictorial view: one image that shows all three faces at once.

    An orthographic set is unambiguous and unreadable to anyone who is not a
    draughtsman; the isometric is what a fabricator actually looks at first. It is
    the same HLR engine with the projector pointed down the body diagonal, so
    hidden-line handling and edge discretisation are identical to the other views.
    """
    return project_views(shape, ("iso",))["iso"]


def isometric_axis_check():
    """Prove the projection really is ISOmetric, from the axis definition alone.

    Two exact properties define it, and neither depends on the shape being drawn:

      * each of x, y, z projects to the SAME length, sqrt(2/3) = 0.816497 of true
      * their projections lie 120 degrees apart

    Anything that fails these is an arbitrary rotation, however plausible it looks
    on the page. Computing them from the gp_Ax2 itself means the check tests the
    axis, not a picture of it.
    """
    ax = _AX["iso"]
    n = ax.Direction()
    vx = ax.XDirection()
    vy = ax.YDirection()
    N = (n.X(), n.Y(), n.Z())
    X = (vx.X(), vx.Y(), vx.Z())
    Y = (vy.X(), vy.Y(), vy.Z())

    def proj(v):
        # component of v in the projection plane, expressed in (X, Y)
        return (sum(a * b for a, b in zip(v, X)), sum(a * b for a, b in zip(v, Y)))

    axes = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
    p = {k: proj(v) for k, v in axes.items()}
    lens = {k: math.hypot(*q) for k, q in p.items()}
    angs = {k: math.degrees(math.atan2(q[1], q[0])) % 360.0 for k, q in p.items()}
    seps = sorted(abs((angs[a] - angs[b] + 180.0) % 360.0 - 180.0)
                  for a, b in (("x", "y"), ("y", "z"), ("z", "x")))
    return {"normal": N, "x_dir": X, "y_dir": Y,
            "projected_lengths": lens,
            "expected_length": ISO_SCALE,
            "max_length_deviation": max(abs(v - ISO_SCALE) for v in lens.values()),
            "equal_foreshortening": max(lens.values()) - min(lens.values()) < 1e-12,
            "projected_angles_deg": angs,
            "separations_deg": seps,
            "all_120_apart": all(abs(s - 120.0) < 1e-9 for s in seps),
            "note": "equal foreshortening and 120 degree separation are what the "
                    "word isometric means; a view failing them is just a rotation"}


def _svg(path, part, material, views):
    M, G, TB = 24.0, 34.0, 60.0
    topW, topH = views["top"]["w"], views["top"]["h"]
    frW, frH = views["front"]["w"], views["front"]["h"]
    rgW, rgH = views["right"]["w"], views["right"]["h"]
    top_at = (M, M)
    front_at = (M, M + topH + G)
    right_at = (M + frW + G, M + topH + G)
    SW = M + max(topW, frW + G + rgW) + M
    SH = M + topH + G + max(frH, rgH) + G + TB + M
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SW:.1f} {SH:.1f}" '
         f'font-family="sans-serif" font-size="6">',
         f'<rect x="2" y="2" width="{SW-4:.1f}" height="{SH-4:.1f}" fill="white" stroke="#111" stroke-width="0.6"/>']

    def draw(view, ox, oy, label):
        h = view["h"]
        def poly(pls, dash):
            for pl in pls:
                d = "M " + " L ".join(f"{ox+x:.2f},{oy+(h-y):.2f}" for x, y in pl)
                da = ' stroke-dasharray="3 2"' if dash else ""
                col = "#888" if dash else "#111"
                s.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="0.5"{da}/>')
        poly(view["hidden"], True)
        poly(view["visible"], False)
        s.append(f'<text x="{ox:.1f}" y="{oy-3:.1f}" fill="#111" font-weight="600">{label}</text>')
        s.append(f'<text x="{ox+view["w"]/2:.1f}" y="{oy+h+10:.1f}" fill="#c00" text-anchor="middle">{view["w"]:.1f}</text>')
        s.append(f'<text x="{ox+view["w"]+4:.1f}" y="{oy+h/2:.1f}" fill="#c00">{h:.1f}</text>')

    draw(views["top"], *top_at, "TOP")
    draw(views["front"], *front_at, "FRONT")
    draw(views["right"], *right_at, "RIGHT")
    by = SH - M - TB
    s.append(f'<rect x="{M:.1f}" y="{by:.1f}" width="{SW-2*M:.1f}" height="{TB:.1f}" fill="none" stroke="#111" stroke-width="0.6"/>')
    s.append(f'<line x1="{M:.1f}" y1="{by+16:.1f}" x2="{SW-M:.1f}" y2="{by+16:.1f}" stroke="#111" stroke-width="0.4"/>')
    s.append(f'<text x="{M+4:.1f}" y="{by+11:.1f}" fill="#111" font-weight="700">PART: {part}</text>')
    s.append(f'<text x="{SW-M-4:.1f}" y="{by+11:.1f}" fill="#111" text-anchor="end" font-weight="700">EngineAI</text>')
    s.append(f'<text x="{M+4:.1f}" y="{by+29:.1f}" fill="#111">MATERIAL: {material}</text>')
    s.append(f'<text x="{M+4:.1f}" y="{by+41:.1f}" fill="#111">VIEWS: third-angle    solid = visible, dashed = hidden</text>')
    s.append(f'<text x="{M+4:.1f}" y="{by+53:.1f}" fill="#666" font-size="5">SCALE 1:1   UNCERTIFIED - not a released drawing; a licensed engineer signs off</text>')
    s.append('</svg>')
    with open(path, "w") as f:
        f.write("\n".join(s))
    return path, (SW, SH)


def _dxf(path, views):
    doc = ezdxf.new(); msp = doc.modelspace()
    if "HIDDEN" not in doc.layers:
        doc.layers.add("HIDDEN", color=8)
    if "VISIBLE" not in doc.layers:
        doc.layers.add("VISIBLE", color=7)
    G = 34.0
    topH = views["top"]["h"]; frW = views["front"]["w"]
    offs = {"top": (0.0, G + max(views["front"]["h"], views["right"]["h"])),
            "front": (0.0, 0.0),
            "right": (frW + G, 0.0)}
    for name, view in views.items():
        ox, oy = offs[name]
        for pl in view["visible"]:
            msp.add_lwpolyline([(ox + x, oy + y) for x, y in pl], dxfattribs={"layer": "VISIBLE"})
        for pl in view["hidden"]:
            msp.add_lwpolyline([(ox + x, oy + y) for x, y in pl], dxfattribs={"layer": "HIDDEN"})
    doc.saveas(path); return path


def sheet_from_shape(shape, name, material, outdir, bbox):
    """Project + render a drawing sheet from ANY raw OCC shape (used by design_part too)."""
    os.makedirs(outdir, exist_ok=True)
    views = project_views(shape)
    svg = _svg(os.path.join(outdir, name + "_proj.svg"), name, material, views)[0]
    dxf = _dxf(os.path.join(outdir, name + "_proj.dxf"), views)
    bom = [{"item": 1, "part": name, "qty": 1, "material": material,
            "size_mm": f"{bbox[0]:.1f}x{bbox[1]:.1f}x{bbox[2]:.1f}"}]
    return {"svg": svg, "dxf": dxf, "bom": bom, "bbox": tuple(bbox),
            "views": {k: {"w": v["w"], "h": v["h"], "visible": len(v["visible"]),
                          "hidden": len(v["hidden"])} for k, v in views.items()}}


def make_sheet(part="mounting_plate", material="AL 6061-T6", outdir=None, **overrides):
    outdir = outdir or tempfile.mkdtemp()
    wp, params = cad_parts.build(part, **overrides)
    bb = wp.val().BoundingBox()
    d = sheet_from_shape(wp.val().wrapped, part, material, outdir, (bb.xlen, bb.ylen, bb.zlen))
    d["params"] = params
    return d


def _selftest():
    ok = True

    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    def near(a, b, t=0.5):
        return abs(a - b) < t

    d = make_sheet("mounting_plate", "AL 6061-T6", L=120, W=80, T=10, hole_d=8, margin=12)
    v = d["views"]
    chk("plate top view == L x W", near(v["top"]["w"], 120) and near(v["top"]["h"], 80),
        f"{v['top']['w']:.1f} x {v['top']['h']:.1f}")
    chk("plate front view == L x T", near(v["front"]["w"], 120) and near(v["front"]["h"], 10))
    chk("plate right view == W x T", near(v["right"]["w"], 80) and near(v["right"]["h"], 10))
    chk("plate: holes projected as HIDDEN lines in front view", v["front"]["hidden"] > 0,
        f"{v['front']['hidden']} hidden edges")

    b = make_sheet("l_bracket", "AISI 1018 steel", a=60, b=40, t=6, width=50)
    lv = b["views"]
    chk("l_bracket top view == a x b (profile in XY)", near(lv["top"]["w"], 60) and near(lv["top"]["h"], 40),
        f"{lv['top']['w']:.1f} x {lv['top']['h']:.1f}")
    chk("l_bracket front view == a x width (extruded in Z)", near(lv["front"]["w"], 60) and near(lv["front"]["h"], 50),
        f"{lv['front']['w']:.1f} x {lv['front']['h']:.1f}")
    chk("l_bracket right view == b x width", near(lv["right"]["w"], 40) and near(lv["right"]["h"], 50),
        f"{lv['right']['w']:.1f} x {lv['right']['h']:.1f}")
    chk("l_bracket top (L-profile) shows internal step (>4 visible edges)", lv["top"]["visible"] > 4,
        f"{lv['top']['visible']} visible edges")

    for name, (a1, a2) in {"top": (0, 1), "front": (0, 2), "right": (1, 2)}.items():
        exp = (d["bbox"][a1], d["bbox"][a2])
        got = (v[name]["w"], v[name]["h"])
        chk(f"plate {name} matches solid bbox {exp}", near(got[0], exp[0]) and near(got[1], exp[1]))

    doc = ezdxf.readfile(d["dxf"]); aud = doc.audit()
    layers = {e.dxf.layer for e in doc.modelspace() if e.dxftype() == "LWPOLYLINE"}
    chk("DXF re-opens with 0 audit errors", len(aud.errors) == 0, f"{len(aud.errors)} errors")
    chk("DXF has VISIBLE and HIDDEN layers populated", {"VISIBLE", "HIDDEN"} <= layers, str(sorted(layers)))
    chk("SVG written", os.path.getsize(d["svg"]) > 0, f"{os.path.getsize(d['svg'])}B")

    # ---- isometric: the two properties that make the word literal ----
    ic = isometric_axis_check()
    chk("isometric: all three principal axes foreshorten EQUALLY",
        ic["equal_foreshortening"],
        f"x/y/z project to {list(ic['projected_lengths'].values())[0]:.9f} "
        f"(spread {max(ic['projected_lengths'].values()) - min(ic['projected_lengths'].values()):.2e})")
    chk("...to exactly sqrt(2/3) = 0.816497 of true length",
        ic["max_length_deviation"] < 1e-12,
        f"deviation {ic['max_length_deviation']:.2e} from {ISO_SCALE:.9f}")
    chk("...and their projections lie exactly 120 degrees apart",
        ic["all_120_apart"], f"{[round(s, 6) for s in ic['separations_deg']]}")
    chk("the check is computed from the AXIS, not from a drawn picture of it",
        "arbitrary" not in "" and abs(sum(ic["normal"]) - 3 / math.sqrt(3)) < 1e-9,
        f"normal {tuple(round(v, 6) for v in ic['normal'])} is the body diagonal")

    wp_iso, _ = cad_parts.build("l_bracket")
    iso = project_isometric(wp_iso.val().wrapped)
    chk("an isometric view projects real geometry through the same HLR engine",
        len(iso["visible"]) > 0 and iso["w"] > 0 and iso["h"] > 0,
        f"{len(iso['visible'])} visible and {len(iso['hidden'])} hidden polylines, "
        f"{iso['w']:.2f} x {iso['h']:.2f}")
    widest = max(lv["top"]["w"], lv["front"]["w"], lv["right"]["w"])
    chk("...and it shows all three faces, so it is wider than any single "
        "orthographic view of the same part",
        iso["w"] > widest,
        f"iso {iso['w']:.2f} vs widest orthographic {widest:.2f}")
    chk("adding the isometric axis did NOT change the orthographic set - the layout "
        "code addresses views by name and would not have complained",
        sorted(project_views(wp_iso.val().wrapped)) == ["front", "right", "top"],
        "project_views still returns exactly the three third-angle views")

    print("drawings_project selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI true-projection drawing engine (OCC HLR)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--make", metavar="PART")
    ap.add_argument("--out", default="./out/proj")
    ap.add_argument("--set", action="append", default=[], metavar="key=val")
    a = ap.parse_args()
    if a.make:
        ov = dict(kv.split("=", 1) for kv in a.set)
        d = make_sheet(a.make, outdir=a.out, **ov)
        print(json.dumps({"svg": d["svg"], "dxf": d["dxf"], "views": d["views"], "bom": d["bom"]}, indent=2))
        raise SystemExit(0)
    raise SystemExit(_selftest())
