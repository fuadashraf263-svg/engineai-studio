#!/usr/bin/env python3
"""
sandbox_runner.py - isolated runner for model-generated solvers, via Bubblewrap.

Runs untrusted Python with NO network, NO access to the host home directory, NO
access to any other mounted drive (e.g. WSL's /mnt/c - found and fixed 2026-08-10:
`--ro-bind / /` alone leaves every other mount, including the whole Windows C:
drive, readable from "inside" the sandbox), and a capped address space / process
count so a runaway (not even malicious - just buggy) generated script cannot
exhaust host memory. Returns (returncode, combined_output) - the interface
solve_or_build expects. If bwrap is unavailable it REFUSES; it never falls
through to host execution.
"""
from __future__ import annotations
import resource, shutil, subprocess

BWRAP = shutil.which("bwrap")

BWRAP_FLAGS = [
    "--ro-bind", "/", "/",
    "--tmpfs", "/home", "--tmpfs", "/root", "--tmpfs", "/tmp",
    "--tmpfs", "/mnt",   # hides every other mounted drive - on WSL this is the ONLY
                         # thing standing between "isolated" and the whole host C: drive
    "--dev", "/dev", "--proc", "/proc",
    "--unshare-all", "--new-session", "--die-with-parent",
]

DEFAULT_MEM_BYTES = 1 << 30   # 1 GiB - generated solvers are arithmetic, not big-data
# NB: fork bombs are contained by --unshare-all's PID namespace (the whole tree dies
# with the sandboxed process), NOT by RLIMIT_NPROC - that limit is per REAL UID
# system-wide, so capping it here intermittently broke bwrap's own namespace setup
# (observed: "Creating new namespace failed: Resource temporarily unavailable").
# Memory is the one runaway resource a PID namespace does NOT contain, so only that
# is rlimited.


def available():
    return BWRAP is not None


def _rlimit_mem(mem_bytes):
    def _set():
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    return _set


def run(code, timeout=15, mem_bytes=DEFAULT_MEM_BYTES):
    """(returncode, stdout+stderr). Refuses (never runs on host) if bwrap missing."""
    if not available():
        return 127, "REFUSED: bwrap not installed; refusing to run generated code on the host."
    argv = [BWRAP] + BWRAP_FLAGS + ["python3", "-"]
    try:
        p = subprocess.run(argv, input=code, capture_output=True, text=True, timeout=timeout,
                           preexec_fn=_rlimit_mem(mem_bytes))
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout}s"


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    if not available():
        rc, out = run("print('should never run')")
        chk("no bwrap -> REFUSES, does NOT run on host", rc == 127 and out.startswith("REFUSED"))
        print("sandbox_runner selftest: PASS (refusal path)"); return 0 if ok else 1
    rc, out = run("print('SANDBOX_OK')")
    chk("runs code + captures stdout", rc == 0 and "SANDBOX_OK" in out)
    rc, out = run("import os; print('home_empty', os.path.isdir('/home') and len(os.listdir('/home')) == 0)")
    chk("host home hidden", "home_empty True" in out)
    rc, out = run("import socket\ntry:\n socket.create_connection(('1.1.1.1',53),timeout=3); print('NET')\n"
                  "except Exception as e: print('net_blocked', type(e).__name__)")
    chk("network blocked", "net_blocked" in out)
    rc, out = run("open('/etc/hosts','a').write('x')")
    chk("host filesystem is read-only", rc != 0)
    rc, out = run("import os; print('mnt_empty', not os.path.isdir('/mnt/c') or len(os.listdir('/mnt')) == 0)")
    chk("other host drives (e.g. /mnt/c) hidden", "mnt_empty True" in out, out.strip()[:120])
    rc, out = run("b = bytearray(2*1024*1024*1024); print('ALLOCATED', len(b))")
    chk("runaway memory allocation is capped, not left to exhaust the host", rc != 0 and "ALLOCATED" not in out)
    print("sandbox_runner selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
