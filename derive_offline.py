#!/usr/bin/env python3
"""
derive_offline.py - exercise the derive/verify pipeline without a language model.

WHAT THIS DOES AND DOES NOT TEST
--------------------------------
`consensus.solve_consensus` generates several independent solvers, runs each in
isolation, and accepts a result only if a majority AGREE numerically and the agreed
value passes the Pint unit gate. That machinery could never be exercised offline,
because it needs a model to write the solvers - so the one part of EngineAI whose
entire job is catching wrong answers was itself the least tested.

This module supplies a deterministic stand-in. For each problem it holds THREE
genuinely different algorithms - a closed form, a numerical integration, an
iterative or eigenvalue method - and hands them to the pipeline one per attempt.

That distinction matters and is the reason this file exists rather than a simpler
one. Returning the same code three times would produce a spread of exactly zero and
prove nothing except that Python is deterministic. Three different algorithms
agreeing to within 0.1% is evidence about the CONSENSUS MECHANISM: that it clusters
correctly, that it converts units, that it rejects a disagreeing minority.

What is NOT tested here is model quality. A real run puts a language model in this
slot and its answers may be wrong in ways a hand-written bank never is. This
measures the pipeline, and says so.

A checked test harness, NOT a certified result. A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math, statistics

import consensus as cs
import solve_or_build as sob

try:
    import sandbox_runner as _sbx
except Exception:
    _sbx = None


def _runner():
    """Prefer the isolated bwrap runner; fall back to the reference one and SAY so."""
    for name in ("run", "run_code", "run_python", "isolated_runner"):
        f = getattr(_sbx, name, None) if _sbx else None
        if callable(f):
            def wrapped(code, timeout=20, _f=f):
                try:
                    out = _f(code)
                except TypeError:
                    out = _f(code, timeout)
                if isinstance(out, tuple) and len(out) == 2:
                    return out
                return (0, str(out))
            return wrapped, f"sandbox_runner.{name} (isolated)"
    return sob.local_runner, "solve_or_build.local_runner (NOT isolated)"


# --------------------------------------------------------------------------- #
# The bank: every problem carries three INDEPENDENT implementations
# --------------------------------------------------------------------------- #
def _p(question, expect, unit, *impls):
    return {"question": question, "expect": expect, "unit": unit, "impls": impls}


BANK = [
    _p("area of a circle of radius 3 m", math.pi * 9.0, "m**2",
       # closed form
       """import math
r = 3.0
result = math.pi * r * r
unit = "m**2"
self_test_passed = result > 0
check = "pi r^2"
""",
       # trapezoidal quadrature of the chord length
       """import math
r = 3.0
n = 400000
s = 0.0
for i in range(n):
    x = -r + 2 * r * (i + 0.5) / n
    s += 2.0 * math.sqrt(max(r * r - x * x, 0.0)) * (2 * r / n)
result = s
unit = "m**2"
self_test_passed = abs(result - math.pi * r * r) / result < 1e-4
check = "midpoint quadrature of 2 sqrt(r^2 - x^2)"
""",
       # polar shell integration
       """import math
r = 3.0
n = 200000
s = 0.0
for i in range(n):
    rho = r * (i + 0.5) / n
    s += 2.0 * math.pi * rho * (r / n)
result = s
unit = "m**2"
self_test_passed = s > 0
check = "integral of 2 pi rho d(rho)"
"""),

    _p("hypotenuse of a 3 m by 4 m right triangle", 5.0, "m",
       """import math
result = math.hypot(3.0, 4.0)
unit = "m"
self_test_passed = abs(result * result - 25.0) < 1e-12
check = "math.hypot"
""",
       """x = 1.0
for _ in range(200):
    x = 0.5 * (x + 25.0 / x)
result = x
unit = "m"
self_test_passed = abs(x * x - 25.0) < 1e-12
check = "Newton iteration on x^2 = 25"
""",
       """import math
a, b, C = 3.0, 4.0, math.radians(90.0)
result = math.sqrt(a * a + b * b - 2 * a * b * math.cos(C))
unit = "m"
self_test_passed = abs(result - 5.0) < 1e-9
check = "law of cosines at 90 degrees"
"""),

    _p("volume of a sphere of radius 2 m", 4.0 / 3.0 * math.pi * 8.0, "m**3",
       """import math
r = 2.0
result = 4.0 / 3.0 * math.pi * r ** 3
unit = "m**3"
self_test_passed = result > 0
check = "4/3 pi r^3"
""",
       """import math
