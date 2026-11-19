#!/usr/bin/env python3
"""
pre_push.py - refuse to publish secrets. Run before every push.

A leaked API key on GitHub is scraped within minutes, and rewriting history
afterwards does not un-leak it. This checks what git is ACTUALLY tracking, not
what .gitignore claims, and scans the staged content for key-shaped strings.

  python pre_push.py            check, and print the push commands if clean
"""
import os, re, sys, subprocess

EA = os.path.expanduser(os.environ.get("ENGINEAI_DIR", "~/engineai"))

NEVER = ["/.env", "/.owner.json", "audit.log", "/.keypool.json", "id_rsa",
         ".pem", ".key", "credentials", "secrets", "/.last_part.json"]

HARD_LIMIT = 100 * 1024 * 1024      # GitHub rejects anything over this
WARN_LIMIT = 25 * 1024 * 1024       # and complains above this
BULKY = (".sh", ".run", ".bin", ".exe", ".msi", ".deb", ".rpm", ".appimage",
         ".pdf", ".stl", ".step", ".stp", ".zip", ".tar", ".gz", ".7z", ".rar",
         ".db", ".sqlite", ".gguf", ".bin", ".pt", ".safetensors", ".iso")
PATTERNS = [
 (r"sk-or-v?1?-[A-Za-z0-9_\-]{20,}", "OpenRouter key"),
 (r"sk-ant-[A-Za-z0-9_\-]{20,}",     "Anthropic key"),
 (r"\bsk-[A-Za-z0-9]{32,}",          "OpenAI-style key"),
 (r"gsk_[A-Za-z0-9]{30,}",           "Groq key"),
 (r"\bAIza[0-9A-Za-z_\-]{30,}",      "Google API key"),
 (r"ghp_[A-Za-z0-9]{30,}",           "GitHub token"),
 (r"github_pat_[A-Za-z0-9_]{50,}",   "GitHub fine-grained token"),
 (r"csk-[A-Za-z0-9]{20,}",           "Cerebras key"),
 (r"\b[0-9a-f]{32}\b(?=.*(?:api|key|token|secret))", "32-hex secret"),
]

def git(*a):
    return subprocess.run(["git", "-C", EA, *a], capture_output=True, text=True).stdout

def main():
    if not os.path.isdir(os.path.join(EA, ".git")):
        sys.exit(f"\n  no git repository at {EA}\n  run: cd {EA} && git init\n")
    tracked = [f for f in git("ls-files").splitlines() if f.strip()]
    print(f"\n  {len(tracked)} file(s) tracked by git\n")

    bad = []
    for f in tracked:
        for n in NEVER:
            if f.endswith(n.lstrip("/")) or ("/" + f).endswith(n):
                bad.append((f, "must never be committed")); break
    # size first: this is the failure that actually stops a push
    big, warn, total = [], [], 0
    for f in tracked:
        p = os.path.join(EA, f)
        if not os.path.exists(p): continue
        n = os.path.getsize(p); total += n
        if n > HARD_LIMIT: big.append((f, n))
        elif n > WARN_LIMIT: warn.append((f, n))

    hits = []
    for f in tracked:
        p = os.path.join(EA, f)
        if not os.path.exists(p) or os.path.getsize(p) > 3_000_000: continue
        try: txt = open(p, errors="ignore").read()
        except Exception: continue
        for pat, what in PATTERNS:
            for m in re.finditer(pat, txt):
                s = m.group(0)
                if "..." in s or "xxx" in s.lower() or s.count("*") > 3: continue
                hits.append((f, what, s[:12] + "..." + s[-4:]))

    ign = os.path.join(EA, ".gitignore")
    missing = []
    if os.path.exists(ign):
        g = open(ign).read()
        for n in (".env", ".owner.json", "audit.log", ".keypool.json", "*.db",
                  "*.sh", "*.pdf", "*.stl", "out/"):
            if n not in g: missing.append(n)
    else:
        missing = ["no .gitignore at all"]

    if big:
        print("  REFUSING TO PUSH — GitHub rejects any file over 100 MB:")
        for f, n in sorted(big, key=lambda x: -x[1]):
            print(f"    {n/1048576:8.1f} MB  {f}")
        print("\n  Untrack them (the files stay on disk):")
        for f, _n in big: print(f"    git -C {EA} rm --cached '{f}'")
        print()
    if warn:
        print(f"  large but allowed ({len(warn)} file(s) over 25 MB):")
        for f, n in sorted(warn, key=lambda x: -x[1])[:8]:
            print(f"    {n/1048576:8.1f} MB  {f}")
        print()
    if total > 200*1024*1024:
        print(f"  the repository is {total/1048576:,.0f} MB. A code repo should be a few MB.")
        print("  Data, books, models and generated output do not belong in git.\n")
    if bad:
        print("  REFUSING TO PUSH — these are tracked and must not be:")
        for f, why in bad: print(f"    {f:44s} {why}")
        print("\n  Remove them from git (the file stays on disk):")
        for f, _ in bad: print(f"    git -C {EA} rm --cached '{f}'")
        print()
    if hits:
        print("  REFUSING TO PUSH — key-shaped strings inside tracked files:")
        for f, what, s in hits[:12]: print(f"    {f:40s} {what:22s} {s}")
        print("\n  Move them to ~/engineai/.env (which is ignored) and remove from the file.\n")
    if missing:
        print("  .gitignore is missing entries: " + ", ".join(missing))
        print(f"    cp gitignore.recommended {EA}/.gitignore   # shipped alongside this script\n")

    if bad or hits or big:
        sys.exit(1)
    print("  no secrets found in tracked files.\n")
    if missing:
        print("  fix .gitignore first, then re-run.\n"); sys.exit(1)
    print("  safe to push:")
    print(f"    git -C {EA} add -A && git -C {EA} commit -m 'EngineAI'")
    print(f"    git -C {EA} push -u origin main\n")

if __name__ == "__main__":
    main()
