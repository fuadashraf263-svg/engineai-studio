#!/usr/bin/env python3
"""
cfd_tools.py  v2 -- CFD domain verification, self-diagnosing.

WHAT CHANGED FROM v1 AND WHY
----------------------------
v1 returned u_max = 4.2345 mm/s against an analytical 6.2500 mm/s: a
uniform factor of 0.678, converged, on a mesh far too fine for 32%
numerical error. That is a SETUP or SAMPLING error, not a solver error.

v1 assumed blockMesh cell ordering is x-fastest-then-y and indexed the
raw internalField list directly. That assumption is the weakest link in
the tool, so v2 deletes it: OpenFOAM is asked to write its own cell
centres (Cx, Cy) and every velocity is paired with its real coordinate.
Ordering is now irrelevant.

v2 also MEASURES, rather than assumes, the things that would each produce
exactly this kind of uniform deficit:

  D1  the ACTUAL pressure gradient in the field, from a least-squares fit
      of p(x). If the boundary condition is not delivering the imposed
      dp/dx, u scales down by precisely that ratio and D1 says so.
  D2  profiles at FOUR x-stations, so entrance development is visible
      rather than inferred.
  D3  a shape test independent of magnitude: the profile normalised by
      its own peak, against 1-(y/h)^2. If the shape is right and only the
      magnitude is wrong, the fault is the driving force -- not the
      discretisation, not the sampling.
  D4  flow rate, against the exact Q = (2/3) u_max (2h) w.

Run:  conda activate eng && cd ~/engineai
      python cfd_tools.py --probe
      python cfd_tools.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ------------------------------------------------------------------ config --

FOAM_BASHRC = Path("/opt/openfoam13/etc/bashrc")
HOME = Path.home() / "engineai"
WORK = HOME / "work" / "cfd_poiseuille"

H_HALF = 0.005        # channel half-height [m]
LENGTH = 0.200        # channel length      [m]
DEPTH = 0.001         # spanwise depth      [m]
NU = 1.0e-5           # kinematic viscosity [m2/s]
DP_KIN = 1.0e-3       # imposed kinematic dp inlet->outlet [m2/s2]

NX, NY = 200, 40
STATIONS = (0.30, 0.50, 0.70, 0.90)   # fractions of LENGTH

TOL_PCT = 5.0
D_HYD = 4.0 * H_HALF   # hydraulic diameter, infinite parallel plates

END_ITER = 20000       # fixed march; convergence judged on the ANSWER
WRITE_INTERVAL = 2000  # ten snapshots
SETTLE_PCT = 0.20      # u_max must move less than this between last two


def exact_umax(gradient: float | None = None) -> float:
    """u_max = G h^2 / (2 nu). G defaults to the IMPOSED gradient."""
    G = gradient if gradient is not None else DP_KIN / LENGTH
    return G * H_HALF**2 / (2.0 * NU)


# ------------------------------------------------------------ case writing --

HEADER = ("/*--------------------------------*- C++ -*------------------"
          "--------------*\\\n| EngineAI generated case |\n"
          "\\*----------------------------------------------------------"
          "-----------------*/\n")

CONTROL_DICT = """\
FoamFile {{ version 2.0; format ascii; class dictionary; object controlDict; }}

application     foamRun;
solver          incompressibleFluid;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {end};
deltaT          1;
writeControl    runTime;
writeInterval   {wint};
purgeWrite      0;
writeFormat     ascii;
writePrecision  10;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable false;
"""

FV_SCHEMES = """\
FoamFile {{ version 2.0; format ascii; class dictionary; object fvSchemes; }}

ddtSchemes      {{ default steadyState; }}
gradSchemes     {{ default Gauss linear; }}
divSchemes
{{
    default                        none;
    div(phi,U)                     bounded Gauss linearUpwind grad(U);
    div((nuEff*dev2(T(grad(U)))))  Gauss linear;
}}
laplacianSchemes     {{ default Gauss linear corrected; }}
interpolationSchemes {{ default linear; }}
snGradSchemes        {{ default corrected; }}
"""

FV_SOLUTION = """\
FoamFile {{ version 2.0; format ascii; class dictionary; object fvSolution; }}

solvers
{{
    p {{ solver GAMG; tolerance 1e-12; relTol 0.001; smoother GaussSeidel; }}
    U {{ solver smoothSolver; smoother symGaussSeidel;
        tolerance 1e-14; relTol 0.001; }}
}}

