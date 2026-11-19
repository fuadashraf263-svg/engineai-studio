#!/usr/bin/env python3
"""
circuits_nl.py - EngineAI nonlinear circuit analysis (diodes), verified.

Nonlinear DC operating points solved by Newton-Raphson, cross-checked against an
independent bisection solve (no shared code path) and, where ngspice is present,
against ngspice. Emits SPICE netlists. A verified analysis layer, not a
substitute for full circuit simulation.

Shockley diode:  I = Is (exp(V/(n Vt)) - 1)
"""
from __future__ import annotations
import argparse, math, os, re, shutil, subprocess, tempfile

VT = 0.025852  # thermal voltage kT/q near 300 K


def diode_current(vd, Is=1e-14, n=1.0, Vt=VT):
    return Is * (math.exp(vd / (n * Vt)) - 1.0)


def diode_series_op(vs=5.0, r=1000.0, Is=1e-14, n=1.0, Vt=VT):
    """Operating point of V -> R -> diode -> gnd. KCL: (vs-vd)/r = I_d(vd). Newton-Raphson."""
    f = lambda vd: (vs - vd) / r - diode_current(vd, Is, n, Vt)
    fp = lambda vd: -1.0 / r - (Is / (n * Vt)) * math.exp(vd / (n * Vt))
    vd = min(vs, 0.7)
    for _ in range(100):
        step = f(vd) / fp(vd)
        vd -= step
        if abs(step) < 1e-12:
            break
    return {"vd": vd, "id": diode_current(vd, Is, n, Vt), "residual": f(vd)}


def _bisection_vd(vs=5.0, r=1000.0, Is=1e-14, n=1.0, Vt=VT):
    """Independent solve of the same KCL by bisection - cross-check for Newton."""
    f = lambda vd: (vs - vd) / r - diode_current(vd, Is, n, Vt)
    lo, hi = 0.0, max(vs, 1.0)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def ngspice_available():
    return shutil.which("ngspice") is not None


def _run_ngspice(netlist):
    fd, path = tempfile.mkstemp(suffix=".cir")
    with os.fdopen(fd, "w") as fh:
        fh.write(netlist)
    try:
        p = subprocess.run(["ngspice", "-b", path], capture_output=True, text=True, timeout=30)
        return p.stdout + p.stderr
    finally:
        os.unlink(path)


def diode_series_netlist(vs=5.0, r=1000.0, Is=1e-14, n=1.0):
    return (f"* diode series\nV1 in 0 {vs:g}\nR1 in out {r:g}\nD1 out 0 DMOD\n"
            f".model DMOD D(Is={Is:g} N={n:g})\n.control\nop\nprint v(out)\n.endc\n.end\n")


def ngspice_diode_vd(vs=5.0, r=1000.0, Is=1e-14, n=1.0):
    txt = _run_ngspice(diode_series_netlist(vs, r, Is, n))
    m = re.search(r"v\(out\)\s*=\s*([-\d.eE+]+)", txt)
    if not m:
        raise RuntimeError("could not parse ngspice output:\n" + txt)
    return float(m.group(1))


def _selftest():
    ok = True

    def chk(name, cond, extra=""):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + extra) if extra else ''}")
        ok = ok and cond

    have_ng = ngspice_available()
    print(f"backends: Newton=yes  bisection=yes  ngspice={'yes' if have_ng else 'no'}")
    op = diode_series_op(5.0, 1000.0)
    chk("Newton KCL residual ~ 0", abs(op["residual"]) < 1e-12,
        f"vd={op['vd']:.6f} V, id={op['id']*1e3:.4f} mA")
    vb = _bisection_vd(5.0, 1000.0)
    chk("Newton == independent bisection", abs(op["vd"] - vb) < 1e-9, f"{op['vd']:.9f} vs {vb:.9f}")
    chk("vd in a physical diode-drop range", 0.5 < op["vd"] < 0.8)
    if have_ng:
        try:
            vg = ngspice_diode_vd(5.0, 1000.0)
            chk("ngspice vd matches Newton (2 mV)", abs(vg - op["vd"]) < 2e-3, f"{vg:.6f} V")
        except Exception as e:
            chk("ngspice cross-check", False, str(e))
    print("circuits_nl selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI nonlinear circuits (diode)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--spice", action="store_true")
    a = ap.parse_args()
    if a.spice:
        print(diode_series_netlist()); raise SystemExit(0)
    raise SystemExit(_selftest())
