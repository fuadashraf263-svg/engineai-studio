#!/usr/bin/env python3
"""
chat_command.py -- `agent.py chat "..."`

YOUR QUESTION: does EngineAI have a chatbot? Until now, NO.

What you had:
    agent.py       a command-line executive with fixed subcommands
    server.py      a web control panel with buttons
    studio.html    those buttons, styled
    brain.py       Fable 5 -- but reachable ONLY through a mission that
                   was already written by hand into config.json

So you could press "Run mission" and watch verified physics happen, but
you could not TYPE a problem and have the system work out what to solve.
Every mission had to exist in config.json first. That is a control panel,
not a chatbot, and it is the single biggest gap between what you have and
what a customer expects when you say "engineering AI".

WHAT THIS ADDS
--------------
Natural language in -> a mission SPECIFICATION out -> your approval ->
the existing verified machinery runs it.

    python agent.py chat "how thin can a 7075 strut go at 300 N?"

Fable 5 sees the real tool registry, the real materials library and the
real constraint format, and returns a mission spec. Nothing runs yet.

WHY IT ASKS BEFORE IT RUNS
--------------------------
This is the part that matters. The brain is good at turning words into a
spec and bad at knowing whether the spec is what you meant. A chatbot
that silently runs whatever it inferred would spend your API budget and
your solver time on the wrong problem, and -- worse -- return a confident
number for a question you did not ask.

So: it prints the spec, names its assumptions, and waits. `--run` is an
explicit second step. Every guardrail you already built (verification
gate, budget cap, bounds check, MAX_NODES) stays in the path.

    python agent.py chat "..." --run     # accept and execute
    python agent.py chat "..." --save X  # store it in config.json as X

COST: one API call to draft a spec (~$0.30 at Fable 5). Zero if the
request maps onto a mission that already exists -- it checks first.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HOME = Path.home() / "engineai"
CONFIG = HOME / "config.json"

CHAT_SYSTEM = """You translate an engineer's request into ONE mission \
specification for a verified multi-physics agent. You do not solve \
anything; the executive runs real solvers and checks every result against \
an analytical solution.

Reply with ONE JSON object, nothing else:

{"tool":"<exact tool name from the registry>",
 "objective":"<one paragraph: what to optimise, subject to what>",
 "fixed_parameters":{...},
 "constraints":{"<result key>": <number>},
 "search_parameters":{"<name>":{"min":<n>,"max":<n>}, ...},
 "material":"<name from the materials library, or null>",
 "assumptions":["<each thing you inferred that the user did not say>"],
 "confidence":"high|medium|low",
 "unclear":["<anything you had to guess and would rather be told>"]}

Rules:
- Use ONLY tools that appear in the registry you are given.
- Constraint keys must be result keys the tool actually returns.
- If the request needs a domain that is not active, say so in "unclear" \
and pick the closest available tool.
- Put EVERY inference in "assumptions". An unstated assumption that \
reaches the solver is a wrong answer with a confident number on it.
- Prefer fewer search parameters. Each one costs solves."""


def _registry_summary() -> str:
    import engine_tools as et
    lines = []
    for dom, info in et.registry().items():
        if "active" not in str(info.get("status", "")).lower():
            continue
        tools = info.get("tools") or []
        if tools:
            lines.append(f"  {dom}: {info['backend']} -- tools: "
                         f"{', '.join(tools)}")
        else:
            lines.append(f"  {dom}: {info['backend']} (no named tools)")
    return "\n".join(lines)


def _result_keys() -> str:
    """What the tools actually return -- constraints must name these."""
    keys = set()
    scene = HOME / "scene"
    for p in sorted(scene.glob("iter_*.json"))[-40:]:
        try:
            r = json.loads(p.read_text()).get("result", {})
        except Exception:
            continue
        keys |= {k for k, v in r.items()
                 if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return ", ".join(sorted(keys)) or "(no solve history yet)"


def _materials() -> str:
    try:
        import materials
        return ", ".join(sorted(materials.MATERIALS))
    except Exception:
        return "(materials.py not installed)"


def cmd_chat(cfg: dict, text: str, run: bool = False,
             save: str | None = None) -> int:
    from brain import FablePlanner
    from core import Budget, audit, extract_json

    print("== CHAT " + "=" * 56)
    print(f"  request: {text}\n")

    # free path first: does a mission for this already exist?
    for name, m in (cfg.get("missions") or {}).items():
        words = {w for w in text.lower().split() if len(w) > 4}
        if words and words <= set(m.get("objective", "").lower().split()):
            print(f"  an existing mission already covers this: '{name}'")
            print(f"  run it for free:  python agent.py mission {name}")
            return 0

    budget = Budget(cfg["budget"])
    planner = FablePlanner(cfg, budget)

    payload = {
        "request": text,
        "available_tools": _registry_summary(),
        "result_keys_seen_in_history": _result_keys(),
        "materials_library": _materials(),
        "example_mission": next(iter((cfg.get("missions") or {}).values()),
                                {}),
    }
    print("  drafting a specification with Fable 5 ...")
    try:
        raw = planner._call([{"role": "user",
                              "content": json.dumps(payload, indent=2)}],
                            system=CHAT_SYSTEM)
        spec = extract_json(raw)
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return 1

    print(f"  cost: {budget.state()}\n")
    print("  --- PROPOSED MISSION " + "-" * 43)
    for k in ("tool", "objective", "material", "confidence"):
        if spec.get(k):
            print(f"  {k:20s} {spec[k]}")
    for k in ("fixed_parameters", "constraints", "search_parameters"):
        if spec.get(k):
            print(f"  {k:20s} {json.dumps(spec[k])}")

    if spec.get("assumptions"):
        print("\n  ASSUMPTIONS the brain made (you did not say these):")
        for a in spec["assumptions"]:
            print(f"      - {a}")
    if spec.get("unclear"):
        print("\n  UNCLEAR -- tell it these and re-run for a better spec:")
        for u in spec["unclear"]:
            print(f"      ? {u}")

    # apply the named material over whatever the brain guessed
    if spec.get("material"):
        try:
            import materials
            props = materials.as_mission_parameters(spec["material"])
            spec.setdefault("fixed_parameters", {}).update(props)
            print(f"\n  material {spec['material']} applied: {props}")
        except Exception as exc:
            print(f"\n  material lookup failed: {exc}")

    audit({"event": "chat", "request": text, "spec": spec,
           "spent_usd": budget.spent_usd})

    import engine_tools as et
    if spec.get("tool") not in et.TOOLS:
        print(f"\n  REJECTED: '{spec.get('tool')}' is not a real tool.")
        print(f"  available: {sorted(et.TOOLS)}")
        return 1

    if save:
        cfg.setdefault("missions", {})[save] = {
            k: spec[k] for k in ("tool", "objective", "fixed_parameters",
                                 "constraints", "search_parameters")
            if k in spec}
        CONFIG.write_text(json.dumps(cfg, indent=2))
        print(f"\n  saved to config.json as '{save}'")
        print(f"  run it any time:  python agent.py mission {save}")

    if not run:
        print("\n  NOT EXECUTED. Read the assumptions above first.")
        print("  accept:  python agent.py chat \"...\" --run")
        print("  keep it: python agent.py chat \"...\" --save <name>")
        return 0

    name = save or "_chat"
    cfg.setdefault("missions", {})[name] = {
        k: spec[k] for k in ("tool", "objective", "fixed_parameters",
                             "constraints", "search_parameters")
        if k in spec}
    print("\n  executing through the normal verified path ...\n")
    from agent import cmd_mission
    return cmd_mission(cfg, name, dry_run=False)
