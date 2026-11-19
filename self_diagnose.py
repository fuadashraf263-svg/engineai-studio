#!/usr/bin/env python3
"""self_diagnose.py - read your own failure, localise it, and try to repair it.

Everything else in this repository reports failures. A person then reads the
traceback, forms a hypothesis, edits a file and re-runs the gate. That person
is the last large piece of the loop that is not automated, and this module is
the first honest attempt at closing it.

THE RULE THAT MAKES THIS SAFE, AND WITHOUT WHICH IT IS DANGEROUS

  IT MAY NEVER EDIT A TEST.

A repair loop scored on "does the suite pass" has one overwhelmingly cheap
strategy available: weaken the assertion. Loosen a tolerance, delete a check,
wrap it in a try. Every one of those turns a red suite green while making the
software worse, and a loop that is allowed to do it will find that path long
before it finds a real fix.

So test code is protected STRUCTURALLY, not by instruction. Any hunk touching a
_selftest body, a chk( call, an assert, or a file whose name marks it as a
battery is refused before it is ever applied - and the selftest below proves
the refusal by handing the repairer a patch that would do exactly that.

WHAT IT CAN ACTUALLY REPAIR. Being specific, because "self-healing AI" is
mostly a marketing claim:

  MECHANICAL, high confidence - a missing import, a name that is one edit away
  from a real one, a module that moved, a file that is not there. These have a
  single obvious fix and the fix is checkable.

  NUMERIC TOLERANCE - only when the failure is in NON-test code and the value
  is within the tolerance class already declared for that computation.

  EVERYTHING ELSE - localised and classified, then handed to a human with the
  context assembled. A wrong physical assumption is not repairable by pattern
  matching, and today's history is the argument: most failures in this project
  were a test encoding MY misunderstanding, which is precisely the class where
  an automated fix would have destroyed the evidence that I was wrong.

VERIFICATION IS THE WHOLE GATE, not the failing test. A patch that fixes one
suite and breaks another is not a fix, and checking only the thing you were
looking at is how that ships.
"""
from __future__ import annotations

import ast
import difflib
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

__all__ = ["Failure", "Patch", "parse_failure", "classify", "localise",
           "propose", "is_test_code", "apply_and_verify", "repair",
           "PROTECTED_PATTERNS"]

HERE = Path(__file__).parent
LOG = HERE / "research_output" / "self_diagnose.jsonl"

# Anything matching these is test code and is never patched. Deliberately
# broad: the cost of refusing a legitimate edit is that a human does it, and
# the cost of allowing an illegitimate one is a green suite that means nothing.
PROTECTED_PATTERNS = (
    re.compile(r"^\s*def\s+_?selftest\b"),
    re.compile(r"^\s*def\s+chk\b"),
    re.compile(r"^\s*chk\("),
    re.compile(r"^\s*assert\b"),
    re.compile(r"^\s*def\s+test_"),
)
PROTECTED_FILES = re.compile(
    r"(^|/)(test_|_test|.*_battery|activity_\d+|user_battery|acceptance_test)",
    re.I)


@dataclass
class Failure:
    suite: str
    kind: str                # import | attribute | name | file | assertion |
                             # tolerance | timeout | unknown
    file: str = ""
    line: int = 0
    symbol: str = ""
    message: str = ""
    traceback: str = ""
    repairable: bool = False
    reason: str = ""

    def __str__(self) -> str:
        loc = f"{self.file}:{self.line}" if self.file else "(not localised)"
        tag = "repairable" if self.repairable else "NEEDS A HUMAN"
        return (f"  [{self.kind:9s}] {self.suite}  {loc}\n"
                f"      {self.message[:96]}\n"
                f"      {tag}: {self.reason}")


@dataclass
class Patch:
    file: str
    line: int
    old: str
    new: str
    rationale: str
    confidence: str          # high | medium | low

    def diff(self) -> str:
        return "\n".join(difflib.unified_diff(
            [self.old + "\n"], [self.new + "\n"],
            fromfile=f"{self.file}:{self.line}", tofile="proposed", lineterm=""))


