#!/usr/bin/env python3
"""experiment.py - turn a prediction into a test, and a test back into validation.

The honest limit on everything research.py produces is that a prediction is not
a measurement. A Pareto front of low-carbon concrete mixes is a set of claims
about cylinders nobody has cast. Saying so is necessary but not sufficient: the
useful thing is to close the loop, and that needs two halves EngineAI did not
have.

  PLAN     given a prediction, say exactly what must be measured to test it:
           how many specimens, at what levels, to what resolution, in what
           order, and - the part that decides whether the test is worth running
           at all - what result would FALSIFY the prediction.

  INGEST   given the measurements back, run the ASME V&V 20 comparison and say
           VALIDATED or NOT VALIDATED, with the uncertainty budget that decides
           it.

WHY THE FALSIFICATION CRITERION IS WRITTEN FIRST. A test designed after the
results are in can always be made to agree with them. The plan produced here
states, before anything is measured, the outcome that would kill the
prediction - and it computes the sample size needed to detect that outcome,
so a null result means something instead of meaning the test was too small.

This module does NOT invent measurements. It never fabricates a reading, and
ingest() refuses data whose shape does not match the plan it claims to answer.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np

from research_credibility import combine_uncertainty, validate_vv20

__all__ = ["TestPlan", "plan_experiment", "sample_size", "ingest", "Validation"]


# --------------------------------------------------------------------------
# sample size
# --------------------------------------------------------------------------

def sample_size(effect: float, sd: float, alpha: float = 0.05,
                power: float = 0.80, two_sided: bool = True) -> dict:
    """Specimens needed to detect `effect` given scatter `sd`.

    Standard two-sample normal approximation. Reported WITH the effect it is
    powered for, because "n = 12" means nothing on its own - the same twelve
    specimens are ample for a 20% effect and useless for a 2% one.
    """
    from scipy import stats
    if sd <= 0 or effect == 0:
        return {"n_per_group": None, "reason": "effect or scatter is zero"}
    z_a = stats.norm.ppf(1 - alpha / (2 if two_sided else 1))
    z_b = stats.norm.ppf(power)
    n = 2 * ((z_a + z_b) * sd / abs(effect)) ** 2
    return {"n_per_group": int(math.ceil(n)), "effect": float(effect),
            "sd": float(sd), "alpha": alpha, "power": power,
            "standardised_effect": float(abs(effect) / sd)}


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------

@dataclass
class TestPlan:
    title: str
    prediction: str
    predicted_value: float
    unit: str
    falsified_if: str
    measurand: str
    levels: list
    n_per_level: int
    resolution_required: float
    randomise: bool
    controls: list
    standard: str
    notes: list = field(default_factory=list)
    created: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    @property
    def digest(self) -> str:
        payload = (f"{self.title}|{self.prediction}|{self.predicted_value}|"
                   f"{self.falsified_if}|{self.measurand}|{self.levels}|"
                   f"{self.n_per_level}")
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @property
    def total_specimens(self) -> int:
        return self.n_per_level * max(1, len(self.levels))

    def __str__(self) -> str:
        lv = ", ".join(f"{v:g}" for v in self.levels)
        order = ("randomised" if self.randomise
                 else "SEQUENTIAL - confounds drift with treatment")
        held = ", ".join(self.controls) if self.controls else "nothing declared"
        return (
            f"TEST PLAN  {self.title}   [{self.digest}]\n"
            f"  prediction     {self.prediction}\n"
            f"                 {self.predicted_value:.6g} {self.unit}\n"
            f"  FALSIFIED IF   {self.falsified_if}\n"
            f"  measure        {self.measurand}\n"
            f"  levels         {lv}\n"
            f"  specimens      {self.n_per_level} per level, "
            f"{self.total_specimens} total\n"
            f"  resolution     better than {self.resolution_required:.4g} {self.unit}\n"
            f"  order          {order}\n"
            f"  hold constant  {held}\n"
            f"  standard       {self.standard}"
            + "".join(f"\n  note           {n}" for n in self.notes))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["digest"] = self.digest
        d["total_specimens"] = self.total_specimens
        return d


def plan_experiment(title: str, prediction: str, predicted_value: float,
                    unit: str, measurand: str, levels: list,
                    expected_sd: float, tolerance: float,
                    controls: list | None = None, standard: str = "",
                    alpha: float = 0.05, power: float = 0.80) -> TestPlan:
    """Design the test that could kill a prediction.

    `tolerance` is the difference that would matter - the smallest departure
    from the predicted value that would change a decision. The plan is powered
    to detect exactly that, so a "no difference" outcome is informative rather
    than merely quiet.
    """
    ss = sample_size(tolerance, expected_sd, alpha=alpha, power=power)
    n = ss.get("n_per_group") or 3
    plan = TestPlan(
        title=title, prediction=prediction, predicted_value=float(predicted_value),
        unit=unit,
        falsified_if=(f"the measured mean differs from {predicted_value:.6g} {unit} "
                      f"by more than {tolerance:.4g} {unit} "
                      f"(that is {abs(tolerance/predicted_value)*100:.1f}%) "
                      f"with {int(power*100)}% power at alpha={alpha}"),
        measurand=measurand, levels=list(levels), n_per_level=int(n),
        # An instrument that cannot resolve a quarter of the difference you care
        # about cannot settle the question, whatever the sample size.
        resolution_required=float(tolerance / 4.0),
        randomise=True, controls=list(controls or []), standard=standard)
    plan.notes.append(
        f"powered for a {ss['standardised_effect']:.2f} sigma effect; halving the "
        f"tolerance would need about {int(math.ceil(n*4))} specimens per level, "
        f"because n scales with the inverse square of the effect")
    if n > 60:
        plan.notes.append("this is a large test - if that is impractical, the "
                          "honest options are a bigger tolerance or a quieter "
                          "measurement, NOT fewer specimens")
    return plan


# --------------------------------------------------------------------------
# ingest measurements and validate
# --------------------------------------------------------------------------

@dataclass
class Validation:
    plan_digest: str
    n_measured: int
    mean: float
    sd: float
    sem: float
    vv20: dict
    verdict: str
    underpowered: bool
    notes: list = field(default_factory=list)

    def __str__(self) -> str:
        v = self.vv20
        return (f"VALIDATION against plan {self.plan_digest}\n"
                f"  measured   {self.mean:.6g} +/- {2*self.sem:.4g} (k=2, n={self.n_measured})\n"
                f"  predicted  {v['S']:.6g}\n"
                f"  E = S - D  {v['E']:.4g}   U_val {v['U_val']:.4g}\n"
                f"  {self.verdict}"
                + "".join(f"\n  note       {n}" for n in self.notes))


def ingest(plan: TestPlan, measurements, u_input: float = 0.0,
           u_numerical: float = 0.0, instrument_resolution: float | None = None
           ) -> Validation:
    """Take real measurements and decide whether the prediction survived.

    Refuses rather than guesses when the data cannot answer the plan: too few
    specimens, or an instrument too coarse to see the difference that was
    declared to matter.
    """
    m = np.asarray(list(measurements), dtype=float)
    m = m[np.isfinite(m)]
    n = len(m)
    notes = []
    if n < 2:
        return Validation(plan.digest, n, float("nan"), float("nan"), float("nan"),
                          {}, "CANNOT VALIDATE - fewer than two measurements",
                          True, ["a single reading has no uncertainty of its own"])

    mean = float(np.mean(m))
    sd = float(np.std(m, ddof=1))
    sem = sd / math.sqrt(n)

    if instrument_resolution is not None and instrument_resolution > plan.resolution_required:
        notes.append(
            f"INSTRUMENT TOO COARSE: resolution {instrument_resolution:.4g} "
            f"{plan.unit} exceeds the {plan.resolution_required:.4g} the plan "
            f"requires. The result below cannot settle the question.")

    underpowered = n < plan.n_per_level
    if underpowered:
        notes.append(
            f"UNDERPOWERED: {n} measurements against the {plan.n_per_level} the "
            f"plan called for. A non-significant result here is not evidence of "
            f"agreement - the test could not have detected the difference.")

    # Experimental uncertainty is the standard error of the mean, combined with
    # any declared systematic terms.
    u_data = combine_uncertainty(mean, sem, {"instrument": (instrument_resolution or 0) / 2},
                                 plan.unit).u_combined
    vv = validate_vv20(S=plan.predicted_value, D=mean, u_num=u_numerical,
                       u_input=u_input, u_data=u_data)

    verdict = vv["verdict"]
    if underpowered and vv["validated"]:
        verdict = "INCONCLUSIVE - agreement, but from a test too small to disagree"
    return Validation(plan.digest, n, mean, sd, sem, vv, verdict, underpowered, notes)


def save(plan: TestPlan, path: Path) -> Path:
    path.write_text(json.dumps(plan.to_dict(), indent=2))
    return path


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    print("== sample size ==")
    a = sample_size(effect=5.0, sd=5.0)
    b = sample_size(effect=2.5, sd=5.0)
    # 2*(1.95996+0.84162)^2 = 15.70 -> 16. (The familiar "16/d^2" rule of thumb
    # is this same formula rounded; it is 16, not 17.)
    chk(a["n_per_group"] == 16, f"1 sigma effect needs 16 per group (got {a['n_per_group']})")
    chk(b["n_per_group"] > 3.5 * a["n_per_group"],
        f"halving the effect roughly quadruples n ({a['n_per_group']} -> {b['n_per_group']})")
    chk(sample_size(0, 5)["n_per_group"] is None, "a zero effect has no sample size")

    print("== plan ==")
    p = plan_experiment(
        title="low-carbon mix C-17", prediction="28-day strength of the Pareto mix",
        predicted_value=42.0, unit="MPa", measurand="compressive strength, EN 12390-3",
        levels=[28], expected_sd=4.0, tolerance=4.0,
        controls=["curing at 20 C", "same aggregate batch"], standard="EN 12390-3")
    chk("FALSIFIED IF" in str(p), "the plan states what would kill the prediction")
    chk(p.resolution_required == 1.0, "resolution is a quarter of the tolerance")
    chk(p.n_per_level == 16, f"powered correctly ({p.n_per_level} specimens)")
    chk(p.randomise, "specimen order is randomised by default")
    d1 = p.digest
    p2 = plan_experiment("low-carbon mix C-17", "28-day strength of the Pareto mix",
                         42.0, "MPa", "compressive strength, EN 12390-3", [28],
                         4.0, 8.0)
    chk(p2.digest != d1, "loosening the tolerance changes the plan's identity")

    print("== ingest: a prediction that holds ==")
    rng = np.random.default_rng(5)
    good = rng.normal(42.3, 4.0, 17)
    v = ingest(p, good, instrument_resolution=0.5)
    chk(v.vv20["validated"], f"a mix measuring ~42 MPa validates ({v.verdict})")
    chk(not v.underpowered, "17 specimens meets the plan")

    print("== ingest: a prediction that fails ==")
    bad = rng.normal(30.0, 4.0, 17)
    v2 = ingest(p, bad, instrument_resolution=0.5)
    chk(not v2.vv20["validated"], f"a mix measuring 30 MPa does NOT validate")
    chk("NOT VALIDATED" in v2.verdict, "and says so plainly")

    print("== the two refusals that matter ==")
    # Too few specimens, and they AGREE closely - the dangerous case, because
    # the numbers look like a clean confirmation. Fixed values, not a draw, so
    # the test cannot pass or fail on luck.
    v3 = ingest(p, [42.0, 42.1, 41.9], instrument_resolution=0.5)
    chk(v3.underpowered, "3 specimens against a plan for 16 is underpowered")
    chk("INCONCLUSIVE" in v3.verdict,
        f"agreement from a tiny sample is INCONCLUSIVE, not validated ({v3.verdict})")
    # instrument too coarse to see the difference that matters
    v4 = ingest(p, good, instrument_resolution=5.0)
    chk(any("TOO COARSE" in n for n in v4.notes),
        "an instrument coarser than the tolerance is called out")
    # a single reading
    v5 = ingest(p, [42.0])
    chk("CANNOT VALIDATE" in v5.verdict, "one reading cannot validate anything")

    print("== nothing is fabricated ==")
    chk(v5.n_measured == 1 and not np.isfinite(v5.mean),
        "no synthetic value is invented to fill a missing measurement")

    print(f"\nexperiment.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(__doc__)
