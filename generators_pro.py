#!/usr/bin/env python3
"""
generators_pro.py - one best-in-class generator per engineering discipline.

Every library used here is already installed on your machine. These are the
capabilities that were sitting idle. Import this and the registry in
generators.py grows; nothing else changes.

  python generators_pro.py check
  python generators_pro.py demo
  python generators_pro.py run pipe_system '{"Q":0.01,"D":0.05,"L":50}'
"""
import os, sys, math, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generators import gen, REGISTRY, available, _out, have    # shared registry

def _plt():
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt; return plt

# ===================================================== FLUID MECHANICS
@gen("pipe_system", "fluids", ["fluids", "CoolProp", "matplotlib"],
     "Pipe pressure drop: real fluid properties, Colebrook friction, fittings")
def pipe_system(Q=0.01, D=0.05, L=50.0, eps=4.5e-5, fluid="Water", T=293.15, P=101325.0,
                elbows=4, valves=1, **kw):
    from fluids import friction_factor, Reynolds, K_from_f, bend_rounded
    from CoolProp.CoolProp import PropsSI
    rho = PropsSI("D", "T", T, "P", P, fluid)
    mu  = PropsSI("V", "T", T, "P", P, fluid)
    A   = math.pi*D**2/4
    v   = Q/A
    Re  = Reynolds(V=v, D=D, rho=rho, mu=mu)
    fd  = friction_factor(Re=Re, eD=eps/D)
    K_pipe = fd*L/D
    K_fit  = elbows*0.3 + valves*0.15                     # typical K, turbulent
    dp     = (K_pipe + K_fit)*rho*v**2/2
    plt = _plt()
    Ls = [L*i/60 for i in range(61)]
    dps = [(fd*x/D)*rho*v**2/2/1000 for x in Ls]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(Ls, dps, lw=2.2, color="#1b6ca8")
    ax.axhline(dp/1000, ls="--", color="#c0392b",
               label=f"total incl. fittings {dp/1000:.2f} kPa")
    ax.set_xlabel("pipe length [m]"); ax.set_ylabel("pressure drop [kPa]"); ax.grid(alpha=.3)
    ax.set_title(f"{fluid} {D*1000:g} mm pipe, Q={Q*1000:g} L/s, v={v:.2f} m/s, "
                 f"Re={Re:.0f} ({'turbulent' if Re>4000 else 'laminar'}), f={fd:.4f}")
    ax.legend()
    p = _out("pipe_system.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    return dict(figure=p, velocity_m_s=v, Reynolds=Re, friction_factor=fd,
                dp_Pa=dp, dp_kPa=dp/1000, rho=rho, mu=mu, head_loss_m=dp/(rho*9.80665))

