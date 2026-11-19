#!/usr/bin/env python3
"""reliability.py - how long it lasts, how it fails, and how bad the worst case is.

The biggest engineering hole left. EngineAI could size a beam and refuse to
certify it, but could not answer the four questions that decide whether a
design survives service:

  WEIBULL       how long does a population last, and what is B10 life?
                With right-censoring, because in any real test programme some
                specimens are still running when you stop, and throwing those
                away biases the answer pessimistically - discarding survivors
                is the classic way to under-predict life.

  FMEA          which failure mode deserves the effort? Risk priority numbers,
                with their well-known defect stated rather than hidden.

  FAULT TREE    what combination of failures brings the system down? Minimal
                cut sets, because a single cut set of size one is a single
                point of failure and that is the thing you must know.

  EXTREME VALUE what is the 50-year wind, the 100-year flood? Fitting the tail
                rather than the body, because a design load is a question about
                the tail and a normal distribution fitted to the whole record
                will understate it badly.

WHAT THIS MODULE REFUSES TO DO. It will not extrapolate a return period far
beyond the record that supports it. Ten years of data does not contain a
1000-year event, and a GEV fit will nonetheless produce a confident number for
one. That number is an artefact of the model, not a property of the world, and
return_period() says so rather than printing it.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field

import numpy as np

__all__ = ["weibull_mle", "weibull_median_rank", "b_life", "WeibullFit",
           "fmea", "FailureMode", "fault_tree", "minimal_cut_sets",
           "gev_fit", "return_period", "monte_carlo_reliability"]


# ==========================================================================
# WEIBULL
# ==========================================================================

@dataclass
class WeibullFit:
    beta: float                 # shape
    eta: float                  # scale (characteristic life, 63.2% failed)
    n_failures: int
    n_censored: int
    method: str
    r_squared: float = float("nan")

    @property
    def regime(self) -> str:
        """What the shape parameter says about the failure mechanism."""
        if self.beta < 0.95:
            return ("infant mortality (beta<1): failure rate DECREASING - "
                    "manufacturing defects or bad installation, burn-in helps")
        if self.beta <= 1.05:
            return ("random (beta=1): constant failure rate, memoryless - "
                    "preventive replacement does NOT help")
        if self.beta < 4:
            return (f"early wear-out (beta={self.beta:.2f}): failure rate "
                    "increasing - scheduled replacement is effective")
        return (f"rapid wear-out (beta={self.beta:.2f}): sharply increasing - "
                "a well-defined service life exists")

    def reliability(self, t):
        t = np.asarray(t, dtype=float)
        return np.exp(-((t / self.eta) ** self.beta))

    def hazard(self, t):
        t = np.asarray(t, dtype=float)
        return (self.beta / self.eta) * (t / self.eta) ** (self.beta - 1)

    def mttf(self) -> float:
        return self.eta * math.gamma(1 + 1 / self.beta)

    def __str__(self) -> str:
        return (f"  Weibull beta={self.beta:.4f}  eta={self.eta:.4f}\n"
                f"    {self.n_failures} failures, {self.n_censored} censored "
                f"({self.method})\n"
                f"    B10 = {b_life(self, 10):.4g}   MTTF = {self.mttf():.4g}\n"
                f"    {self.regime}")


def weibull_mle(times, censored=None, tol: float = 1e-10,
                max_iter: int = 200) -> WeibullFit:
    """Maximum likelihood Weibull, handling right-censored observations.

    censored[i] True means specimen i was still running when the test stopped.
    Those observations carry real information - the unit survived at least that
    long - and dropping them biases life downwards. Including them properly is
    the difference between a usable estimate and a pessimistic one.

    Solves the MLE equation for beta by Newton iteration, then eta in closed
    form given beta.
    """
    t = np.asarray(times, dtype=float)
    c = (np.zeros(len(t), dtype=bool) if censored is None
         else np.asarray(censored, dtype=bool))
    ok = np.isfinite(t) & (t > 0)
    t, c = t[ok], c[ok]
    r = int((~c).sum())
    if r < 2:
        raise ValueError("Weibull MLE needs at least two observed failures; "
                         "censored observations alone cannot locate the curve")

    lnt = np.log(t)
    beta = 1.0
    for _ in range(max_iter):
        tb = t ** beta
        s0, s1 = tb.sum(), (tb * lnt).sum()
        s2 = (tb * lnt * lnt).sum()
        f = s1 / s0 - 1.0 / beta - lnt[~c].mean()
        df = (s2 * s0 - s1 * s1) / (s0 * s0) + 1.0 / (beta * beta)
        step = f / df
        beta -= step
        beta = max(beta, 1e-6)
        if abs(step) < tol:
            break
    eta = float(((t ** beta).sum() / r) ** (1.0 / beta))
    return WeibullFit(float(beta), eta, r, int(c.sum()), "MLE (censoring aware)")


def weibull_median_rank(times) -> WeibullFit:
    """Median rank regression - the graphical method, complete data only.

    Kept because it is what an engineer draws on Weibull paper and is the
    check an MLE result gets compared against in practice. Bernard's
    approximation for the median rank.
    """
    t = np.sort(np.asarray(times, dtype=float))
    t = t[np.isfinite(t) & (t > 0)]
    n = len(t)
    if n < 3:
        raise ValueError("median rank regression needs at least three failures")
    i = np.arange(1, n + 1)
    F = (i - 0.3) / (n + 0.4)                      # Bernard
    x = np.log(t)
    y = np.log(-np.log(1 - F))
    A = np.column_stack([np.ones(n), x])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    beta = float(coef[1])
    eta = float(np.exp(-coef[0] / beta))
    pred = A @ coef
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot else float("nan")
    return WeibullFit(beta, eta, n, 0, "median rank regression", r2)


def b_life(fit: WeibullFit, percent: float = 10.0) -> float:
    """B{p} life: the time by which p% of the population has failed."""
    p = percent / 100.0
    return float(fit.eta * (-math.log(1 - p)) ** (1 / fit.beta))


# ==========================================================================
# FMEA
# ==========================================================================

@dataclass
class FailureMode:
    item: str
    mode: str
    effect: str
    severity: int              # 1-10
    occurrence: int            # 1-10
    detection: int             # 1-10, 10 = cannot detect
    cause: str = ""

    @property
    def rpn(self) -> int:
        return self.severity * self.occurrence * self.detection

    @property
    def critical(self) -> bool:
        """Severity 9-10 is critical REGARDLESS of RPN.

        This is the fix for the well-known RPN defect: severity 10,
        occurrence 1, detection 1 gives RPN 10 and sorts to the bottom, next
        to trivia. A mode that kills someone is not low priority because it is
        rare - and AIAG-VDA moved to action priority for exactly this reason.
        """
        return self.severity >= 9

    def __str__(self) -> str:
        flag = "  <-- CRITICAL (severity alone)" if self.critical else ""
        return (f"    RPN {self.rpn:4d}  S{self.severity} O{self.occurrence} "
                f"D{self.detection}  {self.item}: {self.mode}{flag}")


def fmea(modes, rpn_threshold: int = 100) -> dict:
    """Rank failure modes, by RPN and by severity, and say why both are needed."""
    for m in modes:
        for v, n in ((m.severity, "severity"), (m.occurrence, "occurrence"),
                     (m.detection, "detection")):
            if not 1 <= v <= 10:
                raise ValueError(f"{n} must be 1-10, got {v} for {m.mode}")
    by_rpn = sorted(modes, key=lambda m: -m.rpn)
    critical = [m for m in modes if m.critical]
    over = [m for m in by_rpn if m.rpn >= rpn_threshold]
    # modes a pure RPN ranking would bury despite being lethal
    missed = [m for m in critical if m.rpn < rpn_threshold]
    return {
        "n_modes": len(modes),
        "by_rpn": by_rpn,
        "over_threshold": over,
        "critical_by_severity": critical,
        "missed_by_rpn": missed,
        "max_rpn": by_rpn[0].rpn if by_rpn else 0,
        "note": ("RPN alone is not a priority order. Multiplying three ordinal "
                 "scales produces a number with no units and non-unique values, "
                 "and it buries rare catastrophes. Severity >= 9 is actioned "
                 "regardless of RPN."),
    }


# ==========================================================================
# FAULT TREE
# ==========================================================================

def minimal_cut_sets(tree) -> list:
    """Minimal combinations of basic events that cause the top event.

    tree is nested: ("AND"|"OR", [child, ...]) or a string naming a basic event.
    A cut set of size ONE is a single point of failure, which is usually the
    single most important thing a fault tree tells you.
    """
    def expand(node):
        if isinstance(node, str):
            return [frozenset([node])]
        op, kids = node[0].upper(), node[1]
        sets = [expand(k) for k in kids]
        if op == "OR":
            out = []
            for s in sets:
                out += s
            return out
        combined = [frozenset()]
        for s in sets:                     # AND: cartesian product
            combined = [a | b for a in combined for b in s]
        return combined

    raw = expand(tree)
    minimal = []
    for cs in sorted(raw, key=len):
        if not any(m <= cs for m in minimal):
            minimal.append(cs)
    return [set(m) for m in minimal]


def fault_tree(tree, probabilities: dict) -> dict:
    """Top-event probability from basic event probabilities.

    Uses the rare-event approximation (sum of cut set probabilities) and also
    the exact inclusion-exclusion for small trees, reporting BOTH when they
    differ materially - the approximation is an upper bound and drifts once
    probabilities are large.
    """
    cuts = minimal_cut_sets(tree)
    missing = {e for c in cuts for e in c} - set(probabilities)
    if missing:
        raise ValueError(f"no probability given for: {sorted(missing)}")

    cut_p = [math.prod(probabilities[e] for e in c) for c in cuts]
    rare = sum(cut_p)

    exact = float("nan")
    if len(cuts) <= 16:                     # 2^16 terms is the practical limit
        total = 0.0
        for mask in range(1, 1 << len(cuts)):
            idx = [i for i in range(len(cuts)) if mask >> i & 1]
            union = set().union(*(cuts[i] for i in idx))
            term = math.prod(probabilities[e] for e in union)
            total += term if len(idx) % 2 else -term
        exact = total

    spf = [sorted(c)[0] for c in cuts if len(c) == 1]
    return {
        "minimal_cut_sets": [sorted(c) for c in cuts],
        "n_cut_sets": len(cuts),
        "cut_set_probabilities": cut_p,
        "top_probability_rare_event": min(rare, 1.0),
        "top_probability_exact": exact,
        "single_points_of_failure": spf,
        "approximation_error": (abs(rare - exact) if exact == exact else float("nan")),
        "note": ("the rare-event sum is an UPPER bound and is only close when "
                 "the basic events are unlikely; the exact figure is "
                 "inclusion-exclusion over the cut sets"),
    }


# ==========================================================================
# EXTREME VALUE
# ==========================================================================

def gev_fit(block_maxima, method: str = "mle") -> dict:
    """Generalised extreme value fit to block maxima (annual maxima, usually).

    The sign of the shape parameter xi decides the tail:
      xi < 0  bounded above  (Weibull family) - there is a physical maximum
      xi = 0  exponential tail (Gumbel)
      xi > 0  heavy tail (Frechet) - no upper bound, and extrapolation is
              dangerous because the tail decays slowly
    """
    from scipy import stats
    x = np.asarray(block_maxima, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 5:
        raise ValueError("GEV needs at least five block maxima; fewer cannot "
                         "distinguish the tail shape from noise")
    c, loc, scale = stats.genextreme.fit(x)
    xi = -float(c)                          # scipy's c is the negated shape
    tail = ("bounded above (Weibull tail) - a physical maximum is implied"
            if xi < -0.02 else
            "heavy tailed (Frechet) - no upper bound, extrapolate with care"
            if xi > 0.02 else
            "exponential tail (Gumbel)")
    return {"xi": xi, "loc": float(loc), "scale": float(scale),
            "n": len(x), "c_scipy": float(c), "tail": tail,
            "record_max": float(x.max()), "record_min": float(x.min())}


def return_period(fit: dict, years: float, record_years: int | None = None) -> dict:
    """The value expected once every `years`, with an honest extrapolation limit.

    REFUSES beyond about 4x the record length. Ten years of annual maxima do
    not contain a 1000-year event; a GEV will still produce a confident number
    for one, and that number is a property of the fitted model rather than of
    the world. Reporting it without the caveat is how a design load ends up
    resting on an artefact.
    """
    from scipy import stats
    n = record_years if record_years is not None else fit.get("n", 0)
    p = 1.0 - 1.0 / years
    value = float(stats.genextreme.ppf(p, fit["c_scipy"], fit["loc"],
                                       fit["scale"]))
    limit = 4 * n if n else 0
    supported = bool(n and years <= limit)
    return {
        "return_period_years": years,
        "value": value,
        "supported_by_record": supported,
        "record_years": n,
        "extrapolation_limit_years": limit,
        "warning": ("" if supported else
                    f"EXTRAPOLATION BEYOND THE RECORD: {years:g} years from "
                    f"{n} years of data. Above roughly {limit} years this "
                    f"number describes the fitted model, not the climate. "
                    f"Use a regional frequency analysis or a longer record."),
    }


def monte_carlo_reliability(limit_state, variables: dict, n: int = 100_000,
                            seed: int = 20260811) -> dict:
    """Probability of failure by simulation, with its own sampling uncertainty.

    limit_state(**samples) returns g; failure is g <= 0, the standard
    convention. The standard error of pf is reported because a Monte Carlo
    estimate of a rare probability is itself uncertain, and quoting pf without
    it invites reading three significant figures into noise.
    """
    from scipy import stats
    rng = np.random.default_rng(seed)
    samples = {}
    for name, spec in variables.items():
        kind = spec[0]
        if kind == "normal":
            samples[name] = rng.normal(spec[1], spec[2], n)
        elif kind == "lognormal":
            mu, sd = spec[1], spec[2]
            s = math.sqrt(math.log(1 + (sd / mu) ** 2))
            m = math.log(mu) - 0.5 * s * s
            samples[name] = rng.lognormal(m, s, n)
        elif kind == "uniform":
            samples[name] = rng.uniform(spec[1], spec[2], n)
        elif kind == "gumbel":
            samples[name] = rng.gumbel(spec[1], spec[2], n)
        else:
            raise ValueError(f"unknown distribution '{kind}' for {name}")

    g = np.asarray(limit_state(**samples), dtype=float)
    failures = int((g <= 0).sum())
    pf = failures / n
    se = math.sqrt(pf * (1 - pf) / n) if 0 < pf < 1 else 0.0
    beta_hl = float(-stats.norm.ppf(pf)) if 0 < pf < 1 else float("inf")
    return {
        "p_failure": pf, "n_samples": n, "n_failures": failures,
        "standard_error": se,
        "pf_95ci": (max(0.0, pf - 1.96 * se), pf + 1.96 * se),
        "reliability_index_beta": beta_hl,
        "reliable_estimate": failures >= 30,
        "note": ("" if failures >= 30 else
                 f"only {failures} failures in {n} samples - the estimate is "
                 f"dominated by sampling noise. Increase n by roughly "
                 f"{max(1, int(30/max(failures,1)))}x, or use FORM/importance "
                 f"sampling for a rare event."),
    }


# ==========================================================================
# selftest
# ==========================================================================

def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    rng = np.random.default_rng(11)

    print("== Weibull recovers parameters it was generated from ==")
    beta_true, eta_true = 2.5, 1000.0
    data = eta_true * rng.weibull(beta_true, 4000)
    f = weibull_mle(data)
    chk(abs(f.beta - beta_true) / beta_true < 0.06,
        f"beta recovered ({f.beta:.3f} vs {beta_true})")
    chk(abs(f.eta - eta_true) / eta_true < 0.04,
        f"eta recovered ({f.eta:.1f} vs {eta_true})")
    # B10 has a closed form to check against
    b10_true = eta_true * (-math.log(0.9)) ** (1 / beta_true)
    chk(abs(b_life(f, 10) - b10_true) / b10_true < 0.08,
        f"B10 matches the closed form ({b_life(f,10):.1f} vs {b10_true:.1f})")
    chk("wear-out" in f.regime, f"beta=2.5 is called wear-out ({f.regime[:28]})")
    chk("random" in weibull_mle(1000 * rng.weibull(1.0, 3000)).regime,
        "beta=1 is called random / memoryless")
    chk("infant" in weibull_mle(1000 * rng.weibull(0.6, 3000)).regime,
        "beta<1 is called infant mortality")

    print("== censoring is handled, and matters ==")
    full = eta_true * rng.weibull(beta_true, 1500)
    cutoff = 900.0
    obs = np.minimum(full, cutoff)
    cens = full > cutoff
    f_cens = weibull_mle(obs, cens)
    # dropping the survivors entirely is the mistake this guards against
    f_wrong = weibull_mle(obs[~cens])
    chk(abs(f_cens.eta - eta_true) / eta_true < 0.06,
        f"censored MLE recovers eta ({f_cens.eta:.0f})")
    chk(f_wrong.eta < f_cens.eta,
        f"discarding survivors under-predicts life ({f_wrong.eta:.0f} vs "
        f"{f_cens.eta:.0f}) - the classic bias")
    chk(f_cens.n_censored == int(cens.sum()), "the censored count is reported")
    try:
        weibull_mle([100.0], [True])
        raised = False
    except ValueError:
        raised = True
    chk(raised, "censored observations alone cannot locate the curve, and it says so")

    print("== median rank regression agrees with MLE ==")
    mr = weibull_median_rank(data[:300])
    chk(abs(mr.beta - beta_true) / beta_true < 0.15,
        f"graphical method lands close ({mr.beta:.3f})")
    chk(mr.r_squared > 0.97, f"with a good fit (R2 {mr.r_squared:.4f})")

    print("== FMEA does not bury a rare catastrophe ==")
    modes = [
        FailureMode("brake line", "rupture", "loss of braking", 10, 1, 1),
        FailureMode("trim panel", "rattle", "noise", 2, 8, 3),
        FailureMode("sensor", "drift", "wrong reading", 6, 6, 5),
    ]
    r = fmea(modes)
    chk(r["by_rpn"][0].item == "sensor", "RPN ranks the sensor first (180)")
    chk(modes[0].rpn == 10, "the brake line scores only RPN 10")
    chk(modes[0].critical, "but is flagged critical on severity alone")
    chk(modes[0] in r["missed_by_rpn"],
        "and is explicitly listed as one a pure RPN ranking would miss")
    chk("buries rare catastrophes" in r["note"], "the defect in RPN is stated")
    try:
        fmea([FailureMode("x", "y", "z", 11, 1, 1)])
        bad = False
    except ValueError:
        bad = True
    chk(bad, "an out-of-range rating is refused")

    print("== fault tree finds single points of failure ==")
    tree = ("OR", ["mains_fail", ("AND", ["generator_fail", "battery_fail"])])
    cuts = minimal_cut_sets(tree)
    chk({"mains_fail"} in cuts, "the single point of failure is a cut set")
    chk({"generator_fail", "battery_fail"} in cuts, "and the redundant pair")
    chk(len(cuts) == 2, f"exactly two minimal cut sets ({len(cuts)})")

    ft = fault_tree(tree, {"mains_fail": 0.01, "generator_fail": 0.05,
                           "battery_fail": 0.02})
    chk(ft["single_points_of_failure"] == ["mains_fail"],
        "the SPF is named explicitly")
    # exact: P(A or B) = 0.01 + 0.001 - 0.01*0.001
    expected = 0.01 + 0.001 - 0.01 * 0.001
    chk(abs(ft["top_probability_exact"] - expected) < 1e-12,
        f"exact inclusion-exclusion is right ({ft['top_probability_exact']:.8f})")
    chk(ft["top_probability_rare_event"] > ft["top_probability_exact"],
        "and the rare-event sum is the upper bound it claims to be")

    # a pure AND tree has no single point of failure
    ft2 = fault_tree(("AND", ["a", "b", "c"]), {"a": .1, "b": .1, "c": .1})
    chk(not ft2["single_points_of_failure"], "triple redundancy has no SPF")
    chk(abs(ft2["top_probability_exact"] - 0.001) < 1e-12, "and P = 1e-3")

    print("== extreme value: tail shape and honest extrapolation ==")
    from scipy import stats
    gum = stats.gumbel_r.rvs(loc=25, scale=4, size=60, random_state=7)
    g = gev_fit(gum)
    chk(abs(g["xi"]) < 0.25, f"a Gumbel sample gives xi near zero ({g['xi']:.3f})")
    r50 = return_period(g, 50, record_years=60)
    chk(r50["supported_by_record"], "a 50-year value from 60 years is supported")
    chk(r50["value"] > g["record_max"] * 0.8, "and exceeds most of the record")
    r1000 = return_period(g, 1000, record_years=60)
    chk(not r1000["supported_by_record"], "a 1000-year value from 60 years is NOT")
    chk("EXTRAPOLATION BEYOND THE RECORD" in r1000["warning"],
        "and the warning says so plainly")
    chk("fitted model, not the climate" in r1000["warning"],
        "explaining what the number actually describes")
    try:
        gev_fit([1, 2, 3])
        few = False
    except ValueError:
        few = True
    chk(few, "three points cannot determine a tail shape, and it refuses")

    print("== Monte Carlo reliability, with its own uncertainty ==")
    # R ~ N(100,10), S ~ N(60,10): g = R - S, beta = 40/sqrt(200) = 2.828
    mc = monte_carlo_reliability(lambda R, S: R - S,
                                 {"R": ("normal", 100, 10),
                                  "S": ("normal", 60, 10)}, n=200_000)
    beta_exact = 40 / math.sqrt(200)
    chk(abs(mc["reliability_index_beta"] - beta_exact) < 0.05,
        f"beta matches the analytic 2.828 ({mc['reliability_index_beta']:.3f})")
    chk(mc["standard_error"] > 0, "a standard error is reported")
    lo, hi = mc["pf_95ci"]
    chk(lo < mc["p_failure"] < hi, "and the CI brackets the estimate")
    chk(mc["reliable_estimate"], "enough failures for the estimate to mean something")

    rare = monte_carlo_reliability(lambda R, S: R - S,
                                   {"R": ("normal", 100, 5),
                                    "S": ("normal", 30, 5)}, n=2000)
    chk(not rare["reliable_estimate"], "a rare event with few samples is flagged")
    chk("dominated by sampling noise" in rare["note"],
        "and told what to do about it")

    print(f"\nreliability.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(__doc__)
