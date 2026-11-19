#!/usr/bin/env python3
"""
consensus.py - research-hardened verification for generated solvers.

Generates independent solvers, runs each in isolation, and accepts a result ONLY
if a majority AGREE numerically AND the agreed value passes the external Pint unit
gate. Now records a per-attempt log so an unverified result explains WHY each
attempt failed. Fail-safe: no consensus -> no answer.
"""
from __future__ import annotations
from collections import Counter
from units_gate import has_dimension, HAVE_PINT
try:
    from units_gate import _UREG
except Exception:
    _UREG = None
from receipt import make_receipt
from solve_or_build import _parse_result, SOLVER_PROTOCOL, _extract_code, HARVEST

STATUS_HELP = {
    "ok": "produced a usable numeric result",
    "model_error": "the model call raised (timeout / server / OOM)",
    "empty_reply": "the model returned nothing",
    "no_code": "reply had no runnable code block (prose only)",
    "run_error": "the generated code crashed when executed",
    "no_result": "code ran but emitted no harvestable result (the 'correct-but-not-usable' small-model failure)",
}


def _base(value, unit):
    """(magnitude in base SI, dimensionality) so runs are compared as PHYSICAL
    quantities, not bare numbers. Without this, 12.566 m^2 and 12.566 ft^2 'agree'
    - they are numerically identical and physically different. Returns None when the
    unit cannot be parsed, so such a run cannot silently join a cluster."""
    if not HAVE_PINT:
        return (float(value), None)
    try:
        q = _UREG.Quantity(float(value), unit or "").to_base_units()
        return (float(q.magnitude), str(q.dimensionality))
    except Exception:
        return None


