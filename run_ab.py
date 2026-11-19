#!/usr/bin/env python3
"""
run_ab.py - the 7B vs 14B comparison, run through the harness that cannot fake it.

WHY THIS SCRIPT EXISTS RATHER THAN A ONE-LINER
----------------------------------------------
The first attempt at this comparison reported the 14B answering 31 questions in
0.026 s each and the 7B taking 21.2 s. Both numbers were nonsense: the models
were sharing VRAM, one of them was never actually resident, and the "answers"
were empty. model_harness.ensure(exclusive=True) now evicts every other model
and makes each one answer a probe out loud before a single question is timed,
and sanity() rejects a run whose per-question time is impossibly fast.

It also must not run while anything else is using the model. The question-bank
battery held the 7B for three and a half hours; starting this alongside it
would have measured contention, not capability.

  ./run_in_eng.sh python run_ab.py --n 24
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import model_harness            # noqa: E402
import qbank_csv                # noqa: E402


def ask_model(question: str, model: str, timeout: int = 180) -> str:
    """Put the question to the model DIRECTLY, over Ollama's generate endpoint."""
    import json as _json
    import urllib.request
    body = _json.dumps({
        "model": model, "prompt": question, "stream": False,
        "options": {"temperature": 0, "seed": 7}}).encode()
    req = urllib.request.Request(
        f"{model_harness.OLLAMA}/api/generate", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _json.loads(r.read()).get("response", "")


def build_battery(n: int, seed: int = 11):
    """The same questions for every model, drawn from the AUDITED rows only.

    THE BATTERY MUST ACTUALLY USE THE MODEL. The first version called
    chat.respond, and 16 of 24 questions came back in under 0.15 s - because
    most of chat.respond's paths never reach a model at all. They deflect, or
    look a law up, or route to a solver. Those are the right behaviours for
    EngineAI and completely wrong for comparing two models: it would have been
    timing the router.

    The harness caught it and refused to report the run, which is the third
    time it has stopped a false A/B from being published.

    Same questions, same order, temperature 0, fixed seed - a comparison
    across different samples or different sampling measures neither model.
    """
    import numeric_battery
    rows = numeric_battery.sample(numeric_battery.gradeable(), n, seed=seed)

    def battery():
        model = os.environ.get("ENGINEAI_MODEL", "")
        out = []
        for q in rows:
            t0 = time.time()
            try:
                ans = ask_model(q.question, model)
                basis = "MODEL"
            except Exception as exc:                          # noqa: BLE001
                ans, basis = "", f"error {type(exc).__name__}"
            g = numeric_battery.grade(q, ans, basis)
            out.append({"id": q.id, "seconds": round(time.time() - t0, 3),
                        "chars": len(ans), "basis": basis, **g})
        return out

    return battery, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24)
    # The 14B is installed but is 9.0 GB against 7 GB of system RAM: Ollama
    # drops the connection while loading it, every time. It is excluded from
    # the default set because a run that cannot start is not a result, and
    # scoring it would repeat the 2/18-in-0.81s mistake this harness exists to
    # prevent. Pass it explicitly with --models if the machine ever grows.
    ap.add_argument("--models", nargs="*",
                    default=["qwen2.5:7b-instruct-q4_K_M", "llama3.2:3b",
                             "qwen2.5-coder:7b"])
    a = ap.parse_args()

    busy = [m for m in model_harness.loaded()]
    if busy:
        print(f"  NOTE: {', '.join(busy)} already resident; they will be evicted "
              f"so each run has the GPU to itself.")

    battery, rows = build_battery(a.n)
    print(f"  {len(rows)} audited questions, identical for every model")
    print(f"  models: {', '.join(a.models)}\n")

    res = model_harness.ab(a.models, battery)

    print("\n" + "=" * 74)
    for m, s in res.items():
        if not s.get("valid"):
            print(f"  {m:30s} INVALID - {s.get('reason')[:90]}")
            continue
        rows = s.get("detail_rows") or []
        correct = sum(1 for r in rows if r.get("outcome") == "CORRECT")
        lenient = sum(1 for r in rows if r.get("lenient"))
        nonum = sum(1 for r in rows if r.get("outcome") == "NO_NUMBER")
        n = max(len(rows), 1)
        print(f"  {m:30s} {s['wall']:7.1f}s wall  cold {s['cold_seconds']:5.1f}s  "
              f"{s['wall']/n:5.1f}s/q")
        print(f"  {'':30s} strict {100*correct/n:5.1f}%   lenient "
              f"{100*lenient/n:5.1f}%   no number {nonum}")
    print()

    detail = {}
    for m in a.models:
        path = os.path.join(HERE, "data", f"ab_{m.replace(':', '_').replace('/', '_')}.json")
        if os.path.exists(path):
            detail[m] = json.load(open(path))
    out = os.path.join(HERE, "data", "ab_result.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"models": a.models, "n": len(rows), "summary": res},
                  fh, indent=2, default=str)
    print(f"  written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
