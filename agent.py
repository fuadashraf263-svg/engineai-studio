#!/usr/bin/env python3
"""
agent.py (v2) -- the executive. The brain proposes; this layer disposes.

    python agent.py tools              # domain registry + availability
    python agent.py verify             # analytical checks, every domain
    python agent.py mission            # default mission (gate first)
    python agent.py mission NAME       # named mission from config
    python agent.py mission --dry-run  # keyless planner
    python agent.py status             # last iteration summary

Safety rails enforced HERE, independent of which brain is driving:
    * verification gate before any mission
    * hard dollar budget + API-call cap + iteration cap
    * every parameter clamped to config bounds
    * TRUST-BUT-VERIFY: a claimed optimum must match a solved history
      entry (or it is solved once to confirm); constraint-violating or
      unsolved claims are rejected back to the planner
    * full audit trail in scene/decisions.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import engine_tools as et
from brain import make_planner
from core import Budget, Memory, audit

CONFIG = Path(__file__).parent / "config.json"


# ---------------------------------------------------------------- commands --

def cmd_tools():
    reg = et.registry()
    print(f"{'DOMAIN':<20} {'BACKEND':<28} STATUS / TOOLS")
    print("-" * 78)
    for dom, info in reg.items():
        tools = ", ".join(info.get("tools", [])) or ""
        line = f"{dom:<20} {info['backend']:<28} {info['status']}"
        print(line)
        if tools:
            print(f"{'':<20} {'':<28} tools: {tools}")
    return 0


def cmd_verify(cfg):
    limit = cfg["verification"]["max_error_pct"]
    print("== CALIBRATION SUITE " + "=" * 50)
    all_ok = True
    for domain, fn, err_key in et.verification_suite():
        try:
            r = fn()
            err = r[err_key]
            ok = err <= limit
            all_ok &= ok
            print(f"  {domain:<12} {err_key:<24} {err:7.2f}%  "
                  f"{'PASS' if ok else 'FAIL'}")
            audit({"event": "verify", "domain": domain,
                   "error_pct": err, "pass": ok})
        except Exception as exc:
            all_ok = False
            print(f"  {domain:<12} ERROR: {exc}")
            audit({"event": "verify_error", "domain": domain,
                   "err": str(exc)[:300]})
    print("  " + ("ALL DOMAINS TRUTHFUL." if all_ok
                  else "AT LEAST ONE DOMAIN IS LYING -- fix before missions."))
    return 0 if all_ok else 1


def _gate(cfg) -> bool:
    print("== VERIFICATION GATE " + "=" * 45)
    r = et.cantilever(iteration=0)
    err = r["deflection_error_pct"]
    lim = cfg["verification"]["max_error_pct"]
    print(f"   structural error {err:.2f}% (limit {lim}%)  ->  "
          f"{'PASS' if err <= lim else 'FAIL'}")
    return err <= lim


def _clamp(mission, params: dict) -> dict:
    out = {}
    for name, spec in mission["search_parameters"].items():
        v = float(params.get(name, 0.5 * (spec["min"] + spec["max"])))
        out[name] = max(spec["min"], min(spec["max"], v))
    return out


def _summarize(result: dict) -> dict:
    keep = ("converged", "error", "max_von_mises_MPa", "mass_g",
            "outer_diameter_mm", "wall_mm", "diameter_mm",
            "deflection_error_pct", "f1_Hz", "profile_error_pct",
            "wall_time_s", "geometry")
    return {k: result[k] for k in keep if k in result}


def _find_solved(history, params, tol=1e-6):
    for h in history:
        if all(abs(h["parameters"].get(k, 1e9) - v) < tol
               for k, v in params.items()):
            return h
    return None


def _constraints_ok(mission, result) -> bool:
    for key, allow in mission["constraints"].items():
        if result.get(key, float("inf")) > allow:
            return False
    return True


def cmd_mission(cfg, name: str, dry_run: bool):
    mission = cfg["missions"][name]
    tool = et.TOOLS[mission["tool"]]
    budget = Budget(cfg["budget"])
    memory = Memory()
    planner, is_dry = make_planner(cfg, mission, budget, dry_run)
    lessons = memory.top(mission["objective"])

    if not _gate(cfg):
        print("   refusing to run the mission on a lying tool chain.")
        return 1

    label = "DRY-RUN (heuristic)" if is_dry else cfg["brain"]["model"]
    print(f"\n== MISSION: {name}  |  brain: {label} " + "=" * 20)
    print(f"   {mission['objective'][:120]}...\n")

    history = []
    max_iter = int(cfg["brain"]["max_api_calls_per_mission"])
    for it in range(1, max_iter + 1):
        if not is_dry and not budget.allow():
            print(f"\n== BUDGET CAP HIT: ${budget.spent_usd:.4f} "
                  f">= ${budget.cap_usd} -- stopping. ==")
            audit({"event": "budget_stop", **budget.state()})
            break

        try:
            decision = planner.plan(mission, history, lessons)
        except Exception as exc:
            print(f"\n== PLANNER FAILURE: {exc} ==")
            audit({"event": "planner_error", "err": str(exc)[:300]})
            return 1
        audit({"event": "decision", "iter": it, "decision": decision,
               **budget.state()})

        if decision.get("action") == "stop":
            opt = _clamp(mission, decision.get("optimum", {}))
            solved = _find_solved(history, opt)
            if solved is None:
                print(f"[verify] optimum {opt} was never solved -- "
                      f"confirming with one solve")
                fixed = {k: v for k, v in mission["fixed_parameters"].items()}
                try:
                    result = tool(iteration=it, mission=name, **fixed, **opt)
                except Exception as exc:
                    result = {"converged": False, "error": str(exc)}
                solved = {"parameters": opt, "result": _summarize(result)}
                history.append({"iteration": it, **solved})
            if solved["result"].get("converged") and \
                    _constraints_ok(mission, solved["result"]):
                print(f"\n== CONCLUSION ==\n   {decision.get('conclusion')}")
                print(f"   confirmed optimum: {solved['parameters']}")
                lesson = decision.get("lesson")
                if lesson:
                    memory.save(name, lesson)
                    print(f"   lesson saved: {lesson}")
                print(f"   budget: {budget.state()}")
                return 0
            print("[verify] claimed optimum FAILS constraints -- "
                  "rejecting, planner continues")
            history.append({"iteration": it, "rejected_optimum":
                            solved["parameters"],
                            "reason": "constraint violation or non-converged"})
            continue

        params = _clamp(mission, decision.get("parameters", {}))
        why = decision.get("reasoning", "")[:100]
        print(f"[iter {it}] {params}  ({why})")
        fixed = dict(mission["fixed_parameters"])
        try:
            result = tool(iteration=it, mission=name, **fixed, **params)
        except Exception as exc:
            result = {"converged": False, "error": str(exc)[:300]}
        s = _summarize(result)
        if s.get("converged"):
            vm = s.get("max_von_mises_MPa", 0)
            mg = s.get("mass_g", 0)
            print(f"          vm {vm:8.2f} MPa | mass {mg:7.3f} g | "
                  f"{s.get('wall_time_s', '?')}s")
        else:
            print(f"          SOLVE FAILED: {s.get('error')}")
        history.append({"iteration": it, "parameters": params, "result": s})

    print("\n== iteration cap reached ==")
    print(f"   budget: {budget.state()}")
    return 0


def cmd_status():
    latest = Path.home() / "engineai" / "scene" / "latest.json"
    if not latest.exists():
        print("no iterations yet")
        return 0
    print(latest.read_text())
    return 0


# --------------------------------------------------------------------- main --

def main():
    ap = argparse.ArgumentParser(description="EngineAI executive")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("tools")
    sub.add_parser("verify")
    sub.add_parser("status")
    mp = sub.add_parser("mission")
    mp.add_argument("name", nargs="?", default=None)
    mp.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.loads(CONFIG.read_text())

    if args.cmd == "tools":
        return cmd_tools()
    if args.cmd == "verify":
        return cmd_verify(cfg)
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "mission" or args.cmd is None:
        name = getattr(args, "name", None) or cfg["default_mission"]
        dry = getattr(args, "dry_run", False)
        return cmd_mission(cfg, name, dry)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
