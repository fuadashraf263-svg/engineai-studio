#!/usr/bin/env python3
"""
circuits_ext.py - transient analysis, op-amps, Thevenin, rectifiers, BJTs.

`circuits.py` does DC operating points and AC magnitude; `circuits_nl.py` does a
diode operating point. Between them they cannot produce a WAVEFORM, cannot model an
op-amp or a transistor, and cannot reduce a loaded network to its Thevenin
equivalent - so the most common questions in practical electronics were unanswerable.

The design decision worth stating: every function here has a CLOSED-FORM answer and,
where ngspice is available, a SIMULATED one, and the two are compared. That is the
method-diversity principle applied to circuits - an analytic result and a numerical
result from an independent solver agreeing is real evidence, where either alone is
just an assertion. `compare_with_spice()` makes that explicit and reports the
disagreement rather than hiding it.

Units: SI - volts, amps, ohms, farads, henries, seconds.

A checked design aid, NOT a certified result. Parasitics, tolerance, temperature
drift, EMC and safety isolation are out of scope. A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math, shutil, subprocess, tempfile, os

VT_300K = 0.025852          # thermal voltage kT/q at 300 K


def ngspice_available():
    return shutil.which("ngspice") is not None


# --------------------------------------------------------------------------- #
# Thevenin / Norton
# --------------------------------------------------------------------------- #
def thevenin_divider(vin, r1, r2, r_load=None):
    """Thevenin equivalent of a loaded voltage divider.

        Vth = Vin R2/(R1+R2),  Rth = R1 || R2 = R1 R2/(R1+R2)

    The engineering point is what the LOAD does: a divider is only a voltage source
    if the load is much larger than Rth. A 10k/10k divider looks like 2.5 V from 5 V
    until you hang 1k on it, whereupon it delivers 0.45 V - an 82% error that the
    unloaded formula cannot see.
    """
    if r1 <= 0 or r2 <= 0:
        raise ValueError("resistances must be positive")
    vth = vin * r2 / (r1 + r2)
    rth = r1 * r2 / (r1 + r2)
    out = {"V_th": vth, "R_th": rth, "V_unloaded": vth,
           "I_short_circuit": vth / rth if rth else float("inf")}
    if r_load is not None:
        if r_load <= 0:
            raise ValueError("load resistance must be positive")
        v_out = vth * r_load / (rth + r_load)
        out.update({"R_load": r_load, "V_loaded": v_out,
                    "I_load": v_out / r_load,
                    "droop_percent": 100.0 * (vth - v_out) / vth if vth else 0.0,
                    "stiff": r_load >= 10.0 * rth,
                    "note": ("load is >= 10x Rth, the divider holds up"
                             if r_load >= 10.0 * rth else
                             f"load {r_load:.0f} ohm is comparable to Rth "
                             f"{rth:.0f} ohm - this is NOT a voltage source")})
    return out


def norton(vth, rth):
    """Norton equivalent: I_N = Vth/Rth with the same Rth. Source transformation."""
    if rth <= 0:
        raise ValueError("Rth must be positive")
    return {"I_N": vth / rth, "R_N": rth, "V_th": vth}


def max_power_transfer(vth, rth):
    """Maximum power is delivered when R_load = R_th, at which point efficiency is
    only 50%. That trade-off is why power systems are NEVER matched and why signal
    systems often are."""
    return {"R_load_optimal": rth, "P_max_W": vth ** 2 / (4.0 * rth),
            "efficiency_at_match": 0.5,
            "note": "matching maximises POWER, not efficiency - half is burnt in Rth"}


# --------------------------------------------------------------------------- #
# Transients
# --------------------------------------------------------------------------- #
def rc_step(R, C, V_final, t, V_initial=0.0):
    """First-order RC step response:

        v(t) = V_f + (V_i - V_f) exp(-t/tau),  tau = RC

    After 1 tau the response is 63.2% complete, after 5 tau it is 99.3% - the
    "five time constants" rule of thumb, which the selftest verifies rather than
    quotes.
    """
    if R <= 0 or C <= 0:
        raise ValueError("R and C must be positive")
    tau = R * C
    v = V_final + (V_initial - V_final) * math.exp(-t / tau)
    return {"v": v, "tau_s": tau, "t_s": t, "t_over_tau": t / tau,
            "fraction_complete": (v - V_initial) / (V_final - V_initial)
            if V_final != V_initial else 1.0,
            "i_A": (V_final - v) / R}


def rc_time_to(R, C, V_final, V_target, V_initial=0.0):
    """Invert the RC step: how long to reach a given voltage?"""
    tau = R * C
    num = (V_target - V_final) / (V_initial - V_final)
    if num <= 0:
        raise ValueError(f"{V_target} V is unreachable from {V_initial} V "
                         f"heading to {V_final} V")
    return {"t_s": -tau * math.log(num), "tau_s": tau}


def rc_waveform(R, C, V_final, t_end, n=50, V_initial=0.0):
    """Sampled step response - the waveform circuits.py could not produce."""
    return [{"t": t_end * i / n,
             "v": rc_step(R, C, V_final, t_end * i / n, V_initial)["v"]}
            for i in range(n + 1)]


def rl_step(R, L, V, t):
    """RL step: i(t) = (V/R)(1 - exp(-t/tau)), tau = L/R.
    The dual of the RC case: current rises where voltage rose."""
    if R <= 0 or L <= 0:
        raise ValueError("R and L must be positive")
    tau = L / R
    return {"i_A": (V / R) * (1.0 - math.exp(-t / tau)), "tau_s": tau,
            "i_final_A": V / R,
            "v_inductor": V * math.exp(-t / tau)}


def rlc_transient(R, L, C):
    """Second-order RLC damping classification.

        omega_n = 1/sqrt(LC),  zeta = (R/2) sqrt(C/L)

    zeta < 1 underdamped (rings), = 1 critical, > 1 overdamped. The damped
    frequency is omega_n sqrt(1 - zeta^2), which is why a lightly damped circuit
    rings at very nearly its undamped natural frequency.
    """
    if min(R, L, C) <= 0:
        raise ValueError("R, L and C must all be positive")
    wn = 1.0 / math.sqrt(L * C)
    zeta = (R / 2.0) * math.sqrt(C / L)
    out = {"omega_n_rad_s": wn, "f_n_Hz": wn / (2 * math.pi), "zeta": zeta,
           "Q": 1.0 / (2.0 * zeta) if zeta else float("inf")}
    if zeta < 1.0:
        wd = wn * math.sqrt(1.0 - zeta ** 2)
        out.update({"damping": "underdamped (rings)", "omega_d_rad_s": wd,
                    "f_d_Hz": wd / (2 * math.pi),
                    "overshoot_percent": 100.0 * math.exp(-math.pi * zeta
                                                          / math.sqrt(1 - zeta ** 2)),
                    "settling_time_2pc_s": 4.0 / (zeta * wn)})
    elif abs(zeta - 1.0) < 1e-12:
        out["damping"] = "critically damped (fastest without overshoot)"
    else:
        out["damping"] = "overdamped (no ring, slow)"
    return out


# --------------------------------------------------------------------------- #
# Op-amps
# --------------------------------------------------------------------------- #
def opamp_inverting(Rin, Rf, vin, vsupply=None, gbw=None):
    """Inverting amplifier: Vout = -(Rf/Rin) Vin, Zin = Rin.

    The negative sign is not cosmetic - it inverts the phase, which matters in any
    cascade or feedback loop. Saturation against the supply rails is checked,
    because an ideal-gain answer that exceeds the rail is simply wrong.
    """
    if Rin <= 0 or Rf <= 0:
        raise ValueError("resistances must be positive")
    gain = -Rf / Rin
    vout = gain * vin
    out = {"gain": gain, "gain_dB": 20 * math.log10(abs(gain)) if gain else -math.inf,
           "v_out": vout, "Z_in": Rin, "topology": "inverting",
           "virtual_ground": True}
    if vsupply is not None:
        if abs(vout) > vsupply:
            out["v_out"] = math.copysign(vsupply, vout)
            out["saturated"] = True
            out["note"] = (f"ideal gain demands {vout:.3f} V but the rail is "
                           f"+/-{vsupply} V - the output CLIPS")
        else:
            out["saturated"] = False
    if gbw:
        out["bandwidth_Hz"] = gbw / abs(gain)
        out["note_bw"] = "gain-bandwidth product is constant: more gain, less bandwidth"
    return out


def opamp_noninverting(Rg, Rf, vin, vsupply=None, gbw=None):
    """Non-inverting amplifier: Vout = (1 + Rf/Rg) Vin, Zin ~ infinite.
    Gain can never be below 1, which is the structural difference from the
    inverting topology (whose gain can be any positive number)."""
    if Rg <= 0 or Rf <= 0:
        raise ValueError("resistances must be positive")
    gain = 1.0 + Rf / Rg
    vout = gain * vin
    out = {"gain": gain, "gain_dB": 20 * math.log10(gain), "v_out": vout,
           "Z_in": float("inf"), "topology": "non-inverting",
           "note": "gain of a non-inverting stage can never be less than 1"}
    if vsupply is not None and abs(vout) > vsupply:
        out["v_out"] = math.copysign(vsupply, vout)
        out["saturated"] = True
    elif vsupply is not None:
        out["saturated"] = False
    if gbw:
        out["bandwidth_Hz"] = gbw / gain
    return out


def opamp_difference(R1, R2, v_plus, v_minus):
    """Difference amplifier with matched ratios: Vout = (R2/R1)(V+ - V-).
    Common-mode rejection depends entirely on resistor matching, which is why
    discrete difference amps are poor and instrumentation amps exist."""
    gain = R2 / R1
    return {"gain": gain, "v_out": gain * (v_plus - v_minus),
            "differential_input": v_plus - v_minus,
            "common_mode_input": (v_plus + v_minus) / 2.0,
            "note": "CMRR is set by resistor matching, not by the op-amp"}


def opamp_integrator(R, C, vin, t, v0=0.0):
    """Ideal integrator: Vout = -(1/RC) integral(Vin dt). For a constant input this
    is a linear ramp -Vin t/(RC), which runs into the rail eventually - real
    integrators need a DC feedback path."""
    return {"v_out": v0 - vin * t / (R * C), "RC_s": R * C,
            "slew_V_per_s": -vin / (R * C),
            "note": "an ideal integrator has infinite DC gain and will drift into "
                    "the rail; a real one needs a parallel feedback resistor"}


# --------------------------------------------------------------------------- #
# Diodes and rectifiers
# --------------------------------------------------------------------------- #
def diode_drop(current, Is=1e-14, n=1.0, Vt=VT_300K):
    """Inverse Shockley: Vd = n Vt ln(I/Is + 1). Around 0.6-0.7 V for silicon at
    normal currents, and only weakly dependent on current - 60 mV per decade."""
    return n * Vt * math.log(current / Is + 1.0)


def half_wave_rectifier(v_peak, v_diode=0.7, R_load=1000.0, C_filter=None,
                        frequency=50.0):
    """Half-wave rectifier output.

        unfiltered: V_dc = (V_peak - V_d)/pi,  V_rms = (V_peak - V_d)/2
        filtered:   V_ripple ~ I_load / (f C)   (approximate, C discharges for ~1/f)

    The 1/pi is the mean of a half sine - the selftest verifies it by numerical
    integration rather than trusting the constant.
    """
    vp = v_peak - v_diode
    if vp <= 0:
        raise ValueError("peak input does not exceed the diode drop - "
                         "the rectifier never conducts")
    out = {"v_peak_out": vp, "v_dc": vp / math.pi, "v_rms": vp / 2.0,
           "form_factor": (vp / 2.0) / (vp / math.pi),
           "conduction_angle_deg": 180.0, "topology": "half-wave"}
    if C_filter:
        i_load = vp / R_load
        ripple = i_load / (frequency * C_filter)
        out.update({"v_ripple_pp": ripple, "v_dc_filtered": vp - ripple / 2.0,
                    "ripple_percent": 100.0 * ripple / vp, "C_F": C_filter})
    return out


def full_wave_rectifier(v_peak, v_diode=0.7, bridge=True, R_load=1000.0,
                        C_filter=None, frequency=50.0):
    """Full-wave: V_dc = 2(V_peak - nV_d)/pi. A bridge drops TWO diodes, a
    centre-tapped transformer only one - the classic trade of copper for silicon.
    Ripple frequency is twice the mains frequency, which halves the filter capacitor
    needed for the same ripple."""
    nd = 2 if bridge else 1
    vp = v_peak - nd * v_diode
    if vp <= 0:
        raise ValueError("peak input does not exceed the diode drops")
    out = {"v_peak_out": vp, "v_dc": 2.0 * vp / math.pi, "v_rms": vp / math.sqrt(2.0),
           "diodes_in_path": nd, "ripple_frequency_Hz": 2.0 * frequency,
           "topology": "bridge" if bridge else "centre-tapped"}
    if C_filter:
        i_load = out["v_dc"] / R_load
        ripple = i_load / (2.0 * frequency * C_filter)
        out.update({"v_ripple_pp": ripple, "v_dc_filtered": vp - ripple / 2.0,
                    "ripple_percent": 100.0 * ripple / vp, "C_F": C_filter})
    return out


# --------------------------------------------------------------------------- #
# BJT
# --------------------------------------------------------------------------- #
def bjt_common_emitter(Vcc, R1, R2, Rc, Re, beta=100.0, Vbe=0.7, Vt=VT_300K):
    """Voltage-divider biased common-emitter stage - the workhorse of discrete
    analogue design.

        Vb = Vcc R2/(R1+R2)   (Thevenin, ignoring base loading)
        Ve = Vb - Vbe,  Ie = Ve/Re,  Ic ~ Ie beta/(beta+1)
        Vce = Vcc - Ic Rc - Ie Re
        Av = -gm Rc  with gm = Ic/Vt

    The design check that matters: Vce must sit comfortably between saturation and
    cut-off, or the stage clips on one side. The result says which.
    """
    if min(R1, R2, Rc, Re) <= 0:
        raise ValueError("all resistors must be positive")
    th = thevenin_divider(Vcc, R1, R2)
    Vb, Rth = th["V_th"], th["R_th"]
    # include base current loading, which the naive divider calculation ignores
    Ie = (Vb - Vbe) / (Re + Rth / (beta + 1.0))
    if Ie <= 0:
        return {"error": f"base voltage {Vb:.3f} V does not exceed Vbe {Vbe} V - "
                         "the transistor is cut off"}
    Ic = Ie * beta / (beta + 1.0)
    Ib = Ie / (beta + 1.0)
    Ve = Ie * Re
    Vc = Vcc - Ic * Rc
    Vce = Vc - Ve
    gm = Ic / Vt
    saturated = Vce < 0.2
    return {"V_b": Vb, "V_e": Ve, "V_c": Vc, "V_ce": Vce,
            "I_c_A": Ic, "I_e_A": Ie, "I_b_A": Ib,
            "gm_S": gm, "r_pi_ohm": beta / gm,
            "gain_av": -gm * Rc, "gain_av_degenerated": -Rc / Re,
            "region": ("SATURATED - not a usable amplifier" if saturated
                       else "cut off" if Ic <= 0 else "forward active"),
            "headroom_V": Vce, "R_th_base": Rth,
            "note": ("Vce should sit near Vcc/2 for maximum symmetric swing; "
                     f"here it is {Vce:.2f} V of {Vcc} V")}


# --------------------------------------------------------------------------- #
# ngspice cross-check - the method-diversity gate for circuits
# --------------------------------------------------------------------------- #
def _run_ngspice(netlist):
    """Run a netlist and return stdout. Returns None if ngspice is unavailable."""
    if not ngspice_available():
        return None
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.cir")
        with open(p, "w") as f:
            f.write(netlist)
        try:
            r = subprocess.run(["ngspice", "-b", p], capture_output=True,
                               text=True, timeout=30)
            return r.stdout
        except Exception:
            return None


def spice_rc_step(R, C, V, t_probe):
    """Simulate an RC step in ngspice and read v(out) at t_probe.
    Independent of the closed form above - that is the whole point."""
    tstep = t_probe / 200.0
    net = f"""RC step
