#!/usr/bin/env python3
"""
tolerance_stack.py - EngineAI 1D tolerance stack-up (worst-case + RSS), verified.

Given an assembly dimension chain, computes the gap nominal and its accumulated
tolerance by worst-case (sum of tolerances) and RSS (root-sum-square, statistical),
then cross-checks both against an independent Monte-Carlo simulation. A computed
engineering result - 1D linear stacks with symmetric tolerances; not 2D/3D
geometric stacks, MMC bonus tolerance, or certification.
"""
import argparse, json, math
import numpy as np


def stackup(chain):
    """chain: list of {name, nominal, tol, direction(+1/-1)}, symmetric +/- tol."""
    nom = sum(c["direction"] * c["nominal"] for c in chain)
    wc = sum(abs(c["tol"]) for c in chain)
    rss = math.sqrt(sum(c["tol"] ** 2 for c in chain))
    return {
        "nominal": nom,
        "worst_case": {"tol": wc, "min": nom - wc, "max": nom + wc},
        "rss": {"tol": rss, "sigma": rss / 3.0, "min": nom - rss, "max": nom + rss},
        "contributors": len(chain),
    }


def _montecarlo(chain, n=200000, seed=0):
    rng = np.random.default_rng(seed)
    nom = np.array([c["direction"] * c["nominal"] for c in chain])
    tol = np.array([c["tol"] for c in chain], dtype=float)
    dr = np.array([c["direction"] for c in chain], dtype=float)
    normal = rng.normal(0.0, tol / 3.0, size=(n, len(chain)))       # tol = 3 sigma
    gap_n = nom.sum() + (normal * dr).sum(axis=1)
    uni = rng.uniform(-tol, tol, size=(n, len(chain)))
    gap_u = nom.sum() + (uni * dr).sum(axis=1)
    return {"rss_sigma": float(gap_n.std()), "u_min": float(gap_u.min()), "u_max": float(gap_u.max())}


EXAMPLE = [
    {"name": "slot_width", "nominal": 40.0, "tol": 0.05, "direction": +1},
    {"name": "key_width",  "nominal": 39.8, "tol": 0.03, "direction": -1},
]


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    r = stackup(EXAMPLE)
    chk("nominal gap == 0.2", abs(r["nominal"] - 0.2) < 1e-9, f"{r['nominal']:.4f}")
    chk("worst-case tol == 0.08", abs(r["worst_case"]["tol"] - 0.08) < 1e-9, f"{r['worst_case']['tol']:.4f}")
    chk("RSS tol == sqrt(0.0034)", abs(r["rss"]["tol"] - math.sqrt(0.0034)) < 1e-9, f"{r['rss']['tol']:.5f}")
    chk("RSS tighter than worst-case", r["rss"]["tol"] < r["worst_case"]["tol"])
    s = stackup([{"name": "x", "nominal": 10, "tol": 0.02, "direction": 1}])
    chk("single contributor: WC == RSS", abs(s["worst_case"]["tol"] - s["rss"]["tol"]) < 1e-12)
    mc = _montecarlo(EXAMPLE)
    chk("MC sigma ~ RSS sigma (3%)", abs(mc["rss_sigma"] - r["rss"]["sigma"]) / r["rss"]["sigma"] < 0.03,
        f"{mc['rss_sigma']:.5f} vs {r['rss']['sigma']:.5f}")
    chk("worst-case bounds contain all uniform samples",
        mc["u_min"] >= r["worst_case"]["min"] - 1e-9 and mc["u_max"] <= r["worst_case"]["max"] + 1e-9,
        f"[{mc['u_min']:.4f},{mc['u_max']:.4f}] in [{r['worst_case']['min']:.4f},{r['worst_case']['max']:.4f}]")
    print("tolerance_stack selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI 1D tolerance stack-up")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--example", action="store_true")
    a = ap.parse_args()
    if a.example:
        print(json.dumps(stackup(EXAMPLE), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
