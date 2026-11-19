#!/usr/bin/env python3
"""Materials screening companion for EngineAI.

This does not replace ``materials.py``.  That file is a handbook-style
library for nine named structural materials.  This companion supplies the
commands that were incorrectly used with it:

    python materials_discovery.py hea Al Co Cr Fe Ni
    python materials_discovery.py screen Ti Al V Fe Cr Nb Mo
    python materials_discovery.py fetch --formula TiAl

``hea`` and ``screen`` are inexpensive *heuristic* pre-screens.  They are
useful for prioritising compositions, but are never a substitute for CALPHAD,
DFT, experimental phase identification, or certified design allowables.
``fetch`` obtains real computed entries from Materials Project when the user
sets MP_API_KEY.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
from typing import Iterable


# Atomic weights (g/mol), metallic radii (pm), Pauling electronegativities,
# room-temperature pure-metal densities (g/cm^3), and valence-electron counts.
# Values are deliberately limited to the elements this small pre-screen can
# responsibly handle; add a source and validation before extending this table.
ELEMENTS: dict[str, dict[str, float]] = {
    "Al": {"mass": 26.982, "radius": 143, "chi": 1.61, "density": 2.70, "vec": 3},
    "Co": {"mass": 58.933, "radius": 125, "chi": 1.88, "density": 8.90, "vec": 9},
    "Cr": {"mass": 51.996, "radius": 128, "chi": 1.66, "density": 7.19, "vec": 6},
    "Cu": {"mass": 63.546, "radius": 128, "chi": 1.90, "density": 8.96, "vec": 11},
    "Fe": {"mass": 55.845, "radius": 126, "chi": 1.83, "density": 7.87, "vec": 8},
    "Hf": {"mass": 178.49, "radius": 159, "chi": 1.30, "density": 13.31, "vec": 4},
    "Mn": {"mass": 54.938, "radius": 127, "chi": 1.55, "density": 7.21, "vec": 7},
    "Mo": {"mass": 95.95, "radius": 139, "chi": 2.16, "density": 10.28, "vec": 6},
    "Nb": {"mass": 92.906, "radius": 146, "chi": 1.60, "density": 8.57, "vec": 5},
    "Ni": {"mass": 58.693, "radius": 124, "chi": 1.91, "density": 8.91, "vec": 10},
    "Ta": {"mass": 180.948, "radius": 146, "chi": 1.50, "density": 16.69, "vec": 5},
    "Ti": {"mass": 47.867, "radius": 147, "chi": 1.54, "density": 4.51, "vec": 4},
    "V": {"mass": 50.942, "radius": 134, "chi": 1.63, "density": 6.11, "vec": 5},
    "W": {"mass": 183.84, "radius": 139, "chi": 2.36, "density": 19.25, "vec": 6},
    "Zr": {"mass": 91.224, "radius": 160, "chi": 1.33, "density": 6.52, "vec": 4},
}

R_GAS = 8.314462618  # J/(mol K)


def symbols(values: Iterable[str]) -> list[str]:
    """Normalise element symbols and reject unknown/duplicate inputs."""
    result: list[str] = []
    for raw in values:
        item = raw.strip().capitalize()
        if item not in ELEMENTS:
            available = ", ".join(sorted(ELEMENTS))
            raise ValueError(f"unsupported element '{raw}'. Available: {available}")
        if item in result:
            raise ValueError(f"duplicate element '{item}'")
        result.append(item)
    if len(result) < 2:
        raise ValueError("provide at least two unique elements")
    return result


def composition(items: list[str]) -> dict[str, float]:
    return {item: 1.0 / len(items) for item in items}


def metrics(items: list[str]) -> dict[str, float | str]:
    """Calculate transparent, low-cost descriptors for equiatomic alloys."""
    fractions = composition(items)
    rbar = sum(fractions[e] * ELEMENTS[e]["radius"] for e in items)
    delta = 100.0 * math.sqrt(
        sum(fractions[e] * (1.0 - ELEMENTS[e]["radius"] / rbar) ** 2 for e in items)
    )
    vec = sum(fractions[e] * ELEMENTS[e]["vec"] for e in items)
    chi_mean = sum(fractions[e] * ELEMENTS[e]["chi"] for e in items)
    chi_sd = math.sqrt(sum(fractions[e] * (ELEMENTS[e]["chi"] - chi_mean) ** 2 for e in items))
    entropy = -R_GAS * sum(x * math.log(x) for x in fractions.values())
    mass = sum(fractions[e] * ELEMENTS[e]["mass"] for e in items)
    # Ideal specific-volume mixture.  It is an estimate, not an alloy density
    # measurement; ordered phases, porosity and processing can alter it.
    density = mass / sum(fractions[e] * ELEMENTS[e]["mass"] / ELEMENTS[e]["density"] for e in items)

    if vec >= 8.0:
        phase = "FCC-favoured by VEC rule of thumb"
    elif vec < 6.87:
        phase = "BCC-favoured by VEC rule of thumb"
    else:
        phase = "mixed FCC/BCC likely by VEC rule of thumb"

    # This ranks only for follow-up.  It intentionally does not use an
    # invented mixing-enthalpy table or claim a phase prediction.
    score = 100.0
    score -= 4.0 * max(0.0, delta - 6.6)
    score -= 30.0 * max(0.0, chi_sd - 0.15)
    score -= 2.0 * abs(vec - 7.5)
    if len(items) < 4:
        score -= 25.0

    return {
        "elements": "".join(items),
        "components": len(items),
        "config_entropy_j_mol_k": entropy,
        "config_entropy_over_R": entropy / R_GAS,
        "size_mismatch_percent": delta,
        "vec": vec,
        "electronegativity_sd": chi_sd,
        "ideal_density_g_cm3": density,
        "phase_rule_of_thumb": phase,
        "follow_up_priority_0_100": max(0.0, score),
    }


def print_report(data: dict[str, float | str]) -> None:
    print(f"\n  EQUIMOLAR PRE-SCREEN: {data['elements']}")
    print("  " + "-" * 60)
    print(f"  components                    {data['components']}")
    print(f"  configurational entropy        {data['config_entropy_j_mol_k']:.3f} J/(mol K)")
    print(f"  entropy / R                    {data['config_entropy_over_R']:.4f}")
    print(f"  atomic size mismatch, delta    {data['size_mismatch_percent']:.3f} %")
    print(f"  VEC                            {data['vec']:.3f}")
    print(f"  electronegativity SD           {data['electronegativity_sd']:.4f}")
    print(f"  ideal density estimate         {data['ideal_density_g_cm3']:.3f} g/cm3")
    print(f"  VEC phase indicator            {data['phase_rule_of_thumb']}")
    print(f"  follow-up priority             {data['follow_up_priority_0_100']:.1f}/100")
    print("\n  This is a heuristic pre-screen, not a materials prediction. Confirm")
    print("  with a CALPHAD/DFT workflow and experiment before selecting an alloy.\n")


def cmd_hea(args: argparse.Namespace) -> int:
    print_report(metrics(symbols(args.elements)))
    return 0


def cmd_screen(args: argparse.Namespace) -> int:
    items = symbols(args.elements)
    max_components = min(args.max_components, len(items))
    if args.min_components > max_components:
        raise ValueError("--min-components cannot exceed --max-components or element count")
    rows = [metrics(list(combo)) for n in range(args.min_components, max_components + 1)
            for combo in itertools.combinations(items, n)]
    rows.sort(key=lambda row: float(row["follow_up_priority_0_100"]), reverse=True)
    print("\n  HEURISTIC EQUIMOLAR FOLLOW-UP PRIORITIES")
    print("  composition          priority  delta %    VEC    density g/cm3  VEC indicator")
    print("  " + "-" * 93)
    for row in rows[:args.top]:
        indicator = str(row["phase_rule_of_thumb"]).split(" by ")[0]
        print(f"  {str(row['elements']):20s} {float(row['follow_up_priority_0_100']):8.1f}"
              f" {float(row['size_mismatch_percent']):8.3f} {float(row['vec']):6.2f}"
              f" {float(row['ideal_density_g_cm3']):14.3f}  {indicator}")
    print(f"\n  examined {len(rows)} combinations; showing {min(args.top, len(rows))}.")
    print("  Ranking is only for deciding what to investigate next, not a phase or")
    print("  performance prediction. Use `fetch --formula ...` for real MP entries.\n")
    return 0


def serialise(value):
    """Make pydantic/NumPy objects returned by mp-api safe for JSON output."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [serialise(item) for item in value]
    if isinstance(value, dict):
        return {str(k): serialise(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        return serialise(value.model_dump())
    if hasattr(value, "dict"):
        return serialise(value.dict())
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def cmd_fetch(args: argparse.Namespace) -> int:
    key = os.environ.get("MP_API_KEY")
    if not key:
        raise RuntimeError("MP_API_KEY is not set. Set a newly issued key in the current shell; do not paste it into source code.")
    try:
        from mp_api.client import MPRester
    except ImportError as exc:
        raise RuntimeError("mp-api is not installed. Run: python -m pip install mp-api") from exc

    fields = [
        "material_id", "formula_pretty", "formation_energy_per_atom",
        "energy_above_hull", "band_gap", "density", "is_stable", "symmetry",
    ]
    with MPRester(key) as mpr:
        # mp-api versions have accepted either a list or scalar here.  Prefer
        # the documented list form, then give a useful error if the provider
        # changes its client API again.
        docs = mpr.materials.summary.search(formula=[args.formula], fields=fields)
    records = [serialise(doc) for doc in docs]
    print(json.dumps({"formula_query": args.formula, "count": len(records), "records": records}, indent=2))
    return 0


def selftest() -> int:
    cantor = metrics(["Co", "Cr", "Fe", "Mn", "Ni"])
    refractory = metrics(["Nb", "Mo", "Ta", "W"])
    ok = (
        round(float(cantor["config_entropy_over_R"]), 3) == round(math.log(5), 3)
        and float(cantor["vec"]) == 8.0
        and float(refractory["vec"]) == 5.5
    )
    print("materials_discovery selftest:", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    hea = sub.add_parser("hea", help="report descriptors for an equiatomic candidate")
    hea.add_argument("elements", nargs="+", help="element symbols, e.g. Al Co Cr Fe Ni")
    hea.set_defaults(func=cmd_hea)
    screen = sub.add_parser("screen", help="rank equiatomic combinations for follow-up")
    screen.add_argument("elements", nargs="+", help="available element symbols")
    screen.add_argument("--min-components", type=int, default=4)
    screen.add_argument("--max-components", type=int, default=6)
    screen.add_argument("--top", type=int, default=15)
    screen.set_defaults(func=cmd_screen)
    fetch = sub.add_parser("fetch", help="query Materials Project computed entries")
    fetch.add_argument("--formula", required=True, help="formula, e.g. TiAl")
    fetch.set_defaults(func=cmd_fetch)
    parser.add_argument("--selftest", action="store_true", help="run a no-network smoke test")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.selftest:
            return selftest()
        if not getattr(args, "command", None):
            build_parser().print_help()
            return 2
        return args.func(args)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
