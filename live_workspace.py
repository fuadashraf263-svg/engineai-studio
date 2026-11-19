#!/usr/bin/env python3
"""EngineAI Live Workspace - safe, local parametric CAD and 3-D review.

This is a companion service for the existing EngineAI Studio.  It deliberately
does not pretend that every CAD file can be edited: native editable projects
are stored as versioned parameter specifications; imported STEP/STL/DXF/IFC
files are preserved as *view/reference* assets until an explicit converter is
implemented and verified.

Run (Ubuntu/WSL):
  conda activate eng
  python engineai_live_workspace.py --selftest
  python engineai_live_workspace.py
Then open http://127.0.0.1:8101.  The browser fetches Three.js from a pinned
public CDN.  Use --host 127.0.0.1 unless a reverse proxy, authentication and
TLS have been configured.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field


APP_NAME = "EngineAI Live Workspace"
MAX_UPLOAD_BYTES = int(os.environ.get("ENGINEAI_MAX_UPLOAD_BYTES", 100 * 1024 * 1024))
ENGINE_ROOT = Path(os.environ.get("ENGINEAI_ROOT", str(Path.home() / "engineai"))).resolve()
WORK_ROOT = (ENGINE_ROOT / "work" / "live_workspace").resolve()
ARTIFACT_ROOT = (ENGINE_ROOT / "out" / "live_workspace").resolve()
DATABASE = WORK_ROOT / "projects.sqlite3"
ALLOWED_IMPORTS = {".step", ".stp", ".stl", ".dxf", ".ifc", ".iges", ".igs"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,79}$")


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=1000)


class DesignSpec(BaseModel):
    """Only simple, civilian, parametric part families are generated here."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["plate", "bracket", "enclosure", "flange_tube"]
    name: str = Field(default="part", min_length=1, max_length=80)
    units: Literal["mm"] = "mm"
    parameters: dict[str, float] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=1000)


class RevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: DesignSpec
    message: str = Field(default="", max_length=300)


@dataclass(frozen=True)
class BuiltArtifact:
    directory: Path
    stl: Path
    step: Path
    manifest: Path


def now() -> int:
    return int(time.time())


def public_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def checked_part_name(value: str) -> str:
    value = value.strip()
    if not SAFE_NAME.fullmatch(value) or value in {".", ".."}:
        raise ValueError("name must contain only letters, digits, spaces, dot, underscore or hyphen")
    return value


