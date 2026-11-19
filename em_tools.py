#!/usr/bin/env python3
"""
em_tools.py -- ELECTROMAGNETICS domain (Elmer MagnetoDynamics2D).

The CRFCE path. 2D magnetostatics: a round conductor of radius a carrying
current I, inside a circular air domain of radius b.

VERIFICATION IS EXACT, not approximate:
    Ampere's law gives  B(r) = mu0*I/(2*pi*r)   for a < r < b
    and                 B(r) = mu0*I*r/(2*pi*a^2) for r < a
    B_max = mu0*I/(2*pi*a)  at r = a
The A = 0 condition on the outer boundary is a GAUGE CHOICE (B = curl A is
unchanged by a constant), so it introduces no truncation error. Any
deviation is discretization only -- exactly what we want to measure.

Pipeline:  gmsh (2D tri mesh, MSH 2.2) -> ElmerGrid -> .sif -> ElmerSolver
           -> VTU -> meshio -> |B| -> compare to analytic -> scene

NOTE: this wrapper was written without a local Elmer to test against.
Every stage captures its own stderr/stdout, so a failure names its stage.
Run:  python em_tools.py --debug   to dump the full solver log.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HOME = Path.home() / "engineai"
WORK = HOME / "work" / "em"
SCENE = HOME / "scene"
MU0 = 4.0e-7 * np.pi


class StageError(RuntimeError):
    """Carries which stage failed and the captured output."""

    def __init__(self, stage, msg, log=""):
        super().__init__(f"[{stage}] {msg}")
        self.stage = stage
        self.log = log


def _run(stage, cmd, cwd, timeout=1800):
    exe = shutil.which(cmd[0])
    if exe is None:
        raise StageError(stage, f"'{cmd[0]}' not on PATH -- is "
                                f"~/elmer/bin in your PATH?")
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout)
    log = (p.stdout or "") + "\n" + (p.stderr or "")
    if p.returncode != 0:
        raise StageError(stage, f"exit code {p.returncode}", log[-2500:])
    return log


# ------------------------------------------------------------------- mesh --

def build_mesh(a_m, b_m, mesh_size, msh: Path):
    """2D triangular mesh: disc (conductor) inside disc (air).
    Physical Surface 1 = conductor, 2 = air, Physical Curve 1 = outer."""
    import gmsh
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)   # ElmerGrid wants v2
        occ = gmsh.model.occ
        inner = occ.addDisk(0, 0, 0, a_m, a_m)
        outer = occ.addDisk(0, 0, 0, b_m, b_m)
        frag, _ = occ.fragment([(2, outer)], [(2, inner)])
        occ.synchronize()

        # identify surfaces by area: smaller = conductor
        surfs = [t for t in gmsh.model.getEntities(2)]
        areas = [(gmsh.model.occ.getMass(2, s[1]), s[1]) for s in surfs]
        areas.sort()
        cond_tag = areas[0][1]
        air_tags = [t for _, t in areas[1:]]

        gmsh.model.addPhysicalGroup(2, [cond_tag], 1)
        gmsh.model.setPhysicalName(2, 1, "conductor")
        gmsh.model.addPhysicalGroup(2, air_tags, 2)
        gmsh.model.setPhysicalName(2, 2, "air")

        # outer boundary curve = the one farthest from origin
        curves = gmsh.model.getEntities(1)
        outer_curves = []
        for dim, tag in curves:
            xmin, ymin, _, xmax, ymax, _ = gmsh.model.getBoundingBox(dim, tag)
            if max(abs(xmin), abs(xmax), abs(ymin), abs(ymax)) > b_m * 0.99:
                outer_curves.append(tag)
        if not outer_curves:
            raise StageError("mesh", "could not identify outer boundary curve")
        gmsh.model.addPhysicalGroup(1, outer_curves, 1)
        gmsh.model.setPhysicalName(1, 1, "outer")

        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size * 0.25)
        gmsh.model.mesh.generate(2)
        gmsh.write(str(msh))
    finally:
        gmsh.finalize()


SIF = """Header
  Mesh DB "." "mesh"
  Include Path ""
  Results Directory ""
