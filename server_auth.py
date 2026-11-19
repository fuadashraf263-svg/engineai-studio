#!/usr/bin/env python3
"""server_auth.py - authentication for the three servers that had none.

server.py (8095), chat_server.py (8096) and workspace_server.py (8097) accepted
any request that reached them. On 127.0.0.1 that is merely unwise; with --lan it
means anyone on the network can drive the engine, read the workspace and write
files. This module closes that, and it is applied to all three by one line each.

DECISIONS, AND WHY

  AUTH IS ALWAYS ON. There is no "trusted localhost" bypass by default. A
  bypass that depends on the bind address fails the moment someone adds --lan
  for a demo and forgets, and that is exactly how this kind of hole gets
  shipped. Set ENGINEAI_ALLOW_LOCAL=1 to opt out for local development; it
  prints a warning every time so it cannot become invisible.

  THE TOKEN IS GENERATED, NOT CHOSEN. 32 bytes from secrets.token_urlsafe on
  first run, stored at ~/.engineai/token with 0600. Nobody picks a memorable
  password and nobody commits one. If ENGINEAI_TOKEN is set it wins, so
  containers and systemd units can inject it.

  COMPARISON IS CONSTANT TIME. secrets.compare_digest, because == on a token
  leaks its prefix through timing, and this is one line either way.

  THE TOKEN IS NEVER LOGGED. Not at startup, not in errors, not in access logs.
  Startup prints the URL with the token once, to the terminal only, because
  the operator needs it to connect - after that it is masked everywhere.

  FAILURES ARE RATE LIMITED. Twenty bad attempts from one address in five
  minutes and that address is refused for the rest of the window. Enough to
  make online guessing of a 256-bit token pointless without locking out a user
  who fat-fingers a URL twice.

WHAT THIS IS NOT. It is single-token shared-secret auth, appropriate for a
personal engineering tool on a LAN. It is not multi-user, has no roles, and no
account recovery. Do not put it on the public internet: put it behind a reverse
proxy that terminates TLS and does real authentication.
"""
from __future__ import annotations

import hmac
import os
import secrets
import stat
import subprocess
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

__all__ = ["get_token", "rotate_token", "install", "check_token",
           "RateLimiter", "ensure_tls_cert", "TOKEN_FILE"]

TOKEN_FILE = Path.home() / ".engineai" / "token"
CERT_DIR = Path.home() / ".engineai" / "tls"

# Paths reachable without a token. Deliberately tiny: a health check that says
# "alive" and nothing else, so a monitor can poll without holding a credential.
PUBLIC_PATHS = frozenset({"/healthz", "/favicon.ico"})

MAX_FAILURES = 20
WINDOW_SECONDS = 300


# --------------------------------------------------------------------------
# token
# --------------------------------------------------------------------------

def get_token(create: bool = True) -> str:
    """Environment first, then the token file, then generate one."""
    env = os.environ.get("ENGINEAI_TOKEN", "").strip()
    if env:
        return env
    try:
        if TOKEN_FILE.exists():
            mode = TOKEN_FILE.stat().st_mode
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                print(f"WARNING: {TOKEN_FILE} is readable by others. "
                      f"chmod 600 it.", file=sys.stderr)
            t = TOKEN_FILE.read_text().strip()
            if t:
                return t
    except OSError:
        pass
    if not create:
        return ""
    return rotate_token()


def rotate_token() -> str:
    """Make a new token and store it. Any existing session is invalidated."""
    t = secrets.token_urlsafe(32)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(t)
    TOKEN_FILE.chmod(0o600)
    return t


def mask(t: str) -> str:
    return f"{t[:6]}...{t[-4:]}" if len(t) > 12 else "(short token)"


def check_token(supplied: str, expected: str) -> bool:
    """Constant-time comparison. Never use == on a secret."""
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


# --------------------------------------------------------------------------
# rate limiting
# --------------------------------------------------------------------------

