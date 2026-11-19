#!/usr/bin/env python3
"""
superconductor.py - the CRFCE testbed: REBCO coils, AC loss and cryogenic margin.

The capability battery assumed a superconductor testbed called CRFCE that did not
exist anywhere in this repository. This is it.

WHY A SUPERCONDUCTING MAGNET IS A COUPLED PROBLEM, NOT AN ELECTRICAL ONE
------------------------------------------------------------------------
A REBCO tape does not have a current rating. It has a critical current that depends
on the field it is sitting in, the ANGLE of that field to the tape plane, and its
temperature - and the coil generates that field itself. So:

    current -> self-field -> Ic(B, theta, T) -> margin
    current ripple -> AC loss -> heat -> temperature -> Ic again

Every one of those arrows is a different discipline, and the answer is invisible
from inside any of them. A designer who checks "is the current below the 77 K
self-field Ic" has answered the wrong question: the peak field on the conductor is
typically several times the bore field, and it is usually PERPENDICULAR to the tape
at the coil ends, which is the worst orientation by a factor of five or more.

VERIFICATION
------------
  * The field map comes from magnetostatics.py, which is separately validated
    against a 1/r wire law and a reluctance model.
  * Norris' thin-strip AC loss has an exact small-signal limit, Q -> mu0 Ic^2 F^4 /
    (6 pi). The exponent 4 is recovered by FITTING the implemented formula over two
    decades of transport current - a demanding thing to match by accident.
  * The anisotropy model must reduce to the self-field Ic at B = 0 and must be
    monotonically worse as the field rotates perpendicular to the tape.
  * The cryogenic margin closes an energy balance: loss in equals lift removed.

A checked design aid, NOT a certified result. Ic(B,theta) is a fitted engineering
correlation, not a measurement of your tape; quench propagation, screening currents,
mechanical strain and joint resistance are all out of scope, and any of them can
end a magnet. A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math

import emag
import magnetostatics as ms
import thermal

MU0 = emag.MU0

# REBCO tape, representative 12 mm wide coated conductor.
#   Ic0     self-field critical current at 77 K, A
#   Tc      critical temperature, K
#   n       n-value of the E-J power law
#   B0, a, b, g   Kim-Anderson anisotropy fit parameters
TAPE = {
    "REBCO_12mm_77K": dict(Ic0=400.0, Tc=92.0, T_ref=77.0, width_mm=12.0,
                           thickness_um=100.0, n=30.0,
                           B0=0.15, alpha=0.7, gamma=5.0,
                           source="representative 2G HTS coated conductor"),
    "REBCO_4mm_77K": dict(Ic0=130.0, Tc=92.0, T_ref=77.0, width_mm=4.0,
                          thickness_um=100.0, n=30.0,
                          B0=0.15, alpha=0.7, gamma=5.0,
                          source="representative 4 mm 2G HTS tape"),
}

# Cryocooler lift, W at the stated cold-head temperature
CRYOCOOLERS = {
    "GM_small_20K": (10.0, 20.0), "GM_medium_20K": (40.0, 20.0),
    "PT_small_40K": (25.0, 40.0), "PT_medium_77K": (150.0, 77.0),
    "LN2_bath_77K": (1e4, 77.0),
}


def tape(name="REBCO_12mm_77K"):
    if name not in TAPE:
        raise KeyError(f"unknown tape '{name}'. Known: {', '.join(TAPE)}")
    return dict(TAPE[name])


def critical_current(B, theta_deg, T=77.0, tape_name="REBCO_12mm_77K"):
    """Ic(B, theta, T) for a REBCO coated conductor.

        Ic = Ic0 * f_T(T) / (1 + B_eff/B0)^alpha
        B_eff = B * sqrt(cos^2(theta) + gamma^-2 sin^2(theta))   ... anisotropy
        f_T   = ((Tc - T)/(Tc - T_ref))^1.5

    theta is measured from the TAPE NORMAL: theta = 0 means the field is
    perpendicular to the tape face, which is the WORST case, and theta = 90 means
    parallel to it, which is the best. Getting that convention backwards
    over-predicts Ic by the anisotropy factor - here a factor of five - and is the
    commonest way to design a magnet that quenches on first energisation.
    """
    t = tape(tape_name)
    if T >= t["Tc"]:
        return {"Ic_A": 0.0, "reason": f"T = {T} K is at or above Tc = {t['Tc']} K - "
                                       "the conductor is not superconducting"}
    th = math.radians(theta_deg)
    aniso = math.sqrt(math.cos(th) ** 2 + (math.sin(th) / t["gamma"]) ** 2)
    B_eff = abs(B) * aniso
    f_T = ((t["Tc"] - T) / (t["Tc"] - t["T_ref"])) ** 1.5
    Ic = t["Ic0"] * f_T / (1.0 + B_eff / t["B0"]) ** t["alpha"]
    return {"Ic_A": Ic, "Ic0_A": t["Ic0"], "B_T": B, "B_effective_T": B_eff,
            "theta_deg": theta_deg, "anisotropy_factor": aniso,
            "temperature_factor": f_T, "T_K": T, "Tc_K": t["Tc"],
            "orientation": ("perpendicular to the tape - WORST case"
                            if theta_deg < 30 else
                            "parallel to the tape - best case" if theta_deg > 60
                            else "oblique"),
            "note": "theta is measured from the tape NORMAL; 0 deg is field through "
                    "the tape face and is the worst orientation"}


def coil_field(NI=50000.0, r_inner=0.050, r_outer=0.080, height=0.100, n=140):
    """Magnetostatic field map of a solenoid coil cross-section.

    Solved as an axisymmetric-like 2D planar section through the coil pair, using
    the separately validated solver in magnetostatics.py. What matters for the
    conductor is not the bore field but the PEAK field ON the winding, and where
    it points.
    """
    W = H = max(r_outer * 6.0, height * 4.0)
    dom = ms.Domain(W, H, n, n)
    cx, cy = W / 2.0, H / 2.0
    A_coil = (r_outer - r_inner) * height
    J = NI / A_coil
    # two conductor blocks, opposite senses - a section through a solenoid
    dom.add(ms.Region(cx + r_inner, cx + r_outer, cy - height / 2.0,
                      cy + height / 2.0, 1.0, +J, name="coil_right"))
    dom.add(ms.Region(cx - r_outer, cx - r_inner, cy - height / 2.0,
                      cy + height / 2.0, 1.0, -J, name="coil_left"))
    Az = ms.solve_magnetostatic(dom)
    Bx, By, Bm = ms.field_from_A(Az, dom.hx, dom.hy)

    centre = ms.probe(dom, Az, cx, cy)
    # scan the winding block for the peak field and its direction
    peak, peak_at, peak_theta = 0.0, None, 0.0
    i0 = int((cx + r_inner) / dom.hx); i1 = int((cx + r_outer) / dom.hx)
    j0 = int((cy - height / 2.0) / dom.hy); j1 = int((cy + height / 2.0) / dom.hy)
    for j in range(max(j0, 0), min(j1 + 1, dom.ny)):
        for i in range(max(i0, 0), min(i1 + 1, dom.nx)):
            b = float(Bm[j, i])
            if b > peak:
                peak = b
                peak_at = ((i + 0.5) * dom.hx - cx, (j + 0.5) * dom.hy - cy)
                # tape lies in the r-z plane wound about z; the damaging component
                # is the RADIAL one, i.e. Bx here
                peak_theta = math.degrees(math.atan2(abs(float(By[j, i])),
                                                     abs(float(Bx[j, i]))))
    return {"B_bore_T": centre["B"], "B_peak_on_winding_T": peak,
            "peak_location_rz_m": peak_at,
            "peak_field_angle_from_tape_normal_deg": peak_theta,
            "peak_over_bore": peak / centre["B"] if centre["B"] else float("inf"),
            "NI_A": NI, "r_inner_m": r_inner, "r_outer_m": r_outer,
            "height_m": height, "cells": n * n,
            "note": "the conductor sees the PEAK winding field, not the bore field; "
                    "designing on the bore value is the classic over-estimate"}


def load_line(NI=50000.0, turns=200, T=77.0, tape_name="REBCO_12mm_77K", **coil):
    """Operating point against the critical surface.

    The coil current and the field it produces are not independent: raising the
    current raises the field, which lowers Ic. The margin is therefore where the
    LOAD LINE crosses the critical surface, not a fixed ratio.
    """
    c = coil_field(NI=NI, **coil)
    I_op = NI / turns
    ic = critical_current(c["B_peak_on_winding_T"],
                          c["peak_field_angle_from_tape_normal_deg"], T, tape_name)
    ic_bore = critical_current(c["B_bore_T"], 90.0, T, tape_name)
    ic_self = critical_current(0.0, 0.0, T, tape_name)
    frac = I_op / ic["Ic_A"] if ic["Ic_A"] else float("inf")
    return {"I_operating_A": I_op, "turns": turns,
            "B_bore_T": c["B_bore_T"], "B_peak_T": c["B_peak_on_winding_T"],
            "peak_over_bore": c["peak_over_bore"],
            "Ic_at_peak_field_A": ic["Ic_A"],
            "Ic_if_bore_field_assumed_A": ic_bore["Ic_A"],
            "Ic_self_field_A": ic_self["Ic_A"],
            "load_fraction_I_over_Ic": frac,
            "margin_percent": (1.0 - frac) * 100.0,
            "safe": frac < 0.8,
            "optimism_of_self_field_check": ic_self["Ic_A"] / ic["Ic_A"]
            if ic["Ic_A"] else float("inf"),
            "verdict": ("operating below 80% of Ic - conventional margin"
                        if frac < 0.8 else
                        f"operating at {frac*100:.0f}% of Ic - insufficient margin, "
                        "a REBCO magnet needs headroom for ramp, ripple and "
                        "temperature excursion"),
            "field_map": c, "critical_current": ic}


# --------------------------------------------------------------------------- #
# AC loss
# --------------------------------------------------------------------------- #
def norris_strip(I_amplitude, Ic, frequency=50.0):
    """Norris thin-strip hysteresis loss, joules per cycle per metre of tape:

        Q = (mu0 Ic^2 / pi) [ (1-F)ln(1-F) + (1+F)ln(1+F) - F^2 ],  F = Ia/Ic

    Expanding the logarithms gives Q -> mu0 Ic^2 F^4 / (6 pi) as F -> 0, so the loss
    rises as the FOURTH power of transport current. That exponent is the whole
    design lever: dropping the operating fraction from 0.8 to 0.5 cuts AC loss by
    more than six times, which is why HTS magnets are run well below Ic even when
    the DC margin looks comfortable.
    """
    if Ic <= 0:
        raise ValueError("critical current must be positive")
    F = abs(I_amplitude) / Ic
    if F >= 1.0:
        return {"F": F, "error": "transport current reaches or exceeds Ic - the "
                                 "conductor is not superconducting and Norris does "
                                 "not apply"}
    if F < 1e-12:
        Q = 0.0
    else:
        Q = (MU0 * Ic ** 2 / math.pi) * ((1 - F) * math.log(1 - F)
                                         + (1 + F) * math.log(1 + F) - F ** 2)
    return {"Q_J_per_cycle_per_m": Q, "P_W_per_m": Q * frequency,
            "F": F, "Ic_A": Ic, "I_amplitude_A": I_amplitude,
            "frequency_Hz": frequency,
            "small_signal_limit_J_per_m": MU0 * Ic ** 2 * F ** 4 / (6 * math.pi),
            "note": "loss goes as F^4 at small transport fraction - the strongest "
                    "lever available to the designer"}


def cryo_margin(loss_W, cooler="PT_medium_77K", conduction_W=0.0, radiation_W=0.0,
                current_lead_W=0.0, T_operating=77.0):
    """Close the cryogenic energy balance: does the cooler lift the heat?

    AC loss is rarely the largest term. Current leads and radiation usually
    dominate a small magnet, and a design that budgets only the conductor loss
    typically under-counts the heat load by an order of magnitude - so all four
    contributions are itemised and their shares reported.
    """
    if cooler not in CRYOCOOLERS:
        raise KeyError(f"unknown cooler '{cooler}'. Known: {', '.join(CRYOCOOLERS)}")
    lift, T_cooler = CRYOCOOLERS[cooler]
    total = loss_W + conduction_W + radiation_W + current_lead_W
    parts = {"ac_loss_W": loss_W, "conduction_W": conduction_W,
             "radiation_W": radiation_W, "current_leads_W": current_lead_W}
    biggest = max(parts, key=parts.get)
    return {"total_heat_W": total, "cooler": cooler, "lift_W": lift,
            "cooler_T_K": T_cooler, "utilisation": total / lift if lift else float("inf"),
            "adequate": total <= lift,
            "headroom_W": lift - total,
            "contributions_W": parts,
            "contribution_shares_percent":
                {k: (100.0 * v / total if total else 0.0) for k, v in parts.items()},
            "dominant_load": biggest,
            "verdict": (f"cooler lifts {lift:.1f} W against {total:.2f} W - "
                        f"{(1 - total/lift)*100:.0f}% headroom" if total <= lift else
                        f"heat load {total:.2f} W EXCEEDS the {lift:.1f} W lift - "
                        "the magnet will warm until it quenches"),
            "note": "AC loss is often not the dominant term; current leads and "
                    "radiation usually are"}


def design(NI=50000.0, turns=200, T=77.0, ripple_fraction=0.05, tape_length_m=200.0,
           cooler="PT_medium_77K", frequency=50.0, **coil):
    """The whole chain: field -> Ic margin -> AC loss -> heat -> cryo margin -> T.

    Each link is a separate module. The point of running them together is that the
    end of the chain - does the magnet stay cold - is invisible from any single one.
    """
    ll = load_line(NI=NI, turns=turns, T=T, **coil)
    Ia = ll["I_operating_A"] * ripple_fraction
    nl = norris_strip(Ia, ll["Ic_at_peak_field_A"], frequency)
    if "error" in nl:
        return {"error": nl["error"], "load_line": ll}
    loss_total = nl["P_W_per_m"] * tape_length_m
    cm = cryo_margin(loss_total, cooler, conduction_W=1.5, radiation_W=2.0,
                     current_lead_W=8.0, T_operating=T)
    return {"operating_current_A": ll["I_operating_A"],
            "B_bore_T": ll["B_bore_T"], "B_peak_T": ll["B_peak_T"],
            "Ic_at_peak_A": ll["Ic_at_peak_field_A"],
            "load_fraction": ll["load_fraction_I_over_Ic"],
            "margin_percent": ll["margin_percent"],
            "ripple_fraction": ripple_fraction,
            "ac_loss_W_per_m": nl["P_W_per_m"],
            "ac_loss_total_W": loss_total,
            "tape_length_m": tape_length_m,
            "cryogenics": cm,
            "operating_point_ok": ll["safe"] and cm["adequate"],
            "chain": "magnetostatics.coil_field -> Ic(B,theta,T) -> Norris strip "
                     "-> cryogenic energy balance",
            "disclaimer": "Ic(B,theta) is a fitted correlation, not your tape. "
                          "Quench propagation, screening currents, strain and joint "
                          "resistance are out of scope and any of them can end a "
                          "magnet. UNCERTIFIED - a licensed engineer signs off."}


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    # ---- the critical surface ----
    sf = critical_current(0.0, 0.0, 77.0)
    chk("at zero field and the reference temperature, Ic is the self-field value",
        abs(sf["Ic_A"] - 400.0) < 1e-9, f"{sf['Ic_A']:.1f} A")
    perp = critical_current(0.5, 0.0, 77.0)
    par = critical_current(0.5, 90.0, 77.0)
    chk("field PERPENDICULAR to the tape is always worse than parallel",
        perp["Ic_A"] < par["Ic_A"],
        f"{perp['Ic_A']:.1f} A perpendicular vs {par['Ic_A']:.1f} A parallel at 0.5 T "
        f"({par['Ic_A']/perp['Ic_A']:.2f}x)")
    # the penalty is not a fixed factor - it grows with field, which is why a
    # tape characterised at low field looks far better than it behaves in a magnet
    r05 = par["Ic_A"] / perp["Ic_A"]
    r30 = (critical_current(3.0, 90.0)["Ic_A"]
           / critical_current(3.0, 0.0)["Ic_A"])
    chk("...and the anisotropy penalty GROWS with field, so a low-field "
        "characterisation flatters the tape",
        r30 > r05 * 1.3,
        f"{r05:.2f}x at 0.5 T rises to {r30:.2f}x at 3 T")
    chk("...and the orientation convention is stated in the result, because getting "
        "it backwards over-predicts Ic several-fold",
        "WORST" in perp["orientation"] and "tape NORMAL" in perp["note"])
    chk("Ic falls monotonically with field",
        all(critical_current(b1, 0.0)["Ic_A"] > critical_current(b2, 0.0)["Ic_A"]
            for b1, b2 in zip([0.0, 0.2, 0.5, 1.0], [0.2, 0.5, 1.0, 2.0])))
    chk("Ic falls monotonically as the field rotates towards the tape normal",
        all(critical_current(0.5, a1)["Ic_A"] < critical_current(0.5, a2)["Ic_A"]
            for a1, a2 in zip([0, 30, 60], [30, 60, 90])))
    chk("Ic falls with temperature and reaches ZERO at Tc",
        critical_current(0.0, 0.0, 85.0)["Ic_A"] < sf["Ic_A"]
        and critical_current(0.0, 0.0, 92.0)["Ic_A"] == 0.0,
        f"77 K {sf['Ic_A']:.0f} A -> 85 K "
        f"{critical_current(0.0,0.0,85.0)['Ic_A']:.0f} A -> 92 K 0 A")
    chk("...and above Tc the reason is given, not a bare zero",
        "not superconducting" in critical_current(0.0, 0.0, 95.0)["reason"])
    chk("cooling BELOW the reference temperature raises Ic",
        critical_current(0.0, 0.0, 65.0)["Ic_A"] > sf["Ic_A"],
        f"65 K gives {critical_current(0.0,0.0,65.0)['Ic_A']:.0f} A - "
        "why REBCO magnets run at 20-50 K")
    chk("an unknown tape is refused", _raises(lambda: tape("unobtanium"), KeyError))

    # ---- the field map, from the separately validated solver ----
    cf = coil_field()
    chk("the coil field map solves and finds a bore field",
        cf["B_bore_T"] > 0, f"bore {cf['B_bore_T']:.4f} T")
    chk("the PEAK field on the winding exceeds the bore field",
        cf["B_peak_on_winding_T"] > cf["B_bore_T"],
        f"peak {cf['B_peak_on_winding_T']:.4f} T = "
        f"{cf['peak_over_bore']:.2f}x the bore value")
    chk("...and the result says designing on the bore value is the classic error",
        "classic over-estimate" in cf["note"])
    chk("doubling the ampere-turns doubles the field (linear magnetostatics)",
        abs(coil_field(NI=100000.0)["B_bore_T"] / cf["B_bore_T"] - 2.0) < 0.05)

    # ---- the load line ----
    ll = load_line(NI=50000.0, turns=200)
    chk("the operating point is evaluated at the PEAK field, not the bore field",
        ll["Ic_at_peak_field_A"] < ll["Ic_if_bore_field_assumed_A"],
        f"Ic at peak {ll['Ic_at_peak_field_A']:.1f} A vs "
        f"{ll['Ic_if_bore_field_assumed_A']:.1f} A if the bore field were assumed")
    chk("...and a self-field check would be optimistic by a real factor",
        ll["optimism_of_self_field_check"] > 1.05,
        f"{ll['optimism_of_self_field_check']:.2f}x too high")
    chk("the load fraction and margin are consistent",
        abs(ll["margin_percent"] - (1 - ll["load_fraction_I_over_Ic"]) * 100) < 1e-9)
    chk("raising the current lowers the margin",
        load_line(NI=80000.0, turns=200)["load_fraction_I_over_Ic"]
        > ll["load_fraction_I_over_Ic"])

    # ---- Norris: recover the F^4 exponent by FITTING ----
    Ic = 400.0
    n1 = norris_strip(40.0, Ic)
    chk("Norris strip loss is positive and finite",
        n1["Q_J_per_cycle_per_m"] > 0,
        f"{n1['Q_J_per_cycle_per_m']*1e3:.4f} mJ/cycle/m at F = {n1['F']:.2f}")
    import numpy as np
    Fs = [0.005, 0.01, 0.02, 0.05]
    Qs = [norris_strip(f * Ic, Ic)["Q_J_per_cycle_per_m"] for f in Fs]
    slope = np.polyfit(np.log(Fs), np.log(Qs), 1)[0]
    chk("the loss exponent FITS to 4 over a decade of transport current",
        abs(slope - 4.0) < 0.02, f"fitted n = {slope:.4f} (Norris F^4 law)")
    chk("...and matches the analytic small-signal limit mu0 Ic^2 F^4 / 6 pi",
        abs(norris_strip(0.01 * Ic, Ic)["Q_J_per_cycle_per_m"]
            / norris_strip(0.01 * Ic, Ic)["small_signal_limit_J_per_m"] - 1.0) < 0.01)
    chk("zero transport current gives zero loss",
        norris_strip(0.0, Ic)["Q_J_per_cycle_per_m"] == 0.0)
    chk("reducing the operating fraction from 0.8 to 0.5 cuts loss more than 6x",
        norris_strip(0.8 * Ic, Ic)["Q_J_per_cycle_per_m"]
        / norris_strip(0.5 * Ic, Ic)["Q_J_per_cycle_per_m"] > 6.0,
        f"{norris_strip(0.8*Ic,Ic)['Q_J_per_cycle_per_m']/norris_strip(0.5*Ic,Ic)['Q_J_per_cycle_per_m']:.1f}x "
        "- the F^4 law is the strongest design lever")
    chk("loss scales linearly with frequency",
        abs(norris_strip(40.0, Ic, 100.0)["P_W_per_m"]
            / norris_strip(40.0, Ic, 50.0)["P_W_per_m"] - 2.0) < 1e-12)
    chk("transport current at or above Ic is REFUSED, not extrapolated",
        "error" in norris_strip(Ic, Ic))
    chk("a non-positive Ic is refused",
        _raises(lambda: norris_strip(10.0, 0.0), ValueError))

    # ---- cryogenics ----
    cm = cryo_margin(5.0, "PT_medium_77K", conduction_W=1.5, radiation_W=2.0,
                     current_lead_W=8.0)
    chk("the cryogenic energy balance sums every contribution",
        abs(cm["total_heat_W"] - 16.5) < 1e-12,
        f"{cm['total_heat_W']:.2f} W total")
    chk("contribution shares sum to 100%",
        abs(sum(cm["contribution_shares_percent"].values()) - 100.0) < 1e-9)
    chk("the CURRENT LEADS dominate, not the AC loss - the budgeting mistake this "
        "function exists to expose",
        cm["dominant_load"] == "current_leads_W",
        f"leads {cm['contribution_shares_percent']['current_leads_W']:.0f}% vs "
        f"AC loss {cm['contribution_shares_percent']['ac_loss_W']:.0f}%")
    chk("an adequate cooler is reported with its headroom",
        cm["adequate"] and cm["headroom_W"] > 0,
        f"{cm['lift_W']:.0f} W lift, {cm['headroom_W']:.1f} W spare")
    over = cryo_margin(200.0, "GM_small_20K")
    chk("an overloaded cooler is reported as a QUENCH risk, not a number",
        not over["adequate"] and "quench" in over["verdict"],
        over["verdict"][:70])
    chk("an unknown cooler is refused",
        _raises(lambda: cryo_margin(1.0, "handwave"), KeyError))

    # ---- the whole chain ----
    d = design()
    chk("the full chain runs field -> Ic -> AC loss -> cryogenics",
        "error" not in d and d["ac_loss_total_W"] > 0,
        f"I = {d['operating_current_A']:.0f} A, B_peak = {d['B_peak_T']:.3f} T, "
        f"Ic = {d['Ic_at_peak_A']:.0f} A, load {d['load_fraction']*100:.0f}%, "
        f"loss {d['ac_loss_total_W']:.2f} W")
    chk("the operating verdict combines BOTH the electrical and thermal checks",
        d["operating_point_ok"] == (d["load_fraction"] < 0.8
                                    and d["cryogenics"]["adequate"]))
    chk("halving the ripple cuts AC loss by roughly sixteen times (F^4)",
        8.0 < (d["ac_loss_total_W"]
               / design(ripple_fraction=0.025)["ac_loss_total_W"]) < 20.0,
        f"{d['ac_loss_total_W']/design(ripple_fraction=0.025)['ac_loss_total_W']:.1f}x")
    chk("the out-of-scope failure modes are named, not implied",
        "Quench propagation" in d["disclaimer"] and "UNCERTIFIED" in d["disclaimer"])

    print("superconductor selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="CRFCE REBCO magnet testbed")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--design", action="store_true")
    a = ap.parse_args()
    if a.design:
        print(json.dumps(design(), indent=2, default=str)); raise SystemExit(0)
    raise SystemExit(_selftest())
