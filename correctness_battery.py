#!/usr/bin/env python3
"""correctness_battery.py - are the explanations RIGHT, not merely present?

definitions_battery.py measured 137 of 137 answered. That is an availability
number and it says nothing about truth. A spot check of four found Hall-Petch
returned as an "inverse linear dependence" when the relationship is d^(-1/2) -
right concept, wrong exponent, and fluent enough that nothing about the
sentence looks wrong.

One in four is an anecdote. This is the measurement.

HOW A FACT IS MADE CHECKABLE. Each question is paired with what a correct
answer MUST contain and what it must NOT. Both halves are needed: an
explanation of Stefan-Boltzmann that says "fourth power" is right, and one that
says "proportional to temperature" is wrong even though it also contains the
word temperature. Requiring the right phrase catches omission; forbidding the
wrong one catches the confident error, which is the dangerous kind.

WHAT THE PATTERNS ARE. Exponents, constants and named limits - the parts that
are unambiguously true or false. Not style, not completeness, not whether the
explanation is well written. A model can be verbose and correct or terse and
correct; it cannot be correct and say Hall-Petch is linear.

WHY THIS IS THE RIGHT SHAPE OF TEST. It is an oracle test on the one part of
an explanation that has a definite answer. It cannot measure whether the
surrounding prose is sound, and it does not try to - a battery that claimed to
grade explanation quality would be measuring my taste.
"""
from __future__ import annotations

import re
import sys
from collections import Counter

import chat

