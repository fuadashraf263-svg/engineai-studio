#!/usr/bin/env python3
"""
cfd_validate.py - give OpenFOAM the same treatment that made your FEA trustworthy.

Builds a 2D laminar plane-channel case from scratch, runs it, extracts the
fully-developed velocity profile and compares it against the exact analytical
solution:            u(y) = (3/2) * Ub * (1 - (y/h)^2)
which is the textbook plane-Poiseuille result (White, Fluid Mechanics ch.6;
Sachdeva ch.7). Reports max and RMS error, and PASS/FAIL.

This does NOT use the planarPoiseuille tutorial - that case is viscoelastic and
was never the right benchmark.

  python cfd_validate.py --build          write the case, do not run
  python cfd_validate.py                  build, run, compare, verdict
  python cfd_validate.py --selftest       check the maths and parser (no OpenFOAM)
"""
import os, re, sys, math, json, shutil, subprocess, argparse, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CASE = os.path.abspath(os.path.join(HERE, "work", "cfd_poiseuille"))
OUT = os.path.abspath(os.environ.get("ENGINEAI_OUT", os.path.join(HERE, "out")))
H, LX, UB, NU = 0.005, 0.20, 0.10, 1.0e-4      # half-gap, length, bulk vel, kinematic visc
NX, NY, TOL = 200, 60, 5.0                      # cells, and pass threshold in %

def head(cls, obj, loc=None):
    l = f'    location    "{loc}";\n' if loc else ""
    return ("FoamFile\n{\n    format      ascii;\n"
            f"    class       {cls};\n{l}    object      {obj};\n}}\n")

def bash_path(path):
    """Convert local paths to something bash on Windows/WSL can `cd` into."""
    p = os.path.normpath(path)
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
    if not m:
        return p.replace("\\", "/")
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"

def _safe_case(path):
    """Use a space-free case directory for the OpenFOAM run if needed.

    OpenFOAM's fileName parser can reject Windows paths with spaces. Keep the
    generated artifacts in a space-free temporary directory, then report the
    evidence path as the canonical absolute case location.
    """
    p = os.path.abspath(path)
    if " " not in p:
        return p
    return os.path.join(tempfile.gettempdir(), "engineai_cfd_poiseuille")

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("FoamFile\n" if False else text)

def clean_case(case=CASE, keep_zero=True):
    """Old runs leave time directories and a polyMesh from a DIFFERENT mesh.
    Reading those is how you get '8000 cells, expected 12000'."""
    import shutil as _sh
    if not os.path.isdir(case): return 0
    n = 0
    for _v, d in time_dirs(case):
        if keep_zero and d in ("0", "0.0"): continue
        _sh.rmtree(os.path.join(case, d), ignore_errors=True); n += 1
    pm = os.path.join(case, "constant", "polyMesh")
    if os.path.isdir(pm): _sh.rmtree(pm, ignore_errors=True)
    if n: print(f"  cleaned {n} stale time director(ies) and the old mesh")
    return n

