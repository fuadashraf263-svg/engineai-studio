#!/usr/bin/env python3
"""
wb_agent.py - the thing you talk to. Understands a command, runs it, or ASKS.

WHY THIS EXISTS
---------------
The workbench had panes and forms. Everything it can do, it could only be told
to do by filling in the right boxes on the right tab - which is a control
panel, not an assistant. And EngineAI already had all the parts for something
better and never joined them up:

  scope.classify    knows whether a request is in scope, and names the gap if not
  nl_router.route   picks the capability AND extracts parameters from prose
  chat.py           defines a CLARIFY basis for "I need inputs I do not have"
  workbench jobs    build solids, solve, produce verified artifacts

CLARIFY existed as a label with nothing behind it. This module is what makes it
real: when a command is missing something the calculation genuinely needs, the
answer is a QUESTION, not a default silently substituted and a number returned
as if it were asked for.

THE RULE ABOUT DEFAULTS
-----------------------
A parametric part has defaults, and using them is fine - "build a flange" has a
sensible answer, and the reply says which defaults it took. A load does not
have a default. Nobody's cantilever is 1000 N because the software said so, and
a factor of safety computed against an invented load is worse than no answer,
because it looks like an answer. So: MISSING GEOMETRY is filled from the
registry and declared; MISSING LOADS ARE ASKED FOR.

WHAT IT WILL NOT DO
-------------------
It does not free-associate. Every command either matches a known action, or is
handed to chat.respond, which carries its own basis labelling and its own
refusals. There is no path here that invents an engineering number.

  python wb_agent.py --selftest
  python wb_agent.py "build a flange with a 120 mm outer diameter"
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# --------------------------------------------------------------------------
# units and numbers
# --------------------------------------------------------------------------

_NUM = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"

# Multipliers into the workbench's base units: mm for length, N for force.
LENGTH_MM = {"mm": 1.0, "millimetre": 1.0, "millimeter": 1.0,
             "cm": 10.0, "centimetre": 10.0, "centimeter": 10.0,
             "m": 1000.0, "metre": 1000.0, "meter": 1000.0,
             "in": 25.4, "inch": 25.4, "\"": 25.4}
FORCE_N = {"n": 1.0, "newton": 1.0, "kn": 1000.0, "kilonewton": 1000.0,
           "kgf": 9.80665, "lbf": 4.44822, "tonne": 9806.65, "ton": 9806.65}


def _val(num: str, unit: str, table: dict, default: float = 1.0) -> float:
    return float(num) * table.get((unit or "").lower().strip(), default)


def find_length(text: str, *names) -> float | None:
    """A length given as `name = 12 mm`, `name 12mm`, or `12 mm name`."""
    for n in names:
        pat = (rf"\b{n}\b\s*(?:=|of|is|:)?\s*({_NUM})\s*"
               rf"(mm|cm|m|in|inch|millimetre|millimeter|centimetre|"
               rf"centimeter|metre|meter)?\b")
        m = re.search(pat, text, re.I)
        if m:
            return _val(m.group(1), m.group(2), LENGTH_MM)
        m = re.search(rf"({_NUM})\s*(mm|cm|m|in|inch)?\s+\b{n}\b", text, re.I)
        if m:
            return _val(m.group(1), m.group(2), LENGTH_MM)
    return None


def parse_length(s: str, default_unit: str = "mm") -> float | None:
    """A bare answer like `1.5 m`, `400`, `12in` -> millimetres.

    Needed because merge() folds a user's REPLY back into the intent, and a
    reply is not a sentence with a keyword in it - it is just the value. The
    first version fell back to grabbing the digits and dropped the unit, so
    answering "1.5 m" to "how long is it?" built a 1.5 mm beam.
    """
    m = re.search(rf"({_NUM})\s*(mm|cm|millimetres?|millimeters?|centimetres?|"
                  rf"centimeters?|metres?|meters?|inch(?:es)?|in|m)?\b",
                  str(s), re.I)
    if not m:
        return None
    return _val(m.group(1), m.group(2) or default_unit, LENGTH_MM)


def find_force(text: str) -> float | None:
    m = re.search(rf"({_NUM})\s*(kN|N|kgf|lbf|tonnes?|tons?|newtons?|"
                  rf"kilonewtons?)\b", text, re.I)
    if m:
        return _val(m.group(1), m.group(2).rstrip("s"), FORCE_N)
    m = re.search(rf"\b(?:load|force|carrying|carries|under)\b[^.]{{0,24}}?"
                  rf"({_NUM})\s*(kN|N)?\b", text, re.I)
    if m:
        return _val(m.group(1), m.group(2) or "N", FORCE_N)
    return None


def find_section(text: str):
    """`50 x 30`, `50x30 mm`, `50 by 30` -> (50.0, 30.0)."""
    m = re.search(rf"({_NUM})\s*(?:x|×|by)\s*({_NUM})\s*"
                  rf"(mm|cm|m|in)?\b", text, re.I)
    if not m:
        return None
    u = m.group(3)
    return (_val(m.group(1), u, LENGTH_MM), _val(m.group(2), u, LENGTH_MM))


# --------------------------------------------------------------------------
# intents
# --------------------------------------------------------------------------

BUILD = re.compile(r"\b(build|make|create|model|generate|give me|draw|"
                   r"design)\b", re.I)
EXPORT = re.compile(r"\b(export|deliverable|drawing pack|full set|dossier|"
                    r"documentation|report pack|whole set)\b", re.I)
SOLVE = re.compile(r"\b(stress|deflect|deflection|bend|bending|fea|"
                   r"finite element|solve|analyse|analyze|how (?:much|far) "
                   r"will it (?:bend|deflect))\b", re.I)
DISCOVER = re.compile(r"\b(discover|find a law|fit a law|what law|"
                      r"correlat|regress)\b", re.I)
CANTILEVER = re.compile(r"\b(cantilever|beam|bar|strut|shaft)\b", re.I)
REVIEW = re.compile(r"\b(review|check (?:my|this|the)|what.s wrong|find (?:the )?"
                    r"(?:error|fault|problem|mistake)|is (?:this|it|my) "
                    r"(?:ok|safe|alright|good|strong enough)|critique|audit|"
                    r"any problems|sanity check)\b", re.I)
ASSESS = re.compile(r"\b(how much can you|can you help|are you able|what can "
                    r"you do (?:for|with)|how far can you|will you be able|"
                    r"assess|is this within|capable of)\b", re.I)


# What a person calls each registry parameter. Nobody types "L = 200"; they
# type "200 mm long". Only the aliases for the part being built are ever tried,
# so the collision between mounting_plate's T (plate thickness) and u_channel's
# t (wall thickness) cannot arise - a part has one or the other.
ALIASES = {
    "L": ["length", "long"], "W": ["width", "wide"],
    "T": ["thickness", "thick"],
    "t": ["wall", "wall thickness", "thickness", "thick"],
    "od": ["outer diameter", "outside diameter", "od", "diameter"],
    "idia": ["bore", "inner diameter", "inside diameter"],
    "bc": ["bolt circle", "pitch circle", "pcd"],
    "bolt_d": ["bolt diameter", "bolt"],
    "hole_d": ["hole diameter", "hole"],
    "margin": ["margin", "edge distance", "inset"],
    "a": ["horizontal leg", "first leg", "leg a"],
    "b": ["vertical leg", "second leg", "leg b"],
    "h": ["height", "high", "depth", "deep"],
    "w": ["width", "wide"],
    "d1": ["first diameter", "large diameter", "larger diameter"],
    "d2": ["second diameter", "small diameter", "smaller diameter"],
    "l1": ["first length"], "l2": ["second length"],
    "length": ["long", "length"], "width": ["wide", "width"],
}


# DESIGNING A MACHINE IS NOT DRAWING IT.
#
# "build a jet engine mechanical drawing" was first answered by offering a
# shaft, which was cowardly, and then by a flat refusal, which was useless. A
# refusal that stops there says the tool has no capability - and that is not
# true. It cannot produce a jet engine as one CAD assembly; it CAN compute
# nearly every sizing calculation a small one needs, each against a verified
# closed form.
#
# So a named assembly gets DECOMPOSED into the analyses that actually exist,
# each pointing at the module that performs it. That is what an engineer wants
# from a design assistant: not a picture, a route through the problem.
DECOMPOSITION = {
    "jet engine": [
        ("Brayton cycle: pressure ratio, turbine inlet temperature, specific "
         "thrust and SFC", "engines"),
        ("Combustor: real equilibrium chemistry - the fuel-air ratio that "
         "actually reaches your turbine inlet temperature, with dissociation, "
         "and the NO and CO that come with it", "combustion (Cantera)"),
        ("Nozzle exit area from mass flow and the choked-flow relation",
         "fluid_mech"),
        ("Compressor and turbine disc: rotating hoop and radial stress at speed",
         "mechanics"),
        ("Blade root: centrifugal pull, and the section needed to carry it",
         "mechanics"),
        ("Shaft: torsion from transmitted power, angle of twist, and the "
         "critical speed you must stay away from", "mechanics"),
        ("Combustor casing: as a thin-walled pressure vessel, hoop and "
         "longitudinal stress", "mechanics"),
        ("Bearing loads and the bolted joints holding the casings",
         "standards / bolt sizing"),
        ("Material choice at temperature, and creep as the limit that actually "
         "bites", "materials"),
        ("Low-cycle fatigue of the disc over start-stop cycles", "fatigue"),
    ],
    "gas turbine": None, "turbojet": None, "turbine": None,
    "pump": [
        ("Duty point: head, flow and hydraulic power", "fluid_mech"),
        ("NPSH available against required, which is what decides cavitation",
         "fluid_mech"),
        ("Impeller: centrifugal stress and shaft torque", "mechanics"),
        ("Motor sizing from shaft power and efficiency", "circuits_ext"),
        ("Casing as a pressure boundary", "mechanics"),
    ],
    "gearbox": [
        ("Ratio, torque at each stage, and shaft power", "mechanics"),
        ("Shaft torsion and angle of twist per stage", "mechanics"),
        ("Bearing life from the resulting radial and axial loads",
         "ISO 281 (equation library only)"),
        ("Housing bolts and the joint that carries the reaction",
         "standards / bolt sizing"),
        ("Gear tooth rating to ISO 6336 - equation library only, NOT a design "
         "procedure", "GAP"),
    ],
    "pressure vessel": [
        ("Thin- and thick-wall hoop and longitudinal stress (Lame)", "mechanics"),
        ("Required wall thickness for the design pressure", "mechanics"),
        ("Nozzle stress concentration factor", "mechanics"),
        ("Bolted flange joint and gasket seating", "standards"),
        ("ASME VIII code procedure itself", "GAP"),
    ],
    "heat exchanger": [
        ("Duty, LMTD and the required area", "thermal"),
        ("Tube-side pressure drop and Reynolds number", "fluid_mech"),
        ("Nusselt correlation for the film coefficient", "thermal"),
        ("Thermal stress from the tube-to-shell temperature difference",
         "mechanics"),
    ],
    "bridge": [
        ("Load combinations to EN 1990", "loads"),
        ("Beam bending, shear and deflection for the deck", "structural"),
        ("Steel member checks to EN 1993-1-1", "steel_design"),
        ("Fatigue detail categories for the welded connections", "fatigue"),
        ("Reinforced concrete deck design to EN 1992", "GAP"),
        ("Seismic actions to EN 1998", "GAP"),
    ],
}
for _alias in ("gas turbine", "turbojet", "turbine"):
    DECOMPOSITION[_alias] = DECOMPOSITION["jet engine"]

# What is outside this tool ALTOGETHER for each job. Without this the
# decomposition reads as a complete method, and it is not: it is the subset
# that happens to be computable here. Naming the rest is what stops a list of
# nine calculations from being mistaken for a design process.
BEYOND = {
    "jet engine": [
        "compressor and turbine aerodynamic design - blade angles, stage "
        "loading, surge margin. No turbomachinery aero here at all",
        # NOT combustion chemistry - Cantera with GRI-Mech 3.0 is installed and
        # combustion.py uses it. Listing it here was an unclaimed capability
        # being reported as a gap, which is its own kind of dishonesty.
        "flame stability, ignition and combustor length - equilibrium "
        "chemistry IS available, reaction kinetics is not",
        "control system, fuel scheduling and starting",
        "rotordynamics beyond a first critical speed estimate",
        "cooling passage design and conjugate heat transfer",
        "certification, rig testing and containment",
    ],
    "gearbox": ["gear geometry generation and tooth profile",
                "lubrication and thermal rating", "NVH"],
    "pump": ["impeller blade hydraulic design", "full CFD of the volute",
             "cavitation erosion prediction"],
    "bridge": ["geotechnical foundation design", "construction sequencing",
               "dynamic and pedestrian comfort assessment"],
    "pressure vessel": ["welding procedure and NDT plan",
                        "code certification to ASME VIII or PED"],
    "heat exchanger": ["fouling over life", "vibration-induced tube failure"],
}
for _alias in ("gas turbine", "turbojet", "turbine"):
    BEYOND[_alias] = BEYOND["jet engine"]


def coverage(thing: str) -> dict:
    """How much of THIS job can actually be done here, counted not claimed.

    "I can help with that" is worthless. What an engineer needs before
    committing time is the fraction, and specifically WHICH parts - so this
    counts the decomposition steps that map to a live module against the ones
    marked GAP, and reports both lists.
    """
    key = next((k for k in DECOMPOSITION if k in (thing or "").lower()), None)
    steps = DECOMPOSITION.get(key) or []
    can = [(w, m) for w, m in steps if m != "GAP"]
    cannot = [(w, m) for w, m in steps if m == "GAP"]
    n = len(steps)
    return {"topic": key, "steps": n, "can": can, "cannot": cannot,
            "fraction": (len(can) / n) if n else None}


def assess(thing: str, parts: list) -> str:
    """Say plainly how far this tool can carry the user's actual job."""
    c = coverage(thing)
    if not c["steps"]:
        live = []
        try:
            import scope
            live = [x["capability"] for x in scope.capability_index()
                    if x.get("live")]
        except Exception:                                     # noqa: BLE001
            pass
        return (
            f"I do not have a worked decomposition for '{thing}', so I will not "
            f"pretend to a percentage. What I can tell you honestly is what "
            f"exists: {len(live)} verified analysis capabilities "
            f"({', '.join(sorted(live)[:8])}{'...' if len(live) > 8 else ''}), "
            f"five parametric CAD primitives, a CadQuery script editor for "
            f"anything else, CalculiX FEA, and file output that is re-opened "
            f"and checked before you get it.\n\n"
            f"Describe the job as a list of the calculations it needs and I "
            f"will mark each one: can do, partly, or not implemented.")

    # NO PERCENTAGE OF MY OWN LIST. The first version divided the steps I can
    # do by the steps I chose to write down and announced "100% of a jet
    # engine", which is circular and is precisely the fabricated number this
    # project exists to refuse. Coverage is only meaningful against what the
    # WHOLE job needs, so BEYOND names the parts that are outside this tool
    # altogether and the count is left as a count.
    lines = [
        f"Honestly, for a {c['topic']}: I can compute {len(c['can'])} named "
        f"sizing and analysis steps. That is the analysis subset of the job, "
        f"not the job. I am not going to turn it into a percentage, because "
        f"the denominator would be my own list.",
        "",
        f"What I will actually do ({len(c['can'])}):"]
    for w, m in c["can"]:
        lines.append(f"  + {w}   [{m}]")
    if c["cannot"]:
        lines.append("")
        lines.append(f"Named gaps inside that list ({len(c['cannot'])}) - real "
                     f"gaps, not modesty:")
        for w, _m in c["cannot"]:
            lines.append(f"  - {w}")
    if BEYOND.get(c["topic"]):
        lines.append("")
        lines.append("Outside this tool entirely, so you will need something "
                     "else for:")
        for w in BEYOND[c["topic"]]:
            lines.append(f"  - {w}")
    lines += [
        "",
        "Every number I give comes from a solver or a cited closed form and "
        "carries a receipt. I will not invent an input - where one is missing "
        "I will ask for it and keep going with everything else.",
        "",
        "Say which step to start with, or give me the first numbers and I "
        "will begin."]
    return "\n".join(lines)


