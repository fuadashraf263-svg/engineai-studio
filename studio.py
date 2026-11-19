#!/usr/bin/env python3
"""
studio.py - the two-panel workspace, done properly.

Left: you ask, and the agent's TOOL TRACE streams live - every lookup, every
unit conversion, every solver call, tagged with where the number came from.
Right: the 3D field view, which updates itself when a step produces geometry.

This is a new server rather than a patch to workspace_server.py, because it is
built directly on the pieces that are tested: agent_loop, viz3d, sdf_kernel,
cem, coupled. Old server stays untouched on :8097.

  python studio.py                 -> http://127.0.0.1:8099
  python studio.py --selftest      exercises every route without a browser
"""
import os, sys, json, time, queue, threading, argparse, mimetypes, re

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT  = os.path.expanduser(os.environ.get("ENGINEAI_OUT", "~/engineai/out"))
PORT = int(os.environ.get("ENGINEAI_PORT", "8099"))
HOST = os.environ.get("ENGINEAI_HOST", "127.0.0.1")     # localhost until TLS is in front

try:
    from owner import OWNER
except Exception:
    OWNER = {"name": "Fuad Ashraf", "product": "EngineAI", "email": ""}

# ------------------------------------------------------------------ page
PAGE = r"""<!doctype html><html><head><meta charset=utf-8><title>%PRODUCT% Studio</title>
<style>
:root{--bg:#0e1319;--pan:#141c25;--ln:#243447;--tx:#dbe6f2;--mut:#8aa2ba;--ac:#7fb3ff;
      --ok:#3ddc97;--warn:#ffb454;--bad:#ff6b6b}
*{box-sizing:border-box}
body{margin:0;height:100vh;display:flex;background:var(--bg);color:var(--tx);
     font:14px/1.55 system-ui}
#left{width:46%;min-width:380px;display:flex;flex-direction:column;border-right:1px solid var(--ln)}
#right{flex:1;position:relative;background:#0b1017}
h1{margin:0;padding:13px 16px;font-size:15px;border-bottom:1px solid var(--ln);color:var(--ac)}
h1 small{color:var(--mut);font-weight:400;margin-left:8px}
#feed{flex:1;overflow:auto;padding:14px 16px}
#bar{padding:12px 16px;border-top:1px solid var(--ln);display:flex;gap:8px}
input#q{flex:1;background:#0b1017;border:1px solid var(--ln);border-radius:7px;
        padding:10px 12px;color:var(--tx);font:14px system-ui}
button{background:#1b2735;color:var(--tx);border:1px solid var(--ln);border-radius:7px;
       padding:9px 15px;cursor:pointer;font:13px system-ui}
button:hover{background:#25384d} button:disabled{opacity:.45;cursor:default}
.msg{margin:12px 0}
.you{color:var(--ac);font-weight:600}
.step{margin:7px 0;padding:9px 11px;background:var(--pan);border-left:3px solid var(--ln);
      border-radius:0 6px 6px 0;font:12.5px ui-monospace}
.step .t{color:var(--ac);font-weight:700}
.step .th{color:var(--mut);font-style:italic}
.step.ok{border-left-color:var(--ok)} .step.warn{border-left-color:var(--warn)}
.step.warn .t:after{content:" ⚠"} .ans .ev:has(+ .x){}
.step.bad{border-left-color:var(--bad)}
.prov{display:inline-block;font-size:10.5px;padding:1px 6px;border-radius:3px;margin-left:6px;
      background:#1d2a38;color:var(--mut)}
.ans{margin:14px 0;padding:13px 15px;background:#12212e;border:1px solid #24435e;
     border-radius:8px}
.ans b{color:var(--ok)}
.ev{color:var(--mut);font-size:12px;margin-top:7px}
#right iframe{width:100%;height:100%;border:0;background:#fff}
#tabs button{white-space:nowrap}
#empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
       color:#4d647d;text-align:center;padding:30px;font-size:13px}
#tabs{position:absolute;top:0;left:0;right:0;padding:8px 12px;background:#0b1017ee;z-index:5;
      display:flex;gap:6px;border-bottom:1px solid var(--ln);flex-wrap:wrap}
#tabs button{padding:4px 10px;font-size:12px}
#tabs .on{background:#24435e;border-color:#3a6a94}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--ok);
     margin-right:6px;vertical-align:middle}
#welcome{padding:2px 0 8px}
.wtitle{font-size:15px;color:var(--tx);margin-bottom:4px}
.wsub{color:var(--mut);font-size:12.5px;margin-bottom:14px;max-width:52em}
.card{border:1px solid var(--ln);border-left:2px solid #2c4a68;border-radius:7px;
      padding:9px 12px;margin-bottom:8px;cursor:pointer;transition:.12s}
.card:hover{border-left-color:var(--ac);background:#18222e}
.card b{display:block;font-size:13px;color:var(--tx);font-weight:600}
.card i{display:block;font:12px ui-monospace,monospace;color:var(--ac);
        font-style:normal;margin:3px 0 5px}
.card u{display:block;text-decoration:none;color:var(--mut);font-size:12px;line-height:1.5}
.wfoot{color:#5f7791;font-size:12px;margin-top:12px}
.k{font:11.5px ui-monospace,monospace;background:#1b2735;padding:1px 5px;border-radius:3px;
   color:var(--ac)}
</style></head><body>
<div id=left>
  <h1><span class=dot></span>%PRODUCT% Studio<small>%OWNER% · verified engineering</small></h1>
  <div id=feed><div id=welcome>
    <div class=wtitle>What %PRODUCT% can do</div>
    <div class=wsub>Click any example to load it. Every number below is produced by a
      solver and tagged with its source &mdash; the model chooses the tool, it never
      does the arithmetic.</div>

    <div class=card data-q="tip deflection of a 1.2 m steel cantilever under 2.5 kN">
      <b>Solve, with the formula checked</b>
      <i>tip deflection of a 1.2 m steel cantilever under 2.5&nbsp;kN</i>
      <u>87 cited closed-form references. If the answer disagrees with the reference
         formula, you are told rather than shown a confident wrong number.</u></div>

    <div class=card data-q="teach me buckling of columns">
      <b>Explain, from the library</b>
      <i>teach me buckling of columns</i>
      <u>Retrieval over your indexed textbooks with page citations. If nothing
         relevant is found it says so instead of inventing a source.</u></div>

    <div class=card data-q="make a gyroid lattice block 30 mm with 8 mm cells">
      <b>Generate geometry a CAD kernel cannot</b>
      <i>make a gyroid lattice block 30&nbsp;mm with 8&nbsp;mm cells</i>
      <u>Implicit modelling with signed distance fields &mdash; lattices, conformal
         cooling channels, smooth blends. Appears in the 3D view on the right.</u></div>

    <div class=card data-q="stress analyse the beam under 2 kN">
      <b>Prove it survives</b>
      <i>stress analyse the beam under 2&nbsp;kN</i>
      <u>Hex mesh straight from the distance field into CalculiX. The cantilever
         case checks itself against P&middot;L&sup3;/(3EI) and reports the error.</u></div>

    <div class=card data-q="design a regeneratively cooled chamber at 4 MW/m2">
      <b>Design from requirements</b>
      <i>design a regeneratively cooled chamber at 4&nbsp;MW/m&sup2;</i>
      <u>Requirements in, sized design and printable geometry out, with the
         flow &rarr; thermal &rarr; stress chain coupled and an additive-manufacturing check.</u></div>

    <div class=card data-q="drawings of it">
      <b>Draw it and export it</b>
      <i>drawings of it</i>
      <u>Multiview and sectioned mechanical drawings, DXF, STEP, STL, plus report
         PDF, DOCX, XLSX and PPTX. Everything lands in the FILES tab.</u></div>

    <div class=wfoot>Type anything else and it will route to the closest tool.
      Ask <span class=k>what can you do</span> at any time to see this again.</div>
  </div></div>
  <div id=bar>
    <input id=q placeholder="ask..." autofocus>
    <button id=go onclick="ask()">Run</button>
  </div>
</div>
<div id=right>
  <div id=tabs></div>
  <div id=empty>the 3D view appears here when a step produces geometry</div>
  <iframe id=view style="display:none"></iframe>
</div>
<script>
const feed=document.getElementById('feed'),view=document.getElementById('view'),
      empty=document.getElementById('empty'),tabs=document.getElementById('tabs');
let views=[{url:'/browse',label:'FILES'}];
function el(h){const d=document.createElement('div');d.innerHTML=h;return d.firstElementChild;}
function addView(url,label){
  const i0=views.findIndex(v=>v.url===url);
  if(i0>=0){view.src=views[i0].url+'?t='+Date.now();show(i0);return;}
  views.push({url,label});
  tabbar(); show(views.length-1);}
function show(i){view.src=views[i].url+(views[i].url==='/browse'?'?t='+Date.now():'');
  view.style.display='block';empty.style.display='none';
  [...tabs.children].forEach((b,j)=>b.className=j===i?'on':'');}
function stepBox(s){
  const cls=s.error?'bad':(s.verdict==='BLOCK'?'warn':'ok');
  let detail='';
  if(s.summary)detail=`<div>${s.summary}</div>`;
  const prov=s.source?`<span class=prov>${s.source}</span>`:'';
  return el(`<div class="step ${cls}"><span class=t>${s.tool||'?'}</span>${prov}
    ${s.thought?`<div class=th>${s.thought}</div>`:''}${detail}</div>`);}
function bindCards(){
  document.querySelectorAll('.card').forEach(c=>c.onclick=()=>{
    document.getElementById('q').value=c.dataset.q;
    document.getElementById('q').focus();});}
bindCards();

function showWelcome(){
  const w=document.getElementById('welcome');
  if(w){w.scrollIntoView({behavior:'smooth'});return true;}
  fetch('/api/welcome').then(r=>r.text()).then(h=>{
    feed.appendChild(el('<div id=welcome>'+h+'</div>'));
    bindCards(); feed.scrollTop=feed.scrollHeight;});
  return true;}

async function ask(){
  const q=document.getElementById('q'),go=document.getElementById('go');
  const text=q.value.trim(); if(!text||go.disabled)return;
  q.value='';go.disabled=true;q.disabled=true;
  feed.appendChild(el(`<div class="msg you">${text.replace(/[<>]/g,'')}</div>`));
  if(/^\s*(what can you do|help|capabilities|examples?)\s*\??\s*$/i.test(text)){
    document.getElementById('q').value=''; showWelcome(); return;}
  feed.scrollTop=feed.scrollHeight;
  const r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},
                                 body:JSON.stringify({text})});
  const rd=r.body.getReader();const dec=new TextDecoder();let buf='';
  while(true){const {value,done}=await rd.read(); if(done)break;
    buf+=dec.decode(value,{stream:true});
    let i;
    while((i=buf.indexOf('\n\n'))>=0){
      const raw=buf.slice(0,i);buf=buf.slice(i+2);
      if(!raw.startsWith('data: '))continue;
      let ev;try{ev=JSON.parse(raw.slice(6))}catch(e){continue}
      if(ev.kind==='step'){feed.appendChild(stepBox(ev));}
      else if(ev.kind==='view'){addView(ev.url,ev.label||'view');}
      else if(ev.kind==='answer'){
        feed.appendChild(el(`<div class=ans><b>${(ev.answer||'').replace(/[<>]/g,'')}</b>
          <div class=ev>${(ev.evidence||'').replace(/[<>]/g,'')}</div></div>`));}
      else if(ev.kind==='error'){
        feed.appendChild(el(`<div class="step bad">${(ev.message||'').replace(/[<>]/g,'')}</div>`));}
      feed.scrollTop=feed.scrollHeight;}}
  go.disabled=false;q.disabled=false;q.focus();}
document.getElementById('q').addEventListener('keydown',
  e=>{if(e.key==='Enter'&&!document.getElementById('go').disabled)ask()});
function tabbar(){tabs.innerHTML=views.map((v,i)=>
  `<button onclick="show(${i})" id="tb${i}">${v.label}</button>`).join('');}
tabbar();show(0);
</script></body></html>"""

