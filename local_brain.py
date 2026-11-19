#!/usr/bin/env python3
"""
local_brain.py -- run the chat brain on YOUR GPU via Ollama. Free. Offline.

WHAT THIS IS
------------
A drop-in replacement for the Fable 5 brain in ask_command.py. It talks to
a local Ollama server running Qwen 14B (or any model you pull), so the
open-ended [UNVERIFIED] tier costs $0 and needs no internet.

WHAT IT CHANGES AND WHAT IT DOES NOT
------------------------------------
CHANGES: only the open-ended chat fallback -- the [UNVERIFIED] answers.
DOES NOT CHANGE: handbook, mathsolver, builder (circuits), library,
materials, cad. Those are verified code and never touched the brain. Your
21/21 exam score and every citation stay exactly the same.

So this swap makes the WEAKEST, least-trusted tier free-and-offline while
leaving every trustworthy tier identical. That is the right trade.

THE RAG CONNECTION -- this is how it "knows your books"
------------------------------------------------------
You do NOT fine-tune Qwen on your books. Instead, when a question might be
in your library, library.py finds the right pages and this passes them to
Qwen as context. The model READS the page and answers from it, cited. That
is retrieval-augmented generation -- accurate, instant, no training. It is
how every serious "chat with your documents" system works.

SETUP (on your machine, one time):
    # install Ollama from https://ollama.com  (Linux one-liner on their site)
    ollama pull qwen2.5:14b        # ~9 GB, fits your 3060
    # smaller/faster alternative:  ollama pull llama3.1:8b
    ollama serve                    # runs the local server on :11434

    pip install requests

Then in config.json add:   "brain": "local"   (or "fable" to switch back)

Run:  python local_brain.py --check      # is Ollama up + model present?
      python local_brain.py "explain laminar vs turbulent flow"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OLLAMA = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:14b"
HOME = Path.home() / "engineai"

# same senior-engineer prompt as the cloud brain: answer, don't deflect,
# label assumptions. Kept identical so switching brains doesn't change tone.
SYSTEM = """You are a senior engineer answering a student or colleague. \
GIVE A USEFUL ANSWER, do not ask them to resend a vague question. State the \
most likely interpretation and answer it: show the governing equation, \
define symbols, state assumptions, give a worked number if numbers were \
provided, and end with the most common mistake on the topic. If passages \
from the student's own books are provided, prefer them and cite the page. \
Be honest when something is genuinely uncertain rather than inventing \
precision."""


def status(model: str = DEFAULT_MODEL) -> dict:
    """Is Ollama running and is the model pulled?"""
    try:
        import requests
    except ImportError:
        return {"ok": False, "reason": "pip install requests"}
    try:
        r = requests.get(f"{OLLAMA}/api/tags", timeout=5)
        tags = [m["name"] for m in r.json().get("models", [])]
        have = any(model.split(":")[0] in t for t in tags)
        return {"ok": True, "server": True, "models": tags,
                "has_model": have,
                "reason": "" if have else
                          f"model '{model}' not pulled -- run: ollama pull {model}"}
    except Exception as e:
        return {"ok": False, "server": False,
                "reason": f"Ollama not reachable at {OLLAMA} -- run: "
                          f"ollama serve  ({type(e).__name__})"}


def ask_local(question: str, library_context: str | None = None,
              model: str = DEFAULT_MODEL, timeout: int = 180) -> tuple:
    """Return (answer_text, cost_usd). cost is always 0.0 -- it's local.
    Signature matches ask_command.ask_brain so it's a drop-in."""
    import requests
    prompt = question
    if library_context:
        prompt += ("\n\nRelevant passages from the student's own books "
                   "(prefer these, cite the page):\n" + library_context)
    payload = {"model": model, "stream": False,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": prompt}]}
    r = requests.post(f"{OLLAMA}/api/chat", json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data["message"]["content"], 0.0


def brain_for(cfg: dict):
    """Return the right ask_brain function based on config. This is what
    ask_command imports so one config line switches local <-> cloud."""
    mode = cfg.get("brain", "fable")
    model = cfg.get("local_model", DEFAULT_MODEL)
    if mode == "local":
        def ask(cfg_, question, ctx):
            return ask_local(question, ctx, model)
        return ask
    # fall back to the cloud brain
    from ask_command import ask_brain
    return ask_brain


def main() -> int:
    a = sys.argv[1:]
    cfgp = HOME / "config.json"
    cfg = json.loads(cfgp.read_text()) if cfgp.exists() else {}
    model = cfg.get("local_model", DEFAULT_MODEL)

    if not a or a[0] == "--check":
        st = status(model)
        print("\n  LOCAL BRAIN (Ollama) status:")
        if st.get("server"):
            print(f"    server      running at {OLLAMA}")
            print(f"    models      {', '.join(st.get('models', [])) or '(none)'}")
            print(f"    target      {model}  "
                  f"{'READY' if st.get('has_model') else 'NOT PULLED'}")
            if not st.get("has_model"):
                print(f"\n  {st['reason']}")
        else:
            print(f"    {st['reason']}")
        print(f"\n  config brain mode: {cfg.get('brain', 'fable')} "
              f"(set \"brain\":\"local\" in config.json to use this)")
        return 0 if st.get("has_model") else 1

    # a bare question -> answer it locally (with library context if available)
    q = " ".join(a)
    ctx = None
    try:
        sys.path.insert(0, str(HOME))
        import library
        hits = library.search(q, k=3)
        if hits:
            ctx = "\n".join(f"[{h['book']} p.{h['page']}] {h['text'][:400]}"
                            for h in hits)
            print(f"  (found {len(hits)} library passage(s) to ground the answer)")
    except Exception:
        pass

    st = status(model)
    if not st.get("has_model"):
        print(f"  {st['reason']}")
        return 1
    print(f"  asking {model} locally ...\n")
    try:
        txt, cost = ask_local(q, ctx, model)
    except Exception as e:
        print(f"  failed: {e}")
        return 1
    print(txt)
    print(f"\n  [UNVERIFIED, local model, $0.00]  "
          "-- verified tiers (handbook/maths/circuits/library) are separate "
          "and exact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
