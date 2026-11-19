#!/usr/bin/env python3
"""
providers.py - plug any hosted model into EngineAI. Free Chinese tiers included.

Almost every provider now speaks the OpenAI chat format, so ONE implementation
covers all of them. What matters for this agent is JSON mode: without it a model
replies with prose and the loop stalls. Each entry below records whether the
provider supports it and how.

You supply your own key - I cannot give you one. Register, export the variable,
done:

    export OPENROUTER_API_KEY=sk-or-...
    export ENGINEAI_PROVIDER=openrouter
    python studio.py

  python providers.py list          what is configured and what is missing
  python providers.py test          send one real request to the active provider
  python providers.py --selftest    verify the plumbing without any key
"""
import os, sys, json, argparse, urllib.request, urllib.error


def load_local_env() -> int:
    """Load EngineAI's owner-only key file without echoing any secret.

    keypool.py already does this, but providers.py is also a supported direct
    CLI.  Keeping this tiny loader here makes `python providers.py models` and
    `python providers.py test` use the same local configuration.
    """
    path = os.path.expanduser(os.environ.get("ENGINEAI_ENV", "~/engineai/.env"))
    loaded = 0
    try:
        for raw in open(path, encoding="utf-8"):
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ")
            value = value.strip().strip('"').strip("'")
            if key and value and not os.environ.get(key):
                os.environ[key] = value
                loaded += 1
    except FileNotFoundError:
        pass
    return loaded


load_local_env()

# Model names move. Override any of them with ENGINEAI_API_MODEL.
PROVIDERS = {
 "openrouter": dict(
     base="https://openrouter.ai/api/v1", env="OPENROUTER_API_KEY",
     model="openrouter/free", json_mode="response_format",
     free="official free-model router; model availability and quota can change",
     signup="openrouter.ai — one key, many models, easiest place to start"),
 "deepseek": dict(
     base="https://api.deepseek.com/v1", env="DEEPSEEK_API_KEY",
     model="deepseek-chat", json_mode="response_format",
     free="trial credit on signup, then very low cost",
     signup="platform.deepseek.com"),
 "zhipu": dict(
     base="https://open.bigmodel.cn/api/paas/v4", env="ZHIPU_API_KEY",
     model="glm-4-flash", json_mode="response_format",
     free="glm-4-flash is free",
     signup="open.bigmodel.cn (GLM / Zhipu AI)"),
 "siliconflow": dict(
     base="https://api.siliconflow.cn/v1", env="SILICONFLOW_API_KEY",
     model="Qwen/Qwen2.5-7B-Instruct", json_mode="response_format",
     free="a set of smaller models is free",
     signup="siliconflow.cn"),
 "dashscope": dict(
     base="https://dashscope.aliyuncs.com/compatible-mode/v1", env="DASHSCOPE_API_KEY",
     model="qwen-plus", json_mode="response_format",
     free="free quota on new accounts",
     signup="Alibaba Cloud DashScope (Qwen)"),
 "moonshot": dict(
     base="https://api.moonshot.cn/v1", env="MOONSHOT_API_KEY",
     model="moonshot-v1-8k", json_mode="response_format",
     free="trial credit", signup="platform.moonshot.cn (Kimi)"),
 "groq": dict(
     base="https://api.groq.com/openai/v1", env="GROQ_API_KEY",
     model="llama-3.3-70b-versatile", json_mode="response_format",
     free="free tier with a daily cap", signup="console.groq.com"),
 "cerebras": dict(
     base="https://api.cerebras.ai/v1", env="CEREBRAS_API_KEY",
     model="llama3.1-8b", json_mode="response_format",
     free="free tier with a daily cap", signup="cloud.cerebras.ai"),
 "together": dict(
     base="https://api.together.xyz/v1", env="TOGETHER_API_KEY",
     model="meta-llama/Llama-3.3-70B-Instruct-Turbo-Free", json_mode="response_format",
     free="some models marked Free", signup="api.together.ai"),
 "google": dict(
     base="https://generativelanguage.googleapis.com/v1beta/openai", env="GEMINI_API_KEY",
     model="gemini-3.5-flash", json_mode="response_format",
     free="generous free tier", signup="aistudio.google.com"),
 "mistral": dict(
     base="https://api.mistral.ai/v1", env="MISTRAL_API_KEY",
     model="mistral-small-latest", json_mode="response_format",
     free="free tier", signup="console.mistral.ai"),
 "openai": dict(
     base="https://api.openai.com/v1", env="OPENAI_API_KEY",
     model="gpt-4o-mini", json_mode="response_format",
     free=None, signup="platform.openai.com"),
 "anthropic": dict(
     base="https://api.anthropic.com/v1", env="ANTHROPIC_API_KEY",
     model="claude-sonnet-4-20250514", json_mode="prompt",
     free=None, signup="console.anthropic.com"),
}

