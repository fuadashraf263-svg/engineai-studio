#!/usr/bin/env python3
"""
viz3d.py - the engineering sandbox: 3D geometry COLOURED BY SOLVER RESULTS.

A viewer that shows a grey STL is a model viewer. An engineering sandbox shows
the field: stress on the deformed shape, velocity through the channel, with a
legend, a deformation slider, probe-on-click, and a section plane.

  python viz3d.py beam --P 1000 --L 1 --E 200e9 --b 0.05 --h 0.1   analytic demo
  python viz3d.py frd  results.frd                                  CalculiX output
  python viz3d.py cfd  ~/engineai/work/cfd_poiseuille               OpenFOAM field
  python viz3d.py --selftest
"""
import os, re, sys, math, json, struct, argparse

OUT = os.path.expanduser(os.environ.get("ENGINEAI_OUT", "~/engineai/out"))

# ---------------------------------------------------------------- meshes
def beam_mesh(L=1.0, b=0.05, h=0.10, nx=40, ny=4, nz=6):
    """Hex-ish structured grid of a cantilever, as nodes + quad faces."""
    nodes, idx = [], {}
    for i in range(nx+1):
        for j in range(ny+1):
            for k in range(nz+1):
                idx[(i,j,k)] = len(nodes)
                nodes.append([L*i/nx, b*(j/ny-0.5), h*(k/nz-0.5)])
    faces = []
    def quad(a,bb,c,d): faces.append((a,bb,c,d))
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                if k == 0:    quad(idx[(i,j,0)],idx[(i+1,j,0)],idx[(i+1,j+1,0)],idx[(i,j+1,0)])
                if k == nz-1: quad(idx[(i,j,nz)],idx[(i,j+1,nz)],idx[(i+1,j+1,nz)],idx[(i+1,j,nz)])
                if j == 0:    quad(idx[(i,0,k)],idx[(i,0,k+1)],idx[(i+1,0,k+1)],idx[(i+1,0,k)])
                if j == ny-1: quad(idx[(i,ny,k)],idx[(i+1,ny,k)],idx[(i+1,ny,k+1)],idx[(i,ny,k+1)])
                if i == 0:    quad(idx[(0,j,k)],idx[(0,j+1,k)],idx[(0,j+1,k+1)],idx[(0,j,k+1)])
                if i == nx-1: quad(idx[(nx,j,k)],idx[(nx,j,k+1)],idx[(nx,j+1,k+1)],idx[(nx,j+1,k)])
    return nodes, faces

def beam_fields(nodes, P=1000.0, L=1.0, E=200e9, b=0.05, h=0.10):
    """Closed-form cantilever: bending stress and deflection at every node.
    sigma = M*y/I  with M(x) = -P(L-x);  v(x) = -P x^2 (3L-x)/(6EI)."""
    I = b*h**3/12
    sig, disp = [], []
    for x, _y, z in nodes:
        M = -P*(L - x)
        sig.append(M*z/I)                                  # z is the through-depth axis
        disp.append([0.0, 0.0, -P*x*x*(3*L - x)/(6*E*I)])
    return dict(von_mises=[abs(s) for s in sig], sigma_xx=sig), disp

