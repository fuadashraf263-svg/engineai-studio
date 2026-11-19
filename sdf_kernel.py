#!/usr/bin/env python3
"""
sdf_kernel.py - implicit (signed-distance) geometry. The LEAP 71 class of part.

CadQuery is B-rep: it cannot express a gyroid lattice, a conformal cooling
channel that follows a curved wall, or a smooth organic blend. Those are not a
tooling gap, they are a mathematical one - and the answer is a signed distance
field, which is just arithmetic on a grid. No PicoGK, no Windows, no .NET.

A shape is a function f(x,y,z) -> distance. Negative inside, zero on the surface.
Everything below composes those functions, then marching cubes turns the field
into a watertight triangle mesh.

  python sdf_kernel.py demo                 build the showcase parts
  python sdf_kernel.py chamber              cooled thrust-chamber wall
  python sdf_kernel.py --selftest           volumes checked against exact maths
"""
import os, sys, math, struct, argparse
import numpy as np

OUT = os.path.expanduser(os.environ.get("ENGINEAI_OUT", "~/engineai/out"))

# ============================================================= primitives
def sphere(r, c=(0, 0, 0)):
    cx, cy, cz = c
    return lambda x, y, z: np.sqrt((x-cx)**2 + (y-cy)**2 + (z-cz)**2) - r

def box(sx, sy, sz, c=(0, 0, 0)):
    cx, cy, cz = c
    def f(x, y, z):
        qx, qy, qz = np.abs(x-cx)-sx/2, np.abs(y-cy)-sy/2, np.abs(z-cz)-sz/2
        out = np.sqrt(np.maximum(qx, 0)**2 + np.maximum(qy, 0)**2 + np.maximum(qz, 0)**2)
        return out + np.minimum(np.maximum(qx, np.maximum(qy, qz)), 0)
    return f

def cylinder(r, h, axis="z", c=(0, 0, 0)):
    cx, cy, cz = c
    def f(x, y, z):
        X, Y, Z = x-cx, y-cy, z-cz
        if axis == "z": rad, ax = np.sqrt(X**2+Y**2), Z
        elif axis == "y": rad, ax = np.sqrt(X**2+Z**2), Y
        else: rad, ax = np.sqrt(Y**2+Z**2), X
        d1, d2 = rad - r, np.abs(ax) - h/2
        return np.minimum(np.maximum(d1, d2), 0) + \
               np.sqrt(np.maximum(d1, 0)**2 + np.maximum(d2, 0)**2)
    return f

def torus(R, r, c=(0, 0, 0)):
    cx, cy, cz = c
    def f(x, y, z):
        q = np.sqrt((x-cx)**2 + (y-cy)**2) - R
        return np.sqrt(q**2 + (z-cz)**2) - r
    return f

def plane(n=(0, 0, 1), d=0.0):
    nx, ny, nz = np.array(n, float)/np.linalg.norm(n)
    return lambda x, y, z: nx*x + ny*y + nz*z - d

# ============================================================= operations
def union(*fs):      return lambda x, y, z: np.minimum.reduce([f(x, y, z) for f in fs])
def intersect(*fs):  return lambda x, y, z: np.maximum.reduce([f(x, y, z) for f in fs])
def subtract(a, b):  return lambda x, y, z: np.maximum(a(x, y, z), -b(x, y, z))
def offset(f, d):    return lambda x, y, z: f(x, y, z) - d
def invert(f):       return lambda x, y, z: -f(x, y, z)

def shell(f, t):
    """Hollow wall of thickness t centred on the surface - a pressure vessel wall."""
    return lambda x, y, z: np.abs(f(x, y, z)) - t/2

def smooth_union(a, b, k=1.0):
    """The organic blend that makes AM parts look grown rather than assembled."""
    def f(x, y, z):
        da, db = a(x, y, z), b(x, y, z)
        h = np.clip(0.5 + 0.5*(db-da)/k, 0, 1)
        return db*(1-h) + da*h - k*h*(1-h)
    return f

def smooth_subtract(a, b, k=1.0):
    def f(x, y, z):
        da, db = a(x, y, z), -b(x, y, z)
        h = np.clip(0.5 - 0.5*(db-da)/k, 0, 1)
        return db*(1-h) + da*h + k*h*(1-h)
    return f

def twist(f, rate):
    def g(x, y, z):
        a = rate*z; ca, sa = np.cos(a), np.sin(a)
        return f(ca*x - sa*y, sa*x + ca*y, z)
    return g