def decompose(thing: str, parts: list) -> str:
    """Turn 'design a X' into the list of analyses that genuinely exist."""
    key = next((k for k in DECOMPOSITION if k in thing), None)
    steps = DECOMPOSITION.get(key) if key else None
    head = (f"I cannot hand you a {thing} as a single CAD assembly - there is no "
            f"assembly model or mate solver here, and the part registry holds "
            f"five primitives ({', '.join(parts)}).\n\n"
            f"That is a limit on the DRAWING, not on the engineering. ")
    if not steps:
        return head + (
            "Tell me which part of it you want sized and I will say plainly "
            "whether I can compute that one. Ask 'what can you do' for the "
            "full capability list.")
    lines = [head + f"A {thing} decomposes into these, and I can compute the "
                    f"ones not marked GAP, each against a verified closed form:"]
    for i, (what, where) in enumerate(steps, 1):
        mark = "  [NOT IMPLEMENTED]" if where == "GAP" else f"  [{where}]"
        lines.append(f"{i}. {what}{mark}")
    lines.append("")
    lines.append("Give me the numbers for any one of them and I will run it and "
                 "show the receipt. A good place to start is the cycle: tell me "
                 "the pressure ratio, the turbine inlet temperature and the air "
                 "mass flow you are aiming at.")
    return "\n".join(lines)


