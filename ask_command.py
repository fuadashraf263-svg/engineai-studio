#!/usr/bin/env python3
"""
ask_command.py -- `agent.py ask "..."`  the student-facing answer layer.

THE ONE PROMISE THIS MAKES
--------------------------
Every sentence of an answer carries a tag saying where it came from:

    [SOLVED]      a real solver ran; error vs the analytical solution known
    [HANDBOOK]    a closed-form formula; formula and validity range shown
    [LOOKUP]      a nominal published value from the materials library
    [LIBRARY]     a passage from one of the user's books, with page number
    [UNVERIFIED]  the language model's own words -- NOT checked against
                  anything, shown only because nothing better was found

For a student this tag is not decoration. A student cannot tell a correct
answer from a confident wrong one -- that is the whole reason they are
studying. The tag is the one thing that protects them, and an
[UNVERIFIED] answer is never printed WITHOUT the label, no matter how
plausible it looks.

WHAT THIS IS AND IS NOT
-----------------------
It is a router. It reaches for the cheapest trustworthy source first:
  1. a direct calculation if the question maps onto handbook.py
  2. a material property if it is a lookup
  3. passages from the library if the method lives in a book
  4. only then the brain, and only clearly labelled

It is NOT a guarantee of correctness. It is a guarantee of HONESTY about
correctness, which is the most any tool can offer and more than most give.

Run:  python agent.py ask "Reynolds number for water at 2 m/s in a 25mm pipe"
      python agent.py ask "yield strength of Ti-6Al-4V"
      python agent.py ask "how does a Kalman filter estimate state"
      python agent.py ask "..." --brain      # allow an UNVERIFIED fallback
"""

from __future__ import annotations

import json
import re
from pathlib import Path

HOME = Path.home() / "engineai"


# --------------------------------------------------- cheap deterministic ---

_UNIT_RE = re.compile(
    r"(-?\d+\.?\d*(?:e[-+]?\d+)?)\s*([a-zA-Z/]+)\s+(?:to|in)\s+([a-zA-Z/]+)",
    re.I)


def try_conversion(q: str):
    import handbook
    m = _UNIT_RE.search(q)
    if not m:
        return None
    try:
        r = handbook.convert(float(m.group(1)), m.group(2), m.group(3))
        return (f"[HANDBOOK] {m.group(1)} {m.group(2)} = "
                f"{r['value']:.6g} {r['units']}   ({r['formula']})")
    except Exception:
        return None


def try_material(q: str):
    import materials
    low = q.lower()
    if not any(w in low for w in ("yield", "modulus", "density", "strength",
                                  "conductivity", "property", "properties",
                                  "material")):
        return None
    for name in materials.MATERIALS:
        stem = name.split("-")[0].lower()
        if name.lower() in low or stem in low.replace(" ", ""):
            m = materials.get(name)
            lines = [f"[LOOKUP] {m['full_name']}  (nominal, 293 K, typical "
                     f"not minimum)",
                     f"    E = {m['E_MPa']:.0f} MPa,  yield = "
                     f"{m['yield_MPa']:.0f} MPa,  density = "
                     f"{m['density_kg_m3']:.0f} kg/m3,  k = "
                     f"{m['k_W_mK']:.1f} W/mK"]
            if "cryo" in low or "65" in q or "cold" in low:
                lines.append(f"    [LOOKUP] cryogenic note: {m['cryo']}")
            return "\n".join(lines)
    return None


def try_pipe(q: str):
    """Recognise a pipe-flow question and actually compute it."""
    import handbook
    low = q.lower()
    if not any(w in low for w in ("pipe", "pressure drop", "head loss",
                                  "reynolds", "friction")):
        return None
    D = re.search(r"(\d+\.?\d*)\s*mm", low)
    V = re.search(r"(\d+\.?\d*)\s*m/s", low)
    # length: 'm' NOT followed by '/' or a letter, so '2 m/s' and 'mm' are
    # excluded. Take the LAST such match -- "in a 25mm pipe over 50 m" puts
    # the length last. The first draft used \b and grabbed the 2 from m/s.
    L_all = re.findall(r"(\d+\.?\d*)\s*m(?![/a-z])", low)
    if not (D and V):
        return None
    class _M:
        def __init__(s,v): s._v=v
        def group(s,_): return s._v
    L = _M(L_all[-1]) if L_all else None
    fluid = ("air" if "air" in low else "oil" if "oil" in low else "water")
    r = handbook.pipe_pressure_drop(float(D.group(1)) / 1000,
                                    float(L.group(1)) if L else 1.0,
                                    float(V.group(1)), fluid)
    out = [f"[HANDBOOK] {fluid}, {V.group(1)} m/s in "
           f"{D.group(1)} mm pipe ({r['regime']} flow):",
           f"    Re = {r['reynolds']['value']:.0f}   "
           f"[{r['reynolds']['formula']}]",
           f"    f  = {r['friction_factor']['value']:.4f}   "
           f"[{r['friction_factor']['formula']}]"]
    if L:
        dp = r['pressure_drop']['value']
        out.append(f"    dp = {dp:.1f} Pa = {dp/1e5:.4f} bar over "
                   f"{L.group(1)} m   [{r['pressure_drop']['formula']}]")
        for w in r['pressure_drop']['warnings']:
            out.append(f"    ! {w}")
    return "\n".join(out)


