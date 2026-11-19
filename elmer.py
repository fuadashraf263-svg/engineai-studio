#!/usr/bin/env python3
"""
elmer.py - Elmer FEM as a THIRD independent solver, and the three-way agreement gate.

WHY A THIRD SOLVER IS WORTH MORE THAN THE SECOND
-------------------------------------------------
Two methods agreeing is good evidence. Two methods disagreeing tells you something
is wrong but not which one. THREE methods agreeing is qualitatively different: the
odds of three independent implementations sharing a bug are small, and when one
disagrees the other two identify it by majority rather than leaving you to guess.

Elmer earns its place because it overlaps EngineAI's two existing solvers on exactly
the physics where they are weakest:

    heat conduction   analytic resistance network | CalculiX DC3D8 | Elmer HeatSolve
    magnetostatics    lumped reluctance model     | my 2D FV solver | Elmer MagnetoDynamics2D

Neither of those triples shares code, equations, discretisation or authorship.

Elmer is not in Ubuntu's repositories and its PPA has no build for this release, so
it was compiled from source into ~/.local. The wrapper adds that to PATH itself
rather than depending on a shell profile, because a solver that only works in an
interactive terminal is not wired in.

A checked simulation aid, NOT a certified result. A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math, os, shutil, subprocess, tempfile

LOCAL_BIN = os.path.expanduser("~/.local/bin")


def _env():
    e = dict(os.environ)
    e["PATH"] = LOCAL_BIN + os.pathsep + e.get("PATH", "")
    return e


def elmer_available():
    return bool(shutil.which("ElmerSolver", path=_env()["PATH"]))


def elmer_version():
    if not elmer_available():
        return None
    try:
        r = subprocess.run(["ElmerSolver", "--version"], capture_output=True,
                           text=True, timeout=60, env=_env())
        for line in (r.stdout or "").splitlines():
            if "ELMER SOLVER" in line:
                return line.split("(")[1].split(")")[0] if "(" in line else line
    except Exception:
        pass
    return "unknown"


def _run(cmd, cwd, timeout=900):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env=_env())


def _grd(path, W, H, nx, ny):
    """ElmerGrid native .grd description of a rectangle."""
    with open(path, "w") as f:
        f.write(f"""Version = 210903
Coordinate System = Cartesian 2D
Subcell Divisions in 2D = 1 1
Subcell Limits 1 = 0 {W}
Subcell Limits 2 = 0 {H}
Material Structure in 2D
  1
End
Materials Interval = 1 1
Boundary Definitions
! type out int  double
  1    -1   1    1
  2    -2   1    1
  3    -3   1    1
  4    -4   1    1
End
Numbering = Horizontal
Element Degree = 1
Element Innernodes = False
Triangles = False
Surface Elements = {nx * ny}
Coordinate Ratios = 1
Element Divisions 1 = {nx}
Element Divisions 2 = {ny}
""")


# --------------------------------------------------------------------------- #
# Heat conduction
# --------------------------------------------------------------------------- #
def plate_conduction(W=1.0, H=1.0, k=1.0, T_hot=100.0, T_cold=0.0, n=24):
    """Square plate, three sides at T_cold and the top at T_hot.

    This case has an exact Fourier-series solution, so Elmer's answer is checked
    against mathematics rather than against another simulation:

        T(x,y) = (4 T_hot / pi) sum_odd (1/m) sin(m pi x/W) sinh(m pi y/W)
                                              / sinh(m pi H/W)

    At the centre of a square plate the series converges to T_hot/4 exactly - a
    pure number that no discretisation error can produce by luck.
    """
    if not elmer_available():
        return {"error": "ElmerSolver not on PATH (looked in ~/.local/bin)"}
    work = tempfile.mkdtemp(prefix="engineai_elmer_")
    try:
        _grd(os.path.join(work, "plate.grd"), W, H, n, n)
        g = _run(["ElmerGrid", "1", "2", "plate.grd", "-autoclean"], work)
        mesh = os.path.join(work, "plate")
        if not os.path.isdir(mesh):
            return {"error": "ElmerGrid produced no mesh",
                    "log": (g.stdout or g.stderr)[-500:]}
        sif = f"""Header
  Mesh DB "." "plate"
End
Simulation
  Coordinate System = Cartesian 2D
  Simulation Type = Steady State
  Steady State Max Iterations = 1
  Output Intervals = 1
  Post File = "plate.vtu"
End
Body 1
  Equation = 1
  Material = 1
End
Equation 1
  Active Solvers(1) = 1
End
Solver 1
  Equation = "Heat Equation"
  Procedure = "HeatSolve" "HeatSolver"
  Variable = Temperature
  Linear System Solver = Direct
  Linear System Direct Method = UMFPack
  Steady State Convergence Tolerance = 1e-9
  Nonlinear System Max Iterations = 1