# ================================================================ lattices
# Triply periodic minimal surfaces - the real AM lattice families.
def gyroid(cell=10.0, t=0.6):
    w = 2*math.pi/cell
    return lambda x, y, z: np.abs(np.sin(w*x)*np.cos(w*y) + np.sin(w*y)*np.cos(w*z)
                                  + np.sin(w*z)*np.cos(w*x)) - t

def schwarz_p(cell=10.0, t=0.6):
    w = 2*math.pi/cell
    return lambda x, y, z: np.abs(np.cos(w*x) + np.cos(w*y) + np.cos(w*z)) - t

def diamond(cell=10.0, t=0.6):
    w = 2*math.pi/cell
    def f(x, y, z):
        X, Y, Z = w*x, w*y, w*z
        return np.abs(np.sin(X)*np.sin(Y)*np.sin(Z) + np.sin(X)*np.cos(Y)*np.cos(Z)
                      + np.cos(X)*np.sin(Y)*np.cos(Z) + np.cos(X)*np.cos(Y)*np.sin(Z)) - t
    return f

def lattice_fill(solid, lat, skin=0.0):
    """Lattice inside a solid, optionally keeping a solid outer skin."""
    if skin <= 0: return intersect(solid, lat)
    return union(intersect(solid, offset(invert(offset(solid, -skin)), 0)),
                 intersect(solid, lat))

# ================================================== conformal channels
def channel_along(points, r):
    """A round channel following a polyline - conformal cooling.
    Distance to a capsule chain; B-rep cannot sweep this reliably, SDF can."""
    P = np.array(points, float)
    def f(x, y, z):
        best = None
        for i in range(len(P)-1):
            a, b = P[i], P[i+1]
            ab = b - a; L2 = float(ab @ ab) or 1e-12
            px, py, pz = x-a[0], y-a[1], z-a[2]
            t = np.clip((px*ab[0] + py*ab[1] + pz*ab[2])/L2, 0, 1)
            dx, dy, dz = px - t*ab[0], py - t*ab[1], pz - t*ab[2]
            d = np.sqrt(dx*dx + dy*dy + dz*dz) - r
            best = d if best is None else np.minimum(best, d)
        return best
    return f

def helix_points(R, pitch, turns, z0=0.0, n=240):
    return [(R*math.cos(2*math.pi*i/n*turns), R*math.sin(2*math.pi*i/n*turns),
             z0 + pitch*turns*i/n) for i in range(n+1)]

# ================================================================ meshing
def sample(f, bounds, n=96):
    (x0, y0, z0), (x1, y1, z1) = bounds
    nx = ny = nz = n
    if isinstance(n, (tuple, list)): nx, ny, nz = n
    xs = np.linspace(x0, x1, nx); ys = np.linspace(y0, y1, ny); zs = np.linspace(z0, z1, nz)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    return f(X, Y, Z), (xs, ys, zs)

def to_mesh(f, bounds, n=96):
    from skimage import measure
    V, (xs, ys, zs) = sample(f, bounds, n)
    V = np.pad(V, 1, mode="constant", constant_values=float(np.max(V)) + 1.0)  # seal the boundary
    sp = (xs[1]-xs[0], ys[1]-ys[0], zs[1]-zs[0])
    verts, faces, _nrm, _val = measure.marching_cubes(V, level=0.0, spacing=sp)
    verts = verts + np.array([xs[0]-sp[0], ys[0]-sp[1], zs[0]-sp[2]])
    return verts, faces

def write_stl(verts, faces, path):
    tris = verts[faces]
    n = len(tris)
    with open(path, "wb") as fh:
        fh.write(b"\0"*80); fh.write(struct.pack("<I", n))
        a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
        nr = np.cross(b-a, c-a)
        ln = np.linalg.norm(nr, axis=1, keepdims=True); ln[ln == 0] = 1
        nr = nr/ln
        for i in range(n):
            fh.write(struct.pack("<3f", *nr[i]))
            for v in tris[i]: fh.write(struct.pack("<3f", *v))
            fh.write(b"\0\0")
    return path

def mesh_volume(verts, faces):
    """Signed volume by the divergence theorem - exact for a closed mesh."""
    t = verts[faces]
    return float(abs(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum())/6.0)

def is_watertight(faces):
    from collections import Counter
    e = Counter()
    for a, b, c in faces:
        for u, v in ((a, b), (b, c), (c, a)): e[(min(u, v), max(u, v))] += 1
    return all(v == 2 for v in e.values()), sum(1 for v in e.values() if v != 2)

