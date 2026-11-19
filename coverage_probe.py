#!/usr/bin/env python3
"""coverage_probe.py - can the library actually answer the question?

WHY COUNTING IS THE WRONG METRIC. "How many books should the library have" has
no answer, because 70 books did not give coverage and 700 would not either.
What matters is whether a real question can be answered FROM the library, with
a citation, and that is measurable.

THE TRAP THAT INVALIDATES MOST SUCH MEASUREMENTS. If the test questions are
generated from the library, coverage is 100% by construction and the number is
worthless. Every question here must carry an `origin` recording where it came
from OUTSIDE the library - a textbook problem set, a standards clause title, a
real user request - and the selftest refuses a bank whose questions were
sourced from the corpus.

FOUR OUTCOMES, because one number hides the thing you need to know:

  GROUNDED    answered, with a citation to something in the library
  UNGROUNDED  answered with no source. THE DANGEROUS ONE. Not a coverage gap -
              a fabrication risk, and it gets worse as the library grows
              because plausible-sounding context makes ungrounded answers more
              convincing rather than less.
  REFUSED     honestly declined. A correct outcome, not a failure.
  MISSED      the library CONTAINS the answer and retrieval did not find it.

THE DISTINCTION THAT DECIDES A LARGE DOWNLOAD. MISSED is a retrieval defect;
absent-from-library is an acquisition problem. If most failures are MISSED then
adding 800 GB fixes nothing and makes retrieval harder. Only genuine absence
justifies acquisition, and the per-discipline breakdown then says WHICH 80 GB
to fetch rather than all 800.

ESTIMATING WHAT IS STILL MISSING. Capture-recapture, borrowed from ecology and
used in systematic-review methodology to estimate missed studies: run the same
questions against two INDEPENDENT sources and the overlap estimates the size of
what neither found. Lincoln-Petersen with Chapman's small-sample correction. It
gives a defensible figure for the unseen fraction without knowing the universe,
which is otherwise impossible.
"""
from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

__all__ = ["Question", "Outcome", "QuestionBank", "measure", "chapman_estimate",
           "GROUNDED", "UNGROUNDED", "REFUSED", "MISSED"]

GROUNDED = "GROUNDED"
UNGROUNDED = "UNGROUNDED"
REFUSED = "REFUSED"
MISSED = "MISSED"

OUT = Path(__file__).parent / "research_output"


@dataclass
class Question:
    text: str
    discipline: str
    origin: str                 # where it came from, OUTSIDE the library
    expects_answer: bool = True  # False for deliberately unanswerable probes
    known_in_library: bool | None = None   # set only when independently known

    def __post_init__(self):
        if not self.origin.strip():
            raise ValueError(
                f"every question needs an origin outside the library, "
                f"or coverage is measured against itself: {self.text[:50]}")


@dataclass
class Outcome:
    question: Question
    result: str
    citation: str = ""
    answer: str = ""

    def __str__(self) -> str:
        c = f"  [{self.citation[:40]}]" if self.citation else ""
        return f"    {self.result:10s} {self.question.discipline:14s} " \
               f"{self.question.text[:52]}{c}"


class QuestionBank:
    """Questions whose provenance is outside the library being measured."""

    def __init__(self, questions=None):
        self.questions = list(questions or [])

    def add(self, text, discipline, origin, expects_answer=True,
            known_in_library=None) -> "QuestionBank":
        self.questions.append(Question(text, discipline, origin,
                                       expects_answer, known_in_library))
        return self

    @property
    def origins(self) -> set:
        return {q.origin for q in self.questions}

    @property
    def disciplines(self) -> set:
        return {q.discipline for q in self.questions}

    def independent_of(self, corpus_markers=("research_corpus", "library/",
                                             "vault", "generated")) -> bool:
        """No question may have been sourced from the corpus it tests."""
        return not any(any(m in q.origin.lower() for m in corpus_markers)
                       for q in self.questions)


# --------------------------------------------------------------------------
# capture-recapture
# --------------------------------------------------------------------------

def chapman_estimate(n1: int, n2: int, overlap: int) -> dict:
    """Lincoln-Petersen with Chapman's correction, for small samples.

    N_hat = (n1+1)(n2+1)/(m+1) - 1

    Two independent searches answer n1 and n2 questions with m in common. If
    they overlap heavily, they are both seeing most of what exists; if barely,
    a great deal is invisible to both. The plain Lincoln-Petersen estimator is
    biased and undefined at zero overlap, which is exactly the case that
    matters here, hence Chapman.
    """
    if n1 <= 0 or n2 <= 0:
        return {"estimated_total": None,
                "reason": "one source found nothing - no estimate possible"}
    n_hat = (n1 + 1) * (n2 + 1) / (overlap + 1) - 1
    var = ((n1 + 1) * (n2 + 1) * (n1 - overlap) * (n2 - overlap) /
           ((overlap + 1) ** 2 * (overlap + 2)))
    se = math.sqrt(var) if var > 0 else 0.0
    seen = n1 + n2 - overlap
    return {
        "estimated_total": n_hat,
        "observed": seen,
        "estimated_missing": max(0.0, n_hat - seen),
        "standard_error": se,
        "ci95": (max(seen, n_hat - 1.96 * se), n_hat + 1.96 * se),
        "assumption_warning": (
            "capture-recapture assumes the two sources are INDEPENDENT and that "
            "every item is equally findable. Two search engines over the same "
            "index violate the first; rare topics violate the second. Treat "
            "this as an order of magnitude, not a count."),
    }


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------

