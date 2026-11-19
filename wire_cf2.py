#!/usr/bin/env python3
"""
wire_cf2.py -- four bugs the DOE sweep exposed. All real, all from data.

BUG 1  ONE BAD ROW KILLED AN ENTIRE OUTPUT.
       Sweep solve 1/8 (D=4.0, wall=1.549) returned max_von_mises_MPa=0.000.
       world_model.fit() does:
           if any(... y <= 0 for y in ys): continue
       -- so a single zero dropped max_von_mises_MPa from the model
       completely. The counterfactual then fell through to mass_g, which is
       why it started reasoning about mass instead of stress.
       Fix: drop the offending ROWS for that output, keep the output.

BUG 2  THE SWEEP GENERATED A DEGENERATE DESIGN.
       D=4.0 with wall=1.549 gives inner diameter 4 - 2(1.549) = 0.90 mm --
       a nearly solid rod. That is what produced the zero.
       Fix: skip designs whose result is non-physical, and report them.

BUG 3  THE CONSTRAINT VALUE WAS APPLIED TO THE WRONG OUTPUT.
       cf_command defaults the hypothetical limit to
       `actual_limit * 0.8`, where actual_limit is the mission's constraint
       (250 MPa). When out_key fell through to mass_g it asked
       "if the limit on mass_g had been 200.0 instead of 3.1" -- 200 grams,
       borrowed from a stress constraint. Nonsense.
       Fix: only use the mission constraint when out_key IS the constrained
       key; otherwise scale from the factual value.

BUG 4  THE ANSWER LEFT THE SEARCH BOUNDS.
       It asked for outer_diameter = 203.797 mm against bounds 4-14 mm,
       then tried to solve it: 484123 nodes, stopped by the RAM guard.
       (The guard worked. That is good design -- but the question should
       never have been asked.)
       Fix: refuse counterfactuals outside the mission's own bounds.

Run:  conda activate eng && cd ~/engineai && python wire_cf2.py
"""

from __future__ import annotations

import ast
import shutil
import sys
import time
from pathlib import Path

HOME = Path.home() / "engineai"
G, R, Y, O = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def ok(m):
    print(f"{G}  PASS{O}  {m}")


def bad(m):
    print(f"{R}  FAIL{O}  {m}")


def note(m):
    print(f"{Y}  ..  {O}  {m}")


def patch(path: Path, old: str, new: str, label: str, marker: str) -> bool:
    if not path.exists():
        bad(f"{path.name} not found")
        return False
    src = path.read_text()
    if marker in src:
        note(f"{label} -- already applied")
        return True
    if old not in src:
        bad(f"{label} -- anchor not found")
        return False
    shutil.copy(path, Path(str(path) + f".bak.{int(time.time())}"))
    path.write_text(src.replace(old, new, 1))
    ok(label)
    return True


# ------------------------------------------------------------------ BUG 1 ---

FIT_OLD = '''        for key in self.outputs:
            ys = [r.get(key) for _, r in pts]
            if any(y is None or not isinstance(y, (int, float)) or y <= 0
                   for y in ys):
                continue
            y = np.log(np.array(ys, float))
            coef, *_ = np.linalg.lstsq(A, y, rcond=None)
            resid = y - A @ coef
            self.models[key] = {"coef": coef.tolist(),
                                "std": float(np.std(resid))}'''

FIT_NEW = '''        self.dropped_rows = {}
        for key in self.outputs:
            # Drop the offending ROWS, not the whole output. One degenerate
            # solve returning 0.0 used to delete an entire physical quantity
            # from the world model, silently redirecting every downstream
            # question to whatever output happened to survive.
            mask = [isinstance(r.get(key), (int, float))
                    and not isinstance(r.get(key), bool)
                    and r.get(key) > 0 for _, r in pts]
            n_ok = sum(mask)
            self.dropped_rows[key] = len(pts) - n_ok
            if n_ok < len(self.param_names) + 2:
                continue
            idx = np.array(mask, dtype=bool)
            Ak = A[idx]
            y = np.log(np.array([r[key] for keep, (_, r) in zip(mask, pts)
                                 if keep], float))
            coef, *_ = np.linalg.lstsq(Ak, y, rcond=None)
            resid = y - Ak @ coef
            self.models[key] = {"coef": coef.tolist(),
                                "std": float(np.std(resid)),
                                "n_used": int(n_ok)}'''

# ------------------------------------------------------------------ BUG 3 ---

KEY_OLD = '''    out_key = list(mission["constraints"])[0]
    if out_key not in wm.models:
        out_key = next(iter(wm.models))'''

KEY_NEW = '''    constrained_key = list(mission["constraints"])[0]
    out_key = constrained_key
    if out_key not in wm.models:
        dropped = getattr(wm, "dropped_rows", {}).get(out_key)
        print(f"\\n  WARNING: '{out_key}' is not in the world model"
              + (f" ({dropped} solve(s) returned a non-positive value)"
                 if dropped else "") + ".")
        if not wm.models:
            print("  no output could be fitted -- run: python agent.py sweep")
            return 1
        out_key = next(iter(wm.models))
        print(f"  falling back to '{out_key}'. The mission constraint does "
              f"NOT apply to it;\\n  the hypothetical limit will be scaled "
              f"from the factual value instead.")'''

