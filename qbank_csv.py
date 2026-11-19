"""qbank_csv.py - the answered question bank, and an audit of its answers.

WHAT CHANGED. The first question bank was a PDF flattened to text: questions
only. question_bank.py therefore scores REACH - did EngineAI produce a real,
grounded answer - and deliberately refuses to report correctness, because with
no answer key any correctness figure would have been me grading myself.

This bank is different. It carries answer_value (full precision), unit,
formula and a worked solution. That makes a genuine correctness score possible
for the first time.

WHY THE AUDIT EXISTS. Grading against an answer key only moves the trust
problem: a wrong key marks right answers wrong, and the failure is invisible
because everything still runs. So before this file is allowed to grade
anything, it re-derives answer_value itself.

The solution field is structured - "Given: a = 1, b = 2 | Formula: ... |
Answer: ..." - so the inputs can be read back out. audit() feeds those inputs
to an INDEPENDENT implementation of the formula, written here from the physics
rather than copied from the bank, and compares. A row only becomes a graded
row if the two agree. Rows with no oracle are reported as UNAUDITED, not
quietly assumed correct.

DROPPING IN MORE DATA. load() globs data/qbank_csv_*.csv, so further chunks
need no code change. Copy the file straight into data/ - do not paste it
through a chat window, which truncates at 50k characters and has already cost
this project 385 questions once.
"""
import csv
import glob
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
PATTERN = os.path.join(DATA, "qbank_csv_*.csv")

COLUMNS = ["id", "branch", "topic", "difficulty", "question", "answer",
           "answer_value", "unit", "solution", "formula", "tags", "template_id"]

# Agreement band for the audit. The bank's own `answer` field is rounded to
# ~4 significant figures while answer_value is full precision, so the audit
# compares against answer_value and only needs to absorb float noise.
AUDIT_TOL = 1e-6


@dataclass
class Q:
    id: str
    branch: str
    topic: str
    difficulty: str
    question: str
    answer: str
    answer_value: float
    unit: str
    solution: str
    formula: str
    tags: str
    template_id: str

    @property
    def tag_list(self) -> list:
        return [t for t in self.tags.split(";") if t]

    @property
    def given(self) -> dict:
        return parse_given(self.solution)


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def sources() -> list:
    """Every CSV chunk present, in name order."""
    return sorted(glob.glob(PATTERN))


def load(paths=None) -> list:
    """All questions, deduplicated by id, first file wins.

    A row is dropped if it has the wrong column count or an answer_value that
    is not a number. Both mean a truncated or corrupted chunk, and a half-row
    silently kept would become a permanently wrong oracle entry.
    """
    out, seen = [], set()
    for path in (paths if paths is not None else sources()):
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if len(row) != len(COLUMNS) or None in row.values():
                    continue
                if row.get("id") in seen or not row.get("id"):
                    continue
                try:
                    val = float(row["answer_value"])
                except (TypeError, ValueError):
                    continue
                seen.add(row["id"])
                out.append(Q(id=row["id"], branch=row["branch"], topic=row["topic"],
                             difficulty=row["difficulty"], question=row["question"],
                             answer=row["answer"], answer_value=val,
                             unit=row["unit"], solution=row["solution"],
                             formula=row["formula"], tags=row["tags"],
                             template_id=row["template_id"]))
    return out


NUM = r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
_GIVEN = re.compile(r"Given:\s*(.*?)(?:\s*\||$)")
_PAIR = re.compile(r"([A-Za-z_]\w*)\s*=\s*(" + NUM + r")")


def parse_given(solution: str) -> dict:
    """The `Given: a = 1, b = 2` clause as a dict, or {} if there is none.

    Only the Given clause is read. The Substitute clause restates the same
    numbers already arranged into the bank's own formula, so trusting it would
    make the audit a copy of the thing it is auditing.
    """
    m = _GIVEN.search(solution or "")
    if not m:
        return {}
    return {k: float(v) for k, v in _PAIR.findall(m.group(1))}


# --------------------------------------------------------------------------
# the oracles - written from the physics, not from the bank's formula column
# --------------------------------------------------------------------------

def _sig(given: dict) -> tuple:
    return tuple(sorted(given))