def within(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(403, "path escapes the workspace")
    return candidate


def finite(value: Any, label: str, lo: float, hi: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not math.isfinite(result) or not lo <= result <= hi:
        raise ValueError(f"{label} must be between {lo:g} and {hi:g} mm")
    return result


def parameters(spec: DesignSpec) -> dict[str, float]:
    """Validate dimensions before CadQuery sees them.  Values are millimetres."""
    p = {str(k): v for k, v in spec.parameters.items()}
    defaults: dict[str, dict[str, float]] = {
        "plate": {"width": 100, "depth": 70, "thickness": 6, "hole_diameter": 8},
        "bracket": {"width": 100, "depth": 60, "base_thickness": 8, "wall_height": 80,
                    "wall_thickness": 8, "hole_diameter": 8},
        "enclosure": {"width": 120, "depth": 80, "height": 45, "wall": 3},
        "flange_tube": {"length": 100, "outer_diameter": 50, "inner_diameter": 38,
                        "flange_diameter": 80, "flange_thickness": 8, "bolt_diameter": 6},
    }
    allowed = set(defaults[spec.kind])
    extra = sorted(set(p) - allowed)
    if extra:
        raise ValueError(f"unsupported parameters for {spec.kind}: {', '.join(extra)}")
    q = {key: finite(p.get(key, value), key, 0.2, 2000) for key, value in defaults[spec.kind].items()}
    if spec.kind == "plate":
        if q["hole_diameter"] >= min(q["width"], q["depth"]) * 0.45:
            raise ValueError("hole_diameter is too large for the plate")
    elif spec.kind == "bracket":
        if q["base_thickness"] >= q["wall_height"] or q["wall_thickness"] >= q["width"]:
            raise ValueError("bracket wall dimensions are inconsistent")
        if q["hole_diameter"] >= min(q["width"], q["depth"], q["wall_height"]) * 0.35:
            raise ValueError("hole_diameter is too large for this bracket")
    elif spec.kind == "enclosure":
        if 2 * q["wall"] >= min(q["width"], q["depth"], q["height"]):
            raise ValueError("wall leaves no internal cavity")
    else:
        if not q["inner_diameter"] < q["outer_diameter"] < q["flange_diameter"]:
            raise ValueError("require inner_diameter < outer_diameter < flange_diameter")
        if q["bolt_diameter"] >= q["flange_diameter"] / 5:
            raise ValueError("bolt_diameter is too large for the flange")
    return q


def build_cad(spec: DesignSpec, destination: Path) -> BuiltArtifact:
    """Generate a native CadQuery solid and both manufacturing interchange files."""
    try:
        import cadquery as cq
        from cadquery import exporters
    except ImportError as exc:
        raise RuntimeError("CadQuery is required: conda install -n eng -c conda-forge cadquery") from exc

    p = parameters(spec)
    destination.mkdir(parents=True, exist_ok=True)
    stem = checked_part_name(spec.name).replace(" ", "_")
    if spec.kind == "plate":
        solid = cq.Workplane("XY").box(p["width"], p["depth"], p["thickness"])
        offset_x, offset_y = p["width"] * 0.30, p["depth"] * 0.30
        solid = (solid.faces(">Z").workplane()
                 .pushPoints([(x, y) for x in (-offset_x, offset_x) for y in (-offset_y, offset_y)])
                 .hole(p["hole_diameter"]))
    elif spec.kind == "bracket":
        base = cq.Workplane("XY").box(p["width"], p["depth"], p["base_thickness"])
        base = (base.faces(">Z").workplane()
                .pushPoints([(x, y) for x in (-p["width"] * .30, p["width"] * .30)
                              for y in (-p["depth"] * .30, p["depth"] * .30)])
                .hole(p["hole_diameter"]))
        wall = (cq.Workplane("XY").box(p["wall_thickness"], p["depth"], p["wall_height"])
                .translate((-p["width"] / 2 + p["wall_thickness"] / 2, 0,
                            (p["wall_height"] - p["base_thickness"]) / 2)))
        wall = wall.faces(">X").workplane().hole(p["hole_diameter"])
        solid = base.union(wall)
    elif spec.kind == "enclosure":
        outer = cq.Workplane("XY").box(p["width"], p["depth"], p["height"])
        inner = (cq.Workplane("XY").box(p["width"] - 2 * p["wall"], p["depth"] - 2 * p["wall"],
                                        p["height"] - p["wall"])
                 .translate((0, 0, p["wall"] / 2)))
        solid = outer.cut(inner)
    else:
        body = (cq.Workplane("XY").circle(p["outer_diameter"] / 2)
                .circle(p["inner_diameter"] / 2).extrude(p["length"]))
        flange = (cq.Workplane("XY").circle(p["flange_diameter"] / 2)
                  .circle(p["inner_diameter"] / 2).extrude(p["flange_thickness"]))
        solid = body.union(flange)
        radius = (p["outer_diameter"] + p["flange_diameter"]) / 4
        solid = (solid.faces("<Z").workplane().polarArray(radius, 0, 360, 4)
                 .hole(p["bolt_diameter"]))

    stl = destination / f"{stem}.stl"
    step = destination / f"{stem}.step"
    exporters.export(solid, str(stl), tolerance=0.05, angularTolerance=0.1)
    exporters.export(solid, str(step))
    manifest = destination / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": 1, "generated_at": now(), "generator": "CadQuery",
        "spec": spec.model_dump(), "parameters_mm": p,
        "artifacts": {"stl": stl.name, "step": step.name},
        "disclaimer": "Geometry only. Verify dimensions, material, loads, mesh and safety factors before use.",
    }, indent=2), encoding="utf-8")
    return BuiltArtifact(destination, stl, step, manifest)


