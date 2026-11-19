#!/usr/bin/env python3
"""
direct.py - deterministic routing. Files and geometry every time, no model needed.

The model is now the FALLBACK, not the primary. For the questions this product
exists to answer - beams, chambers, lattices, CAD - the pipeline runs directly:
compute, generate geometry, write files, push the 3D view. A free-tier model
cannot break it because it is not involved.

Where a value is missing (no section given for a beam) it uses a stated default
and labels it, rather than refusing to answer. An answer with a visible
assumption is more useful than a question.

  python direct.py "tip deflection of a 1.2 m steel cantilever under 2.5 kN"
  python direct.py "design a cooled chamber at 4 MW/m2"
  python direct.py "gyroid lattice block 30 mm"
  python direct.py --selftest
"""
import os, re, sys, math, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT  = os.path.expanduser(os.environ.get("ENGINEAI_OUT", "~/engineai/out"))

DEFAULTS = {"b": 0.05, "h": 0.10, "E": 200e9}     # 50 x 100 mm steel, stated when used

LAST = {}        # the part most recently built, so "drawings of it" means something
_LASTF = os.path.join(OUT, ".last_part.json")

def _save_last():
    try:
        os.makedirs(OUT, exist_ok=True)
        json.dump(LAST, open(_LASTF, "w"), default=str)
    except Exception: pass

def _load_last():
    """Survive a server restart, and fall back to the newest solid on disk so
    'drawings of it' works even if the part was built in an earlier session."""
    if LAST.get("stl") and os.path.exists(LAST["stl"]): return LAST
    try:
        d = json.load(open(_LASTF))
        if d.get("stl") and os.path.exists(d["stl"]):
            LAST.clear(); LAST.update(d); return LAST
    except Exception: pass
    try:
        import glob
        stls = [f for f in glob.glob(os.path.join(OUT, "*.stl")) if os.path.getsize(f) > 400]
        if stls:
            newest = max(stls, key=os.path.getmtime)
            LAST.clear()
            LAST.update(kind="unknown", stl=newest, parts=[newest],
                        label=os.path.splitext(os.path.basename(newest))[0]
                                .replace("sdf_", ""))
            return LAST
    except Exception: pass
    return LAST

def remember(kind, stl=None, parts=None, label=None, **extra):
    LAST.clear()
    LAST.update(kind=kind, stl=stl, parts=parts or ([stl] if stl else []),
                label=label or kind, **extra)
    _save_last()

def _q(text):
    import agent_loop as A
    return A.parse_quantities(text)

def wants_geometry(t):
    return bool(re.search(r"\b(cad|3d|stl|step|sandbox|model|geometry|solid|show|render|"
                          r"visuali[sz]e|draw)\b", t.lower()))

# ------------------------------------------------------------------ beams
BEAM = re.compile(r"(deflect|deflection|sag|bend|stress|cantilever|beam|buckl|column)", re.I)

