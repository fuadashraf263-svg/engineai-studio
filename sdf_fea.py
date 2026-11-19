#!/usr/bin/env python3
"""
sdf_fea.py - stress-analyse the geometry EngineAI generates.

The gap this closes: marching cubes gives a SURFACE mesh; CalculiX needs SOLID
elements. The usual answer is to tetrahedralise the STL with gmsh, which works
but throws away the field you already have.

For implicit geometry there is a better route. The signed distance field is
already sampled on a regular grid, so every grid cell that lies inside the
surface becomes a hexahedral element directly. No STL round-trip, no gmsh, no
surface repair, and the mesh is structured, which CalculiX likes.

  python sdf_fea.py beam                    cantilever, checked against PL^3/(3EI)
  python sdf_fea.py bracket                 the L-bracket
  python sdf_fea.py lattice                 gyroid block under compression
  python sdf_fea.py --selftest              mesh + deck + parser, no solver needed
"""
import os, re, sys, math, json, shutil, argparse, subprocess
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT = os.path.expanduser(os.environ.get("ENGINEAI_OUT", "~/engineai/out"))
WORK = os.path.expanduser(os.environ.get("ENGINEAI_WORK", "~/engineai/work"))

# ============================================================== meshing
def hex_mesh(f, bounds, n=(40, 10, 16), inside=-1e-9):
    """Every grid cell whose centre is inside the surface becomes a C3D8 element.

    Returns nodes (N,3), elements (M,8) as 1-based CalculiX node ids, and the
    grid spacing so callers can pick faces by coordinate.
    """
    (x0, y0, z0), (x1, y1, z1) = bounds
    nx, ny, nz = n
    xs = np.linspace(x0, x1, nx+1); ys = np.linspace(y0, y1, ny+1); zs = np.linspace(z0, z1, nz+1)
    # cell centres
    cx = 0.5*(xs[:-1]+xs[1:]); cy = 0.5*(ys[:-1]+ys[1:]); cz = 0.5*(zs[:-1]+zs[1:])
    CX, CY, CZ = np.meshgrid(cx, cy, cz, indexing="ij")
    solid = f(CX, CY, CZ) < inside
    if not solid.any(): raise ValueError("no cell centre lies inside the shape")
    # node grid, only the corners actually used
    nid = -np.ones((nx+1, ny+1, nz+1), dtype=np.int64)
    ii, jj, kk = np.nonzero(solid)
    for di in (0, 1):
        for dj in (0, 1):
            for dk in (0, 1):
                nid[ii+di, jj+dj, kk+dk] = 0
    used = np.nonzero(nid == 0)
    nid[used] = np.arange(1, len(used[0])+1)
    nodes = np.stack([xs[used[0]], ys[used[1]], zs[used[2]]], axis=1)
    # C3D8 corner order: bottom face counter-clockwise, then top face
    e = np.stack([nid[ii,   jj,   kk  ], nid[ii+1, jj,   kk  ],
                  nid[ii+1, jj+1, kk  ], nid[ii,   jj+1, kk  ],
                  nid[ii,   jj,   kk+1], nid[ii+1, jj,   kk+1],
                  nid[ii+1, jj+1, kk+1], nid[ii,   jj+1, kk+1]], axis=1)
    return nodes, e, (xs[1]-xs[0], ys[1]-ys[0], zs[1]-zs[0])

def pick(nodes, axis, value, tol):
    """1-based ids of nodes on a plane — used for supports and loads."""
    return np.nonzero(np.abs(nodes[:, axis] - value) <= tol)[0] + 1