# -------------------------------------------------------------- summaries
def summarise(tool, obs):
    """One line a human can read, plus the provenance tag."""
    if not isinstance(obs, dict): return str(obs)[:160], ""
    if "error" in obs: return str(obs["error"])[:160], ""
    if tool == "lookup_formula" and not obs.get("found"):
        return obs.get("note", "not in the verified reference table"), ""
    if tool == "lookup_formula" and obs.get("candidates"):
        c = obs["candidates"][0]
        return f"{c['formula']}  [{c['unit']}]", c["source"]
    if tool == "compute":
        line = f"= {obs['value']:.6g} {obs.get('unit','')}"
        if obs.get("ASSUMED"):
            line += ("   ⚠ ASSUMED " +
                     ", ".join(f"{k}={v:g}" for k, v in sorted(obs["ASSUMED"].items())) +
                     " — not from your question")
        return line, obs.get("source", "")
    if tool == "check_units":
        w = obs.get("warnings") or []
        return (", ".join(f"{k}={v:g}" for k, v in obs.get("si_values", {}).items())
                + ("   ⚠ " + w[0] if w else "")), "SI conversion"
    if tool == "search_library" and obs.get("hits"):
        h = obs["hits"][0]
        return f"{h['book'][:46]} p.{h['page']}", "library"
    if tool == "verify_formula":
        return f"{obs.get('verdict')} — {str(obs.get('detail',''))[:90]}", obs.get("source", "")
    if tool == "visualise":
        return (f"tip {obs['tip_deflection_m']*1000:.4g} mm, "
                f"σmax {obs['sigma_max_Pa']/1e6:.4g} MPa"), "closed form"
    if tool == "list_generators":
        return f"{len(obs.get('generators', []))} generators available", ""
    if tool in ("run_generator", "make_beam_cad"):
        files = [os.path.basename(x) for x in _walk(obs)
                 if isinstance(x, str) and os.path.exists(x)]
        return (", ".join(files) if files else str(obs)[:140]), ("wrote " + str(len(files)) +
                " file(s)" if files else "")
    return json.dumps(obs, default=str)[:150], ""