LIMIT_OLD = '''    limit = new_limit if new_limit is not None else actual_limit * 0.8'''
LIMIT_NEW = '''    if new_limit is not None:
        limit = new_limit
    elif out_key == constrained_key:
        limit = actual_limit * 0.8
    else:
        # BUG 3: a stress constraint must never become a mass target.
        limit = f_val * 0.8'''

# ------------------------------------------------------------------ BUG 5 ---
# Found while testing the first four: cf_command takes the factual as
# hist[-1] -- the MOST RECENT solve. After a sweep that ends on a degenerate
# design, hist[-1] has out_key = 0.00, and if_constraint_were() divides by it.
# The factual must be the most recent solve with a POSITIVE value.

FACT_OLD = '''    fact = hist[-1]'''
FACT_NEW = '''    usable = [h for h in hist
              if isinstance(h["result"].get(out_key), (int, float))
              and not isinstance(h["result"].get(out_key), bool)
              and h["result"][out_key] > 0]
    if not usable:
        print(f"  no solve in history has a positive {out_key}")
        return 1
    if len(usable) < len(hist):
        print(f"  ({len(hist)-len(usable)} solve(s) with non-positive "
              f"{out_key} excluded from the factual)")
    fact = usable[-1]'''

# ------------------------------------------------------------------ BUG 4 ---

BOUNDS_OLD = '''    print(f"\\n  QUESTION: {q['question']}")'''
BOUNDS_NEW = '''    # BUG 4: refuse to ask a question outside the mission's own bounds.
    bounds = mission["search_parameters"].get(primary, {})
    ans = float(list(q["answer"].values())[0])
    lo, hi = bounds.get("min"), bounds.get("max")
    if lo is not None and hi is not None and not (lo <= ans <= hi):
        print(f"\\n  QUESTION: {q['question']}")
        print(f"  ANSWER  : {primary} = {ans:.3f}")
        print(f"  REFUSED : that is outside the mission bounds "
              f"[{lo}, {hi}].")
        print("  Extrapolating a power law far outside the sampled range is")
        print("  not a counterfactual, it is a guess. Pick a limit inside")
        print("  the design space:  python agent.py counterfactual --limit X")
        return 1

    print(f"\\n  QUESTION: {q['question']}")'''

# ------------------------------------------------------------------ BUG 2 ---

SWEEP_OLD = '''            conv = bool(r.get("converged"))'''
SWEEP_NEW = '''            conv = bool(r.get("converged"))
            # BUG 2: a solve can "converge" and still be nonsense. A hollow
            # strut with wall >= D/2 is a solid rod; the FEA returns 0.0 and
            # one zero used to delete an entire output from the world model.
            nonphys = [k for k in ("max_von_mises_MPa", "mass_g", "f1_Hz")
                       if k in r and (r[k] is None or r[k] <= 0)]
            if nonphys:
                conv = False
                r = dict(r, converged=False)
                print(f"  [{i:2d}/{len(plan)}] {row}  ->  DEGENERATE "
                      f"({', '.join(f'{k}={r[k]}' for k in nonphys)}) "
                      f"-- excluded")
                fail_n += 1
                it += 1
                continue'''


def main() -> int:
    print("\n" + "=" * 66)
    print("  EngineAI: five bugs the sweep exposed")
    print("=" * 66 + "\n")

    wm = HOME / "world_model.py"
    cf = HOME / "cf_command.py"
    doe = HOME / "doe_tools.py"
    good = True

    print("[1] world_model -- one bad row must not delete an output")
    good &= patch(wm, FIT_OLD, FIT_NEW, "per-output row masking",
                  marker="Drop the offending ROWS, not the whole output")

    print("\n[2] cf_command -- constraint must match the output")
    good &= patch(cf, KEY_OLD, KEY_NEW, "output selection made explicit",
                  marker="constrained_key = list(mission")
    good &= patch(cf, LIMIT_OLD, LIMIT_NEW, "limit scaled from the right base",
                  marker="a stress constraint must never become a mass target")

    print("\n[3] cf_command -- factual must have a positive value")
    good &= patch(cf, FACT_OLD, FACT_NEW, "degenerate factual excluded",
                  marker="no solve in history has a positive")

    print("\n[4] cf_command -- refuse answers outside the bounds")
    good &= patch(cf, BOUNDS_OLD, BOUNDS_NEW, "bounds check added",
                  marker="outside the mission bounds")

    print("\n[5] doe_tools -- exclude degenerate designs")
    good &= patch(doe, SWEEP_OLD, SWEEP_NEW, "degenerate designs excluded",
                  marker="a solve can \"converge\" and still be nonsense")

    print("\n[6] syntax check")
    for f in (wm, cf, doe):
        if not f.exists():
            continue
        try:
            ast.parse(f.read_text())
            ok(f"{f.name} parses cleanly")
        except SyntaxError as e:
            bad(f"{f.name}: {e}  -- restore the .bak")
            good = False

    print("\n" + "=" * 66)
    if good:
        print("  Done. Re-run the sequence:")
        print("      python agent.py sweep          # degenerate rows excluded")
        print("      python agent.py counterfactual # back on max_von_mises")
        print("      python agent.py repeat")
        print("      python agent.py agi")
        print()
        print("  Then CFD with the new D0 mesh check:")
        print("      python cfd_tools.py")
    else:
        print("  Some steps failed -- send me the output.")
    print("=" * 66 + "\n")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
