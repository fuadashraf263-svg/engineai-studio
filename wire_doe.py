#!/usr/bin/env python3
"""
wire_doe.py -- add `agent.py sweep` and `agent.py repeat`, and fix the
PyChrono registry probe.

CORRECTION: I previously told you to run `agent.py mission --dry-run` a few
times to improve the world model's elasticities. That advice was wrong.
The DryRunPlanner pins secondary parameters at their MINIMUM, so every run
added another row at wall=0.6. Four solves, one distinct wall value -- the
system stays rank-deficient in wall no matter how many rows you add.
`sweep` varies each parameter deliberately. That is what was needed.

PyChrono: registry probes `_py("pychrono")` from inside the `eng` env, but
PyChrono lives in the separate `chrono` env on Python 3.13. It will read
"not installed" forever. Probe it in its own env, the same way CFD probes
OpenFOAM in its own subshell.

Run:  conda activate eng && cd ~/engineai && python wire_doe.py
"""

from __future__ import annotations

import ast
import shutil
import sys
import time
from pathlib import Path

HOME = Path.home() / "engineai"
G, R, Y, O = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def ok(m):
    print(f"{G}  PASS{O}  {m}")


def bad(m):
    print(f"{R}  FAIL{O}  {m}")


def note(m):
    print(f"{Y}  ..  {O}  {m}")


def patch(path: Path, old: str, new: str, label: str, marker: str) -> bool:
    if not path.exists():
        bad(f"{path.name} not found")
        return False
    src = path.read_text()
    if marker in src:
        note(f"{label} -- already applied")
        return True
    if old not in src:
        bad(f"{label} -- anchor not found")
        return False
    shutil.copy(path, Path(str(path) + f".bak.{int(time.time())}"))
    path.write_text(src.replace(old, new, 1))
    ok(label)
    return True


PARSER_OLD = '''    cfp = sub.add_parser("counterfactual")'''
PARSER_NEW = '''    sw = sub.add_parser("sweep")
    sw.add_argument("name", nargs="?", default=None)
    sw.add_argument("--points", type=int, default=4,
                    help="geometric points per parameter (default 4)")
    sw.add_argument("--plan-only", action="store_true",
                    help="print the design without solving")
    rp = sub.add_parser("repeat")
    rp.add_argument("name", nargs="?", default=None)
    rp.add_argument("--times", type=int, default=3)
    cfp = sub.add_parser("counterfactual")'''

DISPATCH_OLD = '''    if args.cmd == "counterfactual":'''
DISPATCH_NEW = '''    if args.cmd == "sweep":
        from doe_tools import cmd_sweep
        return cmd_sweep(cfg, args.name, args.points, args.plan_only)
    if args.cmd == "repeat":
        from doe_tools import cmd_repeat
        return cmd_repeat(cfg, args.name, args.times)
    if args.cmd == "counterfactual":'''

CHRONO_OLD = '''        "multibody": {"backend": "pychrono", "status": _py("pychrono")},'''
CHRONO_NEW = '''        "multibody": {"backend": "pychrono (conda env 'chrono')",
                      "status": _chrono_status()},'''

CHRONO_FN = '''def _chrono_status():
    """PyChrono lives in the separate 'chrono' env on Python 3.13 -- it can
    never be imported from 'eng'. Probe it where it actually is."""
    import subprocess
    try:
        base = subprocess.run(["conda", "info", "--base"],
                              capture_output=True, text=True,
                              timeout=60).stdout.strip()
        if not base:
            return "conda not found"
        r = subprocess.run(
            ["bash", "-c",
             f"source {base}/etc/profile.d/conda.sh >/dev/null 2>&1 && "
             "conda activate chrono >/dev/null 2>&1 && "
             "python -c 'import pychrono,sys; "
             "print(sys.version.split()[0])'"],
            capture_output=True, text=True, timeout=180)
        if r.returncode == 0 and r.stdout.strip():
            return (f"active (conda env 'chrono', Python "
                    f"{r.stdout.strip()}, isolated subshell)")
    except Exception:
        pass
    return "not installed -- install_domains.sh sec 6"


def _py(mod):'''


def main() -> int:
    print("\n" + "=" * 64)
    print("  EngineAI: design of experiments + repeatability + chrono")
    print("=" * 64 + "\n")

    agent = HOME / "agent.py"
    et = HOME / "engine_tools.py"
    doe = HOME / "doe_tools.py"
    good = True

    print("[1] doe_tools.py present?")
    if doe.exists():
        ok("doe_tools.py found")
    else:
        bad("doe_tools.py missing -- copy it from Downloads first")
        good = False

    print("\n[2] agent.py -- add sweep + repeat")
    good &= patch(agent, PARSER_OLD, PARSER_NEW, "argparse entries added",
                  marker='sub.add_parser("sweep")')
    good &= patch(agent, DISPATCH_OLD, DISPATCH_NEW, "dispatch added",
                  marker="from doe_tools import cmd_sweep")

    print("\n[3] engine_tools -- probe PyChrono in its own env")
    good &= patch(et, CHRONO_OLD, CHRONO_NEW, "multibody entry corrected",
                  marker="conda env 'chrono'")
    good &= patch(et, "def _py(mod):", CHRONO_FN,
                  "_chrono_status() helper added",
                  marker="def _chrono_status")

    print("\n[4] syntax check")
    for f in (agent, et, doe):
        if not f.exists():
            continue
        try:
            ast.parse(f.read_text())
            ok(f"{f.name} parses cleanly")
        except SyntaxError as e:
            bad(f"{f.name}: {e}  -- restore the .bak")
            good = False

    print("\n" + "=" * 64)
    if good:
        print("  Done. The sequence that fixes the world model:")
        print()
        print("      python agent.py sweep --plan-only   # see the design")
        print("      python agent.py sweep               # ~32 solves, $0.00")
        print("      python agent.py counterfactual      # real elasticities")
        print("      python agent.py repeat              # determinism proof")
        print("      python agent.py agi                 # full scorecard")
        print()
        print("  A sweep is ~10s per solve. Start it and leave it.")
    else:
        print("  Some steps failed -- send me the output.")
    print("=" * 64 + "\n")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