def do_beam(text, vals):
    """Compute, then ALWAYS produce the 3D field view and a solid."""
    import engineai as E, viz3d, generators as G
    steps, files, notes = [], [], []
    buckling = bool(re.search(r"buckl|critical load|column", text, re.I))
    key = "euler_pinned" if buckling else "cantilever_point_tip"
    ref = next(r for r in E.REFERENCE if r["key"] == key)

    v = dict(vals)
    if "E" not in v: v["E"] = DEFAULTS["E"]; notes.append("E = 200 GPa (steel) assumed")
    if "I" not in v:
        b, h = DEFAULTS["b"], DEFAULTS["h"]
        v["I"] = b*h**3/12
        notes.append(f"section {b*1000:g} x {h*1000:g} mm assumed -> I = {v['I']:.4g} m^4")
    else:
        b = vals.get("_b", DEFAULTS["b"]); h = vals.get("_h", DEFAULTS["h"])
    if "L" not in v: v["L"] = 1.0; notes.append("L = 1 m assumed")
    if not buckling and "P" not in v: v["P"] = 1000.0; notes.append("P = 1 kN assumed")

    steps.append(dict(tool="lookup_formula", summary=f"{ref['expr']}  [{ref['unit']}]",
                      source=ref["src"], thought="matched deterministically"))
    val, miss = E.compute(ref, v)
    if val is None:
        return dict(ok=False, steps=steps, error="missing " + ", ".join(miss))
    from worksheet import nice_unit, fmt
    disp, dunit, _f = nice_unit(val, ref["unit"])
    steps.append(dict(tool="compute", summary=f"= {fmt(disp)} {dunit}   ({val:.6g} {ref['unit']})",
                      source=ref["src"], thought="closed form, no model involved"))
    if notes:
        steps.append(dict(tool="assumptions", summary="; ".join(notes),
                          source="stated defaults", warn=True))
    # ---- 2D drawings, always
    b = DEFAULTS["b"]; h = DEFAULTS["h"]
    if "I" in vals:                                   # recover a section from I if we can
        h = (12*vals["I"]/b)**(1/3)
    twod = []
    for gen, kw, lbl in (("beam_diagram", dict(P=v.get("P", 1000.0), L=v["L"], E=v["E"],
                                               I=v["I"], case="cantilever"),
                          "loaded beam with deflected shape"),
                         ("shear_moment", dict(P=v.get("P", 1000.0), L=v["L"],
                                               case="cantilever"), "shear + moment diagrams"),
                         ("stress_field", dict(M=v.get("P", 1000.0)*v["L"], b=b, h=h),
                          "bending stress across the section")):
        try:
            if G.available(gen):
                f_ = G.REGISTRY[gen](**kw); twod.append(f_); files.append(f_)
                steps.append(dict(tool=gen, summary=lbl + ": " + os.path.basename(f_),
                                  source="2D, closed form"))
        except Exception as e:
            steps.append(dict(tool=gen, summary=f"failed: {type(e).__name__}", warn=True))
    # ---- 3D field
    try:
        view, tip, smax = viz3d.demo_beam(P=v.get("P", 1000.0), L=v["L"], E=v["E"], b=b, h=h)
        files.append(view)
        steps.append(dict(tool="visualise", summary=f"3D stress field on the deformed beam, "
                          f"sigma_max {smax/1e6:.4g} MPa", source="closed form"))
    except Exception as e:
        steps.append(dict(tool="visualise", summary=f"failed: {type(e).__name__}", warn=True))
        view = None
    if wants_geometry(text):
        try:
            stl = G.REGISTRY["stl_part"](w=v["L"]*1000, d=b*1000, h=h*1000, rib=False, name="beam")
            files.append(stl)
            s2 = None
            if G.available("cad_step"):
                r = G.REGISTRY["cad_step"](w=v["L"]*1000, d=b*1000, h=h*1000,
                                           hole=min(b, h)*200, name="beam")
                for f_ in (r if isinstance(r, list) else [r]): files.append(f_)
                s2 = "STEP + STL"
            steps.append(dict(tool="make_cad", summary=(s2 or "STL") + " written: " +
                              ", ".join(os.path.basename(f_) for f_ in files[-3:]),
                              source="CadQuery / SDF"))
        except Exception as e:
            steps.append(dict(tool="make_cad", summary=f"failed: {type(e).__name__}", warn=True))
    if files:
        stl_f = next((f_ for f_ in files if f_.endswith(".stl")), None)
        if stl_f: remember("beam", stl_f, label="beam", L=v["L"], b=b, h=h)
    ans = f"{fmt(disp)} {dunit}"
    ev = ref["src"] + ("   ⚠ assumed: " + "; ".join(notes) if notes else "")
    return dict(ok=True, steps=steps, files=files, view=view, answer=ans, evidence=ev)

# --------------------------------------------------------------- chambers
CHAMBER = re.compile(r"(cooled|regen|thrust|combustion)\s*chamber|chamber\s*at|rocket", re.I)