def build(case=CASE, clean=True):
    if clean: clean_case(case)
    for d in ("system", "constant", "0"):
        os.makedirs(os.path.join(case, d), exist_ok=True)
    v = [(0,-H,0),(LX,-H,0),(LX,H,0),(0,H,0),(0,-H,0.001),(LX,-H,0.001),(LX,H,0.001),(0,H,0.001)]
    verts = "\n".join(f"    ({x} {y} {z})" for x, y, z in v)
    write(f"{case}/system/blockMeshDict", head("dictionary","blockMeshDict","system") +
f"""convertToMeters 1;
vertices
(
{verts}
);
blocks ( hex (0 1 2 3 4 5 6 7) ({NX} {NY} 1) simpleGrading (1 1 1) );
edges ();
boundary
(
    inlet  {{ type patch; faces ( (0 4 7 3) ); }}
    outlet {{ type patch; faces ( (1 2 6 5) ); }}
    walls  {{ type wall;  faces ( (0 1 5 4) (3 7 6 2) ); }}
    frontAndBack {{ type empty; faces ( (0 3 2 1) (4 5 6 7) ); }}
);
""")
    write(f"{case}/system/controlDict", head("dictionary","controlDict","system") +
"""application     simpleFoam;
startFrom       startTime;   startTime 0;
stopAt          endTime;     endTime   2000;
deltaT          1;           writeControl timeStep;  writeInterval 2000;
purgeWrite      0;  writeFormat ascii;  writePrecision 8;
runTimeModifiable true;
""")
    write(f"{case}/system/fvSchemes", head("dictionary","fvSchemes","system") +
"""ddtSchemes      { default steadyState; }
gradSchemes     { default Gauss linear; }
divSchemes      { default none; div(phi,U) bounded Gauss linearUpwind grad(U);
                  div((nuEff*dev2(T(grad(U))))) Gauss linear; }
laplacianSchemes{ default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes   { default corrected; }
""")
    write(f"{case}/system/fvSolution", head("dictionary","fvSolution","system") +
"""solvers
{
    p { solver GAMG; tolerance 1e-08; relTol 0.01; smoother GaussSeidel; }
    U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-08; relTol 0.1; }
}
SIMPLE { nNonOrthogonalCorrectors 0; consistent yes;
         residualControl { p 1e-6; U 1e-6; } }
relaxationFactors { equations { U 0.9; p 0.9; } }
""")
    props = head("dictionary","physicalProperties","constant") + \
            f"viscosityModel  constant;\nnu              {NU};\n"
    write(f"{case}/constant/physicalProperties", props)      # OpenFOAM 11+
    write(f"{case}/constant/transportProperties", props)     # older releases
    write(f"{case}/constant/momentumTransport", head("dictionary","momentumTransport","constant") +
          "simulationType  laminar;\nlaminar { model Stokes; }\n")
    write(f"{case}/0/U", head("volVectorField","U","0") +
f"""dimensions      [0 1 -1 0 0 0 0];
internalField   uniform ({UB} 0 0);
boundaryField
{{
    inlet   {{ type fixedValue; value uniform ({UB} 0 0); }}
    outlet  {{ type zeroGradient; }}
    walls   {{ type noSlip; }}
    frontAndBack {{ type empty; }}
}}
""")
    write(f"{case}/0/p", head("volScalarField","p","0") +
"""dimensions      [0 2 -2 0 0 0 0];
internalField   uniform 0;
boundaryField
{
    inlet   { type zeroGradient; }
    outlet  { type fixedValue; value uniform 0; }
    walls   { type zeroGradient; }
    frontAndBack { type empty; }
}
""")
    re_num = UB*(4*H)/NU
    print(f"  case written: {case}")
    print(f"  gap {2*H*1000:g} mm, length {LX*1000:g} mm, Ub {UB} m/s, nu {NU} m2/s")
    print(f"  Re = Ub*Dh/nu = {re_num:.0f}  (laminar, entrance length ~{0.05*re_num*4*H*1000:.0f} mm"
          f" of {LX*1000:g} mm)")
    return case

def run(case=CASE):
    env = os.environ.copy()
    bashrc = "/opt/openfoam13/etc/bashrc"
    case_bash = bash_path(case)
    def sh(cmd):
        full = f'source {bashrc} 2>/dev/null; cd "{case_bash}" && {cmd}'
        return subprocess.run(["bash", "-lc", full], capture_output=True, text=True, timeout=1800)
    print("  blockMesh ...", end="", flush=True)
    r = sh("blockMesh")
    if r.returncode: print(" FAILED\n" + r.stdout[-1200:] + r.stderr[-600:]); return False
    print(" ok")
    for solver in ("simpleFoam", "foamRun -solver incompressibleFluid"):
        print(f"  {solver.split()[0]} ...", end="", flush=True)
        r = sh(solver)
        if r.returncode == 0:
            print(" ok")
            for pp in ("postProcess -func writeCellCentres -latestTime",
                       "foamPostProcess -func writeCellCentres -latestTime"):
                if sh(pp).returncode == 0: break
            return True
        print(" failed, trying next")
    print(r.stdout[-1500:] + r.stderr[-600:])
    return False

