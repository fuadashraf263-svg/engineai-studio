#!/usr/bin/env python3
"""nl_router.py - a language model that reads, and never calculates.

THE PROBLEM IT SOLVES. Keyword routing cannot generalise. One beam question
posed ten ways a real engineer would phrase it reached the solver four times;
"joist", "UB", "clear span", "gap", "what do I specify" and "strong enough" all
failed on wording alone. Every previous fix was another keyword, and English
has unbounded ways to say the same thing while a regex table has a finite
number of rows.

THE ARRANGEMENT. A local model reads the request in any phrasing or language
and returns ONLY two things: which capability is being asked for, and which
numbers the user supplied. The solver then computes. The model never sees a
formula and never emits a result.

FOUR GUARDS, because a language model will confidently make things up:

  1  CLOSED VOCABULARY. The capability it names must already exist in
     EngineAI's registry. A capability the model invents is discarded, not
     created.

  2  NUMBERS MUST BE THE USER'S. Every parameter value is checked back against
     the original text. If the model returns a span of 6 m for a request that
     never mentioned 6, the parameter is dropped - that is a hallucinated
     input, and a hallucinated input produces a real-looking wrong answer.

  3  NO ANSWER FIELD SURVIVES. Anything resembling a computed result is
     stripped from the model's reply before it goes anywhere. The model is a
     reader; the receipt has to come from a solver.

  4  FALLBACK, NEVER FABRICATION. If the model is unavailable, malformed, or
     unsure, routing falls back to the deterministic keyword table. Worst case
     is the behaviour EngineAI already had - never a guess dressed as a route.

The model makes EngineAI easier to talk to. It does not make it cleverer about
physics, and it is not allowed to try.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

import scope

__all__ = ["route", "available", "extract_numbers", "MODEL", "OLLAMA"]

OLLAMA = os.environ.get("ENGINEAI_OLLAMA", "http://127.0.0.1:11434")
# Overridable so the model can be A/B tested without editing code. Which model
# is running is a deployment choice, and hard-coding it made "does a bigger
# model help" a question that required a commit to answer.
MODEL = os.environ.get("ENGINEAI_MODEL", "qwen2.5:7b-instruct-q4_K_M")
TIMEOUT = 90

SYSTEM = """You route engineering requests to a solver. You never calculate.

Reply with ONLY a JSON object, no prose, no markdown fence:
{"capability": "<one id from the list>", "parameters": {...}, "confidence": 0.0-1.0}

Rules:
- "capability" MUST be copied exactly from the list you are given. If nothing
  in the list fits, use "none".
- "parameters" may ONLY contain numbers and units that appear literally in the
  user's message. Never supply a typical, assumed or remembered value.
- Never include an answer, a result, a formula or a calculation.
- If the request is ambiguous, lower the confidence. Do not guess.
"""


def available(timeout: int = 3) -> bool:
    try:
        req = urllib.request.Request(f"{OLLAMA}/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _ask(prompt: str, model: str = MODEL, timeout: int = TIMEOUT) -> str:
    body = json.dumps({
        "model": model, "prompt": prompt, "system": SYSTEM, "stream": False,
        "options": {"temperature": 0.0, "num_predict": 300},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("response", "")


# --------------------------------------------------------------------------
# guards
# --------------------------------------------------------------------------

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")


def extract_numbers(text: str) -> set:
    """Every number the user actually typed, as floats."""
    out = set()
    for m in _NUM.finditer(text or ""):
        try:
            out.add(float(m.group().replace(",", "")))
        except ValueError:
            pass
    return out


def _user_supplied(value, user_nums: set, tol: float = 1e-9) -> bool:
    """Is this value traceable to a number in the user's own message?

    Unit-scaled restatements count: 6 m written as 6000 mm is still the user's
    6, and refusing it would make the guard useless on exactly the requests it
    is meant to serve.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return True                       # non-numeric parameters are not the risk
    if not user_nums:
        return False
    for u in user_nums:
        for k in (1.0, 1e-3, 1e3, 1e-2, 1e2, 1e-6, 1e6, 60.0, 1 / 60.0):
            if abs(v - u * k) <= tol + 1e-6 * abs(v):
                return True
    return False


# SUBJECT IS NOT INTENT. "Write a blog post explaining beam deflection" and
# "translate this datasheet into German" are both ABOUT engineering, and the
# deliverable in both is prose. A model that sees "beam deflection" and reaches
# for the beam solver has confused what the request is about with what it asks
# for - and the battery scored exactly that as a FABRICATION, because the user
# gets a calculation they never asked for, dressed as an answer.
#
# The test is what comes out: a document, or a number.
PROSE_DELIVERABLE = re.compile(
    r"\b(write|draft|compose|rewrite|reword|proofread|edit)\s+(me\s+)?"
    r"(a|an|the|this|some)?\s*"
    r"(blog|post|article|essay|email|e-mail|letter|memo|paragraph|summary|"
    r"story|script|caption|description|introduction|abstract|readme|"
    r"documentation|tutorial|guide|newsletter|proposal|cover letter)\b"
    r"|\btranslate\b|\bsummari[sz]e\b|\bparaphrase\b|\bproof-?read\b"
    r"|\bexplain .{0,30}\bto (beginners|a child|a layman|non-engineers)\b",
    re.I)