def do_chamber(text, vals):
    import cem, coupled as C
    m = re.search(r"([\d.]+)\s*(mw|kw)\s*/?\s*m", text.lower())
    q = float(m.group(1))*(1e6 if m.group(2) == "mw" else 1e3) if m else 4e6
    mat = "CuCrZr"
    for k in C.MAT:
        if k.lower()[:4] in text.lower().replace(" ", ""): mat = k
    steps = [dict(tool="requirements", source="your words",
                  summary=f"q = {q/1e6:g} MW/m2, material {C.MAT[mat]['name']}",
                  thought="parsed deterministically")]
    r = cem.run(dict(q=q, material=mat), n=int(os.environ.get("ENGINEAI_CEM_N", "80")),
                verbose=False)
    if not r["ok"]:
        steps.append(dict(tool="size_it", summary="no feasible design at this heat flux — "
                          "raise the coolant flow, lower q, or change material", warn=True))
        return dict(ok=True, steps=steps, files=[], view=None,
                    answer="No feasible design", evidence="coupled flow/thermal/stress chain")
    d, g = r["design"], r["geometry"]
    steps += [dict(tool="coupled_chain", source="Dittus-Boelter -> conduction -> thermal stress",
                   summary=f"wall {d['wall']*1000:.2f} mm, {d['n_ch']} channels of "
                           f"⌀{d['ch_r']*2000:.2f} mm, hot wall {d['T_hot']:.0f} C, SF {d['SF']:.2f}"),
              dict(tool="generate_geometry", source="signed distance field",
                   summary=f"{g['triangles']} triangles, "
                           f"{'watertight' if g['watertight'] else 'NOT watertight'}"),
              dict(tool="am_check", source="AM constraints",
                   summary=f"{r['am']['note']}, overhang {r['am']['overhang_fraction']*100:.0f}%",
                   warn=not r["am"].get("min_wall_ok"))]
    files = [g["path"], r["report"]]
    try:                                     # 2D: how the margin varies with wall thickness
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        walls = [w for w in (0.002, 0.0025, 0.003, 0.004, 0.005, 0.006, 0.008)]
        rows = []
        for w in walls:
            rr = C.chain(q=q, wall=w, material=mat, verbose=False)
            rows.append((w*1000, rr["T_wall_hot_C"], rr["safety_factor"]))
        fig, ax1 = plt.subplots(figsize=(7, 4.2))
        ax1.plot([x[0] for x in rows], [x[1] for x in rows], "o-", color="#c0392b",
                 label="hot wall temperature")
        ax1.set_xlabel("wall thickness [mm]"); ax1.set_ylabel("hot wall [°C]", color="#c0392b")
        ax2 = ax1.twinx()
        ax2.plot([x[0] for x in rows], [x[2] for x in rows], "s--", color="#1b6ca8",
                 label="safety factor")
        ax2.axhline(1.5, ls=":", color="#7f8c8d"); ax2.set_ylabel("safety factor", color="#1b6ca8")
        ax1.axvline(d["wall"]*1000, color="#27ae60", lw=2, alpha=.6)
        ax1.set_title(f"chosen wall {d['wall']*1000:.2f} mm — q = {q/1e6:g} MW/m², "
                      f"{C.MAT[mat]['name']}")
        ax1.grid(alpha=.3); fig.tight_layout()
        p2 = os.path.join(OUT, "chamber_tradeoff.png"); fig.savefig(p2, dpi=150); plt.close(fig)
        files.append(p2)
        steps.append(dict(tool="tradeoff_plot", summary="wall thickness vs temperature and margin",
                          source="2D, coupled chain"))
    except Exception as e:
        steps.append(dict(tool="tradeoff_plot", summary=f"failed: {type(e).__name__}", warn=True))
    view = None
    try:
        import generators as G
        view = G.REGISTRY["viewer_3d"](stl=g["path"], title="cooled chamber")
        files.append(view)
    except Exception: pass
    # the three bodies, so an exploded view has something to explode
    parts, plabels = [g["path"]], ["chamber wall"]
    try:
        import sdf_kernel as S
        R = r["requirements"]["r_inner"]*1000; H = R*3; W = d["wall"]*1000
        for nm, shp, lb in (("liner", S.cylinder(R, H), "inner liner"),
                            ("jacket", S.subtract(S.cylinder(R+W+1.2, H),
                                                  S.cylinder(R+W, H)), "outer jacket")):
            rr = S.build("cem_" + nm, shp, ((-(R+W+3), -(R+W+3), -H/2-2),
                                            (R+W+3, R+W+3, H/2+2)), 56, quiet=True)
            parts.append(rr["path"]); plabels.append(lb); files.append(rr["path"])
    except Exception: pass
    remember("chamber", g["path"], parts=parts, label="cooled_chamber", part_labels=plabels)
    return dict(ok=True, steps=steps, files=files, view=view,
                answer=f"wall {d['wall']*1000:.2f} mm, {d['n_ch']} channels, SF {d['SF']:.2f}, "
                       f"{d['mass_kg']*1000:.0f} g",
                evidence="coupled chain + SDF geometry + AM check; report written")