# (question, must_match, must_not_match, what the right answer is)
# Patterns are lowercase regex. must_not is checked first: a confident wrong
# statement fails even if the right phrase also appears somewhere.
CHECKS = [
    # --- exponents, the class the model actually gets wrong
    ("what is the Hall-Petch relationship",
     r"(square root|d\^?\s*\(?-\s*1\s*/\s*2|-0\.5|inverse.{0,15}square|\bd\^-½)",
     r"(inverse linear|linearly with grain size|proportional to 1/d\b)",
     "sigma_y = sigma_0 + k d^(-1/2), inverse SQUARE ROOT of grain size"),
    ("what is the Stefan-Boltzmann law",
     r"(fourth power|t\^?\s*4|\bt4\b|quartic)",
     r"(proportional to temperature\b(?!.*fourth)|linear in temperature)",
     "q = eps sigma A T^4, the FOURTH power of absolute temperature"),
    ("what is the Hagen-Poiseuille equation",
     r"(fourth power|r\^?\s*4|radius.{0,30}fourth|\br4\b|d\^?\s*4)",
     r"(square of the radius|r\^2\b)",
     "Q = pi dp r^4 / (8 mu L), the FOURTH power of radius"),
    ("what is Kepler's third law",
     r"(3\s*/\s*2|1\.5|cube.{0,25}square|square.{0,25}cube|a\^?3|t\^?2)",
     r"(directly proportional to the radius\b)",
     "T^2 proportional to a^3"),
    ("how does pump power vary with speed",
     r"(cube|third power|\^3\b|n\^3)",
     r"(linearly with speed|proportional to speed\b(?!.*cub))",
     "power goes as the CUBE of speed - the affinity laws"),
    ("how does wind turbine power vary with wind speed",
     r"(cube|third power|v\^?\s*3|\^3\b)",
     r"(square of the wind speed|v\^2\b)",
     "P = 0.5 Cp rho A v^3, the CUBE of wind speed"),
    ("what is Stokes settling velocity",
     r"(square.{0,25}diameter|d\^?\s*2|diameter squared|\bd2\b)",
     r"(linearly with diameter|proportional to diameter\b(?!.*squar))",
     "v proportional to d^2"),
    ("what is Euler buckling load",
     r"(1\s*/\s*l\^?\s*2|inverse.{0,20}square|l\^?\s*2|length squared)",
     r"(inversely proportional to the length\b(?!.*squar))",
     "P_cr = pi^2 EI/(KL)^2, inverse SQUARE of length"),
    ("how does beam deflection depend on span",
     r"(cube|third power|l\^?\s*3|\^3\b|fourth power|l\^?\s*4)",
     r"(linearly with span|proportional to length\b(?!.*cub))",
     "L^3 for a point load, L^4 for a UDL"),
    ("what is the Joule heating law",
     r"(square.{0,20}current|i\^?\s*2|current squared|\bi2r\b)",
     r"(proportional to the current\b(?!.*squar))",
     "P = I^2 R, the SQUARE of current"),

    # --- constants and definite numbers
    ("what is the Betz limit",
     r"(0\.593|59\.3|16\s*/\s*27|59 ?%)",
     r"(100 ?%|no theoretical limit)",
     "16/27 = 0.593, the maximum fraction of wind power extractable"),
    ("what is the Reynolds number for transition in a pipe",
     r"(2300|2000|2100|4000)",
     r"^(?!.*\d)",
     "roughly 2300 for pipe flow"),
    ("what is the Nusselt number for fully developed laminar pipe flow "
     "with constant wall temperature",
     r"(3\.66)", r"", "3.66, a constant with no Reynolds dependence"),
    ("what is absolute zero in Celsius",
     r"(-\s*273\.15|-\s*273)", r"(0 ?°?c\b|zero degrees celsius)",
     "-273.15 C"),
    ("what is the acceleration due to gravity at sea level",
     r"(9\.8|9\.81|9\.807)", r"", "9.80665 m/s^2"),
    ("what is the Strouhal number for vortex shedding from a cylinder",
     r"(0\.2\b|0\.21|0\.19)", r"", "about 0.21, nearly constant over "
     "300 < Re < 2e5"),

    # --- direction of an effect, where a sign error is the failure
    ("does deflection increase or decrease with Young's modulus",
     r"(decreas|inversely|reduc|less|lower|stiffer)",
     r"(increas.{0,30}(with|as).{0,20}(young|modulus|stiff))",
     "deflection DECREASES as stiffness rises"),
    ("does NOx increase or decrease with flame temperature",
     r"(increas|rise|higher|grow)",
     r"(decreas.{0,25}(with|as).{0,20}(flame )?temperature)",
     "NOx rises steeply with flame temperature - Zeldovich"),
    ("what happens to critical heat flux if you exceed it",
     r"(film boiling|burnout|temperature.{0,25}(excursion|spike|rise)|"
     r"dryout|damage|failure)",
     r"(nothing|improves|more efficient)",
     "transition to film boiling and a sudden temperature excursion"),
    ("does adding insulation always reduce heat loss from a small pipe",
     r"(no\b|not always|critical radius|can increase|may increase)",
     r"^(yes\b|always)",
     "no - below the critical radius k/h it INCREASES loss"),
    ("what happens to condensation heat transfer coefficient as the "
     "temperature difference increases",
     r"(decreas|falls|reduc|lower|thicker film)",
     r"(increas.{0,30}with.{0,20}temperature difference)",
     "it FALLS - a larger dT thickens the condensate film"),

    # --- named limits and validity, the part people drop
    ("when is the Blasius friction factor correlation valid",
     r"(3000|4000|10\^?5|100000|1e5|turbulent).{0,80}(smooth|re)|"
     r"(smooth).{0,60}(3000|100000|1e5)",
     r"", "smooth pipe, roughly 3000 < Re < 100000"),
    ("when can you use the lumped capacitance method",
     r"(biot|bi\b).{0,30}(0\.1|<)|0\.1", r"",
     "Biot number below 0.1"),
    ("when is Stokes drag valid",
     r"(re\b|reynolds).{0,25}(<|less|below).{0,10}1\b|creeping|1\b",
     r"", "Re < 1, creeping flow"),
    ("what are the assumptions behind Bernoulli's equation",
     r"(inviscid|frictionless|no.{0,10}(viscosity|friction)|incompressible|"
     r"steady|streamline)",
     r"", "inviscid, incompressible, steady, along a streamline"),
    ("is the Hazen-Williams formula valid for oil",
     r"(no\b|not\b|only.{0,15}water|water only|designed for water)",
     r"^(yes\b)",
     "no - it is water-specific and has no viscosity term"),

    # --- distinctions that are commonly conflated
    ("what is the difference between accuracy and precision",
     r"(accuracy.{0,80}(true|correct|actual)|"
     r"precision.{0,80}(repeat|scatter|consisten|spread))",
     r"", "accuracy is closeness to truth, precision is repeatability"),
    ("what is the difference between thermodynamic and dynamic stability "
     "of a crystal structure",
     r"(phonon|imaginary|vibrat|dynamic.{0,60}(mode|phonon))",
     r"", "thermodynamic is energy above hull; dynamic requires no "
     "imaginary phonon modes"),
    ("what is the difference between verification and validation",
     r"(verification.{0,90}(right|correctly|equations|solving)|"
     r"validation.{0,90}(right|reality|real world|experiment))",
     r"", "verification is solving the equations right, validation is "
     "solving the right equations"),
    ("what is the difference between stress and strain",
     r"(stress.{0,60}(force|per unit area|pressure))",
     r"", "stress is force per area, strain is deformation per length"),
    ("what is the difference between MTBF and service life",
     r"(mtbf.{0,90}(between failures|repairable|mean time))",
     r"", "MTBF is a rate for repairable items, not a lifetime"),
]


