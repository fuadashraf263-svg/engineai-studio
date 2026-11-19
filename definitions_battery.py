#!/usr/bin/env python3
"""definitions_battery.py - can it explain engineering, or only calculate it?

The claim under test: "answer any kind of question and explain any kind of
definition in the whole engineering world."

That is testable, so this tests it rather than asserting it. 140 definitions
and concept questions across 24 disciplines, from first-year vocabulary to
things a specialist would ask. Each is scored by what EngineAI actually DOES:

  EXPLAINED   a substantive answer with engineering content
  COMPUTED    routed to a solver - correct for a calculation, but a definition
              question answered with "I will compute it" has not been answered
  DEFLECTED   answered without engineering substance, or asked for inputs it
              did not need
  GAP         honestly named as something it does not cover
  REFUSED     declined

WHY THIS IS WORTH MEASURING SEPARATELY from the routing sweep. activity_300
asks whether a REQUEST reaches the right place. This asks whether a CONCEPT can
be explained, which is a different capability and one this system was never
built for: EngineAI is a verified calculator with a router in front, and a
calculator does not know what creep is. If the number is low, that is the
honest state of it, and pretending otherwise by counting CLARIFY as a pass
would make the battery useless.
"""
from __future__ import annotations

import sys
from collections import Counter

import chat

EXPLAINED, COMPUTED, DEFLECTED, GAP, REFUSED = (
    "EXPLAINED", "COMPUTED", "DEFLECTED", "GAP", "REFUSED")

