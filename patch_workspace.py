#!/usr/bin/env python3
"""
patch_workspace.py - point the two-panel UI at the clean, field-filtered index.

Your UI cited "Docker in Practice" for a CAD question because it calls the old
library.py, which has no relevance floor and no idea which books are engineering.
This swaps that one call for kb.py. Makes a .bak first. Safe to run twice.

  python patch_workspace.py            patch ~/engineai/workspace_server.py
  python patch_workspace.py --check    show what would change, touch nothing
"""
import os, sys, shutil, re

TARGET = os.path.expanduser("~/engineai/workspace_server.py")

OLD = '''        import library
        for h in library.search(text, k=2):
            b = " ".join(h["text"].split())[:280]
            segments.append({"provenance": "LIBRARY",
                             "text": f"[{h['book']}, p.{h['page']}]\\n\\"{b}...\\""})'''

NEW = '''        import kb                       # clean index: engineering books only
        for _t, _pg, _sn, _sc, _body in kb.search(text, k=2, quiet=True,
                                                  field="engineering"):
            b = " ".join(_body.split())[:280]
            segments.append({"provenance": "LIBRARY",
                             "text": f"[{_t}, p.{_pg}]\\n\\"{b}...\\""})'''

def main():
    check = "--check" in sys.argv
    path = next((a for a in sys.argv[1:] if not a.startswith("-")), TARGET)
    if not os.path.exists(path):
        sys.exit(f"not found: {path}")
    src = open(path).read()
    if "import kb " in src or "import kb\n" in src:
        print("already patched - nothing to do"); return
    if OLD not in src:
        print("The expected library.py call was not found. Nothing changed.")
        print("Look for this line yourself and swap it by hand:")
        for i, l in enumerate(src.splitlines(), 1):
            if "library.search" in l: print(f"  line {i}: {l.strip()}")
        return
    if check:
        print("would replace:\n" + OLD + "\n\nwith:\n" + NEW); return
    shutil.copy2(path, path + ".bak")
    open(path, "w").write(src.replace(OLD, NEW))
    import ast; ast.parse(open(path).read())      # refuse to leave it broken
    print(f"patched {path}   (backup at {path}.bak)")
    print("restart the UI:  python workspace_server.py")

if __name__ == "__main__":
    main()
