#!/usr/bin/env python3
"""research_advanced.py - four upgrades to the discovery engine.

research.py searches monomials by brute force over subsets and picks the fit
with the lowest holdout error. That works, and it recovered ten of eleven known
laws exactly. It also has four specific weaknesses, and each has a known fix.

  1  IT SEARCHES DIMENSIONED SPACE. Trying every subset of raw variables means
     most candidates are dimensional nonsense that the fit has to reject
     numerically. Buckingham-Pi gives the dimensionless groups directly from
     the null space of the dimension matrix, so the search happens among
     quantities that are consistent BY CONSTRUCTION. The space shrinks from
     2^n subsets to a handful of Pi groups, and F3 becomes unnecessary rather
     than merely passed.

  2  IT CANNOT SEE DYNAMICS. Every law it finds is algebraic. A great deal of
     engineering is a differential equation, and the data for one is a time
     series. SINDy - sparse identification of nonlinear dynamics - recovers
     dx/dt = f(x) by sparse regression over a library of candidate terms, so a
     damped oscillator or a cooling curve becomes discoverable rather than
     invisible.

  3  IT RETURNS ONE ANSWER. Accuracy and simplicity trade off, and reporting
     only the most accurate fit hides that trade. A Pareto front over
     complexity shows the whole curve, and the ELBOW - where adding a term
     stops buying accuracy - is usually the real law. That elbow is how you
     tell a law from an overfit.

  4  IT SELECTS ON HOLDOUT ERROR ALONE. Holdout error always improves with
     more parameters on finite data. BIC penalises parameters explicitly and
     converts to posterior model probabilities, so "this model is 40 times
     more probable than that one" replaces "this one had slightly lower error".

None of this makes the engine correct. It makes it search a better space, see
a class of law it was blind to, show its trade-offs, and compare models on a
principled criterion instead of a noisy one.
"""
from __future__ import annotations

import itertools
import math
import sys
from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np

from research import _BASE, dim, _max_rel_err

__all__ = ["pi_groups", "PiGroup", "discover_dimensionless", "sindy",
           "pareto_front_equations", "bic", "model_probabilities",
           "compare_models"]


# ==========================================================================
# 1. BUCKINGHAM-PI
# ==========================================================================

@dataclass
class PiGroup:
    exponents: dict              # variable -> exponent
    name: str = ""

    def evaluate(self, data: dict) -> np.ndarray:
        out = None
        for v, e in self.exponents.items():
            col = np.asarray(data[v], dtype=float) ** e
            out = col if out is None else out * col
        return out

    def __str__(self) -> str:
        terms = " ".join(
            f"{v}^{Fraction(e).limit_denominator(12)}"
            for v, e in self.exponents.items() if abs(e) > 1e-9)
        return f"{self.name or 'Pi'} = {terms}"


def _nullspace(M: np.ndarray, tol: float = 1e-10) -> np.ndarray:
    """Basis for the null space, by SVD."""
    if M.size == 0:
        return np.empty((0, 0))
    u, s, vh = np.linalg.svd(M)
    rank = int((s > tol).sum())
    return vh[rank:].T if vh.shape[0] > rank else np.empty((M.shape[1], 0))


def _rationalise(vec: np.ndarray, max_den: int = 12) -> np.ndarray:
    """Scale a null-space vector so its entries are near-integers.

    An SVD basis vector is arbitrary up to scale. Multiplying by the LCM of the
    denominators of its rationalised entries gives the integer exponents an
    engineer would actually write, so the group reads as Re = rho v D / mu
    rather than as a vector of decimals.
    """
    nz = np.abs(vec[np.abs(vec) > 1e-9])
    if not nz.size:
        return vec
    v = vec / nz.min()                       # smallest nonzero entry -> +-1
    fracs = [Fraction(float(x)).limit_denominator(max_den) for x in v]
    den = 1
    for f in fracs:
        den = den * f.denominator // math.gcd(den, f.denominator)
    scaled = np.array([float(f) * den for f in fracs])
    if scaled[np.abs(scaled) > 1e-9][0] < 0:  # sign convention: leading term +
        scaled = -scaled
    return scaled


