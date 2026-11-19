#!/usr/bin/env python3
"""understand.py - stop pattern-matching intent, stop recalling facts.

TWO TREADMILLS THIS ENDS.

THE FIRST is the regex table. scope.py grew keyword patterns until a beam
question routed to web development because "react" matched inside "reactions".
I diagnosed that, wrote that English has unbounded ways to say the same thing
while a table has finite rows - and then built DEFINITION as another table and
widened it twice when it missed "when is" and "does". Same wall, same solution
available the whole time: let the model CLASSIFY the intent, from a closed list,
while never producing the answer. A classifier generalises to phrasings nobody
enumerated. A table cannot, ever.

THE SECOND is more important. The correctness battery reported "the required
fact is absent" eleven times: the model did not know the Strouhal number is
0.21, or that Blasius holds between Re 3000 and 100000. No amount of code fixes
that, because the fact lives in the model's weights and those are fixed. Coding
each answer in would not be an engineering AI, it would be a lookup table with
a language model bolted on.

The fix is retrieval. research_knowledge.py holds 162 named laws WITH their
constants, exponents and validity bands. equations.py holds 109 more. Retrieve
what is relevant, hand it to the model as context, and it is no longer recalling
from weights - it is reading from a verified source. Adding a law then improves
every answer that touches it, with no code change anywhere. That is the property
that makes this scale and a hardcoded answer table not.

AND THEN CHECK THE ANSWER AGAINST WHAT WAS RETRIEVED. A model given the right
fact can still restate it wrongly. If the retrieved law says the exponent is
-0.5 and the answer says "linear", the answer is contradicted by its own
source and is flagged, not returned quietly.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field

__all__ = ["Intent", "classify_intent", "retrieve", "explain_grounded",
           "INTENTS", "check_against_sources"]

INTENTS = {
    "DEFINE": "asks what a concept IS, what it means, when it applies, which "
              "direction an effect goes, or how two things differ",
    "COMPUTE": "asks for a NUMBER from the user's own values",
    "RESEARCH": "asks to find a relationship, optimise, or explore a dataset",
    "WRITE": "asks for a document - a report, an email, a translation",
    "META": "asks about this system itself",
    "OTHER": "none of the above",
}

_SYSTEM = """Classify the user's request into exactly one category.

DEFINE   - what a concept IS or MEANS; when it applies; its validity range;
           which direction an effect goes; how two things differ; why
           something happens. The answer is an explanation.
COMPUTE  - a number is wanted, from values the user supplied. The answer is a
           calculation.
RESEARCH - find a relationship in data, optimise something, explore a design
           space.
WRITE    - produce a document: report, email, blog post, translation.
META     - about the assistant itself.
OTHER    - none of these.

Reply with ONLY the single word. No punctuation, no explanation."""


@dataclass
class Intent:
    label: str
    confidence: str              # model | fallback
    reason: str = ""


def _ask(prompt: str, system: str, model=None, timeout: int = 60,
         predict: int = 400, temperature: float = 0.0) -> str | None:
    try:
        import nl_router
        if not nl_router.available():
            return None
        body = json.dumps({
            "model": model or nl_router.MODEL, "prompt": prompt,
            "system": system, "stream": False,
            "options": {"temperature": temperature, "num_predict": predict},
        }).encode()
        req = urllib.request.Request(
            f"{nl_router.OLLAMA}/api/generate", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()).get("response", "").strip()
    except Exception:
        return None


def classify_intent(text: str) -> Intent:
    """Ask the model what KIND of request this is. Closed list, one word.

    Falls back to a small pattern set only when no model is reachable, so the
    behaviour degrades to what it was rather than to a guess.
    """
    out = _ask(text, _SYSTEM, predict=6)
    if out:
        word = re.sub(r"[^A-Z]", "", out.upper())
        for k in INTENTS:
            if word.startswith(k) or k in word:
                return Intent(k, "model")
    low = (text or "").lower()
    if re.search(r"^(what|define|explain|describe|when|why|how|does|is|are)\b",
                 low):
        return Intent("DEFINE", "fallback", "no model - matched a fallback pattern")
    return Intent("OTHER", "fallback", "no model reachable")


# --------------------------------------------------------------------------
# retrieval
# --------------------------------------------------------------------------

_STOP = {"what", "is", "are", "the", "a", "an", "of", "for", "in", "to", "and",
         "does", "do", "when", "why", "how", "explain", "define", "describe",
         "difference", "between", "can", "you", "use", "used", "with", "it",
         "its", "this", "that", "on", "at", "by", "from", "if", "or"}


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9\-]{3,}", (text or "").lower())
            if w not in _STOP}


def retrieve(question: str, k: int = 4) -> list:
    """Pull the most relevant verified facts out of the library.

    Scored on token overlap against every field of each law - name, formula,
    validity, note - because "when is Blasius valid" must reach an entry whose
    NAME is Blasius and whose answer lives in its VALIDITY field.
    """
    try:
        import research_knowledge as rk
    except Exception:
        return []
    q = _tokens(question)
    if not q:
        return []
    scored = []
    for law in rk.LAWS:
        hay = " ".join((law.name, law.formula, law.discipline, law.target,
                        law.validity, law.note, law.constant_note))
        toks = _tokens(hay)
        overlap = len(q & toks)
        if not overlap:
            continue
        # a name hit is worth more than an incidental word in a note
        name_hit = 2 if _tokens(law.name) & q else 0
        scored.append((overlap + name_hit, law))
    scored.sort(key=lambda t: -t[0])
    return [law for _s, law in scored[:k]]


def _render(laws) -> str:
    out = []
    for law in laws:
        bits = [f"- {law.name}: {law.formula}"]
        if law.constant is not None:
            bits.append(f"  constant: {law.constant_note or law.constant}")
        if law.exponents:
            bits.append("  exponents: " + ", ".join(
                f"{v}^{e:g}" for v, e in law.exponents.items()))
        if law.validity:
            bits.append(f"  valid for: {law.validity}")
        if law.note:
            bits.append(f"  note: {law.note}")
        out.append("\n".join(bits))
    return "\n".join(out)


_GROUNDED_SYSTEM = """You are an engineering reference.