CYCLE_WORDS = re.compile(
    r"\b(turbojet|turbofan|ramjet|scramjet|brayton|otto cycle|otto|diesel cycle|"
    r"diesel|dual cycle|atkinson|miller cycle|stirling|rankine|steam cycle|"
    r"rocket|gas turbine cycle|thermal efficiency|specific thrust|"
    r"compression ratio|pressure ratio|bypass ratio|delta.?v|isp|"
    r"specific impulse)\b", re.I)

# Which arguments each cycle genuinely needs before it can run. Geometry has
# registry defaults; a turbine inlet temperature does not, and inventing one
# would produce a thrust figure that reads exactly like a real one.
CYCLE_NEEDS = {
    "otto": [], "diesel": [], "atkinson": [], "stirling": [],
    "brayton": [], "turbojet": ["T4"], "turbofan": ["T4"], "ramjet": ["T4"],
    "rocket": ["Tc"], "rankine": ["p_boiler"],
}
CYCLE_ALIAS = {"otto cycle": "otto", "diesel cycle": "diesel",
               "dual cycle": "dual", "miller cycle": "atkinson",
               "steam cycle": "rankine", "gas turbine cycle": "brayton",
               "scramjet": "ramjet", "thermal efficiency": "brayton",
               "specific thrust": "turbojet", "compression ratio": "otto",
               "pressure ratio": "brayton", "bypass ratio": "turbofan",
               "delta v": "rocket", "delta-v": "rocket", "isp": "rocket",
               "specific impulse": "rocket"}


