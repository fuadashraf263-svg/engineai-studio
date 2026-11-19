#!/usr/bin/env python3
"""
engineai_fluids.py - EngineAI fluid-mechanics reference module.

Closed-form, source-cited incompressible pipe/channel-flow relations so that
"fluid mechanics" is no longer an empty discipline. Every function documents its
source and validity range. Verification-grade closed forms - a reference layer,
NOT a substitute for CFD or certified design.

Sources:
  [W]  F. M. White, "Fluid Mechanics", 7th ed. (McGraw-Hill).
  [CC] Cengel & Cimbala, "Fluid Mechanics: Fundamentals and Applications".
  [NIST] NIST reference values for water/air at 20 C, 1 atm.

Run:
  python engineai_fluids.py --selftest
  python engineai_fluids.py --demo
"""
from __future__ import annotations
import argparse
import math

FLUIDS = {
    "water": {"rho": 998.2, "mu": 1.002e-3, "nu": 1.004e-6, "src": "NIST/White 20C"},
    "air":   {"rho": 1.204, "mu": 1.825e-5, "nu": 1.516e-5, "src": "NIST/White 20C"},
}


def reynolds(U, D, *, nu=None, rho=None, mu=None):
    """Re = U*D/nu = rho*U*D/mu.  [W 6.2]"""
    if nu is not None:
        return U * D / nu
    if rho is not None and mu is not None:
        return rho * U * D / mu
    raise ValueError("supply nu, or both rho and mu")


def hydraulic_diameter(area, perimeter):
    """D_h = 4*A/P.  Parallel plates gap b -> D_h = 2b.  [W 6.8]"""
    return 4.0 * area / perimeter


def flow_regime(Re):
    """Internal-flow regime by Reynolds number.  [CC 8-3]"""
    if Re < 2300:
        return "laminar"
    if Re < 4000:
        return "transitional"
    return "turbulent"


def f_laminar(Re):
    """Darcy friction factor, laminar pipe: f = 64/Re.  [W 6.9]"""
    if Re <= 0:
        raise ValueError("Re must be > 0")
    return 64.0 / Re


def f_blasius(Re):
    """Blasius, smooth turbulent, 4e3 < Re < 1e5: f = 0.316 Re^-1/4.  [W 6.10]"""
    return 0.316 * Re ** -0.25


def f_haaland(Re, eps_over_D):
    """Haaland explicit approximation to Colebrook (within ~2%).  [W 6.48]"""
    return (-1.8 * math.log10((eps_over_D / 3.7) ** 1.11 + 6.9 / Re)) ** -2


def f_colebrook(Re, eps_over_D, tol=1e-12, itmax=100):
    """Darcy friction factor, turbulent, iterating Colebrook. [W 6.48]"""
    f = f_haaland(Re, eps_over_D)
    for _ in range(itmax):
        rhs = -2.0 * math.log10(eps_over_D / 3.7 + 2.51 / (Re * math.sqrt(f)))
        f_new = 1.0 / rhs ** 2
        if abs(f_new - f) < tol:
            return f_new
        f = f_new
    return f


def friction_factor(Re, eps_over_D=0.0):
    """Regime-aware Darcy friction factor."""
    if Re < 2300:
        return f_laminar(Re)
    return f_colebrook(Re, eps_over_D)


def pressure_drop(f, L, D, rho, U):
    """Darcy-Weisbach pressure drop: dP = f (L/D) (rho U^2 / 2) [Pa]. [W 6.7]"""
    return f * (L / D) * (rho * U ** 2 / 2.0)


def poiseuille_dp(mu, L, Q, D):
    """Hagen-Poiseuille: dP = 128 mu L Q / (pi D^4) [Pa]. [W 6.12]"""
    return 128.0 * mu * L * Q / (math.pi * D ** 4)


def entrance_length(Re, D):
    """Entrance length: laminar 0.05 Re D; turbulent ~4.4 Re^(1/6) D. [CC 8-1]"""
    if Re < 2300:
        return 0.05 * Re * D
    return 4.4 * Re ** (1.0 / 6.0) * D


def _selftest():
    ok = True

    def chk(name, cond, extra=""):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + extra) if extra else ''}")
        ok = ok and cond

    chk("laminar f = 64/Re", abs(f_laminar(2000) - 0.032) < 1e-9)
    fc = f_colebrook(1e5, 1e-3)
    fh = f_haaland(1e5, 1e-3)
    chk("Colebrook ~ Haaland (2%)", abs(fc - fh) / fc < 0.02, f"f={fc:.5f}")
    chk("smooth Colebrook ~ Blasius (4%)",
        abs(f_colebrook(1e5, 0.0) - f_blasius(1e5)) / f_blasius(1e5) < 0.04)
    rho, mu, U, D, L = 998.2, 1.002e-3, 0.5, 0.02, 3.0
    Re = reynolds(U, D, rho=rho, mu=mu)
    Q = U * math.pi * D ** 2 / 4.0
    dp_darcy = pressure_drop(f_laminar(Re), L, D, rho, U)
    dp_hp = poiseuille_dp(mu, L, Q, D)
    chk("Darcy == Hagen-Poiseuille", abs(dp_darcy - dp_hp) / dp_hp < 1e-9, f"{dp_darcy:.3f} Pa")
    Dh = 2 * 0.010
    Le = entrance_length(20, Dh)
    chk("entrance length ~20 mm (matches CFD case)", abs(Le - 0.020) < 1e-6, f"{Le * 1000:.1f} mm")

    print("fluids selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _demo():
    p = FLUIDS["water"]
    U, D, L, eps = 1.5, 0.05, 10.0, 0.045e-3
    Re = reynolds(U, D, nu=p["nu"])
    ff = friction_factor(Re, eps / D)
    dp = pressure_drop(ff, L, D, p["rho"], U)
    print(f"water, U={U} m/s, D={D} m, L={L} m, eps/D={eps / D:.2e}  [{p['src']}]")
    print(f"  Re={Re:.0f} ({flow_regime(Re)})  f={ff:.4f}  dP={dp:.1f} Pa  Le={entrance_length(Re, D):.3f} m")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI fluid-mechanics reference")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(_selftest())
    if a.demo:
        raise SystemExit(_demo())
    _selftest()
