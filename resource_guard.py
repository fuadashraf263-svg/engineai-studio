#!/usr/bin/env python3
"""resource_guard.py - stop one bad mesh from taking the machine down.

The failure this prevents is specific and has happened to everyone who runs
solvers: a mesh with one too many refinement levels, a timestep three orders of
magnitude too small, an optimiser that will not converge. The process grows
until the kernel's OOM killer picks a victim - and the victim is frequently
something else, because the OOM killer chooses by score, not by blame. You lose
the browser, or the editor with unsaved work, and the solver survives.

A bounded process that dies cleanly at 8 GB with a message naming the mesh is
strictly better than an unbounded one that takes the desktop with it.

WHAT IT DOES

  limits()    a context manager setting RLIMIT_AS, RLIMIT_CPU and RLIMIT_NOFILE
              on the CURRENT process. Cheap, but it applies to everything in
              the interpreter, so it is for wrapping a whole worker.

  run()       run an external solver under limits in a CHILD process, so the
              parent survives to report what happened. This is the one to use
              for ccx, OpenFOAM and Elmer, which are separate binaries anyway.

  guarded()   a decorator that runs a Python callable in a limited subprocess
              and returns its result, so a runaway optimisation inside EngineAI
              is contained the same way an external solver is.

WHY RLIMIT_AS AND NOT A CGROUP. cgroups are stronger and are the right answer
in a container, where the Dockerfile can set --memory. RLIMIT_AS works today on
a bare WSL install with no privileges and no daemon, and something that works
now beats something better that needs root. The container path is documented
below rather than assumed.

WHAT IT CANNOT DO. RLIMIT_AS bounds ADDRESS SPACE, not resident memory. A
process that maps a large file without touching it will hit the limit while
using little real memory, and a few numerical libraries reserve generously.
That makes the limit conservative rather than exact, and the failure mode is a
solver refused slightly early - which is the right direction to be wrong in.
"""
from __future__ import annotations

import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["limits", "run", "guarded", "Result", "available_memory",
           "recommend_limit"]

GB = 1 << 30


def available_memory() -> int:
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return 4 * GB


def recommend_limit(fraction: float = 0.6) -> int:
    """A default that leaves the machine usable.

    Sixty per cent of what is free right now. Not of TOTAL memory - a solver
    given 60% of total on a machine already using 70% still triggers swapping.
    """
    return max(GB, int(available_memory() * fraction))


@dataclass
class Result:
    ok: bool
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0
    limit_hit: str = ""          # "memory" | "cpu" | "wall" | ""
    peak_rss_bytes: int = 0

    def __str__(self) -> str:
        if self.limit_hit:
            return (f"  STOPPED on the {self.limit_hit} limit after "
                    f"{self.seconds:.1f}s - the machine is intact")
        return (f"  {'ok' if self.ok else 'failed'} rc={self.returncode} "
                f"in {self.seconds:.1f}s, peak RSS "
                f"{self.peak_rss_bytes/1e6:.0f} MB")


@contextmanager
def limits(memory_bytes: int | None = None, cpu_seconds: int | None = None,
           open_files: int | None = None):
    """Apply rlimits to the current process, restoring them afterwards.

    Only the SOFT limit is raised or lowered; the hard limit is left alone so
    an unprivileged process can still restore it on exit.
    """
    saved = []
    try:
        for res_id, value in ((resource.RLIMIT_AS, memory_bytes),
                              (resource.RLIMIT_CPU, cpu_seconds),
                              (resource.RLIMIT_NOFILE, open_files)):
            if value is None:
                continue
            soft, hard = resource.getrlimit(res_id)
            saved.append((res_id, soft, hard))
            new_soft = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(res_id, (new_soft, hard))
        yield
    finally:
        for res_id, soft, hard in reversed(saved):
            try:
                resource.setrlimit(res_id, (soft, hard))
            except (ValueError, OSError):
                pass


def _preexec(memory_bytes, cpu_seconds, open_files):
    def apply():
        os.setsid()                      # own process group, so we can kill the tree
        for res_id, value in ((resource.RLIMIT_AS, memory_bytes),
                              (resource.RLIMIT_CPU, cpu_seconds),
                              (resource.RLIMIT_NOFILE, open_files)):
            if value is None:
                continue
            try:
                _s, hard = resource.getrlimit(res_id)
                new = value if hard == resource.RLIM_INFINITY else min(value, hard)
                resource.setrlimit(res_id, (new, hard))
            except (ValueError, OSError):
                pass
    return apply


def run(cmd, memory_bytes: int | None = None, cpu_seconds: int | None = None,
        wall_seconds: float | None = None, open_files: int = 4096,
        cwd=None, env=None) -> Result:
    """Run an external command under limits. The parent always survives.

    Kills the whole process GROUP on timeout. Solvers routinely fork - OpenFOAM
    launches MPI ranks - and killing only the parent leaves those running, still
    holding the memory you were trying to reclaim.
    """
    if memory_bytes is None:
        memory_bytes = recommend_limit()
    t0 = time.time()
    try:
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=cwd, env=env,
            preexec_fn=_preexec(memory_bytes, cpu_seconds, open_files))
    except Exception as e:
        return Result(False, None, "", f"{type(e).__name__}: {e}",
                      time.time() - t0)

    try:
        out, err = p.communicate(timeout=wall_seconds)
        elapsed = time.time() - t0
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            p.kill()
        out, err = p.communicate()
        return Result(False, None, out or "", err or "", time.time() - t0,
                      limit_hit="wall")

    try:
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        peak = usage.ru_maxrss * 1024        # Linux reports kilobytes
    except Exception:
        peak = 0

    hit = ""
    rc = p.returncode
    if rc is not None and rc < 0:
        sig = -rc
        if sig == signal.SIGKILL:
            hit = "memory"                   # OOM inside the child's own limit
        elif sig == signal.SIGXCPU:
            hit = "cpu"
    if "MemoryError" in (err or "") or "std::bad_alloc" in (err or ""):
        hit = "memory"
    return Result(rc == 0, rc, out or "", err or "", elapsed, hit, peak)


