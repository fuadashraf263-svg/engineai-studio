#!/usr/bin/env python3
"""
sysinfo.py - what this machine actually is, and what EngineAI can use on it.

  python sysinfo.py            hardware, OS, python, engineering software, library
  python sysinfo.py files DIR  catalogue files by true type (magic bytes), find duplicates
  python sysinfo.py --json     machine-readable, for the UI to consume
"""
import os, sys, json, shutil, sqlite3, platform, subprocess, hashlib, collections

MAGIC = [(b"%PDF", "pdf"), (b"PK\x03\x04", "zip/ooxml"), (b"\x89PNG", "png"),
         (b"\xff\xd8\xff", "jpeg"), (b"GIF8", "gif"), (b"ISO-10303", "step"),
         (b"solid ", "stl-ascii"), (b"Rar!", "rar"), (b"7z\xbc\xaf", "7z"),
         (b"\x1f\x8b", "gzip"), (b"SQLite format 3", "sqlite"), (b"AT&TFORM", "djvu"),
         (b"\xd0\xcf\x11\xe0", "ms-ole"), (b"OggS", "ogg"), (b"\x00\x01\x00\x00", "font/ttf")]

TOOLS = {"ccx": "CalculiX FEA", "ollama": "local LLM runtime", "gmsh": "mesher",
         "ElmerSolver": "multiphysics", "simpleFoam": "OpenFOAM CFD", "blockMesh": "OpenFOAM mesh",
         "FreeCADCmd": "FreeCAD headless (TechDraw drawings)", "prusa-slicer": "slicer / G-code",
         "dot": "graphviz", "pandoc": "document conversion", "ngspice": "SPICE circuits",
         "kicad-cli": "KiCad CLI", "tesseract": "OCR", "pdflatex": "LaTeX", "qpdf": "PDF repair"}

PYMOD = {"numpy":"maths","scipy":"maths","sympy":"symbolic","matplotlib":"plots","pint":"units",
         "fitz":"PDF read","reportlab":"PDF write","docx":"Word","openpyxl":"Excel","pptx":"PowerPoint",
         "ezdxf":"DXF drawings","cadquery":"CAD B-rep","trimesh":"meshes","stl":"STL",
         "CoolProp":"fluid properties","cantera":"combustion","fluids":"fluid mechanics","ht":"heat transfer",
         "thermo":"thermodynamics","Pynite":"frame FEA","anastruct":"2D frames",
         "sectionproperties":"section props","control":"control systems","filterpy":"Kalman",
         "skidl":"circuit netlists","PySpice":"SPICE","schemdraw":"schematics","pymoo":"optimisation",
         "pyDOE3":"design of experiments","SALib":"sensitivity","sklearn":"ML","nevergrad":"optimisation",
         "pyvista":"3D viz","graphviz":"diagrams","chromadb":"embeddings store","PIL":"images"}

PIPNAME = {"Pynite":"PyNiteFEA","stl":"numpy-stl","sklearn":"scikit-learn","PyNite":"PyNiteFEA","fitz":"pymupdf",
           "docx":"python-docx","pptx":"python-pptx","PIL":"pillow","cadquery":"cadquery (conda-forge)",
           "PySpice":"PySpice","control":"control","graphviz":"graphviz"}

