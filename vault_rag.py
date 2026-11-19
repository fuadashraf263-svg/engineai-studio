#!/usr/bin/env python3
"""
vault_rag.py - EngineAI private local retrieval over the engineer's own files.

Indexes local documents into a local vector store using a local Ollama embedding
model, and retrieves the most relevant chunks WITH provenance (source + chunk).
Everything stays on the machine - nothing is uploaded (a capability a cloud agent
like Manus cannot offer). Requires: ollama pull nomic-embed-text
"""
from __future__ import annotations
import argparse, hashlib, json, math, os, urllib.request


def embed_ollama(text, model="nomic-embed-text", host="http://127.0.0.1:11434", timeout=60):
    body = json.dumps({"model": model, "prompt": text}).encode()
    req = urllib.request.Request(host + "/api/embeddings", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["embedding"]


def _chunks(text, size=800, overlap=150):
    words = text.split()
    if not words:
        return []
    out, i, step = [], 0, max(1, size - overlap)
    while i < len(words):
        out.append(" ".join(words[i:i + size])); i += step
    return out


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Vault:
    def __init__(self, embed_fn=embed_ollama, path=None):
        self.embed_fn = embed_fn
        self.path = path
        self.items = []

    def add_text(self, text, source, chunk_size=800, overlap=150):
        for j, ch in enumerate(_chunks(text, chunk_size, overlap)):
            self.items.append({"source": source, "chunk_id": j, "text": ch, "vec": self.embed_fn(ch)})
        return self

    def add_file(self, filepath, **kw):
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            return self.add_text(f.read(), source=os.path.basename(filepath), **kw)

    def add_dir(self, directory, exts=(".md", ".txt", ".rst"), **kw):
        for root, _, files in os.walk(directory):
            for fn in files:
                if fn.lower().endswith(exts):
                    try:
                        self.add_file(os.path.join(root, fn), **kw)
                    except Exception:
                        pass
        return self

    def query(self, q, k=4):
        qv = self.embed_fn(q)
        scored = [(_cosine(qv, it["vec"]), it) for it in self.items]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"source": it["source"], "chunk_id": it["chunk_id"],
                 "score": round(s, 4), "text": it["text"][:400]} for s, it in scored[:k]]

    def save(self, path=None):
        path = path or self.path
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.items, f)
        return path

    def load(self, path=None):
        path = path or self.path
        with open(path, "r", encoding="utf-8") as f:
            self.items = json.load(f)
        return self


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    D = 256
    def stub(text):
        v = [0.0] * D
        for w in text.lower().split():
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % D] += 1.0
        return v
    v = Vault(embed_fn=stub)
    v.add_text("Cantilever beam tip deflection under a point load equals P L^3 divided by 3 E I.", "beams.md")
    v.add_text("The area of a circle of radius r is pi times r squared.", "geometry.md")
    v.add_text("Darcy friction factor for laminar pipe flow is 64 over the Reynolds number.", "fluids.md")
    h = v.query("beam tip deflection point load", k=1)
    chk("retrieves the right document", h[0]["source"] == "beams.md", h[0]["source"])
    chk("hit carries provenance", all(x in h[0] for x in ("source", "chunk_id", "score")))
    chk("semantic match to fluids", v.query("laminar friction factor", k=1)[0]["source"] == "fluids.md")
    chk("semantic match to geometry", v.query("circle area radius", k=1)[0]["source"] == "geometry.md")
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "vault.json")
    v.save(p)
    v2 = Vault(embed_fn=stub).load(p)
    chk("index persists + reloads", len(v2.items) == len(v.items))
    chk("reloaded index still retrieves", v2.query("beam deflection", k=1)[0]["source"] == "beams.md")
    print("vault_rag selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI private local RAG")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--index", metavar="DIR")
    ap.add_argument("--out", default="vault_index.json")
    ap.add_argument("--query")
    ap.add_argument("--db", default="vault_index.json")
    a = ap.parse_args()
    if a.index:
        vv = Vault(path=a.out).add_dir(a.index); vv.save()
        print(f"indexed {len(vv.items)} chunks from {a.index} -> {a.out}"); raise SystemExit(0)
    if a.query:
        vv = Vault(path=a.db).load()
        print(json.dumps(vv.query(a.query), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
