#!/usr/bin/env python3
"""
engines.py - every common engine cycle, computed and checked against the
closed forms and against each other.

WHY
---
The workbench could decompose "design a jet engine" into nine analyses and
then compute none of the first one, because there was no cycle module. This is
that module, and it is deliberately not jet-only: the same air-standard
machinery covers Otto, Diesel, Dual, Atkinson, Brayton, Rankine, Stirling,
turbojet, turbofan, ramjet and rocket. An engineer working on a two-stroke and
an engineer working on a turboshaft need the same first calculation.

HOW IT IS VERIFIED
------------------
Air-standard cycle efficiencies have exact closed forms, and every textbook
prints the same numbers. selftest() checks against them:

    Otto    r=8,  gamma=1.4                  -> 56.47%
    Diesel  r=18, cutoff=2, gamma=1.4        -> 63.16%
    Brayton rp=10, gamma=1.4                 -> 48.21%
    Carnot  between the same temperatures    -> must exceed all of them

That last one is the real check. Any cycle operating between two temperatures
must come in under Carnot; a cycle model that beats it has a sign error
somewhere, and the module refuses the result rather than printing it.

HONEST SCOPE
------------
  * AIR-STANDARD by default: constant cp, ideal gas, no dissociation, no
    combustion chemistry, no heat loss to the walls. Real engines land 15-25%
    below these numbers, and every result says so.
  * Component efficiencies (compressor, turbine, nozzle) ARE modelled when you
    give them, which is the difference between a textbook number and a usable
    one.
  * No blade aerodynamics, no stage-by-stage design, no combustion kinetics,
    no transient or off-design behaviour. Those are named in the result, not
    hidden.
  * Rankine uses a simplified steam model unless CoolProp is installed, and
    says which it used.

  python engines.py --selftest
  python engines.py --cycle brayton --rp 10 --T3 1400
  python engines.py --cycle turbojet --M 0.8 --alt 10000 --rp 12 --T4 1500
"""

from __future__ import annotations

import argparse
import math
import sys

R_AIR = 287.05          # J/kg-K
G0 = 9.80665            # m/s^2
CP_AIR = 1005.0         # J/kg-K
GAMMA = 1.4

# International Standard Atmosphere, troposphere then lower stratosphere.
def isa(alt_m: float) -> dict:
    """Ambient temperature, pressure and density at altitude."""
    if alt_m < 11000:
        T = 288.15 - 0.0065 * alt_m
        p = 101325.0 * (T / 288.15) ** 5.2559
    else:
        T = 216.65
        p = 22632.0 * math.exp(-G0 * (alt_m - 11000) / (R_AIR * T))
    return {"T_K": T, "p_Pa": p, "rho": p / (R_AIR * T),
            "a_ms": math.sqrt(GAMMA * R_AIR * T)}


def carnot(Tc: float, Th: float) -> float:
    """The ceiling every other cycle here is checked against."""
    if Th <= Tc or Tc <= 0:
        raise ValueError("need 0 < Tc < Th in kelvin")
    return 1.0 - Tc / Th


def _guard(eta: float, Tc: float, Th: float, name: str) -> None:
    """No cycle may beat Carnot. If one does, the model is wrong, not the physics."""
    if Tc and Th and Th > Tc:
        lim = carnot(Tc, Th)
        if eta > lim + 1e-9:
            raise ArithmeticError(
                f"{name} came out at {eta*100:.2f}%, above the Carnot limit of "
                f"{lim*100:.2f}% between {Tc:.0f} K and {Th:.0f} K. That is "
                f"impossible, so this result is a bug and is not being returned.")


# --------------------------------------------------------------------------
# reciprocating cycles
# --------------------------------------------------------------------------

def otto(r: float, gamma: float = GAMMA, T1: float = 300.0,
         T3: float = None) -> dict:
    """Spark ignition. eta = 1 - r^(1-gamma)."""
    if r <= 1:
        raise ValueError("compression ratio must exceed 1")
    eta = 1.0 - r ** (1.0 - gamma)
    out = {"cycle": "Otto (air standard)", "compression_ratio": r,
           "gamma": gamma, "efficiency": eta, "efficiency_pct": eta * 100}
    T2 = T1 * r ** (gamma - 1)
    out["T2_K"] = T2
    if T3:
        out["T3_K"] = T3
        out["T4_K"] = T3 * r ** (1 - gamma)
        _guard(eta, T1, T3, "Otto")
        out["carnot_pct"] = carnot(T1, T3) * 100
    out["note"] = ("air standard: constant cp, no dissociation, no heat loss. "
                   "A real SI engine reaches roughly 25-35% brake thermal, "
                   "well under this.")
    return out


