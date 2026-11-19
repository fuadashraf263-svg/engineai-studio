#!/usr/bin/env python3
"""
scope.py - decide whether a request is inside EngineAI's competence, and refuse
cleanly when it is not.

WHY THIS IS A MODULE AND NOT A PROMPT
-------------------------------------
The documented failure of general agents is not that they get engineering wrong.
It is that they answer ANYTHING, and the answer looks the same whether the system
knew or guessed. Published testing of Manus records it producing "curl responses,
file listings, and status checks formatted like real server output but describing a
state that did not exist", and not admitting the error when challenged.

That failure has a specific shape: the system had no capability, produced output
anyway, and the output was indistinguishable from a real one. A refusal cannot be a
matter of tone or intention - it has to be enforced by something that knows what
capabilities actually exist. This module is that something. It reads the LIVE tool
registry and capability probes rather than a hand-written list, so it cannot drift
out of date as the system grows.

THE THREE OUTCOMES
------------------
    IN_SCOPE      a named capability covers this; proceed, and produce artifacts
    PARTIAL       adjacent capability exists but the specific ask is not covered;
                  say exactly what can and cannot be done
    OUT_OF_SCOPE  refuse, name the domain, and do not produce a plausible answer

A fourth outcome - fluent prose with no artifact behind it - is the failure mode
this file exists to make impossible.
"""
from __future__ import annotations
import argparse, json, re


# Domains EngineAI genuinely covers, each tied to the module that implements it.
# Keys are matched against the request; the module is IMPORTED to confirm it is
# real, so a deleted module downgrades the claim automatically.
CAPABILITY_MAP = {
    "beam|cantilever|deflection|bending|simply supported|udl":
        ("structural", "beam design and deflection"),
    "column|buckling|strut|slenderness|euler":
        ("column_design", "compression members and buckling"),
    "steel|IPE|HEA|HEB|SHS|CHS|eurocode|EN 1993":
        ("steel_design", "steel member design to EN 1993-1-1"),
    "bolt|fastener|thread|torque|preload":
        ("standards", "bolts and threaded fasteners"),
    "load combination|factored|gamma|ULS|SLS|EN 1990|imposed load":
        ("loads", "EN 1990 load combinations"),
    "torsion|shaft|pressure vessel|hoop|stress concentration|thermal stress|"
    "lame|thick wall|thin wall|uplift|overhang|reaction|propped|fixed-fixed|"
    "off-centre|off-center|kt|notch":
        ("mechanics", "mechanics of materials"),
    # "cycles" and "welded detail" are how the request is actually phrased. The
    # first version listed only "fatigue", so "how many cycles will this welded
    # detail survive" fell through to unrecognised and was refused - the gate
    # failing closed, which is safe but costs real capability.
    "fatigue|s-n|miner|crack|fracture|paris|endurance|cycles|fatigue life|"
    "welded detail|detail category|goodman|gerber|soderberg|mean stress|"
    "basquin|fat |stress range|butt weld|weld crack":
        ("fatigue", "fatigue life and fracture mechanics"),
    "heat|thermal|conduction|convection|radiation|fin|heat exchanger|temperature":
        ("thermal", "heat transfer"),
    # Geotechnics moved out of KNOWN_GAPS when geotech.py was written. The
    # keywords are deliberately the ANALYSES that module actually performs -
    # bearing capacity, Rankine pressures, consolidation - and NOT "foundation
    # design", "pile" or "slope stability", which stay gaps below. Claiming a
    # whole subject because part of it started computing is precisely the drift
    # this map exists to prevent.
    "bearing capacity|terzaghi|rankine|earth pressure|active pressure|"
    "passive pressure|at-rest pressure|jaky|effective stress|pore pressure|"
    "consolidation|void ratio|porosity|submerged unit weight|seepage|"
    "hydraulic gradient|quicksand|piping|preconsolidation|overconsolidat|"
    "friction angle|cohesionless|bearing pressure":
        ("geotech", "soil mechanics: bearing capacity, earth pressure, consolidation"),
    "flow|pipe|pressure drop|reynolds|drag|bernoulli|nozzle|channel|manning|"
    "blasius|boundary layer|colebrook|friction factor|lift coefficient|"
    "angle of attack|induced drag|froude|terminal velocity|normal depth|"
    "cumec|open channel|airfoil|aerofoil lift":
        ("fluid_mech", "fluid mechanics"),
    "cfd|navier|turbulen|openfoam":
        ("cfd", "computational fluid dynamics"),
    "magnetic|solenoid|coil|inductance|capacitance|skin depth|eddy":
        ("emag", "electromagnetics"),
    "circuit|resistor|capacitor|op-amp|opamp|rectifier|transistor|spice|filter|"
    "rc low|rc high|low pass|high pass|rlc|resonance|cutoff|thevenin|norton|"
    "bjt|common emitter|divider|power transfer|bridge|coax|impedance|"
    "time constant|bode|gain":
        ("circuits_ext", "circuit analysis"),
    "cad|solid model|step file|stl|3d model|part model":
        ("cad_parts", "parametric CAD"),
    "drawing|dimension|gd&t|tolerance|orthographic|blueprint":
        ("drawings_dim", "dimensioned engineering drawings"),
    "fea|finite element|mesh|calculix|stress analysis":
        ("fea", "finite element analysis"),
    "lattice|unit cell|gibson|relative density|architected":
        ("lattice", "architected lattice materials"),
    "superconduct|rebco|cryo|critical current|magnet":
        ("superconductor", "superconducting magnet design"),
    "phase diagram|calphad|eutectic|equilibrium|alloy":
        ("phase_diagram", "CALPHAD phase equilibria"),
    "pendulum|multibody|rigid body|linkage|dynamics simulation":
        ("multibody", "multibody dynamics"),
    "material propert|modulus|yield|density|steel grade|aluminium":
        ("materials_db", "material properties"),
    "equation|formula|constant|codata":
        ("equations", "the verified equation library"),
    "spreadsheet|xlsx|report|docx|pdf|deliverable|bom|design package|"
    "full package|dossier|design pack|receipt|plot|chart|graph|"
    "stress field|figure":
        ("deliverables", "engineering deliverable files"),
    # Added after the 300-activity sweep. Each of these is something EngineAI
    # genuinely computes that the first keyword pass did not recognise, so the
    # request fell through and was answered as if unverified - the gate failing
    # closed, which is safe but silently costs real capability. Breadth of PHRASING
    # matters as much as breadth of physics.
    "volt drop|voltage drop|cable siz|conductor siz|ampacity|awg|cable":
        ("pipe_cable", "cable and conductor sizing"),
    "rolling|incline|spring mass|damper|damping ratio|natural frequency|"
    "oscillat|vibration|log decrement":
        ("multibody", "dynamics and vibration"),
    "lever rule|tie line|phase fraction|which phases|stable phase":
        ("phase_diagram", "CALPHAD phase equilibria"),
    "read this|what is in this|identify this file|open this|"
    "extract from|this file|this photo|this drawing|this scan|attached":
        ("fileread", "reading and identifying files"),
    "busbar|current to stress|joule heating|electro-thermal|coupled|"
    "multiphysics|thermal runaway":
        ("multiphysics", "coupled multiphysics chains"),
    "clamped plate|plate deflection|wind pressure|pressure on a plate":
        ("multiphysics", "plate theory and one-way FSI"),
}