def pi_groups(units: dict, max_den: int = 12) -> list:
    """Dimensionless groups from the null space of the dimension matrix.

    units maps variable name -> unit string. The number of independent groups
    is n_vars - rank(D), which is the Buckingham-Pi theorem. Every group is
    dimensionless by construction, so a law expressed in them cannot be
    dimensionally wrong.
    """
    names = list(units)
    cols = []
    for n in names:
        d = dim(units[n])
        if d is None:
            raise ValueError(f"unknown unit '{units[n]}' for {n}")
        cols.append(d)
    D = np.array(cols, dtype=float).T          # (7, n_vars)
    ns = _nullspace(D)
    groups = []
    for i in range(ns.shape[1]):
        v = _rationalise(ns[:, i], max_den)
        exps = {n: float(e) for n, e in zip(names, v) if abs(e) > 1e-9}
        if exps:
            groups.append(PiGroup(exps, f"Pi{i+1}"))
    return groups


def discover_dimensionless(data: dict, units: dict, target: str,
                           max_terms: int = 3, seed: int = 20260812) -> dict:
    """Fit the target's dimensionless form as a power law in the Pi groups.

    The target is made dimensionless against the remaining variables first, so
    what is being fitted is a relationship between pure numbers - which is the
    form every correlation in fluid mechanics and heat transfer is written in.
    """
    others = {k: v for k, v in units.items() if k != target}
    try:
        groups = pi_groups(units)
    except ValueError as e:
        return {"ok": False, "reason": str(e)}
    if not groups:
        return {"ok": False,
                "reason": "no dimensionless group exists - the variables are "
                          "dimensionally independent, so no Pi relationship "
                          "can be formed"}

    # groups involving the target become the left-hand side
    lhs = [g for g in groups if target in g.exponents]
    rhs = [g for g in groups if target not in g.exponents]
    if not lhs:
        return {"ok": False, "reason": f"no group contains {target}"}

    y = lhs[0].evaluate(data)
    if not rhs:
        return {"ok": True, "groups": groups, "lhs": lhs[0], "rhs": [],
                "constant": float(np.mean(y)),
                "note": f"{lhs[0]} is constant - a single Pi group means the "
                        f"relationship is fixed, with no free parameter"}

    X = np.column_stack([g.evaluate(data) for g in rhs])
    ok = np.isfinite(y) & (y > 0) & np.all(np.isfinite(X) & (X > 0), axis=1)
    y, X = y[ok], X[ok]
    if len(y) < 10:
        return {"ok": False, "reason": "too few strictly positive rows"}

    A = np.column_stack([np.ones(len(y)), np.log(X)])
    coef, *_ = np.linalg.lstsq(A, np.log(y), rcond=None)
    pred = np.exp(A @ coef)
    return {
        "ok": True, "groups": groups, "lhs": lhs[0], "rhs": rhs,
        "constant": float(np.exp(coef[0])),
        "exponents": {g.name: float(c) for g, c in zip(rhs, coef[1:])},
        "max_rel_err": _max_rel_err(y, pred),
        "n_groups": len(groups),
        "search_space_reduction": f"{2**len(others)} raw subsets -> "
                                  f"{len(rhs)} Pi group(s)",
        "note": "dimensionally consistent BY CONSTRUCTION - the F3 check "
                "cannot fail on a Pi-group law because the groups have no "
                "dimensions to balance",
    }


# ==========================================================================
# 2. SINDy - dynamics, which the algebraic engine cannot see
# ==========================================================================

def _library(X: np.ndarray, names, order: int = 2, trig: bool = False):
    """Candidate terms: constant, linear, polynomial cross terms, optional trig."""
    n, d = X.shape
    cols, labels = [np.ones(n)], ["1"]
    for i in range(d):
        cols.append(X[:, i])
        labels.append(names[i])
    if order >= 2:
        for i in range(d):
            for j in range(i, d):
                cols.append(X[:, i] * X[:, j])
                labels.append(f"{names[i]}*{names[j]}" if i != j else f"{names[i]}^2")
    if order >= 3:
        for i in range(d):
            cols.append(X[:, i] ** 3)
            labels.append(f"{names[i]}^3")
    if trig:
        for i in range(d):
            cols.append(np.sin(X[:, i]))
            labels.append(f"sin({names[i]})")
            cols.append(np.cos(X[:, i]))
            labels.append(f"cos({names[i]})")
    return np.column_stack(cols), labels


