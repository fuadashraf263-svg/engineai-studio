#!/usr/bin/env python3
"""
cf_command.py -- `python agent.py counterfactual`

THE LAST ARCHITECTURAL GAP.

counterfactual.py has existed since v2 and is a careful piece of work.
wire_domains.py connected metrics.py to CounterfactualEngine.rung_achieved().
But rung_achieved() reads scene/counterfactuals.jsonl -- and NOTHING HAS
EVER WRITTEN TO IT. The rung will keep reading 2 forever, correctly,
because no counterfactual has ever been posed or settled.

This command closes that loop:

  1. load solved history from scene/iter_*.json
  2. fit the PowerLawSurrogate  (rung 1: association)
  3. read off the causal elasticities d(ln y)/d(ln x)
  4. pose a genuine RUNG-3 question -- one whose answer is not in the data:
     "the constraint was 250 MPa and we never varied it. Had the limit
      been L instead, what diameter would have been required?"
  5. settle it with ONE real CalculiX solve
  6. log predicted vs actual

Step 5 is the part that matters. A counterfactual that is merely computed
is decoration. One that is computed and then TESTED is knowledge, and the
error bar on it is a measurement. If the prediction misses, that is logged
too -- the scorecard is allowed to report a bad model, never a fake rung.

Install:  python wire_cf.py     (adds the subcommand to agent.py)
Run:      python agent.py counterfactual
          python agent.py counterfactual --limit 200
"""

from __future__ import annotations

import json
from pathlib import Path

SCENE = Path.home() / "engineai" / "scene"


def _history(tool_name: str | None = None) -> list:
    """Solved iterations, newest last. Same shape the world model expects."""
    out = []
    for p in sorted(SCENE.glob("iter_*.json")):
        try:
            m = json.loads(p.read_text())
        except Exception:
            continue
        r = m.get("result")
        if not isinstance(r, dict) or not r.get("converged"):
            continue
        if tool_name and m.get("tool") != tool_name:
            continue
        if not isinstance(m.get("parameters"), dict):
            continue
        out.append(m)
    return out


def cmd_counterfactual(cfg: dict, new_limit: float | None = None,
                       verify: bool = True) -> int:
    import engine_tools as et
    from counterfactual import CounterfactualEngine
    from core import audit
    from world_model import PowerLawSurrogate

    print("== COUNTERFACTUAL (Pearl rung 3) " + "=" * 36)

    mission_name = cfg["default_mission"]
    mission = cfg["missions"][mission_name]
    tool_name = mission["tool"]
    tool = et.TOOLS[tool_name]

    hist = _history(tool_name)
    if len(hist) < 3:
        print(f"  need >=3 converged solves of '{tool_name}'; have "
              f"{len(hist)}.")
        print("  run:  python agent.py mission --dry-run   (costs nothing)")
        return 1
    print(f"  history: {len(hist)} converged solves of {tool_name}")

    wm = PowerLawSurrogate()
    if not wm.fit(hist):
        print("  surrogate would not fit -- parameters may be constant "
              "across the history (a power law needs variation)")
        return 1

    out_key = list(mission["constraints"])[0]
    if out_key not in wm.models:
        out_key = next(iter(wm.models))
    cf = CounterfactualEngine(wm)

    el = cf.elasticities(out_key)
    print(f"\n  elasticities for {out_key}:")
    for k, v in el.items():
        print(f"      d(ln {out_key})/d(ln {k}) = {v:+.3f}")
    if not el:
        print("  no elasticities recovered")
        return 1

    # ---- the factual: the last solve we actually performed ---------------
    fact = hist[-1]
    f_par = fact["parameters"]
    f_val = float(fact["result"][out_key])
    actual_limit = float(list(mission["constraints"].values())[0])
    print(f"\n  factual : {f_par}  ->  {out_key} = {f_val:.2f}")

    # ---- the rung-3 question ---------------------------------------------
    primary = max(el, key=lambda k: abs(el[k]))
    limit = new_limit if new_limit is not None else actual_limit * 0.8
    q = cf.if_constraint_were(primary, f_par, out_key, f_val, limit)
    if not q:
        print("  could not form the constraint-shift query")
        return 1
    print(f"\n  QUESTION: {q['question']}")
    print(f"  ANSWER  : {q['answer']}   (exponent {q['exponent_used']:+.3f})")
    print("            -- the limit was NEVER varied in the history;")
    print("               this answer is not interpolation.")

    claim = cf.what_if(f_par, q["answer"], out_key)
    if not claim:
        print("  what_if returned nothing")
        return 1
    print(f"\n  predicted {out_key} at that setting: "
          f"{claim['absolute_prediction']}")
    print(f"  ratio vs factual                  : "
          f"{claim['ratio_vs_factual']}")

    if not verify:
        print("\n  --no-verify: claim NOT settled, rung stays at 2.")
        return 0

    # ---- settle it with ONE real solve -----------------------------------
    print("\n  settling with one real solve ...")
    fixed = dict(mission["fixed_parameters"])
    for k in list(claim["counterfactual_params"]):
        fixed.pop(k, None)
    res = CounterfactualEngine.verify(
        tool, fixed, claim, out_key,
        iteration=9000 + len(hist), mission=mission_name)

    if not res.get("verified"):
        print(f"  SOLVE FAILED: {res.get('error')}")
        print("  rung stays at 2 -- an unsettled claim is not knowledge.")
        return 1

    print(f"  predicted {res['predicted']}   actual {res['actual']:.3f}   "
          f"error {res['error_pct']}%   ({res['wall_time_s']}s)")
    audit({"event": "counterfactual", "question": q["question"],
           "predicted": res["predicted"], "actual": res["actual"],
           "error_pct": res["error_pct"]})

    rung = CounterfactualEngine.rung_achieved()
    print(f"\n  Pearl rung now: {rung}/3")
    if rung >= 3:
        print("  Rung 3 reached -- and EARNED: the counterfactual was "
              "tested against a real solve, not asserted.")
        if res["error_pct"] is not None and res["error_pct"] > 10:
            print(f"  Note: the model was {res['error_pct']}% off. The rung "
                  "is real; the accuracy is a measured weakness.")
    print("\n  run:  python agent.py agi     to see it on the scorecard")
    return 0