# Domains EngineAI does NOT cover. Naming them explicitly is the point: a refusal
# that says "I do not do travel planning" is useful; silence or a plausible
# itinerary is not.
OUT_OF_SCOPE = {
    "travel|itinerary|flight|hotel|holiday|vacation|trip to":
        "travel planning",
    "stock|share price|invest|portfolio|ticker|earnings|valuation|crypto":
        "financial analysis and investment advice",
    "resume|cv|cover letter|job applicat|interview slot|recruit|hiring":
        "recruitment and career services",
    "slide|powerpoint|pptx|deck|presentation|pitch":
        "presentation and slide generation",
    "website|landing page|web app|frontend|react|html site|seo":
        "web development and marketing",
    "recipe|cook|meal plan|diet|nutrition|workout|fitness":
        "food, health and lifestyle",
    "legal|contract|lawsuit|patent filing|compliance filing|tax":
        "legal and tax advice",
    "medical|diagnos|patient|prescri|symptom|treatment|clinical|hurts|"
    "pain|injur|doctor|illness|disease":
        "medical advice",
    "essay|blog post|social media|copywriting|newsletter|ad campaign":
        "general content writing and marketing",
    "translate|translation|language learning":
        "translation and language services",
}

# Engineering topics that are real but which this system does not implement.
# These get PARTIAL, not OUT_OF_SCOPE - the distinction matters, because the honest
# answer is "that is engineering and I cannot do it", not "that is not engineering".
KNOWN_GAPS = {
    "reinforced concrete|rebar|concrete beam|concrete slab|concrete column|EN 1992|ACI 318":
        "reinforced concrete design (EN 1992 / ACI 318) is not implemented",
    "timber design|timber beam|timber joist|glulam|EN 1995":
        "timber design (EN 1995) is not implemented",
    "masonry|brickwork|EN 1996": "masonry design (EN 1996) is not implemented",
    "composite deck|decking|shear connector|EN 1994|composite slab":
        "composite steel-concrete design (EN 1994) is not implemented - shear "
        "connector degree, effective width and profiled decking are all absent",
    "seismic|earthquake|response spectrum|EN 1998":
        "seismic design (EN 1998 / ASCE 7) is not implemented",
    "wind load|EN 1991-1-4|terrain category":
        "wind actions (EN 1991-1-4) are not implemented - a wind PRESSURE can be "
        "turned into a structural load, but the code's terrain, exposure and shape "
        "factors are not",
    # Narrowed when geotech.py landed. Shallow-footing bearing capacity, Rankine
    # earth pressure and 1D consolidation settlement now COMPUTE, so listing them
    # as gaps would refuse work the system can do. What remains absent is real
    # and named: deep foundations, slope stability, and the EN 1997 design
    # procedure (partial factors, design approaches) as opposed to the mechanics.
    "pile|slope stability|bishop|fellenius|caisson|sheet pile|EN 1997|"
    "soil nail|ground anchor|liquefaction":
        "deep-foundation and slope-stability geotechnical design (EN 1997) is not "
        "implemented; shallow-footing bearing capacity, Rankine earth pressure and "
        "1D consolidation settlement are, in geotech.py",
    "weld design|fillet weld|weld group|weld throat|weld sizing":
        "weld design to EN 1993-1-8 is not implemented; weld FATIGUE detail "
        "categories are",
    "gear|bearing life|spline|cam|clutch|brake":
        "machine element rating (ISO 6336 gears, ISO 281 bearings) exists only as "
        "equation-library formulas, not as a design procedure",
    "aisc|american institute of steel|asd|lrfd":
        "AISC 360 is not implemented - only EN 1993",
    "assembly|exploded view|kinematic mechanism|mate":
        "CAD assemblies are not implemented; single parts are",
    # The line here is cost ESTIMATION from geometry (a real gap) versus putting
    # unit costs the user supplies into a BOM (which deliverables.py does). The
    # first narrowing of this entry cut "cost" down so far that "what will this
    # part cost to manufacture" stopped being recognised as engineering at all -
    # over-correcting a shadow into a hole.
    "cost estimation|manufacturing cost|cost to manufacture|cost to make|"
    "part cost|how much to manufacture|how much will it cost to make|"
    "unit price|cost model|costing":
        "cost estimation is not implemented",
    "cam|toolpath|g-code|machining strategy":
        "CAM and toolpath generation are not implemented",
}


