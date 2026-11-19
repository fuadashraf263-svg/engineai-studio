#!/usr/bin/env python3
"""sandbox_server.py - EngineAI's live sandbox, on localhost only.

One page over the capabilities that actually exist and are tested, rather than
a demo of capabilities that would be nice. Five panels:

  ASK       chat.respond with the BASIS shown, never hidden. An answer the
            model produced and an answer a solver produced look different on
            screen, because conflating them is the whole failure mode this
            project exists to avoid.
  LIBRARY   the 264 verified laws, searchable, with validity ranges and the
            traps - the sources the Ask panel actually reads from.
  CAD       real parametric solids through OpenCASCADE, exported to STEP and
            STL, with the volume cross-checked against the closed-form volume
            before the file is offered for download.
  RESEARCH  paste a dataset, get a law discovered and then ATTACKED. The
            verdict can be REFUTED, and it is shown as prominently as SETTLED.
  STATUS    what is installed, what is proven, what the model is doing, and
            how the question-bank run is progressing.

LOCALHOST ONLY, DELIBERATELY. The bind address is 127.0.0.1 and there is no
--lan flag. Binding wider needs an explicit environment variable AND a
non-loopback --host, so it cannot happen by tab-completion. The user asked for
local first; a convenience flag is exactly how "local first" becomes "exposed
by accident".

AUTH IS STILL ON. Loopback is not a security boundary on a shared machine -
any process and any page in the browser can reach 127.0.0.1. The same token
middleware as the other three servers applies here, with /healthz public so a
liveness check needs no secret.

NO PAID API. This page never calls a metered model. Ask goes to chat.respond,
which uses the local Ollama model or nothing at all.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out" / "sandbox"
PORT = 8098

# Imported at MODULE level, not inside build_app, and the reason is subtle
# enough to be worth writing down. `from __future__ import annotations` turns
# every annotation into a string, and FastAPI resolves those strings against
# the function's __globals__. A `Request` imported inside build_app is a local
# name, invisible there, so FastAPI could not tell the parameter was the
# request object and treated it as a missing QUERY parameter - every POST on
# this page returned 422 with "loc: query, request". The endpoints were fine;
# the annotation could not be looked up.
try:
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
except ImportError:                       # importable without the web stack
    FastAPI = Request = None
    HTMLResponse = JSONResponse = FileResponse = None

# --------------------------------------------------------------------------
# capability probes - every one of these is allowed to fail, and the page then
# says the capability is missing instead of the request exploding later.


def _try(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def library_stats() -> dict:
    import research_knowledge as rk
    disc = {}
    for law in rk.LAWS:
        d = getattr(law, "discipline", "other")
        disc[d] = disc.get(d, 0) + 1
    return {"laws": len(rk.LAWS), "disciplines": dict(sorted(disc.items()))}


def law_json(law) -> dict:
    """A Law as the page needs it, tolerant of fields being added later."""
    out = {}
    for f in getattr(law, "__dataclass_fields__", {}):
        v = getattr(law, f, None)
        out[f] = v if isinstance(v, (str, int, float, list, dict, type(None))) else str(v)
    out.setdefault("name", str(law))
    return out


def bank_progress() -> dict:
    try:
        import question_bank as qb
        rows = qb.load_results()
        bank = qb.load_bank()
    except Exception as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}"}
    if not rows:
        return {"available": True, "answered": 0, "total": len(bank)}
    from collections import Counter
    c = Counter(r["outcome"] for r in rows)
    hits = sum(1 for r in rows if r.get("retrieved"))
    secs = sum(r.get("seconds", 0) for r in rows)
    return {"available": True, "answered": len(rows), "total": len(bank),
            "counts": dict(c),
            "explained_rate": round(c["EXPLAINED"] / len(rows), 3),
            "library_hit_rate": round(hits / len(rows), 3),
            "seconds_per_question": round(secs / len(rows), 1),
            "last": rows[-1].get("topic")}


def model_status() -> dict:
    try:
        import model_harness as mh
        return {"ollama": mh.OLLAMA, "installed": mh.installed(),
                "resident": mh.loaded(),
                "selected": os.environ.get("ENGINEAI_MODEL",
                                           "qwen2.5:7b-instruct-q4_K_M")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


SOLVERS = [("CalculiX", "ccx"), ("Elmer", "ElmerSolver"), ("ngspice", "ngspice"),
           ("gmsh", "gmsh"), ("OpenFOAM", "simpleFoam")]
PYMODS = ["cadquery", "gpaw", "chgnet", "pymatgen", "ase", "phonopy",
          "numpy", "scipy", "ezdxf"]


def capabilities() -> dict:
    import shutil
    import importlib.util
    return {
        "solvers": {n: bool(shutil.which(exe)) for n, exe in SOLVERS},
        "python": {m: importlib.util.find_spec(m) is not None for m in PYMODS},
    }


# --------------------------------------------------------------------------
def build_app():
    import server_auth
    if FastAPI is None:
        raise RuntimeError("fastapi is not installed in this interpreter - "
                           "run through run_in_eng.sh")

    app = FastAPI(title="EngineAI Sandbox", docs_url=None, redoc_url=None)
    token = server_auth.install(app, service="EngineAI Sandbox")
    app.state.token = token
    OUT.mkdir(parents=True, exist_ok=True)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(PAGE)

    @app.get("/api/status")
    def status():
        return {
            "library": _try(library_stats, {"laws": 0, "disciplines": {}}),
            "bank": bank_progress(),
            "model": model_status(),
            "capabilities": _try(capabilities, {}),
            "out_dir": str(OUT),
            "bound": "127.0.0.1 (local only)",
        }

    @app.post("/api/ask")
    async def ask(request: Request):
        body = await request.json()
        text = (body.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "empty question"}, status_code=400)

        import chat
        t0 = time.time()
        try:
            r = chat.respond(text, model_fn=None)
        except Exception as e:
            return JSONResponse(
                {"error": f"{type(e).__name__}: {e}",
                 "trace": traceback.format_exc()[-1200:]}, status_code=500)
        dt = time.time() - t0

        sources = []
        try:
            import understand
            sources = [law_json(h) for h in understand.retrieve(text, k=4)]
        except Exception:
            pass

        return {"basis": r.get("basis"), "answer": r.get("answer"),
                "seconds": round(dt, 2), "sources": sources,
                "needs": r.get("needs"), "note": r.get("note"),
                "extra": {k: v for k, v in r.items()
                          if k not in ("basis", "answer", "needs", "note")
                          and isinstance(v, (str, int, float, bool))}}

    @app.get("/api/library")
    def library(q: str = "", limit: int = 40):
        import research_knowledge as rk
        laws = rk.search(q) if q else rk.LAWS[:limit]
        return {"n": len(laws), "laws": [law_json(l) for l in laws[:limit]],
                "total": len(rk.LAWS)}

    @app.get("/api/parts")
    def parts():
        # cad_parts.PARTS is the authority: builder, volume function, defaults.
        # Reading the defaults off the function signature instead would drift
        # the moment the two disagree.
        import cad_parts
        return {"parts": {name: dict(defaults)
                          for name, (_, _, defaults) in cad_parts.PARTS.items()}}

    @app.post("/api/cad")
    async def cad(request: Request):
        body = await request.json()
        name = body.get("part", "mounting_plate")
        params = {k: float(v) for k, v in (body.get("params") or {}).items()}
        import cad_parts

        if name not in cad_parts.PARTS:
            return JSONResponse(
                {"error": f"unknown part {name}. Known: "
                          f"{', '.join(cad_parts.PARTS)}"}, status_code=400)
        try:
            wp, used = cad_parts.build(name, **params)   # build returns a pair
            stem = OUT / f"{name}_{int(time.time())}"
            files = cad_parts.export(wp, str(stem))
        except Exception as e:
            return JSONResponse({"error": f"{type(e).__name__}: {e}"},
                                status_code=500)

        # The check that makes this more than a file dump: the solid's own
        # volume against the closed-form volume for the same parameters. A
        # geometry kernel that silently produced the wrong shape fails here,
        # on screen, before anyone downloads the file.
        _, vol_fn, _ = cad_parts.PARTS[name]
        try:
            want = vol_fn(**used)
            got = cad_parts._volume(wp)
            rel = abs(got - want) / want
            check = {"analytic_mm3": round(want, 4), "solid_mm3": round(got, 4),
                     "error_pct": float(f"{rel * 100:.3g}"), "agrees": rel < 1e-4}
        except Exception as e:
            check = {"error": f"{type(e).__name__}: {e}"}
        params = used

        listed = []
        for f in (files if isinstance(files, (list, tuple)) else [files]):
            p = Path(str(f))
            if p.exists():
                listed.append({"name": p.name, "bytes": p.stat().st_size})
        return {"part": name, "params": params, "files": listed,
                "volume_check": check}

    @app.get("/api/file/{name}")
    def get_file(name: str):
        # Basename only. A download endpoint that accepts a path is a path
        # traversal, loopback or not.
        p = OUT / Path(name).name
        if not p.exists() or not p.is_file():
            return JSONResponse({"error": "no such file"}, status_code=404)
        return FileResponse(str(p), filename=p.name,
                            media_type="application/octet-stream")

    @app.post("/api/research")
    async def research_run(request: Request):
        """Discover a law from pasted data, then attack it.

        The attack is the point. A discovery panel that only reports the fit
        is a fabrication machine with a progress bar.
        """
        body = await request.json()
        csv_text = (body.get("csv") or "").strip()
        target = (body.get("target") or "").strip()
        if not csv_text:
            return JSONResponse({"error": "no data"}, status_code=400)

        import csv as _csv
        rows = list(_csv.DictReader(io.StringIO(csv_text)))
        # 30 is the engine's own floor for a log-space fit, not a number I
        # chose for the page. Accepting fewer used to return REJECTED with
        # fit_failed, which on screen reads as "your law is wrong" when the
        # truth is "you did not give it enough data" - two very different
        # things to tell an engineer.
        if len(rows) < 30:
            return JSONResponse(
                {"error": f"only {len(rows)} rows. The discovery engine needs "
                          "at least 30 strictly-positive rows for a log-space "
                          "fit. Fewer is refused here rather than run, because "
                          "a failed fit reported as REJECTED would look like a "
                          "verdict on your data instead of on its size."},
                status_code=400)
        cols = list(rows[0].keys())
        target = target or cols[-1]
        if target not in cols:
            return JSONResponse({"error": f"target {target} not in {cols}"},
                                status_code=400)

        try:
            data = {c: [float(r[c]) for r in rows] for c in cols}
        except Exception as e:
            return JSONResponse({"error": f"non-numeric column: {e}"},
                                status_code=400)

        import research
        try:
            # falsify() re-attacks the Finding, which already carries its own
            # data and target - it takes neither.
            f = research.discover_law(data, target=target)
            research.falsify(f)
            f.settle()
        except Exception as e:
            return JSONResponse({"error": f"{type(e).__name__}: {e}",
                                 "trace": traceback.format_exc()[-1200:]},
                                status_code=500)

        return {"expression": f.claim or None,   # Finding calls it `claim`
                "verdict": f.verdict,
                "question": f.question,
                "method": f.method,
                "metrics": {k: v for k, v in (getattr(f, "metrics", {}) or {}).items()
                            if isinstance(v, (str, int, float, bool))},
                "attacks": [{"name": a.name, "passed": bool(a.passed),
                             "detail": str(getattr(a, "detail", ""))[:300]}
                            for a in getattr(f, "attacks", [])],
                "notes": [str(n) for n in getattr(f, "notes", [])][:12],
                "caveat": "Discovered from the data supplied and attacked with "
                          "the checks listed. It still needs real validation - "
                          "an AI can be wrong, and a law that survives seven "
                          "attacks has survived seven attacks, not been proven."}

    @app.get("/api/bank")
    def bank():
        return bank_progress()

    return app


# --------------------------------------------------------------------------
PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EngineAI Sandbox</title>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --line:#30363d; --ink:#e6edf3;
    --muted:#8b949e; --accent:#58a6ff;
    --computed:#3fb950; --explained:#bc8cff; --reference:#58a6ff;
    --clarify:#d29922; --gap:#f85149; --none:#6e7681;
  }
  *{box-sizing:border-box}
  body{margin:0;font:14.5px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;
       background:var(--bg);color:var(--ink);min-height:100vh}
  header{padding:12px 20px;border-bottom:1px solid var(--line);
         display:flex;align-items:center;gap:14px;flex-wrap:wrap;
         position:sticky;top:0;background:var(--bg);z-index:5}
  header h1{font-size:15px;margin:0;font-weight:650;letter-spacing:.2px}
  .pill{font-size:11px;color:var(--muted);border:1px solid var(--line);
        padding:2px 8px;border-radius:999px}
  .pill.ok{color:var(--computed);border-color:#2ea04355}
  nav{display:flex;gap:4px;margin-left:auto;flex-wrap:wrap}
  nav button{background:transparent;border:1px solid transparent;color:var(--muted);
             padding:6px 12px;border-radius:7px;cursor:pointer;font-size:13px}
  nav button:hover{color:var(--ink);background:#ffffff08}
  nav button.on{color:var(--ink);background:var(--panel);border-color:var(--line)}
  main{max-width:1000px;margin:0 auto;padding:20px}
  section{display:none} section.on{display:block}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
        padding:16px;margin-bottom:14px}
  h2{font-size:14px;margin:0 0 4px;font-weight:650}
  .sub{color:var(--muted);font-size:12.5px;margin-bottom:12px}
  textarea,input,select{width:100%;background:#0d1117;color:var(--ink);
       border:1px solid var(--line);border-radius:8px;padding:10px 12px;
       font:inherit;outline:none}
  textarea:focus,input:focus{border-color:var(--accent)}
  textarea{resize:vertical;min-height:70px;font-family:ui-monospace,Consolas,monospace;
           font-size:13px}
  button.go{background:var(--accent);color:#04121f;border:0;border-radius:8px;
       padding:9px 18px;font-weight:600;cursor:pointer;font-size:13.5px}
  button.go:disabled{opacity:.5;cursor:default}
  .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
  .basis{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.7px;
         padding:3px 9px;border-radius:5px;text-transform:uppercase}
  .b-COMPUTED{background:#2ea04326;color:var(--computed)}
  .b-EXPLAINED{background:#bc8cff26;color:var(--explained)}
  .b-REFERENCE{background:#58a6ff26;color:var(--reference)}
  .b-CLARIFY{background:#d2992226;color:var(--clarify)}
  .b-MODEL_REQUIRED,.b-GAP{background:#f8514926;color:var(--gap)}
  .b-CONVERSATIONAL{background:#6e768126;color:var(--none)}
  .ans{white-space:pre-wrap;margin:12px 0 0}
  .src{border-left:2px solid var(--line);padding:6px 0 6px 12px;margin-top:8px;
       font-size:12.5px;color:var(--muted)}
  .src b{color:var(--ink);font-weight:600}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);
        vertical-align:top}
  th{color:var(--muted);font-weight:600;font-size:11.5px;text-transform:uppercase;
     letter-spacing:.4px}
  code{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;color:#39c5cf}
  .ok{color:var(--computed)} .bad{color:var(--gap)} .warn{color:var(--clarify)}
  .muted{color:var(--muted)}
  .bar{height:6px;background:#0d1117;border:1px solid var(--line);
       border-radius:99px;overflow:hidden;margin:6px 0}
  .bar div{height:100%;background:var(--accent)}
  .spin{display:inline-block;width:11px;height:11px;border:2px solid var(--line);
        border-top-color:var(--accent);border-radius:50%;animation:s .7s linear infinite}
  @keyframes s{to{transform:rotate(360deg)}}
  .foot{color:var(--muted);font-size:12px;padding:6px 20px 30px;
        max-width:1000px;margin:0 auto}
</style></head><body>

<header>
  <h1>EngineAI Sandbox</h1>
  <span class="pill" id="bound">local only</span>
  <span class="pill" id="lawcount">library</span>
  <nav>
    <button class="on" data-t="ask">Ask</button>
    <button data-t="library">Library</button>
    <button data-t="cad">CAD</button>
    <button data-t="research">Research</button>
    <button data-t="status">Status</button>
  </nav>
</header>

<main>
<section id="ask" class="on">
  <div class="card">
    <h2>Ask anything engineering</h2>
    <div class="sub">The BASIS label says where the answer came from.
      COMPUTED is a solver. EXPLAINED is the local model reading verified laws.
      CLARIFY means it needs inputs it genuinely does not have.</div>
    <textarea id="q" placeholder="what is effective stress"></textarea>
    <div class="row" style="margin-top:10px">
      <button class="go" id="askgo">Ask</button>
      <span id="askstat" class="muted"></span>
    </div>
  </div>
  <div id="askout"></div>
</section>

<section id="library">
  <div class="card">
    <h2>Verified law library</h2>
    <div class="sub">The sources the Ask panel reads. Each law carries its
      validity range and the trap that catches people who use it outside one.</div>
    <input id="lq" placeholder="search: bearing capacity, Nusselt, Weibull, tool life...">
  </div>
  <div class="card"><table id="ltab"><tbody></tbody></table></div>
</section>

<section id="cad">
  <div class="card">
    <h2>Parametric solids</h2>
    <div class="sub">Built as B-rep solids, exported to STEP and STL. Every
      build is checked against the closed-form volume before the file is
      offered - a kernel that produced the wrong shape fails here, visibly.</div>
    <div class="row"><select id="part" style="max-width:240px"></select>
      <button class="go" id="cadgo">Build</button>
      <span id="cadstat" class="muted"></span></div>
    <div class="grid" id="params" style="margin-top:12px"></div>
  </div>
  <div id="cadout"></div>
</section>

<section id="research">
  <div class="card">
    <h2>Discover a law, then attack it</h2>
    <div class="sub">Paste CSV with a header row. The engine fits a law and
      then tries to break it. REFUTED is a real and useful outcome.</div>
    <textarea id="csv" style="min-height:150px"
      placeholder="d,v,L,dp&#10;0.05,1.0,10,320&#10;..."></textarea>
    <div class="row" style="margin-top:10px">
      <input id="target" placeholder="target column (default: last)" style="max-width:280px">
      <button class="go" id="resgo">Discover and attack</button>
      <span id="resstat" class="muted"></span>
    </div>
  </div>
  <div id="resout"></div>
</section>

<section id="status">
  <div id="statout"><div class="card muted">loading…</div></div>
</section>
</main>

<div class="foot">Bound to 127.0.0.1. Nothing on this page calls a metered API.
Answers are labelled by basis; anything not marked COMPUTED has not been
through a solver and needs a qualified engineer's review before it is used.</div>

<script>
const TOK = new URLSearchParams(location.search).get('token') || '';
if (TOK) history.replaceState({}, '', location.pathname);   // keep it out of history
const H = {'Content-Type':'application/json','X-EngineAI-Token':TOK};
const $ = s => document.querySelector(s);
const esc = s => String(s==null?'':s).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

async function api(path, body){
  const r = await fetch(path, body ? {method:'POST',headers:H,body:JSON.stringify(body)}
                                   : {headers:H});
  const j = await r.json().catch(()=>({error:'bad response '+r.status}));
  if (!r.ok && !j.error) j.error = 'HTTP '+r.status;
  return j;
}

document.querySelectorAll('nav button').forEach(b => b.onclick = () => {
  document.querySelectorAll('nav button').forEach(x=>x.classList.remove('on'));
  document.querySelectorAll('section').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); $('#'+b.dataset.t).classList.add('on');
  if (b.dataset.t==='status') loadStatus();
  if (b.dataset.t==='library' && !$('#ltab tbody').children.length) searchLib('');
  if (b.dataset.t==='cad' && !$('#part').children.length) loadParts();
});

// ---- ask
$('#askgo').onclick = async () => {
  const text = $('#q').value.trim(); if(!text) return;
  $('#askgo').disabled = true;
  $('#askstat').innerHTML = '<span class="spin"></span> thinking (a local 7B takes ~20s)';
  const j = await api('/api/ask', {text});
  $('#askgo').disabled = false; $('#askstat').textContent = '';
  if (j.error){ $('#askout').innerHTML = '<div class="card bad">'+esc(j.error)+'</div>'; return; }
  let html = '<div class="card"><div class="row">'
    + '<span class="basis b-'+esc(j.basis)+'">'+esc(j.basis)+'</span>'
    + '<span class="muted">'+j.seconds+'s</span></div>'
    + '<div class="ans">'+esc(j.answer)+'</div>';
  if (j.needs) html += '<div class="src">needs: '+esc(j.needs.join(', '))+'</div>';
  if (j.sources && j.sources.length){
    html += '<div class="sub" style="margin:14px 0 4px">read from the verified library:</div>';
    for (const s of j.sources)
      html += '<div class="src"><b>'+esc(s.name)+'</b>'
           + (s.validity? ' — valid: '+esc(s.validity):'')
           + (s.note? '<br>'+esc(s.note):'') + '</div>';
  } else {
    html += '<div class="src warn">no verified law matched this question — the '
         + 'answer came from the model\'s own weights, with nothing to read</div>';
  }
  $('#askout').innerHTML = html + '</div>' + $('#askout').innerHTML;
  $('#q').value='';
};
$('#q').addEventListener('keydown', e => {
  if (e.key==='Enter' && (e.ctrlKey||e.metaKey)) $('#askgo').click();
});

// ---- library
let lt;
$('#lq').oninput = e => { clearTimeout(lt); lt = setTimeout(()=>searchLib(e.target.value), 220); };
async function searchLib(q){
  const j = await api('/api/library?limit=60&q='+encodeURIComponent(q));
  if (j.error) return;
  $('#lawcount').textContent = j.total + ' verified laws';
  $('#ltab tbody').innerHTML = (j.laws||[]).map(l =>
    '<tr><td><b>'+esc(l.name)+'</b>'
    + (l.discipline?' <span class="muted">'+esc(l.discipline)+'</span>':'')
    + (l.note?'<div class="muted">'+esc(l.note)+'</div>':'')
    + '</td><td class="muted">'+esc(l.validity||'')+'</td></tr>').join('')
    || '<tr><td class="muted">nothing matched</td></tr>';
}

// ---- cad
let PARTS = {};
async function loadParts(){
  const j = await api('/api/parts'); if (j.error) return;
  PARTS = j.parts;
  $('#part').innerHTML = Object.keys(PARTS).map(p=>'<option>'+esc(p)+'</option>').join('');
  showParams();
}
$('#part').onchange = showParams;
function showParams(){
  const p = PARTS[$('#part').value] || {};
  $('#params').innerHTML = Object.entries(p).map(([k,v]) =>
    '<div><div class="muted" style="font-size:11.5px">'+esc(k)+'</div>'
    + '<input data-p="'+esc(k)+'" value="'+esc(v)+'"></div>').join('');
}
$('#cadgo').onclick = async () => {
  const params = {};
  document.querySelectorAll('#params input').forEach(i => params[i.dataset.p] = i.value);
  $('#cadgo').disabled = true; $('#cadstat').innerHTML = '<span class="spin"></span> building';
  const j = await api('/api/cad', {part:$('#part').value, params});
  $('#cadgo').disabled = false; $('#cadstat').textContent = '';
  if (j.error){ $('#cadout').innerHTML = '<div class="card bad">'+esc(j.error)+'</div>'; return; }
  const c = j.volume_check || {};
  let v = c.agrees === true
    ? '<span class="ok">volume agrees with the closed form to '+c.error_pct+'%</span>'
    : (c.error_pct!=null ? '<span class="bad">volume disagrees by '+c.error_pct+'%</span>'
                         : '<span class="muted">no closed-form check for this part</span>');
  $('#cadout').innerHTML = '<div class="card"><h2>'+esc(j.part)+'</h2><div class="sub">'+v+'</div>'
    + (j.files||[]).map(f=>'<div><a href="/api/file/'+encodeURIComponent(f.name)+'?token='
        +encodeURIComponent(TOK)+'">'+esc(f.name)+'</a> <span class="muted">'
        +f.bytes+' bytes</span></div>').join('') + '</div>' + $('#cadout').innerHTML;
};

// ---- research
$('#resgo').onclick = async () => {
  $('#resgo').disabled = true; $('#resstat').innerHTML = '<span class="spin"></span> discovering, then attacking';
  const j = await api('/api/research', {csv:$('#csv').value, target:$('#target').value});
  $('#resgo').disabled = false; $('#resstat').textContent = '';
  if (j.error){ $('#resout').innerHTML = '<div class="card bad">'+esc(j.error)+'</div>'; return; }
  const bad = /REFUT|FAIL|REJECT/i.test(j.verdict||'');
  $('#resout').innerHTML = '<div class="card"><div class="row">'
    + '<span class="basis '+(bad?'b-GAP':'b-COMPUTED')+'">'+esc(j.verdict)+'</span>'
    + '<code>'+esc(j.expression)+'</code></div>'
    + '<table><tbody>' + (j.attacks||[]).map(a=>'<tr><td>'+esc(a.name)+'</td>'
        + '<td class="'+(a.passed?'ok':'bad')+'">'+(a.passed?'passed':'FAILED')+'</td>'
        + '<td class="muted">'+esc(a.detail)+'</td></tr>').join('')
    + '</tbody></table>'
    + '<div class="src warn">'+esc(j.caveat)+'</div></div>' + $('#resout').innerHTML;
};

// ---- status
async function loadStatus(){
  const j = await api('/api/status');
  if (j.error){ $('#statout').innerHTML='<div class="card bad">'+esc(j.error)+'</div>'; return; }
  $('#bound').textContent = j.bound;
  const b = j.bank||{}, m = j.model||{}, c = j.capabilities||{};
  let h = '';
  if (b.available && b.answered){
    const pct = Math.round(b.answered/b.total*100);
    h += '<div class="card"><h2>Question bank run</h2>'
      + '<div class="sub">the user\'s own 1062-question corpus, 677 of which survived the paste limit</div>'
      + '<div class="bar"><div style="width:'+pct+'%"></div></div>'
      + '<div class="muted">'+b.answered+' / '+b.total+' answered ('+pct+'%), '
      + b.seconds_per_question+'s each · explained '
      + Math.round(b.explained_rate*100)+'% · library reached '
      + Math.round(b.library_hit_rate*100)+'%</div>'
      + '<table><tbody>' + Object.entries(b.counts||{}).map(([k,v])=>
          '<tr><td>'+esc(k)+'</td><td>'+v+'</td></tr>').join('') + '</tbody></table></div>';
  }
  h += '<div class="card"><h2>Model</h2><div class="sub">local only, nothing metered</div>'
    + '<table><tbody>'
    + '<tr><td>selected</td><td><code>'+esc(m.selected)+'</code></td></tr>'
    + '<tr><td>resident</td><td>'+esc((m.resident||[]).join(', ')||'none')+'</td></tr>'
    + '<tr><td>installed</td><td class="muted">'+esc((m.installed||[]).join(', '))+'</td></tr>'
    + '</tbody></table></div>';
  const flag = v => v ? '<span class="ok">yes</span>' : '<span class="bad">no</span>';
  h += '<div class="card"><h2>Solvers</h2><table><tbody>'
    + Object.entries(c.solvers||{}).map(([k,v])=>'<tr><td>'+esc(k)+'</td><td>'+flag(v)+'</td></tr>').join('')
    + '</tbody></table></div>'
    + '<div class="card"><h2>Python stack</h2><table><tbody>'
    + Object.entries(c.python||{}).map(([k,v])=>'<tr><td>'+esc(k)+'</td><td>'+flag(v)+'</td></tr>').join('')
    + '</tbody></table></div>'
    + '<div class="card"><h2>Library</h2><div class="sub">'+(j.library.laws)+' verified laws</div>'
    + '<table><tbody>' + Object.entries(j.library.disciplines||{}).map(([k,v])=>
        '<tr><td>'+esc(k)+'</td><td>'+v+'</td></tr>').join('') + '</tbody></table></div>';
  $('#statout').innerHTML = h;
}
searchLib('');
</script></body></html>
"""


