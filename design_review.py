#!/usr/bin/env python3
"""
design_review.py - take somebody's design and find what is WRONG with it.

WHY THIS EXISTS
---------------
Everything else in this project answers a question. This one interrogates an
artefact. An engineer does not usually arrive with "what is the hoop stress" -
they arrive with a part they have already drawn and the real question, which is
"is this going to be alright, and if not, what do I change?"

Nothing here could do that. The workbench would build your solid and verify its
volume, and then say nothing at all about whether it was any good.

THE RULE THIS MODULE FOLLOWS
----------------------------
NEVER STOP AT "I CANNOT". A missing number is a question, not an exit. If the
load is unknown, every geometric and code check that does not need the load is
still run, the load-dependent ones are listed as PENDING with the one input
required, and the review is still delivered. A review that refuses because one
field was blank is worth nothing to the person holding the part.

Equally: NEVER INVENT THE MISSING NUMBER. An assumed load produces a factor of
safety that looks like an answer. Assumptions are permitted only where a
standard default genuinely exists (steel E = 210 GPa), and every one is
reported in the finding that used it.

WHAT A FINDING IS
-----------------
  severity  FAIL     a limit is exceeded; the design does not work as drawn
            WARN     inside the limit but with little margin, or a modelling
                     assumption is being strained
            PENDING  the check is real and cannot run yet; names the input
            PASS     checked and satisfactory
  check     the physics, named
  numbers   what was computed, with units
  fix       a concrete change, with the direction and rough size of it

Every FAIL and WARN carries a fix. A defect report with no remedy is half a
job.

  python design_review.py --selftest
  python design_review.py --part mounting_plate --load 4000 --material steel
"""

from __future__ import annotations

import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

FAIL, WARN, PENDING, PASS_ = "FAIL", "WARN", "PENDING", "PASS"
ORDER = {FAIL: 0, WARN: 1, PENDING: 2, PASS_: 3}

# Yield strength MPa, Young's modulus MPa, density kg/m3, max service temp C.
# Small and explicit on purpose: a review that silently guesses a material
# property is the thing this module exists to catch other people doing.
MATERIALS = {
    "steel (mild)": (250.0, 210000.0, 7850.0, 400.0),
    "steel (stainless 304)": (215.0, 193000.0, 8000.0, 800.0),
    "aluminium 6061-T6": (276.0, 68900.0, 2700.0, 170.0),
    "titanium Ti-6Al-4V": (880.0, 113800.0, 4430.0, 400.0),
    "cast iron": (130.0, 110000.0, 7200.0, 400.0),
    "ABS": (40.0, 2300.0, 1040.0, 80.0),
    "PLA": (50.0, 3500.0, 1240.0, 55.0),
    "nylon 6": (70.0, 2900.0, 1140.0, 100.0),
}


def finding(sev, check, detail, fix=None, numbers=None, assumed=None):
    return {"severity": sev, "check": check, "detail": detail, "fix": fix,
            "numbers": numbers or {}, "assumed": assumed or []}


# --------------------------------------------------------------------------
# geometry checks - these need NO load, so they always run
# --------------------------------------------------------------------------