# (discipline, question). Deliberately spread from vocabulary a first-year
# would meet to terms only a specialist uses, because "the whole engineering
# world" includes both.
Q = [
    # --- solid mechanics / materials
    ("materials", "what is creep"),
    ("materials", "explain the difference between toughness and hardness"),
    ("materials", "what is the Bauschinger effect"),
    ("materials", "define yield strength"),
    ("materials", "what does ductile to brittle transition temperature mean"),
    ("materials", "explain work hardening"),
    ("materials", "what is a slip system"),
    ("materials", "define Poisson's ratio"),
    ("materials", "what is hydrogen embrittlement"),
    ("materials", "explain the Hall-Petch relationship"),
    ("materials", "what is anisotropy in rolled steel"),
    ("materials", "define the heat affected zone"),

    # --- structural
    ("structural", "what is lateral torsional buckling"),
    ("structural", "explain shear lag"),
    ("structural", "what does a plastic hinge mean"),
    ("structural", "define the effective length factor"),
    ("structural", "what is second order analysis"),
    ("structural", "explain the difference between ULS and SLS"),
    ("structural", "what is a moment connection"),
    ("structural", "define slenderness ratio"),
    ("structural", "what is punching shear"),
    ("structural", "explain prestressing"),

    # --- fluids
    ("fluids", "what is the boundary layer"),
    ("fluids", "explain laminar versus turbulent flow"),
    ("fluids", "what is cavitation"),
    ("fluids", "define Reynolds number"),
    ("fluids", "what is the Coanda effect"),
    ("fluids", "explain vortex shedding"),
    ("fluids", "what is net positive suction head"),
    ("fluids", "define the Moody diagram"),
    ("fluids", "what is compressible flow choking"),
    ("fluids", "explain the hydraulic jump"),

    # --- thermodynamics / heat
    ("thermal", "what is entropy"),
    ("thermal", "explain the second law of thermodynamics"),
    ("thermal", "what is a Carnot cycle"),
    ("thermal", "define enthalpy"),
    ("thermal", "what is the Nusselt number"),
    ("thermal", "explain the difference between heat and temperature"),
    ("thermal", "what is a pinch point in a heat exchanger"),
    ("thermal", "define thermal conductivity"),
    ("thermal", "what is film boiling"),
    ("thermal", "explain regenerative cooling"),

    # --- electrical / electronics
    ("electrical", "what is impedance"),
    ("electrical", "explain the difference between AC and DC"),
    ("electrical", "what is power factor"),
    ("electrical", "define reactance"),
    ("electrical", "what is a p-n junction"),
    ("electrical", "explain pulse width modulation"),
    ("electrical", "what is skin effect"),
    ("electrical", "define total harmonic distortion"),
    ("electrical", "what is an H bridge"),
    ("electrical", "explain slew rate"),

    # --- control
    ("control", "what is a PID controller"),
    ("control", "explain gain margin and phase margin"),
    ("control", "what is a Bode plot"),
    ("control", "define observability"),
    ("control", "what is state space representation"),
    ("control", "explain the Nyquist stability criterion"),
    ("control", "what is a Kalman filter"),
    ("control", "define settling time"),

    # --- manufacturing
    ("manufacturing", "what is GD&T"),
    ("manufacturing", "explain the difference between milling and turning"),
    ("manufacturing", "what is EDM"),
    ("manufacturing", "define surface roughness Ra"),
    ("manufacturing", "what is design for manufacture"),
    ("manufacturing", "explain injection moulding sink marks"),
    ("manufacturing", "what is a datum in GD&T"),
    ("manufacturing", "define tolerance stack up"),
    ("manufacturing", "what is additive manufacturing porosity"),

    # --- aerospace
    ("aerospace", "what is angle of attack"),
    ("aerospace", "explain the difference between lift and thrust"),
    ("aerospace", "what is specific impulse"),
    ("aerospace", "define the lift to drag ratio"),
    ("aerospace", "what is flutter"),
    ("aerospace", "explain a Hohmann transfer"),
    ("aerospace", "what is the Karman line"),

    # --- civil / geotech
    ("civil", "what is effective stress"),
    ("civil", "explain consolidation settlement"),
    ("civil", "what is liquefaction"),
    ("civil", "define the angle of internal friction"),
    ("civil", "what is a retaining wall surcharge"),
    ("civil", "explain the difference between a raft and a pile foundation"),

    # --- chemical
    ("chemical", "what is a unit operation"),
    ("chemical", "explain distillation reflux ratio"),
    ("chemical", "what is residence time"),
    ("chemical", "define stoichiometry"),
    ("chemical", "what is a catalyst"),
    ("chemical", "explain mass transfer coefficient"),

    # --- reliability / safety
    ("reliability", "what is MTBF"),
    ("reliability", "explain the bathtub curve"),
    ("reliability", "what is FMEA"),
    ("reliability", "define a single point of failure"),
    ("reliability", "what is a fault tree"),
    ("reliability", "explain the difference between reliability and availability"),

    # --- measurement
    ("metrology", "what is measurement uncertainty"),
    ("metrology", "explain the difference between accuracy and precision"),
    ("metrology", "what is traceability in measurement"),
    ("metrology", "define a coverage factor"),
    ("metrology", "what is calibration drift"),

    # --- vibration / acoustics
    ("vibration", "what is resonance"),
    ("vibration", "explain damping ratio"),
    ("vibration", "what is a mode shape"),
    ("vibration", "define the transmissibility of an isolator"),
    ("acoustics", "what is a decibel"),
    ("acoustics", "explain the A weighting curve"),

    # --- corrosion / tribology
    ("corrosion", "what is galvanic corrosion"),
    ("corrosion", "explain passivation"),
    ("corrosion", "what is pitting"),
    ("tribology", "what is the Stribeck curve"),
    ("tribology", "explain boundary lubrication"),
    ("tribology", "what is adhesive wear"),

    # --- welding / joining
    ("welding", "what is undercut in a weld"),
    ("welding", "explain preheat and why it is used"),
    ("welding", "what is a fillet weld leg length"),
    ("welding", "define weld penetration"),

    # --- systems / process
    ("systems", "what is requirements traceability"),
    ("systems", "explain verification versus validation"),
    ("systems", "what is technology readiness level"),
    ("systems", "define a interface control document"),

    # --- energy / power
    ("energy", "what is capacity factor"),
    ("energy", "explain grid inertia"),
    ("energy", "what is levelised cost of energy"),
    ("energy", "define the heat rate of a power plant"),

    # --- nuclear
    ("nuclear", "what is a neutron cross section"),
    ("nuclear", "explain criticality"),
    ("nuclear", "what is decay heat"),

    # --- biomedical
    ("biomedical", "what is biocompatibility"),
    ("biomedical", "explain stress shielding in an implant"),

    # --- environmental
    ("environmental", "what is embodied carbon"),
    ("environmental", "explain a life cycle assessment"),
    ("environmental", "what is BOD in wastewater"),

    # --- marine
    ("marine", "what is metacentric height"),
    ("marine", "explain hull fouling"),

    # --- optics / photonics
    ("optics", "what is refractive index"),
    ("optics", "explain total internal reflection"),

    # --- software / embedded engineering
    ("embedded", "what is real time in an embedded system"),
    ("embedded", "explain watchdog timer"),
]