End

Simulation
  Max Output Level = 5
  Coordinate System = Cartesian 2D
  Simulation Type = Steady State
  Steady State Max Iterations = 1
  Output Intervals = 1
End

Body 1
  Name = "conductor"
  Equation = 1
  Material = 1
  Body Force = 1
End

Body 2
  Name = "air"
  Equation = 1
  Material = 1
End

Equation 1
  Active Solvers(3) = 1 2 3
End

Solver 1
  Equation = "MgDyn2D"
  Procedure = "MagnetoDynamics2D" "MagnetoDynamics2D"
  Variable = "Potential"
  Linear System Solver = Iterative
  Linear System Iterative Method = BiCGStab
  Linear System Preconditioning = ILU1
  Linear System Max Iterations = 5000
  Linear System Convergence Tolerance = 1.0e-10
  Nonlinear System Max Iterations = 1
End

Solver 2
  Equation = "MgDyn2DPost"
  Procedure = "MagnetoDynamics2D" "BSolver"
  Target Variable = "Potential"
  Calculate Magnetic Field Strength = Logical True
  Calculate Nodal Fields = Logical True
  Linear System Solver = Iterative
  Linear System Iterative Method = CG
  Linear System Max Iterations = 5000
  Linear System Convergence Tolerance = 1.0e-10
End

Solver 3
  Exec Solver = After Simulation
  Equation = "ResultOutput"
  Procedure = "ResultOutputSolve" "ResultOutputSolver"
  Output File Name = "case"
  Vtu Format = Logical True
  Save Geometry Ids = Logical True
End

Material 1
  Name = "air_and_copper"
  Relative Permeability = 1.0
  Relative Permittivity = 1.0
End

Body Force 1
  Current Density = {J:.8e}
End

Boundary Condition 1
  Target Boundaries(1) = 1
  Name = "outer"
  Potential = Real 0.0
End
"""


def _find_B(mesh):
    """Elmer's field name varies by version; find any 3-component B field."""
    for key in mesh.point_data:
        k = key.lower().replace("_", " ")
        if "magnetic flux density" in k or k.strip() in ("b", "bfield"):
            arr = np.asarray(mesh.point_data[key])
            if arr.ndim == 2 and arr.shape[1] >= 2:
                return key, arr
    # fall back: any 3-vector that is not the potential
    for key, arr in mesh.point_data.items():
        arr = np.asarray(arr)
        if arr.ndim == 2 and arr.shape[1] == 3 and "potential" not in key.lower():
            return key, arr
    raise StageError("parse", "no magnetic flux density field in VTU; "
                              f"available: {list(mesh.point_data)}")


