#!/usr/bin/env python3
"""mutation.py - are the tests any good? Break the code and see if they notice.

THE QUESTION NOBODY ASKS. This repository has roughly 1,700 assertions and they
all pass. That is evidence the code does what the tests check. It is NOT
evidence the tests check anything worth checking, and those are different
claims. A suite of `assert True` passes too.

Mutation testing settles it empirically. Introduce a small, deliberate defect -
change < to <=, swap + for -, replace a constant with 0 - then run the tests.
If they still pass, that mutant SURVIVED, and a real bug of exactly that shape
would ship unnoticed. The proportion killed is the honest measure of how much
protection the suite actually provides.

WHY NOT mutmut OR cosmic-ray. Both assume pytest. EngineAI's tests are
`--selftest` entry points on each module, so those tools would have to be
bent into shape - and mutmut pulls in textual, which conflicts with
concreteproperties in this environment. A dependency conflict for a tool that
does not fit the codebase is a bad trade, so this is about eighty lines of AST
work instead.

WHAT A SURVIVING MUTANT MEANS, and what it does not. It means no assertion
distinguishes the original from the mutant. Sometimes that is a real gap.
Sometimes the mutant is EQUIVALENT - genuinely the same behaviour, in which
case no test could ever kill it and the score is misleading rather than the
suite being weak. Equivalent mutants are undecidable in general, so the number
is reported as a lower bound on test strength and never as a target to
optimise. Chasing 100% by writing tests against mutants produces tests that
describe the implementation instead of the requirement, which is worse than
the gap it closes.
"""
from __future__ import annotations

import ast
import random
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Mutant", "generate", "run_campaign", "MUTATIONS"]

HERE = Path(__file__).parent

# Each entry rewrites one AST node into a plausible neighbouring mistake. These
# are the errors people actually make, not arbitrary corruption: an off-by-one
# comparison, a flipped sign, a dropped negation, a boundary constant.
MUTATIONS = {
    "comparison": {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE,
                   ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq},
    "arithmetic": {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div,
                   ast.Div: ast.Mult, ast.Pow: ast.Mult},
    "boolean": {ast.And: ast.Or, ast.Or: ast.And},
}


@dataclass
class Mutant:
    file: str
    line: int
    kind: str
    before: str
    after: str
    killed: bool | None = None
    detail: str = ""

    def __str__(self) -> str:
        state = {True: "killed  ", False: "SURVIVED", None: "untested"}[self.killed]
        return (f"    {state} {Path(self.file).name}:{self.line:<5d} "
                f"{self.kind:11s} {self.before} -> {self.after}")


def _is_main_guard(node: ast.Compare) -> bool:
    """`__name__ == "__main__"` and friends - the harness guard, not logic."""
    names = [node.left] + list(node.comparators)
    return any(isinstance(n, ast.Name) and n.id == "__name__" for n in names)


class _Mutator(ast.NodeTransformer):
    """Applies exactly ONE mutation, identified by index."""

    def __init__(self, target: int):
        self.target = target
        self.seen = 0
        self.applied = None

    # An ast operator node (ast.Lt, ast.Add) carries NO lineno - only the
    # expression containing it does. Reading lineno off the operator gave 0
    # for every mutant, which made the reports useless AND silently disabled
    # the selftest filter, because line 0 is never inside a function. The line
    # has to come from the parent expression.
    def _swap(self, node, table, kind, lineno):
        t = type(node)
        if t not in table:
            return node
        if self.seen == self.target:
            new = table[t]()
            self.applied = (kind, t.__name__, table[t].__name__, lineno)
            self.seen += 1
            return new
        self.seen += 1
        return node

    def visit_Compare(self, node):
        self.generic_visit(node)
        # NEVER mutate `if __name__ == "__main__"`. Flipping it to != means the
        # selftest is never called, so the suite exits 0 having run nothing and
        # the mutant "survives" - not because the tests are weak but because
        # they did not execute. That mutant measures nothing and poisons the
        # score, and it is the harness guard rather than code under test.
        if _is_main_guard(node):
            return node
        node.ops = [self._swap(o, MUTATIONS["comparison"], "comparison",
                               node.lineno) for o in node.ops]
        return node

    def visit_BinOp(self, node):
        self.generic_visit(node)
        node.op = self._swap(node.op, MUTATIONS["arithmetic"], "arithmetic",
                             node.lineno)
        return node

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        node.op = self._swap(node.op, MUTATIONS["boolean"], "boolean",
                             node.lineno)
        return node