# Fragments deliberately written as STEMS, which must keep a loose tail so
# 'diagnos' catches 'diagnosis' and 'turbulen' catches 'turbulence'. Everything
# else is a whole word and gets a closing boundary.
_STEMS = {"diagnos", "prescri", "injur", "turbulen", "crystallis", "compliance",
          "material propert", "recruit", "copywrit", "invest", "valuat",
          "cost estimat", "oscillat", "cable siz", "conductor siz", "superconduct",
          "manufactur", "reinforc", "estimat", "electr", "magnet"}


def _frag_re(frag):
    """Word-boundary match at BOTH ends, unless the fragment is a declared stem.

    The original gave long fragments an open tail so stems would work. That let
    'react' - declared for web development - match 'reactions', and a question
    about beam reactions and uplift was routed to web development and marketing.
    An open-ended prefix match is a substring match wearing a boundary on one side.

    Short fragments still allow a plural, so 'bolt' catches 'bolts'.
    """
    f = frag.lower()
    if f in _STEMS:
        return re.compile(r"\b" + re.escape(f))
    tail = r"(?:s|es)?\b"
    return re.compile(r"\b" + re.escape(f) + tail)


_RE_CACHE = {}


# Verbs and framings that mean the user wants a PRODUCT RECOMMENDATION, an OPINION
# or an EXPLANATION - not a calculation. Without this, "recommend a laptop for CAD
# work" matched the word "CAD" and EngineAI claimed it would run a parametric CAD
# solver on it. Over-claiming on an incidental engineering noun is the same failure
# as answering out of domain, and it is more dangerous because it looks competent.
NON_COMPUTE_INTENT = re.compile(
    r"\b(recommend|suggest|which (one|brand|laptop|software|tool|supplier)|"
    r"what (laptop|computer|software|brand|company|supplier)|best (laptop|software|"
    r"tool|brand|way to buy)|should i buy|where can i buy|how much does .* cost|"
    r"review of|opinion on|compare .* (brands|vendors|products))\b", re.I)

# Framings that mean the user wants a CONCEPT EXPLAINED rather than a number
# computed. "Explain what lateral-torsional buckling is" is a teaching request; the
# right response is a definition, not a solver invocation.
EXPLAIN_INTENT = re.compile(
    r"\b(explain|what (is|are|does) .*(mean|\?)?|what'?s the (difference|point)|"
    r"why (does|do|is|are)|how does .* work|tell me about|teach me|"
    r"help me understand|in simple terms)\b", re.I)


# --------------------------------------------------------------------------- #
# Standards register.
#
# A request that NAMES a standard is making a specific claim about what it wants.
# Matching a keyword and claiming the capability without checking the standard is
# how "pressure vessel to ASME VIII" came back as COMPUTED when only thin-wall
# membrane theory exists. Naming a standard EngineAI does not implement must
# override any keyword match - the noun is not the deliverable, the code is.
# --------------------------------------------------------------------------- #
IMPLEMENTED_STANDARDS = {
    "en 1993-1-1": "steel members", "en 1993-1-8": "bolt shear only",
    "en 1993-1-9": "fatigue detail categories", "en 1993-1-2": "steel at temperature",
    "en 1990": "load combinations", "en 1991-1-1": "imposed loads",
    "en 10365": "section tables", "en 10210": "hollow sections",
    "iso 898-1": "bolt classes", "iso 261": "threads", "iso 724": "thread geometry",
    "iso 2768": "general tolerances", "iso 1101": "GD&T frames",
    "iso 129": "dimensioning", "iso 5459": "datums", "iso 128": "centre marks",
    "iso 2533": "standard atmosphere", "asme b36.10": "pipe schedules",
    "astm b258": "AWG conductors", "astm a536": "ductile iron",
    "iapws": "steam properties", "codata": "physical constants",
}