You are given VERIFIED FACTS from a curated library. Use them as the authority.

Rules:
- If the facts answer the question, use them and state the numbers and
  exponents EXACTLY as given. Do not round an exponent or restate -0.5 as
  "linear".
- If the facts are only partly relevant, use what applies and answer the rest
  from general knowledge, but say which part is not from the library.
- If the facts contradict what you were going to say, the FACTS WIN.
- Do not invent numbers about any specific component the user owns.
- 3-6 sentences. No preamble, no markdown headings."""


def check_against_sources(answer: str, laws) -> list:
    """Does the answer contradict a fact it was handed?

    A model given the right number can still restate it wrongly, and that is
    the failure worth catching: an answer contradicted by its own cited source
    is worse than one with no source at all, because it looks grounded.
    """
    problems = []
    a = " ".join((answer or "").lower().split())
    for law in laws:
        for var, e in (law.exponents or {}).items():
            if abs(e + 0.5) < 1e-9:                # an inverse square root
                if re.search(r"inverse linear|linearly with|proportional to 1/"
                             + re.escape(var.lower()) + r"\b", a):
                    problems.append(
                        f"{law.name}: the answer says linear, but the library "
                        f"gives {var}^-1/2 (inverse square root)")
            if abs(e - 4) < 1e-9 and re.search(r"\bsquare of\b", a):
                problems.append(
                    f"{law.name}: the answer says square, but the library "
                    f"gives {var}^4")
        if law.constant is not None and 0.001 < abs(law.constant) < 1e6:
            # a nearby-but-wrong constant is worth flagging
            nums = [float(x) for x in re.findall(r"\d+\.\d+", a)]
            close = [n for n in nums
                     if 0 < abs(n - law.constant) / abs(law.constant) < 0.5]
            if close and not any(abs(n - law.constant) / abs(law.constant) < 0.02
                                 for n in nums):
                problems.append(
                    f"{law.name}: the answer cites {close[0]:g} where the "
                    f"library gives {law.constant:g}")
    return problems


def explain_grounded(question: str, k: int = 4, model=None) -> dict | None:
    """Retrieve verified facts, then explain USING them.

    The difference from asking the model directly is the whole point: it is no
    longer recalling from weights, it is reading from a source that can be
    corrected. Adding a law to the library improves this answer with no code
    change.
    """
    laws = retrieve(question, k=k)
    if laws:
        prompt = (f"VERIFIED FACTS FROM THE LIBRARY:\n{_render(laws)}\n\n"
                  f"QUESTION: {question}\n\nAnswer:")
        system = _GROUNDED_SYSTEM
    else:
        prompt = question
        system = (_GROUNDED_SYSTEM +
                  "\n\nNo library facts were found for this question. Answer "
                  "from general knowledge and say that it is not from the "
                  "verified library.")
    out = _ask(prompt, system, model=model, predict=380, temperature=0.1)
    if not out or len(out) < 40:
        return None
    contradictions = check_against_sources(out, laws)
    return {
        "answer": out,
        "sources": [law.name for law in laws],
        "n_sources": len(laws),
        "grounded": bool(laws),
        "contradictions": contradictions,
        "note": ("answered from the verified library"
                 if laws and not contradictions else
                 "CONTRADICTS ITS OWN SOURCE - " + "; ".join(contradictions)
                 if contradictions else
                 "no library entry matched, so this is the model's own "
                 "knowledge and is NOT verified"),
    }


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

    print("== retrieval reaches the right law regardless of phrasing ==")
    cases = [
        ("when is the Blasius correlation valid", "Blasius"),
        ("what is the Strouhal number for a cylinder", "Strouhal"),
        ("when can I use lumped capacitance", "Biot"),
        ("is Hazen-Williams ok for oil", "Hazen-Williams"),
        ("how does grain size affect yield strength", "Hall-Petch"),
        ("what sets radiated heat", "Stefan-Boltzmann"),
        ("does pipe radius matter much for laminar flow", "Poiseuille"),
    ]
    for q, want in cases:
        names = [law.name for law in retrieve(q, k=5)]
        chk(any(want.lower() in n.lower() for n in names),
            f"'{q[:38]}' -> {want} (got {names[:3]})")

    print("== retrieval scores a name hit above an incidental word ==")
    got = [law.name for law in retrieve("Blasius", k=3)]
    chk(got and "Blasius" in got[0], f"the named law ranks first ({got[:2]})")
    chk(retrieve("xyzzy plugh", k=3) == [],
        "an unrelated question retrieves nothing rather than noise")

    print("== the contradiction checker catches a restated-wrong fact ==")
    import research_knowledge as rk
    hp = [law for law in rk.LAWS if law.name == "Hall-Petch"]
    bad = check_against_sources(
        "It describes the inverse linear dependence of yield strength on "
        "grain size d.", hp)
    chk(bad, f"the exact error the model made is caught ({bad[:1]})")
    good = check_against_sources(
        "Yield strength rises as d^(-1/2), an inverse square root of grain "
        "size.", hp)
    chk(not good, "the correct statement is not flagged")

    pois = [law for law in rk.LAWS if "Poiseuille" in law.name]
    bad2 = check_against_sources(
        "Flow scales with the square of the radius r.", pois)
    chk(bad2, f"square-instead-of-fourth is caught ({bad2[:1]})")

    print("== intent classification, if a model is running ==")
    try:
        import nl_router
        live = nl_router.available()
    except Exception:
        live = False
    if live:
        expect = [
            ("what is creep", "DEFINE"),
            ("when is Stokes drag valid", "DEFINE"),
            ("does deflection increase with Young's modulus", "DEFINE"),
            ("deflection of a 6 m beam carrying 15 kN", "COMPUTE"),
            ("write me a blog post about beams", "WRITE"),
            ("find the relationship between w/c ratio and strength in my data",
             "RESEARCH"),
        ]
        hits = 0
        for q, want in expect:
            got = classify_intent(q)
            hits += got.label == want
            if got.label != want:
                print(f"    {q[:44]!r} -> {got.label}, wanted {want}")
        chk(hits >= len(expect) - 1,
            f"intent classified correctly {hits}/{len(expect)} - and these "
            f"phrasings were never enumerated anywhere")

        print("== grounded explanation uses the library ==")
        r = explain_grounded("when is the Blasius correlation valid")
        chk(r is not None, "an answer came back")
        if r:
            chk(r["grounded"], f"it cited sources ({r['sources'][:2]})")
            chk(re.search(r"3000|100000|1e5|10\^5", r["answer"]),
                f"and it contains the band from the library: "
                f"{r['answer'][:100]}")
            chk(not r["contradictions"],
                f"with no contradiction ({r['contradictions']})")

        r2 = explain_grounded("what is the Strouhal number for a cylinder")
        if r2:
            chk(re.search(r"0\.2", r2["answer"]),
                f"the Strouhal constant is now present: {r2['answer'][:90]}")

        r3 = explain_grounded("what is the Hall-Petch relationship")
        if r3:
            chk(re.search(r"square root|-1/2|\^-0?\.5|d\^\(-1/2\)",
                          r3["answer"], re.I),
                f"Hall-Petch is now right: {r3['answer'][:110]}")
    else:
        print("    (no model reachable - retrieval and checking tested above)")

    print(f"\nunderstand.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    q = " ".join(a for a in sys.argv[1:] if not a.startswith("-"))
    if not q:
        print(__doc__)
        raise SystemExit(0)
    print(f"intent: {classify_intent(q).label}")
    r = explain_grounded(q)
    if r:
        print(f"sources: {r['sources']}")
        print(f"\n{r['answer']}\n")
        print(f"note: {r['note']}")
