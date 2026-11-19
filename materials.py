#!/usr/bin/env python3
"""
materials.py -- the engineering materials library.

Until now every mission hardcoded E=71700, nu=0.33, density=2810 with no
label. Those are Al 7075-T6 numbers, but nothing in the system said so,
nothing stopped you typing 7175 by mistake, and no tool could reason about
"try titanium instead".

UNITS -- matched to engine_tools, not to a textbook:
    E                MPa          (CalculiX decks use N/mm^2)
    density_kg_m3    kg/m^3
    yield_MPa        MPa
    k_W_mK           W/(m K)
    cte_per_K        1/K
    T_max_K          practical service ceiling, not the melting point

HONEST LIMITS -- read these before trusting a number:
  * Nominal room-temperature (293 K) handbook values for the stated
    temper. Real stock varies with heat treatment, thickness and
    direction; wrought alloys are anisotropic and these are longitudinal.
  * Yield strengths are typical, NOT design minimums. A design allowable
    needs MMPDS/EN values and a knockdown factor.
  * CRITICAL FOR CRFCE: these are 293 K properties. At 65 K most metals
    gain 5-15% stiffness, gain yield strength, and LOSE thermal
    conductivity by a large factor. Austenitic steels and Hastelloy in
    particular change enough that a 293 K number will mislead you. The
    library flags every material whose cryogenic behaviour differs
    materially, and refuses to pretend it knows the 65 K value.
  * Additively manufactured versions of these alloys are NOT these
    alloys. AM 7075 is barely weldable; AM Ti-6Al-4V is anisotropic and
    porosity-sensitive. Flagged where relevant.

Run:  python materials.py                 # list
      python materials.py Al7075-T6       # detail
      python materials.py --compare Al7075-T6 Ti6Al4V
"""

from __future__ import annotations

import json
import sys