@gen("moody_chart", "fluids", ["fluids", "matplotlib"], "Moody diagram with your operating point")
def moody_chart(Re_op=None, eD_op=None, **kw):
    from fluids import friction_factor
    plt = _plt()
    import numpy as np
    fig, ax = plt.subplots(figsize=(8, 5))
    Re = np.logspace(3.3, 8, 400)
    for eD in (0, 1e-5, 1e-4, 5e-4, 1e-3, 5e-3, 1e-2, 5e-2):
        f = [friction_factor(Re=r, eD=eD) for r in Re]
        ax.loglog(Re, f, lw=1.1, color="#34495e", alpha=.75)
        ax.text(Re[-1], f[-1], f" {eD:g}", fontsize=7, va="center")
    Rl = np.logspace(2.6, 3.35, 40); ax.loglog(Rl, 64/Rl, lw=2, color="#c0392b", label="laminar 64/Re")
    if Re_op and eD_op is not None:
        f = friction_factor(Re=Re_op, eD=eD_op)
        ax.plot(Re_op, f, "o", ms=10, color="#e67e22", label=f"operating Re={Re_op:.0f} f={f:.4f}")
    ax.set_xlabel("Reynolds number"); ax.set_ylabel("Darcy friction factor")
    ax.set_title("Moody chart (relative roughness e/D labelled)"); ax.grid(which="both", alpha=.3)
    ax.legend()
    p = _out("moody.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    return p

# ===================================================== THERMODYNAMICS
@gen("thermo_cycle", "thermo", ["CoolProp", "matplotlib"],
     "Rankine / refrigeration cycle: state table, efficiency and T-s diagram")
def thermo_cycle(cycle="rankine", fluid="Water", p_hi=8e6, p_lo=10e3, T_hi=773.15,
                 eta_t=0.85, eta_p=0.75, **kw):
    from CoolProp.CoolProp import PropsSI
    import numpy as np
    plt = _plt()
    if cycle == "rankine":
        h1 = PropsSI("H", "P", p_lo, "Q", 0, fluid); s1 = PropsSI("S", "P", p_lo, "Q", 0, fluid)
        v1 = 1/PropsSI("D", "P", p_lo, "Q", 0, fluid)
        h2s = h1 + v1*(p_hi - p_lo); h2 = h1 + (h2s - h1)/eta_p
        h3 = PropsSI("H", "P", p_hi, "T", T_hi, fluid); s3 = PropsSI("S", "P", p_hi, "T", T_hi, fluid)
        h4s = PropsSI("H", "P", p_lo, "S", s3, fluid);  h4 = h3 - eta_t*(h3 - h4s)
        wt, wp, qin = h3 - h4, h2 - h1, h3 - h2
        eta = (wt - wp)/qin
        states = [("1 pump in  (sat liq)", p_lo, h1), ("2 pump out", p_hi, h2),
                  ("3 turbine in", p_hi, h3), ("4 turbine out", p_lo, h4)]
        res = dict(cycle="Rankine", fluid=fluid, w_turbine_kJkg=wt/1e3, w_pump_kJkg=wp/1e3,
                   q_in_kJkg=qin/1e3, thermal_efficiency=eta,
                   back_work_ratio=wp/wt, x_turbine_exit=PropsSI("Q","P",p_lo,"H",h4,fluid))
    else:                                    # vapour-compression refrigeration
        h1 = PropsSI("H", "P", p_lo, "Q", 1, fluid); s1 = PropsSI("S", "P", p_lo, "Q", 1, fluid)
        h2s = PropsSI("H", "P", p_hi, "S", s1, fluid); h2 = h1 + (h2s - h1)/eta_t
        h3 = PropsSI("H", "P", p_hi, "Q", 0, fluid);  h4 = h3
        qe, wc = h1 - h4, h2 - h1
        res = dict(cycle="vapour-compression", fluid=fluid, q_evap_kJkg=qe/1e3,
                   w_comp_kJkg=wc/1e3, COP=qe/wc)
        states = [("1 comp in", p_lo, h1), ("2 comp out", p_hi, h2),
                  ("3 cond out", p_hi, h3), ("4 evap in", p_lo, h4)]
    Tc = PropsSI("Tcrit", fluid); Tt = PropsSI("Ttriple", fluid)
    Ts = np.linspace(Tt+1, Tc-0.5, 220)
    sf = [PropsSI("S","T",t,"Q",0,fluid)/1e3 for t in Ts]
    sg = [PropsSI("S","T",t,"Q",1,fluid)/1e3 for t in Ts]
    fig, ax = plt.subplots(figsize=(7.4, 5))
    ax.plot(sf, Ts, color="#7f8c8d", lw=1.6); ax.plot(sg, Ts, color="#7f8c8d", lw=1.6)
    xs, ys, lab = [], [], []
    for nm, p_, h_ in states:
        try:
            s_ = PropsSI("S","P",p_,"H",h_,fluid)/1e3; t_ = PropsSI("T","P",p_,"H",h_,fluid)
            xs.append(s_); ys.append(t_); lab.append(nm)
        except Exception: pass
    if xs:
        ax.plot(xs+[xs[0]], ys+[ys[0]], "o-", color="#c0392b", lw=2, ms=7)
        for s_, t_, nm in zip(xs, ys, lab): ax.annotate(nm, (s_, t_), fontsize=8,
                                                        xytext=(6, 6), textcoords="offset points")
    ax.set_xlabel("entropy [kJ/kg·K]"); ax.set_ylabel("temperature [K]"); ax.grid(alpha=.3)
    key = "thermal_efficiency" if "thermal_efficiency" in res else "COP"
    ax.set_title(f"{res['cycle']} on {fluid} — {key} = {res[key]:.4f}")
    p = _out(f"cycle_{cycle}.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    res["figure"] = p; return res

@gen("property_table", "thermo", ["CoolProp"], "Real-fluid property table over a temperature range")
def property_table(fluid="Water", P=101325.0, T1=280.0, T2=370.0, n=10, **kw):
    from CoolProp.CoolProp import PropsSI
    rows = [["T [K]", "rho [kg/m3]", "cp [J/kgK]", "mu [Pa s]", "k [W/mK]", "Pr"]]
    for i in range(n+1):
        T = T1 + (T2-T1)*i/n
        try:
            rho = PropsSI("D","T",T,"P",P,fluid); cp = PropsSI("C","T",T,"P",P,fluid)
            mu  = PropsSI("V","T",T,"P",P,fluid); k  = PropsSI("L","T",T,"P",P,fluid)
            rows.append([f"{T:.1f}", f"{rho:.3f}", f"{cp:.1f}", f"{mu:.3e}", f"{k:.4f}", f"{cp*mu/k:.3f}"])
        except Exception: pass
    p = _out(f"properties_{fluid}.csv")
    import csv
    with open(p, "w", newline="") as f: csv.writer(f).writerows(rows)
    return dict(csv=p, rows=len(rows)-1, fluid=fluid)

# ===================================================== HEAT TRANSFER
@gen("heat_exchanger", "thermal", ["ht", "CoolProp", "matplotlib"],
     "Heat exchanger by effectiveness-NTU, with the effectiveness curve")
def heat_exchanger(m_h=0.5, m_c=0.8, T_hi=363.15, T_ci=293.15, UA=2000.0,
                   fluid_h="Water", fluid_c="Water", arrangement="counterflow", **kw):
    from CoolProp.CoolProp import PropsSI
    import ht, numpy as np
    cph = PropsSI("C","T",T_hi,"P",101325,fluid_h); cpc = PropsSI("C","T",T_ci,"P",101325,fluid_c)
    Ch, Cc = m_h*cph, m_c*cpc
    Cmin, Cmax = min(Ch, Cc), max(Ch, Cc); Cr = Cmin/Cmax
    NTU = UA/Cmin
    fn = {"counterflow": ht.effectiveness_from_NTU, "parallel": ht.effectiveness_from_NTU}
    eff = ht.effectiveness_from_NTU(NTU=NTU, Cr=Cr, subtype=arrangement)
    Qmax = Cmin*(T_hi - T_ci); Q = eff*Qmax
    T_ho, T_co = T_hi - Q/Ch, T_ci + Q/Cc
    plt = _plt()
    N = np.linspace(0.05, 6, 200)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for sub in ("counterflow", "parallel", "crossflow approximate"):
        try:
            e = [ht.effectiveness_from_NTU(NTU=n_, Cr=Cr, subtype=sub) for n_ in N]
            ax.plot(N, e, lw=1.8, label=sub)
        except Exception: pass
    ax.plot(NTU, eff, "o", ms=10, color="#e67e22", label=f"design NTU={NTU:.2f}, e={eff:.3f}")
    ax.set_xlabel("NTU"); ax.set_ylabel("effectiveness"); ax.grid(alpha=.3); ax.legend(fontsize=8)
    ax.set_title(f"Cr = {Cr:.3f},  Q = {Q/1000:.2f} kW,  hot {T_hi-273.15:.1f}->{T_ho-273.15:.1f} C,"
                 f"  cold {T_ci-273.15:.1f}->{T_co-273.15:.1f} C")
    p = _out("heat_exchanger.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    return dict(figure=p, NTU=NTU, Cr=Cr, effectiveness=eff, Q_W=Q, Q_kW=Q/1000,
                T_hot_out_K=T_ho, T_cold_out_K=T_co, LMTD_K=(Q/UA if UA else None))

@gen("fin_analysis", "thermal", ["matplotlib"], "Fin temperature profile, efficiency and heat rate")
def fin_analysis(k=200.0, h=50.0, L=0.05, t=0.003, w=1.0, T_b=373.15, T_inf=293.15, **kw):
    import numpy as np
    P, Ac = 2*(w + t), w*t
    m = math.sqrt(h*P/(k*Ac)); mL = m*L
    eff = math.tanh(mL)/mL
    q = math.sqrt(h*P*k*Ac)*(T_b - T_inf)*math.tanh(mL)
    x = np.linspace(0, L, 200)
    T = T_inf + (T_b - T_inf)*np.cosh(m*(L - x))/math.cosh(mL)
    plt = _plt(); fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(x*1000, T - 273.15, lw=2.3, color="#c0392b")
    ax.axhline(T_inf - 273.15, ls="--", color="#7f8c8d", label="ambient")
    ax.set_xlabel("distance from base [mm]"); ax.set_ylabel("temperature [C]"); ax.grid(alpha=.3)
    ax.set_title(f"Fin: mL={mL:.3f}, efficiency={eff*100:.1f}%, q={q:.1f} W"); ax.legend()
    p = _out("fin_analysis.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    return dict(figure=p, m=m, mL=mL, efficiency=eff, q_W=q, tip_temp_K=float(T[-1]))

# ===================================================== CONTROL SYSTEMS
@gen("control_design", "control", ["control", "matplotlib"],
     "Bode, step response, root locus and stability margins for a plant")
def control_design(num=(1.0,), den=(1.0, 2.0, 1.0), Kp=0.0, Ki=0.0, Kd=0.0, **kw):
    import control as ct, numpy as np
    plt = _plt()
    G = ct.tf(list(num), list(den))
    sys_ = G
    if Kp or Ki or Kd:
        C = ct.tf([Kd, Kp, Ki], [1, 0]) if Ki else ct.tf([Kd, Kp], [1])
        sys_ = ct.feedback(C*G, 1)
    gm, pm, wg, wp = ct.margin(G)
    fig = plt.figure(figsize=(11, 7))
    ax1 = fig.add_subplot(2, 2, 1); ax2 = fig.add_subplot(2, 2, 3)
    w = np.logspace(-2, 2, 500)
    mag, ph, om = ct.frequency_response(G, w)
    ax1.semilogx(om, 20*np.log10(mag), lw=2); ax1.set_ylabel("|G| [dB]"); ax1.grid(which="both", alpha=.3)
    ax1.set_title(f"gain margin {20*math.log10(gm) if gm and gm>0 else float('inf'):.2f} dB, "
                  f"phase margin {pm:.1f} deg")
    ax2.semilogx(om, np.degrees(ph), lw=2, color="#c0392b"); ax2.set_ylabel("phase [deg]")
    ax2.set_xlabel("rad/s"); ax2.grid(which="both", alpha=.3)
    ax3 = fig.add_subplot(2, 2, 2)
    t, y = ct.step_response(sys_)
    ax3.plot(t, y, lw=2.2); ax3.axhline(1, ls="--", color="#7f8c8d"); ax3.grid(alpha=.3)
    info = ct.step_info(sys_)
    ax3.set_title(f"step: rise {info['RiseTime']:.3g}s, settle {info['SettlingTime']:.3g}s, "
                  f"overshoot {info['Overshoot']:.1f}%")
    ax4 = fig.add_subplot(2, 2, 4)
    poles = ct.poles(sys_); zeros = ct.zeros(sys_)
    ax4.plot(np.real(poles), np.imag(poles), "x", ms=11, mew=2.4, color="#c0392b", label="poles")
    if len(zeros): ax4.plot(np.real(zeros), np.imag(zeros), "o", ms=9, mfc="none", label="zeros")
    ax4.axhline(0, color="k", lw=.8); ax4.axvline(0, color="k", lw=.8); ax4.grid(alpha=.3)
    ax4.set_title("pole-zero map"); ax4.legend(fontsize=8)
    p = _out("control_design.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    return dict(figure=p, gain_margin=float(gm), phase_margin_deg=float(pm),
                stable=bool(np.all(np.real(poles) < 0)),
                overshoot_pct=float(info["Overshoot"]), settling_s=float(info["SettlingTime"]))

# ===================================================== DESIGN EXPLORATION
@gen("doe_matrix", "optimisation", ["pyDOE3"], "Design-of-experiments matrix (Latin hypercube)")
def doe_matrix(factors=None, samples=20, name="doe", **kw):
    from pyDOE3 import lhs
    import csv
    factors = factors or {"thickness_mm": [1.0, 5.0], "width_mm": [20.0, 60.0],
                          "length_mm": [80.0, 200.0]}
    keys = list(factors); X = lhs(len(keys), samples=samples)
    rows = [keys]
    for r in X:
        rows.append([round(factors[k][0] + v*(factors[k][1]-factors[k][0]), 4)
                     for k, v in zip(keys, r)])
    p = _out(f"{name}.csv")
    with open(p, "w", newline="") as f: csv.writer(f).writerows(rows)
    return dict(csv=p, runs=samples, factors=keys)

@gen("sobol_sensitivity", "optimisation", ["SALib", "matplotlib"],
     "Global sensitivity: which input actually drives the answer")
def sobol_sensitivity(expr="P*L**3/(3*E*I)", bounds=None, n=1024, **kw):
    from SALib.sample import sobol as sobol_sample
    from SALib.analyze import sobol as sobol_an
    import numpy as np, sympy as sp
    try:
        from owner import safe_expr; safe_expr(expr, extra_names=list((bounds or {}).keys()))
    except Exception: pass
    bounds = bounds or {"P": [500, 2000], "L": [0.5, 2.0], "E": [1.8e11, 2.2e11],
                        "I": [5e-6, 1.2e-5]}
    names = list(bounds)
    loc = {k: sp.Symbol(k, positive=True) for k in names}
    f = sp.lambdify([loc[k] for k in names], sp.sympify(expr, locals=loc), "numpy")
    prob = {"num_vars": len(names), "names": names, "bounds": [bounds[k] for k in names]}
    X = sobol_sample.sample(prob, n)
    Y = np.array([f(*row) for row in X])
    Si = sobol_an.analyze(prob, Y, print_to_console=False)
    plt = _plt(); fig, ax = plt.subplots(figsize=(7, 4))
    idx = np.arange(len(names))
    ax.bar(idx-0.2, Si["S1"], 0.4, label="first order", color="#1b6ca8")
    ax.bar(idx+0.2, Si["ST"], 0.4, label="total", color="#e67e22")
    ax.set_xticks(idx); ax.set_xticklabels(names); ax.grid(alpha=.3, axis="y"); ax.legend()
    ax.set_ylabel("Sobol index"); ax.set_title(f"What drives {expr}")
    p = _out("sobol.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    return dict(figure=p, first_order=dict(zip(names, map(float, Si["S1"]))),
                total=dict(zip(names, map(float, Si["ST"]))))

# ===================================================== AEROSPACE
@gen("airfoil", "aero", ["matplotlib"], "NACA 4-digit airfoil coordinates, plot and DXF-ready points")
def airfoil(code="2412", chord=1.0, n=120, **kw):
    import numpy as np
    m, p_, tt = int(code[0])/100, int(code[1])/10, int(code[2:])/100
    beta = np.linspace(0, math.pi, n); x = (1-np.cos(beta))/2
    yt = 5*tt*(0.2969*np.sqrt(x) - 0.1260*x - 0.3516*x**2 + 0.2843*x**3 - 0.1036*x**4)
    yc = np.where(x < p_, m/max(p_**2,1e-9)*(2*p_*x - x**2),
                  m/max((1-p_)**2,1e-9)*((1-2*p_) + 2*p_*x - x**2)) if m else np.zeros_like(x)
    dyc = np.where(x < p_, 2*m/max(p_**2,1e-9)*(p_-x), 2*m/max((1-p_)**2,1e-9)*(p_-x)) if m else np.zeros_like(x)
    th = np.arctan(dyc)
    xu, yu = x - yt*np.sin(th), yc + yt*np.cos(th)
    xl, yl = x + yt*np.sin(th), yc - yt*np.cos(th)
    plt = _plt(); fig, ax = plt.subplots(figsize=(9, 3))
    ax.plot(xu*chord, yu*chord, lw=2, color="#1b2631"); ax.plot(xl*chord, yl*chord, lw=2, color="#1b2631")
    ax.plot(x*chord, yc*chord, "--", lw=1, color="#c0392b", label="camber line")
    ax.set_aspect("equal"); ax.grid(alpha=.3); ax.legend()
    ax.set_title(f"NACA {code}   chord {chord:g}   max thickness {tt*100:.0f}%   "
                 f"max camber {m*100:.0f}% at {p_*100:.0f}%")
    p = _out(f"naca{code}.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    pts = [(float(a), float(b)) for a, b in zip(np.r_[xu[::-1], xl[1:]]*chord,
                                                np.r_[yu[::-1], yl[1:]]*chord)]
    import csv
    c = _out(f"naca{code}.csv")
    with open(c, "w", newline="") as f: csv.writer(f).writerows([["x", "y"]] + pts)
    return dict(figure=p, csv=c, points=len(pts), thickness_pct=tt*100, camber_pct=m*100)

# ===================================================== CLI
def demo():
    made, skipped = [], []
    args = {"sobol_sensitivity": dict(n=256)}
    for k, f in list(REGISTRY.items()):
        if f.__module__ != __name__: continue
        if not available(k): skipped.append((k, "needs " + ",".join(f.needs))); continue
        try:
            r = f(**args.get(k, {}))
            files = [v for v in (r.values() if isinstance(r, dict) else [r])
                     if isinstance(v, str) and os.path.exists(v)]
            for x in files: made.append(x)
            extra = ""
            if isinstance(r, dict):
                nums = {kk: vv for kk, vv in r.items() if isinstance(vv, (int, float))}
                extra = "  " + ", ".join(f"{kk}={vv:.4g}" for kk, vv in list(nums.items())[:3])
            print(f"  ok   {k:20s} {os.path.basename(files[0]) if files else '':26s}{extra}")
        except Exception as e:
            skipped.append((k, f"{type(e).__name__}: {str(e)[:60]}"))
    for k, w in skipped: print(f"  skip {k:20s} {w}")
    print(f"\n{len(made)} file(s) in {_out('')}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["check","demo","run","list"])
    ap.add_argument("name", nargs="?"); ap.add_argument("args", nargs="?", default="{}")
    a = ap.parse_args()
    mine = {k: v for k, v in REGISTRY.items() if v.__module__ == __name__}
    if a.cmd in ("check", "list"):
        for k, f in sorted(mine.items(), key=lambda x: (x[1].cat, x[0])):
            print(f"  {k:20s} {f.cat:12s} {'ok' if available(k) else 'MISSING '+','.join(f.needs):12s} {f.doc}")
        print(f"\n{sum(available(k) for k in mine)}/{len(mine)} available")
    elif a.cmd == "demo": demo()
    elif a.cmd == "run": print(json.dumps(REGISTRY[a.name](**json.loads(a.args)), indent=2, default=str))
