#!/usr/bin/env python3
"""
generators.py - every EngineAI output type behind one interface.

  python generators.py list                 what exists
  python generators.py check                what is available on THIS machine
  python generators.py demo                 build one of everything into ~/engineai/out
  python generators.py run beam_diagram '{"P":1000,"L":1.0,"E":200e9,"I":8.333e-6}'

Each generator declares its dependencies and reports itself unavailable rather
than crashing. Nothing here needs a GPU or an internet connection.
"""
import os, sys, json, math, struct, base64, argparse, datetime

OUT = os.path.expanduser(os.environ.get("ENGINEAI_OUT", "~/engineai/out"))
REGISTRY = {}

def gen(name, cat, needs=(), doc=""):
    def deco(fn):
        fn.gen_name, fn.cat, fn.needs, fn.doc = name, cat, tuple(needs), doc
        REGISTRY[name] = fn
        return fn
    return deco

def have(mod):
    try: __import__(mod); return True
    except Exception: return False

def available(name):
    return all(have(m) for m in REGISTRY[name].needs)

def _out(fn):
    os.makedirs(OUT, exist_ok=True)
    return os.path.join(OUT, fn)

# ============================================================ VISUAL / 2D
@gen("beam_diagram", "visual", ["matplotlib", "numpy"],
     "Beam with supports, load, deflected shape and annotated maximum")
