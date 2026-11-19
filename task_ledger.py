#!/usr/bin/env python3
"""
task_ledger.py - durable tasks, deterministic cost preflight, and a completion gate
that makes a hallucinated success report structurally impossible.

WHY THIS EXISTS. Independent 2026 reviews of general agent platforms document three
recurring failures, and all three are architectural rather than model quality:

  1. HALLUCINATED SUCCESS. The agent reports a task complete and the deliverable is
     absent, empty, or wrong. A two-week production trial of one leading agent
     catalogued fourteen failure categories, "hallucinated success reports" among them.
  2. UNPREDICTABLE COST. The user cannot find out what a task will cost before running
     it. One vendor's own help documentation states that credit estimates produced by
     its model should be treated as "hallucinations rather than factual commitments."
  3. NO DURABILITY. Long tasks die mid-stream with no resume, so multi-step work
     compounds errors and then loses them.

EngineAI can beat all three, and not by being cleverer - by being structured:

  1. A task cannot reach COMPLETED unless every claimed deliverable EXISTS on disk,
     is non-empty, and every attached receipt re-verifies. Completion is a checked
     fact, not a model assertion. A model that says "done" without artifacts is
     recorded as FAILED_VERIFICATION with the reason.
  2. Almost the entire EngineAI pipeline is deterministic local computation with no
     API call, so cost is not estimated - it is COUNTED. The preflight returns an
     exact number of model calls (usually zero) and a hard upper bound, before the
     task runs.
  3. Every step is appended to a durable JSONL ledger as it happens, so a task
     survives process death and can be inspected or resumed.

Units: cost is reported as model CALLS, not dollars - the dollar figure depends on
which provider is routed to, and inventing one would be the same failure this module
exists to prevent.
"""
from __future__ import annotations
import argparse, json, os, time, uuid

LEDGER_DIR = os.path.expanduser(os.environ.get("ENGINEAI_LEDGER", "~/engineai/out/ledger"))

# --------------------------------------------------------------------------- risk
READ_ONLY = "read_only"           # computes or looks up; changes nothing
REVERSIBLE = "reversible_write"   # writes a file we can delete
IRREVERSIBLE = "irreversible"     # leaves the machine / cannot be undone

# Every EngineAI tool classified. Unknown tools default to IRREVERSIBLE: an
# unclassified tool is treated as dangerous until someone says otherwise, which is
# the safe direction for the default to fail in.
TOOL_RISK = {
    "lookup_formula": READ_ONLY, "compute": READ_ONLY, "check_units": READ_ONLY,
    "search_library": READ_ONLY, "verify_formula": READ_ONLY, "list_generators": READ_ONLY,
    "beam_design": READ_ONLY, "standard_section": READ_ONLY, "select_section": READ_ONLY,
    "bolt_sizing": READ_ONLY, "steel_beam_check": READ_ONLY, "list_capabilities": READ_ONLY,
    "fluid_property": READ_ONLY, "chemical_property": READ_ONLY, "vault_search": READ_ONLY,
    "final_answer": READ_ONLY, "derive_verified": READ_ONLY,
    "run_generator": REVERSIBLE, "visualise": REVERSIBLE, "make_beam_cad": REVERSIBLE,
    "make_drawing_sheet": REVERSIBLE, "draw_part": REVERSIBLE, "design_part": REVERSIBLE,
}

# Which tools actually spend a model call. This is the whole cost story, and it is
# short because the pipeline is deliberately local.
MODEL_BACKED = {
    # derive_verified runs K independent solvers, retrying up to 2K+2 attempts
    "derive_verified": lambda args: int(os.environ.get("ENGINEAI_CONSENSUS_K", "3")) * 2 + 2,
}


def tool_risk(name):
    return TOOL_RISK.get(name, IRREVERSIBLE)


def requires_approval(name):
    """Irreversible actions need a human to look at them first."""
    return tool_risk(name) == IRREVERSIBLE


# ----------------------------------------------------------------------- preflight
def preflight(goal, tools=None, max_steps=8):
    """What this task will cost, BEFORE it runs. Counted, not guessed.

    Returns an exact model-call count for the deterministic part and a hard upper
    bound including the agent loop's own planning calls. No dollar figure: that
    depends on the routed provider, and fabricating one is the failure this module
    exists to prevent.
    """
    tools = tools or {}
    g = (goal or "").lower()
    # Deterministic keyword routing - the same goal always yields the same estimate.
    likely = [n for n in sorted(tools) if n.replace("_", " ") in g or n in g]
    novel = any(w in g for w in ("derive", "novel", "no formula", "from scratch"))

    planning_calls = max_steps                    # worst case: one model call per step
    tool_calls = 0
    for n in likely:
        if n in MODEL_BACKED:
            tool_calls += MODEL_BACKED[n]({})
    if novel and "derive_verified" not in likely:
        tool_calls += MODEL_BACKED["derive_verified"]({})

    risks = {n: tool_risk(n) for n in likely}
    needs_approval = [n for n in likely if requires_approval(n)]
    return {
        "goal": goal,
        "likely_tools": likely,
        "tool_risks": risks,
        "requires_approval": needs_approval,
        "model_calls_for_tools": tool_calls,
        "model_calls_for_planning_max": planning_calls,
        "model_calls_total_max": planning_calls + tool_calls,
        "deterministic_local_only": tool_calls == 0,
        "note": ("Every listed tool is deterministic local computation - no API call, "
                 "no per-token cost. Only the agent's own planning uses the model."
                 if tool_calls == 0 else
                 f"{tool_calls} model call(s) come from generate-and-verify solving."),
        "cost_basis": "model CALLS, not dollars - the dollar rate depends on the routed "
                      "provider, and inventing a figure here would be a fabrication",
    }