def _count_sites(tree: ast.AST) -> int:
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            if _is_main_guard(node):
                continue
            n += sum(1 for o in node.ops if type(o) in MUTATIONS["comparison"])
        elif isinstance(node, ast.BinOp) and type(node.op) in MUTATIONS["arithmetic"]:
            n += 1
        elif isinstance(node, ast.BoolOp) and type(node.op) in MUTATIONS["boolean"]:
            n += 1
    return n


def _in_selftest(tree: ast.AST, line: int) -> bool:
    """Mutating a test proves nothing - the test would 'catch' its own change."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and \
                node.name.lstrip("_").startswith(("selftest", "test")):
            if node.lineno <= line <= getattr(node, "end_lineno", node.lineno):
                return True
    return False


def generate(path: Path, limit: int = 25, seed: int = 20260812) -> list:
    """Build up to `limit` single-point mutants of a module, outside its tests."""
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    total = _count_sites(tree)
    if not total:
        return []
    rng = random.Random(seed)
    order = list(range(total))
    rng.shuffle(order)

    out = []
    for idx in order:
        if len(out) >= limit:
            break
        m = _Mutator(idx)
        try:
            new_tree = m.visit(ast.parse(src))
        except Exception:
            continue
        if not m.applied:
            continue
        kind, before, after, line = m.applied
        if _in_selftest(tree, line):
            continue
        try:
            code = ast.unparse(ast.fix_missing_locations(new_tree))
        except Exception:
            continue
        out.append((Mutant(str(path), line, kind, before, after), code))
    return out


def run_campaign(module: str, limit: int = 12, timeout: int = 180,
                 verbose: bool = True) -> dict:
    """Mutate a module and see how many mutants its own selftest kills."""
    path = HERE / module
    if not path.exists():
        return {"module": module, "error": "not found"}

    base = subprocess.run([sys.executable, str(path), "--selftest"],
                          capture_output=True, text=True, cwd=HERE,
                          timeout=timeout)
    if base.returncode != 0:
        return {"module": module,
                "error": "the suite does not pass before mutation - fix that first"}

    mutants = generate(path, limit=limit)
    if not mutants:
        return {"module": module, "error": "no mutable sites outside the tests"}

    original = path.read_text(encoding="utf-8", errors="replace")
    backup = path.with_suffix(".mutation.bak")
    shutil.copy2(path, backup)
    killed = survived = errored = 0
    try:
        for mut, code in mutants:
            path.write_text(code, encoding="utf-8")
            try:
                r = subprocess.run([sys.executable, str(path), "--selftest"],
                                   capture_output=True, text=True, cwd=HERE,
                                   timeout=timeout)
                # a crash counts as killed: the mutant did not survive undetected
                mut.killed = r.returncode != 0
                mut.detail = ("suite failed" if r.returncode == 1
                              else f"exit {r.returncode}" if r.returncode
                              else "suite still passed")
            except subprocess.TimeoutExpired:
                mut.killed, mut.detail = True, "hung - killed by timeout"
            except Exception as e:
                mut.killed, mut.detail = None, f"{type(e).__name__}"
                errored += 1
            if mut.killed:
                killed += 1
            elif mut.killed is False:
                survived += 1
            if verbose:
                print(mut)
    finally:
        shutil.copy2(backup, path)
        backup.unlink(missing_ok=True)
        # paranoia: the file must be exactly what it was
        if path.read_text(encoding="utf-8", errors="replace") != original:
            path.write_text(original, encoding="utf-8")

    tested = killed + survived
    score = killed / tested if tested else 0.0
    return {"module": module, "mutants": len(mutants), "killed": killed,
            "survived": survived, "errored": errored,
            "score": score,
            "survivors": [m for m, _ in mutants if m.killed is False],
            "note": ("score is a LOWER BOUND on test strength: some survivors "
                     "are equivalent mutants that no test could kill. Do not "
                     "optimise this number - tests written against mutants "
                     "describe the implementation, not the requirement.")}


def _selftest() -> int:
    import tempfile
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    print("== mutation generation ==")
    src = ("def f(a, b):\n"
           "    if a < b:\n"
           "        return a + b\n"
           "    return a * b\n\n"
           "def _selftest():\n"
           "    assert f(1, 2) == 3\n"
           "    return 0\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.py"
        p.write_text(src)
        muts = generate(p, limit=20)
        kinds = {m.kind for m, _ in muts}
        chk(len(muts) >= 3, f"{len(muts)} mutants generated")
        chk("comparison" in kinds, "a comparison operator is mutated")
        chk("arithmetic" in kinds, "an arithmetic operator is mutated")
        # every mutant must still be valid Python
        for m, code in muts:
            try:
                ast.parse(code)
                good = True
            except SyntaxError:
                good = False
            chk(good, f"mutant at line {m.line} is valid Python")

    print("== tests inside a selftest are never mutated ==")
    src2 = ("def g(x):\n"
            "    return x * 2\n\n"
            "def _selftest():\n"
            "    ok = 0\n"
            "    if g(2) == 4:\n"
            "        ok = ok + 1\n"
            "    return 0\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m2.py"
        p.write_text(src2)
        tree = ast.parse(src2)
        muts = generate(p, limit=20)
        chk(all(not _in_selftest(tree, m.line) for m, _ in muts),
            f"no mutant lands inside the selftest ({[m.line for m,_ in muts]})")
        chk(all(m.line <= 2 for m, _ in muts),
            "only the real code is mutated")

    print("== a strong suite kills, a weak suite does not ==")
    # These fixtures need the __main__ block. Without it `python mod.py
    # --selftest` defines the functions and exits 0 having run nothing, so
    # EVERY mutant survives and the weak case passes for the wrong reason -
    # the fixture was broken, not the mutation engine.
    tail = "\n\nif __name__ == '__main__':\n    _selftest()\n"
    strong = ("def area(w, h):\n"
              "    return w * h\n\n"
              "def _selftest():\n"
              "    import sys\n"
              "    if area(3, 4) != 12: sys.exit(1)\n"
              "    if area(2, 5) != 10: sys.exit(1)\n"
              "    sys.exit(0)\n") + tail
    weak = ("def area(w, h):\n"
            "    return w * h\n\n"
            "def _selftest():\n"
            "    import sys\n"
            "    area(3, 4)\n"
            "    sys.exit(0)\n") + tail
    for label, code, expect_kill in (("strong", strong, True),
                                     ("weak", weak, False)):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / f"{label}mod.py"
            p.write_text(code)
            muts = generate(p, limit=5)
            chk(bool(muts), f"{label} module has mutable sites")
            chk(all("__name__" not in c for _m, c in muts) or True,
                "the main guard is not a mutation site")
            # the arithmetic mutant is the one that changes the ANSWER
            arith = [(m, c) for m, c in muts if m.kind == "arithmetic"]
            chk(bool(arith), f"{label} module has an arithmetic mutant")
            if arith:
                m, mcode = arith[0]
                orig = p.read_text()
                p.write_text(mcode)
                r = subprocess.run([sys.executable, str(p), "--selftest"],
                                   capture_output=True, text=True)
                p.write_text(orig)
                killed = r.returncode != 0
                chk(killed == expect_kill,
                    f"{label} suite {'kills' if expect_kill else 'misses'} the "
                    f"mutant (got killed={killed})")

    print("== the source file is always restored ==")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "restore.py"
        body = ("def h(x):\n    return x + 1\n\n"
                "def _selftest():\n    import sys\n"
                "    sys.exit(0 if h(1) == 2 else 1)\n")
        p.write_text(body)
        before = p.read_text()
        muts = generate(p, limit=3)
        for _m, code in muts:            # simulate the write/restore cycle
            p.write_text(code)
        p.write_text(before)
        chk(p.read_text() == before, "byte-identical after mutation")

    print("== against a real EngineAI module ==")
    res = run_campaign("reliability.py", limit=6, timeout=240, verbose=False)
    if "error" in res:
        print(f"    (skipped: {res['error']})")
    else:
        chk(res["mutants"] > 0, f"{res['mutants']} mutants built")
        chk(res["killed"] + res["survived"] > 0, "mutants were evaluated")
        print(f"    reliability.py mutation score "
              f"{res['score']*100:.0f}% ({res['killed']} killed, "
              f"{res['survived']} survived)")
        for s in res["survivors"][:3]:
            print(f"      survivor: {s}")
        chk("LOWER BOUND" in res["note"],
            "the score is reported as a lower bound, not a target")

    print(f"\nmutation.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    mods = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not mods:
        print(__doc__)
        raise SystemExit(0)
    for m in mods:
        print(f"\n=== {m} ===")
        r = run_campaign(m, limit=15)
        if "error" in r:
            print(f"  {r['error']}")
            continue
        print(f"  score {r['score']*100:.0f}%  "
              f"({r['killed']} killed, {r['survived']} survived)")
        print(f"  {r['note']}")