def configured():
    return [n for n, p in PROVIDERS.items() if os.environ.get(p["env"])]

def active():
    want = os.environ.get("ENGINEAI_PROVIDER")
    if want and want in PROVIDERS and os.environ.get(PROVIDERS[want]["env"]): return want
    c = configured()
    return c[0] if c else None

class ProviderError(RuntimeError):
    def __init__(self, code, detail, hint=""):
        self.code, self.detail, self.hint = code, detail, hint
        super().__init__(f"HTTP {code}: {detail}" + (f"\n  -> {hint}" if hint else ""))

HINTS = {
 400: "the request was rejected - often an unsupported option for this model",
 401: "the API key is wrong or not set for this provider",
 402: "no credit on the account - pick a model whose id ends in :free",
 403: "the key is not allowed to use this model",
 404: "THE MODEL NAME DOES NOT EXIST on this provider. Run: python providers.py models",
 429: "rate limited - free tiers are slow; wait, or use a different model",
}

def post(url, body, headers, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors="ignore")
        # Gemini reports an invalid/foreign key as HTTP 400.  Normalise it so
        # the pool gives the owner an actionable bad-key message and does not
        # describe it as a model-option problem.
        if e.code == 400 and "valid api key" in detail.lower():
            raise ProviderError(401, detail, HINTS[401]) from None
        raise ProviderError(e.code, detail, HINTS.get(e.code, "")) from None

def get(url, headers, timeout=30):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def list_models(provider=None, only_free=False):
    """Ask the provider what it actually serves. Model ids change; this does not."""
    name = provider or active()
    if not name: raise RuntimeError("no provider configured")
    p = PROVIDERS[name]
    key = os.environ.get(p["env"], "")
    base = os.environ.get("ENGINEAI_API_BASE") or p["base"]
    h = {"authorization": "Bearer " + key} if key else {}
    data = get(base + "/models", h)
    items = data.get("data", data.get("models", []))
    out = []
    for m in items:
        mid = m.get("id") or m.get("name") or ""
        pr = m.get("pricing") or {}
        free = (":free" in mid) or (str(pr.get("prompt", "1")) in ("0", "0.0", "0e0"))
        out.append((mid, free, m.get("context_length") or m.get("context") or ""))
    if only_free: out = [x for x in out if x[1]]
    return sorted(out, key=lambda x: (not x[1], x[0]))

def pick_free_model(provider=None):
    """Used when the configured model 404s: find one that exists and is free."""
    try:
        ms = list_models(provider, only_free=True)
    except Exception:
        return None
    prefer = ("deepseek", "qwen", "glm", "llama", "mistral", "gemma")
    for want in prefer:
        for mid, _f, _c in ms:
            if want in mid.lower(): return mid
    return ms[0][0] if ms else None