def review_geometry(part: str, params: dict) -> list:
    """Manufacturability and modelling-validity checks on the shape alone."""
    out = []
    p = dict(params)

    if part == "mounting_plate":
        L, W, T = p.get("L", 0), p.get("W", 0), p.get("T", 0)
        hd, m = p.get("hole_d", 0), p.get("margin", 0)
        edge = m - hd / 2
        if edge <= 0:
            out.append(finding(
                FAIL, "hole edge distance",
                f"the hole breaks the plate edge: margin {m:g} mm is not more "
                f"than hole_d/2 = {hd/2:g} mm",
                f"increase margin above {hd/2:g} mm, or reduce hole_d",
                {"edge_mm": edge}))
        elif edge < hd:
            out.append(finding(
                WARN, "hole edge distance",
                f"only {edge:g} mm of material outboard of the hole, less than "
                f"one hole diameter. Tear-out governs long before the plate "
                f"section does",
                f"aim for margin >= 1.5 x hole_d = {1.5*hd:g} mm",
                {"edge_mm": edge, "edge_over_d": edge / hd if hd else 0}))
        else:
            out.append(finding(PASS_, "hole edge distance",
                               f"{edge:g} mm of material outboard of each hole",
                               numbers={"edge_mm": edge}))
        if T and min(L, W) / T > 80:
            out.append(finding(
                WARN, "plate slenderness",
                f"span/thickness = {min(L, W)/T:.0f}. A plate this thin is "
                f"governed by buckling and by how flat you can actually make "
                f"it, not by direct stress",
                "thicken the plate, add a stiffener, or accept it is a "
                "membrane and check it as one",
                {"span_over_t": min(L, W) / T}))

    if part == "flange":
        od, idia = p.get("od", 0), p.get("idia", 0)
        bc, bd, n = p.get("bc", 0), p.get("bolt_d", 0), int(p.get("n", 0) or 0)
        if bc and n:
            pitch = math.pi * bc / n
            if pitch < 3 * bd:
                out.append(finding(
                    FAIL if pitch < 2 * bd else WARN, "bolt pitch",
                    f"bolt spacing on the circle is {pitch:.1f} mm, only "
                    f"{pitch/bd:.1f} bolt diameters. There is no room for a "
                    f"spanner and the ligaments between holes are weak",
                    f"reduce n below {int(math.pi*bc/(3*bd))}, or raise bc "
                    f"above {3*bd*n/math.pi:.0f} mm",
                    {"pitch_mm": pitch, "pitch_over_d": pitch / bd}))
            else:
                out.append(finding(PASS_, "bolt pitch",
                                   f"{pitch:.1f} mm, {pitch/bd:.1f} diameters",
                                   numbers={"pitch_mm": pitch}))
        if od and bc and bd and (od - bc) / 2 < 1.5 * bd:
            out.append(finding(
                WARN, "flange outer ligament",
                f"only {(od-bc)/2:.1f} mm from bolt circle to the rim",
                f"increase od above {bc + 3*bd:.0f} mm",
                {"ligament_mm": (od - bc) / 2}))

    if part == "u_channel":
        w, h, t = p.get("w", 0), p.get("h", 0), p.get("t", 0)
        if t and w / t > 60:
            out.append(finding(
                WARN, "web slenderness",
                f"w/t = {w/t:.0f}; local buckling of the web will govern "
                f"before the section yields",
                "thicken the wall or add a lip to the free edge",
                {"w_over_t": w / t}))

    if part == "shaft":
        d1, d2 = p.get("d1", 0), p.get("d2", 0)
        if d1 and d2:
            big, small = max(d1, d2), min(d1, d2)
            if big / small > 1.5:
                out.append(finding(
                    WARN, "shoulder stress concentration",
                    f"diameter steps {small:g} -> {big:g} mm, ratio "
                    f"{big/small:.2f}. With a sharp corner Kt is roughly 2-3, "
                    f"and fatigue cracks start at shoulders",
                    f"add a fillet of at least {0.1*small:.1f} mm radius, or "
                    f"an undercut",
                    {"D_over_d": big / small}))
    return out


# --------------------------------------------------------------------------
# strength checks - need a load, and SAY so when they do not have one
# --------------------------------------------------------------------------