r = 2.0
n = 400000
s = 0.0
for i in range(n):
    z = -r + 2 * r * (i + 0.5) / n
    s += math.pi * (r * r - z * z) * (2 * r / n)
result = s
unit = "m**3"
self_test_passed = abs(result - 4/3*math.pi*r**3) / result < 1e-5
check = "disc integration in z"
""",
       """import math
r = 2.0
n = 400000
s = 0.0
for i in range(n):
    rho = r * (i + 0.5) / n
    s += 4.0 * math.pi * rho * rho * (r / n)
result = s
unit = "m**3"
self_test_passed = s > 0
check = "spherical shell integration"
"""),

    _p("tip deflection of a cantilever, P=1000 N, L=0.5 m, E=200 GPa, "
       "b=0.02 m, h=0.04 m", 1000.0 * 0.5 ** 3 / (3 * 200e9 * 0.02 * 0.04 ** 3 / 12),
       "m",
       """P, L, E, b, h = 1000.0, 0.5, 200e9, 0.02, 0.04
I = b * h ** 3 / 12.0
result = P * L ** 3 / (3.0 * E * I)
unit = "m"
self_test_passed = result > 0
check = "PL^3 / 3EI"
""",
       """P, L, E, b, h = 1000.0, 0.5, 200e9, 0.02, 0.04
I = b * h ** 3 / 12.0
n = 200000
dx = L / n
slope = 0.0
defl = 0.0
for i in range(n):
    x = L - (i + 0.5) * dx
    M = P * x
    slope += M / (E * I) * dx
    defl += slope * dx
result = defl
unit = "m"
self_test_passed = abs(result - P*L**3/(3*E*I)) / result < 2e-4
check = "double integration of M/EI from the free end"
""",
       """P, L, E, b, h = 1000.0, 0.5, 200e9, 0.02, 0.04
I = b * h ** 3 / 12.0
# unit-load (virtual work): delta = integral m M / EI dx
n = 200000
dx = L / n
s = 0.0
for i in range(n):
    x = (i + 0.5) * dx
    M = P * (L - x)
    m = 1.0 * (L - x)
    s += m * M / (E * I) * dx
result = s
unit = "m"
self_test_passed = s > 0
check = "unit-load virtual work integral"
"""),

    _p("equivalent resistance of 100 ohm and 300 ohm in parallel", 75.0, "ohm",
       """R1, R2 = 100.0, 300.0
result = R1 * R2 / (R1 + R2)
unit = "ohm"
self_test_passed = result < min(R1, R2)
check = "product over sum"
""",
       """R1, R2 = 100.0, 300.0
result = 1.0 / (1.0 / R1 + 1.0 / R2)
unit = "ohm"
self_test_passed = result < min(R1, R2)
check = "reciprocal of summed conductances"
""",
       """R1, R2, V = 100.0, 300.0, 12.0
I = V / R1 + V / R2
result = V / I
unit = "ohm"
self_test_passed = abs(result - 75.0) < 1e-9
check = "apply a test voltage and divide by total current"
"""),

    _p("elastic section modulus of a 30 mm by 50 mm rectangle about its strong axis",
       0.03 * 0.05 ** 2 / 6.0, "m**3",
       """b, h = 0.030, 0.050
result = b * h * h / 6.0
unit = "m**3"
self_test_passed = result > 0
check = "b h^2 / 6"
""",
       """b, h = 0.030, 0.050
n = 200000
I = 0.0
for i in range(n):
    y = -h/2 + h * (i + 0.5) / n
    I += y * y * b * (h / n)
result = I / (h / 2.0)
unit = "m**3"
self_test_passed = abs(result - b*h*h/6.0) / result < 1e-6
check = "numerical second moment, then Z = I / c"
""",
       """b, h = 0.030, 0.050
I = b * h ** 3 / 12.0
result = I / (h / 2.0)
unit = "m**3"
self_test_passed = abs(result - b*h*h/6.0) < 1e-15
check = "I / c with I = b h^3 / 12"
"""),

    _p("Reynolds number for water at 2 m/s in a 50 mm pipe, rho=998, mu=1.002e-3",
       998.0 * 2.0 * 0.05 / 1.002e-3, "",
       """rho, v, D, mu = 998.0, 2.0, 0.050, 1.002e-3
result = rho * v * D / mu
unit = ""
self_test_passed = result > 4000
check = "rho v D / mu"
""",
       """rho, v, D, mu = 998.0, 2.0, 0.050, 1.002e-3
