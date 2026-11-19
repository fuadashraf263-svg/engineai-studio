#!/usr/bin/env python3
"""EngineAI CalculiX verification benchmark: elastic cantilever deflection.

This is a *verification harness*, not a general certification tool.  It creates
a structured C3D8I solid mesh, constrains x=0, applies a distributed end force,
runs CalculiX, compares average end displacement to Euler-Bernoulli theory and
writes an evidence JSON file with inputs, solver status and relative error.

Usage:
  conda activate eng
  python engineai_fea_benchmark.py --selftest
  python engineai_fea_benchmark.py --run --out ~/engineai/out/fea_benchmark

Use the result to establish that the local solver chain runs.  It does not
validate a product, nonlinear material, contacts, buckling, fatigue, dynamic
loads, or a model made from an arbitrary STL.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Case:
    # SI units throughout.
    length: float = 1.2
    width: float = 0.05
    height: float = 0.10
    youngs_modulus: float = 210e9
    poisson: float = 0.30
    end_load: float = -2500.0  # N in global z
    nx: int = 24
    ny: int = 3
    nz: int = 4

    def validate(self) -> None:
        positive = (self.length, self.width, self.height, self.youngs_modulus, self.nx, self.ny, self.nz)
        if any(v <= 0 for v in positive):
            raise ValueError("length, section, modulus and mesh divisions must be positive")
        if not 0.0 <= self.poisson < 0.5:
            raise ValueError("Poisson ratio must be in [0, 0.5)")
        if self.nx < 2 or self.ny < 1 or self.nz < 1:
            raise ValueError("mesh must have nx>=2, ny>=1, nz>=1")


def node_id(i: int, j: int, k: int, c: Case) -> int:
    return 1 + i + (c.nx + 1) * (j + (c.ny + 1) * k)


def mesh(c: Case) -> tuple[dict[int, tuple[float, float, float]], list[tuple[int, tuple[int, ...]]], list[int], list[int]]:
    c.validate()
    nodes: dict[int, tuple[float, float, float]] = {}
    for k in range(c.nz + 1):
        z = -c.height / 2 + c.height * k / c.nz
        for j in range(c.ny + 1):
            y = -c.width / 2 + c.width * j / c.ny
            for i in range(c.nx + 1):
                x = c.length * i / c.nx
                nodes[node_id(i, j, k, c)] = (x, y, z)
    elements: list[tuple[int, tuple[int, ...]]] = []
    eid = 1
    for k in range(c.nz):
        for j in range(c.ny):
            for i in range(c.nx):
                # Abaqus/CalculiX C3D8I node order.
                conn = (
                    node_id(i, j, k, c), node_id(i + 1, j, k, c),
                    node_id(i + 1, j + 1, k, c), node_id(i, j + 1, k, c),
                    node_id(i, j, k + 1, c), node_id(i + 1, j, k + 1, c),
                    node_id(i + 1, j + 1, k + 1, c), node_id(i, j + 1, k + 1, c),
                )
                elements.append((eid, conn)); eid += 1
    fixed = [node_id(0, j, k, c) for k in range(c.nz + 1) for j in range(c.ny + 1)]
    tip = [node_id(c.nx, j, k, c) for k in range(c.nz + 1) for j in range(c.ny + 1)]
    return nodes, elements, fixed, tip


def inp_text(c: Case) -> str:
    nodes, elements, fixed, tip = mesh(c)
    def set_rows(ids: list[int]) -> list[str]:
        # CalculiX limits a data record to 16 comma-separated entries.  Keep
        # node-set records below that limit for meshes with many boundary nodes.
        return [", ".join(map(str, ids[start:start + 16])) for start in range(0, len(ids), 16)]

    lines = ["*HEADING", "EngineAI verification: linear elastic cantilever", "*NODE"]
    lines.extend(f"{nid}, {x:.12g}, {y:.12g}, {z:.12g}" for nid, (x, y, z) in nodes.items())
    lines.append("*ELEMENT, TYPE=C3D8I, ELSET=EALL")
    lines.extend(f"{eid}, " + ", ".join(map(str, conn)) for eid, conn in elements)
    lines.extend(["*NSET, NSET=FIXED", *set_rows(fixed), "*NSET, NSET=TIP", *set_rows(tip)])
    lines.extend([
        "*MATERIAL, NAME=ELASTIC", "*ELASTIC", f"{c.youngs_modulus:.12g}, {c.poisson:.12g}",
        "*SOLID SECTION, ELSET=EALL, MATERIAL=ELASTIC", ",",
        "*STEP", "*STATIC", "*BOUNDARY", "FIXED, 1, 3", "*CLOAD",
    ])
    nodal_force = c.end_load / len(tip)
    lines.extend(f"{nid}, 3, {nodal_force:.12g}" for nid in tip)
    lines.extend(["*NODE PRINT, NSET=TIP, FREQUENCY=1", "U", "*NODE FILE, NSET=TIP", "U", "*EL FILE", "S", "*END STEP", ""])
    return "\n".join(lines)


def analytic_tip_deflection(c: Case) -> float:
    inertia = c.width * c.height ** 3 / 12.0
    return c.end_load * c.length ** 3 / (3.0 * c.youngs_modulus * inertia)


def parse_tip_z(dat: Path, tip_ids: set[int]) -> float:
    """Extract final printed z-displacements for known tip nodes from .dat."""
    pattern = re.compile(r"^\s*(\d+)\s+([+-]?(?:\d+\.?\d*|\.\d+)(?:[Ee][+-]?\d+)?)\s+([+-]?(?:\d+\.?\d*|\.\d+)(?:[Ee][+-]?\d+)?)\s+([+-]?(?:\d+\.?\d*|\.\d+)(?:[Ee][+-]?\d+)?)\s*$")
    final_block: list[float] = []
    current: list[float] = []
    for raw in dat.read_text(errors="replace").splitlines():
        match = pattern.match(raw)
        if match and int(match.group(1)) in tip_ids:
            current.append(float(match.group(4)))
            if len(current) == len(tip_ids):
                final_block = current[:]
                current = []
    if not final_block:
        raise RuntimeError("could not find printed tip displacement rows in CalculiX .dat output")
    return sum(final_block) / len(final_block)


def ccx_version(executable: str) -> str:
    try:
        result = subprocess.run([executable, "-v"], capture_output=True, text=True, timeout=15)
        return (result.stdout + result.stderr).strip().splitlines()[0][:200]
    except Exception:
        return "unknown"


def run(case: Case, output: Path, ccx: str) -> dict[str, object]:
    case.validate()
    output.mkdir(parents=True, exist_ok=True)
    inp = output / "cantilever.inp"
    inp.write_text(inp_text(case), encoding="ascii")
    path = shutil.which(ccx) or ccx
    if not Path(path).exists() and shutil.which(ccx) is None:
        raise RuntimeError(f"CalculiX executable not found: {ccx}")
    completed = subprocess.run([str(path), "-i", inp.stem], cwd=output, capture_output=True, text=True, timeout=180)
    (output / "ccx.stdout.txt").write_text(completed.stdout, encoding="utf-8", errors="replace")
    (output / "ccx.stderr.txt").write_text(completed.stderr, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        raise RuntimeError(f"CalculiX returned {completed.returncode}; inspect ccx.stdout.txt and ccx.stderr.txt")
    dat = output / "cantilever.dat"
    if not dat.is_file():
        raise RuntimeError("CalculiX did not produce cantilever.dat")
    _, _, _, tip = mesh(case)
    fea = parse_tip_z(dat, set(tip))
    analytic = analytic_tip_deflection(case)
    report = {
        "schema": 1, "case": case.__dict__, "units": {"length": "m", "force": "N", "stress": "Pa"},
        "element_type": "C3D8I", "solver": {"executable": str(path), "version": ccx_version(str(path))},
        "analytic_tip_displacement_m": analytic, "fea_mean_tip_displacement_m": fea,
        "relative_error": abs(fea - analytic) / abs(analytic),
        "acceptable_for_this_coarse_smoke_test": abs(fea - analytic) / abs(analytic) <= 0.15,
        "artifacts": ["cantilever.inp", "cantilever.dat", "cantilever.frd", "ccx.stdout.txt", "ccx.stderr.txt"],
        "scope": "linear elastic cantilever verification only; not certification or validation of a product",
    }
    (output / "cantilever_benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def selftest() -> bool:
    c = Case()
    try:
        nodes, elements, fixed, tip = mesh(c)
        assert len(nodes) == (c.nx + 1) * (c.ny + 1) * (c.nz + 1)
        assert len(elements) == c.nx * c.ny * c.nz
        assert fixed and tip and not set(fixed).intersection(tip)
        text = inp_text(c)
        assert "*ELEMENT, TYPE=C3D8I" in text and "*BOUNDARY" in text and "*CLOAD" in text
        assert analytic_tip_deflection(c) < 0
        print("FEA benchmark selftest: PASSED")
        return True
    except Exception as exc:
        print(f"FEA benchmark selftest: FAILED: {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--out", type=Path, default=Path.home() / "engineai" / "out" / "fea_benchmark")
    parser.add_argument("--ccx", default="ccx")
    args = parser.parse_args()
    if args.selftest:
        return 0 if selftest() else 1
    if not args.run:
        parser.error("choose --selftest or --run")
    try:
        report = run(Case(), args.out.expanduser().resolve(), args.ccx)
    except Exception as exc:
        print(f"FEA benchmark: FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report["acceptable_for_this_coarse_smoke_test"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