# Keyed by (tag, signature of the Given variables). The signature is part of
# the key because the same tag appears with different inputs across branches:
# `ideal-gas` is mass-based in Mechanical and mole-based in Chemical, and a
# single tag-keyed oracle would apply the wrong physics to one of them.
ORACLES = {}


def oracle(tag, *names):
    def deco(fn):
        ORACLES[(tag, tuple(sorted(names)))] = fn
        return fn
    return deco


D = math.degrees
R_UNIV = 8.314          # J/mol-K
R_AIR = 0.287           # kJ/kg-K
G = 9.81                # m/s^2
GAMMA = 1.4
CV_AIR, CP_AIR = 0.718, 1.005

# --- engineering mathematics ---
oracle("determinant;matrix", "a", "b", "c", "d")(lambda g: g["a"]*g["d"] - g["b"]*g["c"])
oracle("slope;line", "x1", "y1", "x2", "y2")(lambda g: (g["y2"]-g["y1"])/(g["x2"]-g["x1"]))
oracle("distance", "x1", "y1", "x2", "y2")(
    lambda g: math.hypot(g["x2"]-g["x1"], g["y2"]-g["y1"]))
oracle("combinations", "n", "r")(lambda g: math.comb(int(g["n"]), int(g["r"])))
oracle("permutations", "n", "r")(lambda g: math.perm(int(g["n"]), int(g["r"])))
oracle("AP", "a", "d", "n")(lambda g: g["a"] + (g["n"]-1)*g["d"])
oracle("AP;series", "a", "d", "n")(lambda g: g["n"]/2*(2*g["a"] + (g["n"]-1)*g["d"]))
oracle("GP", "a", "r", "n")(lambda g: g["a"] * g["r"]**(g["n"]-1))
oracle("simple-interest", "P", "R", "Tt")(lambda g: g["P"]*g["R"]*g["Tt"]/100)
oracle("compound-interest", "P", "R", "Tt")(lambda g: g["P"]*(1+g["R"]/100)**g["Tt"])
oracle("dot-product", "a1", "a2", "a3", "b1", "b2", "b3")(
    lambda g: g["a1"]*g["b1"] + g["a2"]*g["b2"] + g["a3"]*g["b3"])
oracle("complex;modulus", "a", "b")(lambda g: math.hypot(g["a"], g["b"]))
oracle("complex;argument", "a", "b")(lambda g: D(math.atan2(g["b"], g["a"])))
oracle("evaluate;polynomial", "a", "b", "c", "d", "x0")(
    lambda g: g["a"]*g["x0"]**3 + g["b"]*g["x0"]**2 + g["c"]*g["x0"] + g["d"])
oracle("newton-raphson", "N", "x0")(lambda g: g["x0"] - (g["x0"]**2 - g["N"])/(2*g["x0"]))
oracle("trapezoidal", "h", "y0", "y1", "y2", "y3", "y4")(
    lambda g: g["h"]*(g["y0"]/2 + g["y1"] + g["y2"] + g["y3"] + g["y4"]/2))
oracle("lcm", "a", "b")(lambda g: math.lcm(int(g["a"]), int(g["b"])))

# --- mechanical ---
oracle("torsion", "Tt", "d")(lambda g: 16*(g["Tt"]*1000)/(math.pi*g["d"]**3))
oracle("section-modulus", "b", "h")(lambda g: g["b"]*g["h"]**2/6)
oracle("factor-of-safety", "su", "sw")(lambda g: g["su"]/g["sw"])
oracle("thermal-stress", "E", "al", "dT")(lambda g: g["E"]*1000*g["al"]*1e-6*g["dT"])
oracle("hoop-stress", "P", "d", "t")(lambda g: g["P"]*g["d"]/(2*g["t"]))
oracle("carnot", "Tc", "Th")(lambda g: (1 - g["Tc"]/g["Th"])*100)
oracle("COP;refrigerator", "Tc", "Th")(lambda g: g["Tc"]/(g["Th"]-g["Tc"]))
oracle("COP;heat-pump", "Tc", "Th")(lambda g: g["Th"]/(g["Th"]-g["Tc"]))
oracle("otto", "r")(lambda g: (1 - 1/g["r"]**(GAMMA-1))*100)
oracle("internal-energy", "dT", "m")(lambda g: g["m"]*CV_AIR*g["dT"])
oracle("enthalpy", "dT", "m")(lambda g: g["m"]*CP_AIR*g["dT"])
oracle("ideal-gas", "Tk", "V", "m")(lambda g: g["m"]*R_AIR*g["Tk"]/g["V"])
oracle("isothermal-work", "Tk", "m", "ratio")(
    lambda g: g["m"]*R_AIR*g["Tk"]*math.log(g["ratio"]))