def complete(system, messages, provider=None, model=None, timeout=180, base=None, json_mode=True):
    """One call. Returns the model's text. Raises on transport failure."""
    name = provider or active()
    if not name: raise RuntimeError("no provider configured — see: python providers.py list")
    p = PROVIDERS[name]
    key = os.environ.get(p["env"])
    if not key: raise RuntimeError(f"{p['env']} is not set")
    model = model or os.environ.get("ENGINEAI_API_MODEL") or p["model"]
    base = base or os.environ.get("ENGINEAI_API_BASE") or p["base"]

    if name == "anthropic":                       # different wire format
        body = {"model": model, "max_tokens": 1200, "system": system,
                "messages": [{"role": m["role"], "content": m["content"]} for m in messages]}
        h = {"content-type": "application/json", "x-api-key": key,
             "anthropic-version": "2023-06-01"}
        return post(base + "/messages", body, h, timeout)["content"][0]["text"]

    body = {"model": model, "temperature": 0.1,
            "messages": [{"role": "system", "content": system}] +
                        [{"role": m["role"], "content": m["content"]} for m in messages]}
    if json_mode and p["json_mode"] == "response_format":
        body["response_format"] = {"type": "json_object"}   # the thing that makes it work
    h = {"content-type": "application/json", "authorization": "Bearer " + key}
    if name == "openrouter":
        h["HTTP-Referer"] = "http://localhost"; h["X-Title"] = "EngineAI"
    try:
        r = post(base + "/chat/completions", body, h, timeout)
    except ProviderError as e:
        if e.code != 404: raise
        alt = pick_free_model(name)
        if not alt or alt == model:
            raise ProviderError(404, e.detail,
                "the model name does not exist and no free alternative was found. "
                "Run: python providers.py models") from None
        body["model"] = alt
        r = post(base + "/chat/completions", body, h, timeout)
        os.environ["ENGINEAI_API_MODEL"] = alt        # stick with what worked
        sys.stderr.write(f"[providers] '{model}' not found; switched to '{alt}'\n")
    return r["choices"][0]["message"]["content"]

# --------------------------------------------------- backend for agent_loop
class HostedBackend:
    name = "hosted"
    def __init__(self, provider=None, model=None, timeout=180):
        self.provider = provider or active()
        self.model = model
        self.timeout = timeout
        if self.provider: self.name = self.provider
    def available(self): return bool(self.provider)
    def complete(self, system, messages):
        return complete(system, messages, self.provider, self.model, self.timeout)

def show():
    a = active()
    print(f"\n  {'provider':13s} {'key set':8s} {'free tier':44s} model")
    print("  " + "-"*96)
    for n, p in PROVIDERS.items():
        has = "yes" if os.environ.get(p["env"]) else "-"
        mark = " <- active" if n == a else ""
        print(f"  {n:13s} {has:8s} {(p['free'] or 'paid only')[:44]:44s} {p['model']}{mark}")
    print()
    if not a:
        print("  Nothing configured. The quickest free route:\n")
        print("    1. register at openrouter.ai and copy the key")
        print("    2. export OPENROUTER_API_KEY=sk-or-...")
        print("       export ENGINEAI_PROVIDER=openrouter")
        print("    3. python studio.py\n")
        print("  Free alternatives if you prefer a Chinese provider directly:")
        for n, p in PROVIDERS.items():
            if p["free"]: print(f"    {n:13s} {p['signup']}")
    else:
        p = PROVIDERS[a]
        print(f"  active: {a} — model {os.environ.get('ENGINEAI_API_MODEL') or p['model']}")
        print(f"  override the model with: export ENGINEAI_API_MODEL=<name>")
    print()

def test(model=None):
    a = active()
    if not a: print("\n  no provider configured\n"); return False
    print(f"\n  testing {a} ...")
    try:
        out = complete('Reply with only this JSON: {"ok":true,"provider":"<your name>"}',
                       [{"role": "user", "content": "respond"}], model=model, timeout=60)
        print("  raw:", out[:180])
        try:
            j = json.loads(out); print("  parsed JSON:", j)
            print("  JSON mode works — the agent loop will run on this provider.")
        except json.JSONDecodeError:
            print("  ⚠ the reply was not valid JSON. The loop will still work "
                  "(it extracts JSON from prose) but less reliably.")
        return True
    except ProviderError as e:
        print(f"  HTTP {e.code}: {e.detail[:180]}")
        if e.hint: print(f"  -> {e.hint}")
    except Exception as e:
        print(f"  {type(e).__name__}: {e}")
    return False

