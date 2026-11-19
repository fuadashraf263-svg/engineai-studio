#!/usr/bin/env python3
"""
cfd_reference.py -- stop guessing. Diff against OpenFOAM's own tutorials.

WHERE THE CFD CASE STANDS
-------------------------
Every physical input is now verified against the solver's own output:

    nu               exact 1/nu scaling (0.17% deviation over a 2x change)
    dp/dx            0.9998 of imposed, measured from the p field
    convergence      0.0000% change from iteration 2000 to 20000
    mesh             half-height 5.0000 mm, length 200.0000 mm, dy 0.2500 mm
                     -- read from OpenFOAM's own cell centres

and u_max is still 31.15% low, settled, at both nu values.

No input is wrong. Therefore the equation being solved is not the one I
believe is being solved, and the only artefact never validated is the set
of case dictionaries -- which I wrote from memory.

I have been wrong about this case five times: cell ordering, residual
convergence, relaxation factors, the sigFpe guard, and viscosity. A sixth
guess is not a method. OpenFOAM 13 ships validated tutorial cases at
$FOAM_TUTORIALS. This tool finds the closest one, runs it, and diffs its
dictionaries against ours -- so the next change is evidence, not a hunch.

Usage
-----
    python cfd_reference.py --list          # what laminar cases exist
    python cfd_reference.py --show NAME     # print its dictionaries
    python cfd_reference.py --diff NAME     # dictionary-by-dictionary diff
    python cfd_reference.py --run NAME      # run the tutorial unmodified
                                            # (proves OpenFOAM itself is sane)
"""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
import sys
from pathlib import Path

FOAM_BASHRC = Path("/opt/openfoam13/etc/bashrc")
HOME = Path.home() / "engineai"
OURS = HOME / "work" / "cfd_poiseuille"
REF = HOME / "work" / "cfd_reference"

DICTS = ["system/fvSchemes", "system/fvSolution", "system/controlDict",
         "constant/physicalProperties", "constant/momentumTransport",
         "0/U", "0/p"]

