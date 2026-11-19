#!/usr/bin/env python3
"""
guard.py — verify engineering answers before they reach the user
================================================================

On 2026-08-05 qwen2.5:14b was asked for the cantilever tip deflection formula
and answered:

    delta = P*L**2/(E*I)          <-- WRONG

The correct result, in Roark and in every mechanics textbook, is:

    delta = P*L**3/(3*E*I)

The model then gave a THIRD case (load at distance a from the fixed end) that
reduces to the correct answer when a = L, and did not notice it had
contradicted itself. That is the failure mode that matters: not gibberish,
but a fluent, plausible, confidently wrong formula sitting next to correct
ones.

This module refuses to let that reach a user. Every formula the model produces
is checked against a closed-form reference by evaluating both at random
parameter values. Symbolically rearranged forms pass; wrong ones cannot.

    python guard.py selftest          # proves it catches the real failure
    python guard.py ask "cantilever tip deflection"
    python guard.py check "delta = P*L**2/(E*I)" cantilever_point_tip

    from guard import verify, lookup
    verify(text_from_llm)
"""

from __future__ import annotations

import random
import re
import sys

import sympy as sp

G, R, Y, D, X = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    G = R = Y = D = X = ""

# ---------------------------------------------------------------------------
# The reference library. Every entry is a closed-form solution with a source.
# Symbols are declared positive so sympy never wanders into complex numbers,
# and E / I are explicit Symbols so they are NOT Euler's number and sqrt(-1).
# ---------------------------------------------------------------------------

S = {n: sp.Symbol(n, positive=True) for n in
     "P L E I w a b c d G J A T h k t r R D Q mu rho nu K M N".split()}


def _s(expr: str) -> sp.Expr:
    return sp.sympify(expr, locals=S)


REFERENCE: dict[str, dict] = {
    "cantilever_point_tip": {
        "expr": "P*L**3/(3*E*I)", "units": "m",
        "keywords": ["cantilever", "tip", "free end", "point load", "deflection"],
        "source": "Roark's Formulas for Stress and Strain, Table 8.1 case 1a",
    },
    "cantilever_udl_tip": {
        "expr": "w*L**4/(8*E*I)", "units": "m",
        "keywords": ["cantilever", "uniform", "udl", "distributed", "deflection"],
        "source": "Roark's Table 8.1 case 2a",
    },
    "cantilever_point_at_a": {
        "expr": "P*a**2*(3*L-a)/(6*E*I)", "units": "m",
        "keywords": ["cantilever", "distance", "intermediate", "deflection"],
        "source": "Roark's Table 8.1 case 1b",
    },
    "simple_point_mid": {
        "expr": "P*L**3/(48*E*I)", "units": "m",
        "keywords": ["simply supported", "centre", "center", "point load"],
        "source": "Roark's Table 8.1 case 1e",
    },
    "simple_udl_mid": {
        "expr": "5*w*L**4/(384*E*I)", "units": "m",
        "keywords": ["simply supported", "uniform", "udl", "distributed"],
        "source": "Roark's Table 8.1 case 2e",
    },
    "fixed_fixed_point_mid": {
        "expr": "P*L**3/(192*E*I)", "units": "m",
        "keywords": ["fixed", "clamped", "both ends", "point"],
        "source": "Roark's Table 8.1",
    },
    "euler_buckling_pinned": {
        "expr": "pi**2*E*I/L**2", "units": "N",
        "keywords": ["euler", "buckling", "critical load", "pinned", "column"],
        "source": "Shigley's Mechanical Engineering Design eq 4-43",
    },
    "bending_stress": {
        "expr": "M*c/I", "units": "Pa",
        "keywords": ["bending stress", "flexure", "section modulus"],
        "source": "flexure formula",
    },
    "torsion_shear": {
        "expr": "T*r/J", "units": "Pa",
        "keywords": ["torsion", "shear stress", "shaft", "torque"],
        "source": "torsion formula",
    },
    "torsion_angle": {
        "expr": "T*L/(G*J)", "units": "rad",
        "keywords": ["angle of twist", "torsion", "shaft"],
        "source": "torsion formula",
    },
    "axial_elongation": {
        "expr": "P*L/(A*E)", "units": "m",
        "keywords": ["axial", "elongation", "extension", "bar", "rod"],
        "source": "Hooke's law",
    },
    "hoop_stress_thin": {
        "expr": "P*r/t", "units": "Pa",
        "keywords": ["hoop", "circumferential", "thin wall", "pressure vessel"],
        "source": "thin-wall pressure vessel",
    },
    "lame_hoop_bore": {
        "expr": "P*(b**2+a**2)/(b**2-a**2)", "units": "Pa",
        "keywords": ["lame", "thick wall", "hoop", "bore", "cylinder"],
        "source": "Roark's Table 13.5 case 1b",
    },
    "hagen_poiseuille_Q": {
        "expr": "pi*P*R**4/(8*mu*L)", "units": "m^3/s",
        "keywords": ["poiseuille", "laminar", "flow rate", "pipe"],
        "source": "Hagen-Poiseuille",
    },
    "laminar_friction": {
        "expr": "64/R", "units": "-",
        "keywords": ["laminar", "friction factor", "darcy"],
        "source": "f = 64/Re, fully developed laminar",
    },
    "fin_efficiency": {
        "expr": "tanh(a*L)/(a*L)", "units": "-",
        "keywords": ["fin", "efficiency", "adiabatic tip"],
        "source": "Incropera, adiabatic-tip fin",
    },
    "cantilever_first_frequency": {
        "expr": "1.8751040687**2/(2*pi)*sqrt(E*I/(rho*A*L**4))", "units": "Hz",
        "keywords": ["natural frequency", "cantilever", "vibration", "first mode"],
        "source": "Blevins Table 8-1",
    },
}


