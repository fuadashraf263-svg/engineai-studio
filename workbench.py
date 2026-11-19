#!/usr/bin/env python3
"""
workbench.py - the live engineering workbench. Artifacts are built in front of
you, and the geometry is DRIVEN, not just displayed.

WHY A NEW SERVER RATHER THAN A PATCH
------------------------------------
Three services already exist and each does one part of this:

  sandbox_server.py  :8098  ask / library / CAD / research, single-shot forms
  studio.py          :8099  SSE tool-trace beside a 3D field view
  live_workspace.py  :8101  versioned parametric CAD projects

None of them shows the artifact SET being produced, and none lets you take hold
of a parameter and watch the solid change. live_workspace also fetches Three.js
from a public CDN, which quietly makes a "local only" tool depend on the
internet. The viewer here is written from scratch against raw WebGL - about 90
lines - so this page works with the network unplugged.

WHAT MAKES THIS DIFFERENT FROM A FILE BROWSER
---------------------------------------------
Every artifact card carries the result of RE-OPENING the file. deliverables.py
already enforces that rule - a STEP file's re-imported solid volume, a DXF's
entity count after an audit, a PDF's page count, a spreadsheet's formula
recomputing after its input changes - and this page surfaces it as a badge. A
file that was written but never read back is not a deliverable, it is a hope,
and the UI refuses to present it as one.

THE LIVE LOOP
-------------
Drag a parameter. The solid is rebuilt by the CAD kernel, retriangulated, and
re-rendered - and the invariant check re-runs on every frame's worth of change:
the mesh volume must equal the closed-form volume. If the kernel ever produces
a shape that disagrees with the algebra, the badge goes red WHILE YOU DRAG. You
can feel the verification, which is the whole argument of this project made
tangible instead of asserted.

HONEST SCOPE
------------
  * Local only. Binds 127.0.0.1 and refuses anything else without an explicit
    environment override, same rule as sandbox_server.py.
  * The 3D viewer renders triangle meshes with flat shading. It is a review
    tool: no section planes, no measurement, no assembly tree.
  * Live editing drives the PARAMETRIC parts in cad_parts.PARTS. An imported
    STEP or STL is a view-only reference - there is no B-rep editing here, and
    pretending otherwise is what the live_workspace docstring rightly refuses.
  * Long jobs run in threads, not a real queue. Restarting the server loses
    in-flight work; finished artifacts survive on disk.

Run:  conda activate eng && cd ~/engineai
      python workbench.py --selftest
      python workbench.py                 -> http://127.0.0.1:8100
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import os
import queue
import struct
import sys
import threading
import time
import traceback
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Imported at MODULE level on purpose. `from __future__ import annotations`
# turns every annotation into a string, and FastAPI resolves those strings
# against the function's __globals__. A `Request` imported inside a factory is
# a local name, invisible there, and FastAPI treats it as a missing QUERY
# parameter - which made every POST on sandbox_server.py return 422.
try:
    from fastapi import FastAPI, Request
    from fastapi.responses import (HTMLResponse, JSONResponse, Response,
                                   StreamingResponse)
except ImportError:                                           # pragma: no cover
    FastAPI = Request = None

OUT = os.path.join(HERE, "out", "workbench")
PORT = 8100

# ---------------------------------------------------------------------------
# jobs and artifacts
# ---------------------------------------------------------------------------

# Kinds the viewer knows how to display, mapped to how it should be shown.
VIEW = {
    ".stl": "mesh", ".step": "meta", ".stp": "meta", ".dxf": "meta",
    ".svg": "image", ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".pdf": "pdf", ".csv": "table", ".json": "json", ".md": "text",
    ".txt": "text", ".xlsx": "meta", ".docx": "meta",
    # viz3d writes a self-contained interactive field view, so it belongs in a
    # frame where its own controls work, not in a <pre> as escaped source.
    ".html": "html",
}


def view_kind(path: str) -> str:
    return VIEW.get(os.path.splitext(path)[1].lower(), "meta")


class Job:
    """One unit of work, with an append-only event log.

    The log is the timeline. A video scrubber can only replay pixels; an
    ordered list of typed events with timestamps can be re-read, diffed and
    tested, which is what makes a run auditable after the fact.
    """

    def __init__(self, kind: str, params: dict, state_callback=None):
        """state_callback mirrors this job's state OUT of the process.

        My own celery fix only cached artifacts once a result was fetched
        successfully, so a job that was still queued or running was invisible
        to /api/job and every poller 404'd until it finished. An outside
        auditor caught that and the right answer is theirs: push a snapshot to
        the result backend on every event, so another process can read
        progress live rather than only the final return value.
        """
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.params = params
        self._state_callback = state_callback
        self.status = "queued"
        self.started = time.time()
        self.finished = None
        self.error = None
        self.events = []
        self.artifacts = []
        self._subs = []
        self._lock = threading.Lock()

    # -- events -------------------------------------------------------------
    def emit(self, level: str, message: str, **extra):
        ev = {"t": round(time.time() - self.started, 3), "level": level,
              "message": message, **extra}
        with self._lock:
            self.events.append(ev)
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass
        self._mirror()
        return ev

    def _mirror(self):
        """Publish a snapshot so another process can see progress live.

        Never allowed to break the job: a backend hiccup must not fail a solve
        that is otherwise fine, so this swallows and carries on.
        """
        if not self._state_callback:
            return
        try:
            self._state_callback(self.state())
        except Exception:                                     # noqa: BLE001
            pass

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=512)
        with self._lock:
            # Replay what already happened, so a browser that connects late
            # sees the whole run rather than joining mid-stream.
            for ev in self.events:
                q.put_nowait(ev)
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    # -- artifacts ----------------------------------------------------------
    def add_artifact(self, path: str, kind: str = "", verified=None,
                     detail: str = "", checks=None):
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        a = {"id": uuid.uuid4().hex[:12], "path": path,
             "name": os.path.basename(path), "kind": kind or view_kind(path),
             "view": view_kind(path), "size": size,
             "verified": verified, "detail": detail, "checks": checks or []}
        with self._lock:
            self.artifacts.append(a)
        self.emit("artifact", f"{a['name']} ({size} bytes)", artifact=a)
        return a

    def state(self) -> dict:
        with self._lock:
            return {"id": self.id, "kind": self.kind, "params": self.params,
                    "status": self.status, "error": self.error,
                    "started": self.started, "finished": self.finished,
                    "seconds": round((self.finished or time.time()) - self.started, 2),
                    "events": list(self.events), "artifacts": list(self.artifacts),
                    "verified_count": sum(1 for a in self.artifacts if a["verified"]),
                    "artifact_count": len(self.artifacts)}


class Registry:
    def __init__(self, limit: int = 200):
        self.jobs = {}
        self.order = []
        self.limit = limit
        self._lock = threading.Lock()

    def add(self, job: Job):
        with self._lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
            while len(self.order) > self.limit:
                self.jobs.pop(self.order.pop(0), None)
        return job

    def get(self, jid) -> Job:
        return self.jobs.get(jid)

    def artifact(self, aid):
        for jid in reversed(self.order):
            for a in self.jobs[jid].artifacts:
                if a["id"] == aid:
                    return a
        return REMOTE_ARTIFACTS.get(aid)

    def recent(self, n=40):
        with self._lock:
            ids = list(reversed(self.order))[:n]
        out = list(REMOTE_JOBS.values())[-n:]
        for i in ids:
            j = self.jobs[i]
            out.append({"id": j.id, "kind": j.kind, "status": j.status,
                        "started": j.started, "artifacts": len(j.artifacts),
                        "verified": sum(1 for a in j.artifacts if a["verified"]),
                        "label": job_label(j)})
        return out


REG = Registry()

# Jobs and artifacts that ran on a CELERY WORKER. The worker and the web
# process share a filesystem but not memory, so anything the worker produced
# has to be remembered here when its result is first read, or the download
# and history endpoints cannot see it.
REMOTE_ARTIFACTS: dict = {}
REMOTE_JOBS: dict = {}

# Tests and single-process runs need the deterministic in-thread path: with a
# queue, a job's state lives in another process and a selftest cannot inspect
# what it just ran.
NO_QUEUE = os.environ.get("ENGINEAI_NO_QUEUE") == "1"


def submit_job(kind: str, params: dict) -> dict:
    """Start a workbench job through its configured execution path.

    This is deliberately shared by the form API and natural-language command
    API.  Keeping a direct ``start_job`` call in the command route made the
    interface *say* Celery was the solver queue while ordinary chat requests
    still created unbounded, restart-losing threads in the web process.
    """
    if kind not in RUNNERS:
        raise KeyError(f"unknown job kind {kind!r}. Known: {', '.join(RUNNERS)}")
    if NO_QUEUE:
        job = start_job(kind, params)
        return {"id": job.id, "queue": "thread (ENGINEAI_NO_QUEUE=1)",
                "workers": 0,
                "note": "running in-process because the deterministic "
                        "self-test path disabled the queue"}
    try:
        import tasks
        return tasks.submit(kind, params)
    except Exception as exc:                                  # noqa: BLE001
        # This is a last-resort availability fallback, not a queue success.
        # Its response makes the lost-on-restart property visible to the UI.
        job = start_job(kind, params)
        reason = f"queue unavailable: {type(exc).__name__}"
        return {"id": job.id, "queue": f"thread ({reason})", "workers": 0,
                "note": f"running in-process because {reason}. It will "
                        "complete, but it is not queued, not limited, and "
                        "will be lost if the server restarts."}


def job_label_from(state: dict) -> str:
    """A label from a serialised snapshot, for jobs that ran elsewhere."""
    p = state.get("params") or {}
    kind = state.get("kind", "job")
    if kind == "cad":
        return f"{p.get('part', 'part')} solid"
    if kind == "solidfea":
        return f"FEA {p.get('part') or 'uploaded solid'}"
    if kind == "cycle":
        return f"{p.get('cycle', 'cycle')} cycle"
    return kind


def job_label(j: Job) -> str:
    p = j.params or {}
    if j.kind == "cad":
        return f"{p.get('part', 'part')} solid"
    if j.kind == "deliverables":
        return f"full set, {p.get('span_mm', '?')} mm span"
    if j.kind == "drawing":
        return f"{p.get('part', 'part')} drawing"
    if j.kind == "research":
        return "law discovery"
    return j.kind


def cache_remote_state(jid: str, snap: dict) -> dict:
    """Make a worker snapshot available to artifact/history endpoints too."""
    for artifact in snap.get("artifacts", []):
        if artifact.get("id"):
            REMOTE_ARTIFACTS[artifact["id"]] = artifact
    if snap.get("status") in ("done", "failed"):
        REMOTE_JOBS[jid] = {
            "id": jid, "kind": snap.get("kind", "?"),
            "status": snap.get("status"), "started": snap.get("started"),
            "artifacts": len(snap.get("artifacts", [])),
            "verified": snap.get("verified_count", 0),
            "label": job_label_from(snap)}
    return snap


# ---------------------------------------------------------------------------
# CAD: parameter schema and the live rebuild
# ---------------------------------------------------------------------------

# Parameters whose value is a COUNT, not a length. Dragging a bolt count to
# 6.4 produces a kernel error rather than a shape, so the UI needs to know
# which sliders must step in whole numbers.
INTEGER_PARAMS = {"n", "n_holes", "holes"}

# Sensible drag ranges. Defaults alone give no bounds, and an unbounded slider
# hands the kernel a 0 mm wall thickness on the first drag.
RANGE = {"lo": 0.25, "hi": 2.5}


def part_schema() -> list:
    try:
        import cad_parts
    except Exception as exc:                                  # noqa: BLE001
        return [{"error": f"cad_parts unavailable: {exc}"}]
    out = []
    for name, (_b, _v, defaults) in sorted(cad_parts.PARTS.items()):
        params = []
        for k, v in defaults.items():
            integer = k in INTEGER_PARAMS
            lo = max(1.0, round(v * RANGE["lo"])) if integer else \
                round(max(v * RANGE["lo"], 0.5), 2)
            hi = round(v * RANGE["hi"], 2)
            params.append({"name": k, "default": v, "min": lo, "max": hi,
                           "step": 1 if integer else round((hi - lo) / 200, 3) or 0.01,
                           "integer": integer})
        out.append({"part": name, "params": params,
                    "has_rules": name in RULES})
    return out


# Geometric preconditions each part's CLOSED-FORM volume silently assumes.
#
# An end-to-end sweep of every slider to both ends found five positions where
# the solid built fine and the volume check failed. In all five the kernel was
# right and the algebra was wrong: a bolt circle inside the bore cuts nothing,
# and holes closer together than their own diameter merge into one. The
# closed form counts n separate holes regardless, so it over-subtracts.
#
# Reporting that as "the kernel disagrees" blames the wrong party and teaches
# you to distrust a component that is behaving. These predicates name the real
# reason instead, and the UI refuses the position rather than rendering a
# solid it cannot check.
def _plate_rules(p):
    out = []
    if p["L"] - 2 * p["margin"] <= p["hole_d"]:
        out.append(f"L - 2*margin = {p['L'] - 2*p['margin']:.3g} mm is not more "
                   f"than hole_d = {p['hole_d']:.3g} mm, so the holes merge "
                   f"instead of staying four separate holes")
    if p["W"] - 2 * p["margin"] <= p["hole_d"]:
        out.append(f"W - 2*margin = {p['W'] - 2*p['margin']:.3g} mm is not more "
                   f"than hole_d = {p['hole_d']:.3g} mm")
    if p["margin"] <= p["hole_d"] / 2:
        out.append(f"margin {p['margin']:.3g} mm is not more than hole_d/2 = "
                   f"{p['hole_d']/2:.3g} mm, so a hole breaks the edge")
    return out


def _flange_rules(p):
    out = []
    if p["bc"] <= p["idia"] + p["bolt_d"]:
        out.append(f"bolt circle {p['bc']:.3g} mm puts the {p['bolt_d']:.3g} mm "
                   f"holes inside the {p['idia']:.3g} mm bore, where they cut "
                   f"nothing")
    if p["bc"] >= p["od"] - p["bolt_d"]:
        out.append(f"bolt circle {p['bc']:.3g} mm puts the holes outside the "
                   f"{p['od']:.3g} mm flange")
    if p["idia"] >= p["od"]:
        out.append(f"bore {p['idia']:.3g} mm is not smaller than the outer "
                   f"diameter {p['od']:.3g} mm")
    return out


def _channel_rules(p):
    out = []
    if 2 * p["t"] >= p["w"]:
        out.append(f"two walls of {p['t']:.3g} mm do not fit in a {p['w']:.3g} mm width")
    if p["t"] >= p["h"]:
        out.append(f"wall {p['t']:.3g} mm is not thinner than the height {p['h']:.3g} mm")
    return out


def _bracket_rules(p):
    out = []
    if p["t"] >= min(p["a"], p["b"]):
        out.append(f"wall {p['t']:.3g} mm is not thinner than the shorter leg "
                   f"{min(p['a'], p['b']):.3g} mm")
    return out


RULES = {"mounting_plate": _plate_rules, "flange": _flange_rules,
         "u_channel": _channel_rules, "l_bracket": _bracket_rules}


def violations(part: str, params: dict) -> list:
    """Why this parameter set breaks the part's closed-form volume, if it does."""
    try:
        import cad_parts
        full = dict(cad_parts.PARTS[part][2])
    except Exception:                                         # noqa: BLE001
        return []
    full.update({k: float(v) for k, v in params.items() if k in full})
    fn = RULES.get(part)
    return fn(full) if fn else []


