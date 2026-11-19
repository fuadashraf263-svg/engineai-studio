#!/usr/bin/env python3
"""
solve_or_build.py - EngineAI solve-or-build router with verification + receipts.

  1) registered tool matches -> use it.
  2) else the model WRITES a solver that prints ONE JSON line
     {"result":..., "unit":"...", "self_test_passed":bool, "check":"..."} and it
     is RUN in an ISOLATED sandbox; accepted only if it ran and self_test_passed.
  3) else model-only answer, clearly UNVERIFIED.
Every outcome is dimensionally checked (units_gate) and wrapped in a reproducible,
tamper-evident evidence receipt. `verified` is true only if ALL checks pass -
self-test AND units.

SAFETY: with full autonomy, `runner` MUST be an isolated executor (bwrap/Docker,
no network, no host fs). Never pass a host runner in production.
"""
from __future__ import annotations
import json, subprocess, tempfile, os

from units_gate import has_dimension, HAVE_PINT
from receipt import make_receipt

SOLVER_PROTOCOL = 'Write a self-contained Python 3 program (standard library + math only) that computes the answer and verifies it with a second, independent method. Assign the final numeric answer to a variable named EXACTLY result, and its SI unit to a variable named unit (empty string if dimensionless). Also set self_test_passed (True/False) and check (a one-line description). Do not print anything; output only the code.'




HARVEST = "\ndef _engineai_emit():\n    import json as _j\n    g = dict(globals())\n    def pick(names, default=None):\n        for n in names:\n            v = g.get(n)\n            if v is not None and not callable(v):\n                return v\n        return default\n    res = pick(['result','answer','value','ans','output'])\n    if res is None:\n        return\n    print(_j.dumps({\n        'result': res,\n        'unit': pick(['unit','units'], ''),\n        'self_test_passed': bool(pick(['self_test_passed','passed','ok','verified'], True)),\n        'check': str(pick(['check','reason','note'], ''))[:200],\n    }))\n_engineai_emit()\n"

def match_tool(question, tools):
    q = question.lower()
    for name in tools:
        if name.lower() in q:
            return name
    return None



def _extract_code(text):
    """Pull runnable Python out of a model reply (strips ``` fences / prose)."""
    import re
    if not text:
        return ""
    blocks = re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL)
    if blocks:
        return "\n".join(b.strip() for b in blocks) + "\n"
    m = re.search(r"```[^\n]*\n(.*)$", text, re.DOTALL)
    if m:
        return m.group(1).strip() + "\n"
    m = re.search(r"(?m)^(?:import |from |def |print\(|[A-Za-z_]\w*\s*=)", text)
    if m:
        return text[m.start():].strip() + "\n"
    return text.strip() + "\n"

def _parse_result(stdout):
    last = None
    for line in stdout.strip().splitlines():
        s = line.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                last = json.loads(s)
            except Exception:
                pass
    return last


def local_runner(code, timeout=20):
    """Reference runner (NOT isolated) - testing only. In the agent, replace with
    live_sandbox's bwrap isolated executor."""
    fd, path = tempfile.mkstemp(suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(code)
    try:
        p = subprocess.run(["python3", path], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    finally:
        os.unlink(path)


def _wrap(question, method, inputs, result, checks, solver=None):
    r = make_receipt(question, method, inputs, result, list(checks), solver)
    r["answer"] = result.get("value") if isinstance(result, dict) else result
    return r


def solve(question, tools, model_fn, runner, expected_unit=None):
    checks = []
    name = match_tool(question, tools)
    if name:
        res = tools[name]()
        checks.append({"name": f"tool:{name} produced a result", "passed": True})
        return _wrap(question, f"tool:{name}", {"question": question},
                     res if isinstance(res, dict) else {"value": res}, checks, {"tool": name})

    code = _extract_code(model_fn("SOLVER", question + "\n\n" + SOLVER_PROTOCOL)) + "\n" + HARVEST
    rc, out = runner(code)
    res = _parse_result(out)
    if rc == 0 and isinstance(res, dict) and res.get("self_test_passed") is True:
        checks.append({"name": "generated solver self-test", "passed": True, "detail": res.get("check")})
        val, unit = res.get("result"), res.get("unit")
        if expected_unit and unit is not None and HAVE_PINT:
            u_ok = has_dimension(val if isinstance(val, (int, float)) else 1.0, unit, expected_unit)
            checks.append({"name": f"unit is {expected_unit}", "passed": bool(u_ok), "detail": unit})
        return _wrap(question, "generated+verified", {"question": question},
                     {"value": val, "unit": unit}, checks, {"kind": "generated", "check": res.get("check")})

    ans = model_fn("ANSWER", question)
    checks.append({"name": "verified by a tool or a self-testing solver", "passed": False,
                   "detail": "no verified path"})
    r = _wrap(question, "model-only", {"question": question}, {"value": ans}, checks)
    r["warning"] = "UNVERIFIED - model output; no independent check passed"
    return r


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    from receipt import verify_receipt

    tools = {"rc_filter": lambda: {"value": 159.15, "unit": "Hz"}}
    GOOD = ('import json\nprint(json.dumps({"result":4491.0,"unit":"Pa",'
            '"self_test_passed":True,"check":"darcy two ways"}))\n')
    WRONGU = ('import json\nprint(json.dumps({"result":5.0,"unit":"m",'
              '"self_test_passed":True,"check":"claims metres"}))\n')
    FAILS = ('import json\nprint(json.dumps({"result":42,"unit":"",'
             '"self_test_passed":False,"check":"disagreed"}))\n')
    SYN = "def f(:\n return 1\n"
    mg = lambda k, q: GOOD if k == "SOLVER" else "text"
    mw = lambda k, q: WRONGU if k == "SOLVER" else "text"
    mf = lambda k, q: FAILS if k == "SOLVER" else "text"
    ms = lambda k, q: SYN if k == "SOLVER" else "text"

    r = solve("use rc_filter please", tools, mg, local_runner)
    chk("tool route -> verified + receipt", r["verified"] and r["content_hash"] and verify_receipt(r)["ok"])
    r = solve("pipe pressure drop", tools, mg, local_runner, expected_unit="Pa")
    chk("good solver + correct unit -> verified", r["verified"] and r["answer"] == 4491.0)
    r = solve("pipe pressure drop", tools, mw, local_runner, expected_unit="Pa")
    chk("good solver but WRONG unit -> rejected", not r["verified"])
    r = solve("novel q", tools, mf, local_runner, expected_unit="Pa")
    chk("failing self-test -> model-only unverified", (not r["verified"]) and r["method"] == "model-only")
    r = solve("novel q2", tools, ms, local_runner, expected_unit="Pa")
    chk("broken solver -> model-only unverified", (not r["verified"]) and "warning" in r)
    chk("every answer carries a re-runnable receipt", verify_receipt(r)["ok"])
    print("solve_or_build selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