oracle("reynolds", "D", "V")(lambda g: 1000*g["V"]*g["D"]/0.001)
oracle("hydrostatic", "h")(lambda g: 1000*G*g["h"]/1000)
oracle("torricelli", "cv", "h")(lambda g: g["cv"]*math.sqrt(2*G*g["h"]))
oracle("continuity", "V1", "d1", "d2")(lambda g: g["V1"]*(g["d1"]/g["d2"])**2)
oracle("darcy", "D", "L", "V", "f")(lambda g: g["f"]*g["L"]*g["V"]**2/(2*G*g["D"]))
oracle("gear-ratio", "N1", "z1", "z2")(lambda g: g["N1"]*g["z1"]/g["z2"])
oracle("belt-speed", "D", "N")(lambda g: math.pi*(g["D"]/1000)*g["N"]/60)
oracle("power-torque", "N", "P")(lambda g: 60*g["P"]*1000/(2*math.pi*g["N"]))
oracle("natural-frequency", "k", "m")(lambda g: math.sqrt(g["k"]/g["m"])/(2*math.pi))
oracle("conduction", "A", "L", "dT", "k")(lambda g: g["k"]*g["A"]*g["dT"]/g["L"])
oracle("convection", "A", "dT", "h")(lambda g: g["h"]*g["A"]*g["dT"])
oracle("LMTD", "dT1", "dT2")(
    lambda g: (g["dT1"]-g["dT2"])/math.log(g["dT1"]/g["dT2"]))
oracle("MRR", "d", "f", "v")(lambda g: 1000*g["v"]*g["f"]*g["d"])
oracle("machining-time", "L", "N", "f")(lambda g: g["L"]/(g["f"]*g["N"]))

# --- electrical ---
oracle("ohms-law", "I", "R")(lambda g: g["I"]*g["R"])
oracle("series", "R1", "R2", "R3")(lambda g: g["R1"]+g["R2"]+g["R3"])
oracle("parallel", "R1", "R2")(lambda g: g["R1"]*g["R2"]/(g["R1"]+g["R2"]))
oracle("power", "I", "V")(lambda g: g["V"]*g["I"])
oracle("power;i2r", "I", "R")(lambda g: g["I"]**2*g["R"])
oracle("capacitors-series", "C1", "C2")(lambda g: g["C1"]*g["C2"]/(g["C1"]+g["C2"]))
oracle("rc-time-constant", "C", "R")(lambda g: g["R"]*g["C"])          # kohm*uF -> ms
oracle("rl-time-constant", "L", "R")(lambda g: g["L"]/g["R"])          # mH/ohm -> ms
oracle("reactance;inductive", "L", "f")(lambda g: 2*math.pi*g["f"]*g["L"]*1e-3)
oracle("reactance;capacitive", "C", "f")(lambda g: 1/(2*math.pi*g["f"]*g["C"]*1e-6))
oracle("impedance", "R", "XC", "XL")(
    lambda g: math.hypot(g["R"], g["XL"]-g["XC"]))
oracle("resonance", "C", "L")(
    lambda g: 1/(2*math.pi*math.sqrt(g["L"]*1e-3*g["C"]*1e-6)))
oracle("capacitor-energy", "C", "V")(lambda g: 0.5*g["C"]*1e-6*g["V"]**2*1000)
oracle("rms", "Vp")(lambda g: g["Vp"]/math.sqrt(2))
oracle("transformer", "N1", "N2", "V1")(lambda g: g["V1"]*g["N2"]/g["N1"])
oracle("back-emf", "Ia", "Ra", "V")(lambda g: g["V"] - g["Ia"]*g["Ra"])
oracle("synchronous-speed", "P", "f")(lambda g: 120*g["f"]/g["P"])
oracle("three-phase-power", "IL", "VL", "pf")(
    lambda g: math.sqrt(3)*g["VL"]*g["IL"]*g["pf"]/1000)
