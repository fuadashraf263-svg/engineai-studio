#!/usr/bin/env python3
"""
render_image.py - generate a real image of a part. No GPU, no diffusion model.

When you asked EngineAI to "generate an image", it explained why it couldn't.
This is the answer: it renders YOUR geometry - shaded, from four angles, with
real dimensions - which is what an engineering image actually is.

  python render_image.py part.stl                 -> part_render.png
  python render_image.py part.stl --views iso     -> single large view
  python render_image.py --selftest
"""
import os, sys, math, struct, argparse

OUT = os.path.expanduser(os.environ.get("ENGINEAI_OUT", "~/engineai/out"))

def load_stl(path):
    d = open(path, "rb").read()
    if d[:5] == b"solid" and b"facet" in d[:2000]:                    # ascii
        vs = [tuple(map(float, l.split()[1:4]))
              for l in d.decode(errors="ignore").splitlines() if l.strip().startswith("vertex")]
        return [tuple(vs[i:i+3]) for i in range(0, len(vs) - 2, 3)]
    n = struct.unpack("<I", d[80:84])[0]; tris = []; o = 84
    for _ in range(n):
        v = struct.unpack("<12f", d[o:o+48]); o += 50
        tris.append(tuple(tuple(v[3+3*k:6+3*k]) for k in range(3)))
    return tris

def render(stl, out=None, views=("iso", "front", "side", "top"), colour="#7fb3ff", dpi=150):
    import numpy as np, matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    tris = load_stl(stl)
    if not tris: raise ValueError("no triangles in " + stl)
    T = np.array(tris)                                              # (n,3,3)
    mn, mx = T.reshape(-1, 3).min(0), T.reshape(-1, 3).max(0)
    ctr, span = (mn + mx) / 2, float((mx - mn).max()) or 1.0
    ANG = {"iso": (24, -58), "front": (0, -90), "side": (0, 0), "top": (89, -90)}
    views = [v for v in views if v in ANG] or ["iso"]
    cols = 2 if len(views) > 1 else 1
    rows = math.ceil(len(views) / cols)
    fig = plt.figure(figsize=(6.2*cols, 5.0*rows))
    # flat shading from a fixed light, so faces read as solid rather than wireframe
    light = np.array([0.45, -0.7, 0.55]); light /= np.linalg.norm(light)
    e1, e2 = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
    nrm = np.cross(e1, e2)
    ln = np.linalg.norm(nrm, axis=1, keepdims=True); ln[ln == 0] = 1
    shade = np.clip(np.abs((nrm/ln) @ light), 0.18, 1.0)
    base = np.array(matplotlib.colors.to_rgb(colour))
    facecols = np.clip(base[None, :]*(0.35 + 0.65*shade[:, None]), 0, 1)
    for i, v in enumerate(views, 1):
        ax = fig.add_subplot(rows, cols, i, projection="3d")
        pc = Poly3DCollection(T, facecolors=facecols, edgecolors=(0, 0, 0, 0.22), linewidths=0.25)
        ax.add_collection3d(pc)
        for lo, hi, s in ((mn, mx, "x"),):
            pass
        ax.set_xlim(ctr[0]-span*.6, ctr[0]+span*.6)
        ax.set_ylim(ctr[1]-span*.6, ctr[1]+span*.6)
        ax.set_zlim(ctr[2]-span*.6, ctr[2]+span*.6)
        try: ax.set_box_aspect((1, 1, 1))
        except Exception: pass
        el, az = ANG[v]; ax.view_init(elev=el, azim=az)
        ax.set_title(v, fontsize=11, weight="bold")
        ax.grid(False); ax.set_axis_off()
    size = mx - mn
    fig.suptitle(f"{os.path.basename(stl)}   bounding box "
                 f"{size[0]:.4g} x {size[1]:.4g} x {size[2]:.4g}   ({len(tris)} triangles)",
                 fontsize=11)
    os.makedirs(OUT, exist_ok=True)
    out = out or os.path.join(OUT, os.path.splitext(os.path.basename(stl))[0] + "_render.png")
    fig.tight_layout(); fig.savefig(out, dpi=dpi, facecolor="white"); plt.close(fig)
    return out

def selftest():
    import subprocess, tempfile
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import generators as G
    stl = G.REGISTRY["stl_part"]()
    p = render(stl)
    ok = os.path.exists(p) and os.path.getsize(p) > 20000
    with open(p, "rb") as f: sig = f.read(8)
    good = sig[:4] == bytes([0x89, 0x50, 0x4E, 0x47])
    print("  rendered %s  %.1f KB  PNG header %s"
          % (os.path.basename(p), os.path.getsize(p)/1024, "ok" if good else "BAD"))
    ok = ok and good
    print("render selftest:", "PASSED" if ok else "FAILED")
    return ok

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stl", nargs="?"); ap.add_argument("--views", default="iso,front,side,top")
    ap.add_argument("--out"); ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    if not a.stl: sys.exit("give an .stl path, or --selftest")
    print(render(a.stl, a.out, tuple(v.strip() for v in a.views.split(","))))
