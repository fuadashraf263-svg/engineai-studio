#!/usr/bin/env python3
"""
propose.py -- the AI improves itself SAFELY: propose, test, human approves.

THE PRINCIPLE
-------------
You asked for an AI that can write new code, fix bugs, and extend itself to
meet a goal. That is real and valuable -- but an AI that edits its own code
and runs it with NO human check will eventually break the very thing that
keeps it honest (the verification layer), or "fix" a failing test by
deleting the test. So this does it the way real engineering teams do:

    1. PROPOSE   -- the AI writes the new tool / fix into a quarantined
                    proposals/ folder. It touches NOTHING live.
    2. TEST      -- the proposal MUST ship a self-test; propose.py runs it
                    in a subprocess. No self-test, or a failing one =
                    rejected automatically.
    3. REVIEW    -- you read the diff and the test output.
    4. APPROVE   -- only on your explicit yes does it copy into the live
                    system. You are the merge gate, always.

WHAT IS LOCKED AND WHY
----------------------
The files that make the AI honest -- the verification/benchmark core --
are on a PROTECTED list. A proposal that tries to modify them is refused
outright, even by you through this tool, because weakening the checks is
exactly the failure we are preventing. The AI may add NEW tools; it may
never quietly weaken the checks that catch its mistakes. Extending
capability is safe; eroding verification is not.

This is not a limit on the AI's power -- it is what makes its power
trustworthy. A student relies on the checks being intact.

Run:  python propose.py new "<what the tool should do>"    # AI drafts it
      python propose.py list                                # pending proposals
      python propose.py test <name>                         # run its selftest
      python propose.py show <name>                         # print the code
      python propose.py approve <name>                      # merge (after test)
      python propose.py reject <name>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from safe_write import write_python_checked

HOME = Path.home() / "engineai"
PROPOSALS = HOME / "proposals"
LOG = PROPOSALS / "_log.json"

# The honesty core. A proposal may NEVER modify these -- not even with your
# approval through this tool -- because they are what keep every answer
# checked. To change these, edit them yourself, deliberately, outside this
# self-improvement path.
PROTECTED = {
    "benchmark.py", "exam.py", "propose.py",
    "handbook.py",       # verified formulas -- changing these changes truth
    "mathsolver.py",     # round-trip checks
    "cad.py",            # re-measure verification
    "materials.py",      # reference values
}

G, R, Y, O = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


DRAFT_SYSTEM = """You are extending an engineering AI that lives or dies on \
being verifiable. Write a SINGLE Python module that does what the user asks. \
Hard requirements:
- It MUST include a `selftest()` function and a `--selftest` CLI path that \
exits 0 on success, 1 on failure.
- The self-test must check the module's output against KNOWN-correct values \
computed independently (from a formula or a hand-worked example), not against \
the module trusting itself.
- No network calls, no reading/writing outside its own scope.
- Pure standard library plus numpy/scipy/sympy if needed.
- If you cannot make a correct, self-testable tool, say so plainly instead \
of writing something unverified.
Output ONLY the Python code, no prose, no markdown fences."""


def _log_read():
    if LOG.exists():
        return json.loads(LOG.read_text())
    return {}


def _log_write(d):
    PROPOSALS.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(d, indent=2))


def cmd_new(description: str) -> int:
    """Ask the brain to draft a self-testable tool into proposals/."""
    PROPOSALS.mkdir(parents=True, exist_ok=True)
    name = "prop_" + "".join(c if c.isalnum() else "_"
                             for c in description.lower())[:40].strip("_")
    dest = PROPOSALS / f"{name}.py"
    if dest.exists():
        name = f"{name}_{int(time.time())}"
        dest = PROPOSALS / f"{name}.py"

    # use whichever brain is configured (local Qwen or cloud)
    cfg = json.loads((HOME / "config.json").read_text()) \
        if (HOME / "config.json").exists() else {"budget": {}}
    try:
        sys.path.insert(0, str(HOME))
        try:
            import local_brain
            code, _ = local_brain.ask_local(
                DRAFT_SYSTEM + "\n\nTOOL TO WRITE:\n" + description)
        except Exception:
            from ask_command import ask_brain
            code, _ = ask_brain(cfg, DRAFT_SYSTEM + "\n\nTOOL:\n" +
                                description, None)
    except Exception as e:
        print(f"  brain unavailable: {e}")
        return 1

    # strip accidental markdown fences
    code = code.strip()
    if code.startswith("```"):
        code = code.split("```", 2)[1]
        if code.startswith("python"):
            code = code[6:]
    try:
        write_python_checked(dest, code.strip() + "\n")
    except ValueError as e:
        print(f"[propose] rejected {name!r}: {e}")
        return 1

    log = _log_read()
    log[name] = {"description": description, "created": time.time(),
                 "status": "pending", "tested": False, "test_passed": None}
    _log_write(log)

    print(f"  {Y}proposed{O}  {name}")
    print(f"  written to {dest} (quarantined -- nothing live changed)")
    print(f"\n  next:  python propose.py test {name}")
    print(f"         python propose.py show {name}")
    return 0


def cmd_test(name: str) -> int:
    """Run the proposal's --selftest in a subprocess. This is the gate."""
    f = PROPOSALS / f"{name}.py"
    if not f.exists():
        print(f"  no proposal '{name}'")
        return 1
    src = f.read_text()
    if "selftest" not in src or "--selftest" not in src:
        print(f"  {R}REJECTED{O}: proposal has no --selftest. A tool that "
              f"cannot test itself\n  cannot enter a system whose whole "
              f"value is verification.")
        log = _log_read()
        if name in log:
            log[name]["tested"] = True
            log[name]["test_passed"] = False
            _log_write(log)
        return 1

    print(f"  running {name} --selftest ...\n")
    try:
        r = subprocess.run([sys.executable, str(f), "--selftest"],
                           capture_output=True, text=True, timeout=120,
                           cwd=str(HOME))
    except subprocess.TimeoutExpired:
        print(f"  {R}TIMEOUT{O} -- self-test hung. Rejected.")
        return 1
    print(r.stdout[-2000:])
    if r.stderr:
        print(r.stderr[-500:])

    passed = r.returncode == 0
    log = _log_read()
    if name in log:
        log[name]["tested"] = True
        log[name]["test_passed"] = passed
        _log_write(log)

    if passed:
        print(f"\n  {G}SELF-TEST PASSED{O}. Review the code, then: "
              f"python propose.py approve {name}")
    else:
        print(f"\n  {R}SELF-TEST FAILED{O} (exit {r.returncode}). Not "
              f"eligible for approval.")
    return 0 if passed else 1


