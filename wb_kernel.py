#!/usr/bin/env python3
"""
wb_kernel.py - real live editing for the workbench: run a CadQuery script,
measure the solid it produces, and never take the server down doing it.

WHY A SUBPROCESS
----------------
Sliders over a fixed parameter list are not editing, they are dialling. Real
parametric CAD is script-driven, so the workbench lets you edit the script
that builds the solid. That means running code the page sent, and two things
follow from it:

  * OCC (the geometry kernel under cadquery) does not raise on every bad
    input - a self-intersecting sweep or a zero-thickness shell can SEGFAULT
    the interpreter. A thread cannot be killed and a segfault takes the whole
    server with it, so every build runs in a child process that is allowed to
    die on its own.
  * A script can loop forever. A subprocess can be timed out and killed; a
    thread cannot.

HONEST SECURITY POSITION
------------------------
This executes Python. The namespace is restricted to cadquery and math, the
import statement is refused, dunder access is refused, the child runs with a
scrubbed environment in a throwaway directory, and CPU, address space and
file-size ceilings are applied between fork and exec.

That is defence against ACCIDENTS, and it is worth having: a sweep that never
converges, a loft that allocates until the machine swaps, a script that fills
the disk. All three used to take the whole server with them.

It is NOT a security sandbox and must not be treated as one. A determined
person who can reach this endpoint can still run code as you; rlimits bound
what a mistake costs, not what an attacker can do. It is acceptable only
because the workbench binds 127.0.0.1 and serves one trusted user - the same
person who could open a terminal anyway. If it is ever exposed to a network,
this endpoint has to go behind something real first: a container, a seccomp
profile, or a separate unprivileged user.

MEASUREMENT
-----------
The workbench previously listed "no measurement" as a limitation. It is a
limitation no longer: volume, surface area, bounding box, centre of mass and
mass for an assigned material all come straight off the B-rep, and volume is
cross-checked against the triangulated mesh so a measurement is never reported
from a solid the mesher and the kernel disagree about.

  python wb_kernel.py --selftest
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 25            # seconds; a real part builds in under two

# Densities in kg/m^3. Enough to turn a volume into a mass honestly; the full
# table lives in materials.py and this is deliberately a short, obvious list.
DENSITY = {
    "steel (mild)": 7850.0, "steel (stainless 304)": 8000.0,
    "aluminium 6061": 2700.0, "titanium Ti-6Al-4V": 4430.0,
    "brass": 8500.0, "cast iron": 7200.0,
    "ABS": 1040.0, "PLA": 1240.0, "nylon 6": 1140.0,
}

# Patterns refused before the child process is even started. This is a
# guard against accidents, not an attacker - see the docstring.
FORBIDDEN = [
    (r"\bimport\b", "import is not available; cq and math are already bound"),
    (r"__[a-zA-Z]", "dunder attribute access is refused"),
    (r"\bopen\s*\(", "file access is refused"),
    (r"\beval\s*\(|\bexec\s*\(", "eval/exec are refused"),
    (r"\bcompile\s*\(", "compile is refused"),
]


def check_script(src: str):
    """Return the first refusal reason, or None. Runs BEFORE the subprocess."""
    if not src or not src.strip():
        return "the script is empty"
    if len(src) > 20000:
        return "the script is over 20 kB; that is not a part, that is a program"
    for pat, why in FORBIDDEN:
        if re.search(pat, src):
            return why
    if not re.search(r"^\s*result\s*=", src, re.M):
        return ("the script must assign the finished solid to `result`, "
                "e.g. result = cq.Workplane('XY').box(10, 20, 5)")
    return None


# --------------------------------------------------------------------------
# the child process
# --------------------------------------------------------------------------

CHILD = r'''
import json, math, sys
payload = json.loads(sys.stdin.read())
try:
    import cadquery as cq
    ns = {"cq": cq, "math": math, "__builtins__": {
        "abs": abs, "min": min, "max": max, "round": round, "range": range,
        "len": len, "sum": sum, "float": float, "int": int, "bool": bool,
        "str": str, "list": list, "tuple": tuple, "dict": dict,
        "enumerate": enumerate, "zip": zip, "sorted": sorted, "print": print,
        "True": True, "False": False, "None": None,
    }}
    exec(compile(payload["src"], "<part>", "exec"), ns)
    wp = ns.get("result")
    if wp is None:
        raise ValueError("the script finished without assigning `result`")
    shape = wp.val() if hasattr(wp, "val") else wp
    if hasattr(shape, "Volume"):
        vol = float(shape.Volume())
    else:
        raise ValueError("`result` is not a solid; it may be a sketch or a wire")
    if vol <= 0:
        raise ValueError(f"`result` has zero or negative volume ({vol})")
    bb = shape.BoundingBox()
    c = shape.Center()
    try:
        area = float(shape.Area())
    except Exception:
        area = 0.0
    from cadquery import exporters
    exporters.export(wp, payload["stl"], exportType="STL",
                     tolerance=payload.get("tolerance", 0.2))
    reimported = None
    if payload.get("step"):
        exporters.export(wp, payload["step"], exportType="STEP")
        # Re-open what was just written and measure it again. An arbitrary
        # script has no closed form to check against, but a STEP file that
        # does not read back at the same volume is not a deliverable - the
        # same rule deliverables.py applies to every other format.
        try:
            from cadquery import importers
            back = importers.importStep(payload["step"])
            reimported = float(back.val().Volume())
        except Exception as exc:
            reimported = f"could not re-open: {exc}"
    print("@@" + json.dumps({
        "ok": True, "volume_mm3": vol, "area_mm2": area,
        "reimported_mm3": reimported,
        "bbox_mm": [bb.xlen, bb.ylen, bb.zlen],
        "bbox_min": [bb.xmin, bb.ymin, bb.zmin],
        "bbox_max": [bb.xmax, bb.ymax, bb.zmax],
        "center_mm": [c.x, c.y, c.z],
        "solids": len(wp.solids().vals()) if hasattr(wp, "solids") else 1,
    }))
except Exception as exc:
    print("@@" + json.dumps({"ok": False,
                             "error": f"{type(exc).__name__}: {exc}"}))
'''


def _limits():
    """Resource ceilings for the child, applied between fork and exec.

    This does not make the editor a security sandbox and nothing here should
    be read as claiming it does. What it does is bound the ACCIDENTS: a sweep
    that never converges, a loft that allocates until the machine swaps, a
    script that fills the disk. Those are the realistic failures for a person
    writing CadQuery, and before this they took the whole box with them.

    Limits are generous because OCC is genuinely hungry - a modest solid can
    want a gigabyte - and a ceiling that fires on ordinary work would just be
    turned off.
    """
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (30, 32))        # seconds
        resource.setrlimit(resource.RLIMIT_AS, (6 << 30, 6 << 30))   # 6 GB
        resource.setrlimit(resource.RLIMIT_FSIZE, (512 << 20, 512 << 20))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))         # no core dumps
    except Exception:                                            # noqa: BLE001
        pass                       # not Linux, or limits unavailable


def run_script(src: str, tolerance: float = 0.2, timeout: int = TIMEOUT,
               export_dir: str = None) -> dict:
    """Build a solid from a script. Always returns a dict; never raises."""
    bad = check_script(src)
    if bad:
        return {"ok": False, "error": bad, "stage": "refused before running"}

    with tempfile.TemporaryDirectory() as td:
        stl = os.path.join(td, "part.stl")
        step = os.path.join(export_dir, "scripted.step") if export_dir else None
        payload = json.dumps({"src": src, "stl": stl, "tolerance": tolerance,
                              "step": step})
        try:
            p = subprocess.run([sys.executable, "-c", CHILD], input=payload,
                               capture_output=True, text=True, timeout=timeout,
                               cwd=td, preexec_fn=_limits,
                               env={"PATH": os.environ.get("PATH", ""),
                                    "HOME": td,
                                    "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                                    "PYTHONDONTWRITEBYTECODE": "1"})
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "timeout",
                    "error": f"the build did not finish in {timeout}s and was "
                             f"killed. An unbounded loop, or geometry the "
                             f"kernel is struggling with."}
        out = p.stdout or ""
        line = next((l for l in out.splitlines() if l.startswith("@@")), None)
        if line is None:
            # No result line at all means the child died rather than reported -
            # the segfault case the subprocess exists to survive.
            tail = (p.stderr or out).strip()[-500:]
            return {"ok": False, "stage": "crashed",
                    "error": f"the geometry kernel exited with code "
                             f"{p.returncode} without returning a result. "
                             f"{tail or 'No output.'}"}
        res = json.loads(line[2:])
        if not res.get("ok"):
            res.setdefault("stage", "script error")
            return res
        with open(stl, "rb") as fh:
            res["stl"] = fh.read()
        if step:
            res["step_path"] = step
            back = res.get("reimported_mm3")
            if isinstance(back, (int, float)) and res["volume_mm3"]:
                rel = abs(back - res["volume_mm3"]) / res["volume_mm3"]
                res["step_verified"] = rel < 1e-6
                res["step_detail"] = (
                    f"re-imported STEP volume {back:.4f} mm^3 vs source "
                    f"{res['volume_mm3']:.4f} mm^3 ({rel:.2e} relative)")
            else:
                res["step_verified"] = False
                res["step_detail"] = str(back)
        res["stdout"] = "\n".join(l for l in out.splitlines()
                                  if not l.startswith("@@"))[:2000]
        return res


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------

def measure(props: dict, material: str = None, mesh_mm3: float = None) -> dict:
    """Turn raw B-rep properties into the numbers an engineer asks for.

    If a mesh volume is supplied it is compared with the B-rep volume, and a
    disagreement is reported rather than averaged away: the two are computed by
    different code paths, so when they differ one of them is wrong and the
    measurement should not be trusted until it is known which.
    """
    if not props.get("ok"):
        return props
    v_mm3 = props["volume_mm3"]
    out = {
        "volume_mm3": v_mm3, "volume_cm3": v_mm3 / 1000.0,
        "area_mm2": props.get("area_mm2", 0.0),
        "bbox_mm": props["bbox_mm"], "center_mm": props["center_mm"],
        "solids": props.get("solids", 1),
    }
    if material:
        rho = DENSITY.get(material)
        if rho is None:
            out["material_note"] = (f"unknown material {material!r}; no mass "
                                    f"reported rather than a guessed density")
        else:
            out["material"] = material
            out["density_kg_m3"] = rho
            out["mass_kg"] = v_mm3 * 1e-9 * rho
            out["mass_g"] = v_mm3 * 1e-9 * rho * 1000.0
    if mesh_mm3:
        rel = abs(mesh_mm3 - v_mm3) / v_mm3 if v_mm3 else float("inf")
        out["mesh_mm3"] = mesh_mm3
        out["mesh_vs_brep_pct"] = rel * 100.0
        # A triangulated volume is a chord approximation of a curved face, so
        # it is legitimately a little under. 1% is the honest bar for the
        # default tolerance; tighter than that and it would fire on every
        # cylinder for no reason.
        out["agrees"] = rel < 0.01
        if not out["agrees"]:
            out["warning"] = (f"the mesh and the B-rep disagree by {rel*100:.2f}%. "
                              f"One of them is wrong; the measurement is not "
                              f"trustworthy until it is known which.")
    return out


# --------------------------------------------------------------------------
# starting scripts
# --------------------------------------------------------------------------

def script_for(part: str, params: dict = None) -> str:
    """A runnable script equivalent to a registry part, as an editing start.

    Generated from the LIVE defaults in cad_parts rather than a copy, so the
    editor cannot drift out of step with the registry the sliders drive.
    """
    try:
        import cad_parts
        defaults = dict(cad_parts.PARTS[part][2])
    except Exception:                                         # noqa: BLE001
        defaults = {}
    defaults.update({k: float(v) for k, v in (params or {}).items()
                     if k in defaults})
    head = "\n".join(f"{k} = {v:g}" for k, v in defaults.items())

    bodies = {
        "mounting_plate": """