def beam_diagram(P=1000.0, L=1.0, E=200e9, I=8.333e-6, case="cantilever", **kw):
    import numpy as np, matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x = np.linspace(0, L, 240)
    if case == "cantilever":
        y = -P * x**2 * (3*L - x) / (6*E*I); dmax = P*L**3/(3*E*I); at = L
    else:                                     # simply supported, central load
        y = -P*x*(3*L**2 - 4*x**2)/(48*E*I)
        y = np.where(x <= L/2, y, np.interp(L-x, x, y)); dmax = P*L**3/(48*E*I); at = L/2
    sc = (0.15*L)/max(abs(y).max(), 1e-30)
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.plot([0, L], [0, 0], lw=6, color="#b0b7c3", solid_capstyle="butt", zorder=1)
    ax.plot(x, y*sc, lw=2.4, color="#c0392b", zorder=3, label="deflected shape")
    if case == "cantilever":
        ax.add_patch(plt.Rectangle((-0.03*L, -0.09*L), 0.03*L, 0.18*L, color="#34495e"))
        for i in range(6):
            yy = -0.09*L + i*0.036*L
            ax.plot([-0.03*L, -0.06*L], [yy, yy-0.02*L], color="#34495e", lw=1.2)
    else:
        for xs in (0, L):
            ax.plot([xs, xs-0.035*L, xs+0.035*L, xs], [0, -0.07*L, -0.07*L, 0], color="#34495e", lw=1.5)
    ax.annotate("", xy=(at, 0), xytext=(at, 0.30*L),
                arrowprops=dict(arrowstyle="-|>", lw=2.2, color="#2c3e50"))
    ax.text(at, 0.33*L, f"P = {P:g} N", ha="center", fontsize=10, weight="bold")
    ax.annotate(f"$\\delta_{{max}}$ = {dmax*1000:.4g} mm",
                xy=(at, y[-1 if case == 'cantilever' else len(x)//2]*sc),
                xytext=(0.55*L, -0.30*L), fontsize=10, color="#c0392b",
                arrowprops=dict(arrowstyle="->", color="#c0392b"))
    ax.set_xlim(-0.12*L, 1.12*L); ax.set_ylim(-0.42*L, 0.45*L)
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_title(f"{case.replace('_',' ').title()} beam - L={L:g} m, EI={E*I:.3g} N·m²", fontsize=11)
    p = _out(f"beam_{case}.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    return p

@gen("shear_moment", "visual", ["matplotlib", "numpy"], "Shear force and bending moment diagrams")
def shear_moment(P=1000.0, L=1.0, case="cantilever", **kw):
    import numpy as np, matplotlib
    matplotlib.use("Agg"); import matplotlib.pyplot as plt
    x = np.linspace(0, L, 200)
    if case == "cantilever": V = np.full_like(x, P); M = -P*(L-x)
    else:
        V = np.where(x < L/2, P/2, -P/2); M = np.where(x < L/2, P*x/2, P*(L-x)/2)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.4, 5), sharex=True)
    a1.fill_between(x, V, color="#3498db", alpha=.35); a1.plot(x, V, color="#2471a3", lw=2)
    a1.set_ylabel("V  [N]"); a1.grid(alpha=.3); a1.axhline(0, color="k", lw=.8)
    a1.set_title("Shear force"); 
    a2.fill_between(x, M, color="#e67e22", alpha=.35); a2.plot(x, M, color="#ba4a00", lw=2)
    a2.set_ylabel("M  [N·m]"); a2.set_xlabel("x  [m]"); a2.grid(alpha=.3); a2.axhline(0, color="k", lw=.8)
    a2.set_title(f"Bending moment   |M|max = {abs(M).max():.4g} N·m")
    p = _out(f"shear_moment_{case}.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    return p

@gen("stress_field", "visual", ["matplotlib", "numpy"], "Bending stress distribution over a section")
def stress_field(M=500.0, b=0.05, h=0.10, **kw):
    import numpy as np, matplotlib
    matplotlib.use("Agg"); import matplotlib.pyplot as plt
    I = b*h**3/12; y = np.linspace(-h/2, h/2, 120); s = M*y/I/1e6
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8, 4), gridspec_kw=dict(width_ratios=[1, 1.5]))
    a1.add_patch(plt.Rectangle((-b/2, -h/2), b, h, fc="#d6dbdf", ec="#2c3e50", lw=1.6))
    a1.set_xlim(-b, b); a1.set_ylim(-h*.75, h*.75); a1.set_aspect("equal")
    a1.set_title(f"section {b*1000:g}×{h*1000:g} mm"); a1.axis("off")
    a2.fill_betweenx(y*1000, 0, s, where=s >= 0, color="#c0392b", alpha=.45)
    a2.fill_betweenx(y*1000, 0, s, where=s < 0, color="#2980b9", alpha=.45)
    a2.plot(s, y*1000, color="#2c3e50", lw=2); a2.axvline(0, color="k", lw=.8)
    a2.set_xlabel("σ  [MPa]"); a2.set_ylabel("y  [mm]"); a2.grid(alpha=.3)
    a2.set_title(f"σ = My/I    σmax = ±{abs(s).max():.4g} MPa")
    p = _out("stress_field.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    return p

@gen("mohr_circle", "visual", ["matplotlib", "numpy"], "Mohr's circle for a 2D stress state")
def mohr_circle(sx=120e6, sy=40e6, txy=30e6, **kw):
    import numpy as np, matplotlib
    matplotlib.use("Agg"); import matplotlib.pyplot as plt
    c = (sx+sy)/2; r = math.hypot((sx-sy)/2, txy)
    th = np.linspace(0, 2*np.pi, 400)
    fig, ax = plt.subplots(figsize=(5.6, 5.2))
    ax.plot((c+r*np.cos(th))/1e6, r*np.sin(th)/1e6, color="#2c3e50", lw=2)
    ax.plot([sx/1e6, sy/1e6], [txy/1e6, -txy/1e6], "o-", color="#c0392b", lw=1.6)
    ax.plot([(c-r)/1e6, (c+r)/1e6], [0, 0], "s", color="#27ae60")
    ax.axhline(0, color="k", lw=.8); ax.axvline(0, color="k", lw=.8)
    ax.set_xlabel("σ  [MPa]"); ax.set_ylabel("τ  [MPa]"); ax.grid(alpha=.3); ax.set_aspect("equal")
    ax.set_title(f"σ1={(c+r)/1e6:.4g}  σ2={(c-r)/1e6:.4g}  τmax={r/1e6:.4g} MPa")
    p = _out("mohr_circle.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    return p

# ============================================================ CAD / 3D
def _tri_box(w, d, h, x0=0, y0=0, z0=0):
    v = [(x0,y0,z0),(x0+w,y0,z0),(x0+w,y0+d,z0),(x0,y0+d,z0),
         (x0,y0,z0+h),(x0+w,y0,z0+h),(x0+w,y0+d,z0+h),(x0,y0+d,z0+h)]
    f = [(0,3,2),(0,2,1),(4,5,6),(4,6,7),(0,1,5),(0,5,4),
         (1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    return [(v[a],v[b],v[c]) for a,b,c in f]

def _write_stl(tris, path, name="engineai"):
    with open(path, "wb") as f:
        f.write(b"\0"*80); f.write(struct.pack("<I", len(tris)))
        for a,b,c in tris:
            ux,uy,uz = (b[0]-a[0], b[1]-a[1], b[2]-a[2])
            vx,vy,vz = (c[0]-a[0], c[1]-a[1], c[2]-a[2])
            n = (uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx)
            m = math.sqrt(sum(t*t for t in n)) or 1.0
            f.write(struct.pack("<3f", *[t/m for t in n]))
            for p in (a,b,c): f.write(struct.pack("<3f", *p))
            f.write(b"\0\0")
    return path

def _tri_prism(profile, depth, y0=0.0):
    """Extrude a closed 2D profile [(x,z)...] by `depth` in y. Watertight by
    construction: every edge is shared by exactly two triangles."""
    n = len(profile)
    lo = [(x, y0, z) for x, z in profile]
    hi = [(x, y0 + depth, z) for x, z in profile]
    tris = []
    for i in range(n):                                   # side walls
        j = (i + 1) % n
        tris += [(lo[i], lo[j], hi[j]), (lo[i], hi[j], hi[i])]
    for k in range(1, n - 1):                            # fan-triangulated caps
        tris.append((lo[0], lo[k + 1], lo[k]))
        tris.append((hi[0], hi[k], hi[k + 1]))
    return tris

@gen("stl_part", "cad", [], "Parametric watertight solid -> binary STL (no CAD kernel needed)")
def stl_part(w=100.0, d=40.0, h=20.0, rib=True, name="bracket", **kw):
    if rib:                                              # a true L, one closed profile
        t = w * 0.15
        prof = [(0, 0), (w, 0), (w, h), (t, h), (t, h * 3.0), (0, h * 3.0)]
        tris = _tri_prism(prof, d)
    else:
        tris = _tri_prism([(0, 0), (w, 0), (w, h), (0, h)], d)
    return _write_stl(tris, _out(f"{name}.stl"))

@gen("cad_step", "cad", ["cadquery"], "Parametric solid -> STEP + STL via CadQuery (real B-rep)")
def cad_step(w=100.0, d=40.0, h=20.0, hole=8.0, name="bracket", **kw):
    import cadquery as cq
    r = (cq.Workplane("XY").box(w, d, h)
           .faces(">Z").workplane().pushPoints([(-w/3, 0), (w/3, 0)]).hole(hole))
    sp, tp = _out(f"{name}.step"), _out(f"{name}_cq.stl")
    cq.exporters.export(r, sp); cq.exporters.export(r, tp)
    return [sp, tp]

@gen("stl_deflected_beam", "cad", [], "3D mesh of the SOLVED deflected shape -> STL")
def stl_deflected_beam(P=1000.0, L=1.0, E=200e9, I=8.333e-6, b=0.05, h=0.10,
                       n=48, case="cantilever", name="deflected_beam", **kw):
    xs = [L*i/n for i in range(n+1)]
    if case == "cantilever":
        ys = [-P*x**2*(3*L-x)/(6*E*I) for x in xs]
    else:
        ys = [-P*x*(3*L**2-4*x**2)/(48*E*I) if x <= L/2 else
              -P*(L-x)*(3*L**2-4*(L-x)**2)/(48*E*I) for x in xs]
    mx = max(abs(v) for v in ys) or 1e-30
    ex = (0.08*L)/mx                                   # exaggerate so it is visible
    ring = [(-b/2,-h/2),(b/2,-h/2),(b/2,h/2),(-b/2,h/2)]
    secs = [[(x, dy+ys[i]*ex, dz) for dz, dy in ring] for i, x in enumerate(xs)]
    tris = []
    for i in range(n):
        a, c = secs[i], secs[i+1]
        for j in range(4):
            k = (j+1) % 4
            tris += [(a[j], a[k], c[k]), (a[j], c[k], c[j])]
    for cap, rev in ((secs[0], True), (secs[-1], False)):
        t = [(cap[0], cap[1], cap[2]), (cap[0], cap[2], cap[3])]
        tris += [tuple(reversed(x)) for x in t] if rev else t
    return _write_stl(tris, _out(f"{name}.stl"))

@gen("sensitivity", "visual", ["matplotlib", "numpy"],
     "How the answer moves with each input - works for any formula")
def sensitivity(expr=None, sym=None, values=None, unit="", title="", **kw):
    import numpy as np, matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt, sympy as sp
    # Sensible default so the generator runs standalone (like all the others):
    # the cantilever tip deflection, the canonical worked example.
    if not expr or not sym or not values:
        expr = "P*L**3/(3*E*I)"; sym = "P L E I"
        values = dict(P=2500.0, L=1.2, E=200e9, I=8.333e-6)
        unit = unit or "m"; title = title or "cantilever tip deflection sensitivity"
    loc = {n: sp.Symbol(n, positive=True) for n in sym.split()}
    loc.update(pi=sp.pi, sqrt=sp.sqrt, tanh=sp.tanh, log=sp.log, exp=sp.exp)
    e = sp.sympify(expr, locals=loc)
    names = sorted(str(s) for s in e.free_symbols)
    base = float(e.subs({loc[n]: values[n] for n in names}))
    f = np.linspace(0.5, 1.5, 60)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for n in names:
        ys = []
        for k in f:
            v = dict(values); v[n] = values[n]*k
            ys.append(float(e.subs({loc[m]: v[m] for m in names}))/base)
        ax.plot((f-1)*100, ys, lw=2, label=n)
    ax.axhline(1, color="k", lw=.8, ls="--"); ax.axvline(0, color="k", lw=.8, ls="--")
    ax.set_xlabel("change in input  [%]"); ax.set_ylabel("result / baseline")
    ax.set_title(f"{title or expr}    baseline = {base:.5g} {unit}", fontsize=10)
    ax.legend(fontsize=9, ncol=len(names)); ax.grid(alpha=.3)
    p = _out("sensitivity.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    return p

@gen("viewer_3d", "visual", [], "Self-contained interactive 3D sandbox (HTML, opens in a browser)")
def viewer_3d(stl=None, title="EngineAI 3D sandbox", **kw):
    stl = stl or stl_part()
    b64 = base64.b64encode(open(stl, "rb").read()).decode()
    html = """<!doctype html><html><head><meta charset=utf-8><title>%TITLE%</title>
<style>body{margin:0;background:#11161c;color:#dfe6ee;font:14px system-ui}
#h{position:fixed;top:0;left:0;right:0;padding:9px 14px;background:#0d1117ee;z-index:9}
#dims{position:fixed;bottom:0;left:0;padding:8px 14px;background:#0d1117cc;font:12px ui-monospace;z-index:9;color:#9fb4cc}
button{background:#1b2735;color:#cfe0f5;border:1px solid #2f4258;border-radius:5px;padding:3px 9px;margin-left:5px;cursor:pointer;font:12px system-ui}
button:hover{background:#25384d}
b{color:#7fb3ff}</style></head><body>
<div id=h><b>%TITLE%</b> &nbsp;<span id=st></span>
<span style="float:right">
<button onclick="setView(0)">iso</button><button onclick="setView(1)">front</button>
<button onclick="setView(2)">side</button><button onclick="setView(3)">top</button>
<button onclick="toggleWire()">wireframe</button><button onclick="shot()">save PNG</button>
</span></div>
<div id=dims></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
const b64="%B64%";
const bin=Uint8Array.from(atob(b64),c=>c.charCodeAt(0)).buffer;
function parseSTL(buf){const dv=new DataView(buf),n=dv.getUint32(80,true);
 const pos=new Float32Array(n*9),nor=new Float32Array(n*9);let o=84;
 for(let i=0;i<n;i++){const nx=dv.getFloat32(o,true),ny=dv.getFloat32(o+4,true),nz=dv.getFloat32(o+8,true);o+=12;
  for(let j=0;j<3;j++){pos[i*9+j*3]=dv.getFloat32(o,true);pos[i*9+j*3+1]=dv.getFloat32(o+4,true);
   pos[i*9+j*3+2]=dv.getFloat32(o+8,true);nor[i*9+j*3]=nx;nor[i*9+j*3+1]=ny;nor[i*9+j*3+2]=nz;o+=12;}o+=2;}
 const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(pos,3));
 g.setAttribute('normal',new THREE.BufferAttribute(nor,3));return g;}
const g=parseSTL(bin);g.computeBoundingBox();const bb=g.boundingBox;
const ctr=new THREE.Vector3();bb.getCenter(ctr);g.translate(-ctr.x,-ctr.y,-ctr.z);
const size=bb.getSize(new THREE.Vector3()).length();
const sz=bb.getSize(new THREE.Vector3());
document.getElementById('st').textContent=(g.attributes.position.count/3|0)+" triangles";
document.getElementById('dims').textContent=
 "bounding box  X "+sz.x.toFixed(3)+"   Y "+sz.y.toFixed(3)+"   Z "+sz.z.toFixed(3)+
 "   |   diagonal "+sz.length().toFixed(3)+"   |   drag rotate, scroll zoom, shift+drag pan";
const sc=new THREE.Scene();sc.background=new THREE.Color(0x11161c);
const cam=new THREE.PerspectiveCamera(50,innerWidth/innerHeight,size/500,size*50);
const rd=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});rd.setSize(innerWidth,innerHeight);document.body.appendChild(rd.domElement);
sc.add(new THREE.HemisphereLight(0xffffff,0x223044,1.0));
const dl=new THREE.DirectionalLight(0xffffff,.8);dl.position.set(1,1,1);sc.add(dl);
const mesh=new THREE.Mesh(g,new THREE.MeshPhongMaterial({color:0x7fb3ff,specular:0x222222,shininess:40,flatShading:true}));
sc.add(mesh);sc.add(new THREE.LineSegments(new THREE.EdgesGeometry(g,25),new THREE.LineBasicMaterial({color:0x0d1117})));
sc.add(new THREE.GridHelper(size*2,20,0x2a3441,0x1b222c));
sc.add(new THREE.AxesHelper(size*0.6));
let rx=-.5,ry=.6,dist=size*1.9,px=0,py=0,down=false,pan={x:0,y:0},shift=false;
const VIEWS=[[-.5,.6],[0,0],[0,Math.PI/2],[-Math.PI/2+.001,0]];
function setView(i){rx=VIEWS[i][0];ry=VIEWS[i][1];pan={x:0,y:0};}
let wire=false;
function toggleWire(){wire=!wire;mesh.material.wireframe=wire;mesh.material.needsUpdate=true;}
function shot(){const a=document.createElement('a');a.download='engineai_view.png';
 rd.render(sc,cam);a.href=rd.domElement.toDataURL('image/png');a.click();}
addEventListener('keydown',e=>{if(e.key==='Shift')shift=true});
addEventListener('keyup',e=>{if(e.key==='Shift')shift=false});
addEventListener('mousedown',e=>{down=true;px=e.clientX;py=e.clientY});
addEventListener('mouseup',()=>down=false);
addEventListener('mousemove',e=>{if(!down)return;
 if(shift){pan.x-=(e.clientX-px)*size*.002;pan.y+=(e.clientY-py)*size*.002;}
 else{ry+=(e.clientX-px)*.01;rx+=(e.clientY-py)*.01;}
 px=e.clientX;py=e.clientY});
addEventListener('wheel',e=>{dist*=1+Math.sign(e.deltaY)*.1});
addEventListener('resize',()=>{cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();rd.setSize(innerWidth,innerHeight)});
(function loop(){requestAnimationFrame(loop);
 cam.position.set(pan.x+dist*Math.cos(rx)*Math.sin(ry),pan.y+dist*Math.sin(rx),
                  dist*Math.cos(rx)*Math.cos(ry));
 cam.lookAt(pan.x,pan.y,0);rd.render(sc,cam);})();
</script></body></html>"""
    html = html.replace("%TITLE%", title).replace("%B64%", b64)
    p = _out("sandbox_3d.html"); open(p, "w").write(html); return p

@gen("render_image", "visual", ["matplotlib", "numpy"],
     "Shaded 4-view image of a real part (the honest answer to 'generate an image')")
def render_image(stl=None, views="iso,front,side,top", **kw):
    import sys as _s, os as _o
    _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
    from render_image import render
    return render(stl or stl_part(), views=tuple(v.strip() for v in views.split(",")))

# ============================================================ DRAWING
@gen("dxf_drawing", "drawing", ["ezdxf"], "Dimensioned 2D mechanical drawing with title block (DXF)")
def dxf_drawing(w=100.0, h=40.0, hole=8.0, title="BRACKET", part="EA-001", **kw):
    import ezdxf
    d = ezdxf.new("R2010", setup=True); m = d.modelspace()
    m.add_lwpolyline([(0,0),(w,0),(w,h),(0,h)], close=True, dxfattribs={"layer":"OUTLINE"})
    for cx in (w/4, 3*w/4):
        m.add_circle((cx, h/2), hole/2, dxfattribs={"layer":"OUTLINE"})
        m.add_line((cx-hole, h/2), (cx+hole, h/2), dxfattribs={"layer":"CENTER"})
        m.add_line((cx, h/2-hole), (cx, h/2+hole), dxfattribs={"layer":"CENTER"})
    m.add_linear_dim(base=(0,-14), p1=(0,0), p2=(w,0), dimstyle="EZDXF").render()
    m.add_linear_dim(base=(-16,0), p1=(0,0), p2=(0,h), angle=90, dimstyle="EZDXF").render()
    m.add_linear_dim(base=(0,h+14), p1=(w/4,h), p2=(3*w/4,h), dimstyle="EZDXF").render()
    # title block
    bx, by = w+26, -26
    m.add_lwpolyline([(bx,by),(bx+92,by),(bx+92,by+34),(bx,by+34)], close=True)
    m.add_line((bx,by+22),(bx+92,by+22)); m.add_line((bx,by+11),(bx+92,by+11))
    for txt, pos, hgt in ((title,(bx+3,by+25),4.4), (f"PART {part}",(bx+3,by+14),3.0),
                          (datetime.date.today().isoformat(),(bx+3,by+3),3.0),
                          ("ALL DIMS mm",(bx+52,by+3),3.0), ("EngineAI",(bx+52,by+14),3.0)):
        m.add_text(txt, height=hgt).set_placement(pos)
    p = _out(f"{title.lower()}_drawing.dxf"); d.saveas(p); return p

@gen("drawing_png", "drawing", ["matplotlib"], "Dimensioned drawing rendered to PNG (viewable anywhere)")
def drawing_png(w=100.0, h=40.0, hole=8.0, title="BRACKET", **kw):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 4.4))
    ax.add_patch(plt.Rectangle((0,0), w, h, fill=False, lw=2, ec="#1b2631"))
    for cx in (w/4, 3*w/4):
        ax.add_patch(plt.Circle((cx, h/2), hole/2, fill=False, lw=1.6, ec="#1b2631"))
        ax.plot([cx-hole, cx+hole],[h/2,h/2], lw=.7, ls=(0,(8,3,1,3)), color="#c0392b")
        ax.plot([cx,cx],[h/2-hole,h/2+hole], lw=.7, ls=(0,(8,3,1,3)), color="#c0392b")
    def dim(x1,y1,x2,y2,txt,off):
        if y1==y2:
            ax.annotate("", (x1,y1-off), (x2,y2-off), arrowprops=dict(arrowstyle="<|-|>", color="#1b6ca8"))
            ax.text((x1+x2)/2, y1-off+1.5, txt, ha="center", fontsize=9, color="#1b6ca8")
        else:
            ax.annotate("", (x1-off,y1), (x2-off,y2), arrowprops=dict(arrowstyle="<|-|>", color="#1b6ca8"))
            ax.text(x1-off-2, (y1+y2)/2, txt, va="center", rotation=90, fontsize=9, color="#1b6ca8")
    dim(0,0,w,0, f"{w:g}", 10); dim(0,0,0,h, f"{h:g}", 10)
    dim(w/4,h,3*w/4,h, f"{w/2:g}", -12)
    ax.text(w/4, h/2+hole+3, f"2 × ⌀{hole:g}", ha="center", fontsize=9)
    ax.set_xlim(-24, w+18); ax.set_ylim(-24, h+24); ax.set_aspect("equal"); ax.axis("off")
    ax.set_title(f"{title}   —   all dimensions in mm", fontsize=11, weight="bold")
    p = _out(f"{title.lower()}_drawing.png"); fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    return p

# ============================================================ DOCUMENTS
@gen("docx_report", "document", ["docx"], "Word report")
def docx_report(title="EngineAI Report", rows=None, body="", **kw):
    from docx import Document
    d = Document(); d.add_heading(title, 0)
    d.add_paragraph(datetime.datetime.now().strftime("%d %B %Y"))
    if body: d.add_paragraph(body)
    rows = rows or [["quantity","value","unit"],["deflection","0.200","mm"],["stress","39.8","MPa"]]
    t = d.add_table(rows=0, cols=len(rows[0])); t.style = "Light Grid Accent 1"
    for r in rows:
        for i, c in enumerate(t.add_row().cells): c.text = str(r[i])
    p = _out("report.docx"); d.save(p); return p

@gen("xlsx_sheet", "document", ["openpyxl"], "Excel calculation sheet with live formulas")
def xlsx_sheet(P=1000.0, L=1.0, E=200e9, I=8.333e-6, **kw):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook(); ws = wb.active; ws.title = "cantilever"
    ws["A1"] = "EngineAI - cantilever tip deflection"; ws["A1"].font = Font(bold=True, size=13)
    for i,(k,v,u) in enumerate([("P",P,"N"),("L",L,"m"),("E",E,"Pa"),("I",I,"m^4")], start=3):
        ws[f"A{i}"], ws[f"B{i}"], ws[f"C{i}"] = k, v, u
    ws["A8"] = "delta = P*L^3/(3*E*I)"; ws["A8"].font = Font(italic=True)
    ws["A9"] = "delta"; ws["B9"] = "=B3*B4^3/(3*B5*B6)"; ws["C9"] = "m"
    ws["B9"].fill = PatternFill("solid", start_color="FFF2CC"); ws["B9"].font = Font(bold=True)
    ws.column_dimensions["A"].width = 26; ws.column_dimensions["B"].width = 18
    p = _out("calc.xlsx"); wb.save(p); return p

@gen("pptx_deck", "document", ["pptx"], "Slide deck")
def pptx_deck(title="EngineAI", subtitle="Verified engineering answers", bullets=None, **kw):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    pr = Presentation(); s = pr.slides.add_slide(pr.slide_layouts[0])
    s.shapes.title.text = title; s.placeholders[1].text = subtitle
    s2 = pr.slides.add_slide(pr.slide_layouts[1]); s2.shapes.title.text = "Result"
    tf = s2.placeholders[1].text_frame
    for i, b in enumerate(bullets or ["Closed-form solution, cited","Checked against the textbook",
                                      "Model output audited by the guard"]):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.text = b; para.font.size = Pt(20)
    p = _out("deck.pptx"); pr.save(p); return p

@gen("html_report", "document", [], "Self-contained HTML report with embedded figures")
def html_report(title="EngineAI Report", images=(), rows=(), note="", **kw):
    imgs = ""
    for im in images:
        if im and os.path.exists(im):
            b = base64.b64encode(open(im, "rb").read()).decode()
            imgs += f'<img src="data:image/png;base64,{b}" style="max-width:100%;margin:12px 0;border-radius:6px">'
    tr = "".join(f"<tr><td>{a}</td><td><b>{b}</b></td></tr>" for a, b in rows)
    p = _out("report.html")
    open(p, "w").write(f"""<!doctype html><meta charset=utf-8><title>{title}</title>
<style>body{{font:15px/1.6 system-ui;max-width:900px;margin:40px auto;padding:0 20px;color:#1b2631}}
h1{{border-bottom:3px solid #1b6ca8;padding-bottom:6px}} table{{border-collapse:collapse;margin:14px 0}}
td{{border:1px solid #ccd;padding:6px 14px}} .n{{color:#555;font-style:italic}}</style>
<h1>{title}</h1><p class=n>{datetime.datetime.now():%d %B %Y, %H:%M}</p>
<table>{tr}</table>{imgs}<p class=n>{note}</p>""")
    return p

@gen("csv_data", "data", [], "CSV / machine-readable results")
def csv_data(rows=None, name="results", **kw):
    import csv
    rows = rows or [["x","deflection_m"],[0,0],[0.5,7.5e-5],[1.0,2.0e-4]]
    p = _out(f"{name}.csv")
    with open(p, "w", newline="") as f: csv.writer(f).writerows(rows)
    return p

# ============================================================ ELECTRICAL
@gen("circuit_schematic", "circuit", ["schemdraw"], "Circuit schematic drawing")
def circuit_schematic(name="rc_lowpass", **kw):
    import matplotlib; matplotlib.use("Agg")
    import schemdraw, schemdraw.elements as e
    with schemdraw.Drawing(show=False) as d:
        d += e.SourceSin().up().label("Vin")
        d += e.Resistor().right().label("R  1kΩ")
        d += e.Capacitor().down().label("C  1µF")
        d += e.Line().left()
        p = _out(f"{name}.png"); d.save(p)
    return p

# ============================================================ DIAGRAM
@gen("flowchart", "diagram", ["graphviz"], "Process / decision flowchart")
def flowchart(name="engineai_pipeline", **kw):
    import graphviz
    g = graphviz.Digraph(format="png")
    g.attr(rankdir="LR", bgcolor="white", fontname="Helvetica")
    g.attr("node", shape="box", style="rounded,filled", fillcolor="#eaf2fb",
           color="#1b6ca8", fontname="Helvetica")
    for a, b in [("question","closed form"),("closed form","compute"),("compute","cite books"),
                 ("cite books","guard model"),("guard model","visualise"),("visualise","report")]:
        g.edge(a, b)
    try:
        return g.render(_out(name), cleanup=True)
    except Exception:
        p = _out(name + ".dot"); open(p, "w").write(g.source); return p

# ============================================================ CLI
def check():
    print(f"\n{'generator':20s} {'category':10s} {'status':10s} needs")
    print("-"*76)
    n_ok = 0
    for k in sorted(REGISTRY, key=lambda x: (REGISTRY[x].cat, x)):
        f = REGISTRY[k]; ok = available(k); n_ok += ok
        miss = "" if ok else "pip install " + " ".join(m for m in f.needs if not have(m))
        print(f"{k:20s} {f.cat:10s} {'ok' if ok else 'MISSING':10s} {miss}")
    print(f"\n{n_ok}/{len(REGISTRY)} available   output dir: {OUT}")

def demo():
    os.makedirs(OUT, exist_ok=True)
    made, skipped = [], []
    order = ["beam_diagram","shear_moment","sensitivity","stl_deflected_beam","stress_field","mohr_circle","drawing_png",
             "dxf_drawing","stl_part","cad_step","viewer_3d","docx_report","xlsx_sheet",
             "pptx_deck","html_report","csv_data","circuit_schematic","flowchart"]
    for k in order:
        if k not in REGISTRY: continue
        if not available(k): skipped.append((k, "missing " + ",".join(REGISTRY[k].needs))); continue
        try:
            kw = dict(expr="P*L**3/(3*E*I)", sym="P L E I",
                      values=dict(P=1000., L=1., E=200e9, I=8.333e-6), unit="m") if k == "sensitivity" else {}
            r = REGISTRY[k](**kw)
            for f in (r if isinstance(r, list) else [r]):
                made.append(f); print(f"  made  {os.path.basename(f):34s} {os.path.getsize(f)/1024:8.1f} KB")
        except Exception as ex:
            skipped.append((k, f"{type(ex).__name__}: {str(ex)[:52]}"))
    for k, why in skipped: print(f"  skip  {k:34s} {why}")
    print(f"\n{len(made)} file(s) in {OUT}")
    return made

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list","check","demo","run"])
    ap.add_argument("name", nargs="?"); ap.add_argument("args", nargs="?", default="{}")
    a = ap.parse_args()
    if a.cmd == "list":
        cat = None
        for k in sorted(REGISTRY, key=lambda x: (REGISTRY[x].cat, x)):
            f = REGISTRY[k]
            if f.cat != cat: cat = f.cat; print(f"\n[{cat}]")
            print(f"  {k:20s} {f.doc}")
    elif a.cmd == "check": check()
    elif a.cmd == "demo": demo()
    elif a.cmd == "run":
        if a.name not in REGISTRY: sys.exit(f"unknown generator: {a.name}")
        print(REGISTRY[a.name](**json.loads(a.args)))

if __name__ == "__main__":
    main()