UNIMPLEMENTED_STANDARDS = {
    "asme viii": "pressure vessel design", "asme b31": "process piping",
    "en 13445": "unfired pressure vessels",
    "en 1992": "reinforced concrete", "aci 318": "reinforced concrete",
    "en 1994": "composite steel-concrete", "en 1995": "timber",
    "en 1996": "masonry", "en 1997": "geotechnical", "en 1998": "seismic",
    "en 1999": "aluminium", "en 1991-1-3": "snow loading",
    "en 1991-1-4": "wind loading", "asce 7": "US loading and seismic",
    "aisc 360": "US steel design", "aashto": "pavement and bridge design",
    "iso 281": "rolling bearing life", "iso 6336": "gear rating",
    "din 743": "shaft fatigue", "agma": "gear rating",
    "en 10270": "spring wire", "vdi 2230": "bolted joint preload",
    "iec 60909": "short circuit currents", "iec 60364": "electrical installations",
    "iec 60034": "rotating machines", "iec 60076": "transformers",
    "iec 62305": "lightning protection", "ieee 80": "substation earthing",
    "ieee 519": "harmonic limits", "ieee 1584": "arc flash",
    "en 12464": "lighting", "api 520": "relief sizing", "api 521": "flare systems",
    "nace mr0175": "sour service materials", "mil-hdbk-217": "reliability prediction",
    "iso 10993": "biocompatibility", "iso 14040": "life cycle assessment",
    "iso 10816": "vibration severity", "iso 1940": "balance grades",
    "tema": "shell and tube exchangers", "endf": "nuclear cross sections",
}

# Engineering subject matter EngineAI does not implement, recognised so it can be
# NAMED as an engineering gap rather than lumped in with holiday planning. Being
# told "that is geotechnics and I do not do geotechnics" is a useful answer;
# being told "that is outside what I verify" is not.
ENGINEERING_SUBJECTS = {
    "reinforced concrete": "reinforced concrete design (EN 1992 / ACI 318)",
    "concrete beam": "reinforced concrete design", "concrete slab": "RC slab design",
    "concrete column": "RC column design",
    "rebar": "reinforced concrete design", "reinforcement": "reinforced concrete design",
    "prestress": "prestressed concrete", "timber design": "timber design (EN 1995)", "timber joist": "timber design",
    "timber beam": "timber design",
    "masonry": "masonry design (EN 1996)", "brickwork": "masonry design",
    # "footing", "settlement", "earth pressure" and "retaining wall" were removed
    # from this list when geotech.py landed: an UNIMPLEMENTED subject outranks a
    # capability match, so leaving them here would have refused the very
    # calculations that module now performs. "foundation design" is kept but
    # narrowed to the phrase, because sizing and detailing a foundation is still
    # absent even though its bearing capacity now computes.
    "foundation design": "full foundation design to EN 1997",
    "raft foundation": "raft foundation design",
    "pile": "pile design",
    "slope stability": "slope stability analysis", "bishop": "slope stability",
    "retaining wall design": "retaining wall design (stability and detailing)",
    "seismic": "seismic design (EN 1998 / ASCE 7)", "earthquake": "seismic design",
    "bridge design": "bridge design", "bridge deck": "bridge design",
    "wind load": "wind actions (EN 1991-1-4)", "snow": "snow actions (EN 1991-1-3)",
    "pavement": "pavement design",
    "weld design": "weld design (EN 1993-1-8 section 4)",
    "fillet weld": "weld sizing", "weld throat": "weld sizing",
    "weld group": "weld group design",
    "prying": "connection component method", "block shear": "connection design",
    "bolt bearing": "connection design", "end plate": "connection design",
    "gear": "gear rating (ISO 6336)", "bearing life": "bearing rating (ISO 281)",
    # "spring" alone was too broad - it shadowed spring-mass-damper dynamics, which
    # EngineAI does compute. A subject list that blocks real capability is as bad
    # as one that misses a gap, so these are narrowed to the actual design task.
    "spring design": "helical spring design", "spring rate": "spring design",
    "wahl": "spring design", "cam": "cam and follower design",
    "clutch": "clutch and brake design", "brake": "brake design",
    "gearbox": "gearbox design", "chain drive": "chain drive design",
    "distillation": "distillation column design",
    "absorption": "separation process design", "extraction": "separation design",
    "crystallis": "crystallisation", "relief valve": "relief sizing (API 520)",
    "flare": "flare system design", "dispersion": "consequence modelling",
    "pinch analysis": "heat integration", "corrosion": "corrosion assessment",
    "protection coordination": "protection coordination",
    "short circuit": "fault analysis (IEC 60909)",
    "load flow": "power flow analysis", "power flow": "power flow analysis",
    "motor starting": "motor starting analysis",
    "harmonic": "harmonic analysis", "earthing": "earthing design",
    "lightning": "lightning protection", "transformer": "transformer design",
    "inverter": "power electronics", "converter": "power electronics",
    "antenna": "RF and antenna design", "microwave": "RF design",
    "illumination": "lighting design", "luminaire": "lighting design",
    "photovoltaic": "PV system design", "wind turbine": "wind turbine design",
    "manufacturability": "DFM assessment", "dfm": "DFM assessment",
    "toolpath": "CAM toolpath generation", "cnc": "CAM",
    "tolerance stack": "tolerance stack-up analysis",
    "weibull": "reliability analysis", "fmea": "failure mode analysis",
    "fault tree": "fault tree analysis",
    "life cycle assessment": "LCA", "lca": "LCA",
    # "cost" alone blocked "generate a BOM with costs", which EngineAI produces.
    # The gap is COST ESTIMATION from geometry, not the word appearing anywhere.
    "cost estimat": "cost estimation from geometry",
    "manufacturing cost": "cost estimation", "quote": "cost estimation",
    "assembly": "CAD assemblies",
    "exploded view": "assembly drawings", "bom explosion": "assembly BOM",
    "aerofoil": "aerodynamic design", "airfoil": "aerodynamic design",
    "orbital": "orbital mechanics", "rocket": "propulsion design",
    "turbine blade": "turbomachinery design", "compressor map": "turbomachinery",
    "hull": "naval architecture", "seakeeping": "naval architecture",
    "neutron": "nuclear analysis", "oil reservoir": "petroleum engineering",
    "reservoir simulation": "petroleum engineering",
    "haz": "aluminium HAZ softening (EN 1999)",
}