# ---------------------------------------------------------------------------

def _numeric_equal(e1: sp.Expr, e2: sp.Expr, trials: int = 40,
                   tol: float = 1e-9) -> bool:
    """Two expressions are equivalent if they agree at random positive points.

    This beats symbolic simplification: it accepts any algebraic rearrangement
    and rejects anything genuinely different, without ever hanging.
    """
    syms = sorted(e1.free_symbols | e2.free_symbols, key=str)
    if not syms:
        try:
            return abs(float(e1) - float(e2)) < tol
        except Exception:
            return False
    rng = random.Random(20260805)
    for _ in range(trials):
        subs = {s: rng.uniform(0.4, 3.5) for s in syms}
        try:
            v1, v2 = complex(e1.subs(subs)), complex(e2.subs(subs))
        except Exception:
            return False
        if abs(v1) > 1e12 or abs(v2) > 1e12:
            continue
        if abs(v1 - v2) > tol * max(1.0, abs(v1), abs(v2)):
            return False
    return True


def _latex_to_sympy(s: str) -> str:
    """Convert the \\frac{}{} the model emits into something sympy parses."""
    s = s.replace("\\left", "").replace("\\right", "").replace("\\,", "")
    for _ in range(6):
        new = re.sub(r"\\d?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"((\1)/(\2))", s)
        if new == s:
            break
        s = new
    s = s.replace("\\cdot", "*").replace("\\times", "*")
    s = re.sub(r"\\(?:delta|sigma|tau|theta|pi)", lambda m: m.group(0)[1:], s)
    s = re.sub(r"\{([^{}]*)\}", r"(\1)", s)
    s = s.replace("^", "**").replace("\\", "")
    return s


def check(candidate: str, key: str) -> dict:
    """Compare one candidate formula against a named reference."""
    if key not in REFERENCE:
        raise KeyError(f"unknown reference '{key}'. Have: {sorted(REFERENCE)}")
    ref = REFERENCE[key]
    raw = candidate.split("=")[-1] if "=" in candidate else candidate
    try:
        cand = _s(_latex_to_sympy(raw.strip()))
    except Exception as e:
        return {"ok": False, "reason": f"could not parse: {e}",
                "correct": ref["expr"], "source": ref["source"]}
    truth = _s(ref["expr"])
    ok = _numeric_equal(cand, truth)
    out = {"ok": ok, "candidate": str(cand), "correct": ref["expr"],
           "source": ref["source"], "reference": key}
    if not ok:
        try:
            out["ratio_wrong_over_right"] = str(sp.simplify(cand / truth))
        except Exception:
            pass
    return out


def lookup(question: str) -> list[dict]:
    """Find reference formulas matching a plain-language question."""
    q = question.lower()
    scored = []
    for key, ref in REFERENCE.items():
        hits = sum(1 for kw in ref["keywords"] if kw in q)
        if hits:
            scored.append((hits, key, ref))
    scored.sort(reverse=True, key=lambda t: t[0])
    return [{"reference": k, "formula": r["expr"], "units": r["units"],
             "source": r["source"], "match_score": h} for h, k, r in scored]


FORMULA_LINE = re.compile(
    r"(?:\\\[|\\\(|\$\$?)?\s*"
    r"(?:[a-zA-Z\\]+)\s*=\s*"
    r"([^\n$\\]*(?:\\frac\{[^{}]*\}\{[^{}]*\}|[A-Za-z0-9_^*/().+\- ]+)[^\n$]*)")


def verify(text: str, expect: str | None = None) -> dict:
    """Scan model output, extract formulas, and check every one it recognises.

    Returns a verdict dict. `errors` is what should block the answer.
    """
    candidates = []
    for chunk in re.split(r"\n\s*\n|\d\.\s+\*\*", text):
        for m in re.finditer(r"=\s*(\\frac\{[^{}]+\}\{[^{}]+\}"
                             r"|[A-Za-z0-9_^*/().+\-\s]{3,60})", chunk):
            expr = m.group(1).strip()
            if expr and any(ch.isalpha() for ch in expr):
                candidates.append((chunk, expr))

    keys = [expect] if expect else list(REFERENCE)
    errors, confirmed, unknown = [], [], []

    for chunk, expr in candidates:
        matched = False
        for key in keys:
            ref = REFERENCE[key]
            ctx = (chunk + " " + text[:200]).lower()
            if expect is None and \
                    sum(1 for kw in ref["keywords"] if kw in ctx) < 2:
                continue
            r = check(expr, key)
            if r.get("candidate") is None:
                continue
            matched = True
            (confirmed if r["ok"] else errors).append(r)
            break
        if not matched:
            unknown.append(expr)

    return {"errors": errors, "confirmed": confirmed,
            "unchecked": unknown[:10],
            "verdict": "BLOCK" if errors else
                       ("PASS" if confirmed else "UNVERIFIED")}


# ---------------------------------------------------------------------------

QWEN_ACTUAL_OUTPUT = r"""
1. **Point Load at the Free End:**
   \[ delta = \frac{PL^2}{EI} \]
2. **Uniformly Distributed Load:**
   \[ delta = \frac{wL^4}{8EI} \]
3. **Point Load at Any Distance a from the Fixed End:**
   \[ delta = \frac{Pa^2}{EI} \left( \frac{3L - a}{6} \right) \]
"""


def selftest():
    print(f"\n{Y}Replaying the actual qwen2.5:14b answer from 2026-08-05{X}")
    print(f"{D}{'-' * 70}{X}")

    cases = [
        ("P*L**2/(E*I)",              "cantilever_point_tip",  "case 1, as given"),
        ("w*L**4/(8*E*I)",            "cantilever_udl_tip",    "case 2, as given"),
        ("P*a**2*(3*L-a)/(6*E*I)",    "cantilever_point_at_a", "case 3, as given"),
        ("P*L**3/(3*E*I)",            "cantilever_point_tip",  "the correct answer"),
        ("(P/(3*E*I))*L**3",          "cantilever_point_tip",  "rearranged, still right"),
        ("P*L*L*L/(E*I*3)",           "cantilever_point_tip",  "written out, still right"),
    ]
    bad = 0
    for expr, key, label in cases:
        r = check(expr, key)
        mark = f"{G}PASS{X}" if r["ok"] else f"{R}BLOCKED{X}"
        print(f"  {mark}  {label}")
        print(f"          {expr}")
        if not r["ok"]:
            bad += 1
            print(f"          {R}correct: {r['correct']}{X}")
            print(f"          {D}{r['source']}{X}")
            if "ratio_wrong_over_right" in r:
                print(f"          {D}wrong by a factor of "
                      f"{r['ratio_wrong_over_right']}{X}")

    print(f"\n{D}{'-' * 70}{X}")
    print(f"  {bad} formula(s) blocked, {len(cases)-bad} passed")
    print(f"  {D}Note that rearranged-but-correct forms pass. Only genuinely"
          f"\n  wrong formulas are blocked.{X}")

    v = verify(QWEN_ACTUAL_OUTPUT, expect="cantilever_point_tip")
    print(f"\n  full-answer verdict: "
          f"{R if v['verdict']=='BLOCK' else G}{v['verdict']}{X}"
          f"  ({len(v['errors'])} error, {len(v['confirmed'])} confirmed)")
    return 0 if bad == 1 else 1      # exactly one case should fail


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "selftest":
        return selftest()
    cmd = sys.argv[1]
    if cmd == "ask":
        hits = lookup(" ".join(sys.argv[2:]))
        if not hits:
            print(f"{Y}no verified formula matches that question{X}")
            return 1
        for h in hits[:5]:
            print(f"\n  {G}{h['formula']}{X}   [{h['units']}]")
            print(f"  {D}{h['reference']} — {h['source']}{X}")
    elif cmd == "check":
        print(check(sys.argv[2], sys.argv[3]))
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
