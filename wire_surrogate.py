#!/usr/bin/env python3
"""
wire_surrogate.py -- two fixes exposed by the first real counterfactual run.

BUG 1  world_model.PowerLawSurrogate.fit() takes EVERY parameter, including
       the ones that never change. With 3 solves and 7 parameters the system
       is 3 equations in 8 unknowns; numpy's lstsq returns the minimum-norm
       solution, which smears the fit across all columns -- including five
       constants that are collinear with the intercept.

       The result on your real run:
           d(ln vm)/d(ln E)      = +0.646
           d(ln vm)/d(ln force)  = +0.306
           d(ln vm)/d(ln length) = +0.186

       In linear elasticity max von Mises is INDEPENDENT of E, and EXACTLY
       proportional to force and to length. The fit says Young's modulus
       matters more than wall thickness. It does not. Those numbers are
       artefacts of an underdetermined system, not physics.

       Fix: fit only over parameters that actually VARY, and report the
       fit's determinacy honestly. An underdetermined fit is still usable
       for prediction -- your counterfactual verified to 0.68% -- but the
       per-parameter attribution is not, and the system should say so.

BUG 2  wire_cf.py made its first commit by passing the identity inline with
       `git -c user.email=... -c user.name=...`. That worked for that one
       commit and configured nothing, so YOUR next commit failed with
       "Author identity unknown". My oversight. Fixed by setting the
       identity in the repo itself.

Run:  conda activate eng && cd ~/engineai && python wire_surrogate.py
Backs up world_model.py first. Safe to re-run.
"""

from __future__ import annotations

import shutil
import subprocess
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


FIT_OLD = '''        if len(pts) < 3:
            self.models = {}
            return False
        self.param_names = sorted(pts[0][0])'''

FIT_NEW = '''        if len(pts) < 3:
            self.models = {}
            return False

        # Fit ONLY over parameters that actually vary. A parameter held
        # constant across the whole history is, in log space, a constant
        # column -- perfectly collinear with the intercept. Including it
        # does not add information; it adds an unknown, and lstsq's
        # minimum-norm solution then attributes physics to it that is not
        # there. This is how a fit came back claiming max von Mises depends
        # on Young's modulus.
        all_names = sorted(pts[0][0])
        self.param_names = [
            n for n in all_names
            if len({round(float(p.get(n, 0.0)), 12) for p, _ in pts}) > 1]
        self.invariant_params = [n for n in all_names
                                 if n not in self.param_names]
        if not self.param_names:
            self.models = {}
            return False
        # degrees of freedom = points - (intercept + one exponent each)
        self.dof = len(pts) - (len(self.param_names) + 1)
        self.underdetermined = self.dof < 1
        self.n_points = len(pts)'''

PREDICT_OLD = '''    def summary(self) -> dict:
        return {"fitted": bool(self.models),
                "outputs": list(self.models),
                "mean_abs_prediction_error_pct":
                    PowerLawSurrogate.calibration()}'''

PREDICT_NEW = '''    def summary(self) -> dict:
        return {"fitted": bool(self.models),
                "outputs": list(self.models),
                "fitted_over": list(self.param_names),
                "held_constant": list(getattr(self, "invariant_params", [])),
                "n_points": getattr(self, "n_points", 0),
                "degrees_of_freedom": getattr(self, "dof", None),
                "underdetermined": getattr(self, "underdetermined", None),
                "mean_abs_prediction_error_pct":
                    PowerLawSurrogate.calibration()}

    def determinacy_note(self) -> str:
        """One line the executive can print. Honest about what the fit can
        and cannot support."""
        if not self.models:
            return "surrogate unfitted"
        n = getattr(self, "n_points", 0)
        k = len(self.param_names)
        dof = getattr(self, "dof", None)
        held = getattr(self, "invariant_params", [])
        base = (f"fitted over {k} varying parameter(s) from {n} solves; "
                f"{len(held)} held constant and excluded; dof={dof}")
        if getattr(self, "underdetermined", False):
            return (base + "\\n      WARNING: dof<1 -- the fit passes through"
                    " the data exactly.\\n      Predictions may still be"
                    " usable; per-parameter exponents are NOT.\\n      Add"
                    " solves that vary each parameter independently.")
        return base'''

INIT_OLD = '''        self.param_names: list[str] = []
        self.models: dict = {}'''
INIT_NEW = '''        self.param_names: list[str] = []
        self.invariant_params: list[str] = []
        self.models: dict = {}
        self.n_points = 0
        self.dof = None
        self.underdetermined = None'''

CF_OLD = '''    el = cf.elasticities(out_key)
    print(f"\\n  elasticities for {out_key}:")'''
CF_NEW = '''    print(f"\\n  {wm.determinacy_note()}")
    el = cf.elasticities(out_key)
    print(f"\\n  elasticities for {out_key}:")'''


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


def main() -> int:
    print("\n" + "=" * 64)
    print("  EngineAI: surrogate determinacy + git identity")
    print("=" * 64 + "\n")

    wm = HOME / "world_model.py"
    cf = HOME / "cf_command.py"
    good = True

    print("[1] world_model -- fit only over parameters that VARY")
    good &= patch(wm, INIT_OLD, INIT_NEW, "state fields added",
                  marker="self.invariant_params: list[str] = []")
    good &= patch(wm, FIT_OLD, FIT_NEW, "invariant parameters excluded",
                  marker="Fit ONLY over parameters that actually vary")
    good &= patch(wm, PREDICT_OLD, PREDICT_NEW, "determinacy reporting added",
                  marker="def determinacy_note")

    print("\n[2] cf_command -- print the determinacy warning")
    good &= patch(cf, CF_OLD, CF_NEW, "warning wired into the report",
                  marker="wm.determinacy_note()")

    print("\n[3] syntax check")
    import ast
    for f in (wm, cf):
        if not f.exists():
            continue
        try:
            ast.parse(f.read_text())
            ok(f"{f.name} parses cleanly")
        except SyntaxError as e:
            bad(f"{f.name}: {e}")
            good = False

    print("\n[4] git identity (your commits were failing)")
    if not (HOME / ".git").exists():
        bad("no git repo -- run wire_cf.py first")
        good = False
    else:
        try:
            for k, v in (("user.name", "Fuad Ashraf"),
                         ("user.email", "")):
                subprocess.run(["git", "config", k, v], cwd=HOME, check=True)
            ok("repo identity set -- `git commit` will work now")
            note("change it with: git config user.email ")
        except Exception as e:
            bad(f"git config failed: {e}")
            good = False

    print("\n" + "=" * 64)
    if good:
        print("  Done. Verify:")
        print("      git add -A && git commit -m 'surrogate determinacy fix'")
        print("      python agent.py counterfactual")
        print("          -> now prints which parameters were fitted over")
        print("             and warns that dof<1 with only 3 solves")
        print()
        print("  To make the elasticities TRUE physics, give it more solves:")
        print("      python agent.py mission --dry-run    # free, no API")
        print("      python agent.py counterfactual       # re-fit, re-check")
    else:
        print("  Some steps failed -- send me the output.")
    print("=" * 64 + "\n")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
