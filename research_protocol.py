#!/usr/bin/env python3
"""research_protocol.py - the safeguards that stop research being wrong.

The falsifier in research.py attacks a result after it exists. That is not
enough. Most false findings in the published literature are not arithmetic
errors - they are process failures that happen BEFORE and AROUND the analysis:

  the hypothesis was written after seeing the result      -> preregister()
  the target leaked into the features                     -> audit_dataset()
  twenty tests were run and the lucky one was reported    -> fdr_correct()
  a confounder drove both variables                       -> control_for()
  the effect vanished on other data                       -> replicate()
  the test could never have detected the effect anyway    -> power_check()

This module implements those six. Each returns evidence, not reassurance, and
each can fail. Nothing here makes a result more likely to be true; it makes an
untrue result more likely to be caught.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Sequence

import numpy as np

__all__ = ["Hypothesis", "preregister", "audit_dataset", "fdr_correct",
           "control_for", "replicate", "power_check", "DataAudit"]


# --------------------------------------------------------------------------
# 1. pre-registration
# --------------------------------------------------------------------------

@dataclass
class Hypothesis:
    """A prediction written down BEFORE the test is run.

    The point is the timestamp and the hash. Once a hypothesis is registered,
    the criterion cannot be quietly widened to fit whatever came back - the
    single most common way an honest analyst produces a false claim.
    """
    name: str
    prediction: str
    test: str
    criterion: str
    source: str = ""                  # where the prediction comes from
    registered_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    outcome: str = "PENDING"          # SUPPORTED | REFUTED | INCONCLUSIVE
    observed: str = ""

    @property
    def digest(self) -> str:
        payload = f"{self.name}|{self.prediction}|{self.test}|{self.criterion}|{self.source}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def resolve(self, supported: bool | None, observed: str) -> "Hypothesis":
        self.outcome = ("SUPPORTED" if supported else "REFUTED") if supported is not None \
            else "INCONCLUSIVE"
        self.observed = observed
        return self

    def __str__(self) -> str:
        return (f"  [{self.outcome:12s}] {self.name}  (registered {self.digest})\n"
                f"      predicted: {self.prediction}\n"
                f"      criterion: {self.criterion}\n"
                f"      observed : {self.observed}")


_REGISTRY: list[Hypothesis] = []


def preregister(name: str, prediction: str, test: str, criterion: str,
                source: str = "") -> Hypothesis:
    """Record a prediction before running it. Returns the registered hypothesis."""
    h = Hypothesis(name, prediction, test, criterion, source)
    _REGISTRY.append(h)
    return h


def registry() -> list[Hypothesis]:
    return list(_REGISTRY)


# --------------------------------------------------------------------------
# 2. data audit, including leakage
# --------------------------------------------------------------------------

@dataclass
class DataAudit:
    n_rows: int
    n_cols: int
    missing: dict
    duplicate_rows: int
    constant_columns: list
    leakage_suspects: list
    sentinel_suspects: dict
    clean: bool

    def __str__(self) -> str:
        bits = [f"  {self.n_rows} rows x {self.n_cols} cols"]
        if self.duplicate_rows:
            bits.append(f"  {self.duplicate_rows} duplicate rows")
        if self.constant_columns:
            bits.append(f"  constant: {self.constant_columns}")
        top = sorted(self.missing.items(), key=lambda t: -t[1])[:3]
        if top and top[0][1]:
            bits.append("  missing: " + ", ".join(f"{k} {v}" for k, v in top if v))
        if self.sentinel_suspects:
            bits.append(f"  sentinel codes: {self.sentinel_suspects}")
        if self.leakage_suspects:
            bits.append(f"  LEAKAGE SUSPECTS: {self.leakage_suspects}")
        return "\n".join(bits)


def audit_dataset(data: dict[str, np.ndarray], target: str,
                  leak_r: float = 0.999) -> DataAudit:
    """Check a dataset before analysing it.

    LEAKAGE is the one that matters. A feature that is an algebraic
    transformation of the target produces a superb model and a worthless
    finding, and it is invisible in the accuracy numbers - which is exactly
    why it has to be checked separately rather than inferred from a good R^2.
    """
    cols = {k: np.asarray(v, dtype=float) for k, v in data.items()}
    n = len(next(iter(cols.values())))
    y = cols[target]
    missing = {k: int(np.sum(~np.isfinite(v))) for k, v in cols.items()}
    stacked = np.column_stack(list(cols.values()))
    finite = stacked[np.all(np.isfinite(stacked), axis=1)]
    dup = int(len(finite) - len(np.unique(finite, axis=0))) if len(finite) else 0
    const = [k for k, v in cols.items() if np.nanstd(v) == 0]

    # sentinel values: a repeated impossible-looking constant such as -200
    sentinels = {}
    for k, v in cols.items():
        fv = v[np.isfinite(v)]
        if not len(fv):
            continue
        vals, counts = np.unique(fv, return_counts=True)
        for val, cnt in zip(vals, counts):
            if cnt >= max(20, 0.02 * len(fv)) and (val < 0 <= fv.max()) and val in (-999, -200, -99, -9999):
                sentinels[k] = f"{val:g} x{cnt}"

    leaks = []
    yf = np.isfinite(y)
    for k, v in cols.items():
        if k == target:
            continue
        m = yf & np.isfinite(v)
        if m.sum() < 10 or np.std(v[m]) == 0 or np.std(y[m]) == 0:
            continue
        if abs(np.corrcoef(v[m], y[m])[0, 1]) >= leak_r:
            leaks.append(k)
            continue
        with np.errstate(all="ignore"):           # a monotone transform still leaks
            pos = m & (v > 0) & (y > 0)
            if pos.sum() > 10 and np.std(np.log(v[pos])) > 0:
                if abs(np.corrcoef(np.log(v[pos]), np.log(y[pos]))[0, 1]) >= leak_r:
                    leaks.append(f"{k} (log-linear in target)")

    return DataAudit(n, len(cols), missing, dup, const, leaks, sentinels,
                     clean=not leaks and not const and not sentinels)


# --------------------------------------------------------------------------
# 3. multiple comparisons
# --------------------------------------------------------------------------

def fdr_correct(pvalues: Sequence[float], alpha: float = 0.05):
    """Benjamini-Hochberg. Returns (rejected flags, adjusted p-values).

    Run twenty independent tests at alpha = 0.05 and one will pass by chance.
    A battery that reports its winners without this correction is a machine
    for generating false findings.
    """
    p = np.asarray(pvalues, dtype=float)
    n = len(p)
    if n == 0:
        return np.array([], bool), np.array([])
    order = np.argsort(p)
    ranked = p[order]
    adj = ranked * n / (np.arange(n) + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1]      # enforce monotonicity
    adj = np.clip(adj, 0, 1)
    out_adj = np.empty(n)
    out_adj[order] = adj
    return out_adj <= alpha, out_adj


# --------------------------------------------------------------------------
# 4. confounding
# --------------------------------------------------------------------------

def control_for(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> dict:
    """Partial correlation of x and y with z held fixed, plus a stratified check.

    Reports BOTH the raw and the controlled association, because the
    interesting case is when they disagree - that disagreement is the
    confounder announcing itself.
    """
    x, y = np.asarray(x, float), np.asarray(y, float)
    Z = np.atleast_2d(np.asarray(z, float))
    if Z.shape[0] != len(x):
        Z = Z.T
    m = np.isfinite(x) & np.isfinite(y) & np.all(np.isfinite(Z), axis=1)
    x, y, Z = x[m], y[m], Z[m]
    A = np.column_stack([np.ones(len(x)), Z])
    rx = x - A @ np.linalg.lstsq(A, x, rcond=None)[0]
    ry = y - A @ np.linalg.lstsq(A, y, rcond=None)[0]
    raw = float(np.corrcoef(x, y)[0, 1])
    partial = float(np.corrcoef(rx, ry)[0, 1]) if np.std(rx) and np.std(ry) else float("nan")

    # stratified: does the sign hold inside slices of the confounder?
    signs = []
    if Z.shape[1] == 1:
        edges = np.percentile(Z[:, 0], np.linspace(0, 100, 11))
        for lo, hi in zip(edges[:-1], edges[1:]):
            s = (Z[:, 0] >= lo) & (Z[:, 0] <= hi)
            if s.sum() > 30 and np.std(x[s]) and np.std(y[s]):
                signs.append(np.sign(np.corrcoef(x[s], y[s])[0, 1]))
    agree = float(np.mean(np.array(signs) == np.sign(partial))) if signs else float("nan")
    return {"raw_r": raw, "partial_r": partial, "n": int(len(x)),
            "strata": len(signs), "strata_sign_agreement": agree,
            "sign_flipped": bool(np.isfinite(partial) and raw * partial < 0)}


# --------------------------------------------------------------------------
# 5. replication
# --------------------------------------------------------------------------

def replicate(analysis: Callable[[np.ndarray], float], n: int,
              n_splits: int = 2, seed: int = 20260811, tol: float = 0.25) -> dict:
    """Run the SAME analysis on disjoint halves and compare the answers.

    An effect that appears in one half and not the other is not an effect.
    This is the cheapest imitation of independent replication available
    without collecting new data, and it catches results driven by a subset.
    """
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    parts = np.array_split(idx, n_splits)
    vals = []
    for p in parts:
        try:
            vals.append(float(analysis(p)))
        except Exception:
            vals.append(float("nan"))
    v = np.array(vals, dtype=float)
    ok = np.all(np.isfinite(v))
    spread = float(np.nanmax(v) - np.nanmin(v)) if ok else float("nan")
    scale = float(np.nanmean(np.abs(v))) if ok else float("nan")
    rel = spread / scale if (ok and scale) else float("inf")
    same_sign = bool(ok and (np.all(v > 0) or np.all(v < 0)))
    return {"values": [float(x) for x in v], "relative_spread": rel,
            "same_sign": same_sign, "replicated": bool(ok and same_sign and rel <= tol)}


# --------------------------------------------------------------------------
# 6. power
# --------------------------------------------------------------------------

def power_check(n: int, effect: float, alpha: float = 0.05) -> dict:
    """Could this test have detected this effect at all?

    A 'no significant difference' from an underpowered test is not evidence of
    no difference, and reporting it as one is a false claim in the other
    direction.
    """
    from scipy import stats
    if n < 4:
        return {"n": n, "power": 0.0, "adequate": False,
                "min_detectable_r": float("nan")}
    # Fisher z for a correlation-sized effect
    se = 1.0 / math_sqrt(n - 3)
    zcrit = stats.norm.ppf(1 - alpha / 2)
    zeff = 0.5 * np.log((1 + abs(effect)) / (1 - abs(effect))) if abs(effect) < 1 else 10.0
    power = float(stats.norm.cdf(zeff / se - zcrit) + stats.norm.cdf(-zeff / se - zcrit))
    mdr = float(np.tanh(zcrit * se))
    return {"n": n, "power": power, "adequate": power >= 0.8, "min_detectable_r": mdr}


def math_sqrt(x: float) -> float:
    return float(np.sqrt(x))


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

    rng = np.random.default_rng(5)

    print("== pre-registration ==")
    h = preregister("test", "x rises with y", "OLS slope", "slope > 0", "textbook")
    d1 = h.digest
    chk(h.outcome == "PENDING", "starts pending")
    h.resolve(False, "slope = -0.4")
    chk(h.outcome == "REFUTED", "resolves to refuted")
    chk(h.digest == d1, "digest is fixed by the prediction, not the outcome")
    h2 = Hypothesis("test", "x rises with y", "OLS slope", "slope > 0.5", "textbook")
    chk(h2.digest != d1, "widening the criterion changes the digest")

    print("== leakage detection ==")
    x = rng.uniform(1, 10, 300)
    y = 3 * x + rng.normal(0, 0.01, 300)
    a = audit_dataset({"x": x, "copy": y * 2.0, "noise": rng.normal(0, 1, 300), "y": y}, "y")
    chk("copy" in " ".join(a.leakage_suspects), f"caught the scaled copy {a.leakage_suspects}")
    chk("noise" not in " ".join(a.leakage_suspects), "did not flag genuine noise")
    chk(not a.clean, "dataset with leakage is not clean")
    # A genuinely clean case needs real scatter: y = 3x + tiny noise is a
    # 0.99999 correlation, which the auditor is RIGHT to call leakage.
    y_scat = 3 * x + rng.normal(0, 3.0, 300)
    b = audit_dataset({"x": x, "noise": rng.normal(0, 1, 300), "y": y_scat}, "y")
    chk(b.clean, f"clean dataset passes (suspects {b.leakage_suspects})")
    chk(abs(np.corrcoef(x, y_scat)[0, 1]) > 0.6,
        "and it is clean despite a strong, honest correlation")
    c = audit_dataset({"x": x, "flag": np.where(rng.random(300) < 0.2, -200.0, x), "y": y}, "y")
    chk("flag" in c.sentinel_suspects, f"caught the -200 sentinel {c.sentinel_suspects}")

    print("== multiple comparisons ==")
    # 20 null tests: uncorrected, ~1 passes; corrected, none should
    nulls = [float(rng.uniform(0, 1)) for _ in range(20)]
    rej, adj = fdr_correct(nulls)
    chk(len(adj) == 20 and np.all(adj >= np.array(nulls) - 1e-12), "adjusted p >= raw p")
    rej2, adj2 = fdr_correct([0.001, 0.002, 0.5, 0.6, 0.7])
    chk(rej2[0] and rej2[1] and not rej2[2], "keeps real signals, drops the rest")
    chk(np.all(np.diff(adj2[np.argsort([0.001, 0.002, 0.5, 0.6, 0.7])]) >= -1e-12),
        "adjusted p-values are monotone")

    print("== confounding ==")
    # z drives both x and y; the x-y association is entirely spurious
    z = rng.normal(0, 1, 500)
    xs = z + rng.normal(0, 0.3, 500)
    ys = z + rng.normal(0, 0.3, 500)
    r = control_for(xs, ys, z)
    chk(abs(r["raw_r"]) > 0.7, f"raw association looks strong ({r['raw_r']:.2f})")
    chk(abs(r["partial_r"]) < 0.2, f"vanishes when z is held ({r['partial_r']:.2f})")
    # a real association must SURVIVE the control
    yr = 2 * xs + rng.normal(0, 0.3, 500)
    r2 = control_for(xs, yr, z)
    chk(abs(r2["partial_r"]) > 0.7, f"real association survives ({r2['partial_r']:.2f})")

    print("== replication ==")
    strong = rng.normal(0, 1, 600)
    tgt = 2 * strong + rng.normal(0, 0.5, 600)
    rep = replicate(lambda i: float(np.polyfit(strong[i], tgt[i], 1)[0]), 600)
    chk(rep["replicated"], f"a real slope replicates across halves {rep['values']}")
    junk = rng.normal(0, 1, 600)
    rep2 = replicate(lambda i: float(np.corrcoef(junk[i], rng.normal(0, 1, len(i)))[0, 1]), 600)
    chk(not rep2["replicated"], "a noise correlation does not replicate")

    print("== power ==")
    p_small = power_check(12, 0.2)
    chk(not p_small["adequate"], f"12 rows cannot detect r=0.2 (power {p_small['power']:.2f})")
    p_big = power_check(5000, 0.2)
    chk(p_big["adequate"], f"5000 rows can (power {p_big['power']:.2f})")
    chk(p_small["min_detectable_r"] > p_big["min_detectable_r"],
        "smaller samples need bigger effects")

    print(f"\nresearch_protocol.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(__doc__)
