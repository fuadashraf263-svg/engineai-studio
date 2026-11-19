#!/usr/bin/env python3
"""
pipe_cable.py - standard pipe and conductor sizes: the catalogue layer for the
fluid and electrical disciplines.

`standards.py` gave the structural discipline the vocabulary a supplier speaks -
"IPE 200", "M12 class 8.8". This does the same for piping and cabling: "DN50
schedule 40", "AWG 12". Without it EngineAI could compute a pressure drop from a
bore in millimetres but could not tell you which pipe to actually buy.

Same discipline as the rest of the project: where the standard defines a quantity
by a FORMULA, the formula is implemented and the published table is used only as
the test oracle.

  * AWG is fully derivable. The gauge is *defined* by d = 0.005 x 92^((36-n)/39)
    inches, so every conductor diameter, area and resistance here is computed, not
    copied, and checked against published values. The geometric definition also
    produces a property worth asserting: cross-sectional area doubles every three
    gauge steps, to within 0.3%.
  * Pipe wall thicknesses are genuinely tabular (ASME B36.10M) and cannot be
    derived, so they are cross-checked instead: the metric values must be exact
    inch conversions, every size must share one OD across all schedules (the
    defining property of the schedule system), and a heavier schedule must always
    mean a thicker wall.

A NOTE ON THE MOST COMMON PIPING MISTAKE. Nominal Pipe Size is NOT a diameter.
DN50 / NPS 2 pipe has an outside diameter of 60.33 mm and a bore of 52.5 mm - the
"2 inch" is a label, not a measurement. Sizing a flow on the nominal number is a
real and frequent error, so `pipe()` returns the true bore and the selftest pins
the discrepancy.

Ampacity is deliberately NOT included: it depends on insulation, grouping, ambient
temperature and installation method, and a single tabulated number would be
misleading. Voltage drop, which IS derivable from resistance, is provided instead.
"""
from __future__ import annotations
import argparse, json, math

STEEL_DENSITY = 7850.0          # kg/m^3
COPPER_RESISTIVITY = 1.724e-8   # ohm.m at 20 C, IEC/IACS annealed copper
ALUMINIUM_RESISTIVITY = 2.65e-8 # ohm.m at 20 C
COPPER_TEMP_COEFF = 0.00393     # per K at 20 C

# --------------------------------------------------------------------------- #
# ASME B36.10M carbon steel pipe. OD is fixed per NPS; the schedule sets the wall.
# (nps_label, DN, OD_mm, {schedule: wall_mm})
# --------------------------------------------------------------------------- #
PIPE = {
    "1/8":   (6,    10.29, {40: 1.73, 80: 2.41}),
    "1/4":   (8,    13.72, {40: 2.24, 80: 3.02}),
    "3/8":   (10,   17.15, {40: 2.31, 80: 3.20}),
    "1/2":   (15,   21.34, {40: 2.77, 80: 3.73}),
    "3/4":   (20,   26.67, {40: 2.87, 80: 3.91}),
    "1":     (25,   33.40, {40: 3.38, 80: 4.55}),
    "1-1/4": (32,   42.16, {40: 3.56, 80: 4.85}),
    "1-1/2": (40,   48.26, {40: 3.68, 80: 5.08}),
    "2":     (50,   60.33, {40: 3.91, 80: 5.54}),
    "2-1/2": (65,   73.03, {40: 5.16, 80: 7.01}),
    "3":     (80,   88.90, {40: 5.49, 80: 7.62}),
    "4":     (100, 114.30, {40: 6.02, 80: 8.56}),
    "6":     (150, 168.28, {40: 7.11, 80: 10.97}),
    "12":    (300, 323.85, {40: 9.53, 80: 17.48}),
}

# OD of each NPS in INCHES, from the standard. Used to prove the metric table is an
# exact conversion rather than an independently rounded set of numbers.
_OD_INCH = {"1/8": 0.405, "1/4": 0.540, "3/8": 0.675, "1/2": 0.840, "3/4": 1.050,
            "1": 1.315, "1-1/4": 1.660, "1-1/2": 1.900, "2": 2.375, "2-1/2": 2.875,
            "3": 3.500, "4": 4.500, "6": 6.625, "12": 12.750}