# Things no engineering tool may do, whatever it can compute.
PROHIBITED = {
    "sign off": "professional sign-off requires a licensed engineer's seal",
    "certify": "certification requires a licensed engineer",
    "stamp": "stamping drawings requires a licensed engineer",
    "approve for construction": "construction approval requires a licensed engineer",
    "guarantee": "no analysis tool can guarantee a design",
}

# Research requests. These are no longer refused.
#
# The old rule sent every one of these to a blanket "EngineAI does not invent".
# That was wrong in one direction and right in the other, and the two cases
# have to be separated rather than collapsed:
#
#   RESEARCHABLE  a question that can be attacked with data and solvers -
#                 fit a law, search a design space, test a published claim,
#                 map a Pareto front. research.py runs it and the falsifier
#                 decides whether the answer survives. This is real research
#                 and refusing it was the mistake.
#
#   UNRESEARCHABLE  a request for a result with nothing to compute it from:
#                 invent an alloy from thin air, name a breakthrough, write
#                 the paper. There is no data, no candidate and no way to
#                 check the answer, so producing one would be fabrication.
# Deliberately NOT here: bare "study" and "analysis of". Both are ordinary
# names for routine engineering tasks - a "protection coordination study", a
# "short circuit study", a "pinch analysis of a heat network" - and matching
# them would let the research engine shadow named gaps it cannot compute.
# A research verb has to be one that only appears in actual research.
RESEARCHABLE = re.compile(
    r"\b(research|investigate|explore|"
    r"optimi[sz]e|optimi[sz]ation|trade[- ]?off|pareto|design space|"
    r"sensitivity analysis|parameter sweep|what drives|"
    r"which .{0,20}matters most|"
    r"fit a (law|model|curve)|find the (relationship|law|trend|optimum)|"
    r"rediscover|symbolic regression|surrogate|"
    r"does .{0,40}(hold|apply|agree)|test (the|this) (hypothesis|law|claim)|"
    r"correlat\w+|regression|calibrat\w+)\b", re.I)

UNRESEARCHABLE = re.compile(
    r"\b(invent|breakthrough|"
    r"(discover|find me|give me|come up with)\s+(a|an|the)?\s*"
    r"(novel|new|unknown|undiscovered)|"
    r"nobody has (tried|thought|discovered)|"
    r"write (me )?a (journal |research )?paper|"
    r"nobel|patent-?able idea|"
    r"solve (an? )?(unsolved|open) problem)\b", re.I)

# Research verbs are not enough on their own. "What research direction should
# I pursue" and "optimise this across every discipline at once" both contain a
# research verb and neither names anything computable - the first wants career
# advice, the second wants a scope no solver covers. Matching those to the
# research engine would be the same over-claim as answering "ASME VIII" with
# membrane theory.
VAGUE_RESEARCH = re.compile(
    r"\b(research direction|what should i (research|pursue|study|work on)|"
    r"across (every|all) disciplin\w+|all disciplines at once|"
    r"every discipline at once|what should i do next)\b", re.I)

# Kept for callers that still import it; now means "unresearchable" only.
DISCOVERY = UNRESEARCHABLE


def named_standard(text):
    """The standard a request names, and whether EngineAI implements it."""
    t = " " + re.sub(r"[^a-z0-9. -]", " ", (text or "").lower()) + " "
    for s, what in UNIMPLEMENTED_STANDARDS.items():
        if s in t:
            return {"standard": s.upper(), "implemented": False, "covers": what}
    for s, what in IMPLEMENTED_STANDARDS.items():
        if s in t:
            return {"standard": s.upper(), "implemented": True, "covers": what}
    return None


def engineering_subject(text):
    """Engineering subject matter present in the text, implemented or not."""
    t = (text or "").lower()
    for frag, what in ENGINEERING_SUBJECTS.items():
        if frag in t:
            return frag, what
    return None, None


def intent(text):
    """What KIND of response is wanted, independent of the subject matter."""
    if NON_COMPUTE_INTENT.search(text or ""):
        return "recommend"
    if EXPLAIN_INTENT.search(text or ""):
        return "explain"
    return "compute"


def _hit(text, table):
    """Longest matching fragment wins.

    First-match-wins made the verdict depend on dictionary order, which is not a
    property of the request: 'pitch deck for investors' matched 'invest' before
    'deck' purely because financial advice happened to be declared earlier.
    """
    t = text.lower()
    best = (None, None, 0)
    for pattern, val in table.items():
        for frag in pattern.split("|"):
            # Fragments are lowercased when compiled. Several tables were written
            # with uppercase acronyms - ULS, SLS, EN 1993, IPE - and matching them
            # against already-lowercased text meant they could never fire. "give me
            # the ULS and SLS combinations" fell through to unrecognised for that
            # reason alone.
            frag = frag.strip().lower()
            if not frag:
                continue
            rx = _RE_CACHE.get(frag) or _RE_CACHE.setdefault(frag, _frag_re(frag))
            if rx.search(t) and len(frag) > best[2]:
                best = (frag, val, len(frag))
    return best[0], best[1]