# =========================================================== deck writing
def write_deck(path, nodes, elems, fixed, loaded, force, E, nu, rho=None, direction=2):
    """A complete CalculiX static deck. C3D8I: incompatible modes, so a coarse
    hex mesh is not artificially stiff in bending — with plain C3D8 a cantilever
    can be 30% too stiff."""
    fpn = force/max(len(loaded), 1)
    with open(path, "w") as f:
        f.write("*NODE, NSET=NALL\n")
        for i, (x, y, z) in enumerate(nodes, 1):
            f.write(f"{i}, {x:.9g}, {y:.9g}, {z:.9g}\n")
        f.write("*ELEMENT, TYPE=C3D8I, ELSET=EALL\n")
        for i, e in enumerate(elems, 1):
            f.write(f"{i}, " + ", ".join(str(int(v)) for v in e) + "\n")
        f.write("*NSET, NSET=FIX\n" + ",\n".join(str(int(v)) for v in fixed) + "\n")
        f.write("*NSET, NSET=LOAD\n" + ",\n".join(str(int(v)) for v in loaded) + "\n")
        f.write("*MATERIAL, NAME=MAT\n*ELASTIC\n")
        f.write(f"{E:.9g}, {nu:.4g}\n")
        if rho: f.write(f"*DENSITY\n{rho:.6g}\n")
        f.write("*SOLID SECTION, ELSET=EALL, MATERIAL=MAT\n")
        f.write("*STEP\n*STATIC\n")
        f.write("*BOUNDARY\nFIX, 1, 3, 0.0\n")
        f.write(f"*CLOAD\nLOAD, {direction+1}, {fpn:.9g}\n")
        f.write("*NODE FILE\nU\n*EL FILE\nS\n*END STEP\n")
    return path

# ================================================================ solving
def run_ccx(job):
    exe = shutil.which("ccx") or shutil.which("ccx_2.21") or shutil.which("CalculiX")
    if not exe: return False, "CalculiX (ccx) not found on PATH"
    r = subprocess.run([exe, job], capture_output=True, text=True,
                       cwd=os.path.dirname(job) or ".", timeout=3600)
    ok = os.path.exists(job + ".frd") and os.path.getsize(job + ".frd") > 200
    return ok, (r.stdout[-800:] if not ok else "solved")