_ANSWERY = re.compile(
    r"^(answer|result|value|output|solution|computed|calculation|formula|"
    r"equation|deflection_mm|stress_mpa|utilisation|utilization|verdict)$", re.I)


def _clean_parameters(params: dict, text: str) -> tuple[dict, list]:
    kept, dropped = {}, []
    for k, v in (params or {}).items():
        if _ANSWERY.match(str(k)):
            dropped.append(f"{k} (looks like a computed result, not an input)")
            continue
        if isinstance(v, (int, float)) and not _user_supplied(v, extract_numbers(text)):
            dropped.append(f"{k}={v} (not present in the request)")
            continue
        kept[k] = v
    return kept, dropped


def _capabilities() -> list:
    try:
        idx = scope.capability_index()
        return [c["capability"] for c in idx if c.get("live")]
    except Exception:
        return []


def _capability_words(cap: str) -> set:
    """Every keyword scope.py associates with this capability, plus its name.

    Read from the LIVE table rather than restated here, so a capability whose
    vocabulary is widened later is corroborated by the new words automatically.
    """
    words = set()
    try:
        for pattern, (_module, label) in scope.CAPABILITY_MAP.items():
            if label == cap:
                for frag in pattern.split("|"):
                    frag = frag.strip().lower()
                    if len(frag) >= 3:
                        words.add(frag)
    except Exception:                                         # noqa: BLE001
        pass
    for w in re.split(r"[^a-z0-9]+", (cap or "").lower()):
        if len(w) >= 4 and w not in ("and", "the", "for", "with", "life"):
            words.add(w)
    return words


def _corroborated(text: str, cap: str) -> bool:
    """Does the request contain any of the capability's own vocabulary?"""
    t = (text or "").lower()
    words = _capability_words(cap)
    if not words:
        return False        # a capability with no vocabulary cannot be checked
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t)
               for w in words)


def _parse(reply: str) -> dict | None:
    s = (reply or "").strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group())
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------