def _module_live(name):
    """Confirm the claimed module actually imports. A capability that cannot be
    loaded is not a capability, and saying so is the whole point."""
    try:
        __import__(name)
        return True
    except Exception:
        return False


def classify(request):
    """Route a request. Returns the verdict, the covering capability if any, and -
    when refusing - the reason and what the user should use instead."""
    text = str(request)

    frag_out, domain = _hit(text, OUT_OF_SCOPE)
    frag_gap, gap = _hit(text, KNOWN_GAPS)
    frag_in, cap = _hit(text, CAPABILITY_MAP)

    # An out-of-domain match ALWAYS wins, even when an engineering word appears too.
    # The out-of-scope vocabulary names the DELIVERABLE - blog post, slide deck,
    # website - while the capability vocabulary names only the SUBJECT. A blog post
    # about beam deflection is still a blog post. Weighing the two by fragment
    # length got this backwards, because "heat exchanger" is simply a longer phrase
    # than "slide".
    if domain:
        return {"verdict": "OUT_OF_SCOPE", "domain": domain, "matched": frag_out,
                "capability": None,
                "reason": f"'{domain}' is outside EngineAI's competence. This system "
                          "does engineering analysis, design and deliverables; it has "
                          "no capability here and will not produce an answer that "
                          "looks like one.",
                "recommend": "use a general assistant for this",
                "will_produce_output": False}

    # A KNOWN GAP OUTRANKS A CAPABILITY MATCH. The gap keyword names the material or
    # the code ("timber", "concrete", "AISC"); the capability keyword names only the
    # member type ("beam", "column"). A timber beam is a timber question, and
    # answering it with the steel module would be exactly the confident-wrong output
    # this gate exists to prevent.
    if gap:
        return {"verdict": "PARTIAL", "capability": None, "matched": frag_gap,
                "would_have_matched": cap[1] if cap else None,
                "reason": gap,
                "note": "this IS engineering, and EngineAI cannot do it - which is a "
                        "different statement from it being out of scope"
                        + (f". Note the request also mentions '{frag_in}', which "
                           f"EngineAI does cover, but not for this material or code"
                           if cap else ""),
                "will_produce_output": False}

    # ORDER MATTERS. A named standard, a prohibited act or a discovery request all
    # override a keyword match, because the keyword is only a noun and these are
    # statements about what is actually being asked for.
    for frag, why in PROHIBITED.items():
        if frag in (text or "").lower():
            return {"verdict": "PARTIAL", "domain": "professional certification",
                    "capability": None, "matched": frag,
                    "reason": f"{why}. EngineAI stamps every output UNCERTIFIED and "
                              "will not do this whatever it can compute.",
                    "will_produce_output": False}

    t = text or ""
    # Order matters: a request can contain both words ("research a novel alloy").
    # Only the part with nothing to compute from is refused, and the refusal
    # says what data would turn it into a question EngineAI can actually run.
    if UNRESEARCHABLE.search(t) and not RESEARCHABLE.search(t):
        return {"verdict": "PARTIAL", "domain": "unresearchable request",
                "capability": None, "matched": "invention request",
                "reason": "There is nothing here to compute from. EngineAI runs "
                          "research against data and solvers - it will not produce "
                          "a result it has no way to check, because that is "
                          "fabrication however confident it sounds.",
                "will_produce_output": False,
                "what_it_can_do_instead": "give it a dataset, a candidate design or "
                                          "a hypothesis and it will run the full "
                                          "protocol: pre-register the prediction, "
                                          "audit the data, fit, then attack the "
                                          "result and report what survived"}

    std = named_standard(text)
    if std and not std["implemented"]:
        return {"verdict": "PARTIAL", "domain": std["covers"],
                "capability": None, "matched": std["standard"],
                "reason": f"{std['standard']} ({std['covers']}) is NOT implemented. "
                          "A keyword in the request may overlap something EngineAI "
                          "does compute, but naming a standard it does not have is "
                          "a request it cannot honour, and claiming otherwise would "
                          "be an over-claim.",
                "will_produce_output": False}

    # RESEARCH sits BELOW the standards register on purpose. "Research the
    # short-circuit currents to IEC 60909" is still a request for a standard
    # EngineAI does not implement, and the word "research" must not be able to
    # launder a named gap into a computable question.
    if RESEARCHABLE.search(t) and not VAGUE_RESEARCH.search(t):
        return {"verdict": "RESEARCH", "domain": "research",
                "capability": "research.run", "matched": "research request",
                "reason": "This is a research question. EngineAI runs it through the "
                          "research protocol: data audit for leakage and sentinels, "
                          "pre-registered prediction, law discovery or surrogate "
                          "search, then falsification (unseen data, extrapolation, "
                          "dimensional consistency, shuffled-target null, bootstrap) "
                          "and replication on disjoint halves. Every number is "
                          "iterated to convergence and quoted with a k=2 expanded "
                          "uncertainty.",
                "will_produce_output": True,
                "verdicts_it_may_return": ["DISCOVERED", "APPROXIMATION",
                                           "WEAK_TREND", "DEGRADED", "REJECTED"],
                "note": "a REJECTED result is a real answer, not a failure"}

    subj_frag, subj_what = engineering_subject(text)

    # An UNIMPLEMENTED engineering subject beats a capability keyword. "bolt bearing
    # and block shear" contains "bolt", which EngineAI does compute - but bearing
    # and block shear are connection checks it does not have, and answering the
    # bolt part would be answering a different question from the one asked.
    if subj_what and cap:
        return {"verdict": "PARTIAL", "domain": subj_what,
                "capability": None, "matched": subj_frag,
                "reason": f"{subj_what} is not implemented. The request does mention "
                          f"'{cap[1]}', which EngineAI can compute, but that is a "
                          "different question from the one asked and answering it "
                          "would be an over-claim.",
                "partial_overlap": cap[1],
                "will_produce_output": False}

    if cap:
        module, label = cap
        want = intent(text)
        # A capability keyword plus a recommendation verb is a shopping question,
        # not an engineering one. "recommend a laptop for CAD work" is not CAD.
        if want == "recommend":
            return {"verdict": "OUT_OF_SCOPE",
                    "domain": "product recommendation",
                    "capability": None, "matched": frag_in,
                    "reason": f"this mentions '{frag_in}', which EngineAI does "
                              f"compute, but the request is asking for a "
                              "recommendation or a purchase opinion rather than a "
                              "calculation. Claiming the capability here would be "
                              "an over-claim.",
                    "will_produce_output": False}
        if want == "explain":
            return {"verdict": "IN_SCOPE", "capability": label, "module": module,
                    "matched": frag_in, "intent": "explain",
                    "will_produce_output": True,
                    "obligation": "define the concept from the reference library; "
                                  "do NOT invoke a solver, no numbers were given"}
        if not _module_live(module):
            return {"verdict": "PARTIAL", "capability": label, "module": module,
                    "matched": frag_in,
                    "reason": f"'{label}' is claimed by {module}.py, but that module "
                              "does not import on this machine, so the capability is "
                              "not real right now.",
                    "will_produce_output": False}
        return {"verdict": "IN_SCOPE", "capability": label, "module": module,
                "matched": frag_in, "will_produce_output": True,
                "obligation": "produce a verifiable artifact and a receipt, not prose"}

    # Engineering it does not implement is a NAMED GAP, not a generic decline.
    # "That is geotechnics and I do not do geotechnics" is a useful answer;
    # "that is outside what I verify" tells the user nothing and reads the same as
    # a refusal to plan their holiday.
    if subj_what:
        return {"verdict": "PARTIAL", "domain": subj_what,
                "capability": None, "matched": subj_frag,
                "reason": f"{subj_what} is not implemented in EngineAI. This IS "
                          "engineering and it is a real gap, named so you know "
                          "exactly what is missing rather than being told the "
                          "request was simply unrecognised.",
                "will_produce_output": False}

    return {"verdict": "OUT_OF_SCOPE", "domain": "unrecognised",
            "capability": None, "matched": None,
            "reason": "no capability in EngineAI matches this request. Rather than "
                      "produce a plausible answer, it declines.",
            "will_produce_output": False}


