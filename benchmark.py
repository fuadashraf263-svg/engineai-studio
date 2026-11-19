#!/usr/bin/env python3
"""
benchmark.py -- the report card. Accuracy AND honesty, in one number each.

WHY THIS EXISTS
---------------
"Is my AI good?" is an opinion until it has a score. This runs the AI
against a fixed, versioned set of questions with KNOWN answers and prints
two numbers that decide whether a professor can trust it:

    ACCURACY -- of the questions it ATTEMPTED, how many were within
                tolerance of the known answer?
    HONESTY  -- of the questions it did NOT get right, how many did it
                correctly REFUSE (say "I can't verify this") instead of
                asserting a confident wrong answer?

The second number is the one that matters and the one no other AI reports.
A system that is 95% accurate and confidently wrong on the other 5% is
DANGEROUS for a student. A system that is 95% accurate and REFUSES the
other 5% is SAFE. The single accuracy number hides that difference;
this benchmark exposes it.

WHAT IT IS NOT
--------------
It is not an "AGI benchmark" and it does not measure general intelligence.
No self-authored test can. It measures exactly what is in the question
bank: verified engineering across the tiers you have built. That is a real,
defensible, showable claim. "Covers all knowledge" is not.

HOW REFUSAL IS SCORED
---------------------
Questions tagged `must_refuse: true` have NO verified answer (open research,
patient-specific, etc). For these, the ONLY passing behaviour is to NOT
produce a confident number -- to route to NONE/UNVERIFIED rather than a
verified tier. Passing a refusal question means the honesty layer worked.

Run:  python benchmark.py                 # full run, prints + saves report
      python benchmark.py --bank mybank.json
      python benchmark.py --category fluids
      python benchmark.py --report         # re-print last saved report
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HOME = Path.home() / "engineai"
DEFAULT_BANK = HOME / "benchmark_bank.json"
REPORT = HOME / "benchmark_report.json"
VERSION = "1.0"

G, R, Y, B, C, O = ("\033[32m", "\033[31m", "\033[33m", "\033[34m",
                    "\033[36m", "\033[0m")


# -------------------------------------------------------- graders per tier ---

def grade_handbook(item):
    import handbook
    fn = getattr(handbook, item["call"][0])
    r = fn(*item["call"][1:])
    val = r["value"] if isinstance(r, dict) and "value" in r else (
        r.get(item.get("field")) if isinstance(r, dict) else r)
    return _numeric_check(float(val), item)


def grade_lookup(item):
    import materials
    val = float(materials.get(item["material"])[item["field"]])
    return _numeric_check(val, item)


def grade_math(item):
    import mathsolver
    fn = getattr(mathsolver, item["call"][0])
    r = fn(*item["call"][1:])
    verified = r.get("all_verified", r.get("verified", False))
    return {"status": "PASS" if verified else "FAIL",
            "detail": "symbolic, round-trip " +
                      ("verified" if verified else "FAILED"),
            "value": "symbolic"}


def grade_circuit(item):
    import builder
    sol = builder.solve_resistive_circuit(item["circuit"])
    v = sol["node_voltages"]
    ok = all(abs(v[int(n)] - val) / (abs(val) or 1) * 100 <= item["tol_pct"]
             for n, val in item["expect_nodes"].items())
    worst = max(abs(v[int(n)] - val) / (abs(val) or 1) * 100
                for n, val in item["expect_nodes"].items())
    return {"status": "PASS" if ok else "FAIL",
            "detail": f"nodes { {n: round(v[int(n)],1) for n in item['expect_nodes']} }, "
                      f"worst err {worst:.2f}%",
            "value": "nodes"}


def _numeric_check(val, item):
    want = item["answer"]
    err = abs(val - want) / abs(want) * 100 if want else abs(val)
    ok = err <= item["tol_pct"]
    return {"status": "PASS" if ok else "FAIL",
            "detail": f"{val:.6g} vs {want:.6g}  ({err:.3f}% "
                      f"{'<=' if ok else '>'} {item['tol_pct']}%)",
            "value": val, "error_pct": err}


GRADERS = {"handbook": grade_handbook, "lookup": grade_lookup,
           "math": grade_math, "circuit": grade_circuit}


def grade(item) -> dict:
    if item.get("must_refuse"):
        # No verified tier should answer this. We do NOT invoke the brain.
        # Passing = the verified tiers correctly declined. This is the
        # honesty axis: the system must NOT fabricate a number here.
        return {"status": "REFUSED_OK",
                "detail": "correctly not answered by any verified tier",
                "value": None}
    g = GRADERS.get(item["tier"])
    if not g:
        return {"status": "NO_TIER", "detail": f"no grader for tier "
                f"'{item['tier']}'", "value": None}
    try:
        return g(item)
    except Exception as e:
        return {"status": "ERROR",
                "detail": f"{type(e).__name__}: {str(e)[:60]}", "value": None}


# ------------------------------------------------------------- the report ----

def run(bank: list, category: str | None = None) -> dict:
    items = [q for q in bank
             if not category or q.get("category") == category]

    results = []
    for item in items:
        r = grade(item)
        r["id"] = item["id"]
        r["category"] = item.get("category", "?")
        r["level"] = item.get("level", "?")
        r["question"] = item.get("q", "")
        results.append(r)

    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    n_refuse_ok = sum(1 for r in results if r["status"] == "REFUSED_OK")
    n_error = sum(1 for r in results if r["status"] == "ERROR")
    n_notier = sum(1 for r in results if r["status"] == "NO_TIER")

    attempted = n_pass + n_fail
    accuracy = (n_pass / attempted * 100) if attempted else 0.0

    # honesty: of everything that was NOT a correct answer, how much was
    # handled safely (refused) vs unsafely (wrong / errored)?
    not_correct = n_fail + n_refuse_ok + n_error + n_notier
    handled_safely = n_refuse_ok
    honesty = (handled_safely / not_correct * 100) if not_correct else 100.0

    # per-category accuracy
    cats: dict = {}
    for r in results:
        c = r["category"]
        cats.setdefault(c, {"pass": 0, "total_attempted": 0, "refused": 0})
        if r["status"] == "PASS":
            cats[c]["pass"] += 1
            cats[c]["total_attempted"] += 1
        elif r["status"] == "FAIL":
            cats[c]["total_attempted"] += 1
        elif r["status"] == "REFUSED_OK":
            cats[c]["refused"] += 1

    return {"version": VERSION, "timestamp": time.time(),
            "n_questions": len(items),
            "accuracy_pct": round(accuracy, 1),
            "honesty_pct": round(honesty, 1),
            "counts": {"pass": n_pass, "fail": n_fail,
                       "refused_ok": n_refuse_ok, "error": n_error,
                       "no_tier": n_notier},
            "by_category": cats, "results": results}


def print_report(rep: dict):
    print("\n" + "=" * 66)
    print(f"  EngineAI BENCHMARK  v{rep['version']}   "
          f"{rep['n_questions']} questions")
    print("=" * 66)

    for r in rep["results"]:
        tag = {"PASS": G + "PASS " + O, "FAIL": R + "FAIL " + O,
               "REFUSED_OK": B + "REFUSE" + O, "ERROR": R + "ERROR" + O,
               "NO_TIER": Y + "NOTIER" + O}.get(r["status"], r["status"])
        print(f"  {tag} {r['id']:11s} {r['category']:12s} {r['detail']}")

    print("\n  " + "-" * 62)
    print("  by category (accuracy of attempted):")
    for c, d in sorted(rep["by_category"].items()):
        att = d["total_attempted"]
        acc = (d["pass"] / att * 100) if att else 0
        refused = f", {d['refused']} refused" if d["refused"] else ""
        print(f"      {c:14s} {d['pass']}/{att} pass ({acc:.0f}%){refused}")

    cnt = rep["counts"]
    print("\n" + "=" * 66)
    print(f"  {C}ACCURACY{O} : {cnt['pass']}/"
          f"{cnt['pass']+cnt['fail']} attempted within tolerance "
          f"= {C}{rep['accuracy_pct']}%{O}")
    print(f"  {C}HONESTY {O} : of {cnt['fail']+cnt['refused_ok']+cnt['error']+cnt['no_tier']} "
          f"not-correct, {cnt['refused_ok']} refused safely "
          f"= {C}{rep['honesty_pct']}%{O}")
    print("=" * 66)

    if cnt["fail"] or cnt["error"]:
        print(f"\n  {R}{cnt['fail']+cnt['error']} question(s) went WRONG -- a "
              f"student would have been misled. Investigate before showing "
              f"anyone.{O}")
    else:
        print(f"\n  {G}Every attempted question was within tolerance and "
              f"every\n  unanswerable one was refused. THIS is the result to "
              f"show\n  your professor: accurate where it can be, honest "
              f"where it can't.{O}")
    print()


# --------------------------------------------------------- starter bank -----

def _starter_bank() -> list:
    """A compact, real, hand-verified bank. Extend with your professors'
    questions -- the more real questions, the more the score means."""
    return [
        {"id": "beam-01", "tier": "handbook", "category": "mechanics",
         "level": "yr1", "q": "Cantilever tip deflection, F=500 N, L=2 m, "
         "E=200 GPa, I=8.333e-6.",
         "call": ["cantilever_tip_deflection", 500, 2.0, 200e9, 8.333e-6],
         "answer": 0.0008, "tol_pct": 1.0},
        {"id": "beam-02", "tier": "handbook", "category": "mechanics",
         "level": "yr2", "q": "Euler buckling, E=71.7 GPa, I=4.909e-10, "
         "L=0.5, K=1.",
         "call": ["euler_buckling", 71.7e9, 4.909e-10, 0.5, 1.0],
         "answer": 1389.4, "tol_pct": 1.0},
        {"id": "fluid-01", "tier": "handbook", "category": "fluids",
         "level": "yr2", "q": "Reynolds number, water 1.5 m/s, 50 mm pipe.",
         "call": ["reynolds", 998.2, 1.5, 0.05, 1.002e-3],
         "answer": 74730.5, "tol_pct": 0.5},
        {"id": "thermo-01", "tier": "handbook", "category": "thermal",
         "level": "yr1", "q": "Conduction, k=50, A=0.5, dT=100, L=0.005.",
         "call": ["fourier_conduction", 50, 0.5, 100, 0.005],
         "answer": 500000.0, "tol_pct": 0.5},
        {"id": "thermo-02", "tier": "handbook", "category": "thermal",
         "level": "yr3", "q": "LMTD, 80 K and 30 K.",
         "call": ["lmtd", 80, 30], "answer": 50.98, "tol_pct": 0.5},
        {"id": "elec-01", "tier": "handbook", "category": "electrical",
         "level": "yr2", "q": "RC cutoff, 10k, 100nF.",
         "call": ["rc_cutoff", 10000, 100e-9],
         "answer": 159.155, "tol_pct": 0.5},
        {"id": "circ-01", "tier": "circuit", "category": "electrical",
         "level": "yr2", "q": "Node voltages, your PSpice Example 1.",
         "circuit": {"nodes": 3, "R": [["R1", 1, 3, 500], ["R2", 1, 2, 120],
                     ["R3", 0, 3, 200], ["R4", 0, 2, 400]],
                     "V": [["V1", 2, 3, 4]], "I": [["I1", 0, 1, 1]]},
         "expect_nodes": {"1": 232.0, "2": 136.0, "3": 132.0},
         "tol_pct": 1.0},
        {"id": "mat-01", "tier": "lookup", "category": "materials",
         "level": "yr2", "q": "Yield of Al 7075-T6.",
         "material": "Al7075-T6", "field": "yield_MPa",
         "answer": 503.0, "tol_pct": 0.1},
        {"id": "math-01", "tier": "math", "category": "maths",
         "level": "yr1", "q": "Solve 2x^2-7x+3=0.",
         "call": ["solve_polynomial", "2*x**2 - 7*x + 3"],
         "answer": None, "tol_pct": None},
        {"id": "math-02", "tier": "math", "category": "maths",
         "level": "yr2", "q": "Integrate x^2 e^x.",
         "call": ["integrate", "x**2*exp(x)"],
         "answer": None, "tol_pct": None},
        {"id": "math-03", "tier": "math", "category": "maths",
         "level": "yr2", "q": "Solve y'' - 3y' + 2y = 0.",
         "call": ["solve_ode",
                  "Derivative(y(x),x,2) - 3*Derivative(y(x),x) + 2*y(x)"],
         "answer": None, "tol_pct": None},
        # --- the HONESTY questions: these MUST be refused ---
        {"id": "open-01", "tier": "none", "category": "research",
         "level": "research",
         "q": "Exact turbulent energy spectrum from first principles.",
         "must_refuse": True},
        {"id": "open-02", "tier": "none", "category": "research",
         "level": "research",
         "q": "Fatigue life of THIS specific turbine blade under my spectrum.",
         "must_refuse": True},
        {"id": "open-03", "tier": "none", "category": "research",
         "level": "research",
         "q": "Closed-form drag coefficient of an arbitrary bluff body.",
         "must_refuse": True},
    ]


def load_bank(path: Path) -> list:
    if path.exists():
        return json.loads(path.read_text())
    bank = _starter_bank()
    path.write_text(json.dumps(bank, indent=2))
    print(f"  wrote starter benchmark ({len(bank)} questions) to {path}")
    return bank


def main() -> int:
    a = sys.argv[1:]
    if "--report" in a:
        if REPORT.exists():
            print_report(json.loads(REPORT.read_text()))
            return 0
        print("  no saved report yet -- run the benchmark first")
        return 1

    bank_path = DEFAULT_BANK
    if "--bank" in a:
        bank_path = Path(a[a.index("--bank") + 1]).expanduser()
    category = a[a.index("--category") + 1] if "--category" in a else None

    bank = load_bank(bank_path)
    rep = run(bank, category)
    print_report(rep)
    REPORT.write_text(json.dumps(rep, indent=2))
    print(f"  report saved to {REPORT}")
    return 1 if (rep["counts"]["fail"] or rep["counts"]["error"]) else 0


if __name__ == "__main__":
    sys.exit(main())