def _walk(o):
    """Every string in a nested result."""
    if isinstance(o, str): yield o
    elif isinstance(o, dict):
        for v in o.values(): yield from _walk(v)
    elif isinstance(o, (list, tuple)):
        for v in o: yield from _walk(v)

def find_view(obs):
    """An HTML view if the step made one - otherwise BUILD one from any STL it wrote.
    A generator that produces geometry should light up the right panel; before this,
    it produced a STEP file and the panel stayed black."""
    if not isinstance(obs, dict): return None
    paths = [x for x in _walk(obs) if isinstance(x, str) and os.path.exists(x)]
    for p_ in paths:
        if p_.endswith(".html"): return p_
    for p_ in paths:
        if p_.lower().endswith(".stl"):
            try:
                import generators as G
                return G.REGISTRY["viewer_3d"](stl=p_, title=os.path.basename(p_))
            except Exception:
                return None
    return None

# ------------------------------------------------------------ the stream
def sse(d): return "data: " + json.dumps(d, default=str) + "\n\n"

def _log(question, route, tools, answer, evidence, files=0, seconds=0.0,
         assumed=False, unverified=False, failed=False):
    try:
        import journal
        journal.record(question, route, tools, answer, evidence, files, seconds,
                       assumed, unverified, failed)
    except Exception: pass