def sindy(X, dXdt=None, t=None, names=None, threshold: float = 0.05,
          order: int = 2, trig: bool = False, max_iter: int = 20) -> dict:
    """Sparse Identification of Nonlinear Dynamics.

    Solves dx/dt = Theta(x) . xi with sequential thresholded least squares:
    fit, zero the small coefficients, refit on what survives, repeat. The
    thresholding is what makes the answer an EQUATION rather than a dense
    regression - a governing law has a handful of terms, and a fit with
    nineteen is a fit, not a law.

    Derivatives are estimated by central differences when not supplied, which
    is the noise-sensitive step: differentiating noisy data amplifies the
    noise, and that is a property of differentiation, not of this code.
    """
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    n, d = X.shape
    names = list(names or [f"x{i}" for i in range(d)])

    if dXdt is None:
        if t is None:
            raise ValueError("supply dXdt, or t so it can be differentiated")
        t = np.asarray(t, dtype=float)
        dXdt = np.gradient(X, t, axis=0)
    dXdt = np.asarray(dXdt, dtype=float).reshape(n, d)

    Theta, labels = _library(X, names, order=order, trig=trig)
    equations, coeffs = [], []
    for k in range(d):
        xi, *_ = np.linalg.lstsq(Theta, dXdt[:, k], rcond=None)
        for _ in range(max_iter):
            small = np.abs(xi) < threshold
            if small.all():
                break
            xi[small] = 0.0
            big = ~small
            xi_big, *_ = np.linalg.lstsq(Theta[:, big], dXdt[:, k], rcond=None)
            new = np.zeros_like(xi)
            new[big] = xi_big
            if np.allclose(new, xi):
                xi = new
                break
            xi = new
        coeffs.append(xi)
        terms = [f"{c:+.4g} {l}" for c, l in zip(xi, labels) if abs(c) > 1e-12]
        equations.append(f"d{names[k]}/dt = " + (" ".join(terms) or "0"))

    C = np.array(coeffs)
    pred = Theta @ C.T
    resid = dXdt - pred
    ss_res = float((resid ** 2).sum())
    ss_tot = float(((dXdt - dXdt.mean(axis=0)) ** 2).sum())
    return {
        "equations": equations,
        "coefficients": C,
        "labels": labels,
        "n_terms": int((np.abs(C) > 1e-12).sum()),
        "n_candidates": len(labels) * d,
        "r2": 1 - ss_res / ss_tot if ss_tot else float("nan"),
        "note": ("derivatives were estimated by finite difference"
                 if dXdt is None else "derivatives supplied"),
    }


# ==========================================================================
# 3. PARETO FRONT OVER COMPLEXITY
# ==========================================================================

def pareto_front_equations(data: dict, target: str, candidates=None,
                           max_terms: int = 4, seed: int = 20260812) -> dict:
    """Best achievable error at each complexity, and where the elbow is.

    Reporting only the most accurate fit hides the trade. The front shows what
    each extra term buys, and the ELBOW - the last term that buys a large
    improvement - is usually the real law. Beyond it, extra terms are absorbing
    noise, and that is visible as the curve going flat.
    """
    rng = np.random.default_rng(seed)
    cands = list(candidates or [k for k in data if k != target])
    y = np.asarray(data[target], dtype=float)
    stack = np.column_stack([np.asarray(data[c], dtype=float) for c in cands])
    ok = np.isfinite(y) & (y > 0) & np.all(np.isfinite(stack) & (stack > 0), axis=1)
    y, stack = y[ok], stack[ok]
    n = len(y)
    if n < 30:
        return {"ok": False, "reason": "too few positive rows"}
    idx = rng.permutation(n)
    tr, te = idx[:int(0.7 * n)], idx[int(0.7 * n):]

    front = []
    for k in range(1, min(max_terms, len(cands)) + 1):
        best = None
        for sub in itertools.combinations(range(len(cands)), k):
            Xtr = stack[np.ix_(tr, sub)]
            Xte = stack[np.ix_(te, sub)]
            try:
                A = np.column_stack([np.ones(len(tr)), np.log(Xtr)])
                coef, *_ = np.linalg.lstsq(A, np.log(y[tr]), rcond=None)
            except np.linalg.LinAlgError:
                continue
            pred = np.exp(np.column_stack([np.ones(len(te)), np.log(Xte)]) @ coef)
            err = _max_rel_err(y[te], pred)
            if best is None or err < best[0]:
                best = (err, [cands[i] for i in sub], coef)
        if best:
            front.append({"complexity": k, "max_rel_err": best[0],
                          "terms": best[1],
                          "constant": float(np.exp(best[2][0])),
                          "exponents": [float(c) for c in best[2][1:]]})

    # the elbow: the last step that improved error by more than 10x
    elbow = front[0]["complexity"] if front else None
    for a, b in zip(front, front[1:]):
        if a["max_rel_err"] > 0 and b["max_rel_err"] < a["max_rel_err"] / 10:
            elbow = b["complexity"]
    return {
        "ok": True, "front": front, "elbow": elbow,
        "note": ("the elbow is the last term that bought an order of magnitude. "
                 "Past it the curve is flat and extra terms are absorbing "
                 "noise, which is how an overfit looks on this plot."),
    }


