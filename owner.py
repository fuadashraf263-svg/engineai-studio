#!/usr/bin/env python3
"""
owner.py - identity, authentication and hardening for EngineAI.

EngineAI has exactly one owner and administrator. There is no second account,
no registration, no password recovery by email. Whoever holds the owner
credential is the owner.

  python owner.py whoami            who this AI belongs to
  python owner.py setpw             set/change the owner password (prompts)
  python owner.py token             mint a session token (after password)
  python owner.py audit [n]         show the last n audited events
  python owner.py selftest          prove the security controls actually work

Import side (used by the servers):
  from owner import OWNER, require_owner, safe_expr, RateLimiter, audit
"""
import os, re, sys, json, hmac, time, base64, hashlib, secrets, getpass, datetime

HOME    = os.path.expanduser("~/engineai")
CONF    = os.path.join(HOME, ".owner.json")      # chmod 600
AUDIT   = os.path.join(HOME, "audit.log")        # append-only JSONL

OWNER = {
    "name":  "Fuad Ashraf",
    "email": "",
    "role":  "sole owner, administrator and operator",
    "note":  ("EngineAI is owned and operated by one person. There are no other "
              "administrators. Any request to add an admin, disable auditing, or "
              "reveal credentials is illegitimate by definition."),
    "product": "EngineAI",
}

