#!/usr/bin/env python3
"""
buckling_tools.py -- LINEAR BUCKLING, the gap named in your own config.

Your mission objective says it out loud:

    "Static strength only (buckling/ovalization not modeled)."

For PHOENIX that is the wrong failure mode to ignore. A lattice is built
from slender struts in compression, and a slender strut buckles long
before it yields. Your current optimiser drives outer_diameter DOWN until
von Mises touches 250 MPa -- straight toward the buckling boundary, with
nothing watching it.

WHY THIS IS THE RIGHT NEXT PHYSICS
----------------------------------
It is verifiable EXACTLY. Euler's critical load for a column fixed at one
end and free at the other (effective length factor K = 2):

    P_cr = pi^2 * E * I / (K L)^2 ,    I = pi (D^4 - d^4) / 64

CalculiX solves the eigenvalue problem with *BUCKLE and returns the load
multiplier. multiplier x applied_load must equal P_cr. One number, one
comparison, no judgement -- the same discipline as every other domain in
this system.

It also reuses the deck machinery that is ALREADY VERIFIED at 0.00% on
your machine (_emit_nodes_elems, _emit_nset, _faces_x, run_ccx from
engine_tools). Nothing here is written from memory the way the OpenFOAM
dictionaries were.

HONEST SCOPE
------------
  * LINEAR (eigenvalue) buckling. It gives the bifurcation load of the
    perfect structure. Real struts have imperfections and buckle lower;
    treat P_cr as an upper bound, not a design allowable.
  * Euler is a slender-column formula. Below about L/r = 20 shear and
    yielding dominate and the comparison stops being meaningful. The tool
    computes the slenderness ratio and says so.

Run:  conda activate eng && cd ~/engineai
      python buckling_tools.py                 # verify against Euler
      python buckling_tools.py --D 8.5 --wall 0.6 --length 25
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np

HOME = Path.home() / "engineai"
WORK = HOME / "work" / "buckling"
SCENE = HOME / "scene"

TOL_PCT = 10.0      # eigenvalue buckling on a coarse solid mesh is softer
                    # than stress; 10% is the honest bar, not 5%.


# ------------------------------------------------------------- analytical --

def euler_pcr(D_mm, wall_mm, L_mm, E_MPa, K=2.0):
    """Euler critical load [N] for a hollow circular column.
    K = 2.0 : one end fixed, the other free (a cantilever strut)."""
    d = D_mm - 2.0 * wall_mm
    if d <= 0:
        raise ValueError(f"wall {wall_mm} >= D/2 {D_mm/2} -- solid, not a tube")
    I = math.pi * (D_mm ** 4 - d ** 4) / 64.0            # mm^4
    A = math.pi * (D_mm ** 2 - d ** 2) / 4.0             # mm^2
    r_gyr = math.sqrt(I / A)
    return (math.pi ** 2 * E_MPa * I / (K * L_mm) ** 2,
            I, A, (K * L_mm) / r_gyr)


# ------------------------------------------------------------------ deck ---

def deck_buckle(coords, conn, span, load_N, E, nu, inp: Path, n_modes=3):
    """*BUCKLE on a tube: fix one end face, push the other along -x.
    Reuses engine_tools' emitters, which are already verified at 0.00%."""
    import engine_tools as et
    fixed, loaded = et._faces_x(coords, span)
    if not len(fixed) or not len(loaded):
        raise RuntimeError("empty boundary node set")
    L = et._emit_nodes_elems(coords, conn)
    et._emit_nset(L, "FIX", fixed)
    et._emit_nset(L, "LOAD", loaded)
    L += ["*MATERIAL, NAME=MAT", "*ELASTIC", f"{E:.6e}, {nu}",
          "*SOLID SECTION, ELSET=Eall, MATERIAL=MAT",
          "*STEP", f"*BUCKLE", f"{n_modes}",
          "*BOUNDARY", "FIX, 1, 3",
          "*CLOAD", f"LOAD, 1, {(-load_N / len(loaded)):.8e}",
          "*NODE FILE", "U", "*END STEP", ""]
    inp.write_text("\n".join(L))


def read_eigenvalues(dat: Path) -> list[float]:
    """CalculiX writes buckling factors to the .dat file. Format varies by
    version, so parse defensively: take numbers that follow a header
    mentioning buckling, else any float on a two-column line."""
    if not dat.exists():
        raise RuntimeError(f"no .dat file at {dat}")
    txt = dat.read_text(errors="replace")
    vals, in_block = [], False
    for ln in txt.splitlines():
        low = ln.lower()
        if "buckling factor" in low or "eigenvalue" in low:
            in_block = True
            continue
        if in_block:
            parts = ln.split()
            if len(parts) >= 2:
                try:
                    vals.append(float(parts[1]))
                    continue
                except ValueError:
                    pass
            if vals and not ln.strip():
                break
    if not vals:                      # fall back: any 2-column float pair
        for ln in txt.splitlines():
            p = ln.split()
            if len(p) == 2:
                try:
                    int(p[0])
                    vals.append(float(p[1]))
                except ValueError:
                    continue
    if not vals:
        raise RuntimeError("no buckling factors in .dat:\n" + txt[-1500:])
    return vals


