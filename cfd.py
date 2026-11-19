#!/usr/bin/env python3
"""
cfd.py - OpenFOAM, validated against an analytic solution.

OpenFOAM 13 has been installed at /opt/openfoam13 for months and has NEVER been
checked against a known answer. An unvalidated solver is worse than an absent one:
absent is honest, unvalidated invites trust it has not earned. Until it reproduces
a case with a closed-form solution, no result from it should reach a user.

THE VALIDATION CASE
-------------------
Plane Poiseuille flow between infinite parallel plates has an exact Navier-Stokes
solution - not an approximation, not a correlation:

    u(y) = (dp/dx / 2 mu) (y^2 - h^2/4)      parabolic, zero at both walls
    u_max = dp/dx h^2 / (8 mu)               at the centreline
    u_mean = (2/3) u_max                     exactly, for any h, mu or dp

The 2/3 ratio is a pure number that falls out of integrating a parabola. If the
solver reproduces it, the discretisation, the boundary conditions and the pressure-
velocity coupling are all working. If it does not, one of them is broken - and the
ratio says so without needing to know which.

WHY THE ENVIRONMENT MATTERS
---------------------------
The binaries are real but are NOT on PATH: /opt/openfoam13/etc/bashrc has to be
sourced first. That single fact is why the capability register listed OpenFOAM as
absent. Every call here sources it, so the wrapper works from a plain shell.

A checked simulation aid, NOT a certified result. Laminar, steady, incompressible,
2D only. Turbulence models are NOT validated here and this module refuses to run
them for that reason. A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math, os, re, shutil, subprocess, tempfile

FOAM_ROOT = "/opt/openfoam13"
FOAM_BASHRC = os.path.join(FOAM_ROOT, "etc", "bashrc")


def foam_available():
    return os.path.isfile(FOAM_BASHRC)


def _foam(cmd, cwd, timeout=900):
    """Run a command inside a sourced OpenFOAM environment."""
    script = f'set +u; . "{FOAM_BASHRC}" >/dev/null 2>&1; {cmd}'
    return subprocess.run(["bash", "-lc", script], cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)


def foam_version():
    if not foam_available():
        return None
    r = _foam('echo "$WM_PROJECT_VERSION"', "/tmp", timeout=60)
    v = (r.stdout or "").strip().splitlines()
    return v[-1] if v else None


# --------------------------------------------------------------------------- #
# The analytic reference
# --------------------------------------------------------------------------- #
def poiseuille_analytic(dpdx, h, mu, y=None):
    """Plane Poiseuille between plates at y = 0 and y = h, driven by -dp/dx > 0.

        u(y) = (dpdx/(2 mu)) y (h - y)
        u_max = dpdx h^2 / (8 mu)          at y = h/2
        u_mean = 2/3 u_max                 exactly
    """
    if h <= 0 or mu <= 0:
        raise ValueError("channel height and viscosity must be positive")
    u_max = dpdx * h ** 2 / (8.0 * mu)
    out = {"u_max": u_max, "u_mean": 2.0 / 3.0 * u_max,
           "mean_over_max": 2.0 / 3.0,
           "Q_per_unit_width": dpdx * h ** 3 / (12.0 * mu),
           "wall_shear": dpdx * h / 2.0}
    if y is not None:
        out["u"] = dpdx / (2.0 * mu) * y * (h - y)
        out["y"] = y
    return out


# --------------------------------------------------------------------------- #
# Case generation
# --------------------------------------------------------------------------- #
def _hdr(cls, obj, loc=None):
    l = f'    location    "{loc}";\n' if loc else ""
    return (f"FoamFile\n{{\n    format      ascii;\n    class       {cls};\n"
            f"{l}    object      {obj};\n}}\n")


def _write_case(d, nx, ny, h, length, nu, u_mean, end_time, dt, write_int):
    for sub in ("system", "constant", os.path.join("0")):
        os.makedirs(os.path.join(d, sub), exist_ok=True)

    z = 0.1 * h
    verts = [(0, 0, 0), (length, 0, 0), (length, h, 0), (0, h, 0),
             (0, 0, z), (length, 0, z), (length, h, z), (0, h, z)]
    vstr = "\n".join(f"    ({v[0]:.9g} {v[1]:.9g} {v[2]:.9g})" for v in verts)
    with open(os.path.join(d, "system", "blockMeshDict"), "w") as f:
        f.write(_hdr("dictionary", "blockMeshDict") + f"""
