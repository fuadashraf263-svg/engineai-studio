#!/usr/bin/env python3
"""
circuits.py - EngineAI electronics module (SPICE netlists + verified solver).

Two backends, one source of truth:
  * a self-contained modified-nodal-analysis (MNA) solver (numpy) that computes
    DC operating points and AC transfer functions - verified against closed-form
    values in --selftest, and usable with no external tools;
  * real SPICE netlist export + an ngspice driver, so the SAME circuits run in
    ngspice when it is installed (and the netlists import into KiCad).

Run:
  python circuits.py --selftest
  python circuits.py --list
  python circuits.py --spice rc_lowpass
  python circuits.py --ngspice        # cross-check via ngspice if installed
"""
from __future__ import annotations
import argparse
import math
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np


class Circuit:
    """A linear circuit: resistors, capacitors, inductors, and V sources.
    Node "0" is ground. A node (+) is listed first for each element."""

    def __init__(self, name):
        self.name = name
        self.R = []
        self.C = []
        self.L = []
        self.V = []
        self._nodes = {"0"}

    def _n(self, *ns):
        self._nodes.update(ns)

    def add_R(self, a, b, val): self.R.append((a, b, val)); self._n(a, b); return self
    def add_C(self, a, b, val): self.C.append((a, b, val)); self._n(a, b); return self
    def add_L(self, a, b, val): self.L.append((a, b, val)); self._n(a, b); return self
    def add_V(self, a, b, dc=0.0, ac=0.0): self.V.append((a, b, dc, ac)); self._n(a, b); return self

    def _index(self):
        nodes = sorted(n for n in self._nodes if n != "0")
        return {n: i for i, n in enumerate(nodes)}, len(nodes)

    @staticmethod
    def _stamp(Y, idx, a, b, y):
        ia, ib = idx.get(a), idx.get(b)
        if ia is not None:
            Y[ia, ia] += y
        if ib is not None:
            Y[ib, ib] += y
        if ia is not None and ib is not None:
            Y[ia, ib] -= y
            Y[ib, ia] -= y

    def _solve(self, dtype, admittance):
        idx, n = self._index()
        m = len(self.V)
        A = np.zeros((n + m, n + m), dtype=dtype)
        z = np.zeros(n + m, dtype=dtype)
        Y = A[:n, :n]
        for a, b, val in self.R:
            self._stamp(Y, idx, a, b, admittance("R", val))
        for a, b, val in self.C:
            self._stamp(Y, idx, a, b, admittance("C", val))
        for a, b, val in self.L:
            self._stamp(Y, idx, a, b, admittance("L", val))
        for k, (a, b, dc, ac) in enumerate(self.V):
            ia, ib = idx.get(a), idx.get(b)
            if ia is not None:
                A[ia, n + k] += 1; A[n + k, ia] += 1
            if ib is not None:
                A[ib, n + k] -= 1; A[n + k, ib] -= 1
            z[n + k] = (dc if dtype is float else ac)
        x = np.linalg.solve(A, z)
        return {node: (x[idx[node]] if node != "0" else dtype(0)) for node in self._nodes}

    def solve_dc(self):
        def adm(kind, val):
            if kind == "R":
                return 1.0 / val
            return 0.0
        return self._solve(float, adm)

    def solve_ac(self, f):
        w = 2 * math.pi * f

        def adm(kind, val):
            if kind == "R":
                return 1.0 / val
            if kind == "C":
                return 1j * w * val
            return 1.0 / (1j * w * val)
        return self._solve(complex, adm)

    def _body(self):
        out = [f"* {self.name}"]
        for i, (a, b, v) in enumerate(self.R, 1): out.append(f"R{i} {a} {b} {v:g}")
        for i, (a, b, v) in enumerate(self.C, 1): out.append(f"C{i} {a} {b} {v:g}")
        for i, (a, b, v) in enumerate(self.L, 1): out.append(f"L{i} {a} {b} {v:g}")
        for i, (a, b, dc, ac) in enumerate(self.V, 1):
            out.append(f"V{i} {a} {b} DC {dc:g} AC {ac:g}")
        return "\n".join(out)

    def spice(self):
        return self._body() + "\n.end\n"

    def spice_control(self, ctrl):
        return self._body() + "\n.control\n" + ctrl + "\n.endc\n.end\n"


def voltage_divider(vin=5.0, r1=1000.0, r2=2000.0):
    c = Circuit("voltage_divider")
    c.add_V("in", "0", dc=vin, ac=vin); c.add_R("in", "out", r1); c.add_R("out", "0", r2)
    return c


def voltage_divider_vout(vin=5.0, r1=1000.0, r2=2000.0):
    return vin * r2 / (r1 + r2)


def rc_lowpass(r=1000.0, cap=1e-6, vin=1.0):
    c = Circuit("rc_lowpass")
    c.add_V("in", "0", ac=vin); c.add_R("in", "out", r); c.add_C("out", "0", cap)
    return c


def rc_lowpass_fc(r=1000.0, cap=1e-6):
    return 1.0 / (2 * math.pi * r * cap)


