#!/usr/bin/env python3
"""
cfd_fix.py -- apply the known planarPoiseuille corrections, then re-measure.

BACKGROUND
----------
Our long diagnosis established that every INPUT to the channel case was
correct (viscosity, gradient, mesh, convergence) yet u_max came out ~31%
low. That isolates the fault to the case DICTIONARIES differing from
OpenFOAM's validated planarPoiseuille tutorial. The `--diff` in
cfd_reference.py showed 7/7 dictionaries differ; the ones that actually
change the physics are:

  1. constant/momentumTransport -- ours had a bare `simulationType laminar`
     with no model block. The tutorial specifies the laminar STRESS model
     explicitly. Without it some builds fall back to a default that damps
     the developing profile.
  2. 0/p boundary conditions -- the tutorial fixes a pressure DIFFERENCE
     (inlet fixedValue vs outlet fixedValue 0) that drives the flow. A
     wrong or missing inlet pressure under-drives it -> low u_max.
  3. system/fvSolution relaxation -- over-damped relaxation can stop the
     profile reaching full developed magnitude within endTime.

This tool writes the CORRECT versions of those three, backing up yours
first, then re-runs cfd_tools.py so the result is MEASURED, not assumed.
If u_max still disagrees after this, the fault is elsewhere (mesh grading
or the analytical reference itself) and we look there next -- but these
three are the highest-probability cause by far.

IMPORTANT: this edits an OpenFOAM case, so it only does anything on your
machine where the case exists. It changes nothing live until you run it,
and it backs up every file it touches.

Run:  python cfd_fix.py --show           # print what it WOULD write, no change
      python cfd_fix.py --apply          # back up + write the 3 dictionaries
      python cfd_fix.py --apply --run     # ...then re-run cfd_tools.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home() / "engineai"
# The validated case builder writes here. An environment override keeps this
# repair tool usable for a copied case without silently editing another one.
CASE = Path(os.environ.get("ENGINEAI_CFD_CASE", str(HOME / "work" / "cfd_poiseuille")))

G, R, Y, O = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


# The three corrected dictionaries. These match OpenFOAM's planarPoiseuille
# tutorial for an incompressible laminar developing channel flow.

MOMENTUM_TRANSPORT = """/*--------------------------------*- C++ -*----------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "constant";
    object      momentumTransport;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

simulationType  laminar;

laminar
{
    model   Stokes;
}

// ************************************************************************* //
"""

# 0/p -- fix a pressure DIFFERENCE across the channel to drive the flow.
# Kinematic pressure (m^2/s^2). Inlet value sets the drive; tune if your
# target gradient differs, but a non-zero inlet vs zero outlet is the point.
P_FIELD = """/*--------------------------------*- C++ -*----------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       volScalarField;
    location    "0";
    object      p;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

dimensions      [0 2 -2 0 0 0 0];

internalField   uniform 0;

boundaryField
{
    inlet
    {
        type            fixedValue;
        value           uniform 0.001;
    }
    outlet
    {
        type            fixedValue;
        value           uniform 0;
    }
    walls
    {
        type            zeroGradient;
    }
    "(front|back)"
    {
        type            empty;
    }
}

// ************************************************************************* //
"""

FVSOLUTION = """/*--------------------------------*- C++ -*----------------------------------*/
FoamFile
{
    version     2.0;
    format      ascii;
    class       dictionary;
    location    "system";
    object      fvSolution;
}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

solvers
{
    p
    {
        solver          PCG;
        preconditioner  DIC;
        tolerance       1e-08;
        relTol          0.01;
    }

    U
    {
        solver          PBiCGStab;
        preconditioner  DILU;
        tolerance       1e-08;
        relTol          0.01;
    }
}

SIMPLE
{
    nNonOrthogonalCorrectors 2;
    consistent      yes;
}

relaxationFactors
{
    equations
    {
        U               0.9;
        p               0.9;
    }
}

// ************************************************************************* //
"""

FILES = {
    "constant/momentumTransport": MOMENTUM_TRANSPORT,
    "0/p": P_FIELD,
    "system/fvSolution": FVSOLUTION,
}


def cmd_show() -> int:
    print("  These three dictionaries would be written (backing up yours):\n")
    for rel in FILES:
        exists = (CASE / rel).exists()
        print(f"  {'overwrite' if exists else 'CREATE   '}  {rel}"
              f"   {'(you have this)' if exists else '(missing -- will add)'}")
    print(f"\n  case: {CASE}")
    print("  --apply writes them (with .bak backups). --apply --run re-runs.")
    print("\n  --- preview of constant/momentumTransport ---")
    print("  " + MOMENTUM_TRANSPORT.split("//", 1)[-1].split(
        "* * *")[-1].strip().replace("\n", "\n  ")[:400])
    return 0


def cmd_apply(run_after: bool) -> int:
    if not CASE.exists():
        print(f"  {R}case not found at {CASE}{O}")
        print("  Edit CASE at the top of this file to point at your channel")
        print("  case (the folder containing system/, constant/, 0/).")
        return 1

    stamp = int(time.time())
    print(f"  applying planarPoiseuille corrections to {CASE}\n")
    for rel, content in FILES.items():
        target = CASE / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = target.with_suffix(target.suffix + f".bak.{stamp}")
            shutil.copy(target, backup)
            print(f"  {Y}backed up{O} {rel} -> {backup.name}")
        target.write_text(content)
        print(f"  {G}wrote{O}     {rel}")

    print(f"\n  done. Originals saved as .bak.{stamp}")

    if run_after:
        tools = HOME / "cfd_tools.py"
        if not tools.exists():
            print(f"  {Y}cfd_tools.py not found -- run your CFD test manually{O}")
            return 0
        print(f"\n  re-running cfd_tools.py to MEASURE the result ...\n")
        r = subprocess.run([sys.executable, str(tools)],
                           cwd=str(HOME), text=True)
        print(f"\n  {'='*60}")
        print("  Look at u_max above. If it now matches the analytical value")
        print("  (within a few %), the fix worked. If still ~31% low, the")
        print("  cause is elsewhere (mesh grading / reference) -- tell me the")
        print("  new number and we look there.")
        return r.returncode
    else:
        print("\n  next:  python cfd_tools.py     (to measure the result)")
        print("  or re-run:  python cfd_fix.py --apply --run")
    return 0


def main() -> int:
    a = sys.argv[1:]
    if not a or a[0] == "--help":
        print(__doc__.split("Run:")[-1])
        return 0
    if "--show" in a:
        return cmd_show()
    if "--apply" in a:
        return cmd_apply("--run" in a)
    print("commands: --show | --apply [--run]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