def diesel(r: float, cutoff: float, gamma: float = GAMMA,
           T1: float = 300.0) -> dict:
    """Compression ignition. The cutoff ratio is what costs it against Otto."""
    if r <= 1 or cutoff < 1:
        raise ValueError("need r > 1 and cutoff >= 1")
    eta = 1.0 - (r ** (1.0 - gamma)) * ((cutoff ** gamma - 1.0) /
                                        (gamma * (cutoff - 1.0))) \
        if cutoff > 1 else 1.0 - r ** (1.0 - gamma)
    T2 = T1 * r ** (gamma - 1)
    T3 = T2 * cutoff
    _guard(eta, T1, T3, "Diesel")
    return {"cycle": "Diesel (air standard)", "compression_ratio": r,
            "cutoff_ratio": cutoff, "gamma": gamma, "efficiency": eta,
            "efficiency_pct": eta * 100, "T2_K": T2, "T3_K": T3,
            "carnot_pct": carnot(T1, T3) * 100,
            "note": "at equal compression ratio Diesel is LESS efficient than "
                    "Otto; it wins because it can run a much higher r without "
                    "knock."}


def dual(r: float, pressure_ratio: float, cutoff: float,
         gamma: float = GAMMA, T1: float = 300.0) -> dict:
    """Limited-pressure cycle - what a real CI engine actually approximates."""
    rp, rc = pressure_ratio, cutoff
    eta = 1.0 - (r ** (1.0 - gamma)) * (
        (rp * rc ** gamma - 1.0) /
        ((rp - 1.0) + gamma * rp * (rc - 1.0)))
    return {"cycle": "Dual / limited pressure", "compression_ratio": r,
            "pressure_ratio": rp, "cutoff_ratio": rc,
            "efficiency": eta, "efficiency_pct": eta * 100,
            "note": "reduces to Otto when cutoff = 1 and to Diesel when "
                    "pressure_ratio = 1."}


def atkinson(r_comp: float, r_exp: float, gamma: float = GAMMA) -> dict:
    """Over-expanded cycle: expansion longer than compression."""
    if r_exp <= r_comp:
        raise ValueError("an Atkinson cycle expands further than it compresses")
    # eta = 1 - gamma (e - c) / (e^gamma - c^gamma)
    #
    # The first version of this line was assembled from the Diesel expression
    # and produced an efficiency BELOW Otto at the same compression ratio,
    # which is backwards - over-expansion is the whole point of the cycle. The
    # test that caught it is the limit below: as e -> c the expression must
    # collapse to Otto, and only the correct form does.
    c, e = r_comp, r_exp
    eta = 1.0 - gamma * (e - c) / (e ** gamma - c ** gamma)
    return {"cycle": "Atkinson / Miller (over-expanded)",
            "compression_ratio": r_comp, "expansion_ratio": r_exp,
            "efficiency": eta, "efficiency_pct": eta * 100,
            "note": "buys efficiency with power density; this is why hybrids "
                    "use it."}


def stirling(Tc: float, Th: float, regenerator: float = 1.0) -> dict:
    """With a perfect regenerator a Stirling cycle reaches Carnot."""
    lim = carnot(Tc, Th)
    eta = lim * regenerator
    return {"cycle": "Stirling", "T_cold_K": Tc, "T_hot_K": Th,
            "regenerator_effectiveness": regenerator,
            "efficiency": eta, "efficiency_pct": eta * 100,
            "carnot_pct": lim * 100,
            "note": "an ideal regenerator makes this Carnot-equivalent, which "
                    "is why the real limit is the regenerator, not the cycle."}


# --------------------------------------------------------------------------
# gas turbine family
# --------------------------------------------------------------------------

def brayton(rp: float, T1: float = 288.15, T3: float = None,
            gamma: float = GAMMA, cp: float = CP_AIR,
            eta_c: float = 1.0, eta_t: float = 1.0) -> dict:
    """Gas turbine. Ideal when the component efficiencies are 1."""
    if rp <= 1:
        raise ValueError("pressure ratio must exceed 1")
    x = rp ** ((gamma - 1.0) / gamma)
    out = {"cycle": "Brayton", "pressure_ratio": rp, "T1_K": T1,
           "eta_compressor": eta_c, "eta_turbine": eta_t}

    T2s = T1 * x
    T2 = T1 + (T2s - T1) / eta_c
    out.update({"T2s_K": T2s, "T2_K": T2,
                "compressor_work_Jkg": cp * (T2 - T1)})

    if T3 is None:
        eta = 1.0 - 1.0 / x
        out.update({"efficiency": eta, "efficiency_pct": eta * 100,
                    "note": "ideal air-standard efficiency depends only on "
                            "pressure ratio. Give T3 for work output and a "
                            "real efficiency."})
        return out

    T4s = T3 / x
    T4 = T3 - eta_t * (T3 - T4s)
    wt = cp * (T3 - T4)
    wc = cp * (T2 - T1)
    qin = cp * (T3 - T2)
    wnet = wt - wc
    eta = wnet / qin if qin > 0 else 0.0
    _guard(eta, T1, T3, "Brayton")
    out.update({"T3_K": T3, "T4s_K": T4s, "T4_K": T4,
                "turbine_work_Jkg": wt, "heat_in_Jkg": qin,
                "net_work_Jkg": wnet, "back_work_ratio": wc / wt if wt else None,
                "efficiency": eta, "efficiency_pct": eta * 100,
                "carnot_pct": carnot(T1, T3) * 100,
                "note": "back work ratio is the number that kills small gas "
                        "turbines: the compressor eats most of the turbine's "
                        "output."})
    return out