def time_dirs(case=CASE):
    out = []
    for d in os.listdir(case):
        try: out.append((float(d), d))
        except ValueError: pass
    return sorted(out)

def latest_time(case=CASE):
    ts = [(v, d) for v, d in time_dirs(case) if v > 0]
    return ts[-1][1] if ts else None

def _open_field(case, t, name="U"):
    """OpenFOAM may write U, U.gz, or binary. Return the text, or None."""
    import gzip
    for fn, op in ((f"{name}", open), (f"{name}.gz", gzip.open)):
        p = os.path.join(case, t, fn)
        if os.path.exists(p):
            try:
                return op(p, "rt", errors="ignore").read()
            except Exception:
                return None
    return None

def parse_vectors(txt):
    """Pull the internalField vector list without relying on one rigid regex."""
    i = txt.find("internalField")
    if i < 0: return None
    seg = txt[i:]
    if "uniform" in seg[:60] and "nonuniform" not in seg[:60]:
        m = re.search(r"uniform\s*\(([^)]*)\)", seg)
        return None if not m else "UNIFORM"
    j = seg.find("(")                       # first bracket after the count
    if j < 0: return None
    depth, k = 0, j
    while k < len(seg):                     # walk to the matching close bracket
        if seg[k] == "(": depth += 1
        elif seg[k] == ")":
            depth -= 1
            if depth == 0: break
        k += 1
    body = seg[j+1:k]
    vals = re.findall(r"\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)", body)
    return [float(a) for a, b, c in vals] or None

def _scalar_field(case, t, name):
    txt = _open_field(case, t, name)
    if txt is None: return None
    i = txt.find("internalField")
    if i < 0 or "nonuniform" not in txt[i:i+60]: return None
    seg = txt[i:]; j = seg.find("(")
    depth, k = 0, j
    while k < len(seg):
        if seg[k] == "(": depth += 1
        elif seg[k] == ")":
            depth -= 1
            if depth == 0: break
        k += 1
    return [float(v) for v in re.findall(r"[-\d][\d.eE+-]*", seg[j+1:k])]

def cell_centres(case, t):
    """Ccx/Ccy written by writeCellCentres, or the vector field C."""
    cx, cy = _scalar_field(case, t, "Ccx"), _scalar_field(case, t, "Ccy")
    if cx and cy and len(cx) == len(cy): return cx, cy
    txt = _open_field(case, t, "C")
    if txt:
        i = txt.find("internalField"); seg = txt[i:]; j = seg.find("(")
        depth, k = 0, j
        while k < len(seg):
            if seg[k] == "(": depth += 1
            elif seg[k] == ")":
                depth -= 1
                if depth == 0: break
            k += 1
        v = re.findall(r"\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)", seg[j+1:k])
        if v: return [float(a) for a, b, c in v], [float(b) for a, b, c in v]
    return None, None