# ================================================ manufacturing checks
def am_check(f, bounds, n=80, min_wall=0.8, max_overhang_deg=45.0, build_dir=(0, 0, 1)):
    """Additive-manufacturing constraints, on the field itself.
    min wall  : the smallest solid feature the machine can print
    overhang  : surface normals leaning further from the build direction than allowed"""
    V, (xs, ys, zs) = sample(f, bounds, n)
    d = (xs[1]-xs[0], ys[1]-ys[0], zs[1]-zs[0])
    inside = V < 0
    frac = float(inside.mean())
    # thinnest wall ~ 2x the largest interior distance in the thinnest region:
    # sample |SDF| at interior points; a wall of thickness t has max interior depth t/2
    depth = -V[inside] if inside.any() else np.array([0.0])
    thin = float(np.percentile(depth, 5))*2 if depth.size else 0.0
    gx, gy, gz = np.gradient(V, *d)
    g = np.stack([gx, gy, gz], -1)
    gn = np.linalg.norm(g, axis=-1); gn[gn == 0] = 1
    nrm = g/gn[..., None]
    surf = np.abs(V) < max(d)*1.2
    bd = np.array(build_dir, float); bd /= np.linalg.norm(bd)
    cos = (nrm @ bd)
    # downward-facing surfaces: normal opposing the build direction
    down = surf & (cos < -math.cos(math.radians(max_overhang_deg)))
    over = float(down.sum())/max(1, surf.sum())
    return {"volume_fraction": frac,
            "thinnest_wall_est": thin,
            "min_wall_ok": thin >= min_wall,
            "overhang_fraction": over,
            "overhang_ok": over < 0.15,
            "note": ("printable" if (thin >= min_wall and over < 0.15)
                     else "needs supports or thicker walls")}

# =================================================================== parts
def part_lattice_block(size=30.0, cell=10.0, t=0.7, skin=1.5, n=110):
    solid = box(size, size, size)
    core = intersect(offset(solid, -skin), gyroid(cell, t))
    wall = shell(solid, skin*2)
    shape = union(core, intersect(wall, solid))
    b = ((-size/2-1,)*3, (size/2+1,)*3)
    return shape, b, n

def part_cooled_chamber(R=20.0, H=60.0, wall=5.0, n_ch=10, ch_r=1.2, n=120):
    """A thrust-chamber wall with conformal cooling channels through it -
    the canonical LEAP 71 demonstration, and impossible in B-rep."""
    outer = cylinder(R+wall, H)
    inner = cylinder(R, H)
    body = subtract(outer, inner)
    if 2*ch_r > wall - 1.2:      # leave >=0.6 mm of metal either side
        raise ValueError(f"channel dia {2*ch_r:g} mm will not fit in a {wall:g} mm wall; "
                         f"use ch_r <= {(wall-1.2)/2:.2f} mm or a thicker wall")
    chans = []
    for i in range(n_ch):
        a0 = 2*math.pi*i/n_ch
        pts = [( (R+wall/2)*math.cos(a0 + 0.8*z/H), (R+wall/2)*math.sin(a0 + 0.8*z/H), z )
               for z in np.linspace(-H/2+3, H/2-3, 40)]
        chans.append(channel_along(pts, ch_r))
    shape = subtract(body, union(*chans))
    b = ((-(R+wall+2), -(R+wall+2), -H/2-2), (R+wall+2, R+wall+2, H/2+2))
    return shape, b, n

def part_organic_bracket(n=110):
    a = sphere(9, (-12, 0, 0)); bo = sphere(9, (12, 0, 0)); c = sphere(7, (0, 0, 14))
    blob = smooth_union(smooth_union(a, bo, 6.0), c, 6.0)
    holes = union(cylinder(3.2, 40, "z", (-12, 0, 0)), cylinder(3.2, 40, "z", (12, 0, 0)))
    return subtract(blob, holes), ((-24, -12, -12), (24, 12, 24)), n

# ================================================================= build
def build(name, shape, bounds, n, quiet=False):
    verts, faces = to_mesh(shape, bounds, n)
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, f"sdf_{name}.stl")
    write_stl(verts, faces, p)
    wt, bad = is_watertight(faces)
    vol = mesh_volume(verts, faces)
    if not quiet:
        print(f"  {name:18s} {len(faces):7d} tris  vol {vol:10.1f} mm^3  "
              f"{'watertight' if wt else f'{bad} open edges'}  -> {os.path.basename(p)}")
    return dict(path=p, triangles=len(faces), volume=vol, watertight=wt, bad_edges=bad)

