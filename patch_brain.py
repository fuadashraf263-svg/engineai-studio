#!/usr/bin/env python3
"""
patch_brain.py - merge YOUR local LLM into EngineAI, replacing the paid Claude call.

brain.py line ~50 is `import anthropic` -> the hosted API, your key, real money
per answer. That is why your AI said "I'm Claude" and printed $0.023.
This repoints the brain at ollama on your own machine: free, offline, and it
identifies itself as EngineAI.

  python patch_brain.py            patch workspace_server.py and ask_command.py
  python patch_brain.py --check    show the changes, touch nothing
  python patch_brain.py --revert   restore the .bak files
"""
import os, sys, ast, shutil

EA = os.environ.get('EA_DIR', os.path.expanduser('~/engineai'))
EDITS = [
    # (file, old, new)
    ("workspace_server.py",
     "            import ask_command as ak\n            txt, spent = ak.ask_brain(cfg, text, None)",
     "            import brain_local as ak          # local ollama, $0.00, offline\n"
     "            txt, spent = ak.ask_brain(cfg, text, None)"),
    ("ask_command.py",
     "            txt, brain_cost = ask_brain(cfg, question, lib_context)",
     "            from brain_local import ask_brain as _local_brain\n"
     "            txt, brain_cost = _local_brain(cfg, question, lib_context)"),
]

def revert():
    n = 0
    for f, _, _ in EDITS:
        p, b = os.path.join(EA, f), os.path.join(EA, f + ".bak")
        if os.path.exists(b): shutil.copy2(b, p); print("  restored", f); n += 1
    print(f"{n} file(s) restored" if n else "no backups found")

def main():
    if "--revert" in sys.argv: return revert()
    check = "--check" in sys.argv
    if not os.path.exists(os.path.join(EA, "brain_local.py")):
        sys.exit("brain_local.py is not in ~/engineai - copy it there first")
    done = skipped = 0
    for f, old, new in EDITS:
        p = os.path.join(EA, f)
        if not os.path.exists(p): print(f"  -- {f} not found"); continue
        src = open(p).read()
        if "brain_local" in src:
            print(f"  ok {f} already uses the local brain"); skipped += 1; continue
        if old not in src:
            print(f"  !! {f}: expected call not found. Lines that call ask_brain:")
            for i, l in enumerate(src.splitlines(), 1):
                if "ask_brain" in l: print(f"       line {i}: {l.strip()}")
            skipped += 1; continue
        if check:
            print(f"  would patch {f}:\n    - {old.strip()}\n    + {new.strip()}"); continue
        shutil.copy2(p, p + ".bak")
        out = src.replace(old, new)
        try: ast.parse(out)
        except SyntaxError as e: print(f"  !! {f} would not parse ({e}); left alone"); continue
        open(p, "w").write(out); print(f"  patched {f}  (backup {f}.bak)"); done += 1
    if not check and done:
        print("\nYour AI now answers from the local model. Verify:")
        print("  python brain_local.py --selftest")
        print("  python workspace_server.py     -> ask 'who are you'")
        print("\nThe paid path is still in brain.py if you ever want it as a priced tier.")

if __name__ == "__main__":
    main()
