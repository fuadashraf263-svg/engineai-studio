#!/usr/bin/env python3
"""Local transparent chat UI with evidence labels and a live sandbox monitor."""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

APP_NAME = "EngineAI Assurance Chat"
SANDBOX_URL = os.environ.get("ENGINEAI_SANDBOX_URL", "http://127.0.0.1:8102")
MAX_CONTEXT_CHARS = 18_000


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8_000)
    project_id: str | None = Field(default=None, max_length=80)
    confirm_external_send: bool = False


class Activity:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: list[dict[str, Any]] = []

    def add(self, phase: str, detail: str, request_id: str | None = None) -> None:
        with self.lock:
            self.items.insert(0, {"id": f"evt_{uuid.uuid4().hex[:16]}", "at": int(time.time()),
                                  "phase": phase, "detail": detail, "request_id": request_id})
            del self.items[100:]

    def recent(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.items)


activity = Activity()


def sandbox_json(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(SANDBOX_URL + path, data=data, method=method,
                                     headers={"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(503, f"local task service is unavailable: {type(exc).__name__}") from exc


def context_for(project_id: str | None) -> tuple[str, list[dict[str, str]]]:
    if not project_id:
        return "No project context was selected.", []
    try:
        from project_hub import DATABASE, Repository
        project = Repository(DATABASE).project(project_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"project evidence is unavailable: {type(exc).__name__}") from exc
    records = [f"Project: {project['name']}", f"Description: {project.get('description') or '(none)'}"]
    evidence: list[dict[str, str]] = []
    for memory in project.get("memories", [])[:20]:
        records.append(f"{memory['kind']} from {memory['source']}: {memory['text']}")
    for citation in project.get("citations", [])[:20]:
        records.append(f"Citation [{citation['verification']}]: {citation['title']} — {citation['locator']}")
        evidence.append({"kind": "project citation", "verification": citation["verification"],
                         "detail": f"{citation['title']} — {citation['locator']}"})
    for file in project.get("files", [])[:10]:
        records.append(f"Local file checksum {file['sha256'][:12]}…: {file['filename']}\n{file['preview'][:1200]}")
        evidence.append({"kind": "local file", "verification": "checksum retained",
                         "detail": f"{file['filename']} ({file['sha256'][:12]}…)"})
    for vision in project.get("vision_analyses", [])[:5]:
        records.append(f"Unverified visual interpretation: {vision['result'][:800]}")
        evidence.append({"kind": "vision interpretation", "verification": "unverified",
                         "detail": f"{vision['provider']}/{vision['model']}"})
    return "\n\n".join(records)[:MAX_CONTEXT_CHARS], evidence


SYSTEM = """You are EngineAI Assurance Chat, an engineering and coding assistant. Never claim universal accuracy, certification, compliance, physical testing, manufacturability, or a source that was not supplied. Use SI units and state assumptions. Treat project memories and file previews as supplied context, not independently verified facts. Do not fabricate citations, measurements, standards, model results, tool output, or sources.

Use exactly these Markdown headings:
## Answer
## Verified evidence
## Assumptions
## Unverified / needs validation
## Next action

Only name supplied evidence under Verified evidence. If no supplied evidence proves a claim, write "No independent evidence supplied." Model synthesis and inferred calculations remain unverified until checked by a deterministic tool, cited source, or qualified reviewer. Require appropriate professional review for safety-critical, electrical, pressure, vehicle, flight, manufacturing, medical, or legal decisions."""


def label_answer(raw: str) -> str:
    required = ("## answer", "## verified evidence", "## assumptions", "## unverified / needs validation", "## next action")
    if all(item in raw.lower() for item in required):
        return raw.strip()
    return raw.strip() + "\n\n## Verification status\nThe model omitted required headings. Treat all substantive claims as unverified until checked."


def ask(payload: ChatRequest) -> dict[str, Any]:
    if not payload.confirm_external_send:
        raise HTTPException(422, "set confirm_external_send=true to send the prompt and selected project context to the hosted model")
    request_id = f"chat_{uuid.uuid4().hex[:16]}"
    activity.add("queued", "Request accepted locally.", request_id)
    context, evidence = context_for(payload.project_id)
    activity.add("evidence", f"Collected {len(evidence)} local evidence record(s).", request_id)
    try:
        tasks = sandbox_json("/api/tasks")
        for task in [row for row in tasks if row.get("state") == "completed" and row.get("exit_code") == 0][:5]:
            evidence.append({"kind": "local allow-listed task", "verification": "completed exit code 0",
                             "detail": f"{task['title']} ({task['id']})"})
    except HTTPException:
        activity.add("sandbox", "Task monitor is unavailable; the model answer continues without task status.", request_id)
    prompt = f"User question:\n{payload.message.strip()}\n\nProject context:\n{context}\n\nEvidence labels:\n{json.dumps(evidence, ensure_ascii=False)}"
    activity.add("model", "Sending the consented request to the configured hosted model.", request_id)
    try:
        import providers
        raw = providers.complete(SYSTEM, [{"role": "user", "content": prompt}], timeout=180, json_mode=False)
    except Exception as exc:
        activity.add("failed", f"Hosted model failed: {type(exc).__name__}", request_id)
        raise HTTPException(502, f"hosted model failed: {type(exc).__name__}: {exc}") from exc
    activity.add("completed", "Hosted model returned; evidence labels remain separate from its synthesis.", request_id)
    return {"request_id": request_id, "answer": label_answer(raw), "evidence": evidence,
            "system_status": {"model_output": "unverified synthesis unless a supplied evidence record proves a specific claim",
                              "external_send": "explicitly confirmed for this request",
                              "sandbox": "task states are live local status; completed allow-listed tasks are reproducible evidence"}}


def page() -> str:
    return r'''<!doctype html><meta charset="utf-8"><title>EngineAI Assurance Chat</title><style>body{margin:24px auto;max-width:1280px;background:#0b1220;color:#e8eff7;font:15px system-ui}main{display:grid;grid-template-columns:minmax(0,2fr) minmax(330px,1fr);gap:16px}.panel{background:#121e2e;border:1px solid #28415d;border-radius:10px;padding:16px}textarea,input,button{width:100%;box-sizing:border-box;padding:10px;margin:6px 0;border-radius:6px;border:1px solid #385571;background:#091522;color:#e8eff7}textarea{min-height:120px}button{background:#1e6398;font-weight:700;cursor:pointer}.muted{color:#9fb4c8}pre{white-space:pre-wrap;overflow:auto;max-height:480px}.event{border-left:3px solid #4884b8;margin:7px 0;padding:4px 8px}.task{border-top:1px solid #2b4159;padding:7px 0}.ok{color:#8be0ac}</style><h1>EngineAI Assurance Chat <small class="muted">answer with evidence boundaries</small></h1><main><section class="panel"><p class="muted">The model is helpful, not automatically correct. Each answer must separate evidence, assumptions, and validation. Sending a prompt or project context to the hosted model requires your explicit consent.</p><input id="project" placeholder="Optional Project Hub project ID"><textarea id="message" placeholder="Ask an engineering, coding, research, or design question…"></textarea><label><input id="consent" type="checkbox" style="width:auto"> I consent to send this prompt and selected project context to my configured hosted model.</label><button id="send">Ask EngineAI</button><div id="state" class="muted">Ready.</div><h2>Answer</h2><pre id="answer">No answer yet.</pre><h2>Evidence records used</h2><pre id="evidence">None.</pre></section><aside class="panel"><h2>Live activity</h2><div id="events" class="muted">Loading…</div><h2>Isolated local tasks</h2><button data-task="mathsolver_selftest">Run math safety check</button><button data-task="provider_selftest">Run provider check</button><button data-task="workspace_selftest">Run CAD workspace check</button><div id="tasks" class="muted">Loading…</div></aside></main><script>const $=x=>document.getElementById(x),api=async(p,o={})=>{const r=await fetch(p,o),d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.detail||d.error||r.statusText);return d};async function refresh(){try{const[a,t]=await Promise.all([api('/api/activity'),api('/api/sandbox/tasks')]);$('events').replaceChildren(...a.events.map(e=>{const d=document.createElement('div');d.className='event';d.textContent=new Date(e.at*1000).toLocaleTimeString()+' · '+e.phase+' · '+e.detail;return d}));$('tasks').replaceChildren(...t.map(x=>{const d=document.createElement('div');d.className='task '+(x.state==='completed'?'ok':'');d.textContent=x.state+' · '+x.title+' · '+x.id;return d}))}catch(e){$('events').textContent=e.message}}$('send').onclick=async()=>{try{$('state').textContent='Working: gathering local evidence and calling the hosted model…';$('answer').textContent='Waiting for the model; activity updates on the right.';const r=await api('/api/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({message:$('message').value,project_id:$('project').value||null,confirm_external_send:$('consent').checked})});$('answer').textContent=r.answer;$('evidence').textContent=JSON.stringify({records:r.evidence,status:r.system_status},null,2);$('state').textContent='Completed. Read the unverified section before relying on the answer.'}catch(e){$('state').textContent=e.message}finally{refresh()}};document.querySelectorAll('[data-task]').forEach(b=>b.onclick=async()=>{try{await api('/api/sandbox/tasks/'+b.dataset.task,{method:'POST'});refresh()}catch(e){$('state').textContent=e.message}});refresh();setInterval(refresh,1500);</script>'''


def make_app() -> FastAPI:
    app = FastAPI(title=APP_NAME, version="1.0")
    @app.get("/", response_class=HTMLResponse)
    def home() -> str: return page()
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": APP_NAME, "bind": "localhost only", "hosted_model": "explicit consent per request", "verification": "model synthesis, evidence records and task results remain separate"}
    @app.get("/api/activity")
    def events() -> dict[str, Any]: return {"events": activity.recent()}
    @app.get("/api/sandbox/tasks")
    def tasks() -> list[dict[str, Any]]: return sandbox_json("/api/tasks")
    @app.post("/api/sandbox/tasks/{kind}", status_code=202)
    def start_task(kind: str) -> dict[str, Any]:
        if kind not in {"mathsolver_selftest", "provider_selftest", "keypool_selftest", "workspace_selftest"}:
            raise HTTPException(422, "task is not in the verified local allow-list")
        activity.add("sandbox", f"Queued verified local task: {kind}")
        return sandbox_json("/api/tasks", "POST", {"kind": kind})
    @app.post("/api/chat")
    def chat(payload: ChatRequest) -> dict[str, Any]: return ask(payload)
    return app


def selftest() -> bool:
    try:
        assert "## Answer" in SYSTEM and "## Verified evidence" in SYSTEM
        assert "Verification status" in label_answer("plain output")
        context, evidence = context_for(None)
        assert "No project" in context and not evidence
        print("assurance chat selftest: PASSED")
        return True
    except Exception as exc:
        print(f"assurance chat selftest: FAILED: {type(exc).__name__}: {exc}")
        return False


def live_smoke() -> bool:
    """One consented, non-sensitive hosted-model check of the answer contract."""
    try:
        result = ask(ChatRequest(message="What is 2 + 2? Be concise.", confirm_external_send=True))
        answer = result["answer"].lower()
        assert "## answer" in answer and "## unverified / needs validation" in answer
        print("assurance chat live smoke: PASSED")
        return True
    except Exception as exc:
        print(f"assurance chat live smoke: FAILED: {type(exc).__name__}: {exc}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8104)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--live-smoke", action="store_true", help="send a harmless consented test prompt to the configured model")
    args = parser.parse_args()
    if args.selftest: raise SystemExit(0 if selftest() else 1)
    if args.live_smoke: raise SystemExit(0 if live_smoke() else 1)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("WARNING: do not expose Assurance Chat until authentication, TLS and security review exist.")
    import uvicorn
    uvicorn.run(make_app(), host=args.host, port=args.port, log_level="info")