def try_circuit(q: str):
    """A DC resistive-circuit question with a netlist -> solve it exactly."""
    import re as _re
    low = q.lower()
    if "netlist" not in low and "node voltage" not in low:
        return None
    # parse lines like 'R1 1 2 500' / 'V1 2 3 4' / 'I1 0 1 1'
    R, V, I, G = [], [], [], []
    maxn = 0
    for ln in q.splitlines():
        m = _re.match(r"\s*([RVIG])\w*\s+(\d+)\s+(\d+)\s+"
                      r"([-\d.]+)", ln)
        if not m:
            continue
        kind, a, b, val = m.group(1), int(m.group(2)), int(m.group(3)), \
            float(m.group(4))
        maxn = max(maxn, a, b)
        nm = ln.split()[0]
        {"R": R, "V": V, "I": I}[kind].append([nm, a, b, val]) \
            if kind in "RVI" else None
    if maxn < 1 or not R:
        return None
    try:
        import builder
        spec = {"nodes": maxn, "R": R, "V": V, "I": I, "G": G}
        sol = builder.solve_resistive_circuit(spec)
        v = sol["node_voltages"]
        lines = ["[SOLVED] DC circuit, nodal analysis:"]
        for n in sorted(v):
            lines.append(f"    node {n}: {v[n]:.4f} V")
        return "\n".join(lines)
    except Exception:
        return None


DETERMINISTIC = [try_conversion, try_pipe, try_material, try_circuit]


# --------------------------------------------------------------- library ---

def try_library(q: str, k: int = 4):
    try:
        import library
    except Exception:
        return None
    hits = library.search(q, k=k)
    if not hits:
        return None
    out = ["[LIBRARY] from your books:"]
    for h in hits:
        body = re.sub(r"\s+", " ", h["text"])[:280]
        out.append(f"    [{h['book']}, p.{h['page']}]  (relevance "
                   f"{h['score']}, coverage {h['coverage']})")
        out.append(f"      \"{body}...\"")
    absent = hits[0].get("query_terms_absent", [])
    if absent:
        out.append(f"    note: your library has no passages containing "
                   f"{absent} -- that part of the question is uncovered.")
    return "\n".join(out), hits


# ----------------------------------------------------------------- brain ---

BRAIN_SYSTEM = """You are a senior engineer answering a colleague or student. Your job is to GIVE A USEFUL ANSWER, not to ask for clarification.

When a question is vague or missing numbers, DO NOT ask them to resend it. Instead:
- State the most likely interpretation and answer THAT, in full.
- Where a typical value is expected (a fan pressure ratio, a safety factor, a friction coefficient), give the typical range with the reasoning, and say what it depends on.
- Show the governing equation, define symbols, state every assumption.
- If numbers were given, work the full numerical answer.
- End with the single most common mistake on this topic.

Example: asked "what ratio should my fan input/output have" with no numbers, a good answer explains that for a fan the relevant ratio is the PRESSURE RATIO (outlet/inlet total pressure), that for most HVAC and cooling fans it is very close to 1.0 (typically 1.001-1.05, i.e. a rise of a few hundred to a few thousand Pa), that a "fan" by definition has a pressure ratio below ~1.1 (above that it is a compressor), and that the exact value depends on the system resistance the fan must overcome. THEN it notes what would pin the number down (flow rate, duct losses). It does NOT say "your question didn't come through."

Never refuse for vagueness. Answer the likely question well, label your assumptions, and move the person forward. Another layer marks this UNVERIFIED; your job is a genuinely helpful, technically correct answer."""


def ask_brain(cfg, q, library_context=None):
    from brain import FablePlanner
    from core import Budget
    budget = Budget(cfg["budget"])
    planner = FablePlanner(cfg, budget)
    payload = q
    if library_context:
        payload += ("\n\nRelevant passages from the student's own books "
                    "(prefer these, cite the page):\n" + library_context)
    txt = planner._call([{"role": "user", "content": payload}],
                        system=BRAIN_SYSTEM)
    return txt, budget.spent_usd


# ------------------------------------------------------------------ main ---

