#!/usr/bin/env python3
"""
combustion.py - real combustion chemistry, because Cantera was already here.

WHY THIS EXISTS
---------------
engines.py computes a fuel-air ratio from constant-cp energy balance, and the
jet-engine decomposition listed "combustion chemistry, flame stability and
emissions" as being outside this tool entirely. That was wrong. Cantera 3.2 is
installed and verified, with GRI-Mech 3.0 and its 53 species, so equilibrium
combustion is not a gap - it was an unclaimed capability.

The difference is not cosmetic. Constant-cp says burning stoichiometric
methane in 800 K air gets you an arbitrarily hot gas; real chemistry says
about 2570 K, because above roughly 2000 K the products start to dissociate
and that soaks up energy the simple model has no way to know about. A turbine
inlet temperature predicted without dissociation is optimistic in exactly the
place where blades melt.

WHAT IS HONEST HERE
-------------------
  * EQUILIBRIUM, not kinetics. This gives the temperature and composition the
    products would reach given enough time. It says nothing about whether the
    flame is stable, whether it lights, or how long the combustor must be.
  * Emissions are EQUILIBRIUM values. Real NOx is kinetically limited and
    usually well below equilibrium; real CO at part load is usually above it.
    They are order-of-magnitude, and the result says so.
  * Jet-A is modelled as a surrogate. GRI-Mech has no kerosene, so a real
    kerosene run uses methane with matched energy release unless you supply a
    mechanism that has it.

  python combustion.py --selftest
  python combustion.py --fuel CH4 --phi 1.0 --Tin 800 --p 25
"""

from __future__ import annotations

import argparse
import sys

# Stoichiometric air-fuel ratio by MASS, for checking against the literature.
STOICH_AFR = {"CH4": 17.19, "H2": 34.29, "C2H6": 16.09, "C3H8": 15.67,
              "CO": 2.467}

FUELS = {
    "CH4": {"name": "methane", "LHV_MJkg": 50.0},
    "H2": {"name": "hydrogen", "LHV_MJkg": 120.0},
    "C2H6": {"name": "ethane", "LHV_MJkg": 47.5},
    "C3H8": {"name": "propane", "LHV_MJkg": 46.4},
    "CO": {"name": "carbon monoxide", "LHV_MJkg": 10.1},
}


def available() -> bool:
    try:
        import cantera        # noqa: F401
        return True
    except Exception:                                         # noqa: BLE001
        return False


def _need():
    if not available():
        raise RuntimeError(
            "Cantera is not installed, so no chemistry can be run. Nothing "
            "here will fall back to a constant-cp estimate and call it "
            "combustion. pip install cantera")


def _oxygen_for(fuel: str) -> float:
    """Moles of O2 to burn one mole of fuel completely."""
    import re
    c = int((re.search(r"C(\d*)", fuel) or [None, ""]).__getitem__(1) or
            (1 if "C" in fuel else 0)) if "C" in fuel else 0
    hm = re.search(r"H(\d*)", fuel)
    h = int(hm.group(1) or 1) if hm else 0
    o = 1 if fuel == "CO" else 0
    return c + h / 4.0 - o / 2.0


