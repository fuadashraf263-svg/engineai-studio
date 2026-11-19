#!/usr/bin/env python3
"""
wire_chat.py -- install the chat interface, the materials library and the
buckling domain.

Run:  conda activate eng && cd ~/engineai && python wire_chat.py
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


PARSER_OLD = '''    sw = sub.add_parser("sweep")'''
PARSER_NEW = '''    ch = sub.add_parser("chat")
    ch.add_argument("text", nargs="+", help="what you want, in plain words")
    ch.add_argument("--run", action="store_true",
                    help="execute the drafted spec (default: draft only)")
    ch.add_argument("--save", default=None,
                    help="store the spec in config.json under this name")
    sub.add_parser("materials")
    sw = sub.add_parser("sweep")'''

DISPATCH_OLD = '''    if args.cmd == "sweep":'''
DISPATCH_NEW = '''    if args.cmd == "chat":
        from chat_command import cmd_chat
        return cmd_chat(cfg, " ".join(args.text), args.run, args.save)
    if args.cmd == "materials":
        import materials
        return materials.main()
    if args.cmd == "sweep":'''

# ---- buckling into the verification suite ------------------------------

SUITE_OLD = '''        ("cfd", _cfd_check, "error_vs_imposed_pct"),
    ]'''
SUITE_NEW = '''        ("cfd", _cfd_check, "error_vs_imposed_pct"),
        ("buckling", _buckling_check, "buckling_error_pct"),
    ]


def _buckling_check():
    """Linear eigenvalue buckling vs Euler P_cr = pi^2 EI/(KL)^2.
    L=200 mm so KL/r = 142: genuinely slender, where Euler is exact."""
    import buckling_tools
    return buckling_tools.buckling_tube(D_mm=8.56, wall_mm=0.6,
                                        length_mm=200.0, mesh_size=1.0)'''

REG_OLD = '''        "materials_thermo": {"backend": "pycalphad",'''
REG_NEW = '''        "buckling": {"backend": "CalculiX *BUCKLE",
                     "status": "active" if _has("ccx") else "missing ccx",
                     "tools": ["buckling_tube"]},
        "materials_thermo": {"backend": "pycalphad",'''


def main() -> int:
    print("\n" + "=" * 66)
    print("  EngineAI: chat interface + materials + buckling domain")
    print("=" * 66 + "\n")

    agent = HOME / "agent.py"
    et = HOME / "engine_tools.py"
    good = True

    print("[1] required files")
    for f in ("chat_command.py", "materials.py", "buckling_tools.py"):
        if (HOME / f).exists():
            ok(f)
        else:
            bad(f"{f} missing -- copy it from Downloads first")
            good = False

    print("\n[2] agent.py -- add chat + materials commands")
    good &= patch(agent, PARSER_OLD, PARSER_NEW, "argparse entries added",
                  marker='sub.add_parser("chat")')
    good &= patch(agent, DISPATCH_OLD, DISPATCH_NEW, "dispatch added",
                  marker="from chat_command import cmd_chat")

    print("\n[3] engine_tools -- register buckling as a verified domain")
    good &= patch(et, SUITE_OLD, SUITE_NEW, "buckling added to the suite",
                  marker="def _buckling_check")
    good &= patch(et, REG_OLD, REG_NEW, "buckling in the registry",
                  marker='"buckling": {"backend": "CalculiX *BUCKLE"')

    print("\n[4] syntax check")
    for f in (agent, et, HOME / "chat_command.py", HOME / "materials.py",
              HOME / "buckling_tools.py"):
        if not f.exists():
            continue
        try:
            ast.parse(f.read_text())
            ok(f"{f.name} parses cleanly")
        except SyntaxError as e:
            bad(f"{f.name}: {e}")
            good = False

    print("\n" + "=" * 66)
    if good:
        print("  Done. Your AI can now be TALKED TO:")
        print()
        print('      python agent.py chat "thinnest 7075 strut at 300 N"')
        print("          -> drafts a spec, lists its assumptions, STOPS")
        print('      python agent.py chat "..." --run')
        print("          -> accepts and runs it through the verified path")
        print()
        print("      python agent.py materials        # the library")
        print("      python agent.py tools            # buckling now listed")
        print("      python agent.py verify           # 10 domains attempted")
        print()
        print("  And the CFD reference is waiting:")
        print("      python cfd_reference.py --diff planarPoiseuille")
    else:
        print("  Some steps failed -- send me the output.")
    print("=" * 66 + "\n")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
