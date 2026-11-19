#!/usr/bin/env python3
"""
server.py -- EngineAI Studio: the launch-facing web app.

Single-user, local-first. Wraps the executive (agent.py) without modifying
it: missions run as subprocesses, stdout streams to the browser over a
WebSocket, and the scene/audit artifacts drive the live panels.

Security posture (deliberate):
  * binds 127.0.0.1 unless --lan is passed (then laptop can view)
  * ONLY whitelisted commands can run -- no arbitrary execution
  * read-only over the scene; the executive owns all writes
  * one job at a time; a second launch is refused, not queued

Run:   conda activate eng && pip install fastapi uvicorn
       python server.py            ->  http://localhost:8095
       python server.py --lan      ->  http://<desktop-ip>:8095
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

HOME = Path.home() / "engineai"
SCENE = HOME / "scene"
APPDIR = Path(__file__).parent

app = FastAPI(title="EngineAI Studio")

# This server runs shell commands and serves the workspace. It accepted every
# request that reached it until now, which on --lan meant anyone on the network
# could drive it. One line, applied before any route is declared.
import server_auth
AUTH_TOKEN = server_auth.install(app, "EngineAI Studio")

state = {"running": False, "label": None, "log": [], "rc": None}

ALLOWED = {"verify": ["verify"], "agi": ["agi"], "report": ["report"],
           "tools": ["tools"]}


def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


async def _launch(args, label):
    if state["running"]:
        return False
    state.update(running=True, label=label, log=[], rc=None)

    async def run():
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "agent.py", *args, cwd=APPDIR,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
            assert proc.stdout is not None
            async for line in proc.stdout:
                state["log"].append(line.decode(errors="replace").rstrip())
                if len(state["log"]) > 4000:
                    del state["log"][:1000]
            state["rc"] = await proc.wait()
        except Exception as exc:
            state["log"].append(f"[studio] launch failed: {exc}")
            state["rc"] = -1
        finally:
            state["running"] = False

    asyncio.create_task(run())
    return True


# ------------------------------------------------------------------ routes --

@app.get("/")
def index():
    return FileResponse(APPDIR / "studio.html")


@app.get("/api/status")
def status():
    cfg = _read_json(APPDIR / "config.json") or {}
    return {"running": state["running"], "label": state["label"],
            "rc": state["rc"],
            "missions": list((cfg.get("missions") or {}).keys()),
            "default_mission": cfg.get("default_mission"),
            "model": (cfg.get("brain") or {}).get("model")}


@app.get("/api/latest")
def latest():
    return _read_json(SCENE / "latest.json") or {}


@app.get("/api/iters")
def iters():
    out = []
    for p in sorted(SCENE.glob("iter_*.json")):
        m = _read_json(p)
        if m:
            out.append({"iteration": m.get("iteration"),
                        "tool": m.get("tool"),
                        "mission": m.get("mission"),
                        "parameters": m.get("parameters"),
                        "result": {k: v for k, v in
                                   (m.get("result") or {}).items()
                                   if isinstance(v, (int, float, str, bool))}})
    return out[-80:]


@app.get("/api/decisions")
def decisions():
    p = SCENE / "decisions.jsonl"
    out = []
    if p.exists():
        for ln in p.read_text().splitlines()[-300:]:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    return out


@app.get("/api/scorecard")
def scorecard():
    return _read_json(SCENE / "agi_scorecard.json") or {}


@app.get("/api/report")
def report():
    p = SCENE / "report.md"
    return {"text": p.read_text() if p.exists() else "(no report yet)"}


@app.post("/api/mission/{name}")
async def start_mission(name: str, hierarchical: int = 0, dry: int = 0):
    cfg = _read_json(APPDIR / "config.json") or {}
    if name not in (cfg.get("missions") or {}):
        return JSONResponse({"error": "unknown mission"}, 400)
    args = ["mission", name]
    if hierarchical:
        args.append("--hierarchical")
    if dry:
        args.append("--dry-run")
    ok = await _launch(args, f"mission:{name}")
    return {"ok": ok} if ok else JSONResponse({"error": "busy"}, 409)


@app.post("/api/run/{what}")
async def run_cmd(what: str):
    if what not in ALLOWED:
        return JSONResponse({"error": "not allowed"}, 400)
    ok = await _launch(ALLOWED[what], what)
    return {"ok": ok} if ok else JSONResponse({"error": "busy"}, 409)


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    sent = 0
    last_latest = 0.0
    try:
        while True:
            if sent < len(state["log"]):
                for ln in state["log"][sent:]:
                    await sock.send_json({"type": "log", "line": ln})
                sent = len(state["log"])
            lp = SCENE / "latest.json"
            if lp.exists():
                mt = lp.stat().st_mtime
                if mt > last_latest:
                    last_latest = mt
                    man = _read_json(lp)
                    if man:
                        await sock.send_json({"type": "iteration",
                                              "data": man})
            await sock.send_json({"type": "state",
                                  "running": state["running"],
                                  "label": state["label"],
                                  "rc": state["rc"]})
            await asyncio.sleep(0.7)
    except WebSocketDisconnect:
        return
    except Exception:
        return


if __name__ == "__main__":
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--lan", action="store_true",
                    help="serve on all interfaces (laptop access)")
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--tls", action="store_true",
                    help="serve over HTTPS with a self-signed certificate")
    a = ap.parse_args()
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    print(server_auth.startup_banner("EngineAI Studio", host, a.port,
                                     AUTH_TOKEN, tls=a.tls))
    kw = {}
    if a.tls:
        cert, key = server_auth.ensure_tls_cert()
        kw = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    uvicorn.run(app, host=host, port=a.port, log_level="warning", **kw)