def review_strength(part: str, params: dict, duty: dict) -> list:
    out = []
    load = duty.get("load_N")
    mat = duty.get("material")
    span = duty.get("span_mm")

    props = MATERIALS.get(mat) if mat else None
    if mat and not props:
        out.append(finding(
            WARN, "material data",
            f"'{mat}' is not in the property table, so no strength check can "
            f"run. Nothing is being assumed in its place",
            f"use one of: {', '.join(sorted(MATERIALS))}"))

    if load is None:
        out.append(finding(
            PENDING, "bending stress and factor of safety",
            "the section can be checked as soon as there is a load. Nothing "
            "will be assumed: a factor of safety computed against an invented "
            "load reads exactly like a real one",
            "give the applied load in newtons"))
    if props is None:
        out.append(finding(
            PENDING, "yield check",
            "no material chosen, so there is no allowable stress to compare "
            "against",
            f"choose a material: {', '.join(sorted(MATERIALS))}"))

    if load is None or props is None:
        return out

    fy, E, rho, tmax = props
    p = dict(params)

    if part == "mounting_plate":
        L = span or p.get("L", 0)
        b, t = p.get("W", 0), p.get("T", 0)
        if L and b and t:
            Z = b * t ** 2 / 6.0
            M = load * L / 4.0                    # simply supported, central
            sigma = M / Z
            fos = fy / sigma if sigma else float("inf")
            sev = FAIL if fos < 1.0 else (WARN if fos < 1.5 else PASS_)
            out.append(finding(
                sev, "bending stress, simply supported with a central load",
                f"sigma = M/Z = {sigma:.1f} MPa against yield {fy:.0f} MPa, "
                f"so the factor of safety is {fos:.2f}",
                (f"thicken to {t*math.sqrt(max(fos,0.01) and (1.5/fos)):.1f} mm "
                 f"to reach FoS 1.5, or reduce the span"
                 if sev != PASS_ else None),
                {"M_Nmm": M, "Z_mm3": Z, "sigma_MPa": sigma, "FoS": fos},
                ["simply supported with a central point load - say otherwise "
                 "if it is built in or the load is distributed"]))
            I = b * t ** 3 / 12.0
            defl = load * L ** 3 / (48 * E * I)
            ratio = L / defl if defl else float("inf")
            out.append(finding(
                WARN if ratio < 250 else PASS_, "deflection",
                f"{defl:.2f} mm, which is span/{ratio:.0f}",
                ("stiffen it: deflection goes as t^3, so 25% more thickness "
                 "roughly halves it" if ratio < 250 else None),
                {"deflection_mm": defl, "span_over_delta": ratio},
                ["the conventional serviceability limit is span/250"]))
    return out


def review_thermal(duty: dict) -> list:
    out = []
    T = duty.get("service_C")
    mat = duty.get("material")
    props = MATERIALS.get(mat) if mat else None
    if T is None:
        return [finding(PENDING, "service temperature",
                        "no temperature given, so no thermal limit was checked",
                        "give the service temperature in Celsius")]
    if props:
        tmax = props[3]
        if T > tmax:
            out.append(finding(
                FAIL, "material service temperature",
                f"{T:g} C is above the {tmax:g} C limit for {mat}",
                f"change material, or cool the part below {tmax:g} C",
                {"service_C": T, "limit_C": tmax}))
        elif T > 0.8 * tmax:
            out.append(finding(
                WARN, "material service temperature",
                f"{T:g} C is within 20% of the {tmax:g} C limit; creep and "
                f"modulus loss start to matter here",
                "check creep, and derate the allowable stress",
                {"service_C": T, "limit_C": tmax}))
        else:
            out.append(finding(PASS_, "material service temperature",
                               f"{T:g} C against a {tmax:g} C limit"))
    return out