def selftest():
    ok = True
    print("\nPROVIDERS SELFTEST   (no key needed)")
    # 1 request shape, verified against a local stand-in server
    import http.server, threading, socket
    seen = {}
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            n = int(self.headers["Content-Length"])
            seen["body"] = json.loads(self.rfile.read(n))
            seen["headers"] = {k.lower(): v for k, v in self.headers.items()}
            seen["path"] = self.path
            out = json.dumps({"choices": [{"message": {"content":
                   '{"thought":"t","tool":"lookup_formula","args":{"question":"beam"}}'}}]}).encode()
            self.send_response(200); self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out))); self.end_headers()
            self.wfile.write(out)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["OPENROUTER_API_KEY"] = "test-key"
    txt = complete("SYS", [{"role": "user", "content": "hi"}], provider="openrouter",
                   base=f"http://127.0.0.1:{port}/v1", timeout=20)
    b = seen["body"]
    checks = [("json mode requested", b.get("response_format") == {"type": "json_object"}),
              ("system message first", b["messages"][0]["role"] == "system"),
              ("user message present", b["messages"][-1]["content"] == "hi"),
              ("low temperature", b.get("temperature") == 0.1),
              ("bearer auth sent", seen["headers"].get("authorization") == "Bearer test-key"),
              ("openrouter headers", "x-title" in seen["headers"]),
              ("path correct", seen["path"].endswith("/chat/completions")),
              ("reply returned", "lookup_formula" in txt)]
    for n, c in checks:
        print(f"  {'ok  ' if c else 'FAIL'} {n}"); ok &= c
    # 2 the backend drives the real agent loop against the stand-in
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import agent_loop as A
    bk = HostedBackend("openrouter"); bk_base = f"http://127.0.0.1:{port}/v1"
    bk.complete = lambda s_, m_: complete(s_, m_, "openrouter", None, 20, bk_base)
    rec = A.run("beam deflection", backend=bk, max_steps=2, verbose=False,
                log="/tmp/_prov.jsonl")
    good = rec["steps"] and rec["steps"][0]["tool"] == "lookup_formula"
    print(f"  {'ok  ' if good else 'FAIL'} agent loop runs on a hosted backend "
          f"(first tool: {rec['steps'][0].get('tool') if rec['steps'] else None})")
    ok &= bool(good)
    # 3 selection logic
    os.environ["ENGINEAI_PROVIDER"] = "openrouter"
    good = active() == "openrouter"
    print(f"  {'ok  ' if good else 'FAIL'} ENGINEAI_PROVIDER selects the provider"); ok &= good
    del os.environ["OPENROUTER_API_KEY"]; del os.environ["ENGINEAI_PROVIDER"]
    good = active() is None or active() != "openrouter"
    print(f"  {'ok  ' if good else 'FAIL'} unsets cleanly when the key is removed"); ok &= good
    srv.shutdown()
    os.path.exists("/tmp/_prov.jsonl") and os.remove("/tmp/_prov.jsonl")
    print("\nproviders selftest:", "PASSED" if ok else "FAILED")
    return ok

if __name__ == "__main__":
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("cmd", nargs="?", default="list", choices=["list", "test", "models"])
    ap.add_argument("--free", action="store_true", help="only free models")
    ap.add_argument("--model", help="test a specific model id")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    if a.cmd == "list": show()
    elif a.cmd == "models":
        prov = active()
        if not prov: sys.exit("\n  no provider configured — python providers.py list\n")
        try: ms = list_models(prov, a.free)
        except Exception as e: sys.exit(f"\n  could not list models: {e}\n")
        print(f"\n  {len(ms)} model(s) on {prov}" + ("  (free only)" if a.free else ""))
        for mid, free, ctx in ms[:60]:
            print(f"   {'FREE' if free else '    '}  {mid:58s} {ctx}")
        f = [m for m in ms if m[1]]
        if f:
            print(f"\n  use one:  export ENGINEAI_API_MODEL={f[0][0]}\n")
    else: sys.exit(0 if test(a.model) else 1)
