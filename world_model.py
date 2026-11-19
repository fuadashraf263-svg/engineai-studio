#!/usr/bin/env python3
"""
world_model.py -- the domain world model, separate from the language policy.

From the AGI build plan: the brain (language policy) should not be the only
thing that "knows" the physics. This surrogate LEARNS the design space from
solved history -- power-law fits, because sizing physics IS power laws
(stress ~ 1/d^3, mass ~ d^2, deflection ~ 1/d^4) -- and PREDICTS each
proposed solve before it runs.

Then the executive compares prediction vs FEA. That running error IS the
calibration classifier: a continuously measured answer to "how well does
this system actually understand its domain?" It appears on the AGI
scorecard as parameter C.

    y  ~=  exp(a) * prod_i  x_i ^ b_i        (fit in log space, lstsq)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

SCENE = Path.home() / "engineai" / "scene"
PRED_LOG = SCENE / "predictions.jsonl"

DEFAULT_OUTPUTS = ("max_von_mises_MPa", "mass_g", "tip_deflection_mm")


class PowerLawSurrogate:
    def __init__(self, outputs=DEFAULT_OUTPUTS):
        self.outputs = outputs
        self.param_names: list[str] = []
        self.invariant_params: list[str] = []
        self.models: dict = {}
        self.n_points = 0
        self.dof = None
        self.underdetermined = None

    # ------------------------------------------------------------------ fit
    def fit(self, history) -> bool:
        pts = [(h["parameters"], h["result"]) for h in history
               if isinstance(h.get("result"), dict)
               and h["result"].get("converged")]
        if len(pts) < 3:
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
        self.n_points = len(pts)
        X = np.array([[np.log(max(float(p.get(n, 1e-9)), 1e-9))
                       for n in self.param_names] for p, _ in pts])
        A = np.hstack([np.ones((len(X), 1)), X])
        self.models = {}
        self.dropped_rows = {}
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
                                "n_used": int(n_ok)}
        return bool(self.models)

    # -------------------------------------------------------------- predict
    def predict(self, params: dict) -> dict:
        """{key: (mean, one_sigma_band)} -- empty until fitted."""
        if not self.models:
            return {}
        try:
            x = np.array([1.0] + [np.log(max(float(params[n]), 1e-9))
                                  for n in self.param_names])
        except KeyError:
            return {}
        out = {}
        for k, m in self.models.items():
            mu = float(np.exp(x @ np.array(m["coef"])))
            out[k] = (mu, mu * (np.exp(m["std"]) - 1.0))
        return out

    # --------------------------------------------------------------- record
    @staticmethod
    def record(iteration: int, params: dict, predicted: dict,
               actual: dict) -> float | None:
        """Log prediction vs FEA; return mean abs % error this round."""
        errs = {}
        for k, (mu, _s) in predicted.items():
            a = actual.get(k)
            if isinstance(a, (int, float)) and a > 0 and mu > 0:
                errs[k] = abs(mu - a) / a * 100.0
        SCENE.mkdir(parents=True, exist_ok=True)
        with PRED_LOG.open("a") as fh:
            fh.write(json.dumps({"t": time.time(), "iter": iteration,
                                 "params": params,
                                 "pred": {k: v[0] for k, v in
                                          predicted.items()},
                                 "err_pct": errs}) + "\n")
        return float(np.mean(list(errs.values()))) if errs else None

    # ---------------------------------------------------------- calibration
    @staticmethod
    def calibration(last_n: int = 25):
        """Mean abs prediction error over recent records -- parameter C."""
        if not PRED_LOG.exists():
            return None
        errs = []
        for ln in PRED_LOG.read_text().splitlines()[-last_n:]:
            try:
                e = json.loads(ln).get("err_pct", {})
                errs.extend(e.values())
            except Exception:
                continue
        return float(np.mean(errs)) if errs else None

    def summary(self) -> dict:
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
            return (base + "\n      WARNING: dof<1 -- the fit passes through"
                    " the data exactly.\n      Predictions may still be"
                    " usable; per-parameter exponents are NOT.\n      Add"
                    " solves that vary each parameter independently.")
        return base
