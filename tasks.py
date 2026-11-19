#!/usr/bin/env python3
"""
tasks.py - the real job queue. Celery over Redis, replacing threads.

WHY THREADS WERE NOT ENOUGH
---------------------------
The workbench ran every job in a `threading.Thread`. That is fine for a demo
and wrong for this:

  * A restart loses in-flight work with no record it ever started.
  * Nothing limits concurrency, so two CalculiX runs and a gmsh mesh fight
    over the same cores and all three get slower.
  * A native crash in OCC or ccx takes the web server down with it.
  * There is no queue to look at, so "what is running" is unanswerable.
  * It cannot span machines, which is the whole point of the later move to
    Slurm.

A Celery worker is a separate process. It can be killed, restarted, scaled to
N workers, and watched.

DEGRADES HONESTLY
-----------------
If Redis is not up, `submit()` says so and runs the job in a thread exactly as
before, with `queue: "thread (redis unavailable)"` in the reply. It does not
pretend to have queued something. A silent fallback would make the queue depth
and the worker count meaningless.

  bash services.sh start                 # redis must be up
  bash workers/run_celery.sh             # start a worker
  python tasks.py --selftest
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

BROKER = os.environ.get("ENGINEAI_BROKER", "redis://127.0.0.1:6379/0")
BACKEND = os.environ.get("ENGINEAI_BACKEND", "redis://127.0.0.1:6379/1")

try:
    from celery import Celery
    app = Celery("engineai", broker=BROKER, backend=BACKEND)
    app.conf.update(
        task_serializer="json", result_serializer="json",
        accept_content=["json"],
        # Solvers are CPU-bound and long. One task at a time per worker, and
        # no prefetching, or a worker grabs a queue of meshes it cannot start.
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        task_track_started=True,
        result_expires=7 * 24 * 3600,
        # A CFD or a refinement ladder can legitimately run for an hour.
        task_time_limit=7200,
        task_soft_time_limit=6900,
        broker_connection_retry_on_startup=True,
    )
except Exception:                                             # noqa: BLE001
    app = None


def broker_up(timeout: float = 1.5) -> bool:
    """Is Redis actually answering? Asked, never assumed."""
    try:
        import redis
        return bool(redis.Redis.from_url(BROKER, socket_connect_timeout=timeout,
                                         socket_timeout=timeout).ping())
    except Exception:                                         # noqa: BLE001
        return False


def workers_online() -> int:
    if app is None or not broker_up():
        return 0
    try:
        replies = app.control.ping(timeout=1.0) or []
        return len(replies)
    except Exception:                                         # noqa: BLE001
        return 0


def result_state(task_id: str) -> dict | None:
    """Read a Celery job snapshot without executing or waiting for anything.

    Celery workers and the FastAPI server are separate processes.  The worker's
    in-memory ``workbench.REG`` is therefore invisible to ``/api/job`` unless
    its state is read back from the result backend.  Returning a common Job
    state shape lets the web process hydrate its own read-only cache and makes
    artifacts and the event stream available after a queued solve.
    """
    if app is None:
        return None
    try:
        res = app.AsyncResult(task_id)
        raw = res.info
        if isinstance(raw, dict) and raw.get("id"):
            return dict(raw)
        if res.state == "SUCCESS" and isinstance(res.result, dict):
            return dict(res.result)
        if res.state == "PENDING":
            return {"id": task_id, "kind": "unknown", "params": {},
                    "status": "queued", "error": None, "events": [],
                    "artifacts": [], "started": None, "finished": None,
                    "seconds": 0.0, "verified_count": 0,
                    "artifact_count": 0}
        if res.state in ("STARTED", "RETRY"):
            return {"id": task_id, "kind": "unknown", "params": {},
                    "status": "running", "error": None, "events": [],
                    "artifacts": [], "started": None, "finished": None,
                    "seconds": 0.0, "verified_count": 0,
                    "artifact_count": 0}
        if res.state == "FAILURE":
            error = str(res.result or "Celery task failed")
            return {"id": task_id, "kind": "unknown", "params": {},
                    "status": "failed", "error": error,
                    "events": [{"t": 0.0, "level": "error", "message": error},
                               {"t": 0.0, "level": "end", "message": "failed"}],
                    "artifacts": [], "started": 0.0, "finished": 0.0,
                    "seconds": 0.0, "verified_count": 0,
                    "artifact_count": 0}
    except Exception:                                         # noqa: BLE001
        return None
    return None


if app is not None:
    @app.task(bind=True, name="engineai.run_job")
    def run_job(self, kind: str, params: dict) -> dict:
        """Execute one workbench job inside a worker process.

        The runners are imported HERE rather than at module scope: a Celery
        worker should not have to import FastAPI, and the web process should
        not have to import a solver.
        """
        # Re-insert the path INSIDE the task. The module-scope insert does not
        # survive into the fork pool worker, so every queued job died with
        # "No module named 'workbench'" while the queue itself looked healthy.
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import workbench

        def mirror(snapshot: dict):
            # STARTED plus metadata is readable by another process while the
            # solver runs; the return value below replaces it on SUCCESS.
            self.update_state(state="STARTED", meta=snapshot)

        job = workbench.Job(kind, params, state_callback=mirror)
        job.id = self.request.id or job.id
        workbench.REG.add(job)
        job.status = "running"
        job.emit("start", f"{kind} on celery worker "
                          f"{self.request.hostname or '?'}")
        try:
            workbench.RUNNERS[kind](job)
            job.status = "done"
        except Exception as exc:                              # noqa: BLE001
            import traceback
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.emit("error", job.error, trace=traceback.format_exc()[-1200:])
        finally:
            import time as _t
            job.finished = _t.time()
            job.emit("end", f"{job.status}")
        return job.state()


def submit(kind: str, params: dict):
    """Queue a job if there is a queue; otherwise say so and run it here."""
    if app is not None and broker_up():
        n = workers_online()
        async_result = run_job.apply_async(args=[kind, params])
        return {"id": async_result.id, "queue": "celery",
                "workers": n,
                "note": (f"queued on celery, {n} worker(s) online"
                         if n else
                         "queued on celery but NO WORKER is online - it will "
                         "sit in the queue until one starts. "
                         "bash workers/run_celery.sh")}
    reason = "celery not installed" if app is None else "redis unavailable"
    import workbench
    job = workbench.start_job(kind, params)
    return {"id": job.id, "queue": f"thread ({reason})", "workers": 0,
            "note": f"running in-process because {reason}. It will complete, "
                    f"but it is not queued, not limited, and will be lost if "
                    f"the server restarts."}


def status() -> dict:
    return {"broker": BROKER, "broker_up": broker_up(),
            "workers": workers_online(), "celery": app is not None}


def selftest() -> int:
    global app
    ok = True

    def chk(cond, label, extra=""):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label
              + (f"   {extra}" if extra else ""))
        ok = ok and bool(cond)

    print("tasks selftest")
    # The web process cannot see a worker's REG.  Exercise result_state with
    # a stand-in backend so this contract is checked even when Redis is down.
    saved_app = app

    class _FakeResult:
        def __init__(self, state, info=None, result=None):
            self.state, self.info, self.result = state, info, result

    class _FakeApp:
        def __init__(self, result):
            self.result = result
        def AsyncResult(self, _task_id):
            return self.result

    sample = {"id": "00000000-0000-0000-0000-000000000001",
              "kind": "cycle", "params": {"cycle": "otto"},
              "status": "done", "error": None, "events": [],
              "artifacts": [], "started": 1.0, "finished": 2.0}
    app = _FakeApp(_FakeResult("SUCCESS", result=sample))
    chk(result_state(sample["id"]) == sample,
        "a completed worker result is readable by the web process")
    app = _FakeApp(_FakeResult("PENDING"))
    chk(result_state(sample["id"])["status"] == "queued",
        "a task waiting for a worker is reported as queued, not missing")
    app = _FakeApp(_FakeResult("FAILURE", result=RuntimeError("boom")))
    failed = result_state(sample["id"])
    chk(failed["status"] == "failed" and "boom" in failed["error"],
        "a worker failure reaches the web process as a failure")
    app = saved_app

    chk(app is not None, "celery is importable and configured")
    if app is not None:
        chk(app.conf.worker_prefetch_multiplier == 1,
            "a worker takes one solver job at a time, not a batch")
        chk(app.conf.task_acks_late is True,
            "tasks ack LATE, so a worker killed mid-solve requeues its job")
        chk(app.conf.task_time_limit >= 3600,
            f"long solves are allowed ({app.conf.task_time_limit}s)")

    up = broker_up()
    print(f"  info  redis at {BROKER}: {'up' if up else 'down'}")
    print(f"  info  workers online: {workers_online()}")

    # The fallback must be HONEST about what it did - that is the whole
    # contract. A silent thread fallback makes the queue meaningless.
    import unittest.mock as mock
    with mock.patch(__name__ + ".broker_up", return_value=False):
        # A REAL job kind: start_job rejects unknown ones, so a fake kind
        # tested the error path instead of the fallback path it claimed to.
        r = submit("cycle", {"cycle": "otto", "r": 7.0})
        chk(r["queue"].startswith("thread"),
            f"with no broker it falls back to a thread ({r['queue']})")
        chk("redis unavailable" in r["queue"] or "not installed" in r["queue"],
            "...and names the reason in the reply, not just the log")
        chk("not queued" in r["note"] and "lost if the server restarts"
            in r["note"],
            "...and states the consequences plainly")

    if up:
        n = workers_online()
        r = submit("cycle", {"cycle": "otto", "r": 9.0})
        chk(r["queue"] == "celery", f"with a broker it queues ({r['queue']})")
        chk(r["id"], "and returns a task id to follow")
        if n == 0:
            chk("NO WORKER" in r["note"],
                "with no worker running it SAYS the job will just sit there")
        else:
            import time
            from celery.result import AsyncResult
            res = AsyncResult(r["id"], app=app)
            for _ in range(120):
                if res.ready():
                    break
                time.sleep(0.5)
            # SUCCEEDED, not merely finished. The first version checked
            # ready() and then guarded the real assertions behind
            # `if successful()` - so a task that raised passed the test and
            # skipped everything that would have caught it.
            chk(res.ready() and res.successful(),
                f"a queued job ran and SUCCEEDED (state {res.state})",
                str(res.result)[:90] if res.failed() else "")
            if res.successful():
                st = res.get()
                chk(st.get("status") == "done",
                    f"...and the job itself reports done ({st.get('status')})",
                    str(st.get("error"))[:70])
                ans = (st.get("params") or {}).get("answer", "")
                chk("Otto" in ans or "efficiency" in ans.lower(),
                    f"...and produced the real result ({ans[:44]!r})")
    else:
        print("  skip  broker down; queue path not exercised")

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    if "--status" in sys.argv:
        import json
        print(json.dumps(status(), indent=2))
        raise SystemExit(0)
    print(__doc__)