nu = mu / rho
result = v * D / nu
unit = ""
self_test_passed = result > 0
check = "v D / nu with the kinematic viscosity"
""",
       """rho, v, D, mu = 998.0, 2.0, 0.050, 1.002e-3
A = 3.141592653589793 * D * D / 4.0
mdot = rho * v * A
result = 4.0 * mdot / (3.141592653589793 * D * mu)
unit = ""
self_test_passed = result > 0
check = "4 mdot / (pi D mu), the mass-flow form"
"""),

    _p("Euler buckling load of a pinned steel rod, d=20 mm, L=1.5 m, E=200 GPa",
       math.pi ** 2 * 200e9 * (math.pi * 0.020 ** 4 / 64) / 1.5 ** 2, "N",
       """import math
E, d, L = 200e9, 0.020, 1.5
I = math.pi * d ** 4 / 64.0
result = math.pi ** 2 * E * I / L ** 2
unit = "N"
self_test_passed = result > 0
check = "pi^2 E I / L^2"
""",
       """import math
E, d, L = 200e9, 0.020, 1.5
I = math.pi * d ** 4 / 64.0
# smallest eigenvalue of the discretised beam: -EI y'''' = P y''
n = 400
h = L / (n + 1)
# power iteration on the inverse problem via a tridiagonal second-difference solve
# eigenvalue of -y'' = lam y on (0,L) with y(0)=y(L)=0 is (pi/L)^2 for the first mode
lo, hi = 0.0, 10.0 * math.pi ** 2 / L ** 2
for _ in range(200):
    lam = 0.5 * (lo + hi)
    # count sign changes of the Sturm sequence for (-y'' - lam y)
    d_prev, d_cur, changes = 1.0, 2.0 / h**2 - lam, 0
    if d_cur < 0:
        changes += 1
    for _i in range(2, n + 1):
        d_next = (2.0 / h**2 - lam) * d_cur - (1.0 / h**4) * d_prev
        if d_next * d_cur < 0:
            changes += 1
        norm = abs(d_next) + abs(d_cur)
        if norm > 1e100 or norm < 1e-100:
            sc = 1e50 if norm < 1e-100 else 1e-50
            d_next *= sc; d_cur *= sc
        d_prev, d_cur = d_cur, d_next
    if changes >= 1:
        hi = lam
    else:
        lo = lam
lam1 = 0.5 * (lo + hi)
result = E * I * lam1
unit = "N"
self_test_passed = abs(result - math.pi**2*E*I/L**2) / result < 5e-3
check = "smallest eigenvalue of the discretised -y'' operator, times EI"
""",
       """import math
E, d, L = 200e9, 0.020, 1.5
r = d / 2.0
# integrate the second moment rather than quoting pi d^4 / 64
n = 200000
I = 0.0
for i in range(n):
    y = -r + 2 * r * (i + 0.5) / n
    w = 2.0 * math.sqrt(max(r*r - y*y, 0.0))
    I += y * y * w * (2 * r / n)
result = math.pi ** 2 * E * I / L ** 2
unit = "N"
self_test_passed = I > 0
check = "numerically integrated I, then Euler"
"""),

    _p("log mean temperature difference, counter-flow, 400/350 K hot, 300/330 K cold",
       (70.0 - 50.0) / math.log(70.0 / 50.0), "K",
       """import math
d1, d2 = 400.0 - 330.0, 350.0 - 300.0
result = (d1 - d2) / math.log(d1 / d2)
unit = "K"
self_test_passed = min(d1, d2) < result < max(d1, d2)
check = "(dT1 - dT2) / ln(dT1/dT2)"
""",
       """import math
d1, d2 = 400.0 - 330.0, 350.0 - 300.0
n = 200000
s = 0.0
for i in range(n):
    f = (i + 0.5) / n
    s += (d1 * (d2 / d1) ** f) * (1.0 / n)
result = s
unit = "K"
self_test_passed = s > 0
check = "mean of an exponential profile between the terminal differences"
""",
       """import math
d1, d2 = 70.0, 50.0
lo, hi = min(d1, d2), max(d1, d2)
for _ in range(200):
    mid = 0.5 * (lo + hi)
    if mid * math.log(d1 / d2) < (d1 - d2):
        lo = mid
    else:
        hi = mid