# --------------------------------------------------------------- lattices
LATTICE = re.compile(r"(lattice|gyroid|schwarz|tpms|infill|organic|bracket|blend)", re.I)

def do_lattice(text, vals):
    import sdf_kernel as S, generators as G
    t = text.lower()
    size = (vals.get("L") or 0.03)*1000
    n = int(os.environ.get("ENGINEAI_SDF_N", "90"))
    if "bracket" in t or "organic" in t or "blend" in t:
        shape, b, _ = S.part_organic_bracket(n); name = "organic_bracket"
        what = "smooth organic blend"
    else:
        cell = 8.0
        shape, b, _ = S.part_lattice_block(size=max(size, 20), cell=cell, t=0.8, skin=1.2, n=n)
        name = "lattice_block"; what = f"gyroid lattice, {size:g} mm block, {cell:g} mm cell"
    steps = [dict(tool="sdf_geometry", source="implicit / signed distance field",
                  summary=what + " — not expressible in B-rep CAD")]
    r = S.build(name, shape, b, n, quiet=True)
    steps.append(dict(tool="marching_cubes",
                      summary=f"{r['triangles']} triangles, vol {r['volume']:.0f} mm³, "
                              f"{'watertight' if r['watertight'] else 'open edges'}",
                      source="mesh check"))
    files = [r["path"]]; view = None
    if r["triangles"] < 120000:            # matplotlib cannot render huge meshes
        try:
            import render_image as RI
            png = RI.render(r["path"], views=("iso", "front"))
            files.append(png)
            steps.append(dict(tool="render_image", summary="shaded 2D render: " +
                              os.path.basename(png), source="2D"))
        except Exception: pass
    try:
        view = G.REGISTRY["viewer_3d"](stl=r["path"], title=name); files.append(view)
    except Exception: pass
    remember("lattice", r["path"], label=name)
    return dict(ok=True, steps=steps, files=files, view=view,
                answer=f"{name}: {r['triangles']} triangles, {r['volume']:.0f} mm³",
                evidence="implicit geometry, meshed by marching cubes")

# -------------------------------------------------------------- plain CAD
def do_cad(text, vals):
    import generators as G
    L = (vals.get("L") or 0.1)*1000
    steps, files = [], []
    stl = G.REGISTRY["stl_part"](w=L, d=40, h=20, rib=True, name="part")
    files.append(stl)
    steps.append(dict(tool="stl_part", summary=f"watertight L-bracket {L:g} x 40 x 20 mm",
                      source="SDF prism"))
    if G.available("cad_step"):
        r = G.REGISTRY["cad_step"](w=L, d=40, h=20, hole=8, name="part")
        for f_ in (r if isinstance(r, list) else [r]): files.append(f_)
        steps.append(dict(tool="cad_step", summary="STEP + STL written", source="CadQuery"))
    for gen, lbl in (("dxf_drawing", "dimensioned DXF"), ("drawing_png", "drawing PNG"),
                     ("render_image", "shaded 4-view render")):
        if G.available(gen):
            try:
                f_ = G.REGISTRY[gen](w=L, h=40) if gen != "render_image" else G.REGISTRY[gen](stl=stl)
                files.append(f_); steps.append(dict(tool=gen, summary=lbl + ": " +
                                                    os.path.basename(f_), source="generator"))
            except Exception: pass
    view = None
    try:
        view = G.REGISTRY["viewer_3d"](stl=stl, title="part"); files.append(view)
    except Exception: pass
    remember("part", stl, label="part")
    return dict(ok=True, steps=steps, files=files, view=view,
                answer=", ".join(os.path.basename(f_) for f_ in files),
                evidence="generated files, all written to disk")

