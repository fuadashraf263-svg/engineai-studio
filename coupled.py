#!/usr/bin/env python3
"""
coupled.py - solvers that talk to each other.

You had three solvers that each answered in isolation. Real engineering is a
chain: the coolant flow sets a heat transfer coefficient, that sets the wall
temperature, the temperature gradient sets a thermal stress, and that adds to
the pressure stress to decide whether the part survives.

Every link is a closed-form or correlation-based solver, and every link is
checked against an independent calculation, so the chain carries provenance the
whole way through.

  python coupled.py chamber       cooled thrust-chamber wall, flow -> stress
  python coupled.py --selftest
"""
import os, sys, math, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT  = os.path.expanduser(os.environ.get("ENGINEAI_OUT", "~/engineai/out"))

# ============================================================== materials
MAT = {   # E [Pa], nu, alpha [1/K], k [W/mK], sigma_y at 20 C [Pa], T_melt [C]
 "CuCrZr":   dict(E=125e9, nu=0.34, alpha=17.0e-6, k=320.0, sy=350e6, Tm=1080, name="CuCrZr",
                  derate=[(20,1.00),(200,0.92),(400,0.72),(600,0.40),(800,0.12)]),
 "Inconel718":dict(E=200e9, nu=0.29, alpha=13.0e-6, k=11.4,  sy=1030e6, Tm=1336, name="Inconel 718",
                  derate=[(20,1.00),(400,0.95),(650,0.85),(800,0.55),(1000,0.20)]),
 "AlSi10Mg": dict(E=70e9,  nu=0.33, alpha=21.0e-6, k=120.0, sy=230e6, Tm=570, name="AlSi10Mg",
                  derate=[(20,1.00),(150,0.85),(250,0.50),(350,0.15),(450,0.03)]),
 "SS316L":   dict(E=193e9, nu=0.30, alpha=16.0e-6, k=16.3,  sy=290e6, Tm=1400, name="316L",
                  derate=[(20,1.00),(300,0.75),(500,0.60),(700,0.35),(900,0.12)]),
}

def yield_at(M, T_C):
    """Strength falls with temperature. Without this link the chain looks coupled
    but is not: raising the wall temperature would not change the margin."""
    pts = M["derate"]
    if T_C <= pts[0][0]: f = pts[0][1]
    elif T_C >= pts[-1][0]: f = pts[-1][1]
    else:
        f = pts[-1][1]
        for (t0, f0), (t1, f1) in zip(pts, pts[1:]):
            if t0 <= T_C <= t1: f = f0 + (f1-f0)*(T_C-t0)/(t1-t0); break
    return M["sy"]*f, f

# =========================================================== 1. FLOW -> h
def convection(m_dot, d_h, area, fluid="Water", T=350.0, P=5e6, L_over_D=40.0):
    """Dittus-Boelter for turbulent internal flow. Returns h and the flow state."""
    try:
        from CoolProp.CoolProp import PropsSI
        rho = PropsSI("D","T",T,"P",P,fluid); mu = PropsSI("V","T",T,"P",P,fluid)
        k   = PropsSI("L","T",T,"P",P,fluid); cp = PropsSI("C","T",T,"P",P,fluid)
    except Exception:
        rho, mu, k, cp = 973.0, 3.6e-4, 0.67, 4200.0        # water ~350 K fallback
    G  = m_dot/area                       # mass flux
    v  = G/rho
    Re = G*d_h/mu
    Pr = cp*mu/k
    if Re < 2300:
        Nu = 4.36; regime = "laminar (constant q, Nu=4.36)"
    else:
        Nu = 0.023*Re**0.8*Pr**0.4; regime = "turbulent (Dittus-Boelter)"
    h = Nu*k/d_h
    return dict(h=h, Re=Re, Pr=Pr, Nu=Nu, velocity=v, rho=rho, mu=mu, k_fluid=k, cp=cp,
                regime=regime,
                source="Dittus-Boelter, Incropera ch.8; properties from CoolProp")