def read_profile(case=CASE, nx=NX, ny=NY, verbose=False):
    t = latest_time(case)
    if not t:
        if verbose: print(f"  no written time directory in {case}")
        return None, None
    txt = _open_field(case, t, "U")
    if txt is None:
        if verbose: print(f"  no readable U in {case}/{t}: {sorted(os.listdir(os.path.join(case,t)))}")
        return None, None
    ux = parse_vectors(txt)
    if ux == "UNIFORM":
        if verbose: print("  U is still uniform - the solver did not develop a profile")
        return None, None
    if not ux:
        if verbose:
            i = txt.find("internalField"); print("  unparseable:", repr(txt[i:i+200]))
        return None, None

    # PREFERRED: use the real cell coordinates - no assumption about cell ordering
    cx, cy = cell_centres(case, t)
    if cx and len(cx) == len(ux):
        xmax = max(cx)
        band = [(y, u) for x, y, u in zip(cx, cy, ux) if x > xmax - (LX/nx)*0.6]
        if len(band) >= 8:
            # more than one cell column can fall in the band; average by y so the
            # spacing is uniform. Duplicated y values make dy zero and the bulk
            # integral collapses to nothing.
            from collections import defaultdict
            acc = defaultdict(list)
            for y, u in band: acc[round(y, 9)].append(u)
            ys = sorted(acc)
            prof = [sum(acc[y])/len(acc[y]) for y in ys]
            if verbose:
                print(f"  profile from cell centres at x={xmax:.4f} m "
                      f"({len(ys)} distinct y, {len(band)} cells)")
            return ys, prof

    # FALLBACK: index arithmetic, inferring nx from the actual cell count
    n = len(ux)
    if n % ny == 0:
        nx_eff = n // ny
        if verbose and nx_eff != nx: print(f"  inferred nx={nx_eff} from {n} cells")
        prof = [ux[(nx_eff-1) + nx_eff*j] for j in range(ny)]
        dy = 2*H/ny
        return [-H + dy*(j+0.5) for j in range(ny)], prof
    if verbose:
        print(f"  {n} cells does not divide by ny={ny}, and no cell centres were written.")
        print("  Rebuild cleanly:  python cfd_validate.py   (it now wipes stale runs first)")
    return None, None

def diagnose(case=CASE):
    print(f"\n  case: {case}")
    if not os.path.isdir(case): print("  case directory does not exist"); return
    print("  time directories:", [d for _, d in time_dirs(case)] or "none")
    t = latest_time(case)
    if not t: print("  nothing written - did the solver converge and exit cleanly?"); return
    d = os.path.join(case, t)
    print(f"  latest time {t} contains: {sorted(os.listdir(d))}")
    txt = _open_field(case, t, "U")
    if txt is None: print("  U unreadable (binary writeFormat? set writeFormat ascii)"); return
    i = txt.find("internalField")
    print("  internalField header:", repr(txt[i:i+90]))
    ux = parse_vectors(txt)
    print("  parsed cell count:", "uniform field" if ux == "UNIFORM" else (len(ux) if ux else 0))
    print(f"  expected NX*NY = {NX*NY}")

def analytic(y, ub=UB, h=H):
    return 1.5*ub*(1.0 - (y/h)**2)