def review(part: str = None, params: dict = None, duty: dict = None,
           measured: dict = None) -> dict:
    """Review a design. Always returns findings - never an empty refusal."""
    params = dict(params or {})
    duty = dict(duty or {})
    if part and not params:
        try:
            import cad_parts
            params = dict(cad_parts.PARTS[part][2])
        except Exception:                                     # noqa: BLE001
            pass

    out = []
    if part:
        out += review_geometry(part, params)
        out += review_strength(part, params, duty)
    elif measured:
        # An uploaded solid: no parametric model, but the numbers still say
        # things. Refusing to look because it did not come from the registry
        # would be exactly the unhelpfulness this module is against.
        bb = measured.get("bbox_mm") or []
        if len(bb) == 3 and min(bb) > 0:
            if max(bb) / min(bb) > 50:
                out.append(finding(
                    WARN, "aspect ratio",
                    f"the bounding box is {max(bb)/min(bb):.0f}:1. Very slender "
                    f"parts are governed by buckling and by handling damage",
                    "check the slender direction for buckling"))
            else:
                out.append(finding(PASS_, "aspect ratio",
                                   f"{max(bb)/min(bb):.1f}:1"))
        v = measured.get("volume_mm3")
        mat = duty.get("material")
        if v and mat in MATERIALS:
            kg = v * 1e-9 * MATERIALS[mat][2]
            out.append(finding(PASS_, "mass",
                               f"{kg:.3f} kg in {mat}",
                               numbers={"mass_kg": kg}))
        out += review_strength("", {}, duty)
    out += review_thermal(duty)

    out.sort(key=lambda f: ORDER[f["severity"]])
    counts = {s: sum(1 for f in out if f["severity"] == s)
              for s in (FAIL, WARN, PENDING, PASS_)}
    if counts[FAIL]:
        verdict = (f"{counts[FAIL]} problem(s) that stop this working as drawn")
    elif counts[WARN]:
        verdict = f"no hard failures, {counts[WARN]} thing(s) worth changing"
    elif counts[PENDING] and not counts[PASS_]:
        verdict = "nothing could be checked yet - see what is needed below"
    else:
        verdict = "passes every check that could be run"
    return {"part": part, "params": params, "duty": duty,
            "findings": out, "counts": counts, "verdict": verdict,
            "needs": [f["fix"] for f in out if f["severity"] == PENDING]}


def as_text(r: dict) -> str:
    lines = [f"DESIGN REVIEW - {r['verdict']}"]
    if r.get("part"):
        lines.append(f"  {r['part']}: "
                     + ", ".join(f"{k}={v:g}" for k, v in r["params"].items()))
    lines.append("")
    for f in r["findings"]:
        lines.append(f"[{f['severity']:<7}] {f['check']}")
        lines.append(f"           {f['detail']}")
        if f.get("fix"):
            lines.append(f"    FIX -> {f['fix']}")
        for a in f.get("assumed", []):
            lines.append(f"    assumed: {a}")
    return "\n".join(lines)


