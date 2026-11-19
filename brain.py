#!/usr/bin/env python3
"""
brain.py -- the planning intelligence, hardened for the real world.

Fable 5 planner with:
    * extended thinking requested at the configured budget ("effort max");
      if the API rejects the thinking parameter it AUTOMATICALLY retries
      without it -- experiment with a fallback, never a crash
    * network retry w/ exponential backoff (built for a mobile tether)
    * balanced-JSON extraction + ONE self-repair round on malformed output
    * multi-parameter protocol: any number of search variables with bounds
    * every decision and token count audited to scene/decisions.jsonl

Dry-run planner (no key): deterministic two-phase heuristic --
cube-root scaling on the primary variable at minimum wall, then a
0.1 mm verification step. Exists so the nervous system always runs,
and as a floor Fable 5 must beat.
"""

from __future__ import annotations

import json
import os
import time

from core import Budget, Memory, audit, extract_json

SYSTEM_PROMPT = """You are the planning brain of an autonomous engineering \
system. Every number you see came from a real CalculiX solve on a real mesh; \
you never invent results.

Protocol -- reply with ONE JSON object, nothing else:
  run a solve:  {"action":"run","parameters":{<name>:<value>,...},\
"reasoning":"<one sentence>"}
  finish:       {"action":"stop","optimum":{<name>:<value>,...},\
"conclusion":"<answer with numbers>","lesson":"<one transferable \
engineering lesson>"}

Rules:
- Set EVERY search parameter on every run action; respect the bounds.
- Constraints bind on FEA values (they include real stress concentrations), \
not on handbook formulas -- use formulas to aim, FEA to confirm.
- Converge in as few solves as possible; use scaling laws.
- Your stated optimum MUST be a parameter set you actually solved; the \
executive will verify it against the history and reject unsolved claims."""


class FablePlanner:
    def __init__(self, cfg: dict, budget: Budget):
        import anthropic
        self.client = anthropic.Anthropic()
        self.cfg = cfg["brain"]
        self.budget = budget
        self.thinking_ok = True          # optimistic; falls back on 400

    # ---------------------------------------------------------------- call --
    def _request(self, messages):
        kwargs = dict(model=self.cfg["model"],
                      system=SYSTEM_PROMPT,
                      messages=messages)
        if self.thinking_ok and self.cfg.get("thinking_budget_tokens", 0) > 0:
            tb = int(self.cfg["thinking_budget_tokens"])
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": tb}
            kwargs["max_tokens"] = tb + int(self.cfg["answer_max_tokens"])
        else:
            kwargs["max_tokens"] = int(self.cfg["answer_max_tokens"])
        return self.client.messages.create(**kwargs)

    def _call(self, messages):
        import anthropic
        last_exc = None
        for attempt in range(int(self.cfg["retry_attempts"])):
            try:
                msg = self._request(messages)
                self.budget.add(msg.usage.input_tokens,
                                msg.usage.output_tokens)
                text = "".join(b.text for b in msg.content
                               if getattr(b, "type", "") == "text")
                return text
            except anthropic.BadRequestError as e:
                if self.thinking_ok and "thinking" in str(e).lower():
                    self.thinking_ok = False     # experiment failed: fall back
                    audit({"event": "thinking_fallback", "err": str(e)[:200]})
                    continue
                raise
            except (anthropic.APIConnectionError,
                    anthropic.RateLimitError,
                    anthropic.InternalServerError) as e:
                last_exc = e
                wait = float(self.cfg["retry_backoff_s"]) * (2 ** attempt)
                audit({"event": "net_retry", "attempt": attempt,
                       "wait_s": wait, "err": str(e)[:200]})
                time.sleep(wait)
        raise RuntimeError(f"API unreachable after retries: {last_exc}")

    # ---------------------------------------------------------------- plan --
    def plan(self, mission: dict, history: list, lessons: str) -> dict:
        payload = {
            "mission": mission["objective"],
            "tool": mission["tool"],
            "search_parameters": mission["search_parameters"],
            "fixed_parameters": mission["fixed_parameters"],
            "constraints": mission["constraints"],
            "budget_state": self.budget.state(),
            "memory_top_lessons": lessons,
            "history": history,
        }
        user = json.dumps(payload, indent=2)
        text = self._call([{"role": "user", "content": user}])
        try:
            return extract_json(text)
        except ValueError:
            # one structured repair round, then give up loudly
            repair = [{"role": "user", "content": user},
                      {"role": "assistant", "content": text},
                      {"role": "user", "content":
                       "That was not a single valid JSON object. Resend "
                       "ONLY the JSON object, no other text."}]
            return extract_json(self._call(repair))


class DryRunPlanner:
    """Keyless deterministic heuristic for any single-constraint,
    stress-limited sizing mission. Phase 1: pin secondary params at their
    minimum, cube-root-scale the first parameter to the constraint.
    Phase 2: one 0.1 step verification, then stop."""

    def __init__(self, mission: dict):
        self.m = mission
        names = list(mission["search_parameters"])
        self.primary = names[0]
        self.others = {n: mission["search_parameters"][n]["min"]
                       for n in names[1:]}
        self.phase = 0

    def plan(self, mission, history, lessons):
        sp = self.m["search_parameters"][self.primary]
        allow = list(self.m["constraints"].values())[0]
        key = list(self.m["constraints"])[0]
        if not history:
            mid = 0.5 * (sp["min"] + sp["max"])
            return {"action": "run",
                    "parameters": {self.primary: round(mid, 2),
                                   **self.others},
                    "reasoning": "seed at midpoint, secondary params at min"}
        last = history[-1]
        val = last["parameters"][self.primary]
        r = last["result"]
        if not r.get("converged"):
            bump = min(sp["max"], val * 1.3)
            return {"action": "run",
                    "parameters": {self.primary: round(bump, 2),
                                   **self.others},
                    "reasoning": "previous solve failed; stepping up"}
        vm = r.get(key.replace("max_", "max_"), r.get("max_von_mises_MPa"))
        if self.phase == 0:
            target = val * (vm / allow) ** (1.0 / 3.0)
            target = max(sp["min"], min(sp["max"], target))
            self.phase = 1
            return {"action": "run",
                    "parameters": {self.primary: round(target + 0.1, 2),
                                   **self.others},
                    "reasoning": "cube-root jump to constraint + 0.1 margin"}
        if vm <= allow:
            return {"action": "stop",
                    "optimum": {self.primary: val, **self.others},
                    "conclusion": f"{self.primary}={val} with "
                                  f"{key}={vm:.1f} <= {allow}",
                    "lesson": "cube-root scaling from one solve reaches the "
                              "stress boundary in ~2 iterations"}
        nxt = min(sp["max"], val + 0.2)
        return {"action": "run",
                "parameters": {self.primary: round(nxt, 2), **self.others},
                "reasoning": "constraint violated; stepping up 0.2"}


def make_planner(cfg: dict, mission: dict, budget: Budget, dry_run: bool):
    if dry_run or not os.environ.get("ANTHROPIC_API_KEY"):
        return DryRunPlanner(mission), True
    return FablePlanner(cfg, budget), False