def stream_direct(text):
    """Deterministic path: compute, geometry, files, view. No model involved."""
    import direct, time as _t
    t0 = _t.time()
    r = direct.route(text)
    if r is None: return False
    for st in r.get("steps", []):
        yield sse({"kind": "step", "tool": st["tool"], "thought": st.get("thought", ""),
                   "summary": st.get("summary", ""), "source": st.get("source", ""),
                   "verdict": "BLOCK" if st.get("warn") else None})
    VIEWABLE = (".html", ".png", ".svg", ".jpg", ".jpeg", ".pdf")
    for f in r.get("files", []):
        if not (f and os.path.exists(f)): continue
        if not f.endswith(".html"):
            yield sse({"kind": "step", "tool": "file written", "source": "on disk",
                       "summary": os.path.basename(f) +
                                  f"   ({os.path.getsize(f)/1024:.0f} KB)"})
        if f.lower().endswith(VIEWABLE):        # 2D images and 3D views both go to the panel
            lbl = os.path.basename(f)
            lbl = ("3D " if f.endswith(".html") else "2D ") + \
                  os.path.splitext(lbl)[0].replace("sandbox_", "").replace("_", " ")[:20]
            yield sse({"kind": "view", "url": "/files/" + os.path.basename(f), "label": lbl})
    v = r.get("view")
    if v and os.path.exists(v) and v not in r.get("files", []):
        yield sse({"kind": "view", "url": "/files/" + os.path.basename(v), "label": "3D view"})
    yield sse({"kind": "answer", "answer": r.get("answer", ""),
               "evidence": r.get("evidence", "")})
    _log(text, "direct", [st["tool"] for st in r.get("steps", [])],
         r.get("answer", ""), r.get("evidence", ""),
         files=len(r.get("files", [])), seconds=_t.time()-t0,
         assumed="assumed" in str(r.get("evidence", "")).lower(),
         failed=not r.get("ok", True))
    return True

def stream_answer(text, backend=None):
    """Deterministic routing first; the model only handles what it must."""
    import agent_loop as A
    # ENGINEAI_ROUTING = direct (default) | model | auto
    #   direct : engineering questions are computed, never routed through a model
    #   model  : everything goes through the API model and its tools
    routing = os.environ.get("ENGINEAI_ROUTING", "direct").lower()
    if routing != "model":
        try:
            handled = False
            for chunk in stream_direct(text):
                handled = True
                yield chunk
            if handled: return
        except Exception as e:
            yield sse({"kind": "step", "tool": "direct routing",
                       "summary": f"failed ({type(e).__name__}: {e}) — handing to the model",
                       "error": True})
    bk = backend or A.pick_backend()
    if bk is None:
        yield sse({"kind": "error", "message": A.why_no_backend()}); return
    system = A.SYSTEM.replace("%TOOLS%", A.tool_manifest())
    A.USER_GIVEN.clear(); A.USER_GIVEN.update(A.parse_quantities(text))
    guard = A.RunGuard(text)
    if A.USER_GIVEN:
        yield sse({"kind": "step", "tool": "read the question", "source": "your words",
                   "summary": ", ".join(f"{k} = {v:g}" for k, v in sorted(A.USER_GIVEN.items())),
                   "thought": "these are the only values you actually gave"})
    msgs = [{"role": "user", "content": text}]
    bad, used, t_start = 0, [], time.time()
    for n in range(1, 9):
        try: raw = bk.complete(system, msgs)
        except Exception as e:
            yield sse({"kind": "error", "message": f"model error: {type(e).__name__}: {e}"}); return
        call = A.extract_json(raw)
        if not call or "tool" not in call:
            bad += 1
            msgs.append({"role": "user", "content":
                         'INVALID. Output only:\n'
                         '{"thought":"...","tool":"lookup_formula","args":{"question":"..."}}'})
            yield sse({"kind": "step", "tool": "(reprompt)",
                       "thought": f"the model did not return a tool call ({bad}/2)",
                       "error": True})
            if bad >= 2:                       # do not let a weak model dead-end the product
                fb = A.fallback(text)
                if fb:
                    yield sse({"kind": "step", "tool": "rule-based routing",
                               "thought": "the model would not cooperate; routing directly",
                               "summary": "matched a known problem type", "source": "deterministic"})
                    for st in fb.get("steps", []):
                        sm, src = summarise(st["tool"], st["observation"])
                        yield sse({"kind": "step", "tool": st["tool"],
                                   "thought": st.get("thought", ""), "summary": sm, "source": src})
                    if fb["answer"]: yield sse({"kind": "answer", **fb["answer"]})
                    else: yield sse({"kind": "error", "message": fb.get("error", "")})
                else:
                    yield sse({"kind": "error", "message":
                               "The model would not produce a tool call and this is not a "
                               "recognised problem type. Try phrasing it as a standard case, "
                               "e.g. 'tip deflection of a 3 m steel cantilever, 50 x 100 mm "
                               "section, under 2.5 kN'."})
                return
            continue
        name, args = call["tool"], call.get("args") or {}
        rep = guard.check_repeat(name, args) if name != "final_answer" else None
        if rep: obs = rep
        else:
            try: obs = A.TOOLS[name](**args) if name in A.TOOLS else {"error": f"no tool '{name}'"}
            except Exception as e: obs = {"error": f"{type(e).__name__}: {e}"}
        guard.note(name, obs); used.append(name)
        summary, source = summarise(name, obs)
        assumed = obs.get("ASSUMED") if isinstance(obs, dict) else None
        yield sse({"kind": "step", "tool": name, "thought": call.get("thought", ""),
                   "summary": summary, "source": source,
                   "error": isinstance(obs, dict) and "error" in obs,
                   "verdict": "BLOCK" if assumed else
                              (obs.get("verdict") if isinstance(obs, dict) else None)})
        v = find_view(obs)
        if v: yield sse({"kind": "view", "url": "/files/" + os.path.basename(v),
                         "label": os.path.basename(v).replace(".html", "")[:22]})
        if name == "final_answer":
            fin = guard.vet_answer(obs)
            _log(text, "model:" + getattr(bk, "name", "?"), used,
                 fin.get("answer", ""), fin.get("evidence", ""),
                 seconds=time.time()-t_start,
                 assumed=bool(guard.assumed),
                 unverified="UNVERIFIED" in str(fin.get("evidence", "")))
            yield sse({"kind": "answer", **fin}); return
        msgs.append({"role": "assistant", "content": json.dumps(call)})
        msgs.append({"role": "user", "content": "OBSERVATION: " + json.dumps(obs, default=str)[:1800]})
    _log(text, "model:" + getattr(bk, "name", "?"), used, "", "",
         seconds=time.time()-t_start, failed=True)
    yield sse({"kind": "error", "message": "ran out of steps without a final answer"})