def _check(answer: str, must: str, must_not: str) -> tuple:
    a = " ".join((answer or "").lower().split())
    if must_not:
        m = re.search(must_not, a)
        if m:
            return False, f"contains a wrong statement: '{m.group()[:44]}'"
    if must:
        if not re.search(must, a):
            return False, "the required fact is absent"
    return True, "correct"


def run(quiet: bool = False) -> dict:
    rows = []
    for q, must, must_not, truth in CHECKS:
        try:
            r = chat.respond(q)
            ans = str(r.get("answer") or "")
            basis = r.get("basis")
        except Exception as e:
            ans, basis = "", f"error {type(e).__name__}"
        if not ans:
            rows.append({"q": q, "ok": None, "why": "no answer produced",
                         "basis": basis, "truth": truth})
            continue
        ok, why = _check(ans, must, must_not)
        rows.append({"q": q, "ok": ok, "why": why, "basis": basis,
                     "truth": truth, "answer": ans[:160]})

    answered = [r for r in rows if r["ok"] is not None]
    correct = [r for r in answered if r["ok"]]
    wrong = [r for r in answered if not r["ok"]]
    if not quiet:
        print(f"  {len(rows)} checkable facts, {len(answered)} answered\n")
        for r in wrong:
            print(f"    WRONG  {r['q'][:62]}")
            print(f"           {r['why']}")
            print(f"           truth: {r['truth'][:70]}")
        print(f"\n    correct  {len(correct)}/{len(answered)} "
              f"({len(correct)/max(len(answered),1)*100:.0f}%)")
        print(f"    wrong    {len(wrong)}")
        print(f"    silent   {len(rows)-len(answered)}")
    return {"n": len(rows), "answered": len(answered),
            "correct": len(correct), "wrong": len(wrong),
            "accuracy": len(correct) / max(len(answered), 1),
            "wrong_items": wrong, "rows": rows}


def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    print("== the checker itself is right ==")
    good, why = _check("It varies with the fourth power of temperature, T^4.",
                       r"(fourth power|t\^?\s*4)", r"(linear in temperature)")
    chk(good, f"a correct statement passes ({why})")
    bad, why2 = _check("Radiated power is linear in temperature.",
                       r"(fourth power|t\^?\s*4)", r"(linear in temperature)")
    chk(not bad, "a wrong statement fails")
    chk("wrong statement" in why2, f"and says why ({why2})")
    miss, why3 = _check("Radiation depends on the emissivity and area.",
                        r"(fourth power|t\^?\s*4)", r"")
    chk(not miss, "an omission fails too")
    chk("absent" in why3, f"with a different reason ({why3})")
    # must_not is checked FIRST - a confident error is not excused by also
    # containing the right phrase somewhere
    both, _ = _check("It is linear in temperature, some say fourth power.",
                     r"(fourth power)", r"(linear in temperature)")
    chk(not both, "a wrong claim is not excused by a later correct one")

    print("== the battery is substantial ==")
    chk(len(CHECKS) >= 28, f"{len(CHECKS)} checkable facts")
    chk(all(len(c) == 4 for c in CHECKS), "each has question, must, must_not, truth")
    chk(all(c[3] for c in CHECKS), "each states what the right answer is")
    kinds = sum(1 for c in CHECKS if c[2])
    chk(kinds >= 15, f"{kinds} carry a forbidden pattern, not just a required one")

    print("== the Hall-Petch case is covered ==")
    hp = next(c for c in CHECKS if "Hall-Petch" in c[0])
    ok_hp, _ = _check("sigma_y = sigma_0 + k d^(-1/2), an inverse square root "
                      "of grain size", hp[1], hp[2])
    chk(ok_hp, "the correct Hall-Petch form passes")
    bad_hp, _ = _check("It describes the inverse linear dependence of yield "
                       "strength on grain size.", hp[1], hp[2])
    chk(not bad_hp, "the exact wrong answer the model gave is CAUGHT")

    print("== measuring the live model ==")
    res = run(quiet=True)
    chk(res["n"] == len(CHECKS), "every check ran")
    print(f"\n  MEASURED: {res['correct']}/{res['answered']} correct "
          f"({res['accuracy']*100:.0f}%), {res['wrong']} wrong, "
          f"{res['n']-res['answered']} silent")
    for w in res["wrong_items"][:6]:
        print(f"    WRONG: {w['q'][:58]}")
        print(f"           {w['why']}")
    # No pass threshold is asserted. The number is the deliverable, and an
    # assertion here would only tempt me to move it.
    print(f"\ncorrectness_battery.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    r = run()