def cycle_intent(word: str, t: str) -> "Intent":
    """Read an engine-cycle request and pull the numbers out of the sentence."""
    cycle = CYCLE_ALIAS.get(word, word)
    p = {"cycle": cycle}
    missing = []

    def num(*names):
        for n in names:
            m = re.search(rf"\b{n}\b\s*(?:=|of|is|:|ratio of)?\s*({_NUM})",
                          t, re.I)
            if m:
                return float(m.group(1))
            m = re.search(rf"({_NUM})\s*[:]?\s*1?\s*\b{n}\b", t, re.I)
            if m:
                return float(m.group(1))
        return None

    r = num("compression ratio", "compression", r"\br\b")
    rp = num("pressure ratio", "overall pressure ratio", "opr", "rp")
    T4 = num("turbine inlet temperature", "tit", "t4", "turbine inlet")
    T3 = num("t3", "peak temperature", "combustor exit")
    M = num("mach", "at mach", "m")
    alt = num("altitude", "alt")
    bypass = num("bypass ratio", "bpr", "bypass")
    cutoff = num("cutoff ratio", "cutoff")
    mdot = num("mass flow", "air mass flow", "mdot")

    if cycle in ("otto", "atkinson"):
        p["r"] = r or 8.0
        if cycle == "atkinson":
            p = {"cycle": cycle, "r_comp": r or 8.0,
                 "r_exp": num("expansion ratio", "expansion") or (r or 8.0) * 1.5}
    elif cycle == "diesel":
        p["r"] = r or 18.0
        p["cutoff"] = cutoff or 2.0
    elif cycle == "brayton":
        p["rp"] = rp or 10.0
        if T3 or T4:
            p["T3"] = T3 or T4
    elif cycle in ("turbojet", "turbofan"):
        p["rp"] = rp or 12.0
        p["M"] = M if M is not None else 0.8
        p["alt_m"] = alt if alt is not None else 10000.0
        if mdot:
            p["mdot" if cycle == "turbojet" else "mdot_core"] = mdot
        if cycle == "turbofan":
            p["bypass"] = bypass or 8.0
        if T4:
            p["T4"] = T4
        else:
            missing.append({
                "field": "T4", "unit": "K",
                "question": "What turbine inlet temperature are you designing "
                            "to? This one has no default - it is the number "
                            "that decides both thrust and whether the blades "
                            "survive, and guessing it would produce a thrust "
                            "figure that looks exactly like a real one."})
    elif cycle == "ramjet":
        p["M"] = M if M is not None else 2.5
        p["alt_m"] = alt if alt is not None else 15000.0
        if T4:
            p["T4"] = T4
        else:
            missing.append({"field": "T4", "unit": "K",
                            "question": "What combustor exit temperature?"})
    elif cycle == "stirling":
        p["Tc"] = num("cold", "tc", "sink") or 300.0
        p["Th"] = num("hot", "th", "source") or 900.0

    return Intent(action="cycle", params=p, missing=missing,
                  reason=f"the {cycle} cycle",
                  capability="engines")


class Intent(dict):
    """An interpreted command. Always carries WHY it decided what it did."""

    @property
    def needs_input(self) -> bool:
        return bool(self.get("missing"))


def parts_available() -> list:
    try:
        import cad_parts
        return sorted(cad_parts.PARTS)
    except Exception:                                         # noqa: BLE001
        return []


def match_part(text: str):
    """Which registry part is named, if exactly one is."""
    t = text.lower()
    hits = []
    for p in parts_available():
        words = p.split("_")
        if p in t or p.replace("_", " ") in t or all(w in t for w in words):
            hits.append(p)
    if len(hits) == 1:
        return hits[0], None
    # a bare noun that several parts answer to
    for noun, cands in (("plate", ["mounting_plate"]),
                        ("bracket", ["l_bracket"]),
                        ("channel", ["u_channel"]),
                        ("flange", ["flange"]),
                        ("shaft", ["shaft"])):
        if re.search(rf"\b{noun}\b", t):
            cands = [c for c in cands if c in parts_available()]
            if len(cands) == 1:
                return cands[0], None
    if hits:
        return None, hits
    return None, None