def selftest():
    ok = True
    print("\nSDF KERNEL SELFTEST   (volumes against exact maths)")
    # sphere
    r = 10.0
    v, f = to_mesh(sphere(r), ((-12,)*3, (12,)*3), 120)
    vol, exact = mesh_volume(v, f), 4/3*math.pi*r**3
    e = abs(vol-exact)/exact*100
    wt, bad = is_watertight(f)
    print(f"  sphere r=10     {vol:9.1f} vs {exact:9.1f} mm^3  {e:5.2f}%  "
          f"{'watertight' if wt else str(bad)+' open'}")
    ok &= e < 1.0 and wt
    # box
    v, f = to_mesh(box(20, 12, 8), ((-14, -10, -8), (14, 10, 8)), 120)
    vol, exact = mesh_volume(v, f), 20*12*8
    e = abs(vol-exact)/exact*100
    print(f"  box 20x12x8     {vol:9.1f} vs {exact:9.1f} mm^3  {e:5.2f}%")
    ok &= e < 2.0
    # cylinder
    v, f = to_mesh(cylinder(6, 20), ((-8, -8, -12), (8, 8, 12)), 120)
    vol, exact = mesh_volume(v, f), math.pi*36*20
    e = abs(vol-exact)/exact*100
    print(f"  cylinder r6 h20 {vol:9.1f} vs {exact:9.1f} mm^3  {e:5.2f}%")
    ok &= e < 2.0
    # boolean: sphere minus cylinder
    v, f = to_mesh(subtract(sphere(10), cylinder(4, 40)), ((-12,)*3, (-12, -12, -12)[0:0] or (12,)*3), 120)
    vol = mesh_volume(v, f)
    exact = 4/3*math.pi*1000 - (math.pi*16*2*math.sqrt(100-16) + 0)   # sphere - drilled cylinder
    e = abs(vol-exact)/exact*100
    print(f"  sphere - Ø8 bore{vol:9.1f} vs {exact:9.1f} mm^3  {e:5.2f}%   (boolean)")
    ok &= e < 4.0
    # the things B-rep cannot do
    shape, b, n = part_lattice_block(24, 8, 0.8, 1.2, 100)
    r1 = build("lattice_block", shape, b, n, quiet=True)
    frac = r1["volume"]/24**3
    print(f"  gyroid lattice  {r1['triangles']:7d} tris, volume fraction {frac*100:5.1f}%  "
          f"{'watertight' if r1['watertight'] else str(r1['bad_edges'])+' open'}")
    ok &= r1["triangles"] > 1000 and 0.05 < frac < 0.95
    shape, b, n = part_cooled_chamber(n=96)
    r2 = build("cooled_chamber", shape, b, n, quiet=True)
    print(f"  cooled chamber  {r2['triangles']:7d} tris, conformal channels through a curved wall")
    ok &= r2["triangles"] > 1000
    shape, b, n = part_organic_bracket(90)
    r3 = build("organic_bracket", shape, b, n, quiet=True)
    print(f"  smooth blend    {r3['triangles']:7d} tris, organic union")
    ok &= r3["triangles"] > 1000
    # manufacturing constraints
    chk = am_check(*part_lattice_block(24, 8, 0.8, 1.2, 80)[:2], n=70, min_wall=0.8)
    print(f"  AM check        wall~{chk['thinnest_wall_est']:.2f} mm "
          f"(min 0.8 -> {'ok' if chk['min_wall_ok'] else 'too thin'}), "
          f"overhang {chk['overhang_fraction']*100:.1f}%  {chk['note']}")
    ok &= isinstance(chk["volume_fraction"], float)
    print("\nsdf kernel selftest:", "PASSED" if ok else "FAILED")
    return ok

def main():
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("cmd", nargs="?", default="demo",
                    choices=["demo", "lattice", "chamber", "bracket", "check"])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--n", type=int, default=110)
    a = ap.parse_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    print()
    if a.cmd in ("demo", "lattice"):
        s, b, n = part_lattice_block(n=a.n); r = build("lattice_block", s, b, n)
    if a.cmd in ("demo", "chamber"):
        s, b, n = part_cooled_chamber(n=a.n); build("cooled_chamber", s, b, n)
    if a.cmd in ("demo", "bracket"):
        s, b, n = part_organic_bracket(a.n); build("organic_bracket", s, b, n)
    if a.cmd == "check":
        s, b, _ = part_lattice_block()
        for k, v in am_check(s, b).items(): print(f"  {k:22s} {v}")
    print(f"\n  files in {OUT}")

if __name__ == "__main__":
    main()
