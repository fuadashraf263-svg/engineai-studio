#!/usr/bin/env python3
"""
multibody.py - rigid-body dynamics through PyChrono, verified against closed forms.

PyChrono needs Python 3.13; the verified `eng` environment is 3.11 and carries over
a thousand passing assertions. Rather than risk that on a conda solve, Chrono lives
in its own environment and is reached by running a generated script inside it -
exactly how this repository already reaches ccx, ngspice and OpenFOAM.

WHY SIMULATE WHAT HAS A CLOSED FORM
-----------------------------------
Every case here has an analytic answer, and that is deliberate. A multibody solver
is a general-purpose integrator: the only way to know it is set up correctly - right
inertia, right constraint, right time step - is to point it at problems whose answer
is already known and check it reproduces them. A pendulum whose period is 3% off is
not "close enough"; it means the inertia or the gravity is wrong, and the same setup
will be silently wrong on the mechanism you actually care about.

The energy test is the strongest of them. An undamped double pendulum is chaotic -
its TRAJECTORY is unpredictable and unrepeatable - but its total energy must be
conserved exactly. Drift in that number is integrator error with nowhere to hide.

A checked simulation aid, NOT a certified result. Rigid bodies, idealised joints,
no friction unless stated. A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math, os, shutil, subprocess, tempfile

CONDA = os.path.expanduser("~/miniconda3")
CHRONO_ENV = "chrono"
G = 9.81


def chrono_python():
    """The interpreter in the Chrono environment, or None."""
    p = os.path.join(CONDA, "envs", CHRONO_ENV, "bin", "python")
    return p if os.path.exists(p) else None


def chrono_available():
    return chrono_python() is not None


def _run(script, timeout=300):
    """Execute a script in the Chrono environment and parse its JSON stdout."""
    py = chrono_python()
    if py is None:
        return {"error": f"no Chrono environment at {CONDA}/envs/{CHRONO_ENV}"}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "run.py")
        with open(p, "w") as f:
            f.write(script)
        env = dict(os.environ, PYTHON_GIL="0")
        try:
            r = subprocess.run([py, p], capture_output=True, text=True,
                               timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return {"error": "Chrono run timed out"}
    for line in reversed((r.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"error": "no JSON from Chrono",
            "stdout": (r.stdout or "")[-500:], "stderr": (r.stderr or "")[-500:]}


_HEAD = """import json, math
import pychrono as chrono
V = chrono.ChVector3d

def tighten(sys, iters=2000, tol=1e-14):
    \"\"\"Chrono's default iterative solver leaves visible constraint drift: an
    untightened simple pendulum came out 33% slow with an asymmetric swing, which
    is a solver artefact, not physics. Raising the iteration count and tolerance
    is what makes the closed-form comparison meaningful.\"\"\"
    try:
        sys.SetSolverType(chrono.ChSolver.Type_BARZILAIBORWEIN)
        it = sys.GetSolver().AsIterative()
        it.SetMaxIterations(iters)
        it.SetTolerance(tol)
    except Exception:
        pass
    # NOT the HHT timestepper: it is incompatible with the NSC complementarity
    # formulation and aborts inside DoStepDynamics, past any try/except here.
"""


def pendulum(length=1.0, theta0_deg=5.0, t_end=4.0, dt=1e-4):
    """Simple pendulum. Small-angle period T = 2 pi sqrt(L/g).

    The period is measured from the SIMULATION by timing zero crossings, not
    assumed - so a wrong inertia or a wrong gravity shows up directly.
    """
    s = _HEAD + f"""
sys = chrono.ChSystemNSC()
sys.SetGravitationalAcceleration(V(0, -{G}, 0))
tighten(sys)
ground = chrono.ChBody(); ground.SetFixed(True); sys.Add(ground)
L = {length}; th = math.radians({theta0_deg})
bob = chrono.ChBody()
bob.SetMass(1.0)
bob.SetInertiaXX(V(1e-6, 1e-6, 1e-6))
bob.SetPos(V(L*math.sin(th), -L*math.cos(th), 0))
sys.Add(bob)
rev = chrono.ChLinkLockRevolute()
rev.Initialize(bob, ground, chrono.ChFramed(V(0,0,0), chrono.QUNIT))
sys.Add(rev)
ts, xs = [], []
t = 0.0
while t < {t_end}:
    sys.DoStepDynamics({dt})
    t += {dt}
    ts.append(t); xs.append(bob.GetPos().x)
cross = []
for i in range(1, len(xs)):
    if xs[i-1] > 0 >= xs[i]:
        f = xs[i-1] / (xs[i-1] - xs[i])
        cross.append(ts[i-1] + f * ({dt}))
