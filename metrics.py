#!/usr/bin/env python3
"""
metrics.py -- the AGI scorecard: measure THIS system on OUR definition.

From our derivation: general intelligence is skill-acquisition efficiency
under novelty, measurable on seven axes. This module scores the running
system from its own artifacts (audit log, scene, prediction log) --
no self-flattery, numbers only, gaps stated plainly.

    G   breadth              verified domains / registry domains
    eta sample efficiency    solves-to-conclusion per mission per brain
    D   compositional depth  max chained solver stages in a verified tool
    T   autonomous horizon   longest uninterrupted mission (iters, wall s)
    Th  energy/cost          tokens, dollars, solver wall time
    C   calibration          worst domain error + world-model pred error
    I   intervention rank    Pearl rung actually exercised (of 3)
"""

from __future__ import annotations

import json
from pathlib import Path

SCENE = Path.home() / "engineai" / "scene"


def _events():
    p = SCENE / "decisions.jsonl"
    if not p.exists():
        return []
    out = []
    for ln in p.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def cmd_agi():
    import engine_tools as et
    from world_model import PowerLawSurrogate

    ev = _events()
    reg = et.registry()

    # G -- breadth
    verified = {}
    for e in ev:
        if e.get("event") == "verify":
            verified[e["domain"]] = bool(e.get("pass"))
    n_dom = len(reg)
    n_ver = sum(1 for v in verified.values() if v)
    G = n_ver / n_dom if n_dom else 0.0

    # eta -- sample efficiency: run-decisions between mission start & stop
    runs, missions = 0, []
    cur = 0
    for e in ev:
        if e.get("event") == "decision":
            d = e.get("decision", {})
            if d.get("action") == "run":
                cur += 1
            elif d.get("action") == "stop":
                missions.append(cur)
                cur = 0
    eta = (f"{missions[-1]} solves to conclusion (last mission); "
           f"all: {missions}") if missions else "no completed missions"

    # D -- compositional depth
    D = 2 if verified.get("pipeline") else 1

    # T -- autonomous horizon
    T_iters = max(missions) if missions else 0
    times = [e["t"] for e in ev if e.get("event") == "decision"]
    T_wall = round(max(times) - min(times), 1) if len(times) > 1 else 0.0

    # Theta -- cost
    spent = calls = 0
    for e in ev:
        if "spent_usd" in e:
            spent = e["spent_usd"]
            calls = e.get("api_calls", calls)
    solver_s = 0.0
    for p in SCENE.glob("iter_*.json"):
        try:
            solver_s += float(json.loads(p.read_text())
                              ["result"].get("wall_time_s", 0))
        except Exception:
            pass

    # C -- calibration
    dom_errs = [e["error_pct"] for e in ev if e.get("event") == "verify"]
    worst_dom = max(dom_errs) if dom_errs else None
    wm_err = PowerLawSurrogate.calibration()

    # I -- Pearl rung: 1 association (surrogate fitted from observation),
    # 2 intervention (system CHOOSES params, observes outcome),
    # 3 counterfactual (not implemented -- stated plainly)
    # Pearl rung, MEASURED not asserted. counterfactual.rung_achieved()
    # returns 3 only if a counterfactual claim was settled against a real
    # solve -- claiming rung 3 on an unverified prediction would be exactly
    # the self-flattery this scorecard exists to prevent.
    base = 2 if missions else (1 if wm_err is not None else 0)
    try:
        from counterfactual import CounterfactualEngine
        rung = max(base, CounterfactualEngine.rung_achieved())
    except Exception:
        rung = base

    lines = [
        "== AGI SCORECARD (our derived definition) " + "=" * 28,
        f"  G   breadth           {n_ver}/{n_dom} domains verified "
        f"({G:.0%})",
        f"  eta sample-eff        {eta}",
        f"  D   comp. depth       {D} chained solver stage(s) verified",
        f"  T   autonomy          {T_iters} iters / {T_wall}s longest "
        f"uninterrupted run",
        f"  Th  cost              ${spent} / {calls} API calls / "
        f"{solver_s:.0f}s solver time",
        f"  C   calibration       worst domain "
        f"{'%.2f%%' % worst_dom if worst_dom is not None else 'n/a'}; "
        f"world-model pred "
        f"{'%.1f%%' % wm_err if wm_err is not None else 'unfitted'}",
        f"  I   Pearl rung        {rung}/3 "
        + ("(counterfactual VERIFIED against a real solve)" if rung >= 3
           else "(counterfactual engine present but never verified)"),
        "",
        "  Honest verdict: broad, verified, tool-using narrow system.",
        "  The generality gap is unchanged from our derivation: sample",
        "  efficiency under NOVELTY -- these missions are in-family.",
        "  Next rungs: counterfactual queries on the world model, and",
        "  transfer tests to a domain the lessons file has never seen.",
    ]
    print("\n".join(lines))
    SCENE.mkdir(parents=True, exist_ok=True)
    (SCENE / "agi_scorecard.json").write_text(json.dumps({
        "G_domains_verified": n_ver, "G_domains_total": n_dom,
        "eta_solves_per_mission": missions, "D_depth": D,
        "T_iters": T_iters, "T_wall_s": T_wall,
        "cost_usd": spent, "api_calls": calls, "solver_s": solver_s,
        "C_worst_domain_pct": worst_dom, "C_world_model_pct": wm_err,
        "I_pearl_rung": rung}, indent=2))
    print(f"\nscorecard written: {SCENE / 'agi_scorecard.json'}")
    return 0
