#!/usr/bin/env python3
"""
activity_300.py - 300 real user activities, from trivial to research-grade.

WHAT THIS MEASURES THAT THE OTHER BATTERIES DO NOT
---------------------------------------------------
battery.py asks "can the solvers produce correct numbers" and gets 99/99.
user_battery.py asks "does it handle 40 realistic requests honestly" and gets 40/40.
Neither asks the question a launch depends on: across the FULL SPREAD of what people
actually type - from "hello" to "find me a novel lattice topology" - what fraction
does it handle, and where exactly does it stop?

So this is a breadth-and-depth map, not a pass/fail. Every activity is scored by
what EngineAI ACTUALLY DOES with it:

    COMPUTED      a solver ran and produced a verified number or file
    REFERENCE     answered from data the system verifies on load
    CONVERSATIONAL handled as an assistant, no engineering claim made
    CLARIFY       answerable, inputs missing, asked the right question
    NAMED_GAP     engineering it cannot do, and it says which and why
    UNVERIFIED    answered but explicitly flagged as not checked
    MISROUTED     went somewhere wrong - the only outcome that is a DEFECT

Only MISROUTED is a failure. A named gap is a correct answer to a question the
system cannot compute; treating it as a failure would push the system towards
guessing, which is the behaviour this whole project exists to prevent.

Tiers: BASIC (everyday, 1-100), ADVANCED (professional, 101-220),
RESEARCH (novel work, 221-300).
"""
from __future__ import annotations
import argparse, json, re, sys

import chat, scope

COMPUTED = "COMPUTED"
REFERENCE = "REFERENCE"
CONVERSATIONAL = "CONVERSATIONAL"
CLARIFY = "CLARIFY"
NAMED_GAP = "NAMED_GAP"
UNVERIFIED = "UNVERIFIED"
MISROUTED = "MISROUTED"

BASIC, ADVANCED, RESEARCH = "basic", "advanced", "research"

# (tier, activity, what a correct handling looks like)
# expected is a SET of acceptable outcomes - several are legitimately answerable
# more than one way, and forcing a single one would test my opinion, not the system.
ACT = []


def A(tier, text, *expected):
    ACT.append((tier, text, set(expected)))


# ============================ BASIC: 1-100 =================================
for t in [
    "hello", "hi", "good morning", "thanks", "thank you very much",
    "who are you", "what are you", "what can you do", "what can you help me with",
    "are you an AI", "how do you work", "how do you know that is right",
    "can I trust your answers", "do you make mistakes", "what are your limits",
]:
    A(BASIC, t, CONVERSATIONAL)

for t in [
    "নমস্কার", "কেমন আছো", "hola", "bonjour", "guten tag", "ciao", "こんにちは",
    "안녕하세요", "مرحبا", "привет", "你好", "merhaba", "xin chào", "สวัสดี",
]:
    A(BASIC, t, CONVERSATIONAL)

for t in [
    "convert 250 psi to bar", "convert 100 mm to inches", "convert 50 kN to lbf",
    "convert 20 degC to K", "convert 1500 rpm to rad/s", "convert 3 GPa to psi",
    "convert 10 m/s to km/h", "convert 2 tonnes to kN", "convert 25 mm2 to m2",
]:
    A(BASIC, t, REFERENCE)

for t in [
    "what is the speed of light", "what is Planck's constant",
    "what is the Boltzmann constant", "what is Avogadro's number",
    "what is the gas constant", "what is the elementary charge",
    "¿Cuál es la constante de Boltzmann?",
]:
    A(BASIC, t, REFERENCE)

for t in [
    "what is the density of steel", "what is the density of aluminium",
    "what is the yield strength of mild steel", "modulus of titanium",
    "what is the melting point of copper",
    "what's the boiling point of water at 2 bar",
    "density of water at 25 C", "viscosity of air",
    "specific heat of water",
]:
    A(BASIC, t, REFERENCE)

for t in [
    "what is the formula for bending stress", "formula for beam deflection",
    "what is Hooke's law", "what is the equation for Euler buckling",
    "formula for Reynolds number", "what is Ohm's law",
    "equation for thermal expansion", "what is the Darcy-Weisbach equation",
    "formula for kinetic energy", "what is the Stefan-Boltzmann law",
]:
    A(BASIC, t, REFERENCE, COMPUTED)

