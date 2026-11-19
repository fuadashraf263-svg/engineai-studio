#!/usr/bin/env python3
"""
limits.py — resource limits and log rotation for the EngineAI servers.

Three things stand between a working local server and one that a stranger
can knock over. None of them need the server to be public to build or test:

  1. Body size cap    — refuse an oversized POST *before* it reaches RAM.
  2. Log rotation     — audit.log otherwise grows until the disk is full.
  3. Output quota     — out/ otherwise grows until the disk is full.

The first is the one that matters most and the one most often done wrong.
`await request.json()` reads the entire body into memory first, so checking
the size after that point is checking after the damage. This middleware
reads the stream in chunks and aborts the moment the running total is over
the cap, so a 2 GB upload costs 64 KB of RAM and a closed connection.

    python limits.py --selftest
"""
from __future__ import annotations
import os, re, sys, gzip, glob, shutil, time, json

# ------------------------------------------------------------------ config
# Generous for a question, small enough that a thousand of them is nothing.
MAX_BODY       = int(os.environ.get("ENGINEAI_MAX_BODY",  256 * 1024))      # 256 KB
MAX_UPLOAD     = int(os.environ.get("ENGINEAI_MAX_UPLOAD", 32 * 1024 * 1024))  # 32 MB
MAX_QUESTION   = int(os.environ.get("ENGINEAI_MAX_QUESTION", 4000))         # characters
MAX_LOG_BYTES  = int(os.environ.get("ENGINEAI_MAX_LOG", 16 * 1024 * 1024))  # 16 MB
KEEP_LOGS      = int(os.environ.get("ENGINEAI_KEEP_LOGS", 6))
MAX_OUT_BYTES  = int(os.environ.get("ENGINEAI_MAX_OUT", 4 * 1024 * 1024 * 1024))  # 4 GB

# Routes allowed the larger cap, because they legitimately receive files.
UPLOAD_ROUTES = ("/api/upload", "/api/vision", "/api/import")


# ------------------------------------------------------------------ 1. body
class BodyLimit:
    """Pure-ASGI middleware. Rejects an oversized body while it streams.

    Deliberately not a BaseHTTPMiddleware subclass: that class buffers the
    whole request, which is precisely the behaviour being defended against.
    """

    def __init__(self, app, max_body=MAX_BODY, max_upload=MAX_UPLOAD,
                 upload_routes=UPLOAD_ROUTES):
        self.app = app
        self.max_body = max_body
        self.max_upload = max_upload
        self.upload_routes = tuple(upload_routes)

    def cap_for(self, path: str) -> int:
        return self.max_upload if path.startswith(self.upload_routes) else self.max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        cap = self.cap_for(scope.get("path", ""))

        # Fast path: an honest client declares Content-Length, so we can
        # refuse before reading a single byte of the body.
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    if int(v) > cap:
                        return await self._reject(send, cap, int(v))
                except ValueError:
                    pass

        # Slow path: chunked, or a lying Content-Length. Count as we go.
        seen = 0
        over = False

        async def counted_receive():
            nonlocal seen, over
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > cap:
                    over = True
                    # Hand the app an empty final chunk; it will fail to parse
                    # and we have already stopped accumulating.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return msg

        sent_start = False

        async def guarded_send(msg):
            nonlocal sent_start
            if over and not sent_start:
                sent_start = True
                return await self._reject(send, cap, seen)
            if over:
                return
            if msg["type"] == "http.response.start":
                sent_start = True
            await send(msg)

        await self.app(scope, counted_receive, guarded_send)

    @staticmethod
    async def _reject(send, cap, got):
        body = json.dumps({
            "error": "request too large",
            "limit_bytes": cap,
            "received_bytes": got,
            "hint": f"maximum accepted body is {cap // 1024} KB",
        }).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


