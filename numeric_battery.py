#!/usr/bin/env python3
"""
numeric_battery.py - the first correctness score this project is allowed to report.

WHY THIS IS DIFFERENT FROM question_bank.py
-------------------------------------------
question_bank.py scores REACH: did EngineAI produce a real, grounded answer.
It deliberately refuses to report correctness, because its corpus is questions
only and any correctness figure would have been the system marking its own
homework.

This battery grades against answer_value from the CSV bank - and only against
the rows qbank_csv.audit() re-derived independently and agreed with. Rows with
no oracle are NOT graded. Grading against an unverified key just moves the
trust problem one file to the left.

THE TRAP IN SCORING FREE TEXT
-----------------------------
A model answers in prose. Pulling "the" number out of prose is where a scoring
harness invents its own results, in both directions:

  * Too strict, and correct answers phrased unusually are marked wrong.
  * Too lenient - "does the right number appear anywhere?" - and a wrong
    answer scores a point because the correct value happened to appear in the
    working, or because the question restated it.

So this reports BOTH and never collapses them:

  STRICT   the number the answer settles on (after a final-answer marker, or
           else the last number in the text) is right.
  LENIENT  the right number appears somewhere in the response.

LENIENT is an upper bound, not a score. The honest headline is STRICT. Where
they differ by a lot, the model knows the physics and is being marked down by
the extractor - which is a finding about the harness, and it is reported as
one rather than quietly split down the middle.

NO_NUMBER is its own bucket. A response that never produced a figure is a
different failure from one that produced the wrong figure, and merging them
would hide whether the problem is the physics or the formatting.

Run:  ./run_in_eng.sh python numeric_battery.py --selftest
      ./run_in_eng.sh python numeric_battery.py --n 60
      ./run_in_eng.sh python numeric_battery.py --full
      ./run_in_eng.sh python numeric_battery.py --report
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

RESULTS = os.path.join(HERE, "data", "numeric_results.jsonl")

# The bank's own `answer` field is rounded to about four significant figures
# while answer_value is full precision, and a model that rounds sensibly must
# not be marked wrong for it. 1% absorbs display rounding without absorbing
# real error - a wrong formula is almost never within 1%.
TOL = 0.01

# Topics whose answer is a STRING in a non-decimal base. answer_value stores
# 1001000.0 for binary 1001000, so a numeric comparison there is meaningless.
STRING_TOPICS = {"Number Systems"}

CORRECT, WRONG, NO_NUMBER, REFUSED, ERROR = \
    "CORRECT", "WRONG", "NO_NUMBER", "REFUSED", "ERROR"

# Markers after which a model states its conclusion. Ordered longest-first so
# "final answer" wins over "answer".
_MARKERS = [
    r"final answer\s*(?:is)?\s*[:=]?", r"the answer is", r"answer\s*[:=]",
    r"therefore\s*,?", r"hence\s*,?", r"so\s*,?\s*the", r"result\s*[:=]",
    r"=\s*",
]
_NUM = r"[-+]?\d[\d,]*\.?\d*(?:\s*[eE]\s*[-+]?\d+)?"


def _clean(tok: str) -> float:
    return float(re.sub(r"[,\s]", "", tok))


def numbers_in(text: str) -> list:
    """Every number in the text, in order, commas and exponents handled."""
    out = []
    for m in re.finditer(_NUM, text or ""):
        try:
            out.append(_clean(m.group()))
        except ValueError:
            pass
    return out


def settled_number(text: str):
    """The number the answer SETTLES on, or None.

    Prefers the first number following a final-answer marker; falls back to the
    last number in the text, which is where a worked solution ends up.
    """
    if not text:
        return None
    low = text.lower()
    best_pos, best_val = -1, None
    for mk in _MARKERS:
        for m in re.finditer(mk, low):
            tail = text[m.end():m.end() + 120]
            nums = re.search(_NUM, tail)
            if nums and m.start() > best_pos:
                try:
                    best_pos, best_val = m.start(), _clean(nums.group())
                except ValueError:
                    pass
    if best_val is not None:
        return best_val
    nums = numbers_in(text)
    return nums[-1] if nums else None


def close(got, want, tol: float = TOL) -> bool:
    if got is None or want is None:
        return False
    if not math.isfinite(got) or not math.isfinite(want):
        return False
    if want == 0:
        return abs(got) < tol
    return abs(got - want) / abs(want) <= tol


def grade(q, answer: str, basis: str = "", tol: float = TOL) -> dict:
    """Grade one response. Returns the outcome and everything behind it."""
    if q.topic in STRING_TOPICS:
        # Compare the literal token: "930" as hexadecimal is not 930 decimal.
        want_s = str(q.answer).strip().split()[0]
        # Word boundaries matter here. Without them "[0-9A-Fa-f]+" matches the
        # 'e' in "the" and the 'a' in "hexadecimal", so the last "number" in
        # "the result is 930 in hexadecimal" came out as a letter and a correct
        # answer was graded WRONG. Bounded tokens, and then only tokens that
        # contain a digit or ARE the expected answer, since a bare "face" or
        # "cab" is an English word that happens to be valid hex.
        found = re.findall(r"\b[0-9A-Fa-f]+\b", answer or "")
        cand = [f for f in found
                if any(ch.isdigit() for ch in f) or f.upper() == want_s.upper()]
        hit = any(f.upper() == want_s.upper() for f in cand)
        tail = cand[-1] if cand else None
        return {"outcome": CORRECT if (tail and tail.upper() == want_s.upper())
                else (WRONG if cand else NO_NUMBER),
                "lenient": hit, "got": tail, "want": want_s, "mode": "string"}

    settled = settled_number(answer)
    allnums = numbers_in(answer)
    want = q.answer_value
    strict = close(settled, want, tol)
    lenient = any(close(n, want, tol) for n in allnums)

    if basis in ("CLARIFY", "MODEL_REQUIRED") and settled is None:
        out = REFUSED
    elif settled is None:
        out = NO_NUMBER
    else:
        out = CORRECT if strict else WRONG
    return {"outcome": out, "lenient": lenient, "got": settled, "want": want,
            "n_numbers": len(allnums), "mode": "numeric"}


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------
#
# WHICH DOOR YOU KNOCK ON DECIDES WHAT YOU MEASURE. The first live run of this
# battery scored 0.0%, and the number was meaningless: 15 of 30 answers came
# back with basis COMPUTED and NO number, because chat.respond() said
#
#     "This is heat transfer. I will compute it and return a result with a
#      receipt."
#
# chat.respond is a ROUTER. It classifies the question and promises a solver;
# it does not call one. Reporting its output as EngineAI's correctness would
# have been the fourth time in this project that a measurement described the
# apparatus instead of the system.
#
# So the engine is now explicit and named in every result file. What is
# missing - and it is the real gap, not a harness detail - is an executor:
# nl_router already returns the right capability AND the extracted parameters
# for these questions, and nothing takes that last step of running the module
# and returning the number.

def engine_chat(text):
    import chat
    return chat.respond(text)


def engine_direct(text):
    """direct.py's deterministic router. Narrow by design - beams, chambers,
    lattices, CAD - so most exam questions fall straight through it."""
    import direct
    r = direct.route(text)
    if not r:
        return {"answer": "", "basis": "NO_ROUTE"}
    return {"answer": json.dumps(r, default=str), "basis": "COMPUTED"}


ENGINES = {"chat": engine_chat, "direct": engine_direct}


def gradeable() -> list:
    """Only the rows whose answer_value was independently re-derived."""
    import qbank_csv
    return qbank_csv.audit()["agreed"]


def sample(rows, n, seed=7):
    """Stratified by branch, with a floor, so a big branch cannot crowd out a
    small one and make the headline a statement about Mechanical alone."""
    if n is None or n >= len(rows):
        return list(rows)
    by = defaultdict(list)
    for q in rows:
        by[q.branch].append(q)
    rnd = random.Random(seed)
    for v in by.values():
        rnd.shuffle(v)
    floor = max(1, n // (len(by) * 2))
    picked, pool = [], []
    for b, v in by.items():
        picked.extend(v[:floor])
        pool.extend(v[floor:])
    rnd.shuffle(pool)
    picked.extend(pool[:max(0, n - len(picked))])
    return picked[:n]


def load_results(path=RESULTS) -> list:
    rows, seen = [], set()
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("id") in seen:
                continue
            seen.add(r["id"])
            rows.append(r)
    return rows


def run(n=None, path=RESULTS, fresh=False, quiet=False, tol=TOL, ask=None,
        engine="chat"):
    if ask is None:
        if engine not in ENGINES:
            raise KeyError(f"unknown engine {engine!r}. Known: {', '.join(ENGINES)}")
        ask = ENGINES[engine]
    rows = gradeable()
    if fresh and os.path.exists(path):
        os.remove(path)
    done = {r["id"] for r in load_results(path)}
    todo = [q for q in sample(rows, n) if q.id not in done]
    if not quiet:
        print(f"{len(rows)} gradeable rows; {len(done)} already done; "
              f"{len(todo)} to run")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for i, q in enumerate(todo, 1):
            t0 = time.time()
            try:
                r = ask(q.question)
                # `.get("answer", "")` is not enough: chat.respond returns the
                # key present and set to None on some routes, and the default
                # only applies to a MISSING key. That crashed the first live
                # run at answer[:1500]. It is also, almost certainly, what
                # correctness_battery counts as its "silent" cases.
                answer = (r.get("answer") or "") if isinstance(r, dict) else str(r or "")
                basis = (r.get("basis") or "") if isinstance(r, dict) else ""
                g = grade(q, answer, basis, tol)
            except Exception as exc:                          # noqa: BLE001
                answer, basis = "", ""
                g = {"outcome": ERROR, "lenient": False, "got": None,
                     "want": q.answer_value, "error": f"{type(exc).__name__}: {exc}"}
            rec = {"id": q.id, "branch": q.branch, "topic": q.topic,
                   "difficulty": q.difficulty, "tags": q.tags, "basis": basis,
                   "engine": engine,
                   "seconds": round(time.time() - t0, 2),
                   "question": q.question, "answer": answer[:1500], **g}
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            if not quiet and (i % 5 == 0 or i == len(todo)):
                print(f"  {i}/{len(todo)}  {g['outcome']:<9} "
                      f"{rec['seconds']:5.1f}s  {q.topic}")
    return load_results(path)


def report(rows=None, path=RESULTS) -> dict:
    rows = rows if rows is not None else load_results(path)
    if not rows:
        return {"n": 0}
    n = len(rows)
    out = Counter(r["outcome"] for r in rows)
    strict = out[CORRECT]
    lenient = sum(1 for r in rows if r.get("lenient"))
    scored = n - out[ERROR]

    def pct(k, d=None):
        d = d or n
        return 100.0 * k / d if d else 0.0

    by_branch, by_diff, by_basis = defaultdict(Counter), defaultdict(Counter), \
        defaultdict(Counter)
    for r in rows:
        by_branch[r["branch"]][r["outcome"]] += 1
        by_diff[r["difficulty"]][r["outcome"]] += 1
        by_basis[r.get("basis") or "?"][r["outcome"]] += 1
    return {"n": n, "scored": scored, "outcomes": dict(out),
            "strict_pct": pct(strict), "lenient_pct": pct(lenient),
            "extractor_gap": pct(lenient - strict),
            "by_branch": {k: dict(v) for k, v in by_branch.items()},
            "by_difficulty": {k: dict(v) for k, v in by_diff.items()},
            "by_basis": {k: dict(v) for k, v in by_basis.items()}}


def show(rep: dict):
    if not rep.get("n"):
        print("no results yet")
        return
    o = rep["outcomes"]
    print(f"\n{rep['n']} graded against an independently re-derived answer key\n")
    print(f"  STRICT   {rep['strict_pct']:5.1f}%   the answer settles on the "
          f"right number      <-- the score")
    print(f"  LENIENT  {rep['lenient_pct']:5.1f}%   the right number appears "
          f"anywhere            (upper bound)")
    if rep["extractor_gap"] > 5:
        print(f"\n  The {rep['extractor_gap']:.1f}-point gap is answers that contain the right "
              f"number\n  but do not end on it. That is as likely to be the "
              f"extractor as the\n  model, and it is reported rather than split "
              f"down the middle.")
    print()
    for k in (CORRECT, WRONG, NO_NUMBER, REFUSED, ERROR):
        if o.get(k):
            print(f"    {k:<10} {o[k]:4d}   {100.0*o[k]/rep['n']:5.1f}%")

    for title, key in (("by branch", "by_branch"),
                       ("by difficulty", "by_difficulty"),
                       ("by basis", "by_basis")):
        print(f"\n  {title}")
        for name, c in sorted(rep[key].items(),
                              key=lambda kv: -sum(kv[1].values())):
            tot = sum(c.values())
            print(f"    {name[:36]:<38} {c.get(CORRECT,0):3d}/{tot:<3d} "
                  f"{100.0*c.get(CORRECT,0)/tot:5.1f}%")


# ---------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def chk(cond, label, extra=""):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label
              + (f"   {extra}" if extra else ""))
        ok = ok and bool(cond)

    print("numeric_battery selftest")

    # ---- extraction ----
    chk(settled_number("so the answer is 35.77 MPa") == 35.77,
        "a final-answer marker is preferred")
    chk(settled_number("A = 754.8 mm^2, therefore sigma = 35.77 MPa") == 35.77,
        "'therefore' beats an earlier working number")
    chk(settled_number("the working gives 12.5 then 99.9") == 99.9,
        "with no marker, the LAST number is taken")
    chk(settled_number("about 1,234.5 units") == 1234.5, "commas are handled")
    chk(settled_number("k = 3.7e-4 per second") == 3.7e-4,
        "exponent notation is handled")
    chk(settled_number("no digits at all here") is None,
        "prose with no number yields None, not zero")
    chk(numbers_in("1 and 2 and 3") == [1.0, 2.0, 3.0], "all numbers are found")

    # ---- grading ----
    class Q:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    q = Q(id="X", branch="B", topic="Strength of Materials", difficulty="easy",
          tags="stress", question="?", answer="35.77 MPa",
          answer_value=35.77259907164349)
    chk(grade(q, "therefore sigma = 35.77 MPa")["outcome"] == CORRECT,
        "a rounded but right answer is CORRECT")
    chk(grade(q, "sigma = 41.2 MPa")["outcome"] == WRONG, "a wrong number is WRONG")
    chk(grade(q, "I cannot determine this")["outcome"] == NO_NUMBER,
        "an answer with no number is NO_NUMBER, not WRONG")
    chk(grade(q, "needs more data", basis="CLARIFY")["outcome"] == REFUSED,
        "a genuine clarification request is REFUSED, not WRONG")

    # The distinction the whole harness rests on.
    g = grade(q, "the stress is 35.77 MPa, but I will say 99 MPa")
    chk(g["outcome"] == WRONG and g["lenient"],
        "an answer that ends on the wrong number is WRONG but counts as lenient")
    g2 = grade(q, "sigma = 35.77 MPa")
    chk(g2["outcome"] == CORRECT and g2["lenient"],
        "...and a right answer is both")

    qn = Q(id="Y", branch="C", topic="Number Systems", difficulty="easy",
           tags="dec-to-hex", question="?", answer="930", answer_value=930.0)
    chk(grade(qn, "the result is 930 in hexadecimal")["outcome"] == CORRECT,
        "a base-N answer is compared as a STRING, not as a float")
    chk(grade(qn, "the result is 2352")["outcome"] == WRONG,
        "...and the decimal input is not accepted as the answer")

    # ---- tolerance ----
    chk(close(35.77, 35.7726), "0.007% is within tolerance")
    chk(not close(36.2, 35.7726), "1.2% is outside it")
    chk(close(0.0, 0.0), "zero equals zero")

    # ---- sampling and the corpus ----
    rows = gradeable()
    chk(len(rows) > 0, f"{len(rows)} rows are safe to grade against")
    chk(all(hasattr(r, "answer_value") for r in rows), "each carries its key")
    s = sample(rows, 12)
    chk(len(s) == min(12, len(rows)), f"a sample of 12 has {len(s)} rows")
    chk(len({q.branch for q in s}) >= min(4, len({q.branch for q in rows})),
        f"the sample spans {len({q.branch for q in s})} branches, not one")
    chk([q.id for q in sample(rows, 12)] == [q.id for q in s],
        "sampling is deterministic, so two runs are comparable")

    # ---- the loop, with a stubbed model ----
    # Stubbed on purpose. A selftest that calls the live model would make
    # verify_all.sh depend on Ollama being warm, which is the exact cold-start
    # trap that has already reported a false score twice in this project.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "r.jsonl")
        # Key off the SAMPLE, not rows[:6]. run() draws a stratified sample, so
        # keying the oracle off the head of the corpus gave the "perfect" stub
        # the right answer to one question in six and scored it 16.7%.
        keyed = {q.question: q.answer_value for q in sample(rows, 6)}

        def perfect(text):
            return {"answer": f"the answer is {keyed.get(text, 0)}",
                    "basis": "COMPUTED"}

        got = run(n=6, path=p, quiet=True, ask=perfect)
        rep = report(got)
        chk(rep["n"] == 6, f"six questions were graded ({rep['n']})")
        chk(rep["strict_pct"] == 100.0,
            f"an oracle-perfect model scores 100% ({rep['strict_pct']})")

        # Resume must not re-ask, and must not double-count.
        again = run(n=6, path=p, quiet=True, ask=perfect)
        chk(len(again) == 6, "re-running does not duplicate rows")

        def useless(text):
            return {"answer": "I am not sure.", "basis": "MODEL_REQUIRED"}

        p2 = os.path.join(td, "r2.jsonl")
        rep2 = report(run(n=6, path=p2, quiet=True, ask=useless))
        chk(rep2["strict_pct"] == 0.0, "a model that says nothing scores 0%")
        chk(rep2["outcomes"].get(REFUSED, 0) == 6,
            "...and every row lands in REFUSED, not WRONG")

        # chat.respond really does return {"answer": None} on some routes, and
        # a .get default does not cover a key that is present and null. This
        # crashed the first live run 30 questions in.
        p4 = os.path.join(td, "r4.jsonl")
        rep4 = report(run(n=3, path=p4, quiet=True,
                          ask=lambda t: {"answer": None, "basis": None}))
        chk(rep4["outcomes"].get(NO_NUMBER, 0) == 3,
            "a None answer is NO_NUMBER, not a crash")
        chk(rep4["outcomes"].get(ERROR, 0) == 0,
            "...and is not mislabelled as a harness error")

        def crashes(text):
            raise RuntimeError("model is down")

        p3 = os.path.join(td, "r3.jsonl")
        rep3 = report(run(n=3, path=p3, quiet=True, ask=crashes))
        chk(rep3["outcomes"].get(ERROR, 0) == 3,
            "a dead model is ERROR, and is excluded from the scored count")
        chk(rep3["scored"] == 0, "...so a crashed run cannot report 0% as a result")

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="grade EngineAI against the audited key")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--report", action="store_true")
    p.add_argument("--n", type=int, default=None)
    p.add_argument("--full", action="store_true")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--tol", type=float, default=TOL)
    p.add_argument("--wrong", action="store_true", help="list the wrong answers")
    p.add_argument("--engine", default="chat", choices=sorted(ENGINES),
                   help="which entry point to grade; chat.respond only ROUTES")
    a = p.parse_args()

    if a.selftest:
        return selftest()
    if a.report:
        show(report())
        if a.wrong:
            print("\n  wrong answers")
            for r in load_results():
                if r["outcome"] == WRONG:
                    print(f"    {r['id']} {r['topic'][:26]:<28} "
                          f"got {r['got']} want {r['want']}")
        return 0

    rows = run(n=None if a.full else (a.n or 40), fresh=a.fresh, tol=a.tol,
               engine=a.engine)
    show(report(rows))
    if a.engine == "chat":
        promises = sum(1 for r in rows if r["outcome"] == NO_NUMBER
                       and r.get("basis") in ("COMPUTED", "ROUTED"))
        if promises:
            print(f"\n  {promises} answers had basis COMPUTED and produced no "
                  f"number.\n  chat.respond ROUTES; it names the solver and "
                  f"promises a receipt\n  without running one. This score "
                  f"describes the router, not the engine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
