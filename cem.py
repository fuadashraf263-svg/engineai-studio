#!/usr/bin/env python3
"""
cem.py - Computational Engineering Model. Requirements in, printable part out.

This is the loop LEAP 71 actually sells: you state what the part must DO -
heat flux, chamber pressure, coolant flow, material, required margin - and the
model sizes it, generates the geometry, and checks it can be made.

It closes the circle between the two halves that were standalone:

    requirements -> coupled.py (flow/thermal/stress) -> chosen wall + channels
                 -> sdf_kernel.py (implicit geometry) -> watertight STL
                 -> AM constraints -> verdict + report

Nothing here is AI. It is explicit physics driving explicit geometry, which is
exactly how LEAP 71 works. The novelty is yours; the computer supplies speed.

  python cem.py design                          size and build the default chamber
  python cem.py design --q 12e6 --material Inconel718
  python cem.py --selftest
"""
import os, sys, math, json, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT  = os.path.expanduser(os.environ.get("ENGINEAI_OUT", "~/engineai/out"))

import coupled as C

# ----------------------------------------------------------- requirements
DEFAULT = dict(
    r_inner=0.020,        # m, chamber radius
    length=0.060,         # m
    q=8e6,                # W/m2, hot-gas heat flux
    p_chamber=6e6,        # Pa
    m_dot=0.35,           # kg/s coolant
    coolant="Water",
    T_cool=350.0,         # K
    P_cool=8e6,           # Pa
    material="CuCrZr",
    min_SF=1.5,           # required safety factor on yield
    min_land=0.0008,      # m, metal either side of a channel (AM limit)
    min_wall=0.0008,      # m, thinnest printable feature
    max_T_frac=0.6,       # hot wall must stay below this fraction of T_melt [C]
)

WALLS = [0.0020, 0.0025, 0.0030, 0.0035, 0.0040, 0.0050, 0.0060, 0.0080]
CHANS = [8, 10, 12, 16, 20, 24, 30]

# --------------------------------------------------------------- sizing
def evaluate(req, wall, n_ch, ch_r):
    """One candidate through the coupled chain. Returns metrics + why it fails."""
    d_h = 2*ch_r
    r = C.chain(q=req["q"], m_dot=req["m_dot"], d_h=d_h, n_ch=n_ch, wall=wall,
                r_inner=req["r_inner"], p_chamber=req["p_chamber"],
                material=req["material"], coolant=req["coolant"],
                T_cool=req["T_cool"], P_cool=req["P_cool"], verbose=False)
    M = C.MAT[req["material"]]
    T_limit = req["max_T_frac"]*M["Tm"]
    # coolant-side pressure drop, so a silly channel size is not "free"
    f = r["steps"]["1 FLOW"]
    fd = 0.316*f["Re"]**-0.25 if f["Re"] > 2300 else 64/max(f["Re"], 1e-9)
    dp = fd*(req["length"]/d_h)*f["rho"]*f["velocity"]**2/2
    # mass of the wall (annulus minus channels)
    vol = math.pi*((req["r_inner"]+wall)**2 - req["r_inner"]**2)*req["length"] \
          - n_ch*math.pi*ch_r**2*req["length"]
    fails = []
    if 2*ch_r > wall - 2*req["min_land"]: fails.append("channel will not fit the wall")
    if r["safety_factor"] < req["min_SF"]: fails.append(f"SF {r['safety_factor']:.2f} < {req['min_SF']}")
    if r["T_wall_hot_C"] > T_limit:       fails.append(f"hot wall {r['T_wall_hot_C']:.0f} C > {T_limit:.0f} C")
    if wall < req["min_wall"]:            fails.append("wall below the printable minimum")
    if vol <= 0:                          fails.append("channels consume the whole wall")
    return dict(wall=wall, n_ch=n_ch, ch_r=ch_r, d_h=d_h,
                SF=r["safety_factor"], T_hot=r["T_wall_hot_C"], vM=r["von_mises_MPa"],
                yield_T=r["yield_at_temp_MPa"], h=f["h"], Re=f["Re"], dp_kPa=dp/1e3,
                volume_m3=vol, mass_kg=vol*8900 if req["material"] == "CuCrZr" else vol*8200,
                feasible=not fails, fails=fails)