# --------------------------------------------------------------------------
def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    from fastapi.testclient import TestClient
    import server_auth

    app = build_app()
    tok = app.state.token
    c = TestClient(app)
    H = {"X-EngineAI-Token": tok}

    # --- auth applies here exactly as on the other three servers
    chk(c.get("/").status_code == 401, "no token is rejected on the page itself")
    chk(c.get("/api/status").status_code == 401, "no token is rejected on the API")
    chk(c.get("/api/status", headers={"X-EngineAI-Token": "wrong"}).status_code
        == 401, "a wrong token is rejected")
    chk(c.get("/healthz").status_code == 200, "healthz stays public for liveness")
    chk(c.get("/", headers=H).status_code == 200, "the page loads with a token")
    chk(c.get(f"/?token={tok}").status_code == 200, "?token= works for a browser")

    # --- the page must not contain the token
    body = c.get("/", headers=H).text
    chk(tok not in body, "the served page does not embed the token")
    chk("127.0.0.1" in body, "the page states it is local only")

    # --- status
    r = c.get("/api/status", headers=H)
    chk(r.status_code == 200, "status responds")
    j = r.json()
    chk(j["library"]["laws"] >= 264, f"status reports the library ({j['library']['laws']})")
    chk(len(j["library"]["disciplines"]) >= 20,
        f"disciplines counted ({len(j['library']['disciplines'])})")
    chk("bound" in j and "127.0.0.1" in j["bound"], "status states the bind address")

    # --- library search
    r = c.get("/api/library?q=bearing capacity", headers=H).json()
    chk(r["n"] >= 1, f"library search finds Terzaghi ({r['n']} hits)")
    chk(all("name" in l for l in r["laws"]), "every law returned has a name")
    r2 = c.get("/api/library?q=zzzznotathing", headers=H).json()
    chk(r2["n"] == 0, "a nonsense search returns nothing rather than everything")

    # --- parts
    p = c.get("/api/parts", headers=H).json()["parts"]
    chk("mounting_plate" in p, "parts are listed with their parameters")
    chk(p["mounting_plate"].get("L") == 120.0, "defaults come from cad_parts.PARTS")

    # --- a REAL solid, not a 200. This endpoint returned 200 with an error
    # body once; only building one catches that.
    cd = c.post("/api/cad", headers=H,
                json={"part": "mounting_plate",
                      "params": {"L": 150, "W": 90, "T": 12, "hole_d": 10,
                                 "margin": 15}})
    chk(cd.status_code == 200, f"a part builds ({cd.status_code})")
    cj = cd.json()
    chk("error" not in cj, f"and without an error body ({cj.get('error')})")
    if "error" not in cj:
        chk(cj["volume_check"]["agrees"] is True,
            f"solid volume matches the closed form "
            f"({cj['volume_check'].get('error_pct')}%)")
        chk({f["name"].rsplit(".", 1)[-1] for f in cj["files"]} == {"step", "stl"},
            "both STEP and STL were written")
        chk(all(f["bytes"] > 500 for f in cj["files"]),
            "neither export is an empty stub")
        got = c.get(f"/api/file/{cj['files'][0]['name']}", headers=H)
        chk(got.status_code == 200 and len(got.content) > 500,
            "the generated file downloads through the API")
    chk(c.post("/api/cad", headers=H, json={"part": "not_a_part"}).status_code
        == 400, "an unknown part is a clean 400")

    # --- research refuses too little data rather than fitting noise
    tiny = "x,y\n1,2\n2,4\n3,6\n"
    rr = c.post("/api/research", headers=H, json={"csv": tiny})
    chk(rr.status_code == 400 and "at least 30" in rr.json().get("error", ""),
        "research refuses 3 points instead of reporting a law")
    chk("verdict on your data" in rr.json().get("error", ""),
        "and explains that too-little-data is not the same as a refutation")

    rr2 = c.post("/api/research", headers=H,
                 json={"csv": "x,y\n" + "".join(f"a{i},b{i}\n" for i in range(40))})
    chk(rr2.status_code == 400, "non-numeric data is refused, not coerced")

    # --- real discovery: v = sqrt(2 g h), a law with a known exponent of 1/2
    import math
    lines = ["h,g,v"]
    for h_ in (1.0, 1.6, 2.0, 2.8, 3.5, 4.2, 5.0, 6.1, 7.5, 9.0,
               10.0, 12.0, 14.0, 17.0, 20.0, 24.0):
        for g_ in (9.80665, 3.71, 24.79):
            lines.append(f"{h_},{g_},{math.sqrt(2*g_*h_):.9g}")
    rr3 = c.post("/api/research", headers=H,
                 json={"csv": "\n".join(lines), "target": "v"})
    chk(rr3.status_code == 200, f"a real dataset is accepted ({rr3.status_code})")
    if rr3.status_code == 200:
        j3 = rr3.json()
        chk(j3.get("expression") is not None, "an expression came back")
        chk(len(j3.get("attacks", [])) >= 3,
            f"the finding was attacked, not just fitted ({len(j3.get('attacks', []))})")
        chk("real validation" in j3.get("caveat", ""),
            "the caveat about needing real validation is always attached")

    # --- file endpoint refuses traversal
    (OUT / "harmless.txt").write_text("ok", encoding="utf-8")
    chk(c.get("/api/file/harmless.txt", headers=H).status_code == 200,
        "a generated file downloads")
    bad = c.get("/api/file/..%2f..%2fchat.py", headers=H)
    chk(bad.status_code == 404 or b"import" not in bad.content,
        "a path traversal does not read source files")
    chk(c.get("/api/file/nope.step", headers=H).status_code == 404,
        "a missing file is a clean 404")

    # --- ask, with chat stubbed. The live model takes 20 s and a selftest that
    # depends on a warm model is the cold-start trap all over again.
    import chat
    real = chat.respond
    chat.respond = lambda text, model_fn=None, lang=None: {
        "basis": chat.EXPLAINED, "answer": "Effective stress is total stress "
        "minus pore water pressure. " * 4}
    try:
        a = c.post("/api/ask", headers=H,
                   json={"text": "what is effective stress"}).json()
        chk(a["basis"] == chat.EXPLAINED, "ask returns the basis label")
        chk(a["seconds"] >= 0, "ask times the answer")
        chk(len(a["sources"]) >= 1,
            f"ask attaches the laws it read ({len(a['sources'])})")
        chk(any("effective stress" in (s.get("name", "") or "").lower()
                for s in a["sources"]), "and they are the RIGHT laws")
        e = c.post("/api/ask", headers=H, json={"text": "   "})
        chk(e.status_code == 400, "an empty question is a 400, not a crash")

        def boom(text, model_fn=None, lang=None):
            raise RuntimeError("model down")
        chat.respond = boom
        d = c.post("/api/ask", headers=H, json={"text": "x"})
        chk(d.status_code == 500 and "model down" in d.json()["error"],
            "a failure in chat is reported, not swallowed into a blank answer")
    finally:
        chat.respond = real

    print(f"\nsandbox_server.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--tls", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return _selftest()

    # There is no --lan flag. Reaching past loopback takes an explicit
    # environment variable as well as an explicit address, so "local first"
    # cannot be undone by a habit.
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        if os.environ.get("ENGINEAI_SANDBOX_ALLOW_REMOTE") != "1":
            print(f"refusing to bind {a.host}: this sandbox is local-only.\n"
                  "Set ENGINEAI_SANDBOX_ALLOW_REMOTE=1 if you really mean it, "
                  "and use --tls if you do.", file=sys.stderr)
            return 2

    import uvicorn
    import server_auth
    app = build_app()
    kw = {}
    if a.tls:
        cert, key = server_auth.ensure_tls_cert()
        kw = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    scheme = "https" if a.tls else "http"
    print(f"\n  EngineAI Sandbox  {scheme}://{a.host}:{a.port}/?token={app.state.token}")
    print(f"  token file: {server_auth.TOKEN_FILE}")
    print("  local only - nothing on this page calls a metered API\n")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning", **kw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