def sh(*c):
    try: return subprocess.run(c, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception: return ""

def hardware():
    d = {"os": platform.platform(), "python": platform.python_version(),
         "executable": sys.executable, "cores": os.cpu_count()}
    try:
        mem = {l.split(":")[0]: l.split()[1] for l in open("/proc/meminfo")}
        d["ram_gb"] = round(int(mem["MemTotal"])/1048576, 1)
        d["ram_free_gb"] = round(int(mem["MemAvailable"])/1048576, 1)
    except Exception: pass
    try:
        t = shutil.disk_usage(os.path.expanduser("~"))
        d["disk_total_gb"], d["disk_free_gb"] = round(t.total/1e9), round(t.free/1e9)
    except Exception: pass
    try:
        d["wsl"] = "microsoft" in open("/proc/version").read().lower()
    except Exception: d["wsl"] = False
    return d

def software():
    ALT = {"FreeCADCmd": ["freecadcmd", "FreeCADCmd", "freecad", "FreeCAD"]}
    found = {}
    for k in TOOLS:
        w = shutil.which(k) or next((shutil.which(x) for x in ALT.get(k, []) if shutil.which(x)), None)
        if not w:                       # conda envs are not always on PATH
            for base in (os.path.dirname(sys.executable),
                         os.path.expanduser("~/miniconda3/envs/eng/bin"),
                         "/usr/lib/freecad/bin", "/usr/lib/freecad-daily/bin"):
                for cand in [k] + ALT.get(k, []):
                    c = os.path.join(base, cand)
                    if os.path.exists(c): w = c; break
                if w: break
        found[k] = w or ""
    return found

class _Quiet:
    """Silence C-level and logging noise from probed libraries (skidl/KiCad)."""
    def __enter__(self):
        self.null = os.open(os.devnull, os.O_WRONLY)
        self.saved = os.dup(2); os.dup2(self.null, 2); return self
    def __exit__(self, *a):
        os.dup2(self.saved, 2); os.close(self.saved); os.close(self.null)

def pymods():
    out = {}
    os.environ.setdefault("KICAD_SYMBOL_DIR", "")
    with _Quiet():
        for m in PYMOD:
            try:
                mod = __import__(m); out[m] = getattr(mod, "__version__", "ok")
            except Exception: out[m] = ""
    return out

def models():
    if not shutil.which("ollama"): return []
    return [l.split()[0] for l in sh("ollama", "list").splitlines()[1:] if l.strip()]

def library():
    p = os.path.expanduser("~/engineai/library/kb.db")
    if not os.path.exists(p): return {}
    try:
        c = sqlite3.connect(p)
        b, pg, ch = c.execute("SELECT COUNT(*),SUM(pages),SUM(chunks) FROM docs").fetchone()
        return {"db": p, "books": b, "pages": pg, "chunks": ch,
                "size_mb": round(os.path.getsize(p)/1e6)}
    except Exception: return {"db": p, "error": "unreadable"}

def sniff(path):
    try:
        with open(path, "rb") as f: head = f.read(64)
    except Exception: return "unreadable"
    if head and head.count(0) == len(head): return "ZERO-FILLED (placeholder / failed download)"
    for sig, name in MAGIC:
        if head.startswith(sig) or sig in head[:32]: return name
    try: head.decode("utf-8"); return "text"
    except Exception: return "binary"

def files(root, limit=200000):
    root = os.path.expanduser(root)
    kinds, sizes, seen, dupes, bad = collections.Counter(), collections.Counter(), {}, [], []
    n = 0
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in (".git", "node_modules", "__pycache__", ".cache")]
        for f in fn:
            p = os.path.join(dp, f)
            try: sz = os.path.getsize(p)
            except Exception: continue
            n += 1
            if n > limit: break
            ext = os.path.splitext(f)[1].lower() or "(none)"
            kinds[ext] += 1; sizes[ext] += sz
            if sz > 1_000_000:
                k = sniff(p)
                if "ZERO" in k: bad.append((p, sz))
                key = (sz, hashlib.md5(open(p, "rb").read(65536)).hexdigest())
                if key in seen: dupes.append((p, seen[key], sz))
                else: seen[key] = p
    print(f"\nFILES under {root}   {n} file(s)")
    print(f"{'ext':12s} {'count':>7s} {'total':>10s}")
    for e, c in kinds.most_common(18):
        print(f"{e:12s} {c:7d} {sizes[e]/1e6:9.1f} MB")
    if bad:
        print(f"\nZERO-FILLED / broken ({len(bad)}) - these look real but contain nothing:")
        for p, s in bad[:12]: print(f"   {s/1e6:8.1f} MB  {p}")
    if dupes:
        w = sum(s for _, _, s in dupes)
        print(f"\nDUPLICATES ({len(dupes)}), {w/1e9:.2f} GB reclaimable:")
        for a, b, s in dupes[:10]: print(f"   {s/1e6:8.1f} MB  {os.path.basename(a)[:44]}")

def report(as_json=False):
    d = dict(hardware=hardware(), software=software(), python_modules=pymods(),
             models=models(), library=library())
    if as_json: print(json.dumps(d, indent=2)); return d
    h = d["hardware"]
    print("\n" + "="*66); print("  MACHINE"); print("="*66)
    print(f"  {h['os']}")
    print(f"  python {h['python']}   {h['executable']}")
    print(f"  {h.get('cores')} cores   RAM {h.get('ram_gb','?')} GB "
          f"({h.get('ram_free_gb','?')} free)   disk {h.get('disk_free_gb','?')}/"
          f"{h.get('disk_total_gb','?')} GB free   WSL={h.get('wsl')}")
    if h.get("ram_gb", 99) < 9:
        print("  !! RAM under 9 GB - a 14B model will not fit. Use a 7B.")
    print("\n" + "="*66); print("  ENGINEERING SOFTWARE"); print("="*66)
    for k, v in d["software"].items():
        print(f"  {'ok ' if v else '-- '} {k:14s} {TOOLS[k]:38s} {v}")
    print("\n" + "="*66); print("  PYTHON CAPABILITY"); print("="*66)
    ok = [k for k, v in d["python_modules"].items() if v]
    no = [k for k, v in d["python_modules"].items() if not v]
    for k in ok: print(f"  ok  {k:20s} {PYMOD[k]}")
    if no:
        print("\n  MISSING: " + ", ".join(no))
        print("  install:  pip install " + " ".join(PIPNAME.get(m, m) for m in no))
    print("\n" + "="*66); print("  LOCAL MODELS"); print("="*66)
    print("  " + (", ".join(d["models"]) if d["models"] else "none pulled"))
    L = d["library"]
    print("\n" + "="*66); print("  KNOWLEDGE LIBRARY"); print("="*66)
    print(f"  {L.get('books','-')} books, {L.get('pages','-')} pages, "
          f"{L.get('chunks','-')} chunks, {L.get('size_mb','-')} MB" if L else "  not built")
    print(f"\n  {len(ok)}/{len(PYMOD)} python capabilities, "
          f"{sum(1 for v in d['software'].values() if v)}/{len(TOOLS)} external tools\n")
    return d

if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "files": files(a[1] if len(a) > 1 else "~")
    else: report("--json" in a)
