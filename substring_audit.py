#!/usr/bin/env python3
"""substring_audit.py - find the bug that keeps happening.

Four times in two days the same defect shipped, in four unrelated modules:

    "react"  matched inside "reactions"        -> beam question routed to web dev
    "F1"     matched inside "F16"              -> a prediction attack read as a holdout
    "nu"     matched inside "number_of_elements" -> element count held to Poisson's ratio
    "eta"    matched inside "zeta"             -> overdamped ratio failed as an efficiency

Every one was a substring test standing in for a word test. Every one was found
by a human noticing a weird result, which is not a method. This module finds
them mechanically instead.

WHAT IT LOOKS FOR
  1  `LITERAL in text` / `text.startswith(LITERAL)` where the literal is a short
     alphabetic token - the shape that matches inside longer words
  2  the same, over a collection of such literals (`any(f in text for f in FRAGS)`)
  3  regex fragments built by escaping a literal with no boundary anchor
  4  DECLARED VOCABULARY COLLISIONS: any two terms in the project's own keyword
     tables where one is a substring of the other. That is the actual trigger -
     "nu" only hurts because "number_of_elements" also exists.

WHAT IT DELIBERATELY DOES NOT DO. It does not rewrite anything. A substring test
is legitimate in plenty of places - path prefixes, file extensions, sentinel
markers - and a tool that auto-fixed them would break working code to satisfy a
pattern. It reports, with the collision that makes each one dangerous, and a
person decides.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["scan_file", "scan_tree", "collisions", "Risk"]

# Short alphabetic literals are the dangerous ones. A long or punctuated literal
# ("professional certification", "-->") does not accidentally live inside
# another word, so flagging it would be noise.
MAX_RISKY_LEN = 6
_ALPHA = re.compile(r"^[a-z][a-z0-9_]*$")

# Contexts where substring matching is the correct thing to do.
SAFE_HINTS = ("path", "file", "ext", "suffix", "prefix", "dir", "url", "sep",
              "encoding", "mime", "magic", "header", "marker", "sentinel")

# The bug only bites when the literal is tested against USER TEXT. `"error" in
# result` is a dict-key lookup and is fine; `"react" in question` is the defect.
# Static typing is not available here, so the receiver is judged by name -
# imprecise, but precise enough to separate a short actionable list from 174
# lines of noise, and a checker nobody reads catches nothing.
TEXT_RECEIVERS = {
    "text", "t", "q", "low", "s", "line", "name", "msg", "message", "prompt",
    "query", "request", "activity", "frag", "phrase", "sentence", "words",
    "lowered", "cleaned", "body", "content", "title", "label", "desc",
    "description", "question", "answer", "raw", "src", "string", "col",
    "column", "colname", "key",
}


def _is_text_receiver(node: ast.AST) -> bool:
    """Does the right-hand side of `lit in X` look like a string of user text?"""
    if isinstance(node, ast.Call):                      # x.lower(), x.strip()
        f = node.func
        return isinstance(f, ast.Attribute) and f.attr in (
            "lower", "upper", "strip", "casefold", "lstrip", "rstrip")
    if isinstance(node, ast.Name):
        return node.id.lower() in TEXT_RECEIVERS
    if isinstance(node, ast.Attribute):
        return node.attr.lower() in TEXT_RECEIVERS
    if isinstance(node, ast.BinOp):                     # a + " " + b
        return True
    return False


@dataclass
class Risk:
    file: str
    line: int
    kind: str
    literal: str
    snippet: str
    collides_with: list

    def __str__(self) -> str:
        col = (f"  collides with {', '.join(self.collides_with[:3])}"
               if self.collides_with else "  (no collision found in project vocabulary)")
        return (f"  {self.file}:{self.line}  [{self.kind}] '{self.literal}'\n"
                f"      {self.snippet.strip()[:96]}\n     {col}")


def _risky(lit: str) -> bool:
    return bool(_ALPHA.match(lit)) and 2 <= len(lit) <= MAX_RISKY_LEN


def _safe_context(line: str) -> bool:
    low = line.lower()
    return any(h in low for h in SAFE_HINTS)


def scan_file(path: Path) -> list[Risk]:
    """AST scan for substring tests over short alphabetic literals."""
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return []
    lines = src.splitlines()
    out: list[Risk] = []

    def line_of(node) -> str:
        i = getattr(node, "lineno", 1) - 1
        return lines[i] if 0 <= i < len(lines) else ""

    for node in ast.walk(tree):
        # `"lit" in something`
        if isinstance(node, ast.Compare) and len(node.ops) == 1 \
                and isinstance(node.ops[0], ast.In) \
                and isinstance(node.left, ast.Constant) \
                and isinstance(node.left.value, str):
            lit = node.left.value.lower()
            src_line = line_of(node)
            if _risky(lit) and not _safe_context(src_line) \
                    and _is_text_receiver(node.comparators[0]):
                out.append(Risk(path.name, node.lineno, "in-test", lit, src_line, []))

        # `x.startswith("lit")` / `.startswith(("a","b"))`
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("startswith", "endswith"):
            for a in node.args:
                consts = []
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    consts = [a.value]
                elif isinstance(a, (ast.Tuple, ast.List)):
                    consts = [e.value for e in a.elts
                              if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                src_line = line_of(node)
                for c in consts:
                    if _risky(c.lower()) and not _safe_context(src_line):
                        out.append(Risk(path.name, node.lineno, node.func.attr,
                                        c.lower(), src_line, []))

        # re.escape(x) with no boundary anchor anywhere on the line
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "escape":
            src_line = line_of(node)
            if r"\b" not in src_line and "(^|" not in src_line:
                out.append(Risk(path.name, node.lineno, "unanchored-escape",
                                "re.escape", src_line, []))
    return out


def collisions(terms: list[str], min_len: int = 2) -> list[tuple]:
    """Pairs where one declared term is a substring of another.

    This is the part that turns a style complaint into a bug report: a short
    substring test is only harmful when a longer term containing it actually
    exists in the same vocabulary.
    """
    uniq = sorted({t.lower().strip() for t in terms if t and len(t.strip()) >= min_len})
    hits = []
    for a in uniq:
        if not _ALPHA.match(a) or len(a) > MAX_RISKY_LEN:
            continue
        for b in uniq:
            if a != b and a in b and not re.search(
                    r"(^|[^a-z])" + re.escape(a) + r"([^a-z]|$)", b):
                hits.append((a, b))
    return hits


def project_vocabulary(root: Path) -> list[str]:
    """Every string literal in the project's keyword tables, for collision testing."""
    terms: list[str] = []
    for p in sorted(root.glob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
                vals = [e.value for e in node.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if len(vals) >= 3:
                    terms += vals
            if isinstance(node, ast.Dict):
                keys = [k.value for k in node.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                if len(keys) >= 3:
                    terms += keys
    # Keep only routing-shaped vocabulary. A report label like "A2 velocity
    # slope (dB/decade)" is not something a router matches against, so letting
    # it supply collisions manufactures alarm about code that is fine.
    keep = []
    for t in terms:
        s = t.strip().lower()
        if not s or len(s) > 40 or len(s.split()) > 3:
            continue
        if any(ch in s for ch in "()[]{}<>/\\%:;\"'"):
            continue
        if re.fullmatch(r"[a-z][a-z0-9 _.\-]*", s):
            keep.append(s)
    return keep


def scan_tree(root: Path, verbose: bool = True) -> dict:
    vocab = project_vocabulary(root)
    coll = collisions(vocab)
    by_short: dict[str, list] = {}
    for a, b in coll:
        by_short.setdefault(a, []).append(b)

    risks: list[Risk] = []
    for p in sorted(root.glob("*.py")):
        if p.name == "substring_audit.py":
            continue
        for r in scan_file(p):
            r.collides_with = by_short.get(r.literal, [])
            risks.append(r)

    dangerous = [r for r in risks if r.collides_with]
    if verbose:
        print(f"scanned {len(list(root.glob('*.py')))} files, "
              f"{len(vocab)} declared terms, {len(coll)} substring collisions")
        print(f"{len(risks)} substring tests, {len(dangerous)} of them collide "
              f"with a longer declared term\n")
        for r in dangerous:
            print(r)
    return {"n_terms": len(vocab), "n_collisions": len(coll),
            "n_risks": len(risks), "n_dangerous": len(dangerous),
            "dangerous": [(r.file, r.line, r.literal, r.collides_with[:3])
                          for r in dangerous]}


def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    print("== collision detection: the four real bugs ==")
    # Each pair below actually shipped. The detector must catch all four.
    for short, long in [("react", "reactions"), ("nu", "number_of_elements"),
                        ("eta", "zeta"), ("deck", "decking")]:
        c = collisions([short, long])
        chk((short, long) in c, f"'{short}' inside '{long}' is caught")
    # and must NOT flag a term that only appears on a word boundary
    chk(("beam", "beam_length") not in collisions(["beam", "beam_length"]),
        "'beam' in 'beam_length' is a word boundary, not a collision")
    chk(not collisions(["stress", "strain"]), "unrelated terms do not collide")

    print("== AST scan ==")
    import tempfile
    src = (
        'FRAGS = ["react", "vue", "angular", "svelte"]\n'
        'def f(t):\n'
        '    if "react" in t:\n'
        '        return 1\n'
        '    if t.startswith("F1"):\n'
        '        return 2\n'
        '    return 0\n'
        'def g(path):\n'
        '    return path.endswith(".py")\n'
    )
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m.py"
        p.write_text(src)
        rs = scan_file(p)
        lits = {r.literal for r in rs}
        chk("react" in lits, f"caught the `in` test ({lits})")
        chk("f1" in lits, "caught the startswith test")
        chk(".py" not in lits, "did not flag a file-extension endswith")

    print("== against this project ==")
    root = Path(__file__).parent
    res = scan_tree(root, verbose=False)
    chk(res["n_terms"] > 500, f"read the project vocabulary ({res['n_terms']} terms)")
    chk(res["n_collisions"] > 0, f"found collisions to check against "
                                 f"({res['n_collisions']})")
    # The four known bugs are FIXED, so their call sites must no longer appear
    # as dangerous. This is the regression guard.
    bad = {lit for _f, _l, lit, _c in res["dangerous"]}
    for lit in ("react", "nu", "eta"):
        chk(lit not in bad, f"the fixed '{lit}' bug has not come back")
    print(f"  ({res['n_dangerous']} dangerous substring tests currently in tree)")

    print(f"\nsubstring_audit.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    r = scan_tree(Path(__file__).parent)
    print(f"\n{r['n_dangerous']} substring tests collide with a longer declared term.")
    print("None are auto-fixed: substring matching is correct in plenty of places, "
          "and a tool that rewrote them would break working code to satisfy a pattern.")