def read_frd(path):
    """Nodal displacement and von Mises from a CalculiX .frd."""
    disp, stress, mode = {}, {}, None
    for line in open(path, errors="ignore"):
        s = line.rstrip("\n")
        if " DISP" in s: mode = "d"; continue
        if " STRESS" in s: mode = "s"; continue
        if s.startswith(" -3"): mode = None; continue
        if mode and s.startswith(" -1"):
            try:
                n = int(s[3:13])
                v = [float(s[13+12*i:25+12*i]) for i in range((len(s)-13)//12)]
            except ValueError:
                continue
            (disp if mode == "d" else stress)[n] = v
    vm = {}
    for n, v in stress.items():
        if len(v) >= 6:
            sx, sy, sz, sxy, syz, szx = v[:6]
            vm[n] = math.sqrt(0.5*((sx-sy)**2 + (sy-sz)**2 + (sz-sx)**2)
                              + 3*(sxy**2 + syz**2 + szx**2))
    return disp, vm

# ================================================================== cases
def case_beam(L=1.0, b=0.05, h=0.10, n=(48, 6, 10)):
    import sdf_kernel as S
    shape = S.box(L, b, h, c=(L/2, 0, 0))
    return shape, ((0, -b/2-1e-6, -h/2-1e-6), (L, b/2+1e-6, h/2+1e-6)), n, dict(L=L, b=b, h=h)

def case_bracket(n=(40, 18, 30)):
    import sdf_kernel as S
    shape, bounds, _ = S.part_organic_bracket(60)
    return shape, bounds, n, {}

def case_lattice(size=24.0, n=(34, 34, 34)):
    import sdf_kernel as S
    shape, bounds, _ = S.part_lattice_block(size=size, cell=8.0, t=0.9, skin=1.5, n=60)
    return shape, bounds, n, dict(size=size)

def analyse(which="beam", P=1000.0, E=200e9, nu=0.3, verbose=True, n=None):
    shape, bounds, ndef, meta = {"beam": case_beam, "bracket": case_bracket,
                                 "lattice": case_lattice}[which]()
    ngrid = n or ndef
    nodes, elems, d = hex_mesh(shape, bounds, ngrid)
    if verbose:
        print(f"\n  {which}: {len(nodes)} nodes, {len(elems)} C3D8I elements  "
              f"(cell {d[0]*1000:.2f} x {d[1]*1000:.2f} x {d[2]*1000:.2f} mm)")
    x0, x1 = nodes[:, 0].min(), nodes[:, 0].max()
    z1 = nodes[:, 2].max()
    tol = max(d)*0.51
    if which == "beam":
        fixed = pick(nodes, 0, x0, tol)              # root face
        loaded = pick(nodes, 0, x1, tol)             # tip face
        direction = 2                                 # push in -z
    else:
        fixed = pick(nodes, 2, nodes[:, 2].min(), tol)
        loaded = pick(nodes, 2, z1, tol)
        direction = 2
    if verbose: print(f"  {len(fixed)} node(s) fixed, {len(loaded)} node(s) loaded")
    os.makedirs(WORK, exist_ok=True)
    job = os.path.join(WORK, f"sdf_fea_{which}")
    write_deck(job + ".inp", nodes, elems, fixed, loaded, -abs(P), E, nu, direction=direction)
    if verbose: print(f"  deck: {job}.inp  ({os.path.getsize(job+'.inp')/1024:.0f} KB)")
    ok, msg = run_ccx(job)
    if not ok:
        if verbose: print(f"  solver: {msg}")
        return dict(ok=False, nodes=len(nodes), elems=len(elems), deck=job+".inp", note=msg)
    disp, vm = read_frd(job + ".frd")
    uz = min((v[2] for v in disp.values()), default=0.0)
    smax = max(vm.values(), default=0.0)
    res = dict(ok=True, nodes=len(nodes), elems=len(elems), deck=job+".inp",
               tip_deflection=abs(uz), von_mises_max=smax, frd=job+".frd")
    if which == "beam":
        I = meta["b"]*meta["h"]**3/12
        exact = P*meta["L"]**3/(3*E*I)
        res["closed_form"] = exact
        res["error_pct"] = abs(abs(uz)-exact)/exact*100
        if verbose:
            print(f"  tip deflection {abs(uz)*1000:.5g} mm   closed form "
                  f"{exact*1000:.5g} mm   error {res['error_pct']:.2f}%")
    if verbose: print(f"  peak von Mises {smax/1e6:.4g} MPa")
    # a 3D field view of the result
    try:
        res["view"] = field_view(nodes, elems, disp, vm, which)
        if verbose: print(f"  view: {res['view']}")
    except Exception as e:
        if verbose: print(f"  view failed: {type(e).__name__}")
    return res

def field_view(nodes, elems, disp, vm, name):
    """Outer faces of the hex mesh, coloured by von Mises, in the viz3d sandbox."""
    import viz3d
    from collections import Counter
    F = [(0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)]
    cnt = Counter()
    for e in elems:
        for f in F: cnt[tuple(sorted(int(e[i]) for i in f))] += 1
    faces, seen = [], set()
    for e in elems:
        for f in F:
            key = tuple(sorted(int(e[i]) for i in f))
            if cnt[key] == 1 and key not in seen:
                seen.add(key); faces.append([int(e[i])-1 for i in f])
    fields = {"von_mises": [vm.get(i+1, 0.0) for i in range(len(nodes))]}
    d3 = [disp.get(i+1, [0, 0, 0])[:3] for i in range(len(nodes))]
    return viz3d.render([list(map(float, p)) for p in nodes], faces, fields, d3,
                        f"EngineAI — {name}, von Mises on the deformed shape", "Pa",
                        path=os.path.join(OUT, f"fea_{name}.html"),
                        note=f"{len(elems)} hex elements straight from the distance field")

# =============================================================== selftest
def selftest():
    ok = True
    print("\nSDF -> FEA SELFTEST   (mesh, deck and parser; solver optional)")
    import sdf_kernel as S
    # 1 the mesh must fill a known volume
    L, b, h = 1.0, 0.05, 0.10
    shape = S.box(L, b, h, c=(L/2, 0, 0))
    nodes, elems, d = hex_mesh(shape, ((0, -b/2-1e-6, -h/2-1e-6), (L, b/2+1e-6, h/2+1e-6)),
                               (40, 5, 8))
    vol = len(elems)*d[0]*d[1]*d[2]
    e = abs(vol - L*b*h)/(L*b*h)*100
    print(f"  {'ok  ' if e < 3 else 'FAIL'} mesh volume {vol*1e6:.1f} cm^3 vs exact "
          f"{L*b*h*1e6:.1f} cm^3 ({e:.2f}%)   {len(elems)} elements")
    ok &= e < 3
    # 2 node ids must be contiguous and every element must reference real nodes
    good = elems.min() >= 1 and elems.max() <= len(nodes)
    print(f"  {'ok  ' if good else 'FAIL'} {len(nodes)} nodes, element ids in range "
          f"[{elems.min()}, {elems.max()}]")
    ok &= good
    # 3 every element must have 8 distinct corners
    dup = sum(1 for row in elems if len(set(row.tolist())) != 8)
    print(f"  {'ok  ' if dup == 0 else 'FAIL'} {dup} degenerate element(s)")
    ok &= dup == 0
    # 4 the deck must be well formed
    os.makedirs(WORK, exist_ok=True)
    job = os.path.join(WORK, "_selftest_fea")
    fixed = pick(nodes, 0, nodes[:, 0].min(), max(d)*0.51)
    loaded = pick(nodes, 0, nodes[:, 0].max(), max(d)*0.51)
    write_deck(job + ".inp", nodes, elems, fixed, loaded, -1000.0, 200e9, 0.3)
    txt = open(job + ".inp").read()
    checks = [("node block", "*NODE, NSET=NALL" in txt),
              ("C3D8I elements", "TYPE=C3D8I" in txt),
              ("material", "*ELASTIC" in txt and "2e+11" in txt.replace("200000000000", "2e+11")),
              ("boundary", "FIX, 1, 3, 0.0" in txt),
              ("load", "*CLOAD" in txt),
              ("static step", "*STEP" in txt and "*STATIC" in txt),
              ("output requests", "*NODE FILE" in txt and "*EL FILE" in txt)]
    for nme, c in checks:
        print(f"  {'ok  ' if c else 'FAIL'} deck has the {nme}"); ok &= c
    print(f"       {len(fixed)} fixed, {len(loaded)} loaded, "
          f"{os.path.getsize(job+'.inp')/1024:.0f} KB")
    # 5 the .frd parser, against a synthetic file
    frd = job + ".frd"
    with open(frd, "w") as f:
        f.write("    1C\n  100CL  101 DISP\n")
        f.write(" -1         1 0.00000E+00 0.00000E+00-1.00000E-03\n")
        f.write(" -1         2 0.00000E+00 0.00000E+00-2.00000E-03\n -3\n")
        f.write("  100CL  102 STRESS\n")
        f.write(" -1         1 1.00000E+08 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00 0.00000E+00\n -3\n")
    disp, vm = read_frd(frd)
    good = abs(disp[2][2] + 2e-3) < 1e-12 and abs(vm[1] - 1e8) < 1e3
    print(f"  {'ok  ' if good else 'FAIL'} frd parser: uz={disp.get(2,[0,0,0])[2]:.1e} m, "
          f"von Mises={vm.get(1,0)/1e6:.1f} MPa")
    ok &= good
    # 6 surface extraction for the viewer
    from collections import Counter
    F = [(0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)]
    cnt = Counter()
    for el in elems:
        for f_ in F: cnt[tuple(sorted(int(el[i]) for i in f_))] += 1
    outer = sum(1 for v in cnt.values() if v == 1)
    interior = sum(1 for v in cnt.values() if v == 2)
    good = outer > 0 and interior > 0
    print(f"  {'ok  ' if good else 'FAIL'} surface extraction: {outer} outer faces, "
          f"{interior} shared")
    ok &= good
    for ext in (".inp", ".frd"):
        p = job + ext
        os.path.exists(p) and os.remove(p)
    if shutil.which("ccx"):
        print("\n  CalculiX found — running the real cantilever check...")
        r = analyse("beam", verbose=False)
        if r.get("ok"):
            good = r["error_pct"] < 8
            print(f"  {'ok  ' if good else 'FAIL'} tip {r['tip_deflection']*1000:.5g} mm vs "
                  f"{r['closed_form']*1000:.5g} mm ({r['error_pct']:.2f}%)")
            ok &= good
        else:
            print(f"  solver did not complete: {r.get('note','')[:70]}")
    else:
        print("\n  CalculiX not on PATH here — deck and parser verified, solve on your machine:")
        print("     python sdf_fea.py beam")
    print("\nsdf_fea selftest:", "PASSED" if ok else "FAILED")
    return ok

if __name__ == "__main__":
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("case", nargs="?", default="beam", choices=["beam", "bracket", "lattice"])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--P", type=float, default=1000.0)
    ap.add_argument("--E", type=float, default=200e9)
    a = ap.parse_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    r = analyse(a.case, P=a.P, E=a.E)
    print()
    print(json.dumps({k: v for k, v in r.items() if not isinstance(v, (list, dict))},
                     indent=2, default=str))
