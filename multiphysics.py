#!/usr/bin/env python3
"""
multiphysics.py - chains where one physics drives another.

Every module in this repository answers inside one discipline. Real failures happen
between them: a busbar carries current, the current heats it, the heat expands it,
the expansion is restrained, and the stress cracks the joint. No single-discipline
tool sees that, and each one individually reports "fine".

Two kinds of coupling live here, and the difference matters:

  ONE-WAY (a chain). Wind pressure loads a plate; the plate's deflection does not
  change the wind. Solve in sequence, once.

  TWO-WAY (a loop). A resistor's temperature raises its resistance, which raises its
  dissipation, which raises its temperature. That must be ITERATED, and it can
  diverge - thermal runaway is not a numerical artefact, it is the physical answer.
  `joule_heating_coupled` detects that case and refuses to return a temperature,
  because a converged-looking number there would be a lie.

The runaway case has an exact closed form, derived below, and the selftest checks
the iteration against it - which is how you tell a converged solver from one that
merely stopped changing.

Units: SI, except where a chained module dictates otherwise; every function states
what it takes.

A checked design aid, NOT a certified result. A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math

import emag, thermal, mechanics, fatigue, fluid_mech

# EN 1993-1-2 Table 3.1 - reduction factors for structural steel at temperature.
# (T degC, k_E modulus factor, k_y effective yield factor)
STEEL_AT_TEMPERATURE = [
    (20, 1.00, 1.00), (100, 1.00, 1.00), (200, 0.90, 1.00), (300, 0.80, 1.00),
    (400, 0.70, 1.00), (500, 0.60, 0.78), (600, 0.31, 0.47), (700, 0.13, 0.23),
    (800, 0.09, 0.11), (900, 0.0675, 0.06), (1000, 0.045, 0.04),
]


def steel_at_temperature(T_celsius, E_20=210000.0, fy_20=355.0):
    """Linear interpolation of the EN 1993-1-2 knockdown factors.

    Steel keeps its full YIELD to 400 C but has already lost 30% of its MODULUS by
    then. That divergence is the reason a fire-exposed frame deflects long before it
    yields, and why a strength-only check misses it.
    """
    tab = STEEL_AT_TEMPERATURE
    if T_celsius <= tab[0][0]:
        kE, ky = tab[0][1], tab[0][2]
    elif T_celsius >= tab[-1][0]:
        kE, ky = tab[-1][1], tab[-1][2]
    else:
        for (t1, e1, y1), (t2, e2, y2) in zip(tab, tab[1:]):
            if t1 <= T_celsius <= t2:
                f = (T_celsius - t1) / (t2 - t1)
                kE, ky = e1 + f * (e2 - e1), y1 + f * (y2 - y1)
                break
    return {"T_celsius": T_celsius, "k_E": kE, "k_y": ky,
            "E_MPa": E_20 * kE, "fy_MPa": fy_20 * ky,
            "code": "EN 1993-1-2 Table 3.1",
            "note": "steel keeps full yield to 400 C but has lost 30% of its "
                    "MODULUS by then - stiffness fails before strength"}


# --------------------------------------------------------------------------- #
# 1. Electro-thermal (TWO-WAY: resistance depends on the temperature it causes)
# --------------------------------------------------------------------------- #
def joule_heating(current, resistance, R_thermal, T_ambient=20.0):
    """One-way: P = I^2 R, dT = P R_th. Ignores the resistance-temperature feedback."""
    P = current ** 2 * resistance
    return {"P_W": P, "dT_K": P * R_thermal, "T_celsius": T_ambient + P * R_thermal,
            "coupling": "one-way (resistance assumed constant)"}


def joule_heating_coupled(current, R_20, R_thermal, T_ambient=20.0,
                          alpha=0.00393, max_iter=500, tol=1e-12):
    """TWO-WAY electro-thermal: R(T) = R20 (1 + alpha (T - 20)) feeds back into
    P = I^2 R(T), which sets T again.

    Substituting and solving gives the exact fixed point:

        k = I^2 R20 R_th
        T = (T_amb + k - 20 k alpha) / (1 - k alpha)

    and the denominator makes the failure mode explicit: when k*alpha >= 1 there is
    NO steady state. That is thermal runaway, and it is a physical result, not a
    convergence problem - so this function refuses to return a temperature rather
    than iterating to something plausible.
    """
    k = current ** 2 * R_20 * R_thermal
    if k * alpha >= 1.0:
        return {"runaway": True, "T_celsius": None,
                "loop_gain_k_alpha": k * alpha,
                "coupling": "two-way",
                "reason": f"loop gain k*alpha = {k*alpha:.4f} >= 1: each degree of "
                          "heating raises the dissipation by more than it takes to "
                          "cause that degree. There is no steady state - the "
                          "component runs away thermally.",
                "fix": "reduce the current, or the thermal resistance, or both - "
                       f"R_th must fall below {1.0/(current**2 * R_20 * alpha):.4f} K/W"}
    T = T_ambient
    for i in range(max_iter):
        R = R_20 * (1.0 + alpha * (T - 20.0))
        T_new = T_ambient + current ** 2 * R * R_thermal
        if abs(T_new - T) < tol * max(abs(T_new), 1.0):
            T = T_new
            break
        T = T_new
    exact = (T_ambient + k - 20.0 * k * alpha) / (1.0 - k * alpha)
    R_final = R_20 * (1.0 + alpha * (T - 20.0))
    P_final = current ** 2 * R_final
    P_cold = current ** 2 * R_20
    return {"runaway": False, "T_celsius": T, "T_exact_closed_form": exact,
            "iterations": i + 1, "residual": abs(T - exact),
            "R_final_ohm": R_final, "R_cold_ohm": R_20,
            "P_W": P_final, "P_if_feedback_ignored_W": P_cold,
            "power_underestimate_percent": 100.0 * (P_final - P_cold) / P_final,
            "loop_gain_k_alpha": k * alpha, "coupling": "two-way (iterated)",
            "note": "ignoring the feedback understates both the power and the "
                    "temperature; the error grows as the loop gain approaches 1"}


# --------------------------------------------------------------------------- #
# 2. Thermo-mechanical
# --------------------------------------------------------------------------- #
def thermal_stress_check(material, dT, restraint=1.0, T_service=None):
    """Restrained thermal expansion -> stress -> margin against yield.

    Chains fatigue.alpha (the CTE table), mechanics.thermal_stress (the physics) and,
    if a service temperature is given, the EN 1993-1-2 knockdown - because the yield
    it is being compared against has itself fallen.
    """
    p = fatigue.props(material)
    a = p["alpha"]
    E, fy = p["E"], p["Sy"] or p["Su"]
    if T_service is not None and "steel" in material:
        hot = steel_at_temperature(T_service, E, fy)
        E, fy = hot["E_MPa"], hot["fy_MPa"]
    ts = mechanics.thermal_stress(E, a, dT, restraint, sigma_y=fy)
    return {"material": material, "alpha_per_K": a, "dT_K": dT,
            "E_used_MPa": E, "fy_used_MPa": fy,
            "sigma_MPa": ts["sigma_MPa"], "magnitude_MPa": ts["magnitude_MPa"],
            "sense": ts["sense"], "fos": ts.get("fos"), "yields": ts.get("yields"),
            "T_service_celsius": T_service,
            "chain": "fatigue.alpha -> mechanics.thermal_stress"
                     + (" -> EN 1993-1-2 knockdown" if T_service else "")}


# --------------------------------------------------------------------------- #
# 3. Fluid-structure (ONE-WAY: pressure loads the plate; the plate does not
#    change the flow)
# --------------------------------------------------------------------------- #
def plate_clamped_circular(pressure, radius, thickness, E, nu=0.3):
    """Clamped circular plate under uniform pressure.

        D = E t^3 / (12 (1 - nu^2))          flexural rigidity
        w_max = p a^4 / (64 D)               at the centre
        sigma_max = 3 p a^2 / (4 t^2)        at the EDGE, radial - not the centre

    The stress maximum being at the edge and the deflection maximum at the centre is
    the detail that catches people: a plate cracks at its clamped rim.
    """
    if min(radius, thickness, E) <= 0:
        raise ValueError("radius, thickness and E must be positive")
    D = E * thickness ** 3 / (12.0 * (1.0 - nu ** 2))
    w = pressure * radius ** 4 / (64.0 * D)
    s_edge = 3.0 * pressure * radius ** 2 / (4.0 * thickness ** 2)
    s_centre = 3.0 * pressure * radius ** 2 * (1.0 + nu) / (8.0 * thickness ** 2)
    return {"D_flexural_rigidity": D, "w_max_mm": w,
            "sigma_edge_MPa": s_edge, "sigma_centre_MPa": s_centre,
            "governing_location": "edge (clamped rim)" if s_edge > s_centre else "centre",
            "thin_plate_valid": w < thickness / 2.0,
            "validity": ("small-deflection theory applies (w < t/2)"
                         if w < thickness / 2.0 else
                         f"w = {w:.2f} mm exceeds t/2 = {thickness/2:.2f} mm - "
                         "membrane action matters and this UNDERSTATES stiffness")}


def wind_on_plate(wind_speed_m_s, radius_mm, thickness_mm, E=210000.0, nu=0.3,
                  rho_air=1.225, Cp=1.2):
    """One-way FSI: wind dynamic pressure -> plate deflection and stress.

    Chains fluid_mech (dynamic pressure) into plate theory. Cp = 1.2 is a typical
    net pressure coefficient for a windward face; the real value comes from
    EN 1991-1-4, which EngineAI does not yet carry, and that is stated rather than
    hidden.
    """
    q = 0.5 * rho_air * wind_speed_m_s ** 2          # Pa
    p_MPa = q * Cp / 1e6                              # Pa -> MPa for mm units
    pl = plate_clamped_circular(p_MPa, radius_mm, thickness_mm, E, nu)
    return {"wind_speed_m_s": wind_speed_m_s, "dynamic_pressure_Pa": q,
            "net_pressure_Pa": q * Cp, "Cp": Cp, **pl,
            "chain": "0.5 rho v^2 (fluid) -> Cp -> clamped plate (structural)",
            "caveat": "Cp is assumed; EN 1991-1-4 terrain, exposure and shape "
                      "factors are NOT applied - EngineAI has no wind-action module"}


# --------------------------------------------------------------------------- #
# 4. Magneto-structural
# --------------------------------------------------------------------------- #
def lorentz_beam_deflection(current_1, current_2, separation_mm, span_mm,
                            b_mm, h_mm, E=210000.0, supports="simply_supported"):
    """Two parallel busbars: each carries current, each sits in the other's field,
    and the resulting Lorentz force bends them.

    Chains emag.force_parallel_wires (N/m) -> a UDL in N/mm -> structural deflection.
    This is a real design case: a short-circuit fault drives tens of kiloamps and the
    forces go as I^2, so a bar comfortable at 200 A can be destroyed at 20 kA.
    """
    import structural
    f = emag.force_parallel_wires(current_1, current_2, separation_mm / 1000.0)
    w = f["F_per_L_N_per_m"] / 1000.0                 # N/m -> N/mm
    sec = structural.rectangle(b_mm, h_mm)
    if supports == "simply_supported":
        r = structural.simply_udl(w, span_mm, E, sec["I"])
    else:
        r = mechanics.fixed_fixed_udl(w, span_mm, E, sec["I"])
    sigma = r["M"] / sec["Z"]
    return {"current_A": current_1, "separation_mm": separation_mm,
            "force_per_metre_N_m": f["F_per_L_N_per_m"],
            "udl_N_per_mm": w, "sense": f["sense"],
            "deflection_mm": r["defl"], "moment_Nmm": r["M"],
            "sigma_MPa": sigma, "supports": supports,
            "chain": "emag.force_parallel_wires -> UDL -> structural beam",
            "note": "force scales as I^2, so a fault current of 100x the rated "
                    "current produces 10000x the force"}


def fault_force_scaling(rated_A, fault_A, separation_mm):
    """How much worse a short circuit is than normal operation. The answer is always
    the square of the current ratio, which is why busbar bracing is sized by the
    fault, never by the load."""
    n = emag.force_parallel_wires(rated_A, rated_A, separation_mm / 1000.0)
    f = emag.force_parallel_wires(fault_A, fault_A, separation_mm / 1000.0)
    return {"rated_force_N_m": n["F_per_L_N_per_m"],
            "fault_force_N_m": f["F_per_L_N_per_m"],
            "ratio": f["F_per_L_N_per_m"] / n["F_per_L_N_per_m"],
            "current_ratio": fault_A / rated_A,
            "note": "force ratio is exactly the SQUARE of the current ratio"}


# --------------------------------------------------------------------------- #
# 5. Coupled shaft: torsion + temperature + fatigue
# --------------------------------------------------------------------------- #
def shaft_life(material, torque_Nmm, moment_Nmm, diameter_mm, T_service=20.0,
               cycles_per_rev=1, surface_factor=0.9, size_factor=0.85):
    """A rotating shaft, all three physics at once.

    Bending on a ROTATING shaft is fully reversed - every fibre goes from tension to
    compression once per revolution - while the torque is steady. So the bending
    stress is the alternating component and the torsional stress is the mean, and
    treating them the other way round is a common and serious error.

    Chains: mechanics.combined_bending_torsion -> EN 1993-1-2 knockdown ->
    fatigue.mean_stress -> fatigue.sn_life.
    """
    p = fatigue.props(material)
    E, fy, Su = p["E"], p["Sy"] or p["Su"], p["Su"]
    if T_service > 20.0 and ("steel" in material):
        hot = steel_at_temperature(T_service, E, fy)
        E, fy = hot["E_MPa"], hot["fy_MPa"]
        Su = Su * hot["k_y"]
    cbt = mechanics.combined_bending_torsion(moment_Nmm, torque_Nmm, diameter_mm,
                                             sigma_y=fy)
    sigma_a = cbt["sigma_bending_MPa"]            # fully reversed by rotation
    tau_m = cbt["tau_torsion_MPa"]
    sigma_m = math.sqrt(3.0) * tau_m              # von Mises equivalent of steady shear
    Se_corrected = p["Se"] * surface_factor * size_factor
    try:
        ms = fatigue.mean_stress(sigma_a, sigma_m, Su, fy, "goodman")
        sar = ms["sigma_ar_MPa"]
    except ValueError as e:
        return {"error": f"static failure before fatigue: {e}"}
    infinite = sar <= Se_corrected and "steel" in material
    out = {"material": material, "T_service_celsius": T_service,
           "E_used_MPa": E, "fy_used_MPa": fy, "Su_used_MPa": Su,
           "sigma_bending_alternating_MPa": sigma_a,
           "tau_torsion_steady_MPa": tau_m,
           "sigma_mean_equivalent_MPa": sigma_m,
           "sigma_ar_MPa": sar,
           "Se_lab_MPa": p["Se"], "Se_corrected_MPa": Se_corrected,
           "surface_factor": surface_factor, "size_factor": size_factor,
           "static_fos_von_mises": cbt.get("fos_von_mises"),
           "infinite_life": infinite,
           "chain": "mechanics.combined_bending_torsion -> EN 1993-1-2 -> "
                    "fatigue.mean_stress -> fatigue.sn_life",
           "note": "rotation makes BENDING the alternating component and TORQUE the "
                   "steady one; swapping them is a serious and common error"}
    if not infinite:
        try:
            life = fatigue.basquin_life(sar, p["sigma_f"], p["b"])
            out["N_cycles"] = life["N_cycles"]
            out["revolutions"] = life["N_cycles"] / cycles_per_rev
            out["hours_at_1500rpm"] = life["N_cycles"] / cycles_per_rev / 1500.0 / 60.0
        except Exception as e:
            out["life_error"] = str(e)
    return out


# --------------------------------------------------------------------------- #
# 6. Busbar chain: current -> Joule -> temperature -> expansion -> stress
# --------------------------------------------------------------------------- #
def busbar_chain(current, length_mm, width_mm, thickness_mm, R_thermal,
                 material="copper", T_ambient=20.0, restraint=1.0):
    """The full four-physics chain, end to end.

        electrical : R = rho L / A, with rho(T)
        thermal    : P = I^2 R, dT = P R_th          (iterated - see above)
        expansion  : free dL = alpha L dT
        mechanical : if restrained, sigma = -restraint E alpha dT

    Each link is a separate module. The point of running them together is that the
    answer at the end - a stress in MPa from nothing but an ampere reading - is not
    visible from any single one of them.
    """
    A_mm2 = width_mm * thickness_mm
    A_m2 = A_mm2 * 1e-6
    L_m = length_mm / 1000.0
    cond = emag.conductivity(material, T_ambient)
    R_20 = cond["resistivity_ohm_m"] * L_m / A_m2
    alpha_R = emag.CONDUCTIVITY[material][1]

    et = joule_heating_coupled(current, R_20, R_thermal, T_ambient, alpha_R)
    if et["runaway"]:
        return {"runaway": True, "electro_thermal": et,
                "chain": "electrical -> thermal (DIVERGED)",
                "verdict": "no steady state exists - the bar runs away thermally"}

    T = et["T_celsius"]
    dT = T - T_ambient
    mat_map = {"copper": "copper_C11000", "aluminium": "AL_6061_T6"}
    fmat = mat_map.get(material, "copper_C11000")
    a = fatigue.alpha(fmat)
    E = fatigue.props(fmat)["E"]
    expansion = mechanics.thermal_expansion(a, length_mm, dT)
    stress = mechanics.thermal_stress(E, a, dT, restraint,
                                      sigma_y=fatigue.props(fmat)["Sy"])
    return {
        "current_A": current, "material": material,
        "R_20_ohm": R_20, "R_hot_ohm": et["R_final_ohm"],
        "P_W": et["P_W"], "T_celsius": T, "dT_K": dT,
        "free_expansion_mm": expansion["dL"],
        "sigma_MPa": stress["sigma_MPa"], "sigma_magnitude_MPa": stress["magnitude_MPa"],
        "sense": stress["sense"], "fos_vs_yield": stress.get("fos"),
        "yields": stress.get("yields"), "restraint": restraint,
        "current_density_A_per_mm2": current / A_mm2,
        "electro_thermal": et,
        "chain": "emag.conductivity -> joule_heating_coupled -> "
                 "mechanics.thermal_expansion -> mechanics.thermal_stress",
        "verdict": ("restrained expansion yields the bar" if stress.get("yields")
                    else "within yield, but check the JOINTS - they see this force"),
    }


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

    # ---- EN 1993-1-2 knockdown ----
    chk("steel is undegraded at 20 C",
        steel_at_temperature(20.0)["k_E"] == 1.0 and steel_at_temperature(20.0)["k_y"] == 1.0)
    h4 = steel_at_temperature(400.0)
    chk("at 400 C steel keeps FULL yield but has lost 30% of its modulus",
        abs(h4["k_y"] - 1.0) < 1e-9 and abs(h4["k_E"] - 0.70) < 1e-9,
        "stiffness fails before strength - the fire-design point")
    chk("at 600 C both have collapsed",
        steel_at_temperature(600.0)["k_E"] < 0.35
        and steel_at_temperature(600.0)["k_y"] < 0.5)
    chk("knockdown factors decrease monotonically with temperature",
        all(steel_at_temperature(t)["k_E"] >= steel_at_temperature(t + 50)["k_E"]
            for t in range(20, 950, 50)))
    chk("interpolation is linear between table points",
        abs(steel_at_temperature(250.0)["k_E"] - 0.85) < 1e-9,
        "midway between 0.90 at 200 and 0.80 at 300")

    # ---- 1. electro-thermal, iterated against its own closed form ----
    one = joule_heating(10.0, 0.5, 2.0)
    chk("one-way Joule heating: P = I^2 R and dT = P R_th",
        abs(one["P_W"] - 50.0) < 1e-12 and abs(one["dT_K"] - 100.0) < 1e-12,
        f"{one['P_W']:.1f} W -> {one['dT_K']:.1f} K rise")

    two = joule_heating_coupled(10.0, 0.5, 2.0)
    chk("two-way iteration converges to the EXACT closed-form fixed point",
        two["residual"] < 1e-9,
        f"iterated {two['T_celsius']:.6f} C vs exact "
        f"{two['T_exact_closed_form']:.6f} C in {two['iterations']} iterations")
    chk("the feedback makes it HOTTER than the one-way answer",
        two["T_celsius"] > one["T_celsius"],
        f"{one['T_celsius']:.1f} C one-way -> {two['T_celsius']:.1f} C coupled")
    chk("...and ignoring it understates the power dissipated",
        two["power_underestimate_percent"] > 0,
        f"{two['P_if_feedback_ignored_W']:.2f} W assumed vs {two['P_W']:.2f} W actual "
        f"({two['power_underestimate_percent']:.1f}% low)")

    run = joule_heating_coupled(10.0, 0.5, 6.0)
    chk("THERMAL RUNAWAY is detected and NO temperature is returned",
        run["runaway"] and run["T_celsius"] is None and run["loop_gain_k_alpha"] >= 1.0,
        f"loop gain {run['loop_gain_k_alpha']:.3f} - a converged-looking number "
        "here would be a lie")
    chk("...and the result says exactly what to change",
        "R_th must fall below" in run["fix"], run["fix"][-42:])
    edge = joule_heating_coupled(10.0, 0.5, 5.0)
    chk("just below the runaway threshold it still converges, but runs very hot",
        (not edge["runaway"]) and edge["T_celsius"] > two["T_celsius"] * 2,
        f"loop gain {edge['loop_gain_k_alpha']:.3f} -> {edge['T_celsius']:.0f} C")

    # ---- 2. thermo-mechanical ----
    tm = thermal_stress_check("AISI_1018_steel", 80.0)
    chk("restrained steel bar, 80 K rise: sigma = E alpha dT ~ 187 MPa",
        180.0 < tm["magnitude_MPa"] < 195.0 and tm["sense"] == "compression",
        f"{tm['magnitude_MPa']:.1f} MPa compression, FoS {tm['fos']:.2f}")
    hot = thermal_stress_check("AISI_1018_steel", 80.0, T_service=600.0)
    # Counter-intuitive and worth pinning down: thermal stress is STRAIN-controlled,
    # so it is driven by E - and at 600 C the modulus has fallen to 0.31 while the
    # yield has only fallen to 0.47. The stress drops FASTER than the strength, so
    # the margin actually IMPROVES. This is the opposite of a load-controlled stress,
    # where the stress is fixed and only the strength falls.
    chk("at 600 C the same dT gives much LESS stress, because E collapses first",
        hot["magnitude_MPa"] < 0.4 * tm["magnitude_MPa"],
        f"{tm['magnitude_MPa']:.1f} -> {hot['magnitude_MPa']:.1f} MPa "
        f"(k_E = {steel_at_temperature(600.)['k_E']})")
    chk("...and because E degrades FASTER than fy, the margin actually IMPROVES - "
        "thermal stress is strain-controlled, not load-controlled",
        hot["fos"] > tm["fos"],
        f"FoS {tm['fos']:.2f} cold -> {hot['fos']:.2f} hot, since k_E = "
        f"{steel_at_temperature(600.)['k_E']} < k_y = {steel_at_temperature(600.)['k_y']}")
    chk("...whereas a LOAD-controlled stress gets worse at the same temperature, "
        "because only the strength falls",
        (tm["fy_used_MPa"] / 150.0) > (hot["fy_used_MPa"] / 150.0),
        f"a fixed 150 MPa applied stress: FoS {tm['fy_used_MPa']/150:.2f} -> "
        f"{hot['fy_used_MPa']/150:.2f} - the distinction that matters")
    chk("aluminium develops less thermal stress than steel despite expanding twice "
        "as much - because its modulus is a third",
        thermal_stress_check("AL_6061_T6", 80.0)["magnitude_MPa"]
        < tm["magnitude_MPa"],
        f"Al {thermal_stress_check('AL_6061_T6', 80.)['magnitude_MPa']:.1f} MPa vs "
        f"steel {tm['magnitude_MPa']:.1f} MPa")

    # ---- 3. one-way FSI ----
    pl = plate_clamped_circular(0.05, 250.0, 6.0, 210000.0)
    chk("clamped circular plate w = p a^4 / 64 D",
        abs(pl["w_max_mm"] - 0.05 * 250.0 ** 4
            / (64 * 210000.0 * 6.0 ** 3 / (12 * (1 - 0.09)))) < 1e-9,
        f"{pl['w_max_mm']:.4f} mm")
    chk("the STRESS maximum is at the clamped edge, not the centre",
        pl["governing_location"].startswith("edge")
        and pl["sigma_edge_MPa"] > pl["sigma_centre_MPa"],
        f"edge {pl['sigma_edge_MPa']:.1f} vs centre {pl['sigma_centre_MPa']:.1f} MPa")
    chk("deflection scales as a^4 and as 1/t^3",
        abs(plate_clamped_circular(.05, 500., 6., 210000.)["w_max_mm"]
            / pl["w_max_mm"] - 16.0) < 1e-9
        and abs(plate_clamped_circular(.05, 250., 12., 210000.)["w_max_mm"]
                / pl["w_max_mm"] - 0.125) < 1e-9)
    thin = plate_clamped_circular(0.5, 250.0, 3.0, 210000.0)
    chk("a large deflection is FLAGGED as outside small-deflection theory",
        not thin["thin_plate_valid"] and "UNDERSTATES" in thin["validity"],
        f"w = {thin['w_max_mm']:.1f} mm on a 3 mm plate")

    wp = wind_on_plate(40.0, 500.0, 4.0)
    chk("wind chain: 0.5 rho v^2 gives the dynamic pressure",
        abs(wp["dynamic_pressure_Pa"] - 0.5 * 1.225 * 1600.0) < 1e-9,
        f"{wp['dynamic_pressure_Pa']:.1f} Pa at 40 m/s")
    chk("...and it flows through to a plate deflection and stress",
        wp["w_max_mm"] > 0 and wp["sigma_edge_MPa"] > 0,
        f"{wp['w_max_mm']:.3f} mm, {wp['sigma_edge_MPa']:.1f} MPa at the rim")
    chk("doubling the wind speed quadruples the load and the deflection",
        abs(wind_on_plate(80.0, 500.0, 4.0)["w_max_mm"] / wp["w_max_mm"] - 4.0) < 1e-9)
    chk("the missing EN 1991-1-4 wind module is DECLARED, not glossed over",
        "EN 1991-1-4" in wp["caveat"] and "NOT applied" in wp["caveat"])

    # ---- 4. magneto-structural ----
    lb = lorentz_beam_deflection(20000.0, 20000.0, 100.0, 1000.0, 10.0, 50.0)
    chk("busbar fault force chains from emag into a beam deflection",
        lb["deflection_mm"] > 0 and lb["sigma_MPa"] > 0,
        f"20 kA at 100 mm: {lb['force_per_metre_N_m']:.0f} N/m -> "
        f"{lb['deflection_mm']:.3f} mm, {lb['sigma_MPa']:.1f} MPa")
    chk("the force matches emag's own value exactly (no transcription in the chain)",
        abs(lb["force_per_metre_N_m"]
            - emag.force_parallel_wires(20000., 20000., 0.1)["F_per_L_N_per_m"]) < 1e-9)
    fs = fault_force_scaling(200.0, 20000.0, 100.0)
    chk("fault force is exactly the SQUARE of the current ratio",
        abs(fs["ratio"] - fs["current_ratio"] ** 2) < 1e-6,
        f"100x current -> {fs['ratio']:.0f}x force")
    chk("...which is why bracing is sized by the fault, not the load",
        fs["fault_force_N_m"] > 1000.0 * fs["rated_force_N_m"])
    chk("a fixed-ended bar deflects less than a simply supported one",
        lorentz_beam_deflection(20000., 20000., 100., 1000., 10., 50.,
                                supports="fixed")["deflection_mm"]
        < lb["deflection_mm"])

    # ---- 5. coupled shaft ----
    sl = shaft_life("AISI_1018_steel", torque_Nmm=200e3, moment_Nmm=150e3,
                    diameter_mm=40.0)
    chk("rotating shaft: BENDING is the alternating component, torque the steady one",
        sl["sigma_bending_alternating_MPa"] > 0 and sl["tau_torsion_steady_MPa"] > 0
        and "swapping them" in sl["note"],
        f"sigma_a = {sl['sigma_bending_alternating_MPa']:.1f} MPa, "
        f"tau_m = {sl['tau_torsion_steady_MPa']:.1f} MPa")
    chk("surface and size factors REDUCE the laboratory endurance limit",
        sl["Se_corrected_MPa"] < sl["Se_lab_MPa"],
        f"{sl['Se_lab_MPa']:.0f} -> {sl['Se_corrected_MPa']:.1f} MPa "
        "(a real shaft is not a polished specimen)")
    chk("the shaft assessment returns either infinite life or a cycle count",
        sl["infinite_life"] or "N_cycles" in sl,
        f"infinite={sl['infinite_life']}"
        + (f", N={sl.get('N_cycles', 0):.3g}" if "N_cycles" in sl else ""))
    hot_shaft = shaft_life("AISI_1018_steel", 200e3, 150e3, 40.0, T_service=500.0)
    chk("the same shaft at 500 C has a lower usable yield",
        hot_shaft["fy_used_MPa"] < sl["fy_used_MPa"],
        f"{sl['fy_used_MPa']:.0f} -> {hot_shaft['fy_used_MPa']:.0f} MPa")
    big = shaft_life("AISI_1018_steel", 200e3, 400e3, 40.0)
    chk("more bending moment shortens life",
        (not big["infinite_life"]) or big["sigma_ar_MPa"] > sl["sigma_ar_MPa"],
        f"sigma_ar {sl['sigma_ar_MPa']:.1f} -> {big['sigma_ar_MPa']:.1f} MPa")

    # ---- 6. the full busbar chain ----
    bb = busbar_chain(2000.0, 1000.0, 100.0, 10.0, R_thermal=0.05)
    chk("busbar chain runs all four physics end to end",
        not bb.get("runaway") and bb["T_celsius"] > 20.0 and bb["sigma_MPa"] != 0,
        f"2000 A -> {bb['P_W']:.1f} W -> {bb['T_celsius']:.1f} C -> "
        f"{bb['free_expansion_mm']:.3f} mm -> {bb['sigma_magnitude_MPa']:.1f} MPa")
    chk("the hot resistance exceeds the cold resistance",
        bb["R_hot_ohm"] > bb["R_20_ohm"],
        f"{bb['R_20_ohm']*1e6:.3f} -> {bb['R_hot_ohm']*1e6:.3f} micro-ohm")
    chk("restrained expansion puts the bar in COMPRESSION",
        bb["sense"] == "compression")
    chk("an unrestrained bar expands freely and develops NO stress",
        busbar_chain(2000.0, 1000.0, 100.0, 10.0, 0.05,
                     restraint=0.0)["sigma_magnitude_MPa"] == 0.0)
    chk("the free expansion is alpha L dT, matching mechanics directly",
        abs(bb["free_expansion_mm"]
            - fatigue.alpha("copper_C11000") * 1000.0 * bb["dT_K"]) < 1e-12)
    chk("a bar with too little cooling is reported as RUNAWAY, not given a stress",
        busbar_chain(6000.0, 1000.0, 100.0, 10.0, R_thermal=0.5).get("runaway") is True,
        "the chain refuses to continue past a diverging link")
    chk("the verdict points at the JOINTS, which is where thermal cycling actually "
        "fails busbars",
        "JOINTS" in bb["verdict"] or bb["yields"])
    chk("current density is reported - the number an electrical engineer checks",
        abs(bb["current_density_A_per_mm2"] - 2.0) < 1e-12, "2.0 A/mm^2")

    print("multiphysics selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="coupled multiphysics chains")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--busbar", nargs=5, type=float,
                    metavar=("I_A", "L_mm", "w_mm", "t_mm", "Rth"))
    a = ap.parse_args()
    if a.busbar:
        print(json.dumps(busbar_chain(*a.busbar), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