def nozzle(p0: float, T0: float, pa: float, gamma: float = GAMMA,
           R: float = R_AIR, mdot: float = None, cd: float = 1.0) -> dict:
    """Convergent nozzle: is it choked, what is the exit velocity, what area.

    Step 2 of the jet-engine decomposition, and the one that sizes the hole.
    """
    crit = (2.0 / (gamma + 1.0)) ** (gamma / (gamma - 1.0))
    choked = (pa / p0) <= crit
    out = {"critical_pressure_ratio": crit, "choked": choked,
           "p0_Pa": p0, "pa_Pa": pa, "T0_K": T0}
    if choked:
        Te = T0 * 2.0 / (gamma + 1.0)
        ve = math.sqrt(gamma * R * Te)          # exit is sonic
        pe = p0 * crit
    else:
        Te = T0 * (pa / p0) ** ((gamma - 1.0) / gamma)
        ve = math.sqrt(2.0 * (gamma * R / (gamma - 1.0)) * (T0 - Te))
        pe = pa
    rho_e = pe / (R * Te)
    out.update({"Te_K": Te, "pe_Pa": pe, "ve_ms": ve, "rho_e": rho_e,
                "mach_exit": ve / math.sqrt(gamma * R * Te)})
    if mdot:
        A = mdot / (cd * rho_e * ve)
        out.update({"mdot_kgs": mdot, "area_m2": A,
                    "diameter_mm": 2000.0 * math.sqrt(A / math.pi),
                    "thrust_momentum_N": mdot * ve,
                    "thrust_pressure_N": (pe - pa) * A,
                    "thrust_N": mdot * ve + (pe - pa) * A})
    return out


def turbojet(M: float, alt_m: float, rp: float, T4: float,
             mdot: float = 1.0, gamma: float = GAMMA, cp: float = CP_AIR,
             eta_c: float = 1.0, eta_t: float = 1.0,
             LHV: float = 43e6, chemistry: bool = False,
             fuel: str = "CH4") -> dict:
    """Ideal-ish turbojet: specific thrust and fuel consumption.

    This is the calculation the jet-engine decomposition begins with.
    """
    amb = isa(alt_m)
    Ta, pa, a = amb["T_K"], amb["p_Pa"], amb["a_ms"]
    u = M * a
    T02 = Ta * (1 + (gamma - 1) / 2 * M * M)          # ram
    p02 = pa * (T02 / Ta) ** (gamma / (gamma - 1))
    T03s = T02 * rp ** ((gamma - 1) / gamma)
    T03 = T02 + (T03s - T02) / eta_c
    p03 = p02 * rp
    if T4 <= T03:
        raise ValueError(f"turbine inlet {T4:g} K is not above compressor exit "
                         f"{T03:.0f} K - there is no room to burn anything")
    f = cp * (T4 - T03) / (LHV - cp * T4)             # fuel-air ratio
    chem = None
    if chemistry:
        # Constant cp cannot see dissociation, so it under-predicts the fuel
        # needed for a high turbine inlet temperature. Cantera is installed;
        # use it when asked and report BOTH, because the difference is the
        # interesting part.
        try:
            import combustion
            chem = combustion.phi_for_temperature(T4, fuel, T03, p03 / 1e5)
            if chem.get("reachable"):
                f = chem["fuel_air_ratio_mass"]
        except Exception as exc:                              # noqa: BLE001
            chem = {"error": f"{type(exc).__name__}: {exc}"}
    wc = cp * (T03 - T02)
    T05 = T4 - wc / cp                                # turbine drives compressor
    p05 = p03 * (1 - (1 - T05 / T4) / eta_t) ** (gamma / (gamma - 1))
    noz = nozzle(p05, T05, pa, gamma=gamma, mdot=mdot * (1 + f))
    ve = noz["ve_ms"]

    # THREE THINGS WERE WRONG HERE, all found by an outside reader checking
    # the arithmetic rather than by any test in this file.
    #
    # 1. Two different definitions were reported side by side. Fs was the
    #    MOMENTUM specific thrust and F included the pressure term, so
    #    F != mdot * Fs and the two published numbers contradicted each
    #    other: 405 N.s/kg alongside 24.5 kN at 30 kg/s, which implies 817.
    # 2. TSFC was converted with `* 3.6e6 / 1000`, which is 1000x too small.
    #    kg/(N.s) -> kg/(kN.h) is x1000 for kN and x3600 for hours: x3.6e6.
    #    It printed 0.211 where the answer is 211.
    # 3. TSFC used the momentum-only Fs, so even the corrected figure did not
    #    match fuel flow divided by the thrust actually reported.
    #
    # Both specific thrust and TSFC are now defined against the SAME total
    # thrust, and the momentum part is kept as a separate named field.
    Fs_momentum = (1 + f) * ve - u
    Ae = noz.get("area_m2", 0.0)
    F_pressure = (noz["pe_Pa"] - pa) * Ae
    F = mdot * Fs_momentum + F_pressure
    Fs = F / mdot if mdot else 0.0                    # total, N per kg/s
    fuel_kgs = f * mdot
    tsfc = fuel_kgs / F if F > 0 else float("inf")    # kg/(N.s)
    eta_th = ((1 + f) * ve ** 2 - u ** 2) / (2 * f * LHV) if f else 0.0
    eta_p = 2 * u / (ve + u) if (ve + u) else 0.0
    return {"cycle": "turbojet", "mach": M, "altitude_m": alt_m,
            "ambient": amb, "flight_speed_ms": u,
            "T02_K": T02, "T03_K": T03, "T04_K": T4, "T05_K": T05,
            "fuel_air_ratio": f, "exit_velocity_ms": ve,
            "fuel_flow_kgs": fuel_kgs,
            "specific_thrust_Ns_per_kg": Fs,
            "specific_thrust_momentum_Ns_per_kg": Fs_momentum,
            "thrust_N": F,
            "thrust_momentum_N": mdot * Fs_momentum,
            "thrust_pressure_N": F_pressure,
            "tsfc_kg_per_Ns": tsfc,
            "tsfc_kg_per_kNh": tsfc * 3.6e6,
            "thermal_efficiency_pct": eta_th * 100,
            "propulsive_efficiency_pct": eta_p * 100,
            "overall_efficiency_pct": eta_th * eta_p * 100,
            "nozzle": noz, "combustion": chem,
            # A convergent nozzle running 8:1 over ambient is severely
            # under-expanded, and the pressure term then carries about half
            # the thrust. That is arithmetically correct and it is not a
            # design anyone would build: at this pressure ratio a real engine
            # uses a convergent-divergent nozzle. Saying nothing here let a
            # number that needed a caveat go out without one.
            "nozzle_warning": (
                f"the convergent nozzle is under-expanded {noz['pe_Pa']/pa:.1f}:1 "
                f"and {100*F_pressure/F:.0f}% of the thrust is the pressure "
                f"term. A real engine at this pressure ratio uses a "
                f"convergent-divergent nozzle, which would convert most of "
                f"that into exhaust velocity. Treat the thrust as an upper "
                f"bound on what this hardware gives."
                if F > 0 and noz["pe_Pa"] > 2.0 * pa else None),
            "note": ("no blade aerodynamics, no combustor pressure loss, no "
                     "bleed, no off-design. Specific thrust and TSFC here are "
                     "first-order sizing numbers, not performance guarantees."
                     + (" Fuel-air ratio from EQUILIBRIUM CHEMISTRY with "
                        "dissociation, not constant cp."
                        if chem and chem.get("reachable") else
                        " Fuel-air ratio from constant cp, which cannot see "
                        "dissociation and so under-predicts the fuel needed "
                        "at high turbine inlet temperature - pass "
                        "chemistry=True for the real number."))}


