#!/usr/bin/env python3
"""
model_router.py - diverse-model router for consensus (local-first, cloud fallback).

Fans the K consensus samples across K DISTINCT model sources to decorrelate errors.
Honors a `private` flag: private questions use LOCAL sources only and NEVER touch the
cloud. Local (Ollama) sources are preferred; cloud free-API sources are fallback for
non-private or hard questions. The selftest proves the routing/privacy logic; the
Ollama/cloud adapters are thin wrappers you wire on your machine.
"""
from __future__ import annotations
import json, urllib.request


class Source:
    def __init__(self, name, kind, generate, available=None):
        self.name = name
        self.kind = kind
        self._generate = generate
        self._available = available or (lambda: True)

    def available(self):
        try:
            return bool(self._available())
        except Exception:
            return False

    def generate(self, prompt):
        return self._generate(prompt)


class Router:
    def __init__(self, sources):
        self.sources = list(sources)

    def select(self, k, private):
        elig = [s for s in self.sources
                if s.available() and (s.kind == "local" or not private)]
        seen, chosen = set(), []
        for s in elig:
            if s.name not in seen:
                seen.add(s.name); chosen.append(s)
            if len(chosen) >= k:
                break
        return chosen

    def model_fn(self, k=3, private=True):
        chosen = self.select(k, private)
        state = {"i": 0}
        def fn(kind, prompt):
            if not chosen:
                raise RuntimeError("no eligible model source"
                                   + (" (private: no local model available)" if private else ""))
            s = chosen[state["i"] % len(chosen)]; state["i"] += 1
            return s.generate(prompt)
        return fn, [s.name for s in chosen]


def ollama_source(model, host="http://127.0.0.1:11434", timeout=120):
    def gen(prompt):
        try:
            body = json.dumps({"model": model, "prompt": prompt, "stream": False, "keep_alive": "30m",
                               "options": {"temperature": 0.3, "top_p": 0.95}}).encode()
            req = urllib.request.Request(host + "/api/generate", data=body,
                                         headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read()).get("response", "")
        except Exception:
            return ""
    def avail():
        try:
            with urllib.request.urlopen(host + "/api/tags", timeout=3) as r:
                return any(model in t.get("name", "") for t in json.loads(r.read()).get("models", []))
        except Exception:
            return False
    return Source(f"ollama:{model}", "local", gen, avail)


def cloud_source(name, generate, available=None):
    return Source(name, "cloud", generate, available)


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    def stub(tag): return lambda prompt: f"{tag}:{prompt[:8]}"
    L1 = Source("local-A", "local", stub("A")); L2 = Source("local-B", "local", stub("B"))
    L3 = Source("local-C", "local", stub("C")); C1 = Source("cloud-X", "cloud", stub("X"))
    C2 = Source("cloud-Y", "cloud", stub("Y"))
    dead = Source("local-dead", "local", stub("D"), available=lambda: False)
    fn, names = Router([L1, L2, L3, C1]).model_fn(k=3, private=True)
    outs = [fn("SOLVER", "q") for _ in range(3)]
    chk("private -> 3 distinct LOCAL sources", names == ["local-A", "local-B", "local-C"] and len({o[0] for o in outs}) == 3)
    fn, names = Router([dead, C1, C2]).model_fn(k=3, private=True)
    refused = False
    try: fn("SOLVER", "q")
    except RuntimeError: refused = True
    chk("private + no local -> REFUSES (no cloud leak)", refused and names == [])
    fn, names = Router([L1, C1, C2]).model_fn(k=3, private=False)
    chk("non-private fills with cloud, local first", names == ["local-A", "cloud-X", "cloud-Y"])
    fn, names = Router([dead, L1, L2]).model_fn(k=3, private=True)
    chk("unavailable source skipped", names == ["local-A", "local-B"])
    fn, names = Router([L1, L2]).model_fn(k=3, private=True)
    seq = [fn("x", "q") for _ in range(3)]
    chk("degrades gracefully when < k sources", len(names) == 2 and seq[0][0] == "A" and seq[2][0] == "A")
    print("model_router selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