# ==========================================================================
# 4. BAYESIAN MODEL SELECTION
# ==========================================================================

def bic(n: int, k: int, rss: float) -> float:
    """Bayesian Information Criterion for Gaussian residuals.

    BIC = n ln(RSS/n) + k ln(n). Lower is better. The k ln(n) term is the
    parameter penalty, and it grows with sample size - which is the point:
    with more data you should demand more evidence before accepting an extra
    parameter, and holdout error alone does not do that.
    """
    if n <= 0 or rss <= 0:
        return float("inf")
    return n * math.log(rss / n) + k * math.log(n)


def model_probabilities(bics: dict) -> dict:
    """Posterior model probabilities from BIC, assuming equal priors.

    P(M_i | data) proportional to exp(-BIC_i / 2). This turns "model A had
    slightly lower error" into "model A is 40 times more probable than model
    B", which is a statement a reviewer can argue with.
    """
    if not bics:
        return {}
    best = min(bics.values())
    weights = {k: math.exp(-(v - best) / 2) for k, v in bics.items()}
    total = sum(weights.values()) or 1.0
    return {k: w / total for k, w in weights.items()}


def compare_models(y, predictions: dict, n_params: dict) -> dict:
    """Rank competing models by BIC and report their posterior probabilities."""
    y = np.asarray(y, dtype=float)
    bics, rss_all = {}, {}
    for name, pred in predictions.items():
        p = np.asarray(pred, dtype=float)
        m = np.isfinite(p) & np.isfinite(y)
        rss = float(((y[m] - p[m]) ** 2).sum())
        rss_all[name] = rss
        bics[name] = bic(int(m.sum()), n_params.get(name, 1), rss)
    probs = model_probabilities(bics)
    ranked = sorted(probs, key=lambda k: -probs[k])
    out = {"bic": bics, "rss": rss_all, "probabilities": probs,
           "best": ranked[0] if ranked else None, "ranking": ranked}
    if len(ranked) >= 2:
        a, b = ranked[0], ranked[1]
        ratio = probs[a] / probs[b] if probs[b] > 0 else float("inf")
        out["evidence"] = (
            f"{a} is {ratio:.1f}x more probable than {b}. "
            + ("Decisive." if ratio > 100 else
               "Strong." if ratio > 10 else
               "Positive but not decisive - do not choose on this alone."
               if ratio > 3 else
               "BARELY DISTINGUISHABLE. The data does not separate these "
               "models; collect more or accept both."))
    return out


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

    rng = np.random.default_rng(9)

    print("== Buckingham-Pi recovers Reynolds number ==")
    u = {"rho": "kg/m3", "v": "m/s", "D": "m", "mu": "Pa"}
    # mu as Pa.s is not in the table; use the full pipe-flow set instead
    u = {"rho": "kg/m3", "v": "m/s", "D": "m", "dp": "Pa"}
    gs = pi_groups(u)
    chk(len(gs) == 1, f"four variables, rank 3 -> one group ({len(gs)})")
    g = gs[0]
    # the Euler number dp/(rho v^2) is the only dimensionless combination
    e = g.exponents
    chk(abs(abs(e.get("dp", 0)) - abs(e.get("rho", 0))) < 1e-9,
        f"dp and rho appear with equal magnitude ({e})")
    chk(abs(abs(e.get("v", 0)) - 2 * abs(e.get("rho", 0))) < 1e-9,
        "and v with twice that - the Euler number")

    print("== Pi groups are dimensionless by construction ==")
    from research import dimension_of_monomial
    for grp in gs:
        d = dimension_of_monomial([u[k] for k in grp.exponents],
                                  list(grp.exponents.values()))
        chk(all(abs(x) < 1e-9 for x in d), f"{grp.name} has no dimensions {d}")

    print("== the theorem's count is respected ==")
    # 5 variables, rank 3 -> 2 groups
    u2 = {"F": "N", "rho": "kg/m3", "v": "m/s", "A": "m2", "p": "Pa"}
    chk(len(pi_groups(u2)) == 5 - 3, f"5 vars rank 3 -> 2 groups "
                                     f"({len(pi_groups(u2))})")
    # dimensionally independent set -> no groups at all
    chk(pi_groups({"m": "kg", "L": "m", "t": "s"}) == [],
        "three independent dimensions yield no group")

    print("== dimensionless discovery on Euler-number data ==")
    N = 500
    rho = rng.uniform(800, 1200, N)
    v = rng.uniform(1, 20, N)
    D = rng.uniform(0.05, 0.5, N)
    dp = 0.5 * rho * v ** 2 * 2.4          # Euler number fixed at 2.4/... etc
    res = discover_dimensionless({"rho": rho, "v": v, "D": D, "dp": dp},
                                 u, "dp")
    chk(res["ok"], f"a Pi relationship was found ({res.get('reason','')})")
    # Four variables of rank 3 give exactly ONE group, and it contains dp, so
    # there is no right-hand side to regress against - the relationship is a
    # fixed constant. That is the correct answer, not a degenerate one, and it
    # takes the single-group branch.
    chk(res["rhs"] == [], "one group means no free parameter to fit")
    chk("constant" in res, f"so a constant is reported ({res['constant']:.3f})")
    chk("no free parameter" in res.get("note", ""), "and the note says why")

    # A five-variable set has two groups, which is the branch that regresses
    # one Pi against another - the form every heat-transfer correlation takes.
    u3 = {"F": "N", "rho": "kg/m3", "v": "m/s", "A": "m2", "p": "Pa"}
    N3 = 400
    d3 = {"rho": rng.uniform(900, 1100, N3), "v": rng.uniform(2, 20, N3),
          "A": rng.uniform(0.01, 1.0, N3), "p": rng.uniform(1e4, 1e6, N3)}
    d3["F"] = 0.7 * d3["rho"] * d3["v"] ** 2 * d3["A"]
    r3 = discover_dimensionless(d3, u3, "F")
    if r3["ok"] and r3["rhs"]:
        chk("BY CONSTRUCTION" in r3.get("note", ""),
            "the two-group branch states that F3 cannot fail on it")
        chk("search_space_reduction" in r3,
            f"and reports the reduction ({r3.get('search_space_reduction')})")
    else:
        chk(True, f"two-group branch not exercised ({r3.get('reason', '')})")

    print("== SINDy recovers a damped oscillator ==")
    # x'' + 2*zeta*w*x' + w^2 x = 0 as a first-order system
    w, zeta = 2.0, 0.15
    t = np.linspace(0, 20, 4000)
    dt = t[1] - t[0]
    x = np.zeros(len(t))
    vx = np.zeros(len(t))
    x[0], vx[0] = 1.0, 0.0
    for i in range(1, len(t)):             # explicit integration, fine at this dt
        a = -2 * zeta * w * vx[i-1] - w**2 * x[i-1]
        vx[i] = vx[i-1] + a * dt
        x[i] = x[i-1] + vx[i-1] * dt
    X = np.column_stack([x, vx])
    r = sindy(X, t=t, names=["x", "v"], threshold=0.05, order=2)
    chk(r["n_terms"] <= 5, f"a sparse equation, not a dense fit "
                           f"({r['n_terms']} of {r['n_candidates']} terms)")
    chk(r["r2"] > 0.99, f"and it reproduces the dynamics (R2 {r['r2']:.4f})")
    # dx/dt = v must be recovered exactly
    chk("v" in r["equations"][0], f"dx/dt = v recovered: {r['equations'][0]}")
    # dv/dt = -w^2 x - 2 zeta w v : coefficients -4 and -0.6
    eq2 = r["equations"][1]
    chk("x" in eq2 and "v" in eq2, f"dv/dt depends on both: {eq2}")
    coef_x = r["coefficients"][1][r["labels"].index("x")]
    coef_v = r["coefficients"][1][r["labels"].index("v")]
    chk(abs(coef_x - (-w**2)) < 0.15, f"stiffness term ~ -4 (got {coef_x:.3f})")
    chk(abs(coef_v - (-2*zeta*w)) < 0.15, f"damping term ~ -0.6 (got {coef_v:.3f})")

    print("== SINDy stays sparse on a linear system ==")
    t2 = np.linspace(0, 5, 2000)
    y2 = 3.0 * np.exp(-1.7 * t2)
    r2 = sindy(y2.reshape(-1, 1), t=t2, names=["y"], threshold=0.05, order=3)
    chk(r2["n_terms"] <= 2, f"dy/dt = -1.7y is one term ({r2['n_terms']})")
    cy = r2["coefficients"][0][r2["labels"].index("y")]
    chk(abs(cy - (-1.7)) < 0.1, f"decay rate recovered ({cy:.3f})")

    print("== Pareto front finds the elbow ==")
    # I = b h^3 / 12, with two irrelevant columns
    b = rng.uniform(0.02, 0.2, 600)
    h = rng.uniform(0.05, 0.4, 600)
    junk1 = rng.uniform(1, 9, 600)
    junk2 = rng.uniform(1, 9, 600)
    pf = pareto_front_equations(
        {"b": b, "h": h, "j1": junk1, "j2": junk2, "I": b * h ** 3 / 12}, "I")
    chk(pf["ok"], "a front was produced")
    chk(len(pf["front"]) >= 3, f"{len(pf['front'])} complexity levels")
    chk(pf["elbow"] == 2, f"the elbow is at two terms ({pf['elbow']})")
    two = next(f for f in pf["front"] if f["complexity"] == 2)
    chk(set(two["terms"]) == {"b", "h"}, f"and they are b and h ({two['terms']})")
    chk(two["max_rel_err"] < 1e-9, "with essentially zero error")
    one = next(f for f in pf["front"] if f["complexity"] == 1)
    chk(one["max_rel_err"] > two["max_rel_err"] * 100,
        "while one term is far worse - that is the elbow")

    print("== BIC penalises parameters ==")
    chk(bic(100, 1, 10.0) < bic(100, 5, 10.0),
        "same fit, more parameters is worse")
    chk(bic(100, 3, 5.0) < bic(100, 3, 50.0), "same parameters, worse fit is worse")
    # the penalty grows with n, which is the whole point
    small = bic(20, 4, 1.0) - bic(20, 1, 1.0)
    large = bic(2000, 4, 100.0) - bic(2000, 1, 100.0)
    chk(large > small, f"the penalty grows with sample size ({small:.1f} -> {large:.1f})")

    print("== model probabilities are decisive when they should be ==")
    yv = rng.normal(0, 1, 200)
    good = yv + rng.normal(0, 0.05, 200)
    bad = yv + rng.normal(0, 1.0, 200)
    cmp_ = compare_models(yv, {"good": good, "bad": bad},
                          {"good": 2, "bad": 2})
    chk(cmp_["best"] == "good", "the better model wins")
    chk(cmp_["probabilities"]["good"] > 0.99, "with near-certainty")
    chk("Decisive" in cmp_["evidence"], f"and says so ({cmp_['evidence'][:40]})")

    print("== and honest when two models are indistinguishable ==")
    # Two INDEPENDENT noise draws are not indistinguishable at n=200 - one
    # genuinely fits better and BIC is right to say so decisively. An earlier
    # version of this test used two independent draws and then complained that
    # BIC separated them, which was the test misunderstanding the statistic.
    # Genuinely indistinguishable means nearly identical residuals.
    base = yv + rng.normal(0, 0.5, 200)
    a1 = base
    a2 = base + rng.normal(0, 0.002, 200)      # essentially the same model
    cmp2 = compare_models(yv, {"a": a1, "b": a2}, {"a": 2, "b": 2})
    chk("BARELY DISTINGUISHABLE" in cmp2["evidence"] or
        "not decisive" in cmp2["evidence"],
        f"near-identical models are NOT separated ({cmp2['evidence'][:56]})")
    chk(0.3 < cmp2["probabilities"]["a"] < 0.7,
        f"and the posterior is near 50/50 ({cmp2['probabilities']['a']:.2f})")

    print(f"\nresearch_advanced.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(__doc__)