def measure(bank: QuestionBank, answerer: Callable[[str], dict],
            library_contains: Callable[[str], bool] | None = None,
            verbose: bool = True) -> dict:
    """Run the bank and classify every outcome.

    answerer(text) must return {"answered": bool, "citation": str,
    "answer": str}. library_contains(text), when supplied, decides MISSED
    versus genuinely absent - which is the distinction that decides whether to
    acquire more data or fix retrieval.
    """
    outcomes = []
    for q in bank.questions:
        try:
            r = answerer(q.text) or {}
        except Exception as e:
            r = {"answered": False, "error": f"{type(e).__name__}: {e}"}
        answered = bool(r.get("answered"))
        cite = str(r.get("citation") or "")

        if answered and cite:
            res = GROUNDED
        elif answered and not cite:
            res = UNGROUNDED
        else:
            in_lib = (library_contains(q.text) if library_contains
                      else q.known_in_library)
            res = MISSED if in_lib else REFUSED
        outcomes.append(Outcome(q, res, cite, str(r.get("answer", ""))[:200]))
        if verbose:
            print(outcomes[-1])

    counts = {k: sum(1 for o in outcomes if o.result == k)
              for k in (GROUNDED, UNGROUNDED, REFUSED, MISSED)}
    n = len(outcomes) or 1

    by_disc = {}
    for d in sorted(bank.disciplines):
        rows = [o for o in outcomes if o.question.discipline == d]
        by_disc[d] = {
            "n": len(rows),
            "grounded": sum(1 for o in rows if o.result == GROUNDED),
            "ungrounded": sum(1 for o in rows if o.result == UNGROUNDED),
            "missed": sum(1 for o in rows if o.result == MISSED),
            "refused": sum(1 for o in rows if o.result == REFUSED),
        }

    # the acquisition decision
    failures = counts[MISSED] + counts[REFUSED]
    if failures == 0:
        verdict = "the bank is fully covered - a bigger bank is needed to learn more"
    elif counts[MISSED] > counts[REFUSED]:
        verdict = ("MOST FAILURES ARE RETRIEVAL, NOT ABSENCE. The library "
                   "already holds these answers and search is not finding "
                   "them. Acquiring more data will not help and will make "
                   "retrieval harder - fix retrieval first.")
    else:
        worst = sorted(by_disc.items(),
                       key=lambda kv: -(kv[1]["refused"]))[:3]
        verdict = ("failures are genuine absence. Acquire selectively, "
                   "worst-covered first: "
                   + ", ".join(f"{d} ({v['refused']}/{v['n']})" for d, v in worst))

    return {
        "n_questions": len(outcomes),
        "counts": counts,
        "grounded_rate": counts[GROUNDED] / n,
        "ungrounded_rate": counts[UNGROUNDED] / n,
        "by_discipline": by_disc,
        "outcomes": outcomes,
        "origins": sorted(bank.origins),
        "independent": bank.independent_of(),
        "verdict": verdict,
        "note": ("UNGROUNDED is the number to watch. It is not a coverage gap, "
                 "it is a fabrication risk, and it gets WORSE as the library "
                 "grows because plausible retrieved context makes an ungrounded "
                 "answer more convincing rather than less."),
    }