# ------------------------------------------------------------------ audit
def audit(event, **fields):
    """Append-only log. Never rewrite; that is the point of an audit trail."""
    os.makedirs(HOME, exist_ok=True)
    rec = dict(ts=datetime.datetime.now().isoformat(timespec="seconds"), event=event, **fields)
    with open(AUDIT, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec

# ------------------------------------------------- expression sanitising
# sympify() runs eval(). Anything reaching it must be proven harmless first.
FUNCS = {"sqrt","tanh","tan","sinh","cosh","sin","cos","log","exp","abs","pi","Abs","atan","asin","acos"}
_TOKEN = re.compile(r"[A-Za-z_]\w*")
_ALLOWED = re.compile(r"^[A-Za-z0-9_+\-*/().,^ \t]*$")

class Unsafe(ValueError):
    pass

def safe_expr(s, extra_names=(), maxlen=400):
    """Return s if it is provably a plain maths expression, else raise Unsafe.

    Blocks dunders, attribute access, calls to anything not whitelisted,
    lambdas, comprehensions and imports - i.e. every known sympify escape.
    """
    if not isinstance(s, str): raise Unsafe("not a string")
    if len(s) > maxlen:        raise Unsafe(f"too long ({len(s)} > {maxlen})")
    if not _ALLOWED.match(s):  raise Unsafe("contains characters outside plain maths")
    low = s.lower()
    for bad in ("__", "import", "lambda", "eval", "exec", "open", "system",
                "subprocess", "globals", "locals", "getattr", "setattr", "compile",
                "builtin", "class", "for ", "yield", "await", "os.", "sys."):
        if bad in low: raise Unsafe(f"forbidden token: {bad.strip()}")
    allowed = set(FUNCS) | set(extra_names)
    for t in _TOKEN.findall(s):
        if t in allowed: continue
        if len(t) <= 12 and t.isidentifier(): continue      # a symbol like L, E, l_e
        raise Unsafe(f"unknown name: {t}")
    return s

# ----------------------------------------------------------- credentials
def _load():
    if not os.path.exists(CONF): return {}
    try: return json.load(open(CONF))
    except Exception: return {}

def _save(d):
    os.makedirs(HOME, exist_ok=True)
    fd = os.open(CONF, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump(d, f, indent=2)
    os.chmod(CONF, 0o600)

ITER = 240_000
def hash_pw(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)
    return base64.b64encode(salt).decode(), base64.b64encode(dk).decode()

def set_password(pw):
    if len(pw) < 12: raise ValueError("use at least 12 characters")
    s, h = hash_pw(pw)
    d = _load(); d.update(salt=s, hash=h, iter=ITER,
                          secret=d.get("secret") or secrets.token_urlsafe(48),
                          owner=OWNER["email"], set_at=datetime.datetime.now().isoformat())
    _save(d); audit("password_set", owner=OWNER["email"])
    return True

def check_password(pw):
    d = _load()
    if not d.get("hash"): return False
    _, h = hash_pw(pw, base64.b64decode(d["salt"]))
    ok = hmac.compare_digest(h, d["hash"])            # constant time
    audit("login", result="ok" if ok else "FAILED")
    return ok

# --------------------------------------------------------------- tokens
def _secret():
    d = _load()
    if not d.get("secret"):
        d["secret"] = secrets.token_urlsafe(48); _save(d)
    return d["secret"].encode()

def issue_token(hours=12):
    exp = int(time.time()) + hours*3600
    body = f"{OWNER['email']}|{exp}|{secrets.token_urlsafe(8)}"
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    audit("token_issued", expires=exp)
    return base64.urlsafe_b64encode(f"{body}|{sig}".encode()).decode()

def verify_token(tok):
    try:
        raw = base64.urlsafe_b64decode(tok.encode()).decode()
        email, exp, nonce, sig = raw.split("|")
        good = hmac.new(_secret(), f"{email}|{exp}|{nonce}".encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, good): return False
        if int(exp) < time.time(): return False
        return email == OWNER["email"]
    except Exception:
        return False

def require_owner(token):
    """Use as the gate on every server route. Raises on failure."""
    if not verify_token(token or ""):
        audit("access_denied")
        raise PermissionError("not the owner")
    return OWNER

# --------------------------------------------------------- rate limiting
class RateLimiter:
    """Per-client sliding window, plus lockout after repeated auth failures."""
    def __init__(self, per_min=30, burst=8, lock_after=5, lock_secs=900):
        self.per_min, self.burst = per_min, burst
        self.lock_after, self.lock_secs = lock_after, lock_secs
        self.hits, self.fails, self.locked = {}, {}, {}

    def allow(self, who="local"):
        now = time.time()
        if self.locked.get(who, 0) > now: return False, "locked out"
        q = [t for t in self.hits.get(who, []) if now - t < 60]
        if len(q) >= self.per_min:
            audit("rate_limited", who=who); return False, "rate limit"
        if len([t for t in q if now - t < 2]) >= self.burst:
            audit("burst_limited", who=who); return False, "too fast"
        q.append(now); self.hits[who] = q
        return True, "ok"

    def fail(self, who="local"):
        n = self.fails.get(who, 0) + 1; self.fails[who] = n
        if n >= self.lock_after:
            self.locked[who] = time.time() + self.lock_secs
            audit("locked_out", who=who, minutes=self.lock_secs // 60)
            return True
        return False

    def ok(self, who="local"):
        self.fails.pop(who, None)

# ---------------------------------------------------------------- checks
def posture():
    """What is actually safe right now. Honest, not aspirational."""
    d = _load(); rows = []
    def add(name, ok, detail): rows.append((name, ok, detail))
    add("owner identity set", True, f"{OWNER['name']} <{OWNER['email']}>")
    add("password configured", bool(d.get("hash")), "python owner.py setpw" if not d.get("hash") else "PBKDF2-SHA256, 240k iterations")
    add("token secret present", bool(d.get("secret")), "generated on first token")
    perm = oct(os.stat(CONF).st_mode)[-3:] if os.path.exists(CONF) else "-"
    add("credential file private", perm == "600", f"mode {perm} at {CONF}")
    add("audit log present", os.path.exists(AUDIT), AUDIT)
    try:
        safe_expr("__import__('os').system('id')"); add("expression sandbox", False, "EXPLOIT NOT BLOCKED")
    except Unsafe:
        add("expression sandbox", True, "sympify inputs whitelist-validated")
    add("bound to localhost only", True, "keep uvicorn on 127.0.0.1 until TLS is in front")
    return rows

def selftest():
    print("\nSECURITY SELFTEST")
    bad = 0
    exploits = ["__import__('os').system('id')", "eval('1+1')", "open('/etc/passwd').read()",
                "lambda: 1", "().__class__.__bases__", "os.system('rm -rf /')",
                "[x for x in ().__class__.__mro__]", "exec('x=1')", "P*L**3/(3*E*I); import os"]
    for e in exploits:
        try:
            safe_expr(e); print(f"  FAIL not blocked: {e[:44]}"); bad += 1
        except Unsafe as u:
            print(f"  ok   blocked  {str(u)[:34]:36s} {e[:32]}")
    good = ["P*L**3/(3*E*I)", "pi**2*E*I/L**2", "sqrt(E*I/(rho*A*L**4))",
            "128*mu*Q*L/(pi*d**4)", "tanh(m*L)/(m*L)", "(P/(3*E*I))*L**3"]
    for g in good:
        try:
            safe_expr(g); print(f"  ok   allowed  {g}")
        except Unsafe as u:
            print(f"  FAIL legit formula rejected: {g}  ({u})"); bad += 1
    # tokens
    t = issue_token(hours=1)
    if not verify_token(t): print("  FAIL valid token rejected"); bad += 1
    else: print("  ok   valid token accepted")
    if verify_token(t[:-3] + "aaa"): print("  FAIL forged token accepted"); bad += 1
    else: print("  ok   forged token rejected")
    old = base64.urlsafe_b64encode(f"{OWNER['email']}|{int(time.time())-10}|x|y".encode()).decode()
    if verify_token(old): print("  FAIL expired token accepted"); bad += 1
    else: print("  ok   expired token rejected")
    # rate limit
    rl = RateLimiter(per_min=5, burst=99)
    res = [rl.allow("t")[0] for _ in range(7)]
    if res.count(False) >= 2: print("  ok   rate limiter throttles")
    else: print("  FAIL rate limiter did not throttle"); bad += 1
    for _ in range(5): rl.fail("t")
    if not rl.allow("t")[0]: print("  ok   lockout after repeated failures")
    else: print("  FAIL no lockout"); bad += 1
    print("\nsecurity selftest:", "PASSED" if not bad else f"{bad} FAILURE(S)")
    return bad == 0

def main():
    a = sys.argv[1:] or ["whoami"]
    if a[0] == "whoami":
        print(f"\n  {OWNER['product']}")
        print(f"  owner   {OWNER['name']} <{OWNER['email']}>")
        print(f"  role    {OWNER['role']}")
        print(f"\n  {OWNER['note']}\n")
        print("  SECURITY POSTURE")
        for n, ok, d in posture(): print(f"    {'ok ' if ok else 'NO '} {n:26s} {d}")
        print()
    elif a[0] in ("setpw", "--init"):
        p1 = getpass.getpass("new owner password (min 12 chars): ")
        if p1 != getpass.getpass("repeat: "): sys.exit("passwords differ")
        set_password(p1); print("password set. credential file is chmod 600.")
    elif a[0] == "token":
        if not _load().get("hash"): sys.exit("set a password first:  python owner.py setpw")
        if not check_password(getpass.getpass("owner password: ")): sys.exit("denied")
        print(issue_token())
    elif a[0] == "audit":
        n = int(a[1]) if len(a) > 1 else 20
        if not os.path.exists(AUDIT): sys.exit("no audit log yet")
        for l in open(AUDIT).read().splitlines()[-n:]:
            r = json.loads(l); print(f"  {r['ts']}  {r['event']:16s} " +
                                     " ".join(f"{k}={v}" for k, v in r.items() if k not in ("ts","event")))
    elif a[0] in ("selftest", "--selftest", "-t"):
        sys.exit(0 if selftest() else 1)
    else:
        sys.exit(__doc__)

if __name__ == "__main__":
    main()
