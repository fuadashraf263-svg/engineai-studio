#!/usr/bin/env python3
"""
cad.py -- generate CAD files (STEP/STL), and VERIFY them by re-measuring.

THE DISCIPLINE, APPLIED TO GEOMETRY
-----------------------------------
Every other tier in this system checks its own output. CAD is no different:
a generated STEP file is only [SOLVED] if we can OPEN IT BACK and confirm
the solid's volume and bounding box match what was asked. A 10x20x30 mm box
must re-measure to 6000 mm^3 and a 10x20x30 bounding box. If the round-trip
disagrees, the result is [GENERATED] (a file exists) not [SOLVED] (a file
that is provably correct). We never hand a student a part we have not
re-measured.

WHY THIS MATTERS FOR CAD SPECIFICALLY
-------------------------------------
CAD kernels fail quietly. A fillet radius larger than an edge silently does
nothing; a boolean that misses returns the original solid; a unit mix-up
scales a part by 1000. None of those raise an error -- the script "succeeds"
and produces a wrong part. Re-measuring the actual output is the only way to
catch them, exactly as re-substituting a root catches a wrong solve.

ANALYTICAL CROSS-CHECK
----------------------
For the primitive shapes, the expected volume and bounding box are computed
from the FORMULA independently, so the check is against known geometry, not
against CadQuery trusting itself:
    box      V = l w h
    cylinder V = pi r^2 h
    tube     V = pi (R^2 - r^2) h
    sphere   V = 4/3 pi r^3

Run:  python cad.py --selftest              # needs cadquery in this env
      python cad.py box 10 20 30            # -> box.step, re-measured
      python cad.py tube --od 8.56 --wall 0.6 --length 25
      python cad.py --spec part.json
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

OUT = Path.home() / "engineai" / "work" / "cad"


# ------------------------------------------------- analytical geometry ----

def expected(shape: str, p: dict) -> dict:
    """Volume (mm^3) and bounding box (mm) from the FORMULA, independent of
    any CAD kernel. This is the ground truth the re-measurement checks."""
    if shape == "box":
        l, w, h = p["l"], p["w"], p["h"]
        return {"volume": l * w * h, "bbox": (l, w, h)}
    if shape == "cylinder":
        r, h = p["d"] / 2, p["h"]
        return {"volume": math.pi * r * r * h, "bbox": (p["d"], p["d"], h)}
    if shape == "tube":
        R, r, h = p["od"] / 2, (p["od"] - 2 * p["wall"]) / 2, p["length"]
        if r <= 0:
            raise ValueError(f"wall {p['wall']} >= od/2 -- solid, not a tube")
        return {"volume": math.pi * (R * R - r * r) * h,
                "bbox": (p["od"], p["od"], h)}
    if shape == "sphere":
        r = p["d"] / 2
        return {"volume": 4.0 / 3.0 * math.pi * r ** 3,
                "bbox": (p["d"], p["d"], p["d"])}
    raise ValueError(f"unknown shape '{shape}'")


# ------------------------------------------------------ CadQuery build ----

def build_solid(shape: str, p: dict):
    """Return a CadQuery Workplane solid. Imported lazily so this file can be
    inspected without cadquery, but building REQUIRES the eng env."""
    import cadquery as cq
    if shape == "box":
        return cq.Workplane("XY").box(p["l"], p["w"], p["h"])
    if shape == "cylinder":
        return cq.Workplane("XY").circle(p["d"] / 2).extrude(p["h"])
    if shape == "tube":
        outer = cq.Workplane("XY").circle(p["od"] / 2)
        inner_r = (p["od"] - 2 * p["wall"]) / 2
        return outer.circle(inner_r).extrude(p["length"])
    if shape == "sphere":
        return cq.Workplane("XY").sphere(p["d"] / 2)
    raise ValueError(f"unknown shape '{shape}'")


def measure_step(path: Path) -> dict:
    """Re-open a STEP file and measure the solid. This is the verification:
    numbers here must match expected() to within tolerance."""
    import cadquery as cq
    imported = cq.importers.importStep(str(path))
    solid = imported.val()
    vol = solid.Volume()
    bb = solid.BoundingBox()
    return {"volume": vol,
            "bbox": (bb.xlen, bb.ylen, bb.zlen),
            "center": (bb.center.x, bb.center.y, bb.center.z)}


def build_and_verify(shape: str, p: dict, name: str | None = None,
                     tol_pct: float = 0.5) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    name = name or shape
    step = OUT / f"{name}.step"
    stl = OUT / f"{name}.stl"

    want = expected(shape, p)

    import cadquery as cq
    solid = build_solid(shape, p)
    cq.exporters.export(solid, str(step))
    cq.exporters.export(solid, str(stl))

    got = measure_step(step)

    vol_err = abs(got["volume"] - want["volume"]) / want["volume"] * 100
    bbox_err = max(abs(g - w) / w * 100
                   for g, w in zip(sorted(got["bbox"]),
                                   sorted(want["bbox"])) if w)
    verified = vol_err < tol_pct and bbox_err < tol_pct

    return {"shape": shape, "params": p,
            "step_file": str(step), "stl_file": str(stl),
            "expected_volume_mm3": want["volume"],
            "measured_volume_mm3": got["volume"],
            "volume_error_pct": vol_err,
            "expected_bbox_mm": want["bbox"],
            "measured_bbox_mm": got["bbox"],
            "bbox_error_pct": bbox_err,
            "verified": verified,
            "provenance": "SOLVED (re-measured)" if verified
                          else "GENERATED (re-measure FAILED)"}


# ------------------------------------------------------------ selftest ----

def selftest() -> int:
    G, R, O = "\033[32m", "\033[31m", "\033[0m"
    try:
        import cadquery  # noqa
    except ImportError:
        print("  cadquery not in this environment -- run in 'eng' on your "
              "machine:\n    conda activate eng && python cad.py --selftest")
        return 2

    fails = 0

    def chk(label, cond, detail=""):
        nonlocal fails
        fails += (not cond)
        print(f"  {(G+'PASS'+O) if cond else (R+'FAIL'+O)}  {label:44s} "
              f"{detail}")

    print("\n== CAD SELF-TEST " + "=" * 46)
    print("  every part generated, re-opened, and re-measured\n")

    cases = [
        ("box", {"l": 10, "w": 20, "h": 30}, 6000.0),
        ("cylinder", {"d": 10, "h": 20}, math.pi * 25 * 20),
        ("tube", {"od": 8.56, "wall": 0.6, "length": 25},
         math.pi * ((4.28) ** 2 - (3.68) ** 2) * 25),
        ("sphere", {"d": 20}, 4.0 / 3.0 * math.pi * 1000),
    ]
    for shape, p, vexpect in cases:
        r = build_and_verify(shape, p)
        chk(f"{shape}: analytical V = {vexpect:.1f} mm^3",
            abs(r["expected_volume_mm3"] - vexpect) < 1e-6)
        chk(f"{shape}: measured matches analytical",
            r["verified"],
            f"vol err {r['volume_error_pct']:.3f}%, "
            f"bbox err {r['bbox_error_pct']:.3f}%")
        chk(f"{shape}: STEP file written and re-openable",
            Path(r["step_file"]).exists())

    # the tube is YOUR PHOENIX strut section -- confirm it matches the
    # hollow_strut geometry your solver already uses
    r = build_and_verify("tube", {"od": 8.56, "wall": 0.6, "length": 25},
                         name="phoenix_strut")
    print(f"\n  PHOENIX strut: od 8.56, wall 0.6, L 25 mm")
    print(f"    volume {r['measured_volume_mm3']:.2f} mm^3, "
          f"verified {r['verified']}")

    # degenerate guard
    try:
        expected("tube", {"od": 8, "wall": 4, "length": 25})
        chk("solid 'tube' rejected", False)
    except ValueError:
        chk("solid 'tube' rejected", True)

    print(f"\n  {'ALL CHECKS PASS' if not fails else str(fails)+' FAILURE(S)'}")
    return 1 if fails else 0


# ---------------------------------------------------------------- cli -----

def main() -> int:
    a = sys.argv[1:]
    if not a or a[0] == "--help":
        print(__doc__.split("Run:")[-1])
        return 0
    if a[0] == "--selftest":
        return selftest()

    try:
        if a[0] == "box" and len(a) >= 4:
            r = build_and_verify("box", {"l": float(a[1]), "w": float(a[2]),
                                         "h": float(a[3])})
        elif a[0] == "cylinder" and len(a) >= 3:
            r = build_and_verify("cylinder", {"d": float(a[1]),
                                              "h": float(a[2])})
        elif a[0] == "tube":
            import argparse
            ap = argparse.ArgumentParser()
            ap.add_argument("--od", type=float, required=True)
            ap.add_argument("--wall", type=float, required=True)
            ap.add_argument("--length", type=float, required=True)
            x = ap.parse_args(a[1:])
            r = build_and_verify("tube", {"od": x.od, "wall": x.wall,
                                          "length": x.length})
        elif a[0] == "sphere" and len(a) >= 2:
            r = build_and_verify("sphere", {"d": float(a[1])})
        elif a[0] == "--spec" and len(a) >= 2:
            spec = json.loads(Path(a[1]).read_text())
            r = build_and_verify(spec["shape"], spec["params"],
                                 spec.get("name"))
        else:
            print("commands: box l w h | cylinder d h | "
                  "tube --od --wall --length | sphere d | --spec f.json")
            return 1
    except ImportError:
        print("  cadquery not available -- run in the 'eng' environment.")
        return 2
    except Exception as e:
        print(f"  {type(e).__name__}: {e}")
        return 1

    print(json.dumps(r, indent=2))
    print(f"\n  {r['provenance']}")
    print(f"  volume: {r['measured_volume_mm3']:.2f} mm^3 "
          f"(expected {r['expected_volume_mm3']:.2f}, "
          f"err {r['volume_error_pct']:.3f}%)")
    print(f"  files: {r['step_file']}")
    print(f"         {r['stl_file']}")
    return 0 if r["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