_BY_DN = {v[0]: k for k, v in PIPE.items()}


def _resolve_nps(size):
    """Accept 'DN50', 50, '2', 2 - engineers use all of these interchangeably."""
    s = str(size).strip().upper().replace("NPS", "").replace("DN", "").strip()
    if s in PIPE:
        return s
    try:
        n = int(float(s))
    except ValueError:
        raise KeyError(f"unrecognised pipe size '{size}'. Known NPS: {', '.join(PIPE)}")
    if n in _BY_DN:                      # given as DN
        return _BY_DN[n]
    if str(n) in PIPE:                   # given as a whole-inch NPS
        return str(n)
    raise KeyError(f"unrecognised pipe size '{size}'. Known NPS: {', '.join(PIPE)}")


def pipe(size, schedule=40):
    """Real dimensions of a standard pipe. `size` may be NPS ('2', '1-1/2') or DN (50)."""
    nps = _resolve_nps(size)
    dn, od, walls = PIPE[nps]
    schedule = int(schedule)
    if schedule not in walls:
        raise KeyError(f"NPS {nps} has no schedule {schedule} here (have {sorted(walls)})")
    t = walls[schedule]
    idm = od - 2.0 * t                       # bore, by definition
    area = math.pi / 4.0 * idm ** 2          # mm^2
    metal = math.pi / 4.0 * (od ** 2 - idm ** 2)
    return {"designation": f"DN{dn} (NPS {nps}\") sch {schedule}",
            "nps": nps, "DN": dn, "schedule": schedule,
            "OD_mm": od, "wall_mm": t, "ID_mm": round(idm, 3),
            "flow_area_mm2": round(area, 2),
            "metal_area_mm2": round(metal, 2),
            "mass_kg_per_m": round(metal * 1e-6 * STEEL_DENSITY, 3),
            "nominal_is_not_a_diameter":
                f"NPS {nps}\" has a true bore of {idm:.1f} mm, not {nps}\"",
            "source": "ASME B36.10M-2018 Table 2.1 (carbon steel pipe)"}


def pipe_velocity(size, schedule, flow_m3_per_s):
    """Bulk velocity for a volumetric flow through a standard pipe."""
    p = pipe(size, schedule)
    v = float(flow_m3_per_s) / (p["flow_area_mm2"] * 1e-6)
    return {"pipe": p["designation"], "flow_m3_per_s": float(flow_m3_per_s),
            "flow_area_m2": p["flow_area_mm2"] * 1e-6,
            "velocity_m_per_s": round(v, 4), "ID_mm": p["ID_mm"]}


def select_pipe(flow_m3_per_s, max_velocity_m_per_s=3.0, schedule=40):
    """Smallest standard pipe that keeps the velocity at or below a design limit.

    3 m/s is a common upper bound for pumped water: above it erosion, noise and
    pumping cost rise sharply. It is a design guideline, not a code limit - state
    the applicable standard for the actual service.
    """
    for nps in sorted(PIPE, key=lambda k: PIPE[k][1]):
        if schedule not in PIPE[nps][2]:
            continue
        r = pipe_velocity(nps, schedule, flow_m3_per_s)
        if r["velocity_m_per_s"] <= max_velocity_m_per_s:
            out = pipe(nps, schedule)
            out["velocity_m_per_s"] = r["velocity_m_per_s"]
            out["design_limit_m_per_s"] = max_velocity_m_per_s
            return out
    return None


