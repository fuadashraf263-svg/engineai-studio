#!/usr/bin/env python3
"""
units_gate.py - EngineAI dimensional-analysis (unit-safety) gate.

Checks every engineering result for dimensional consistency BEFORE it is trusted,
using Pint. Rejects results whose units do not match the physical quantity asked
for, and catches formulas that combine quantities incorrectly - a whole class of
errors made impossible, not merely unlikely. Requires: pip install pint
"""
from __future__ import annotations

try:
    import pint
    _UREG = pint.UnitRegistry()
    HAVE_PINT = True
except Exception:
    HAVE_PINT = False


def q(value, unit):
    return _UREG.Quantity(value, unit)


def has_dimension(value, unit, expected_unit):
    """True if value*unit has the same dimensionality as expected_unit."""
    try:
        return _UREG.Quantity(value, unit).dimensionality == _UREG.Unit(expected_unit).dimensionality
    except Exception:
        return False


def evaluate_checked(fn):
    """Run a function that builds a Pint quantity. Pint raises on inconsistent
    combinations, so returns (False, error); otherwise (True, quantity)."""
    try:
        return True, fn()
    except Exception as e:
        return False, e


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    if not HAVE_PINT:
        print("  SKIP  pint not installed -> pip install pint"); return 1

    def darcy():
        f = q(0.02, ""); L = q(10, "m"); D = q(0.05, "m")
        rho = q(998, "kg/m^3"); U = q(1.5, "m/s")
        return f * (L / D) * (rho * U ** 2 / 2)
    good, val = evaluate_checked(darcy)
    chk("darcy formula evaluates", good)
    chk("result is a pressure", good and val.dimensionality == _UREG.Unit("Pa").dimensionality, str(val.to("Pa")))

    def wrong():
        return q(2, "kg") + q(9.81, "m/s^2")
    bad, err = evaluate_checked(wrong)
    chk("mass + acceleration is rejected", not bad, type(err).__name__)

    Re = q(998, "kg/m^3") * q(1.5, "m/s") * q(0.05, "m") / q(1.0e-3, "Pa*s")
    chk("Reynolds number is dimensionless", Re.dimensionless, f"{Re.to_base_units():~P}")

    chk("5061 Pa is a pressure", has_dimension(5061.6, "Pa", "Pa"))
    chk("5 m is NOT a pressure", not has_dimension(5, "m", "Pa"))
    print("units_gate selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