oracle("power-factor", "P", "S")(lambda g: g["P"]/g["S"])
oracle("efficiency", "Pi", "Po")(lambda g: g["Po"]/g["Pi"]*100)
oracle("voltage-divider", "R1", "R2", "Vin")(
    lambda g: g["Vin"]*g["R2"]/(g["R1"]+g["R2"]))
oracle("opamp;inverting", "Rf", "Rin")(lambda g: g["Rf"]/g["Rin"])
oracle("opamp;non-inverting", "Rf", "Rg")(lambda g: 1 + g["Rf"]/g["Rg"])
oracle("decibel", "ratio")(lambda g: 10*math.log10(g["ratio"]))
oracle("nyquist;sampling", "fs")(lambda g: g["fs"]/2)

# --- civil ---
oracle("bending-moment", "L", "W")(lambda g: g["W"]*g["L"]/4)
oracle("bending-moment;udl", "L", "w")(lambda g: g["w"]*g["L"]**2/8)
oracle("cantilever", "L", "W")(lambda g: g["W"]*g["L"])
oracle("cantilever;udl", "L", "w")(lambda g: g["w"]*g["L"]**2/2)
oracle("reactions", "L", "W", "a")(lambda g: g["W"]*(g["L"]-g["a"])/g["L"])
oracle("fixed-beam", "L", "w")(lambda g: g["w"]*g["L"]**2/12)
oracle("effective-depth", "D", "c", "dia")(lambda g: g["D"] - g["c"] - g["dia"]/2)
oracle("tributary-load", "A", "w")(lambda g: g["w"]*g["A"])
oracle("development-length", "dia", "fs", "tbd")(
    lambda g: g["dia"]*g["fs"]/(4*g["tbd"]))
oracle("void-ratio", "n")(lambda g: g["n"]/(1-g["n"]))
oracle("active-pressure;Ka", "phi")(
    lambda g: (1-math.sin(math.radians(g["phi"])))/(1+math.sin(math.radians(g["phi"]))))
oracle("effective-stress", "g", "z")(lambda g: (g["g"] - 9.81)*g["z"])
oracle("darcy-seepage", "A", "i", "k")(lambda g: g["k"]*1e-4*g["i"]*g["A"]*1e-3*1e9)
oracle("manning", "R", "S", "n")(lambda g: (1/g["n"])*g["R"]**(2/3)*g["S"]**0.5)
oracle("weir", "H", "L", "cd")(
    lambda g: (2/3)*g["cd"]*math.sqrt(2*G)*g["L"]*g["H"]**1.5)
oracle("superelevation", "R", "V")(lambda g: g["V"]**2/(127*g["R"]))
oracle("curve-radius", "V", "e", "f")(lambda g: g["V"]**2/(127*(g["e"]+g["f"])))
oracle("retention-time", "Q", "V")(lambda g: g["V"]/g["Q"])
oracle("BOD", "L0", "k", "t")(lambda g: g["L0"]*(1-math.exp(-g["k"]*g["t"])))
oracle("rational-method", "A", "C", "i")(lambda g: g["C"]*g["i"]*g["A"]/360)
oracle("tape-correction", "L", "M", "e")(lambda g: g["M"]*(g["L"]+g["e"])/g["L"])

# --- computer science ---
oracle("subnetting", "prefix")(lambda g: 2**(32-int(g["prefix"])) - 2)
oracle("transmission-time", "B", "S")(lambda g: g["S"]/g["B"])
oracle("bandwidth-delay", "B", "R")(lambda g: g["B"]*1e6*g["R"]*1e-3/1e3)
oracle("nyquist-capacity", "B", "M")(lambda g: 2*g["B"]*math.log2(g["M"]))
oracle("shannon-capacity", "B", "SNR")(lambda g: g["B"]*math.log2(1+g["SNR"]))
oracle("address-bits", "N")(lambda g: math.log2(g["N"]))
oracle("effective-access-time", "h", "tc", "tm")(
    lambda g: g["h"]*g["tc"] + (1-g["h"])*g["tm"])