class RateLimiter:
    """Sliding window of failures per client address."""

    def __init__(self, max_failures: int = MAX_FAILURES,
                 window: int = WINDOW_SECONDS):
        self.max_failures = max_failures
        self.window = window
        self._fails: dict[str, deque] = defaultdict(deque)

    def record_failure(self, addr: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        q = self._fails[addr]
        q.append(now)
        self._prune(addr, now)

    def _prune(self, addr: str, now: float) -> None:
        q = self._fails[addr]
        while q and now - q[0] > self.window:
            q.popleft()

    def blocked(self, addr: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        self._prune(addr, now)
        return len(self._fails[addr]) >= self.max_failures

    def reset(self, addr: str) -> None:
        self._fails.pop(addr, None)


# --------------------------------------------------------------------------
# FastAPI wiring
# --------------------------------------------------------------------------

COOKIE_NAME = "engineai_token"


def extract_token(headers, query_params, cookies=None) -> str:
    """Bearer header, X-EngineAI-Token, ?token=, or the session cookie.

    The query parameter exists because a person opening a dashboard in a
    browser cannot set a header. It is the weakest of the three - URLs end up
    in history and referrers - so it is accepted but the page is expected to
    move it into storage and drop it from the address bar.

    The COOKIE exists because dropping it from the address bar meant that
    typing the bare host afterwards - the obvious thing to do - came back as a
    raw 401 JSON blob. The cookie is set once, from a query token that already
    validated, and is HttpOnly and SameSite=Strict: JavaScript cannot read it
    and another site cannot make the browser send it.
    """
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    h = headers.get("x-engineai-token", "")
    if h:
        return h.strip()
    try:
        q = (query_params.get("token") or "").strip()
        if q:
            return q
    except Exception:
        pass
    try:
        return (cookies.get(COOKIE_NAME) or "").strip() if cookies else ""
    except Exception:
        return ""


def _set_session_cookie(request, response, token: str) -> None:
    """Turn a VALID ?token= into a session cookie, so the user can afterwards
    just type the host.

    HttpOnly: script cannot read it, so an XSS in a rendered artifact cannot
    exfiltrate it. SameSite=Strict: another origin cannot make the browser send
    it, which matters a great deal here because these servers execute code on
    request. The cookie is only ever set from a token that already matched -
    this never hands a credential to someone who did not have one.
    """
    try:
        supplied = (request.query_params.get("token") or "").strip()
        if supplied and check_token(supplied, token):
            response.set_cookie(COOKIE_NAME, token, httponly=True,
                                samesite="strict", path="/")
    except Exception:
        pass


def install(app, service: str = "EngineAI", allow_local: bool | None = None,
            public_paths=None):
    """Attach auth to a FastAPI app. One line per server.

    `public_paths` adds routes served without a token. It exists for one
    specific case: an HTML SHELL that contains no data. Gating the shell means
    a browser at the bare address gets a raw JSON 401 and the page never loads
    far enough to say what to do about it - which is exactly what happened
    here, twice. The shell is public; every /api/ route behind it is not.
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    public = frozenset(PUBLIC_PATHS | set(public_paths or ()))
    token = get_token()
    limiter = RateLimiter()
    if allow_local is None:
        allow_local = os.environ.get("ENGINEAI_ALLOW_LOCAL", "") == "1"
    if allow_local:
        print("WARNING: ENGINEAI_ALLOW_LOCAL=1 - requests from 127.0.0.1 skip "
              "authentication. Never set this on a machine others can reach.",
              file=sys.stderr)

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        path = request.url.path
        if path in public:
            # A public path still honours a valid ?token=. Without this the
            # shell at "/" - the one page a person actually arrives at with a
            # token in the URL - returned before the cookie was ever set, so
            # signing in appeared to work and then the API still refused.
            response = await call_next(request)
            _set_session_cookie(request, response, token)
            return response

        client = request.client.host if request.client else "unknown"
        if allow_local and client in ("127.0.0.1", "::1", "localhost"):
            return await call_next(request)

        if limiter.blocked(client):
            return JSONResponse(
                {"error": "too many failed attempts",
                 "retry_after_seconds": WINDOW_SECONDS},
                status_code=429, headers={"Retry-After": str(WINDOW_SECONDS)})

        supplied = extract_token(request.headers, request.query_params,
                                 request.cookies)
        if not check_token(supplied, token):
            limiter.record_failure(client)
            return JSONResponse(
                {"error": "unauthorised",
                 "how": "send 'Authorization: Bearer <token>', header "
                        "'X-EngineAI-Token: <token>', or ?token=<token>",
                 "token_location": str(TOKEN_FILE)},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer realm=\"EngineAI\""})

        limiter.reset(client)
        response = await call_next(request)
        _set_session_cookie(request, response, token)
        # Cheap, always-correct hardening for a tool that renders its own HTML.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    async def _healthz():
        return {"ok": True, "service": service}

    return token


def startup_banner(service: str, host: str, port: int, token: str,
                   tls: bool = False) -> str:
    scheme = "https" if tls else "http"
    shown = "localhost" if host == "127.0.0.1" else "<this-machine-ip>"
    lines = [
        f"{service} -> {scheme}://{shown}:{port}/?token={token}",
        f"  token stored at {TOKEN_FILE} (chmod 600); rotate with "
        f"`python server_auth.py --rotate`",
    ]
    if host == "0.0.0.0" and not tls:
        lines.append("  WARNING: bound to all interfaces WITHOUT TLS. The token "
                     "crosses the network in clear text.")
        lines.append("  Run `python server_auth.py --make-cert` and pass --tls, "
                     "or keep it on 127.0.0.1.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# TLS
# --------------------------------------------------------------------------

def ensure_tls_cert(days: int = 825) -> tuple:
    """Self-signed certificate for local and LAN use.

    A self-signed certificate encrypts the connection but proves nothing about
    identity - browsers will warn, and they are right to. It stops the token
    and the traffic crossing a LAN in clear text, which is the actual risk
    here. For anything public, terminate TLS at a reverse proxy with a real
    certificate.
    """
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    cert, key = CERT_DIR / "engineai.crt", CERT_DIR / "engineai.key"
    if cert.exists() and key.exists():
        return cert, key
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", str(days),
         "-subj", "/CN=engineai.local",
         "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        check=True, capture_output=True)
    key.chmod(0o600)
    return cert, key


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

    print("== token handling ==")
    t = secrets.token_urlsafe(32)
    chk(check_token(t, t), "the right token is accepted")
    chk(not check_token(t + "x", t), "a token with an extra character is refused")
    chk(not check_token(t[:-1], t), "a truncated token is refused")
    chk(not check_token("", t), "an empty token is refused")
    chk(not check_token(t, ""), "nothing matches an empty expected token")
    chk(len(secrets.token_urlsafe(32)) >= 40, "generated tokens are long")
    chk(mask(t) != t and t[:6] in mask(t), f"masking hides the token ({mask(t)})")

    print("== token extraction ==")
    class Q(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)
    chk(extract_token({"authorization": f"Bearer {t}"}, Q()) == t, "bearer header")
    chk(extract_token({"authorization": f"bearer {t}"}, Q()) == t, "lowercase bearer")
    chk(extract_token({"x-engineai-token": t}, Q()) == t, "custom header")
    chk(extract_token({}, Q(token=t)) == t, "query parameter")
    chk(extract_token({}, Q()) == "", "nothing supplied yields empty")
    chk(extract_token({"authorization": f"Basic {t}"}, Q()) == "",
        "a Basic header is not mistaken for a bearer token")

    print("== rate limiting ==")
    rl = RateLimiter(max_failures=3, window=60)
    now = 1000.0
    chk(not rl.blocked("1.2.3.4", now), "a fresh address is not blocked")
    for _ in range(3):
        rl.record_failure("1.2.3.4", now)
    chk(rl.blocked("1.2.3.4", now), "three failures blocks the address")
    chk(not rl.blocked("5.6.7.8", now), "and only that address")
    chk(not rl.blocked("1.2.3.4", now + 61), "the window expires")
    rl.record_failure("9.9.9.9", now)
    rl.reset("9.9.9.9")
    chk(not rl.blocked("9.9.9.9", now), "a success resets the counter")

    print("== the middleware actually refuses ==")
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        os.environ["ENGINEAI_TOKEN"] = t
        app = FastAPI()

        @app.get("/secret")
        def secret():
            return {"data": "sensitive"}

        install(app, "test", allow_local=False)
        c = TestClient(app)

        chk(c.get("/secret").status_code == 401, "no token -> 401")
        chk(c.get("/secret?token=wrong").status_code == 401, "wrong token -> 401")
        r_ok = c.get("/secret", headers={"Authorization": f"Bearer {t}"})
        chk(r_ok.status_code == 200, "correct bearer token -> 200")
        chk(r_ok.json()["data"] == "sensitive", "and the handler runs")
        chk(c.get(f"/secret?token={t}").status_code == 200, "query token works")
        chk(c.get("/healthz").status_code == 200, "health check needs no token")
        chk("ok" in c.get("/healthz").json(), "and reports liveness only")

        # A FRESH client. `c` has been signed in with ?token= by the line
        # above, so it now carries the session cookie and is legitimately
        # authorised - reusing it here tested the cookie, not the 401 body.
        c_anon = TestClient(app)
        body = c_anon.get("/secret").text
        chk(t not in body, "the token is NEVER echoed in an error response")
        chk("token_location" in c_anon.get("/secret").json(),
            "but the error says where to find it")

        print("== the session cookie ==")
        c_ck = TestClient(app)
        first = c_ck.get(f"/secret?token={t}")
        sc = str(first.headers.get("set-cookie", ""))
        chk(COOKIE_NAME in sc, "a valid ?token= sets a session cookie")
        chk("HttpOnly" in sc, "the cookie is HttpOnly")
        chk("strict" in sc.lower(), "the cookie is SameSite=Strict")
        chk(c_ck.get("/secret").status_code == 200,
            "the next request needs no token in the URL")
        chk(t not in c_ck.get("/secret").text,
            "and the token is still not echoed anywhere")
        c_bad = TestClient(app)
        bad = c_bad.get("/secret?token=wrong")
        chk(COOKIE_NAME not in str(bad.headers.get("set-cookie", "")),
            "a WRONG token sets no cookie - this cannot mint a credential")
        chk(extract_token({}, Q(), {COOKIE_NAME: t}) == t,
            "extract_token reads the cookie")
        chk(extract_token({}, Q(token=t), {COOKIE_NAME: "stale"}) == t,
            "an explicit query token beats a stale cookie")
        chk(r_ok.headers.get("X-Content-Type-Options") == "nosniff",
            "security headers are set on success")
        chk(r_ok.headers.get("X-Frame-Options") == "DENY", "framing is denied")

        # exhaust the limiter
        app2 = FastAPI()

        @app2.get("/x")
        def x():
            return {}
        install(app2, "t2", allow_local=False)
        c2 = TestClient(app2)
        codes = [c2.get("/x?token=bad").status_code for _ in range(MAX_FAILURES + 2)]
        chk(codes[-1] == 429, f"repeated failures end in 429 (got {codes[-1]})")
        chk(codes[0] == 401, "the first failure is a plain 401")
        blocked = c2.get("/x", headers={"Authorization": f"Bearer {t}"})
        chk(blocked.status_code == 429,
            "and a blocked address stays blocked even with the right token")
        os.environ.pop("ENGINEAI_TOKEN", None)
    except ImportError as e:
        print(f"  (fastapi testclient unavailable: {e} - middleware untested)")

    print("== local bypass is off unless asked for ==")
    os.environ.pop("ENGINEAI_ALLOW_LOCAL", None)
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        os.environ["ENGINEAI_TOKEN"] = t
        app3 = FastAPI()

        @app3.get("/y")
        def y():
            return {}
        install(app3, "t3")            # allow_local resolved from env, unset
        chk(TestClient(app3).get("/y").status_code == 401,
            "a request from localhost is STILL refused by default")
        os.environ.pop("ENGINEAI_TOKEN", None)
    except ImportError:
        pass

    print("== the LAN warning fires ==")
    b = startup_banner("X", "0.0.0.0", 8095, t, tls=False)
    chk("WARNING" in b and "clear text" in b,
        "binding to all interfaces without TLS warns loudly")
    chk(t in b, "the banner does show the token once, to the terminal")
    b2 = startup_banner("X", "127.0.0.1", 8095, t)
    chk("WARNING" not in b2, "localhost does not warn")

    print(f"\nserver_auth.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    if "--rotate" in sys.argv:
        t = rotate_token()
        print(f"new token written to {TOKEN_FILE}\n  {t}")
        print("every existing session is now invalid")
    elif "--make-cert" in sys.argv:
        c, k = ensure_tls_cert()
        print(f"certificate {c}\nkey         {k}")
        print("self-signed: browsers will warn, and they are right to. It "
              "encrypts the link; it does not prove identity.")
    elif "--show" in sys.argv:
        print(f"token file {TOKEN_FILE}")
        print(f"token      {mask(get_token(create=False)) or '(none yet)'}")
    else:
        print(__doc__)