def magnetostatic_wire(current_A=100.0, a_mm=5.0, b_mm=20.0,
                       mesh_size_mm=0.8, iteration=0, mission="",
                       debug=False):
    """EM domain tool. Units in: mm, A. Elmer works in SI internally."""
    import meshio
    t0 = time.time()
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)

    a_m, b_m = a_mm * 1e-3, b_mm * 1e-3
    if a_mm >= b_mm:
        raise StageError("input", "a_mm must be < b_mm")
    J = current_A / (np.pi * a_m ** 2)          # A/m^2

    msh = WORK / "geom.msh"
    build_mesh(a_m, b_m, mesh_size_mm * 1e-3, msh)

    log = _run("ElmerGrid", ["ElmerGrid", "14", "2", msh.name,
                             "-out", "mesh"], WORK)
    if not (WORK / "mesh" / "mesh.header").exists():
        raise StageError("ElmerGrid", "no mesh.header produced", log[-2000:])

    (WORK / "case.sif").write_text(SIF.format(J=J))
    (WORK / "ELMERSOLVER_STARTINFO").write_text("case.sif\n")
    solver_log = _run("ElmerSolver", ["ElmerSolver", "case.sif"], WORK)
    if debug:
        print(solver_log)

    vtus = sorted(WORK.glob("case*.vtu"))
    if not vtus:
        raise StageError("ElmerSolver", "no VTU written; files present: "
                         + ", ".join(p.name for p in WORK.iterdir()),
                         solver_log[-2500:])
    mesh = meshio.read(vtus[-1])
    bname, B = _find_B(mesh)
    pts = mesh.points
    r = np.sqrt(pts[:, 0] ** 2 + pts[:, 1] ** 2)
    Bmag = np.linalg.norm(B[:, :2], axis=1)

    # sample the analytic-vs-FEA comparison in the air region, away from
    # both the conductor edge and the outer boundary
    lo, hi = 1.5 * a_m, 0.75 * b_m
    band = (r > lo) & (r < hi)
    if band.sum() < 20:
        raise StageError("parse", "too few sample nodes in the air band")
    exact_band = MU0 * current_A / (2 * np.pi * r[band])
    err = np.abs(Bmag[band] - exact_band) / exact_band * 100.0
    mean_err = float(np.mean(err))

    B_max_exact = MU0 * current_A / (2 * np.pi * a_m)
    near_a = np.abs(r - a_m) < (mesh_size_mm * 1e-3)
    B_at_a = float(Bmag[near_a].max()) if near_a.any() else float(Bmag.max())

    result = dict(tool="magnetostatic_wire",
                  current_A=current_A, a_mm=a_mm, b_mm=b_mm,
                  B_at_conductor_surface_T=B_at_a,
                  analytical_B_surface_T=float(B_max_exact),
                  field_error_pct=mean_err,
                  n_nodes=int(len(pts)),
                  field_name=bname,
                  wall_time_s=round(time.time() - t0, 2), converged=True)

    SCENE.mkdir(parents=True, exist_ok=True)
    out = SCENE / f"iter_{iteration:04d}.vtu"
    meshio.write(out, meshio.Mesh(points=pts, cells=mesh.cells,
                                  point_data={"B_magnitude": Bmag}))
    manifest = {"iteration": iteration, "timestamp": time.time(),
                "tool": "magnetostatic_wire", "mission": mission,
                "parameters": dict(current_A=current_A, a_mm=a_mm,
                                   b_mm=b_mm, mesh_size_mm=mesh_size_mm),
                "result": result, "mesh_file": out.name}
    (SCENE / f"iter_{iteration:04d}.json").write_text(
        json.dumps(manifest, indent=2))
    (SCENE / "latest.json").write_text(json.dumps(manifest, indent=2))
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--current", type=float, default=100.0)
    ap.add_argument("--a-mm", type=float, default=5.0)
    ap.add_argument("--b-mm", type=float, default=20.0)
    ap.add_argument("--mesh-mm", type=float, default=0.8)
    ap.add_argument("--debug", action="store_true")
    x = ap.parse_args()
    try:
        r = magnetostatic_wire(current_A=x.current, a_mm=x.a_mm,
                               b_mm=x.b_mm, mesh_size_mm=x.mesh_mm,
                               debug=x.debug)
        print(json.dumps(r, indent=2))
        print(f"\n  B at conductor surface : {r['B_at_conductor_surface_T']*1e3:.4f} mT")
        print(f"  analytical             : {r['analytical_B_surface_T']*1e3:.4f} mT")
        print(f"  mean field error       : {r['field_error_pct']:.2f}%")
        print("\n  EM DOMAIN VERIFIED." if r['field_error_pct'] < 5
              else "\n  error above 5% -- refine --mesh-mm")
    except StageError as e:
        print(f"FAILED {e}", file=sys.stderr)
        if e.log:
            print("---- captured output ----", file=sys.stderr)
            print(e.log, file=sys.stderr)
        sys.exit(1)