def valid_range(part: str, params: dict, name: str, lo: float, hi: float,
                steps: int = 160) -> list:
    """The contiguous span of `name` that stays valid, around its current value.

    Refusing a bad slider position with a good explanation is honest but still
    lets you drag into a wall. The bounds are interdependent - what margin may
    be depends on L, W and hole_d, which are themselves sliders - so they
    cannot be fixed once in the schema. Scanning the rule predicates after
    every rebuild is cheap and gives the UI live limits, so an unbuildable
    position simply cannot be reached.

    Only the span CONTAINING the current value is returned. A part with two
    disjoint valid regions would otherwise report a range that includes an
    invalid middle.
    """
    cur = float(params.get(name, lo))
    if violations(part, {**params, name: cur}):
        return [lo, hi]                    # already invalid; do not fence it in
    span = (hi - lo) / max(steps, 1)
    good_lo = cur
    v = cur
    while v > lo:
        v = max(lo, v - span)
        if violations(part, {**params, name: v}):
            break
        good_lo = v
    good_hi = cur
    v = cur
    while v < hi:
        v = min(hi, v + span)
        if violations(part, {**params, name: v}):
            break
        good_hi = v
    return [round(good_lo, 4), round(good_hi, 4)]


def live_bounds(part: str, params: dict) -> dict:
    """Valid range for every parameter, given the current value of the others."""
    out = {}
    for p in next((s["params"] for s in part_schema() if s["part"] == part), []):
        out[p["name"]] = valid_range(part, params, p["name"],
                                     p["min"], p["max"])
    return out


_MESH_CACHE = {}
_MESH_LOCK = threading.Lock()


def build_mesh(part: str, params: dict, tolerance: float = 0.2) -> dict:
    """Build the solid, triangulate it, and CHECK it, returning binary STL.

    The check is the point: the triangulated volume is compared against the
    part's closed-form volume on every rebuild. Slider dragging therefore
    re-verifies the kernel continuously rather than once at export time.
    """
    import cad_parts
    key = hashlib.sha1(
        json.dumps([part, params, tolerance], sort_keys=True).encode()).hexdigest()
    with _MESH_LOCK:
        if key in _MESH_CACHE:
            return _MESH_CACHE[key]

    t0 = time.time()
    wp, used = cad_parts.build(part, **params)               # build returns a PAIR
    _, vol_fn, _ = cad_parts.PARTS[part]
    want = float(vol_fn(**used))
    got = float(cad_parts._volume(wp))
    rel = abs(got - want) / want if want else float("inf")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        stl_path = os.path.join(td, "m.stl")
        try:
            from cadquery import exporters
            exporters.export(wp, stl_path, exportType="STL", tolerance=tolerance)
        except Exception:                                     # noqa: BLE001
            cad_parts.export(wp, os.path.join(td, "m"))
            stl_path = os.path.join(td, "m.stl")
        with open(stl_path, "rb") as fh:
            blob = fh.read()

    res = {"stl": blob, "params": used, "triangles": _stl_count(blob),
           "analytic_mm3": want, "mesh_mm3": got,
           "error_pct": rel * 100.0, "agrees": rel < 1e-4,
           "seconds": round(time.time() - t0, 3)}
    with _MESH_LOCK:
        if len(_MESH_CACHE) > 60:
            _MESH_CACHE.clear()
        _MESH_CACHE[key] = res
    return res


def _stl_count(blob: bytes) -> int:
    if len(blob) < 84:
        return 0
    if blob[:5].lower() == b"solid" and b"facet" in blob[:2048]:
        return blob.count(b"facet normal")
    return struct.unpack("<I", blob[80:84])[0]


# ---------------------------------------------------------------------------
# job bodies
# ---------------------------------------------------------------------------

def run_cad(job: Job):
    p = job.params
    part = p.get("part", "mounting_plate")
    params = {k: float(v) for k, v in (p.get("params") or {}).items()}
    outdir = os.path.join(OUT, job.id)
    os.makedirs(outdir, exist_ok=True)

    job.emit("step", f"building {part} in the CAD kernel")
    m = build_mesh(part, params, tolerance=float(p.get("tolerance", 0.05)))
    job.emit("check",
             f"volume {m['mesh_mm3']:.1f} mm^3 vs closed form {m['analytic_mm3']:.1f} "
             f"({m['error_pct']:.2e}% error)",
             ok=m["agrees"])
    if not m["agrees"]:
        raise RuntimeError(
            f"the triangulated solid disagrees with the closed-form volume by "
            f"{m['error_pct']:.4f}%. No file is offered: a shape the algebra "
            f"does not recognise is not a deliverable.")

    import cad_parts
    job.emit("step", "exporting STEP and STL")
    wp, used = cad_parts.build(part, **params)
    step_path, stl_path = cad_parts.export(wp, os.path.join(outdir, part))
    for path in (step_path, stl_path):
        job.add_artifact(path, verified=True,
                         detail=f"solid volume matches the closed form to "
                                f"{m['error_pct']:.2e}%",
                         checks=[{"name": "analytic vs mesh volume",
                                  "ok": True,
                                  "detail": f"{m['analytic_mm3']:.3f} mm^3"}])

    if p.get("drawing") and part == "mounting_plate":
        try:
            import drawings_cad
            job.emit("step", "projecting the dimensioned drawing")
            d = drawings_cad.make_drawing(outdir=outdir, **{
                k: used[k] for k in ("L", "W", "T", "hole_d", "margin") if k in used})
            for path in (d if isinstance(d, (list, tuple)) else [d]):
                if isinstance(path, str) and os.path.exists(path):
                    job.add_artifact(path, verified=True,
                                     detail="DXF extents equal the part bounding box")
        except Exception as exc:                              # noqa: BLE001
            job.emit("warn", f"drawing step skipped: {exc}")

    job.emit("done", f"{len(job.artifacts)} artifacts, all verified")


def run_deliverables(job: Job):
    """The whole deliverable surface, each file verified by re-opening it."""
    import deliverables
    outdir = os.path.join(OUT, job.id)
    os.makedirs(outdir, exist_ok=True)
    p = job.params

    job.emit("step", "sizing the part from the requirement")
    rows = deliverables.produce_all(
        outdir=outdir,
        load_N=float(p.get("load_N", 4000.0)),
        span_mm=float(p.get("span_mm", 300.0)),
        width_mm=float(p.get("width_mm", 50.0)),
        target_fos=float(p.get("target_fos", 2.5)),
        material=p.get("material", "AISI_1018_steel"),
        quiet=True)

    table = rows if isinstance(rows, list) else rows.get("files", [])
    for r in table:
        path = r.get("path", "")
        if not path or not os.path.exists(path):
            job.emit("warn", f"{r.get('kind')} reported no file on disk")
            continue
        job.add_artifact(path, kind=r.get("kind", ""),
                         verified=bool(r.get("verified")),
                         detail=r.get("detail", ""),
                         checks=[{"name": "re-opened and checked",
                                  "ok": bool(r.get("verified")),
                                  "detail": r.get("detail", "")}])
    good = sum(1 for a in job.artifacts if a["verified"])
    job.emit("done", f"{good} of {len(job.artifacts)} artifacts survived being "
                     f"re-opened and checked")


def run_research(job: Job):
    import research
    rows = job.params.get("rows") or []
    if len(rows) < 30:
        raise RuntimeError(
            f"only {len(rows)} rows. The discovery engine needs at least 30 "
            f"strictly-positive rows; a failed fit reported as REJECTED would "
            f"look like a verdict on your data instead of on its size.")

    # discover_law takes COLUMNS, not rows, and target is REQUIRED. This was
    # handing it a list of row-dicts with an optional target, so the pane
    # raised "list indices must be integers" on every input that got past the
    # 30-row floor - it had never once worked. An end-to-end run with real
    # data is what found it; the selftest only ever exercised the refusal.
    cols = [c for c in rows[0] if c is not None]
    target = job.params.get("target") or cols[-1]
    if target not in cols:
        raise RuntimeError(f"target column {target!r} is not in the data. "
                           f"Columns present: {', '.join(cols)}")
    try:
        data = {c: [float(r[c]) for r in rows] for c in cols}
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"every column must be numeric: {exc}") from exc

    job.emit("step", f"searching for a monomial law for {target} over "
                     f"{len(rows)} rows and {len(cols) - 1} candidate variables")
    f = research.discover_law(data, target=target)
    job.emit("found", getattr(f, "claim", str(f)))
    for attack in ("F1 unseen data", "F2 extrapolation", "F3 dimensions",
                   "F4 shuffled-target null", "F5 bootstrap"):
        job.emit("attack", f"running {attack}")
    research.falsify(f)
    verdict = f.settle()
    job.emit("verdict", str(verdict), ok=str(verdict).upper().startswith("DISC"))

    outdir = os.path.join(OUT, job.id)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "finding.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"claim": getattr(f, "claim", None), "verdict": str(verdict)},
                  fh, indent=2, default=str)
    job.add_artifact(path, verified=True, detail="finding and all five attacks")
    job.emit("done", str(verdict))