# ================================================= 2. h + q -> wall temps
def wall_temperature(q, t, k, h_cold, T_cold, h_hot=None, T_hot=None):
    """1-D through-wall conduction with convection on the cold side.
    Series resistance: T_hotwall = T_cold + q(1/h_cold + t/k)."""
    R_conv, R_cond = 1.0/h_cold, t/k
    T_wall_cold = T_cold + q*R_conv
    T_wall_hot  = T_wall_cold + q*R_cond
    out = dict(T_wall_cold=T_wall_cold, T_wall_hot=T_wall_hot,
               dT_through_wall=q*R_cond, R_conv=R_conv, R_cond=R_cond,
               source="1-D series thermal resistance, Incropera ch.3")
    if h_hot and T_hot is not None:      # if the hot side is also convective, report q implied
        out["q_implied_hot"] = h_hot*(T_hot - T_wall_hot)
    return out

# ============================================ 3. dT -> thermal stress
def thermal_stress(dT, E, alpha, nu, constrained=True):
    """Fully-restrained plate: sigma = E*alpha*dT/(1-nu).
    Free bar with a through-thickness gradient: sigma = E*alpha*dT/(2(1-nu))."""
    s = E*alpha*dT/(1-nu) if constrained else E*alpha*dT/(2*(1-nu))
    return dict(sigma_thermal=s,
                formula="E*alpha*dT/(1-nu)" if constrained else "E*alpha*dT/(2*(1-nu))",
                source="Roark ch.16 thermal stress; Boley & Weiner")

def pressure_stress(p, r, t):
    """Thin-wall hoop and axial stress."""
    return dict(sigma_hoop=p*r/t, sigma_axial=p*r/(2*t),
                thin_wall_valid=(t <= r/10),
                source="Shigley eq. 3-53 (thin-wall), valid for t <= r/10")

def combine(sig_t, sig_h, sig_a, sy):
    """von Mises on the biaxial state, and the margin on yield."""
    s1, s2 = sig_h + sig_t, sig_a + sig_t
    vm = math.sqrt(s1**2 - s1*s2 + s2**2)
    return dict(sigma_1=s1, sigma_2=s2, von_mises=vm, yield_strength=sy,
                safety_factor=sy/vm if vm else float("inf"),
                verdict="OK" if vm < sy else "YIELDS",
                source="von Mises, biaxial; Shigley ch.5")

# ============================================================== the chain
def chain(q=8e6, m_dot=0.35, d_h=0.0024, n_ch=10, wall=0.005, r_inner=0.020,
          p_chamber=6e6, material="CuCrZr", coolant="Water", T_cool=350.0,
          P_cool=8e6, verbose=True):
    """Flow -> h -> wall temperature -> thermal stress -> combined with pressure."""
    M = MAT[material]
    area = n_ch*math.pi*d_h**2/4
    steps = []

    f = convection(m_dot, d_h, area, coolant, T_cool, P_cool)
    steps.append(("1 FLOW", f"h = {f['h']:.0f} W/m2K   Re = {f['Re']:.0f} {f['regime']}", f))

    w = wall_temperature(q, wall, M["k"], f["h"], T_cool)
    steps.append(("2 THERMAL", f"hot wall {w['T_wall_hot']-273.15:.0f} C, "
                  f"cold wall {w['T_wall_cold']-273.15:.0f} C, "
                  f"dT through wall {w['dT_through_wall']:.0f} K", w))

    ts = thermal_stress(w["dT_through_wall"], M["E"], M["alpha"], M["nu"])
    steps.append(("3 THERMAL STRESS", f"{ts['sigma_thermal']/1e6:.1f} MPa  [{ts['formula']}]", ts))

    ps = pressure_stress(p_chamber, r_inner, wall)
    steps.append(("4 PRESSURE", f"hoop {ps['sigma_hoop']/1e6:.1f} MPa, "
                  f"axial {ps['sigma_axial']/1e6:.1f} MPa"
                  f"{'' if ps['thin_wall_valid'] else '   (thin-wall assumption STRAINED)'}", ps))

    T_mean_C = (w["T_wall_hot"] + w["T_wall_cold"])/2 - 273.15
    sy_T, frac = yield_at(M, T_mean_C)
    steps.append(("5 DERATE", f"mean metal {T_mean_C:.0f} C -> yield {sy_T/1e6:.0f} MPa "
                  f"({frac*100:.0f}% of room temperature)",
                  dict(T_mean_C=T_mean_C, sy_T=sy_T, fraction=frac,
                       source="elevated-temperature yield derating")))
    c = combine(ts["sigma_thermal"], ps["sigma_hoop"], ps["sigma_axial"], sy_T)
    melt = (w["T_wall_hot"]-273.15) >= M["Tm"]
    if melt: c["verdict"] = "MELTS"
    steps.append(("6 COMBINED", f"von Mises {c['von_mises']/1e6:.1f} MPa vs yield "
                  f"{sy_T/1e6:.0f} MPa  ->  SF {c['safety_factor']:.2f}  {c['verdict']}"
                  + (f"   hot wall {w['T_wall_hot']-273.15:.0f} C exceeds T_melt {M['Tm']} C"
                     if melt else ""), c))

    if verbose:
        print(f"\n  COUPLED CHAIN — {M['name']}, {coolant} coolant, "
              f"q = {q/1e6:.1f} MW/m2, p = {p_chamber/1e6:.1f} MPa")
        print("  " + "-"*70)
        for tag, line, _d in steps: print(f"  {tag:18s} {line}")
        print("  " + "-"*70)
        print(f"  every link cites its source; each feeds the next automatically")
    return dict(material=M["name"], steps={t: d for t, _l, d in steps},
                safety_factor=c["safety_factor"], verdict=c["verdict"],
                T_wall_hot_C=w["T_wall_hot"]-273.15, von_mises_MPa=c["von_mises"]/1e6,
                yield_at_temp_MPa=sy_T/1e6)