# --------------------------------------------------------------- drawings
DRAWING = re.compile(r"(mechanical\s*drawing|technical\s*drawing|drawing|blueprint|"
                     r"orthograph|all\s*view|multi.?view|three\s*view|section\s*view|"
                     r"explod|exploded|elevation|plan\s*view|dimension)", re.I)

def do_drawings(text, vals):
    """Drawings of whatever was built last. This is what 'of it' refers to."""
    import drawings as D, generators as G
    t = text.lower()
    _load_last()
    if not LAST.get("stl") or not os.path.exists(LAST["stl"]):
        return dict(ok=False, steps=[dict(tool="drawings",
                    summary="nothing has been built yet in this session", warn=True)],
                    error=("Build something first, then ask for drawings. Try: "
                           "'design a cooled chamber at 4 MW/m2', then 'drawings of it'."))
    stl, label = LAST["stl"], LAST.get("label", "part")
    steps, files = [dict(tool="subject", source="previous step",
                         summary=f"drawings of {os.path.basename(stl)} ({label})")], []
    try:
        p = D.multiview(stl, name=label, title=label.replace("_", " ").upper(),
                        notes="third-angle projection · dimensions from the generated solid")
        files.append(p)
        steps.append(dict(tool="multiview", source="orthographic projection",
                          summary="FRONT / SIDE / TOP / ISOMETRIC with overall dimensions"))
    except Exception as e:
        steps.append(dict(tool="multiview", summary=f"failed: {type(e).__name__}: {e}", warn=True))
    # \ball\b, not "all" - the bare substring fires on wall, small, install,
    # metallic and alloy, so "section through the wall" would have been read
    # as a request for every view there is.
    if re.search(r"section|cut|cross", t) or re.search(r"\ball\b", t):
        try:
            p = D.section(stl, axis=1, name=label); files.append(p)
            steps.append(dict(tool="section", source="mid-plane cut",
                              summary="SECTION A–A showing the internal features"))
        except Exception as e:
            steps.append(dict(tool="section", summary=f"failed: {type(e).__name__}", warn=True))
    if re.search(r"explod", t) or "all" in t:
        parts = LAST.get("parts") or [stl]
        try:
            p = D.exploded(parts, LAST.get("part_labels"), name=label); files.append(p)
            steps.append(dict(tool="exploded", source="assembly",
                              summary=f"exploded view of {len(parts)} bod"
                                      f"{'y' if len(parts)==1 else 'ies'}"))
        except Exception as e:
            steps.append(dict(tool="exploded", summary=f"failed: {type(e).__name__}", warn=True))
    try:
        if G.available("dxf_drawing"):
            import drawings as _d
            tris = _d.load(stl); mn, mx = tris.reshape(-1,3).min(0), tris.reshape(-1,3).max(0)
            f_ = G.REGISTRY["dxf_drawing"](w=float(mx[0]-mn[0]), h=float(mx[2]-mn[2]),
                                           title=label.upper()[:14])
            files.append(f_)
            steps.append(dict(tool="dxf_drawing", source="CAD exchange",
                              summary="DXF written: " + os.path.basename(f_)))
    except Exception: pass
    return dict(ok=True, steps=steps, files=files, view=None,
                answer=f"{len(files)} drawing file(s) for {label}",
                evidence="projected from the generated solid; dimensions measured, not stated")

# --------------------------------------------------------------- reshape
SHAPES = {"box": "box", "cube": "box", "block": "box", "rectangular": "box",
          "cylinder": "cylinder", "tube": "tube", "pipe": "tube", "round": "cylinder",
          "sphere": "sphere", "ball": "sphere", "cone": "cone",
          "torus": "torus", "ring": "torus", "l-bracket": "bracket", "bracket": "bracket"}