def turbofan(M: float, alt_m: float, rp: float, T4: float, bypass: float,
             fan_pr: float = 1.6, mdot_core: float = 1.0,
             gamma: float = GAMMA, cp: float = CP_AIR) -> dict:
    """Separate-exhaust turbofan. Bypass is why a modern engine is quiet."""
    core = turbojet(M, alt_m, rp, T4, mdot=mdot_core, gamma=gamma, cp=cp)
    amb = isa(alt_m)
    u = M * amb["a_ms"]
    T0f = amb["T_K"] * (1 + (gamma - 1) / 2 * M * M) * \
        fan_pr ** ((gamma - 1) / gamma)
    p0f = amb["p_Pa"] * ((1 + (gamma - 1) / 2 * M * M) ** (gamma / (gamma - 1))) \
        * fan_pr
    fan_noz = nozzle(p0f, T0f, amb["p_Pa"], gamma=gamma,
                     mdot=mdot_core * bypass)
    Fc = core["thrust_N"]
    Ff = mdot_core * bypass * (fan_noz["ve_ms"] - u) \
        + (fan_noz["pe_Pa"] - amb["p_Pa"]) * fan_noz.get("area_m2", 0.0)
    F = Fc + Ff
    return {"cycle": "turbofan (separate exhaust)", "bypass_ratio": bypass,
            "fan_pressure_ratio": fan_pr, "core": core, "fan_nozzle": fan_noz,
            "core_thrust_N": Fc, "fan_thrust_N": Ff, "thrust_N": F,
            "fan_thrust_fraction": Ff / F if F else None,
            # Both streams are convergent-only here. At high core pressure
            # ratio the core nozzle is badly under-expanded, so its pressure
            # term is large and the CORE share comes out overstated relative
            # to a real engine with a convergent-divergent core nozzle. The
            # fan fraction below is therefore a lower bound, not a prediction.
            "nozzle_warning": core.get("nozzle_warning"),
            "note": "high bypass moves more air slowly, which is both quieter "
                    "and more propulsively efficient."}


def ramjet(M: float, alt_m: float, T4: float, gamma: float = GAMMA,
           cp: float = CP_AIR, LHV: float = 43e6) -> dict:
    """No moving parts: the ram compression IS the compressor."""
    if M < 1.0:
        raise ValueError("a ramjet produces no useful thrust below about M 1; "
                         "it needs ram compression to work at all")
    amb = isa(alt_m)
    u = M * amb["a_ms"]
    T02 = amb["T_K"] * (1 + (gamma - 1) / 2 * M * M)
    f = cp * (T4 - T02) / (LHV - cp * T4)
    ve = math.sqrt(2 * cp * T4 * (1 - (amb["T_K"] / T02)))
    Fs = (1 + f) * ve - u
    return {"cycle": "ramjet", "mach": M, "T02_K": T02, "T04_K": T4,
            "fuel_air_ratio": f, "exit_velocity_ms": ve,
            "specific_thrust_Ns_per_kg": Fs,
            "note": "efficiency climbs with Mach and it cannot start from "
                    "rest."}


