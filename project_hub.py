#!/usr/bin/env python3
"""EngineAI Project Hub: durable memory, provenance and approval gates.

This local-only service makes a project survive beyond one model response.  It
stores requirements, decisions, constraints, citations and uploaded work files
with checksums and previews.  Browser and connector actions are *requests* in
an approval queue; they cannot execute by merely being created or approved.

Run:
    conda activate eng
    python project_hub.py --selftest
    python project_hub.py --host 127.0.0.1 --port 8103

It deliberately has no public multi-user authentication or OAuth implementation
yet.  Keep it bound to localhost until those layers have been separately
reviewed and configured.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field


APP_NAME = "EngineAI Project Hub"
ENGINE_ROOT = Path(os.environ.get("ENGINEAI_ROOT", Path.home() / "engineai")).resolve()
WORK_ROOT = (ENGINE_ROOT / "work" / "project_hub").resolve()
FILE_ROOT = (WORK_ROOT / "files").resolve()
DATABASE = WORK_ROOT / "hub.sqlite3"
MAX_UPLOAD_BYTES = int(os.environ.get("ENGINEAI_HUB_MAX_UPLOAD", 100 * 1024 * 1024))
MAX_PREVIEW_CHARS = 12_000
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,119}$")
TEXT_EXTENSIONS = {".txt", ".md", ".rst", ".csv", ".json", ".yaml", ".yml", ".py", ".m", ".c", ".h", ".cpp", ".hpp", ".dxf", ".svg"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg", ".webp", ".stl", ".step", ".stp", ".ifc", ".iges", ".igs"}


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=2000)


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["requirement", "constraint", "decision", "risk", "note"]
    text: str = Field(min_length=1, max_length=6000)
    source: str = Field(default="user", max_length=240)


class CitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    locator: str = Field(default="", max_length=700)
    source_type: Literal["url", "file", "standard", "book", "dataset", "other"] = "other"
    license_note: str = Field(default="", max_length=1000)
    verification: Literal["unverified", "checked", "primary_source"] = "unverified"


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["browser_navigate", "browser_click", "browser_type", "browser_download", "browser_upload", "connector_auth", "connector_read", "connector_write", "external_share"]
    target: str = Field(min_length=1, max_length=700)
    summary: str = Field(min_length=1, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)


class VisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(default="Describe this image.", min_length=1, max_length=4000)
    confirm_external_send: Literal[True]
    provider: Literal["openrouter"] = "openrouter"
    model: str | None = Field(default=None, max_length=180)


def now() -> int:
    return int(time.time())


def public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def checked_name(value: str) -> str:
    value = value.strip()
    if not SAFE_NAME.fullmatch(value) or value in {".", ".."}:
        raise HTTPException(422, "filename may contain letters, digits, spaces, dot, underscore and hyphen only")
    return value


def within(root: Path, candidate: Path) -> Path:
    root, candidate = root.resolve(), candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(403, "path escapes the project workspace")
    return candidate


class Repository:
    def __init__(self, database: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.database, self.lock = database, threading.Lock()
        with self.connect() as con:
            con.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS projects(
                id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS memories(
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                kind TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS citations(
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL, locator TEXT NOT NULL, source_type TEXT NOT NULL,
                license_note TEXT NOT NULL, verification TEXT NOT NULL, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS files(
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                filename TEXT NOT NULL, suffix TEXT NOT NULL, stored_path TEXT NOT NULL,
                sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, preview TEXT NOT NULL,
                created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals(
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                action TEXT NOT NULL, target TEXT NOT NULL, summary TEXT NOT NULL,
                payload_json TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL,
                resolved_at INTEGER, resolution_note TEXT);
            CREATE TABLE IF NOT EXISTS vision_analyses(
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE, provider TEXT NOT NULL,
                model TEXT NOT NULL, prompt TEXT NOT NULL, result TEXT NOT NULL, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS audit_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, event TEXT NOT NULL,
                details_json TEXT NOT NULL, created_at INTEGER NOT NULL);
            """)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.database, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def audit(self, con: sqlite3.Connection, project_id: str | None, event: str, details: dict[str, Any]) -> None:
        con.execute("INSERT INTO audit_events(project_id,event,details_json,created_at) VALUES(?,?,?,?)",
                    (project_id, event, json.dumps(details, sort_keys=True), now()))

    def require_project(self, project_id: str, con: sqlite3.Connection | None = None) -> sqlite3.Row:
        if con is not None:
            row = con.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        else:
            with self.connect() as c:
                row = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "project not found")
        return row

    def create_project(self, payload: ProjectCreate) -> dict[str, Any]:
        if not payload.name.strip():
            raise HTTPException(422, "project name cannot be blank")
        project_id, stamp = public_id("prj"), now()
        with self.lock, self.connect() as con:
            con.execute("INSERT INTO projects VALUES(?,?,?,?,?)", (project_id, payload.name.strip(), payload.description.strip(), stamp, stamp))
            self.audit(con, project_id, "project.created", {"name": payload.name.strip()})
        return self.project(project_id)

    def projects(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [dict(row) for row in con.execute("SELECT * FROM projects ORDER BY updated_at DESC, created_at DESC")]

    def project(self, project_id: str) -> dict[str, Any]:
        with self.connect() as con:
            result = dict(self.require_project(project_id, con))
            result["memories"] = [dict(row) for row in con.execute("SELECT * FROM memories WHERE project_id=? ORDER BY created_at DESC", (project_id,))]
            result["citations"] = [dict(row) for row in con.execute("SELECT * FROM citations WHERE project_id=? ORDER BY created_at DESC", (project_id,))]
            result["files"] = [dict(row) for row in con.execute("SELECT id,filename,suffix,sha256,bytes,preview,created_at FROM files WHERE project_id=? ORDER BY created_at DESC", (project_id,))]
            result["approvals"] = [self._approval(row) for row in con.execute("SELECT * FROM approvals WHERE project_id=? ORDER BY created_at DESC", (project_id,))]
            result["vision_analyses"] = [dict(row) for row in con.execute("SELECT * FROM vision_analyses WHERE project_id=? ORDER BY created_at DESC", (project_id,))]
            return result

    @staticmethod
    def _approval(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def add_memory(self, project_id: str, payload: MemoryCreate) -> dict[str, Any]:
        memory_id, stamp = public_id("mem"), now()
        with self.lock, self.connect() as con:
            self.require_project(project_id, con)
            con.execute("INSERT INTO memories VALUES(?,?,?,?,?,?)", (memory_id, project_id, payload.kind, payload.text.strip(), payload.source.strip(), stamp))
            con.execute("UPDATE projects SET updated_at=? WHERE id=?", (stamp, project_id))
            self.audit(con, project_id, "memory.created", {"id": memory_id, "kind": payload.kind, "source": payload.source})
        return {"id": memory_id, "project_id": project_id, **payload.model_dump(), "created_at": stamp}

    def add_citation(self, project_id: str, payload: CitationCreate) -> dict[str, Any]:
        citation_id, stamp = public_id("cite"), now()
        with self.lock, self.connect() as con:
            self.require_project(project_id, con)
            con.execute("INSERT INTO citations VALUES(?,?,?,?,?,?,?,?)", (citation_id, project_id, payload.title.strip(), payload.locator.strip(), payload.source_type, payload.license_note.strip(), payload.verification, stamp))
            con.execute("UPDATE projects SET updated_at=? WHERE id=?", (stamp, project_id))
            self.audit(con, project_id, "citation.created", {"id": citation_id, "type": payload.source_type, "verification": payload.verification})
        return {"id": citation_id, "project_id": project_id, **payload.model_dump(), "created_at": stamp}

    def add_file(self, project_id: str, filename: str, suffix: str, stored: Path, sha256: str, size: int, preview: str) -> dict[str, Any]:
        file_id, stamp = public_id("file"), now()
        with self.lock, self.connect() as con:
            self.require_project(project_id, con)
            con.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)", (file_id, project_id, filename, suffix, str(stored), sha256, size, preview, stamp))
            con.execute("UPDATE projects SET updated_at=? WHERE id=?", (stamp, project_id))
            self.audit(con, project_id, "file.uploaded", {"id": file_id, "filename": filename, "sha256": sha256, "bytes": size})
        return {"id": file_id, "project_id": project_id, "filename": filename, "suffix": suffix, "sha256": sha256, "bytes": size, "preview": preview, "created_at": stamp}

    def file_path(self, file_id: str) -> tuple[Path, str]:
        with self.connect() as con:
            row = con.execute("SELECT stored_path,filename FROM files WHERE id=?", (file_id,)).fetchone()
        if not row:
            raise HTTPException(404, "file not found")
        return within(FILE_ROOT, Path(row["stored_path"])), str(row["filename"])

    def project_file_path(self, project_id: str, file_id: str) -> tuple[Path, str]:
        with self.connect() as con:
            row = con.execute("SELECT stored_path,filename,project_id FROM files WHERE id=?", (file_id,)).fetchone()
        if not row or row["project_id"] != project_id:
            raise HTTPException(404, "project file not found")
        return within(FILE_ROOT, Path(row["stored_path"])), str(row["filename"])

    def add_vision(self, project_id: str, file_id: str, provider: str, model: str, prompt: str, result: str) -> dict[str, Any]:
        analysis_id, stamp = public_id("vision"), now()
        with self.lock, self.connect() as con:
            self.require_project(project_id, con)
            con.execute("INSERT INTO vision_analyses VALUES(?,?,?,?,?,?,?,?)", (analysis_id, project_id, file_id, provider, model, prompt, result, stamp))
            con.execute("UPDATE projects SET updated_at=? WHERE id=?", (stamp, project_id))
            self.audit(con, project_id, "vision.external_analysis", {"id": analysis_id, "file_id": file_id, "provider": provider, "model": model})
        return {"id": analysis_id, "project_id": project_id, "file_id": file_id, "provider": provider,
                "model": model, "prompt": prompt, "result": result, "created_at": stamp,
                "disclaimer": "Unverified visual interpretation. It is not a dimensioned drawing, material certificate or safety validation."}

    def request_approval(self, project_id: str, payload: ApprovalCreate) -> dict[str, Any]:
        approval_id, stamp = public_id("approval"), now()
        with self.lock, self.connect() as con:
            self.require_project(project_id, con)
            con.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?)", (
                approval_id, project_id, payload.action, payload.target.strip(), payload.summary.strip(),
                json.dumps(payload.payload, sort_keys=True), "pending", stamp, None, None))
            con.execute("UPDATE projects SET updated_at=? WHERE id=?", (stamp, project_id))
            self.audit(con, project_id, "approval.requested", {"id": approval_id, "action": payload.action, "target": payload.target})
        return {"id": approval_id, "project_id": project_id, **payload.model_dump(), "state": "pending", "created_at": stamp,
                "note": "Approval changes the request state only. No browser or connector action is executed by this service."}

    def resolve_approval(self, approval_id: str, approve: bool) -> dict[str, Any]:
        stamp, state = now(), "approved" if approve else "declined"
        with self.lock, self.connect() as con:
            row = con.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if not row:
                raise HTTPException(404, "approval not found")
            if row["state"] != "pending":
                raise HTTPException(409, f"approval is already {row['state']}")
            note = "authorized for a future configured bridge; no action executed" if approve else "declined by local owner"
            con.execute("UPDATE approvals SET state=?,resolved_at=?,resolution_note=? WHERE id=?", (state, stamp, note, approval_id))
            self.audit(con, row["project_id"], "approval." + state, {"id": approval_id, "action": row["action"], "target": row["target"]})
            result = con.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return self._approval(result)