def report(res: dict) -> str:
    c = res["counts"]
    lines = [
        f"  {res['n_questions']} questions from {len(res['origins'])} outside origins",
        f"    GROUNDED   {c[GROUNDED]:4d}  ({res['grounded_rate']*100:.0f}%)  answered with a citation",
        f"    UNGROUNDED {c[UNGROUNDED]:4d}  ({res['ungrounded_rate']*100:.0f}%)  answered with NO source <-- fabrication risk",
        f"    MISSED     {c[MISSED]:4d}        in the library, retrieval failed",
        f"    REFUSED    {c[REFUSED]:4d}        honestly declined",
        "",
        "  by discipline:",
    ]
    for d, v in sorted(res["by_discipline"].items(),
                       key=lambda kv: kv[1]["grounded"] / max(kv[1]["n"], 1)):
        lines.append(f"    {d:16s} {v['grounded']}/{v['n']} grounded, "
                     f"{v['ungrounded']} ungrounded, {v['missed']} missed")
    lines += ["", f"  VERDICT: {res['verdict']}", "", f"  {res['note']}"]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    print("== a question without an outside origin is refused ==")
    try:
        Question("what is E?", "materials", "")
        raised = False
    except ValueError:
        raised = True
    chk(raised, "an empty origin is rejected at construction")

    bank = QuestionBank()
    bank.add("deflection of a cantilever under tip load", "mechanics",
             "Hibbeler Mechanics of Materials, ch.12 problem set")
    bank.add("hoop stress in a thin pressure vessel", "mechanics",
             "Hibbeler ch.8")
    bank.add("critical temperature of YBCO", "materials",
             "user request, 2026-08")
    bank.add("fatigue limit of 316L at R=-1", "materials",
             "user request, 2026-08")
    bank.add("Bishop slope stability factor", "geotechnics",
             "Craig Soil Mechanics ch.9")
    chk(bank.independent_of(), "a bank sourced from textbooks is independent")

    tainted = QuestionBank()
    tainted.add("what does the corpus say about beams", "mechanics",
                "generated from research_corpus/manifest.json")
    chk(not tainted.independent_of(),
        "a bank generated FROM the corpus is caught as not independent")

    print("== the four outcomes are distinguished ==")
    def answerer(text):
        if "cantilever" in text:
            return {"answered": True, "citation": "Roark 7e Table 8.1",
                    "answer": "PL^3/3EI"}
        if "hoop" in text:
            return {"answered": True, "citation": "", "answer": "pr/t"}
        if "YBCO" in text:
            return {"answered": False}
        if "fatigue" in text:
            return {"answered": False}
        return {"answered": False}

    # YBCO IS in the library (retrieval failed); fatigue and Bishop are not
    in_lib = lambda t: "YBCO" in t
    res = measure(bank, answerer, library_contains=in_lib, verbose=False)
    c = res["counts"]
    chk(c[GROUNDED] == 1, f"one grounded answer ({c[GROUNDED]})")
    chk(c[UNGROUNDED] == 1, f"one ungrounded answer ({c[UNGROUNDED]})")
    chk(c[MISSED] == 1, f"one retrieval miss ({c[MISSED]})")
    chk(c[REFUSED] == 2, f"two honest refusals ({c[REFUSED]})")
    chk(sum(c.values()) == 5, "every question is accounted for")

    print("== ungrounded is called a fabrication risk, not a gap ==")
    chk("fabrication risk" in res["note"], "the note says so")
    chk("worse as the library grows" in res["note"] or
        "WORSE as the library grows" in res["note"],
        "and warns it worsens with more data")

    print("== the verdict decides acquisition versus retrieval ==")
    # mostly retrieval failures -> do NOT acquire
    b2 = QuestionBank()
    for i in range(6):
        b2.add(f"question {i}", "mechanics", "textbook")
    r2 = measure(b2, lambda t: {"answered": False},
                 library_contains=lambda t: True, verbose=False)
    chk(r2["counts"][MISSED] == 6, "all six are retrieval misses")
    chk("RETRIEVAL, NOT ABSENCE" in r2["verdict"],
        "and the verdict says fix retrieval")
    chk("will not help" in r2["verdict"],
        "explicitly advising against acquiring more data")

    # genuine absence -> acquire, worst discipline named
    b3 = QuestionBank()
    for i in range(4):
        b3.add(f"geo question {i}", "geotechnics", "textbook")
    b3.add("mech question", "mechanics", "textbook")
    r3 = measure(b3, lambda t: ({"answered": True, "citation": "Roark"}
                                if "mech" in t else {"answered": False}),
                 library_contains=lambda t: False, verbose=False)
    chk("genuine absence" in r3["verdict"], "genuine gaps advise acquisition")
    chk("geotechnics" in r3["verdict"],
        f"and name the worst-covered discipline ({r3['verdict'][-60:]})")

    print("== capture-recapture ==")
    # two sources, 60 and 50 answers, 30 in common -> N ~ (61*51)/31 - 1 ~ 99.3
    est = chapman_estimate(60, 50, 30)
    chk(abs(est["estimated_total"] - 99.35) < 0.5,
        f"Chapman estimate is right ({est['estimated_total']:.2f})")
    chk(est["observed"] == 80, "observed is the union, not the sum")
    chk(est["estimated_missing"] > 19, "and roughly 19 are invisible to both")
    # heavy overlap means little is missing
    est2 = chapman_estimate(50, 50, 50)
    chk(est2["estimated_missing"] < 1.0,
        f"complete overlap implies nothing missing ({est2['estimated_missing']:.2f})")
    # zero overlap is where plain Lincoln-Petersen breaks, Chapman does not
    est3 = chapman_estimate(10, 10, 0)
    chk(est3["estimated_total"] is not None and est3["estimated_total"] > 100,
        f"zero overlap is defined and implies a large unseen set "
        f"({est3['estimated_total']:.0f})")
    chk("INDEPENDENT" in est3["assumption_warning"],
        "and the independence assumption is stated")
    chk(chapman_estimate(0, 10, 0)["estimated_total"] is None,
        "a source that found nothing yields no estimate")

    print("== report renders ==")
    txt = report(res)
    chk("GROUNDED" in txt and "VERDICT" in txt, "the report has both")
    chk("fabrication risk" in txt, "and surfaces the dangerous bucket")

    print(f"\ncoverage_probe.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(__doc__)
