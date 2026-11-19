#!/usr/bin/env python3
"""research_prove.py - proof that the engine does the research, not its author.

THE QUESTION THIS ANSWERS. Every result in the corpus campaign could in
principle have been written into the code by whoever built it. Recovering
I = b*h^3/12 proves nothing if "b*h^3/12" is sitting in a lookup table
somewhere. So the honest test is not "did it get the right answer" - it is
"can it get an answer that nobody could have planted".

HOW IT WORKS. At run time this module INVENTS physical laws that did not exist
when research.py was written: random exponents, random constants, random
numbers of variables, all drawn from a seed taken from the clock unless one is
supplied. It generates data from each invented law, hands the engine nothing
but the columns, and checks whether the recovered expression matches the law
that was invented seconds earlier.

There is no table that can contain these answers, because the answers did not
exist until the run started. If the engine recovers them, it is doing the
algebra.

THREE CONTROLS, because success alone is not evidence:
  1  invented laws      must be RECOVERED
  2  pure noise         must be REJECTED - an engine that finds a law in
                        random numbers has found nothing anywhere
  3  planted decoys     irrelevant columns must be EXCLUDED, not absorbed

Run it yourself:
    python research_prove.py                 # random seed from the clock
    python research_prove.py --seed 12345    # reproducible
    python research_prove.py --trials 50     # more evidence
"""
from __future__ import annotations

import math
import sys
import time
from fractions import Fraction

import numpy as np

from research import discover_law, falsify, _monomial_eval


def invent_law(rng) -> dict:
    """Invent a monomial law that has never existed before this instant."""
    n_vars = int(rng.integers(2, 5))
    names = [f"q{i}" for i in range(n_vars)]
    # exponents from simple rationals, as real physics tends to use
    choices = [-3, -2, -1.5, -1, -0.5, 0.5, 1, 1.5, 2, 3]
    exps = np.array([float(rng.choice(choices)) for _ in range(n_vars)])
    const = float(rng.choice([1.0, 0.5, 2.0, 1 / 3, 1 / 12, 1 / 48, math.pi,
                              2 * math.pi, 0.25, 4.0]))
    return {"names": names, "exps": exps, "const": const}


def render(law: dict) -> str:
    body = " * ".join(
        f"{n}^{Fraction(e).limit_denominator(6)}" for n, e in zip(law["names"], law["exps"]))
    return f"{law['const']:.6g} * {body}"


def sample(law: dict, rng, n: int = 600) -> dict:
    """Generate data from the invented law, on random positive ranges."""
    cols = {}
    for nm in law["names"]:
        lo = float(10 ** rng.uniform(-2, 2))
        cols[nm] = rng.uniform(lo, lo * float(rng.uniform(3, 50)), n)
    X = np.column_stack([cols[nm] for nm in law["names"]])
    cols["y"] = law["const"] * np.prod(X ** law["exps"], axis=1)
    return cols


def run(trials: int = 20, seed: int | None = None, verbose: bool = True) -> dict:
    if seed is None:
        seed = int(time.time() * 1000) % (2 ** 31)
        origin = "clock (nobody could have known it in advance)"
    else:
        origin = "supplied by you"
    rng = np.random.default_rng(seed)
    if verbose:
        print(f"seed {seed} - {origin}")
        print(f"inventing {trials} laws that did not exist until this moment\n")

    recovered = decoys_ok = 0
    for t in range(trials):
        law = invent_law(rng)
        data = sample(law, rng)
        # plant two irrelevant columns the law does NOT use
        data["decoy_a"] = rng.uniform(1, 100, len(data["y"]))
        data["decoy_b"] = rng.uniform(1, 100, len(data["y"]))

        cands = law["names"] + ["decoy_a", "decoy_b"]
        f = falsify(discover_law(data, "y", candidates=cands))

        truth = law["const"] * np.prod(
            np.column_stack([data[n] for n in law["names"]]) ** law["exps"], axis=1)
        fit = getattr(f, "_fit", None)
        if fit:
            C, exps, subset, stack, y, names, _ = fit
            pred = _monomial_eval(C, exps, stack[:, subset])
            err = float(np.max(np.abs(pred - truth[:len(pred)]) / np.abs(truth[:len(pred)])))
            no_decoy = not any(n.startswith("decoy") for n in names)
        else:
            err, no_decoy, names = float("inf"), False, []
        got = err < 1e-9 and f.verdict == "DISCOVERED"
        recovered += got
        decoys_ok += no_decoy
        if verbose:
            mark = "RECOVERED" if got else "missed   "
            print(f"  {mark}  invented: y = {render(law):44s}")
            print(f"             engine  : {f.claim[4:] if f.claim.startswith('y = ') else f.claim}")
            print(f"             err vs invented law {err:.2e}, decoys "
                  f"{'excluded' if no_decoy else 'ABSORBED'}")

    # control: pure noise must NOT yield a law
    noise_rejected = 0
    for _ in range(5):
        nd = {f"x{i}": rng.uniform(1, 50, 500) for i in range(4)}
        nd["y"] = np.exp(rng.normal(0, 1.5, 500))
        fn = falsify(discover_law(nd, "y"))
        noise_rejected += fn.verdict == "REJECTED"

    out = {"seed": seed, "trials": trials, "recovered": recovered,
           "decoys_excluded": decoys_ok, "noise_rejected": noise_rejected,
           "noise_trials": 5}
    if verbose:
        print(f"\n  invented laws recovered exactly : {recovered}/{trials}")
        print(f"  decoy columns excluded          : {decoys_ok}/{trials}")
        print(f"  pure noise correctly rejected   : {noise_rejected}/5")
        print("\n  These laws were generated at run time from a clock seed. No "
              "lookup\n  table can contain them, and no author could have "
              "pre-written the\n  answers. Recovery here is the engine doing the "
              "algebra itself.")
    return out


def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    r = run(trials=12, seed=None, verbose=False)
    chk(r["recovered"] >= 11, f"recovered {r['recovered']}/12 laws invented at run time")
    chk(r["decoys_excluded"] >= 11, f"excluded decoys in {r['decoys_excluded']}/12")
    chk(r["noise_rejected"] >= 4, f"rejected noise in {r['noise_rejected']}/5")

    # the same seed must reproduce exactly - otherwise nobody can check the run
    a = run(trials=5, seed=4242, verbose=False)
    b = run(trials=5, seed=4242, verbose=False)
    chk(a == b, "a supplied seed reproduces the run exactly")

    print(f"\nresearch_prove.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    seed = None
    trials = 20
    for i, a in enumerate(sys.argv):
        if a == "--seed" and i + 1 < len(sys.argv):
            seed = int(sys.argv[i + 1])
        if a == "--trials" and i + 1 < len(sys.argv):
            trials = int(sys.argv[i + 1])
    run(trials=trials, seed=seed)