for t in [
    "deflection of a cantilever with a 1 kN tip load over 500 mm",
    "stress in a 20x40 mm bar carrying 10 kN",
    "size a beam for 5 kN over 2 m",
    "what size steel beam for a 4 m span carrying 10 kN/m",
    "check an IPE 300 for a 6 m span at 12 kN/m",
    "how much will a 3 m steel beam deflect under 8 kN/m",
    "second moment of area of a 50x100 rectangle",
    "section modulus of a 30 mm round bar",
    "buckling load of a 2 m steel strut, 25 mm diameter",
    "how many M12 bolts for a 40 kN shear load",
]:
    A(BASIC, t, COMPUTED)

for t in [
    "pressure drop in 20 m of 50 mm pipe at 2 m/s",
    "Reynolds number for water at 1.5 m/s in a 25 mm pipe",
    "flow rate through a 100 mm pipe at 3 m/s",
    "heat loss through a 200 mm brick wall",
    "how much heat flows through 20 mm of steel with a 100 K difference",
    "temperature of a steel ball cooling in air",
    "radiative heat loss from a 400 K plate",
]:
    A(BASIC, t, COMPUTED)

for t in [
    "make me a 3D model of a mounting plate",
    "create a flange with 8 bolt holes",
    "draw an L bracket",
    "export a bracket as STEP",
    "make an STL I can print",
    "produce a dimensioned drawing of a flange",
    "give me a drawing with GD&T",
]:
    A(BASIC, t, COMPUTED)

for t in [
    "will this fail?", "is it safe?", "can this take the load?",
    "check this for me", "is that ok?",
]:
    A(BASIC, t, CLARIFY)

for t in [
    "recommend a laptop for CAD work", "which CAD software should I buy",
    "what's the best FEA package", "where can I buy steel beams",
]:
    A(BASIC, t, UNVERIFIED, CONVERSATIONAL)

for t in [
    "write me a poem", "plan a holiday in Japan", "what's the weather",
    "tell me a joke", "summarise this news article", "help me write an email",
    "what should I cook tonight", "translate this to French",
]:
    A(BASIC, t, UNVERIFIED)

# ========================= ADVANCED: 101-220 ================================
for t in [
    "check a 9 m unrestrained IPE 400 for lateral torsional buckling",
    "design a column for 800 kN over 4 m",
    "check a beam-column with 500 kN axial and 60 kNm moment",
    "what is the reduction factor chi_LT for a 12 m unrestrained beam",
    "classify an IPE 600 web in pure compression",
    "size an HEB column braced at mid-height about the weak axis",
    "check moment-shear interaction on a short deep beam",
    "what happens to an IPE if I use it as a column",
    "compare a hollow section against an I-beam for LTB",
    "design a mezzanine beam at 6 m with a 2 m restraint spacing",
]:
    A(ADVANCED, t, COMPUTED)

for t in [
    "combine 15 kN dead and 8 kN imposed to EN 1990",
    "what are the gamma factors for an office floor",
    "give me the ULS and SLS combinations for a storage building",
    "what imposed load should I use for a department store",
    "is 6.10a or 6.10b governing for a heavy permanent action",
    "self weight of an IPE 300 as a UDL",
]:
    A(ADVANCED, t, COMPUTED, REFERENCE)

for t in [
    "torsion in a 40 mm shaft carrying 500 Nm",
    "combined bending and torsion on a 50 mm shaft",
    "hoop stress in a 400 mm vessel at 2 MPa",
    "thick wall Lame stresses in a gun barrel",
    "stress concentration at a 10 mm hole in a plate",
    "thermal stress in a restrained steel bar heated 80 K",
    "fixed-fixed beam with a central point load",
    "reactions and uplift on an overhanging beam",
    "off-centre point load on a simply supported beam",
    "propped cantilever under UDL",
    "hollow versus solid shaft mass saving",
    "critical speed of a shaft with 1 mm static deflection",
]:
    A(ADVANCED, t, COMPUTED)