def cmd_approve(name: str) -> int:
    """Merge into the live system -- only if tested and passing, and only if
    it does not touch a protected file."""
    f = PROPOSALS / f"{name}.py"
    if not f.exists():
        print(f"  no proposal '{name}'")
        return 1
    log = _log_read()
    entry = log.get(name, {})
    if not entry.get("test_passed"):
        print(f"  {R}refused{O}: '{name}' has not passed its self-test. "
              f"Run:\n    python propose.py test {name}")
        return 1

    target_name = name.replace("prop_", "") + ".py"
    if target_name in PROTECTED or f"{name}.py" in PROTECTED:
        print(f"  {R}refused{O}: '{target_name}' is a PROTECTED verification "
              f"file.\n  The self-improvement path may add tools, never "
              f"weaken the checks.\n  If you truly must change it, edit it "
              f"by hand, deliberately.")
        return 1

    live = HOME / target_name
    if live.exists():
        backup = HOME / f"{target_name}.bak.{int(time.time())}"
        shutil.copy(live, backup)
        print(f"  backed up existing {target_name} -> {backup.name}")
    shutil.copy(f, live)
    entry["status"] = "approved"
    entry["approved_at"] = time.time()
    _log_write(log)
    print(f"  {G}APPROVED{O}: {target_name} is now live.")
    print(f"  run the benchmark to confirm nothing regressed:")
    print(f"    python benchmark.py")
    return 0


def cmd_reject(name: str) -> int:
    f = PROPOSALS / f"{name}.py"
    if f.exists():
        f.rename(PROPOSALS / f"{name}.rejected")
    log = _log_read()
    if name in log:
        log[name]["status"] = "rejected"
        _log_write(log)
    print(f"  rejected {name}")
    return 0


def cmd_list() -> int:
    log = _log_read()
    if not log:
        print("  no proposals yet")
        return 0
    print(f"\n  PROPOSALS ({PROPOSALS})\n")
    for name, e in sorted(log.items(), key=lambda kv: -kv[1]["created"]):
        tested = ("passed" if e.get("test_passed") else
                  "FAILED" if e.get("tested") else "untested")
        col = (G if e.get("test_passed") else
               R if e.get("tested") else Y)
        print(f"  {col}{e['status']:9s}{O} {name:34s} [{tested}]")
        print(f"            {e['description'][:56]}")
    print()
    return 0


def cmd_show(name: str) -> int:
    f = PROPOSALS / f"{name}.py"
    if not f.exists():
        print(f"  no proposal '{name}'")
        return 1
    print(f"\n  ---- {name}.py ----\n")
    print(f.read_text())
    return 0


def main() -> int:
    a = sys.argv[1:]
    if not a or a[0] == "--help":
        print(__doc__.split("Run:")[-1])
        return 0
    cmd = a[0]
    if cmd == "new" and len(a) > 1:
        return cmd_new(" ".join(a[1:]))
    if cmd == "test" and len(a) > 1:
        return cmd_test(a[1])
    if cmd == "approve" and len(a) > 1:
        return cmd_approve(a[1])
    if cmd == "reject" and len(a) > 1:
        return cmd_reject(a[1])
    if cmd == "show" and len(a) > 1:
        return cmd_show(a[1])
    if cmd == "list":
        return cmd_list()
    print("commands: new <desc> | list | test <name> | show <name> | "
          "approve <name> | reject <name>")
    return 1


if __name__ == "__main__":
    sys.exit(main())