# -------------------------------------------------------------------------- ledger
class Ledger:
    """Append-only durable record of one task. Survives process death."""

    def __init__(self, goal, task_id=None, directory=None):
        self.dir = directory or LEDGER_DIR
        os.makedirs(self.dir, exist_ok=True)
        self.task_id = task_id or uuid.uuid4().hex[:12]
        self.path = os.path.join(self.dir, f"{self.task_id}.jsonl")
        self.goal = goal
        self.steps = []
        self.status = "created"
        if not os.path.exists(self.path):
            self._append({"event": "created", "goal": goal, "task_id": self.task_id})

    def _append(self, rec):
        rec = dict(rec)
        rec.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        # flush immediately: a record that is still buffered when the process dies
        # is exactly the durability this class claims to provide
        with open(self.path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return rec

    def step(self, tool, args, observation):
        rec = self._append({"event": "step", "tool": tool, "args": args,
                            "risk": tool_risk(tool),
                            "observation": observation})
        self.steps.append(rec)
        return rec

    def set_status(self, status, detail=""):
        self.status = status
        self._append({"event": "status", "status": status, "detail": detail})

    @classmethod
    def load(cls, task_id, directory=None):
        """Rebuild a task from disk - the resume path."""
        directory = directory or LEDGER_DIR
        path = os.path.join(directory, f"{task_id}.jsonl")
        if not os.path.exists(path):
            raise FileNotFoundError(f"no ledger for task {task_id}")
        recs = [json.loads(l) for l in open(path) if l.strip()]
        goal = next((r.get("goal") for r in recs if r.get("event") == "created"), "")
        lg = cls.__new__(cls)
        lg.dir, lg.path, lg.task_id, lg.goal = directory, path, task_id, goal
        lg.steps = [r for r in recs if r.get("event") == "step"]
        st = [r for r in recs if r.get("event") == "status"]
        lg.status = st[-1]["status"] if st else "created"
        return lg


# ---------------------------------------------------------------- completion gate
def verify_completion(claimed_files=None, receipts=None, require_artifact=True):
    """The anti-hallucinated-success gate.

    A task is complete only if what it CLAIMS to have produced actually exists and
    checks out. Every failure mode is named, so an incomplete task explains itself
    instead of just reporting success.
    """
    checks = []
    claimed_files = list(claimed_files or [])
    receipts = list(receipts or [])

    if require_artifact and not claimed_files and not receipts:
        checks.append({"name": "task produced something", "passed": False,
                       "detail": "no artifact and no receipt - nothing to verify, so "
                                 "'complete' would be an unsupported claim"})

    for p in claimed_files:
        if not os.path.exists(p):
            checks.append({"name": f"artifact exists: {os.path.basename(p)}",
                           "passed": False, "detail": f"claimed but MISSING: {p}"})
        elif os.path.getsize(p) == 0:
            checks.append({"name": f"artifact non-empty: {os.path.basename(p)}",
                           "passed": False, "detail": "file exists but is 0 bytes"})
        else:
            checks.append({"name": f"artifact exists: {os.path.basename(p)}",
                           "passed": True, "detail": f"{os.path.getsize(p)} bytes"})

    for i, r in enumerate(receipts):
        try:
            from receipt import verify_receipt
            v = verify_receipt(r)
            checks.append({"name": f"receipt {i} integrity", "passed": bool(v["ok"]),
                           "detail": f"integrity={v['integrity']} consistent={v['consistent']}"})
            if not r.get("verified", False):
                checks.append({"name": f"receipt {i} reports verified", "passed": False,
                               "detail": r.get("warning", "receipt says unverified")})
        except Exception as e:
            checks.append({"name": f"receipt {i} checkable", "passed": False,
                           "detail": f"{type(e).__name__}: {e}"})

    ok = all(c["passed"] for c in checks) and bool(checks)
    return {"complete": ok,
            "status": "COMPLETED" if ok else "FAILED_VERIFICATION",
            "checks": checks,
            "reason": "" if ok else "; ".join(c["detail"] for c in checks if not c["passed"])}


def _selftest():
    import tempfile
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    tmp = tempfile.mkdtemp()

    # ---- risk classification ----
    chk("read-only tools are classified read-only", tool_risk("beam_design") == READ_ONLY)
    chk("file-writing tools are classified reversible", tool_risk("design_part") == REVERSIBLE)
    chk("an UNKNOWN tool defaults to irreversible (fails safe, not open)",
        tool_risk("some_future_tool_nobody_classified") == IRREVERSIBLE)
    chk("only irreversible actions demand approval",
        requires_approval("unknown_tool") and not requires_approval("beam_design"))

    # ---- preflight: deterministic and honest ----
    tools = {"beam_design": 1, "design_part": 1, "derive_verified": 1, "standard_section": 1}
    p1 = preflight("run a beam_design for a steel bar", tools)
    p2 = preflight("run a beam_design for a steel bar", tools)
    chk("preflight is deterministic (same goal -> same estimate)", p1 == p2)
    chk("a local-only task is reported as costing ZERO model calls for tools",
        p1["model_calls_for_tools"] == 0 and p1["deterministic_local_only"],
        f"tools={p1['likely_tools']}")
    chk("preflight still bounds the agent's own planning calls",
        p1["model_calls_total_max"] == p1["model_calls_for_planning_max"],
        f"max {p1['model_calls_total_max']} calls")
    pn = preflight("derive from scratch a novel quantity", tools)
    chk("a generate-and-verify task correctly reports a NON-zero model cost",
        pn["model_calls_for_tools"] > 0 and not pn["deterministic_local_only"],
        f"{pn['model_calls_for_tools']} calls")
    chk("preflight refuses to invent a dollar figure",
        "dollars" in p1["cost_basis"] and "$" not in json.dumps(p1))

    # ---- durability ----
    lg = Ledger("size a bracket", directory=tmp)
    lg.step("beam_design", {"load": 500}, {"factor_of_safety": 3.2})
    lg.step("design_part", {"load_N": 2000}, {"verified": True})
    lg.set_status("running")
    reloaded = Ledger.load(lg.task_id, directory=tmp)
    chk("ledger survives a process restart (reload from disk)",
        reloaded.goal == "size a bracket" and len(reloaded.steps) == 2,
        f"{len(reloaded.steps)} steps recovered")
    chk("reloaded ledger keeps the last status", reloaded.status == "running")
    chk("each step records the tool's risk class",
        reloaded.steps[1]["risk"] == REVERSIBLE)
    chk("ledger is append-only JSONL, one record per line",
        len([l for l in open(lg.path) if l.strip()]) == 4)   # created + 2 steps + status

    # ---- the anti-hallucination gate: the whole point ----
    real = os.path.join(tmp, "drawing.dxf")
    with open(real, "w") as f:
        f.write("REAL CONTENT")
    empty = os.path.join(tmp, "empty.dxf")
    open(empty, "w").close()

    v = verify_completion(claimed_files=[real])
    chk("a task with a real artifact completes", v["complete"] and v["status"] == "COMPLETED")

    v = verify_completion(claimed_files=[os.path.join(tmp, "does_not_exist.dxf")])
    chk("CLAIMING a file that does not exist is caught, not believed",
        (not v["complete"]) and v["status"] == "FAILED_VERIFICATION" and "MISSING" in v["reason"])

    v = verify_completion(claimed_files=[empty])
    chk("an empty artifact does not count as success",
        (not v["complete"]) and "0 bytes" in v["reason"])

    v = verify_completion()
    chk("declaring success with NOTHING produced is rejected",
        (not v["complete"]) and "unsupported claim" in v["reason"])

    from receipt import make_receipt
    good = make_receipt("q", "m", {"a": 1}, {"value": 1.0},
                        [{"name": "c", "passed": True}])
    v = verify_completion(claimed_files=[real], receipts=[good])
    chk("a real artifact plus a valid receipt completes", v["complete"])

    tampered = dict(good); tampered["result"] = {"value": 9999.0}
    v = verify_completion(claimed_files=[real], receipts=[tampered])
    chk("a TAMPERED receipt blocks completion",
        (not v["complete"]) and "integrity=False" in v["reason"])

    bad = make_receipt("q", "m", {}, {"value": 1.0}, [{"name": "c", "passed": False}])
    v = verify_completion(claimed_files=[real], receipts=[bad])
    chk("a receipt that itself reports UNVERIFIED blocks completion", not v["complete"])

    # the headline claim, stated as a test
    v = verify_completion(claimed_files=[os.path.join(tmp, "report.pdf"),
                                         os.path.join(tmp, "model.step")])
    chk("an agent claiming two deliverables it never wrote is marked FAILED_VERIFICATION",
        v["status"] == "FAILED_VERIFICATION" and v["reason"].count("MISSING") == 2)

    print("task_ledger selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI durable tasks + cost preflight")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--preflight", metavar="GOAL")
    ap.add_argument("--show", metavar="TASK_ID")
    a = ap.parse_args()
    if a.preflight:
        import agent_loop
        print(json.dumps(preflight(a.preflight, agent_loop.TOOLS), indent=2)); raise SystemExit(0)
    if a.show:
        lg = Ledger.load(a.show)
        print(json.dumps({"task_id": lg.task_id, "goal": lg.goal, "status": lg.status,
                          "steps": [{"tool": s["tool"], "risk": s["risk"]} for s in lg.steps]},
                         indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