# name -> properties. Sources are standard handbook values (ASM, MMPDS
# class data); every entry carries its own caveats rather than one blanket
# disclaimer at the top of the file.
MATERIALS: dict[str, dict] = {
    "Al7075-T6": {
        "full_name": "Aluminium 7075-T6 (Al-Zn-Mg-Cu)",
        "E_MPa": 71700.0, "nu": 0.33, "density_kg_m3": 2810.0,
        "yield_MPa": 503.0, "uts_MPa": 572.0,
        "k_W_mK": 130.0, "cte_per_K": 23.6e-6, "T_max_K": 400.0,
        "notes": "PHOENIX default. Highest strength-to-weight of the common "
                 "aluminiums. Poor corrosion resistance and effectively "
                 "unweldable -- fasteners or bonding only.",
        "am_warning": "Very difficult to print: solidification cracking. "
                      "Use AlSi10Mg or Scalmalloy for AM lattices.",
        "cryo": "Gains ~5% stiffness and ~10% yield at 77 K; stays ductile. "
                "One of the safer alloys to extrapolate, but verify.",
    },
    "Al6061-T6": {
        "full_name": "Aluminium 6061-T6 (Al-Mg-Si)",
        "E_MPa": 68900.0, "nu": 0.33, "density_kg_m3": 2700.0,
        "yield_MPa": 276.0, "uts_MPa": 310.0,
        "k_W_mK": 167.0, "cte_per_K": 23.6e-6, "T_max_K": 420.0,
        "notes": "Weldable, corrosion-resistant, machinable. Half the yield "
                 "of 7075 -- the trade you make for weldability.",
        "am_warning": "Also crack-prone; AlSi10Mg is the printable analogue.",
        "cryo": "Well characterised to 4 K, widely used in cryostats.",
    },
    "AlSi10Mg-AM": {
        "full_name": "AlSi10Mg, laser powder-bed fusion, stress relieved",
        "E_MPa": 70000.0, "nu": 0.33, "density_kg_m3": 2670.0,
        "yield_MPa": 230.0, "uts_MPa": 350.0,
        "k_W_mK": 120.0, "cte_per_K": 21.0e-6, "T_max_K": 420.0,
        "notes": "The realistic choice for a PRINTED PHOENIX lattice. "
                 "Properties depend strongly on build direction and "
                 "post-processing; treat as indicative until you test "
                 "coupons from your own machine.",
        "am_warning": None,
        "cryo": "Sparse data. Do not extrapolate to 65 K.",
    },
    "Ti6Al4V": {
        "full_name": "Ti-6Al-4V, annealed (Grade 5)",
        "E_MPa": 113800.0, "nu": 0.342, "density_kg_m3": 4430.0,
        "yield_MPa": 880.0, "uts_MPa": 950.0,
        "k_W_mK": 6.7, "cte_per_K": 8.6e-6, "T_max_K": 700.0,
        "notes": "Best strength-to-weight above ~150 C. Thermal "
                 "conductivity is 20x WORSE than aluminium -- excellent if "
                 "you want thermal isolation, a serious problem if you "
                 "need to shed heat.",
        "am_warning": "Prints well, but anisotropic and porosity-sensitive; "
                      "HIP is usually required for fatigue-critical parts.",
        "cryo": "Strength rises substantially at 77 K, ductility falls. "
                "A standard cryogenic structural choice.",
    },
    "SS316L": {
        "full_name": "Stainless 316L, annealed",
        "E_MPa": 193000.0, "nu": 0.30, "density_kg_m3": 8000.0,
        "yield_MPa": 170.0, "uts_MPa": 485.0,
        "k_W_mK": 16.3, "cte_per_K": 16.0e-6, "T_max_K": 1100.0,
        "notes": "Corrosion resistant, weldable, non-magnetic in the "
                 "annealed state -- that last property matters for CRFCE.",
        "am_warning": "One of the easiest alloys to print.",
        "cryo": "Austenitic: retains ductility to 4 K and yield roughly "
                "TRIPLES. The 170 MPa above is badly wrong at 65 K -- look "
                "up the cryogenic value, do not scale it.",
    },
    "Inconel718": {
        "full_name": "Inconel 718, solution treated and aged",
        "E_MPa": 200000.0, "nu": 0.294, "density_kg_m3": 8190.0,
        "yield_MPa": 1035.0, "uts_MPa": 1275.0,
        "k_W_mK": 11.4, "cte_per_K": 13.0e-6, "T_max_K": 980.0,
        "notes": "Turbine-grade. The candidate for the PHOENIX recuperated "
                 "microturbine hot section.",
        "am_warning": "Prints well; aging heat treatment is mandatory.",
        "cryo": "Retains strength and ductility to 4 K.",
    },
    "Cu-OFHC": {
        "full_name": "Copper, oxygen-free high conductivity, annealed",
        "E_MPa": 110000.0, "nu": 0.34, "density_kg_m3": 8960.0,
        "yield_MPa": 70.0, "uts_MPa": 220.0,
        "k_W_mK": 391.0, "cte_per_K": 16.5e-6, "T_max_K": 500.0,
        "notes": "Structurally weak, thermally superb. CRFCE stabiliser and "
                 "thermal path material.",
        "am_warning": "Hard to print: high reflectivity and conductivity. "
                      "Green or IR lasers required.",
        "cryo": "Conductivity at 65 K depends ENORMOUSLY on purity (RRR). "
                "391 W/mK is the 293 K value; high-RRR copper can exceed "
                "1000 W/mK at 65 K. Use the RRR-specific curve.",
    },
    "Hastelloy-C276": {
        "full_name": "Hastelloy C-276 (REBCO tape substrate)",
        "E_MPa": 205000.0, "nu": 0.307, "density_kg_m3": 8890.0,
        "yield_MPa": 355.0, "uts_MPa": 790.0,
        "k_W_mK": 9.8, "cte_per_K": 11.2e-6, "T_max_K": 1000.0,
        "notes": "The substrate under REBCO tape -- directly relevant to "
                 "CRFCE. Its magnetic and thermal behaviour is why the "
                 "non-magnetic substrate variant cut your AC loss.",
        "am_warning": None,
        "cryo": "Low, weakly temperature-dependent conductivity. Verify "
                "against tape-manufacturer data, not generic alloy tables.",
    },
    "Steel4340": {
        "full_name": "AISI 4340, quenched and tempered",
        "E_MPa": 205000.0, "nu": 0.29, "density_kg_m3": 7850.0,
        "yield_MPa": 1620.0, "uts_MPa": 1760.0,
        "k_W_mK": 44.5, "cte_per_K": 12.3e-6, "T_max_K": 700.0,
        "notes": "High-strength structural steel. Values are for the "
                 "quenched-and-tempered condition; annealed 4340 yields "
                 "about 470 MPa. State the condition or the number is "
                 "meaningless.",
        "am_warning": "Printable but crack-sensitive; preheat required.",
        "cryo": "Ferritic: has a ductile-to-brittle transition. DO NOT use "
                "below its DBTT without impact data.",
    },
}


