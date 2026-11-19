#!/usr/bin/env python3
"""
fluid_mech.py - flow beyond pipe friction: Bernoulli, external flow, boundary
layers, lift, and open channels.

`engineai_fluids.py` does one thing well - pressure drop in a closed conduit. It has
no Bernoulli, no drag, no boundary layer and no lift, so EngineAI could size a pipe
and could not answer "how fast does it come out of the nozzle", "what force does
the wind put on this sign" or "how deep is the water in this channel".

The verification here leans on identities that hold between SEPARATELY derived
results, which is stronger than re-checking one formula:

  * Stokes' law F = 3 pi mu d V and the drag-coefficient route F = 0.5 rho V^2 A Cd
    with Cd = 24/Re are algebraically the same force. They are written independently
    below and must agree exactly.
  * The Blasius plate drag coefficient C_D = 1.328/sqrt(Re_L) must equal exactly
    twice the local skin friction at x = L, because the average of 0.664/sqrt(x)
    over 0..L is 2 x its endpoint value.
  * Bernoulli plus continuity must conserve total head to machine precision.

Units: SI - m, s, kg, Pa, N. (The structural modules use mm; fluids everywhere in
the world uses metres, and mixing them silently is exactly the error units_gate
exists to catch.)

A checked design aid, NOT a certified result. Compressibility above M 0.3,
separation, cavitation, free-surface transients and 3D effects are out of scope.
A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math

G_ACC = 9.80665

# Drag coefficients in the high-Reynolds regime (Re ~ 1e4 - 2e5), dimensionless.
CD_SHAPES = {
    "sphere_smooth": 0.47, "sphere_rough": 0.20, "hemisphere_open_front": 1.42,
    "cube_face_on": 1.05, "cylinder_crossflow": 1.20, "streamlined_body": 0.04,
    "flat_plate_normal": 1.28, "flat_plate_parallel": 0.005,
    "car_saloon": 0.30, "car_van": 0.45, "cyclist_upright": 1.10,
    "parachute": 1.40, "aerofoil_zero_lift": 0.008,
}

# Manning roughness coefficient n, s/m^(1/3)
MANNING_N = {
    "concrete_smooth": 0.012, "concrete_rough": 0.017, "brickwork": 0.015,
    "cast_iron": 0.013, "corrugated_metal": 0.024, "earth_clean": 0.022,
    "earth_weedy": 0.030, "gravel": 0.029, "natural_stream_clean": 0.030,
    "natural_stream_weedy": 0.050, "pvc_plastic": 0.010,
}


# --------------------------------------------------------------------------- #
# Bernoulli and continuity
# --------------------------------------------------------------------------- #
def bernoulli(rho, p1=None, v1=None, z1=0.0, p2=None, v2=None, z2=0.0):
    """Steady, incompressible, inviscid energy equation along a streamline:

        p1 + 0.5 rho v1^2 + rho g z1  =  p2 + 0.5 rho v2^2 + rho g z2

    Exactly one of p1, v1, p2, v2 may be left as None and it will be solved for.
    """
    known = [x is not None for x in (p1, v1, p2, v2)]
    if sum(known) != 3:
        raise ValueError("give exactly three of p1, v1, p2, v2 and leave one None")

    def head(p, v, z):
        return p + 0.5 * rho * v ** 2 + rho * G_ACC * z

    if p2 is None:
        p2 = head(p1, v1, z1) - 0.5 * rho * v2 ** 2 - rho * G_ACC * z2
    elif p1 is None:
        p1 = head(p2, v2, z2) - 0.5 * rho * v1 ** 2 - rho * G_ACC * z1
    elif v2 is None:
        arg = 2.0 * (head(p1, v1, z1) - p2 - rho * G_ACC * z2) / rho
        if arg < 0:
            raise ValueError("no real solution: the specified downstream pressure "
                             "exceeds the available total head")
        v2 = math.sqrt(arg)
    else:
        arg = 2.0 * (head(p2, v2, z2) - p1 - rho * G_ACC * z1) / rho
        if arg < 0:
            raise ValueError("no real solution for v1")
        v1 = math.sqrt(arg)
    H1, H2 = head(p1, v1, z1), head(p2, v2, z2)
    return {"p1_Pa": p1, "v1_m_s": v1, "z1_m": z1,
            "p2_Pa": p2, "v2_m_s": v2, "z2_m": z2,
            "total_head_1_Pa": H1, "total_head_2_Pa": H2,
            "head_residual_Pa": H1 - H2,
            "dynamic_pressure_1_Pa": 0.5 * rho * v1 ** 2,
            "dynamic_pressure_2_Pa": 0.5 * rho * v2 ** 2}


def nozzle(rho, d1, d2, v1=None, Q=None, p1=0.0, Cd=1.0):
    """Converging nozzle or contraction: continuity + Bernoulli together.

        A1 v1 = A2 v2   (incompressible continuity)
        dp = 0.5 rho (v2^2 - v1^2)

    Give either the upstream velocity or the volumetric flow. Cd < 1 accounts for
    the vena contracta in a real orifice; Cd = 1 is the ideal nozzle.
    """
    A1, A2 = math.pi / 4 * d1 ** 2, math.pi / 4 * d2 ** 2
    if d2 > d1:
        raise ValueError("d2 must not exceed d1 for a converging nozzle - "
                         "use a diffuser calculation for an expansion")
    if Q is not None:
        v1 = Q / A1
    elif v1 is None:
        raise ValueError("give either v1 or Q")
    v2 = v1 * A1 / A2 * (1.0 / Cd if Cd else 1.0)
    v2_ideal = v1 * A1 / A2
    dp = 0.5 * rho * (v2_ideal ** 2 - v1 ** 2)
    return {"A1_m2": A1, "A2_m2": A2, "area_ratio": A1 / A2,
            "v1_m_s": v1, "v2_m_s": v2, "v2_ideal_m_s": v2_ideal,
            "dp_Pa": dp, "dp_kPa": dp / 1e3,
            "Q_m3_s": v1 * A1, "Cd": Cd,
            "reaction_force_N": rho * (v1 * A1) * (v2_ideal - v1)}


def torricelli(head, Cd=0.62):
    """Efflux from a small orifice under a head h: v = Cd sqrt(2 g h).

    The ideal sqrt(2gh) is Bernoulli with both pressures atmospheric; Cd ~ 0.62 is
    the vena contracta of a sharp-edged hole.
    """
    v_ideal = math.sqrt(2.0 * G_ACC * head)
    return {"v_ideal_m_s": v_ideal, "v_actual_m_s": Cd * v_ideal, "Cd": Cd}


def pitot(rho, dp, Cp=1.0):
    """Pitot-static tube: v = sqrt(2 dp / rho). The inverse of dynamic pressure."""
    if dp < 0:
        raise ValueError("dynamic pressure cannot be negative")
    return {"v_m_s": Cp * math.sqrt(2.0 * dp / rho),
            "dynamic_pressure_Pa": dp}


# --------------------------------------------------------------------------- #
# External flow and drag
# --------------------------------------------------------------------------- #
def reynolds(rho, v, L, mu):
    """Re = rho v L / mu. L is the diameter for a sphere, the length for a plate."""
    if mu <= 0:
        raise ValueError("viscosity must be positive")
    return rho * v * L / mu


def cd_sphere(Re):
    """Sphere drag coefficient across the regimes.

        Re < 0.1        Cd = 24/Re                        (Stokes, exact)
        0.1 < Re < 1000 Cd = 24/Re (1 + 0.15 Re^0.687)    (Schiller-Naumann)
        1e3 < Re < 2e5  Cd ~ 0.44                         (Newton regime)
        Re > 3e5        Cd ~ 0.2                          (post-crisis)

    The regime is REPORTED, because Cd for a sphere varies by four orders of
    magnitude and a value quoted without its regime is meaningless.
    """
    if Re <= 0:
        raise ValueError("Reynolds number must be positive")
    if Re < 0.1:
        return {"Cd": 24.0 / Re, "regime": "Stokes (creeping flow, exact)", "Re": Re}
    if Re < 1000.0:
        return {"Cd": 24.0 / Re * (1.0 + 0.15 * Re ** 0.687),
                "regime": "Schiller-Naumann (intermediate)", "Re": Re}
    if Re < 2e5:
        return {"Cd": 0.44, "regime": "Newton (inertial, Cd ~ constant)", "Re": Re}
    return {"Cd": 0.20, "regime": "post drag-crisis (turbulent BL, separation "
                                  "delayed - Cd DROPS)", "Re": Re}


def stokes_drag(mu, d, v):
    """Stokes' law: F = 3 pi mu d v, valid for Re < 0.1.

    Written independently of the Cd route so the two can be compared. They are the
    same physics and must give the same number to machine precision.
    """
    return 3.0 * math.pi * mu * d * v


def drag_force(rho, v, area, Cd):
    """F = 0.5 rho v^2 A Cd. The dynamic pressure times area times shape factor."""
    return {"F_N": 0.5 * rho * v ** 2 * area * Cd,
            "dynamic_pressure_Pa": 0.5 * rho * v ** 2,
            "Cd": Cd, "area_m2": area}


def drag_sphere(rho, mu, d, v):
    """Sphere drag by the Cd route, with the Stokes cross-check attached whenever
    the flow is slow enough for Stokes to be valid."""
    Re = reynolds(rho, v, d, mu)
    c = cd_sphere(Re)
    A = math.pi / 4 * d ** 2
    F = 0.5 * rho * v ** 2 * A * c["Cd"]
    out = {"F_N": F, "Re": Re, "Cd": c["Cd"], "regime": c["regime"], "area_m2": A}
    if Re < 0.1:
        out["F_stokes_N"] = stokes_drag(mu, d, v)
        out["stokes_agreement_rel"] = abs(F - out["F_stokes_N"]) / out["F_stokes_N"]
    return out


def terminal_velocity(rho_particle, rho_fluid, mu, d, regime="auto"):
    """Settling velocity where weight - buoyancy = drag.

    Stokes regime has a closed form:  v = g d^2 (rho_p - rho_f) / (18 mu)
    Otherwise iterate on Cd(Re), which converges quickly because Cd varies slowly.
    """
    drho = rho_particle - rho_fluid
    if drho <= 0:
        raise ValueError("particle must be denser than the fluid to settle")
    v_stokes = G_ACC * d ** 2 * drho / (18.0 * mu)
    Re_s = reynolds(rho_fluid, v_stokes, d, mu)
    if regime == "stokes" or (regime == "auto" and Re_s < 0.1):
        return {"v_m_s": v_stokes, "Re": Re_s, "regime": "Stokes (closed form)",
                "iterations": 0}
    v = v_stokes
    for i in range(200):
        Re = reynolds(rho_fluid, v, d, mu)
        Cd = cd_sphere(Re)["Cd"]
        v_new = math.sqrt(4.0 * G_ACC * d * drho / (3.0 * Cd * rho_fluid))
        if abs(v_new - v) < 1e-12 * max(v_new, 1e-12):
            v = v_new
            break
        v = 0.5 * (v + v_new)          # damped, because Cd(Re) is piecewise
    Re = reynolds(rho_fluid, v, d, mu)
    return {"v_m_s": v, "Re": Re, "regime": cd_sphere(Re)["regime"],
            "iterations": i + 1, "v_stokes_would_be": v_stokes}


# --------------------------------------------------------------------------- #
# Boundary layers
# --------------------------------------------------------------------------- #
def blasius_boundary_layer(rho, mu, v_inf, x):
    """Laminar flat-plate boundary layer, Blasius similarity solution:

        delta/x     = 5.0 / sqrt(Re_x)          99% velocity thickness
        delta*/x    = 1.721 / sqrt(Re_x)        displacement thickness
        theta/x     = 0.664 / sqrt(Re_x)        momentum thickness
        Cf_local    = 0.664 / sqrt(Re_x)
        Cd_average  = 1.328 / sqrt(Re_L)        = exactly 2 x Cf at x = L

    The factor-of-two relation between the average and local coefficients is a
    consequence of integrating x^-0.5, and the selftest checks it exactly.
    """
    if x <= 0:
        raise ValueError("distance from the leading edge must be positive")
    Re_x = reynolds(rho, v_inf, x, mu)
    if Re_x <= 0:
        raise ValueError("Reynolds number must be positive")
    s = math.sqrt(Re_x)
    return {"Re_x": Re_x, "delta_m": 5.0 * x / s,
            "delta_star_m": 1.721 * x / s, "theta_m": 0.664 * x / s,
            "Cf_local": 0.664 / s, "Cd_average_to_x": 1.328 / s,
            "tau_wall_Pa": 0.664 / s * 0.5 * rho * v_inf ** 2,
            "shape_factor_H": 1.721 / 0.664,
            "laminar": Re_x < 5e5,
            "validity": ("laminar (Re_x < 5e5)" if Re_x < 5e5
                         else f"Re_x = {Re_x:.3g} EXCEEDS 5e5 - the layer has "
                              "transitioned, Blasius no longer applies")}


def plate_drag(rho, mu, v_inf, length, width):
    """Total friction drag on one side of a flat plate in laminar flow."""
    bl = blasius_boundary_layer(rho, mu, v_inf, length)
    A = length * width
    F = bl["Cd_average_to_x"] * 0.5 * rho * v_inf ** 2 * A
    return {"F_N": F, "Cd": bl["Cd_average_to_x"], "Re_L": bl["Re_x"],
            "delta_at_TE_m": bl["delta_m"], "laminar": bl["laminar"]}


# --------------------------------------------------------------------------- #
# Lift
# --------------------------------------------------------------------------- #
def thin_airfoil(alpha_deg, camber_cl0=0.0):
    """Thin-airfoil theory: Cl = 2 pi alpha (radians), plus any camber offset.

    The lift-curve slope 2 pi per radian = 0.1097 per degree is one of the most
    robust results in aerodynamics; real airfoils achieve about 0.9 of it.
    """
    a = math.radians(alpha_deg)
    Cl = 2.0 * math.pi * a + camber_cl0
    return {"Cl": Cl, "alpha_deg": alpha_deg, "alpha_rad": a,
            "lift_slope_per_rad": 2.0 * math.pi,
            "lift_slope_per_deg": 2.0 * math.pi * math.pi / 180.0,
            "valid": abs(alpha_deg) < 12.0,
            "validity": ("thin-airfoil theory applies (small angle, attached flow)"
                         if abs(alpha_deg) < 12.0 else
                         f"alpha = {alpha_deg} deg is beyond stall for most sections - "
                         "inviscid theory has no stall and will over-predict")}


def lift_force(rho, v, area, Cl):
    """L = 0.5 rho v^2 A Cl - the same dynamic pressure as drag, different factor."""
    return {"L_N": 0.5 * rho * v ** 2 * area * Cl,
            "dynamic_pressure_Pa": 0.5 * rho * v ** 2}


def induced_drag(Cl, aspect_ratio, oswald=1.0):
    """Cdi = Cl^2 / (pi AR e). Lift-induced drag - the price of finite span.
    An infinite wing (AR -> inf) has none, which is why thin-airfoil theory omits it.
    """
    if aspect_ratio <= 0:
        raise ValueError("aspect ratio must be positive")
    return {"Cd_induced": Cl ** 2 / (math.pi * aspect_ratio * oswald),
            "aspect_ratio": aspect_ratio, "oswald_efficiency": oswald}


# --------------------------------------------------------------------------- #
# Open channel flow
# --------------------------------------------------------------------------- #
def manning(n, area, perimeter, slope):
    """Manning's equation: V = (1/n) R^(2/3) S^(1/2), Q = V A, R = A/P.

    Empirical and dimensional (the 1/n carries units), so it is only valid in SI
    with n in s/m^(1/3). The imperial form carries a 1.486 factor, and mixing the
    two is a classic 50% error.
    """
    if perimeter <= 0 or area <= 0 or n <= 0 or slope <= 0:
        raise ValueError("area, perimeter, n and slope must all be positive")
    R = area / perimeter
    V = (1.0 / n) * R ** (2.0 / 3.0) * math.sqrt(slope)
    return {"V_m_s": V, "Q_m3_s": V * area, "R_hydraulic_m": R,
            "area_m2": area, "perimeter_m": perimeter, "n": n, "slope": slope}


def rectangular_channel(n, width, depth, slope):
    """Manning for a rectangular channel, with the Froude number attached because
    whether the flow is sub- or supercritical changes how it behaves entirely."""
    A = width * depth
    P = width + 2.0 * depth
    m = manning(n, A, P, slope)
    Fr = m["V_m_s"] / math.sqrt(G_ACC * depth)
    m.update({"depth_m": depth, "width_m": width, "froude": Fr,
              "flow_state": "subcritical (tranquil)" if Fr < 1 else
                            "critical" if abs(Fr - 1) < 1e-9 else
                            "supercritical (rapid)",
              "critical_depth_m": ((m["Q_m3_s"] / width) ** 2 / G_ACC) ** (1.0 / 3.0)})
    return m


def normal_depth(n, width, slope, Q, lo=1e-4, hi=50.0):
    """Solve Manning backwards for the depth that carries a given discharge.
    Monotonic in depth, so bisection is guaranteed to converge."""
    f = lambda d: rectangular_channel(n, width, d, slope)["Q_m3_s"] - Q
    if f(lo) > 0 or f(hi) < 0:
        raise ValueError(f"discharge {Q} m^3/s is outside the bracketable depth range")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    d = 0.5 * (lo + hi)
    r = rectangular_channel(n, width, d, slope)
    r["Q_target_m3_s"] = Q
    r["residual_m3_s"] = r["Q_m3_s"] - Q
    return r


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

    rho_w, mu_w = 998.0, 1.002e-3
    rho_a, mu_a = 1.225, 1.81e-5

    # ---- Bernoulli: total head must be conserved to machine precision ----
    b = bernoulli(rho_w, p1=200000.0, v1=2.0, p2=None, v2=8.0)
    chk("Bernoulli conserves total head exactly",
        abs(b["head_residual_Pa"]) < 1e-9, f"residual {b['head_residual_Pa']:.3e} Pa")
    chk("accelerating the flow DROPS the pressure",
        b["p2_Pa"] < 200000.0,
        f"200.0 kPa at 2 m/s -> {b['p2_Pa']/1e3:.2f} kPa at 8 m/s")
    chk("the pressure drop equals the rise in dynamic pressure",
        abs((200000.0 - b["p2_Pa"]) - (b["dynamic_pressure_2_Pa"]
                                       - b["dynamic_pressure_1_Pa"])) < 1e-9)
    chk("solving for v2 inverts the p2 solution exactly",
        abs(bernoulli(rho_w, p1=200000.0, v1=2.0, p2=b["p2_Pa"], v2=None)["v2_m_s"]
            - 8.0) < 1e-9)
    chk("elevation converts to pressure at rho g z",
        abs(bernoulli(rho_w, p1=0.0, v1=0.0, z1=10.0, p2=None, v2=0.0, z2=0.0)["p2_Pa"]
            - rho_w * G_ACC * 10.0) < 1e-9, "10 m of water = 97.9 kPa")
    chk("an impossible downstream pressure is refused",
        _raises(lambda: bernoulli(rho_w, p1=1000.0, v1=0.0, p2=1e9, v2=None), ValueError))
    chk("specifying the wrong number of knowns is refused",
        _raises(lambda: bernoulli(rho_w, p1=1.0, v1=1.0, p2=1.0, v2=1.0), ValueError))

    nz = nozzle(rho_w, 0.100, 0.050, v1=2.0)
    chk("nozzle continuity: 4x area ratio gives 4x velocity",
        abs(nz["v2_ideal_m_s"] - 8.0) < 1e-12, f"{nz['v2_ideal_m_s']:.3f} m/s")
    chk("nozzle dp = 0.5 rho (v2^2 - v1^2)",
        abs(nz["dp_Pa"] - 0.5 * rho_w * (64.0 - 4.0)) < 1e-9, f"{nz['dp_kPa']:.2f} kPa")
    chk("...and Bernoulli agrees with the nozzle calculation independently",
        abs(bernoulli(rho_w, p1=0.0, v1=2.0, p2=None, v2=8.0)["p2_Pa"] + nz["dp_Pa"])
        < 1e-9)
    chk("an expanding 'nozzle' is refused",
        _raises(lambda: nozzle(rho_w, 0.05, 0.10, v1=2.0), ValueError))

    to = torricelli(5.0)
    chk("Torricelli v = sqrt(2gh) - a 5 m head gives 9.90 m/s ideal",
        abs(to["v_ideal_m_s"] - math.sqrt(2 * G_ACC * 5.0)) < 1e-12,
        f"{to['v_ideal_m_s']:.3f} m/s ideal, {to['v_actual_m_s']:.3f} m/s actual")
    chk("Torricelli is just Bernoulli with both ends at atmosphere",
        abs(to["v_ideal_m_s"]
            - bernoulli(rho_w, p1=0.0, v1=0.0, z1=5.0, p2=0.0, v2=None, z2=0.0)["v2_m_s"])
        < 1e-9, "two routes, same answer")
    chk("Pitot inverts dynamic pressure",
        abs(pitot(rho_a, 0.5 * rho_a * 50.0 ** 2)["v_m_s"] - 50.0) < 1e-9,
        "50 m/s recovered from its own dynamic pressure")

    # ---- drag: two independent derivations must coincide ----
    d, v = 1e-4, 1e-3
    ds = drag_sphere(rho_w, mu_w, d, v)
    chk("Stokes regime is correctly identified", ds["Re"] < 0.1,
        f"Re = {ds['Re']:.5f}")
    chk("Cd = 24/Re exactly in the Stokes regime",
        abs(ds["Cd"] - 24.0 / ds["Re"]) < 1e-12, f"Cd = {ds['Cd']:.1f}")
    chk("the Cd route and Stokes' law F = 3 pi mu d v agree EXACTLY",
        ds["stokes_agreement_rel"] < 1e-12,
        f"{ds['F_N']:.6e} N vs {ds['F_stokes_N']:.6e} N - "
        "two independent formulas, machine-precision agreement")
    chk("drag regimes are reported, not silently assumed",
        "Stokes" in cd_sphere(0.01)["regime"]
        and "Newton" in cd_sphere(1e4)["regime"]
        and "crisis" in cd_sphere(1e6)["regime"])
    chk("the drag crisis DROPS Cd as Re rises past 2e5",
        cd_sphere(1e6)["Cd"] < cd_sphere(1e5)["Cd"],
        f"{cd_sphere(1e5)['Cd']} -> {cd_sphere(1e6)['Cd']} - the counter-intuitive one")
    chk("F = 0.5 rho v^2 A Cd",
        abs(drag_force(rho_a, 30.0, 2.0, 0.47)["F_N"]
            - 0.5 * 1.225 * 900.0 * 2.0 * 0.47) < 1e-9)
    chk("drag scales as v^2",
        abs(drag_force(rho_a, 60., 2., .47)["F_N"]
            / drag_force(rho_a, 30., 2., .47)["F_N"] - 4.0) < 1e-12)
    chk("a streamlined body has ~30x less drag than a flat plate",
        CD_SHAPES["flat_plate_normal"] / CD_SHAPES["streamlined_body"] > 30)

    # 10 um silt genuinely IS in the Stokes regime; 0.1 mm sand is NOT (Re ~ 0.8),
    # and the function detecting that for itself is the behaviour worth testing.
    dp = 1e-5
    tv = terminal_velocity(2650.0, rho_w, mu_w, dp)
    chk("Stokes settling velocity v = g d^2 drho / 18 mu, used as a closed form",
        abs(tv["v_m_s"] - G_ACC * dp ** 2 * (2650 - 998) / (18 * mu_w)) < 1e-18
        and tv["iterations"] == 0 and "Stokes" in tv["regime"],
        f"{tv['v_m_s']*1e6:.3f} um/s for 10 um silt, Re = {tv['Re']:.2e}")
    chk("at terminal velocity, drag exactly balances immersed weight",
        abs(drag_sphere(rho_w, mu_w, dp, tv["v_m_s"])["F_N"]
            - (2650 - 998) * G_ACC * math.pi / 6 * dp ** 3)
        / ((2650 - 998) * G_ACC * math.pi / 6 * dp ** 3) < 1e-9,
        "force balance closes")
    sand = terminal_velocity(2650.0, rho_w, mu_w, 1e-4)
    chk("0.1 mm sand is ALREADY outside Stokes, and the code notices",
        sand["Re"] > 0.1 and sand["iterations"] > 0,
        f"Re = {sand['Re']:.2f} -> iterated to {sand['v_m_s']*1000:.3f} mm/s "
        f"instead of the {sand['v_stokes_would_be']*1000:.3f} mm/s Stokes claims")
    big = terminal_velocity(2650.0, rho_w, mu_w, 0.010)   # 10 mm gravel
    chk("a large particle leaves the Stokes regime and needs iteration",
        big["Re"] > 100 and big["iterations"] > 0,
        f"Re = {big['Re']:.0f}, v = {big['v_m_s']:.3f} m/s after "
        f"{big['iterations']} iterations")
    chk("...and the Stokes formula would have been wildly wrong there",
        big["v_stokes_would_be"] / big["v_m_s"] > 10,
        f"Stokes says {big['v_stokes_would_be']:.1f} m/s, actual {big['v_m_s']:.3f} m/s")
    chk("a buoyant particle is refused rather than given a negative velocity",
        _raises(lambda: terminal_velocity(500.0, 998.0, mu_w, 1e-3), ValueError))

    # ---- boundary layer: the exact factor-of-two identity ----
    bl = blasius_boundary_layer(rho_a, mu_a, 10.0, 0.5)
    chk("Blasius delta/x = 5.0/sqrt(Re_x)",
        abs(bl["delta_m"] - 5.0 * 0.5 / math.sqrt(bl["Re_x"])) < 1e-15,
        f"delta = {bl['delta_m']*1000:.3f} mm at x = 0.5 m")
    chk("displacement thickness < momentum... no: delta* > theta, H = 2.59",
        bl["delta_star_m"] > bl["theta_m"]
        and abs(bl["shape_factor_H"] - 2.591) < 0.002,
        f"H = {bl['shape_factor_H']:.4f}, the Blasius shape factor")
    chk("average Cd is EXACTLY twice the local Cf at the same x",
        abs(bl["Cd_average_to_x"] / bl["Cf_local"] - 2.0) < 1e-12,
        "a consequence of integrating x^-0.5, not a coincidence")
    chk("delta grows as sqrt(x)",
        abs(blasius_boundary_layer(rho_a, mu_a, 10., 2.0)["delta_m"]
            / blasius_boundary_layer(rho_a, mu_a, 10., 0.5)["delta_m"] - 2.0) < 1e-12)
    chk("a turbulent Re_x is FLAGGED, not silently computed as laminar",
        not blasius_boundary_layer(rho_a, mu_a, 100.0, 5.0)["laminar"],
        "Blasius does not apply past Re_x = 5e5")
    pd = plate_drag(rho_a, mu_a, 10.0, 0.5, 1.0)
    chk("plate drag uses the average Cd and is consistent with the local solution",
        abs(pd["Cd"] - bl["Cd_average_to_x"]) < 1e-15, f"F = {pd['F_N']*1000:.3f} mN")

    # ---- lift ----
    ta = thin_airfoil(5.0)
    chk("thin airfoil Cl = 2 pi alpha",
        abs(ta["Cl"] - 2 * math.pi * math.radians(5.0)) < 1e-15, f"Cl = {ta['Cl']:.4f}")
    chk("lift-curve slope is 2 pi per radian = 0.1097 per degree",
        abs(ta["lift_slope_per_deg"] - 0.10966) < 1e-4,
        f"{ta['lift_slope_per_deg']:.5f} /deg")
    chk("zero angle of attack gives zero lift on a symmetric section",
        abs(thin_airfoil(0.0)["Cl"]) < 1e-15)
    chk("Cl is linear in alpha",
        abs(thin_airfoil(10.0)["Cl"] / thin_airfoil(5.0)["Cl"] - 2.0) < 1e-12)
    chk("a post-stall angle is FLAGGED - inviscid theory has no stall",
        not thin_airfoil(20.0)["valid"])
    chk("camber shifts the whole curve up",
        thin_airfoil(0.0, camber_cl0=0.3)["Cl"] == 0.3)
    lf = lift_force(rho_a, 50.0, 16.0, ta["Cl"])
    chk("L = 0.5 rho v^2 A Cl", abs(lf["L_N"] - 0.5 * 1.225 * 2500 * 16 * ta["Cl"]) < 1e-9,
        f"{lf['L_N']/1000:.2f} kN")
    idr = induced_drag(1.0, 8.0)
    chk("induced drag Cdi = Cl^2/(pi AR e)",
        abs(idr["Cd_induced"] - 1.0 / (math.pi * 8.0)) < 1e-15,
        f"Cdi = {idr['Cd_induced']:.5f} at AR 8")
    chk("an infinite-span wing has no induced drag",
        induced_drag(1.0, 1e12)["Cd_induced"] < 1e-12,
        "which is why 2D thin-airfoil theory omits it")
    chk("halving the aspect ratio doubles the induced drag",
        abs(induced_drag(1., 4.)["Cd_induced"] / idr["Cd_induced"] - 2.0) < 1e-12)

    # ---- open channel ----
    rc = rectangular_channel(0.013, 3.0, 1.2, 0.001)
    chk("Manning V = (1/n) R^(2/3) sqrt(S)",
        abs(rc["V_m_s"] - (1 / 0.013) * (3.6 / 5.4) ** (2 / 3) * math.sqrt(0.001)) < 1e-12,
        f"V = {rc['V_m_s']:.3f} m/s, Q = {rc['Q_m3_s']:.2f} m^3/s")
    chk("hydraulic radius R = A/P", abs(rc["R_hydraulic_m"] - 3.6 / 5.4) < 1e-15)
    chk("Froude number identifies the flow state",
        rc["froude"] < 1 and "subcritical" in rc["flow_state"],
        f"Fr = {rc['froude']:.3f}")
    steep = rectangular_channel(0.013, 3.0, 0.3, 0.05)
    chk("a steep shallow channel goes supercritical",
        steep["froude"] > 1 and "supercritical" in steep["flow_state"],
        f"Fr = {steep['froude']:.2f}")
    chk("a rougher channel carries less flow at the same depth and slope",
        rectangular_channel(0.030, 3.0, 1.2, 0.001)["Q_m3_s"] < rc["Q_m3_s"])
    chk("discharge scales as sqrt(slope)",
        abs(rectangular_channel(0.013, 3., 1.2, 0.004)["Q_m3_s"] / rc["Q_m3_s"] - 2.0)
        < 1e-9)
    nd = normal_depth(0.013, 3.0, 0.001, rc["Q_m3_s"])
    chk("normal_depth inverts Manning back to the depth it came from",
        abs(nd["depth_m"] - 1.2) < 1e-6,
        f"recovered {nd['depth_m']:.6f} m from Q = {rc['Q_m3_s']:.3f} m^3/s")
    chk("an unbracketable discharge is refused",
        _raises(lambda: normal_depth(0.013, 3.0, 0.001, 1e9), ValueError))
    chk("Manning n values are ordered sensibly: plastic < concrete < weedy stream",
        MANNING_N["pvc_plastic"] < MANNING_N["concrete_smooth"]
        < MANNING_N["natural_stream_weedy"])

    print("fluid_mech selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Bernoulli, drag, boundary layers, channels")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--drag", nargs=4, type=float, metavar=("rho", "mu", "d", "v"))
    ap.add_argument("--channel", nargs=4, type=float, metavar=("n", "width", "depth", "slope"))
    a = ap.parse_args()
    if a.drag:
        print(json.dumps(drag_sphere(*a.drag), indent=2)); raise SystemExit(0)
    if a.channel:
        print(json.dumps(rectangular_channel(*a.channel), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