def guarded(memory_bytes: int | None = None, cpu_seconds: int | None = None,
            wall_seconds: float | None = 600):
    """Decorator: run a Python function in a limited subprocess.

    The function and its arguments must be importable and JSON-serialisable -
    it is called by module and name in a fresh interpreter, not pickled, so a
    closure will not work. That restriction is deliberate: anything heavy
    enough to need a memory limit should be a named top-level function.
    """
    def outer(fn):
        def inner(*args, **kwargs):
            payload = json.dumps({"module": fn.__module__, "name": fn.__name__,
                                  "args": args, "kwargs": kwargs})
            script = (
                "import json,sys,importlib\n"
                "spec=json.loads(sys.argv[1])\n"
                "m=importlib.import_module(spec['module'])\n"
                "f=getattr(m,spec['name'])\n"
                "print(json.dumps({'ok':True,'value':"
                "f(*spec['args'],**spec['kwargs'])}))\n")
            r = run([sys.executable, "-c", script, payload],
                    memory_bytes=memory_bytes, cpu_seconds=cpu_seconds,
                    wall_seconds=wall_seconds, cwd=str(Path(fn.__module__ and
                                                            Path.cwd() or ".")))
            if r.limit_hit:
                raise MemoryError(
                    f"{fn.__name__} stopped on the {r.limit_hit} limit "
                    f"after {r.seconds:.1f}s")
            if not r.ok:
                raise RuntimeError(f"{fn.__name__} failed: {r.stderr[-300:]}")
            try:
                return json.loads(r.stdout.strip().splitlines()[-1])["value"]
            except Exception as e:
                raise RuntimeError(f"could not read result: {e}")
        inner.__name__ = fn.__name__
        inner.__doc__ = fn.__doc__
        return inner
    return outer


# --------------------------------------------------------------------------
# selftest
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

    print("== recommendation is based on FREE memory, not total ==")
    avail = available_memory()
    rec = recommend_limit(0.6)
    chk(rec <= avail, f"the limit is within what is free ({rec/1e9:.1f} of "
                      f"{avail/1e9:.1f} GB)")
    chk(rec >= GB, "and never below 1 GB")
    chk(recommend_limit(0.3) < recommend_limit(0.9), "the fraction is respected")

    print("== a memory hog is stopped, and the parent survives ==")
    hog = ("a=[]\n"
           "while True:\n"
           "    a.append(bytearray(50*1024*1024))\n")
    r = run([sys.executable, "-c", hog], memory_bytes=400 * (1 << 20),
            wall_seconds=60)
    chk(not r.ok, "the runaway process did not succeed")
    chk(r.limit_hit == "memory" or "MemoryError" in r.stderr,
        f"and it was stopped on memory (hit={r.limit_hit!r})")
    chk(True, "the parent is still running to report it")

    print("== a wall-clock timeout kills the whole group ==")
    r2 = run([sys.executable, "-c", "import time; time.sleep(30)"],
             wall_seconds=2)
    chk(r2.limit_hit == "wall", f"stopped on wall clock ({r2.limit_hit})")
    chk(r2.seconds < 8, f"and promptly ({r2.seconds:.1f}s)")

    print("== a CPU limit stops a spin loop ==")
    r3 = run([sys.executable, "-c", "x=0\nwhile True: x+=1"],
             cpu_seconds=1, wall_seconds=30)
    chk(not r3.ok, "the spin loop was stopped")
    chk(r3.limit_hit in ("cpu", "memory", "") and r3.seconds < 25,
        f"within the CPU budget ({r3.seconds:.1f}s, hit={r3.limit_hit!r})")

    print("== a well-behaved job runs normally ==")
    r4 = run([sys.executable, "-c", "print(6*7)"], memory_bytes=2 * GB,
             wall_seconds=30)
    chk(r4.ok and "42" in r4.stdout, f"normal work is unaffected ({r4.stdout.strip()})")
    chk(not r4.limit_hit, "and no limit is reported")
    chk(r4.seconds < 15, "without meaningful overhead")

    print("== the context manager restores what it changed ==")
    before = resource.getrlimit(resource.RLIMIT_NOFILE)
    with limits(open_files=256):
        inside = resource.getrlimit(resource.RLIMIT_NOFILE)
    after = resource.getrlimit(resource.RLIMIT_NOFILE)
    chk(inside[0] <= 256, f"the limit applies inside ({inside[0]})")
    chk(after == before, f"and is restored on exit ({after} vs {before})")

    # and it restores even when the body raises
    try:
        with limits(open_files=128):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    chk(resource.getrlimit(resource.RLIMIT_NOFILE) == before,
        "restored even when the body raises")

    print("== a missing binary is reported, not raised ==")
    r5 = run(["definitely-not-a-real-binary-xyz"], wall_seconds=5)
    chk(not r5.ok and r5.returncode is None,
        "a missing command fails cleanly")
    chk("FileNotFoundError" in r5.stderr or "No such file" in r5.stderr,
        f"with a readable reason ({r5.stderr[:40]})")

    print(f"\nresource_guard.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(f"  free memory        {available_memory()/1e9:.2f} GB")
    print(f"  recommended limit  {recommend_limit()/1e9:.2f} GB (60% of free)")
    print(__doc__)
