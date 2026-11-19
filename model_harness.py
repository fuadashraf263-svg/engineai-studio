#!/usr/bin/env python3
"""model_harness.py - prove the model is answering BEFORE anything is measured.

WHY THIS EXISTS. Twice now a battery has reported a devastating score that was
pure instrumentation error:

  the definitions battery reported 3%   - Ollama's /api/tags answered while
                                          /api/generate could not yet
  the 7B-vs-14B A/B reported 11%        - 31 questions in 0.81 s of wall time.
                                          Nothing ran. A 9 GB model could not
                                          load while the 7B still held VRAM,
                                          so every call failed instantly and
                                          the battery faithfully scored silence

Both were the same bug wearing different clothes: SILENCE SCORED AS A WRONG
ANSWER. A model that never loaded is not a model that got the question wrong,
and reporting it as one is a false claim about a real thing.

WHAT THIS MODULE GUARANTEES. Before a measurement starts:

  1  the target model EXISTS locally           (/api/tags)
  2  every other model is UNLOADED             (keep_alive: 0 via /api/ps)
  3  the target ANSWERS a question with a known answer, from a cold start,
     within a generous timeout                 (/api/generate)
  4  the wall time of that answer is recorded as the floor - any later answer
     arriving far faster than physically possible is flagged, not scored

And after it finishes, `sanity()` re-checks the aggregate: a run whose mean
answer time is a small fraction of the warm-up time did not happen, whatever
its score says, and the harness says INVALID instead of reporting a number.

The guard is deliberately asymmetric. Refusing to report a real bad score
costs one rerun. Reporting a fake bad score sends me rewriting code that was
never broken - which is exactly what nearly happened with the 14B.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

OLLAMA = os.environ.get("ENGINEAI_OLLAMA", "http://127.0.0.1:11434")

# A question with one unambiguous token in the answer, cheap for any model.
# Not an engineering question: this checks that the model SPEAKS, not that it
# knows anything, and mixing the two would make a warm-up failure look like a
# knowledge failure - the confusion this module exists to end.
PROBE = "Reply with one word only: what colour is a clear midday sky?"
PROBE_EXPECT = ("blue",)

# A 14B from cold on this machine takes real time. Too short a timeout here
# recreates the exact bug: a model that would have loaded, scored as silent.
LOAD_TIMEOUT = 600


class ModelNotReady(RuntimeError):
    """Raised instead of letting a battery measure a model that is not there."""


def _get(path: str, timeout: int = 15):
    req = urllib.request.Request(f"{OLLAMA}{path}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(path: str, payload: dict, timeout: int = 60):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f"{OLLAMA}{path}", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def installed() -> list:
    """Models present on disk. Empty list if the server is not up at all."""
    try:
        return [m["name"] for m in _get("/api/tags").get("models", [])]
    except Exception:
        return []


def loaded() -> list:
    """Models currently occupying memory. This is the VRAM contention check."""
    try:
        return [m["name"] for m in _get("/api/ps").get("models", [])]
    except Exception:
        return []


def unload(model: str) -> bool:
    """Evict one model. keep_alive 0 tells Ollama to drop it immediately."""
    try:
        _post("/api/generate", {"model": model, "prompt": "", "keep_alive": 0},
              timeout=60)
        return True
    except Exception:
        return False


def unload_others(keep: str) -> list:
    """Evict everything that is not the target, and wait for it to actually go.

    Not cosmetic. The 14B run failed because the 7B was resident; asking
    politely and not checking is how that went unnoticed for a whole battery.
    """
    evicted = []
    for m in loaded():
        if m != keep:
            unload(m)
            evicted.append(m)
    for _ in range(30):
        if not [m for m in loaded() if m != keep]:
            break
        time.sleep(1)
    return evicted


def probe(model: str, timeout: int = LOAD_TIMEOUT) -> dict:
    """Ask one trivial question and require a real answer to come back."""
    t0 = time.time()
    try:
        r = _post("/api/generate",
                  {"model": model, "prompt": PROBE, "stream": False,
                   "options": {"temperature": 0, "num_predict": 8}},
                  timeout=timeout)
        text = (r.get("response") or "").strip()
        return {"ok": bool(text), "seconds": round(time.time() - t0, 2),
                "text": text[:80],
                "expected_seen": any(w in text.lower() for w in PROBE_EXPECT)}
    except Exception as e:
        return {"ok": False, "seconds": round(time.time() - t0, 2),
                "error": f"{type(e).__name__}: {e}", "text": "",
                "expected_seen": False}


def ensure(model: str | None = None, exclusive: bool = True,
           quiet: bool = False) -> dict:
    """Make a model ready and PROVE it, or refuse to let measuring start.

    Returns the readiness receipt, including the cold-start wall time that
    later becomes the floor for `sanity()`. Raises ModelNotReady rather than
    returning a falsy value, because the whole point is that a caller cannot
    accidentally proceed.
    """
    model = model or os.environ.get("ENGINEAI_MODEL",
                                    "qwen2.5:7b-instruct-q4_K_M")
    have = installed()
    if not have:
        raise ModelNotReady(f"Ollama is not answering at {OLLAMA} - "
                            "nothing can be measured")
    if model not in have:
        raise ModelNotReady(f"{model} is not installed. Have: {', '.join(have)}")

    evicted = unload_others(model) if exclusive else []
    if not quiet and evicted:
        print(f"  unloaded {', '.join(evicted)} to free memory for {model}")

    p = probe(model)
    if not p["ok"]:
        raise ModelNotReady(
            f"{model} is installed but did not answer a one-word question in "
            f"{p['seconds']:.0f}s ({p.get('error', 'empty response')}). "
            "This is the state that scored 2/18 in 0.81s and was reported as "
            "a bad model. It is not a bad model; it is a model that never ran.")

    if not quiet:
        note = "" if p["expected_seen"] else "  (unexpected wording, but it spoke)"
        print(f"  {model} ready: answered in {p['seconds']:.1f}s{note}")
    return {"model": model, "cold_seconds": p["seconds"], "evicted": evicted,
            "probe": p, "ready": True}


def sanity(rows: list, receipt: dict, min_ratio: float = 0.05) -> dict:
    """After the fact: did this run physically happen?

    A model that answers a real question in under 5% of its own cold-start
    time, on average, did not answer it. That threshold is loose on purpose -
    the observed failure was 0.03 s per question against 20 s, three orders of
    magnitude out, so a factor of 20 is plenty and will not trip a genuinely
    fast run.
    """
    if not rows:
        return {"valid": False, "reason": "no results at all"}
    times = [r.get("seconds", 0) for r in rows]
    mean = sum(times) / len(times)
    floor = receipt.get("cold_seconds", 0) * min_ratio
    instant = sum(1 for t in times if t < 0.15)

    if mean < floor:
        return {"valid": False, "mean_seconds": mean, "floor": floor,
                "reason": f"mean answer time {mean:.2f}s is below {floor:.2f}s, "
                          f"which is {min_ratio*100:.0f}% of the model's own "
                          "cold start. The run did not happen; the score is "
                          "instrumentation, not knowledge."}
    if instant > len(rows) * 0.5:
        return {"valid": False, "mean_seconds": mean, "instant": instant,
                "reason": f"{instant}/{len(rows)} answers returned in under "
                          "0.15s - those are failures being scored, not answers"}
    return {"valid": True, "mean_seconds": mean, "floor": floor,
            "instant": instant}


def ab(models: list, battery, quiet: bool = False) -> dict:
    """Run the same battery under each model, one at a time, each proven live.

    `battery` is called as battery() and must return a list of rows carrying a
    "seconds" field. Each model is made exclusive before its run, so the two
    are never competing for the same VRAM - the thing that invalidated the
    first attempt at this comparison.
    """
    out = {}
    for m in models:
        try:
            receipt = ensure(m, exclusive=True, quiet=quiet)
        except ModelNotReady as e:
            out[m] = {"valid": False, "reason": str(e)}
            continue
        os.environ["ENGINEAI_MODEL"] = m
        t0 = time.time()
        rows = battery()
        s = sanity(rows, receipt)
        s.update({"rows": len(rows), "wall": round(time.time() - t0, 1),
                  "cold_seconds": receipt["cold_seconds"],
                  # Carry the graded rows back so the comparison can report
                  # ACCURACY, not only wall time. A faster model that is wrong
                  # more often is not the better model, and speed alone cannot
                  # say which is which.
                  "detail_rows": rows})
        out[m] = s
        if not quiet:
            verdict = "measured" if s["valid"] else "INVALID - " + s["reason"]
            print(f"  {m}: {verdict}")
    return out


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

    # --- sanity() is the guard that would have caught the 14B run. Test it
    # against the ACTUAL numbers from that run rather than invented ones.
    fake_14b = [{"seconds": 0.026} for _ in range(31)]     # 0.81s / 31
    receipt_14b = {"cold_seconds": 20.0}
    s = sanity(fake_14b, receipt_14b)
    chk(not s["valid"], "the real 14B numbers are rejected as invalid")
    chk("did not happen" in s["reason"] or "under 0.15s" in s["reason"],
        "and the reason says the run did not happen")

    real_7b = [{"seconds": 21.2} for _ in range(26)]        # 552s / 26
    s2 = sanity(real_7b, {"cold_seconds": 20.0})
    chk(s2["valid"], "the real 7B numbers are accepted as a genuine run")

    # A genuinely fast model must NOT be rejected: the guard must not become a
    # reason to disbelieve good results.
    s3 = sanity([{"seconds": 2.5}] * 20, {"cold_seconds": 20.0})
    chk(s3["valid"], "a fast but real run (2.5s vs 20s cold) still passes")

    s4 = sanity([], {"cold_seconds": 20.0})
    chk(not s4["valid"], "an empty run is invalid, not 0% correct")

    # Half the answers instant, half slow - mean passes, instant-count catches.
    mixed = [{"seconds": 0.01}] * 16 + [{"seconds": 40.0}] * 14
    s5 = sanity(mixed, {"cold_seconds": 20.0})
    chk(not s5["valid"], "a run where most answers were instant is invalid "
                         "even when the mean looks healthy")

    # --- ensure() must REFUSE rather than return something falsy
    try:
        ensure("definitely-not-a-real-model:999b", quiet=True)
        chk(False, "ensure refuses an uninstalled model")
    except ModelNotReady as e:
        chk("not installed" in str(e) or "not answering" in str(e),
            "ensure refuses an uninstalled model with a useful message")

    # --- live checks, only where a server is actually up
    have = installed()
    if have:
        chk(isinstance(loaded(), list), "can read which models are resident")
        m = os.environ.get("ENGINEAI_MODEL", have[0])
        if m in have:
            r = ensure(m, quiet=True)
            chk(r["ready"] and r["cold_seconds"] > 0,
                f"{m} proven live in {r['cold_seconds']}s")
            chk(m in loaded(), "and it is resident afterwards")
            others = [x for x in loaded() if x != m]
            chk(not others, f"nothing else is resident ({others})")
    else:
        print("  (Ollama not running - live checks skipped, guards still tested)")

    print(f"\nmodel_harness.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ensure", default=None, help="prove a model is live")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return _selftest()
    if a.ensure:
        try:
            r = ensure(a.ensure)
        except ModelNotReady as e:
            print(f"  NOT READY: {e}")
            return 1
        print(json.dumps(r, indent=2))
        return 0
    print(f"  ollama   {OLLAMA}")
    print(f"  installed {', '.join(installed()) or '(none / not running)'}")
    print(f"  resident  {', '.join(loaded()) or '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