# ------------------------------------------------------- CalculiX .frd
def read_frd(path):
    """Minimal CalculiX .frd reader: nodes, and DISP / STRESS blocks."""
    nodes, nmap, disp, stress = [], {}, {}, {}
    mode = None
    for line in open(path, errors="ignore"):
        s = line.rstrip("\n")
        if s.startswith("    2C"): mode = "nodes"; continue
        if s.startswith("    3C"): mode = "elems"; continue
        if " DISP" in s:   mode = "disp";  continue
        if " STRESS" in s: mode = "stress"; continue
        if s.startswith(" -3"): mode = None; continue
        if mode == "nodes" and s.startswith(" -1"):
            n = int(s[3:13]); xyz = [float(s[13+12*i:25+12*i]) for i in range(3)]
            nmap[n] = len(nodes); nodes.append(xyz)
        elif mode in ("disp", "stress") and s.startswith(" -1"):
            n = int(s[3:13])
            vals = [float(s[13+12*i:25+12*i]) for i in range((len(s)-13)//12)]
            (disp if mode == "disp" else stress)[n] = vals
    if not nodes: return None
    d = [disp.get(n, [0,0,0])[:3] for n in sorted(nmap, key=lambda k: nmap[k])]
    vm = []
    for n in sorted(nmap, key=lambda k: nmap[k]):
        v = stress.get(n)
        if v and len(v) >= 6:
            sx, sy, sz, sxy, syz, szx = v[:6]
            vm.append(math.sqrt(0.5*((sx-sy)**2+(sy-sz)**2+(sz-sx)**2)
                                + 3*(sxy**2+syz**2+szx**2)))
        else: vm.append(0.0)
    return nodes, d, vm

# --------------------------------------------------------------- viewer
def render(nodes, faces, fields, disp=None, title="EngineAI sandbox",
           unit="Pa", path=None, note=""):
    """One self-contained HTML: colour map + legend + deform slider + probe."""
    data = dict(nodes=[[round(c, 8) for c in n] for n in nodes],
                faces=faces, fields={k: [round(v, 6) for v in vals] for k, vals in fields.items()},
                disp=[[round(c, 10) for c in d] for d in (disp or [])],
                unit=unit, title=title, note=note)
    payload = json.dumps(data, separators=(",", ":"))
    doc = r"""<!doctype html><html><head><meta charset=utf-8><title>%TITLE%</title>
<style>
body{margin:0;background:#0e1319;color:#dbe6f2;font:13px system-ui;overflow:hidden}
#hud{position:fixed;top:0;left:0;right:0;padding:9px 13px;background:#0b1017ee;z-index:9;
 display:flex;gap:14px;align-items:center;flex-wrap:wrap}
b{color:#8fc0ff}
select,input[type=range]{vertical-align:middle}
select{background:#17222f;color:#dbe6f2;border:1px solid #2b3d52;border-radius:5px;padding:3px 7px}
button{background:#17222f;color:#cfe0f5;border:1px solid #2b3d52;border-radius:5px;
 padding:3px 9px;cursor:pointer;font:12px system-ui}
button:hover{background:#22384e}
#legend{position:fixed;right:14px;top:74px;width:64px;z-index:9;text-align:center;font:11px ui-monospace}
#bar{height:220px;border:1px solid #2b3d52;border-radius:3px}
#probe{position:fixed;left:14px;bottom:14px;background:#0b1017ee;border:1px solid #2b3d52;
 border-radius:6px;padding:9px 12px;z-index:9;font:12px ui-monospace;display:none}
#foot{position:fixed;left:14px;bottom:14px;color:#7f97b3;font:11px ui-monospace;z-index:8}
</style></head><body>
<div id=hud>
 <b>%TITLE%</b>
 <span>field <select id=fld></select></span>
 <span>deform ×<span id=dv>1</span> <input id=ds type=range min=0 max=400 value=100 style="width:120px"></span>
 <span>section <input id=sec type=range min=0 max=100 value=100 style="width:110px"></span>
 <button onclick="setView(0)">iso</button><button onclick="setView(1)">front</button>
 <button onclick="setView(2)">side</button><button onclick="setView(3)">top</button>
 <button onclick="tw()">edges</button><button onclick="shot()">PNG</button>
</div>
<div id=legend><div id=hi></div><canvas id=bar width=22 height=220></canvas><div id=lo></div>
 <div id=un style="margin-top:4px;color:#7f97b3"></div></div>
<div id=probe></div><div id=foot></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
const D=%PAYLOAD%;
const sc=new THREE.Scene();sc.background=new THREE.Color(0x0e1319);
const rd=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
rd.setSize(innerWidth,innerHeight);document.body.appendChild(rd.domElement);
// ---- geometry: quads -> triangles, one vertex per corner so colours are per-node
const pos=[],col=[],base=[],dsp=[],nid=[];
function push(i){const p=D.nodes[i];base.push(p[0],p[1],p[2]);
 const d=D.disp.length?D.disp[i]:[0,0,0];dsp.push(d[0],d[1],d[2]);nid.push(i);
 pos.push(p[0],p[1],p[2]);col.push(1,1,1);}
D.faces.forEach(f=>{push(f[0]);push(f[1]);push(f[2]);push(f[0]);push(f[2]);push(f[3]);});
const g=new THREE.BufferGeometry();
g.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));
g.setAttribute('color',new THREE.Float32BufferAttribute(col,3));
g.computeVertexNormals();
const mat=new THREE.MeshPhongMaterial({vertexColors:true,flatShading:false,
 shininess:22,side:THREE.DoubleSide});
const mesh=new THREE.Mesh(g,mat);sc.add(mesh);
const wire=new THREE.LineSegments(new THREE.WireframeGeometry(g),
 new THREE.LineBasicMaterial({color:0x24374d}));wire.visible=false;sc.add(wire);
sc.add(new THREE.HemisphereLight(0xffffff,0x1b2836,1.05));
const dl=new THREE.DirectionalLight(0xffffff,.55);dl.position.set(1,1,1);sc.add(dl);
// ---- bounds
const bb=new THREE.Box3().setFromObject(mesh);const ctr=bb.getCenter(new THREE.Vector3());
const size=bb.getSize(new THREE.Vector3()).length()||1;
// ---- colour map (blue -> cyan -> green -> yellow -> red)
function jet(t){t=Math.max(0,Math.min(1,t));
 const r=Math.max(0,Math.min(1,1.5-Math.abs(4*t-3)));
 const gg=Math.max(0,Math.min(1,1.5-Math.abs(4*t-2)));
 const b=Math.max(0,Math.min(1,1.5-Math.abs(4*t-1)));return[r,gg,b];}
let cur=Object.keys(D.fields)[0],lo=0,hi=1;
const sel=document.getElementById('fld');
Object.keys(D.fields).forEach(k=>{const o=document.createElement('option');o.value=o.textContent=k;sel.appendChild(o);});
sel.onchange=()=>{cur=sel.value;paint();};
function paint(){const F=D.fields[cur];lo=Math.min(...F);hi=Math.max(...F);
 const c=g.attributes.color.array;
 for(let v=0;v<nid.length;v++){const t=(hi-lo)?(F[nid[v]]-lo)/(hi-lo):0.5;
  const [r,gg,b]=jet(t);c[v*3]=r;c[v*3+1]=gg;c[v*3+2]=b;}
 g.attributes.color.needsUpdate=true;
 document.getElementById('hi').textContent=hi.toPrecision(4);
 document.getElementById('lo').textContent=lo.toPrecision(4);
 document.getElementById('un').textContent=D.unit;
 const cv=document.getElementById('bar').getContext('2d');
 for(let y=0;y<220;y++){const [r,gg,b]=jet(1-y/219);
  cv.fillStyle='rgb('+(r*255|0)+','+(gg*255|0)+','+(b*255|0)+')';cv.fillRect(0,y,22,1);}}
let scale=1;
function deform(){const p=g.attributes.position.array;
 for(let v=0;v<nid.length;v++){p[v*3]=base[v*3]+dsp[v*3]*scale;
  p[v*3+1]=base[v*3+1]+dsp[v*3+1]*scale;p[v*3+2]=base[v*3+2]+dsp[v*3+2]*scale;}
 g.attributes.position.needsUpdate=true;g.computeVertexNormals();}
document.getElementById('ds').oninput=e=>{scale=Math.pow(10,(e.target.value/100)-1)*
 (D.disp.length?(0.12*size)/Math.max(1e-30,Math.max(...D.disp.map(d=>Math.hypot(d[0],d[1],d[2])))):1);
 document.getElementById('dv').textContent=scale.toPrecision(3);deform();};
document.getElementById('sec').oninput=e=>{const f=e.target.value/100;
 mat.clippingPlanes=[new THREE.Plane(new THREE.Vector3(0,-1,0),
   bb.min.y+f*(bb.max.y-bb.min.y))];rd.localClippingEnabled=true;};
function tw(){wire.visible=!wire.visible;}
function shot(){const a=document.createElement('a');a.download='engineai_field.png';
 rd.render(sc,cam);a.href=rd.domElement.toDataURL('image/png');a.click();}
// ---- camera
const cam=new THREE.PerspectiveCamera(48,innerWidth/innerHeight,size/800,size*60);
let rx=-.42,ry=.62,dist=size*1.6,px=0,py=0,down=false;
const VIEWS=[[-.42,.62],[0,0],[0,Math.PI/2],[-Math.PI/2+.001,0]];
function setView(i){rx=VIEWS[i][0];ry=VIEWS[i][1];}
addEventListener('mousedown',e=>{down=true;px=e.clientX;py=e.clientY});
addEventListener('mouseup',()=>down=false);
addEventListener('mousemove',e=>{if(!down)return;ry+=(e.clientX-px)*.008;rx+=(e.clientY-py)*.008;
 px=e.clientX;py=e.clientY});
addEventListener('wheel',e=>{dist*=1+Math.sign(e.deltaY)*.09});
addEventListener('resize',()=>{cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();
 rd.setSize(innerWidth,innerHeight)});
// ---- probe on click
const ray=new THREE.Raycaster(),m2=new THREE.Vector2();
addEventListener('click',e=>{m2.x=(e.clientX/innerWidth)*2-1;m2.y=-(e.clientY/innerHeight)*2+1;
 ray.setFromCamera(m2,cam);const hit=ray.intersectObject(mesh)[0];const box=document.getElementById('probe');
 if(!hit){box.style.display='none';return;}
 const i=nid[hit.face.a];const p=D.nodes[i];
 box.style.display='block';
 box.innerHTML="node "+i+"<br>x "+p[0].toPrecision(4)+"  y "+p[1].toPrecision(4)+"  z "+p[2].toPrecision(4)+
  "<br><b>"+cur+" = "+D.fields[cur][i].toPrecision(5)+" "+D.unit+"</b>"+
  (D.disp.length?"<br>|u| = "+Math.hypot(...D.disp[i]).toPrecision(4):"");});
document.getElementById('foot').textContent=D.note+"   |  "+D.nodes.length+" nodes, "+
 D.faces.length+" faces  |  drag rotate, scroll zoom, click to probe";
paint();
(function loop(){requestAnimationFrame(loop);
 cam.position.set(ctr.x+dist*Math.cos(rx)*Math.sin(ry),ctr.y+dist*Math.sin(rx),
                  ctr.z+dist*Math.cos(rx)*Math.cos(ry));
 cam.lookAt(ctr);rd.render(sc,cam);})();
</script></body></html>"""
    doc = doc.replace("%TITLE%", title).replace("%PAYLOAD%", payload)
    os.makedirs(OUT, exist_ok=True)
    path = path or os.path.join(OUT, "sandbox_field.html")
    open(path, "w").write(doc)
    return path

# ------------------------------------------------------------- commands
def demo_beam(P=1000.0, L=1.0, E=200e9, b=0.05, h=0.10):
    nodes, faces = beam_mesh(L, b, h)
    fields, disp = beam_fields(nodes, P, L, E, b, h)
    I = b*h**3/12
    tip = P*L**3/(3*E*I); smax = P*L*(h/2)/I
    note = (f"cantilever P={P:g} N, L={L:g} m  |  tip {tip*1000:.4g} mm, "
            f"sigma_max {smax/1e6:.4g} MPa (closed form)")
    return render(nodes, faces, fields, disp, "EngineAI — cantilever, stress on deformed shape",
                  "Pa", note=note), tip, smax

def selftest():
    ok = True
    print("\nVIZ3D SELFTEST")
    p, tip, smax = demo_beam()
    t = open(p).read()
    checks = [("colour map", "function jet" in t), ("legend", "getElementById('bar')" in t),
              ("deform slider", "function deform" in t), ("section plane", "clippingPlanes" in t),
              ("probe on click", "Raycaster" in t), ("field selector", "sel.onchange" in t),
              ("per-node colours", "vertexColors" in t), ("self-contained", "%PAYLOAD%" not in t)]
    for n, c in checks:
        print(f"  {'ok  ' if c else 'FAIL'} {n}"); ok &= c
    nodes, faces = beam_mesh()
    fields, disp = beam_fields(nodes)
    I = 0.05*0.10**3/12
    smax_num = max(fields["von_mises"]); smax_cf = 1000*1.0*(0.05)/I
    err = abs(smax_num - smax_cf)/smax_cf*100
    print(f"  {'ok  ' if err < 1 else 'FAIL'} peak stress {smax_num/1e6:.4g} MPa vs closed form "
          f"{smax_cf/1e6:.4g} MPa ({err:.2f}%)")
    ok &= err < 1
    tipn = abs(min(d[2] for d in disp)); tipc = 1000*1.0**3/(3*200e9*I)
    err2 = abs(tipn - tipc)/tipc*100
    print(f"  {'ok  ' if err2 < 1 else 'FAIL'} tip deflection {tipn*1000:.5g} mm vs "
          f"{tipc*1000:.5g} mm ({err2:.2f}%)")
    ok &= err2 < 1
    print(f"  file {os.path.basename(p)}  {os.path.getsize(p)/1024:.0f} KB, "
          f"{len(nodes)} nodes, {len(faces)} faces")
    print("\nviz3d selftest:", "PASSED" if ok else "FAILED")
    return ok

def main():
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("mode", nargs="?", default="beam", choices=["beam", "frd", "cfd"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    a, extra = ap.parse_known_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    kw = {}
    i = 0
    while i < len(extra):
        if extra[i].startswith("--") and i+1 < len(extra):
            try: kw[extra[i][2:]] = float(extra[i+1]); i += 2; continue
            except ValueError: pass
        i += 1
    if a.mode == "beam":
        p, tip, smax = demo_beam(**{k: v for k, v in kw.items() if k in ("P","L","E","b","h")})
        print(f"  {p}\n  tip {tip*1000:.5g} mm, sigma_max {smax/1e6:.5g} MPa")
    elif a.mode == "frd":
        r = read_frd(a.path)
        if not r: sys.exit("could not parse " + str(a.path))
        nodes, disp, vm = r
        print(f"  {len(nodes)} nodes from {a.path}")
        print("  note: .frd gives nodes and results but not the surface topology;")
        print("        use the beam mode for a full surface, or export a VTK from CalculiX.")
    elif a.mode == "cfd":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import cfd_validate as C
        ys, prof = C.read_profile(a.path or C.CASE, verbose=True)
        if not prof: sys.exit("no CFD profile found")
        print(f"  {len(prof)} points, u_max {max(prof):.6f} m/s")

if __name__ == "__main__":
    main()