for t in [
    "fatigue life at 300 MPa amplitude in 1018 steel",
    "Miner damage for a two-block load spectrum",
    "Goodman correction for a 200 MPa mean stress",
    "life of a FAT 80 transverse butt weld at 100 MPa range",
    "critical crack length in 4340 steel at 400 MPa",
    "stress intensity for a 5 mm crack at 100 MPa",
    "Paris law crack growth from 1 mm to 20 mm",
    "how long before this weld cracks",
]:
    A(ADVANCED, t, COMPUTED)

for t in [
    "effectiveness NTU for a counter-flow exchanger",
    "LMTD for a heat exchanger with a 70 and 50 K approach",
    "fin efficiency for a 50 mm aluminium fin",
    "heat sink base temperature for 20 W",
    "junction to ambient thermal resistance network",
    "transient temperature in a semi-infinite solid",
    "contact temperature between steel and wood",
    "Biot number for a 30 mm steel sphere",
    "Nusselt number for turbulent pipe flow at Re 50000",
    "natural convection from a vertical plate",
]:
    A(ADVANCED, t, COMPUTED)

for t in [
    "Bernoulli through a converging nozzle",
    "terminal velocity of a 100 micron sand grain in water",
    "drag on a sphere at Reynolds 10000",
    "Blasius boundary layer thickness at 0.5 m",
    "lift coefficient at 5 degrees angle of attack",
    "induced drag for an aspect ratio 8 wing",
    "Manning flow in a 3 m concrete channel",
    "normal depth for 5 cumecs in a rectangular channel",
    "Froude number for open channel flow",
    "friction factor from Colebrook at Re 100000",
]:
    A(ADVANCED, t, COMPUTED)

for t in [
    "field around a 100 A busbar at 5 mm",
    "force between two busbars during a 20 kA fault",
    "skin depth in copper at 50 kHz",
    "magnetic circuit with a 1 mm air gap",
    "coax characteristic impedance for b/a = 2.3",
    "inductance of a 500 turn solenoid",
    "capacitance of a parallel plate with FR4",
    "eddy current loss in a 10 mm plate at 1 kHz",
    "C-core electromagnet gap flux density",
]:
    A(ADVANCED, t, COMPUTED)

for t in [
    "RC low pass cutoff for 1k and 1uF",
    "series RLC resonance and Q",
    "RC step response time constant",
    "inverting op-amp gain with 10k feedback",
    "half wave rectifier DC output",
    "full wave bridge with a reservoir capacitor",
    "BJT common emitter bias point",
    "Thevenin equivalent of a loaded divider",
    "maximum power transfer condition",
    "cable volt drop over 100 m at 32 A",
]:
    A(ADVANCED, t, COMPUTED)

for t in [
    "run an FEA on a cantilever and compare to beam theory",
    "mesh convergence study on a bracket",
    "cross check the closed form against finite elements",
    "steady conduction FEA on a slab",
    "solve a 2D plate conduction problem",
    "validate the CFD against Poiseuille flow",
    "developing channel flow entrance length",
    "mesh a heat sink in gmsh",
    "pendulum period from a multibody simulation",
    "double pendulum energy conservation",
    "rolling cylinder on an incline",
    "spring mass damper decay",
]:
    A(ADVANCED, t, COMPUTED)

for t in [
    "Al-Cu phase equilibrium at 700 K",
    "lever rule phase fractions on a tie line",
    "Fe-C equilibrium at the eutectoid",
    "which phases are stable in Al-Ni at 1200 K",
]:
    A(ADVANCED, t, COMPUTED, NAMED_GAP)

for t in [
    "produce a full design dossier for a bracket",
    "give me a spreadsheet with live formulas",
    "write a design report with a signature block",
    "generate a BOM with costs",
    "make a PDF submittal with the drawing and FEA plot",
    "emit a receipt I can verify",
    "produce a mesh convergence CSV",
    "plot the stress field",
    "make an interactive HTML report",
]:
    A(ADVANCED, t, COMPUTED)

for t in [
    "read this STEP file and tell me its volume",
    "what is in this DXF",
    "extract the formulas from this spreadsheet",
    "read the dimensions off this photo of a drawing",
    "identify this file for me",
    "how many triangles in this STL",
    "is this mesh watertight",
]:
    A(ADVANCED, t, COMPUTED, CLARIFY)