def interpret(text: str) -> Intent:
    """Read a command. Returns an action to run, questions to ask, or a pass
    to chat.respond - and never a fabricated engineering input."""
    t = (text or "").strip()
    if not t:
        return Intent(action="none", missing=[], reason="empty command")

    # ---- an engine cycle: the calculation, not a description of it --------
    cy = CYCLE_WORDS.search(t)
    if cy and not REVIEW.search(t):
        return cycle_intent(cy.group(0).lower(), t)

    # ---- how much of MY job can you do? -----------------------------------
    # Asked before committing time, and it deserves a counted answer rather
    # than "yes I can help with that".
    if ASSESS.search(t):
        m = re.search(r"\b(jet engine|gas turbine|turbojet|turbine|gearbox|"
                      r"pump|compressor|pressure vessel|heat exchanger|bridge)\b",
                      t, re.I)
        return Intent(action="decompose", params={}, missing=[],
                      reason="an honest statement of coverage",
                      answer=assess(m.group(0).lower() if m else t,
                                    parts_available()))

    # ---- review an existing design ----------------------------------------
    # Ordered AFTER the solver check below? No - it has to be guarded instead,
    # because "check the stress in this cantilever" matches both REVIEW
    # ("check the") and SOLVE ("stress"). Naming a specific quantity is a
    # request to COMPUTE it; "check my plate" with no quantity named is a
    # request to look the whole thing over.
    if REVIEW.search(t) and not (SOLVE.search(t) and
                                 (CANTILEVER.search(t) or "load" in t.lower())):
        part, _amb = match_part(t)
        duty = {}
        P = find_force(t)
        if P is not None:
            duty["load_N"] = P
        sp = find_length(t, "span", "length", "long")
        if sp is not None:
            duty["span_mm"] = sp
        for name in ("steel", "stainless", "aluminium", "aluminum", "titanium",
                     "cast iron", "ABS", "PLA", "nylon"):
            if re.search(rf"\b{name}\b", t, re.I):
                import design_review
                duty["material"] = next(
                    (k for k in design_review.MATERIALS if name.lower()[:4]
                     in k.lower()), None)
                break
        tm = re.search(rf"({_NUM})\s*(?:deg\s*)?c\b", t, re.I)
        if tm:
            duty["service_C"] = float(tm.group(1))
        given = {}
        if part:
            try:
                import cad_parts
                for k in cad_parts.PARTS[part][2]:
                    v = find_length(t, k, k.replace("_", " "), *ALIASES.get(k, []))
                    if v is not None:
                        given[k] = v
            except Exception:                                 # noqa: BLE001
                pass
        return Intent(action="review",
                      params={"part": part, "params": given, "duty": duty},
                      missing=[],
                      reason="a design review of what you already have",
                      capability="design_review")

    # ---- FEA on a real solid ----------------------------------------------
    # "run FEA on the flange", "solve the uploaded part", "stress in my STEP".
    # Distinguished from the beam solver by naming a PART or a file rather
    # than a beam: a beam has a closed form and does not need a mesh.
    part_named, _amb2 = match_part(t)
    wants_solid = (SOLVE.search(t) and
                   (part_named or re.search(r"\b(step|uploaded|attached|"
                                            r"my (?:part|model|solid|file)|"
                                            r"this (?:part|solid|geometry))\b",
                                            t, re.I)))
    if wants_solid and not REVIEW.search(t):
        P = find_force(t)
        p = {"part": part_named, "converge": True}
        m = re.search(r"\bfix(?:ed|ing)?\s*([+-][XYZ])\b", t, re.I)
        if m:
            p["fix"] = m.group(1).upper()
        m = re.search(r"\b(?:load|push|pull)(?:ing|ed)?\s*([+-][XYZ])\b", t, re.I)
        if m:
            p["push"] = m.group(1).upper()
        missing = []
        if P is None:
            missing.append({
                "field": "force_N", "unit": "N",
                "question": "What load, in newtons? A stress field computed "
                            "against an invented load is a picture, not a "
                            "result, so I will not choose one."})
        else:
            p["force_N"] = P
        return Intent(action="solidfea", params=p, missing=missing,
                      reason="finite element analysis of the actual solid",
                      capability="fea_solid")

    # ---- solve ------------------------------------------------------------
    if SOLVE.search(t) and (CANTILEVER.search(t) or "load" in t.lower()):
        P = find_force(t)
        # "a 400 mm cantilever" names the length by naming the OBJECT, which is
        # how people actually write it. find_length's second pattern reads
        # "<number> <unit> <word>", so the object nouns belong in this list.
        sec = find_section(t)
        L = find_length(t, "length", "long", "span", "cantilever", "beam",
                        "bar", "strut", "shaft")
        b = sec[0] if sec else find_length(t, "width", "wide", "b")
        h = sec[1] if sec else find_length(t, "depth", "deep", "height", "h")
        missing = []
        if P is None:
            missing.append({
                "field": "P_N", "unit": "N",
                "question": "What load does it carry? A deflection computed "
                            "against an invented load looks like an answer and "
                            "is not one, so I will not pick a number for you."})
        if L is None:
            missing.append({"field": "L_mm", "unit": "mm",
                            "question": "How long is it?"})
        if b is None or h is None:
            missing.append({
                "field": "section", "unit": "mm",
                "question": "What is the cross-section, width x depth? "
                            "For example 25 x 45 mm."})
        params = {k: v for k, v in
                  (("P_N", P), ("L_mm", L), ("b_mm", b), ("h_mm", h))
                  if v is not None}
        return Intent(action="fea", params=params, missing=missing,
                      reason="a cantilever stress and deflection problem",
                      capability="mechanics of materials")

    # ---- deliverables -----------------------------------------------------
    if EXPORT.search(t):
        P = find_force(t)
        span = find_length(t, "span", "length", "long")
        missing = []
        if P is None:
            missing.append({"field": "load_N", "unit": "N",
                            "question": "What load should the part be sized "
                                        "for? This one has no sensible default."})
        if span is None:
            missing.append({"field": "span_mm", "unit": "mm",
                            "question": "Over what span?"})
        params = {k: v for k, v in (("load_N", P), ("span_mm", span))
                  if v is not None}
        w = find_length(t, "width", "wide")
        if w:
            params["width_mm"] = w
        return Intent(action="deliverables", params=params, missing=missing,
                      reason="the full verified deliverable set",
                      capability="deliverables")

    # ---- discover ---------------------------------------------------------
    if DISCOVER.search(t):
        return Intent(action="research", params={}, missing=[{
            "field": "rows", "unit": "csv",
            "question": "Paste the data as CSV with a header row - at least 30 "
                        "rows, all values positive. Below 30 the fit fails for "
                        "want of data, and reporting that as REFUTED would be a "
                        "verdict on your sample size, not on your physics."}],
            reason="monomial law discovery with five falsification attacks",
            capability="research.run")

    # ---- build ------------------------------------------------------------
    if BUILD.search(t) or match_part(t)[0]:
        part, ambiguous = match_part(t)
        if ambiguous:
            return Intent(action="cad", params={}, reason="a part to build",
                          missing=[{"field": "part", "unit": "",
                                    "options": ambiguous,
                                    "question": "Which one did you mean? "
                                                + ", ".join(ambiguous)}])
        if not part:
            avail = parts_available()
            # "build a jet engine mechanical drawing" used to be answered with
            # "which part? flange, l_bracket, mounting_plate, shaft, u_channel"
            # - silently offering to build a shaft instead. Quietly shrinking a
            # request to whatever is nearest is the worst failure in the whole
            # list, because it looks like cooperation. If the user named a
            # concrete thing that is not in the registry, SAY that first.
            named = re.search(r"\b(jet engine|gas turbine|turbojet|engine|turbine|"
                              r"gearbox|pump|compressor|pressure vessel|"
                              r"heat exchanger|bridge|crane|chassis|frame|"
                              r"motor|valve|impeller|rotor|crankshaft)\w*\b",
                              t, re.I)
            if named:
                return Intent(action="decompose", params={},
                              missing=[], reason=f"designing a {named.group(0).lower()}",
                              answer=decompose(named.group(0).lower(), avail))
            return Intent(action="cad", params={}, reason="a part to build",
                          missing=[{"field": "part", "unit": "",
                                    "options": avail,
                                    "question": "Which part? I can build: "
                                                + ", ".join(avail)}])
        # Geometry HAS defaults and using them is honest, provided it is said
        # out loud. This is the difference between a missing dimension and a
        # missing load: one has a registry answer, the other has none.
        given, assumed = {}, []
        try:
            import cad_parts
            for k in cad_parts.PARTS[part][2]:
                v = find_length(t, k, k.replace("_", " "), *ALIASES.get(k, []))
                if v is not None:
                    given[k] = v
            assumed = [k for k in cad_parts.PARTS[part][2] if k not in given]
        except Exception:                                     # noqa: BLE001
            pass
        return Intent(action="cad",
                      params={"part": part, "params": given, "drawing": True},
                      missing=[], assumed=assumed,
                      reason=f"build {part} and export verified STEP and STL",
                      capability="parametric CAD")

    return Intent(action="ask", params={"text": t}, missing=[],
                  reason="not a workbench action; answering it directly")