def size_it(req, verbose=True):
    """Search the design space. Lightest feasible candidate wins."""
    cands, tried = [], 0
    for wall in WALLS:
        for n_ch in CHANS:
            ch_r = (wall - 2*req["min_land"])/2
            if ch_r <= 0.0003: continue
            tried += 1
            cands.append(evaluate(req, wall, n_ch, ch_r))
    good = [c for c in cands if c["feasible"]]
    if verbose:
        print(f"\n  SIZING - searched {tried} candidates, {len(good)} feasible")
        if good:
            print(f"  {'wall':>6s} {'n_ch':>5s} {'ch_r':>6s} {'T_hot':>7s} {'SF':>5s} "
                  f"{'dp':>7s} {'mass':>7s}")
            for c in sorted(good, key=lambda x: x["mass_kg"])[:6]:
                print(f"  {c['wall']*1000:6.2f} {c['n_ch']:5d} {c['ch_r']*1000:6.2f} "
                      f"{c['T_hot']:7.0f} {c['SF']:5.2f} {c['dp_kPa']:6.0f}k {c['mass_kg']*1000:6.1f}g")
        else:
            print("  none feasible. Closest misses:")
            for c in sorted(cands, key=lambda x: -x["SF"])[:4]:
                print(f"    wall {c['wall']*1000:.1f} mm, {c['n_ch']} ch: SF {c['SF']:.2f}, "
                      f"T {c['T_hot']:.0f} C  <- {'; '.join(c['fails'])}")
    return (sorted(good, key=lambda x: x["mass_kg"])[0] if good else None), cands

# ------------------------------------------------------------- geometry
def build_geometry(req, d, n=110, verbose=True):
    import sdf_kernel as S
    mm = 1000.0
    R, wall, H = req["r_inner"]*mm, d["wall"]*mm, req["length"]*mm
    shape, bounds, _ = S.part_cooled_chamber(R=R, H=H, wall=wall,
                                             n_ch=d["n_ch"], ch_r=d["ch_r"]*mm, n=n)
    res = S.build("cem_chamber", shape, bounds, n, quiet=not verbose)
    chk = S.am_check(shape, bounds, n=70, min_wall=req["min_wall"]*mm)
    return res, chk

# ---------------------------------------------------------------- report
def report(req, d, res, chk, path=None):
    lines = [f"# EngineAI - Computational Engineering Model",
             f"_{datetime.datetime.now():%d %B %Y, %H:%M}_", "",
             "## Requirements", ""]
    for k, v in req.items(): lines.append(f"- {k}: {v}")
    lines += ["", "## Sized design", "",
              f"- wall thickness: {d['wall']*1000:.2f} mm",
              f"- channels: {d['n_ch']} x D{d['ch_r']*2000:.2f} mm",
              f"- hot wall temperature: {d['T_hot']:.0f} C",
              f"- von Mises: {d['vM']:.1f} MPa vs yield at temperature {d['yield_T']:.0f} MPa",
              f"- safety factor: {d['SF']:.2f}",
              f"- coolant h: {d['h']:.0f} W/m2K at Re {d['Re']:.0f}",
              f"- coolant pressure drop: {d['dp_kPa']:.0f} kPa",
              f"- wall mass: {d['mass_kg']*1000:.1f} g", "",
              "## Geometry", "",
              f"- triangles: {res['triangles']}",
              f"- watertight: {res['watertight']}",
              f"- mesh volume: {res['volume']:.0f} mm^3",
              f"- file: {os.path.basename(res['path'])}", "",
              "## Manufacturability", ""]
    for k, v in chk.items(): lines.append(f"- {k}: {v}")
    lines += ["", "## Provenance", "",
              "Dittus-Boelter (Incropera ch.8) -> 1-D series resistance (Incropera ch.3) ->",
              "E*alpha*deltaT/(1-v) (Roark ch.16) -> thin-wall hoop (Shigley eq. 3-53) ->",
              "von Mises with elevated-temperature yield derating.",
              "Geometry from a signed distance field, meshed by marching cubes.",
              "", "Every number above was computed, not recalled."]
    os.makedirs(OUT, exist_ok=True)
    path = path or os.path.join(OUT, "cem_report.md")
    open(path, "w").write("\n".join(lines))
    return path

def run(req=None, n=110, verbose=True):
    req = {**DEFAULT, **(req or {})}
    if verbose:
        print(f"\n  CEM - {C.MAT[req['material']]['name']} chamber, "
              f"r {req['r_inner']*1000:.0f} mm, q {req['q']/1e6:.1f} MW/m2, "
              f"p {req['p_chamber']/1e6:.1f} MPa, SF >= {req['min_SF']}")
    d, _all = size_it(req, verbose)
    if not d:
        if verbose: print("\n  NO FEASIBLE DESIGN. Raise the coolant flow, drop the heat "
                          "flux, or change material.")
        return dict(ok=False, requirements=req)
    if verbose:
        print(f"\n  CHOSEN  wall {d['wall']*1000:.2f} mm, {d['n_ch']} channels of "
              f"D{d['ch_r']*2000:.2f} mm, SF {d['SF']:.2f}, {d['mass_kg']*1000:.0f} g")
    res, chk = build_geometry(req, d, n, verbose)
    p = report(req, d, res, chk)
    if verbose:
        print(f"  GEOMETRY {res['triangles']} tris, "
              f"{'watertight' if res['watertight'] else 'NOT watertight'}")
        print(f"  AM       {chk['note']}  (overhang {chk['overhang_fraction']*100:.0f}%)")
        print(f"  REPORT   {p}\n           {res['path']}")
    return dict(ok=True, design=d, geometry=res, am=chk, report=p, requirements=req)

