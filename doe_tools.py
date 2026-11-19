#!/usr/bin/env python3
"""
doe_tools.py -- `agent.py sweep` and `agent.py repeat`

WHY SWEEP EXISTS
----------------
The world model fits  y ~ prod_i x_i^b_i  and each b_i is a causal
elasticity. Recovering b_i requires x_i to VARY. The DryRunPlanner pins
secondary parameters at their minimum, so every dry-run mission added
another row at wall=0.6. Four solves, one distinct wall value: the system
is rank-deficient in wall no matter how many rows you add.

`sweep` runs a deliberate one-factor-at-a-time design: each search
parameter is varied across its bounds while the others sit at a fixed
baseline. Geometric spacing, because a power law is linear in log space
and geometric points are evenly spaced there -- the best conditioning per
solve, which matters when every solve costs ten seconds of CalculiX.

It uses ZERO API calls. The brain is not involved; this is the executive
gathering the data the world model needs.

WHY REPEAT EXISTS
-----------------
"Same problem twice, same answer?" is the first thing a serious evaluator
checks, and the system has never measured it. For a deterministic FEA
chain the spread should be exactly zero -- and proving zero is worth more
than assuming it. If it is NOT zero, something (mesh seeding, threading,
file ordering) is non-deterministic and every other number is softer than
it looks.

Both commands are free. Neither touches the brain.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

SCENE = Path.home() / "engineai" / "scene"


def _next_iteration(default: int = 8000) -> int:
    n = default
    for p in SCENE.glob("iter_*.json"):
        try:
            n = max(n, int(p.stem.split("_")[1]) + 1)
        except Exception:
            continue
    return n


def _geom(lo: float, hi: float, n: int) -> list[float]:
    """Geometric spacing: evenly spaced in log space, where the power law
    is linear. Falls back to linear if the range crosses or touches zero."""
    if lo <= 0 or hi <= 0 or n < 2:
        return [lo + (hi - lo) * i / max(n - 1, 1) for i in range(n)]
    r = (hi / lo) ** (1.0 / (n - 1))
    return [lo * r ** i for i in range(n)]


def cmd_sweep(cfg: dict, mission_name: str | None = None,
              points: int = 4, dry: bool = False) -> int:
    import engine_tools as et
    from core import audit

    name = mission_name or cfg["default_mission"]
    mission = cfg["missions"][name]
    tool = et.TOOLS[mission["tool"]]
    search = mission["search_parameters"]
    fixed = dict(mission["fixed_parameters"])

    # baseline: geometric midpoint of every search parameter
    base = {k: math.sqrt(v["min"] * v["max"]) if v["min"] > 0 and v["max"] > 0
            else 0.5 * (v["min"] + v["max"]) for k, v in search.items()}

    plan: list[dict] = []
    for pname, bounds in search.items():
        for val in _geom(bounds["min"], bounds["max"], points):
            row = dict(base)
            row[pname] = round(float(val), 4)
            if row not in plan:
                plan.append(row)

    print(f"== DOE SWEEP: {name} " + "=" * 34)
    print(f"  tool            : {mission['tool']}")
    print(f"  varying         : {', '.join(search)}")
    print(f"  baseline        : { {k: round(v,3) for k,v in base.items()} }")
    print(f"  design          : one-factor-at-a-time, {points} geometric "
          f"points per parameter")
    print(f"  solves planned  : {len(plan)}   (0 API calls, $0.00)")
    for k in search:
        vals = sorted({r[k] for r in plan})
        print(f"      {k:18s} -> {len(vals)} distinct values {vals}")

    if dry:
        print("\n  --plan-only: nothing executed.")
        return 0

    it = _next_iteration()
    ok_n = fail_n = 0
    t0 = time.time()
    print()
    for i, row in enumerate(plan, 1):
        try:
            r = tool(iteration=it, mission=f"sweep:{name}", **fixed, **row)
            conv = bool(r.get("converged"))
            keys = [k for k in ("max_von_mises_MPa", "mass_g", "f1_Hz")
                    if k in r]
            summary = "  ".join(f"{k}={r[k]:.3f}" for k in keys)
            print(f"  [{i:2d}/{len(plan)}] {row}  ->  {summary}"
                  + ("" if conv else "   NOT CONVERGED"))
            ok_n += conv
            fail_n += (not conv)
        except Exception as exc:
            print(f"  [{i:2d}/{len(plan)}] {row}  ->  FAILED: "
                  f"{type(exc).__name__}: {str(exc)[:120]}")
            fail_n += 1
        it += 1

    print(f"\n  {ok_n} converged, {fail_n} failed, "
          f"{time.time()-t0:.1f}s total")
    audit({"event": "sweep", "mission": name, "planned": len(plan),
           "converged": ok_n, "failed": fail_n})

    # ---- did it actually fix the rank deficiency? ------------------------
    try:
        from world_model import PowerLawSurrogate
        from cf_command import _history
        hist = _history(mission["tool"])
        wm = PowerLawSurrogate()
        if wm.fit(hist):
            print("\n  world model after sweep:")
            print("      " + wm.determinacy_note().replace("\n", "\n      "))
        else:
            print("\n  world model still will not fit")
    except Exception as exc:
        print(f"\n  (could not re-fit surrogate: {exc})")

    print("\n  next:  python agent.py counterfactual   "
          "# elasticities should now be physics")
    return 0 if ok_n else 1


def cmd_repeat(cfg: dict, mission_name: str | None = None,
               times: int = 3) -> int:
    """Same inputs, N times. For a deterministic chain the spread is zero."""
    import engine_tools as et
    from core import audit

    name = mission_name or cfg["default_mission"]
    mission = cfg["missions"][name]
    tool = et.TOOLS[mission["tool"]]
    fixed = dict(mission["fixed_parameters"])
    params = {k: round(math.sqrt(v["min"] * v["max"]), 3)
              if v["min"] > 0 and v["max"] > 0
              else 0.5 * (v["min"] + v["max"])
              for k, v in mission["search_parameters"].items()}

    print(f"== REPEATABILITY: {name} " + "=" * 30)
    print(f"  tool       : {mission['tool']}")
    print(f"  parameters : {params}   (identical every run)")
    print(f"  runs       : {times}\n")

    it = _next_iteration()
    runs: list[dict] = []
    for i in range(times):
        try:
            r = tool(iteration=it + i, mission=f"repeat:{name}",
                     **fixed, **params)
            runs.append(r)
            keys = [k for k in ("max_von_mises_MPa", "mass_g", "f1_Hz")
                    if k in r]
            print(f"  run {i+1}: " + "  ".join(f"{k}={r[k]:.10g}"
                                               for k in keys))
        except Exception as exc:
            print(f"  run {i+1}: FAILED {type(exc).__name__}: {exc}")
            return 1

    numeric = [k for k in runs[0]
               if isinstance(runs[0][k], (int, float))
               and not isinstance(runs[0][k], bool)
               and k != "wall_time_s"]
    print("\n  key                        spread        relative")
    print("  " + "-" * 54)
    worst_rel = 0.0
    for k in sorted(numeric):
        vals = [float(r[k]) for r in runs if k in r]
        if len(vals) < 2:
            continue
        spread = max(vals) - min(vals)
        mean = sum(vals) / len(vals)
        rel = abs(spread / mean) * 100 if mean else 0.0
        worst_rel = max(worst_rel, rel)
        print(f"  {k:24s} {spread:12.3e}  {rel:9.6f}%")

    print()
    if worst_rel == 0.0:
        print("  BIT-IDENTICAL across all runs. The chain is deterministic;")
        print("  every other number in this system is reproducible.")
    elif worst_rel < 1e-6:
        print(f"  Reproducible to {worst_rel:.2e}% -- floating-point noise only.")
    else:
        print(f"  NON-DETERMINISTIC: {worst_rel:.4f}% spread on identical")
        print("  inputs. Find the source (mesh seeding, thread count, file")
        print("  ordering) before trusting any single-run result.")

    audit({"event": "repeatability", "mission": name, "runs": times,
           "worst_relative_spread_pct": worst_rel})
    (SCENE / "repeatability.json").write_text(json.dumps(
        {"mission": name, "params": params, "runs": times,
         "worst_relative_spread_pct": worst_rel}, indent=2))
    return 0