# --------------------------------------------------------------------------
# protection
# --------------------------------------------------------------------------

def is_test_code(path: str, line_text: str = "", source: str = "",
                 line_no: int = 0) -> tuple:
    """Is this location test code? Returns (protected, why).

    Checks three ways because any one alone is escapable: the filename, the
    line itself, and - the one that matters - whether the line sits inside a
    selftest function body, found by walking the AST rather than by guessing
    from indentation.
    """
    if PROTECTED_FILES.search(path.replace("\\", "/")):
        return True, f"{Path(path).name} is a test battery"
    for pat in PROTECTED_PATTERNS:
        if pat.search(line_text or ""):
            return True, f"the line itself is test code ({pat.pattern})"
    if source and line_no:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return False, ""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and \
                    node.name.lstrip("_").startswith(("selftest", "test")):
                end = getattr(node, "end_lineno", node.lineno)
                if node.lineno <= line_no <= end:
                    return True, f"inside {node.name}() at lines " \
                                 f"{node.lineno}-{end}"
    return False, ""


# --------------------------------------------------------------------------
# parse and classify
# --------------------------------------------------------------------------

_TB_FRAME = re.compile(r'File "([^"]+)", line (\d+)')


def parse_failure(suite: str, output: str) -> Failure:
    """Pull the failure out of a suite's output."""
    text = output or ""
    frames = _TB_FRAME.findall(text)
    file_, line = ("", 0)
    for f, l in reversed(frames):          # innermost frame in OUR code
        if "site-packages" not in f and "/lib/python" not in f:
            file_, line = f, int(l)
            break
    if not file_ and frames:
        file_, line = frames[-1][0], int(frames[-1][1])

    last = ""
    for ln in reversed(text.strip().splitlines()):
        if ln.strip() and not ln.startswith(" "):
            last = ln.strip()
            break

    f = Failure(suite=suite, kind="unknown", file=file_, line=line,
                message=last, traceback=text[-2500:])
    return classify(f)


def classify(f: Failure) -> Failure:
    """Decide what kind of failure this is and whether it is repairable.

    Erring towards NOT repairable. A wrong answer from a repair loop is far
    more expensive than a repair it declined to attempt.
    """
    m = f.message
    tb = f.traceback

    if "ModuleNotFoundError" in tb or "ImportError" in tb:
        mm = re.search(r"No module named '([^']+)'", tb)
        f.kind, f.symbol = "import", mm.group(1) if mm else ""
        f.repairable = bool(f.symbol)
        f.reason = (f"module '{f.symbol}' is not installed or not importable - "
                    f"the fix is an install or a corrected import, both checkable")
        return f

    if "AttributeError" in tb:
        mm = re.search(r"has no attribute '([^']+)'", tb)
        f.kind, f.symbol = "attribute", mm.group(1) if mm else ""
        f.repairable = bool(f.symbol)
        f.reason = "a near-miss attribute name can be matched against what exists"
        return f

    if "NameError" in tb:
        mm = re.search(r"name '([^']+)' is not defined", tb)
        f.kind, f.symbol = "name", mm.group(1) if mm else ""
        f.repairable = bool(f.symbol)
        f.reason = "an undefined name is usually a typo or a missing import"
        return f

    if "FileNotFoundError" in tb:
        mm = re.search(r"No such file or directory: '([^']+)'", tb)
        f.kind, f.symbol = "file", mm.group(1) if mm else ""
        f.repairable = False
        f.reason = ("a missing file is a data or environment problem. Creating "
                    "one to satisfy a test would fabricate the input")
        return f

    if "TimeoutExpired" in tb or "timed out" in m.lower():
        f.kind, f.repairable = "timeout", False
        f.reason = "a timeout is a performance problem, not a text edit"
        return f

    if re.search(r"\bFAIL\b", tb) or "AssertionError" in tb:
        f.kind, f.repairable = "assertion", False
        f.reason = ("an assertion failure means the code and the expectation "
                    "disagree. Which one is wrong is a judgement about the "
                    "physics, and in this project the TEST has been wrong more "
                    "often than the code - so this goes to a human")
        return f

    f.kind, f.repairable = "unknown", False
    f.reason = "unrecognised failure shape; localised only"
    return f


