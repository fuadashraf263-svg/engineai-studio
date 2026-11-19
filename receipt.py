#!/usr/bin/env python3
"""
receipt.py - EngineAI evidence receipt: a reproducible, tamper-evident record of
how an answer was produced. Every verified answer carries one - inputs, method,
result, the checks that passed, and a content hash. Re-runnable and auditable:
the audit trail is the product.
"""
from __future__ import annotations
import hashlib, json, time


def _hash(body):
    payload = {k: body[k] for k in ("question", "method", "inputs", "result", "checks", "solver", "verified")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def make_receipt(question, method, inputs, result, checks, solver=None):
    checks = list(checks)
    verified = bool(checks) and all(c.get("passed") for c in checks)
    body = {"question": question, "method": method, "inputs": inputs, "result": result,
            "checks": checks, "solver": solver or {}, "verified": verified}
    body["content_hash"] = _hash(body)
    body["created_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    body["status"] = "verified" if verified else "unverified"
    return body


class _Verdict(dict):
    """A verdict that is FALSY when the receipt fails.

    verify_receipt used to return a plain dict, so the natural-looking

        if verify_receipt(r):        # always True - a dict with keys is truthy
            trust(r)

    accepted a forged receipt silently. Every caller in this repo happens to write
    `verify_receipt(r)["ok"]` and is therefore correct, but the trap is one
    reasonable line away for the next caller, and it sits on the one mechanism the
    whole product's credibility rests on. Subclassing dict keeps ["ok"] working
    while making the boolean reading agree with it.
    """
    def __bool__(self):
        return bool(self.get("ok"))


def verify_receipt(receipt):
    integrity = receipt.get("content_hash") == _hash(receipt)
    consistent = receipt.get("verified") == (bool(receipt.get("checks")) and all(c.get("passed") for c in receipt["checks"]))
    return _Verdict(integrity=integrity, consistent=consistent,
                    ok=integrity and consistent)


# ---------------------------------------------------------------------------
# WHAT THE ABOVE DOES NOT COVER, AND WHY IT WAS ADDED
#
# An outside reviewer read a transcript of this project and said:
#
#   "A digit is not evidence a calculation ran. A valid receipt needs a run
#    ID, input hash, solver version, output artifact, exit status, and parsed
#    result."
#
# They were right, and the gap was real. make_receipt seals the JSON BODY -
# question, inputs, result, checks - which catches someone editing the record.
# It says nothing about the FILES the run produced, the versions of the tools
# that produced them, or which code revision was running. A receipt whose
# artifact has since been overwritten still verified.
#
# These additions are strictly additive: make_receipt and verify_receipt keep
# their old signatures and old behaviour, so every existing caller is
# unaffected.
# ---------------------------------------------------------------------------

import os as _os, subprocess as _sp, sys as _sys, uuid as _uuid   # noqa: E402

_HERE = _os.path.dirname(_os.path.abspath(__file__))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tool_versions(names=("python", "cadquery", "gmsh", "calculix", "cantera",
                         "numpy", "scipy")):
    """Read live. A hardcoded version is a claim about the environment, not a
    record of it, and it goes stale without anyone noticing."""
    out = {}
    for n in names:
        if n == "python":
            out[n] = _sys.version.split()[0]
        elif n == "calculix":
            try:
                p = _sp.run(["ccx", "-v"], capture_output=True, text=True,
                            timeout=20)
                txt = ((p.stdout or "") + (p.stderr or "")).strip()
                out[n] = next((l.strip() for l in txt.splitlines()
                               if "version" in l.lower()), txt[:60]) or "unknown"
            except Exception as e:                            # noqa: BLE001
                out[n] = f"unavailable ({type(e).__name__})"
        else:
            try:
                import importlib.metadata as _md
                out[n] = _md.version(n)
            except Exception:                                 # noqa: BLE001
                out[n] = "not installed"
    return out


def code_revision():
    try:
        rev = _sp.run(["git", "-C", _HERE, "rev-parse", "HEAD"],
                      capture_output=True, text=True, timeout=10)
        st = _sp.run(["git", "-C", _HERE, "status", "--porcelain"],
                     capture_output=True, text=True, timeout=10)
        return {"commit": (rev.stdout or "").strip() or "unknown",
                "dirty": bool((st.stdout or "").strip())}
    except Exception:                                         # noqa: BLE001
        return {"commit": "unknown", "dirty": None}


def provenance(receipt, run_id=None, artifacts=(), exit_status=0,
               sources=(), tools=None):
    """Attach run identity, tool versions, file hashes and input provenance.

    artifacts : paths on disk. Each is hashed so a later edit is detectable.
    sources   : (field, value, origin, user_supplied) - where each input came
                from. 'user-supplied' is a legitimate origin; silently using a
                default is not, and this is where that gets recorded.
    """
    receipt["run_id"] = run_id or _uuid.uuid4().hex
    receipt["exit_status"] = int(exit_status)
    receipt["code"] = code_revision()
    receipt["tools"] = tools if tools is not None else tool_versions()
    receipt["sources"] = [
        {"field": f, "value": v, "origin": o, "user_supplied": bool(u)}
        for (f, v, o, u) in sources]
    recs = []
    for p in artifacts:
        if not _os.path.exists(p):
            recs.append({"path": p, "missing": True})
            continue
        recs.append({"path": _os.path.abspath(p),
                     "bytes": _os.path.getsize(p), "sha256": sha256_file(p)})
    receipt["artifacts"] = recs

    # COMPUTED is EARNED. A non-zero exit, a failed check, or no artifact at
    # all each downgrade it - there is no path here that stamps the strongest
    # badge on a run that did not actually succeed and leave evidence.
    if exit_status != 0:
        receipt["basis"] = "FAILED"
    elif not receipt.get("verified"):
        receipt["basis"] = "UNVERIFIED"
    elif not recs or any(a.get("missing") for a in recs):
        receipt["basis"] = "COMPUTED_NO_ARTIFACT"
    else:
        receipt["basis"] = "COMPUTED"
    # Re-seal: the hash must cover the new fields or they could be edited
    # freely, which would make the whole addition decorative.
    receipt["content_hash"] = _hash_full(receipt)
    return receipt


_EXTRA = ("run_id", "exit_status", "code", "tools", "sources", "artifacts",
          "basis")


def _hash_full(body):
    base = {k: body[k] for k in ("question", "method", "inputs", "result",
                                 "checks", "solver", "verified")}
    base.update({k: body[k] for k in _EXTRA if k in body})
    return hashlib.sha256(
        json.dumps(base, sort_keys=True, default=str).encode()).hexdigest()


def verify_full(receipt):
    """Everything verify_receipt does, plus the artifacts still on disk.

    Without re-hashing the files, a receipt is only a claim that they existed
    once.
    """
    expected = _hash_full(receipt) if any(k in receipt for k in _EXTRA) \
        else _hash(receipt)
    integrity = receipt.get("content_hash") == expected
    consistent = receipt.get("verified") == (
        bool(receipt.get("checks"))
        and all(c.get("passed") for c in receipt["checks"]))
    problems = []
    checked = 0
    for a in receipt.get("artifacts", []):
        if a.get("missing"):
            problems.append(f"{a['path']} was missing when the receipt was written")
            continue
        if not _os.path.exists(a["path"]):
            problems.append(f"{_os.path.basename(a['path'])} is gone from disk")
            continue
        now = sha256_file(a["path"])
        checked += 1
        if now != a["sha256"]:
            problems.append(
                f"{_os.path.basename(a['path'])} has CHANGED since the run "
                f"({a['sha256'][:12]} -> {now[:12]})")
    return _Verdict(integrity=integrity, consistent=consistent,
                    artifacts_checked=checked, problems=problems,
                    basis=receipt.get("basis"),
                    ok=integrity and consistent and not problems
                    and receipt.get("exit_status", 0) == 0)


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    checks = [{"name": "unit is pressure", "passed": True, "detail": "Pa"},
              {"name": "darcy==hagen-poiseuille", "passed": True, "detail": "0%"}]
    r = make_receipt("pipe dP", "darcy", {"U": "1.5 m/s", "D": "0.05 m"},
                     {"value": 4491.0, "unit": "Pa"}, checks, {"tool": "fluids.pressure_drop", "version": 1})
    chk("all checks pass -> verified", r["verified"] and r["status"] == "verified")
    r2 = make_receipt("pipe dP", "darcy", {"U": "1.5 m/s", "D": "0.05 m"},
                      {"value": 4491.0, "unit": "Pa"}, checks, {"tool": "fluids.pressure_drop", "version": 1})
    chk("hash deterministic (reproducible id)", r["content_hash"] == r2["content_hash"], r["content_hash"][:16])
    chk("verify_receipt ok on a clean receipt", verify_receipt(r)["ok"])
    tampered = dict(r); tampered["result"] = {"value": 9999.0, "unit": "Pa"}
    chk("tampering breaks integrity", not verify_receipt(tampered)["integrity"])

    # Tamper each field the hash is supposed to cover, one at a time. Covering the
    # ANSWER is the obvious case; covering the INPUTS and the CHECKS matters just as
    # much - a receipt that only sealed the answer would let someone rewrite the
    # question it answered, or flip a failed check to passed, and still verify.
    import copy
    for field, mutate in (
            ("result", lambda d: d.__setitem__("result", {"value": 1.0, "unit": "Pa"})),
            ("inputs", lambda d: d["inputs"].__setitem__("D", "0.5 m")),
            ("checks", lambda d: d["checks"][0].__setitem__("passed", False)),
            ("question", lambda d: d.__setitem__("question", "something else")),
            ("method", lambda d: d.__setitem__("method", "guessed")),
            ("solver", lambda d: d["solver"].__setitem__("tool", "other")),
            ("verified", lambda d: d.__setitem__("verified", False)),
    ):
        t = copy.deepcopy(r)
        mutate(t)
        chk(f"tampering with '{field}' is detected", not verify_receipt(t)["ok"])

    chk("a verdict is FALSY when the receipt fails (not just a truthy dict)",
        not verify_receipt(tampered), "guards `if verify_receipt(r):`")
    chk("...and truthy when it passes", bool(verify_receipt(r)))
    chk("the verdict still reads as a dict for existing callers",
        verify_receipt(r)["ok"] is True and "integrity" in verify_receipt(r))
    bad = make_receipt("x", "m", {}, {"value": 1}, [{"name": "c", "passed": False}])
    chk("any failed check -> unverified", (not bad["verified"]) and bad["status"] == "unverified")
    # ---- provenance: run id, tool versions, code revision, file hashes ----
    print("  -- provenance, which the body hash alone did not cover --")
    import tempfile
    td = tempfile.mkdtemp(prefix="engineai_rcpt_")
    art = _os.path.join(td, "field.vtu")
    with open(art, "w") as fh:
        fh.write("<VTKFile>stress field</VTKFile>")

    full = make_receipt("plate under 3 kN", "gmsh+ccx",
                        {"load_N": 3000, "E_MPa": 210000},
                        {"max_vm_MPa": 61.29},
                        [{"name": "equilibrium", "passed": True,
                          "detail": "0.000% residual"}],
                        {"tool": "CalculiX", "version": "2.21"})
    full = provenance(full, artifacts=[art], exit_status=0,
                      sources=[("E_MPa", 210000, "material table rev 2024-01",
                                False),
                               ("load_N", 3000, "user", True)])
    chk("a clean run with an artifact earns COMPUTED",
        full["basis"] == "COMPUTED", full["basis"])
    chk("it carries a run id", len(full["run_id"]) == 32)
    chk("tool versions are read LIVE, not hardcoded",
        full["tools"]["python"].startswith("3."), full["tools"]["python"])
    chk("the code revision is recorded",
        full["code"]["commit"] != "unknown", full["code"]["commit"][:8])
    chk("the artifact FILE is hashed",
        len(full["artifacts"][0]["sha256"]) == 64)
    chk("a user-supplied input is marked as such",
        next(s for s in full["sources"] if s["field"] == "load_N")["user_supplied"])
    chk("a table value carries its origin",
        "rev 2024-01" in next(s for s in full["sources"]
                              if s["field"] == "E_MPa")["origin"])
    chk("verify_full passes on a fresh receipt", verify_full(full)["ok"])

    # The case the file hash exists for: the artifact is edited afterwards.
    with open(art, "w") as fh:
        fh.write("<VTKFile>DIFFERENT numbers</VTKFile>")
    v = verify_full(full)
    chk("an EDITED artifact is caught", not v["ok"] and v["problems"])
    chk("...and named in the problem", "has CHANGED" in v["problems"][0],
        v["problems"][0][:56])
    _os.remove(art)
    chk("a DELETED artifact is caught too",
        "gone from disk" in verify_full(full)["problems"][0])

    # The new fields must be sealed, or adding them would be decorative.
    tampered = json.loads(json.dumps(full))
    tampered["basis"] = "COMPUTED"
    tampered["exit_status"] = 0
    tampered["tools"]["calculix"] = "9.99 (fake)"
    chk("editing the recorded TOOL VERSION breaks the hash",
        not verify_full(tampered)["integrity"])
    t2 = json.loads(json.dumps(full))
    t2["run_id"] = "0" * 32
    chk("editing the run id breaks it too",
        not verify_full(t2)["integrity"])

    print("  -- COMPUTED is earned, not asserted --")
    failed = provenance(
        make_receipt("q", "m", {}, {"v": 1},
                     [{"name": "equilibrium", "passed": False}]),
        artifacts=[], exit_status=0)
    chk("a failed physics check blocks COMPUTED",
        failed["basis"] == "UNVERIFIED", failed["basis"])
    crashed = provenance(
        make_receipt("q", "m", {}, {"v": 1}, [{"name": "c", "passed": True}]),
        artifacts=[], exit_status=201)
    chk("a non-zero solver exit blocks it", crashed["basis"] == "FAILED",
        crashed["basis"])
    noart = provenance(
        make_receipt("q", "m", {}, {"v": 1}, [{"name": "c", "passed": True}]),
        artifacts=[])
    chk("producing no artifact blocks it",
        noart["basis"] == "COMPUTED_NO_ARTIFACT", noart["basis"])

    print("  -- what a receipt does NOT prove --")
    # A perfectly reproducible run of a wrong model is still wrong. Integrity
    # is not physics, and the empty check list has to stay visible.
    hollow = provenance(make_receipt("q", "m", {}, {"v": 1}, []),
                        artifacts=[], exit_status=0)
    chk("no checks at all -> never verified", not hollow["verified"])
    chk("...and the basis says UNVERIFIED, not COMPUTED",
        hollow["basis"] == "UNVERIFIED", hollow["basis"])

    import shutil
    shutil.rmtree(td, ignore_errors=True)
    print("receipt selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