def read_limited(upload: UploadFile) -> tuple[bytes, str]:
    content = bytearray()
    while True:
        block = upload.file.read(1024 * 1024)
        if not block:
            break
        content.extend(block)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB")
    if not content:
        raise HTTPException(422, "empty upload")
    raw = bytes(content)
    return raw, hashlib.sha256(raw).hexdigest()


def clipped(text: str) -> str:
    text = "\n".join(line.rstrip() for line in text.replace("\x00", "").splitlines()).strip()
    return text[:MAX_PREVIEW_CHARS] + ("\n[preview clipped]" if len(text) > MAX_PREVIEW_CHARS else "")


def preview_file(path: Path, suffix: str) -> str:
    """Extract a bounded, local preview; never sends a file to a model/provider."""
    try:
        if suffix in TEXT_EXTENSIONS:
            return clipped(path.read_text(encoding="utf-8", errors="replace"))
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages[:12])
            return clipped(f"PDF: {len(reader.pages)} page(s)\n{text}")
        if suffix == ".docx":
            from docx import Document
            document = Document(str(path))
            return clipped("\n".join(paragraph.text for paragraph in document.paragraphs))
        if suffix == ".xlsx":
            from openpyxl import load_workbook
            workbook = load_workbook(path, read_only=True, data_only=True)
            rows: list[str] = []
            for sheet in workbook.worksheets[:8]:
                rows.append(f"[sheet: {sheet.title}]")
                for index, row in enumerate(sheet.iter_rows(values_only=True)):
                    rows.append(" | ".join("" if value is None else str(value) for value in row[:20]))
                    if index >= 100:
                        rows.append("[sheet preview clipped]"); break
            return clipped("\n".join(rows))
        if suffix == ".pptx":
            with zipfile.ZipFile(path) as archive:
                names = sorted(name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml"))[:30]
                parts = []
                for name in names:
                    raw = archive.read(name).decode("utf-8", errors="ignore")
                    words = re.findall(r"<a:t>(.*?)</a:t>", raw)
                    parts.append(" ".join(words))
            return clipped("\n".join(f"[slide {i + 1}] {value}" for i, value in enumerate(parts)))
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            from PIL import Image
            with Image.open(path) as image:
                return f"Image: {image.width} × {image.height} px; retained locally. Vision analysis requires an enabled multimodal model and an explicit send-to-model action."
        if suffix in {".step", ".stp", ".stl", ".ifc", ".iges", ".igs"}:
            return "Engineering/CAD file retained with checksum. It is reference data until a format-specific importer validates its geometry and units."
        return "Binary file retained locally; no preview extractor is configured."
    except Exception as exc:
        return f"Preview unavailable: {type(exc).__name__}: {exc}"


def capability_map() -> dict[str, Any]:
    return {
        "project_memory": {"state": "ready", "scope": "requirements, constraints, decisions, risks and notes in SQLite"},
        "provenance": {"state": "ready", "scope": "citations, checksums, file previews and audit events"},
        "files": {"state": "ready", "scope": "local PDF, DOCX, XLSX, PPTX, text, image and CAD retention/preview"},
        "vision": {"state": "ready_with_confirmation", "scope": "OpenRouter vision request requires confirm_external_send=true; result is stored as unverified evidence"},
        "browser": {"state": "local_worker_ready", "scope": "fresh-profile worker is at localhost:8105; each navigation/action requires explicit confirmation and password/login automation is blocked"},
        "connectors": {"state": "needs_authorization", "scope": "GitHub, Drive, OneDrive, email, calendar and databases require per-service OAuth/API credentials"},
        "accounts": {"state": "local_owner_only", "scope": "public multi-user login/roles are intentionally not enabled"},
        "deployment": {"state": "localhost_only", "scope": "TLS, domain/reverse proxy and external access are not configured"},
    }


def page() -> str:
    return r'''<!doctype html><meta charset="utf-8"><title>EngineAI Project Hub</title><style>
body{margin:28px auto;max-width:1100px;background:#0c1320;color:#e1edf8;font:14px system-ui}main{display:grid;grid-template-columns:330px 1fr;gap:18px}section{background:#111f30;border:1px solid #27445e;border-radius:8px;padding:15px}input,textarea,select,button{width:100%;box-sizing:border-box;margin:5px 0;padding:8px;background:#0a1522;border:1px solid #385875;color:#e1edf8;border-radius:5px}button{background:#1c5684;cursor:pointer;font-weight:650}.muted{color:#91a9c0}.card{border-top:1px solid #29465e;padding:9px 0;white-space:pre-wrap}.pending{color:#ffcf71}pre{white-space:pre-wrap;max-height:220px;overflow:auto}</style>
<h1>EngineAI Project Hub <small class="muted">local memory, provenance and approval gates</small></h1><main><section><h2>Project</h2><input id=name placeholder="Project name"><textarea id=desc placeholder="Description"></textarea><button id=create>Create project</button><select id=projects size=10></select><h2>Add memory</h2><select id=kind><option>requirement</option><option>constraint</option><option>decision</option><option>risk</option><option>note</option></select><textarea id=memory placeholder="Record a decision, constraint or requirement"></textarea><button id=addMemory>Add memory</button><h2>Attach file</h2><input id=file type=file><button id=upload>Upload locally</button></section><section><div id=status class=muted>Loading…</div><h2>Project evidence</h2><div id=detail></div><h2>Capabilities</h2><pre id=caps></pre></section></main><script>
let project=null;const $=id=>document.getElementById(id);const api=async(path,opt={})=>{const r=await fetch(path,opt),d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.detail||r.statusText);return d};const stat=t=>$('status').textContent=t;
async function refresh(){const [rows,caps]=await Promise.all([api('/api/projects'),api('/api/capabilities')]);$('caps').textContent=JSON.stringify(caps,null,2);$('projects').replaceChildren(...rows.map(p=>{const o=document.createElement('option');o.value=p.id;o.textContent=p.name;return o}));if(project){await load(project.id)}else if(rows[0]){$('projects').value=rows[0].id;await load(rows[0].id)}}
function item(label,value){const d=document.createElement('div');d.className='card';const b=document.createElement('b');b.textContent=label;d.append(b,document.createElement('br'),document.createTextNode(value));return d}
async function load(id){project=await api('/api/projects/'+id);$('projects').value=id;const d=$('detail');d.replaceChildren(item(project.name,project.description||'No description.'));for(const m of project.memories)d.append(item(m.kind+' · '+m.source,m.text));for(const c of project.citations)d.append(item('citation · '+c.verification,c.title+'\n'+c.locator));for(const f of project.files){const el=item('file · '+f.filename,f.preview);const a=document.createElement('a');a.href='/api/files/'+f.id;a.textContent='download';el.append(document.createElement('br'),a);d.append(el)}for(const a of project.approvals)d.append(item('approval · '+a.state,a.action+' → '+a.target+'\n'+a.summary));stat('Project loaded: '+project.name)}
$('projects').onchange=e=>load(e.target.value).catch(e=>stat(e.message));$('create').onclick=async()=>{try{project=await api('/api/projects',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:$('name').value,description:$('desc').value})});$('name').value='';$('desc').value='';await refresh()}catch(e){stat(e.message)}};$('addMemory').onclick=async()=>{try{if(!project)throw Error('Create or select a project first');await api('/api/projects/'+project.id+'/memories',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({kind:$('kind').value,text:$('memory').value})});$('memory').value='';await load(project.id)}catch(e){stat(e.message)}};$('upload').onclick=async()=>{try{if(!project)throw Error('Create or select a project first');const f=$('file').files[0];if(!f)throw Error('Choose a file');const data=new FormData();data.append('file',f);await api('/api/projects/'+project.id+'/files',{method:'POST',body:data});await load(project.id)}catch(e){stat(e.message)}};refresh().catch(e=>stat(e.message));
</script>'''


def make_app() -> FastAPI:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    FILE_ROOT.mkdir(parents=True, exist_ok=True)
    repository = Repository(DATABASE)
    app = FastAPI(title=APP_NAME, version="1.0")

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return page()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": APP_NAME, "root": str(ENGINE_ROOT), "upload_limit_bytes": MAX_UPLOAD_BYTES,
                "external_actions": "never executed by this service"}

    @app.get("/api/capabilities")
    def capabilities() -> dict[str, Any]:
        return capability_map()

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        return repository.projects()

    @app.post("/api/projects", status_code=201)
    def create_project(payload: ProjectCreate) -> dict[str, Any]:
        return repository.create_project(payload)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        return repository.project(project_id)

    @app.post("/api/projects/{project_id}/memories", status_code=201)
    def add_memory(project_id: str, payload: MemoryCreate) -> dict[str, Any]:
        return repository.add_memory(project_id, payload)

    @app.post("/api/projects/{project_id}/citations", status_code=201)
    def add_citation(project_id: str, payload: CitationCreate) -> dict[str, Any]:
        return repository.add_citation(project_id, payload)

    @app.post("/api/projects/{project_id}/files", status_code=201)
    async def upload_file(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        repository.require_project(project_id)
        filename = checked_name(file.filename or "upload")
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise HTTPException(415, "supported extensions: " + ", ".join(sorted(SUPPORTED_EXTENSIONS)))
        raw, digest = read_limited(file)
        folder = within(FILE_ROOT, FILE_ROOT / project_id)
        folder.mkdir(parents=True, exist_ok=True)
        stored = within(FILE_ROOT, folder / f"{uuid.uuid4().hex}{suffix}")
        stored.write_bytes(raw)
        return repository.add_file(project_id, filename, suffix, stored, digest, len(raw), preview_file(stored, suffix))

    @app.get("/api/files/{file_id}")
    def download_file(file_id: str) -> FileResponse:
        path, filename = repository.file_path(file_id)
        return FileResponse(path, filename=filename, media_type="application/octet-stream")

    @app.post("/api/projects/{project_id}/files/{file_id}/vision", status_code=201)
    def analyse_image(project_id: str, file_id: str, payload: VisionCreate) -> dict[str, Any]:
        # Pydantic's Literal[True] is intentional: an accidental/default call
        # cannot transmit an uploaded image to a hosted model.
        path, _name = repository.project_file_path(project_id, file_id)
        try:
            from vision_tools import analyze
            response = analyze(path, payload.prompt, provider=payload.provider, model=payload.model)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"vision provider failed: {type(exc).__name__}: {exc}") from exc
        return repository.add_vision(project_id, file_id, response["provider"], response["model"], payload.prompt, response["result"])

    @app.post("/api/projects/{project_id}/approvals", status_code=201)
    def request_approval(project_id: str, payload: ApprovalCreate) -> dict[str, Any]:
        return repository.request_approval(project_id, payload)

    @app.post("/api/approvals/{approval_id}/approve")
    def approve(approval_id: str) -> dict[str, Any]:
        return repository.resolve_approval(approval_id, True)

    @app.post("/api/approvals/{approval_id}/decline")
    def decline(approval_id: str) -> dict[str, Any]:
        return repository.resolve_approval(approval_id, False)

    return app