RESHAPE = re.compile(r"\b(change|make|turn|convert|switch|instead)\b.{0,24}?"
                     r"\b(box|cube|block|cylinder|tube|pipe|sphere|ball|cone|torus|ring|bracket)\b"
                     r"|\b(box|cube|cylinder|tube|sphere|cone|torus)\s*(shape|instead|version)\b",
                     re.I)

def do_reshape(text, vals):
    """Rebuild the last part as a different primitive, same overall size."""
    import sdf_kernel as S, generators as G
    _load_last()
    t = text.lower()
    want = next((v for k, v in SHAPES.items() if re.search(r"\b"+re.escape(k)+r"\b", t)), "box")
    # keep the size of what we had, if we have it
    L = W = H = 40.0
    if LAST.get("stl") and os.path.exists(LAST["stl"]):
        try:
            import drawings as D
            tris = D.load(LAST["stl"])
            mn, mx = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
            L, W, H = [float(x) for x in (mx-mn)]
        except Exception: pass
    if vals.get("L"): L = W = vals["L"]*1000
    r = max(L, W)/2
    build = {"box":      (S.box(L, W, H), "box"),
             "cylinder": (S.cylinder(r, H), "cylinder"),
             "tube":     (S.subtract(S.cylinder(r, H), S.cylinder(r*0.72, H*1.1)), "tube"),
             "sphere":   (S.sphere(r), "sphere"),
             "cone":     (S.intersect(S.cylinder(r, H),
                                      lambda x, y, z: __import__("numpy").sqrt(x*x+y*y)
                                                      - r*(0.5 - z/H)), "cone"),
             "torus":    (S.torus(r*0.7, r*0.3), "torus"),
             "bracket":  (None, "bracket")}[want]
    steps = [dict(tool="reshape", source="signed distance field",
                  summary=f"rebuilding as a {want}, keeping {L:.1f} x {W:.1f} x {H:.1f} mm")]
    if want == "bracket":
        stl = G.REGISTRY["stl_part"](w=L, d=W, h=H/3, rib=True, name="reshaped")
        res = dict(path=stl, triangles=0, watertight=True, volume=0.0)
    else:
        shape = build[0]
        pad = max(L, W, H)*0.08 + 2
        b = ((-L/2-pad, -W/2-pad, -H/2-pad), (L/2+pad, W/2+pad, H/2+pad))
        res = S.build("reshaped_" + want, shape, b, int(os.environ.get("ENGINEAI_SDF_N", "90")),
                      quiet=True)
        stl = res["path"]
    steps.append(dict(tool="marching_cubes",
                      summary=f"{res.get('triangles', 0)} triangles, "
                              f"{'watertight' if res.get('watertight') else 'check mesh'}"))
    files = [stl]; view = None
    try:
        view = G.REGISTRY["viewer_3d"](stl=stl, title=want); files.append(view)
    except Exception: pass
    try:
        import drawings as D
        files.append(D.multiview(stl, name="reshaped_" + want,
                                 title=want.upper(), notes="reshaped from the previous part"))
        steps.append(dict(tool="multiview", summary="dimensioned drawing of the new shape",
                          source="orthographic projection"))
    except Exception: pass
    remember(want, stl, label="reshaped_" + want)
    return dict(ok=True, steps=steps, files=files, view=view,
                answer=f"rebuilt as a {want}: {os.path.basename(stl)}",
                evidence="implicit geometry regenerated at the same overall size")

# ------------------------------------------------------------- teaching
TEACH = re.compile(r"(worksheet|practice|quiz|exercise|problem set|fill.?in|"
                   r"blank|study|revis|test me|teach me|homework)", re.I)

