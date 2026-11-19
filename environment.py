#!/usr/bin/env python3
"""environment.py - is this the environment the results were verified on?

Every number in this repository was produced against one set of package
versions and one set of solver binaries. A month later, after a `pip install`
for something unrelated, it may not be the same environment - and nothing
would say so. The gate would still pass, or it would fail somewhere confusing,
and the honest answer to "can I trust last month's result" would be no.

This module answers three questions:

  DRIFT     which packages differ from requirements.lock, and by how much.
            A patch bump is noted; a major bump is flagged, because semantic
            versioning means a major bump is a promise that something broke.

  SOLVERS   which external binaries are present, their versions, and - the
            part that matters - what EngineAI LOSES when one is missing.
            Elmer absent does not break anything; it means heat and magnetics
            rest on two independent methods instead of three, and that is a
            change in the strength of the evidence, not a change in the code.

  CAPABILITY  the honest consequence. Not "3 warnings" but "cross-solver
            agreement on magnetics is unavailable, so those results are
            single-method".

It exits non-zero when a REQUIRED component is missing and zero when only
optional ones are, so it can gate a deployment without blocking a laptop.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["check_packages", "check_solvers", "report", "SOLVERS", "Drift"]

HERE = Path(__file__).parent
LOCK = HERE / "requirements.lock"


# name -> (command, version flag, required?, what is lost without it)
SOLVERS = {
    "ccx": ("ccx", "-v", False,
            "CalculiX: finite element cross-checks. Without it, stress and "
            "deflection rest on closed form alone - correct, but single-method"),
    "ElmerSolver": ("ElmerSolver", "--version", False,
                    "Elmer: the THIRD solver for heat and magnetics. Without it "
                    "those results have two independent methods, not three, so "
                    "a disagreement can be detected but not adjudicated"),
    "ngspice": ("ngspice", "-v", False,
                "circuit simulation. Without it, circuits are analytic only"),
    "gmsh": ("gmsh", "--version", False,
             "meshing. Without it, only built-in structured meshes are available"),
    "openssl": ("openssl", "version", False,
                "TLS certificate generation for --tls"),
    "ollama": ("ollama", "--version", False,
               "the language router. Without it, request routing falls back to "
               "the keyword table: 5 of 10 phrasings of a question reach a "
               "solver instead of 10"),
    "python3": ("python3", "--version", True, "nothing runs without it"),
}


@dataclass
class Drift:
    name: str
    locked: str
    installed: str
    severity: str          # same | patch | minor | MAJOR | missing | extra

    def __str__(self) -> str:
        return f"    {self.severity:8s} {self.name:28s} lock {self.locked:14s} now {self.installed}"


def _parse_lock() -> dict:
    if not LOCK.exists():
        return {}
    out = {}
    for line in LOCK.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)\s*==\s*(.+)$", line)
        if m:
            out[m.group(1).lower().replace("_", "-")] = m.group(2).strip()
    return out


def _installed() -> dict:
    try:
        from importlib.metadata import distributions
        return {d.metadata["Name"].lower().replace("_", "-"): d.version
                for d in distributions() if d.metadata.get("Name")}
    except Exception:
        return {}


def _severity(locked: str, now: str) -> str:
    if locked == now:
        return "same"

    def parts(v):
        return [int(x) for x in re.findall(r"\d+", v)[:3]] or [0]
    a, b = parts(locked), parts(now)
    a += [0] * (3 - len(a))
    b += [0] * (3 - len(b))
    if a[0] != b[0]:
        return "MAJOR"
    if a[1] != b[1]:
        return "minor"
    return "patch"


def check_packages() -> dict:
    locked, now = _parse_lock(), _installed()
    if not locked:
        return {"has_lock": False, "drifts": [], "summary": "no requirements.lock"}
    drifts = []
    for name, ver in sorted(locked.items()):
        cur = now.get(name)
        if cur is None:
            drifts.append(Drift(name, ver, "ABSENT", "missing"))
        else:
            s = _severity(ver, cur)
            if s != "same":
                drifts.append(Drift(name, ver, cur, s))
    counts: dict = {}
    for d in drifts:
        counts[d.severity] = counts.get(d.severity, 0) + 1
    return {"has_lock": True, "n_locked": len(locked), "drifts": drifts,
            "counts": counts,
            "clean": not any(d.severity in ("MAJOR", "missing") for d in drifts)}


def check_solvers() -> dict:
    found, absent = {}, {}
    for name, (cmd, flag, required, lost) in SOLVERS.items():
        path = shutil.which(cmd)
        if not path:
            absent[name] = {"required": required, "lost": lost}
            continue
        ver = ""
        try:
            r = subprocess.run([cmd, flag], capture_output=True, text=True,
                               timeout=15)
            ver = (r.stdout or r.stderr).strip().splitlines()
            ver = ver[0][:60] if ver else ""
        except Exception:
            ver = "(present, version unreadable)"
        found[name] = {"path": path, "version": ver, "required": required}
    return {"found": found, "absent": absent,
            "missing_required": [k for k, v in absent.items() if v["required"]]}


def report(verbose: bool = True) -> int:
    pk = check_packages()
    sv = check_solvers()
    say = print if verbose else (lambda *a, **k: None)

    say("=" * 74)
    say("PACKAGES")
    say("=" * 74)
    if not pk["has_lock"]:
        say("  no requirements.lock - run: pip freeze > requirements.lock")
    else:
        c = pk["counts"]
        say(f"  {pk['n_locked']} pinned, {len(pk['drifts'])} differ "
            f"({', '.join(f'{v} {k}' for k, v in sorted(c.items())) or 'none'})")
        for d in pk["drifts"]:
            if d.severity in ("MAJOR", "missing"):
                say(str(d))
        minor = [d for d in pk["drifts"] if d.severity in ("minor", "patch")]
        if minor:
            say(f"    ({len(minor)} minor/patch differences not listed)")
        if pk["clean"]:
            say("  -> no major drift; the verified results still stand")
        else:
            say("  -> MAJOR drift or missing packages. A major version bump is "
                "the maintainer saying something changed incompatibly, so the "
                "gate must be re-run before trusting any earlier result.")

    say("")
    say("=" * 74)
    say("SOLVERS")
    say("=" * 74)
    for name, info in sv["found"].items():
        say(f"  present  {name:14s} {info['version']}")
    for name, info in sv["absent"].items():
        tag = "REQUIRED" if info["required"] else "optional"
        say(f"  ABSENT   {name:14s} [{tag}]")
        say(f"           {info['lost']}")

    say("")
    say("=" * 74)
    say("WHAT THIS ENVIRONMENT CAN AND CANNOT CLAIM")
    say("=" * 74)
    if "ElmerSolver" in sv["absent"]:
        say("  heat and magnetics: TWO independent methods, not three. A "
            "disagreement between them can be detected but not adjudicated by "
            "majority.")
    else:
        say("  heat and magnetics: three independent methods - an outlier can "
            "be identified.")
    if "ccx" in sv["absent"]:
        say("  stress and deflection: closed form only, no finite element "
            "cross-check.")
    if "ollama" in sv["absent"]:
        say("  routing: keyword table only. Measured at 5 of 10 phrasings of "
            "the same question reaching a solver, against 10 of 10 with the "
            "model running.")
    if not sv["absent"]:
        say("  every optional component is present.")

    rc = 1 if sv["missing_required"] or not pk.get("clean", True) else 0
    say("")
    say(f"  exit {rc}  " + ("environment matches what was verified"
                            if rc == 0 else
                            "environment differs from what was verified"))
    return rc


def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    print("== version comparison ==")
    chk(_severity("1.2.3", "1.2.3") == "same", "identical versions")
    chk(_severity("1.2.3", "1.2.4") == "patch", "patch bump")
    chk(_severity("1.2.3", "1.3.0") == "minor", "minor bump")
    chk(_severity("1.2.3", "2.0.0") == "MAJOR", "major bump is flagged loudly")
    chk(_severity("2.4.6", "1.9.9") == "MAJOR", "a downgrade across major is MAJOR")

    print("== the lockfile is read ==")
    pk = check_packages()
    chk(pk["has_lock"], "requirements.lock exists")
    chk(pk["n_locked"] > 100, f"{pk.get('n_locked')} packages pinned")
    chk(isinstance(pk["drifts"], list), "drift list is produced")
    # the environment this was frozen from must not show major drift against itself
    major = [d for d in pk["drifts"] if d.severity == "MAJOR"]
    chk(not major, f"no major drift against the freeze ({[d.name for d in major][:3]})")

    print("== solvers are probed honestly ==")
    sv = check_solvers()
    chk("python3" in sv["found"], "python3 is found")
    chk(isinstance(sv["absent"], dict), "absent solvers are listed")
    for name, info in sv["absent"].items():
        chk(bool(info["lost"]),
            f"{name} states what capability is lost without it")
    chk(not sv["missing_required"],
        f"no required solver is missing ({sv['missing_required']})")

    print("== the report says what the environment can claim ==")
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = report(verbose=True)
    text = buf.getvalue()
    chk("WHAT THIS ENVIRONMENT CAN AND CANNOT CLAIM" in text,
        "the consequence section is printed")
    chk("independent methods" in text, "and it talks about method diversity")
    chk(rc in (0, 1), "an exit code is returned for deployment gating")

    print(f"\nenvironment.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    if "--json" in sys.argv:
        pk, sv = check_packages(), check_solvers()
        print(json.dumps({
            "packages": {"n_locked": pk.get("n_locked"), "clean": pk.get("clean"),
                         "counts": pk.get("counts"),
                         "drifts": [d.__dict__ for d in pk.get("drifts", [])]},
            "solvers": sv}, indent=2))
        raise SystemExit(0)
    raise SystemExit(report())