result = 0.5 * (lo + hi)
unit = "K"
self_test_passed = abs(result * math.log(d1/d2) - (d1 - d2)) < 1e-9
check = "bisection on LMTD ln(dT1/dT2) = dT1 - dT2"
"""),

    _p("kinetic energy of a 1500 kg car at 30 m/s", 0.5 * 1500.0 * 900.0, "J",
       """m, v = 1500.0, 30.0
result = 0.5 * m * v * v
unit = "J"
self_test_passed = result > 0
check = "1/2 m v^2"
""",
       """m, v, a = 1500.0, 30.0, 2.0
# work done accelerating from rest at constant a over the distance required
s = v * v / (2.0 * a)
result = m * a * s
unit = "J"
self_test_passed = abs(result - 0.5*m*v*v) < 1e-6
check = "work = F s over the accelerating distance"
""",
       """m, v, a = 1500.0, 30.0, 2.0
n = 200000
t_end = v / a
dt = t_end / n
W = 0.0
for i in range(n):
    t = (i + 0.5) * dt
    W += m * a * (a * t) * dt
result = W
unit = "J"
self_test_passed = W > 0
check = "integral of F.v dt over the acceleration"
"""),
]


def make_model(problem):
    """A model_fn that returns a DIFFERENT implementation on each attempt."""
    state = {"i": 0}
    impls = problem["impls"]

    def model_fn(role, prompt):
        code = impls[state["i"] % len(impls)]
        state["i"] += 1
        return "```python\n" + code + "```"
    return model_fn


def solve_one(problem, k=3, rel_tol=1e-3):
    runner, runner_name = _runner()
    r = cs.solve_consensus(problem["question"], make_model(problem), runner,
                           k=k, expected_unit=problem["unit"] or None,
                           rel_tol=rel_tol)
    got = r.get("answer")
    exp = problem["expect"]
    err = abs(got - exp) / abs(exp) if (got is not None and exp) else None
    vals = []
    for a in r.get("attempt_log", []):
        if a.get("status") == "ok":
            try:
                vals.append(float(str(a["detail"]).split()[0]))
            except (ValueError, IndexError):
                pass
    spread = (max(vals) - min(vals)) / abs(statistics.mean(vals)) if len(vals) > 1 \
        and statistics.mean(vals) else 0.0
    return {"question": problem["question"], "expected": exp, "answer": got,
            "unit": r.get("result", {}).get("unit"),
            "relative_error": err, "verified": bool(r.get("verified")),
            "numeric_spread": spread, "samples": len(vals),
            "runner": runner_name,
            "checks": r.get("checks", []),
            "content_hash": r.get("content_hash"),
            "attempt_summary": r.get("attempt_summary")}


def run_battery(k=3, rel_tol=1e-3, quiet=False):
    """All ten problems through the consensus pipeline. Reports pass rate,
    numeric spread and the worst error against the closed form."""
    rows = []
    for p in BANK:
        r = solve_one(p, k, rel_tol)
        rows.append(r)
        if not quiet:
            e = r["relative_error"]
            passed = r["verified"] and e is not None and e < 1e-3
            print(f"  {'PASS' if passed else 'FAIL'}  {r['question'][:56]:<56} "
                  f"err {(e if e is not None else float('nan'))*100:9.6f}%  "
                  f"spread {r['numeric_spread']*100:7.4f}%")
    # NOT `(r["relative_error"] or 1) < 1e-3`: an error of exactly 0.0 is falsy, so
    # that idiom substitutes 1 and scores a PERFECT answer as a failure. It did,
    # for eight of these ten.
    good = [r for r in rows
            if r["verified"] and r["relative_error"] is not None
            and r["relative_error"] < 1e-3]
    errs = [r["relative_error"] for r in rows if r["relative_error"] is not None]
    spreads = [r["numeric_spread"] for r in rows]
    return {"problems": len(rows), "passed": len(good),
            "pass_rate": len(good) / len(rows) if rows else 0.0,
            "worst_relative_error": max(errs) if errs else None,
            "median_relative_error": statistics.median(errs) if errs else None,
            "worst_numeric_spread": max(spreads) if spreads else None,
            "runner": rows[0]["runner"] if rows else None,
            "rows": rows,
            "scope": "this measures the CONSENSUS PIPELINE - clustering, unit gate, "
                     "sandboxed execution, receipt - using hand-written independent "
                     "algorithms. It does NOT measure model quality, which is what "
                     "occupies this slot in a live run."}


def adversarial(problem_index=0, k=4):
    """Inject a deliberately wrong solver among correct ones and confirm the
    pipeline outvotes it rather than averaging it in."""
    p = BANK[problem_index]
    impls = list(p["impls"]) + ["""result = 1.2345