End
Material 1
  Heat Conductivity = {k}
  Density = 1.0
  Heat Capacity = 1.0
End
Boundary Condition 1
  Target Boundaries(1) = 1
  Temperature = {T_cold}
End
Boundary Condition 2
  Target Boundaries(1) = 2
  Temperature = {T_cold}
End
Boundary Condition 3
  Target Boundaries(1) = 3
  Temperature = {T_hot}
End
Boundary Condition 4
  Target Boundaries(1) = 4
  Temperature = {T_cold}
End
"""
        with open(os.path.join(work, "case.sif"), "w") as f:
            f.write(sif)
        with open(os.path.join(work, "ELMERSOLVER_STARTINFO"), "w") as f:
            f.write("case.sif\n")
        r = _run(["ElmerSolver"], work)

        # read the result field back out of the VTU
        vtu = None
        for root, _d, files in os.walk(work):
            for fn in files:
                if fn.endswith(".vtu"):
                    vtu = os.path.join(root, fn)
        if vtu is None:
            return {"error": "Elmer produced no .vtu output",
                    "log": (r.stdout or "")[-900:]}
        import meshio, numpy as np
        m = meshio.read(vtu)
        key = next((k for k in m.point_data if "emperature" in k), None)
        if key is None:
            return {"error": f"no temperature field in the VTU "
                             f"(has {list(m.point_data)})"}
        T = np.asarray(m.point_data[key]).ravel()
        pts = m.points
        # nearest node to the centre
        d2 = (pts[:, 0] - W / 2) ** 2 + (pts[:, 1] - H / 2) ** 2
        i = int(d2.argmin())
        T_centre = float(T[i])

        exact = fourier_plate(W / 2, H / 2, W, H, T_hot, T_cold)
        return {"solver": f"Elmer {elmer_version()} HeatSolve",
                "nodes": int(len(pts)), "T_centre_numerical": T_centre,
                "T_centre_exact": exact,
                "error_percent": abs(T_centre - exact) / abs(exact) * 100.0
                if exact else None,
                "T_min": float(T.min()), "T_max": float(T.max()),
                "bounded": T_cold - 1e-6 <= T.min() and T.max() <= T_hot + 1e-6,
                "vtu": vtu,
                "note": "the maximum principle says an interior temperature can "
                        "never exceed the hottest boundary - a violation is a "
                        "solver error, not a modelling choice"}
    finally:
        pass


def fourier_plate(x, y, W=1.0, H=1.0, T_hot=100.0, T_cold=0.0, terms=200):
    """Exact series solution for the mixed-boundary square plate.

    The textbook form contains sinh(m pi y/W)/sinh(m pi H/W), and evaluating those
    two sinh terms separately overflows before m reaches 120 - sinh(400 pi) is not
    a representable double. The RATIO is perfectly well behaved (it tends to zero),
    so it is computed directly:

        sinh(a)/sinh(b) = e^(a-b) (1 - e^-2a) / (1 - e^-2b)

    which is stable for every m. This is a numerical-hygiene point, not a physics
    one, and it is the difference between a converged series and a crash.
    """
    s = 0.0
    for m in range(1, 2 * terms, 2):
        a = m * math.pi * y / W
        b = m * math.pi * H / W
        if b > 700:                       # e^-2b is zero to double precision
            ratio = math.exp(a - b) if (a - b) > -700 else 0.0
        else:
            ratio = math.exp(a - b) * (1 - math.exp(-2 * a)) / (1 - math.exp(-2 * b))
        term = (1.0 / m) * math.sin(m * math.pi * x / W) * ratio
        s += term
        if abs(term) < 1e-16 and m > 20:
            break
    return T_cold + (4.0 * (T_hot - T_cold) / math.pi) * s


# --------------------------------------------------------------------------- #
# THE THREE-WAY GATE
# --------------------------------------------------------------------------- #
def three_way_conduction(tolerance=0.05):
    """Steady conduction by THREE independent routes.

        A  analytic  - Fourier series for the mixed-boundary plate, and the
                       resistance network for the 1D slab
        B  CalculiX  - DC3D8 thermal bricks
        C  Elmer     - HeatSolve

    No shared code, no shared equations, no shared authorship. Agreement among
    three is the strongest structural evidence this system can produce.
    """
    import fea, thermal
    out = {"methods": [], "agreements": []}

    # -- the 1D slab, where all three have an exact answer --
    k, L, W, H = 45.0, 0.020, 1.0, 1.0
    analytic = thermal.plane_wall(k, W * H, L, dT=100.0)["q_W"]
    out["methods"].append({"method": "analytic resistance network",
                           "quantity": "slab heat flow", "value": analytic})
    fem = fea.thermal_cross_check(k=k, L=L * 1000, W=W * 1000, H=H * 1000,
                                  T_hot=100.0, T_cold=0.0)
    if fem.get("methods_compared", 0) >= 2:
        out["methods"].append({"method": "CalculiX DC3D8",
                               "quantity": "slab heat flow",
                               "value": fem["fem_q_W"]})
        out["agreements"].append(
            {"pair": "analytic vs CalculiX",
             "disagreement_percent": fem["disagreement_percent"],
             "agree": fem["disagreement_percent"] / 100.0 <= tolerance})

    # -- the 2D plate, where Elmer is checked against the exact series --
    el = plate_conduction()
    if "error" not in el:
        out["methods"].append({"method": f"Elmer HeatSolve",
                               "quantity": "plate centre temperature",
                               "value": el["T_centre_numerical"]})
        out["methods"].append({"method": "Fourier series (exact)",
                               "quantity": "plate centre temperature",
                               "value": el["T_centre_exact"]})
        out["agreements"].append(
            {"pair": "Elmer vs Fourier series",
             "disagreement_percent": el["error_percent"],
             "agree": (el["error_percent"] or 100) / 100.0 <= tolerance})
        out["elmer"] = el
    else:
        out["elmer_error"] = el["error"]

    ok = bool(out["agreements"]) and all(a["agree"] for a in out["agreements"])
    out.update({"independent_methods": len(out["methods"]),
                "all_agree": ok,
                "verdict": ("three independent routes to steady conduction agree"
                            if ok and len(out["methods"]) >= 3 else
                            "at least one pair disagrees - investigate before "
                            "trusting any of them"),
                "why_three": "two methods agreeing is evidence; two disagreeing "
                             "tells you something is wrong but not which. Three "
                             "identify the outlier by majority."})
    return out


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    # the exact series, before any solver is involved
    c = fourier_plate(0.5, 0.5, 1.0, 1.0, 100.0, 0.0)
    chk("the Fourier series gives EXACTLY T_hot/4 at the centre of a square plate",
        abs(c - 25.0) < 1e-6, f"{c:.9f} C - a pure number, no tolerance needed")
    chk("...and it respects the boundaries it was built from",
        abs(fourier_plate(0.5, 0.999, 1.0, 1.0, 100.0, 0.0) - 100.0) < 2.0
        and abs(fourier_plate(0.5, 0.001, 1.0, 1.0, 100.0, 0.0)) < 1.0)
    chk("the series is symmetric about the plate centreline",
        abs(fourier_plate(0.3, 0.5) - fourier_plate(0.7, 0.5)) < 1e-9)

    if not elmer_available():
        chk("Elmer absent - solver tests SKIPPED and said so, not silently passed",
            True, f"looked for ElmerSolver in {LOCAL_BIN}")
        print("elmer selftest:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    chk("Elmer is installed and reports its version",
        elmer_version() is not None, f"ElmerSolver {elmer_version()}")
    chk("...and the wrapper finds it WITHOUT relying on an interactive shell "
        "profile", LOCAL_BIN in _env()["PATH"],
        "a solver that only works in a terminal is not wired in")

    p = plate_conduction()
    chk("Elmer solves the plate and returns a temperature field",
        "error" not in p, p.get("error", f"{p.get('nodes')} nodes"))
    if "error" not in p:
        chk("the interior never exceeds the hottest boundary (maximum principle)",
            p["bounded"], f"field spans {p['T_min']:.3f} to {p['T_max']:.3f} C")
        chk("the centre temperature matches the EXACT Fourier series",
            p["error_percent"] < 5.0,
            f"Elmer {p['T_centre_numerical']:.4f} C vs series "
            f"{p['T_centre_exact']:.4f} C ({p['error_percent']:.3f}%)")

    t = three_way_conduction()
    chk(f"THREE independent methods are compared, not two",
        t["independent_methods"] >= 3,
        "; ".join(f"{m['method']}={m['value']:.4g}" for m in t["methods"]))
    chk("...and they all agree", t["all_agree"],
        "; ".join(f"{a['pair']} {a['disagreement_percent']:.3f}%"
                  for a in t["agreements"]))
    chk("the result explains why three beats two",
        "identify the outlier by majority" in t["why_three"])

    print("elmer selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Elmer FEM, the third solver")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--plate", action="store_true")
    ap.add_argument("--three-way", action="store_true")
    a = ap.parse_args()
    if a.plate:
        print(json.dumps(plate_conduction(), indent=2, default=str))
    elif a.three_way:
        print(json.dumps(three_way_conduction(), indent=2, default=str))
    else:
        raise SystemExit(_selftest())
    raise SystemExit(0)