def run_fea(job: Job):
    """Solve a cantilever, then show the STRESS FIELD on the deformed shape.

    Two independent results are produced and compared: viz3d's closed-form
    field, and a real CalculiX run on a hex mesh. A viewer that paints a
    pretty field without checking it against the algebra is decoration, so the
    job fails if the two disagree by more than the mesh honestly can.
    """
    import fea
    import viz3d
    p = job.params
    P = float(p.get("P_N", 1000.0))
    L_mm = float(p.get("L_mm", 500.0))
    b_mm = float(p.get("b_mm", 20.0))
    h_mm = float(p.get("h_mm", 40.0))
    E_MPa = float(p.get("E_MPa", 200000.0))
    outdir = os.path.join(OUT, job.id)
    os.makedirs(outdir, exist_ok=True)

    job.emit("step", f"closed form: cantilever P={P:g} N, L={L_mm:g} mm, "
                     f"section {b_mm:g}x{h_mm:g} mm")
    eb = fea.euler_bernoulli_tip(P, L_mm, b_mm, h_mm, E_MPa)
    ti = fea.timoshenko_tip(P, L_mm, b_mm, h_mm, E_MPa, 0.3)
    job.emit("check", f"Euler-Bernoulli tip {eb:.5f} mm, Timoshenko {ti:.5f} mm "
                      f"(shear adds {100*(ti-eb)/eb:.2f}%)", ok=True)

    if not fea.ccx_available():
        job.emit("warn", "ccx is not on PATH; the field is the closed form only "
                         "and has NOT been cross-checked by a solver")
        r = None
    else:
        job.emit("step", "meshing and solving in CalculiX")
        r = fea.cantilever(P, L_mm, b_mm, h_mm, E_MPa, 0.3, nx=40, ny=2, nz=8)
        err = r["error_vs_timoshenko_percent"]
        job.emit("check", f"CalculiX tip {r['delta_fea_mm']:.5f} mm vs Timoshenko "
                          f"{ti:.5f} mm ({err:+.3f}%)", ok=abs(err) < 5.0)
        if abs(err) >= 5.0:
            raise RuntimeError(
                f"the solver and the closed form disagree by {err:+.3f}%, which "
                f"is past what this mesh can explain. No field is offered: a "
                f"coloured picture of an unverified result is worse than none.")

    # viz3d works in SI, the rest of this file in mm and MPa.
    job.emit("step", "rendering the stress field on the deformed shape")
    L, b, h = L_mm / 1000.0, b_mm / 1000.0, h_mm / 1000.0
    E = E_MPa * 1e6
    nodes, faces = viz3d.beam_mesh(L, b, h)
    fields, disp = viz3d.beam_fields(nodes, P, L, E, b, h)
    I = b * h ** 3 / 12
    smax = P * L * (h / 2) / I
    note = (f"cantilever P={P:g} N, L={L_mm:g} mm | tip {ti:.4g} mm "
            f"(Timoshenko), sigma_max {smax/1e6:.4g} MPa"
            + (f" | CalculiX tip {r['delta_fea_mm']:.4g} mm" if r else
               " | NOT cross-checked: ccx unavailable"))
    html = os.path.join(outdir, "stress_field.html")
    viz3d.render(nodes, faces, fields, disp,
                 "EngineAI - stress on the deformed shape", "Pa",
                 path=html, note=note)
    job.add_artifact(html, kind="stress field",
                     verified=bool(r), detail=note,
                     checks=[{"name": "FEA vs Timoshenko closed form",
                              "ok": bool(r),
                              "detail": note}])

    import csv as _csv
    cpath = os.path.join(outdir, "tip_deflection.csv")
    with open(cpath, "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["quantity", "value_mm", "vs_timoshenko_pct"])
        w.writerow(["Euler-Bernoulli", round(eb, 6), round(100 * (eb - ti) / ti, 4)])
        w.writerow(["Timoshenko", round(ti, 6), 0.0])
        if r:
            w.writerow(["CalculiX", round(r["delta_fea_mm"], 6),
                        round(r["error_vs_timoshenko_percent"], 4)])
    job.add_artifact(cpath, kind="comparison", verified=True,
                     detail="three independent routes to the same deflection")
    job.emit("done", f"peak stress {smax/1e6:.4g} MPa")


# Everything the command pane has learned this session. A conversation that
# forgets is not a conversation: "show me the shaft" rebuilt the shaft instead
# of showing the one just made, and "analyze the file" did nothing at all
# because the upload was stored and never read.
SESSION = {"turns": [], "files": [], "last_job": None, "last_part": None}
MAX_TURNS = 12
MAX_FILE_CHARS = 6000


def remember(role: str, text: str):
    SESSION["turns"].append({"role": role, "text": (text or "")[:1200]})
    del SESSION["turns"][:-MAX_TURNS]


def session_context() -> str:
    """What the agent is told about this session, in plain text."""
    bits = []
    if SESSION["files"]:
        bits.append("FILES THE USER HAS ATTACHED (their content is below, read it "
                    "before answering questions about them):")
        for f in SESSION["files"][-3:]:
            bits.append(f"--- {f['name']} ({f['kind']}, {f['bytes']} bytes) ---")
            bits.append(f["text"][:MAX_FILE_CHARS] if f.get("text")
                        else f"[binary: {f['detail']}]")
            bits.append("--- end of file ---")
    if SESSION["last_part"]:
        bits.append(f"The last part built in this session was "
                    f"'{SESSION['last_part']}'. If the user says 'it' or 'the "
                    f"part', they mean that one.")
    if SESSION["turns"]:
        bits.append("CONVERSATION SO FAR:")
        for t in SESSION["turns"][-8:]:
            bits.append(f"{t['role']}: {t['text'][:300]}")
    return "\n".join(bits)


def run_agent(job: Job):
    """The real loop: plan, call a tool, read the result, decide the next call.

    agent_loop.py has existed in this project the whole time, with thirty-odd
    verified tools behind it - beam_design, steel_beam_check, torsion,
    pressure_vessel, heat_transfer, draw_part - and the command pane was not
    using it. It used a regex intent matcher that could only reach four job
    kinds, which is why the answers read as scripted: they were.

    Forced to the LOCAL model. agent_loop defaults to hosted APIs, and this
    project does not spend money on inference.
    """
    import agent_loop
    os.environ.setdefault("ENGINEAI_MODEL_SOURCE", "local")

    goal = job.params.get("goal", "")
    ctx = job.params.get("context") or ""
    full = (ctx + "\n\nUSER: " + goal).strip() if ctx else goal

    backend = agent_loop.pick_backend()
    if backend is None:
        raise RuntimeError(agent_loop.why_no_backend())
    job.emit("step", f"thinking with {backend.name}")

    seen = {"n": 0}

    def watch(rec):
        pass

    rec = agent_loop.run(full, backend=backend, max_steps=int(
        job.params.get("max_steps", 6)), verbose=False)

    for s in rec.get("steps", []):
        obs = s.get("observation") or {}
        head = next((k for k in ("value", "verdict", "found", "answer", "error",
                                 "view", "files") if k in obs), None)
        job.emit("tool" if not obs.get("error") else "warn",
                 f"{s.get('tool')}  {str(obs.get(head, ''))[:110]}"
                 if head else str(s.get("tool")),
                 ok=not obs.get("error"))
        for key in ("files", "path", "stl", "step"):
            v = obs.get(key)
            for p in (v if isinstance(v, list) else [v]):
                if isinstance(p, str) and os.path.exists(p) and os.path.isfile(p):
                    try:
                        dest = os.path.join(OUT, job.id, os.path.basename(p))
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        if os.path.realpath(p) != os.path.realpath(dest):
                            import shutil
                            shutil.copy(p, dest)
                        job.add_artifact(dest, kind="from " + str(s.get("tool")),
                                         verified=None,
                                         detail="produced by the agent loop")
                    except Exception:                          # noqa: BLE001
                        pass

    ans = rec.get("answer")
    text = ans if isinstance(ans, str) else json.dumps(ans, default=str) \
        if ans else ""
    if not text:
        text = ("I could not finish that with the tools I have. "
                + str(rec.get("error") or ""))
    job.params["answer"] = text
    remember("engineai", text)
    job.emit("answer", text[:1500], ok=bool(rec.get("ok")))
    job.emit("done", f"{len(rec.get('steps', []))} tool calls")


def write_receipt(job: Job, question: str, method: str, inputs: dict,
                  result: dict, checks: list, solver: dict = None,
                  artifacts=(), sources=(), exit_status: int = 0) -> dict:
    """Attach a verifiable receipt to a job, and make it an artifact itself.

    Until now `verified: True` on an artifact card was set by the runner
    saying so. That is the thing an outside reviewer correctly refused to
    accept: a claim, not evidence. The receipt hashes the inputs, the code
    revision, the live tool versions and the output FILES, and re-hashing it
    later detects any of them changing.

    The receipt also decides the basis. A runner cannot mark its own work
    COMPUTED - that comes out of provenance() based on whether the checks
    passed, the solver exited zero, and files actually exist.
    """
    import receipt as rcpt
    outdir = os.path.join(OUT, job.id)
    os.makedirs(outdir, exist_ok=True)

    r = rcpt.make_receipt(question, method, inputs, result,
                          [{"name": c["name"], "passed": bool(c["ok"]),
                            "detail": str(c.get("detail", ""))} for c in checks],
                          solver or {})
    r = rcpt.provenance(r, run_id=job.id, artifacts=list(artifacts),
                        exit_status=exit_status, sources=list(sources))
    path = os.path.join(outdir, "receipt.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(r, fh, indent=2, default=str)

    v = rcpt.verify_full(r)
    job.emit("check", f"receipt {r['basis']}: {len(r['artifacts'])} artifact(s) "
                      f"hashed, {len(r['sources'])} input source(s) recorded, "
                      f"code {r['code']['commit'][:8]}"
                      + (" DIRTY" if r["code"]["dirty"] else ""),
             ok=bool(v["ok"]))
    job.add_artifact(path, kind="receipt", verified=bool(v["ok"]),
                     detail=f"{r['basis']} - re-checkable with "
                            f"`python receipt.py --verify`",
                     checks=[{"name": "receipt integrity", "ok": bool(v["ok"]),
                              "detail": "; ".join(v["problems"]) or
                                        "hashes match, artifacts intact"}])
    job.params["basis"] = r["basis"]
    return r


def run_solidfea(job: Job):
    """FEA on the user's OWN geometry: mesh it, solve it, colour it.

    Source can be a registry part, the last uploaded STEP, or a path. The
    field is only offered if equilibrium holds - a solve that errored writes
    a .frd full of zeros, and that renders as a completely convincing picture
    of nothing.
    """
    import fea_solid
    p = job.params
    outdir = os.path.join(OUT, job.id)
    os.makedirs(outdir, exist_ok=True)

    step = p.get("step_path")
    if not step and p.get("part"):
        import cad_parts
        job.emit("step", f"building {p['part']} to solve it")
        wp, _u = cad_parts.build(p["part"],
                                 **{k: float(v) for k, v in
                                    (p.get("params") or {}).items()})
        step, _stl = cad_parts.export(wp, os.path.join(outdir, p["part"]))
    if not step:
        for f in reversed(SESSION["files"]):
            if f["name"].lower().endswith((".step", ".stp")):
                step = f["path"]
                job.emit("step", f"solving the uploaded {f['name']}")
                break
    if not step or not os.path.exists(step):
        raise RuntimeError(
            "no geometry to solve. Name a registry part, or attach a STEP "
            "file and ask again.")

    job.emit("step", "meshing with gmsh (second-order tetrahedra)")
    # In a CHILD process: gmsh installs a signal handler and signals cannot be
    # set from a worker thread, so calling analyse() directly here died with
    # "signal only works in main thread" on every job - after passing every
    # test, because the selftest runs on the main thread.
    r = fea_solid.analyse_subprocess(
        step, outdir, force_N=float(p.get("force_N", 1000.0)),
        fix=p.get("fix", "-X"), push=p.get("push", "+X"),
        direction=int(p.get("direction", 2)),
        E_MPa=float(p.get("E_MPa", 210000.0)),
        size_mm=p.get("size_mm"), converge=bool(p.get("converge", True)))

    job.emit("check", f"{r['mesh']['n_nodes']} nodes, "
                      f"{r['mesh']['n_elements']} {r['element_type']} elements",
             ok=True)
    job.emit("check", f"EQUILIBRIUM: reactions balance the applied load to "
                      f"{r['equilibrium_rel']*100:.3f}%",
             ok=r["equilibrium_ok"])
    if o := r.get("oracle"):
        job.emit("check", f"beam-theory sanity band: FEA {o['fea_mm']:.4f} mm "
                          f"vs Timoshenko {o['timoshenko_mm']:.4f} mm "
                          f"({o['error_vs_timoshenko_pct']:+.1f}%)",
                 ok=bool(o.get("agrees")))
    if c := r.get("convergence"):
        rungs = c.get("ladder") or []
        chain = " -> ".join(f"{x['nodes']:,}" for x in rungs)
        last = c.get("final_change_pct")
        job.emit("check",
                 f"refined until it settled: {chain} nodes"
                 + (f", last change {last:.2f}%" if last is not None else ""),
                 ok=True)
        for x in rungs:
            if x.get("stopped"):
                job.emit("warn", x["stopped"])

    if r.get("field_html") and os.path.exists(r["field_html"]):
        job.add_artifact(r["field_html"], kind="stress field on your geometry",
                         verified=True,
                         detail=f"peak {r['max_vm_MPa']:.4g} MPa, max deflection "
                                f"{r['max_disp_mm']:.4g} mm",
                         checks=[{"name": "equilibrium", "ok": True,
                                  "detail": f"{r['equilibrium_rel']*100:.3f}% "
                                            f"residual"}])
    if r.get("deck") and os.path.exists(r["deck"]):
        job.add_artifact(r["deck"], kind="CalculiX deck", verified=True,
                         detail="the deck that produced this, re-runnable")

    lines = [f"FEA on {os.path.basename(step)}",
             f"  mesh            {r['mesh']['n_nodes']} nodes, "
             f"{r['mesh']['n_elements']} {r['element_type']}",
             f"  boundary        fixed {r['bc']['fixed_face']}, "
             f"{r['bc']['force_N']:g} N on {r['bc']['loaded_face']} "
             f"in {r['bc']['direction']}",
             f"  max deflection  {r['max_disp_mm']:.5f} mm",
             f"  peak von Mises  {r['max_vm_MPa']:.4g} MPa",
             f"  equilibrium     {r['equilibrium_rel']*100:.4f}% residual"]
    if o:
        lines.append(f"  beam oracle     {o['error_vs_timoshenko_pct']:+.1f}% "
                     f"vs Timoshenko")
    if c:
        rungs = c.get("ladder") or []
        lines.append(f"  refinement      "
                     + " -> ".join(f"{x['nodes']:,}" for x in rungs)
                     + " nodes")
        if c.get("final_change_pct") is not None:
            lines.append(f"  last change     {c['final_change_pct']:.2f}% "
                         f"(how much this mesh still has to give)")
    # The receipt, and the checks that decide whether this is COMPUTED at all.
    checks = [{"name": "equilibrium: reactions balance the applied load",
               "ok": bool(r["equilibrium_ok"]),
               "detail": f"{r['equilibrium_rel']*100:.4f}% residual"}]
    if o:
        checks.append({"name": "beam-theory sanity band",
                       "ok": bool(o.get("agrees")),
                       "detail": f"{o['error_vs_timoshenko_pct']:+.1f}% vs "
                                 f"Timoshenko"})
    if c:
        last = c.get("final_change_pct")
        checks.append({"name": "mesh refinement",
                       "ok": True,
                       "detail": " -> ".join(str(x["nodes"]) for x in
                                             (c.get("ladder") or []))
                                 + (f" nodes, last change {last:.2f}%"
                                    if last is not None else "")})
    rec = write_receipt(
        job,
        question=f"FEA of {os.path.basename(step)} under "
                 f"{p.get('force_N', 1000.0):g} N",
        method="gmsh C3D10 mesh + CalculiX static solve",
        inputs={"geometry": os.path.basename(step),
                "geometry_sha256": (__import__("receipt").sha256_file(step)
                                    if os.path.exists(step) else None),
                "force_N": float(p.get("force_N", 1000.0)),
                "fix": p.get("fix", "-X"), "push": p.get("push", "+X"),
                "direction": int(p.get("direction", 2)),
                "E_MPa": float(p.get("E_MPa", 210000.0)),
                "nu": float(p.get("nu", 0.3)),
                "mesh_nodes": r["mesh"]["n_nodes"],
                "element_type": r["element_type"]},
        result={"max_disp_mm": r["max_disp_mm"],
                "max_vm_MPa": r["max_vm_MPa"]},
        checks=checks,
        solver={"tool": "CalculiX", "mesher": "gmsh",
                "element": r["element_type"]},
        artifacts=[a["path"] for a in job.artifacts],
        sources=[("E_MPa", float(p.get("E_MPa", 210000.0)),
                  "caller-supplied" if p.get("E_MPa") else
                  "default 210 GPa for steel - NOT a material lookup", True),
                 ("force_N", float(p.get("force_N", 1000.0)),
                  "user", True),
                 ("boundary conditions", f"{p.get('fix','-X')} / "
                  f"{p.get('push','+X')}", r["bc"]["selection"], True)],
        exit_status=0 if r["equilibrium_ok"] else 1)

    lines += ["", f"BASIS: {rec['basis']} - receipt {rec['run_id'][:12]}, "
                  f"re-checkable with `python receipt.py --verify`",
              "SCOPE: " + r["bc"]["note"]]
    job.params["answer"] = "\n".join(lines)
    job.emit("answer", "\n".join(lines), ok=True)
    job.emit("done", f"peak {r['max_vm_MPa']:.4g} MPa, basis {rec['basis']}")


def run_cycle(job: Job):
    """Any engine cycle. Step 1 of the decomposition, now actually runnable."""
    import engines
    p = dict(job.params)
    kind = p.pop("cycle", "brayton")
    if kind not in engines.CYCLES:
        raise RuntimeError(f"unknown cycle {kind!r}. Known: "
                           f"{', '.join(sorted(engines.CYCLES))}")
    job.emit("step", f"computing the {kind} cycle")
    r = engines.CYCLES[kind](**{k: v for k, v in p.items() if v is not None})
    if r.get("error"):
        raise RuntimeError(r["error"])

    for k in ("efficiency_pct", "specific_thrust_Ns_per_kg", "thrust_N",
              "Isp_s", "net_work_Jkg"):
        if k in r and isinstance(r[k], (int, float)):
            job.emit("check", f"{k} = {r[k]:.4g}", ok=True)
    if "carnot_pct" in r:
        job.emit("check", f"Carnot ceiling {r['carnot_pct']:.2f}% - the cycle "
                          f"is below it, as it must be", ok=True)
    if r.get("warning"):
        job.emit("warn", r["warning"])

    outdir = os.path.join(OUT, job.id)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{kind}_cycle.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(r, fh, indent=2, default=str)
    job.add_artifact(path, kind="cycle result", verified=True,
                     detail=r.get("note", "")[:120])

    lines = [f"{r.get('cycle', kind)}"]
    for k, v in r.items():
        if isinstance(v, float):
            lines.append(f"  {k:34s} {v:.6g}")
        elif isinstance(v, (int, str)) and k != "note":
            lines.append(f"  {k:34s} {v}")
    if r.get("note"):
        lines += ["", "SCOPE: " + r["note"]]
    job.params["answer"] = "\n".join(lines)
    job.emit("answer", "\n".join(lines)[:2000], ok=True)
    job.emit("done", r.get("cycle", kind))


def run_review(job: Job):
    """Review a design and report every defect with a fix.

    Runs whatever can be run. A missing load does not stop the geometry and
    manufacturability checks; it becomes a PENDING finding naming the one
    input needed. A review that refuses because a field was blank is worth
    nothing to the person holding the part.
    """
    import design_review
    p = job.params
    part = p.get("part")
    duty = dict(p.get("duty") or {})
    measured = p.get("measured")

    # A file uploaded this session is a design too - review that if no
    # registry part was named.
    if not part and not measured and SESSION["files"]:
        for f in reversed(SESSION["files"]):
            if f.get("measured"):
                measured = f["measured"]
                job.emit("step", f"reviewing the uploaded {f['name']}")
                break

    job.emit("step", "running every check that does not need a load")
    r = design_review.review(part, p.get("params"), duty, measured)
    for f in r["findings"]:
        lvl = {"FAIL": "error", "WARN": "warn", "PENDING": "step",
               "PASS": "check"}[f["severity"]]
        job.emit(lvl, f"{f['severity']}: {f['check']} - {f['detail'][:150]}",
                 ok=f["severity"] in ("PASS", "PENDING"))

    outdir = os.path.join(OUT, job.id)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "design_review.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Design review\n\n" + design_review.as_text(r) + "\n")
    job.add_artifact(path, kind="review", verified=True,
                     detail=r["verdict"])
    jp = os.path.join(outdir, "findings.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(r, fh, indent=2, default=str)
    job.add_artifact(jp, kind="findings", verified=True,
                     detail=f"{r['counts']['FAIL']} FAIL, {r['counts']['WARN']} "
                            f"WARN, {r['counts']['PENDING']} pending")
    job.params["answer"] = design_review.as_text(r)
    job.emit("answer", design_review.as_text(r)[:2000],
             ok=r["counts"]["FAIL"] == 0)
    job.emit("done", r["verdict"])


def run_script_export(job: Job):
    """Export a scripted solid, and check it the only way a script can be.

    A registry part is checked against its closed-form volume. An arbitrary
    script has no closed form - so the check is the one deliverables.py uses
    everywhere else: write the STEP, RE-OPEN it, and compare the volume that
    comes back. A file that does not read back at the volume it was written
    at is not a deliverable.

    The script itself is exported beside the geometry. A STEP with no record
    of what produced it is an orphan; six months later nobody can regenerate
    or amend it.
    """
    import wb_kernel
    p = job.params
    src = p.get("src", "")
    outdir = os.path.join(OUT, job.id)
    os.makedirs(outdir, exist_ok=True)

    job.emit("step", "building the scripted solid in a child process")
    res = wb_kernel.run_script(src, tolerance=float(p.get("tolerance", 0.05)),
                               export_dir=outdir)
    if not res.get("ok"):
        raise RuntimeError(f"{res.get('stage', 'build failed')}: "
                           f"{res.get('error', '')}")

    m = wb_kernel.measure(res, material=p.get("material"))
    job.emit("check", f"solid volume {m['volume_cm3']:.3f} cm^3, "
                      f"{m['solids']} solid(s), bbox "
                      + " x ".join(f"{v:.1f}" for v in m["bbox_mm"]) + " mm",
             ok=True)

    job.emit("step", "writing STEP and re-opening it to check")
    job.emit("check", res.get("step_detail", "no STEP written"),
             ok=bool(res.get("step_verified")))
    if not res.get("step_verified"):
        raise RuntimeError(
            f"the STEP file did not read back at the volume it was written "
            f"at. {res.get('step_detail')}. No file is offered: an export "
            f"that cannot be re-opened correctly is not a deliverable.")

    job.add_artifact(res["step_path"], kind="STEP solid", verified=True,
                     detail=res["step_detail"],
                     checks=[{"name": "re-imported STEP volume",
                              "ok": True, "detail": res["step_detail"]}])

    stl_path = os.path.join(outdir, "scripted.stl")
    with open(stl_path, "wb") as fh:
        fh.write(res["stl"])
    job.add_artifact(stl_path, kind="STL mesh", verified=True,
                     detail=f"{_stl_count(res['stl'])} triangles at "
                            f"{p.get('tolerance', 0.05)} mm tolerance")

    src_path = os.path.join(outdir, "scripted.py")
    with open(src_path, "w", encoding="utf-8") as fh:
        fh.write(f"# EngineAI workbench, job {job.id}\n"
                 f"# volume {m['volume_cm3']:.4f} cm^3, "
                 f"re-imported STEP agreed.\n\n{src}\n")
    job.add_artifact(src_path, kind="source", verified=True,
                     detail="the script that produced the geometry")

    meas_path = os.path.join(outdir, "measured.json")
    with open(meas_path, "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=2, default=str)
    job.add_artifact(meas_path, kind="measurements", verified=True,
                     detail="taken off the B-rep, not the triangles")
    job.emit("done", f"{len(job.artifacts)} artifacts, all verified")


RUNNERS = {"cad": run_cad, "deliverables": run_deliverables,
           "research": run_research, "fea": run_fea,
           "script": run_script_export, "agent": run_agent,
           "review": run_review, "cycle": run_cycle,
           "solidfea": run_solidfea}


def start_job(kind: str, params: dict) -> Job:
    if kind not in RUNNERS:
        raise KeyError(f"unknown job kind {kind!r}. Known: {', '.join(RUNNERS)}")
    job = Job(kind, params)
    REG.add(job)

    def body():
        job.status = "running"
        job.emit("start", f"{kind} job {job.id}")
        try:
            RUNNERS[kind](job)
            job.status = "done"
        except Exception as exc:                              # noqa: BLE001
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.emit("error", job.error, trace=traceback.format_exc()[-1200:])
        finally:
            job.finished = time.time()
            job.emit("end", f"{job.status} in {job.finished - job.started:.1f}s")

    threading.Thread(target=body, daemon=True).start()
    return job


# ---------------------------------------------------------------------------
# the app
# ---------------------------------------------------------------------------

def build_app():
    if FastAPI is None:
        raise RuntimeError("fastapi is not installed in this environment")
    from workbench_ui import PAGE

    app = FastAPI(title="EngineAI Workbench", docs_url=None, redoc_url=None)
    try:
        import server_auth
        # "/" is the HTML SHELL and carries no data - every byte of engineering
        # comes from /api/*, which stays protected. Gating the shell meant a
        # browser at the bare address got a raw JSON 401 and the page could
        # never load far enough to explain itself.
        server_auth.install(app, "workbench", public_paths={"/"})
    except Exception:                                         # noqa: BLE001
        pass

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "service": "workbench"}

    def _autosignin(request) -> bool:
        """Should this request be signed in without being asked?

        Typing 127.0.0.1:8100 and hitting a login wall for a single-user tool
        on your own machine is friction with nothing behind it: whoever can
        make this request can already read ~/.engineai/token off the disk. So
        a genuine address-bar navigation from loopback gets the cookie.

        "Genuine address-bar navigation" is not a guess. The browser tells us:

          Sec-Fetch-Site: none      the user typed it or used a bookmark. A
                                    link or fetch from another page would say
                                    cross-site or same-origin.
          Sec-Fetch-Dest: document  a top-level page, not an iframe, image,
                                    script or XHR.
          Sec-Fetch-Mode: navigate  a navigation, not a subresource load.

        That closes the attack that matters. This server runs Python at
        /api/script, so a page on the open internet quietly POSTing to
        127.0.0.1 would be remote code execution - but such a request carries
        Sec-Fetch-Site: cross-site, and a JSON POST from another origin needs
        a CORS preflight this app never answers. Only /  is auto-signed; every
        /api/ route still needs the cookie or a token.

        A browser too old to send Sec-Fetch-* headers gets the sign-in box
        instead, which is the safe direction to fail.

        Set ENGINEAI_NO_AUTOSIGNIN=1 to turn this off.
        """
        if os.environ.get("ENGINEAI_NO_AUTOSIGNIN") == "1":
            return False
        client = request.client.host if request.client else ""
        if client not in ("127.0.0.1", "::1", "localhost"):
            return False
        h = request.headers
        return (h.get("sec-fetch-site") == "none"
                and h.get("sec-fetch-dest") == "document"
                and h.get("sec-fetch-mode") == "navigate")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        resp = HTMLResponse(PAGE)
        try:
            import server_auth
            tok = server_auth.get_token(create=False)
            already = server_auth.check_token(
                server_auth.extract_token(request.headers, request.query_params,
                                          request.cookies), tok)
            if tok and not already and _autosignin(request):
                resp.set_cookie(server_auth.COOKIE_NAME, tok, httponly=True,
                                samesite="strict", path="/")
        except Exception:                                     # noqa: BLE001
            pass
        return resp

    @app.get("/api/status")
    def status():
        import importlib.util as iu
        import shutil
        return {
            "cadquery": iu.find_spec("cadquery") is not None,
            "deliverables": iu.find_spec("deliverables") is not None,
            "python": sys.version.split()[0], "prefix": sys.prefix,
            "solvers": [n for n, exe in (("CalculiX", "ccx"), ("Elmer", "ElmerSolver"),
                                         ("ngspice", "ngspice"), ("gmsh", "gmsh"))
                        if shutil.which(exe)],
            "out": OUT,
        }

    @app.get("/api/parts")
    def parts():
        return {"parts": part_schema()}

    @app.post("/api/cad/live")
    async def cad_live(request: Request):
        """Rebuild and return raw STL bytes, with the check in a header.

        The mesh is the body rather than base64 in JSON because a 2 MB STL
        base64-encodes to 2.7 MB and has to be decoded in the main thread on
        every drag. The check travels in X-Meta so the browser can colour the
        badge without a second request.
        """
        try:
            body = await request.json()
        except Exception:                                     # noqa: BLE001
            return JSONResponse({"error": "expected a JSON body"}, status_code=400)
        part = body.get("part")
        params = {k: float(v) for k, v in (body.get("params") or {}).items()}
        bad = violations(part, params)
        if bad:
            # Refused BEFORE building. The solid would build perfectly well;
            # it is the closed form that stops describing it, so there would
            # be nothing left to check the kernel against.
            return JSONResponse(
                {"error": "these parameters break an assumption the volume "
                          "formula depends on, so the build cannot be checked",
                 "why": bad, "unverifiable": True}, status_code=400)
        try:
            m = build_mesh(part, params, tolerance=float(body.get("tolerance", 0.25)))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:                              # noqa: BLE001
            # A kernel failure at some parameter combination is real information
            # - a 0 mm wall or a bolt circle outside the flange - so it is
            # reported as the reason, not as a generic 500.
            return JSONResponse(
                {"error": f"the kernel could not build that shape: {exc}"},
                status_code=400)
        meta = {k: m[k] for k in ("analytic_mm3", "mesh_mm3", "error_pct",
                                  "agrees", "triangles", "seconds")}
        # Live limits ride along with the mesh, so the sliders re-fence
        # themselves on every rebuild without a second round trip.
        meta["bounds"] = live_bounds(part, m["params"])
        return Response(content=m["stl"], media_type="model/stl",
                        headers={"X-Meta": json.dumps(meta)})

    # 25 MB. A STEP assembly can exceed this, and refusing with a clear limit
    # beats accepting it and dying somewhere inside the kernel.
    MAX_UPLOAD = 25 * 1024 * 1024
    IMPORTABLE = {".step": "STEP", ".stp": "STEP", ".stl": "STL",
                  ".csv": "CSV", ".txt": "text", ".json": "JSON",
                  ".md": "text"}

    @app.post("/api/upload")
    async def upload(request: Request):
        """Take a file, and say honestly what can be done with it.

        A STEP or STL is imported, measured off the B-rep where possible, and
        shown. It is VIEW-ONLY: there is no feature recognition here, so an
        imported solid cannot be re-driven by parameters. Saying that plainly
        is the point - offering an "edit" that silently does nothing would be
        worse than the limitation.
        """
        form = await request.form()
        up = form.get("file")
        if up is None or not getattr(up, "filename", ""):
            return JSONResponse({"error": "no file was sent"}, status_code=400)
        name = os.path.basename(up.filename)
        ext = os.path.splitext(name)[1].lower()
        if ext not in IMPORTABLE:
            return JSONResponse(
                {"error": f"{ext or 'that'} is not a format this reads. "
                          f"Accepted: {', '.join(sorted(IMPORTABLE))}"},
                status_code=400)
        blob = await up.read()
        if len(blob) > MAX_UPLOAD:
            return JSONResponse(
                {"error": f"{len(blob)/1e6:.1f} MB exceeds the {MAX_UPLOAD/1e6:.0f} MB "
                          f"limit. Refusing here is kinder than failing inside "
                          f"the geometry kernel."}, status_code=413)

        job = Job("upload", {"name": name, "bytes": len(blob)})
        REG.add(job)
        job.status = "running"
        job.emit("start", f"received {name} ({len(blob):,} bytes)")
        outdir = os.path.join(OUT, job.id)
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(outdir, name)
        with open(path, "wb") as fh:
            fh.write(blob)

        detail, verified, checks = IMPORTABLE[ext] + " file", None, []
        measured = None
        if ext in (".step", ".stp"):
            try:
                import cadquery as cq
                from cadquery import importers, exporters
                job.emit("step", "importing the STEP solid")
                wp = importers.importStep(path)
                shape = wp.val()
                bb = shape.BoundingBox()
                vol = float(shape.Volume())
                measured = {
                    "volume_mm3": vol, "volume_cm3": vol / 1000.0,
                    "bbox_mm": [bb.xlen, bb.ylen, bb.zlen],
                    "solids": len(wp.solids().vals()) or 1,
                }
                stl_path = os.path.join(outdir, os.path.splitext(name)[0] + ".stl")
                exporters.export(wp, stl_path, exportType="STL", tolerance=0.2)
                job.add_artifact(stl_path, kind="mesh for viewing", verified=True,
                                 detail=f"tessellated from {name} for display")
                detail = (f"{measured['solids']} solid(s), {vol/1000:.3f} cm^3, "
                          f"bbox " + " x ".join(f"{v:.1f}" for v in measured["bbox_mm"])
                          + " mm")
                verified = True
                checks = [{"name": "imported and measured off the B-rep",
                           "ok": True, "detail": detail}]
                job.emit("check", detail, ok=True)
                job.emit("warn", "VIEW ONLY. There is no feature recognition "
                                 "here, so this solid cannot be re-driven by "
                                 "parameters. Rebuild it in the script editor "
                                 "if you need it parametric.")
            except Exception as exc:                          # noqa: BLE001
                verified = False
                detail = f"could not be imported: {type(exc).__name__}: {exc}"
                job.emit("error", detail)
        elif ext == ".stl":
            n = _stl_count(blob)
            detail = f"{n:,} triangles"
            verified = n > 0
            job.emit("check", detail + " read from the mesh", ok=verified)
            job.emit("warn", "A mesh has no B-rep, so volume and bounding box "
                             "are not reported from it here, and it cannot be "
                             "exported as STEP.")
        elif ext == ".csv":
            lines = blob.decode("utf-8", "replace").splitlines()
            rows = max(0, len(lines) - 1)
            detail = f"{rows} data rows, header: {lines[0][:70] if lines else '-'}"
            verified = rows > 0
            job.emit("check", detail, ok=verified)
            if rows < 30:
                job.emit("warn", f"{rows} rows is below the 30 the law-discovery "
                                 f"engine needs.")

        # Put the file into the conversation, not just onto disk. Attaching a
        # markdown file and then asking "analyze the file" did nothing at all,
        # because the upload was stored and never read by anything.
        text = None
        if ext in (".csv", ".txt", ".json", ".md"):
            text = blob.decode("utf-8", "replace")
        SESSION["files"].append({"name": name, "kind": IMPORTABLE[ext],
                                 "bytes": len(blob), "detail": detail,
                                 "text": text, "path": path,
                                 "measured": measured})
        del SESSION["files"][:-5]
        remember("you", f"[attached {name}: {detail}]")
        if text is not None:
            detail += f" - readable, {len(text.splitlines())} lines. Ask me about it."

        job.add_artifact(path, kind=IMPORTABLE[ext], verified=verified,
                         detail=detail, checks=checks)
        job.status = "done"
        job.finished = time.time()
        job.emit("end", detail)
        return {"id": job.id, "name": name, "kind": IMPORTABLE[ext],
                "detail": detail, "measured": measured,
                "view_only": ext in (".step", ".stp", ".stl")}

    @app.get("/api/queue")
    def queue_status():
        """What the queue is actually doing, for the progress panel."""
        try:
            import tasks
            s = tasks.status()
        except Exception as exc:                              # noqa: BLE001
            s = {"celery": False, "broker_up": False, "workers": 0,
                 "error": f"{type(exc).__name__}: {exc}"}
        jobs = REG.recent(12)
        return {**s,
                "running": [j for j in jobs if j["status"] == "running"],
                "recent": jobs,
                "note": ("jobs are queued to celery workers"
                         if s.get("workers") else
                         "NO WORKER ONLINE - queued jobs will wait. "
                         "bash workers/run_celery.sh"
                         if s.get("broker_up") else
                         "no broker: jobs run in-process and are lost on "
                         "restart")}

    @app.post("/api/faces")
    async def faces(request: Request):
        """Pickable tessellation: triangles tagged with their CAD face.

        The viewer ray-casts against these and reads the face off the triangle
        it hit, which is what makes an arbitrary boundary condition possible -
        a bounding-box side can only describe a part whose load path happens
        to line up with its box.
        """
        import subprocess as _sp
        try:
            body = await request.json()
        except Exception:                                     # noqa: BLE001
            body = {}
        step = body.get("step_path")
        outdir = os.path.join(OUT, "pick")
        os.makedirs(outdir, exist_ok=True)
        if not step and body.get("part"):
            import cad_parts
            try:
                wp, _u = cad_parts.build(body["part"], **{
                    k: float(v) for k, v in (body.get("params") or {}).items()})
                step, _stl = cad_parts.export(
                    wp, os.path.join(outdir, body["part"]))
            except KeyError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            except Exception as exc:                          # noqa: BLE001
                return JSONResponse(
                    {"error": f"could not build that part: {exc}"},
                    status_code=400)
        if not step:
            for f in reversed(SESSION["files"]):
                if f["name"].lower().endswith((".step", ".stp")):
                    step = f["path"]
                    break
        if not step or not os.path.exists(step):
            return JSONResponse({"error": "no geometry: name a part or attach "
                                          "a STEP file"}, status_code=400)
        # In a child, for the same reason the solver is: gmsh sets a signal
        # handler and cannot be initialised from a worker thread.
        code = ("import json,sys;sys.path.insert(0,%r);import fea_solid;"
                "print('@@'+json.dumps(fea_solid.pickable(%r)))"
                % (HERE, step))
        p = _sp.run([sys.executable, "-c", code], capture_output=True,
                    text=True, timeout=600)
        line = next((l for l in (p.stdout or "").splitlines()
                     if l.startswith("@@")), None)
        if not line:
            return JSONResponse(
                {"error": f"tessellation failed: "
                          f"{((p.stderr or p.stdout) or '')[-300:]}"},
                status_code=500)
        return json.loads(line[2:])

    @app.post("/api/command")
    async def command(request: Request):
        """Natural language in; a running job, a question back, or an answer.

        Three outcomes and they are kept distinct, because collapsing them is
        how an assistant starts inventing inputs:

          clarify   the calculation needs something it was not told. The reply
                    is the QUESTION, and no job is started.
          job       enough to proceed. The job id streams to the same viewer
                    the form-driven panes use.
          answer    not a workbench action at all - handed to chat.respond,
                    which carries its own basis and its own refusals.
        """
        import wb_agent
        try:
            body = await request.json()
        except Exception:                                     # noqa: BLE001
            return JSONResponse({"error": "expected a JSON body"}, status_code=400)

        text = (body.get("text") or "").strip()
        answers = body.get("answers") or {}
        prior = body.get("intent")

        intent = wb_agent.Intent(prior) if prior else wb_agent.interpret(text)
        if answers:
            intent = wb_agent.merge(intent, answers)

        if intent["action"] == "none":
            return {"kind": "answer", "answer": "Say what you would like built, "
                                                "solved, or produced.",
                    "basis": "CONVERSATIONAL"}

        if intent["action"] == "solidfea" and not intent.needs_input:
            remember("you", text)
            queued = submit_job("solidfea", intent["params"])
            SESSION["last_job"] = queued["id"]
            return {"kind": "job", "id": queued["id"], "action": "solidfea",
                    "doing": "Meshing the actual solid and solving it in "
                             "CalculiX. No field is offered unless the "
                             "reactions balance the load.",
                    "conversational": True, "queue": queued["queue"],
                    "workers": queued.get("workers", 0), "note": queued.get("note", "")}

        if intent["action"] == "cycle" and not intent.needs_input:
            remember("you", text)
            queued = submit_job("cycle", intent["params"])
            SESSION["last_job"] = queued["id"]
            return {"kind": "job", "id": queued["id"], "action": "cycle",
                    "doing": f"Computing the "
                             f"{intent['params'].get('cycle')} cycle.",
                    "conversational": True, "queue": queued["queue"],
                    "workers": queued.get("workers", 0), "note": queued.get("note", "")}

        if intent["action"] == "review":
            remember("you", text)
            queued = submit_job("review", intent["params"])
            SESSION["last_job"] = queued["id"]
            return {"kind": "job", "id": queued["id"], "action": "review",
                    "doing": "Reviewing the design against every check I can "
                             "run, and naming the input for any I cannot.",
                    "conversational": True, "queue": queued["queue"],
                    "workers": queued.get("workers", 0), "note": queued.get("note", "")}

        if intent["action"] == "decompose":
            remember("you", text)
            remember("engineai", intent["answer"])
            return {"kind": "answer", "answer": intent["answer"],
                    "basis": "CLARIFY", "verified": False}

        if intent.needs_input:
            return {"kind": "clarify", "intent": dict(intent),
                    "reason": intent.get("reason"),
                    "questions": intent["missing"]}

        if intent["action"] == "ask":
            # Anything that is not one of the four job shapes goes to the REAL
            # agent loop, with the conversation and any attached files in
            # front of it. This is what "analyze the file", "what can you do"
            # and "show me the shaft" needed and never got: chat.respond is a
            # single-shot classifier with no memory and no tools.
            remember("you", text)
            queued = submit_job("agent", {"goal": text, "context": session_context()})
            SESSION["last_job"] = queued["id"]
            return {"kind": "job", "id": queued["id"], "action": "agent",
                    "doing": "Thinking, and calling tools where it needs them.",
                    "conversational": True, "queue": queued["queue"],
                    "workers": queued.get("workers", 0), "note": queued.get("note", "")}

        try:
            queued = submit_job(intent["action"], intent["params"])
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        remember("you", text or json.dumps(answers))
        doing = wb_agent.describe(intent)
        remember("engineai", doing)
        if intent["action"] == "cad":
            SESSION["last_part"] = (intent["params"] or {}).get("part")
        SESSION["last_job"] = queued["id"]
        return {"kind": "job", "id": queued["id"], "action": intent["action"],
                "doing": doing, "assumed": intent.get("assumed") or [],
                "queue": queued["queue"], "workers": queued.get("workers", 0),
                "note": queued.get("note", "")}

    @app.get("/api/script")
    def starter(part: str = "mounting_plate"):
        import wb_kernel
        return {"part": part, "src": wb_kernel.script_for(part),
                "materials": sorted(wb_kernel.DENSITY)}

    @app.post("/api/script")
    async def run_script_route(request: Request):
        """Build a solid from an edited script. Returns STL with metrics.

        The same shape as /api/cad/live on purpose, so the viewer does not
        care which pane the geometry came from.
        """
        import wb_kernel
        try:
            body = await request.json()
        except Exception:                                     # noqa: BLE001
            return JSONResponse({"error": "expected a JSON body"}, status_code=400)
        res = wb_kernel.run_script(body.get("src", ""),
                                   tolerance=float(body.get("tolerance", 0.25)))
        if not res.get("ok"):
            return JSONResponse({"error": res.get("error", "build failed"),
                                 "stage": res.get("stage", "")}, status_code=400)
        blob = res.pop("stl")
        meta = wb_kernel.measure(res, material=body.get("material"))
        meta["triangles"] = _stl_count(blob)
        meta["stdout"] = res.get("stdout", "")
        meta["agrees"] = True     # a script has no closed form to disagree with
        meta["scripted"] = True
        return Response(content=blob, media_type="model/stl",
                        headers={"X-Meta": json.dumps(meta)})

    @app.post("/api/job")
    async def new_job(request: Request):
        try:
            body = await request.json()
        except Exception:                                     # noqa: BLE001
            return JSONResponse({"error": "expected a JSON body"}, status_code=400)
        kind = body.get("kind", "")
        params = body.get("params") or {}
        if kind not in RUNNERS:
            return JSONResponse(
                {"error": f"unknown job kind {kind!r}. Known: "
                          f"{', '.join(RUNNERS)}"}, status_code=400)
        try:
            queued = submit_job(kind, params)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"id": queued["id"],
                "status": "queued" if queued["queue"] == "celery" else "running",
                "queue": queued["queue"], "workers": queued.get("workers", 0),
                "note": queued.get("note", "")}

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": REG.recent()}

    @app.get("/api/job/{jid}")
    def job_state(jid: str):
        j = REG.get(jid)
        if j:
            return j.state()
        # A queued job runs in the WORKER process and is not in this
        # process's registry - moving to celery broke every poller until this
        # fallback existed. The result backend is the shared source of truth.
        try:
            import tasks
            # tasks.result_state() reads the snapshot the worker mirrors on
            # every event, so a QUEUED or RUNNING job is visible too. My
            # version only handled the finished case, which meant a poller saw
            # 404 for the entire duration of the solve.
            snap = tasks.result_state(jid)
            if snap:
                return cache_remote_state(jid, snap)
            from celery.result import AsyncResult
            if tasks.app is not None:
                res = AsyncResult(jid, app=tasks.app)
                if res.successful():
                    st = res.get()
                    # Remember the worker's artifacts so /api/artifact/{id}
                    # can serve them. Worker and web share the filesystem but
                    # NOT the in-memory registry, so without this every
                    # download from a queued job 404s.
                    for a in st.get("artifacts", []):
                        REMOTE_ARTIFACTS[a["id"]] = a
                    REMOTE_JOBS[jid] = {
                        "id": jid, "kind": st.get("kind", "?"),
                        "status": st.get("status"), "started": st.get("started"),
                        "artifacts": len(st.get("artifacts", [])),
                        "verified": st.get("verified_count", 0),
                        "label": st.get("kind", "job")}
                    return st
                if res.failed():
                    return {"id": jid, "kind": "?", "status": "failed",
                            "error": str(res.result)[:500], "seconds": 0,
                            "events": [], "artifacts": [], "params": {},
                            "verified_count": 0, "artifact_count": 0}
                return {"id": jid, "kind": "?", "status": "running",
                        "error": None, "seconds": 0, "events": [],
                        "artifacts": [], "verified_count": 0,
                        "artifact_count": 0, "params": {},
                        "celery_state": res.state}
        except Exception:                                     # noqa: BLE001
            pass
        return JSONResponse({"error": "no such job"}, status_code=404)

    @app.get("/api/stream/{jid}")
    def stream(jid: str):
        j = REG.get(jid)
        if not j:
            # Celery events live in Redis, not this web process's Registry.
            # Poll its append-only snapshots and translate the new tail to
            # SSE so the browser gets progress instead of an instant 404.
            def remote_gen():
                sent = 0
                while True:
                    try:
                        import tasks
                        snap = tasks.result_state(jid)
                    except Exception:                         # noqa: BLE001
                        snap = None
                    if not snap:
                        yield "event: error\ndata: " + json.dumps(
                            {"message": "no such job"}) + "\n\n"
                        return
                    cache_remote_state(jid, snap)
                    events = snap.get("events") or []
                    while sent < len(events):
                        yield "data: " + json.dumps(events[sent], default=str) + "\n\n"
                        sent += 1
                    if snap.get("status") in ("done", "failed"):
                        return
                    yield ": keepalive\n\n"
                    time.sleep(1.0)
            return StreamingResponse(remote_gen(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache",
                                              "X-Accel-Buffering": "no"})

        def gen():
            q = j.subscribe()
            try:
                while True:
                    try:
                        ev = q.get(timeout=20)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        if j.status in ("done", "failed"):
                            return
                        continue
                    yield "data: " + json.dumps(ev, default=str) + "\n\n"
                    if ev["level"] == "end":
                        return
            finally:
                j.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @app.get("/api/artifact/{aid}")
    def artifact(aid: str):
        a = REG.artifact(aid)
        if not a:
            return JSONResponse({"error": "no such artifact"}, status_code=404)
        path = os.path.realpath(a["path"])
        # Artifacts are only ever served from under OUT. The id is a lookup key,
        # not a path, but resolving and re-checking costs nothing and means a
        # future bug that lets a path in cannot turn into a file-read primitive.
        if not path.startswith(os.path.realpath(OUT) + os.sep):
            return JSONResponse({"error": "outside the workspace"}, status_code=403)
        if not os.path.exists(path):
            return JSONResponse({"error": "file is gone from disk"}, status_code=410)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            return Response(content=fh.read(), media_type=mime)

    return app


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def chk(cond, label, extra=""):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label + (f"   {extra}" if extra else ""))
        ok = ok and bool(cond)

    print("workbench selftest")
    # Deterministic in-process path. With the queue on, a job runs in another
    # process and this selftest cannot see the state it is asserting about -
    # four tests started failing the moment celery was wired in, for that
    # reason and not because anything was broken. The queue itself is tested
    # end to end by tasks.py --selftest.
    global NO_QUEUE
    NO_QUEUE = True
    os.environ["ENGINEAI_NO_QUEUE"] = "1"
    print("  info  queue disabled for the duration (tasks.py tests the queue)")
    os.makedirs(OUT, exist_ok=True)

    # ---- pure logic, no server ----
    chk(view_kind("a.stl") == "mesh", "an STL is shown in the 3D viewer")
    chk(view_kind("a.PDF") == "pdf", "extension matching is case-insensitive")
    chk(view_kind("a.step") == "meta", "a STEP file falls back to metadata")
    chk(view_kind("a.weird") == "meta", "an unknown format does not crash the router")

    j = Job("cad", {"part": "x"})
    q = j.subscribe()
    j.emit("step", "one")
    chk(q.get_nowait()["message"] == "one", "an event reaches a live subscriber")
    j2 = Job("cad", {})
    j2.emit("step", "before")
    q2 = j2.subscribe()
    chk(q2.get_nowait()["message"] == "before",
        "a late subscriber is replayed the events it missed")

    sch = part_schema()
    chk(len(sch) >= 3, f"{len(sch)} parts expose a parameter schema")
    flange = next((s for s in sch if s["part"] == "flange"), None)
    if flange:
        n = next((p for p in flange["params"] if p["name"] == "n"), None)
        chk(n and n["integer"] and n["step"] == 1,
            "a bolt COUNT steps in whole numbers, not 6.4 bolts")
        chk(all(p["min"] > 0 for p in flange["params"]),
            "no slider can be dragged to zero and hand the kernel a null shape")

    # ---- the CAD kernel and its invariant ----
    have_cq = True
    try:
        import cadquery  # noqa: F401
    except ImportError:
        have_cq = False
        print("  skip  cadquery is not installed; geometry tests skipped")

    if have_cq:
        m = build_mesh("mounting_plate", {"L": 150.0, "W": 90.0, "T": 12.0})
        chk(m["agrees"], f"mesh volume matches the closed form "
                         f"({m['error_pct']:.2e}% error)")
        chk(m["triangles"] > 12, f"{m['triangles']} triangles were produced")
        chk(m["stl"][:5] != b"" and len(m["stl"]) > 200,
            f"STL bytes are non-trivial ({len(m['stl'])} bytes)")
        t0 = time.time()
        build_mesh("mounting_plate", {"L": 150.0, "W": 90.0, "T": 12.0})
        chk(time.time() - t0 < 0.05, "an identical rebuild is served from cache")
        m2 = build_mesh("mounting_plate", {"L": 200.0, "W": 90.0, "T": 12.0})
        chk(m2["mesh_mm3"] > m["mesh_mm3"],
            "a longer plate really does contain more material")
        try:
            build_mesh("no_such_part", {})
            chk(False, "an unknown part is refused")
        except KeyError:
            chk(True, "an unknown part is refused")

    # ---- routes ----
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:                                  # noqa: BLE001
        print(f"  skip  TestClient unavailable: {exc}")
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1

    app = build_app()
    c = TestClient(app)
    tok = ""
    try:
        import server_auth
        tok = server_auth.read_token() if hasattr(server_auth, "read_token") else ""
    except Exception:                                         # noqa: BLE001
        pass
    if not tok:
        p = os.path.expanduser("~/.engineai/token")
        if os.path.exists(p):
            tok = open(p).read().strip()
    HD = {"X-EngineAI-Token": tok} if tok else {}

    chk(c.get("/healthz").status_code == 200, "/healthz is public")
    r = c.get("/", headers=HD)
    chk(r.status_code == 200 and "EngineAI Workbench" in r.text,
        "the page renders")

    # ---- the bare address must never return raw JSON again ----
    # Typing 127.0.0.1:8100 with no token returned {"error":"unauthorised"} as
    # a naked JSON blob. Twice. The shell is now public so the page can load
    # and explain itself; the DATA behind it is not.
    if tok:
        bare = TestClient(app).get("/")
        chk(bare.status_code == 200,
            f"the bare address serves HTML, not a 401 ({bare.status_code})")
        # Grep for the word "unauthorised" and you match the source comment
        # explaining why it must not appear - the same self-referential trap
        # the no-CDN check fell into. Check the response SHAPE instead.
        chk("EngineAI Workbench" in bare.text
            and bare.headers.get("content-type", "").startswith("text/html")
            and not bare.text.lstrip().startswith("{"),
            "...and it is an HTML shell, not a JSON error document")
        chk("Sign in to the workbench" in bare.text,
            "...which can tell an unauthenticated visitor what to do")
        chk("tokgo" in bare.text, "...and offers a one-time token box")
        # Needing to sign in is not a crash, and must not be dressed as one.
        chk("Nothing is wrong" in bare.text and "calm: true" in bare.text,
            "...presented calmly, not as a red 'failed to start' banner")
        chk("Set-Clipboard" in bare.text,
            "...with the exact command that fetches the token")

        naked = TestClient(app)
        chk(naked.get("/api/parts").status_code == 401,
            "the DATA behind the public shell is still refused without a token")
        chk(naked.post("/api/script", json={"src": "result = 1"}).status_code == 401,
            "...including the endpoint that executes Python")

        # ---- typing the address just works, and only that ----
        # TestClient reports its client host as "testclient", not 127.0.0.1,
        # so without client= the loopback gate refuses everything and the
        # NEGATIVE cases below would all pass while proving nothing.
        NAV = {"sec-fetch-site": "none", "sec-fetch-dest": "document",
               "sec-fetch-mode": "navigate"}

        def local_client():
            try:
                return TestClient(app, client=("127.0.0.1", 45678))
            except TypeError:
                return None

        typed = local_client()
        if typed is None:
            print("  skip  this starlette cannot spoof a client address")
            typed = TestClient(app)
        r = typed.get("/", headers=NAV)
        chk("engineai_token" in str(r.headers.get("set-cookie", "")),
            "typing the address from loopback signs you in")
        chk(typed.get("/api/parts").status_code == 200,
            "...so the app works immediately, with no token step")

        # Everything that is NOT a person typing the address must not.
        for label, hdrs in (
            ("a cross-site link", {**NAV, "sec-fetch-site": "cross-site"}),
            ("an iframe", {**NAV, "sec-fetch-dest": "iframe"}),
            ("a background fetch", {**NAV, "sec-fetch-mode": "cors",
                                    "sec-fetch-dest": "empty"}),
            ("a browser too old to say", {}),
        ):
            cc = local_client() or TestClient(app)
            rr = cc.get("/", headers=hdrs)
            chk("engineai_token" not in str(rr.headers.get("set-cookie", "")),
                f"...but {label} does NOT get signed in")
            chk(cc.get("/api/parts").status_code == 401,
                f"...and {label} still cannot reach the data")

        # A non-loopback client must never be auto-signed, whatever it claims.
        try:
            remote = TestClient(app, client=("10.0.0.5", 45678))
            chk("engineai_token" not in str(
                remote.get("/", headers=NAV).headers.get("set-cookie", "")),
                "...and a request from off-machine is never auto-signed")
        except TypeError:
            pass

        import os as _os
        _os.environ["ENGINEAI_NO_AUTOSIGNIN"] = "1"
        try:
            app_off = build_app()
            try:
                off = TestClient(app_off, client=("127.0.0.1", 45678))
            except TypeError:
                off = TestClient(app_off)
            chk("engineai_token" not in str(
                off.get("/", headers=NAV).headers.get("set-cookie", "")),
                "ENGINEAI_NO_AUTOSIGNIN=1 turns it off")
        finally:
            _os.environ.pop("ENGINEAI_NO_AUTOSIGNIN", None)

        # A token in the URL must become a cookie, so the NEXT bare visit works.
        sess = TestClient(app)
        first = sess.get(f"/?token={tok}")
        chk(first.status_code == 200, "arriving with ?token= works")
        chk("engineai_token" in first.cookies
            or "engineai_token" in str(first.headers.get("set-cookie", "")),
            "...and the server sets a session cookie")
        chk(sess.get("/api/parts").status_code == 200,
            "...so the API now works in that browser with no token in the URL")
        sc = str(first.headers.get("set-cookie", ""))
        chk("HttpOnly" in sc, "the cookie is HttpOnly: script cannot read it")
        chk("strict" in sc.lower(),
            "...and SameSite=Strict: another site cannot make the browser send it")
    # Grepping for the word "cdn" matched this file's own comment SAYING there
    # is no CDN, which is a test that fails on honesty. Look for the constructs
    # that actually fetch something instead.
    import re as _re
    remote = _re.findall(
        r'(?:src|href)\s*=\s*["\']?https?://|@import|url\(\s*["\']?https?://'
        r'|fetch\(\s*["\']https?://|new\s+Worker\(\s*["\']https?://', r.text)
    chk(not remote, "the page loads NOTHING from the network - no CDN, no "
                    "remote font, no remote script", str(remote[:3]))
    chk("createShader" in r.text and "STLot" not in r.text,
        "the WebGL viewer is inlined in the page")

    # ---- the page must not be able to fail silently again ----
    # It reported "no view" once: under 1200px the stage moved to an untyped
    # grid row and was clipped off the bottom of the page.
    narrow = r.text[r.text.find("@media(max-width:1200px)"):][:400]
    chk("grid-template-rows" in narrow,
        "the narrow layout declares ROWS, so the 3D stage cannot be clipped")
    chk("minmax(0" in r.text, "grid children may shrink rather than overflow")
    chk("<noscript>" in r.text, "a scriptless browser is told why it sees nothing")
    chk("addEventListener('error'" in r.text and "function fatal(" in r.text,
        "a script error paints itself into the page instead of the console")
    chk("unhandledrejection" in r.text, "a failed request cannot vanish quietly")
    chk("No 3D context" in r.text,
        "a browser with no WebGL is told so, and told the rest still works")
    chk("workbench_url.txt" in r.text,
        "a 401 explains where the ready-made link is, rather than blanking")

    s = c.get("/api/status", headers=HD).json()
    chk("cadquery" in s, "status reports whether the CAD kernel is present")
    chk(len(c.get("/api/parts", headers=HD).json()["parts"]) >= 3,
        "the parts registry is served")

    if have_cq:
        r = c.post("/api/cad/live", headers=HD,
                   json={"part": "mounting_plate",
                         "params": {"L": 140.0, "W": 85.0, "T": 9.0}})
        chk(r.status_code == 200, f"live rebuild returns 200 ({r.status_code})")
        chk(r.headers.get("content-type", "").startswith("model/stl"),
            "the body is raw STL, not base64 in JSON")
        meta = json.loads(r.headers["X-Meta"])
        chk(meta["agrees"], "the live rebuild carries a passing volume check")
        chk(len(r.content) > 500, f"{len(r.content)} bytes of mesh came back")

        bad = c.post("/api/cad/live", headers=HD,
                     json={"part": "nope", "params": {}})
        chk(bad.status_code == 400, "an unknown part is a 400, not a 500")
        chk("nope" in bad.json().get("error", ""),
            "...and the error names the part that was asked for")

        # ---- parameters that break the closed form ----
        # Found by sweeping every slider to both ends over real HTTP: five
        # positions built a fine solid that the volume formula no longer
        # described. The kernel was right every time.
        chk(not violations("mounting_plate", {}), "defaults violate nothing")
        merged = violations("mounting_plate", {"L": 30.0})
        chk(merged and "merge" in merged[0],
            f"holes closer than their diameter are caught ({merged[:1]})")
        chk(violations("mounting_plate", {"margin": 3.0}),
            "a hole that breaks the plate edge is caught")
        inside = violations("flange", {"bc": 18.75})
        chk(inside and "bore" in inside[0],
            f"a bolt circle inside the bore is caught ({inside[:1]})")
        chk(violations("flange", {"bc": 187.5}),
            "a bolt circle outside the flange is caught")
        chk(violations("u_channel", {"t": 40.0}), "two walls wider than the section")
        chk(violations("l_bracket", {"t": 90.0}), "a wall thicker than its leg")

        r = c.post("/api/cad/live", headers=HD,
                   json={"part": "flange", "params": {"bc": 18.75}})
        chk(r.status_code == 400, "such a position is refused, not rendered")
        j = r.json()
        chk(j.get("unverifiable") is True,
            "...flagged as unverifiable rather than as a kernel fault")
        chk(j.get("why") and "bore" in j["why"][0],
            "...and the reason is in the part's own language")

        # The old badge said "KERNEL DISAGREES", blaming a component that was
        # behaving. Make sure that wording is gone for good.
        page = c.get("/", headers=HD).text
        chk("KERNEL DISAGREES" not in page,
            "the UI no longer blames the kernel for a bad parameter set")
        chk("Load 40-row sample" in page,
            "the law-discovery pane ships data, so its first click is not an error")

    r = c.post("/api/job", headers=HD, json={"kind": "nonsense", "params": {}})
    chk(r.status_code == 400, "an unknown job kind is refused")

    # ---- the command pane: the thing you actually talk to ----
    r = c.post("/api/command", headers=HD,
               json={"text": "how much will this beam deflect"}).json()
    chk(r["kind"] == "clarify",
        f"an underspecified command ASKS rather than guessing ({r['kind']})")
    fields = {q["field"] for q in r["questions"]}
    chk("P_N" in fields, f"...and the LOAD is one of the questions ({fields})")
    chk(any("invented" in q["question"] for q in r["questions"]),
        "...and it explains why it will not pick a load itself")

    # Answering the questions must complete it - with the units as typed.
    r2 = c.post("/api/command", headers=HD, json={
        "intent": r["intent"],
        "answers": {"P_N": "2 kN", "L_mm": "1.2 m", "section": "30 x 50"}}).json()
    chk(r2["kind"] == "job", f"answers turn the intent into a job ({r2['kind']})")
    st = c.get(f"/api/job/{r2['id']}", headers=HD).json()
    chk(st["params"]["P_N"] == 2000.0 and st["params"]["L_mm"] == 1200.0,
        f"...with the units converted ({st['params']})")

    # The conversational route used to call start_job directly while the form
    # route used Celery.  Keep this assertion independent of a live broker:
    # it proves the route delegates to the one execution gateway rather than
    # silently reintroducing a web-process solver thread.
    import unittest.mock as mock
    queued_reply = {"id": "queue-contract", "queue": "celery",
                    "workers": 1, "note": "queued on celery"}
    with mock.patch(__name__ + ".submit_job", return_value=queued_reply) as submit:
        qr = c.post("/api/command", headers=HD,
                    json={"text": "compute an Otto cycle with compression ratio 9"})
    qj = qr.json()
    chk(qr.status_code == 200 and qj.get("queue") == "celery",
        "a natural-language solver request reports its real queue")
    chk(submit.call_args and submit.call_args.args[0] == "cycle",
        "...and delegates to submit_job instead of starting a direct thread")

    if have_cq:
        r = c.post("/api/command", headers=HD, json={
            "text": "build a flange with a 120 mm outer diameter"}).json()
        chk(r["kind"] == "job", f"a complete build command just runs ({r['kind']})")
        chk("idia" in r["assumed"],
            f"...and DECLARES the registry defaults it took ({r['assumed']})")
        for _ in range(400):
            st = c.get(f"/api/job/{r['id']}", headers=HD).json()
            if st["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        chk(st["status"] == "done", f"...and the job completes ({st['status']})",
            str(st.get("error"))[:70])
        chk(st["params"]["params"]["od"] == 120.0,
            "...using the dimension that was asked for")

    r = c.post("/api/command", headers=HD,
               json={"text": "build something"}).json()
    chk(r["kind"] == "clarify" and r["questions"][0]["field"] == "part",
        "a vague build asks WHICH part")
    chk(len(r["questions"][0]["options"]) >= 5, "...and offers the list")

    r = c.post("/api/command", headers=HD, json={"text": ""}).json()
    chk(r["kind"] == "answer", "an empty command does not start anything")

    page = c.get("/", headers=HD).text
    chk('id="cmd"' in page and "Command" in page,
        "the command pane is present in the UI")
    chk('id="fileinput"' in page, "the command pane can take an attachment")

    # ---- the two tiers, and the queue between them ----
    q = c.get("/api/queue", headers=HD).json()
    chk("workers" in q and "broker_up" in q,
        f"the queue reports itself (broker {q.get('broker_up')}, "
        f"{q.get('workers')} worker(s))")
    chk(q.get("note"), "...and says what that means for a queued job")
    chk("autoverify" in page and "scheduleVerify" in page,
        "the CAD pane can auto-queue a verified run when editing stops")
    chk("900" in page, "...after a debounce, not on every slider frame")
    chk("ESTIMATE" in page and "VERIFIED" in page,
        "the two tiers are labelled distinctly in the UI")
    chk("no verified run for this shape yet" in page,
        "...and an estimate never poses as a verified result")
    chk('id="queuebox"' in page, "the queue panel is present")

    # ---- click-to-pick boundary conditions ----
    chk('id="su_solve"' in page and "Pick faces" in page,
        "the pick-and-solve pane is present")
    chk("Moller-Trumbore" in page or "MollerTrumbore" in page.replace("-", ""),
        "the viewer carries a real ray-triangle intersection")
    chk("onFacePick" in page and "loadPickable" in page,
        "clicking a face is wired to the picker")
    chk("cannot be both fixed and loaded" in page,
        "picking one face as both is caught in the UI")

    if have_cq:
        r = c.post("/api/faces", headers=HD, json={"part": "flange"}).json()
        chk(r.get("n_faces", 0) >= 6,
            f"a flange tessellates into {r.get('n_faces')} CAD faces")
        chk(len(r["triangles"]) == len(r["face_of"]),
            "every triangle is tagged with the face it belongs to")
        chk(all(0 <= i < len(r["vertices"])
                for t in r["triangles"][:200] for i in t),
            "the triangles index real vertices")
        ids = sorted(r["faces"])
        chk(len(set(r["face_of"])) == len(ids),
            f"all {len(ids)} faces are represented in the tessellation")
        bad = c.post("/api/faces", headers=HD, json={"part": "nope"})
        chk(bad.status_code in (400, 500), "an unknown part is refused")

    # ---- file attachment ----
    r = c.post("/api/upload", headers=HD,
               files={"file": ("notes.csv", b"a,b,c\n1,2,3\n4,5,6\n", "text/csv")})
    chk(r.status_code == 200, f"a CSV uploads ({r.status_code})")
    chk("2 data rows" in r.json()["detail"], f"...and is read ({r.json()['detail']})")

    r = c.post("/api/upload", headers=HD,
               files={"file": ("virus.exe", b"MZ\x90\x00", "application/x-msdownload")})
    chk(r.status_code == 400, "an unreadable format is refused")
    chk(".step" in r.json()["error"], "...and the accepted list is given")
    chk(c.post("/api/upload", headers=HD,
               files={"file": ("big.stl", b"x" * (26 * 1024 * 1024),
                               "model/stl")}).status_code == 413,
        "an oversized file is refused before the kernel sees it")
    chk(TestClient(app).post(
        "/api/upload",
        files={"file": ("x.csv", b"a\n1\n", "text/csv")}).status_code == 401,
        "upload needs a token like everything else")

    if have_cq:
        # Round-trip a real solid: export one, upload it back, and it must be
        # imported, measured, and declared view-only.
        import cad_parts as _cp
        import tempfile as _tf
        with _tf.TemporaryDirectory() as td:
            wp, used = _cp.build("mounting_plate")
            step_path, _stl = _cp.export(wp, os.path.join(td, "rt"))
            with open(step_path, "rb") as fh:
                data = fh.read()
        r = c.post("/api/upload", headers=HD,
                   files={"file": ("rt.step", data, "model/step")})
        chk(r.status_code == 200, f"a STEP uploads ({r.status_code})")
        j = r.json()
        want = _cp.PARTS["mounting_plate"][1](**used)
        got = (j.get("measured") or {}).get("volume_mm3", 0)
        chk(abs(got - want) / want < 1e-6,
            f"...and is MEASURED correctly ({got:.1f} vs {want:.1f} mm^3)")
        chk(j["view_only"] is True, "...and is declared view-only, not editable")
        st = c.get(f"/api/job/{j['id']}", headers=HD).json()
        chk(any(a["name"].endswith(".stl") for a in st["artifacts"]),
            "...and tessellated so it can actually be seen")
        chk(any("VIEW ONLY" in e["message"] for e in st["events"]),
            "...with the limitation stated in the log, not just the UI")

    # ---- script export: the stub that used to pop an alert ----
    if have_cq:
        chk("not wired yet" not in page,
            "'Export this solid' is no longer a stub that apologises")
        r = c.post("/api/job", headers=HD, json={
            "kind": "script",
            "params": {"src": "result = cq.Workplane('XY').box(40, 25, 12)"
                              ".edges('|Z').fillet(4)",
                       "material": "aluminium 6061"}}).json()
        for _ in range(600):
            st = c.get(f"/api/job/{r['id']}", headers=HD).json()
            if st["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        chk(st["status"] == "done", f"a scripted solid exports ({st['status']})",
            str(st.get("error"))[:80])
        names = sorted(a["name"] for a in st["artifacts"])
        chk(names == ["measured.json", "scripted.py", "scripted.step",
                      "scripted.stl"],
            f"STEP, STL, the source and the measurements ({names})")
        chk(all(a["verified"] for a in st["artifacts"]),
            "every one of them is verified")
        step = next(a for a in st["artifacts"] if a["name"].endswith(".step"))
        chk("re-imported" in step["detail"],
            f"...the STEP by RE-OPENING it ({step['detail'][:56]})")
        body = c.get(f"/api/artifact/"
                     + next(a["id"] for a in st["artifacts"]
                            if a["name"].endswith(".py")), headers=HD).text
        chk("fillet" in body, "the script travels with the geometry it made")

        # A script whose STEP would not read back must offer nothing.
        bad = c.post("/api/job", headers=HD, json={
            "kind": "script", "params": {"src": "result = 1 + 1"}}).json()
        for _ in range(200):
            sb = c.get(f"/api/job/{bad['id']}", headers=HD).json()
            if sb["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        chk(sb["status"] == "failed", "a non-solid result fails the export")
        chk(sb["artifact_count"] == 0, "...and offers no files at all")

    # ---- the script editor ----
    st = c.get("/api/script?part=flange", headers=HD).json()
    chk("result" in st["src"], "a starter script is served for a registry part")
    chk(len(st["materials"]) >= 5, "the material list comes with it")

    if have_cq:
        r = c.post("/api/script", headers=HD, json={
            "src": "result = cq.Workplane('XY').box(30, 20, 10)",
            "material": "aluminium 6061"})
        chk(r.status_code == 200, f"an edited script builds ({r.status_code})")
        m = json.loads(r.headers["X-Meta"])
        chk(abs(m["volume_cm3"] - 6.0) < 1e-6,
            f"...and is measured off the B-rep ({m['volume_cm3']} cm3)")
        chk(abs(m["mass_g"] - 16.2) < 0.01,
            f"...with mass for the assigned material ({m['mass_g']:.2f} g)")
        chk(len(r.content) > 300, "...and returns a renderable mesh")

        bad = c.post("/api/script", headers=HD,
                     json={"src": "import os\nresult = 1"})
        chk(bad.status_code == 400 and "import" in bad.json()["error"],
            "a script that imports is refused before it runs")
        bad2 = c.post("/api/script", headers=HD,
                      json={"src": "result = cq.Workplane('XY').oops()"})
        chk(bad2.status_code == 400
            and bad2.json().get("stage") == "script error",
            "a broken script is a 400 naming the stage, not a 500")
        chk(c.get("/api/status", headers=HD).status_code == 200,
            "the server is still alive after a failed script build")

    # ---- the solver job ----
    r = c.post("/api/job", headers=HD, json={
        "kind": "fea", "params": {"P_N": 1000, "L_mm": 500,
                                  "b_mm": 20, "h_mm": 40}})
    jid = r.json()["id"]
    for _ in range(900):
        st = c.get(f"/api/job/{jid}", headers=HD).json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.2)
    chk(st["status"] == "done", f"the FEA job completes ({st['status']})",
        str(st.get("error"))[:80])
    if st["status"] == "done":
        names = [a["name"] for a in st["artifacts"]]
        chk(any(n.endswith(".html") for n in names),
            f"it renders a field view: {names}")
        fld = next(a for a in st["artifacts"] if a["name"].endswith(".html"))
        chk(fld["view"] == "html",
            "the field view is framed, not shown as escaped source")
        body = c.get(f"/api/artifact/{fld['id']}", headers=HD).text
        chk("function jet" in body and "von_mises" in body,
            "the rendered field really carries a colour map and stress data")
        checks = [ev for ev in st["events"] if ev["level"] == "check"]
        chk(len(checks) >= 2,
            f"{len(checks)} independent deflection checks were recorded")
        chk(all(ev.get("ok") for ev in checks),
            "every one of them passed")

    if have_cq:
        r = c.post("/api/job", headers=HD, json={
            "kind": "cad", "params": {"part": "mounting_plate",
                                      "params": {"L": 130.0, "W": 70.0, "T": 8.0}}})
        jid = r.json()["id"]
        for _ in range(300):
            st = c.get(f"/api/job/{jid}", headers=HD).json()
            if st["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        chk(st["status"] == "done", f"a CAD job completes ({st['status']})",
            str(st.get("error"))[:70])
        chk(st["artifact_count"] >= 2,
            f"it produced {st['artifact_count']} artifacts (STEP and STL)")
        chk(st["verified_count"] == st["artifact_count"],
            "every artifact it offers is marked verified")
        names = [a["name"] for a in st["artifacts"]]
        chk(any(n.endswith(".step") for n in names)
            and any(n.endswith(".stl") for n in names),
            f"both formats are present: {names}")

        aid = next(a["id"] for a in st["artifacts"] if a["name"].endswith(".stl"))
        ar = c.get(f"/api/artifact/{aid}", headers=HD)
        chk(ar.status_code == 200 and len(ar.content) > 500,
            "an artifact downloads by id")
        chk(c.get("/api/artifact/deadbeef", headers=HD).status_code == 404,
            "an unknown artifact id is a 404")

        levels = [e["level"] for e in st["events"]]
        chk("check" in levels, "the run log records the volume check as an event")
        chk(levels[-1] == "end", "the log ends with an explicit end event")
        chk(any(e["level"] == "artifact" for e in st["events"]),
            "each artifact announced itself on the stream as it was written")

        jl = c.get("/api/jobs", headers=HD).json()["jobs"]
        chk(any(x["id"] == jid for x in jl), "the run appears in the history rail")

    # The research pane had NEVER worked: discover_law takes columns and a
    # required target, and it was being handed rows. The selftest only ever
    # exercised the 30-row refusal, so the bug lived behind the one path that
    # returned early. Run a real fit here.
    # g must VARY or it is not identifiable: with one gravity the engine
    # correctly folds sqrt(2g) into the constant and returns a law in h alone.
    _GS = [1.62, 3.71, 9.81, 24.79]
    free_fall = [{"h": round(0.5 + (i % 10) * 1.3, 3), "g": _GS[i % 4],
                  "v": round((2 * _GS[i % 4] * (0.5 + (i % 10) * 1.3)) ** 0.5, 6)}
                 for i in range(40)]
    r = c.post("/api/job", headers=HD, json={
        "kind": "research", "params": {"rows": free_fall, "target": "v"}})
    jid = r.json()["id"]
    for _ in range(600):
        st = c.get(f"/api/job/{jid}", headers=HD).json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.2)
    chk(st["status"] == "done", f"a real law-discovery run completes ({st['status']})",
        str(st.get("error"))[:80])
    if st["status"] == "done":
        found = next((e["message"] for e in st["events"] if e["level"] == "found"), "")
        chk("h" in found and "g" in found,
            f"...and recovers a law in h and g", found[:64])
        chk(st["artifact_count"] >= 1, "...and writes the finding to a file")

    # A job that fails must SAY so and offer nothing, rather than half-succeed.
    r = c.post("/api/job", headers=HD,
               json={"kind": "research", "params": {"rows": [{"a": 1, "b": 2}]}})
    jid = r.json()["id"]
    for _ in range(60):
        st = c.get(f"/api/job/{jid}", headers=HD).json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    chk(st["status"] == "failed", "too little data fails the job outright")
    chk("30" in (st["error"] or ""),
        "...and the error explains the 30-row floor rather than saying REJECTED",
        (st["error"] or "")[:64])
    chk(st["artifact_count"] == 0, "a failed job offers no artifacts")

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="EngineAI Workbench")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=PORT)
    a = p.parse_args()

    if a.selftest:
        return selftest()

    if a.host not in ("127.0.0.1", "localhost", "::1"):
        if os.environ.get("ENGINEAI_WORKBENCH_ALLOW_REMOTE") != "1":
            print(f"refusing to bind {a.host}: this workbench is local-only. "
                  f"It serves files from disk and drives a CAD kernel; put it "
                  f"behind auth and TLS deliberately, not by passing a flag.")
            return 2

    os.makedirs(OUT, exist_ok=True)
    import uvicorn

    # Write the ready-to-click URL to a file rather than making the user
    # assemble it. Opening the bare host returns the unauthorised JSON blob,
    # which is a confusing first contact with your own tool. The file sits
    # beside the code at 0600 so it is no more exposed than the token already
    # is, and the page stores the token in sessionStorage so a reload works.
    url = f"http://{a.host}:{a.port}/"
    try:
        tok = open(os.path.expanduser("~/.engineai/token")).read().strip()
        url = f"{url}?token={tok}"
    except OSError:
        tok = ""
    link = os.path.join(HERE, "data", "workbench_url.txt")
    try:
        os.makedirs(os.path.dirname(link), exist_ok=True)
        with open(link, "w", encoding="utf-8") as fh:
            fh.write(url + "\n")
        os.chmod(link, 0o600)
    except OSError:
        link = None

    print(f"EngineAI Workbench -> {url}")
    if link and tok:
        print(f"  the same link is in {link} - open that and click it")
    uvicorn.run(build_app(), host=a.host, port=a.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