def route(text: str, model: str = MODEL, min_confidence: float = 0.5,
          use_model: bool = True) -> dict:
    """Route a request. Deterministic table first, model only if that fails.

    The keyword table is tried FIRST on purpose. Where it fires it is exact,
    reproducible and free, and replacing a correct deterministic answer with a
    probabilistic one would be a downgrade dressed as an upgrade. The model is
    for the requests the table cannot read.
    """
    base = scope.classify(text)
    if base.get("verdict") in ("IN_SCOPE", "RESEARCH"):
        return {**base, "routed_by": "keyword table", "model_used": False}

    if PROSE_DELIVERABLE.search(text or ""):
        return {**base, "routed_by": "keyword table (asks for prose, not a number)",
                "model_used": False,
                "note": "the deliverable here is a document. Routing it to a "
                        "solver would return a calculation nobody asked for."}

    if not use_model or not available():
        return {**base, "routed_by": "keyword table (model unavailable)",
                "model_used": False}

    caps = _capabilities()
    if not caps:
        return {**base, "routed_by": "keyword table (no capability index)",
                "model_used": False}

    prompt = ("Available capabilities:\n"
              + "\n".join(f"- {c}" for c in caps)
              + f"\n\nUser message:\n{text}\n\nJSON:")
    try:
        reply = _ask(prompt, model=model)
    except Exception as e:
        return {**base, "routed_by": f"keyword table (model error: {type(e).__name__})",
                "model_used": False}

    parsed = _parse(reply)
    if not parsed:
        return {**base, "routed_by": "keyword table (model reply unparseable)",
                "model_used": True, "model_rejected": "not JSON"}

    cap = str(parsed.get("capability", "")).strip()
    conf = float(parsed.get("confidence", 0) or 0)

    # GUARD 1: closed vocabulary
    if cap not in caps:
        return {**base, "routed_by": "keyword table (model named an unknown capability)",
                "model_used": True,
                "model_rejected": f"'{cap}' is not a registered capability"}
    if conf < min_confidence:
        return {**base, "routed_by": f"keyword table (model unsure, {conf:.2f})",
                "model_used": True, "model_rejected": "low confidence"}

    # GUARD 4: THE TEXT MUST CORROBORATE THE CAPABILITY.
    #
    # Guards 1 and 3 check that the model named a capability that EXISTS and
    # did not invent numbers. Neither asks the question that matters here: is
    # this request actually ABOUT that capability?
    #
    #     "do you have knowledge about my crfce project?"
    #        -> fatigue life and fracture mechanics, confidence 0.70
    #
    # "crfce" is not a word. A model asked to pick from a closed list, given no
    # way to decline, picks the nearest-looking entry - and the user was then
    # told their unnamed project was a fracture-mechanics problem, labelled as
    # an engineering capability.
    #
    # So the keyword table gets a veto. If not one of that capability's own
    # words appears in the request, the model's choice is unsupported and is
    # dropped. This costs real recall: a genuine fatigue question phrased with
    # none of the fatigue vocabulary now falls through. That is the right
    # trade, because falling through says "I do not know", which is true, and
    # the alternative asserts a discipline the user never mentioned.
    if not _corroborated(text, cap):
        return {**base,
                "routed_by": "keyword table (model chose a capability the text "
                             "does not support)",
                "model_used": True,
                "model_rejected": f"nothing in the request corroborates "
                                  f"'{cap}'; the model guessed"}

    # GUARDS 2 and 3
    params, dropped = _clean_parameters(parsed.get("parameters"), text)

    return {"verdict": "IN_SCOPE", "capability": cap,
            "module": base.get("module"), "parameters": params,
            "confidence": conf, "routed_by": f"language model ({model})",
            "model_used": True, "dropped_parameters": dropped,
            "will_produce_output": True,
            "note": "the model chose the route and read the inputs; the solver "
                    "computes the answer and carries the receipt. No number in "
                    "the result comes from the model."}


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

    print("== GUARD 4: the text must corroborate the capability ==")
    # "do you have knowledge about my crfce project?" came back as fatigue life
    # and fracture mechanics at 0.70 confidence. crfce is not a word.
    chk(not _corroborated("do you have knowledge about my crfce project?",
                          "fatigue life and fracture mechanics"),
        "an unknown word does not corroborate fatigue")
    chk(not _corroborated("my zzzz project", "heat transfer"),
        "nor does it corroborate heat transfer")
    chk(_corroborated("how many cycles will this welded detail survive",
                      "fatigue life and fracture mechanics"),
        "a real fatigue question IS corroborated")
    chk(_corroborated("what is the pressure drop in a 50 mm pipe",
                      "fluid mechanics"), "so is a real fluids question")
    chk(_corroborated("check this column for buckling",
                      "compression members and buckling"),
        "so is a real buckling question")
    chk(not _corroborated("", "heat transfer"), "empty text corroborates nothing")
    # Substring matches must not count: "crack" inside "cracker" is not fatigue.
    chk(not _corroborated("pass the crackers", "fatigue life and fracture mechanics"),
        "a keyword inside a longer word does not count")
    chk(len(_capability_words("fatigue life and fracture mechanics")) > 8,
        "the vocabulary is read from the live table, not restated")

    print("== the guards, which must hold with or without a model ==")
    nums = extract_numbers("a 6 m span carrying 12.5 kN with 3,000 mm spacing")
    chk(nums == {6.0, 12.5, 3000.0}, f"numbers read from the request ({nums})")
    chk(_user_supplied(6.0, nums), "a value the user typed is accepted")
    chk(_user_supplied(6000.0, nums), "6 m restated as 6000 mm is still the user's 6")
    chk(not _user_supplied(9.81, nums), "a value the user never typed is refused")
    chk(not _user_supplied(42.0, set()), "with no numbers at all, nothing is accepted")

    kept, dropped = _clean_parameters(
        {"span_m": 6, "load_kn": 12.5, "answer": 3.2, "deflection_mm": 11.1,
         "E_gpa": 210, "material": "S275"},
        "a 6 m span carrying 12.5 kN")
    chk("span_m" in kept and "load_kn" in kept, "real inputs survive")
    chk("answer" not in kept and "deflection_mm" not in kept,
        f"result-shaped fields are stripped ({sorted(kept)})")
    chk("E_gpa" not in kept, "210 GPa was never in the request, so it is dropped")
    chk("material" in kept, "non-numeric parameters pass through")
    chk(len(dropped) == 3, f"and every drop is reported ({len(dropped)})")

    print("== reply parsing ==")
    chk(_parse('```json\n{"capability":"a","confidence":0.9}\n```') ==
        {"capability": "a", "confidence": 0.9}, "a fenced reply is parsed")
    chk(_parse("here you go: {\"capability\":\"a\"} hope that helps")
        == {"capability": "a"}, "JSON is recovered from surrounding prose")
    chk(_parse("I think you want a beam calculation.") is None,
        "prose with no JSON is rejected")
    chk(_parse("") is None, "an empty reply is rejected")

    print("== the deterministic table still wins where it fires ==")
    r = route("deflection of a simply supported beam", use_model=False)
    chk(not r["model_used"], "no model is called when the table already answers")

    print("== subject is not intent: prose requests never reach a solver ==")
    for q in ("write a blog post explaining beam deflection to beginners",
              "translate this datasheet into German",
              "summarise this stress report",
              "draft an email about the bridge inspection",
              "proofread my method statement"):
        rp = route(q)
        chk(not rp["model_used"],
            f"prose request not routed to a solver: {q[:44]}")
    # but a real calculation that happens to contain a verb still routes
    chk(PROSE_DELIVERABLE.search("calculate the deflection of a 6 m beam") is None,
        "a genuine calculation is not mistaken for a writing task")
    chk(PROSE_DELIVERABLE.search("what size joist do I need") is None,
        "nor is a sizing question")

    print("== a hostile model cannot get a number through ==")
    # simulate a model that invents a capability, a value and an answer
    global _ask, available
    real_ask, real_avail = _ask, available
    available = lambda timeout=3: True
    _ask = lambda p, model=MODEL, timeout=TIMEOUT: json.dumps(
        {"capability": "totally_made_up_solver", "parameters": {"span_m": 6},
         "confidence": 0.99})
    r2 = route("what size joist do I need")
    chk(r2["verdict"] != "IN_SCOPE" or r2.get("capability") != "totally_made_up_solver",
        "an invented capability is refused")
    chk("not a registered capability" in str(r2.get("model_rejected", "")),
        "and the refusal says why")

    caps = _capabilities()
    if caps:
        # This block used to feed a confident model reply through
        # "size a joist over a 6 m span" and check the invented parameters
        # were stripped. Guard 4 now refuses that route outright - "joist" is
        # timber vocabulary and corroborates nothing the model named - so the
        # request no longer reaches the parameter guards at all.
        #
        # That is the guard working, not a regression. The parameter stripping
        # itself is tested directly against _clean_parameters at the top of
        # this selftest, where it does not depend on routing succeeding. What
        # belongs here is the new behaviour: a confident model, on a request
        # that supports nothing, gets refused.
        _ask = lambda p, model=MODEL, timeout=TIMEOUT: json.dumps(
            {"capability": caps[0],
             "parameters": {"span_m": 6, "E_gpa": 210, "answer": 12.7},
             "confidence": 0.95})
        r3 = route("size a joist over a 6 m span")
        chk(r3["verdict"] != "IN_SCOPE",
            f"an uncorroborated capability is refused even at 0.95 confidence "
            f"({r3.get('capability')})")
        chk("does not support" in str(r3.get("routed_by", "")),
            f"...and the refusal names the reason ({r3.get('routed_by')})")

        r4 = route("do you have knowledge about my crfce project?")
        chk(r4["verdict"] != "IN_SCOPE",
            "the crfce case: an unknown word cannot become a capability")
        chk(r4.get("capability") is None,
            f"...and no capability is claimed ({r4.get('capability')})")

        _ask = lambda p, model=MODEL, timeout=TIMEOUT: json.dumps(
            {"capability": caps[0], "parameters": {}, "confidence": 0.2})
        r4 = route("something vague")
        chk(r4.get("verdict") != "IN_SCOPE" or "unsure" in r4.get("routed_by", ""),
            "low confidence falls back to the table")

    print("== a broken model degrades to the old behaviour, never to a guess ==")
    def boom(p, model=MODEL, timeout=TIMEOUT):
        raise urllib.error.URLError("model down")
    _ask = boom
    r5 = route("what size joist do I need")
    chk("model error" in r5["routed_by"], f"the failure is reported ({r5['routed_by']})")
    chk(r5.get("capability") in (None, "") or r5["verdict"] != "IN_SCOPE",
        "and nothing is invented to cover it")

    _ask, available = real_ask, real_avail

    print("== live model, if one is running ==")
    if available():
        rl = route("what size steel joist do I need to span 6 m carrying 12 kN")
        print(f"    routed_by: {rl['routed_by']}")
        print(f"    capability: {rl.get('capability')}")
        print(f"    parameters: {rl.get('parameters')}")
        chk(True, "a live model returned without error")
        if rl.get("parameters"):
            un = extract_numbers("what size steel joist do I need to span 6 m "
                                 "carrying 12 kN")
            bad = [k for k, v in rl["parameters"].items()
                   if isinstance(v, (int, float)) and not _user_supplied(v, un)]
            chk(not bad, f"no fabricated parameter survived the live call ({bad})")
    else:
        print("    (ollama not reachable - guards were tested with stubs above)")

    print(f"\nnl_router.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    q = " ".join(a for a in sys.argv[1:] if not a.startswith("-"))
    if not q:
        print(__doc__)
    else:
        r = route(q)
        for k, v in r.items():
            print(f"  {k:22s} {v}")