SIMPLE
{{
    nNonOrthogonalCorrectors 0;
    // NO residualControl. OpenFOAM's normalised initial residuals dropped
    // below 1e-8/1e-9 at iteration 1654 while the velocity field was still
    // creeping upward -- it reported "converged" at 69% of the analytical
    // answer. Convergence is now judged on the ANSWER, not the residual:
    // cfd_tools reads u_max at successive write times and requires it to
    // have stopped moving.
}}

relaxationFactors
{{
    // 0.7/0.3 -- PROVEN stable on this case (ran to 1654 iters cleanly).
    // Raising these to 0.9/0.5 diverged: continuity error reached 1e+102
    // and GAMG threw a floating-point exception at iteration 745.
    // Faster relaxation is not worth an unstable solver.
    equations {{ U 0.7; }}
    fields    {{ p 0.3; }}
}}
"""

BLOCK_MESH = """\
FoamFile {{ version 2.0; format ascii; class dictionary; object blockMeshDict; }}

scale 1;

vertices
(
    (0   {ymin} 0)   ({L} {ymin} 0)   ({L} {ymax} 0)   (0 {ymax} 0)
    (0   {ymin} {d}) ({L} {ymin} {d}) ({L} {ymax} {d}) (0 {ymax} {d})
);

blocks ( hex (0 1 2 3 4 5 6 7) ({nx} {ny} 1) simpleGrading (1 1 1) );

edges ();

boundary
(
    inlet  {{ type patch; faces ( (0 4 7 3) ); }}
    outlet {{ type patch; faces ( (1 2 6 5) ); }}
    walls  {{ type wall;  faces ( (0 1 5 4) (3 7 6 2) ); }}
    frontAndBack {{ type empty; faces ( (0 3 2 1) (4 5 6 7) ); }}
);
"""

PHYSICAL_PROPERTIES = """\
FoamFile {{ version 2.0; format ascii; class dictionary; \
location "constant"; object physicalProperties; }}

viscosityModel  constant;
nu              {nu};
"""

MOMENTUM_TRANSPORT = """\
FoamFile {{ version 2.0; format ascii; class dictionary; \
location "constant"; object momentumTransport; }}

simulationType  laminar;
"""

FIELD_U = """\
FoamFile {{ version 2.0; format ascii; class volVectorField; \
location "0"; object U; }}

dimensions      [0 1 -1 0 0 0 0];
internalField   uniform (0 0 0);

boundaryField
{{
    inlet        {{ type zeroGradient; }}
    outlet       {{ type zeroGradient; }}
    walls        {{ type noSlip; }}
    frontAndBack {{ type empty; }}
}}
"""

FIELD_P = """\
FoamFile {{ version 2.0; format ascii; class volScalarField; \
location "0"; object p; }}

dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;