# ------------------------------------------------------------- selftest
def selftest():
    ok = True
    print("\nCEM SELFTEST")
    req = dict(DEFAULT)
    # 1 the sizer must reject a channel that cannot fit its wall
    bad = evaluate(req, wall=0.002, n_ch=10, ch_r=0.0009)
    good = any("not fit" in f for f in bad["fails"])
    print(f"  {'ok  ' if good else 'FAIL'} rejects D 1.8 mm channel in a 2 mm wall")
    ok &= good
    # 2 harder duty must need a better design or fail
    easy = run({**req, "q": 3e6}, n=40, verbose=False)
    hard = run({**req, "q": 25e6}, n=40, verbose=False)
    good = easy["ok"] and (not hard["ok"] or hard["design"]["SF"] >= req["min_SF"])
    print(f"  {'ok  ' if good else 'FAIL'} q 3 MW/m2 feasible; q 25 MW/m2 "
          f"{'infeasible (correct)' if not hard['ok'] else 'still met'}")
    ok &= good
    # 3 every returned design must actually meet the requirement
    if easy["ok"]:
        d = easy["design"]
        good = d["SF"] >= req["min_SF"] and 2*d["ch_r"] <= d["wall"] - 2*req["min_land"] + 1e-12
        print(f"  {'ok  ' if good else 'FAIL'} chosen design honours SF ({d['SF']:.2f} >= "
              f"{req['min_SF']}) and the channel fits")
        ok &= good
    # 4 physics direction: more coolant -> better margin
    a = run({**req, "m_dot": 0.15, "q": 4e6}, n=40, verbose=False)
    b = run({**req, "m_dot": 0.60, "q": 4e6}, n=40, verbose=False)
    if a["ok"] and b["ok"]:
        good = b["design"]["T_hot"] < a["design"]["T_hot"]
        print(f"  {'ok  ' if good else 'FAIL'} more coolant runs cooler "
              f"({a['design']['T_hot']:.0f} -> {b['design']['T_hot']:.0f} C)")
        ok &= good
    # 5 geometry is real and watertight
    r = run({**req, "q": 3e6}, n=64, verbose=False)
    good = r["ok"] and r["geometry"]["watertight"] and r["geometry"]["triangles"] > 5000
    print(f"  {'ok  ' if good else 'FAIL'} generated {r['geometry']['triangles']} triangles, "
          f"watertight {r['geometry']['watertight']}")
    ok &= good
    # 6 mesh volume must agree with the analytic annulus-minus-channels
    d = r["design"]; q = r["requirements"]
    analytic = (math.pi*((q["r_inner"]+d["wall"])**2 - q["r_inner"]**2)*q["length"]
                - d["n_ch"]*math.pi*d["ch_r"]**2*q["length"])*1e9   # m3 -> mm3
    e = abs(r["geometry"]["volume"] - analytic)/analytic*100
    print(f"  {'ok  ' if e < 12 else 'FAIL'} mesh volume {r['geometry']['volume']:.0f} mm^3 vs "
          f"analytic {analytic:.0f} mm^3 ({e:.1f}%)")
    ok &= e < 12
    # 7 report written with provenance
    good = os.path.exists(r["report"]) and "Dittus-Boelter" in open(r["report"]).read()
    print(f"  {'ok  ' if good else 'FAIL'} report written with the full citation chain")
    ok &= good
    print("\ncem selftest:", "PASSED" if ok else "FAILED")
    return ok

def main():
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("cmd", nargs="?", default="design", choices=["design"])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--material", default=DEFAULT["material"], choices=list(C.MAT))
    ap.add_argument("--q", type=float, default=DEFAULT["q"])
    ap.add_argument("--mdot", type=float, default=DEFAULT["m_dot"])
    ap.add_argument("--p", type=float, default=DEFAULT["p_chamber"])
    ap.add_argument("--sf", type=float, default=DEFAULT["min_SF"])
    ap.add_argument("--n", type=int, default=110)
    a = ap.parse_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    run(dict(material=a.material, q=a.q, m_dot=a.mdot, p_chamber=a.p, min_SF=a.sf), n=a.n)

if __name__ == "__main__":
    main()