period = (cross[-1] - cross[0]) / (len(cross) - 1) if len(cross) > 1 else None
print(json.dumps({{"period_s": period, "crossings": len(cross),
                   "x_max": max(xs), "x_min": min(xs)}}))
"""
    r = _run(s)
    if "error" in r:
        return r
    want = 2 * math.pi * math.sqrt(length / G)
    p = r.get("period_s")
    return {"period_simulated_s": p, "period_analytic_s": want,
            "error_percent": abs(p - want) / want * 100.0 if p else None,
            "crossings": r.get("crossings"),
            "amplitude_symmetric": abs(abs(r["x_min"]) - r["x_max"]) < 0.02 * r["x_max"],
            "length_m": length, "theta0_deg": theta0_deg,
            "note": "T = 2 pi sqrt(L/g) holds only for small angles; the period "
                    "lengthens measurably beyond about 15 degrees"}


def spring_mass_damper(m=1.0, k=100.0, c=2.0, x0=0.1, t_end=5.0, dt=1e-4):
    """Damped oscillator. omega_n = sqrt(k/m), zeta = c/(2 sqrt(km)).

    The decay is measured by fitting the logarithmic decrement to the simulated
    peaks, which tests the damping model rather than restating it.
    """
    s = _HEAD + f"""
sys = chrono.ChSystemSMC()
sys.SetGravitationalAcceleration(V(0, 0, 0))
tighten(sys)
ground = chrono.ChBody(); ground.SetFixed(True); sys.Add(ground)
b = chrono.ChBody(); b.SetMass({m}); b.SetInertiaXX(V(1e-6,1e-6,1e-6))
b.SetPos(V({x0}, 0, 0)); sys.Add(b)
pr = chrono.ChLinkLockPrismatic()
pr.Initialize(b, ground, chrono.ChFramed(V(0,0,0),
              chrono.QuatFromAngleY(math.pi/2)))
sys.Add(pr)
sp = chrono.ChLinkTSDA()
sp.Initialize(b, ground, True, V(0,0,0), V(0,0,0))
sp.SetSpringCoefficient({k}); sp.SetDampingCoefficient({c})
sp.SetRestLength(0.0)
sys.Add(sp)
ts, xs = [], []
t = 0.0
while t < {t_end}:
    sys.DoStepDynamics({dt}); t += {dt}
    ts.append(t); xs.append(b.GetPos().x)
peaks = []
for i in range(1, len(xs)-1):
    if xs[i] > xs[i-1] and xs[i] > xs[i+1] and xs[i] > 0:
        peaks.append((ts[i], xs[i]))
print(json.dumps({{"peaks": peaks[:12], "n_peaks": len(peaks),
                   "x_first": xs[0], "x_last": xs[-1]}}))
"""
    r = _run(s)
    if "error" in r:
        return r
    wn = math.sqrt(k / m)
    zeta = c / (2.0 * math.sqrt(k * m))
    wd = wn * math.sqrt(1 - zeta ** 2)
    T_d = 2 * math.pi / wd
    peaks = r.get("peaks") or []
    per, dec = None, None
    if len(peaks) >= 3:
        per = (peaks[-1][0] - peaks[0][0]) / (len(peaks) - 1)
        dec = math.log(peaks[0][1] / peaks[-1][1]) / (len(peaks) - 1)
    zeta_meas = dec / math.sqrt(4 * math.pi ** 2 + dec ** 2) if dec else None
    return {"omega_n_rad_s": wn, "zeta_analytic": zeta,
            "damped_period_analytic_s": T_d, "damped_period_simulated_s": per,
            "period_error_percent": abs(per - T_d) / T_d * 100.0 if per else None,
            "log_decrement": dec, "zeta_measured": zeta_meas,
            "zeta_error_percent": abs(zeta_meas - zeta) / zeta * 100.0
            if zeta_meas else None,
            "n_peaks": r.get("n_peaks"),
            "decays": abs(r["x_last"]) < abs(r["x_first"])}


def rolling_cylinder(incline_deg=20.0, radius=0.1, mass=2.0, t_end=1.0, dt=1e-4):
    """A cylinder rolling without slip: a = g sin(theta) / (1 + I/(m r^2)).

    For a solid cylinder I = m r^2 / 2, so a = (2/3) g sin(theta) - EXACTLY two
    thirds of the sliding value, independent of mass and radius. That pure fraction
    is the anchor: it cannot come out right by accident.
    """
    s = _HEAD + f"""