oracle("rotational-latency", "rpm")(lambda g: 0.5*60000/g["rpm"])
oracle("binary-search", "n")(lambda g: math.floor(math.log2(g["n"])) + 1)
oracle("complexity", "n")(lambda g: g["n"]**2)
oracle("truth-table", "n")(lambda g: 2**int(g["n"]))
oracle("DAC;resolution", "Vfs", "n")(lambda g: g["Vfs"]/(2**int(g["n"])-1)*1000)
oracle("baud;bit-rate", "M", "baud")(lambda g: g["baud"]*math.log2(g["M"]))

# --- chemical ---
oracle("ideal-gas", "P", "Tk", "V")(lambda g: g["P"]*1e3*g["V"]/(R_UNIV*g["Tk"]))
oracle("first-order;conversion", "k", "t")(lambda g: 1 - math.exp(-g["k"]*g["t"]))
oracle("PFR;space-time", "X", "k")(lambda g: -math.log(1 - g["X"]/100)/g["k"])
oracle("CSTR;space-time", "X", "k")(
    lambda g: (g["X"]/100)/(g["k"]*(1 - g["X"]/100)))
oracle("half-life", "k")(lambda g: math.log(2)/g["k"])
oracle("arrhenius", "A", "Ea", "Tk")(
    lambda g: g["A"]*1e10*math.exp(-g["Ea"]*1e3/(R_UNIV*g["Tk"])))
oracle("relative-volatility", "x", "y")(
    lambda g: (g["y"]/(1-g["y"]))/(g["x"]/(1-g["x"])))
oracle("fenske", "al", "xd", "xw")(
    lambda g: math.log((g["xd"]/(1-g["xd"]))*((1-g["xw"])/g["xw"]))/math.log(g["al"]))
oracle("raoult", "Psat", "x")(lambda g: g["x"]*g["Psat"])
oracle("heat-duty", "cp", "dT", "m")(lambda g: g["m"]*g["cp"]*g["dT"])
oracle("residence-time", "Q", "V")(lambda g: g["V"]/g["Q"])
oracle("fick;diffusion", "D", "L", "dC")(
    lambda g: g["D"]*1e-9*g["dC"]/(g["L"]*1e-3)*1e6)


def find_oracle(q: Q):
    """The oracle for this row, or None. Tag order in the bank is not fixed,
    so every tag is tried against the row's Given signature."""
    sig = _sig(q.given)
    if not sig:
        return None
    for tag in (q.tags, *q.tag_list):
        fn = ORACLES.get((tag, sig))
        if fn is not None:
            return fn
    return None


def audit(rows=None, tol: float = AUDIT_TOL) -> dict:
    """Re-derive answer_value independently. Returns agreed / disagreed / unaudited.

    A disagreement is not automatically the bank's fault - it may be my oracle
    that is wrong - so disagreements are listed in full rather than counted.
    Either way the row is not safe to grade against until it is resolved.
    """
    rows = load() if rows is None else rows
    agreed, disagreed, unaudited = [], [], []
    for q in rows:
        fn = find_oracle(q)
        if fn is None:
            unaudited.append(q)
            continue
        try:
            got = float(fn(q.given))
        except Exception as exc:                      # noqa: BLE001
            disagreed.append((q, None, f"{type(exc).__name__}: {exc}"))
            continue
        want = q.answer_value
        scale = max(abs(want), abs(got), 1e-12)
        if abs(got - want) / scale <= tol:
            agreed.append(q)
        else:
            disagreed.append((q, got, f"bank {want!r} vs oracle {got!r}"))
    return {"agreed": agreed, "disagreed": disagreed, "unaudited": unaudited,
            "total": len(rows)}


def coverage(rows=None) -> dict:
    rows = load() if rows is None else rows
    by_branch, by_topic, by_diff = Counter(), defaultdict(Counter), Counter()
    for q in rows:
        by_branch[q.branch] += 1
        by_topic[q.branch][q.topic] += 1
        by_diff[q.difficulty] += 1
    return {"total": len(rows), "branch": by_branch, "topic": by_topic,
            "difficulty": by_diff}