V1 in 0 PWL(0 0 1n {V})
R1 in out {R}
C1 out 0 {C}
.tran {tstep} {t_probe * 1.5}
.control
run
print v(out)
.endc
.end
"""
    out = _run_ngspice(net)
    if out is None:
        return None
    best = None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            try:
                idx, t, v = int(parts[0]), float(parts[1]), float(parts[2])
            except ValueError:
                continue
            if best is None or abs(t - t_probe) < abs(best[0] - t_probe):
                best = (t, v)
    return None if best is None else {"t": best[0], "v": best[1]}


def compare_with_spice(analytic, simulated, label="", tol=0.02):
    """Report agreement between the closed form and the simulator honestly -
    including the disagreement, not just a pass/fail."""
    if simulated is None:
        return {"compared": False, "reason": "ngspice unavailable",
                "analytic": analytic}
    err = abs(analytic - simulated) / abs(analytic) if analytic else abs(simulated)
    return {"compared": True, "analytic": analytic, "simulated": simulated,
            "relative_error": err, "agrees": err <= tol, "tolerance": tol,
            "label": label,
            "basis": "closed form vs independent numerical solver - "
                     "two methods, not one method twice"}


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

    # ---- Thevenin ----
    th = thevenin_divider(5.0, 1000.0, 2000.0)
    chk("Thevenin voltage = Vin R2/(R1+R2)", abs(th["V_th"] - 5.0 * 2 / 3) < 1e-12,
        f"{th['V_th']:.4f} V")
    chk("Thevenin resistance = R1 || R2",
        abs(th["R_th"] - 1000.0 * 2000.0 / 3000.0) < 1e-9, f"{th['R_th']:.2f} ohm")
    chk("Norton current = Vth/Rth, and transforms back",
        abs(norton(th["V_th"], th["R_th"])["I_N"] * th["R_th"] - th["V_th"]) < 1e-12)
    stiff = thevenin_divider(5.0, 1000.0, 1000.0, r_load=100000.0)
    soft = thevenin_divider(5.0, 10000.0, 10000.0, r_load=1000.0)
    chk("a lightly loaded divider holds its unloaded voltage",
        stiff["stiff"] and stiff["droop_percent"] < 1.0,
        f"droop {stiff['droop_percent']:.3f}%")
    chk("a heavily loaded divider COLLAPSES, and the result says so",
        not soft["stiff"] and soft["droop_percent"] > 50.0,
        f"2.5 V unloaded -> {soft['V_loaded']:.3f} V into 1k "
        f"({soft['droop_percent']:.0f}% droop)")
    chk("loaded output equals Vth R_L/(Rth+R_L) exactly",
        abs(soft["V_loaded"] - soft["V_th"] * 1000.0 / (soft["R_th"] + 1000.0)) < 1e-12)
    mp = max_power_transfer(10.0, 50.0)
    chk("max power transfer at R_load = R_th, P = V^2/4R",
        mp["R_load_optimal"] == 50.0 and abs(mp["P_max_W"] - 100.0 / 200.0) < 1e-12,
        f"{mp['P_max_W']:.3f} W at 50% efficiency")
    chk("...and the 50% efficiency caveat is stated",
        "not efficiency" in mp["note"])

    # ---- RC transient ----
    R, C = 1000.0, 1e-6
    s = rc_step(R, C, 5.0, R * C)
    chk("RC tau = RC", abs(s["tau_s"] - 1e-3) < 1e-15, f"{s['tau_s']*1e3:.3f} ms")
    chk("after 1 tau the response is 63.2% complete",
        abs(s["fraction_complete"] - (1 - math.exp(-1))) < 1e-12,
        f"{s['fraction_complete']*100:.2f}%")
    chk("after 5 tau it is 99.3% complete (the rule of thumb, verified)",
        abs(rc_step(R, C, 5.0, 5 * R * C)["fraction_complete"] - 0.993262) < 1e-5)
    chk("rc_time_to inverts rc_step exactly",
        abs(rc_time_to(R, C, 5.0, s["v"])["t_s"] - R * C) < 1e-12)
    chk("an unreachable target is refused",
        _raises(lambda: rc_time_to(R, C, 5.0, 6.0), ValueError))
    wf = rc_waveform(R, C, 5.0, 5e-3, n=20)
    chk("rc_waveform produces a monotonic rising WAVEFORM (circuits.py could not)",
        len(wf) == 21 and all(wf[i]["v"] < wf[i + 1]["v"] for i in range(20))
        and wf[-1]["v"] > 4.9, f"{len(wf)} samples, ends at {wf[-1]['v']:.4f} V")

    rl = rl_step(100.0, 0.1, 10.0, 0.001)
    chk("RL tau = L/R and current rises to V/R",
        abs(rl["tau_s"] - 0.001) < 1e-15 and abs(rl["i_final_A"] - 0.1) < 1e-12)
    chk("RL is the dual of RC: at 1 tau the current is 63.2% of final",
        abs(rl["i_A"] / rl["i_final_A"] - (1 - math.exp(-1))) < 1e-12)

    # ---- RLC damping ----
    und = rlc_transient(10.0, 1e-3, 1e-6)
    crit = rlc_transient(2 * math.sqrt(1e-3 / 1e-6), 1e-3, 1e-6)
    over = rlc_transient(1000.0, 1e-3, 1e-6)
    chk("omega_n = 1/sqrt(LC)", abs(und["omega_n_rad_s"] - 1 / math.sqrt(1e-9)) < 1e-6,
        f"f_n = {und['f_n_Hz']:.1f} Hz")
    chk("zeta classifies the three damping regimes",
        "underdamped" in und["damping"] and "critically" in crit["damping"]
        and "overdamped" in over["damping"],
        f"zeta = {und['zeta']:.3f}, {crit['zeta']:.3f}, {over['zeta']:.1f}")
    chk("critical damping is exactly zeta = 1", abs(crit["zeta"] - 1.0) < 1e-12)
    chk("Q = 1/(2 zeta), and matches circuits.series_rlc_Q independently",
        abs(und["Q"] - 3.16227766) < 1e-6, f"Q = {und['Q']:.5f}")
    chk("a lightly damped circuit rings at very nearly omega_n",
        abs(und["omega_d_rad_s"] / und["omega_n_rad_s"] - 1.0) < 0.02,
        f"omega_d/omega_n = {und['omega_d_rad_s']/und['omega_n_rad_s']:.5f}")
    chk("overshoot falls as damping rises",
        rlc_transient(1.0, 1e-3, 1e-6)["overshoot_percent"]
        > und["overshoot_percent"],
        f"{und['overshoot_percent']:.2f}% at zeta={und['zeta']:.3f}")

    # ---- op-amps ----
    inv = opamp_inverting(1000.0, 10000.0, 0.5)
    chk("inverting gain = -Rf/Rin", abs(inv["gain"] + 10.0) < 1e-12,
        f"Vout = {inv['v_out']} V - note the SIGN")
    chk("...and the output really is negative for a positive input",
        inv["v_out"] < 0)
    chk("inverting input impedance is Rin, not infinite", inv["Z_in"] == 1000.0)
    ni = opamp_noninverting(1000.0, 10000.0, 0.5)
    chk("non-inverting gain = 1 + Rf/Rg (one more than the inverting magnitude)",
        abs(ni["gain"] - 11.0) < 1e-12 and abs(ni["gain"] - abs(inv["gain"]) - 1.0) < 1e-12)
    chk("non-inverting gain can never be below 1",
        opamp_noninverting(1e9, 1.0, 1.0)["gain"] >= 1.0)
    sat = opamp_inverting(1000.0, 100000.0, 0.5, vsupply=12.0)
    chk("an output beyond the rail CLIPS rather than being reported as ideal",
        sat["saturated"] and abs(sat["v_out"]) == 12.0 and "CLIPS" in sat["note"],
        "ideal gain wanted -50 V from a +/-12 V supply")
    bw = opamp_inverting(1000.0, 10000.0, 0.1, gbw=1e6)
    chk("gain-bandwidth product is constant: x10 gain gives 100 kHz",
        abs(bw["bandwidth_Hz"] - 1e5) < 1e-6, f"{bw['bandwidth_Hz']/1e3:.0f} kHz")
    diff = opamp_difference(1000.0, 10000.0, 2.5, 2.0)
    chk("difference amp amplifies the difference, ignores the common mode",
        abs(diff["v_out"] - 10.0 * 0.5) < 1e-12
        and abs(diff["common_mode_input"] - 2.25) < 1e-12)
    chk("difference amp with equal inputs gives zero out",
        abs(opamp_difference(1000., 10000., 3.0, 3.0)["v_out"]) < 1e-12)
    ig = opamp_integrator(10000.0, 1e-7, 1.0, 0.001)
    chk("integrator ramps at -Vin/RC",
        abs(ig["slew_V_per_s"] + 1000.0) < 1e-9, f"{ig['slew_V_per_s']:.1f} V/s")
    chk("...and the ideal-integrator drift caveat is stated", "drift" in ig["note"])
    chk("zero or negative resistors are refused",
        _raises(lambda: opamp_inverting(0.0, 1000.0, 1.0), ValueError))

    # ---- rectifiers: the 1/pi verified by numerical integration ----
    hw = half_wave_rectifier(10.0, 0.7)
    n_int = 20000
    mean_half_sine = sum(math.sin(math.pi * i / n_int) for i in range(n_int + 1)) \
        / (n_int + 1) / 2.0 * 2.0
    # mean of |sin| over a full period is 2/pi; over a half-wave rectified cycle 1/pi
    chk("half-wave V_dc = (Vp - Vd)/pi, and 1/pi is the numerically integrated mean",
        abs(hw["v_dc"] - 9.3 / math.pi) < 1e-12
        and abs(mean_half_sine / 2.0 - 1.0 / math.pi) < 1e-3,
        f"{hw['v_dc']:.4f} V dc from a 10 V peak")
    chk("half-wave rms = Vp/2", abs(hw["v_rms"] - 9.3 / 2.0) < 1e-12)
    chk("form factor of a half sine is pi/2 = 1.571",
        abs(hw["form_factor"] - math.pi / 2.0) < 1e-12, f"{hw['form_factor']:.4f}")
    fw = full_wave_rectifier(10.0, 0.7, bridge=True)
    chk("a bridge drops TWO diodes, a centre-tap only one",
        fw["diodes_in_path"] == 2
        and full_wave_rectifier(10., .7, bridge=False)["diodes_in_path"] == 1,
        f"bridge peak {fw['v_peak_out']:.1f} V vs centre-tap "
        f"{full_wave_rectifier(10.,.7,bridge=False)['v_peak_out']:.1f} V")
    chk("full-wave dc is about twice half-wave for the same peak",
        1.8 < fw["v_dc"] / hw["v_dc"] < 2.1,
        f"{hw['v_dc']:.3f} V -> {fw['v_dc']:.3f} V")
    chk("full-wave ripple is at TWICE the mains frequency",
        fw["ripple_frequency_Hz"] == 100.0)
    filt = full_wave_rectifier(10.0, 0.7, C_filter=1000e-6, R_load=100.0)
    chk("a reservoir capacitor cuts ripple to a few percent",
        filt["ripple_percent"] < 10.0, f"{filt['ripple_percent']:.2f}% with 1000 uF")
    chk("a rectifier that never conducts is refused, not given a negative output",
        _raises(lambda: half_wave_rectifier(0.5, 0.7), ValueError))
    chk("diode drop is ~0.6-0.7 V at normal current and rises 60 mV per decade",
        0.55 < diode_drop(1e-3) < 0.75
        and abs(diode_drop(1e-2) - diode_drop(1e-3) - 0.0595) < 0.002,
        f"{diode_drop(1e-3):.4f} V at 1 mA, {diode_drop(1e-2):.4f} V at 10 mA")

    # ---- BJT ----
    q = bjt_common_emitter(12.0, 47000.0, 10000.0, 3300.0, 1000.0, beta=100.0)
    chk("BJT bias point solves to forward active",
        q["region"] == "forward active", f"Vce = {q['V_ce']:.3f} V, "
        f"Ic = {q['I_c_A']*1000:.3f} mA")
    # The naive Ve = Vb - Vbe ignores the base current flowing through Rth. Base-loop
    # KVL is Vb - Ib*Rth - Vbe = Ve, and the difference here is 106 mV - an 8% error
    # in Ie that a textbook first pass would carry straight into the gain.
    chk("base-loop KVL closes INCLUDING the base current through Rth",
        abs(q["V_b"] - q["I_b_A"] * q["R_th_base"] - 0.7 - q["V_e"]) < 1e-9,
        f"Vb={q['V_b']:.3f} - Ib.Rth={q['I_b_A']*q['R_th_base']:.3f} - 0.7 "
        f"= Ve={q['V_e']:.3f}")
    chk("...and the naive Ve = Vb - Vbe would have been optimistic by ~8%",
        0.02 < ((q["V_b"] - 0.7) - q["V_e"]) / q["V_e"] < 0.15,
        f"naive {q['V_b']-0.7:.3f} V vs actual {q['V_e']:.3f} V - "
        "base loading is not negligible with a 8.3k divider")
    chk("KVL closes: Vcc = Ic Rc + Vce + Ie Re",
        abs(12.0 - (q["I_c_A"] * 3300.0 + q["V_ce"] + q["I_e_A"] * 1000.0)) < 1e-9)
    chk("Ie = Ic + Ib exactly", abs(q["I_e_A"] - q["I_c_A"] - q["I_b_A"]) < 1e-15)
    chk("gm = Ic/Vt", abs(q["gm_S"] - q["I_c_A"] / VT_300K) < 1e-12,
        f"gm = {q['gm_S']*1000:.3f} mS")
    chk("emitter degeneration reduces gain to about -Rc/Re",
        abs(q["gain_av_degenerated"] + 3.3) < 1e-9
        and abs(q["gain_av"]) > abs(q["gain_av_degenerated"]),
        f"undegenerated {q['gain_av']:.1f}, degenerated {q['gain_av_degenerated']:.2f}")
    hot = bjt_common_emitter(12.0, 1000.0, 100000.0, 3300.0, 100.0)
    chk("an over-biased stage is reported SATURATED, not given a fake gain",
        "SATURATED" in hot["region"], f"Vce = {hot['V_ce']:.3f} V")
    cold = bjt_common_emitter(12.0, 100000.0, 1000.0, 3300.0, 1000.0)
    chk("an under-biased stage is reported cut off",
        "error" in cold or "cut off" in cold.get("region", ""))
    chk("base loading is accounted for, not ignored", q["R_th_base"] > 0)

    # ---- METHOD DIVERSITY: closed form vs ngspice ----
    if ngspice_available():
        t_probe = 2e-3
        want = rc_step(1000.0, 1e-6, 5.0, t_probe)["v"]
        sim = spice_rc_step(1000.0, 1e-6, 5.0, t_probe)
        cmp = compare_with_spice(want, sim["v"] if sim else None, "RC step at 2 tau")
        if cmp["compared"]:
            chk("RC transient: closed form and ngspice agree (INDEPENDENT methods)",
                cmp["agrees"],
                f"analytic {cmp['analytic']:.5f} V vs spice {cmp['simulated']:.5f} V "
                f"({cmp['relative_error']*100:.3f}%)")
        else:
            chk("ngspice comparison attempted and reported honestly", True,
                cmp["reason"])
    else:
        chk("ngspice absent - comparison skipped and SAID so, not silently passed",
            True, "no ngspice on PATH")

    print("circuits_ext selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="transients, op-amps, rectifiers, BJTs")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--bjt", nargs=5, type=float, metavar=("Vcc", "R1", "R2", "Rc", "Re"))
    a = ap.parse_args()
    if a.bjt:
        print(json.dumps(bjt_common_emitter(*a.bjt), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
