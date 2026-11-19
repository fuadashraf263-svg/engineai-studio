#!/usr/bin/env python3
"""research_credibility.py - the protocol engineers and scientists already trust.

EngineAI does not get to invent its own standard of proof. Four bodies of
practice already define what a computational result must carry before a
qualified reviewer will accept it, and this module implements them by name so
that a reviewer can check the work against a document they already know:

  GUM (JCGM 100:2008)  every reported value carries a combined standard
                       uncertainty and an expanded uncertainty at k=2 (95%),
                       separated into Type A (statistical) and Type B
                       (systematic) contributions.

  ASME V&V 20-2009     validation compares a computed value S to a measured
                       value D through E = S - D against a validation
                       uncertainty u_val built from numerical, input and
                       experimental parts. A result is validated when
                       |E| <= U_val, and NOT when E merely looks small.

  NASA-STD-7009A       the Credibility Assessment Scale: eight factors scored
                       0-4 from evidence, with the reported credibility being
                       the WEAKEST link, not the average. Averaging is how a
                       model with no validation gets a respectable score.

  ASTM E691 / Roache   precision by repeated independent runs, and iteration
                       to convergence with an observed order, so that a number
                       is quoted only once it has stopped moving.

THE ITERATION RULE. Nothing here reports a single-pass answer. Every quantity
is recomputed at increasing effort until two conditions hold at once: the
estimate stops changing (relative drift below tolerance) and its own standard
error falls under target. The convergence history is reported with the value,
so a reviewer can see it settle rather than take the final number on trust.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Sequence

import numpy as np

__all__ = ["Uncertainty", "combine_uncertainty", "converge", "Convergence",
           "validate_vv20", "credibility_scale", "CredibilityScore",
           "reproducibility_manifest"]


# --------------------------------------------------------------------------
# GUM - uncertainty
# --------------------------------------------------------------------------

@dataclass
class Uncertainty:
    """A value with its uncertainty budget, in GUM terms."""
    value: float
    u_type_a: float                 # statistical, from repeated observation
    u_type_b: float                 # systematic, from judgement or spec
    unit: str = ""
    k: float = 2.0                  # coverage factor; k=2 is ~95% for normal
    components: dict = field(default_factory=dict)

    @property
    def u_combined(self) -> float:
        return float(np.hypot(self.u_type_a, self.u_type_b))

    @property
    def expanded(self) -> float:
        return self.k * self.u_combined

    @property
    def interval(self) -> tuple[float, float]:
        return (self.value - self.expanded, self.value + self.expanded)

    @property
    def relative(self) -> float:
        return self.u_combined / abs(self.value) if self.value else float("inf")

    def __str__(self) -> str:
        lo, hi = self.interval
        return (f"{self.value:.6g} +/- {self.expanded:.3g} {self.unit} "
                f"(k={self.k:g}, 95%)  [{lo:.6g}, {hi:.6g}]  "
                f"u_A={self.u_type_a:.3g} u_B={self.u_type_b:.3g}")


def combine_uncertainty(value: float, type_a: float,
                        type_b_components: dict[str, float] | None = None,
                        unit: str = "", k: float = 2.0) -> Uncertainty:
    """Root-sum-square combination, GUM section 5. Type B parts are named.

    Naming the Type B components matters: an uncertainty budget whose
    systematic terms are unlabelled cannot be audited, and an unauditable
    budget is decoration.
    """
    comps = dict(type_b_components or {})
    u_b = float(np.sqrt(sum(v ** 2 for v in comps.values()))) if comps else 0.0
    return Uncertainty(value, float(type_a), u_b, unit, k, comps)


# --------------------------------------------------------------------------
# iteration to convergence
# --------------------------------------------------------------------------

@dataclass
class Convergence:
    converged: bool
    value: float
    history: list
    efforts: list
    drift: float
    std_error: float
    observed_order: float | None
    iterations: int
    reason: str

    def __str__(self) -> str:
        hist = " -> ".join(f"{v:.6g}" for v in self.history)
        head = "CONVERGED" if self.converged else "NOT CONVERGED"
        order = f", observed order {self.observed_order:.2f}" if self.observed_order else ""
        return (f"  [{head}] {self.value:.6g} after {self.iterations} passes{order}\n"
                f"      history: {hist}\n"
                f"      drift {self.drift:.3e}, se {self.std_error:.3e} - {self.reason}")


def converge(estimator: Callable[[int, int], float], efforts: Sequence[int],
             tol: float = 1e-3, se_target: float = float("inf"),
             repeats: int = 3, seed: int = 20260811) -> Convergence:
    """Recompute at rising effort until the answer stops moving.

    estimator(effort, seed) -> float. At each effort level the estimator is run
    `repeats` times with different seeds, giving a Type A standard error for
    free; the level's value is the mean of those repeats.

    Convergence requires BOTH:
      drift  |v_n - v_(n-1)| / |v_n|  <= tol         the answer has settled
      se     standard error of the last level        <= se_target

    Requiring both is deliberate. A stable answer that is still noisy is
    precise-looking and wrong; a tight answer that is still moving is accurate
    -looking and wrong. Only the pair is trustworthy.
    """
    hist, ses, used = [], [], []
    for lvl, eff in enumerate(efforts):
        vals = []
        for r in range(repeats):
            try:
                vals.append(float(estimator(eff, seed + 1000 * lvl + r)))
            except Exception:
                vals.append(float("nan"))
        v = np.array(vals, dtype=float)
        good = v[np.isfinite(v)]
        if not len(good):
            continue
        hist.append(float(np.mean(good)))
        ses.append(float(np.std(good, ddof=1) / np.sqrt(len(good))) if len(good) > 1 else 0.0)
        used.append(eff)
        if len(hist) >= 2:
            drift = abs(hist[-1] - hist[-2]) / (abs(hist[-1]) or 1.0)
            if drift <= tol and ses[-1] <= se_target:
                return Convergence(True, hist[-1], hist, used, drift, ses[-1],
                                   _observed_order(hist, used), len(hist),
                                   f"drift {drift:.2e} <= {tol:.0e} and se within target")

    if not hist:
        return Convergence(False, float("nan"), [], [], float("nan"), float("nan"),
                           None, 0, "estimator produced no finite value")
    drift = (abs(hist[-1] - hist[-2]) / (abs(hist[-1]) or 1.0)) if len(hist) > 1 else float("inf")
    why = ("effort budget exhausted while still drifting" if drift > tol
           else f"settled but standard error {ses[-1]:.2e} exceeds target {se_target:.2e}")
    return Convergence(False, hist[-1], hist, used, drift, ses[-1],
                       _observed_order(hist, used), len(hist), why)


def _observed_order(hist: list, efforts: list) -> float | None:
    """Roache-style observed order of convergence from the last three levels."""
    if len(hist) < 3:
        return None
    f1, f2, f3 = hist[-3], hist[-2], hist[-1]
    d1, d2 = f2 - f1, f3 - f2
    if abs(d1) < 1e-15 or abs(d2) < 1e-15 or d2 / d1 <= 0:
        return None
    r = efforts[-1] / efforts[-2] if efforts[-2] else 2.0
    if r <= 1:
        return None
    try:
        return float(abs(np.log(abs(d1 / d2)) / np.log(r)))
    except Exception:
        return None


# --------------------------------------------------------------------------
# ASME V&V 20 - validation
# --------------------------------------------------------------------------

def validate_vv20(S: float, D: float, u_num: float, u_input: float,
                  u_data: float, k: float = 2.0) -> dict:
    """ASME V&V 20-2009 validation comparison.

    E = S - D is the comparison error. u_val combines the numerical, input and
    experimental uncertainties in quadrature. The model is validated at this
    point when |E| <= U_val = k*u_val.

    A small E with a large u_val is NOT a validated model, it is an
    uninformative test - and V&V 20 exists precisely to stop that being
    reported as agreement.
    """
    E = float(S - D)
    u_val = float(np.sqrt(u_num ** 2 + u_input ** 2 + u_data ** 2))
    U_val = k * u_val
    validated = abs(E) <= U_val
    return {
        "S": float(S), "D": float(D), "E": E, "u_num": float(u_num),
        "u_input": float(u_input), "u_data": float(u_data),
        "u_val": u_val, "U_val": U_val, "k": k, "validated": bool(validated),
        "E_over_Uval": (abs(E) / U_val) if U_val else float("inf"),
        "informative": bool(U_val < abs(D) * 0.10) if D else True,
        "verdict": ("VALIDATED" if validated else "NOT VALIDATED")
                   + ("" if (U_val < abs(D) * 0.10 if D else True)
                      else " (but the test is too imprecise to be informative)"),
    }


# --------------------------------------------------------------------------
# NASA-STD-7009A credibility assessment scale
# --------------------------------------------------------------------------

FACTORS = ["verification", "validation", "input_pedigree", "results_uncertainty",
           "results_robustness", "use_history", "ms_management", "people_qualifications"]

_LEVEL_NAMES = {0: "insufficient evidence", 1: "minimal", 2: "moderate",
                3: "strong", 4: "best practice"}


@dataclass
class CredibilityScore:
    scores: dict
    evidence: dict
    not_self_assessable: list

    @property
    def weakest(self) -> tuple[str, int]:
        assessable = {k: v for k, v in self.scores.items()
                      if k not in self.not_self_assessable}
        f = min(assessable, key=lambda k: assessable[k])
        return f, assessable[f]

    @property
    def overall(self) -> int:
        """NASA-STD-7009 reports the MINIMUM, not the mean.

        Averaging lets a model with zero validation look respectable on the
        strength of good documentation, which is exactly the failure the scale
        was written to prevent.
        """
        return self.weakest[1]

    def __str__(self) -> str:
        lines = [f"  NASA-STD-7009A credibility: LEVEL {self.overall} "
                 f"({_LEVEL_NAMES[self.overall]}) - set by the weakest factor, "
                 f"'{self.weakest[0]}'"]
        for f in FACTORS:
            if f in self.not_self_assessable:
                lines.append(f"    {f:22s}  NOT SELF-ASSESSABLE - {self.evidence.get(f,'')}")
            else:
                lines.append(f"    {f:22s}  {self.scores[f]}  {self.evidence.get(f, '')}")
        return "\n".join(lines)


def credibility_scale(*, n_verification_tests: int, verification_pass_rate: float,
                      n_validated_against_truth: int, n_validation_attempts: int,
                      inputs_traceable: bool, uncertainty_quantified: bool,
                      robustness_attacks: int, robustness_survived: int,
                      converged: bool, use_history_runs: int,
                      version_controlled: bool, seeds_fixed: bool,
                      people_level: int | None = None,
                      signatories: int = 0) -> CredibilityScore:
    """Score the eight NASA-STD-7009A factors from evidence, not from opinion.

    Two factors are refused rather than guessed. 'People qualifications' cannot
    be self-assessed by the software being assessed, and claiming a level for
    it would be the exact kind of unearned credibility the scale exists to
    expose.
    """
    s, e = {}, {}

    # 1 verification - does the code compute what it claims to compute
    if n_verification_tests >= 500 and verification_pass_rate >= 0.999:
        s["verification"] = 4
    elif n_verification_tests >= 100 and verification_pass_rate >= 0.99:
        s["verification"] = 3
    elif n_verification_tests >= 20 and verification_pass_rate >= 0.95:
        s["verification"] = 2
    elif n_verification_tests > 0:
        s["verification"] = 1
    else:
        s["verification"] = 0
    e["verification"] = f"{n_verification_tests} tests, {verification_pass_rate*100:.1f}% pass"

    # 2 validation - against independent truth, not against itself
    rate = (n_validated_against_truth / n_validation_attempts) if n_validation_attempts else 0.0
    if n_validation_attempts >= 10 and rate >= 0.9:
        s["validation"] = 3          # 4 requires independent EXPERIMENTAL data
    elif n_validation_attempts >= 5 and rate >= 0.8:
        s["validation"] = 2
    elif n_validation_attempts >= 1:
        s["validation"] = 1
    else:
        s["validation"] = 0
    e["validation"] = (f"{n_validated_against_truth}/{n_validation_attempts} against known "
                       f"closed forms; level 4 needs independent PHYSICAL test data, "
                       f"which no computation can supply for itself")

    # 3 input pedigree
    s["input_pedigree"] = 3 if inputs_traceable else 1
    e["input_pedigree"] = ("sources, licences and SHA-256 checksums recorded"
                           if inputs_traceable else "inputs not fully traceable")

    # 4 results uncertainty
    s["results_uncertainty"] = 3 if (uncertainty_quantified and converged) else (
        2 if uncertainty_quantified else 0)
    e["results_uncertainty"] = ("GUM budget with k=2 expanded uncertainty; iterated to "
                                "convergence" if (uncertainty_quantified and converged)
                                else "uncertainty reported without a convergence study"
                                if uncertainty_quantified else "no uncertainty reported")

    # 5 results robustness
    surv = (robustness_survived / robustness_attacks) if robustness_attacks else 0.0
    if robustness_attacks >= 50 and surv >= 0.85:
        s["results_robustness"] = 4
    elif robustness_attacks >= 20 and surv >= 0.75:
        s["results_robustness"] = 3
    elif robustness_attacks >= 5:
        s["results_robustness"] = 2
    else:
        s["results_robustness"] = 1
    e["results_robustness"] = (f"{robustness_survived}/{robustness_attacks} falsification "
                               f"attacks survived, incl. shuffled-target null tests")

    # 6 use history - brand new software has none, and cannot pretend otherwise
    s["use_history"] = 0 if use_history_runs < 10 else (1 if use_history_runs < 100 else 2)
    e["use_history"] = (f"{use_history_runs} recorded runs - this is new software with no "
                        f"field record, and no amount of internal testing substitutes "
                        f"for one")

    # 7 M&S management
    s["ms_management"] = 3 if (version_controlled and seeds_fixed) else 1
    e["ms_management"] = ("git history, fixed seeds, scripted end-to-end reruns"
                          if version_controlled and seeds_fixed else "not fully controlled")

    # 8 people qualifications - never self-assessed, but no longer permanently
    # zero either. field_record.py lets a NAMED engineer sign off against a
    # specific result hash, and that recorded signature is external evidence in
    # a way an internal test never is. Absent a signature it stays refused.
    if people_level is None:
        s["people_qualifications"] = 0
        e["people_qualifications"] = ("software cannot certify the competence of its "
                                      "own authors or reviewers; a named qualified "
                                      "engineer must sign off - see field_record.sign")
        not_self = ["people_qualifications"]
    else:
        s["people_qualifications"] = int(people_level)
        e["people_qualifications"] = (
            f"scored from {signatories} recorded sign-off(s) by named engineers, "
            f"not self-assessed" if people_level else
            "no sign-off recorded")
        not_self = []

    return CredibilityScore(s, e, not_self_assessable=not_self)


# --------------------------------------------------------------------------
# reproducibility
# --------------------------------------------------------------------------

def reproducibility_manifest(files: Sequence[str] = (), seeds: dict | None = None) -> dict:
    """Everything a third party needs to reproduce the run byte for byte."""
    import importlib
    pkgs = {}
    for m in ("numpy", "scipy", "pandas", "sklearn", "pymoo", "SALib"):
        try:
            pkgs[m] = getattr(importlib.import_module(m), "__version__", "unknown")
        except Exception:
            pkgs[m] = "absent"
    checks = {}
    for fp in files:
        try:
            with open(fp, "rb") as fh:
                checks[str(fp)] = hashlib.sha256(fh.read()).hexdigest()[:16]
        except OSError:
            checks[str(fp)] = "unreadable"
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": pkgs,
        "seeds": dict(seeds or {}),
        "file_sha256": checks,
    }


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

    print("== GUM uncertainty ==")
    u = combine_uncertainty(100.0, 2.0, {"calibration": 3.0, "resolution": 4.0}, "MPa")
    chk(abs(u.u_type_b - 5.0) < 1e-12, "Type B root-sum-square 3,4 -> 5")
    chk(abs(u.u_combined - np.hypot(2.0, 5.0)) < 1e-12, "combined is RSS of A and B")
    chk(abs(u.expanded - 2 * u.u_combined) < 1e-12, "k=2 doubles the combined")
    lo, hi = u.interval
    chk(lo < 100 < hi, "the interval brackets the value")

    print("== convergence: must both settle AND be precise ==")
    # a sequence approaching 5 as effort grows, with shrinking noise
    def est(effort, seed):
        r = np.random.default_rng(seed)
        return 5.0 + 10.0 / effort + r.normal(0, 1.0 / np.sqrt(effort))
    c = converge(est, [10, 100, 1000, 10000, 100000], tol=1e-2, repeats=4)
    chk(c.converged, f"a converging estimator converges ({c.reason})")
    chk(abs(c.value - 5.0) < 0.5, f"lands near the true 5.0 (got {c.value:.3f})")
    chk(len(c.history) >= 2, "history is recorded for review")

    # one that never settles must be reported as NOT converged
    def wander(effort, seed):
        r = np.random.default_rng(seed)
        return float(r.normal(0, 5))
    c2 = converge(wander, [10, 100, 1000], tol=1e-4, repeats=3)
    chk(not c2.converged, "a wandering estimator is reported NOT converged")

    # precise but still drifting: must NOT be called converged
    def drifting(effort, seed):
        return 1.0 + 0.5 * effort           # noiseless, but never settles
    c3 = converge(drifting, [1, 2, 4, 8], tol=1e-3, repeats=2)
    chk(not c3.converged, "noiseless drift is not mistaken for convergence")

    print("== ASME V&V 20 ==")
    v = validate_vv20(S=101.0, D=100.0, u_num=0.5, u_input=0.5, u_data=1.0)
    chk(v["validated"], f"E=1.0 inside U_val={v['U_val']:.2f} is validated")
    v2 = validate_vv20(S=130.0, D=100.0, u_num=0.5, u_input=0.5, u_data=1.0)
    chk(not v2["validated"], "a 30% error is not validated")
    # the trap: huge uncertainty makes anything 'agree'
    v3 = validate_vv20(S=130.0, D=100.0, u_num=50.0, u_input=50.0, u_data=50.0)
    chk(v3["validated"] and not v3["informative"],
        "a test too imprecise to fail is flagged as uninformative")
    chk("too imprecise" in v3["verdict"], "and says so in the verdict")

    print("== NASA-STD-7009A ==")
    good = credibility_scale(
        n_verification_tests=1200, verification_pass_rate=1.0,
        n_validated_against_truth=10, n_validation_attempts=11,
        inputs_traceable=True, uncertainty_quantified=True,
        robustness_attacks=71, robustness_survived=64, converged=True,
        use_history_runs=0, version_controlled=True, seeds_fixed=True)
    chk(good.scores["verification"] == 4, "1200 passing tests scores 4 on verification")
    chk(good.overall == 0, "overall is the MINIMUM, so no use history caps it at 0")
    chk(good.weakest[0] == "use_history", f"weakest factor named ({good.weakest[0]})")
    chk("people_qualifications" in good.not_self_assessable,
        "people qualification is refused rather than guessed")
    # a model with no validation must not score well however well documented
    bad = credibility_scale(
        n_verification_tests=1200, verification_pass_rate=1.0,
        n_validated_against_truth=0, n_validation_attempts=0,
        inputs_traceable=True, uncertainty_quantified=True,
        robustness_attacks=71, robustness_survived=64, converged=True,
        use_history_runs=500, version_controlled=True, seeds_fixed=True)
    chk(bad.scores["validation"] == 0, "no validation attempts scores 0")
    chk(bad.overall == 0, "and drags the overall to 0 despite everything else")

    # With a real field record and a named sign-off, the two capped factors can
    # rise - on evidence, never on assertion.
    signed = credibility_scale(
        n_verification_tests=1200, verification_pass_rate=1.0,
        n_validated_against_truth=10, n_validation_attempts=11,
        inputs_traceable=True, uncertainty_quantified=True,
        robustness_attacks=71, robustness_survived=64, converged=True,
        use_history_runs=150, version_controlled=True, seeds_fixed=True,
        people_level=2, signatories=1)
    chk(signed.scores["people_qualifications"] == 2,
        "a recorded sign-off scores the people factor")
    chk("people_qualifications" not in signed.not_self_assessable,
        "and it stops being 'not self-assessable' once a person signed")
    chk(signed.overall == 2, f"the overall rises to the new weakest link "
                             f"(got {signed.overall}, weakest {signed.weakest[0]})")
    chk("not self-assessed" in signed.evidence["people_qualifications"],
        "and the evidence says where the score came from")

    print("== reproducibility manifest ==")
    m = reproducibility_manifest([__file__], {"global": 20260811})
    chk(m["packages"]["numpy"] != "absent", "records package versions")
    chk(len(next(iter(m["file_sha256"].values()))) == 16, "records file checksums")
    chk(m["seeds"]["global"] == 20260811, "records seeds")

    print(f"\nresearch_credibility.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(__doc__)