# ========================= RESEARCH: 221-300 ================================
for t in [
    "design a reinforced concrete beam", "check this slab to EN 1992",
    "size the reinforcement for a 6 m span", "crack width check on a concrete beam",
    "prestressed concrete losses", "design a timber joist to EN 1995",
    "masonry wall slenderness check", "composite steel-concrete beam design",
    "aluminium member with HAZ softening", "design a pile foundation",
    "bearing capacity of a pad footing", "retaining wall earth pressure",
    "slope stability by Bishop's method", "settlement of a raft foundation",
    "seismic response spectrum analysis", "wind loading to EN 1991-1-4",
    "snow drift loading on a roof", "pavement design to AASHTO",
]:
    A(RESEARCH, t, NAMED_GAP)

for t in [
    "design the fillet welds for this bracket", "bolt bearing and block shear check",
    "prying action in an end plate connection", "rate this gear pair to ISO 6336",
    "bearing L10 life to ISO 281", "shaft fatigue to DIN 743",
    "spring design to EN 10270", "check this beam to AISC 360",
    "pressure vessel to ASME VIII", "protection coordination study",
    "short circuit study to IEC 60909", "load flow on a distribution network",
    "motor starting calculation", "harmonic analysis to IEEE 519",
    "earthing grid to IEEE 80", "lightning protection to IEC 62305",
    "distillation column stage count", "relief valve sizing to API 520",
    "dispersion modelling for a gas release", "pinch analysis of a heat network",
]:
    A(RESEARCH, t, NAMED_GAP)

for t in [
    "what will this part cost to manufacture", "generate the CNC toolpath",
    "build a CAD assembly with an exploded view", "check this for manufacturability",
    "tolerance stack up across five parts", "reliability analysis with Weibull",
    "FMEA on this design", "life cycle assessment of this component",
]:
    A(RESEARCH, t, NAMED_GAP)

for t in [
    "octet lattice relative density at 0.8 mm struts",
    "Gibson-Ashby scaling for a BCC lattice",
    "does this lattice buckle or yield first",
    "transition density between buckling and yield",
    "REBCO critical current at 2 T perpendicular",
    "AC loss in a REBCO tape by Norris",
    "cryogenic margin for a superconducting magnet",
    "peak field on the winding of a solenoid",
    "coupled electro-thermal runaway in a resistor",
    "busbar current to stress chain",
    "magneto-structural force on a beam",
    "shaft life with temperature knockdown and fatigue",
    "wind pressure on a clamped plate",
    "thermal stress with EN 1993-1-2 derating",
]:
    A(RESEARCH, t, COMPUTED)

for t in [
    "find me a novel lattice topology better than octet",
    "invent a new alloy for high temperature service",
    "discover an undocumented failure mode in this design",
    "propose a design nobody has tried before",
    "what research direction should I pursue",
    "write a journal paper on these results",
    "is my new engine concept viable",
    "optimise this design across every discipline at once",
    "predict what will fail in service over ten years",
    "design a fusion reactor first wall",
    "certify this design as safe to build",
    "sign off my drawings",
    "tell me this is good enough to manufacture",
]:
    A(RESEARCH, t, NAMED_GAP, UNVERIFIED, CLARIFY)

# Requests that USED to be refused as "novel discovery" and are now real work.
# research.py runs these against data with the full protocol - pre-registration,
# audit, falsification, replication - so refusing them was the defect, not the
# safeguard. What is still refused is invention with nothing to compute from.
for t in [
    "derive a new correlation from this data",
    "research the trade-off between strength and embodied carbon",
    "find the relationship between water cement ratio and strength",
    "run a sensitivity analysis on these results",
    "which parameter matters most for stability",
    "fit a law to this dataset",
    "map the pareto front for this design space",
]:
    A(RESEARCH, t, "RESEARCH", COMPUTED)


def _classify(text):
    """Run the activity through EngineAI exactly as a user would."""
    r = chat.respond(text, model_fn=lambda t: f"[assistant reply: {t[:32]}]")
    b = r["basis"]
    if b == chat.COMPUTED:
        return COMPUTED, r
    if b == chat.REFERENCE:
        return REFERENCE, r
    if b == chat.CONVERSATIONAL:
        return CONVERSATIONAL, r
    if b == chat.CLARIFY:
        return CLARIFY, r
    if r.get("is_engineering"):
        return NAMED_GAP, r
    return UNVERIFIED, r