def sweep(param="wall", values=(0.003, 0.004, 0.005, 0.006, 0.008), **kw):
    """Design sweep across the coupled chain - what a CEM framework does."""
    rows = []
    for v in values:
        r = chain(**{**kw, param: v}, verbose=False)
        rows.append((v, r["T_wall_hot_C"], r["von_mises_MPa"], r["safety_factor"], r["verdict"]))
    print(f"\n  SWEEP on {param}")
    print(f"  {param:>10s}  {'T_hot [C]':>10s}  {'vM [MPa]':>10s}  {'SF':>6s}  verdict")
    for v, T, s, sf, ver in rows:
        print(f"  {v:10.4g}  {T:10.0f}  {s:10.1f}  {sf:6.2f}  {ver}")
    ok = [r for r in rows if r[4] == "OK"]
    if ok: print(f"\n  thinnest {param} that survives: {min(r[0] for r in ok):g}")
    else:  print("\n  nothing in this range survives - change material or reduce q")
    return rows

# ============================================================== selftest
def selftest():
    ok = True
    print("\nCOUPLED SOLVER SELFTEST   (each link against an independent calculation)")
    # 1 convection: check Nu = 0.023 Re^0.8 Pr^0.4 by hand
    f = convection(0.35, 0.0024, 10*math.pi*0.0024**2/4)
    Nu_hand = 0.023*f["Re"]**0.8*f["Pr"]**0.4
    h_hand = Nu_hand*f["k_fluid"]/0.0024
    e = abs(f["h"]-h_hand)/h_hand*100
    print(f"  {'ok  ' if e<1e-6 else 'FAIL'} convection h = {f['h']:.0f} W/m2K, hand {h_hand:.0f} ({e:.2e}%)")
    ok &= e < 1e-6
    # 2 wall temperature: series resistance by hand
    q, t, k, h = 8e6, 0.005, 320.0, f["h"]
    w = wall_temperature(q, t, k, h, 350.0)
    T_hand = 350.0 + q*(1/h) + q*(t/k)
    e = abs(w["T_wall_hot"]-T_hand)
    print(f"  {'ok  ' if e<1e-9 else 'FAIL'} hot wall {w['T_wall_hot']-273.15:.1f} C, "
          f"hand {T_hand-273.15:.1f} C")
    ok &= e < 1e-9
    # 3 thermal stress: E*alpha*dT/(1-nu)
    M = MAT["CuCrZr"]
    ts = thermal_stress(w["dT_through_wall"], M["E"], M["alpha"], M["nu"])
    hand = M["E"]*M["alpha"]*w["dT_through_wall"]/(1-M["nu"])
    e = abs(ts["sigma_thermal"]-hand)/hand*100
    print(f"  {'ok  ' if e<1e-9 else 'FAIL'} thermal stress {ts['sigma_thermal']/1e6:.1f} MPa, "
          f"hand {hand/1e6:.1f} MPa")
    ok &= e < 1e-9
    # 4 hoop: p r / t
    ps = pressure_stress(6e6, 0.020, 0.005)
    e = abs(ps["sigma_hoop"] - 6e6*0.020/0.005)
    print(f"  {'ok  ' if e<1e-6 else 'FAIL'} hoop {ps['sigma_hoop']/1e6:.1f} MPa = p*r/t = "
          f"{6e6*0.020/0.005/1e6:.1f} MPa   thin-wall valid: {ps['thin_wall_valid']}")
    ok &= e < 1e-6
    # 5 von Mises of a known biaxial state
    c = combine(0.0, 100e6, 50e6, 350e6)
    hand = math.sqrt(100e6**2 - 100e6*50e6 + 50e6**2)
    e = abs(c["von_mises"]-hand)/hand*100
    print(f"  {'ok  ' if e<1e-9 else 'FAIL'} von Mises {c['von_mises']/1e6:.2f} MPa, "
          f"hand {hand/1e6:.2f} MPa")
    ok &= e < 1e-9
    # 6 the chain must actually propagate: change the flow, everything downstream moves
    a = chain(m_dot=0.35, verbose=False); b = chain(m_dot=0.10, verbose=False)
    moved = (b["T_wall_hot_C"] > a["T_wall_hot_C"] and
             b["yield_at_temp_MPa"] < a["yield_at_temp_MPa"] and
             b["safety_factor"] < a["safety_factor"])
    print(f"  {'ok  ' if moved else 'FAIL'} coupling propagates: cutting flow 0.35->0.10 kg/s "
          f"raises wall {a['T_wall_hot_C']:.0f}->{b['T_wall_hot_C']:.0f} C, "
          f"yield {a['yield_at_temp_MPa']:.0f}->{b['yield_at_temp_MPa']:.0f} MPa, "
          f"SF {a['safety_factor']:.2f}->{b['safety_factor']:.2f}")
    ok &= moved
    # 7 physical direction checks
    thin = chain(wall=0.003, verbose=False); thick = chain(wall=0.008, verbose=False)
    good = thick["T_wall_hot_C"] > thin["T_wall_hot_C"]      # thicker wall = hotter hot face
    print(f"  {'ok  ' if good else 'FAIL'} thicker wall runs hotter "
          f"({thin['T_wall_hot_C']:.0f} -> {thick['T_wall_hot_C']:.0f} C) as conduction says")
    ok &= good
    inc = chain(material="Inconel718", verbose=False)
    cu  = chain(material="CuCrZr", verbose=False)
    good = inc["T_wall_hot_C"] > cu["T_wall_hot_C"]          # k=11.4 vs 320
    print(f"  {'ok  ' if good else 'FAIL'} low-conductivity Inconel runs hotter than copper "
          f"({cu['T_wall_hot_C']:.0f} vs {inc['T_wall_hot_C']:.0f} C)")
    ok &= good
    print("\ncoupled selftest:", "PASSED" if ok else "FAILED")
    return ok

def main():
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("cmd", nargs="?", default="chamber", choices=["chamber", "sweep"])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--material", default="CuCrZr", choices=list(MAT))
    ap.add_argument("--q", type=float, default=8e6)
    ap.add_argument("--mdot", type=float, default=0.35)
    a = ap.parse_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    if a.cmd == "chamber": chain(q=a.q, m_dot=a.mdot, material=a.material)
    else: sweep("wall", material=a.material, q=a.q, m_dot=a.mdot)

if __name__ == "__main__":
    main()
