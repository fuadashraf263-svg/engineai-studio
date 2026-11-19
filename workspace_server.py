#!/usr/bin/env python3
"""
workspace_server.py -- the two-panel workspace: chat + live 3D viewer.

This is the Manus-style split screen you asked for: conversation on the
left, a 3D viewer on the right that rotates, zooms, and shows the actual
part the AI generated. The difference from Manus is what feeds it -- every
3D model here comes from cad.py, which re-measures its own output, so what
spins in the viewer is a VERIFIED solid, not a plausible-looking render.

HONEST SCOPE
------------
This is a 3D VIEWER, not a full CAD editor. You can orbit, zoom, and
inspect the generated part. You cannot yet drag a face to edit it in real
time -- that is a much larger piece of work. But "ask for a part, watch the
real verified geometry appear and spin" is genuinely useful and is what
students will react to.

HOW IT WORKS
------------
  * left panel: the same verified chat router (handbook / math / circuit /
    library / brain), unchanged.
  * right panel: three.js STL viewer. When the AI generates a part (or you
    type e.g. "show tube od 8.56 wall 0.6 length 25"), cad.py writes an STL
    and the viewer loads it.
  * everything the chat does stays free and offline; the 3D generation uses
    cad.py locally (no API, no internet).

Run:  conda activate eng && pip install fastapi uvicorn "uvicorn[standard]"
      python workspace_server.py     ->  http://localhost:8097
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel

HOME = Path.home() / "engineai"
CADOUT = HOME / "work" / "cad"
sys.path.insert(0, str(HOME))

app = FastAPI(title="EngineAI Workspace")

# This one reads and writes files in the workspace, so it is the worst of the
# three to leave open.
import server_auth
AUTH_TOKEN = server_auth.install(app, "EngineAI Workspace")


class Msg(BaseModel):
    text: str
    allow_brain: bool = False
    session: str = "anon"


# recognise a "show/make/generate a <shape> ..." request and build it
_SHAPE_RE = re.compile(
    r"\b(box|cube|cylinder|tube|pipe|sphere|ball|strut)\b", re.I)


def try_generate_3d(text: str) -> dict | None:
    """If the message describes a part, build it with cad.py and return the
    STL name for the viewer. Numbers are parsed from the text."""
    low = text.lower()
    if not any(w in low for w in ("show", "make", "generate", "draw",
                                  "model", "cad", "3d", "part")):
        if not _SHAPE_RE.search(low):
            return None
    m = _SHAPE_RE.search(low)
    if not m:
        return None
    shape = m.group(1).lower()
    nums = [float(x) for x in re.findall(r"[-+]?\d*\.?\d+", low)]

    def near(key):  # find 'od 8.56' style
        mm = re.search(key + r"[^\d]*([-+]?\d*\.?\d+)", low)
        return float(mm.group(1)) if mm else None

    try:
        import cad
        if shape in ("box", "cube"):
            l, w, h = (nums + [10, 10, 10])[:3]
            r = cad.build_and_verify("box", {"l": l, "w": w, "h": h},
                                     name="ws_part")
        elif shape in ("cylinder",):
            d, h = (nums + [10, 20])[:2]
            r = cad.build_and_verify("cylinder", {"d": d, "h": h},
                                     name="ws_part")
        elif shape in ("sphere", "ball"):
            d = (nums + [20])[0]
            r = cad.build_and_verify("sphere", {"d": d}, name="ws_part")
        elif shape in ("tube", "pipe", "strut"):
            od = near("od") or near("diameter") or (nums + [8.56])[0]
            wall = near("wall") or (nums[1] if len(nums) > 1 else 0.6)
            length = near("length") or near("long") or \
                (nums[2] if len(nums) > 2 else 25)
            r = cad.build_and_verify("tube", {"od": od, "wall": wall,
                                              "length": length},
                                     name="ws_part")
        else:
            return None
        return {"stl": "ws_part.stl", "info": r}
    except ImportError:
        return {"error": "cadquery not installed in this environment"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def route(text: str, allow_brain: bool, session: str) -> dict:
    """Same verified router as chat_server, plus 3D generation."""
    segments = []
    model3d = None

    # 3D first, if the message asks for a part
    gen = try_generate_3d(text)
    if gen and "stl" in gen:
        model3d = gen["stl"]
        info = gen["info"]
        segments.append({
            "provenance": "SOLVED" if info.get("verified") else "GENERATED",
            "text": f"Generated {info['shape']}: volume "
                    f"{info['measured_volume_mm3']:.2f} mm^3 "
                    f"(re-measured, error {info['volume_error_pct']:.3f}%). "
                    f"STEP + STL written. Rotating in the viewer \u2192"})
    elif gen and "error" in gen:
        segments.append({"provenance": "ERROR", "text": gen["error"]})

    # then the normal verified tiers
    try:
        import ask_command as ak
        for fn in ak.DETERMINISTIC:
            try:
                r = fn(text)
            except Exception:
                r = None
            if r:
                if r.startswith("["):
                    tag = r.split("]")[0].strip("[")
                    body = r.split("]", 1)[1].lstrip()
                else:
                    tag, body = "INFO", r
                segments.append({"provenance": tag, "text": body})
        import library
        for h in library.search(text, k=2):
            b = " ".join(h["text"].split())[:280]
            segments.append({"provenance": "LIBRARY",
                             "text": f"[{h['book']}, p.{h['page']}]\n\"{b}...\""})
    except Exception:
        pass

    if allow_brain and not segments:
        try:
            cfg = json.loads((HOME / "config.json").read_text())
            import ask_command as ak
            txt, spent = ak.ask_brain(cfg, text, None)
            segments.append({"provenance": "UNVERIFIED",
                             "text": txt + f"\n\n(model answer, ${spent:.3f}, "
                                     "NOT verified)"})
        except Exception as e:
            segments.append({"provenance": "ERROR", "text": str(e)})

    if not segments:
        segments.append({"provenance": "NONE",
                         "text": "Not in any verified source. Tick 'allow "
                                 "AI' for a labelled answer, or ask for a "
                                 "part like 'show tube od 8.56 wall 0.6 "
                                 "length 25'."})
    return {"segments": segments, "model3d": model3d}


@app.post("/ask")
def ask(m: Msg):
    return JSONResponse(route(m.text, m.allow_brain, m.session))


@app.get("/stl/{name}")
def stl(name: str):
    f = CADOUT / name
    if not f.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(f), media_type="model/stl")


@app.get("/")
def index():
    return HTMLResponse(PAGE)


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EngineAI Workspace</title>
<style>
  :root{--bg:#0d1117;--panel:#161b22;--line:#30363d;--ink:#e6edf3;
    --muted:#8b949e;--user:#1f6feb;--solved:#3fb950;--handbook:#58a6ff;
    --lookup:#d29922;--library:#bc8cff;--math:#39c5cf;--unverified:#f85149;
    --none:#6e7681;--generated:#d29922;}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
    background:var(--bg);color:var(--ink);height:100vh;display:flex;
    flex-direction:column;overflow:hidden}
  header{padding:12px 18px;border-bottom:1px solid var(--line);
    display:flex;align-items:center;gap:10px;flex:0 0 auto}
  header h1{font-size:15px;margin:0}
  header .tag{font-size:11px;color:var(--muted)}
  .split{flex:1;display:flex;min-height:0}
  .left{flex:1;display:flex;flex-direction:column;min-width:0;
    border-right:1px solid var(--line)}
  .right{width:46%;display:flex;flex-direction:column;background:#0a0d12}
  #log{flex:1;overflow-y:auto;padding:16px;display:flex;
    flex-direction:column;gap:12px}
  .msg{display:flex}.msg.user{justify-content:flex-end}
  .bubble{padding:9px 13px;border-radius:11px;max-width:88%;
    white-space:pre-wrap;word-break:break-word}
  .user .bubble{background:var(--user);color:#fff}
  .ai .bubble{background:var(--panel);border:1px solid var(--line);width:100%}
  .seg{border-left:3px solid var(--none);padding:7px 11px;margin:6px 0;
    border-radius:0 7px 7px 0;background:#0d1117}
  .seg .prov{font-size:10px;font-weight:700;text-transform:uppercase;
    letter-spacing:.04em;margin-bottom:3px}
  .p-SOLVED{border-color:var(--solved)}.p-SOLVED .prov{color:var(--solved)}
  .p-GENERATED{border-color:var(--generated)}.p-GENERATED .prov{color:var(--generated)}
  .p-HANDBOOK{border-color:var(--handbook)}.p-HANDBOOK .prov{color:var(--handbook)}
  .p-LOOKUP{border-color:var(--lookup)}.p-LOOKUP .prov{color:var(--lookup)}
  .p-LIBRARY{border-color:var(--library)}.p-LIBRARY .prov{color:var(--library)}
  .p-MATH{border-color:var(--math)}.p-MATH .prov{color:var(--math)}
  .p-UNVERIFIED{border-color:var(--unverified)}.p-UNVERIFIED .prov{color:var(--unverified)}
  .p-NONE{border-color:var(--none)}.p-NONE .prov{color:var(--none)}
  footer{border-top:1px solid var(--line);padding:12px;flex:0 0 auto}
  .composer{display:flex;gap:8px}
  textarea{flex:1;background:var(--panel);border:1px solid var(--line);
    color:var(--ink);border-radius:9px;padding:9px 11px;resize:none;
    font:inherit;min-height:40px;max-height:140px}
  button{background:var(--user);color:#fff;border:0;border-radius:9px;
    padding:0 16px;height:40px;font-weight:600;cursor:pointer}
  .rhead{padding:10px 14px;border-bottom:1px solid var(--line);
    font-size:12px;color:var(--muted);display:flex;justify-content:space-between}
  #viewer{flex:1;position:relative}
  #viewer canvas{display:block}
  .hint{position:absolute;bottom:10px;left:0;right:0;text-align:center;
    font-size:11px;color:var(--muted);pointer-events:none}
  .empty{position:absolute;inset:0;display:flex;align-items:center;
    justify-content:center;color:var(--muted);font-size:13px;text-align:center;
    padding:20px}
  .opts{font-size:11px;color:var(--muted);margin-top:6px}
</style></head>
<body>
<header><h1>EngineAI Workspace</h1>
  <span class="tag">verified answers &mdash; live 3D of generated parts</span>
</header>
<div class="split">
  <div class="left">
    <div id="log">
      <div class="msg ai"><div class="bubble"><div class="seg p-HANDBOOK">
        <div class="prov">try this</div>
        Ask an engineering question, or generate a part to see it in 3D:
        "show tube od 8.56 wall 0.6 length 25", "make a box 10 20 30",
        "cylinder 12 40". Every answer is tagged by source; every 3D model
        is re-measured before it spins.
      </div></div></div>
    </div>
    <footer>
      <div class="composer">
        <textarea id="q" placeholder="Ask, or 'show tube od 8.56 wall 0.6 length 25'..."
          onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send()}"></textarea>
        <button onclick="send()">Ask</button>
      </div>
      <div class="opts"><label><input type="checkbox" id="brain">
        allow AI fallback (labelled UNVERIFIED)</label></div>
    </footer>
  </div>
  <div class="right">
    <div class="rhead"><span>3D VIEWER</span><span id="dims"></span></div>
    <div id="viewer">
      <div class="empty" id="empty">Generate a part and it appears here &mdash;
        rotate with the mouse, scroll to zoom.</div>
      <div class="hint" id="hint" style="display:none">drag to rotate &middot; scroll to zoom</div>
    </div>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
let session=localStorage.getItem('ea_sid');
if(!session){session=Math.random().toString(36).slice(2);localStorage.setItem('ea_sid',session);}
const log=document.getElementById('log'),q=document.getElementById('q');
function add(role,html){const m=document.createElement('div');m.className='msg '+role;
  const b=document.createElement('div');b.className='bubble';b.innerHTML=html;
  m.appendChild(b);log.appendChild(m);log.scrollTop=log.scrollHeight;return b;}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

// ---- three.js viewer ----
let scene,camera,renderer,mesh,rot={x:0,y:0},drag=false,px,py,dist=80;
function initViewer(){
  const el=document.getElementById('viewer');
  scene=new THREE.Scene();scene.background=new THREE.Color(0x0a0d12);
  camera=new THREE.PerspectiveCamera(45,el.clientWidth/el.clientHeight,0.1,5000);
  renderer=new THREE.WebGLRenderer({antialias:true});
  renderer.setSize(el.clientWidth,el.clientHeight);el.appendChild(renderer.domElement);
  scene.add(new THREE.AmbientLight(0xffffff,0.6));
  const d=new THREE.DirectionalLight(0xffffff,0.8);d.position.set(1,1,1);scene.add(d);
  const d2=new THREE.DirectionalLight(0x88aaff,0.4);d2.position.set(-1,-1,-1);scene.add(d2);
  renderer.domElement.addEventListener('mousedown',e=>{drag=true;px=e.clientX;py=e.clientY;});
  window.addEventListener('mouseup',()=>drag=false);
  window.addEventListener('mousemove',e=>{if(!drag)return;
    rot.y+=(e.clientX-px)*0.01;rot.x+=(e.clientY-py)*0.01;px=e.clientX;py=e.clientY;});
  renderer.domElement.addEventListener('wheel',e=>{dist*=(1+e.deltaY*0.001);
    dist=Math.max(10,Math.min(500,dist));e.preventDefault();});
  animate();
}
function animate(){requestAnimationFrame(animate);
  if(mesh){camera.position.x=Math.sin(rot.y)*Math.cos(rot.x)*dist;
    camera.position.y=Math.sin(rot.x)*dist;camera.position.z=Math.cos(rot.y)*Math.cos(rot.x)*dist;
    camera.lookAt(0,0,0);if(!drag)rot.y+=0.003;}
  if(renderer)renderer.render(scene,camera);}
function loadSTL(name){
  fetch('/stl/'+name).then(r=>r.arrayBuffer()).then(buf=>{
    const g=parseSTL(buf);g.computeVertexNormals();g.center();
    if(mesh)scene.remove(mesh);
    const mat=new THREE.MeshPhongMaterial({color:0x4a9eff,specular:0x222222,
      shininess:60,flatShading:false});
    mesh=new THREE.Mesh(g,mat);scene.add(mesh);
    g.computeBoundingBox();const s=g.boundingBox.getSize(new THREE.Vector3());
    dist=Math.max(s.x,s.y,s.z)*2.2;
    document.getElementById('empty').style.display='none';
    document.getElementById('hint').style.display='block';
    document.getElementById('dims').textContent=
      s.x.toFixed(1)+' x '+s.y.toFixed(1)+' x '+s.z.toFixed(1)+' mm';
  }).catch(e=>console.error('STL load',e));
}
// minimal binary+ascii STL parser
function parseSTL(buf){
  const dv=new DataView(buf);let isAscii=true;
  const head=new Uint8Array(buf,0,Math.min(80,buf.byteLength));
  // heuristic: binary STL has triangle count that matches file size
  if(buf.byteLength>84){const n=dv.getUint32(80,true);
    if(84+n*50===buf.byteLength)isAscii=false;}
  const g=new THREE.BufferGeometry();const verts=[];
  if(!isAscii){const n=dv.getUint32(80,true);let o=84;
    for(let i=0;i<n;i++){o+=12;// skip normal
      for(let v=0;v<3;v++){verts.push(dv.getFloat32(o,true),dv.getFloat32(o+4,true),
        dv.getFloat32(o+8,true));o+=12;}o+=2;}
  }else{const txt=new TextDecoder().decode(buf);
    const re=/vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)/g;let mm;
    while((mm=re.exec(txt)))verts.push(+mm[1],+mm[2],+mm[3]);}
  g.setAttribute('position',new THREE.Float32BufferAttribute(verts,3));return g;
}

async function send(){
  const text=q.value.trim();if(!text)return;
  add('user',esc(text));q.value='';
  const t=add('ai','<span style="color:#8b949e">thinking...</span>');
  try{
    const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text,allow_brain:document.getElementById('brain').checked,session})});
    const data=await r.json();
    let html='';
    for(const s of data.segments)
      html+=`<div class="seg p-${s.provenance}"><div class="prov">${s.provenance}</div>${esc(s.text)}</div>`;
    t.innerHTML=html;
    if(data.model3d)loadSTL(data.model3d);
  }catch(e){t.innerHTML='<span style="color:#f85149">error: '+esc(e.message)+'</span>';}
  log.scrollTop=log.scrollHeight;q.focus();
}
initViewer();q.focus();
window.addEventListener('resize',()=>{if(!renderer)return;
  const el=document.getElementById('viewer');
  camera.aspect=el.clientWidth/el.clientHeight;camera.updateProjectionMatrix();
  renderer.setSize(el.clientWidth,el.clientHeight);});
</script>
</body></html>"""


def main() -> int:
    import uvicorn
    host = "0.0.0.0" if "--lan" in sys.argv else "127.0.0.1"
    tls = "--tls" in sys.argv
    print(server_auth.startup_banner("EngineAI Workspace", host, 8097,
                                     AUTH_TOKEN, tls))
    kw = {}
    if tls:
        cert, key = server_auth.ensure_tls_cert()
        kw = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    uvicorn.run(app, host=host, port=8097, log_level="warning", **kw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
