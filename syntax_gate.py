#!/usr/bin/env python3
"""
syntax_gate.py - recursive syntax gate for the whole repo.

Scans EVERY .py (minus heavy/vendor/quarantine dirs) and exits non-zero on any
syntax error. Add it to the audit and to pre_push.py so generated code can never
slip through green again.

Run:
  python syntax_gate.py            # scan current dir
  python syntax_gate.py ~/engineai # scan a specific root
Exit 0 = all good, 1 = at least one broken file.
"""
from __future__ import annotations
import ast
import pathlib
import sys

SKIP = {".venv", "venv", "env", "__pycache__", "work", "out", "build",
        "dist", ".git", "node_modules", "v1_backup", "_broken", "_quarantine"}


def scan(root="."):
    root = pathlib.Path(root)
    files = [p for p in root.rglob("*.py") if not set(p.parts) & SKIP]
    bad = []
    for p in files:
        try:
            ast.parse(p.read_text(encoding="utf-8", errors="replace"), filename=str(p))
        except SyntaxError as e:
            bad.append((p, e.lineno, e.msg))
    return files, bad


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    files, bad = scan(root)
    print(f"syntax_gate: checked {len(files)} .py files under {root} -> {len(bad)} broken")
    for p, ln, msg in bad:
        print(f"  FAIL  {p}: line {ln}: {msg}")
    sys.exit(1 if bad else 0)