def do_teach(text, vals):
    """Fill-in-the-blank worksheets and self-marking problem sets."""
    import worksheet as W, course as C
    t = text.lower()
    # Word boundaries: bare "hard" fires on hardwood, hardness and case
    # hardening, so "worksheet on case hardening" would have been graded a
    # hard worksheet on the strength of the metallurgy.
    level = ("hard" if re.search(r"\bhard(er|est)?\b", t)
             else "easy" if re.search(r"\beas(y|ier|iest)\b", t) else "medium")
    steps, files = [], []
    wants_set = bool(re.search(r"(problem set|practice|quiz|exercise|test me|"
                               r"\d+\s*(problems|questions))", t))
    if wants_set:
        m = re.search(r"(\d+)\s*(problems|questions)", t)
        n = int(m.group(1)) if m else 6
        probs = C.build_set(min(max(n, 1), 12))
        steps.append(dict(tool="problem_set", source="randomised from the reference table",
                          summary=f"{len(probs)} problems: " +
                                  ", ".join(p["key"].replace("_", " ") for p in probs[:4])))
        p_ = C.render_set_html(probs)
        files.append(p_)
        steps.append(dict(tool="self_marking", source="2% tolerance",
                          summary="marks itself, explains wrong answers, tracks progress"))
        return dict(ok=True, steps=steps, files=files, view=p_,
                    answer=f"{len(probs)}-question problem set",
                    evidence="every question generated from a cited closed-form solution")
    # a single worksheet on whatever was asked
    q = re.sub(TEACH, "", text).strip(" ,.:-") or "cantilever tip deflection point load"
    v = dict(vals)
    if "E" not in v: v["E"] = DEFAULTS["E"]
    if "I" not in v: v["I"] = DEFAULTS["b"]*DEFAULTS["h"]**3/12
    if "L" not in v: v["L"] = 1.0
    if "P" not in v: v["P"] = 1000.0
    try:
        w = W.make(q, v, level)
    except Exception as e:
        return dict(ok=False, steps=[dict(tool="worksheet",
                    summary=f"no reference formula matched '{q[:40]}'", warn=True)],
                    error=str(e)[:120])
    steps.append(dict(tool="worksheet", source=w["ref"]["src"],
                      summary=f"{w['ref']['expr']} — level {level}, "
                              f"{sum(1 for s_ in w['steps'] if s_['blank'])} blank(s)"))
    h = W.render_html(w); files.append(h)
    steps.append(dict(tool="self_marking", source="2% tolerance",
                      summary="type an answer and press Enter; it marks itself"))
    try:
        files.append(W.render_pdf(w))
        steps.append(dict(tool="printable", source="PDF",
                          summary="worksheet + answer key on page 2"))
    except Exception: pass
    return dict(ok=True, steps=steps, files=files, view=h,
                answer=f"worksheet: {W.fmt(w['display'])} {w['display_unit']}",
                evidence=w["ref"]["src"] + " — student works it out, EngineAI marks it")

# ------------------------------------------------------------------ FEA
FEA = re.compile(r"(stress\s*analy[sz]|fea|finite element|von mises|"
                 r"analy[sz]e.*(stress|strength)|is it strong|will it (hold|survive|break))",
                 re.I)

def do_fea(text, vals):
    """Solid-element stress analysis of a generated part."""
    import sdf_fea as F
    t = text.lower()
    which = ("lattice" if re.search(r"lattice|gyroid", t)
             else "bracket" if re.search(r"bracket|organic|blend", t) else "beam")
    P = vals.get("P", 1000.0); E = vals.get("E", DEFAULTS["E"])
    steps = [dict(tool="hex_mesh", source="straight from the distance field",
                  summary=f"{which}: meshing the implicit geometry into C3D8I solid elements")]
    r = F.analyse(which, P=P, E=E, verbose=False)
    steps.append(dict(tool="mesh", source="structured hex",
                      summary=f"{r['nodes']} nodes, {r['elems']} elements"))
    if not r.get("ok"):
        steps.append(dict(tool="calculix", summary=r.get("note", "solver unavailable"), warn=True))
        return dict(ok=True, steps=steps, files=[r["deck"]], view=None,
                    answer="mesh and CalculiX deck written; solver did not run",
                    evidence="install CalculiX: sudo apt install calculix-ccx")
    steps.append(dict(tool="calculix", source="C3D8I static",
                      summary=f"tip {r['tip_deflection']*1000:.4g} mm, "
                              f"peak von Mises {r['von_mises_max']/1e6:.4g} MPa"))
    if "error_pct" in r:
        steps.append(dict(tool="verify", source="closed form",
                          summary=f"vs P*L^3/(3*E*I) = {r['closed_form']*1000:.4g} mm "
                                  f"-> {r['error_pct']:.2f}% error",
                          warn=r["error_pct"] > 8))
    files = [r["deck"]] + ([r["view"]] if r.get("view") else [])
    return dict(ok=True, steps=steps, files=files, view=r.get("view"),
                answer=f"peak von Mises {r['von_mises_max']/1e6:.4g} MPa, "
                       f"deflection {r['tip_deflection']*1000:.4g} mm",
                evidence="CalculiX C3D8I on a mesh built directly from the distance field")

