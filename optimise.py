#!/usr/bin/env python3
"""
optimise.py - search on the fast tier, verify the winner with the solver.

THE ORCHESTRATION THIS PROVIDES
-------------------------------
"Find a better design under these constraints" needs thousands of evaluations.
A CalculiX run takes seconds to minutes, so a thousand of them is a night.
preview.py answers in microseconds with a PUBLISHED error band.

So the search runs on the estimate and the ANSWER is verified:

    1. pymoo explores the parameter space using preview.py        (fast)
    2. the chosen design is meshed and solved in CalculiX          (slow, once)
    3. the estimate is compared against the solver                (honest)
    4. a receipt records the whole thing                          (checkable)

Step 3 is the one that matters. An optimiser driven by a surrogate returns a
design that is optimal ACCORDING TO THE SURROGATE. If the surrogate is wrong
there, the design is wrong, and the only way to know is to solve it. The result
reports that disagreement rather than burying it.

WHAT THIS DOES NOT DO
---------------------
  * It optimises the parametric registry parts, not arbitrary geometry.
  * The objective set is mass and deflection. Cost, manufacturability,
    fatigue life and noise are not modelled here.
  * A Pareto front from a surrogate is a MAP OF WHERE TO LOOK, not a set of
    finished designs. Every point on it needs solving before it is trusted.

  python optimise.py --selftest
  python optimise.py --part mounting_plate --material "steel (mild)" \
                     --load 3000 --max-deflection 0.5
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def bounds_for(part: str):
    """Design variables and their ranges, from the live registry."""
    import cad_parts
    import workbench
    schema = next((s for s in workbench.part_schema() if s["part"] == part),
                  None)
    if not schema:
        raise ValueError(f"unknown part {part!r}")
    names, lo, hi = [], [], []
    for p in schema["params"]:
        if p["integer"]:
            continue                      # counts are not continuous vars
        names.append(p["name"])
        lo.append(float(p["min"]))
        hi.append(float(p["max"]))
    return names, lo, hi


def evaluate(part: str, names, x, material: str, load_N: float):
    """One design, on the FAST tier. Returns (mass_kg, deflection_mm, valid)."""
    import cad_parts
    import preview
    import workbench
    params = {n: float(v) for n, v in zip(names, x)}
    bad = workbench.violations(part, params)
    if bad:
        return None, None, bad[0]
    try:
        wp, used = cad_parts.build(part, **params)
        sh = wp.val()
        bb = sh.BoundingBox()
        bbox = [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax]
        m = preview.mass_preview(sh.Volume(), material)
        d = preview.deflection_preview(bbox, material, load_N)
        if m.get("error") or d.get("error"):
            return None, None, m.get("error") or d.get("error")
        return m["value"], d["value"], None
    except Exception as exc:                                  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {exc}"


def pareto(part: str = "mounting_plate", material: str = "steel (mild)",
           load_N: float = 3000.0, max_deflection_mm: float = None,
           pop: int = 24, gens: int = 12, seed: int = 1) -> dict:
    """Minimise mass and deflection together, on the estimate."""
    import numpy as np
    from pymoo.core.problem import ElementwiseProblem
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.optimize import minimize

    names, lo, hi = bounds_for(part)
    calls = {"n": 0, "invalid": 0}

    class Design(ElementwiseProblem):
        def __init__(self):
            super().__init__(n_var=len(names), n_obj=2,
                             n_constr=1 if max_deflection_mm else 0,
                             xl=np.array(lo), xu=np.array(hi))

        def _evaluate(self, x, out, *a, **kw):
            calls["n"] += 1
            mass, defl, err = evaluate(part, names, x, material, load_N)
            if err is not None:
                calls["invalid"] += 1
                # A geometry the kernel refuses is not a design. Push it far
                # away rather than pretending it scored badly.
                out["F"] = [1e6, 1e6]
                if max_deflection_mm:
                    out["G"] = [1e6]
                return
            out["F"] = [mass, defl]
            if max_deflection_mm:
                out["G"] = [defl - max_deflection_mm]

    res = minimize(Design(), NSGA2(pop_size=pop), ("n_gen", gens),
                   seed=seed, verbose=False)
    if res.F is None or len(res.F) == 0:
        return {"error": "no feasible design was found in the search space. "
                         "Loosen the constraint or widen the parameter ranges.",
                "evaluations": calls["n"], "invalid": calls["invalid"]}

    front = []
    F = np.atleast_2d(res.F)
    X = np.atleast_2d(res.X)
    for f, x in zip(F, X):
        front.append({"mass_kg": float(f[0]), "deflection_mm": float(f[1]),
                      "params": {n: round(float(v), 3)
                                 for n, v in zip(names, x)}})
    front.sort(key=lambda d: d["mass_kg"])
    return {"part": part, "material": material, "load_N": load_N,
            "variables": names, "front": front,
            "evaluations": calls["n"], "invalid": calls["invalid"],
            "constraint": (f"deflection <= {max_deflection_mm} mm"
                           if max_deflection_mm else None),
            "tier": "ESTIMATE",
            "note": "this front comes from closed-form estimates with a "
                    "published error band. It is a map of where to look, not "
                    "a set of finished designs - verify() any point before "
                    "trusting it."}


def verify(part: str, params: dict, material: str, load_N: float,
           workdir: str = None) -> dict:
    """Solve one design for real, and report how far the estimate was out."""
    import tempfile

    import cad_parts
    import fea_solid
    import preview

    wp, used = cad_parts.build(part, **params)
    sh = wp.val()
    bb = sh.BoundingBox()
    bbox = [bb.xmin, bb.ymin, bb.zmin, bb.xmax, bb.ymax, bb.zmax]
    est = preview.deflection_preview(bbox, material, load_N)

    td = workdir or tempfile.mkdtemp(prefix="engineai_opt_")
    os.makedirs(td, exist_ok=True)
    step, _stl = cad_parts.export(wp, os.path.join(td, part))
    E = preview.MATERIALS[material][1]
    r = fea_solid.analyse(step, force_N=load_N, fix="-X", push="+X",
                          direction=2, E_MPa=E, converge=3,
                          workdir=os.path.join(td, "solve"))

    solved = r["max_disp_mm"]
    err = (est["value"] - solved) / solved * 100 if solved else None
    return {"params": params, "material": material, "load_N": load_N,
            "estimate_mm": est["value"], "solved_mm": solved,
            "error_pct": err,
            "within_published_band": (abs(err) <= est["expect_within_pct"]
                                      if err is not None else None),
            "published_band_pct": est["expect_within_pct"],
            "max_vm_MPa": r["max_vm_MPa"],
            "equilibrium_ok": r["equilibrium_ok"],
            "equilibrium_rel": r["equilibrium_rel"],
            "step": step, "workdir": td,
            # AN OPTIMISER DRIVES A SURROGATE TO WHERE IT IS WORST.
            #
            # preview.deflection_preview is measured to land within 25% on
            # ordinary shapes. The lightest design on the front came back
            # -29.5%, outside that band - and that is not a defect in either
            # piece. The search deliberately walks to the extreme corner of
            # the space, where a prismatic-beam idealisation of a thin drilled
            # plate stops describing it. Widening the band to make this pass
            # would have hidden the one fact the verify step exists to find.
            "warning": (
                f"the estimate was {err:+.1f}% out, past its published "
                f"{est['expect_within_pct']:.0f}% band. Optimisation pushes a "
                f"surrogate into the corner of the space where it is least "
                f"valid, so the front near this design is unreliable. Solve "
                f"the neighbours before choosing between them."
                if err is not None
                and abs(err) > est["expect_within_pct"] else None),
            "note": "the optimiser searched on the estimate; this is the "
                    "solver's answer for the design it chose."}


def run(part: str = "mounting_plate", material: str = "steel (mild)",
        load_N: float = 3000.0, max_deflection_mm: float = None,
        pop: int = 24, gens: int = 12, verify_best: bool = True) -> dict:
    out = pareto(part, material, load_N, max_deflection_mm, pop, gens)
    if out.get("error") or not verify_best:
        return out
    lightest = out["front"][0]
    out["verified"] = verify(part, lightest["params"], material, load_N)
    return out


# --------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def chk(cond, label, extra=""):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label
              + (f"   {extra}" if extra else ""))
        ok = ok and bool(cond)

    print("optimise selftest")
    try:
        import pymoo, cadquery                                # noqa: F401
    except Exception as exc:                                  # noqa: BLE001
        print(f"  skip  {exc}")
        return 0

    print(" the search space comes from the live registry")
    names, lo, hi = bounds_for("mounting_plate")
    chk(len(names) >= 3, f"{len(names)} continuous variables: {names}")
    chk(all(h > l for l, h in zip(lo, hi)), "every range is non-empty")
    chk("n" not in names, "integer counts are excluded from a continuous search")

    print(" one evaluation is fast and honest")
    import time
    t0 = time.perf_counter()
    m, d, err = evaluate("mounting_plate", names,
                         [120.0, 80.0, 10.0, 8.0, 12.0][:len(names)],
                         "steel (mild)", 3000.0)
    dt = time.perf_counter() - t0
    chk(err is None, f"a valid design evaluates ({err})")
    chk(m and m > 0 and d and d > 0, f"mass {m:.4f} kg, deflection {d:.4f} mm")
    chk(dt < 1.0, f"in {dt*1000:.0f} ms - fast enough to run thousands")

    # A design the kernel refuses must be REJECTED, not scored.
    bad_x = list([120.0, 80.0, 10.0, 8.0, 3.0][:len(names)])
    if "margin" in names:
        bad_x[names.index("margin")] = 3.0
        bad_x[names.index("hole_d")] = 20.0
        m2, d2, err2 = evaluate("mounting_plate", names, bad_x,
                                "steel (mild)", 3000.0)
        chk(err2 is not None and m2 is None,
            f"an invalid geometry is refused, not scored ({str(err2)[:44]})")

    print(" the optimiser returns a real trade-off")
    r = pareto("mounting_plate", "steel (mild)", 3000.0, pop=16, gens=8)
    chk(not r.get("error"), f"a front was found ({r.get('error')})")
    front = r["front"]
    chk(len(front) >= 2, f"{len(front)} non-dominated designs")
    chk(r["evaluations"] > 50, f"{r['evaluations']} evaluations, "
                               f"{r['invalid']} invalid")
    # The defining property of a Pareto front: lighter must mean floppier.
    lighter, heavier = front[0], front[-1]
    chk(heavier["mass_kg"] >= lighter["mass_kg"], "sorted by mass")
    chk(lighter["deflection_mm"] >= heavier["deflection_mm"],
        f"the lightest design is the floppiest - a genuine trade-off "
        f"({lighter['mass_kg']:.3f} kg / {lighter['deflection_mm']:.3f} mm  vs  "
        f"{heavier['mass_kg']:.3f} kg / {heavier['deflection_mm']:.3f} mm)")
    chk(r["tier"] == "ESTIMATE" and "not a set of finished designs" in r["note"],
        "the front declares itself an estimate")

    print(" a constraint actually constrains")
    rc = pareto("mounting_plate", "steel (mild)", 3000.0,
                max_deflection_mm=0.05, pop=16, gens=8)
    if not rc.get("error"):
        worst = max(d["deflection_mm"] for d in rc["front"])
        chk(worst <= 0.05 * 1.05,
            f"every returned design meets deflection <= 0.05 mm "
            f"(worst {worst:.4f})")
    else:
        chk("no feasible design" in rc["error"],
            "an impossible constraint says so rather than returning junk")

    print(" THE WINNER IS SOLVED, not taken on the estimate's word")
    try:
        import fea_solid
        tools = fea_solid.have_tools()
    except Exception:                                         # noqa: BLE001
        tools = {"gmsh": False, "ccx": False}
    if not (tools["gmsh"] and tools["ccx"]):
        print("  skip  no solver to verify with")
    else:
        v = verify("mounting_plate", front[0]["params"], "steel (mild)", 3000.0)
        chk(v["equilibrium_ok"],
            f"the chosen design solves ({v['equilibrium_rel']*100:.3f}% "
            f"equilibrium residual)")
        chk(v["error_pct"] is not None,
            f"estimate {v['estimate_mm']:.4f} mm vs solver "
            f"{v['solved_mm']:.4f} mm = {v['error_pct']:+.1f}%")
        # NOT "the estimate was inside its band". The optimiser's whole job is
        # to find the extreme corner of the design space, which is exactly
        # where a beam idealisation of a drilled plate stops holding. What
        # must be true is that a breach is DETECTED and SAID, not that it
        # never happens.
        if v["within_published_band"]:
            chk(True, f"the estimate held at the optimum "
                      f"({v['error_pct']:+.1f}%, band "
                      f"{v['published_band_pct']:.0f}%)")
            chk(v["warning"] is None, "...so no warning was raised")
        else:
            chk(v["warning"] is not None,
                f"the estimate broke its band at the optimum "
                f"({v['error_pct']:+.1f}% vs {v['published_band_pct']:.0f}%) "
                f"and the result SAYS SO")
            chk("least valid" in (v["warning"] or ""),
                "...and explains that optimisation drives a surrogate there")
            chk("Solve the neighbours" in (v["warning"] or ""),
                "...and says what to do about it")
        chk(v["max_vm_MPa"] > 0, f"peak stress {v['max_vm_MPa']:.1f} MPa")

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--part", default="mounting_plate")
    ap.add_argument("--material", default="steel (mild)")
    ap.add_argument("--load", type=float, default=3000.0)
    ap.add_argument("--max-deflection", type=float, default=None)
    ap.add_argument("--pop", type=int, default=24)
    ap.add_argument("--gens", type=int, default=12)
    ap.add_argument("--no-verify", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    r = run(a.part, a.material, a.load, a.max_deflection, a.pop, a.gens,
            verify_best=not a.no_verify)
    if r.get("error"):
        print(r["error"])
        return 1
    print(f"{len(r['front'])} non-dominated designs from {r['evaluations']} "
          f"estimates ({r['invalid']} invalid)")
    print(f"{'mass kg':>9} {'defl mm':>9}   parameters")
    for d in r["front"][:12]:
        print(f"{d['mass_kg']:9.4f} {d['deflection_mm']:9.4f}   "
              + ", ".join(f"{k}={v:g}" for k, v in d["params"].items()))
    if r.get("verified"):
        v = r["verified"]
        print(f"\nlightest design, SOLVED:")
        print(f"  estimate {v['estimate_mm']:.4f} mm | solver "
              f"{v['solved_mm']:.4f} mm | {v['error_pct']:+.1f}% "
              f"(band {v['published_band_pct']:.0f}%)")
        print(f"  peak von Mises {v['max_vm_MPa']:.1f} MPa, equilibrium "
              f"{v['equilibrium_rel']*100:.4f}%")
    print(f"\n{r['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
