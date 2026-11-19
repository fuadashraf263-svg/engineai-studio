#!/usr/bin/env python3
"""
fix_old.py - close the last live holes in the older modules.

Three things checkup.py flags, fixed here with backups:

  1. mathsolver.py calls sympy.sympify() on input that reaches it from a user or
     a model. sympify runs eval(). That is remote code execution the moment the
     server is exposed. Same whitelist guard as engineai.py.
  2. library.py is superseded by kb.py but still importable, and it still has the
     duplicate-ingest and surrogate-crash bugs. Renamed, not deleted.
  3. cfd_fix.py points at cfd/channel, which does not exist.

  python fix_old.py --check     show what would change
  python fix_old.py             apply, with .bak files
  python fix_old.py --revert    undo
"""
import os, re, ast, sys, shutil, argparse

EA = os.path.expanduser(os.environ.get("ENGINEAI_DIR", "~/engineai"))

GUARD = '''
# --- added by fix_old.py: sympify() runs eval(), so validate before parsing ---
try:
    from owner import safe_expr as _safe_expr, Unsafe as _Unsafe
except Exception:                      # owner.py absent: fail closed, not open
    import re as _re
    class _Unsafe(ValueError): pass
    _ALLOWED = _re.compile(r"^[A-Za-z0-9_+\\-*/().,^ \\t]*$")
    _BAD = ("__", "import", "lambda", "eval", "exec", "open", "system", "subprocess",
            "globals", "locals", "getattr", "setattr", "compile", "class", "yield")
    def _safe_expr(s, extra_names=(), maxlen=400):
        if not isinstance(s, str) or len(s) > maxlen: raise _Unsafe("bad expression")
        if not _ALLOWED.match(s): raise _Unsafe("characters outside plain maths")
        low = s.lower()
        for b in _BAD:
            if b in low: raise _Unsafe("forbidden token: " + b)
        return s
# --- end guard ---
'''

def sub_sympify(src):
    """Wrap every sympify(x) call so x is validated first."""
    n = 0
    def rep(m):
        nonlocal n; n += 1
        return f"sympify(_safe_expr({m.group(1)})"
    out = re.sub(r"sympify\(\s*([A-Za-z_][\w\.\[\]'\"]*)", rep, src)
    return out, n

def do_mathsolver(check):
    p = os.path.join(EA, "mathsolver.py")
    if not os.path.exists(p): return "mathsolver.py not present — nothing to do"
    src = open(p, errors="ignore").read()
    if "_safe_expr" in src: return "mathsolver.py already guarded"
    out, n = sub_sympify(src)
    if n == 0: return "mathsolver.py has no sympify call — nothing to do"
    out = GUARD + out
    try: ast.parse(out)
    except SyntaxError as e: return f"mathsolver.py: patch would not parse ({e}); left alone"
    if check: return f"mathsolver.py: would guard {n} sympify call(s)"
    shutil.copy2(p, p + ".bak"); open(p, "w").write(out)
    return f"mathsolver.py: guarded {n} sympify call(s)  (backup .bak)"

def do_library(check):
    p = os.path.join(EA, "library.py")
    if not os.path.exists(p): return "library.py already retired"
    if check: return "library.py: would rename to library_old.py.disabled"
    shutil.move(p, os.path.join(EA, "library_old.py.disabled"))
    return "library.py: renamed to library_old.py.disabled (kb.py replaces it)"

def do_cfd(check):
    p = os.path.join(EA, "cfd_fix.py")
    if not os.path.exists(p): return "cfd_fix.py not present"
    src = open(p, errors="ignore").read()
    if "cfd/channel" not in src: return "cfd_fix.py already points somewhere real"
    if check: return "cfd_fix.py: would repoint cfd/channel -> work/cfd_poiseuille"
    shutil.copy2(p, p + ".bak")
    open(p, "w").write(src.replace("cfd/channel", "work/cfd_poiseuille"))
    return "cfd_fix.py: repointed to work/cfd_poiseuille  (backup .bak)"

def revert():
    n = 0
    for f in ("mathsolver.py", "cfd_fix.py"):
        b = os.path.join(EA, f + ".bak")
        if os.path.exists(b): shutil.copy2(b, os.path.join(EA, f)); n += 1; print("  restored", f)
    d = os.path.join(EA, "library_old.py.disabled")
    if os.path.exists(d): shutil.move(d, os.path.join(EA, "library.py")); n += 1; print("  restored library.py")
    print(f"  {n} file(s) restored" if n else "  nothing to restore")

def selftest():
    ok = True
    print("\nFIX_OLD SELFTEST")
    tmp = "/tmp/_fixold"; shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
    open(os.path.join(tmp, "mathsolver.py"), "w").write(
        "import sympy as sp\ndef solve(expr, vals):\n"
        "    e = sp.sympify(expr)\n    return float(e.subs(vals))\n")
    open(os.path.join(tmp, "library.py"), "w").write("# old indexer\n")
    open(os.path.join(tmp, "cfd_fix.py"), "w").write('CASE = "cfd/channel"\n')
    global EA; EA = tmp
    for fn in (do_mathsolver, do_library, do_cfd): print("  " + fn(False))
    src = open(os.path.join(tmp, "mathsolver.py")).read()
    good = "_safe_expr(expr)" in src and "sp.sympify" in src
    print(f"  {'ok  ' if good else 'FAIL'} sympify call is now validated"); ok &= good
    try:
        ast.parse(src); print("  ok   patched file still parses")
    except SyntaxError: print("  FAIL patched file does not parse"); ok = False
    # the guard must actually block an exploit
    sys.path.insert(0, tmp)
    import importlib.util as IU
    spec = IU.spec_from_file_location("ms_test", os.path.join(tmp, "mathsolver.py"))
    m = IU.module_from_spec(spec)
    try: spec.loader.exec_module(m)
    except Exception as e: print("  FAIL module will not import:", e); ok = False
    else:
        try:
            m.solve("__import__('os').getcwd()", {}); print("  FAIL exploit ran"); ok = False
        except Exception as e:
            print(f"  ok   exploit refused ({type(e).__name__})")
        try:
            v = m.solve("2+3", {}); good = abs(v-5) < 1e-9
            print(f"  {'ok  ' if good else 'FAIL'} ordinary maths still works: 2+3 = {v}")
            ok &= good
        except Exception as e: print("  FAIL ordinary maths broke:", e); ok = False
    good = os.path.exists(os.path.join(tmp, "library_old.py.disabled"))
    print(f"  {'ok  ' if good else 'FAIL'} library.py retired, not deleted"); ok &= good
    good = "work/cfd_poiseuille" in open(os.path.join(tmp, "cfd_fix.py")).read()
    print(f"  {'ok  ' if good else 'FAIL'} cfd_fix.py repointed"); ok &= good
    shutil.rmtree(tmp, ignore_errors=True)
    print("\nfix_old selftest:", "PASSED" if ok else "FAILED")
    return ok

if __name__ == "__main__":
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--check", action="store_true"); ap.add_argument("--revert", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    if a.revert: revert(); sys.exit(0)
    print()
    for fn in (do_mathsolver, do_library, do_cfd): print("  " + fn(a.check))
    print("\n  re-check:  python checkup.py\n" if not a.check else
          "\n  apply with:  python fix_old.py\n")