def _cluster(runs, rel_tol):
    nums = [(v, u) for v, u in runs if isinstance(v, (int, float))]
    n = len(nums)
    if n < 2:
        return {"agreed": False, "n": n, "n_agree": 0,
                "value": nums[0][0] if n == 1 else None,
                "unit": nums[0][1] if n == 1 else None, "spread": None}

    # compare in base SI: agreement must be on the physical quantity, and two runs
    # with different dimensionality can never agree however close their numbers are
    keyed = [(v, u, _base(v, u)) for v, u in nums]
    usable = [(v, u, b) for v, u, b in keyed if b is not None]
    if len(usable) < 2:
        return {"agreed": False, "n": n, "n_agree": 0, "value": None, "unit": None, "spread": None}

    bvals = sorted(b[0] for _, _, b in usable)
    med = bvals[len(bvals) // 2]
    med_dim = next(b[1] for _, _, b in usable if b[0] == med)

    def close(a, b):
        d = max(abs(a), abs(b), 1e-12); return abs(a - b) / d <= rel_tol

    cluster = [(v, u) for v, u, b in usable if b[1] == med_dim and close(b[0], med)]
    n_agree = len(cluster)
    agreed = n_agree >= max(2, (n // 2) + 1)
    cvals = sorted(v for v, _ in cluster)
    return {"agreed": agreed, "n": n, "n_agree": n_agree,
            "value": cvals[len(cvals) // 2] if cluster else None,
            "unit": cluster[0][1] if cluster else None,
            "spread": max(b[0] for _, _, b in usable) - min(b[0] for _, _, b in usable)}


def solve_consensus(question, model_fn, runner, k=3, expected_unit=None, rel_tol=1e-3, max_attempts=None):
    max_attempts = max_attempts if max_attempts is not None else (2 * k + 2)
    runs, tries, log = [], 0, []
    while len(runs) < k and tries < max_attempts:
        i = tries; tries += 1
        try:
            reply = model_fn("SOLVER", f"[independent attempt {i}] " + question + "\n\n" + SOLVER_PROTOCOL)
        except Exception as e:
            log.append({"attempt": i, "status": "model_error", "detail": str(e)[:160]}); continue
        if not str(reply or "").strip():
            log.append({"attempt": i, "status": "empty_reply", "detail": ""}); continue
        code = _extract_code(reply)
        if not code.strip():
            log.append({"attempt": i, "status": "no_code", "detail": str(reply)[:120].replace(chr(10), " ")}); continue
        rc, out = runner(code + "\n" + HARVEST)
        res = _parse_result(out)
        if rc != 0:
            log.append({"attempt": i, "status": "run_error", "detail": str(out)[:160].replace(chr(10), " ")}); continue
        if not (isinstance(res, dict) and isinstance(res.get("result"), (int, float))):
            log.append({"attempt": i, "status": "no_result", "detail": str(out)[:160].replace(chr(10), " ")}); continue
        runs.append((res["result"], res.get("unit")))
        log.append({"attempt": i, "status": "ok", "detail": f"{res['result']} {res.get('unit')}"})
    con = _cluster(runs, rel_tol)
    checks = [{"name": f"{con['n_agree']}/{con['n']} independent solvers agree (<= {rel_tol} rel)",
               "passed": con["agreed"], "detail": f"spread={con['spread']}; attempts={tries}"}]
    value, unit = con["value"], con["unit"]
    if con["agreed"] and expected_unit and unit is not None and HAVE_PINT:
        dim_ok = bool(has_dimension(value, unit, expected_unit))
        checks.append({"name": f"unit is {expected_unit}", "passed": dim_ok, "detail": unit})
        # Dimensional agreement is NOT unit agreement. The solvers may legitimately
        # agree in ft^2 while the caller asked for m^2; returning the bare number then
        # is wrong by the conversion factor. Convert into what was actually asked for.
        if dim_ok and unit != expected_unit:
            try:
                value = float(_UREG.Quantity(value, unit).to(expected_unit).magnitude)
                unit = expected_unit
            except Exception:
                checks.append({"name": f"convert answer to {expected_unit}", "passed": False,
                               "detail": f"could not convert from {unit}"})
    verified = con["agreed"] and all(c["passed"] for c in checks)
    r = make_receipt(question, "generated+consensus" if verified else "unverified",
                     {"question": question, "samples": k, "attempts": tries},
                     {"value": value, "unit": unit}, checks,
                     {"kind": "consensus", "runs": con["n"], "agree": con["n_agree"]})
    r["answer"] = value
    r["attempt_log"] = log
    r["attempt_summary"] = dict(Counter(a["status"] for a in log))
    if not verified:
        r["warning"] = "UNVERIFIED - independent solvers did not agree, or unit/format failed"
        bad = Counter(a["status"] for a in log if a["status"] != "ok")
        if bad:
            top = bad.most_common(1)[0][0]
            r["diagnosis"] = f"most attempts failed as '{top}': {STATUS_HELP.get(top, top)}"
    return r


def _mk(value, unit, flag="True"):
    return (f'import json\nprint(json.dumps({{"result":{value},"unit":"{unit}",'
            f'"self_test_passed":{flag},"check":"x"}}))\n')


def _cycler(seq):
    box = {"i": 0}
    def m(kind, q):
        s = seq[box["i"] % len(seq)]; box["i"] += 1; return s
    return m


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    from sandbox_runner import run as sbx
    r = solve_consensus("area", _cycler([_mk(12.566, "m^2")] * 6), sbx, k=3, expected_unit="m^2")
    chk("3 agree -> verified", r["verified"] and abs(r["answer"] - 12.566) < 1e-3)
    r = solve_consensus("area", _cycler([_mk(12.566, "m^2"), _mk(12.566, "m^2"), _mk(999.0, "m^2"), _mk(12.566, "m^2")]),
                        sbx, k=3, expected_unit="m^2")
    chk("gamed minority outvoted", r["verified"] and abs(r["answer"] - 12.566) < 1e-3)
    r = solve_consensus("area", _cycler([_mk(12.566, "m^2"), _mk(10.0, "m^2"), _mk(8.0, "m^2")]),
                        sbx, k=3, expected_unit="m^2", max_attempts=3)
    chk("no agreement -> rejected", not r["verified"])
    r = solve_consensus("area", _cycler([_mk(12.566, "m")] * 6), sbx, k=3, expected_unit="m^2")
    chk("agreed but wrong unit -> rejected", not r["verified"])
    # regression: the same NUMBER in different units is not the same ANSWER. Two
    # solvers agreeing on 12.566 ft^2 must not be reported to a caller who asked for
    # m^2 as a bare "12.566" - that is wrong by 10.76x.
    r = solve_consensus("area", _cycler([_mk(12.566, "m^2"), _mk(12.566, "ft^2"), _mk(12.566, "ft^2")]),
                        sbx, k=3, expected_unit="m^2", max_attempts=3)
    chk("majority in another unit is converted, not returned raw",
        r["verified"] and abs(r["answer"] - 1.16741) < 1e-3 and r["result"]["unit"] == "m^2",
        f"answer={r['answer']:.5f} {r['result']['unit']}")
    # a numeric coincidence across DIFFERENT dimensions must never count as agreement
    r = solve_consensus("area", _cycler([_mk(12.566, "m^2"), _mk(12.566, "m"), _mk(12.566, "s")]),
                        sbx, k=3, expected_unit="m^2", max_attempts=3)
    chk("same number, different DIMENSIONS -> not agreement", not r["verified"],
        f"answer={r['answer']}")
    # the converse must still work: genuinely equal answers written in different units
    r = solve_consensus("length", _cycler([_mk(0.001, "m"), _mk(1.0, "mm"), _mk(0.1, "cm")]),
                        sbx, k=3, expected_unit="m")
    chk("same quantity in different units -> IS agreement", r["verified"],
        f"answer={r['answer']} {r['result']['unit']}")
    r = solve_consensus("area", _cycler([_mk(12.566, "m^2"), "junk not code", _mk(12.566, "m^2"),
                                         "still junk", _mk(12.566, "m^2")]), sbx, k=3, expected_unit="m^2")
    chk("retries recover a flaky model", r["verified"] and abs(r["answer"] - 12.566) < 1e-3, f"attempts in receipt")
    chk("attempt_summary counts the OK runs", r["attempt_summary"].get("ok", 0) >= 3, str(r["attempt_summary"]))
    chk("attempt_log records every attempt", len(r["attempt_log"]) == r["inputs"]["attempts"])
    bad = solve_consensus("area", _cycler(["Sorry, the area is 12.566 m^2 (no code)."]),
                          sbx, k=2, expected_unit="m^2", max_attempts=4)
    chk("prose-only model -> unverified + named diagnosis",
        (not bad["verified"]) and bool(bad.get("diagnosis")), bad.get("diagnosis", ""))
    chk("diagnosis explains the failure in plain words",
        any(h in bad.get("diagnosis", "") for h in ("no code", "no harvestable", "crashed", "returned nothing")),
        bad.get("diagnosis", ""))
    print("consensus selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