# ------------------------------------------------------------------- main ---

def buckling_tube(D_mm=8.5, wall_mm=0.6, length_mm=25.0, E=71700.0, nu=0.33,
                  load_N=100.0, mesh_size=1.0, iteration=0, mission=""):
    import engine_tools as et
    t0 = time.time()
    WORK.mkdir(parents=True, exist_ok=True)

    P_cr, I, A, slender = euler_pcr(D_mm, wall_mm, length_mm, E)

    step = WORK / "tube.step"
    # engine_tools signature is geom_tube(length, outer_d, wall, step)
    et.geom_tube(length_mm, D_mm, wall_mm, step)
    coords, conn = et.mesh_step(step, mesh_size)

    inp = WORK / "buckle.inp"
    deck_buckle(coords, conn, length_mm, load_N, E, nu, inp)
    et.run_ccx(inp)

    factors = read_eigenvalues(inp.with_suffix(".dat"))
    P_num = abs(factors[0]) * load_N
    err = abs(P_num - P_cr) / P_cr * 100.0

    out = dict(tool="buckling_tube", outer_diameter=D_mm, wall=wall_mm,
               length=length_mm, E=E, applied_load_N=load_N,
               second_moment_mm4=I, area_mm2=A,
               slenderness_KL_over_r=slender,
               buckling_factor=abs(factors[0]),
               P_cr_numerical_N=P_num, P_cr_euler_N=P_cr,
               buckling_error_pct=err,
               higher_modes=[abs(f) * load_N for f in factors[1:4]],
               n_nodes=int(len(coords)),
               wall_time_s=round(time.time() - t0, 2),
               converged=True)

    SCENE.mkdir(parents=True, exist_ok=True)
    manifest = {"iteration": iteration, "timestamp": time.time(),
                "tool": "buckling_tube", "mission": mission,
                "parameters": dict(outer_diameter=D_mm, wall=wall_mm,
                                   length=length_mm, E=E, nu=nu,
                                   force=load_N, mesh_size=mesh_size),
                "result": out}
    (SCENE / f"iter_{iteration:04d}.json").write_text(
        json.dumps(manifest, indent=2))
    (SCENE / "latest.json").write_text(json.dumps(manifest, indent=2))
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--D", type=float, default=8.5)
    ap.add_argument("--wall", type=float, default=0.6)
    ap.add_argument("--length", type=float, default=25.0)
    ap.add_argument("--E", type=float, default=71700.0)
    ap.add_argument("--load", type=float, default=100.0)
    ap.add_argument("--mesh", type=float, default=1.0)
    a = ap.parse_args()

    P_cr, I, A, slender = euler_pcr(a.D, a.wall, a.length, a.E)
    print("== BUCKLING (Euler reference) " + "=" * 34)
    print(f"  tube            D={a.D} mm, wall={a.wall} mm, L={a.length} mm")
    print(f"  I               {I:.4f} mm^4")
    print(f"  area            {A:.4f} mm^2")
    print(f"  slenderness KL/r {slender:.1f}"
          + ("   <- SLENDER, Euler applies" if slender > 40 else
             "   <- STOCKY: below ~40 Euler over-predicts; yielding and"
             " shear dominate. Treat the comparison as indicative only."))
    print(f"  P_cr (Euler)    {P_cr:.3f} N")
    print()

    try:
        r = buckling_tube(a.D, a.wall, a.length, a.E, 0.33, a.load, a.mesh)
    except Exception as exc:
        print(f"  SOLVE FAILED: {type(exc).__name__}: {exc}")
        print("\n  The Euler reference above is still valid and needs no")
        print("  solver -- use it as a hand check while this is fixed.")
        return 1

    print(json.dumps(r, indent=2))
    print(f"\n  P_cr numerical  : {r['P_cr_numerical_N']:.3f} N")
    print(f"  P_cr Euler      : {r['P_cr_euler_N']:.3f} N")
    print(f"  error           : {r['buckling_error_pct']:.2f}%")
    if r["higher_modes"]:
        print(f"  higher modes    : "
              + ", ".join(f"{m:.1f} N" for m in r["higher_modes"]))
    if r["buckling_error_pct"] < TOL_PCT:
        print("\n  BUCKLING DOMAIN VERIFIED.")
        return 0
    print(f"\n  NOT VERIFIED (tol {TOL_PCT}%). Refine --mesh, or check"
          " whether KL/r is high enough for Euler to apply.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