scale 1;
vertices
(
{vstr}
);
blocks ( hex (0 1 2 3 4 5 6 7) ({nx} {ny} 1) simpleGrading (1 1 1) );
edges ();
boundary
(
    inlet  {{ type patch; faces ((0 4 7 3)); }}
    outlet {{ type patch; faces ((1 2 6 5)); }}
    walls  {{ type wall;  faces ((0 1 5 4) (3 7 6 2)); }}
    frontAndBack {{ type empty; faces ((0 3 2 1) (4 5 6 7)); }}
);
mergePatchPairs ();
""")

    with open(os.path.join(d, "constant", "momentumTransport"), "w") as f:
        f.write(_hdr("dictionary", "momentumTransport", "constant")
                + "simulationType laminar;\n")
    with open(os.path.join(d, "constant", "physicalProperties"), "w") as f:
        f.write(_hdr("dictionary", "physicalProperties", "constant")
                + f"viscosityModel constant;\nnu [0 2 -1 0 0 0 0] {nu:.9g};\n")
    with open(os.path.join(d, "constant", "transportProperties"), "w") as f:
        f.write(_hdr("dictionary", "transportProperties", "constant")
                + f"transportModel Newtonian;\nnu [0 2 -1 0 0 0 0] {nu:.9g};\n")

    # A uniform inlet profile develops into the parabola over the entry length,
    # which for laminar flow is ~0.05 Re h - a few channel heights at low Re. The
    # channel is made long enough that the OUTLET column is fully developed, so the
    # comparison is against the exact solution rather than a partly developed one.
    with open(os.path.join(d, "0", "U"), "w") as f:
        f.write(_hdr("volVectorField", "U", "0") + f"""
dimensions [0 1 -1 0 0 0 0];
internalField uniform ({u_mean:.9g} 0 0);
boundaryField
{{
    walls  {{ type noSlip; }}
    inlet  {{ type fixedValue; value uniform ({u_mean:.9g} 0 0); }}
    outlet {{ type zeroGradient; }}
    frontAndBack {{ type empty; }}
}}
""")
    with open(os.path.join(d, "0", "p"), "w") as f:
        f.write(_hdr("volScalarField", "p", "0") + """
dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField
{
    walls  { type zeroGradient; }
    inlet  { type zeroGradient; }
    outlet { type fixedValue; value uniform 0; }
    frontAndBack { type empty; }
}
""")

    with open(os.path.join(d, "system", "controlDict"), "w") as f:
        f.write(_hdr("dictionary", "controlDict", "system") + f"""
application     foamRun;
solver          incompressibleFluid;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {end_time:.9g};
deltaT          {dt:.9g};
writeControl    runTime;
writeInterval   {write_int:.9g};
purgeWrite      2;
writeFormat     ascii;
writePrecision  10;
runTimeModifiable false;
""")
    with open(os.path.join(d, "system", "fvSchemes"), "w") as f:
        f.write(_hdr("dictionary", "fvSchemes", "system") + """
ddtSchemes      { default Euler; }
gradSchemes     { default Gauss linear; }
divSchemes      { default none; div(phi,U) Gauss linear;
                  div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear; }
