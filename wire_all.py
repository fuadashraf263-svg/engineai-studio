#!/usr/bin/env python3
"""
wire_all.py -- the FINAL wiring. Every capability behind one command.

After this, your AI is driven entirely through `python agent.py <cmd>`:

    agent.py ask "..."        the offline-first answer router (LIBRARY /
                              HANDBOOK / LOOKUP / SOLVED / UNVERIFIED)
    agent.py math "..."       SymPy, round-trip checked
    agent.py build ...        circuit solver / MATLAB / (CAD later)
    agent.py exam             the accuracy + honesty scoreboard
    agent.py library ...      add / search / stats over your books
    agent.py chat "..."       draft a solver mission from words
    agent.py mission ...      run a verified optimisation
    ...and the existing tools/verify/agi/counterfactual/sweep/repeat

WHAT "OFFLINE" MEANS HERE
-------------------------
`ask`, `math`, `build`, `library`, `exam` and every solver run with NO
internet and NO model. Only the `--brain` fallback in `ask`, and `chat`,
call Fable 5. So the machine answers a large fraction of real questions
fully offline; the brain is the labelled last resort, not the core.

Run:  conda activate eng && cd ~/engineai && python wire_all.py
"""

from __future__ import annotations

import ast
import shutil
import sys
import time
from pathlib import Path

HOME = Path.home() / "engineai"
G, R, Y, O = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def ok(m): print(f"{G}  PASS{O}  {m}")
def bad(m): print(f"{R}  FAIL{O}  {m}")
def note(m): print(f"{Y}  ..  {O}  {m}")


def patch(path, old, new, label, marker):
    if not path.exists():
        bad(f"{path.name} missing"); return False
    s = path.read_text()
    if marker in s:
        note(f"{label} -- already applied"); return True
    if old not in s:
        bad(f"{label} -- anchor not found"); return False
    shutil.copy(path, Path(str(path) + f".bak.{int(time.time())}"))
    path.write_text(s.replace(old, new, 1))
    ok(label); return True


PARSER_OLD = '''    sub.add_parser("tools")'''
PARSER_NEW = '''    ak = sub.add_parser("ask")
    ak.add_argument("q", nargs="+")
    ak.add_argument("--brain", action="store_true",
                    help="allow a labelled UNVERIFIED model answer")
    mth = sub.add_parser("math")
    mth.add_argument("op", choices=["solve", "integrate", "diff", "ode"])
    mth.add_argument("expr")
    mth.add_argument("var", nargs="?", default="x")
    bd = sub.add_parser("build")
    bd.add_argument("kind", choices=["circuit", "matlab"])
    bd.add_argument("arg", nargs="+")
    sub.add_parser("exam")
    lb = sub.add_parser("library")
    lb.add_argument("libcmd", choices=["add", "search", "stats"])
    lb.add_argument("libarg", nargs="*")
    sub.add_parser("tools")'''

DISPATCH_OLD = '''    if args.cmd == "tools":'''
DISPATCH_NEW = '''    if args.cmd == "ask":
        from ask_command import cmd_ask
        return cmd_ask(cfg, " ".join(args.q), args.brain)
    if args.cmd == "math":
        import mathsolver
        fn = {"solve": mathsolver.solve_polynomial,
              "integrate": mathsolver.integrate,
              "diff": mathsolver.differentiate,
              "ode": lambda e, v: mathsolver.solve_ode(e)}[args.op]
        import json as _j
        print(_j.dumps(fn(args.expr, args.var) if args.op != "ode"
                       else fn(args.expr, args.var), indent=2, default=str))
        return 0
    if args.cmd == "build":
        import builder, json as _j
        if args.kind == "circuit":
            spec = _j.loads(Path(args.arg[0]).read_text())
            r = builder.build_circuit(spec, Path("circuit.cir"))
            print(r["netlist"])
            print("\\n  node voltages:", r["solution"]["node_voltages"])
        else:
            r = builder.build_matlab(" ".join(args.arg))
            print(r["code"]); print("  provenance:", r["provenance"])
            if r.get("ran"):
                print("  output:", r.get("stdout", "")[:300])
        return 0
    if args.cmd == "exam":
        import exam
        return exam.main()
    if args.cmd == "library":
        import library, sys as _s
        _s.argv = ["library", args.libcmd] + args.libarg
        return library.main()
    if args.cmd == "tools":'''


def main() -> int:
    print("\n" + "=" * 66)
    print("  EngineAI: final wiring -- one command surface")
    print("=" * 66 + "\n")

    agent = HOME / "agent.py"
    good = True

    print("[1] required modules present")
    needed = ["ask_command.py", "mathsolver.py", "builder.py", "exam.py",
              "library.py", "handbook.py", "materials.py"]
    for f in needed:
        if (HOME / f).exists():
            ok(f)
        else:
            bad(f"{f} missing -- copy it from Downloads first")
            good = False

    print("\n[2] wire commands into agent.py")
    good &= patch(agent, PARSER_OLD, PARSER_NEW, "argparse entries added",
                  marker='sub.add_parser("ask")')
    good &= patch(agent, DISPATCH_OLD, DISPATCH_NEW, "dispatch added",
                  marker='if args.cmd == "ask":')

    print("\n[3] every module carries its own self-test")
    for f, flag in [("handbook.py", "--selftest"),
                    ("mathsolver.py", "--selftest"),
                    ("builder.py", "--selftest"),
                    ("library.py", "--selftest"),
                    ("exam.py", None)]:
        p = HOME / f
        if not p.exists():
            continue
        has = "--selftest" in p.read_text() or f == "exam.py"
        (ok if has else bad)(f"{f} {'has --selftest' if has else 'LACKS a self-test'}")
        good &= has

    print("\n[4] syntax check")
    for f in needed + ["agent.py"]:
        p = HOME / f
        if not p.exists():
            continue
        try:
            ast.parse(p.read_text()); ok(f"{f} parses")
        except SyntaxError as e:
            bad(f"{f}: {e}"); good = False

    print("\n" + "=" * 66)
    if good:
        print("  Done. Your AI, one surface, offline-first:")
        print()
        print('      python agent.py ask "Reynolds number water 2 m/s 25mm pipe"')
        print('      python agent.py math solve "3*x**2 - 5*x + 2"')
        print('      python agent.py build matlab "cos table 0 to 10"')
        print("      python agent.py exam            # the scoreboard")
        print("      python agent.py library stats   # your books")
        print()
        print("  Prove it all still works:")
        print("      for m in handbook mathsolver builder library; do \\")
        print("          python $m.py --selftest; done")
        print("      python agent.py exam")
    else:
        print("  Some steps failed -- send me the output.")
    print("=" * 66 + "\n")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
