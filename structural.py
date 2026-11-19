#!/usr/bin/env python3
"""
structural.py - EngineAI beam & section strength design, verified.

Closed-form section properties + beam bending/deflection + factor-of-safety vs a
material yield. Units: N, mm, MPa. Verified: section I vs closed forms, hollow ==
solid-minus-bore additivity, stress two ways (M/Z == M c/I), and beam deflection
vs an INDEPENDENT numerical double-integration of M(x)/EI. Sources: Roark's; Shigley;
Hibbeler. A checked design aid - NOT a certified stamp; a licensed engineer signs off.

SCOPE WARNING: this module checks BENDING STRESS ONLY. It does not check shear,
lateral-torsional buckling, web buckling or fatigue. For a real steel section that
omission is not conservative - a 12 m unrestrained IPE 300 buckles laterally at about
a FIFTH of the moment this module would report as acceptable. Use `steel_design.py`
(EN 1993-1-1) for any rolled steel member; use this one for simple solid sections
where LTB is not credible, or as the bending component of a fuller check.
"""
from __future__ import annotations
import argparse, json, math
import numpy as np


def rectangle(b, h):
    I = b * h ** 3 / 12.0
    return {"A": b * h, "I": I, "c": h / 2, "Z": I / (h / 2)}

def solid_circle(d):
    I = math.pi * d ** 4 / 64.0
    return {"A": math.pi * d ** 2 / 4, "I": I, "c": d / 2, "Z": I / (d / 2)}

def tube(do, di):
    I = math.pi * (do ** 4 - di ** 4) / 64.0
    return {"A": math.pi * (do ** 2 - di ** 2) / 4, "I": I, "c": do / 2, "Z": I / (do / 2)}

def ibeam(b, h, tw, tf):
    I = (b * h ** 3 - (b - tw) * (h - 2 * tf) ** 3) / 12.0
    return {"A": b * h - (b - tw) * (h - 2 * tf), "I": I, "c": h / 2, "Z": I / (h / 2)}

SECTIONS = {"rectangle": rectangle, "solid_circle": solid_circle, "tube": tube, "ibeam": ibeam}


def cantilever_point(P, L, E, I):
    return {"M": P * L, "defl": P * L ** 3 / (3 * E * I), "Mx": lambda x: -P * (L - x)}

def cantilever_udl(w, L, E, I):
    return {"M": w * L ** 2 / 2, "defl": w * L ** 4 / (8 * E * I), "Mx": lambda x: -w * (L - x) ** 2 / 2}

def simply_point(P, L, E, I):
    return {"M": P * L / 4, "defl": P * L ** 3 / (48 * E * I),
            "Mx": lambda x: (P / 2) * x if x <= L / 2 else (P / 2) * (L - x)}

def simply_udl(w, L, E, I):
    return {"M": w * L ** 2 / 8, "defl": 5 * w * L ** 4 / (384 * E * I),
            "Mx": lambda x: (w * L / 2) * x - w * x ** 2 / 2}

BEAMS = {"cantilever_point": cantilever_point, "cantilever_udl": cantilever_udl,
         "simply_point": simply_point, "simply_udl": simply_udl}


def _numeric_tip_deflection(Mx, L, E, I, n=40001):
    x = np.linspace(0, L, n)
    kappa = np.array([Mx(xi) for xi in x]) / (E * I)
    slope = np.concatenate([[0.0], np.cumsum((kappa[1:] + kappa[:-1]) / 2 * np.diff(x))])
    defl = np.concatenate([[0.0], np.cumsum((slope[1:] + slope[:-1]) / 2 * np.diff(x))])
    return abs(defl[-1])


def design_check(section, section_args, beam, load, L, E, yield_mpa, target_fos=1.5):
    props = SECTIONS[section](**section_args)
    res = BEAMS[beam](load, L, E, props["I"])
    sigma = abs(res["M"]) / props["Z"]
    fos = yield_mpa / sigma if sigma else float("inf")
    return {"section": section, "beam": beam, "I_mm4": round(props["I"], 3), "Z_mm3": round(props["Z"], 3),
            "M_Nmm": round(res["M"], 3), "sigma_MPa": round(sigma, 3), "deflection_mm": round(res["defl"], 5),
            "yield_MPa": yield_mpa, "factor_of_safety": round(fos, 3),
            "pass": fos >= target_fos, "target_fos": target_fos}


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    chk("rectangle I = b h^3/12", abs(rectangle(10, 20)["I"] - 10 * 20 ** 3 / 12) < 1e-9)
    t, so, si = tube(20, 10), solid_circle(20), solid_circle(10)
    chk("tube I == solid(do) - solid(di)", abs(t["I"] - (so["I"] - si["I"])) < 1e-6)
    sec = rectangle(20, 40); M = 1000.0 * 500.0
    chk("stress M/Z == M c / I", abs(M / sec["Z"] - M * sec["c"] / sec["I"]) < 1e-9)
    P, L, E = 1000.0, 500.0, 200000.0
    r = cantilever_point(P, L, E, sec["I"]); num = _numeric_tip_deflection(r["Mx"], L, E, sec["I"])
    chk("cantilever defl: closed form == numerical", abs(r["defl"] - num) / r["defl"] < 1e-3, f"{r['defl']:.4f} vs {num:.4f} mm")
    ru = cantilever_udl(2.0, L, E, sec["I"]); numu = _numeric_tip_deflection(ru["Mx"], L, E, sec["I"])
    chk("cantilever UDL defl: closed form == numerical", abs(ru["defl"] - numu) / ru["defl"] < 1e-3)
    chk("simply_point M == P L / 4", abs(simply_point(P, L, E, sec["I"])["M"] - P * L / 4) < 1e-9)
    chk("simply_udl M == w L^2 / 8", abs(simply_udl(2.0, L, E, sec["I"])["M"] - 2.0 * L ** 2 / 8) < 1e-9)
    import materials_db as mdb
    m = mdb.MATERIALS["AISI_1018_steel"]; y, Es = m["yield"]["value"], m["E"]["value"] * 1000.0
    rep = design_check("rectangle", {"b": 20, "h": 40}, "cantilever_point", 500.0, 300.0, Es, y)
    chk("end-to-end design check FoS sane", 0 < rep["factor_of_safety"] < 1e6 and rep["sigma_MPa"] > 0,
        f"sigma={rep['sigma_MPa']} MPa, FoS={rep['factor_of_safety']}")
    from units_gate import has_dimension, HAVE_PINT
    if HAVE_PINT:
        chk("stress has pressure dimensions", has_dimension(rep["sigma_MPa"], "MPa", "Pa"))
    print("structural selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI structural strength design")
    ap.add_argument("--selftest", action="store_true"); ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if a.demo:
        print(json.dumps(design_check("rectangle", {"b": 20, "h": 40}, "cantilever_point",
                                      500.0, 300.0, 200000.0, 370.0), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
