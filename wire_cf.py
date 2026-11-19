#!/usr/bin/env python3
"""
wire_cf.py -- add `agent.py counterfactual`, then put the whole project
under git so files stop being tracked by hand.

Run:  conda activate eng && cd ~/engineai && python wire_cf.py
Backs up agent.py before editing. Safe to re-run.
"""

from __future__ import annotations

import shutil
import subprocess
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
    shutil.copy(path, path.with_suffix(f".py.bak.{int(time.time())}"))
    path.write_text(src.replace(old, new, 1))
    ok(label)
    return True


PARSER_OLD = '''    mp = sub.add_parser("mission")'''
PARSER_NEW = '''    cfp = sub.add_parser("counterfactual")
    cfp.add_argument("--limit", type=float, default=None,
                     help="hypothetical constraint value (default: 0.8x)")
    cfp.add_argument("--no-verify", action="store_true",
                     help="compute the claim but do NOT settle it (rung "
                          "stays at 2 -- an unsettled claim is not knowledge)")
    mp = sub.add_parser("mission")'''

DISPATCH_OLD = '''    if args.cmd == "mesh-study":'''
DISPATCH_NEW = '''    if args.cmd == "counterfactual":
        from cf_command import cmd_counterfactual
        return cmd_counterfactual(cfg, args.limit, not args.no_verify)
    if args.cmd == "mesh-study":'''

GITIGNORE = """# solver scratch -- regenerated on every run, never commit
work/
scene/*.vtu
scene/*.frd
scene/*.dat
__pycache__/
*.pyc

# our own backups
*.bak
*.bak.*

# secrets -- an API key in git history is a key you must revoke
.env
*.key
"""


def main() -> int:
    print("\n" + "=" * 62)
    print("  EngineAI: counterfactual command + version control")
    print("=" * 62 + "\n")

    agent = HOME / "agent.py"
    good = True

    print("[1] agent.py -- add the counterfactual subcommand")
    good &= patch(agent, PARSER_OLD, PARSER_NEW, "argparse entry added",
                  marker='sub.add_parser("counterfactual")')
    good &= patch(agent, DISPATCH_OLD, DISPATCH_NEW, "dispatch added",
                  marker="from cf_command import cmd_counterfactual")

    print("\n[2] cf_command.py present?")
    if (HOME / "cf_command.py").exists():
        ok("cf_command.py found")
    else:
        bad("cf_command.py missing -- copy it from Downloads first")
        good = False

    print("\n[3] syntax check")
    import ast
    for f in (agent, HOME / "cf_command.py"):
        if not f.exists():
            continue
        try:
            ast.parse(f.read_text())
            ok(f"{f.name} parses cleanly")
        except SyntaxError as e:
            bad(f"{f.name}: {e}")
            good = False

    # ------------------------------------------------------------ git ----
    print("\n[4] version control")
    gi = HOME / ".gitignore"
    if not gi.exists():
        gi.write_text(GITIGNORE)
        ok("wrote .gitignore (excludes work/, *.bak, secrets)")
    else:
        note(".gitignore already present")

    if (HOME / ".git").exists():
        note("git repo already initialised")
    else:
        try:
            subprocess.run(["git", "init", "-q"], cwd=HOME, check=True)
            subprocess.run(["git", "add", "-A"], cwd=HOME, check=True)
            r = subprocess.run(
                ["git", "-c", "user.email=engineai@localhost",
                 "-c", "user.name=EngineAI",
                 "commit", "-q", "-m",
                 "EngineAI v3: 13 domains registered, CFD+EM wired, "
                 "counterfactual command"],
                cwd=HOME, capture_output=True, text=True)
            if r.returncode == 0:
                ok("git repo created and first commit made")
            else:
                bad("commit failed: " + (r.stderr or r.stdout)[:200])
        except FileNotFoundError:
            note("git not installed -- run: sudo apt install -y git")
        except subprocess.CalledProcessError as e:
            bad(f"git init failed: {e}")

    print("\n" + "=" * 62)
    if good:
        print("  Done. Now:")
        print("      python cfd_tools.py            # guard fixed, real answer")
        print("      python agent.py counterfactual # earn Pearl rung 3")
        print("      python agent.py agi            # see it on the scorecard")
        print()
        print("  From now on, before any risky change:")
        print("      git add -A && git commit -m 'what I am about to try'")
        print("  and to undo it:  git checkout -- .")
    else:
        print("  Some steps failed -- send me the output.")
    print("=" * 62 + "\n")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
