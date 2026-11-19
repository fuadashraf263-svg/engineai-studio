# EngineAI — What's Left to Build

`checkup.py` tracks the chores. This is the **build list**: things that don't exist yet, ordered by what they unlock. Written 7 Aug 2026.

---

## Already built (so you can see the shape of what remains)

87 cited references · dimensional audit · formula guard · BM25 over 70 books · CalculiX validated to 0.40% · OpenFOAM validated with a convergence table · SDF geometry (lattices, conformal channels, organic blends) · coupled flow→thermal→stress · CEM (requirements → sized design → printable STL) · mechanical drawings (multiview, section, exploded) · 29 generators · 3D field sandbox · agent loop with tool routing · 13-provider API pool with failover · worksheets and problem sets · owner auth with login · test journal.

---

## 1. Makes the physics trustworthy — build these first

### 1.1 FEA on generated geometry ★ highest leverage
**What's missing:** marching cubes gives a *surface* mesh; CalculiX needs *tetrahedra*.
**Build:** `mesh_to_fea.py` — gmsh reads the STL, tetrahedralises it, writes a CalculiX deck, runs it, maps von Mises back onto `viz3d`.
**Unlocks:** stress analysis of the lattices and chambers you already generate. Right now you can *make* a cooled chamber but not *prove* it survives — the coupled chain only does a 1-D estimate.
**Effort:** 2–3 days. gmsh is installed; the API is `gmsh.merge(stl)` → `classifySurfaces` → `createGeometry` → `mesh(3)`.

### 1.2 NAFEMS benchmarks
**Build:** LE1 (elliptic membrane), LE10 (thick plate), maybe T3 (transient heat), each as a `verify.py`-style case with published target values.
**Unlocks:** the answer to "how do I know your FEA is right" is a recognised benchmark rather than your own cantilever.
**Effort:** 2 days. These are the numbers a buyer's engineer will ask for by name.

### 1.3 Turbulent CFD validation
**What's missing:** you validated *laminar* Poiseuille. Every real duct, pipe and chamber is turbulent.
**Build:** a k-ω SST case against the backward-facing step or turbulent channel benchmark.
**Unlocks:** honest turbulent results. Until this exists, don't claim them.
**Effort:** 3–4 days, and the hardest of the three.

### 1.4 Verify the 87 citations
Not a build — a morning with your Shigley and Gere. I wrote the table/equation numbers from knowledge and never checked them. The formulas are verified twice over; the *provenance* isn't.

---

## 2. Makes it answer more — capability

### 2.1 Semantic retrieval
**Now:** BM25 keyword only. "Buckling" won't find a page that says "instability".
**Build:** `ollama pull nomic-embed-text`, embed the 38,000 pages into chromadb, hybrid BM25 + vector search.
**Effort:** 1 day plus a few hours of embedding. Keep embeddings **local** — re-embedding because a hosted model was deprecated is a day you don't get back.

### 2.2 A second CEM part family
**Now:** one framework, one part (cooled chamber). That's an example, not a framework.
**Build:** a heat exchanger, a pressure vessel, or a lattice-cored bracket — requirements in, sized and printable geometry out.
**Unlocks:** the claim that the CEM approach generalises. Also the second data point that tells you which parts of `cem.py` are actually reusable.
**Effort:** 3–4 days, much less if `cem.py` factors cleanly.

### 2.3 Design optimisation loop
**Now:** `cem.py` does a grid sweep. `pymoo`, `pyDOE3` and `SALib` are installed and idle.
**Build:** wrap the CEM in NSGA-II — minimise mass, maximise margin, subject to AM constraints.
**Unlocks:** "the computer found a design you wouldn't have tried" — the honest version of AI-assisted design.
**Effort:** 2 days.

### 2.4 Vision input
**Now:** the `vision` role exists in `keypool.py`; nothing calls it.
**Build:** upload a photographed sketch or scanned drawing → extract dimensions → build the part.
**Unlocks:** the demo that makes people lean forward.
**Effort:** 2–3 days, and error-prone. Do it after the trust work.

### 2.5 Assemblies and BOM
**Now:** single parts, and an exploded view of bodies you generated separately.
**Build:** a real assembly graph — parts, mates, balloon numbers, a BOM table on the drawing.
**Effort:** a week. Needed for anything with more than one component.

---

## 3. Makes it a product

### 3.1 Onboarding
Someone who isn't you opens the studio and sees a chat box. There's no "here's what I can do" and no worked example. **A day**, and it's the difference between a demo and a product.

### 3.2 Packaging
**Build:** `install.sh` that takes a clean Ubuntu to a working EngineAI — conda env, apt packages, pip, the library, a smoke test.
**Unlocks:** a second machine, a collaborator, or a customer running it themselves. **1–2 days.**

### 3.3 Async request handling
**Now:** the model call blocks the request. One slow answer freezes the service for everyone.
**Build:** a job queue — POST returns a job id, the browser polls or streams.
**Needed before:** more than one user. **2 days.**

### 3.4 Student progress that survives
**Now:** `course.py` writes to a local SQLite; the HTML uses localStorage.
**Build:** progress tied to a user, so a student can use it on two devices. **Needs accounts first.**

### 3.5 Pricing and a landing page
No price, no page, no way to pay. Nothing technical — but nothing sells without it.

---

## 4. Before anything is public

- **TLS** — Caddy in front, two lines of config
- **Async** (3.3) — or one user's slow question blocks everyone
- **Request size limits** — an oversized upload will exhaust RAM
- **Log rotation** — `audit.log` grows forever
- **`server.py` command whitelist audit** — it runs shell commands; whitelists leak through arguments
- **Terms reviewed by a lawyer** — `TERMS.md` is a draft I wrote, not legal advice

---

## 5. Deliberately not building

- **Diffusion image generation** — needs 6–12 GB VRAM. A render of your real CAD is more useful and more honest anyway. Revisit when you have the GPU.
- **Beating a frontier model at open reasoning** — not winnable, and not the point. Your edge is that the numbers are checked.
- **Autonomous novel machine design** — nobody has this, including the companies claiming it.

---

## The order I'd actually work in

1. **FEA on generated geometry** (1.1) — your geometry is beautiful and unverified
2. **NAFEMS** (1.2) — the credibility artifact
3. **Semantic retrieval** (2.1) — one day, improves every answer
4. **Onboarding** (3.1) — one day, makes it demonstrable
5. **Second CEM family** (2.2) — proves the framework
6. **Optimisation loop** (2.3) — the "designs things" story, honestly told

Items 1–4 are about two weeks and take you from "works for me" to "I can show this to an engineer without flinching". Everything in section 3 is what turns that into something someone pays for.