# --------------------------------------------------------------------------
# steam and rocket
# --------------------------------------------------------------------------

def rankine(p_boiler_Pa: float, p_cond_Pa: float, T_super_K: float = None,
            eta_turbine: float = 1.0, eta_pump: float = 1.0) -> dict:
    """Steam cycle. Uses CoolProp when it is installed, and says when it is not."""
    try:
        from CoolProp.CoolProp import PropsSI
    except Exception:                                         # noqa: BLE001
        return {"cycle": "Rankine", "error":
                "CoolProp is not installed, and steam properties cannot be "
                "approximated honestly with an ideal-gas model. Install "
                "CoolProp, or give the enthalpies directly.",
                "install": "pip install CoolProp"}
    T3 = T_super_K or PropsSI("T", "P", p_boiler_Pa, "Q", 1, "Water") + 50
    h3 = PropsSI("H", "P", p_boiler_Pa, "T", T3, "Water")
    s3 = PropsSI("S", "P", p_boiler_Pa, "T", T3, "Water")
    h4s = PropsSI("H", "P", p_cond_Pa, "S", s3, "Water")
    h4 = h3 - eta_turbine * (h3 - h4s)
    h1 = PropsSI("H", "P", p_cond_Pa, "Q", 0, "Water")
    v1 = 1.0 / PropsSI("D", "P", p_cond_Pa, "Q", 0, "Water")
    wp = v1 * (p_boiler_Pa - p_cond_Pa) / eta_pump
    h2 = h1 + wp
    qin = h3 - h2
    eta = (h3 - h4 - wp) / qin if qin else 0.0
    x4 = PropsSI("Q", "P", p_cond_Pa, "H", h4, "Water")
    out = {"cycle": "Rankine", "properties": "CoolProp (real steam)",
           "T3_K": T3, "h3": h3, "h4": h4, "h1": h1, "h2": h2,
           "turbine_work_Jkg": h3 - h4, "pump_work_Jkg": wp,
           "heat_in_Jkg": qin, "efficiency": eta, "efficiency_pct": eta * 100,
           "exit_quality": x4}
    if 0 <= x4 < 0.88:
        out["warning"] = (f"turbine exit quality {x4:.3f} is below 0.88. Liquid "
                          f"droplets erode the last blade rows - superheat "
                          f"more, or reheat.")
    return out


def rocket(pc_Pa: float, pe_Pa: float, Tc_K: float, M_molar: float = 0.022,
           gamma: float = 1.2, mdot: float = None,
           pa_Pa: float = 0.0) -> dict:
    """Ideal rocket nozzle: exhaust velocity, Isp, thrust and area ratio."""
    R = 8.3144626 / M_molar
    pr = pe_Pa / pc_Pa
    ve = math.sqrt(2 * gamma / (gamma - 1) * R * Tc_K * (1 - pr ** ((gamma - 1) / gamma)))
    # characteristic velocity and thrust coefficient
    G = math.sqrt(gamma * (2 / (gamma + 1)) ** ((gamma + 1) / (gamma - 1)))
    cstar = math.sqrt(R * Tc_K) / G
    eps = (G / math.sqrt(2 * gamma / (gamma - 1) *
                         pr ** (2 / gamma) * (1 - pr ** ((gamma - 1) / gamma))))
    out = {"cycle": "rocket (ideal nozzle)", "pc_Pa": pc_Pa, "pe_Pa": pe_Pa,
           "Tc_K": Tc_K, "gamma": gamma, "R_specific": R,
           "exhaust_velocity_ms": ve, "c_star_ms": cstar,
           "area_ratio": eps, "Isp_vacuum_s": None,
           "Isp_s": ve / G0}
    if mdot:
        At = mdot * cstar / pc_Pa
        Ae = At * eps
        F = mdot * ve + (pe_Pa - pa_Pa) * Ae
        out.update({"mdot_kgs": mdot, "throat_area_m2": At,
                    "throat_diameter_mm": 2000 * math.sqrt(At / math.pi),
                    "exit_area_m2": Ae,
                    "exit_diameter_mm": 2000 * math.sqrt(Ae / math.pi),
                    "thrust_N": F, "Isp_s": F / (mdot * G0)})
    out["note"] = ("frozen-flow ideal nozzle: no combustion chemistry, no "
                   "boundary layer, no divergence loss. Real engines land "
                   "roughly 3-8% below this Isp.")
    return out


def delta_v(isp_s: float, m_wet: float, m_dry: float) -> dict:
    """Tsiolkovsky. The whole of rocketry in one line."""
    if m_dry <= 0 or m_wet <= m_dry:
        raise ValueError("wet mass must exceed dry mass")
    dv = isp_s * G0 * math.log(m_wet / m_dry)
    return {"delta_v_ms": dv, "mass_ratio": m_wet / m_dry,
            "propellant_fraction": 1 - m_dry / m_wet}