boundaryField
{{
    inlet        {{ type fixedValue; value uniform {pin}; }}
    outlet       {{ type fixedValue; value uniform 0; }}
    walls        {{ type zeroGradient; }}
    frontAndBack {{ type empty; }}
}}
"""


def write_case(end_iter: int = END_ITER, wint: int = WRITE_INTERVAL) -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    for sub in ("system", "constant", "0"):
        (WORK / sub).mkdir(parents=True)
    files = {
        "system/controlDict": CONTROL_DICT.format(end=end_iter, wint=wint),
        "system/fvSchemes": FV_SCHEMES.format(),
        "system/fvSolution": FV_SOLUTION.format(),
        "system/blockMeshDict": BLOCK_MESH.format(
            L=LENGTH, ymin=-H_HALF, ymax=H_HALF, d=DEPTH, nx=NX, ny=NY),
        "constant/physicalProperties": PHYSICAL_PROPERTIES.format(nu=NU),
        "constant/momentumTransport": MOMENTUM_TRANSPORT.format(),
        "0/U": FIELD_U.format(),
        "0/p": FIELD_P.format(pin=DP_KIN),
    }
    for rel, body in files.items():
        (WORK / rel).write_text(HEADER + body)


# ------------------------------------------------------------- foam runner --

def foam(cmd: str, cwd: Path, timeout: int = 1800):
    return subprocess.run(
        ["bash", "-c", f"source {FOAM_BASHRC} >/dev/null 2>&1 && {cmd}"],
        cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


DIVERGE_LIMIT = 1.0e3


def check_divergence(log: str):
    """Read OpenFOAM's own continuity errors. A run that is blowing up says
    so long before it crashes -- catch it instead of waiting for sigFpe."""
    worst = 0.0
    for ln in log.splitlines():
        if "continuity errors" in ln and "cumulative" in ln:
            try:
                worst = max(worst, abs(float(ln.split("cumulative =")[1]
                                             .split()[0].rstrip(","))))
            except Exception:
                continue
    # Match the CRASH signature, not the startup banner. OpenFOAM prints
    #   "sigFpe : Enabling floating point exception trapping (FOAM_SIGFPE)."
    # on every healthy run -- matching bare "sigFpe" flagged a perfectly
    # converged solution (continuity 3.8e-06, residuals 1e-16) as diverged.
    # The real crash prints "Foam::sigFpe::sigHandler(int)" in a stack trace.
    fatal = ("sigFpe::sigHandler" in log
             or "FOAM FATAL ERROR" in log
             or "FOAM FATAL IO ERROR" in log)
    return worst, (worst > DIVERGE_LIMIT or fatal)


def tail(text: str, n: int = 30) -> str:
    return "\n".join([l for l in text.splitlines() if l.strip()][-n:])


# ----------------------------------------------------------- field readers --

VEC = re.compile(r"\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)")
NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _internal_block(path: Path) -> tuple[str, str]:
    txt = path.read_text()
    i = txt.find("internalField")
    if i < 0:
        raise ValueError(f"no internalField in {path}")
    j = txt.find("boundaryField", i)
    body = txt[i:j if j > 0 else len(txt)]
    return body, body.split("\n")[0]


def read_vector_field(path: Path, n_expected: int):
    body, head = _internal_block(path)
    if "nonuniform" not in head and "uniform" in head:
        m = VEC.search(body)
        return [tuple(map(float, m.groups()))] * n_expected
    inner = body[body.find("("):body.rfind(")")]
    return [tuple(map(float, m.groups())) for m in VEC.finditer(inner)]


def read_scalar_field(path: Path, n_expected: int):
    body, head = _internal_block(path)
    if "nonuniform" not in head and "uniform" in head:
        tok = head.split("uniform")[1]
        return [float(NUM.search(tok).group())] * n_expected
    inner = body[body.find("(") + 1:body.rfind(")")]
    return [float(x) for x in NUM.findall(inner)]


# ------------------------------------------------------------- diagnostics --

def fit_slope(xs, ys) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
            if den else 0.0)


def read_cell_centres(tdir: Path, n: int):
    """Return (xs, ys). OpenFOAM 13's writeCellCentres emits C (vector) plus
    Ccx/Ccy/Ccz. Older versions used Cx/Cy/Cz. Try all, prefer the vector."""
    if (tdir / "C").exists():
        C = read_vector_field(tdir / "C", n)
        if len(C) == n:
            return [c[0] for c in C], [c[1] for c in C]
    for xn, yn in (("Ccx", "Ccy"), ("Cx", "Cy")):
        if (tdir / xn).exists() and (tdir / yn).exists():
            xs = read_scalar_field(tdir / xn, n)
            ys = read_scalar_field(tdir / yn, n)
            if len(xs) == n and len(ys) == n:
                return xs, ys
    raise FileNotFoundError(
        "no cell-centre field found. Directory contains: "
        + str(sorted(p.name for p in tdir.iterdir())))


def station_profile(cells, x_target, _tol=None):
    """All (y, u) in the mesh column NEAREST x_target.

    Deliberately not a distance window: a window of width dx straddles two
    columns and silently doubles the sample. Snap to the real column.
    """
    xs = sorted({round(c[0], 9) for c in cells})
    if not xs:
        return []
    x_col = min(xs, key=lambda x: abs(x - x_target))
    sel = [(y, u) for (x, y, u) in cells if round(x, 9) == x_col]
    sel.sort(key=lambda t: t[0])
    return sel


# ------------------------------------------------------------------- main ---

def probe() -> int:
    if not FOAM_BASHRC.exists():
        print(f"  FOAM NOT FOUND at {FOAM_BASHRC}")
        return 1
    r = foam("echo $WM_PROJECT_VERSION", Path.home(), timeout=60)
    print("OpenFOAM version:", r.stdout.strip() or "unknown")
    r2 = foam("which foamRun blockMesh postProcess", Path.home(), timeout=60)
    print(r2.stdout.strip())
    return 0


def main() -> int:
    global NU, END_ITER
    if "--probe" in sys.argv:
        return probe()
    # --nu X : the DECISIVE test. u_max = G h^2 / (2 nu), so doubling nu must
    # halve u_max EXACTLY. If it does, nu is being applied as specified and
    # the 32% deficit is not viscosity. If it does not, nu is the fault.
    if "--nu" in sys.argv:
        NU = float(sys.argv[sys.argv.index("--nu") + 1])
        print(f"  [nu override] nu = {NU:g} m2/s")
    if "--iters" in sys.argv:
        END_ITER = int(sys.argv[sys.argv.index("--iters") + 1])
        print(f"  [iters override] endTime = {END_ITER}")
    if not FOAM_BASHRC.exists():
        print(f"  CFD SKIPPED -- OpenFOAM not found at {FOAM_BASHRC}")
        return 2

    write_case(END_ITER, WRITE_INTERVAL)

    solver_out = ""
    for cmd in ("blockMesh", "foamRun -solver incompressibleFluid"):
        r = foam(cmd, WORK)
        full = r.stdout + r.stderr
        if cmd.startswith("foamRun"):
            worst, diverged = check_divergence(full)
            if diverged:
                print(f"  SOLVER DIVERGED -- worst cumulative continuity "
                      f"error {worst:.3g} (limit {DIVERGE_LIMIT:g})")
                print("  This is a stability failure, not a physics result.")
                print("  Lower the relaxation factors in FV_SOLUTION.")
                print(tail(full, 15))
                return 1
        if r.returncode != 0:
            print(f"  {cmd.split()[0]} FAILED\n" + tail(full))
            return 1
        solver_out = r.stdout
    converged = "SIMPLE solution converged" in solver_out
    worst_cont, _ = check_divergence(solver_out)

    # --- ask OpenFOAM for its own cell centres: no ordering assumption ------
    rc = foam("postProcess -func writeCellCentres -latestTime", WORK)
    if rc.returncode != 0:
        print("  writeCellCentres FAILED -- cannot verify without coordinates")
        print(tail(rc.stdout + rc.stderr))
        return 1

    times = sorted(float(p.name) for p in WORK.iterdir()
                   if p.is_dir() and re.fullmatch(r"[\d.]+", p.name)
                   and p.name != "0")
    if not times:
        print("  no time directory written\n" + tail(solver_out))
        return 1

    def tdir_of(t):
        return WORK / (str(int(t)) if float(t).is_integer() else str(t))

    tdir = tdir_of(times[-1])
    ncell = NX * NY
    try:
        CX, CY = read_cell_centres(tdir, ncell)
    except (FileNotFoundError, ValueError) as e:
        print(f"  cell centres unreadable: {e}")
        print("  contents:", sorted(p.name for p in tdir.iterdir()))
        return 1

    # --- SETTLING TRAJECTORY: is the ANSWER still moving? -------------------
    # The mesh is identical at every time, so one set of coordinates serves
    # all snapshots. This is the check that residualControl failed to make.
    traj = []
    for t in times:
        d = tdir_of(t)
        if not (d / "U").exists():
            continue
        Ut = read_vector_field(d / "U", ncell)
        if len(Ut) != ncell:
            continue
        cellst = [(CX[i], CY[i], Ut[i][0]) for i in range(ncell)]
        pr = station_profile(cellst, 0.5 * LENGTH)
        if pr:
            traj.append((t, max(u for _, u in pr)))

    if len(traj) < 2:
        print("  fewer than two snapshots -- cannot judge settling")
        settled_pct = float("nan")
    else:
        settled_pct = abs(traj[-1][1] - traj[-2][1]) / traj[-1][1] * 100

    U = read_vector_field(tdir / "U", ncell)
    P = read_scalar_field(tdir / "p", ncell)
    for nm, arr in (("U", U), ("p", P), ("Cx", CX), ("Cy", CY)):
        if len(arr) != ncell:
            print(f"  {nm}: got {len(arr)} values, expected {ncell}")
            return 1

    cells = [(CX[i], CY[i], U[i][0]) for i in range(ncell)]

    # --- D0: the MESH ITSELF, from OpenFOAM's own cell centres -------------
    # nu, gradient and convergence are all verified. Geometry never was.
    # Cell centres sit half a cell inside each wall, so the implied domain
    # half-height is max(|Cy|) + dy/2.
    ys = sorted({round(y, 12) for _, y, _ in cells})
    xs_u = sorted({round(x, 12) for x, _, _ in cells})
    dy_mesh = (ys[-1] - ys[0]) / (len(ys) - 1) if len(ys) > 1 else 0.0
    h_implied = (max(abs(ys[0]), abs(ys[-1])) + dy_mesh / 2) if ys else 0.0
    dx_mesh = ((xs_u[-1] - xs_u[0]) / (len(xs_u) - 1)
               if len(xs_u) > 1 else 0.0)
    L_implied = (xs_u[-1] - xs_u[0]) + dx_mesh
    geom_ok = (abs(h_implied - H_HALF) / H_HALF < 0.01
               and abs(L_implied - LENGTH) / LENGTH < 0.01)
    # what half-height WOULD explain the measured u_max?


    # --- D1: what pressure gradient does the FIELD actually have? ----------
    pbar: dict[float, list[float]] = {}
    for i in range(ncell):
        pbar.setdefault(round(CX[i], 9), []).append(P[i])
    px = sorted(pbar)
    py = [sum(pbar[x]) / len(pbar[x]) for x in px]
    mid = [(x, p) for x, p in zip(px, py) if 0.2 * LENGTH < x < 0.95 * LENGTH]
    dpdx_measured = -fit_slope([m[0] for m in mid], [m[1] for m in mid])
    dpdx_imposed = DP_KIN / LENGTH
    grad_ratio = dpdx_measured / dpdx_imposed if dpdx_imposed else 0.0

    umax_imposed = exact_umax()
    umax_meas_grad = exact_umax(dpdx_measured)

    dx = LENGTH / NX
    dy = 2 * H_HALF / NY

    # --- D2 + D3: profiles at four stations --------------------------------
    report, shape_errs = [], []
    for frac in STATIONS:
        prof = station_profile(cells, frac * LENGTH, dx)
        if not prof:
            continue
        pk = max(u for _, u in prof) or 1e-30
        errs_abs, errs_shape = [], []
        for y, u in prof:
            shape_ex = 1 - (y / H_HALF) ** 2
            if shape_ex > 0.05:
                ex = umax_imposed * shape_ex
                errs_abs.append(abs(u - ex) / ex * 100)
                errs_shape.append(abs(u / pk - shape_ex) / shape_ex * 100)
        report.append({
            "x_over_L": frac, "n_points": len(prof), "u_peak_m_s": pk,
            "abs_error_pct": sum(errs_abs) / len(errs_abs),
            "shape_error_pct": sum(errs_shape) / len(errs_shape),
        })
        shape_errs = errs_shape

    if not report:
        print("  no station captured any cells -- check Cx range")
        return 1

    # take u_max from MID-CHANNEL: x/L=0.9 sits 10 cells from the outlet and
    # showed a 1.6% outlet artefact. Fully developed flow lives at 0.5.
    mid = next((r for r in report if r["x_over_L"] == 0.5), report[-1])
    umax_num = mid["u_peak_m_s"]

    # --- D4: flow rate ------------------------------------------------------
    prof = station_profile(cells, 0.5 * LENGTH)   # same station as u_max
    q_num = sum(u for _, u in prof) * dy * DEPTH
    q_exact = (2.0 / 3.0) * umax_imposed * (2 * H_HALF) * DEPTH
    Re = (q_num / (2 * H_HALF * DEPTH)) * D_HYD / NU

    err_imposed = abs(umax_num - umax_imposed) / umax_imposed * 100
    err_meas = (abs(umax_num - umax_meas_grad) / umax_meas_grad * 100
                if umax_meas_grad else float("nan"))

    out = {
        "tool": "cfd_poiseuille_channel",
        "solver": "foamRun -solver incompressibleFluid",
        "n_cells": ncell, "converged": converged,
        "worst_continuity_error": worst_cont,
        "D0_mesh_half_height_m": h_implied,
        "D0_mesh_length_m": L_implied,
        "D0_geometry_ok": geom_ok,
        "nu_used_m2_s": NU,
        "D1_dpdx_imposed": dpdx_imposed,
        "D1_dpdx_measured": dpdx_measured,
        "D1_gradient_ratio": grad_ratio,
        "u_max_numerical_m_s": umax_num,
        "u_max_vs_imposed_grad_m_s": umax_imposed,
        "u_max_vs_measured_grad_m_s": umax_meas_grad,
        "error_vs_imposed_pct": err_imposed,
        "error_vs_measured_grad_pct": err_meas,
        "D3_shape_error_pct": mid["shape_error_pct"],
        "D4_flowrate_num": q_num, "D4_flowrate_exact": q_exact,
        "reynolds_number": Re, "stations": report,
        "D5_settle_pct": settled_pct,
        "D5_trajectory": [{"iter": t, "u_max_m_s": u} for t, u in traj],
    }
    print(json.dumps(out, indent=2))

    import math as _m
    h_needed = _m.sqrt(2 * NU * umax_num / (dpdx_measured)) if dpdx_measured else 0.0

    print("\n  --- DIAGNOSIS " + "-" * 52)
    print(f"  D0 MESH (from solver): {len(ys)} y-cells spanning "
          f"{ys[0]*1000:+.4f} to {ys[-1]*1000:+.4f} mm, dy={dy_mesh*1000:.4f} mm")
    print(f"       implied half-height {h_implied*1000:.4f} mm "
          f"(specified {H_HALF*1000:.4f})   "
          + ("OK" if abs(h_implied-H_HALF)/H_HALF < 0.01 else "MISMATCH"))
    print(f"       implied length      {L_implied*1000:.4f} mm "
          f"(specified {LENGTH*1000:.4f})   "
          + ("OK" if abs(L_implied-LENGTH)/LENGTH < 0.01 else "MISMATCH"))
    print(f"       half-height that WOULD explain u_max: {h_needed*1000:.4f} mm")
    if geom_ok:
        print("       -> geometry is correct. nu, gradient, convergence and")
        print("          mesh are ALL verified, yet u_max is low. The fault")
        print("          is in the case dictionaries, not in any input.")
        print("          Next: diff this case against $FOAM_TUTORIALS.")
    print(f"  D1 pressure gradient : imposed {dpdx_imposed:.6g}, "
          f"measured {dpdx_measured:.6g}   ratio {grad_ratio:.4f}")
    print("  D2 development       : " + ",  ".join(
        f"x/L={r['x_over_L']:.2f} peak={r['u_peak_m_s']*1000:.4f}mm/s"
        for r in report))
    print(f"  D3 SHAPE error       : {mid['shape_error_pct']:.3f}%")
    print("       -> if D3 is tiny the parabola is CORRECT and only the")
    print("          magnitude is wrong: the fault is the driving gradient,")
    print("          not the mesh, the scheme, or the sampling.")
    print(f"  D4 flow rate         : {q_num:.6g} vs exact {q_exact:.6g}")
    print("  D5 SETTLING          : " + " -> ".join(
        f"{u*1000:.4f}" for _, u in traj) + " mm/s")
    print(f"       last step change {settled_pct:.4f}%  "
          f"(must be < {SETTLE_PCT}%)")
    print(f"\n  u_max numerical        : {umax_num*1000:.4f} mm/s")
    print(f"  u_max (imposed grad)   : {umax_imposed*1000:.4f} mm/s"
          f"   -> {err_imposed:.2f}% error")
    print(f"  u_max (MEASURED grad)  : {umax_meas_grad*1000:.4f} mm/s"
          f"   -> {err_meas:.2f}% error")
    print(f"  Reynolds number        : {Re:.2f}")

    (WORK / "verification.json").write_text(json.dumps(out, indent=2))

    settled = settled_pct == settled_pct and settled_pct < SETTLE_PCT
    if err_imposed < TOL_PCT and settled:
        print("\n  CFD DOMAIN VERIFIED.")
        return 0
    if not settled:
        print(f"\n  CFD NOT VERIFIED -- the answer is STILL MOVING "
              f"({settled_pct:.3f}% per {WRITE_INTERVAL} iters).")
        print("  Raise END_ITER and re-run; the trajectory above shows "
              "which way it is heading.")
    else:
        print(f"\n  CFD NOT VERIFIED (tol {TOL_PCT}%) but SETTLED. "
              "A settled wrong answer is a physics/setup fault, "
              "not a convergence one.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
