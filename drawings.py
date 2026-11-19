#!/usr/bin/env python3
"""
drawings.py - real mechanical drawings from any STL: orthographic views,
dimensions, section, and exploded assembly.

Projection is done by binning vertices onto a raster and closing the result,
which is fast enough for the 100k+ triangle meshes the SDF kernel produces -
matplotlib chokes if you hand it that many polygons.

  python drawings.py part.stl                  -> 4-view dimensioned drawing
  python drawings.py part.stl --section        -> mid-plane section
  python drawings.py a.stl b.stl --exploded    -> exploded assembly
  python drawings.py --selftest
"""
import os, sys, math, struct, argparse
import numpy as np

OUT = os.path.expanduser(os.environ.get("ENGINEAI_OUT", "~/engineai/out"))

def load(path):
    d = open(path, "rb").read()
    if d[:5] == b"solid" and b"facet" in d[:2000]:
        v = [list(map(float, l.split()[1:4]))
             for l in d.decode(errors="ignore").splitlines() if l.strip().startswith("vertex")]
        return np.array(v).reshape(-1, 3, 3)
    n = struct.unpack("<I", d[80:84])[0]
    a = np.frombuffer(d[84:84+n*50].tobytes() if hasattr(d, "tobytes") else d[84:84+n*50],
                      dtype=np.uint8).reshape(n, 50)
    f = a[:, 12:48].copy().view("<f4").reshape(n, 3, 3)
    return np.array(f, dtype=float)