def get(name: str) -> dict:
    if name not in MATERIALS:
        close = [k for k in MATERIALS if name.lower() in k.lower()]
        raise KeyError(f"unknown material '{name}'"
                       + (f"; did you mean {close}?" if close else "")
                       + f"\navailable: {sorted(MATERIALS)}")
    return dict(MATERIALS[name], name=name)


def as_mission_parameters(name: str) -> dict:
    """The subset engine_tools actually consumes, in its units."""
    m = get(name)
    return {"E": m["E_MPa"], "nu": m["nu"],
            "density_kg_m3": m["density_kg_m3"]}


def specific_strength(name: str) -> float:
    """yield / density -- the number that decides an airframe. kN m / kg."""
    m = get(name)
    return m["yield_MPa"] * 1e6 / m["density_kg_m3"] / 1e3


def show(name: str) -> None:
    m = get(name)
    print(f"\n  {m['full_name']}")
    print("  " + "-" * 62)
    for k, lab, unit in [("E_MPa", "Young's modulus", "MPa"),
                         ("nu", "Poisson ratio", ""),
                         ("density_kg_m3", "density", "kg/m3"),
                         ("yield_MPa", "yield (typical)", "MPa"),
                         ("uts_MPa", "ultimate", "MPa"),
                         ("k_W_mK", "thermal conductivity", "W/m.K"),
                         ("cte_per_K", "CTE", "1/K"),
                         ("T_max_K", "service ceiling", "K")]:
        print(f"  {lab:24s} {m[k]:>12.6g} {unit}")
    print(f"  {'specific strength':24s} "
          f"{specific_strength(name):>12.1f} kN.m/kg")
    print(f"\n  {m['notes']}")
    if m.get("am_warning"):
        print(f"\n  ADDITIVE: {m['am_warning']}")
    print(f"\n  CRYOGENIC (65 K, CRFCE): {m['cryo']}")
    print("\n  All values are nominal 293 K, typical not minimum.")


def main() -> int:
    a = sys.argv[1:]
    if not a:
        print("\n  MATERIALS LIBRARY  (293 K nominal, typical values)\n")
        print(f"  {'name':16s} {'E MPa':>8s} {'rho':>7s} {'yield':>7s} "
              f"{'k':>7s} {'spec.str':>9s}")
        print("  " + "-" * 62)
        for n in sorted(MATERIALS, key=lambda x: -specific_strength(x)):
            m = MATERIALS[n]
            print(f"  {n:16s} {m['E_MPa']:8.0f} {m['density_kg_m3']:7.0f} "
                  f"{m['yield_MPa']:7.0f} {m['k_W_mK']:7.1f} "
                  f"{specific_strength(n):9.1f}")
        print("\n  sorted by specific strength (yield/density), kN.m/kg")
        print("  detail:  python materials.py <name>")
        return 0
    if a[0] == "--compare":
        for n in a[1:]:
            show(n)
        return 0
    if a[0] == "--json":
        print(json.dumps(MATERIALS, indent=2))
        return 0
    show(a[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