def clamp_question(text, limit=MAX_QUESTION):
    """Second line of defence, at the application layer.

    Returns (text, note). A truncated question is answered rather than
    refused — refusing a long question annoys an honest user, and the
    body cap has already stopped the abusive case.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text, None
    return (text[:limit],
            f"question truncated from {len(text):,} to {limit:,} characters")


# ------------------------------------------------------------------ 2. logs
def rotate(path, max_bytes=MAX_LOG_BYTES, keep=KEEP_LOGS):
    """Rotate `path` when it exceeds max_bytes. Old copies are gzipped.

    Returns a note if rotation happened, else None. Called on server start
    and after each write, which is cheap: os.path.getsize is a stat().
    """
    try:
        if not os.path.exists(path) or os.path.getsize(path) <= max_bytes:
            return None
    except OSError:
        return None

    stamp = time.strftime("%Y%m%d-%H%M%S")
    archive = f"{path}.{stamp}.gz"
    try:
        with open(path, "rb") as src, gzip.open(archive, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        open(path, "w").close()          # truncate, keep inode + permissions
    except OSError as e:
        return f"rotation failed: {e}"

    olds = sorted(glob.glob(f"{path}.*.gz"))
    removed = 0
    for old in olds[:-keep] if len(olds) > keep else []:
        try:
            os.remove(old); removed += 1
        except OSError:
            pass
    return (f"rotated {os.path.basename(path)} -> "
            f"{os.path.basename(archive)}"
            + (f", removed {removed} old" if removed else ""))


def install_rotation(*paths, max_bytes=MAX_LOG_BYTES, keep=KEEP_LOGS):
    """Rotate every named log once, now. Call at server start."""
    notes = []
    for p in paths:
        n = rotate(p, max_bytes, keep)
        if n:
            notes.append(n)
    return notes


# ------------------------------------------------------------------ 3. disk
def dir_bytes(d):
    t = 0
    for root, _, files in os.walk(d):
        for f in files:
            try:
                t += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return t


def prune_out(out_dir, max_bytes=MAX_OUT_BYTES, keep_recent=50):
    """Delete the oldest generated files when out/ exceeds its quota.

    A single gyroid STL is 21 MB and the 3D sandbox HTML that embeds it is
    28 MB. Fifty of those is 2.5 GB, so this fires in ordinary use, not
    only under attack.
    """
    if not os.path.isdir(out_dir):
        return None
    total = dir_bytes(out_dir)
    if total <= max_bytes:
        return None

    files = []
    for f in glob.glob(os.path.join(out_dir, "*")):
        if os.path.isfile(f) and not os.path.basename(f).startswith("."):
            try:
                files.append((os.path.getmtime(f), os.path.getsize(f), f))
            except OSError:
                pass
    files.sort()                                    # oldest first

    freed = n = 0
    for _, size, path in files[:-keep_recent] if len(files) > keep_recent else []:
        if total - freed <= max_bytes * 0.8:        # clear to 80%, not to the line
            break
        try:
            os.remove(path); freed += size; n += 1
        except OSError:
            pass
    if not n:
        return None
    return f"pruned {n} old file(s), freed {freed / 1e6:.0f} MB from {out_dir}"


# ------------------------------------------------------------------ selftest
def selftest():
    import asyncio, tempfile
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            ok = False

    # -- body limit -------------------------------------------------------
    async def dummy_app(scope, receive, send):
        n = 0
        while True:
            m = await receive()
            n += len(m.get("body", b""))
            if not m.get("more_body"):
                break
        body = json.dumps({"read": n}).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})

    async def call(mw, path, chunks, declare=None):
        scope = {"type": "http", "path": path, "method": "POST",
                 "headers": ([(b"content-length", str(declare).encode())]
                             if declare is not None else [])}
        queue = list(chunks)
        async def receive():
            if queue:
                b = queue.pop(0)
                return {"type": "http.request", "body": b, "more_body": bool(queue)}
            return {"type": "http.request", "body": b"", "more_body": False}
        out = {}
        async def send(msg):
            if msg["type"] == "http.response.start":
                out["status"] = msg["status"]
            else:
                out["body"] = out.get("body", b"") + msg.get("body", b"")
        await mw(scope, receive, send)
        return out

    mw = BodyLimit(dummy_app, max_body=1000, max_upload=5000)

    r = asyncio.get_event_loop().run_until_complete(
        call(mw, "/api/ask", [b"x" * 500], declare=500))
    check(r["status"] == 200, f"small body accepted (status {r['status']})")

    r = asyncio.get_event_loop().run_until_complete(
        call(mw, "/api/ask", [b"x" * 9000], declare=9000))
    check(r["status"] == 413, f"declared oversize refused before read (status {r['status']})")

    # the important case: a lying Content-Length
    r = asyncio.get_event_loop().run_until_complete(
        call(mw, "/api/ask", [b"x" * 400] * 10, declare=10))
    check(r["status"] == 413, f"undeclared oversize refused mid-stream (status {r['status']})")

    r = asyncio.get_event_loop().run_until_complete(
        call(mw, "/api/upload", [b"x" * 3000], declare=3000))
    check(r["status"] == 200, f"upload route gets the larger cap (status {r['status']})")

    r = asyncio.get_event_loop().run_until_complete(
        call(mw, "/api/upload", [b"x" * 9000], declare=9000))
    check(r["status"] == 413, "upload route still capped")

    check(mw.cap_for("/api/ask") == 1000 and mw.cap_for("/api/upload") == 5000,
          "per-route caps resolve correctly")

    # -- question clamp ---------------------------------------------------
    t, note = clamp_question("beam deflection?", 100)
    check(note is None and t == "beam deflection?", "short question untouched")
    t, note = clamp_question("y" * 5000, 100)
    check(len(t) == 100 and note and "truncated" in note, "long question truncated with a note")

    # -- rotation ---------------------------------------------------------
    with tempfile.TemporaryDirectory() as d:
        log = os.path.join(d, "audit.log")
        with open(log, "w") as f:
            f.write("line\n" * 50)
        check(rotate(log, max_bytes=10_000) is None, "small log not rotated")

        with open(log, "w") as f:
            f.write("x" * 50_000)
        note = rotate(log, max_bytes=10_000, keep=3)
        gzs = glob.glob(log + ".*.gz")
        check(note and len(gzs) == 1, f"oversize log rotated ({note})")
        check(os.path.getsize(log) == 0, "live log truncated to zero")
        with gzip.open(gzs[0], "rb") as f:
            check(len(f.read()) == 50_000, "archived content intact and complete")

        # keep=2, make 4 archives, expect 2 survivors
        for i in range(4):
            with open(log, "w") as f:
                f.write("z" * 50_000)
            rotate(log, max_bytes=10_000, keep=2)
            time.sleep(1.05)     # stamps are per-second
        check(len(glob.glob(log + ".*.gz")) <= 2,
              f"keep=2 honoured ({len(glob.glob(log + '.*.gz'))} archives)")

    # -- out pruning ------------------------------------------------------
    with tempfile.TemporaryDirectory() as d:
        for i in range(20):
            with open(os.path.join(d, f"f{i:02d}.bin"), "wb") as f:
                f.write(b"0" * 10_000)
            os.utime(os.path.join(d, f"f{i:02d}.bin"), (1_000_000 + i, 1_000_000 + i))
        check(prune_out(d, max_bytes=10_000_000) is None, "under quota, nothing pruned")
        note = prune_out(d, max_bytes=100_000, keep_recent=5)
        left = sorted(os.listdir(d))
        check(note is not None, f"over quota, pruned ({note})")
        check("f19.bin" in left, "newest file survived")
        check("f00.bin" not in left, "oldest file removed")
        check(dir_bytes(d) <= 100_000, f"pruned to under quota ({dir_bytes(d)} bytes)")

    print("\nlimits selftest:", "PASSED" if ok else "FAILED")
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    print(__doc__)
    print(f"  MAX_BODY      {MAX_BODY:>12,} bytes")
    print(f"  MAX_UPLOAD    {MAX_UPLOAD:>12,} bytes")
    print(f"  MAX_QUESTION  {MAX_QUESTION:>12,} characters")
    print(f"  MAX_LOG       {MAX_LOG_BYTES:>12,} bytes  (keep {KEEP_LOGS})")
    print(f"  MAX_OUT       {MAX_OUT_BYTES:>12,} bytes")