def compare(ys, prof, quiet=False):
    """Standard CFD validation metrics.

    Pointwise RELATIVE error is meaningless next to a no-slip wall, where the
    exact solution goes to zero: a 0.0003 m/s discrepancy becomes '14%'. Real
    validation normalises by a reference velocity and checks integral quantities.
    """
    umax_n, umax_a = max(prof), 1.5*UB
    err_c = abs(umax_n - umax_a)/umax_a*100                       # centreline
    n = len(ys)
    dy = (ys[-1]-ys[0])/(n-1) if n > 1 else 2*H/max(n, 1)
    if dy <= 0: dy = 2*H/max(n, 1)
    # trapezoid over the cell centres, then scale to the full gap: the first and
    # last centres sit half a cell in from the walls where u = 0
    ub_n = (sum(prof)*dy + 0.5*dy*(prof[0]+prof[-1]))/(2*H)
    err_b = abs(ub_n - UB)/UB*100
    norm = [abs(u - analytic(y))/umax_a*100 for y, u in zip(ys, prof)]
    mx_n, rms_n = max(norm), math.sqrt(sum(e*e for e in norm)/len(norm))
    core = [(y, u) for y, u in zip(ys, prof) if abs(y) < 0.8*H]   # away from the walls
    err_core = max(abs(u-analytic(y))/analytic(y)*100 for y, u in core) if core else float("nan")
    if not quiet:
        print("\n  y [mm]     OpenFOAM      analytic    err/u_max")
        for i in range(0, len(ys), max(1, len(ys)//12)):
            ua = analytic(ys[i])
            print(f"  {ys[i]*1000:7.2f}  {prof[i]:11.6f}  {ua:11.6f}  {abs(prof[i]-ua)/umax_a*100:7.2f}%")
        print(f"\n  centreline    {umax_n:.6f} vs {umax_a:.6f} m/s        {err_c:6.2f}%   (limit 2%)")
        print(f"  u_max/Ub      {umax_n/UB:.4f} vs 1.5000")
        print(f"  bulk velocity {ub_n:.6f} vs {UB:.6f} m/s        {err_b:6.2f}%   (limit 2%)  mass conservation")
        print(f"  profile RMS   normalised by u_max                {rms_n:6.2f}%   (limit 2%)")
        print(f"  profile max   normalised by u_max                {mx_n:6.2f}%   (limit 5%)")
        print(f"  core |y|<0.8h relative error                     {err_core:6.2f}%   (limit 3%)")
    checks = [("centreline", err_c, 2.0), ("bulk/mass", err_b, 2.0),
              ("RMS norm", rms_n, 2.0), ("max norm", mx_n, 5.0), ("core rel", err_core, 3.0)]
    bad = [c for c in checks if not (c[1] <= c[2])]
    if not quiet:
        for n, v, lim in checks:
            print(f"    {'PASS' if v <= lim else 'FAIL'}  {n:12s} {v:6.2f}% <= {lim}%")
        print("\n  CFD VALIDATION:", "PASSED" if not bad else
              "FAILED on " + ", ".join(c[0] for c in bad))
        if not bad:
            print("  OpenFOAM reproduces the exact Poiseuille solution on this case.")
    return (not bad), dict(centreline_pct=err_c, bulk_pct=err_b, rms_pct=rms_n,
                           max_pct=mx_n, core_pct=err_core, umax=umax_n, ub=ub_n)

def refine_study(case=CASE, levels=(20, 40, 80)):
    """Mesh convergence: the real proof that the discretisation, not luck, is right."""
    global NY, NX
    ny0, rows = NY, []
    for ny in levels:
        NY = ny
        print(f"\n--- {NX} x {ny} cells ---")
        build(case); 
        if not run(case): print("  run failed"); continue
        ys, prof = read_profile(case, ny=ny, verbose=True)
        if not prof: continue
        ok, m = compare(ys, prof, quiet=True)
        rows.append((ny, m["centreline_pct"], m["rms_pct"], m["bulk_pct"]))
        print(f"  centreline {m['centreline_pct']:.3f}%   RMS {m['rms_pct']:.3f}%   bulk {m['bulk_pct']:.3f}%")
    NY = ny0
    # A printed table disappears with the terminal. Keep a compact,
    # machine-readable evidence record so checkup.py and later reports can
    # distinguish an executed convergence study from an aspirational claim.
    evidence = {
        "benchmark": "laminar plane Poiseuille flow",
        "solver": "OpenFOAM simpleFoam",
        "case": os.path.abspath(case),
        "geometry": {"half_gap_m": H, "length_m": LX},
        "fluid": {"bulk_velocity_m_s": UB, "kinematic_viscosity_m2_s": NU},
        "mesh_levels": [
            {"cells_across_gap": ny, "centreline_error_pct": c,
             "rms_error_pct": r, "bulk_error_pct": b}
            for ny, c, r, b in rows
        ],
        "centreline_error_decreases": len(rows) > 1 and rows[-1][1] < rows[0][1],
    }
    out = OUT
    os.makedirs(out, exist_ok=True)
    report = os.path.join(out, "cfd_mesh_convergence.json")
    with open(report, "w", encoding="utf-8") as f:
        json.dump(evidence, f, indent=2)
    print(f"  evidence: {report}")
    if len(rows) > 1:
        print("\n  MESH CONVERGENCE")
        print("  cells across gap   centreline err   RMS err   bulk err")
        for ny, c, r, b in rows: print(f"  {ny:14d} {c:14.3f}% {r:9.3f}% {b:9.3f}%")
        if rows[-1][1] < rows[0][1]:
            print("  error falls as the mesh refines - the discretisation is converging.")
    return rows

def selftest():
    """Verify the analytical model and the parser without needing OpenFOAM."""
    print("\nCFD SELFTEST (no OpenFOAM needed)")
    ok = True
    # 1. analytical sanity
    a = abs(analytic(0) - 1.5*UB) < 1e-12
    b = abs(analytic(H)) < 1e-12
    q = sum(analytic(-H + 2*H*(i+0.5)/2000)*(2*H/2000) for i in range(2000))/(2*H)
    c = abs(q - UB)/UB < 1e-3
    print(f"  centreline = 1.5*Ub          {'ok' if a else 'FAIL'}")
    print(f"  zero at the wall             {'ok' if b else 'FAIL'}")
    print(f"  profile integrates to Ub     {'ok' if c else 'FAIL'}  (got {q:.6f}, want {UB})")
    ok &= a and b and c
    # 2. parser round-trip against a synthetic OpenFOAM field
    tmp = "/tmp/_cfd_selftest"
    shutil.rmtree(tmp, ignore_errors=True); os.makedirs(f"{tmp}/2000", exist_ok=True)
    dy = 2*H/NY
    cells = []
    for j in range(NY):
        y = -H + dy*(j+0.5)
        for i in range(NX): cells.append(f"({analytic(y):.8f} 0 0)")
    open(f"{tmp}/2000/U", "w").write(
        "internalField   nonuniform List<vector>\n%d\n(\n%s\n)\n;\n" % (len(cells), "\n".join(cells)))
    ys, prof = read_profile(tmp, verbose=True)
    if not prof:
        print("  parser                       FAIL (returned nothing)"); ok = False
    else:
        d = max(abs(p - analytic(y)) for y, p in zip(ys, prof))
        print(f"  parser recovers the profile  {'ok' if d < 1e-6 else 'FAIL'}  (max diff {d:.2e})")
        ok &= d < 1e-6
        print("\n  and the comparison report it would print:")
        compare(ys, prof)
    shutil.rmtree(tmp, ignore_errors=True)
    print("\ncfd selftest:", "PASSED" if ok else "FAILED")
    return ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true"); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--case", default=CASE)
    ap.add_argument("--diagnose", action="store_true")
    ap.add_argument("--norun", action="store_true", help="parse an existing run")
    ap.add_argument("--clean", action="store_true", help="wipe stale time dirs and exit")
    ap.add_argument("--refine", action="store_true", help="mesh convergence study")
    a = ap.parse_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    if a.diagnose: return diagnose(a.case)
    if a.clean: clean_case(a.case); return
    if a.refine:
        case = _safe_case(a.case)
        return refine_study(case) and None
    print("\nCFD VALIDATION - plane Poiseuille flow vs the exact solution")
    case = _safe_case(a.case)
    if not a.norun: build(case)
    if a.build: print("  --build only; run without it to solve and compare."); return
    if not shutil.which("blockMesh") and not os.path.exists("/opt/openfoam13/etc/bashrc"):
        sys.exit("  OpenFOAM not found. source /opt/openfoam13/etc/bashrc first.")
    if not a.norun and not run(case): sys.exit("  solver did not complete")
    ys, prof = read_profile(case, verbose=True)
    if not prof:
        diagnose(a.case); sys.exit("  could not read the velocity field - see above")
    ok, _m = compare(ys, prof)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