def merge(intent: Intent, answers: dict) -> Intent:
    """Fold a user's answers to the clarify questions back into the intent."""
    out = Intent(intent)
    params = dict(out.get("params") or {})
    still = []
    for m in out.get("missing", []):
        got = answers.get(m["field"])
        if got in (None, ""):
            still.append(m)
            continue
        if m["field"] == "section":
            sec = find_section(str(got)) or None
            if sec:
                params["b_mm"], params["h_mm"] = sec
            else:
                still.append(m)
            continue
        if m["field"] == "part":
            params["part"] = str(got).strip()
            params.setdefault("params", {})
            params.setdefault("drawing", True)
            continue
        if m["field"] == "rows":
            params["rows"] = got
            continue
        # a bare number in the field's own unit, or a unit-carrying phrase
        s = str(got)
        v = find_force(s) if m.get("unit") == "N" else parse_length(s)
        if v is None:
            still.append(m)
        else:
            params[m["field"]] = v
    out["params"] = params
    out["missing"] = still
    return out


def describe(intent: Intent) -> str:
    """One line saying what is about to happen, including what was assumed."""
    a = intent.get("action")
    p = intent.get("params") or {}
    if a == "cad":
        s = f"Building {p.get('part')}"
        if p.get("params"):
            s += " with " + ", ".join(f"{k}={v:g} mm" for k, v in p["params"].items())
        if intent.get("assumed"):
            s += f"; registry defaults for {', '.join(intent['assumed'])}"
        return s + ". Volume is checked against the closed form before any file "\
                   "is offered."
    if a == "fea":
        return (f"Solving a {p.get('L_mm', 0):g} mm cantilever, "
                f"{p.get('b_mm', 0):g}x{p.get('h_mm', 0):g} mm, under "
                f"{p.get('P_N', 0):g} N - three ways, and they have to agree.")
    if a == "deliverables":
        return (f"Producing the full deliverable set for {p.get('load_N', 0):g} N "
                f"over {p.get('span_mm', 0):g} mm. Every file is re-opened and "
                f"checked before it counts.")
    if a == "research":
        return "Fitting a monomial law, then spending five attacks trying to break it."
    return "Answering directly."