# ------------------------------------------------------------------ route
PRONOUN = re.compile(r"\b(it|this|that|these|those|the object|the part|the same)\b", re.I)

def route(text):
    """Return a deterministic result, or None to let the model try."""
    vals = _q(text)
    t = text.lower()
    if FEA.search(t):      return do_fea(text, vals)
    if TEACH.search(t):    return do_teach(text, vals)
    if RESHAPE.search(t):  return do_reshape(text, vals)
    if DRAWING.search(t): return do_drawings(text, vals)
    # a bare follow-up about the last thing built
    if _load_last().get("stl") and PRONOUN.search(t) and len(t.split()) < 14 and \
       re.search(r"(view|3d|show|render|cad|stl|step|geometry|sandbox)", t):
        return do_drawings(text, vals)
    if CHAMBER.search(t): return do_chamber(text, vals)
    if LATTICE.search(t): return do_lattice(text, vals)
    if BEAM.search(t):    return do_beam(text, vals)
    if wants_geometry(t): return do_cad(text, vals)
    return None

def selftest():
    ok = True
    print("\nDIRECT ROUTING SELFTEST   (no model, no API key)")
    cases = [("tip deflection of a 1.2 m steel cantilever under 2.5 kN", True),
             ("tip deflection of a 3 m steel cantilever under 2.5 kN and produce a cad", True),
             ("buckling load of a 2 m steel column", True),
             ("design a cooled chamber at 4 MW/m2", True),
             ("gyroid lattice block 30 mm", True),
             ("make a cad model", True),
             ("what is the capital of France", False)]
    for q, want in cases:
        r = route(q)
        got = r is not None and r.get("ok")
        files = len(r.get("files", [])) if r else 0
        view = bool(r and r.get("view"))
        flag = "ok  " if bool(got) == want else "FAIL"
        if bool(got) != want: ok = False
        print(f"  {flag} {q[:46]:48s} files={files:2d} view={'yes' if view else 'no ':3s} "
              f"{(r.get('answer','')[:34] if r else 'handed to the model')}")
        if want and r:
            if not r.get("files"): print("       FAIL no files produced"); ok = False
            for f_ in r["files"]:
                if not os.path.exists(f_): print(f"       FAIL missing {f_}"); ok = False
    # the numbers must still be right
    r = route("tip deflection of a 1.2 m steel cantilever under 2.5 kN")
    I = DEFAULTS["b"]*DEFAULTS["h"]**3/12
    exact = 2500*1.2**3/(3*200e9*I)
    got = float(re.findall(r"[\d.]+", r["answer"])[0])
    good = abs(got - exact*1000)/(exact*1000) < 0.01
    print(f"  {'ok  ' if good else 'FAIL'} value {got:.4g} mm vs closed form {exact*1000:.4g} mm")
    ok &= good
    print("\ndirect selftest:", "PASSED" if ok else "FAILED")
    return ok

if __name__ == "__main__":
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("question", nargs="*"); ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    r = route(" ".join(a.question))
    if not r: sys.exit("  no deterministic route — this one needs the model")
    print()
    for s in r["steps"]:
        print(f"  {s['tool']:18s} {s['summary'][:88]}")
        if s.get("source"): print(f"  {'':18s}   [{s['source']}]")
    print(f"\n  ANSWER   {r['answer']}\n  EVIDENCE {r['evidence']}")
    print(f"\n  {len(r['files'])} file(s):")
    for f_ in r["files"]: print(f"    {f_}")