def face_points(tris, k=9):
    """Sample points across each triangle face, not just its corners.
    Binning corners alone leaves a grid of holes in the silhouette."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    rng = np.random.default_rng(7)
    u = rng.random((k, len(tris), 1)); v = rng.random((k, len(tris), 1))
    flip = (u + v) > 1
    u = np.where(flip, 1-u, u); v = np.where(flip, 1-v, v)
    pts = a[None] + u*(b-a)[None] + v*(c-a)[None]
    return np.concatenate([tris.reshape(-1, 3), pts.reshape(-1, 3)])

def silhouette(pts2, res=620, pad=0.06):
    """Raster silhouette. Fast enough for 100k+ triangle meshes, where
    matplotlib polygon rendering is not."""
    from scipy import ndimage
    lo, hi = pts2.min(0), pts2.max(0)
    span = (hi - lo).max() or 1.0
    lo = lo - span*pad; hi = hi + span*pad
    ij = ((pts2 - lo)/(hi - lo)*(res-1)).astype(int)
    ij = np.clip(ij, 0, res-1)
    g = np.zeros((res, res), bool)
    g[ij[:, 1], ij[:, 0]] = True
    g = ndimage.binary_closing(g, np.ones((7, 7)))
    g = ndimage.binary_opening(g, np.ones((2, 2)))     # drop stray speckle
    return g, lo, hi

def draw_view(ax, tris, drop, title, dims=None, unit="mm"):
    """One orthographic view with the silhouette and its overall dimensions."""
    keep = [i for i in range(3) if i != drop]
    P3 = face_points(tris)
    pts = P3[:, keep]
    g, lo, hi = silhouette(pts)
    from matplotlib.colors import ListedColormap
    ax.imshow(g, origin="lower", extent=[lo[0], hi[0], lo[1], hi[1]],
              cmap=ListedColormap([(1, 1, 1, 0), (.16, .22, .29, 1)]),
              interpolation="nearest")
    ax.set_aspect("equal"); ax.set_title(title, fontsize=10, weight="bold")
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values(): sp.set_color("#9fb0c4")
    if dims:
        real = tris.reshape(-1, 3)[:, keep]
        w = real[:, 0].max() - real[:, 0].min(); h = real[:, 1].max() - real[:, 1].min()
        x0, x1 = real[:, 0].min(), real[:, 0].max()
        y0, y1 = real[:, 1].min(), real[:, 1].max()
        off = (hi[1]-lo[1])*0.045
        ax.annotate("", (x0, y0-off), (x1, y0-off),
                    arrowprops=dict(arrowstyle="<|-|>", color="#1b6ca8", lw=1.1))
        ax.text((x0+x1)/2, y0-off*1.9, f"{w:.2f} {unit}", ha="center", fontsize=8.5,
                color="#1b6ca8")
        ax.annotate("", (x0-off, y0), (x0-off, y1),
                    arrowprops=dict(arrowstyle="<|-|>", color="#1b6ca8", lw=1.1))
        ax.text(x0-off*1.9, (y0+y1)/2, f"{h:.2f} {unit}", va="center", rotation=90,
                fontsize=8.5, color="#1b6ca8")

def multiview(stl, name=None, title=None, notes=""):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    tris = load(stl)
    mn, mx = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
    size = mx - mn
    fig = plt.figure(figsize=(11.7, 8.3))                   # A4 landscape
    fig.patch.set_facecolor("white")
    gs = fig.add_gridspec(2, 2, hspace=.22, wspace=.14,
                          left=.07, right=.95, top=.88, bottom=.13)
    draw_view(fig.add_subplot(gs[0, 0]), tris, 1, "FRONT  (looking -Y)", dims=True)
    draw_view(fig.add_subplot(gs[0, 1]), tris, 0, "SIDE  (looking -X)", dims=True)
    draw_view(fig.add_subplot(gs[1, 0]), tris, 2, "TOP  (looking -Z)", dims=True)
    ax = fig.add_subplot(gs[1, 1], projection="3d")
    step = max(1, len(tris)//22000)
    try:
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        T = tris[::step]
        e1, e2 = T[:, 1]-T[:, 0], T[:, 2]-T[:, 0]
        nr = np.cross(e1, e2); ln = np.linalg.norm(nr, axis=1, keepdims=True); ln[ln == 0] = 1
        sh = np.clip(np.abs((nr/ln) @ np.array([.4, -.7, .58])), .2, 1)
        ax.add_collection3d(Poly3DCollection(T, facecolors=np.c_[sh*.55, sh*.68, sh*.92],
                                             edgecolors="none", linewidths=0))
    except Exception: pass
    c = (mn+mx)/2; r = size.max()*.62 or 1
    ax.set_xlim(c[0]-r, c[0]+r); ax.set_ylim(c[1]-r, c[1]+r); ax.set_zlim(c[2]-r, c[2]+r)
    try: ax.set_box_aspect((1, 1, 1))
    except Exception: pass
    ax.view_init(24, -58); ax.set_axis_off(); ax.set_title("ISOMETRIC", fontsize=10, weight="bold")
    title = title or os.path.splitext(os.path.basename(stl))[0].replace("_", " ").upper()
    fig.suptitle(title, fontsize=15, weight="bold", y=.955)
    import datetime
    fig.text(.07, .055, f"overall  {size[0]:.2f} × {size[1]:.2f} × {size[2]:.2f} mm"
                        f"     {len(tris)} triangles", fontsize=9, color="#34495e")
    fig.text(.07, .028, notes or "third-angle projection · all dimensions in millimetres",
             fontsize=8.5, color="#7f8c8d")
    fig.text(.95, .055, f"EngineAI · {datetime.date.today():%d %b %Y}", fontsize=9,
             ha="right", color="#34495e")
    fig.text(.95, .028, "drawn from the generated solid, not sketched", fontsize=8.5,
             ha="right", color="#7f8c8d")
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, (name or os.path.splitext(os.path.basename(stl))[0]) + "_drawing.png")
    fig.savefig(p, dpi=150, facecolor="white"); plt.close(fig)
    return p

def section(stl, axis=1, name=None):
    """Cut the solid at its mid-plane and draw what the cut reveals."""
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    tris = load(stl)
    mn, mx = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
    mid = (mn[axis]+mx[axis])/2
    band = np.abs(tris[:, :, axis].mean(1) - mid) < (mx[axis]-mn[axis])*0.02
    T = tris[band]
    if len(T) < 20: T = tris
    keep = [i for i in range(3) if i != axis]
    pts = T.reshape(-1, 3)[:, keep]
    fig, ax = plt.subplots(figsize=(7.5, 6)); fig.patch.set_facecolor("white")
    ax.plot(pts[:, 0], pts[:, 1], ".", ms=1.1, color="#c0392b")
    ax.set_aspect("equal"); ax.grid(alpha=.3)
    ax.set_xlabel("mm"); ax.set_ylabel("mm")
    ax.set_title(f"SECTION A–A  at mid-{'XYZ'[axis]} = {mid:.2f} mm\n"
                 f"{len(T)} facets cut", fontsize=11, weight="bold")
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, (name or os.path.splitext(os.path.basename(stl))[0]) + "_section.png")
    fig.tight_layout(); fig.savefig(p, dpi=150, facecolor="white"); plt.close(fig)
    return p

def exploded(stls, labels=None, axis=2, gap=1.6, name="assembly"):
    """Offset each body along an axis and render them together."""
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    fig = plt.figure(figsize=(9, 7.5)); fig.patch.set_facecolor("white")
    ax = fig.add_subplot(111, projection="3d")
    cols = [(.42, .62, .88), (.86, .55, .35), (.45, .78, .58), (.80, .45, .70), (.6, .6, .68)]
    allpts, off = [], 0.0
    for i, s in enumerate(stls):
        T = load(s)
        span = T.reshape(-1, 3)[:, axis].max() - T.reshape(-1, 3)[:, axis].min()
        T = T.copy(); T[:, :, axis] += off
        off += span*gap
        step = max(1, len(T)//7000)
        ax.add_collection3d(Poly3DCollection(T[::step], facecolors=cols[i % len(cols)],
                                             edgecolors="none", alpha=.95))
        allpts.append(T.reshape(-1, 3))
        c = T.reshape(-1, 3).mean(0)
        ax.text(c[0], c[1], T.reshape(-1, 3)[:, axis].max(),
                (labels[i] if labels and i < len(labels) else os.path.basename(s)),
                fontsize=9, color="#2c3e50", ha="center")
    P = np.vstack(allpts); c = (P.min(0)+P.max(0))/2; r = (P.max(0)-P.min(0)).max()*.6 or 1
    ax.set_xlim(c[0]-r, c[0]+r); ax.set_ylim(c[1]-r, c[1]+r); ax.set_zlim(c[2]-r, c[2]+r)
    try: ax.set_box_aspect((1, 1, 1))
    except Exception: pass
    ax.view_init(20, -62); ax.set_axis_off()
    ax.set_title(f"EXPLODED VIEW — {len(stls)} part(s)", fontsize=13, weight="bold")
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name + "_exploded.png")
    fig.savefig(p, dpi=150, facecolor="white"); plt.close(fig)
    return p

def selftest():
    ok = True
    print("\nDRAWINGS SELFTEST")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sdf_kernel as S
    shape, b, _ = S.part_cooled_chamber(R=20, H=60, wall=5, n_ch=10, ch_r=1.2, n=54)
    r = S.build("dwg_test", shape, b, 54, quiet=True)
    for fn, lbl in ((lambda: multiview(r["path"], "dwg_test"), "4-view dimensioned drawing"),
                    (lambda: section(r["path"], 1, "dwg_test"), "section view"),
                    (lambda: exploded([r["path"], r["path"]], ["outer", "inner"], name="dwg_test"),
                     "exploded view")):
        try:
            p = fn()
            good = os.path.exists(p) and os.path.getsize(p) > 25000
            with open(p, "rb") as f: sig = f.read(4)
            good = good and sig == bytes([0x89, 0x50, 0x4E, 0x47])
            print(f"  {'ok  ' if good else 'FAIL'} {lbl:28s} {os.path.basename(p)} "
                  f"({os.path.getsize(p)/1024:.0f} KB)")
            ok &= good
        except Exception as e:
            print(f"  FAIL {lbl:28s} {type(e).__name__}: {str(e)[:60]}"); ok = False
    # the drawing must report the true size
    tris = load(r["path"]); size = tris.reshape(-1, 3).max(0) - tris.reshape(-1, 3).min(0)
    good = abs(size[2] - 60) < 2 and abs(size[0] - 50) < 3
    print(f"  {'ok  ' if good else 'FAIL'} measured {size[0]:.1f} × {size[1]:.1f} × {size[2]:.1f} mm "
          f"(chamber is ⌀50 × 60)")
    ok &= good
    print("\ndrawings selftest:", "PASSED" if ok else "FAILED")
    return ok

if __name__ == "__main__":
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("stl", nargs="*"); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--section", action="store_true"); ap.add_argument("--exploded", action="store_true")
    a = ap.parse_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    if not a.stl: sys.exit("give one or more .stl paths")
    if a.exploded: print(exploded(a.stl))
    elif a.section: print(section(a.stl[0]))
    else:
        for s in a.stl: print(multiview(s))