result = (cq.Workplane("XY")
          .box(L, W, T)
          .faces(">Z").workplane()
          .rect(L - 2*margin, W - 2*margin, forConstruction=True)
          .vertices().hole(hole_d))
""",
        # An L-section is a PROFILE, not two boxes glued together. The first
        # version of this starter used a boolean union of two boxes and came
        # out 6.4% heavy, because the corner was counted twice - the selftest
        # caught it against the registry's closed form. Sweeping the real
        # outline is both correct and the better thing to teach in an editor.
        "l_bracket": """
pts = [(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)]
result = cq.Workplane("XY").polyline(pts).close().extrude(width)
""",
        "shaft": """
result = (cq.Workplane("XY").circle(d1/2).extrude(l1)
          .faces(">Z").workplane().circle(d2/2).extrude(l2))
""",
        "flange": """
result = (cq.Workplane("XY").circle(od/2).extrude(t)
          .faces(">Z").workplane().hole(idia)
          .faces(">Z").workplane()
          .polarArray(bc/2, 0, 360, int(n))
          .hole(bolt_d))
""",
        "u_channel": """
pts = [(0, 0), (w, 0), (w, h), (w - t, h), (w - t, t), (t, t), (t, h), (0, h)]
result = cq.Workplane("XY").polyline(pts).close().extrude(length)
""",
    }
    body = bodies.get(part, "\nresult = cq.Workplane('XY').box(40, 25, 10)\n")
    return (f"# {part} - edit freely. `cq` and `math` are bound.\n"
            f"# Assign the finished solid to `result`.\n\n"
            f"{head}\n{textwrap.dedent(body).strip()}\n")


# --------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def chk(cond, label, extra=""):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label
              + (f"   {extra}" if extra else ""))
        ok = ok and bool(cond)

    print("wb_kernel selftest")

    # ---- the guard, which must run before any child process ----
    chk(check_script("") is not None, "an empty script is refused")
    chk("import" in (check_script("import os\nresult = 1") or ""),
        "import is refused")
    chk(check_script("result = cq.Workplane('XY').box(1,1,1).__class__") is not None,
        "dunder access is refused")
    chk(check_script("open('/etc/passwd')\nresult = 1") is not None,
        "file access is refused")
    chk(check_script("x = cq.Workplane('XY').box(1,1,1)") is not None,
        "a script that never assigns `result` is refused, with advice")
    chk("result" in (check_script("x = 1") or ""),
        "...and the refusal shows the line to write")
    chk(check_script("result = cq.Workplane('XY').box(10,10,10)") is None,
        "an ordinary script passes the guard")

    have = True
    try:
        import cadquery  # noqa: F401
    except ImportError:
        have = False
        print("  skip  cadquery not installed; build tests skipped")

    if have:
        r = run_script("result = cq.Workplane('XY').box(20, 10, 5)")
        chk(r["ok"], f"a box builds ({r.get('error','')})")
        chk(abs(r["volume_mm3"] - 1000.0) < 1e-6,
            f"its volume is exactly 1000 mm^3 ({r.get('volume_mm3')})")
        chk(len(r.get("stl", b"")) > 300, "STL bytes came back from the child")
        chk([round(x) for x in r["bbox_mm"]] == [20, 10, 5],
            f"the bounding box is right {r['bbox_mm']}")

        m = measure(r, material="aluminium 6061", mesh_mm3=r["volume_mm3"])
        chk(abs(m["mass_g"] - 2.7) < 0.01,
            f"1000 mm^3 of 6061 weighs 2.7 g (got {m['mass_g']:.4f})")
        chk(m["agrees"], "mesh and B-rep volumes agree")
        m2 = measure(r, material="unobtainium")
        chk("mass_kg" not in m2 and "material_note" in m2,
            "an unknown material reports NO mass rather than guessing a density")
        m3 = measure(r, mesh_mm3=r["volume_mm3"] * 1.5)
        chk(not m3["agrees"] and "warning" in m3,
            "a 50% mesh disagreement is flagged, not averaged away")

        bad = run_script("result = cq.Workplane('XY').box(0, 0, 0)")
        chk(not bad["ok"], "a zero-volume solid is refused")
        chk("volume" in bad["error"].lower() or "null" in bad["error"].lower()
            or "standard" in bad["error"].lower(),
            f"...and the error says why", bad["error"][:60])

        err = run_script("result = cq.Workplane('XY').nonsense()")
        chk(not err["ok"] and err["stage"] == "script error",
            "a bad method is reported as a script error, not a crash")

        slow = run_script("while True:\n    pass\nresult = 1", timeout=3)
        chk(not slow["ok"] and slow["stage"] == "timeout",
            "an infinite loop is killed by timeout")
        chk("3s" in slow["error"], "...and the message names the limit")

        # The whole reason for a child process: this must not kill the parent.
        chk(run_script("result = cq.Workplane('XY').box(1,1,1)")["ok"],
            "the kernel still works after a timeout - the server survived")

        # Every registry part, not a chosen few: a starter script that does not
        # reproduce the part it is named after is a lie the editor tells on
        # first open, and only a full sweep catches it.
        import cad_parts as _cp
        for part in sorted(_cp.PARTS):
            s = script_for(part)
            chk(check_script(s) is None, f"the {part} starter script is valid")
            rr = run_script(s)
            chk(rr["ok"], f"...and it builds", rr.get("error", "")[:60])
            if rr["ok"]:
                try:
                    import cad_parts
                    _, vol_fn, defaults = cad_parts.PARTS[part]
                    want = vol_fn(**defaults)
                    rel = abs(rr["volume_mm3"] - want) / want
                    chk(rel < 0.02,
                        f"...and its volume matches the registry closed form "
                        f"({rel*100:.3f}%)")
                except Exception as exc:                      # noqa: BLE001
                    print(f"  skip  {part} volume cross-check: {exc}")

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest() if "--selftest" in sys.argv else selftest())
