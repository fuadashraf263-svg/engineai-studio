#!/usr/bin/env python3
"""
verify_engineai.py -- an honest capability check for EngineAI.

Run it from your project folder, in the (eng) env, as your NORMAL user
(no sudo):

    conda activate eng && cd ~/engineai
    python verify_engineai.py

It checks the FOUNDATIONS each capability needs -- Python libraries,
external solver binaries, project files, and whether each module loads.
It does NOT prove correctness: a module that loads can still compute a
wrong answer. The real proof is running each domain and checking its
output against a known value (it prints those commands at the end).

Pure standard library. It reads only -- it changes nothing on your system.
"""

import os, sys, shutil, subprocess, importlib, time, sqlite3, textwrap


# ---- pretty printing -------------------------------------------------
def line():
    print("-" * 68)


def head(t):
    print()
    line()
    print("  " + t)
    line()


STATUS = {
    "ok":      "  OK   ",
    "warn":    " WARN  ",
    "fail":    " FAIL  ",
    "missing": "MISSING",
    "info":    " INFO  ",
}


def row(status, name, note=""):
    print(f"[{STATUS.get(status, '  ?   ')}] {name:<26} {note}")


# ---- helpers ---------------------------------------------------------
def check_import(mod):
    """Import a third-party library in-process; report version if any."""
    try:
        m = importlib.import_module(mod)
        ver = getattr(m, "__version__", "")
        row("ok", mod, str(ver))
        return True
    except Exception as e:
        first = (str(e).splitlines() or [""])[0]
        row("fail", mod, f"{type(e).__name__}: {first[:38]}")
        return False


def check_binary(name, version_args=None, timeout=15):
    path = shutil.which(name)
    if not path:
        row("missing", name, "not on PATH")
        return False
    note = path
    if version_args:
        try:
            out = subprocess.run([name] + version_args, capture_output=True,
                                 text=True, timeout=timeout)
            txt = (out.stdout or out.stderr).strip().splitlines()
            if txt:
                note = txt[0][:46]
        except Exception:
            pass
    row("ok", name, note)
    return True


def check_module_loads(modname, timeout=30):
    """Import a project module in a SUBPROCESS, so a hang or side effect
    can't take down this script. cwd must be the project directory."""
    try:
        r = subprocess.run([sys.executable, "-c", f"import {modname}"],
                           capture_output=True, text=True,
                           timeout=timeout, cwd=os.getcwd())
        if r.returncode == 0:
            row("ok", modname + ".py", "loads")
            return True
        err = (r.stderr.strip().splitlines() or ["error"])[-1]
        row("fail", modname + ".py", err[:46])
        return False
    except subprocess.TimeoutExpired:
        row("warn", modname + ".py", "import hung (starts a loop/server?)")
        return False
    except Exception as e:
        row("fail", modname + ".py", str(e)[:46])
        return False


def file_present(fname):
    if os.path.exists(fname):
        row("ok", fname, f"{os.path.getsize(fname)} bytes")
        return True
    row("missing", fname, "not found")
    return False


# ---- run -------------------------------------------------------------
print()
print("  EngineAI capability check")
print("  cwd    :", os.getcwd())
print("  python :", sys.version.split()[0], "|", sys.executable)
print("  time   :", time.strftime("%Y-%m-%d %H:%M:%S"))

head("1. Python libraries the domains need")
libs = ["numpy", "scipy", "sympy", "matplotlib", "pandas",
        "cadquery", "OCP",
        "fastapi", "uvicorn", "websockets",
        "PIL", "pytesseract",
        "requests", "httpx",
        "sentence_transformers", "chromadb", "ollama",
        "pychrono"]
for l in libs:
    check_import(l)
print("  (sentence_transformers/chromadb missing = expected until the")
print("   RAG upgrade; pychrono lives in your 'chrono' env, not 'eng')")

head("2. External solver / tool binaries")
check_binary("ollama", ["--version"])
if shutil.which("ollama"):
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True,
                             text=True, timeout=15)
        models = [ln for ln in out.stdout.splitlines()[1:] if ln.strip()]
        names = ", ".join(m.split()[0] for m in models) if models else "(none yet)"
        row("info", "ollama models", f"{len(models)} pulled: {names}")
    except Exception:
        pass
check_binary("tesseract", ["--version"])
check_binary("ElmerSolver", None)
check_binary("gmsh", ["--version"])
if shutil.which("blockMesh"):
    check_binary("blockMesh", None)
else:
    foam = [d for d in ("/opt/openfoam13", "/opt/openfoam12", "/opt/openfoam11")
            if os.path.isdir(d)]
    if foam:
        row("ok", "OpenFOAM", foam[0] + " (source it to use)")
    else:
        row("missing", "OpenFOAM", "no /opt/openfoam* dir; source not loaded")

head("3. Project files present")
expected = ["agent.py", "chat_server.py", "server.py", "viewer.py",
            "library.py", "local_brain.py", "brain.py", "ask_command.py",
            "handbook.py", "mathsolver.py", "materials.py", "buckling_tools.py",
            "cad.py", "cfd_tools.py", "cfd_reference.py", "em_tools.py",
            "doe_tools.py", "engine_tools.py", "ocr.py", "exam.py",
            "fetch_papers.py"]
for f in expected:
    file_present(f)
print()
print("  the file that keeps being claimed but should never be assumed:")
file_present("workspace_server.py")

head("4. Do the compute modules load?   (loads != correct)")
print("  servers are NOT import-tested here -- run them directly instead")
compute = ["handbook", "mathsolver", "materials", "buckling_tools",
           "cad", "cfd_tools", "cfd_reference", "em_tools", "doe_tools",
           "engine_tools", "ocr", "exam", "library", "local_brain", "brain"]
for m in compute:
    check_module_loads(m)

head("5. Library database")
db = os.path.join("library", "library.db")
if os.path.exists(db):
    row("ok", "library/library.db", f"{os.path.getsize(db) // (1024 * 1024)} MB")
    try:
        con = sqlite3.connect(db)
        cur = con.cursor()
        tbls = [t[0] for t in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        row("info", "tables", (", ".join(tbls))[:46])
        con.close()
    except Exception as e:
        row("warn", "db read", str(e)[:46])
else:
    row("missing", "library/library.db", "run: python library.py add ~/books")

head("What this did and did NOT prove")
print(textwrap.fill(
    "Green means the foundation is present and the code loads. It does "
    "NOT mean the answer is correct. The real proof of each domain is "
    "running it and checking the number against a known value:", 66))
print("""
  CAD     : python cad.py --selftest      (builds + re-measures a part)
  Exam    : python exam.py                (re-runs the scoreboard)
  Chat    : python chat_server.py   ->  open http://localhost:8096
  Domains : python agent.py --help        (lists every command; run each
                                           one and check its output)
  CFD     : validate against the ANALYTICAL Poiseuille profile,
            not the planarPoiseuille tutorial (that one is viscoelastic)
""")
print("  Done. Nothing on your system was changed.\n")
