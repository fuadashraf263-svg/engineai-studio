#!/usr/bin/env python3
"""
counterfactual.py -- Pearl rung 3, the gap the scorecard reported honestly.

Rung 1 (association): the surrogate fits y ~ f(x) from observation.
Rung 2 (intervention):  the agent SETS x and observes y -- every mission.
Rung 3 (counterfactual): "given what actually happened, what WOULD have
                          happened had x been different?"

Why the power law makes this principled rather than hand-waving: fitting
log y = a + sum_i b_i log x_i means each exponent b_i IS the causal
elasticity d(ln y)/d(ln x_i) under the model. Counterfactual queries are
then closed-form, and -- crucially -- CHEAP: no solver runs.

But cheap answers must still be earned. verify_counterfactual() takes the
model's counterfactual claim and settles it with ONE real solve. A
counterfactual that survives that test is knowledge; one that doesn't is
a measured model error. Both outcomes are logged.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

SCENE = Path.home() / "engineai" / "scene"
CF_LOG = SCENE / "counterfactuals.jsonl"


class CounterfactualEngine:
    def __init__(self, surrogate):
        self.sm = surrogate                      # fitted PowerLawSurrogate

    # ------------------------------------------------------------ elasticity
    def elasticities(self, output_key: str) -> dict:
        """d(ln y)/d(ln x_i) -- the causal exponents, directly readable."""
        m = self.sm.models.get(output_key)
        if not m:
            return {}
        coef = m["coef"][1:]                     # drop intercept
        return {n: round(float(c), 3)
                for n, c in zip(self.sm.param_names, coef)}

    # --------------------------------------------------------- counterfactual
    def what_if(self, factual: dict, change: dict,
                output_key: str) -> dict | None:
        """Factual params + a hypothetical change -> predicted outcome,
        expressed as a RATIO to the factual outcome (ratios are exact
        under a power law even when the intercept is poorly known)."""
        el = self.elasticities(output_key)
        if not el:
            return None
        ratio = 1.0
        for k, new_v in change.items():
            if k not in el or k not in factual:
                continue
            old_v = float(factual[k])
            if old_v <= 0 or float(new_v) <= 0:
                continue
            ratio *= (float(new_v) / old_v) ** el[k]
        cf_params = dict(factual)
        cf_params.update(change)
        pred = self.sm.predict(cf_params).get(output_key)
        return {"counterfactual_params": cf_params,
                "ratio_vs_factual": round(float(ratio), 4),
                "absolute_prediction": None if pred is None
                else round(float(pred[0]), 4),
                "elasticities_used": el}

    # ------------------------------------------------- constraint shift query
    def if_constraint_were(self, primary: str, factual: dict,
                           output_key: str, actual_value: float,
                           new_limit: float) -> dict | None:
        """A TRUE rung-3 question: the constraint was never varied in the
        data, yet we can answer it. Under y ~ x^b, hitting a new limit L
        needs x_new = x_old * (L/y_old)^(1/b)."""
        el = self.elasticities(output_key)
        b = el.get(primary)
        if not b or actual_value <= 0:
            return None
        x_new = float(factual[primary]) * (new_limit / actual_value) ** (1.0 / b)
        return {"question": f"if the limit on {output_key} had been "
                            f"{new_limit} instead of {actual_value:.1f}",
                "answer": {primary: round(float(x_new), 3)},
                "exponent_used": b}

    # ------------------------------------------------------------- verify it
    @staticmethod
    def verify(tool, fixed: dict, claim: dict, output_key: str,
               iteration: int, mission: str = "") -> dict:
        """Settle a counterfactual with ONE real solve. This is what makes
        rung 3 honest rather than decorative."""
        params = claim["counterfactual_params"]
        t0 = time.time()
        try:
            r = tool(iteration=iteration, mission=mission, **fixed, **params)
            actual = float(r.get(output_key, float("nan")))
            pred = claim.get("absolute_prediction")
            err = (abs(pred - actual) / actual * 100
                   if pred and actual and actual > 0 else None)
            out = {"verified": True, "predicted": pred, "actual": actual,
                   "error_pct": None if err is None else round(err, 2),
                   "wall_time_s": round(time.time() - t0, 2)}
        except Exception as exc:
            out = {"verified": False, "error": str(exc)[:200]}
        SCENE.mkdir(parents=True, exist_ok=True)
        with CF_LOG.open("a") as fh:
            fh.write(json.dumps({"t": time.time(), "claim": claim,
                                 "result": out}) + "\n")
        return out

    # ------------------------------------------------------------- reporting
    @staticmethod
    def rung_achieved() -> int:
        """3 only if at least one counterfactual was VERIFIED against a
        real solve. Claiming rung 3 without that test would be exactly the
        self-flattery the scorecard exists to prevent."""
        if not CF_LOG.exists():
            return 2
        for ln in CF_LOG.read_text().splitlines():
            try:
                if json.loads(ln)["result"].get("verified"):
                    return 3
            except Exception:
                continue
        return 2
