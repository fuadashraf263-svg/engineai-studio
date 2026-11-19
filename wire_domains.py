#!/usr/bin/env python3
"""
wire_domains.py -- close three gaps found by reading the actual v3 source.

GAP 1  engine_tools.verification_suite() has SEVEN entries. Electromagnetics
       is not one of them. em_tools.py verified at 0.41% standalone, but
       `agent.py verify` never calls it, so it has never counted toward G.
       The scorecard has been right all along: 7/13, not 8/13.

GAP 2  registry()["cfd"] probes for `simpleFoam`. In OpenFOAM 13 that is a
       deprecation stub, and it is only on PATH inside an OpenFOAM shell --
       which the conda env deliberately is not. CFD would read "not
       installed" forever even after it verifies.

GAP 3  metrics.py hardcodes `rung = 2` and prints "counterfactual reasoning
       NOT implemented". counterfactual.py exists and exposes
       CounterfactualEngine.rung_achieved(), which returns 3 only if a
       counterfactual was VERIFIED against a real solve. metrics.py never
       imports it.

Run:  conda activate eng && cd ~/engineai && python wire_domains.py
Backups are written next to each file before any edit. Safe to re-run.
"""

from __future__ import annotations

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


def patch(path: Path, old: str, new: str, label: str,
          marker: str = "") -> bool:
    """marker must be text that appears ONLY after the patch. Using the first
    line of `new` fails when the patch appends to an existing line -- that
    line is already in the source and every patch reports 'already applied'."""
    if not path.exists():
        bad(f"{path.name} not found")
        return False
    src = path.read_text()
    if marker and marker in src:
        note(f"{label} -- already applied")
        return True
    if old not in src:
        bad(f"{label} -- anchor text not found; file differs from v3")
        return False
    shutil.copy(path, path.with_suffix(path.suffix + f".bak.{int(time.time())}"))
    path.write_text(src.replace(old, new, 1))
    ok(label)
    return True


# ------------------------------------------------------------------ GAP 1 ---

SUITE_OLD = """        ("materials", lambda: binary_liquidus(), "liquidus_error_pct"),
    ]"""

SUITE_NEW = '''        ("materials", lambda: binary_liquidus(), "liquidus_error_pct"),
        ("electromagnetics", _em_check, "field_error_pct"),
        ("cfd", _cfd_check, "error_vs_imposed_pct"),
    ]


def _em_check():
    """Elmer magnetostatics vs Ampere's law. Imported lazily so a missing
    Elmer degrades to a SKIP rather than breaking the whole suite."""
    import em_tools
    return em_tools.magnetostatic_wire(mesh_size_mm=0.8)


def _cfd_check():
    """OpenFOAM plane Poiseuille vs the exact parabolic solution."""
    import json
    import subprocess
    import sys as _sys
    from pathlib import Path as _P
    r = subprocess.run([_sys.executable, "cfd_tools.py"],
                       cwd=str(_P(__file__).parent),
                       capture_output=True, text=True, timeout=3600)
    vj = _P.home() / "engineai" / "work" / "cfd_poiseuille" / "verification.json"
    if not vj.exists():
        raise RuntimeError("cfd_tools produced no verification.json:\\n"
                           + r.stdout[-800:])
    return json.loads(vj.read_text())'''

# ------------------------------------------------------------------ GAP 2 ---

CFD_OLD = '''        "cfd": {"backend": "OpenFOAM",
                "status": "active" if _has("simpleFoam") else
                "not installed -- install_domains.sh sec 1"},'''

CFD_NEW = '''        "cfd": {"backend": "OpenFOAM 13 (foamRun)",
                "status": _foam_status(),
                "tools": ["cfd_poiseuille_channel"]},'''

FOAM_FN = '''def _foam_status():
    """OpenFOAM is NOT on the conda PATH by design -- its bashrc shadows
    conda's libraries. Probe it the same way cfd_tools does: inside its own
    subshell. `simpleFoam` is only a deprecation stub in 13; foamRun is real.
    """
    import subprocess
    brc = "/opt/openfoam13/etc/bashrc"
    if not Path(brc).exists():
        return "not installed -- install_domains.sh sec 1"
    try:
        r = subprocess.run(
            ["bash", "-c", f"source {brc} >/dev/null 2>&1 && "
                           "which foamRun && echo $WM_PROJECT_VERSION"],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and "foamRun" in r.stdout:
            v = r.stdout.strip().splitlines()[-1]
            return f"active (OpenFOAM {v}, isolated subshell)"
    except Exception:
        pass
    return "present but foamRun not callable"


def _py(mod):'''

# ------------------------------------------------------------------ GAP 3 ---

RUNG_OLD = """    rung = 2 if missions else (1 if wm_err is not None else 0)"""

RUNG_NEW = """    # Pearl rung, MEASURED not asserted. counterfactual.rung_achieved()
    # returns 3 only if a counterfactual claim was settled against a real
    # solve -- claiming rung 3 on an unverified prediction would be exactly
    # the self-flattery this scorecard exists to prevent.
    base = 2 if missions else (1 if wm_err is not None else 0)
    try:
        from counterfactual import CounterfactualEngine
        rung = max(base, CounterfactualEngine.rung_achieved())
    except Exception:
        rung = base"""

RUNG_LABEL_OLD = '''        f"  I   Pearl rung        {rung}/3 "
        f"(counterfactual reasoning NOT implemented)",'''

RUNG_LABEL_NEW = '''        f"  I   Pearl rung        {rung}/3 "
        + ("(counterfactual VERIFIED against a real solve)" if rung >= 3
           else "(counterfactual engine present but never verified)"),'''


def main() -> int:
    print("\n" + "=" * 62)
    print("  EngineAI domain wiring")
    print("=" * 62 + "\n")

    et = HOME / "engine_tools.py"
    mt = HOME / "metrics.py"
    good = True

    print("[1] verification_suite -- add electromagnetics + cfd")
    good &= patch(et, SUITE_OLD, SUITE_NEW, "EM and CFD added to the suite",
                  marker="def _em_check")

    print("\n[2] registry -- probe foamRun, not the simpleFoam stub")
    good &= patch(et, CFD_OLD, CFD_NEW, "cfd registry entry corrected",
                  marker="OpenFOAM 13 (foamRun)")
    good &= patch(et, "def _py(mod):", FOAM_FN, "_foam_status() helper added",
                  marker="def _foam_status")

    print("\n[3] metrics -- connect counterfactual.py to the Pearl rung")
    good &= patch(mt, RUNG_OLD, RUNG_NEW, "rung now measured",
                  marker="CounterfactualEngine.rung_achieved()")
    good &= patch(mt, RUNG_LABEL_OLD, RUNG_LABEL_NEW, "rung label honest",
                  marker="counterfactual VERIFIED against a real solve")

    print("\n[4] syntax check")
    import ast
    for f in (et, mt):
        try:
            ast.parse(f.read_text())
            ok(f"{f.name} parses cleanly")
        except SyntaxError as e:
            bad(f"{f.name}: {e}  -- restore the .bak file")
            good = False

    print("\n" + "=" * 62)
    if good:
        print("  Done. Now run:")
        print("      python agent.py tools     # cfd should read 'active'")
        print("      python agent.py verify    # 9 domains attempted, not 7")
        print("      python agent.py agi       # G out of 13, honest rung")
    else:
        print("  Some patches failed -- send me the output.")
    print("=" * 62 + "\n")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