class Repository:
    def __init__(self, db: Path):
        db.parent.mkdir(parents=True, exist_ok=True)
        self.db = db
        self.lock = threading.Lock()
        with self.connect() as con:
            con.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS projects(
                id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS revisions(
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                parent_id TEXT, spec_json TEXT NOT NULL, message TEXT NOT NULL,
                status TEXT NOT NULL, artifact_dir TEXT, error TEXT, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
                state TEXT NOT NULL, error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS assets(
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                filename TEXT NOT NULL, stored_path TEXT NOT NULL, format TEXT NOT NULL,
                sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, created_at INTEGER NOT NULL);
            """)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    @staticmethod
    def as_project(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def create_project(self, data: ProjectCreate) -> dict[str, Any]:
        pid, stamp = public_id("prj"), now()
        with self.lock, self.connect() as con:
            con.execute("INSERT INTO projects VALUES(?,?,?,?,?)", (pid, data.name.strip(), data.description.strip(), stamp, stamp))
        return self.get_project(pid)

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self.connect() as con:
            project = con.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise HTTPException(404, "project not found")
            result = self.as_project(project)
            result["revisions"] = [self.revision_row(r) for r in con.execute(
                "SELECT * FROM revisions WHERE project_id=? ORDER BY created_at DESC", (project_id,))]
            result["assets"] = [dict(r) for r in con.execute(
                "SELECT id, filename, format, sha256, bytes, created_at FROM assets WHERE project_id=? ORDER BY created_at DESC", (project_id,))]
            return result

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [self.as_project(r) for r in con.execute("SELECT * FROM projects ORDER BY updated_at DESC")]

    @staticmethod
    def revision_row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["spec"] = json.loads(d.pop("spec_json"))
        return d

    def add_revision(self, project_id: str, payload: RevisionCreate) -> dict[str, Any]:
        self.get_project(project_id)
        try:
            parameters(payload.spec)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        rid, stamp = public_id("rev"), now()
        with self.lock, self.connect() as con:
            parent = con.execute("SELECT id FROM revisions WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
            con.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?)", (
                rid, project_id, parent["id"] if parent else None,
                json.dumps(payload.spec.model_dump(), sort_keys=True), payload.message.strip(),
                "draft", None, None, stamp))
            con.execute("UPDATE projects SET updated_at=? WHERE id=?", (stamp, project_id))
        return self.get_revision(rid)

    def get_revision(self, revision_id: str) -> dict[str, Any]:
        with self.connect() as con:
            row = con.execute("SELECT * FROM revisions WHERE id=?", (revision_id,)).fetchone()
            if not row:
                raise HTTPException(404, "revision not found")
            return self.revision_row(row)

    def set_revision(self, revision_id: str, status: str, artifact_dir: Path | None = None, error: str | None = None) -> None:
        with self.lock, self.connect() as con:
            con.execute("UPDATE revisions SET status=?, artifact_dir=?, error=? WHERE id=?", (
                status, str(artifact_dir) if artifact_dir else None, error, revision_id))

    def new_job(self, revision_id: str) -> str:
        job, stamp = public_id("job"), now()
        with self.lock, self.connect() as con:
            con.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", (job, revision_id, "queued", None, stamp, stamp))
        return job

    def update_job(self, job_id: str, state: str, error: str | None = None) -> None:
        with self.lock, self.connect() as con:
            con.execute("UPDATE jobs SET state=?, error=?, updated_at=? WHERE id=?", (state, error, now(), job_id))

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, "job not found")
            return dict(row)

    def add_asset(self, project_id: str, original_name: str, stored: Path, suffix: str, digest: str, size: int) -> dict[str, Any]:
        aid, stamp = public_id("asset"), now()
        with self.lock, self.connect() as con:
            con.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?,?)", (aid, project_id, original_name, str(stored), suffix, digest, size, stamp))
            con.execute("UPDATE projects SET updated_at=? WHERE id=?", (stamp, project_id))
        return {"id": aid, "filename": original_name, "format": suffix, "sha256": digest, "bytes": size, "created_at": stamp,
                "mode": "reference_only", "note": "Imported files are retained safely; direct editing requires a verified converter."}

    def asset_path(self, asset_id: str) -> tuple[Path, str]:
        with self.connect() as con:
            row = con.execute("SELECT stored_path, filename FROM assets WHERE id=?", (asset_id,)).fetchone()
            if not row:
                raise HTTPException(404, "asset not found")
        return within(WORK_ROOT, Path(row["stored_path"])), row["filename"]


class Jobs:
    def __init__(self, repo: Repository):
        self.repo = repo
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="engineai-cad")

    def submit_build(self, revision_id: str) -> str:
        revision = self.repo.get_revision(revision_id)
        if revision["status"] == "building":
            raise HTTPException(409, "revision is already building")
        job = self.repo.new_job(revision_id)
        self.repo.set_revision(revision_id, "building")
        self.pool.submit(self._build, job, revision_id, revision["spec"])
        return job

    def _build(self, job: str, revision_id: str, raw_spec: dict[str, Any]) -> None:
        self.repo.update_job(job, "running")
        try:
            spec = DesignSpec.model_validate(raw_spec)
            artifact_dir = ARTIFACT_ROOT / revision_id
            artifact = build_cad(spec, artifact_dir)
            self.repo.set_revision(revision_id, "ready", artifact.directory)
            self.repo.update_job(job, "completed")
        except Exception as exc:  # stored and shown to the project owner, never silently hidden
            message = f"{type(exc).__name__}: {exc}"
            self.repo.set_revision(revision_id, "failed", error=message)
            self.repo.update_job(job, "failed", error=message)


def revision_file(repo: Repository, revision_id: str, filename: str) -> Path:
    if not SAFE_NAME.fullmatch(filename):
        raise HTTPException(404, "invalid filename")
    revision = repo.get_revision(revision_id)
    if revision["status"] != "ready" or not revision["artifact_dir"]:
        raise HTTPException(409, "revision is not ready")
    path = within(ARTIFACT_ROOT, Path(revision["artifact_dir"]) / filename)
    if not path.is_file():
        raise HTTPException(404, "file not found")
    return path


def read_limited(upload: UploadFile) -> tuple[bytes, str]:
    content = bytearray()
    while True:
        block = upload.file.read(1024 * 1024)
        if not block:
            break
        content.extend(block)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit")
    return bytes(content), hashlib.sha256(content).hexdigest()


def page() -> str:
    # The UI contains no string interpolation from users.  Text rendered into
    # the DOM uses textContent, avoiding an XSS path from project/part names.
    return r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EngineAI Live Workspace</title><style>
:root{color-scheme:dark;--bg:#09111c;--panel:#101d2c;--edge:#29455e;--ink:#dcecff;--muted:#91a9c0;--blue:#58a6ff;--bad:#ff8383;--ok:#52d39a}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px system-ui,sans-serif}header{height:58px;padding:15px 22px;border-bottom:1px solid var(--edge);font-size:18px;font-weight:700}header small{color:var(--muted);font-weight:400;margin-left:10px}.app{display:grid;grid-template-columns:360px 1fr;height:calc(100vh - 58px)}aside{overflow:auto;padding:18px;border-right:1px solid var(--edge);background:#0c1725}main{min-width:0;display:grid;grid-template-rows:1fr 220px}.view{position:relative;min-height:0}.hint{position:absolute;z-index:2;top:12px;left:14px;color:var(--muted);background:#0a1320c9;border:1px solid var(--edge);padding:7px 9px;border-radius:7px}#canvas{width:100%;height:100%;display:block}.bottom{overflow:auto;border-top:1px solid var(--edge);padding:16px;background:#0c1725}h2{font-size:14px;margin:16px 0 7px;color:#b9d7f5}h2:first-child{margin-top:0}label{display:block;color:var(--muted);font-size:12px;margin:7px 0 3px}input,select,textarea,button{width:100%;border:1px solid var(--edge);border-radius:6px;background:#0a1320;color:var(--ink);padding:9px;font:inherit}textarea{min-height:64px;resize:vertical}button{background:#16436b;cursor:pointer;font-weight:650;margin-top:9px}button:hover{background:#1d5689}button.alt{background:transparent}.row{display:flex;gap:8px}.row>*{flex:1}.card{border:1px solid var(--edge);border-radius:8px;padding:10px;margin:8px 0;background:#0b1624}.muted{color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}.files a{color:var(--blue);margin-right:12px}.params{display:grid;grid-template-columns:1fr 1fr;gap:7px}.params label{margin:0}.params input{margin-top:3px}.hidden{display:none}@media(max-width:760px){.app{grid-template-columns:1fr;grid-template-rows:auto 1fr}aside{border-right:0;border-bottom:1px solid var(--edge);max-height:45vh}main{height:65vh}}
</style></head><body><header>EngineAI Live Workspace <small>versioned parametric CAD - local only</small></header><div class="app"><aside>
<h2>Project</h2><div class="row"><input id="newName" placeholder="Project name"><button id="create" style="max-width:95px">Create</button></div><select id="projects" size="6" aria-label="projects"></select><div id="projectInfo" class="muted">Create a project to begin.</div>
<h2>Native editable design</h2><label>Part family<select id="kind"><option value="bracket">L bracket</option><option value="enclosure">Enclosure</option><option value="flange_tube">Flange tube</option><option value="plate">Mounting plate</option></select></label><label>Part name<input id="partName" value="bracket"></label><div id="params" class="params"></div><label>Revision note<textarea id="note" placeholder="What changed and why?"></textarea></label><button id="saveBuild">Save revision and build</button><div id="status" class="muted"></div>
<h2>External CAD/reference file</h2><input id="upload" type="file" accept=".step,.stp,.stl,.dxf,.ifc,.iges,.igs"><button id="sendAsset" class="alt">Attach reference file</button><p class="muted">External files are retained and download-ready. They are not falsely labelled editable until their converter is verified.</p>
</aside><main><section class="view"><div class="hint">Left drag orbit - wheel zoom - middle drag pan</div><canvas id="canvas"></canvas></section><section class="bottom"><h2>Project history</h2><div id="history" class="muted">No project selected.</div></section></main></div>
<script type="module">
import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js';
import {OrbitControls} from 'https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/controls/OrbitControls.js';
import {STLLoader} from 'https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/loaders/STLLoader.js';
const fields={plate:{width:100,depth:70,thickness:6,hole_diameter:8},bracket:{width:100,depth:60,base_thickness:8,wall_height:80,wall_thickness:8,hole_diameter:8},enclosure:{width:120,depth:80,height:45,wall:3},flange_tube:{length:100,outer_diameter:50,inner_diameter:38,flange_diameter:80,flange_thickness:8,bolt_diameter:6}};
let project=null,mesh=null,jobTimer=null;const $=id=>document.getElementById(id);const api=async(path,opts={})=>{const r=await fetch(path,opts);const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.detail||r.statusText);return d};
const status=(m,bad=false)=>{$('status').textContent=m;$('status').className=bad?'bad':'muted'};
function params(){const f=fields[$('kind').value];$('params').replaceChildren(...Object.entries(f).map(([k,v])=>{const l=document.createElement('label'),i=document.createElement('input');i.type='number';i.step='any';i.value=v;i.dataset.key=k;l.textContent=k.replace('_',' ');l.append(i);return l}));$('partName').value=$('kind').value}
function scene(){const canvas=$('canvas'),renderer=new THREE.WebGLRenderer({canvas,antialias:true});renderer.setPixelRatio(devicePixelRatio);const sc=new THREE.Scene();sc.background=new THREE.Color(0x09111c);const camera=new THREE.PerspectiveCamera(45,1,.1,10000);camera.position.set(180,150,180);const controls=new OrbitControls(camera,canvas);controls.enableDamping=true;sc.add(new THREE.HemisphereLight(0xc5e2ff,0x1d2935,2));const light=new THREE.DirectionalLight(0xffffff,2.5);light.position.set(200,250,180);sc.add(light,new THREE.GridHelper(300,30,0x35526e,0x1d3348));function resize(){const w=canvas.clientWidth,h=canvas.clientHeight;if(canvas.width!==w||canvas.height!==h){renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix()}}function draw(){resize();controls.update();renderer.render(sc,camera);requestAnimationFrame(draw)}draw();return {sc,camera,controls}}
const view=scene();
function showStl(url){new STLLoader().load(url,g=>{if(mesh)view.sc.remove(mesh);g.computeVertexNormals();const material=new THREE.MeshStandardMaterial({color:0x68b5f5,metalness:.3,roughness:.38});mesh=new THREE.Mesh(g,material);g.computeBoundingBox();const box=g.boundingBox,size=box.getSize(new THREE.Vector3()).length(),center=box.getCenter(new THREE.Vector3());mesh.position.sub(center);view.sc.add(mesh);view.camera.position.set(size*1.2,size*.9,size*1.2);view.controls.target.set(0,0,0);view.controls.update()},undefined,e=>status('Could not load STL: '+e,true))}
async function loadProjects(){const rows=await api('/api/projects');$('projects').replaceChildren(...rows.map(p=>{const o=document.createElement('option');o.value=p.id;o.textContent=p.name;return o}));if(rows.length&&!project){$('projects').value=rows[0].id;await selectProject(rows[0].id)}}
function fileLink(url,label){const a=document.createElement('a');a.href=url;a.textContent=label;a.target='_blank';return a}
async function selectProject(id){project=await api('/api/projects/'+id);$('projectInfo').textContent=project.description||'No description.';const h=$('history');h.replaceChildren();for(const r of project.revisions){const c=document.createElement('div');c.className='card';const title=document.createElement('b');title.textContent=`${r.id} - ${r.status} - ${r.spec.kind}`;c.append(title,document.createElement('br'));const note=document.createElement('span');note.className='muted';note.textContent=r.message||'No revision note.';c.append(note);if(r.status==='ready'){const files=document.createElement('div');files.className='files';files.append(document.createTextNode(' '),fileLink(`/api/revisions/${r.id}/files/${r.spec.name.replaceAll(' ','_')}.stl`,'view STL'),fileLink(`/api/revisions/${r.id}/files/${r.spec.name.replaceAll(' ','_')}.step`,'download STEP'));c.append(document.createElement('br'),files);c.onclick=()=>showStl(`/api/revisions/${r.id}/files/${r.spec.name.replaceAll(' ','_')}.stl`)}if(r.error){const er=document.createElement('div');er.className='bad';er.textContent=r.error;c.append(er)}h.append(c)}for(const a of project.assets){const c=document.createElement('div');c.className='card muted';c.append(document.createTextNode(`${a.filename} (${a.format}) - reference only `),fileLink(`/api/assets/${a.id}`,'download'));h.append(c)}}
$('kind').onchange=params;params();$('projects').onchange=e=>selectProject(e.target.value).catch(err=>status(err.message,true));
$('create').onclick=async()=>{try{const name=$('newName').value.trim();if(!name)throw Error('Enter a project name');const p=await api('/api/projects',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name})});project=p;$('newName').value='';await loadProjects();$('projects').value=p.id;await selectProject(p.id)}catch(e){status(e.message,true)}};
$('saveBuild').onclick=async()=>{try{if(!project)throw Error('Create or select a project first');const p={};document.querySelectorAll('#params input').forEach(i=>p[i.dataset.key]=Number(i.value));const rev=await api(`/api/projects/${project.id}/revisions`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({spec:{kind:$('kind').value,name:$('partName').value,parameters:p},message:$('note').value})});const j=await api(`/api/revisions/${rev.id}/build`,{method:'POST'});status('Build queued: '+j.id);if(jobTimer)clearInterval(jobTimer);jobTimer=setInterval(async()=>{try{const s=await api('/api/jobs/'+j.id);status('Build '+s.state+(s.error?': '+s.error:''),s.state==='failed');if(s.state==='completed'||s.state==='failed'){clearInterval(jobTimer);await selectProject(project.id)}}catch(e){clearInterval(jobTimer);status(e.message,true)}},900)}catch(e){status(e.message,true)}};
$('sendAsset').onclick=async()=>{try{if(!project)throw Error('Select a project first');const f=$('upload').files[0];if(!f)throw Error('Choose a CAD/reference file');const data=new FormData();data.append('file',f);const a=await api(`/api/projects/${project.id}/assets`,{method:'POST',body:data});status('Attached '+a.filename);await selectProject(project.id)}catch(e){status(e.message,true)}};
loadProjects().catch(e=>status(e.message,true));
</script></body></html>'''


def make_app() -> FastAPI:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    repo = Repository(DATABASE)
    jobs = Jobs(repo)
    app = FastAPI(title=APP_NAME, version="1.0")

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return page()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        try:
            import cadquery  # noqa: F401
            cadquery_ready = True
        except ImportError:
            cadquery_ready = False
        return {"ok": True, "service": APP_NAME, "root": str(ENGINE_ROOT), "cadquery_ready": cadquery_ready,
                "upload_limit_bytes": MAX_UPLOAD_BYTES, "import_formats": sorted(ALLOWED_IMPORTS)}

    @app.get("/api/projects")
    def projects() -> list[dict[str, Any]]:
        return repo.list_projects()

    @app.post("/api/projects", status_code=201)
    def create_project(data: ProjectCreate) -> dict[str, Any]:
        if not data.name.strip():
            raise HTTPException(422, "project name cannot be blank")
        return repo.create_project(data)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        return repo.get_project(project_id)

    @app.post("/api/projects/{project_id}/revisions", status_code=201)
    def create_revision(project_id: str, data: RevisionCreate) -> dict[str, Any]:
        return repo.add_revision(project_id, data)

    @app.get("/api/revisions/{revision_id}")
    def get_revision(revision_id: str) -> dict[str, Any]:
        return repo.get_revision(revision_id)

    @app.post("/api/revisions/{revision_id}/build", status_code=202)
    def build_revision(revision_id: str) -> dict[str, str]:
        return {"id": jobs.submit_build(revision_id), "state": "queued"}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        return repo.get_job(job_id)

    @app.get("/api/revisions/{revision_id}/files/{filename}")
    def get_file(revision_id: str, filename: str) -> FileResponse:
        path = revision_file(repo, revision_id, filename)
        return FileResponse(path, filename=path.name)

    @app.post("/api/projects/{project_id}/assets", status_code=201)
    async def upload_asset(project_id: str, request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
        # `request` keeps FastAPI's standard request-size protection available to
        # a future proxy; `read_limited` enforces a hard application limit too.
        del request
        repo.get_project(project_id)
        original = checked_part_name(file.filename or "upload")
        suffix = Path(original).suffix.lower()
        if suffix not in ALLOWED_IMPORTS:
            raise HTTPException(415, "supported formats: " + ", ".join(sorted(ALLOWED_IMPORTS)))
        raw, digest = read_limited(file)
        if not raw:
            raise HTTPException(422, "empty upload")
        asset_dir = within(WORK_ROOT, WORK_ROOT / "assets" / project_id)
        asset_dir.mkdir(parents=True, exist_ok=True)
        stored = within(WORK_ROOT, asset_dir / f"{uuid.uuid4().hex}{suffix}")
        stored.write_bytes(raw)
        return repo.add_asset(project_id, original, stored, suffix, digest, len(raw))

    @app.get("/api/assets/{asset_id}")
    def get_asset(asset_id: str) -> FileResponse:
        path, original = repo.asset_path(asset_id)
        return FileResponse(path, filename=original, media_type="application/octet-stream")

    return app


def selftest() -> bool:
    """No server and no network.  Tests schemas and the project transaction path."""
    import tempfile
    global ENGINE_ROOT, WORK_ROOT, ARTIFACT_ROOT, DATABASE
    original = ENGINE_ROOT, WORK_ROOT, ARTIFACT_ROOT, DATABASE
    try:
        with tempfile.TemporaryDirectory(prefix="engineai_live_") as tmp:
            ENGINE_ROOT = Path(tmp)
            WORK_ROOT = ENGINE_ROOT / "work" / "live_workspace"
            ARTIFACT_ROOT = ENGINE_ROOT / "out" / "live_workspace"
            DATABASE = WORK_ROOT / "projects.sqlite3"
            repo = Repository(DATABASE)
            project = repo.create_project(ProjectCreate(name="Test project"))
            revision = repo.add_revision(project["id"], RevisionCreate(spec=DesignSpec(kind="bracket"), message="first"))
            assert revision["status"] == "draft"
            assert parameters(DesignSpec(kind="flange_tube"))["inner_diameter"] < 50
            try:
                parameters(DesignSpec(kind="flange_tube", parameters={"inner_diameter": 60, "outer_diameter": 50}))
                raise AssertionError("invalid dimensions accepted")
            except ValueError:
                pass
            assert "EngineAI Live Workspace" in page()
        print("live workspace selftest: PASSED")
        return True
    except Exception as exc:
        print(f"live workspace selftest: FAILED: {type(exc).__name__}: {exc}")
        return False
    finally:
        ENGINE_ROOT, WORK_ROOT, ARTIFACT_ROOT, DATABASE = original


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME, allow_abbrev=False)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8101)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return 0 if selftest() else 1
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("WARNING: non-local binding requires authentication, TLS, limits and a reviewed reverse proxy.")
    import uvicorn
    uvicorn.run(make_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