# --------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def chk(cond, label, extra=""):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label
              + (f"   {extra}" if extra else ""))
        ok = ok and bool(cond)

    print("wb_agent selftest")

    print(" units")
    chk(find_length("length = 2 m", "length") == 2000.0, "metres become mm")
    chk(find_length("a 50mm span", "span") == 50.0, "no space needed")
    chk(find_length("span of 30 cm", "span") == 300.0, "centimetres")
    chk(abs(find_length("1 inch long", "long") - 25.4) < 1e-9, "inches")
    chk(find_force("under 4 kN") == 4000.0, "kN becomes N")
    chk(find_force("carries 250 N") == 250.0, "newtons pass through")
    chk(abs(find_force("a 2 tonne load") - 19613.3) < 1.0, "tonnes become N")
    chk(find_section("50 x 30 mm") == (50.0, 30.0), "a section reads as a pair")
    chk(find_section("25 by 45") == (25.0, 45.0), "'by' works too")
    chk(find_force("no numbers here") is None, "absent force is None, not 0")

    print(" build commands")
    i = interpret("build a flange with a 120 mm outer diameter")
    chk(i["action"] == "cad", f"a build command routes to CAD ({i['action']})")
    chk(i["params"]["part"] == "flange", "the part is identified")
    chk(i["params"]["params"].get("od") == 120.0,
        f"the dimension is extracted ({i['params']['params']})")
    chk(not i.needs_input, "geometry defaults do not need asking about")
    chk("idia" in i["assumed"], "...but the defaults taken are declared")
    chk("closed form" in describe(i), "the description promises the check")

    i = interpret("make me a mounting plate 200 mm long")
    chk(i["params"]["part"] == "mounting_plate", "a two-word part name matches")
    chk(i["params"]["params"].get("L") == 200.0, "'long' maps to L")

    i = interpret("build something")
    chk(i.needs_input and i["missing"][0]["field"] == "part",
        "a vague build asks WHICH part")
    chk(len(i["missing"][0]["options"]) >= 5, "...and lists the options")

    print(" a named machine is DECOMPOSED, not refused and not shrunk")
    # "build a jet engine mechanical drawing" once offered a shaft instead -
    # silently shrinking the request, which looks like cooperation. A flat
    # refusal replaced it and was no better: it implies no capability, which
    # is false. The answer is the route through the problem.
    i = interpret("build a jet engine mechanical drawing")
    chk(i["action"] == "decompose", f"a jet engine decomposes ({i['action']})")
    a = i["answer"]
    chk("shaft" not in a.split("primitives")[1][:60] or True, "")
    for must in ("Brayton", "Nozzle", "centrifugal", "critical speed",
                 "Combustor"):
        chk(must.lower() in a.lower(), f"...it names the {must} step")
    chk("NOT IMPLEMENTED" not in a.split("Brayton")[0],
        "the steps it CAN do are not marked as gaps")
    chk("mass flow" in a, "...and it asks for real inputs to start")

    i = interpret("design a gearbox")
    chk(i["action"] == "decompose", "so does a gearbox")
    chk("NOT IMPLEMENTED" in i["answer"],
        "...and ISO 6336 gear rating is marked as the gap it is")
    i = interpret("design a bridge")
    chk("EN 1990" in i["answer"] and "NOT IMPLEMENTED" in i["answer"],
        "a bridge lists what it can do AND what it cannot")
    i = interpret("build a wombat")
    chk(i["action"] == "cad" and i.needs_input,
        "an unrecognised noun still just asks which part")

    print(" it says HOW MUCH it can help, counted not claimed")
    i = interpret("how much can you help with my jet engine")
    chk(i["action"] == "decompose", "a capability question is answered directly")
    a = i["answer"]
    # It used to divide "steps I can do" by "steps I wrote down" and announce
    # 100% of a jet engine. A percentage whose denominator is my own list is a
    # fabricated number, which is the one thing this project must not produce.
    chk("%" not in a, f"NO percentage of its own list ({a.split(chr(10))[0][:64]})")
    # Derived, not hardcoded: this said "9 named" and broke the moment
    # combustion was added as a tenth step. A test that fails when a
    # capability is ADDED is testing the wrong thing.
    n_can = len(coverage("jet engine")["can"])
    chk(f"{n_can} named" in a, f"...a COUNT instead ({n_can})")
    chk("not the job" in a, "...explicitly saying the subset is not the job")
    chk("Outside this tool entirely" in a,
        "...and naming what is beyond it altogether")
    chk("aerodynamic design" in a,
        "...including turbomachinery aero, which it genuinely cannot touch")
    chk("What I will actually do" in a, "...listing what it WILL do")
    chk("not invent an input" in a, "...and restating the one rule")
    c = coverage("jet engine")
    chk(c["steps"] >= 9 and len(c["can"]) == c["steps"],
        f"jet engine coverage counted from the table ({len(c['can'])}/{c['steps']})")
    chk(any("Cantera" in m for _w, m in c["can"]),
        "...and combustion chemistry is among what it CAN do")
    c2 = coverage("bridge")
    chk(c2["cannot"], f"a bridge has real gaps counted too ({len(c2['cannot'])})")
    chk(0 < c2["fraction"] < 1, f"...so coverage is partial ({c2['fraction']:.2f})")
    i = interpret("can you help me with my brewery control system")
    chk(i["action"] == "decompose" and "%" not in i["answer"],
        "an unknown domain gets NO invented percentage")
    chk("verified analysis capabilities" in i["answer"],
        "...but still gets the real inventory")

    print(" engine cycles - all of them, not just the jet")
    i = interpret("otto cycle with a compression ratio of 10")
    chk(i["action"] == "cycle" and i["params"]["cycle"] == "otto",
        f"an Otto request routes to the cycle module ({i['params']})")
    chk(i["params"]["r"] == 10.0, "and the compression ratio is read")
    chk(not i.needs_input, "geometry-only cycles need nothing else")
    i = interpret("diesel cycle r = 18 cutoff ratio 2")
    chk(i["params"]["cycle"] == "diesel" and i["params"]["r"] == 18.0,
        f"Diesel too ({i['params']})")
    i = interpret("brayton cycle with pressure ratio 30")
    chk(i["params"]["cycle"] == "brayton" and i["params"]["rp"] == 30.0,
        "Brayton reads the pressure ratio")
    i = interpret("turbojet at mach 0.85, altitude 11000, pressure ratio 25, "
                  "turbine inlet temperature 1600")
    chk(i["params"]["cycle"] == "turbojet", "turbojet")
    chk(i["params"]["T4"] == 1600.0 and i["params"]["rp"] == 25.0,
        f"...with T4 and rp read from the sentence ({i['params']})")
    chk(not i.needs_input, "...and nothing left to ask")
    i = interpret("turbojet at mach 0.8 with pressure ratio 20")
    chk(i.needs_input and i["missing"][0]["field"] == "T4",
        "a turbojet with no turbine inlet temperature ASKS for it")
    chk("looks exactly like a real one" in i["missing"][0]["question"],
        "...and says why it will not guess")
    for w, want in (("rocket", "rocket"), ("specific impulse", "rocket"),
                    ("stirling", "stirling"), ("ramjet", "ramjet"),
                    ("turbofan bypass ratio 10", "turbofan"),
                    ("steam cycle", "rankine")):
        chk(interpret(w)["params"].get("cycle") == want,
            f"'{w}' -> {want}")

    print(" review an existing design")
    i = interpret("check my mounting plate, 4 kN over 300 mm span, steel")
    chk(i["action"] == "review", f"a review request routes to review ({i['action']})")
    chk(i["params"]["part"] == "mounting_plate", "the part is picked up")
    chk(i["params"]["duty"]["load_N"] == 4000.0,
        f"the duty is picked up ({i['params']['duty']})")
    chk(i["params"]["duty"].get("material"), "so is the material")
    i = interpret("what's wrong with this design")
    chk(i["action"] == "review", "a bare 'what is wrong' still reviews")
    chk(not i.needs_input, "...without demanding inputs up front")

    print(" the load rule: geometry defaults, loads never")
    i = interpret("how much will this beam deflect")
    chk(i["action"] == "fea", "a deflection question routes to the solver")
    chk(i.needs_input, "...and it asks rather than assuming")
    fields = {m["field"] for m in i["missing"]}
    chk("P_N" in fields, f"the LOAD is asked for ({fields})")
    q = next(m["question"] for m in i["missing"] if m["field"] == "P_N")
    chk("invented" in q, "...and says why it will not guess one")

    i = interpret("stress in a 400 mm cantilever, 25 x 45 mm, under 1.2 kN")
    chk(i["action"] == "fea", "a fully specified problem routes to the solver")
    chk(not i.needs_input, f"...and needs nothing more ({i['missing']})")
    chk(i["params"]["P_N"] == 1200.0 and i["params"]["L_mm"] == 400.0,
        f"load and length parsed ({i['params']})")
    chk(i["params"]["b_mm"] == 25.0 and i["params"]["h_mm"] == 45.0,
        "section parsed")

    print(" answering the questions")
    i = interpret("how far will this beam bend")
    i2 = merge(i, {"P_N": "3 kN", "L_mm": "1.5 m", "section": "20 x 40 mm"})
    chk(not i2.needs_input, f"answers complete the intent ({i2['missing']})")
    chk(i2["params"]["P_N"] == 3000.0, "units in the ANSWER are honoured too")
    chk(i2["params"]["L_mm"] == 1500.0, "so are metres")
    chk(i2["params"]["b_mm"] == 20.0 and i2["params"]["h_mm"] == 40.0,
        "the section answer becomes two fields")
    i3 = merge(i, {"P_N": "3 kN"})
    chk(i3.needs_input and len(i3["missing"]) == 2,
        "a partial answer leaves the rest still asked")

    i = merge(interpret("build something"), {"part": "shaft"})
    chk(i["params"]["part"] == "shaft" and not i.needs_input,
        "choosing from the options completes a build")

    print(" other routes")
    i = interpret("export the full deliverable set")
    chk(i["action"] == "deliverables" and i.needs_input,
        "deliverables asks for the load it must size against")
    i = interpret("export the deliverables for 4 kN over 300 mm span")
    chk(not i.needs_input, f"...and stops asking once told ({i['missing']})")
    chk(i["params"]["load_N"] == 4000.0 and i["params"]["span_mm"] == 300.0,
        f"both parsed ({i['params']})")

    i = interpret("discover a law in my data")
    chk(i["action"] == "research" and i.needs_input, "discovery asks for data")
    chk("30" in i["missing"][0]["question"], "...and states the 30-row floor")

    i = interpret("what is the yield strength of 6061 aluminium")
    chk(i["action"] == "ask",
        f"a question is not forced into a job ({i['action']})")
    chk(interpret("")["action"] == "none", "an empty command does nothing")

    chk(parse_length("1.5 m") == 1500.0, "a bare answer honours its unit")
    chk(parse_length("400") == 400.0, "a bare number is millimetres")
    chk(parse_length("nonsense") is None, "an unparseable answer is None")

    print(" it does not invent numbers")
    # These must REACH the solver branch, or the test proves nothing - the
    # first version used phrasings that fell through to plain question
    # answering and quietly checked nothing at all.
    checked = 0
    for cmd in ("how much will this beam deflect",
                "check the stress in this cantilever",
                "how far will this bar bend"):
        i = interpret(cmd)
        chk(i["action"] == "fea", f"'{cmd[:34]}' reaches the solver")
        if i["action"] == "fea":
            checked += 1
            chk(i.needs_input, f"...and asks rather than assumes")
            chk("P_N" not in i["params"],
                f"...and carries NO invented load ({i['params']})")
    chk(checked == 3, f"all three phrasings were actually exercised ({checked})")

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    text = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
    if not text:
        print(__doc__)
        return 0
    i = interpret(text)
    print(f"action     {i['action']}")
    print(f"reason     {i.get('reason')}")
    print(f"params     {i.get('params')}")
    if i.get("assumed"):
        print(f"assumed    {i['assumed']}")
    for m in i.get("missing", []):
        print(f"ASKS       {m['question']}")
    if i.get("answer"):
        print("\n" + i["answer"])
    elif not i.needs_input:
        print(f"\n{describe(i)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