def rc_lowpass_mag(f, r=1000.0, cap=1e-6):
    fc = rc_lowpass_fc(r, cap)
    return 1.0 / math.sqrt(1 + (f / fc) ** 2)


def series_rlc(r=10.0, l=1e-3, cap=1e-6, vin=1.0):
    c = Circuit("series_rlc")
    c.add_V("in", "0", ac=vin); c.add_R("in", "a", r); c.add_L("a", "out", l); c.add_C("out", "0", cap)
    return c


def series_rlc_f0(l=1e-3, cap=1e-6):
    return 1.0 / (2 * math.pi * math.sqrt(l * cap))


def series_rlc_Q(r=10.0, l=1e-3, cap=1e-6):
    return (1.0 / r) * math.sqrt(l / cap)


CIRCUITS = {
    "voltage_divider": voltage_divider,
    "rc_lowpass": rc_lowpass,
    "series_rlc": series_rlc,
}


def ngspice_available():
    return shutil.which("ngspice") is not None


def _run_ngspice(netlist):
    fd, path = tempfile.mkstemp(suffix=".cir")
    with os.fdopen(fd, "w") as f:
        f.write(netlist)
    try:
        p = subprocess.run(["ngspice", "-b", path], capture_output=True, text=True, timeout=30)
        return p.stdout + p.stderr
    finally:
        os.unlink(path)


def ngspice_dc_vout(circuit, node="out"):
    txt = _run_ngspice(circuit.spice_control(f"op\nprint v({node})"))
    m = re.search(rf"v\({node}\)\s*=\s*([-\d.eE+]+)", txt)
    if not m:
        raise RuntimeError("could not parse ngspice DC output:\n" + txt)
    return float(m.group(1))


def ngspice_ac_mag(circuit, f, node="out"):
    txt = _run_ngspice(circuit.spice_control(f"ac lin 1 {f:g} {f:g}\nprint vm({node})"))
    m = re.search(rf"vm\({node}\)\s*=\s*([-\d.eE+]+)", txt)
    if not m:
        raise RuntimeError("could not parse ngspice AC output:\n" + txt)
    return float(m.group(1))


def _selftest():
    ok = True

    def chk(name, cond, extra=""):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + extra) if extra else ''}")
        ok = ok and cond

    have_ng = ngspice_available()
    print(f"backends: MNA=yes  ngspice={'yes' if have_ng else 'no (MNA only here)'}")

    v = voltage_divider(5.0, 1000.0, 2000.0).solve_dc()["out"]
    exact = voltage_divider_vout(5.0, 1000.0, 2000.0)
    chk("divider DC v(out)", abs(v - exact) < 1e-9, f"{v:.4f} vs {exact:.4f} V")

    r, cap = 1000.0, 1e-6
    fc = rc_lowpass_fc(r, cap)
    for f in (fc / 10, fc, fc * 10):
        mag = abs(rc_lowpass(r, cap).solve_ac(f)["out"])
        ref = rc_lowpass_mag(f, r, cap)
        chk(f"rc_lowpass |H| @ {f:8.2f} Hz", abs(mag - ref) < 1e-6, f"{mag:.5f} vs {ref:.5f}")
    at_fc = abs(rc_lowpass(r, cap).solve_ac(fc)["out"])
    chk("rc_lowpass = 1/sqrt(2) at fc", abs(at_fc - 0.70710678) < 1e-4, f"{at_fc:.5f}  (fc={fc:.2f} Hz)")

    r2, l, cp = 10.0, 1e-3, 1e-6
    f0, Q = series_rlc_f0(l, cp), series_rlc_Q(r2, l, cp)
    mag0 = abs(series_rlc(r2, l, cp).solve_ac(f0)["out"])
    chk("series_rlc |Vc| at f0 == Q", abs(mag0 - Q) / Q < 1e-3, f"{mag0:.3f} vs Q={Q:.3f}  (f0={f0:.1f} Hz)")

    if have_ng:
        try:
            vg = ngspice_dc_vout(voltage_divider(5.0, 1000.0, 2000.0))
            chk("ngspice divider v(out)", abs(vg - exact) < 1e-3, f"{vg:.4f} V")
            mg = ngspice_ac_mag(rc_lowpass(r, cap), fc)
            chk("ngspice rc_lowpass at fc", abs(mg - 0.70710678) < 1e-2, f"{mg:.4f}")
        except Exception as e:
            chk("ngspice cross-check", False, str(e))

    print("circuits selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI electronics (SPICE + MNA)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--spice", metavar="NAME")
    ap.add_argument("--ngspice", action="store_true")
    a = ap.parse_args()

    if a.list:
        for n in CIRCUITS:
            print(n)
        raise SystemExit(0)
    if a.spice:
        if a.spice not in CIRCUITS:
            raise SystemExit(f"unknown circuit '{a.spice}'. Known: {', '.join(CIRCUITS)}")
        print(CIRCUITS[a.spice]().spice())
        raise SystemExit(0)
    if a.ngspice and not ngspice_available():
        raise SystemExit("ngspice not found on PATH (install: sudo apt install ngspice)")
    raise SystemExit(_selftest())