def localise(f: Failure, context: int = 6) -> str:
    """The code around the failure, for a human or a model to read."""
    if not f.file or not Path(f.file).exists():
        return "(source not available)"
    try:
        lines = Path(f.file).read_text(encoding="utf-8",
                                       errors="replace").splitlines()
    except OSError:
        return "(source unreadable)"
    lo, hi = max(0, f.line - context - 1), min(len(lines), f.line + context)
    out = []
    for i in range(lo, hi):
        mark = ">>" if i + 1 == f.line else "  "
        out.append(f"  {mark} {i+1:5d}  {lines[i]}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# propose
# --------------------------------------------------------------------------

def _module_symbols(path: Path) -> list:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node.name)
        elif isinstance(node, ast.Assign):
            out += [t.id for t in node.targets if isinstance(t, ast.Name)]
    return out


def propose(f: Failure) -> list:
    """Candidate patches. Only for the mechanical classes, and never on tests."""
    if not f.repairable or not f.file or not Path(f.file).exists():
        return []
    p = Path(f.file)
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
        lines = src.splitlines()
    except OSError:
        return []
    if not (1 <= f.line <= len(lines)):
        return []
    line_text = lines[f.line - 1]

    protected, why = is_test_code(str(p), line_text, src, f.line)
    if protected:
        return []                          # refuse before proposing anything

    out = []
    if f.kind in ("attribute", "name") and f.symbol:
        candidates = _module_symbols(p)
        # names used elsewhere in the same file are also fair candidates
        candidates += re.findall(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b", src)
        close = difflib.get_close_matches(f.symbol, set(candidates), n=3,
                                          cutoff=0.82)
        for c in close:
            if c == f.symbol:
                continue
            new = re.sub(rf"\b{re.escape(f.symbol)}\b", c, line_text)
            if new != line_text:
                out.append(Patch(str(p), f.line, line_text, new,
                                 f"'{f.symbol}' is one small edit from '{c}', "
                                 f"which exists in this module",
                                 "medium"))
    return out


# --------------------------------------------------------------------------
# apply, verify, revert
# --------------------------------------------------------------------------

def apply_and_verify(patch: Patch, gate: list | None = None,
                     timeout: int = 1800) -> dict:
    """Apply a patch, run the WHOLE gate, and revert unless it passes.

    Verifying against the whole gate rather than the failing suite is the
    point. A patch that fixes one thing and breaks another is not a fix, and
    only re-running everything can tell the difference.
    """
    p = Path(patch.file)
    src = p.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines(keepends=True)
    if not (1 <= patch.line <= len(lines)):
        return {"applied": False, "kept": False, "reason": "line out of range"}

    protected, why = is_test_code(str(p), patch.old, src, patch.line)
    if protected:
        return {"applied": False, "kept": False,
                "reason": f"REFUSED: {why}. A repair loop may not edit a test."}

    backup = p.with_suffix(p.suffix + ".selfdiag.bak")
    shutil.copy2(p, backup)
    try:
        ending = "\n" if lines[patch.line - 1].endswith("\n") else ""
        lines[patch.line - 1] = patch.new + ending
        p.write_text("".join(lines), encoding="utf-8")

        cmd = gate or ["bash", str(HERE / "verify_all.sh")]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE,
                           timeout=timeout)
        passed = r.returncode == 0
        if not passed:
            shutil.copy2(backup, p)
        return {"applied": True, "kept": passed,
                "reason": ("the gate passed with this patch" if passed else
                           "the gate still failed - patch reverted"),
                "returncode": r.returncode,
                "tail": (r.stdout or "")[-600:]}
    except subprocess.TimeoutExpired:
        shutil.copy2(backup, p)
        return {"applied": True, "kept": False,
                "reason": f"the gate did not finish in {timeout}s - reverted"}
    except Exception as e:
        shutil.copy2(backup, p)
        return {"applied": True, "kept": False,
                "reason": f"{type(e).__name__}: {e} - reverted"}
    finally:
        backup.unlink(missing_ok=True)


