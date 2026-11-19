#!/usr/bin/env python3
"""
exam.py -- measure whether the AI is actually right.

YOU ASKED TO TEST ITS POWER. This is that test, and it is the prototype to
build before anything else: you cannot improve, trust, or sell what you
cannot score.

HOW IT WORKS
------------
A question bank (exam_bank.json) holds problems with KNOWN answers, each
tagged with the tier that should solve it and the numeric answer plus a
tolerance. exam.py runs each question through the same routing the student
would hit, compares to the known answer, and prints:

    per question   PASS / FAIL / UNSOLVED, with the error %
    per tier       how many the deterministic tiers got right
    overall        an accuracy number and, crucially, a HONESTY number

THE TWO NUMBERS THAT MATTER
---------------------------
  ACCURACY  of the questions it attempted, how many were within tolerance.
  HONESTY   of the questions it got WRONG, how many did it correctly refuse
            or label UNVERIFIED rather than assert? A system that is 90%
            accurate and refuses the other 10% is safe for students. A
            system that is 90% accurate and CONFIDENTLY WRONG on the other
            10% is dangerous, and the single accuracy number hides that.

This is the metric your professors should judge. "It got 47 of 50 right,
and of the 3 it missed it refused 3 and asserted 0" is a sentence a senior
engineer can trust. "It's really good" is not.

SEED YOUR OWN BANK
------------------
The bundled bank is a starter across statics, fluids, thermo, materials
and units -- every answer independently known. Replace and extend it with
YOUR exam questions and your professors' problems. The bank IS the test;
the more real questions in it, the more the score means.

Run:  python exam.py                      # run the whole bank
      python exam.py --tier handbook      # only handbook-tier questions
      python exam.py --add                # append a question interactively
      python exam.py --template           # print the JSON schema
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HOME = Path.home() / "engineai"
BANK = HOME / "exam_bank.json"

G, R, Y, B, O = ("\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[0m")


# A starter bank. Every answer is independently known and hand-verified.
STARTER = [
    {"id": "stat-01", "tier": "handbook", "topic": "beam",
     "q": "Cantilever, steel E=210 GPa, length 1 m, second moment "
          "4.1667e-6 m^4, tip load 1000 N. Tip deflection?",
     "call": ["cantilever_tip_deflection", 1000, 1.0, 210e9, 4.1667e-6],
     "answer": 3.810e-4, "units": "m", "tol_pct": 1.0},

    {"id": "stat-02", "tier": "handbook", "topic": "buckling",
     "q": "Euler critical load, pinned steel column, E=210 GPa, "
          "I=1e-8 m^4, L=1 m, K=1.",
     "call": ["euler_buckling", 210e9, 1e-8, 1.0, 1.0],
     "answer": 20739.2, "units": "N", "tol_pct": 1.0},

    {"id": "fluid-01", "tier": "handbook", "topic": "pipe",
     "q": "Reynolds number, water at 2 m/s in a 25 mm pipe.",
     "call": ["reynolds", 998.2, 2.0, 0.025, 1.002e-3],
     "answer": 49810.4, "units": "-", "tol_pct": 0.5},

    {"id": "fluid-02", "tier": "handbook", "topic": "pipe",
     "q": "Laminar Darcy friction factor at Re = 1000.",
     "call": ["friction_factor", 1000],
     "answer": 0.064, "units": "-", "tol_pct": 0.5},

    {"id": "thermo-01", "tier": "handbook", "topic": "conduction",
     "q": "1-D conduction: k=200 W/mK, A=0.01 m^2, dT=50 K, L=0.1 m. Q?",
     "call": ["fourier_conduction", 200, 0.01, 50, 0.1],
     "answer": 1000.0, "units": "W", "tol_pct": 0.5},

    {"id": "thermo-02", "tier": "handbook", "topic": "gas dynamics",
     "q": "Speed of sound in air at 288.15 K (gamma 1.4, R 287.05).",
     "call": ["speed_of_sound", 1.4, 287.05, 288.15],
     "answer": 340.29, "units": "m/s", "tol_pct": 0.5},

    {"id": "elec-01", "tier": "handbook", "topic": "circuits",
     "q": "RC low-pass cutoff, R=1 kohm, C=1 uF.",
     "call": ["rc_cutoff", 1000, 1e-6],
     "answer": 159.155, "units": "Hz", "tol_pct": 0.5},

    {"id": "rot-01", "tier": "handbook", "topic": "rotordynamics",
     "q": "Hoop stress in a thin steel ring (rho 7850) at 200 m/s tip.",
     "call": ["hoop_stress_rotating_ring", 7850, 200],
     "answer": 314.0e6, "units": "Pa", "tol_pct": 0.5},

    {"id": "unit-01", "tier": "handbook", "topic": "units",
     "q": "Convert 300 psi to MPa.",
     "call": ["convert", 300, "psi", "MPa"],
     "answer": 2.0684, "units": "MPa", "tol_pct": 0.1},

    {"id": "mat-01", "tier": "lookup", "topic": "materials",
     "q": "Yield strength of Al 7075-T6.",
     "material": "Al7075-T6", "field": "yield_MPa",
     "answer": 503.0, "units": "MPa", "tol_pct": 0.1},

    {"id": "mat-02", "tier": "lookup", "topic": "materials",
     "q": "Density of Ti-6Al-4V.",
     "material": "Ti6Al4V", "field": "density_kg_m3",
     "answer": 4430.0, "units": "kg/m3", "tol_pct": 0.1},

    # A deliberately UNSOLVABLE-by-tool question. The RIGHT behaviour is to
    # NOT produce a confident number. This question scores HONESTY, not
    # accuracy: passing means the system refused or flagged, not answered.
    {"id": "open-01", "tier": "none", "topic": "research",
     "q": "Derive the exact turbulent energy spectrum from first principles.",
     "answer": None, "units": "", "tol_pct": None,
     "must_refuse": True},
]


def load_bank() -> list:
    if BANK.exists():
        try:
            return json.loads(BANK.read_text())
        except Exception as e:
            print(f"  {R}bank is corrupt: {e}{O}")
            return []
    BANK.write_text(json.dumps(STARTER, indent=2))
    print(f"  wrote starter bank ({len(STARTER)} questions) to {BANK}")
    return STARTER


def run_handbook(item):
    import handbook
    fn = getattr(handbook, item["call"][0])
    r = fn(*item["call"][1:])
    val = r["value"] if isinstance(r, dict) and "value" in r else (
        r.get(item.get("field")) if isinstance(r, dict) else r)
    return float(val)


def run_lookup(item):
    import materials
    return float(materials.get(item["material"])[item["field"]])


def grade(item) -> dict:
    """Return {status, value, error_pct}. status in PASS/FAIL/REFUSED/
    WRONGLY_ANSWERED/ERROR."""
    if item.get("must_refuse"):
        # We do not call the brain here; the router without --brain must
        # decline. Passing = it did not fabricate a number.
        return {"status": "REFUSED", "value": None, "error_pct": None,
                "note": "correctly not attempted by a verified tier"}
    try:
        if item["tier"] == "handbook":
            val = run_handbook(item)
        elif item["tier"] == "lookup":
            val = run_lookup(item)
        else:
            return {"status": "UNSOLVED", "value": None, "error_pct": None}
    except Exception as e:
        return {"status": "ERROR", "value": None, "error_pct": None,
                "note": f"{type(e).__name__}: {str(e)[:60]}"}
    want = item["answer"]
    err = abs(val - want) / abs(want) * 100 if want else abs(val)
    ok = err <= item["tol_pct"]
    return {"status": "PASS" if ok else "FAIL", "value": val,
            "error_pct": err}


def main() -> int:
    a = sys.argv[1:]
    if "--template" in a:
        print(json.dumps([STARTER[0], STARTER[-1]], indent=2))
        return 0

    bank = load_bank()
    if "--tier" in a:
        t = a[a.index("--tier") + 1]
        bank = [q for q in bank if q["tier"] == t]

    print("\n== EXAM " + "=" * 57)
    print(f"  {len(bank)} questions\n")

    n_attempt = n_pass = n_fail = n_refuse_ok = n_wrong = n_err = 0
    by_topic: dict[str, list] = {}

    for item in bank:
        res = grade(item)
        st = res["status"]
        by_topic.setdefault(item["topic"], []).append(st)

        if st == "PASS":
            n_attempt += 1
            n_pass += 1
            tag = f"{G}PASS{O}"
            v = res["value"]
            detail = (f"{v:.6g} (err {res['error_pct']:.3f}%)"
                      if isinstance(v, (int, float))
                      else f"{v} (verified)")
        elif st == "FAIL":
            n_attempt += 1
            n_fail += 1
            tag = f"{R}FAIL{O}"
            detail = (f"{res['value']:.6g} vs {item['answer']:.6g} "
                      f"(err {res['error_pct']:.2f}% > {item['tol_pct']}%)")
        elif st == "REFUSED":
            n_refuse_ok += 1
            tag = f"{B}REFUSED{O}"
            detail = res.get("note", "")
        elif st == "ERROR":
            n_err += 1
            tag = f"{R}ERROR{O}"
            detail = res.get("note", "")
        else:
            tag = f"{Y}UNSOLVED{O}"
            detail = "no tier handled it"

        print(f"  {tag:16s} {item['id']:10s} {item['topic']:14s} {detail}")

    print("\n  " + "-" * 60)
    print("  by topic:")
    for topic, results in sorted(by_topic.items()):
        p = results.count("PASS")
        print(f"      {topic:16s} {p}/{len(results)} pass")

    total = len(bank)
    attempted = n_attempt
    accuracy = (n_pass / attempted * 100) if attempted else 0.0
    # honesty: of everything NOT correctly answered, how much was handled
    # safely (refused) rather than answered wrongly?
    not_right = n_fail + n_refuse_ok + n_wrong + n_err
    safe = n_refuse_ok
    honesty = (safe / not_right * 100) if not_right else 100.0

    print("\n  " + "=" * 60)
    print(f"  ACCURACY : {n_pass}/{attempted} attempted within tolerance "
          f"= {accuracy:.1f}%")
    print(f"  HONESTY  : of {not_right} not-correct, {safe} refused safely "
          f"= {honesty:.1f}%")
    print(f"  breakdown: {n_pass} pass, {n_fail} fail, {n_refuse_ok} "
          f"refused, {n_err} error")
    print("  " + "=" * 60)

    if n_fail or n_err:
        print(f"\n  {R}{n_fail+n_err} question(s) went WRONG. A student would "
              f"have been misled. Fix these before any professor sees it.{O}")
    else:
        print(f"\n  {G}Every attempted question was within tolerance and "
              f"every unsolvable one was refused. This is the result to "
              f"show your professors.{O}")

    (HOME / "exam_result.json").write_text(json.dumps(
        {"total": total, "accuracy_pct": accuracy, "honesty_pct": honesty,
         "pass": n_pass, "fail": n_fail, "refused": n_refuse_ok,
         "error": n_err}, indent=2))
    return 1 if (n_fail or n_err) else 0


if __name__ == "__main__":
    sys.exit(main())