G, R, Y, O = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def foam(cmd: str, cwd: Path, timeout: int = 1800):
    return subprocess.run(
        ["bash", "-c", f"source {FOAM_BASHRC} >/dev/null 2>&1 && {cmd}"],
        cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def tutorials_dir() -> Path | None:
    r = foam("echo $FOAM_TUTORIALS", Path.home(), timeout=60)
    p = Path(r.stdout.strip()) if r.stdout.strip() else None
    return p if p and p.exists() else None


def cmd_list() -> int:
    """Report what is actually THERE. No clever classification that can
    fail open -- absence of a file is reported as 'unknown', never as a
    verdict. That default is what made the first version claim zero
    laminar cases out of 53."""
    td = tutorials_dir()
    if not td:
        print("  $FOAM_TUTORIALS not found -- is OpenFOAM 13 installed?")
        return 1
    print(f"  tutorials root: {td}\n")

    roots = [td / "incompressibleFluid"]
    if not roots[0].exists():
        roots = [td]
        print(f"  (no incompressibleFluid dir; scanning the whole tree)\n")

    # .parent is the system/ dir; the CASE is one level above. Getting this
    # wrong made every rglob for momentumTransport search inside system/,
    # find nothing, and fall through to the wrong default.
    cases = sorted({p.parent.parent for r in roots
                    for p in r.rglob("system/controlDict")})
    if not cases:
        print("  no cases found")
        return 1

    buckets = {"laminar": [], "RAS": [], "LES": [], "unknown": []}
    for c in cases:
        # look ANYWHERE in the case, and record where it was found
        found = sorted(c.rglob("momentumTransport*"))
        if not found:
            buckets["unknown"].append((c, None))
            continue
        txt = found[0].read_text().lower()
        m = re.search(r"simulationtype\s+(\w+)", txt)
        kind = m.group(1) if m else "unknown"
        key = ("laminar" if kind.startswith("lam")
               else "RAS" if kind.upper().startswith("RAS")
               else "LES" if kind.upper().startswith("LES")
               else "unknown")
        buckets[key].append((c, found[0].relative_to(c)))

    base = roots[0]
    for key in ("laminar", "unknown", "RAS", "LES"):
        items = buckets[key]
        col = G if key == "laminar" else (Y if key == "unknown" else "")
        print(f"  {col}{key.upper()}{O}: {len(items)} case(s)")
        if key in ("laminar", "unknown"):
            for c, where in items[:25]:
                try:
                    rel = c.relative_to(base)
                except ValueError:
                    rel = c
                loc = f"   [{where}]" if where else "   [no momentumTransport]"
                print(f"      {rel}{loc}")
            if len(items) > 25:
                print(f"      ... and {len(items)-25} more")
        print()

    print("  A case with NO momentumTransport is usually laminar by default")
    print("  -- those are the best references for our Poiseuille channel.")
    print("\n  next:  python cfd_reference.py --show <name>   # dump its dicts")
    print("         python cfd_reference.py --diff <name>   # diff vs ours")
    return 0


def cmd_show(name: str) -> int:
    """Print a tutorial's dictionaries verbatim. When a diff is noisy, just
    read the reference."""
    ref = _find(name)
    if not ref:
        print(f"  tutorial '{name}' not found. Try --list")
        return 1
    print(f"  {ref}\n")
    for f in sorted(ref.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(ref)
        if rel.parts[0] not in ("system", "constant", "0", "0.orig"):
            continue
        if f.suffix in (".gz",) or f.stat().st_size > 20000:
            print(f"{'='*70}\n  {rel}   ({f.stat().st_size} bytes, skipped)")
            continue
        print(f"{'='*70}\n  {rel}\n{'='*70}")
        for ln in f.read_text(errors="replace").splitlines():
            t = ln.split("//")[0].rstrip()
            if not t.strip() or t.strip().startswith(("/*", "|", "\\", "*")):
                continue
            print("  " + t)
    return 0


def _find(name: str) -> Path | None:
    td = tutorials_dir()
    if not td:
        return None
    root = td / "incompressibleFluid"
    for base in (root, td):
        if not base.exists():
            continue
        exact = base / name
        if (exact / "system" / "controlDict").exists():
            return exact
        for p in base.rglob("system/controlDict"):
            case = p.parent.parent
            if case.name == name or name in str(case):
                return case
    return None


def cmd_diff(name: str) -> int:
    ref = _find(name)
    if not ref:
        print(f"  tutorial '{name}' not found. Try --list")
        return 1
    if not OURS.exists():
        print(f"  our case not found at {OURS} -- run cfd_tools.py first")
        return 1
    print(f"  reference : {ref}")
    print(f"  ours      : {OURS}\n")

    differences = 0
    for rel in DICTS:
        a, b = ref / rel, OURS / rel
        print(f"{'='*70}\n  {rel}\n{'='*70}")
        if not a.exists():
            print(f"  {Y}reference has no {rel}{O}"
                  + ("  <- and we DO. Suspicious." if b.exists() else ""))
            differences += 1
            continue
        if not b.exists():
            print(f"  {R}WE have no {rel} but the tutorial does.{O}")
            differences += 1
            continue

        def clean(p: Path):
            out = []
            for ln in p.read_text().splitlines():
                s = ln.split("//")[0].rstrip()
                if not s.strip() or s.strip().startswith(("/*", "|", "\\",
                                                          "*", "#include")):
                    continue
                out.append(s)
            return out

        ra, rb = clean(a), clean(b)
        d = list(difflib.unified_diff(ra, rb, "TUTORIAL", "OURS", lineterm=""))
        if not d:
            print(f"  {G}identical (ignoring comments/headers){O}")
            continue
        differences += 1
        for ln in d[2:]:
            col = G if ln.startswith("+") else (R if ln.startswith("-") else "")
            print(f"  {col}{ln}{O}")

    print(f"\n{'='*70}")
    print(f"  {differences} of {len(DICTS)} dictionaries differ.")
    print("  Every keyword the tutorial has and we don't is a candidate for")
    print("  the missing 31%. Work through them one at a time, re-running")
    print("  cfd_tools.py after each -- one change, one measurement.")
    return 0


def cmd_run(name: str) -> int:
    """Run the tutorial UNMODIFIED. If OpenFOAM cannot solve its own
    validated case, the problem is the install, not our dictionaries."""
    ref = _find(name)
    if not ref:
        print(f"  tutorial '{name}' not found. Try --list")
        return 1
    if REF.exists():
        shutil.rmtree(REF)
    REF.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ref, REF)
    print(f"  copied {ref}\n      -> {REF}\n")

    allrun = REF / "Allrun"
    if allrun.exists():
        print("  running its own Allrun script ...")
        r = foam("./Allrun", REF, timeout=3600)
    else:
        print("  no Allrun; running blockMesh + foamRun ...")
        r = foam("blockMesh && foamRun", REF, timeout=3600)

    tail = "\n".join([l for l in (r.stdout + r.stderr).splitlines()
                      if l.strip()][-25:])
    print(tail)
    if r.returncode == 0:
        print(f"\n  {G}tutorial ran clean. OpenFOAM 13 is healthy; the fault "
              f"is in OUR case.{O}")
    else:
        print(f"\n  {R}the tutorial itself failed (exit {r.returncode}). "
              f"That is an install problem, not a case problem.{O}")
    return r.returncode


def main() -> int:
    if not FOAM_BASHRC.exists():
        print(f"  OpenFOAM not found at {FOAM_BASHRC}")
        return 1
    a = sys.argv[1:]
    if not a or "--list" in a:
        return cmd_list()
    if "--diff" in a:
        i = a.index("--diff")
        return cmd_diff(a[i + 1]) if i + 1 < len(a) else cmd_list()
    if "--show" in a:
        i = a.index("--show")
        return cmd_show(a[i + 1]) if i + 1 < len(a) else cmd_list()
    if "--run" in a:
        i = a.index("--run")
        return cmd_run(a[i + 1]) if i + 1 < len(a) else cmd_list()
    return cmd_list()


if __name__ == "__main__":
    sys.exit(main())