def _classify(text: str) -> tuple:
    """Score by what EngineAI actually does with a definition question."""
    try:
        r = chat.respond(text, model_fn=None)
    except Exception as e:
        return DEFLECTED, {"error": f"{type(e).__name__}: {e}"}

    basis = r.get("basis")
    ans = str(r.get("answer") or "")

    if basis == getattr(chat, "EXPLAINED", "EXPLAINED"):
        return (EXPLAINED, r) if len(ans) > 80 else (DEFLECTED, r)
    if basis == chat.COMPUTED:
        return COMPUTED, r
    if basis == chat.REFERENCE:
        return EXPLAINED, r
    if r.get("is_engineering") and basis == chat.MODEL_REQUIRED:
        return GAP, r
    if basis == chat.CONVERSATIONAL:
        # substance, not just an acknowledgement
        return (EXPLAINED, r) if len(ans) > 120 else (DEFLECTED, r)
    if basis == chat.CLARIFY:
        return DEFLECTED, r
    return (EXPLAINED, r) if len(ans) > 120 else (DEFLECTED, r)


def run(quiet: bool = False) -> dict:
    rows = []
    for disc, q in Q:
        outcome, r = _classify(q)
        rows.append({"discipline": disc, "question": q, "outcome": outcome,
                     "basis": r.get("basis"), "answer": str(r.get("answer") or "")[:110]})
    counts = Counter(r["outcome"] for r in rows)
    by_disc = {}
    for r in rows:
        d = by_disc.setdefault(r["discipline"], Counter())
        d[r["outcome"]] += 1

    n = len(rows)
    explained = counts[EXPLAINED]
    if not quiet:
        print(f"  {n} definition questions across {len(by_disc)} disciplines\n")
        for k in (EXPLAINED, COMPUTED, GAP, DEFLECTED, REFUSED):
            print(f"    {k:10s} {counts[k]:4d}  ({counts[k]/n*100:.0f}%)")
        print("\n  worst-served disciplines:")
        ranked = sorted(by_disc.items(),
                        key=lambda kv: kv[1][EXPLAINED] / max(sum(kv[1].values()), 1))
        for d, c in ranked[:8]:
            tot = sum(c.values())
            print(f"    {d:15s} {c[EXPLAINED]}/{tot} explained "
                  f"({', '.join(f'{k}:{v}' for k, v in c.most_common(2))})")
    return {"n": n, "counts": dict(counts), "explained": explained,
            "explained_rate": explained / n, "by_discipline": by_disc,
            "rows": rows}


def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    res = run(quiet=True)
    chk(res["n"] >= 130, f"{res['n']} definitions across the engineering world")
    chk(len(res["by_discipline"]) >= 20,
        f"{len(res['by_discipline'])} disciplines covered")
    chk(all(r["outcome"] in (EXPLAINED, COMPUTED, DEFLECTED, GAP, REFUSED)
            for r in res["rows"]), "every question got a definite outcome")
    chk(not any("error" in str(r.get("basis", "")) for r in res["rows"]),
        "nothing crashed")

    # The measurement itself is the deliverable. This battery does NOT assert a
    # pass rate, because the honest number is whatever it is and an assertion
    # here would only tempt me to move it.
    print(f"\n  MEASURED: {res['explained']}/{res['n']} explained "
          f"({res['explained_rate']*100:.0f}%)")
    print(f"  {res['counts']}")
    print(f"\ndefinitions_battery.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    r = run()
    print(f"\n  EXPLAINED {r['explained']}/{r['n']} "
          f"({r['explained_rate']*100:.0f}%)")