def run(quiet=False):
    rows = []
    for i, (tier, text, expected) in enumerate(ACT, 1):
        try:
            got, r = _classify(text)
        except Exception as e:
            got, r = MISROUTED, {"note": f"{type(e).__name__}: {e}"}
        ok = got in expected
        rows.append({"n": i, "tier": tier, "activity": text, "outcome": got,
                     "expected": sorted(expected), "ok": ok,
                     "detail": (str(r.get("capability") or r.get("answer")
                                    or r.get("note") or ""))[:90]})
        if not quiet and not ok:
            print(f"  MISROUTED {i:>3} [{tier}] {text[:52]:<52} -> {got} "
                  f"(wanted {'/'.join(sorted(expected))})")
    by_tier, by_outcome = {}, {}
    for r in rows:
        t = by_tier.setdefault(r["tier"], {"n": 0, "ok": 0})
        t["n"] += 1
        t["ok"] += 1 if r["ok"] else 0
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
    handled = sum(1 for r in rows if r["ok"])
    engaged = sum(1 for r in rows
                  if r["outcome"] in (COMPUTED, REFERENCE, CONVERSATIONAL, CLARIFY))
    return {"total": len(rows), "handled_correctly": handled,
            "misrouted": sum(1 for r in rows if not r["ok"]),
            "engaged_directly": engaged,
            "by_tier": by_tier, "by_outcome": by_outcome, "rows": rows}


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    chk(f"the battery holds {len(ACT)} distinct activities across three tiers",
        len(ACT) >= 300, f"{len(ACT)} activities")
    chk("no duplicate activities", len({a[1] for a in ACT}) == len(ACT),
        f"{len(ACT) - len({a[1] for a in ACT})} duplicates")
    tiers = {a[0] for a in ACT}
    chk("all three tiers are represented", tiers == {BASIC, ADVANCED, RESEARCH},
        ", ".join(f"{t}={sum(1 for a in ACT if a[0]==t)}" for t in sorted(tiers)))

    s = run(quiet=True)
    chk(f"EngineAI handles {s['handled_correctly']}/{s['total']} correctly",
        s["misrouted"] == 0,
        f"{s['misrouted']} misrouted"
        + ("" if s["misrouted"] == 0 else
           " -> " + "; ".join(f"#{r['n']} {r['activity'][:34]} went {r['outcome']}"
                              for r in s["rows"] if not r["ok"])[:300]))
    for tier in (BASIC, ADVANCED, RESEARCH):
        t = s["by_tier"][tier]
        chk(f"{tier}: {t['ok']}/{t['n']}", t["ok"] == t["n"])
    chk("it ENGAGES directly with most activities rather than deflecting",
        s["engaged_directly"] / s["total"] > 0.5,
        f"{s['engaged_directly']}/{s['total']} answered or computed directly")
    chk("every named gap says WHICH standard or capability is missing",
        all("not implemented" in r["detail"] or "cannot" in r["detail"].lower()
            for r in s["rows"] if r["outcome"] == NAMED_GAP),
        f"{s['by_outcome'].get(NAMED_GAP, 0)} named gaps")

    print("activity_300 selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="300 real user activities")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.run:
        print("=" * 100)
        print(f"EngineAI - {len(ACT)} REAL USER ACTIVITIES, basic to research grade")
        print("=" * 100)
        s = run()
        print()
        for tier in (BASIC, ADVANCED, RESEARCH):
            t = s["by_tier"][tier]
            print(f"  {tier:<10} {t['ok']:>3}/{t['n']:<3} handled correctly")
        print()
        print("  outcomes: " + ", ".join(f"{k}={v}" for k, v in
                                         sorted(s["by_outcome"].items())))
        print()
        print(f"  {s['handled_correctly']}/{s['total']} handled correctly | "
              f"{s['engaged_directly']} engaged directly | "
              f"MISROUTED: {s['misrouted']}")
        if a.json:
            with open(a.json, "w") as f:
                json.dump(s, f, indent=2)
        raise SystemExit(0 if s["misrouted"] == 0 else 1)
    raise SystemExit(_selftest())