# --------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def chk(cond, label, extra=""):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label
              + (f"   {extra}" if extra else ""))
        ok = ok and bool(cond)

    print("design_review selftest")

    print(" it always returns something, even knowing nothing")
    r = review("mounting_plate")
    chk(r["findings"], f"a bare part still gets {len(r['findings'])} findings")
    chk(any(f["severity"] == PENDING for f in r["findings"]),
        "...and the load-dependent checks are PENDING, not silently dropped")
    chk(all(f["fix"] for f in r["findings"] if f["severity"] == PENDING),
        "every PENDING says exactly what input would unblock it")
    chk(r["needs"], f"...collected into a needs list ({len(r['needs'])})")
    chk(any(f["severity"] == PASS_ for f in r["findings"]),
        "geometry checks that need no load DO run")

    print(" it finds real defects")
    bad = review("mounting_plate", {"L": 120, "W": 80, "T": 10,
                                    "hole_d": 20, "margin": 6})
    hole = next(f for f in bad["findings"] if f["check"] == "hole edge distance")
    chk(hole["severity"] == FAIL, f"a hole through the edge is a FAIL")
    chk("increase margin" in hole["fix"], f"...with a concrete fix ({hole['fix']})")

    tight = review("flange", {"od": 100, "idia": 40, "t": 12, "bc": 75,
                              "n": 24, "bolt_d": 9})
    pitch = next(f for f in tight["findings"] if f["check"] == "bolt pitch")
    chk(pitch["severity"] in (FAIL, WARN), "24 bolts on a 75 mm circle is flagged")
    chk("reduce n" in pitch["fix"], f"...and says how many ({pitch['fix'][:44]})")

    good = review("flange")
    gp = next(f for f in good["findings"] if f["check"] == "bolt pitch")
    chk(gp["severity"] == PASS_, "the registry default flange passes bolt pitch")

    step = review("shaft", {"d1": 40, "l1": 40, "d2": 10, "l2": 30})
    sc = [f for f in step["findings"] if "shoulder" in f["check"]]
    chk(sc and sc[0]["severity"] == WARN, "a 4:1 shaft step warns about Kt")
    chk("fillet" in sc[0]["fix"], "...and prescribes a fillet")

    print(" strength checks, once it has what it needs")
    weak = review("mounting_plate", {"L": 400, "W": 40, "T": 3},
                  {"load_N": 5000, "material": "steel (mild)", "span_mm": 400})
    bend = next(f for f in weak["findings"] if "bending" in f["check"])
    chk(bend["severity"] == FAIL, f"an overloaded plate FAILS ({bend['numbers']})")
    chk(bend["numbers"]["FoS"] < 1.0, "...on factor of safety")
    chk(bend["assumed"], "...and declares the support assumption it made")

    strong = review("mounting_plate", {"L": 200, "W": 80, "T": 25},
                    {"load_N": 500, "material": "steel (mild)", "span_mm": 200})
    b2 = next(f for f in strong["findings"] if "bending" in f["check"])
    chk(b2["severity"] == PASS_, f"a sound plate passes ({b2['numbers']['FoS']:.1f})")

    print(" it never invents a material property")
    unk = review("mounting_plate", {}, {"load_N": 1000, "material": "unobtainium"})
    chk(any(f["check"] == "material data" for f in unk["findings"]),
        "an unknown material is reported")
    chk(not any(f["check"].startswith("bending") and f["severity"] != PENDING
                for f in unk["findings"]),
        "...and no strength check runs on a guessed property")

    print(" thermal")
    hot = review("mounting_plate", {}, {"material": "ABS", "service_C": 200})
    t = next(f for f in hot["findings"] if "temperature" in f["check"])
    chk(t["severity"] == FAIL, "ABS at 200 C is a FAIL")
    chk("change material" in t["fix"], "...with a remedy")
    warm = review("mounting_plate", {}, {"material": "ABS", "service_C": 70})
    t2 = next(f for f in warm["findings"] if "temperature" in f["check"])
    chk(t2["severity"] == WARN, "70 C on ABS is a warning, not a failure")

    print(" an uploaded solid with no parametric model")
    up = review(measured={"bbox_mm": [800.0, 6.0, 6.0], "volume_mm3": 28800.0},
                duty={"material": "aluminium 6061-T6"})
    chk(any("aspect" in f["check"] for f in up["findings"]),
        "a 133:1 bar is still reviewed, not refused for lacking a model")
    chk(any(f["check"] == "mass" for f in up["findings"]), "...and weighed")

    print(" the verdict line")
    chk("stop this working" in review("mounting_plate",
        {"L": 120, "W": 80, "T": 10, "hole_d": 20, "margin": 6})["verdict"],
        "a failing design says so at the top")
    chk("passes every check" in good["verdict"] or "worth changing" in good["verdict"],
        f"a sound one says that ({good['verdict']})")
    chk(len(as_text(bad)) > 200, "it renders to readable text")

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--part", default=None)
    ap.add_argument("--load", type=float, default=None)
    ap.add_argument("--span", type=float, default=None)
    ap.add_argument("--material", default=None)
    ap.add_argument("--temp", type=float, default=None)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.part:
        ap.print_help()
        return 0
    print(as_text(review(a.part, None,
                         {"load_N": a.load, "span_mm": a.span,
                          "material": a.material, "service_C": a.temp})))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