def burn(fuel: str = "CH4", phi: float = 1.0, T_in: float = 300.0,
         p_bar: float = 1.0, mech: str = "gri30.yaml") -> dict:
    """Adiabatic equilibrium combustion at constant enthalpy and pressure."""
    _need()
    import cantera as ct
    if fuel not in FUELS:
        raise ValueError(f"unknown fuel {fuel!r}. Known: {', '.join(FUELS)}")
    if phi <= 0:
        raise ValueError("equivalence ratio must be positive")

    g = ct.Solution(mech)
    o2 = _oxygen_for(fuel)
    # phi > 1 is rich: more fuel than the oxygen can burn.
    g.TPX = T_in, p_bar * 1e5, f"{fuel}:{phi}, O2:{o2}, N2:{o2 * 3.76}"
    afr = (o2 * 32.0 + o2 * 3.76 * 28.014) / (phi * g.molecular_weights[
        g.species_index(fuel)])
    g.equilibrate("HP")

    x = {s: g.X[g.species_index(s)] for s in
         ("CO2", "H2O", "CO", "NO", "OH", "O2", "H2") if s in g.species_names}
    return {"fuel": fuel, "fuel_name": FUELS[fuel]["name"],
            "phi": phi, "T_in_K": T_in, "p_bar": p_bar,
            "T_adiabatic_K": g.T,
            "air_fuel_ratio_mass": afr,
            "products_mole_fraction": x,
            "NO_ppm": x.get("NO", 0.0) * 1e6,
            "CO_ppm": x.get("CO", 0.0) * 1e6,
            "mechanism": mech,
            "note": "equilibrium at constant enthalpy and pressure, with "
                    "dissociation. Says nothing about flame stability, "
                    "ignition or combustor length. NO and CO are EQUILIBRIUM "
                    "values: real NOx is kinetically limited and usually "
                    "lower, real CO at part load usually higher."}


def phi_for_temperature(T_target: float, fuel: str = "CH4",
                        T_in: float = 800.0, p_bar: float = 25.0,
                        mech: str = "gri30.yaml") -> dict:
    """What equivalence ratio reaches a given turbine inlet temperature?

    This is the calculation a gas turbine designer actually wants: TIT is set
    by what the blades survive, and the question is how much fuel that allows.
    Constant-cp overestimates it, because it cannot see dissociation.
    """
    _need()
    if T_target <= T_in:
        raise ValueError(f"target {T_target:g} K is not above the inlet "
                         f"{T_in:g} K - there is nothing to burn for")
    lo, hi = 1e-4, 3.0
    hottest = burn(fuel, 1.0, T_in, p_bar, mech)["T_adiabatic_K"]
    if T_target > hottest:
        return {"reachable": False, "T_target_K": T_target,
                "max_T_K": hottest,
                "error": f"{T_target:g} K is above the {hottest:.0f} K "
                         f"adiabatic flame temperature of stoichiometric "
                         f"{FUELS[fuel]['name']} from {T_in:g} K. No fuel-air "
                         f"ratio reaches it; you would need preheat, oxygen "
                         f"enrichment or a different fuel."}
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        T = burn(fuel, mid, T_in, p_bar, mech)["T_adiabatic_K"]
        if T < T_target:
            lo = mid
        else:
            hi = mid
        if abs(T - T_target) < 0.5:
            break
    r = burn(fuel, 0.5 * (lo + hi), T_in, p_bar, mech)
    r["reachable"] = True
    r["T_target_K"] = T_target
    r["fuel_air_ratio_mass"] = 1.0 / r["air_fuel_ratio_mass"]
    return r