def cmd_ask(cfg: dict, question: str, allow_brain: bool = False) -> int:
    print("== ASK " + "=" * 58)
    print(f"  Q: {question}\n")

    answered = False

    # 1. deterministic tiers -- free, checked, instant
    for fn in DETERMINISTIC:
        try:
            r = fn(question)
        except Exception:
            r = None
        if r:
            print(r + "\n")
            answered = True

    # 2. the user's library -- cited
    lib = try_library(question)
    lib_text = None
    if lib:
        print(lib[0] + "\n")
        lib_text = "\n".join(h["text"][:500] for h in lib[1])
        answered = True

    # 3. the brain -- only if asked, always labelled
    if allow_brain:
        print("  [UNVERIFIED] language-model explanation "
              "(NOT checked against a solver or a citation):\n")
        try:
            txt, spent = ask_brain(cfg, question, lib_text)
        except Exception as exc:
            print(f"  brain unavailable: {exc}")
            return 1 if not answered else 0
        for line in txt.splitlines():
            print("    " + line)
        print(f"\n  (cost ${spent:.3f}. The text above is UNVERIFIED -- treat "
              "it as a knowledgeable classmate's explanation, not a "
              "checked result.)")
        answered = True
    elif not answered:
        print("  NOT ANSWERED from any verified source.")
        print("  - no handbook formula matched")
        print("  - no material lookup matched")
        print("  - nothing in your library cleared the relevance floor\n")
        print("  Options:")
        print("    add the relevant book:  python agent.py ... (library add)")
        print("    allow a labelled model answer:  add --brain")
        print("    if it maps onto a solver, run:  python agent.py chat \"...\"")
        return 1

    if answered and not allow_brain:
        print("  ---")
        print("  Every line above is tagged with its source. For a full "
              "worked\n  explanation add --brain (clearly labelled "
              "UNVERIFIED).")
    return 0

# ---------------------------------------------------------- certainty split ---

def answer_with_certainty(cfg: dict, question: str,
                          allow_brain: bool = True) -> dict:
    """Assemble an answer in TWO explicit parts:

        CERTAIN   -- claims backed by a verified source (a solver result, a
                     closed-form formula, a material value, or a cited book
                     page). Each carries WHERE it came from.
        UNCERTAIN -- anything the model added that is NOT backed by a verified
                     source. Clearly fenced off so the reader never mistakes
                     it for a checked fact.

    This is the honesty principle made visible in every single answer: the
    student sees, at a glance, which half to trust completely and which half
    to check.
    """
    certain = []     # (source_tag, text)
    uncertain = []   # text

    # --- gather the VERIFIED tiers (each is a source) ---
    for fn in DETERMINISTIC:
        try:
            r = fn(question)
        except Exception:
            r = None
        if r:
            tag = r.split("]")[0].strip("[") if r.startswith("[") else "INFO"
            body = r.split("]", 1)[1].lstrip() if r.startswith("[") else r
            certain.append((tag, body))

    # --- the library (cited pages ARE verified sources) ---
    lib = try_library(question)
    lib_context = None
    if lib:
        for h in lib[1]:
            snippet = " ".join(h["text"].split())[:220]
            certain.append(("LIBRARY",
                            f"{h['book']}, page {h['page']}: \"{snippet}...\""))
        lib_context = "\n".join(h["text"][:500] for h in lib[1])

    # --- the brain fills the UNCERTAIN half only ---
    brain_cost = 0.0
    if allow_brain:
        try:
            txt, brain_cost = ask_brain(cfg, question, lib_context)
            uncertain.append(txt)
        except Exception as e:
            uncertain.append(f"(AI explanation unavailable: {e})")

    return {"question": question,
            "certain": certain,           # list of (source, claim)
            "uncertain": uncertain,       # list of unverified text
            "has_verified": bool(certain),
            "brain_cost_usd": brain_cost}


def format_certainty(result: dict) -> str:
    """Render the two-part answer as text with a hard visual divider."""
    out = []
    out.append("=" * 62)
    out.append(f"  Q: {result['question']}")
    out.append("=" * 62)

    if result["certain"]:
        out.append("")
        out.append("  ✓ CERTAIN  (backed by a verified source)")
        out.append("  " + "-" * 58)
        for source, claim in result["certain"]:
            out.append(f"  [{source}]")
            for line in claim.splitlines():
                out.append(f"     {line}")
            out.append("")
    else:
        out.append("")
        out.append("  ✓ CERTAIN: nothing here could be verified against a")
        out.append("             solver, formula, material, or your library.")
        out.append("")

    if result["uncertain"]:
        out.append("  " + "~" * 58)
        out.append("  ? UNCERTAIN  (AI explanation -- NOT verified, check it)")
        out.append("  " + "-" * 58)
        for txt in result["uncertain"]:
            for line in txt.splitlines():
                out.append(f"     {line}")
        if result["brain_cost_usd"]:
            out.append(f"\n     (${result['brain_cost_usd']:.3f}, unverified)")
    out.append("=" * 62)
    out.append("  Trust the CERTAIN half completely -- each line names its")
    out.append("  source. Verify the UNCERTAIN half before relying on it.")
    out.append("=" * 62)
    return "\n".join(out)