def selftest() -> bool:
    global ENGINE_ROOT, WORK_ROOT, FILE_ROOT, DATABASE
    original = ENGINE_ROOT, WORK_ROOT, FILE_ROOT, DATABASE
    try:
        with tempfile.TemporaryDirectory(prefix="engineai_hub_") as temp:
            ENGINE_ROOT = Path(temp)
            WORK_ROOT, FILE_ROOT = ENGINE_ROOT / "work", ENGINE_ROOT / "work" / "files"
            DATABASE = WORK_ROOT / "hub.sqlite3"
            repository = Repository(DATABASE)
            project = repository.create_project(ProjectCreate(name="Pump redesign", description="Evidence-led revision"))
            memory = repository.add_memory(project["id"], MemoryCreate(kind="constraint", text="Use SI units throughout."))
            citation = repository.add_citation(project["id"], CitationCreate(title="Roark Table 8.1", locator="book page 123", source_type="book", verification="checked"))
            stored = FILE_ROOT / project["id"] / "note.md"; stored.parent.mkdir(parents=True); stored.write_text("# Requirement\nRecord source and units.")
            attached = repository.add_file(project["id"], "note.md", ".md", stored, hashlib.sha256(stored.read_bytes()).hexdigest(), stored.stat().st_size, preview_file(stored, ".md"))
            approval = repository.request_approval(project["id"], ApprovalCreate(action="browser_download", target="https://example.test/report", summary="Download a public report"))
            assert memory["kind"] == "constraint" and citation["verification"] == "checked"
            assert "Record source" in attached["preview"] and approval["state"] == "pending"
            resolved = repository.resolve_approval(approval["id"], True)
            assert resolved["state"] == "approved" and "no action executed" in resolved["resolution_note"]
            result = repository.project(project["id"])
            assert len(result["memories"]) == len(result["citations"]) == len(result["files"]) == len(result["approvals"]) == 1
        print("project hub selftest: PASSED")
        return True
    except Exception as exc:
        print(f"project hub selftest: FAILED: {type(exc).__name__}: {exc}")
        return False
    finally:
        ENGINE_ROOT, WORK_ROOT, FILE_ROOT, DATABASE = original


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME, allow_abbrev=False)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8103)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return 0 if selftest() else 1
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("WARNING: public binding needs authentication, TLS, rate limits and an independent security review.")
    import uvicorn
    uvicorn.run(make_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