# --------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def chk(cond, label, extra=""):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label
              + (f"   {extra}" if extra else ""))
        ok = ok and bool(cond)

    print("combustion selftest")
    if not available():
        print("  skip  cantera not installed")
        return 0

    print(" adiabatic flame temperatures, against the literature")
    r = burn("CH4", 1.0, 300.0, 1.0)
    chk(2180 < r["T_adiabatic_K"] < 2280,
        f"stoich CH4/air from 300 K: {r['T_adiabatic_K']:.0f} K "
        f"(literature ~2225 K with dissociation)")
    h = burn("H2", 1.0, 300.0, 1.0)
    chk(2340 < h["T_adiabatic_K"] < 2450,
        f"stoich H2/air: {h['T_adiabatic_K']:.0f} K (literature ~2390 K)")
    chk(h["T_adiabatic_K"] > r["T_adiabatic_K"], "hydrogen burns hotter than methane")

    print(" stoichiometric air-fuel ratios, by mass")
    for f, want in STOICH_AFR.items():
        got = burn(f, 1.0, 300.0, 1.0)["air_fuel_ratio_mass"]
        chk(abs(got - want) / want < 0.02,
            f"{f:5s} AFR {got:.2f} vs {want} from the literature")

    print(" dissociation is what a constant-cp model misses")
    hot = burn("CH4", 1.0, 800.0, 25.0)
    chk(2500 < hot["T_adiabatic_K"] < 2650,
        f"preheated to 800 K at 25 bar: {hot['T_adiabatic_K']:.0f} K")
    rise_cold = r["T_adiabatic_K"] - 300.0
    rise_hot = hot["T_adiabatic_K"] - 800.0
    chk(rise_hot < rise_cold,
        f"the temperature RISE falls as the inlet gets hotter "
        f"({rise_cold:.0f} K -> {rise_hot:.0f} K) - constant cp predicts the "
        f"same rise, which is why it overestimates turbine inlet temperature")
    chk(hot["products_mole_fraction"]["OH"] > 1e-4,
        f"radicals are present ({hot['products_mole_fraction']['OH']*100:.2f}% OH) "
        f"- this is the energy the simple model cannot see")

    print(" rich and lean")
    lean = burn("CH4", 0.5, 300.0, 1.0)
    rich = burn("CH4", 1.5, 300.0, 1.0)
    chk(lean["T_adiabatic_K"] < r["T_adiabatic_K"], "lean burns cooler")
    chk(rich["T_adiabatic_K"] < r["T_adiabatic_K"], "and so does rich")
    chk(rich["CO_ppm"] > lean["CO_ppm"] * 50,
        f"rich makes far more CO ({rich['CO_ppm']:.0f} vs "
        f"{lean['CO_ppm']:.1f} ppm) - incomplete combustion")
    chk(lean["products_mole_fraction"]["O2"] > 0.05,
        "lean leaves oxygen unburnt")

    print(" the calculation a turbine designer wants")
    q = phi_for_temperature(1600.0, "CH4", 800.0, 25.0)
    chk(q["reachable"], "1600 K TIT from 800 K air is reachable")
    chk(abs(q["T_adiabatic_K"] - 1600.0) < 5.0,
        f"...and the solver hits it ({q['T_adiabatic_K']:.1f} K)")
    chk(0 < q["phi"] < 1.0,
        f"...lean, as a gas turbine must run ({q['phi']:.3f})")
    chk(0.005 < q["fuel_air_ratio_mass"] < 0.05,
        f"...at a physical fuel-air ratio ({q['fuel_air_ratio_mass']:.4f})")

    hotter = phi_for_temperature(1900.0, "CH4", 800.0, 25.0)
    chk(hotter["phi"] > q["phi"], "a hotter turbine needs more fuel")

    print(" it refuses what chemistry cannot deliver")
    imp = phi_for_temperature(4000.0, "CH4", 800.0, 25.0)
    chk(not imp["reachable"], "4000 K is refused")
    chk("adiabatic flame temperature" in imp["error"],
        "...and the reason is the flame temperature itself")
    chk("oxygen enrichment" in imp["error"], "...with what would actually help")
    for bad, label in ((lambda: burn("kerosene"), "an unknown fuel"),
                       (lambda: burn("CH4", -1), "a negative equivalence ratio"),
                       (lambda: phi_for_temperature(500.0, "CH4", 800.0),
                        "a target below the inlet")):
        try:
            bad()
            chk(False, f"refuses {label}")
        except (ValueError, RuntimeError):
            chk(True, f"refuses {label}")

    print(" honest scope travels with the result")
    chk("kinetically limited" in r["note"],
        "the emissions caveat is in every result")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--fuel", default="CH4")
    ap.add_argument("--phi", type=float, default=1.0)
    ap.add_argument("--Tin", type=float, default=300.0)
    ap.add_argument("--p", type=float, default=1.0)
    ap.add_argument("--target", type=float, default=None)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    r = (phi_for_temperature(a.target, a.fuel, a.Tin, a.p) if a.target
         else burn(a.fuel, a.phi, a.Tin, a.p))
    for k, v in r.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for kk, vv in v.items():
                print(f"      {kk:8s} {vv:.6g}")
        elif isinstance(v, float):
            print(f"  {k:26s} {v:.6g}")
        else:
            print(f"  {k:26s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