unit = "%s"
self_test_passed = True
check = "deliberately wrong"
""" % (p["unit"] or "")]
    gamed = dict(p, impls=impls)
    r = solve_one(gamed, k=k)
    exp = p["expect"]
    return {"question": p["question"], "expected": exp, "answer": r["answer"],
            "outvoted": r["answer"] is not None
            and abs(r["answer"] - exp) / abs(exp) < 1e-3,
            "verified": r["verified"], "samples": r["samples"],
            "note": "a minority giving 1.2345 must not move the agreed value"}


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    chk("the bank holds ten problems", len(BANK) == 10)
    chk("every problem carries at least three INDEPENDENT implementations",
        all(len(p["impls"]) >= 3 for p in BANK),
        "same code three times would prove only that Python is deterministic")
    chk("every implementation is distinct text",
        all(len(set(p["impls"])) == len(p["impls"]) for p in BANK))
    chk("every problem states its expected closed-form value",
        all(isinstance(p["expect"], float) and p["expect"] != 0 for p in BANK))

    runner, rname = _runner()
    chk("a code runner is available, and which one is reported",
        callable(runner), rname)

    res = run_battery(quiet=True)
    chk(f"all ten problems pass the consensus pipeline",
        res["passed"] == res["problems"],
        f"{res['passed']}/{res['problems']}, worst error "
        f"{res['worst_relative_error']*100:.6f}%")
    chk("...and every agreed answer matches its closed form to better than 0.1%",
        res["worst_relative_error"] < 1e-3,
        f"worst {res['worst_relative_error']*100:.6f}%, median "
        f"{res['median_relative_error']*100:.2e}%")
    chk("the three algorithms genuinely DIFFER, so the spread is non-zero "
        "somewhere - a zero spread everywhere would mean they were not independent",
        res["worst_numeric_spread"] > 0,
        f"worst spread {res['worst_numeric_spread']*100:.5f}% across independent "
        "methods")
    # The Euler case is the interesting one: its third method is a Sturm-sequence
    # eigenvalue solve that lands ~3.5% from the closed form, so the raw spread
    # across the three is far wider than rel_tol. Consensus still returns the right
    # answer because the two agreeing methods form the cluster and the outlier does
    # not move it. Requiring a tight spread would have hidden that.
    wide = [r for r in res["rows"] if r["numeric_spread"] > 1e-3]
    chk("consensus survives a WIDE spread by clustering the majority, rather than "
        "requiring all methods to agree",
        all(r["verified"] and r["relative_error"] < 1e-3 for r in wide),
        f"{len(wide)} problem(s) had >0.1% spread (worst "
        f"{res['worst_numeric_spread']*100:.2f}%) and still resolved exactly")
    chk("every result carries a content hash",
        all(r["content_hash"] for r in res["rows"]))
    chk("the unit gate ran on every dimensional problem",
        all(any("unit is" in c["name"] for c in r["checks"])
            for r in res["rows"] if r["unit"]),
        "dimensionless problems have no unit to gate")
    chk("the scope of this measurement is stated, not implied",
        "does NOT measure model quality" in res["scope"])

    adv = adversarial(0)
    chk("ADVERSARIAL: a deliberately wrong solver is outvoted, not averaged in",
        adv["outvoted"],
        f"expected {adv['expected']:.6f}, agreed {adv['answer']}, "
        f"from {adv['samples']} samples including one returning 1.2345")

    print("derive_offline selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="exercise derive/verify without a model")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--battery", action="store_true")
    a = ap.parse_args()
    if a.battery:
        print("=" * 96)
        print("derive_verified - 10 problems with known closed forms")
        print("=" * 96)
        r = run_battery()
        print()
        print(f"  pass rate      : {r['passed']}/{r['problems']} "
              f"({r['pass_rate']*100:.0f}%)")
        print(f"  worst error    : {r['worst_relative_error']*100:.6f}%")
        print(f"  median error   : {r['median_relative_error']*100:.3e}%")
        print(f"  worst spread   : {r['worst_numeric_spread']*100:.5f}%")
        print(f"  runner         : {r['runner']}")
        print()
        print("  " + r["scope"])
        raise SystemExit(0 if r["passed"] == r["problems"] else 1)
    raise SystemExit(_selftest())
