#!/usr/bin/env python3
"""EngineAI local task runner and fail-closed code sandbox.

This service supplies the part of an autonomous assistant that must not be
hand-waved: durable task state, bounded execution, logs and artifact folders.

Two deliberately different paths exist:

* verified tasks are an allow-list of EngineAI self-tests.  They run locally
  with a short timeout and resource limits, and cannot receive shell syntax or
  arbitrary commands from an HTTP request.
* arbitrary Python runs only through Bubblewrap (preferred on this Linux/WSL
  host) or Docker, with networking disabled.  If neither isolated runtime is
  available, the request is refused.  There is no unsafe "run it on the host
  instead" fallback.

Run locally only::

    conda activate eng
    python live_sandbox.py --selftest
    python live_sandbox.py --host 127.0.0.1 --port 8102

Build the optional Docker sandbox image (only after Docker is deliberately installed)::

    docker build -t engineai-python-sandbox:3.12 sandbox

The service is an execution foundation, not a browser-login agent or a way to
run unreviewed software on the host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field


APP_NAME = "EngineAI Local Task Runner"
ENGINE_ROOT = Path(os.environ.get("ENGINEAI_ROOT", Path.home() / "engineai")).resolve()
WORK_ROOT = (ENGINE_ROOT / "work" / "live_sandbox").resolve()
OUT_ROOT = (ENGINE_ROOT / "out" / "live_sandbox").resolve()
DATABASE = WORK_ROOT / "tasks.sqlite3"
MAX_LOG_BYTES = 1_000_000
MAX_TRUSTED_SECONDS = int(os.environ.get("ENGINEAI_TASK_TIMEOUT", "120"))
MAX_SANDBOX_SECONDS = int(os.environ.get("ENGINEAI_SANDBOX_TIMEOUT", "30"))
SANDBOX_IMAGE = os.environ.get("ENGINEAI_SANDBOX_IMAGE", "engineai-python-sandbox:3.12")
SAFE_ID = __import__("re").compile(r"^[a-z][a-z0-9_]{2,79}$")


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["mathsolver_selftest", "provider_selftest", "keypool_selftest", "workspace_selftest"]
    title: str = Field(default="", max_length=120)


class CodeCreate(BaseModel):
    """Python source for the Docker-only path; it is never run on the host."""
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=32_000)
    timeout_seconds: int = Field(default=10, ge=1, le=MAX_SANDBOX_SECONDS)


@dataclass(frozen=True)
class TaskDefinition:
    title: str
    argv: tuple[str, ...]
    timeout_seconds: int = 60


def _catalog() -> dict[str, TaskDefinition]:
    python = str(Path(sys.executable).resolve())
    return {
        "mathsolver_selftest": TaskDefinition("Math expression safety self-test", (python, str(ENGINE_ROOT / "mathsolver.py"), "--selftest")),
        "provider_selftest": TaskDefinition("Provider protocol self-test", (python, str(ENGINE_ROOT / "providers.py"), "--selftest")),
        "keypool_selftest": TaskDefinition("Provider failover self-test", (python, str(ENGINE_ROOT / "keypool.py"), "--selftest")),
        "workspace_selftest": TaskDefinition("Versioned CAD workspace self-test", (python, str(ENGINE_ROOT / "live_workspace.py"), "--selftest")),
    }


def now() -> int:
    return int(time.time())


def identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def inside(root: Path, candidate: Path) -> Path:
    root, candidate = root.resolve(), candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise RuntimeError("sandbox path escaped its root")
    return candidate


class Repository:
    def __init__(self, database: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.database = database
        self.lock = threading.Lock()
        with self.connect() as con:
            con.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                state TEXT NOT NULL, command_json TEXT NOT NULL,
                artifact_dir TEXT NOT NULL, exit_code INTEGER, error TEXT,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
            """)
            # A process cannot safely resume an already-running child after a
            # restart.  Preserve evidence and make this condition explicit.
            con.execute("UPDATE tasks SET state='interrupted', error='service restarted while task was running', updated_at=? WHERE state='running'", (now(),))

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.database, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def create(self, kind: str, title: str, command: list[str], artifact_dir: Path) -> dict[str, Any]:
        task_id, stamp = identifier("task"), now()
        with self.lock, self.connect() as con:
            con.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)", (
                task_id, kind, title or kind.replace("_", " ").title(), "queued", json.dumps(command),
                str(artifact_dir), None, None, stamp, stamp,
            ))
        return self.get(task_id)

    def get(self, task_id: str) -> dict[str, Any]:
        with self.connect() as con:
            row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "task not found")
        data = dict(row)
        data["command"] = json.loads(data.pop("command_json"))
        data.pop("artifact_dir", None)
        return data

    def path(self, task_id: str) -> Path:
        with self.connect() as con:
            row = con.execute("SELECT artifact_dir FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "task not found")
        return inside(OUT_ROOT, Path(row["artifact_dir"]))

    def list(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 100").fetchall()
        return [self.get(str(row["id"])) for row in rows]

    def update(self, task_id: str, state: str, *, exit_code: int | None = None, error: str | None = None) -> None:
        with self.lock, self.connect() as con:
            con.execute("UPDATE tasks SET state=?, exit_code=?, error=?, updated_at=? WHERE id=?", (state, exit_code, error, now(), task_id))


def _limits(seconds: int) -> None:
    """Resource controls for trusted local child processes on Linux/WSL."""
    os.setsid()
    resource.setrlimit(resource.RLIMIT_CPU, (seconds + 2, seconds + 3))
    resource.setrlimit(resource.RLIMIT_AS, (1_000_000_000, 1_000_000_000))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    os.umask(0o077)


def run_bounded(argv: list[str], directory: Path, timeout_seconds: int) -> tuple[int, str, str | None]:
    """Run an argv list without a shell, bounded in wall time, memory and log size."""
    directory.mkdir(parents=True, exist_ok=True)
    safe_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(directory),
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen(
        argv, cwd=directory, env=safe_env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=False,
        start_new_session=False, preexec_fn=lambda: _limits(timeout_seconds),
    )
    output = bytearray()
    deadline = time.monotonic() + timeout_seconds
    reason: str | None = None
    assert process.stdout is not None
    while process.poll() is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            reason = f"timed out after {timeout_seconds} seconds"
            os.killpg(process.pid, signal.SIGKILL)
            break
        ready, _, _ = select.select([process.stdout], [], [], min(0.2, remaining))
        if ready:
            chunk = os.read(process.stdout.fileno(), 65_536)
            output.extend(chunk)
            if len(output) > MAX_LOG_BYTES:
                reason = f"log exceeded {MAX_LOG_BYTES} bytes"
                os.killpg(process.pid, signal.SIGKILL)
                break
    while True:
        chunk = process.stdout.read(65_536)
        if not chunk:
            break
        output.extend(chunk)
        if len(output) > MAX_LOG_BYTES:
            output = output[:MAX_LOG_BYTES]
            reason = reason or f"log exceeded {MAX_LOG_BYTES} bytes"
            break
    process.wait(timeout=5)
    return process.returncode, output.decode("utf-8", errors="replace"), reason


def docker_status() -> dict[str, Any]:
    docker = shutil.which("docker")
    if not docker:
        return {"available": False, "reason": "Docker is not installed; arbitrary code is refused."}
    check = subprocess.run([docker, "image", "inspect", SANDBOX_IMAGE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    if check.returncode:
        return {"available": False, "reason": f"sandbox image '{SANDBOX_IMAGE}' is not built; arbitrary code is refused."}
    return {"available": True, "image": SANDBOX_IMAGE}


def bubblewrap_status() -> dict[str, Any]:
    executable = shutil.which("bwrap")
    if not executable:
        return {"available": False, "reason": "Bubblewrap is not installed."}
    return {"available": True, "executable": executable,
            "network": "disabled", "host_filesystem": "not mounted"}


def isolation_status() -> dict[str, Any]:
    """Prefer Bubblewrap; it is small, present on this host and tested locally."""
    bwrap = bubblewrap_status()
    if bwrap["available"]:
        return {"available": True, "backend": "bubblewrap", **bwrap}
    docker = docker_status()
    if docker["available"]:
        return {"available": True, "backend": "docker", **docker}
    return {"available": False, "reason": "No Bubblewrap or Docker sandbox is available; arbitrary code is refused.",
            "bubblewrap": bwrap["reason"], "docker": docker["reason"]}


class Runner:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="engineai-task")

    def submit(self, request: TaskCreate) -> dict[str, Any]:
        definition = _catalog()[request.kind]
        artifact_dir = inside(OUT_ROOT, OUT_ROOT / "tasks" / identifier("run"))
        task = self.repository.create(request.kind, request.title or definition.title, list(definition.argv), artifact_dir)
        self.pool.submit(self._trusted, task["id"], definition, artifact_dir)
        return task

    def retry(self, task_id: str) -> dict[str, Any]:
        """Create a new durable run from an interrupted/failed known task.

        A process cannot be resumed after a service restart.  Retrying from the
        recorded allow-list is the safe, reproducible equivalent: the original
        log remains immutable and the new task receives a new artifact folder.
        """
        old = self.repository.get(task_id)
        if old["state"] not in {"failed", "interrupted"}:
            raise HTTPException(409, "only failed or interrupted tasks can be retried")
        if old["kind"] not in _catalog():
            raise HTTPException(409, "task kind is not in the verified allow-list")
        return self.submit(TaskCreate(kind=old["kind"], title=f"Retry of {old['id']}: {old['title']}"))

    def _trusted(self, task_id: str, definition: TaskDefinition, artifact_dir: Path) -> None:
        self.repository.update(task_id, "running")
        try:
            code, text, problem = run_bounded(list(definition.argv), artifact_dir, min(definition.timeout_seconds, MAX_TRUSTED_SECONDS))
            (artifact_dir / "task.log").write_text(text, encoding="utf-8", errors="replace")
            (artifact_dir / "manifest.json").write_text(json.dumps({
                "schema": 1, "task_id": task_id, "kind": definition.title,
                "argv": list(definition.argv), "exit_code": code, "problem": problem,
                "sha256": hashlib.sha256(text.encode()).hexdigest(), "created_at": now(),
                "scope": "verified local allow-list task; no shell or arbitrary request command",
            }, indent=2), encoding="utf-8")
            if problem or code != 0:
                self.repository.update(task_id, "failed", exit_code=code, error=problem or f"exit code {code}")
            else:
                self.repository.update(task_id, "completed", exit_code=code)
        except Exception as exc:
            self.repository.update(task_id, "failed", error=f"{type(exc).__name__}: {exc}")

    def run_python_isolated(self, request: CodeCreate) -> dict[str, Any]:
        status = isolation_status()
        if not status["available"]:
            raise HTTPException(503, status["reason"])
        artifact_dir = inside(OUT_ROOT, OUT_ROOT / "docker_runs" / identifier("run"))
        input_dir, output_dir = artifact_dir / "input", artifact_dir / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        (input_dir / "main.py").write_text(request.code, encoding="utf-8")
        (input_dir / "main.py").chmod(0o444)
        input_dir.chmod(0o555)
        digest = hashlib.sha256(request.code.encode()).hexdigest()
        if status["backend"] == "bubblewrap":
            bwrap = shutil.which("bwrap")
            assert bwrap is not None
            # No host root mount is exposed.  The guest sees only the Python
            # runtime, a read-only source file, fresh /proc and its own output
            # directory.  It inherits CPU/address-space/file-size limits from
            # run_bounded and cannot open a network namespace.
            argv = [
                bwrap, "--unshare-all", "--new-session", "--die-with-parent", "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64", "--dev", "/dev", "--proc", "/proc",
                "--tmpfs", "/tmp", "--dir", "/work", "--chdir", "/work", "--ro-bind", str(input_dir), "/input",
                "--bind", str(output_dir), "/output", "--clearenv", "--setenv", "PATH", "/usr/bin:/bin",
                "--setenv", "HOME", "/work", "--setenv", "PYTHONNOUSERSITE", "1",
                "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "/usr/bin/python3", "/input/main.py",
            ]
        else:
            docker = shutil.which("docker")
            assert docker is not None
            output_dir.chmod(0o777)  # Docker process runs as an unprivileged uid.
            argv = [
                docker, "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "512m", "--cpus", "1",
                "--user", "65534:65534", "--mount", f"type=bind,src={input_dir},dst=/input,readonly",
                "--mount", f"type=bind,src={output_dir},dst=/output", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=32m",
                SANDBOX_IMAGE, "python", "/input/main.py",
            ]
        code, text, problem = run_bounded(argv, artifact_dir, request.timeout_seconds + 10)
        (artifact_dir / "task.log").write_text(text, encoding="utf-8", errors="replace")
        manifest = {
            "schema": 1, "mode": status["backend"], "network": "disabled",
            "source_sha256": digest, "exit_code": code, "problem": problem,
            "timeout_seconds": request.timeout_seconds, "output_directory": "output",
        }
        (artifact_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return {"state": "completed" if not problem and code == 0 else "failed", "exit_code": code,
                "error": problem, "log": text, "artifact_dir": str(artifact_dir), "manifest": manifest}


def page() -> str:
    buttons = "".join(f'<button data-kind="{name}">{spec.title}</button>' for name, spec in _catalog().items())
    return f'''<!doctype html><meta charset="utf-8"><title>{APP_NAME}</title><style>
body{{margin:30px auto;max-width:860px;background:#0c1320;color:#dcecff;font:14px system-ui}}button{{margin:4px;padding:9px 12px;background:#174873;color:#fff;border:1px solid #407aa9;border-radius:6px;cursor:pointer}}pre{{white-space:pre-wrap;background:#101d2c;padding:14px;border-radius:7px;max-height:420px;overflow:auto}}.warn{{color:#ffcc76}}small{{color:#91a9c0}}</style>
<h1>{APP_NAME}</h1><p>Verified local tasks have a fixed allow-list. Arbitrary Python runs only in a no-network Docker container; it is refused when that container is unavailable.</p>
<p id=status>Checking sandbox…</p><h2>Verified tasks</h2><div>{buttons}</div><h2>Recent tasks</h2><pre id=tasks>loading…</pre><script>
const status=document.querySelector('#status'),tasks=document.querySelector('#tasks');
async function api(path,opt={{}}){{const r=await fetch(path,opt),d=await r.json();if(!r.ok)throw Error(d.detail||r.statusText);return d}}
async function refresh(){{const h=await api('/api/health'),rows=await api('/api/tasks');status.className=h.isolation.available?'':'warn';status.textContent=h.isolation.available?('Isolated '+h.isolation.backend+' sandbox ready.'):('Sandbox unavailable: '+h.isolation.reason);tasks.textContent=JSON.stringify(rows,null,2)}}
document.querySelectorAll('button[data-kind]').forEach(b=>b.onclick=async()=>{{try{{await api('/api/tasks',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{kind:b.dataset.kind}})}});await refresh()}}catch(e){{status.textContent=e.message}}}});refresh();setInterval(refresh,1500);
</script>'''


def make_app() -> FastAPI:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    repository = Repository(DATABASE)
    runner = Runner(repository)
    app = FastAPI(title=APP_NAME, version="1.0")

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return page()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": APP_NAME, "root": str(ENGINE_ROOT), "isolation": isolation_status(),
                "trusted_tasks": sorted(_catalog()), "arbitrary_host_execution": False}

    @app.get("/api/tasks")
    def list_tasks() -> list[dict[str, Any]]:
        return repository.list()

    @app.post("/api/tasks", status_code=202)
    def create_task(request: TaskCreate) -> dict[str, Any]:
        return runner.submit(request)

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        return repository.get(task_id)

    @app.post("/api/tasks/{task_id}/retry", status_code=202)
    def retry_task(task_id: str) -> dict[str, Any]:
        return runner.retry(task_id)

    @app.get("/api/tasks/{task_id}/log", response_class=PlainTextResponse)
    def task_log(task_id: str) -> str:
        path = inside(OUT_ROOT, repository.path(task_id) / "task.log")
        if not path.is_file():
            raise HTTPException(409, "task has not produced a log yet")
        return path.read_text(encoding="utf-8", errors="replace")

    @app.post("/api/sandbox/python")
    def sandbox_python(request: CodeCreate) -> dict[str, Any]:
        return runner.run_python_isolated(request)

    return app


def selftest() -> bool:
    global ENGINE_ROOT, WORK_ROOT, OUT_ROOT, DATABASE
    original = ENGINE_ROOT, WORK_ROOT, OUT_ROOT, DATABASE
    try:
        with tempfile.TemporaryDirectory(prefix="engineai_sandbox_") as temp:
            ENGINE_ROOT = Path(__file__).resolve().parent
            WORK_ROOT, OUT_ROOT = Path(temp) / "work", Path(temp) / "out"
            DATABASE = WORK_ROOT / "tasks.sqlite3"
            repository, runner = Repository(DATABASE), Runner(Repository(DATABASE))
            task = runner.submit(TaskCreate(kind="mathsolver_selftest"))
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = repository.get(task["id"])["state"]
                if state in {"completed", "failed"}:
                    break
                time.sleep(0.05)
            result = repository.get(task["id"])
            assert result["state"] == "completed", result
            log = (repository.path(task["id"]) / "task.log").read_text(encoding="utf-8")
            assert "ALL CHECKS PASS" in log, log
            repository.update(task["id"], "interrupted", error="test interruption")
            retry = runner.retry(task["id"])
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if repository.get(retry["id"])["state"] in {"completed", "failed"}:
                    break
                time.sleep(0.05)
            assert repository.get(retry["id"])["state"] == "completed"
            if bubblewrap_status()["available"]:
                isolated = runner.run_python_isolated(CodeCreate(code=(
                    "from pathlib import Path\nPath('/output/evidence.txt').write_text('isolated')\nprint('isolated-ok')\n")))
                assert isolated["state"] == "completed", isolated
                assert (Path(isolated["artifact_dir"]) / "output" / "evidence.txt").read_text() == "isolated"
            assert isolation_status()["available"] in {True, False}
            assert not any("sh -c" in " ".join(item.argv) for item in _catalog().values())
            runner.pool.shutdown(wait=True)
        print("live sandbox selftest: PASSED")
        return True
    except Exception as exc:
        print(f"live sandbox selftest: FAILED: {type(exc).__name__}: {exc}")
        return False
    finally:
        ENGINE_ROOT, WORK_ROOT, OUT_ROOT, DATABASE = original


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME, allow_abbrev=False)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8102)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return 0 if selftest() else 1
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("WARNING: keep this executor localhost-only until authentication, TLS and an independent security review exist.")
    import uvicorn
    uvicorn.run(make_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