# --------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def chk(cond, label):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label)
        ok = ok and bool(cond)

    print("qbank_csv selftest")

    rows = load()
    chk(len(rows) > 0, f"loaded {len(rows)} questions from {len(sources())} file(s)")
    chk(len({q.id for q in rows}) == len(rows), "ids are unique after dedupe")
    chk(all(isinstance(q.answer_value, float) for q in rows),
        "every answer_value is a float")

    # A truncated final row is the exact damage a 50k-char paste does, and it
    # must be dropped rather than loaded with missing columns.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "qbank_csv_trunc.csv")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(",".join(COLUMNS) + "\n")
            fh.write("EQ-1,B,T,easy,q?,1,1.0,,Given: a = 1,f,tag,X-1\n")
            fh.write("EQ-2,B,T,easy,\"a truncated ro\n")
        chk(len(load([p])) == 1, "a truncated trailing row is dropped, not loaded")

    g = parse_given("Given: a = -3, b = 1.5, Tk = 4e2 | Formula: x | Answer: 1")
    chk(g == {"a": -3.0, "b": 1.5, "Tk": 400.0}, f"Given clause parses ({g})")
    chk(parse_given("no given clause here") == {}, "a row with no Given yields {}")

    # The Substitute clause must not leak into the parsed inputs - it restates
    # the bank's own arithmetic, and using it would make the audit circular.
    g2 = parse_given("Given: R = 2 | Formula: q | Substitute: R = 999 | Answer: 4")
    chk(g2 == {"R": 2.0}, f"only the Given clause is read, not Substitute ({g2})")

    a = audit(rows)
    n_ok, n_bad = len(a["agreed"]), len(a["disagreed"])
    chk(n_ok > 0, f"{n_ok} rows re-derived and agree to {AUDIT_TOL:g}")
    chk(n_bad == 0, f"no row disagrees with its independent oracle ({n_bad} do)")
    for q, got, why in a["disagreed"][:8]:
        print(f"         {q.id} [{q.tags}] {why}")

    frac = n_ok / max(len(rows), 1)
    chk(frac >= 0.5, f"at least half the bank is audited ({frac:.0%})")

    # An oracle that is merely the bank's own number echoed back would agree
    # with everything, including a corrupted key. Prove the audit can fail.
    bad = Q(id="X", branch="B", topic="T", difficulty="easy", question="q",
            answer="9", answer_value=9.0, unit="", solution="Given: I = 2, R = 3",
            formula="V=IR", tags="ohms-law", template_id="X-0")
    chk(len(audit([bad])["disagreed"]) == 1, "a wrong answer_value is caught")

    cov = coverage(rows)
    chk(len(cov["branch"]) >= 2, f"{len(cov['branch'])} branches present")

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()

    rows = load()
    if not rows:
        print(f"no data. put CSV chunks at {PATTERN}")
        return 1
    cov = coverage(rows)
    print(f"{cov['total']} questions from {len(sources())} file(s)")
    for b, n in cov["branch"].most_common():
        topics = cov["topic"][b]
        print(f"  {n:5d}  {b}   ({len(topics)} topics)")
        if "--topics" in sys.argv:
            for t, k in topics.most_common():
                print(f"           {k:4d}  {t}")
    print("  difficulty: " + ", ".join(f"{d} {n}" for d, n in cov["difficulty"].items()))

    a = audit(rows)
    print(f"\naudit (independent re-derivation of answer_value)")
    print(f"  agreed     {len(a['agreed']):5d}   safe to grade against")
    print(f"  disagreed  {len(a['disagreed']):5d}   quarantined")
    print(f"  unaudited  {len(a['unaudited']):5d}   no oracle yet, not assumed correct")
    for q, got, why in a["disagreed"]:
        print(f"    {q.id} [{q.tags}] {why}")
    if "--unaudited" in sys.argv:
        miss = Counter(q.tags for q in a["unaudited"])
        print("  missing oracles by tag:")
        for t, n in miss.most_common():
            print(f"    {n:4d}  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