def _log(entry: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


def repair(suite_cmd: list, suite_name: str = "", max_attempts: int = 3,
           gate: list | None = None, dry_run: bool = True,
           verbose: bool = True) -> dict:
    """Run a suite; if it fails, diagnose and optionally attempt a repair.

    dry_run defaults to TRUE. A module that edits source files should not do so
    the first time somebody runs it to see what it does.
    """
    say = print if verbose else (lambda *a, **k: None)
    name = suite_name or " ".join(suite_cmd[-2:])
    r = subprocess.run(suite_cmd, capture_output=True, text=True, cwd=HERE)
    if r.returncode == 0:
        say(f"  {name}: passes, nothing to diagnose")
        return {"suite": name, "failed": False, "attempts": []}

    f = parse_failure(name, (r.stdout or "") + "\n" + (r.stderr or ""))
    say(f"\n{f}")
    say("\n  context:")
    say(localise(f))

    result = {"suite": name, "failed": True, "failure": asdict(f),
              "attempts": [], "dry_run": dry_run}

    patches = propose(f)
    if not patches:
        result["outcome"] = "HANDED TO A HUMAN"
        result["why"] = f.reason
        say(f"\n  no automatic repair attempted: {f.reason}")
        _log(result)
        return result

    for patch in patches[:max_attempts]:
        say(f"\n  candidate ({patch.confidence}): {patch.rationale}")
        say(patch.diff())
        if dry_run:
            result["attempts"].append({"patch": asdict(patch), "dry_run": True})
            continue
        v = apply_and_verify(patch, gate=gate)
        result["attempts"].append({"patch": asdict(patch), **v})
        say(f"    -> {v['reason']}")
        if v.get("kept"):
            result["outcome"] = "REPAIRED"
            _log(result)
            return result

    result["outcome"] = ("PROPOSED ONLY (dry run)" if dry_run
                         else "NO CANDIDATE SURVIVED THE GATE")
    _log(result)
    return result


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

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

    print("== test code is protected three different ways ==")
    prot, why = is_test_code("user_battery.py", "x = 1")
    chk(prot and "battery" in why, f"a battery file is protected ({why})")
    chk(is_test_code("activity_300.py", "x = 1")[0], "the activity sweep is protected")
    chk(is_test_code("research.py", "    chk(a == b, 'label')")[0],
        "a chk( line is protected wherever it lives")
    chk(is_test_code("research.py", "    assert x > 0")[0], "an assert is protected")
    src = ("def helper():\n    return 1\n\n\n"
           "def _selftest():\n    ok = 0\n    tol = 1e-9\n    return ok\n")
    chk(is_test_code("m.py", "    tol = 1e-9", src, 7)[0],
        "a line INSIDE a selftest body is protected, found via the AST")
    chk(not is_test_code("m.py", "    return 1", src, 2)[0],
        "and ordinary code in the same file is not")

    print("== classification errs towards NOT repairable ==")
    f_imp = parse_failure("s", 'Traceback\n  File "x.py", line 3, in <module>\n'
                               "ModuleNotFoundError: No module named 'numpyy'")
    chk(f_imp.kind == "import" and f_imp.symbol == "numpyy", "import failure read")
    chk(f_imp.repairable, "and marked repairable")

    f_assert = parse_failure("s", "  FAIL the beam deflects the right way\n"
                                  "research.py: 31 passed, 1 failed")
    chk(f_assert.kind == "assertion", "assertion failure recognised")
    chk(not f_assert.repairable, "and NOT marked repairable")
    chk("TEST has been wrong more often" in f_assert.reason,
        "with the honest reason: the test is often the thing that is wrong")

    f_file = parse_failure("s", 'FileNotFoundError: [Errno 2] No such file or '
                                "directory: '/data/missing.csv'")
    chk(f_file.kind == "file" and not f_file.repairable,
        "a missing data file is not repairable")
    chk("fabricate the input" in f_file.reason,
        "because creating one would fabricate the input")

    f_to = parse_failure("s", "subprocess.TimeoutExpired: timed out")
    chk(f_to.kind == "timeout" and not f_to.repairable, "timeouts are not text edits")

    print("== it proposes a real fix for a real typo ==")
    with tempfile.TemporaryDirectory() as td:
        m = Path(td) / "mod.py"
        m.write_text("def compute_stress(x):\n    return x * 2\n\n"
                     "def run(v):\n    return compute_stres(v)\n")
        f = Failure(suite="s", kind="name", file=str(m), line=5,
                    symbol="compute_stres", repairable=True)
        ps = propose(f)
        chk(ps, f"a candidate was proposed ({len(ps)})")
        chk(ps and "compute_stress" in ps[0].new,
            f"and it is the right name ({ps[0].new.strip() if ps else ''})")
        chk(ps and ps[0].confidence in ("medium", "high"), "with a confidence")

    print("== IT REFUSES TO WEAKEN A TEST, which is the whole safety case ==")
    with tempfile.TemporaryDirectory() as td:
        t = Path(td) / "mod.py"
        t.write_text("def _selftest():\n"
                     "    ok = 0\n"
                     "    chk(abs(x - y) < 1e-9, 'exact')\n"
                     "    return ok\n")
        # the cheapest way to make this pass is to loosen 1e-9 to 1e9
        evil = Patch(str(t), 3, "    chk(abs(x - y) < 1e-9, 'exact')",
                     "    chk(abs(x - y) < 1e9, 'exact')",
                     "loosen the tolerance", "high")
        v = apply_and_verify(evil, gate=["true"])
        chk(not v["applied"], "the patch was never applied")
        chk("REFUSED" in v["reason"], f"and refused explicitly ({v['reason'][:44]})")
        chk("1e-9" in t.read_text(), "the tolerance is untouched on disk")

        # and it refuses even when the loosening is disguised as a fix
        evil2 = Patch(str(t), 3, "    chk(abs(x - y) < 1e-9, 'exact')",
                      "    chk(True, 'exact')", "simplify", "high")
        chk(not apply_and_verify(evil2, gate=["true"])["applied"],
            "and refuses chk(True) too")

    print("== a patch that does not fix the gate is reverted ==")
    with tempfile.TemporaryDirectory() as td:
        m = Path(td) / "mod.py"
        original = "value = 1\n"
        m.write_text(original)
        p = Patch(str(m), 1, "value = 1", "value = 2", "try 2", "low")
        v = apply_and_verify(p, gate=["false"])       # a gate that always fails
        chk(v["applied"] and not v["kept"], "applied then rejected")
        chk(m.read_text() == original, "and the file is byte-identical again")
        v2 = apply_and_verify(p, gate=["true"])       # a gate that always passes
        chk(v2["kept"], "a patch that satisfies the gate is kept")
        chk("value = 2" in m.read_text(), "and the change is on disk")

    print("== the loop does not edit anything unless asked ==")
    res = repair([sys.executable, "-c", "import sys; sys.exit(1)"],
                 "dummy", dry_run=True, verbose=False)
    chk(res["failed"], "the failure is detected")
    chk(res.get("dry_run") is True, "dry run is the default")
    chk("outcome" in res, "an outcome is recorded")

    res_ok = repair([sys.executable, "-c", "pass"], "ok", verbose=False)
    chk(not res_ok["failed"], "a passing suite is left alone")

    print("== every attempt is logged ==")
    chk(LOG.exists(), f"the log exists at {LOG.name}")

    print(f"\nself_diagnose.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    live = "--apply" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__)
        raise SystemExit(0)
    target = args[0]
    print(f"diagnosing: {target}   ({'APPLYING' if live else 'dry run'})")
    r = repair([sys.executable, target, "--selftest"], target, dry_run=not live)
    print(f"\noutcome: {r.get('outcome', 'passed')}")