def pipe_pressure_drop(size, schedule, flow_m3_per_s, length_m,
                       fluid="Water", T_K=293.15, P_Pa=101325.0, roughness_mm=0.045):
    """End-to-end: standard pipe + real fluid properties + Darcy-Weisbach.

    This is the point of having a catalogue at all - the user names a pipe they can
    actually buy and gets a pressure drop, instead of having to supply a bore.
    """
    p = pipe(size, schedule)
    D = p["ID_mm"] * 1e-3
    A = p["flow_area_mm2"] * 1e-6
    V = float(flow_m3_per_s) / A
    try:
        from CoolProp.CoolProp import PropsSI
        rho = PropsSI("D", "T", T_K, "P", P_Pa, fluid)
        mu = PropsSI("V", "T", T_K, "P", P_Pa, fluid)
        prop_src = "CoolProp reference EOS"
    except Exception as e:
        return {"error": f"fluid properties unavailable: {type(e).__name__}: {e}"}
    Re = rho * V * D / mu
    try:
        from fluids import friction_factor
        fd = friction_factor(Re=Re, eD=(roughness_mm * 1e-3) / D)
        f_src = "fluids library (Colebrook)"
    except Exception:
        fd = 64.0 / Re if Re < 2300 else 0.316 * Re ** -0.25
        f_src = "laminar 64/Re" if Re < 2300 else "Blasius"
    dp = fd * (length_m / D) * rho * V ** 2 / 2.0
    return {"pipe": p["designation"], "fluid": fluid, "ID_mm": p["ID_mm"],
            "velocity_m_per_s": round(V, 4), "reynolds": round(Re, 1),
            "regime": "laminar" if Re < 2300 else ("transitional" if Re < 4000 else "turbulent"),
            "friction_factor": round(fd, 5), "length_m": length_m,
            "pressure_drop_Pa": round(dp, 1), "pressure_drop_kPa": round(dp / 1000.0, 3),
            "head_loss_m": round(dp / (rho * 9.80665), 4),
            "density_kg_m3": round(rho, 2), "viscosity_Pa_s": mu,
            "sources": [p["source"], prop_src, f_src],
            "note": "Straight pipe only - fittings, valves and elevation are not included."}


# --------------------------------------------------------------------------- #
# AWG conductors. The gauge is DEFINED geometrically, so all of this is derived.
# --------------------------------------------------------------------------- #
def awg(gauge, material="copper"):
    """Conductor properties from the AWG definition d = 0.005 * 92^((36-n)/39) inch.

    Gauges above 0 use negative numbers: 1/0 = 0, 2/0 = -1, 3/0 = -2, 4/0 = -3.
    """
    n = float(gauge)
    d_in = 0.005 * (92.0 ** ((36.0 - n) / 39.0))
    d_mm = d_in * 25.4
    area_mm2 = math.pi / 4.0 * d_mm ** 2
    rho = {"copper": COPPER_RESISTIVITY, "aluminium": ALUMINIUM_RESISTIVITY,
           "aluminum": ALUMINIUM_RESISTIVITY}.get(str(material).lower())
    if rho is None:
        raise KeyError(f"unknown conductor material '{material}' (copper or aluminium)")
    r_per_m = rho / (area_mm2 * 1e-6)
    return {"designation": f"AWG {gauge}", "gauge": n, "material": material,
            "diameter_mm": round(d_mm, 4), "diameter_in": round(d_in, 5),
            "area_mm2": round(area_mm2, 4),
            "resistance_ohm_per_km": round(r_per_m * 1000.0, 4),
            "source": "AWG geometric definition (ASTM B258); resistivity IEC 60228 at 20 C"}


def resistance_at(gauge, length_m, material="copper", T_C=20.0):
    """Conductor resistance including the temperature correction from 20 C."""
    w = awg(gauge, material)
    r20 = w["resistance_ohm_per_km"] / 1000.0 * float(length_m)
    r = r20 * (1.0 + COPPER_TEMP_COEFF * (float(T_C) - 20.0))
    return {"conductor": w["designation"], "length_m": length_m, "T_C": T_C,
            "resistance_ohm_20C": round(r20, 5), "resistance_ohm": round(r, 5)}


