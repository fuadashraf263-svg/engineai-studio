#!/usr/bin/env python3
"""metamorphic.py - test physics against itself, with no reference answer.

THE ORACLE PROBLEM. To test a solver you normally need to know the right
answer. For a handful of textbook cases you do; for the rest of the input space
you do not, and that untested remainder is where bugs live. Every assertion in
this project so far checks a specific case against a specific known value, so
the coverage is a scatter of points in a space with millions of them.

METAMORPHIC TESTING removes the need for an oracle. You do not need to know
f(x); you need to know how f must CHANGE when x changes. Physics supplies those
relations for free and they hold everywhere in the domain, not at isolated
points:

  HOMOGENEITY   linear elasticity: double the load, exactly double the
                deflection. Any nonlinearity that crept in shows up immediately.
  UNIT SCALING  the same problem in mm and in m must agree after conversion. A
                hard-coded unit conversion cannot survive this.
  MONOTONICITY  stiffer material, less deflection. Always. A sign error here is
                invisible to a single-point check that happens to match.
  SUPERPOSITION linear systems: f(a+b) = f(a) + f(b).
  RECIPROCITY   Maxwell-Betti: deflection at A from a load at B equals
                deflection at B from the same load at A. This one is remarkable
                as a test, because it constrains the solver across two
                DIFFERENT input configurations at once.
  SYMMETRY      swapping symmetric inputs must not change the answer.
  LIMITS        as a parameter goes to a degenerate value, converge to the
                simpler known case.

WHY THIS IS THE RIGHT TECHNIQUE HERE, specifically. The research on defect
detection is consistent that the highest-yield methods are the ones that
explore input space the author did not think about. Metamorphic relations do
that with a correctness criterion attached, so a random input is not merely a
crash test - it is a check that the physics still holds there. Combined with
Hypothesis, which searches for and then SHRINKS counterexamples to a minimal
failing case, a violation arrives as the smallest input that breaks it rather
than as a wall of numbers.

WHAT IT CANNOT DO. A relation that holds for the wrong formula still holds. If
a solver computed PL^3/(3EI) where the truth is PL^3/(48EI), every relation
here passes: it is linear, homogeneous, monotone and reciprocal - and wrong by
16x. Metamorphic testing finds a different CLASS of bug from oracle testing,
and the two are complements, not substitutes. That is why the known-value
assertions stay.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

__all__ = ["Relation", "check_homogeneous", "check_unit_scaling",
           "check_monotone", "check_superposition", "check_symmetry",
           "check_limit", "check_reciprocity", "run_all", "RELATIONS"]


@dataclass
class Relation:
    name: str
    target: str
    held: bool
    n_cases: int
    worst: float
    detail: str
    counterexample: dict = field(default_factory=dict)

    def __str__(self) -> str:
        mark = "holds  " if self.held else "VIOLATED"
        ce = (f"\n        counterexample: {self.counterexample}"
              if self.counterexample else "")
        return (f"    {mark} {self.name:16s} {self.target:28s} "
                f"{self.n_cases:4d} cases, worst {self.worst:.3e}{ce}")


def _rel_err(a: float, b: float) -> float:
    d = max(abs(a), abs(b))
    return abs(a - b) / d if d > 1e-300 else 0.0


# --------------------------------------------------------------------------
# the relations
# --------------------------------------------------------------------------

def check_homogeneous(fn: Callable, base: dict, scale_arg: str,
                      degree: float = 1.0, factors=(0.5, 2.0, 7.3),
                      tol: float = 1e-10, name: str = "") -> Relation:
    """f(k*x) == k^degree * f(x). Linear response to a scaled input."""
    worst, ce = 0.0, {}
    y0 = float(fn(**base))
    n = 0
    for k in factors:
        args = dict(base)
        args[scale_arg] = base[scale_arg] * k
        y = float(fn(**args))
        e = _rel_err(y, y0 * k ** degree)
        n += 1
        if e > worst:
            worst, ce = e, {scale_arg: base[scale_arg], "factor": k,
                            "got": y, "expected": y0 * k ** degree}
    return Relation("homogeneity", name or fn.__name__, worst <= tol, n, worst,
                    f"scaling {scale_arg} by k scales the result by k^{degree:g}",
                    {} if worst <= tol else ce)


def check_unit_scaling(fn: Callable, base: dict, length_args, result_power: int,
                       factor: float = 1000.0, tol: float = 1e-9,
                       name: str = "") -> Relation:
    """Same problem in different length units must agree after conversion.

    Every length input is multiplied by `factor` (m -> mm) and the result is
    expected to change by factor**result_power. A hard-coded conversion buried
    somewhere in the function cannot survive this, and hard-coded conversions
    are among the most common numerical bugs there are.
    """
    y0 = float(fn(**base))
    args = dict(base)
    for a in length_args:
        args[a] = base[a] * factor
    y = float(fn(**args))
    e = _rel_err(y, y0 * factor ** result_power)
    return Relation("unit scaling", name or fn.__name__, e <= tol, 1, e,
                    f"lengths x{factor:g} changes the result by "
                    f"{factor:g}^{result_power}",
                    {} if e <= tol else {"got": y,
                                         "expected": y0 * factor ** result_power})


def check_monotone(fn: Callable, base: dict, arg: str, increasing: bool,
                   values=None, name: str = "") -> Relation:
    """The result must move in one direction as an input increases.

    A sign error is the classic bug this catches, and a single-point check
    against a known value will happily miss it if that point happens to match.
    """
    vals = list(values or [base[arg] * f for f in (0.25, 0.5, 1.0, 2.0, 4.0)])
    out = []
    for v in vals:
        args = dict(base)
        args[arg] = v
        out.append(float(fn(**args)))
    diffs = np.diff(out)
    good = np.all(diffs > -1e-12) if increasing else np.all(diffs < 1e-12)
    worst = float(np.max(-diffs) if increasing else np.max(diffs))
    return Relation("monotonicity", name or fn.__name__, bool(good), len(vals),
                    max(worst, 0.0),
                    f"result {'rises' if increasing else 'falls'} with {arg}",
                    {} if good else {arg: vals, "results": out})


def check_superposition(fn: Callable, base: dict, load_arg: str,
                        a: float, b: float, tol: float = 1e-10,
                        name: str = "") -> Relation:
    """f(a+b) == f(a) + f(b) for a linear system."""
    def at(v):
        args = dict(base)
        args[load_arg] = v
        return float(fn(**args))
    lhs, rhs = at(a + b), at(a) + at(b)
    e = _rel_err(lhs, rhs)
    return Relation("superposition", name or fn.__name__, e <= tol, 1, e,
                    f"f({load_arg}=a+b) equals f(a)+f(b)",
                    {} if e <= tol else {"lhs": lhs, "rhs": rhs})


def check_symmetry(fn: Callable, base: dict, arg_a: str, arg_b: str,
                   tol: float = 1e-10, name: str = "") -> Relation:
    """Swapping two symmetric inputs must not change the answer."""
    y0 = float(fn(**base))
    args = dict(base)
    args[arg_a], args[arg_b] = base[arg_b], base[arg_a]
    y = float(fn(**args))
    e = _rel_err(y, y0)
    return Relation("symmetry", name or fn.__name__, e <= tol, 1, e,
                    f"swapping {arg_a} and {arg_b} leaves the result unchanged",
                    {} if e <= tol else {"before": y0, "after": y})


def check_limit(fn: Callable, base: dict, arg: str, toward: float,
                expected: Callable, tol: float = 1e-6,
                name: str = "") -> Relation:
    """As a parameter approaches a degenerate value, converge to the simpler case."""
    args = dict(base)
    args[arg] = toward
    y = float(fn(**args))
    want = float(expected(**base))
    e = _rel_err(y, want)
    return Relation("limiting case", name or fn.__name__, e <= tol, 1, e,
                    f"{arg}->{toward:g} reduces to the known simpler solution",
                    {} if e <= tol else {"got": y, "expected": want})


def check_reciprocity(fn: Callable, base: dict, pos_a: str, pos_b: str,
                      tol: float = 1e-9, name: str = "") -> Relation:
    """Maxwell-Betti: deflection at A from a load at B equals B from A.

    The most demanding relation here, because it constrains the solver across
    two different input configurations rather than within one.
    """
    args1 = dict(base)
    args1[pos_a], args1[pos_b] = base[pos_a], base[pos_b]
    args2 = dict(base)
    args2[pos_a], args2[pos_b] = base[pos_b], base[pos_a]
    y1, y2 = float(fn(**args1)), float(fn(**args2))
    e = _rel_err(y1, y2)
    return Relation("reciprocity", name or fn.__name__, e <= tol, 2, e,
                    "Maxwell-Betti reciprocal theorem",
                    {} if e <= tol else {"a_from_b": y1, "b_from_a": y2})


# --------------------------------------------------------------------------
# the suite, against EngineAI's real solvers
# --------------------------------------------------------------------------

def _mech():
    import mechanics as M
    out = []

    # fixed-fixed beam, central point load: d = P L^3 / (192 E I)
    ff = lambda P, L, E, I: M.fixed_fixed_point(P, L, E, I)["defl"]
    base = {"P": 5000.0, "L": 3000.0, "E": 210000.0, "I": 8.36e6}
    out.append(check_homogeneous(ff, base, "P", 1.0, name="fixed_fixed_point"))
    out.append(check_homogeneous(ff, base, "L", 3.0, name="fixed_fixed_point"))
    out.append(check_homogeneous(ff, base, "E", -1.0, name="fixed_fixed_point"))
    out.append(check_homogeneous(ff, base, "I", -1.0, name="fixed_fixed_point"))
    out.append(check_monotone(ff, base, "E", increasing=False,
                              name="fixed_fixed_point"))
    out.append(check_monotone(ff, base, "P", increasing=True,
                              name="fixed_fixed_point"))
    out.append(check_superposition(ff, base, "P", 1000.0, 4000.0,
                                   name="fixed_fixed_point"))

    # thin cylinder hoop stress: sigma = p r / t
    tc = lambda p, r, t: M.thin_cylinder(p, r, t)["hoop_MPa"]
    b2 = {"p": 2.0, "r": 500.0, "t": 10.0}
    out.append(check_homogeneous(tc, b2, "p", 1.0, name="thin_cylinder"))
    out.append(check_homogeneous(tc, b2, "r", 1.0, name="thin_cylinder"))
    out.append(check_homogeneous(tc, b2, "t", -1.0, name="thin_cylinder"))
    out.append(check_monotone(tc, b2, "t", increasing=False, name="thin_cylinder"))
    # r and t are both lengths: scaling BOTH must leave stress unchanged
    out.append(check_unit_scaling(tc, b2, ["r", "t"], 0, name="thin_cylinder"))

    # shaft torsion
    st = lambda T, d_outer, L, G: M.shaft_torsion(T, d_outer, L, G)["tau_max_MPa"]
    b3 = {"T": 1e6, "d_outer": 50.0, "L": 1000.0, "G": 80000.0}
    out.append(check_homogeneous(st, b3, "T", 1.0, name="shaft_torsion"))
    out.append(check_homogeneous(st, b3, "d_outer", -3.0, name="shaft_torsion"))
    out.append(check_monotone(st, b3, "d_outer", increasing=False,
                              name="shaft_torsion"))

    # thermal expansion is linear in every argument
    te = lambda alpha, L, dT: M.thermal_expansion(alpha, L, dT)["dL"]
    b4 = {"alpha": 12e-6, "L": 2000.0, "dT": 50.0}
    for a in ("alpha", "L", "dT"):
        out.append(check_homogeneous(te, b4, a, 1.0, name="thermal_expansion"))
    return out


def _offset_beam():
    """simply_point_offset is where reciprocity really bites."""
    import mechanics as M
    L = 6000.0

    def defl(a, b):
        return M.simply_point_offset(10000.0, a, b, 210000.0, 8.36e6)["defl"]

    out = []
    # Maxwell-Betti: a load at a with span split (a,b) gives the same maximum
    # deflection magnitude as the mirrored configuration (b,a)
    out.append(check_reciprocity(lambda a, b: defl(a, b),
                                 {"a": 2000.0, "b": L - 2000.0}, "a", "b",
                                 tol=1e-9, name="simply_point_offset"))
    out.append(check_reciprocity(lambda a, b: defl(a, b),
                                 {"a": 1000.0, "b": L - 1000.0}, "a", "b",
                                 tol=1e-9, name="simply_point_offset"))
    return out


def _circuits():
    import circuits as C
    out = []
    # RC low-pass corner frequency: fc = 1/(2*pi*R*C), symmetric in R and C
    if hasattr(C, "rc_lowpass_fc"):
        fc = lambda R, Cf: float(C.rc_lowpass_fc(R, Cf))
        b = {"R": 1000.0, "Cf": 1e-6}
        out.append(check_symmetry(fc, b, "R", "Cf", tol=1e-9,
                                  name="rc_lowpass_fc"))
        out.append(check_homogeneous(fc, b, "R", -1.0, name="rc_lowpass_fc"))
        out.append(check_monotone(fc, b, "R", increasing=False,
                                  name="rc_lowpass_fc"))
    return out


def _thermal():
    import thermal as T
    out = []
    # Newton cooling: q = h * A * dT, linear in all three
    if hasattr(T, "convection"):
        # signature is (h, area, dT) - not (h, A, dT). The first version of
        # this guessed and the whole thermal block silently did not run, which
        # is why run_all() reports modules it could not exercise instead of
        # quietly reporting a smaller number of relations.
        f = lambda h, area, dT: float(T.convection(h=h, area=area, dT=dT)["q_W"])
        b = {"h": 25.0, "area": 2.0, "dT": 60.0}
        for a in ("h", "area", "dT"):
            out.append(check_homogeneous(f, b, a, 1.0, name="convection"))
        out.append(check_monotone(f, b, "dT", increasing=True, name="convection"))
        out.append(check_monotone(f, b, "h", increasing=True, name="convection"))
    return out


RELATIONS = [("mechanics", _mech), ("offset beam", _offset_beam),
             ("circuits", _circuits), ("thermal", _thermal)]


def run_all(verbose: bool = True) -> dict:
    results, errors = [], {}
    for label, fn in RELATIONS:
        try:
            rs = fn()
        except Exception as e:
            errors[label] = f"{type(e).__name__}: {e}"
            if verbose:
                print(f"  {label}: could not run ({type(e).__name__})")
            continue
        if verbose and rs:
            print(f"  {label}:")
            for r in rs:
                print(r)
        results += rs
    held = sum(1 for r in results if r.held)
    return {"n": len(results), "held": held,
            "violated": [r for r in results if not r.held],
            "errors": errors}


# --------------------------------------------------------------------------
# property-based search with Hypothesis
# --------------------------------------------------------------------------

def hypothesis_sweep(verbose: bool = True) -> dict:
    """Search the input space for a violation, then SHRINK it to a minimal case.

    The value over fixed test points is that Hypothesis looks where nobody
    thought to look, and when it finds something it reduces the input to the
    smallest version that still fails - so a violation arrives as a readable
    counterexample rather than a wall of numbers.
    """
    try:
        from hypothesis import given, settings, strategies as st, HealthCheck
    except ImportError:
        return {"ran": False, "reason": "hypothesis not installed"}

    import mechanics as M
    failures = []

    pos = st.floats(min_value=1e-3, max_value=1e6, allow_nan=False,
                    allow_infinity=False)

    @given(P=pos, L=pos, E=st.floats(1e3, 1e6), I=st.floats(1e3, 1e9),
           k=st.floats(0.1, 10.0))
    @settings(max_examples=300, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    def homogeneous_in_load(P, L, E, I, k):
        r1 = M.fixed_fixed_point(P, L, E, I)
        r2 = M.fixed_fixed_point(P * k, L, E, I)
        d1, d2 = r1["defl"], r2["defl"]
        if not (abs(d2 - k * d1) <= 1e-9 * max(abs(d2), abs(k * d1), 1e-300)):
            raise AssertionError(f"P={P} L={L} E={E} I={I} k={k}: "
                                 f"{d2} != {k}*{d1}")

    @given(p=st.floats(0.01, 100), r=st.floats(1.0, 1e4), t=st.floats(0.1, 500))
    @settings(max_examples=300, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])
    def hoop_scales_with_radius_over_thickness(p, r, t):
        a = M.thin_cylinder(p, r, t)["hoop_MPa"]
        b = M.thin_cylinder(p, r * 3, t * 3)["hoop_MPa"]
        if not (abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1e-300)):
            raise AssertionError(f"p={p} r={r} t={t}: {a} != {b}")

    for fn, nm in ((homogeneous_in_load, "deflection is linear in load"),
                   (hoop_scales_with_radius_over_thickness,
                    "hoop stress depends only on r/t")):
        try:
            fn()
            if verbose:
                print(f"    holds    {nm}  (300 random cases)")
        except AssertionError as e:
            failures.append({"property": nm, "minimal_counterexample": str(e)})
            if verbose:
                print(f"    VIOLATED {nm}\n      {e}")
        except Exception as e:
            failures.append({"property": nm, "error": f"{type(e).__name__}: {e}"})
            if verbose:
                print(f"    error    {nm}: {type(e).__name__}")
    return {"ran": True, "failures": failures}


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

    print("== the relations detect a planted bug ==")
    # a correct linear function, and three broken variants
    good = lambda P, L, E, I: P * L ** 3 / (192 * E * I)
    nonlinear = lambda P, L, E, I: P ** 1.02 * L ** 3 / (192 * E * I)
    # A real sign error: stiffness in the numerator instead of the denominator,
    # so deflection RISES with E. The earlier version of this test flipped sign
    # only at the last sample point, which still produced a monotone decrease -
    # the test was broken, not the checker.
    sign_flip = lambda P, L, E, I: P * L ** 3 * E / (192 * I)
    wrong_power = lambda P, L, E, I: P * L ** 2 / (192 * E * I)
    base = {"P": 5000.0, "L": 3000.0, "E": 210000.0, "I": 8.36e6}

    chk(check_homogeneous(good, base, "P", 1.0).held, "a linear function passes")
    chk(not check_homogeneous(nonlinear, base, "P", 1.0).held,
        "a 2% nonlinearity in the load is CAUGHT")
    chk(check_homogeneous(good, base, "L", 3.0).held, "the L^3 exponent passes")
    chk(not check_homogeneous(wrong_power, base, "L", 3.0).held,
        "L^2 instead of L^3 is CAUGHT")
    chk(check_monotone(good, base, "E", increasing=False).held,
        "deflection falls with stiffness")
    chk(not check_monotone(sign_flip, base, "E", increasing=False).held,
        "a sign flip is CAUGHT by monotonicity")
    chk(check_superposition(good, base, "P", 1000.0, 4000.0).held,
        "superposition holds for a linear function")
    chk(not check_superposition(nonlinear, base, "P", 1000.0, 4000.0).held,
        "and fails for a nonlinear one")

    print("== a violation carries a readable counterexample ==")
    v = check_homogeneous(nonlinear, base, "P", 1.0)
    chk(v.counterexample, "a counterexample is attached")
    chk("got" in v.counterexample and "expected" in v.counterexample,
        f"with both values ({sorted(v.counterexample)})")
    chk(check_homogeneous(good, base, "P", 1.0).counterexample == {},
        "and a passing relation carries none")

    print("== symmetry and limits ==")
    par = lambda r1, r2: r1 * r2 / (r1 + r2)
    chk(check_symmetry(par, {"r1": 100.0, "r2": 220.0}, "r1", "r2").held,
        "parallel resistance is symmetric")
    asym = lambda r1, r2: r1 * r2 / (r1 + 2 * r2)
    chk(not check_symmetry(asym, {"r1": 100.0, "r2": 220.0}, "r1", "r2").held,
        "an asymmetric formula is CAUGHT")
    # hollow shaft with zero bore must reduce to the solid case
    hollow = lambda d_o, d_i: math.pi * (d_o ** 4 - d_i ** 4) / 32
    solid = lambda d_o, d_i: math.pi * d_o ** 4 / 32
    chk(check_limit(hollow, {"d_o": 50.0, "d_i": 10.0}, "d_i", 0.0, solid).held,
        "a hollow section reduces to solid as the bore goes to zero")

    print("== unit scaling catches a hard-coded conversion ==")
    honest = lambda p, r, t: p * r / t
    hardcoded = lambda p, r, t: p * r / t + 0.001 * r      # a length leaks in
    b2 = {"p": 2.0, "r": 500.0, "t": 10.0}
    chk(check_unit_scaling(honest, b2, ["r", "t"], 0).held,
        "a dimensionally honest formula is unit invariant")
    chk(not check_unit_scaling(hardcoded, b2, ["r", "t"], 0).held,
        "a stray absolute length is CAUGHT")

    print("== against EngineAI's real solvers ==")
    res = run_all(verbose=False)
    chk(res["n"] >= 15, f"{res['n']} relations exercised on real code")
    chk(not res["violated"],
        "every relation holds: " + ", ".join(
            f"{r.name}/{r.target}" for r in res["violated"][:3]))
    chk(res["held"] == res["n"], f"{res['held']}/{res['n']} held")
    if res["errors"]:
        print(f"    (modules that could not be exercised: {list(res['errors'])})")

    print("== property-based search over the input space ==")
    hs = hypothesis_sweep(verbose=False)
    if hs.get("ran"):
        chk(not hs["failures"],
            f"no violation found in 600 random cases ({hs['failures'][:1]})")
    else:
        print(f"    (skipped: {hs.get('reason')})")

    print(f"\nmetamorphic.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("metamorphic relations against EngineAI's solvers\n")
    r = run_all()
    print(f"\n  {r['held']}/{r['n']} relations hold")
    print("\nproperty-based search:")
    hypothesis_sweep()