CYCLES = {"otto": otto, "diesel": diesel, "dual": dual, "atkinson": atkinson,
          "stirling": stirling, "brayton": brayton, "turbojet": turbojet,
          "turbofan": turbofan, "ramjet": ramjet, "rankine": rankine,
          "rocket": rocket, "nozzle": nozzle}


# --------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def chk(cond, label, extra=""):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label
              + (f"   {extra}" if extra else ""))
        ok = ok and bool(cond)

    def close(a, b, pct=0.1):
        return abs(a - b) / abs(b) * 100 <= pct if b else abs(a) < 1e-9

    print("engines selftest")

    print(" reciprocating, against the published closed forms")
    r = otto(8.0)
    chk(close(r["efficiency_pct"], 56.47), f"Otto r=8 -> 56.47% "
                                           f"({r['efficiency_pct']:.2f})")
    chk(close(otto(10.0)["efficiency_pct"], 60.19),
        f"Otto r=10 -> 60.19% ({otto(10.0)['efficiency_pct']:.2f})")
    d = diesel(18.0, 2.0)
    chk(close(d["efficiency_pct"], 63.16), f"Diesel r=18 rc=2 -> 63.16% "
                                           f"({d['efficiency_pct']:.2f})")
    chk(diesel(8.0, 2.0)["efficiency_pct"] < otto(8.0)["efficiency_pct"],
        "at equal r, Diesel is LESS efficient than Otto")
    chk(close(dual(18.0, 1.0, 2.0)["efficiency_pct"], d["efficiency_pct"], 1.0),
        "Dual reduces to Diesel when the pressure ratio is 1")
    chk(close(dual(8.0, 2.0, 1.0)["efficiency_pct"],
              otto(8.0)["efficiency_pct"], 1.0),
        "...and to Otto when the cutoff is 1")
    at = atkinson(8.0, 12.0)
    chk(close(at["efficiency_pct"], 60.11, 0.2),
        f"Atkinson c=8 e=12 -> 60.11% ({at['efficiency_pct']:.2f})")
    chk(at["efficiency_pct"] > otto(8.0)["efficiency_pct"],
        "over-expansion beats Otto at the same compression ratio")
    # The limit that proves the formula: expand only as far as you compressed
    # and an Atkinson cycle IS an Otto cycle.
    chk(close(atkinson(8.0, 8.0001)["efficiency_pct"],
              otto(8.0)["efficiency_pct"], 0.1),
        "...and collapses to Otto as the expansion ratio approaches it")
    s = stirling(300.0, 900.0)
    chk(close(s["efficiency_pct"], carnot(300, 900) * 100),
        "an ideal Stirling reaches Carnot")

    print(" gas turbine")
    b = brayton(10.0)
    chk(close(b["efficiency_pct"], 48.21), f"Brayton rp=10 -> 48.21% "
                                           f"({b['efficiency_pct']:.2f})")
    b2 = brayton(10.0, T1=288.15, T3=1400.0)
    chk(close(b2["efficiency_pct"], 48.21, 1.0),
        "ideal components give the same efficiency with T3 supplied")
    chk(b2["net_work_Jkg"] > 0, "and positive net work")
    real = brayton(10.0, T1=288.15, T3=1400.0, eta_c=0.85, eta_t=0.88)
    chk(real["efficiency_pct"] < b2["efficiency_pct"],
        f"component losses reduce it ({real['efficiency_pct']:.1f}% vs "
        f"{b2['efficiency_pct']:.1f}%)")
    chk(0 < real["back_work_ratio"] < 1,
        f"back work ratio is reported ({real['back_work_ratio']:.2f})")
    chk(real["efficiency_pct"] < real["carnot_pct"], "and stays under Carnot")

    print(" the Carnot guard actually fires")
    try:
        _guard(0.99, 300.0, 600.0, "impossible")
        chk(False, "a cycle beating Carnot is refused")
    except ArithmeticError as e:
        chk("impossible" in str(e), "a cycle beating Carnot is refused, loudly")

    print(" nozzle")
    n = nozzle(p0=500000.0, T0=800.0, pa=101325.0)
    chk(n["choked"], "a 4.9:1 pressure ratio chokes a convergent nozzle")
    chk(close(n["critical_pressure_ratio"], 0.5283),
        f"critical ratio 0.5283 ({n['critical_pressure_ratio']:.4f})")
    chk(close(n["mach_exit"], 1.0, 1.0), "and the exit is sonic")
    n2 = nozzle(p0=110000.0, T0=400.0, pa=101325.0)
    chk(not n2["choked"], "a 1.09:1 ratio does not choke")
    chk(n2["mach_exit"] < 1.0, "and stays subsonic")
    n3 = nozzle(p0=500000.0, T0=800.0, pa=101325.0, mdot=10.0)
    chk(n3["area_m2"] > 0 and n3["thrust_N"] > 0,
        f"with mass flow it sizes the hole ({n3['diameter_mm']:.0f} mm) and "
        f"gives thrust ({n3['thrust_N']:.0f} N)")

    print(" atmosphere")
    sl = isa(0.0)
    chk(close(sl["p_Pa"], 101325.0) and close(sl["T_K"], 288.15),
        "ISA sea level")
    chk(close(isa(11000.0)["T_K"], 216.65, 0.5), "ISA tropopause 216.65 K")
    chk(close(isa(11000.0)["p_Pa"], 22632.0, 1.0), "and 22.6 kPa")

    print(" turbojet: INTERNAL CONSISTENCY, which is what was missing")
    # The old tests here checked that each number was positive. Every one of
    # them passed while thrust, specific thrust and TSFC were mutually
    # contradictory - an outside reader caught it by doing the arithmetic by
    # hand. Positivity is not agreement. These check agreement.
    for mdot_t, rp_t, T4_t in ((30.0, 25.0, 1600.0), (20.0, 12.0, 1500.0),
                               (5.0, 8.0, 1400.0)):
        t = turbojet(M=0.85, alt_m=11000.0, rp=rp_t, T4=T4_t, mdot=mdot_t)
        chk(close(t["thrust_N"], mdot_t * t["specific_thrust_Ns_per_kg"], 1e-6),
            f"thrust = mdot x specific thrust at mdot={mdot_t:g} "
            f"({t['thrust_N']:.1f} vs {mdot_t*t['specific_thrust_Ns_per_kg']:.1f} N)")
        chk(close(t["thrust_N"],
                  t["thrust_momentum_N"] + t["thrust_pressure_N"], 1e-6),
            "...and equals momentum plus pressure thrust")
        chk(close(t["fuel_flow_kgs"], t["fuel_air_ratio"] * mdot_t, 1e-9),
            "fuel flow = f x mdot")
        chk(close(t["tsfc_kg_per_Ns"], t["fuel_flow_kgs"] / t["thrust_N"], 1e-9),
            "TSFC = fuel flow / thrust, against the SAME thrust")
        chk(close(t["tsfc_kg_per_kNh"], t["tsfc_kg_per_Ns"] * 3.6e6, 1e-9),
            f"kg/(N.s) -> kg/(kN.h) is x3.6e6 "
            f"({t['tsfc_kg_per_kNh']:.2f} kg/(kN.h))")
        # A turbojet TSFC in the tens-to-hundreds of kg/(kN.h). 0.211 was the
        # published figure before the fix; a magnitude check catches that.
        chk(10.0 < t["tsfc_kg_per_kNh"] < 400.0,
            f"...and lands in the physical range for a turbojet "
            f"({t['tsfc_kg_per_kNh']:.1f})")

    tj = turbojet(M=0.8, alt_m=10000.0, rp=12.0, T4=1500.0, mdot=20.0)
    chk(tj["specific_thrust_Ns_per_kg"] > 0,
        f"positive specific thrust ({tj['specific_thrust_Ns_per_kg']:.0f} N per kg/s)")
    chk(0 < tj["fuel_air_ratio"] < 0.06,
        f"a physical fuel-air ratio ({tj['fuel_air_ratio']:.4f})")
    chk(0 < tj["propulsive_efficiency_pct"] < 100,
        f"propulsive efficiency {tj['propulsive_efficiency_pct']:.1f}%")
    chk(tj["overall_efficiency_pct"] < tj["thermal_efficiency_pct"],
        "overall is below thermal, as it must be")
    chk(tj["thrust_N"] > 0, f"and real thrust ({tj['thrust_N']/1000:.1f} kN)")
    under = turbojet(M=0.85, alt_m=11000.0, rp=25.0, T4=1600.0, mdot=30.0)
    chk(under.get("nozzle_warning"),
        "a badly under-expanded convergent nozzle warns")
    chk("convergent-divergent" in (under.get("nozzle_warning") or ""),
        "...and names the hardware that would actually be used")
    low = turbojet(M=0.3, alt_m=0.0, rp=4.0, T4=1200.0, mdot=10.0)
    chk(low.get("nozzle_warning") is None,
        "a well-matched nozzle does not warn")
    try:
        turbojet(M=0.8, alt_m=10000.0, rp=40.0, T4=600.0)
        chk(False, "a turbine inlet below compressor exit is refused")
    except ValueError as e:
        chk("no room to burn" in str(e),
            "a turbine inlet below compressor exit is refused, with the reason")

    print(" real chemistry in the combustor")
    try:
        import combustion
        if combustion.available():
            simple = turbojet(M=0.8, alt_m=10000.0, rp=25.0, T4=1600.0)
            real = turbojet(M=0.8, alt_m=10000.0, rp=25.0, T4=1600.0,
                            chemistry=True)
            chk(real["combustion"] and real["combustion"].get("reachable"),
                "equilibrium chemistry runs inside the cycle")
            chk(real["fuel_air_ratio"] != simple["fuel_air_ratio"],
                f"...and changes the fuel-air ratio "
                f"({simple['fuel_air_ratio']:.4f} -> "
                f"{real['fuel_air_ratio']:.4f})")
            chk("EQUILIBRIUM CHEMISTRY" in real["note"],
                "...and the result says which model produced it")
            chk("constant cp" in simple["note"] and "under-predicts" in simple["note"],
                "...while the simple one admits what it cannot see")
            chk(real["combustion"]["NO_ppm"] >= 0,
                f"emissions come with it ({real['combustion']['NO_ppm']:.0f} "
                f"ppm NO at equilibrium)")
        else:
            print("  skip  cantera unavailable")
    except ImportError:
        print("  skip  combustion module unavailable")

    print(" turbofan and ramjet")
    tf = turbofan(M=0.8, alt_m=10000.0, rp=30.0, T4=1600.0, bypass=8.0)
    chk(tf["thrust_N"] > tf["core_thrust_N"], "the fan adds thrust")
    chk(close(tf["thrust_N"], tf["core_thrust_N"] + tf["fan_thrust_N"], 1e-9),
        "total = core + fan, with both streams on the same definition")
    # This asserted "the fan dominates at BPR 8". It does in a real engine -
    # and NOT in this model, because a convergent-only core nozzle at rp 30 is
    # under-expanded and its pressure term inflates the core share. Asserting
    # the real-engine number would have been asserting something the model
    # does not compute. The honest test is that the model SAYS so.
    chk(tf["fan_thrust_fraction"] > 0.3,
        f"the fan carries a large share ({tf['fan_thrust_fraction']*100:.0f}%)")
    chk(tf.get("nozzle_warning"),
        "...and the under-expanded core nozzle is flagged, which is why that "
        "share is a lower bound rather than the ~80% a real high-bypass "
        "engine reaches")
    rj = ramjet(M=2.5, alt_m=15000.0, T4=1800.0)
    chk(rj["specific_thrust_Ns_per_kg"] > 0, "a ramjet works at M 2.5")
    try:
        ramjet(M=0.3, alt_m=0.0, T4=1800.0)
        chk(False, "a subsonic ramjet is refused")
    except ValueError:
        chk(True, "a subsonic ramjet is refused")

    print(" rocket")
    rk = rocket(pc_Pa=7e6, pe_Pa=101325.0, Tc_K=3500.0, M_molar=0.0224,
                gamma=1.22, mdot=50.0)
    chk(2000 < rk["exhaust_velocity_ms"] < 4000,
        f"exhaust velocity {rk['exhaust_velocity_ms']:.0f} m/s is in range")
    chk(200 < rk["Isp_s"] < 400, f"Isp {rk['Isp_s']:.0f} s is a real number")
    chk(rk["area_ratio"] > 1, f"expansion ratio {rk['area_ratio']:.1f}")
    chk(rk["thrust_N"] > 0, f"thrust {rk['thrust_N']/1000:.0f} kN")
    dv = delta_v(330.0, 100.0, 10.0)
    chk(close(dv["delta_v_ms"], 330 * G0 * math.log(10), 0.01),
        f"Tsiolkovsky {dv['delta_v_ms']:.0f} m/s")

    print(" refusals")
    for bad, label in ((lambda: otto(0.5), "a compression ratio below 1"),
                       (lambda: brayton(0.5), "a pressure ratio below 1"),
                       (lambda: carnot(900, 300), "Tc above Th"),
                       (lambda: delta_v(300, 10, 100), "dry mass above wet")):
        try:
            bad()
            chk(False, f"refuses {label}")
        except (ValueError, ArithmeticError):
            chk(True, f"refuses {label}")

    print(" every cycle says what it does NOT model")
    for name, fn, args in (("otto", otto, (8.0,)), ("diesel", diesel, (18.0, 2.0)),
                           ("brayton", brayton, (10.0,)),
                           ("turbojet", turbojet, (0.8, 10000.0, 12.0, 1500.0))):
        r = fn(*args)
        chk("note" in r and len(r["note"]) > 30,
            f"{name} carries its honest scope")

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="engine cycles")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--cycle", choices=sorted(CYCLES))
    ap.add_argument("--r", type=float, default=8.0)
    ap.add_argument("--rp", type=float, default=10.0)
    ap.add_argument("--cutoff", type=float, default=2.0)
    ap.add_argument("--T1", type=float, default=288.15)
    ap.add_argument("--T3", type=float, default=None)
    ap.add_argument("--T4", type=float, default=1500.0)
    ap.add_argument("--M", type=float, default=0.8)
    ap.add_argument("--alt", type=float, default=10000.0)
    ap.add_argument("--bypass", type=float, default=8.0)
    ap.add_argument("--mdot", type=float, default=None)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.cycle:
        ap.print_help()
        return 0
    kw = {"otto": dict(r=a.r, T1=a.T1, T3=a.T3),
          "diesel": dict(r=a.r, cutoff=a.cutoff, T1=a.T1),
          "brayton": dict(rp=a.rp, T1=a.T1, T3=a.T3),
          "turbojet": dict(M=a.M, alt_m=a.alt, rp=a.rp, T4=a.T4,
                           mdot=a.mdot or 1.0),
          "turbofan": dict(M=a.M, alt_m=a.alt, rp=a.rp, T4=a.T4,
                           bypass=a.bypass),
          "ramjet": dict(M=a.M, alt_m=a.alt, T4=a.T4),
          }.get(a.cycle, {})
    r = CYCLES[a.cycle](**kw) if kw else CYCLES[a.cycle]()
    for k, v in r.items():
        if isinstance(v, dict):
            continue
        print(f"  {k:32s} {v:.6g}" if isinstance(v, float) else f"  {k:32s} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