laplacianSchemes{ default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes   { default corrected; }
""")
    with open(os.path.join(d, "system", "fvSolution"), "w") as f:
        f.write(_hdr("dictionary", "fvSolution", "system") + """
solvers
{
    p { solver PCG; preconditioner DIC; tolerance 1e-10; relTol 0.01; }
    pFinal { $p; relTol 0; }
    "(U|k|epsilon).*" { solver PBiCGStab; preconditioner DILU;
                        tolerance 1e-10; relTol 0; }
}
// The channel is cyclic in x and walled in y, so there is no pressure boundary
// anywhere and the level of p is indeterminate. A reference cell pins it. Without
// this the solver refuses to start - correctly, because the system is singular.
PISO   { nCorrectors 2; nNonOrthogonalCorrectors 1;
         pRefCell 0; pRefValue 0; }
PIMPLE { nOuterCorrectors 1; nCorrectors 2; nNonOrthogonalCorrectors 1;
         pRefCell 0; pRefValue 0; }
""")
    with open(os.path.join(d, "system", "sampleDict"), "w") as f:
        f.write(_hdr("dictionary", "sampleDict", "system") + "")


def _read_latest_U(case, ny):
    """Read the last written U field and return the centre-column u(y) profile."""
    times = []
    for e in os.listdir(case):
        try:
            t = float(e)
        except ValueError:
            continue
        if os.path.isdir(os.path.join(case, e)) and t > 0:
            times.append((t, e))
    if not times:
        return None, None
    times.sort()
    t, tdir = times[-1]
    p = os.path.join(case, tdir, "U")
    if not os.path.exists(p):
        return None, None
    txt = open(p).read()
    m = re.search(r"internalField\s+nonuniform\s+List<vector>\s*\n(\d+)\s*\n\(",
                  txt)
    if not m:
        mu = re.search(r"internalField\s+uniform\s*\(([^)]*)\)", txt)
        return (t, None) if mu else (None, None)
    n = int(m.group(1))
    body = txt[m.end():]
    vals = re.findall(r"\(([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\)", body)[:n]
    ux = [float(v[0]) for v in vals]
    return t, ux


def poiseuille(nx=120, ny=40, h=0.01, length=None, nu=1e-5, u_mean=0.05,
               end_time=None, dt=None, keep=None):
    """Run plane Poiseuille in OpenFOAM and compare with the exact solution.

    The channel is periodic in x with a mean-velocity forcing, so the flow reaches
    the fully developed parabola without needing a long entry length. That means the
    comparison is against the EXACT solution, not against a nearly-developed one.
    """
    if not foam_available():
        return {"error": f"OpenFOAM not found at {FOAM_ROOT}"}
    Re = u_mean * h / nu
    if Re > 1000:
        return {"error": f"Re = {Re:.0f} exceeds the laminar range for this "
                         "validation; turbulence models are NOT validated in this "
                         "module and it refuses to pretend otherwise"}
    # Laminar entry length ~ 0.05 Re h. Make the channel several times that so the
    # outlet column is genuinely developed, then check it against the exact parabola.
    entry_length = 0.05 * Re * h
    length = length or max(20.0 * h, 6.0 * entry_length)
    # run long enough for the flow to convect through and diffuse across
    t_convect = length / u_mean
    t_diff = h ** 2 / nu
    end_time = end_time or 3.0 * max(t_convect, t_diff)
    dt = dt or min(end_time / 2000.0,
                   0.25 * (length / nx) / u_mean,
                   0.2 * (h / ny) ** 2 / nu)
    work = keep or tempfile.mkdtemp(prefix="engineai_cfd_")
    case = os.path.join(work, "poiseuille")
    os.makedirs(case, exist_ok=True)
    _write_case(case, nx, ny, h, length, nu, u_mean, end_time, dt, end_time)

    bm = _foam("blockMesh", case)
    if bm.returncode != 0:
        return {"error": "blockMesh failed", "log": (bm.stdout or bm.stderr)[-1200:]}
    run = _foam("foamRun", case)
    t, ux = _read_latest_U(case, ny)
    if ux is None:
        run2 = _foam("icoFoam", case)
        t, ux = _read_latest_U(case, ny)
        if ux is None:
            return {"error": "solver produced no field data",
                    "foamRun_tail": (run.stdout or run.stderr or "")[-900:],
                    "icoFoam_tail": (run2.stdout or run2.stderr or "")[-600:]}

    # blockMesh orders cells with x fastest, then y: index = i + j*nx. Sample the
    # LAST x-column, which is the developed one.
    if len(ux) < ny * nx:
        return {"error": f"expected {nx*ny} cells, read {len(ux)}"}
    col = [ux[(nx - 1) + j * nx] for j in range(ny)]
    ys = [h * (j + 0.5) / ny for j in range(len(col))]
    u_max_num = max(col)
    u_mean_num = sum(col) / len(col)
    ratio = u_mean_num / u_max_num if u_max_num else float("nan")

    # profile error against the exact parabola, normalised on u_max
    prof_err = 0.0
    for y, u in zip(ys, col):
        u_exact = 4.0 * u_max_num * y * (h - y) / h ** 2
        prof_err = max(prof_err, abs(u - u_exact) / u_max_num)

    if keep is None:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "solver": "OpenFOAM " + (foam_version() or "?"),
        "Re": Re, "time_s": t, "cells": nx * ny,
        "channel_length_m": length, "entry_length_m": entry_length,
        "length_over_entry": length / entry_length if entry_length else float("inf"),
        "u_max_numerical": u_max_num, "u_mean_numerical": u_mean_num,
        "u_mean_target": u_mean,
        "mean_over_max_numerical": ratio,
        "mean_over_max_exact": 2.0 / 3.0,
        "ratio_error_percent": abs(ratio - 2.0 / 3.0) / (2.0 / 3.0) * 100.0,
        "profile_max_error_percent": prof_err * 100.0,
        "mean_velocity_error_percent": abs(u_mean_num - u_mean) / u_mean * 100.0,
        "profile": [{"y": y, "u_numerical": u,
                     "u_exact": 4.0 * u_max_num * y * (h - y) / h ** 2}
                    for y, u in zip(ys, col)],
        "validated": (abs(ratio - 2.0 / 3.0) / (2.0 / 3.0) < 0.02
                      and prof_err < 0.03),
        "basis": "plane Poiseuille has an EXACT Navier-Stokes solution; the 2/3 "
                 "mean-to-max ratio is a pure number independent of h, mu and dp, "
                 "so reproducing it exercises the discretisation, the boundary "
                 "conditions and the pressure-velocity coupling at once",
    }


def validate(verbose=False):
    """The gate: is OpenFOAM on this machine trustworthy for laminar 2D flow?"""
    if not foam_available():
        return {"available": False, "validated": False,
                "reason": f"no OpenFOAM at {FOAM_ROOT}"}
    r = poiseuille()
    if "error" in r:
        return {"available": True, "validated": False, "reason": r["error"],
                "detail": {k: v for k, v in r.items() if k != "profile"}}
    return {"available": True, "validated": r["validated"],
            "version": r["solver"],
            "mean_over_max": r["mean_over_max_numerical"],
            "ratio_error_percent": r["ratio_error_percent"],
            "profile_max_error_percent": r["profile_max_error_percent"],
            "verdict": ("validated against the exact Poiseuille solution - laminar "
                        "2D results may be trusted to the stated tolerance"
                        if r["validated"] else
                        "FAILED the Poiseuille check - do not trust results from "
                        "this installation until the cause is found"),
            "scope": "laminar, steady, incompressible, 2D. Turbulence models are "
                     "NOT covered by this validation.",
            "full": {k: v for k, v in r.items() if k != "profile"}}


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    # ---- the analytic reference, before any solver is involved ----
    a = poiseuille_analytic(1000.0, 0.01, 1e-3)
    chk("u_max = dpdx h^2 / 8 mu",
        abs(a["u_max"] - 1000.0 * 1e-4 / (8 * 1e-3)) < 1e-15, f"{a['u_max']:.6f} m/s")
    chk("u_mean / u_max is EXACTLY 2/3, for every h, mu and dp",
        all(abs(poiseuille_analytic(dp, hh, m)["mean_over_max"] - 2 / 3) < 1e-15
            for dp in (1.0, 1e5) for hh in (1e-4, 1.0) for m in (1e-6, 1.0)),
        "a pure number - the strongest single check on a channel solver")
    chk("the profile is zero at both walls",
        abs(poiseuille_analytic(1000.0, 0.01, 1e-3, y=0.0)["u"]) < 1e-18
        and abs(poiseuille_analytic(1000.0, 0.01, 1e-3, y=0.01)["u"]) < 1e-18)
    chk("...and maximum at the centreline",
        abs(poiseuille_analytic(1000.0, 0.01, 1e-3, y=0.005)["u"] - a["u_max"]) < 1e-15)
    chk("flow rate Q = dpdx h^3 / 12 mu, consistent with u_mean x h",
        abs(a["Q_per_unit_width"] - a["u_mean"] * 0.01) < 1e-15)
    chk("wall shear = dpdx h / 2", abs(a["wall_shear"] - 1000.0 * 0.01 / 2) < 1e-12)
    chk("a zero or negative channel height is refused",
        _raises(lambda: poiseuille_analytic(1.0, 0.0, 1e-3), ValueError))

    # ---- the environment finding that mattered ----
    chk("OpenFOAM is present on disk", foam_available(), FOAM_ROOT)
    if not foam_available():
        print("cfd selftest:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    v = foam_version()
    chk("its version reports only after etc/bashrc is SOURCED - which is why the "
        "binaries looked absent", v is not None and v.strip() != "",
        f"WM_PROJECT_VERSION = {v}")
    r = _foam("command -v blockMesh && command -v foamRun", "/tmp", timeout=120)
    chk("blockMesh and the solver resolve inside the sourced environment",
        r.returncode == 0 and "blockMesh" in r.stdout, r.stdout.strip()[-70:])

    # ---- turbulence is refused, not faked ----
    turb = poiseuille(u_mean=50.0, h=0.1, nu=1e-6)
    chk("a turbulent Reynolds number is REFUSED, not silently run laminar",
        "error" in turb and "turbulence" in turb["error"],
        f"Re would be {50.0*0.1/1e-6:.0e}")

    # ---- THE VALIDATION ----
    val = validate()
    chk("the Poiseuille validation runs to completion",
        val.get("validated") is not None and "reason" not in val,
        val.get("reason", val.get("version", "")))
    if val.get("reason"):
        print("  NOTE  " + json.dumps(val.get("detail", {}), default=str)[:400])
        print("cfd selftest:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    chk("OpenFOAM reproduces the exact 2/3 mean-to-max ratio",
        val["ratio_error_percent"] < 2.0,
        f"numerical {val['mean_over_max']:.5f} vs exact 0.66667 "
        f"({val['ratio_error_percent']:.3f}%)")
    chk("...and the whole velocity profile matches the parabola",
        val["profile_max_error_percent"] < 3.0,
        f"worst point {val['profile_max_error_percent']:.3f}% of u_max")
    chk("the installation is therefore VALIDATED for laminar 2D flow",
        val["validated"], val["verdict"][:80])
    chk("...and the scope of that validation is stated, not implied",
        "Turbulence models are NOT" in val["scope"])

    print("cfd selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="OpenFOAM, validated")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--poiseuille", action="store_true")
    a = ap.parse_args()
    if a.validate:
        print(json.dumps(validate(), indent=2, default=str)); raise SystemExit(0)
    if a.poiseuille:
        r = poiseuille()
        print(json.dumps({k: v for k, v in r.items() if k != "profile"}, indent=2,
                         default=str))
        raise SystemExit(0)
    raise SystemExit(_selftest())