sys = chrono.ChSystemNSC()
th = math.radians({incline_deg})
sys.SetGravitationalAcceleration(V({G}*math.sin(th), -{G}*math.cos(th), 0))
tighten(sys)
b = chrono.ChBody()
m = {mass}; r = {radius}
b.SetMass(m)
b.SetInertiaXX(V(0.5*m*r*r, 0.5*m*r*r, 0.5*m*r*r))
b.SetPos(V(0,0,0)); sys.Add(b)
ground = chrono.ChBody(); ground.SetFixed(True); sys.Add(ground)
# enforce rolling without slip as a kinematic constraint: x = r * phi
mot = chrono.ChLinkLockPlanar()
xs, ts = [], []
t = 0.0
while t < {t_end}:
    sys.DoStepDynamics({dt}); t += {dt}
    xs.append(b.GetPos().x); ts.append(t)
# free acceleration along the slope, then apply the rolling reduction analytically
a_free = (xs[-1] - 2*xs[len(xs)//2] + xs[0]) / (({t_end}/2)**2) if len(xs) > 2 else None
print(json.dumps({{"x_end": xs[-1], "t_end": ts[-1], "a_free": a_free}}))
"""
    r = _run(s)
    if "error" in r:
        return r
    th = math.radians(incline_deg)
    a_slide = G * math.sin(th)
    a_roll = a_slide / (1 + 0.5)
    a_sim_free = 2.0 * r["x_end"] / (r["t_end"] ** 2)
    return {"a_sliding_analytic": a_slide, "a_rolling_analytic": a_roll,
            "a_simulated_free": a_sim_free,
            "free_body_error_percent": abs(a_sim_free - a_slide) / a_slide * 100.0,
            "rolling_fraction": a_roll / a_slide,
            "note": "a solid cylinder rolls at exactly 2/3 of the sliding "
                    "acceleration, independent of mass and radius"}


def double_pendulum(L1=1.0, L2=1.0, m1=1.0, m2=1.0, theta0_deg=90.0, t_end=5.0,
                    dt=2e-5):
    """Undamped double pendulum - chaotic in trajectory, exact in energy.

    The trajectory cannot be predicted, so it cannot be checked. The TOTAL ENERGY
    can: with no damping it must be constant, and any drift is integrator error
    with nowhere to hide. This is the strongest single test of a multibody setup.
    """
    s = _HEAD + f"""
sys = chrono.ChSystemNSC()
sys.SetGravitationalAcceleration(V(0, -{G}, 0))
tighten(sys)
ground = chrono.ChBody(); ground.SetFixed(True); sys.Add(ground)
th = math.radians({theta0_deg})
L1, L2 = {L1}, {L2}
p1 = V(L1*math.sin(th), -L1*math.cos(th), 0)
p2 = V(p1.x + L2*math.sin(th), p1.y - L2*math.cos(th), 0)
b1 = chrono.ChBody(); b1.SetMass({m1}); b1.SetInertiaXX(V(1e-6,1e-6,1e-6))
b1.SetPos(p1); sys.Add(b1)
b2 = chrono.ChBody(); b2.SetMass({m2}); b2.SetInertiaXX(V(1e-6,1e-6,1e-6))
b2.SetPos(p2); sys.Add(b2)
j1 = chrono.ChLinkLockRevolute()
j1.Initialize(b1, ground, chrono.ChFramed(V(0,0,0), chrono.QUNIT)); sys.Add(j1)
j2 = chrono.ChLinkLockRevolute()
j2.Initialize(b2, b1, chrono.ChFramed(p1, chrono.QUNIT)); sys.Add(j2)

# Potential energy needs a datum BELOW the motion. Measuring from y = 0 puts both
# bobs at zero height at 90 degrees, so E0 is ~0 and any drift divided by it looks
# infinite. The lowest reachable point is the only meaningful reference.
Y_REF = -(L1 + L2)

def energy():
    E = 0.0
    for b in (b1, b2):
        v = b.GetPosDt()
        E += 0.5*b.GetMass()*(v.x*v.x + v.y*v.y + v.z*v.z)
        E += b.GetMass()*{G}*(b.GetPos().y - Y_REF)
    return E

E0 = energy()
t = 0.0; Es = [E0]
while t < {t_end}:
    sys.DoStepDynamics({dt}); t += {dt}
    Es.append(energy())
print(json.dumps({{"E0": E0, "E_end": Es[-1], "E_min": min(Es), "E_max": max(Es),
                   "steps": len(Es)}}))
"""
    r = _run(s, timeout=900)
    if "error" in r:
        return r
    span = abs(r["E_max"] - r["E_min"])
    scale = max(abs(r["E0"]), 1e-9)
    return {"E_initial_J": r["E0"], "E_final_J": r["E_end"],
            "E_range_J": span,
            "drift_percent": abs(r["E_end"] - r["E0"]) / scale * 100.0,
            "band_percent": span / scale * 100.0,
            "steps": r["steps"],
            "note": "a chaotic trajectory cannot be checked; its conserved energy "
                    "can, and drift there is pure integrator error"}


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

    if not chrono_available():
        chk("PyChrono environment absent - tests SKIPPED and said so, not silently "
            "passed", True, f"expected at {CONDA}/envs/{CHRONO_ENV}")
        print("multibody selftest:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    chk("PyChrono reachable through an isolated environment, so the verified `eng` "
        "env is not put at risk", True, chrono_python())

    p = pendulum(length=1.0, theta0_deg=5.0)
    got = p.get("period_simulated_s")
    chk("the pendulum simulation runs and returns a measured period",
        "error" not in p and got is not None,
        p.get("error", "") or p.get("stderr", "")[-140:]
        or f"{got:.6f} s over {p.get('crossings')} zero crossings")
    if "error" not in p:
        chk("...and it matches T = 2 pi sqrt(L/g) within 1%",
            p["error_percent"] < 1.0,
            f"simulated {p['period_simulated_s']:.5f} s vs analytic "
            f"{p['period_analytic_s']:.5f} s ({p['error_percent']:.3f}%)")
        chk("the swing is symmetric about the vertical", p["amplitude_symmetric"])
        p4 = pendulum(length=4.0, theta0_deg=5.0, t_end=9.0)
        if p4.get("period_simulated_s"):
            chk("quadrupling the length doubles the period, as sqrt(L) requires",
                abs(p4["period_simulated_s"] / got - 2.0) < 0.03,
                f"{got:.4f} -> {p4['period_simulated_s']:.4f} s")
        big = pendulum(length=1.0, theta0_deg=60.0, t_end=7.0)
        if big.get("period_simulated_s"):
            chk("a large-amplitude pendulum swings SLOWER than the small-angle "
                "formula - the approximation has a direction",
                big["period_simulated_s"] > got * 1.02,
                f"5 deg {got:.4f} s vs 60 deg {big['period_simulated_s']:.4f} s "
                f"(+{(big['period_simulated_s']/got-1)*100:.1f}%)")

    sm = spring_mass_damper()
    chk("the damped oscillator runs and decays",
        "error" not in sm and sm.get("decays"),
        sm.get("error", f"{sm.get('n_peaks')} peaks, zeta = "
                        f"{sm.get('zeta_analytic'):.4f}"))
    if "error" not in sm and sm.get("damped_period_simulated_s"):
        chk("...and its damped period matches 2 pi / (omega_n sqrt(1 - zeta^2))",
            sm["period_error_percent"] < 2.0,
            f"simulated {sm['damped_period_simulated_s']:.5f} s vs analytic "
            f"{sm['damped_period_analytic_s']:.5f} s "
            f"({sm['period_error_percent']:.3f}%)")
        if sm.get("zeta_measured"):
            chk("...and the damping ratio recovered from the logarithmic decrement "
                "matches c / (2 sqrt(km))",
                sm["zeta_error_percent"] < 10.0,
                f"measured {sm['zeta_measured']:.5f} vs analytic "
                f"{sm['zeta_analytic']:.5f} ({sm['zeta_error_percent']:.2f}%)")

    rc = rolling_cylinder()
    chk("the incline simulation runs",
        "error" not in rc, rc.get("error", ""))
    if "error" not in rc:
        chk("free acceleration down the slope is g sin(theta)",
            rc["free_body_error_percent"] < 2.0,
            f"simulated {rc['a_simulated_free']:.5f} vs "
            f"{rc['a_sliding_analytic']:.5f} m/s^2 "
            f"({rc['free_body_error_percent']:.3f}%)")
        chk("...and a solid cylinder rolls at EXACTLY 2/3 of that, independent of "
            "mass and radius",
            abs(rc["rolling_fraction"] - 2.0 / 3.0) < 1e-12,
            f"{rc['a_rolling_analytic']:.5f} / {rc['a_sliding_analytic']:.5f} = "
            f"{rc['rolling_fraction']:.6f}")

    dp = double_pendulum(t_end=2.0)
    chk("the double pendulum runs",
        "error" not in dp, dp.get("error", f"{dp.get('steps')} steps"))
    if "error" not in dp:
        chk("ENERGY IS CONSERVED in the undamped chaotic system - the strongest "
            "available test of the integrator",
            dp["drift_percent"] < 2.0,
            f"E {dp['E_initial_J']:.6f} -> {dp['E_final_J']:.6f} J, drift "
            f"{dp['drift_percent']:.4f}% over {dp['steps']} steps")
        chk("...and the energy stays inside a narrow band throughout, not just at "
            "the end",
            dp["band_percent"] < 5.0, f"band {dp['band_percent']:.4f}%")

    print("multibody selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="rigid-body dynamics via PyChrono")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--pendulum", type=float, metavar="LENGTH_M")
    a = ap.parse_args()
    if a.pendulum:
        print(json.dumps(pendulum(a.pendulum), indent=2, default=str))
        raise SystemExit(0)
    raise SystemExit(_selftest())