def triage(requests):
    """Classify a batch and summarise. Useful for deciding what a workload needs."""
    rows = [dict(request=r, **classify(r)) for r in requests]
    return {"total": len(rows),
            "in_scope": sum(1 for r in rows if r["verdict"] == "IN_SCOPE"),
            "partial": sum(1 for r in rows if r["verdict"] == "PARTIAL"),
            "out_of_scope": sum(1 for r in rows if r["verdict"] == "OUT_OF_SCOPE"),
            "rows": rows}


def capability_index():
    """Every capability this gate will admit, with whether its module is live."""
    out = []
    for pattern, (module, label) in CAPABILITY_MAP.items():
        out.append({"capability": label, "module": module,
                    "live": _module_live(module),
                    "triggers": pattern.split("|")[:4]})
    return sorted(out, key=lambda r: r["capability"])


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    # ---- every claimed capability must be backed by a module that imports ----
    idx = capability_index()
    dead = [c for c in idx if not c["live"]]
    chk(f"all {len(idx)} claimed capabilities are backed by a module that imports",
        not dead, ", ".join(c["module"] for c in dead) or "none dead")

    # ---- in-scope requests are admitted, with the obligation attached ----
    for req, want in [
        ("size a steel beam for a 6 m span carrying 12 kN/m", "steel"),
        ("what is the pressure drop in a 50 mm pipe at 2 m/s", "flow"),
        ("check this column for buckling over 4 m", "column"),
        ("generate a dimensioned drawing of a flange", "drawing"),
        ("how hot does this heat sink get at 20 W", "heat"),
        ("fatigue life of a shaft at 200 MPa alternating", "fatigue"),
    ]:
        r = classify(req)
        chk(f"IN_SCOPE: {req[:46]}", r["verdict"] == "IN_SCOPE",
            f"-> {r.get('capability')}")
    chk("an in-scope verdict carries the obligation to produce an ARTIFACT",
        "artifact" in classify("size a steel beam")["obligation"])

    # ---- the real Manus workload, refused by name ----
    for req, domain_frag in [
        ("plan a 10 day itinerary for Japan with hotels", "travel"),
        ("analyse Tesla stock and tell me whether to buy", "financial"),
        ("optimise my resume for a mechanical engineer role", "recruitment"),
        # both "deck" and "invest" are out of scope here; either label is honest,
        # so the assertion is on the REFUSAL, not on which domain won the match
        ("make me a pitch deck for investors", ""),
        ("build a landing page for my consultancy", "web development"),
        ("write a blog post about sustainable design", "content writing"),
        ("what should I cook for dinner tonight", "food"),
        ("is this contract enforceable", "legal"),
        ("my knee hurts when I run, what is wrong", "medical"),
    ]:
        r = classify(req)
        chk(f"REFUSED: {req[:46]}",
            r["verdict"] == "OUT_OF_SCOPE" and domain_frag in r["domain"],
            f"-> {r.get('domain')}")
    chk("a refusal explicitly says no output will be produced",
        classify("plan a trip to Rome")["will_produce_output"] is False)
    chk("...and says WHY, rather than just declining",
        "no capability" in classify("plan a trip to Rome")["reason"])

    # ---- the distinction that matters: engineering EngineAI cannot do ----
    for req, frag in [
        ("design the reinforced concrete slab for this floor", "concrete"),
        ("check this glulam timber beam", "timber"),
        ("what is the seismic base shear for this frame", "seismic"),
        ("size the pile foundation for this column", "geotechnical"),
        ("design the fillet weld for this bracket", "weld"),
        ("rate this gear pair to ISO 6336", "gear"),
        ("check this beam to AISC 360", "AISC 360"),
        ("what will this part cost to manufacture", "cost"),
    ]:
        r = classify(req)
        chk(f"PARTIAL (real engineering, not implemented): {req[:40]}",
            r["verdict"] == "PARTIAL", r.get("reason", "")[:60])
    chk("a PARTIAL verdict distinguishes 'cannot do' from 'not engineering'",
        "different statement" in classify("design a concrete slab")["note"])
    chk("...and still refuses to produce output",
        classify("design a concrete slab")["will_produce_output"] is False)

    # ---- the trap: an engineering word inside an out-of-domain request ----
    r = classify("build me a website about bridge engineering")
    chk("'website about bridge engineering' is WEB DEVELOPMENT, not bridge design",
        r["verdict"] == "OUT_OF_SCOPE" and "web" in r["domain"],
        "an engineering noun does not make a request engineering")
    r2 = classify("write a blog post explaining beam deflection")
    chk("'blog post explaining beam deflection' is content writing",
        r2["verdict"] == "OUT_OF_SCOPE")
    r3 = classify("make a slide deck about our new heat exchanger")
    chk("'slide deck about a heat exchanger' is presentation generation",
        r3["verdict"] == "OUT_OF_SCOPE")

    # ---- and the reverse: an in-scope request with an incidental word ----
    r4 = classify("calculate the heat exchanger duty for these flow rates")
    chk("...but an actual heat exchanger CALCULATION is in scope",
        r4["verdict"] == "IN_SCOPE", f"-> {r4['capability']}")

    # ---- vocabulary the gate must recognise, found by running real phrasings ----
    r5 = classify("how many cycles will this welded detail survive at 80 MPa")
    chk("a fatigue question phrased as 'cycles' and 'welded detail' is IN SCOPE",
        r5["verdict"] == "IN_SCOPE" and "fatigue" in r5["capability"],
        "the gate failing closed on real phrasing costs capability")
    r6 = classify("design the fillet welds for this bracket")
    chk("...while WELD DESIGN itself is still correctly a gap",
        r6["verdict"] == "PARTIAL",
        "'welded detail' fatigue and 'fillet weld' design are different requests")
    r7 = classify("produce the full design package for this bracket")
    chk("'full design package' routes to the deliverables engine",
        r7["verdict"] == "IN_SCOPE" and "deliverable" in r7["capability"])
    r8 = classify("check the composite decking span")
    chk("composite DECKING is not mistaken for a slide deck",
        r8["verdict"] == "PARTIAL" and "EN 1994" in r8["reason"],
        "word-boundary matching stops 'deck' firing on 'decking'; the honest "
        "verdict is a named engineering gap, not a presentation request")

    # ---- batch triage ----
    t = triage(["size a beam", "plan a holiday", "design a concrete column",
                "pressure drop in a pipe", "analyse my stock portfolio"])
    chk("triage counts a mixed workload correctly",
        t["in_scope"] == 2 and t["partial"] == 1 and t["out_of_scope"] == 2,
        f"{t['in_scope']} in, {t['partial']} partial, {t['out_of_scope']} out")

    chk("nothing unrecognised is answered by default",
        classify("qwertyuiop asdfghjkl")["will_produce_output"] is False)

    print("scope selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="request scope gate")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--classify", metavar="REQUEST")
    ap.add_argument("--index", action="store_true")
    a = ap.parse_args()
    if a.index:
        for c in capability_index():
            print(f"  {'live' if c['live'] else 'DEAD'}  {c['capability']:<42} "
                  f"{c['module']}")
        raise SystemExit(0)
    if a.classify:
        print(json.dumps(classify(a.classify), indent=2))
        raise SystemExit(0)
    raise SystemExit(_selftest())
