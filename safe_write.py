#!/usr/bin/env python3
"""
safe_write.py - validate-before-save guards so generators never emit broken files.

Route every generated .py / .json write through these helpers: invalid or
suspiciously short output raises instead of being saved, and writes are atomic
(temp file + rename) so a crash never leaves a half-written file behind.

Wire-in (propose.py / generators.py / generators_pro.py):
    from safe_write import write_python_checked
    write_python_checked(path, code)   # raises ValueError if truncated/invalid
"""
from __future__ import annotations
import ast
import json
import os
import pathlib
import tempfile


def _atomic_write(path, text: str) -> str:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, str(p))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return str(p)


def write_python_checked(path, code: str, *, min_lines: int = 3) -> str:
    """Write Python only if non-empty, not suspiciously short, and it parses."""
    if not code or not code.strip():
        raise ValueError(f"refusing to write empty Python to {path}")
    nlines = code.count("\n") + 1
    if nlines < min_lines:
        raise ValueError(f"refusing to write suspiciously short Python ({nlines} lines) to {path}")
    try:
        ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"refusing to write invalid Python to {path}: line {e.lineno}: {e.msg}") from e
    return _atomic_write(path, code)


def write_json_checked(path, obj) -> str:
    """Write JSON only if it round-trips through json.loads."""
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=2, ensure_ascii=False)
    json.loads(text)
    return _atomic_write(path, text)


def _selftest() -> int:
    ok = True
    d = tempfile.mkdtemp()

    good = "def f(x):\n    return x * x\n\nprint(f(3))\n"
    try:
        write_python_checked(os.path.join(d, "good.py"), good)
        print("  PASS  valid Python saved")
    except Exception as e:
        ok = False
        print("  FAIL  valid Python rejected:", e)

    bad = 'def f():\n    y = check("oops",\n    return y\n'
    try:
        write_python_checked(os.path.join(d, "bad.py"), bad)
        ok = False
        print("  FAIL  broken Python was NOT rejected")
    except ValueError:
        print("  PASS  broken Python rejected before save")

    try:
        write_json_checked(os.path.join(d, "ok.json"), {"a": 1, "b": [2, 3]})
        print("  PASS  valid JSON saved")
    except Exception as e:
        ok = False
        print("  FAIL  valid JSON rejected:", e)

    print("safe_write selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
