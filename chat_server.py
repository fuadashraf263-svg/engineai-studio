#!/usr/bin/env python3
"""
chat_server.py -- the conversation UI. A chat window over your verified AI.

This is the 'feels finished' piece. studio.html is buttons; this is a
message box a student types into and gets a source-tagged answer back.

It is a THIN web layer over the routing that already exists and is tested:
every message goes through the same ask -> handbook / math / circuit /
library tiers, so what the browser shows is exactly what `agent.py ask`
computes -- verified, tagged, and honest about what it cannot answer.

Security posture (same as server.py):
  * binds 127.0.0.1 unless --lan
  * calls only the in-process routers -- no shell, no arbitrary execution
  * the brain (online, paid) is OFF unless the user ticks 'allow AI'

Run:  conda activate eng && pip install fastapi uvicorn "uvicorn[standard]"
      python chat_server.py           ->  http://localhost:8096
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

HOME = Path.home() / "engineai"
sys.path.insert(0, str(HOME))

app = FastAPI(title="EngineAI Chat")

import server_auth
AUTH_TOKEN = server_auth.install(app, "EngineAI Chat")

FREE_QUESTIONS = 3   # first N brain answers free, then prompt to upgrade

# per-session counters, keyed by a cookie the browser sends. No account, no
# card -- exactly your model: let them like it first, ask later.
_usage: dict = {}


class Msg(BaseModel):
    text: str
    allow_brain: bool = False
    session: str = "anon"
    image_b64: str | None = None


def route(text: str, allow_brain: bool, image_b64=None,
          session="anon") -> dict:
    """Run the question through the verified tiers, capture what each says.
    Returns structured segments the UI can render with provenance colours."""
    segments = []

    # deterministic tiers
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
    except Exception as e:
        segments.append({"provenance": "ERROR", "text": str(e)})

    # library
    try:
        import library
        hits = library.search(text, k=3)
        for h in hits:
            body = " ".join(h["text"].split())[:300]
            segments.append({"provenance": "LIBRARY",
                             "text": f"[{h['book']}, p.{h['page']}] "
                                     f"(relevance {h['score']})\n\"{body}...\""})
    except Exception:
        pass

    # brain, only if allowed AND within free quota (or paid)
    if allow_brain and not segments:
        used = _usage.get(session, 0)
        if used >= FREE_QUESTIONS:
            segments.append({"provenance": "UPGRADE",
                             "text": f"You have used your {FREE_QUESTIONS} "
                             "free AI answers. The verified tiers (handbook, "
                             "maths, circuits, materials, your library) stay "
                             "free and unlimited. To keep using the AI "
                             "fallback for open-ended questions, upgrade to "
                             "Pro. No card needed to try -- you have already "
                             "seen how it works above."})
            return {"segments": segments, "free_used": used,
                    "free_total": FREE_QUESTIONS, "upgrade": True}
        try:
            cfg = json.loads((HOME / "config.json").read_text())
            import ask_command as ak
            if image_b64:
                txt, spent = _brain_with_image(cfg, text, image_b64)
            else:
                txt, spent = ak.ask_brain(cfg, text, None)
            _usage[session] = used + 1
            left = FREE_QUESTIONS - _usage[session]
            note = (f"\n\n(model answer, ${spent:.3f}, NOT verified"
                    + (f" -- {left} free answer(s) left)" if left >= 0
                       else ")"))
            segments.append({"provenance": "UNVERIFIED", "text": txt + note})
        except Exception as e:
            segments.append({"provenance": "ERROR",
                             "text": f"brain unavailable: {e}"})

    if not segments:
        segments.append({"provenance": "NONE",
                         "text": "Not in any verified source. No handbook "
                                 "formula, material, or library passage "
                                 "matched. Tick 'allow AI' for a labelled "
                                 "model answer, or add the relevant book."})
    return {"segments": segments}


def _brain_with_image(cfg, text, image_b64):
    """Send text + an image to the multimodal brain (Fable 5 is multimodal).
    This is how a student photographs an exam question and gets an answer."""
    from brain import FablePlanner
    from core import Budget
    budget = Budget(cfg["budget"])
    planner = FablePlanner(cfg, budget)
    import ask_command as ak
    content = [
        {"type": "image", "source": {"type": "base64",
         "media_type": "image/png", "data": image_b64}},
        {"type": "text", "text": text or "Solve the engineering problem in "
         "this image. Show the equation, assumptions, and worked answer."}]
    txt = planner._call([{"role": "user", "content": content}],
                        system=ak.BRAIN_SYSTEM)
    return txt, budget.spent_usd


def route_split(text: str, allow_brain: bool):
    """Return the CERTAIN/UNCERTAIN two-part answer for the UI."""
    try:
        import ask_command as ak
        r = ak.answer_with_certainty(
            json.loads((HOME / "config.json").read_text())
            if (HOME / "config.json").exists() else {"budget": {}},
            text, allow_brain)
        return r
    except Exception as e:
        return {"question": text, "certain": [], "uncertain": [str(e)],
                "has_verified": False, "brain_cost_usd": 0.0}


@app.post("/ask_split")
def ask_split(m: Msg):
    return JSONResponse(route_split(m.text, m.allow_brain))


@app.post("/ask")
def ask(m: Msg):
    return JSONResponse(route(m.text, m.allow_brain, m.image_b64, m.session))


@app.get("/")
def index():
    return HTMLResponse(PAGE)


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EngineAI</title>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --line:#30363d; --ink:#e6edf3;
    --muted:#8b949e; --user:#1f6feb;
    --solved:#3fb950; --handbook:#58a6ff; --lookup:#d29922;
    --library:#bc8cff; --math:#39c5cf; --unverified:#f85149; --none:#6e7681;
  }
  *{box-sizing:border-box}
  body{margin:0;font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;
       background:var(--bg);color:var(--ink);height:100vh;display:flex;
       flex-direction:column}
  header{padding:14px 20px;border-bottom:1px solid var(--line);
         display:flex;align-items:center;gap:12px}
  header h1{font-size:16px;margin:0;font-weight:600}
  header .tag{font-size:12px;color:var(--muted)}
  #log{flex:1;overflow-y:auto;padding:20px;display:flex;
       flex-direction:column;gap:14px;max-width:820px;width:100%;
       margin:0 auto}
  .msg{display:flex;gap:10px;align-items:flex-start}
  .msg.user{justify-content:flex-end}
  .bubble{padding:10px 14px;border-radius:12px;max-width:78%;
          white-space:pre-wrap;word-break:break-word}
  .user .bubble{background:var(--user);color:#fff;border-bottom-right-radius:3px}
  .ai .bubble{background:var(--panel);border:1px solid var(--line);
              border-bottom-left-radius:3px;width:100%;max-width:100%}
  .seg{border-left:3px solid var(--none);padding:8px 12px;margin:8px 0;
       border-radius:0 8px 8px 0;background:#0d1117}
  .seg .prov{font-size:11px;font-weight:700;letter-spacing:.04em;
             text-transform:uppercase;margin-bottom:4px}
  .p-SOLVED{border-color:var(--solved)} .p-SOLVED .prov{color:var(--solved)}
  .p-HANDBOOK{border-color:var(--handbook)} .p-HANDBOOK .prov{color:var(--handbook)}
  .p-LOOKUP{border-color:var(--lookup)} .p-LOOKUP .prov{color:var(--lookup)}
  .p-LIBRARY{border-color:var(--library)} .p-LIBRARY .prov{color:var(--library)}
  .p-MATH{border-color:var(--math)} .p-MATH .prov{color:var(--math)}
  .p-UNVERIFIED{border-color:var(--unverified)} .p-UNVERIFIED .prov{color:var(--unverified)}
  .p-NONE{border-color:var(--none)} .p-NONE .prov{color:var(--none)}
  --upgrade:#e3b341;
  .p-UPGRADE{border-color:var(--upgrade);background:#1c1708}
  .p-UPGRADE .prov{color:var(--upgrade)}
  #imgbtn{background:var(--panel);border:1px solid var(--line);color:var(--ink);
          width:44px;padding:0;font-size:18px}
  #preview{max-width:180px;max-height:120px;border-radius:8px;margin:6px auto 0;
           display:none;border:1px solid var(--line)}
  .counter{font-size:11px;color:var(--muted);text-align:center;margin-top:6px}
  footer{border-top:1px solid var(--line);padding:14px 20px}
  .composer{max-width:820px;margin:0 auto;display:flex;gap:10px;
            align-items:flex-end}
  textarea{flex:1;background:var(--panel);border:1px solid var(--line);
           color:var(--ink);border-radius:10px;padding:10px 12px;resize:none;
           font:inherit;min-height:44px;max-height:160px}
  button{background:var(--user);color:#fff;border:0;border-radius:10px;
         padding:0 18px;height:44px;font-weight:600;cursor:pointer}
  button:disabled{opacity:.5;cursor:default}
  .opts{max-width:820px;margin:8px auto 0;font-size:12px;color:var(--muted);
        display:flex;align-items:center;gap:8px}
  .legend{font-size:11px;color:var(--muted);padding:0 20px 10px;
          max-width:820px;margin:0 auto}
  .legend b{color:var(--ink)}
</style></head>
<body>
<header>
  <h1>EngineAI</h1>
  <span class="tag">verified engineering answers &mdash; every claim shows its source</span>
</header>
<div id="log">
  <div class="msg ai"><div class="bubble"><div class="seg p-HANDBOOK">
  <div class="prov">how to read this</div>
  Ask an engineering question. Each answer is tagged by where it came from:
  <b>SOLVED</b> (a solver ran), <b>HANDBOOK</b> (a checked formula),
  <b>LOOKUP</b> (a material value), <b>LIBRARY</b> (your books, with page),
  <b>MATH</b> (symbolic, verified). If nothing is verified it says so rather
  than guessing. Try: "Reynolds number water 2 m/s 25mm pipe", or paste a
  netlist "R1 1 2 500 ...".
  </div></div></div>
</div>
<footer>
  <div class="composer">
    <button id="imgbtn" title="attach a photo of a problem"
      onclick="document.getElementById('file').click()">&#128247;</button>
    <input type="file" id="file" accept="image/*" style="display:none"
      onchange="pickImage(this)">
    <textarea id="q" placeholder="Ask, or paste/attach a photo of a problem..."
      onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send()}"
      onpaste="pasteImage(event)"></textarea>
    <button id="send" onclick="send()">Ask</button>
  </div>
  <img id="preview">
  <div class="counter" id="counter"></div>
  <div class="opts">
    <label><input type="checkbox" id="brain"> allow AI fallback (online, uses your API key, labelled UNVERIFIED)</label>
  </div>
</footer>
<script>
const log=document.getElementById('log'), q=document.getElementById('q'),
      btn=document.getElementById('send'), preview=document.getElementById('preview'),
      counter=document.getElementById('counter');
let pendingImage=null;
// a stable per-browser session id so the free-question count sticks without login
let session=localStorage.getItem('engineai_sid');
if(!session){session=Math.random().toString(36).slice(2); localStorage.setItem('engineai_sid',session);}
function setImage(dataUrl){
  pendingImage=dataUrl.split(',')[1];
  preview.src=dataUrl; preview.style.display='block';
}
function pickImage(input){
  const f=input.files[0]; if(!f) return;
  const r=new FileReader(); r.onload=e=>setImage(e.target.result); r.readAsDataURL(f);
}
function pasteImage(e){
  for(const item of e.clipboardData.items){
    if(item.type.startsWith('image/')){
      const r=new FileReader(); r.onload=ev=>setImage(ev.target.result);
      r.readAsDataURL(item.getAsFile()); e.preventDefault();
    }
  }
}
function add(role, html){
  const m=document.createElement('div'); m.className='msg '+role;
  const b=document.createElement('div'); b.className='bubble';
  b.innerHTML=html; m.appendChild(b); log.appendChild(m);
  log.scrollTop=log.scrollHeight; return b;
}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;');}
async function send(){
  const text=q.value.trim();
  if(!text && !pendingImage) return;
  const userHtml=esc(text)+(pendingImage?'<br><i style="opacity:.7">[image attached]</i>':'');
  add('user', userHtml); q.value=''; btn.disabled=true;
  const img=pendingImage; pendingImage=null;
  preview.style.display='none'; document.getElementById('file').value='';
  const thinking=add('ai','<span style="color:#8b949e">thinking...</span>');
  try{
    const r=await fetch('/ask',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text, allow_brain:document.getElementById('brain').checked,
                           session, image_b64:img})});
    const data=await r.json();
    let html='';
    for(const s of data.segments){
      html+=`<div class="seg p-${s.provenance}">`+
            `<div class="prov">${s.provenance}</div>${esc(s.text)}</div>`;
    }
    thinking.innerHTML=html;
    if(typeof data.free_used!=='undefined'){
      const left=data.free_total-data.free_used;
      counter.textContent=data.upgrade?'Free AI answers used up — verified tiers stay free':
        `${left} free AI answer(s) remaining`;
    }
  }catch(e){ thinking.innerHTML='<span style="color:#f85149">error: '+esc(e.message)+'</span>'; }
  log.scrollTop=log.scrollHeight; btn.disabled=false; q.focus();
}
q.focus();
</script>
</body></html>"""


def main() -> int:
    import uvicorn
    host = "0.0.0.0" if "--lan" in sys.argv else "127.0.0.1"
    tls = "--tls" in sys.argv
    print(server_auth.startup_banner("EngineAI Chat", host, 8096, AUTH_TOKEN, tls))
    kw = {}
    if tls:
        cert, key = server_auth.ensure_tls_cert()
        kw = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    uvicorn.run(app, host=host, port=8096, log_level="warning", **kw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