# ---------------------------------------------------------------- server
REQUIRE_AUTH = os.environ.get("ENGINEAI_REQUIRE_AUTH", "0") == "1"

def check_auth(request):
    """Gate every route on the owner token.

    A header-only token cannot be typed into a browser, which locks you out of
    your own app. So it accepts, in order: a cookie set by the login page, a
    ?token= parameter, or the x-engineai-token header for scripts.
    """
    if not REQUIRE_AUTH: return True
    tok = (request.cookies.get("engineai_token")
           or request.query_params.get("token", "")
           or request.headers.get("x-engineai-token", ""))
    try:
        from owner import require_owner
        require_owner(tok)
        return True
    except Exception:
        return False

def _log_note(msg):
    try:
        with open(os.path.join(OUT, "..", "audit.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} NOTE {msg}\n")
    except Exception:
        pass


def housekeeping():
    """Rotate logs and prune out/ once at start. Cheap, and it is the whole
    defence against the disk filling silently while you sleep."""
    try:
        import limits as LIM
    except Exception:
        return []
    notes = LIM.install_rotation(
        os.path.join(os.path.dirname(OUT.rstrip("/")), "audit.log"),
        os.path.join(os.path.dirname(OUT.rstrip("/")), "journal.log"))
    n = LIM.prune_out(OUT)
    if n: notes.append(n)
    return notes


def make_app():
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, JSONResponse
    try:
        import limits as LIM
    except Exception:
        LIM = None
    app = FastAPI(title="EngineAI Studio")
    try:
        from owner import RateLimiter
        _LIMIT = RateLimiter(per_min=60, burst=10)
    except Exception:
        class _N:
            def allow(self, w="local"): return True, "ok"
        _LIMIT = _N()

    LOGIN = """<!doctype html><meta charset=utf-8><title>EngineAI</title>
<style>body{font:15px system-ui;background:#0e1319;color:#dbe6f2;display:flex;
 align-items:center;justify-content:center;height:100vh;margin:0}
form{background:#141c25;padding:28px 32px;border:1px solid #243447;border-radius:10px;
 min-width:330px}
h1{margin:0 0 4px;font-size:17px;color:#7fb3ff}
p{color:#8aa2ba;font-size:13px;margin:0 0 18px}
input{width:100%;padding:10px 12px;background:#0b1017;border:1px solid #243447;
 border-radius:7px;color:#dbe6f2;font:14px system-ui;box-sizing:border-box}
button{margin-top:12px;width:100%;padding:10px;background:#1b6ca8;color:#fff;border:0;
 border-radius:7px;font:14px system-ui;cursor:pointer}
button:hover{background:#2280c4}
.e{color:#ff6b6b;font-size:13px;margin-top:10px}
.n{color:#5d7891;font-size:12px;margin-top:16px;line-height:1.5}</style>
<form method=post action=/login>
 <h1>EngineAI</h1><p>%OWNER%</p>
 <input type=password name=password placeholder="owner password" autofocus>
 <button>Enter</button>
 %ERR%
 <div class=n>Set it with <code>python owner.py setpw</code>.<br>
 To run without a password on localhost, start with <code>python studio.py</code>
 and no ENGINEAI_REQUIRE_AUTH.</div>
</form>"""

    @app.get("/login", response_class=HTMLResponse)
    def login_page(bad: int = 0):
        return (LOGIN.replace("%OWNER%", OWNER.get("name", ""))
                     .replace("%ERR%", '<div class=e>wrong password</div>' if bad else ""))

    @app.post("/login")
    async def login_post(request: Request):
        from fastapi.responses import RedirectResponse
        # parse the form by hand: request.form() needs python-multipart, and a
        # login page should not require an extra install
        import urllib.parse as _up
        raw = (await request.body()).decode(errors="ignore")
        pw = _up.parse_qs(raw).get("password", [""])[0]
        try:
            from owner import check_password, issue_token
            if check_password(pw):
                r = RedirectResponse("/", status_code=303)
                r.set_cookie("engineai_token", issue_token(hours=12),
                             httponly=True, samesite="lax", max_age=12*3600)
                return r
        except Exception:
            pass
        return RedirectResponse("/login?bad=1", status_code=303)

    @app.get("/logout")
    def logout():
        from fastapi.responses import RedirectResponse
        r = RedirectResponse("/login", status_code=303)
        r.delete_cookie("engineai_token")
        return r

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        if not check_auth(request):
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/login", status_code=303)
        return PAGE.replace("%PRODUCT%", OWNER.get("product", "EngineAI")) \
                   .replace("%OWNER%", OWNER.get("name", ""))

    @app.get("/api/welcome", response_class=HTMLResponse)
    def welcome(request: Request):
        """The onboarding cards, servable on demand so 'what can you do' works
        after the feed has scrolled past the first-run block."""
        if not check_auth(request): raise HTTPException(401, "owner token required")
        import re as _re
        m = _re.search(r"<div id=welcome>(.*?)</div></div>\n  <div id=bar>", PAGE, _re.S)
        body = m.group(1) if m else "<div class=wtitle>examples unavailable</div>"
        return body.replace("%PRODUCT%", OWNER.get("product", "EngineAI"))

    @app.get("/api/health")
    def health(request: Request):
        if not check_auth(request):
            raise HTTPException(401, "owner token required")
        import agent_loop as A
        b = A.pick_backend()
        return {"ok": True, "owner": OWNER.get("name"), "backend": b.name if b else None,
                "tools": list(A.TOOLS), "out": OUT}

    @app.post("/api/ask")
    async def api_ask(req: Request):
        if not check_auth(req):
            raise HTTPException(401, "owner token required")
        ok, why = _LIMIT.allow(req.client.host if req.client else "local")
        if not ok: raise HTTPException(429, why)
        try:
            body = await req.json()
        except Exception:
            raise HTTPException(413, "body too large or not valid JSON")
        raw = body.get("text") or ""
        if LIM:
            text, note = LIM.clamp_question(raw)
            if note: _log_note(note)
        else:
            text = raw.strip()[:4000]
        if not text: raise HTTPException(400, "empty question")
        return StreamingResponse(stream_answer(text), media_type="text/event-stream")

    @app.get("/api/worksheet")
    def api_worksheet(
        request: Request,
        question: str,
        level: str = "medium",
        format: str = "html",
        values: str = "",
    ):
        if not check_auth(request):
            raise HTTPException(401, "owner token required")
        try:
            import worksheet as W
        except Exception as e:
            raise HTTPException(500, f"worksheet feature unavailable: {e}")
        try:
            from units import parse_value
        except Exception:
            parse_value = None

        parsed = {}
        if values:
            try:
                import ast
                parsed.update(ast.literal_eval(values))
            except Exception:
                try:
                    parsed.update(json.loads(values))
                except Exception:
                    raise HTTPException(400, "values must be JSON or mapping-like")
        for key, raw in request.query_params.multi_items():
            if key in {"question", "level", "format", "values"}:
                continue
            try:
                parsed[key] = parse_value(raw, key)[0] if parse_value else float(raw)
            except Exception:
                parsed[key] = None
        if any(v is None for v in parsed.values()):
            raise HTTPException(400, "all custom values must be numeric with valid units")
        try:
            ws = W.make(question, parsed, level=level)
        except Exception as e:
            raise HTTPException(400, str(e))
        if format == "text":
            return JSONResponse({"ok": True, "question": question, "format": "text",
                                "text": W.render_text(ws, key=False),
                                "path": os.path.join(OUT, "worksheet_" + re.sub(r"\W+", "_",
                                                                               ws["question"].lower())[:38] + ".html")})
        # default: html file for immediate review in browser
        path = W.render_html(ws)
        if format == "pdf":
            try:
                path = W.render_pdf(ws)
            except Exception as e:
                raise HTTPException(500, f"worksheet pdf failed: {e}")
        return FileResponse(path)

    @app.get("/api/course-set")
    def api_course_set(request: Request, n: int = 6, seed: int | None = None,
                       format: str = "html", topic: str | None = None):
        if not check_auth(request):
            raise HTTPException(401, "owner token required")
        try:
            import course as C
        except Exception as e:
            raise HTTPException(500, f"course feature unavailable: {e}")
        if format not in {"html", "text", "json"}:
            raise HTTPException(400, "format must be html, text or json")
        topic = (topic or "").strip().lower()
        allowed_topics = None
        try:
            allowed_topics = set(C.references().keys())
        except Exception:
            allowed_topics = set()
        if topic:
            by_topics = [topic]
            if allowed_topics and topic not in allowed_topics:
                raise HTTPException(400, f"invalid topic: {topic}")
            probs = C.build_set(n, by_topics, seed=seed)
        else:
            probs = C.build_set(n, seed=seed)
        if format == "text":
            out = []
            for p in probs:
                out.append(f"{p['key'].replace('_',' ')} — {p['display']} {p['unit']}")
            return JSONResponse({"ok": True, "n": len(probs), "items": out})
        if format == "json":
            payload = [dict(key=p["key"], given=p["values"], answer=p["display"], unit=p["unit"])
                       for p in probs]
            return JSONResponse({"ok": True, "n": len(probs), "items": payload})
        return FileResponse(C.render_set_html(probs))

    @app.get("/browse", response_class=HTMLResponse)
    def browse():
        import glob, html as H, datetime as DT
        BS = chr(92)
        WSLPATH = H.escape(BS*2 + "wsl.localhost" + BS + "Ubuntu" + OUT.replace("/", BS))
        rows = []
        for f in sorted(glob.glob(os.path.join(OUT, "*")), key=os.path.getmtime, reverse=True):
            if os.path.isdir(f) or os.path.basename(f).startswith("."): continue
            n = os.path.basename(f); kb = os.path.getsize(f)/1024
            when = DT.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%H:%M:%S")
            ext = os.path.splitext(n)[1].lower().lstrip(".") or "file"
            view = (f'<a href="/files/{H.escape(n)}" target="_blank">open</a>'
                    if ext in ("html", "png", "svg", "jpg", "pdf", "md", "csv", "txt") else "")
            rows.append(f"<tr><td class=e>{ext}</td><td><b>{H.escape(n)}</b></td>"
                        f"<td class=r>{kb:,.0f} KB</td><td class=r>{when}</td>"
                        f"<td>{view}</td>"
                        f'<td><a class=dl href="/files/{H.escape(n)}?dl=1">download</a></td></tr>')
        return f"""<!doctype html><meta charset=utf-8><title>files</title>
<style>body{{font:13px system-ui;margin:0;padding:14px;background:#0e1319;color:#dbe6f2}}
h2{{margin:0 0 10px;font-size:15px;color:#7fb3ff}}
table{{width:100%;border-collapse:collapse}}
td{{padding:6px 8px;border-bottom:1px solid #1d2a38}}
.e{{color:#6f8aa6;text-transform:uppercase;font:11px ui-monospace}}
.r{{text-align:right;color:#8aa2ba;font:12px ui-monospace}}
a{{color:#7fb3ff;text-decoration:none}} a:hover{{text-decoration:underline}}
.dl{{color:#3ddc97}} .n{{color:#6f8aa6;margin-top:12px}}</style>
<h2>{len(rows)} file(s) — {H.escape(OUT)}</h2>
<table>{''.join(rows) or '<tr><td>nothing generated yet</td></tr>'}</table>
<p class=n>Files are on disk at the path above. "download" saves a copy;
Windows can reach them at <code>{WSLPATH}</code></p>"""

    @app.get("/files/{name}")
    def files(name: str, dl: int = 0, request: Request = None):
        if request is not None and not check_auth(request):
            raise HTTPException(401, "owner token required")
        # confine to OUT: no traversal
        p = os.path.realpath(os.path.join(OUT, os.path.basename(name)))
        if not p.startswith(os.path.realpath(OUT)) or not os.path.exists(p):
            raise HTTPException(404, "not found")
        mt = mimetypes.guess_type(p)[0] or "application/octet-stream"
        if dl:
            return FileResponse(p, media_type="application/octet-stream",
                                filename=os.path.basename(p))
        return FileResponse(p, media_type=mt)

    if LIM is not None:
        app.add_middleware(LIM.BodyLimit)

    return app

# -------------------------------------------------------------- selftest
def selftest():
    ok = True
    print("\nSTUDIO SELFTEST")
    try:
        from fastapi.testclient import TestClient
    except Exception as e:
        print("  need: pip install httpx    (", e, ")"); return False
    os.makedirs(OUT, exist_ok=True)
    app = make_app(); c = TestClient(app)
    r = c.get("/")
    good = r.status_code == 200 and "Studio" in r.text and "id=view" in r.text
    print(f"  {'ok  ' if good else 'FAIL'} page serves, two panels present"); ok &= good
    r = c.get("/api/health").json()
    print(f"  {'ok  ' if r.get('ok') else 'FAIL'} health: {len(r.get('tools',[]))} tools, "
          f"backend {r.get('backend')}")
    ok &= bool(r.get("ok")) and len(r.get("tools", [])) >= 8
    # path traversal must be refused
    r = c.get("/files/..%2f..%2fetc%2fpasswd")
    good = r.status_code == 404
    print(f"  {'ok  ' if good else 'FAIL'} path traversal refused ({r.status_code})"); ok &= good
    open(os.path.join(OUT, "probe.html"), "w").write("<b>probe</b>")
    good = c.get("/files/probe.html").status_code == 200
    print(f"  {'ok  ' if good else 'FAIL'} serves a real file from the output folder"); ok &= good
    # the stream, driven by a scripted model so it does not need ollama
    import agent_loop as A
    script = [
     '{"thought":"find the formula","tool":"lookup_formula","args":{"question":"cantilever tip deflection point load free end"}}',
     '{"thought":"convert","tool":"check_units","args":{"values":{"L":"1200mm","P":"2.5kN"}}}',
     '{"thought":"compute","tool":"compute","args":{"key":"cantilever_point_tip","values":{"P":2500,"L":1.2,"E":200000000000,"I":0.000008333}}}',
     '{"thought":"show it","tool":"visualise","args":{"P":2500,"L":1.2,"E":200000000000,"b":0.04,"h":0.08}}',
     '{"thought":"done","tool":"final_answer","args":{"answer":"0.864 mm","evidence":"Roark Table 8.1 case 1a"}}']
    events = [json.loads(l[6:]) for l in
              "".join(stream_answer("test", A.ScriptedBackend(script))).split("\n\n") if l.startswith("data: ")]
    kinds = [e["kind"] for e in events]
    tools = [e.get("tool") for e in events if e["kind"] == "step"]
    good = tools[:3] == ["lookup_formula", "check_units", "compute"]
    print(f"  {'ok  ' if good else 'FAIL'} stream emits steps in order: {tools}"); ok &= good
    good = "view" in kinds
    v = next((e for e in events if e["kind"] == "view"), {})
    print(f"  {'ok  ' if good else 'FAIL'} 3D view pushed to the right panel: {v.get('url')}")
    ok &= good
    good = kinds[-1] == "answer" and events[-1].get("answer") == "0.864 mm"
    print(f"  {'ok  ' if good else 'FAIL'} final answer delivered: {events[-1].get('answer')}")
    ok &= good
    comp = next(e for e in events if e.get("tool") == "compute")
    good = ("0.000864" in comp["summary"]) and bool(comp["source"])
    src = str(comp["source"][:40]).encode("ascii", "replace").decode("ascii")
    summary = str(comp["summary"]).encode("ascii", "replace").decode("ascii")
    print(f"  {'ok  ' if good else 'FAIL'} compute step carries value + provenance: "
          f"{summary}  [{src}]")
    ok &= good
    units = next(e for e in events if e.get("tool") == "check_units")
    print(f"  {'ok  ' if units['summary'] else 'FAIL'} units summarised: "
          f"{str(units['summary']).encode('ascii','replace').decode('ascii')}")
    # --- onboarding + limits ------------------------------------------
    def check(cond, msg):
        nonlocal_ok[0] &= bool(cond)
        print(f"  {'ok  ' if cond else 'FAIL'} {msg}")
    nonlocal_ok = [ok]
    check("id=welcome" in PAGE and PAGE.count("class=card") >= 6,
          f"onboarding: {PAGE.count('class=card')} capability cards in the page")
    check("bindCards()" in PAGE and "data-q=" in PAGE,
          "onboarding: cards are click-to-fill")
    check("what can you do" in PAGE,
          "onboarding: 'what can you do' reopens the examples")
    try:
        import limits as _L
        check(_L.BodyLimit(None).cap_for("/api/ask") == _L.MAX_BODY, "limits: body cap resolves")
        t, n = _L.clamp_question("z" * 99999)
        check(len(t) == _L.MAX_QUESTION and n, "limits: question clamp active")
        check(callable(_L.rotate) and callable(_L.prune_out), "limits: housekeeping available")
    except ImportError:
        check(False, "limits.py not importable")
    r = c.get("/api/welcome")
    check(r.status_code == 200 and "card" in r.text, f"/api/welcome ({r.status_code})")
    ok &= nonlocal_ok[0]

    print("\nstudio selftest:", "PASSED" if ok else "FAILED")
    return ok

def main():
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--host", default=HOST)
    a = ap.parse_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    import uvicorn
    for n in housekeeping():
        print(f"  housekeeping: {n}")
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"\n  !! binding to {a.host}, not localhost."
              f"\n  !! Do not do this without TLS in front and ENGINEAI_REQUIRE_AUTH=1.\n")
    print(f"\n  {OWNER.get('product','EngineAI')} Studio — http://{a.host}:{a.port}")
    print(f"  owner {OWNER.get('name')}   outputs {OUT}")
    print(f"  auth: {'REQUIRED (x-engineai-token)' if REQUIRE_AUTH else 'off — localhost only'}")
    print("  bound to localhost only. Put TLS in front before exposing it.")
    print("  turn auth on with:  ENGINEAI_REQUIRE_AUTH=1 python studio.py\n")
    uvicorn.run(make_app(), host=a.host, port=a.port, log_level="warning")

if __name__ == "__main__":
    main()