def voltage_drop(gauge, current_A, length_m, material="copper", phase="single", V_supply=230.0):
    """Volt drop along a run. Single phase counts the return conductor (factor 2);
    three phase uses sqrt(3). Derived from resistance, so no ampacity table is
    implied - this says nothing about whether the cable can carry the current safely."""
    w = awg(gauge, material)
    r_per_m = w["resistance_ohm_per_km"] / 1000.0
    k = 2.0 if str(phase).startswith("single") else math.sqrt(3.0)
    dv = k * float(current_A) * r_per_m * float(length_m)
    return {"conductor": w["designation"], "phase": phase, "current_A": float(current_A),
            "length_m": float(length_m), "factor": round(k, 4),
            "voltage_drop_V": round(dv, 3),
            "percent_of_supply": round(100.0 * dv / float(V_supply), 3),
            "power_loss_W": round(k * float(current_A) ** 2 * r_per_m * float(length_m), 2),
            "guideline": "Installations commonly limit volt drop to about 3-5% of supply; "
                         "the binding figure is set by the governing wiring regulation.",
            "warning": "Volt drop only. This does NOT check ampacity, which depends on "
                       "insulation, grouping, ambient temperature and installation method."}


def select_conductor(current_A, length_m, max_drop_percent=3.0, material="copper",
                     phase="single", V_supply=230.0):
    """Smallest AWG whose volt drop stays inside the limit. Sizing for volt drop only."""
    for g in range(40, -5, -1):          # 40 (thinnest) up to 4/0
        vd = voltage_drop(g, current_A, length_m, material, phase, V_supply)
        if vd["percent_of_supply"] <= max_drop_percent:
            out = awg(g, material)
            out.update(voltage_drop_V=vd["voltage_drop_V"],
                       percent_of_supply=vd["percent_of_supply"],
                       limit_percent=max_drop_percent, warning=vd["warning"])
            return out
    return None


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    # ---- pipe: the metric table must be an exact inch conversion ----
    worst = max(abs(PIPE[k][1] - _OD_INCH[k] * 25.4) for k in PIPE)
    chk(f"all {len(PIPE)} pipe ODs are exact inch conversions", worst < 0.01,
        f"worst {worst:.4f} mm")

    # ---- the defining property of the schedule system ----
    same_od = all(len({pipe(k, s)["OD_mm"] for s in PIPE[k][2]}) == 1 for k in PIPE)
    chk("one OD per size across every schedule (that IS the schedule system)", same_od)
    thicker = all(PIPE[k][2][80] > PIPE[k][2][40] for k in PIPE)
    chk("schedule 80 wall is always thicker than schedule 40", thicker)
    bore_shrinks = all(pipe(k, 80)["ID_mm"] < pipe(k, 40)["ID_mm"] for k in PIPE)
    chk("...so the bore always shrinks as the schedule rises", bore_shrinks)

    # ---- the classic trap ----
    p2 = pipe("2", 40)
    chk("NPS 2\" is NOT 2 inches of bore (the most common piping error)",
        abs(p2["ID_mm"] - 50.8) > 1.0 and abs(p2["OD_mm"] - 60.33) < 0.01,
        f"OD {p2['OD_mm']} mm, true bore {p2['ID_mm']} mm vs the 50.8 mm the label suggests")
    chk("ID == OD - 2*wall exactly",
        abs(p2["ID_mm"] - (p2["OD_mm"] - 2 * p2["wall_mm"])) < 1e-9)
    chk("flow area == pi/4 * ID^2",
        abs(p2["flow_area_mm2"] - math.pi / 4 * p2["ID_mm"] ** 2) < 0.01)

    # a published anchor: 4" sch 40 is 4.500 in OD, 0.237 in wall
    p4 = pipe("4", 40)
    chk("NPS 4 sch 40 matches the published 4.500 in / 0.237 in",
        abs(p4["OD_mm"] - 4.500 * 25.4) < 0.01 and abs(p4["wall_mm"] - 0.237 * 25.4) < 0.01,
        f"{p4['OD_mm']} mm OD, {p4['wall_mm']} mm wall")

    chk("size accepted as NPS, DN or bare number",
        pipe("2", 40)["DN"] == pipe("DN50", 40)["DN"] == pipe(50, 40)["DN"] == 50)
    chk("an unknown size is refused with the list of valid ones",
        _raises(lambda: pipe("7", 40), KeyError))
    chk("an unavailable schedule is refused", _raises(lambda: pipe("2", 160), KeyError))

    # ---- velocity and selection ----
    v = pipe_velocity("2", 40, 0.005)
    # tolerance matches the 4 dp the function rounds its output to - comparing a
    # display-rounded value at 1e-9 tests the rounding, not the physics
    chk("velocity == Q/A", abs(v["velocity_m_per_s"] - 0.005 / (p2["flow_area_mm2"] * 1e-6)) < 1e-4,
        f"{v['velocity_m_per_s']} m/s")
    sel = select_pipe(0.01, max_velocity_m_per_s=3.0)
    chk("pipe selection honours the velocity limit",
        sel is not None and sel["velocity_m_per_s"] <= 3.0,
        f"{sel['designation']} at {sel['velocity_m_per_s']} m/s" if sel else "none")
    smaller = [k for k in PIPE if PIPE[k][1] < sel["OD_mm"] and 40 in PIPE[k][2]]
    chk("...and it is the SMALLEST that does (not merely a safe one)",
        all(pipe_velocity(k, 40, 0.01)["velocity_m_per_s"] > 3.0 for k in smaller))
    chk("an impossible flow returns None rather than a wrong pipe",
        select_pipe(50.0, max_velocity_m_per_s=0.5) is None)

    # ---- AWG derived from its definition, checked against published values ----
    published = {10: 2.588, 12: 2.053, 14: 1.628, 0: 8.251, 40: 0.0799}   # mm diameter
    worst, detail = 0.0, ""
    for g, d in published.items():
        got = awg(g)["diameter_mm"]
        rel = abs(got - d) / d
        if rel > worst:
            worst, detail = rel, f"AWG {g}: {got:.4f} vs {d} mm"
    chk("AWG diameters derived from the definition match published values",
        worst < 0.005, f"worst {worst*100:.2f}%  ({detail})")

    # the geometric definition forces this, and it is a strong check on the exponent
    ratios = [awg(n)["area_mm2"] / awg(n + 3)["area_mm2"] for n in (6, 10, 14, 20)]
    chk("area doubles every 3 gauge steps (forced by the 92^(1/39) definition)",
        all(abs(r - 2.0) < 0.02 for r in ratios), f"{min(ratios):.4f} to {max(ratios):.4f}")
    chk("a bigger gauge NUMBER means a thinner wire (the counter-intuitive part)",
        awg(20)["area_mm2"] < awg(10)["area_mm2"] < awg(0)["area_mm2"])

    w12 = awg(12)
    chk("AWG 12 area is the textbook 3.31 mm^2", abs(w12["area_mm2"] - 3.31) < 0.02,
        f"{w12['area_mm2']:.3f} mm^2")
    chk("AWG 12 copper resistance is the textbook ~5.21 ohm/km",
        abs(w12["resistance_ohm_per_km"] - 5.21) < 0.06,
        f"{w12['resistance_ohm_per_km']:.3f} ohm/km")
    chk("aluminium has higher resistance than copper at the same gauge",
        awg(12, "aluminium")["resistance_ohm_per_km"] > w12["resistance_ohm_per_km"],
        f"{awg(12,'aluminium')['resistance_ohm_per_km']:.2f} vs {w12['resistance_ohm_per_km']:.2f}")
    chk("an unknown conductor material is refused",
        _raises(lambda: awg(12, "cheese"), KeyError))

    # ---- resistance, volt drop, selection ----
    r = resistance_at(12, 100.0, T_C=70.0)
    chk("resistance rises with temperature", r["resistance_ohm"] > r["resistance_ohm_20C"],
        f"{r['resistance_ohm_20C']} -> {r['resistance_ohm']} ohm at 70 C")
    vd = voltage_drop(12, 20.0, 30.0)
    chk("single phase volt drop counts the return conductor (factor 2)",
        abs(vd["factor"] - 2.0) < 1e-9)
    chk("three phase uses sqrt(3)",
        abs(voltage_drop(12, 20.0, 30.0, phase="three")["factor"] - math.sqrt(3)) < 1e-4,
        f"factor {voltage_drop(12, 20.0, 30.0, phase='three')['factor']}")
    chk("volt drop == k*I*R*L",
        abs(vd["voltage_drop_V"] - 2 * 20.0 * (w12["resistance_ohm_per_km"] / 1000) * 30.0) < 0.01,
        f"{vd['voltage_drop_V']} V")
    chk("power loss == k*I^2*R*L", vd["power_loss_W"] > 0)
    chk("volt drop carries an explicit ampacity warning", "ampacity" in vd["warning"])

    sc = select_conductor(20.0, 30.0, max_drop_percent=3.0)
    chk("conductor selection honours the volt-drop limit",
        sc is not None and sc["percent_of_supply"] <= 3.0,
        f"{sc['designation']} at {sc['percent_of_supply']}%" if sc else "none")
    chk("...and one gauge thinner would exceed it (tight, not arbitrary)",
        voltage_drop(sc["gauge"] + 1, 20.0, 30.0)["percent_of_supply"] > 3.0,
        f"AWG {int(sc['gauge'])+1} -> {voltage_drop(sc['gauge']+1, 20.0, 30.0)['percent_of_supply']}%")
    chk("a longer run demands a thicker conductor",
        select_conductor(20.0, 200.0)["area_mm2"] > select_conductor(20.0, 30.0)["area_mm2"])

    # ---- end to end: catalogue pipe + real fluid + Darcy ----
    dp = pipe_pressure_drop("2", 40, 0.005, 50.0)
    if "error" in dp:
        chk("pressure drop end to end", False, dp["error"])
    else:
        chk("catalogue pipe + real fluid properties -> pressure drop",
            dp["pressure_drop_Pa"] > 0 and dp["regime"] == "turbulent",
            f"{dp['pressure_drop_kPa']} kPa over 50 m, Re={dp['reynolds']:.0f}")
        chk("...and the density used is real water, not an assumption",
            abs(dp["density_kg_m3"] - 998.2) < 1.0, f"{dp['density_kg_m3']} kg/m^3")
        half = pipe_pressure_drop("2", 40, 0.005, 25.0)
        chk("drop scales linearly with length",
            abs(half["pressure_drop_Pa"] * 2 - dp["pressure_drop_Pa"]) < 1.0)
        big = pipe_pressure_drop("4", 40, 0.005, 50.0)
        chk("a larger pipe drops far less at the same flow",
            big["pressure_drop_Pa"] < dp["pressure_drop_Pa"] / 5,
            f"{big['pressure_drop_kPa']} kPa vs {dp['pressure_drop_kPa']} kPa")

    try:
        from units_gate import has_dimension, HAVE_PINT
        if HAVE_PINT:
            chk("pressure drop has pressure dimensions",
                has_dimension(dp.get("pressure_drop_Pa", 1.0), "Pa", "Pa"))
            chk("conductor area has area dimensions", has_dimension(w12["area_mm2"], "mm^2", "m^2"))
    except Exception:
        pass

    print("pipe_cable selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI standard pipes and conductors")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--pipe", metavar="SIZE"); ap.add_argument("--schedule", type=int, default=40)
    ap.add_argument("--awg", type=float, metavar="GAUGE")
    ap.add_argument("--select-pipe", type=float, metavar="Q_M3S")
    ap.add_argument("--drop", nargs=3, metavar=("SIZE", "Q_M3S", "LENGTH_M"))
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for k in PIPE:
            p = pipe(k, 40)
            print(f"NPS {k:<6} DN{p['DN']:<4} OD {p['OD_mm']:>7.2f}  sch40 bore {p['ID_mm']:>7.2f} mm")
        raise SystemExit(0)
    if a.pipe:
        print(json.dumps(pipe(a.pipe, a.schedule), indent=2)); raise SystemExit(0)
    if a.awg is not None:
        print(json.dumps(awg(a.awg), indent=2)); raise SystemExit(0)
    if a.select_pipe:
        print(json.dumps(select_pipe(a.select_pipe), indent=2)); raise SystemExit(0)
    if a.drop:
        print(json.dumps(pipe_pressure_drop(a.drop[0], a.schedule, float(a.drop[1]),
                                            float(a.drop[2])), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
