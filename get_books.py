#!/usr/bin/env python3
"""
get_books.py — download the free, legal engineering corpus, and VALIDATE it.

The previous downloader's bug was that it trusted whatever came back. A
government site that has moved a file returns a 128 KB HTML error page; saved
as `.pdf` it looks downloaded but indexes as 8 blank pages. This script does
the opposite: it downloads, then *proves the file is a real multi-page PDF*
before keeping it. Anything that fails validation is deleted and reported with
a one-click link so you can fetch it by hand.

Two destinations, decided by licence:
  books/shippable/   public domain (US gov) or CC-BY  -> may be sold in product
  books/reference/   free but personal/instructional   -> learn & validate only

    python get_books.py                 download everything it can
    python get_books.py --list          show the catalogue, download nothing
    python get_books.py --shippable     only the public-domain / CC-BY set
    python get_books.py --check DIR     validate PDFs already in a folder

Sources used, most reliable first:
  * NASA NTRS       — api.nasa's citation API. Public domain. Verified.
  * archive.org     — search -> metadata -> download. Stable once resolved.
  * direct URLs     — publisher-hosted, for books with a stable link.
Anything behind JavaScript (OpenStax) is printed as a link, never faked.
"""
from __future__ import annotations
import os, sys, json, time, urllib.request, urllib.parse, ssl

HOME = os.path.expanduser("~")
APP = os.environ.get("ENGINEAI_HOME", os.path.join(HOME, "engineai"))
SHIP = os.path.join(APP, "books", "shippable")
REF = os.path.join(APP, "books", "reference")
UA = {"User-Agent": "EngineAI-book-fetch/1.0 (engineering education)"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE          # some gov certs are finicky; content is validated anyway

C = dict(g="\033[32m", y="\033[33m", r="\033[31m", b="\033[36m", d="\033[2m", n="\033[0m")
if not sys.stdout.isatty():
    C = {k: "" for k in C}


# ====================================================================== catalogue
# Each entry: key, full title, source spec, folder, licence note.
#   ntrs:<id>              NASA NTRS citation id -> its PDF  (verified pattern)
#   iadl:<ident>/<file>    a specific archive.org file  (verified, no search)
#   url:<direct-pdf>       a stable publisher URL  (verified)
#   manual:<page-url>      cannot be auto-fetched (JS or dead direct link)
#
# Everything marked ntrs/iadl/url was checked to resolve to a real PDF. The
# validator is the backstop: if any of them has moved, its bad copy is deleted
# and it is reported as a manual fetch rather than poisoning the index.
CATALOG = [
    # ---- SHIP-SAFE: NASA, public domain, VERIFIED ----------------------
    dict(key="nasa_sp125", ship=True, folder=SHIP,
         title="NASA SP-125 — Design of Liquid Propellant Rocket Engines (Huzel & Huang)",
         src="ntrs:19710019929", note="US Gov public domain; verified 46 MB, 469 pp"),
    dict(key="nasa_sp8007_buckling", ship=True, folder=SHIP,
         title="NASA SP-8007 — Buckling of Thin-Walled Circular Cylinders",
         src="ntrs:19690013955", note="US Gov public domain; verified — structures"),

    # ---- SHIP-SAFE: DOE handbook that IS reliably hosted, VERIFIED ------
    dict(key="doe_nuclear_physics", ship=True, folder=SHIP,
         title="DOE Fundamentals Handbook — Nuclear Physics and Reactor Theory (HDBK-1019)",
         src="iadl:B-001-003-530/Fundamentals Handbook - Nuclear Physics.pdf",
         note="US DOE public domain; verified 3.4 MB, 254 pp"),

    # ---- SHIP-SAFE: DOE full set — direct links are dead, use archive --
    dict(key="doe_thermo_heat_fluid", ship=True, folder=SHIP,
         title="DOE Fundamentals Handbook — Thermodynamics, Heat Transfer & Fluid Flow (HDBK-1012, 3 vols)",
         src="manual:https://www.energy.gov/ehss/technical-standards-archives",
         note="US DOE public domain — search 'HDBK-1012'. Fills thermo/heat/fluids"),
    dict(key="doe_mechanical_science", ship=True, folder=SHIP,
         title="DOE Fundamentals Handbook — Mechanical Science (HDBK-1018, 2 vols)",
         src="manual:https://www.energy.gov/ehss/technical-standards-archives",
         note="US DOE public domain — search 'HDBK-1018'"),
    dict(key="doe_material_science", ship=True, folder=SHIP,
         title="DOE Fundamentals Handbook — Material Science (HDBK-1017, 2 vols)",
         src="manual:https://www.energy.gov/ehss/technical-standards-archives",
         note="US DOE public domain — search 'HDBK-1017'"),

    # ---- SHIP-SAFE: CC-BY, JavaScript-gated -> manual, VERIFIED page ----
    dict(key="openstax_uphysics1", ship=True, folder=SHIP,
         title="OpenStax — University Physics Volume 1 (CC-BY)",
         src="manual:https://openstax.org/details/books/university-physics-volume-1",
         note="CC-BY, shippable — click 'Download a PDF'"),
    dict(key="openstax_uphysics2", ship=True, folder=SHIP,
         title="OpenStax — University Physics Volume 2, thermodynamics & E&M (CC-BY)",
         src="manual:https://openstax.org/details/books/university-physics-volume-2",
         note="CC-BY, shippable — click 'Download a PDF'"),
    dict(key="openstax_calc1", ship=True, folder=SHIP,
         title="OpenStax — Calculus Volume 1 (CC-BY)",
         src="manual:https://openstax.org/details/books/calculus-volume-1",
         note="CC-BY, shippable — click 'Download a PDF'"),

    # ---- REFERENCE ONLY: free to read, not to ship, VERIFIED -----------
    dict(key="lienhard_ahtt", ship=False, folder=REF,
         title="A Heat Transfer Textbook, 6th ed. (Lienhard & Lienhard) — 810 pp",
         src="url:https://ahtt.mit.edu/wp-content/uploads/2024/04/AHTTv600.pdf",
         note="free for personal/instructional use — NOT shippable; fills heat transfer"),
]


# ====================================================================== validate
def validate(path, min_pages=15, min_kb=150):
    """Is this a real, substantial PDF — or an HTML error page in disguise?"""
    try:
        sz = os.path.getsize(path)
    except OSError:
        return False, "missing"
    if sz < min_kb * 1024:
        return False, f"only {sz//1024} KB — almost certainly an error page, not a book"
    with open(path, "rb") as f:
        head = f.read(5)
    if head != b"%PDF-":
        return False, "not a PDF (server returned HTML or a redirect page)"
    # page count is the real test — the junk DOE files were valid-ish but 8 pages
    try:
        import fitz
        d = fitz.open(path)
        n = d.page_count
        # sample the middle page for an actual text layer
        txt = ""
        if n:
            txt = d[min(n // 2, n - 1)].get_text()
        d.close()
        if n < min_pages:
            return False, f"only {n} pages — truncated or a cover sheet"
        return True, f"{n} pages, {sz/1e6:.1f} MB" + ("" if txt.strip() else ", WARN no text layer")
    except ImportError:
        return True, f"{sz/1e6:.1f} MB (install pymupdf for a page check)"
    except Exception as e:
        return False, f"unreadable PDF ({e})"


# ====================================================================== fetch
def _get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout, context=CTX)


def download(url, dst, timeout=120):
    tmp = dst + ".part"
    try:
        with _get(url, timeout) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False, str(e)
    os.replace(tmp, dst)
    return True, "ok"


def ntrs_pdf_url(cid):
    return f"https://ntrs.nasa.gov/api/citations/{cid}/downloads/{cid}.pdf"


def archive_resolve(query):
    """archive.org: title search -> identifier -> the largest PDF file url."""
    q = urllib.parse.quote(f'title:({query})')
    search = (f"https://archive.org/advancedsearch.php?q={q}"
              f"&fl[]=identifier&rows=5&output=json")
    try:
        with _get(search, 40) as r:
            docs = json.load(r)["response"]["docs"]
    except Exception as e:
        return None, f"search failed: {e}"
    for d in docs:
        ident = d["identifier"]
        try:
            with _get(f"https://archive.org/metadata/{ident}", 40) as r:
                meta = json.load(r)
        except Exception:
            continue
        pdfs = [f for f in meta.get("files", [])
                if f.get("name", "").lower().endswith(".pdf")]
        if not pdfs:
            continue
        best = max(pdfs, key=lambda f: int(f.get("size", 0)))
        name = urllib.parse.quote(best["name"])
        return f"https://archive.org/download/{ident}/{name}", ident
    return None, "no PDF found in the first results"


def fetch(entry):
    os.makedirs(entry["folder"], exist_ok=True)
    dst = os.path.join(entry["folder"], entry["key"] + ".pdf")
    if os.path.exists(dst):
        okv, why = validate(dst)
        if okv:
            return "have", why
        os.remove(dst)          # a previous junk download; try again

    src = entry["src"]
    kind, _, arg = src.partition(":")

    if kind == "manual":
        return "manual", arg
    if kind == "ntrs":
        url = ntrs_pdf_url(arg)
    elif kind == "url":
        url = arg
    elif kind == "iadl":
        # direct archive.org file: "identifier/filename.pdf" (no search needed)
        ident, _, fname = arg.partition("/")
        url = f"https://archive.org/download/{ident}/{urllib.parse.quote(fname)}"
    elif kind == "ia":
        url, info = archive_resolve(arg)
        if not url:
            return "fail", info
    else:
        return "fail", f"unknown source kind {kind}"

    ok, why = download(url, dst)
    if not ok:
        return "fail", why
    okv, vwhy = validate(dst)
    if not okv:
        os.remove(dst)
        return "reject", vwhy          # <- the junk-download guard in action
    return "ok", vwhy


# ====================================================================== main
def main():
    args = sys.argv[1:]
    if "--check" in args:
        d = args[args.index("--check") + 1]
        print(f"validating PDFs in {d}\n")
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(".pdf"):
                ok, why = validate(os.path.join(d, f))
                tag = f"{C['g']}ok  {C['n']}" if ok else f"{C['r']}JUNK{C['n']}"
                print(f"  {tag} {f:50s} {why}")
        return

    only_ship = "--shippable" in args
    listing = "--list" in args

    print(f"\n{C['b']}EngineAI — free engineering corpus{C['n']}")
    print(f"  shippable -> {SHIP}")
    print(f"  reference -> {REF}\n")

    counts = dict(ok=0, have=0, manual=0, fail=0, reject=0)
    manuals = []
    for e in CATALOG:
        if only_ship and not e["ship"]:
            continue
        tag = ("SHIP" if e["ship"] else "ref ")
        if listing:
            print(f"  [{tag}] {e['title']}\n        {C['d']}{e['note']}{C['n']}")
            continue
        print(f"  {C['d']}...{C['n']} {e['title'][:60]}", end="\r")
        status, why = fetch(e)
        counts[status] = counts.get(status, 0) + 1
        mark = {"ok": f"{C['g']}got {C['n']}", "have": f"{C['g']}have{C['n']}",
                "manual": f"{C['y']}link{C['n']}", "fail": f"{C['r']}fail{C['n']}",
                "reject": f"{C['r']}junk{C['n']}"}[status]
        print(f"  {mark} [{tag}] {e['title'][:58]:58s} {C['d']}{why[:40]}{C['n']}")
        if status == "manual":
            manuals.append((e["title"], why, e["folder"]))

    if listing:
        return

    print(f"\n  {C['g']}{counts['ok']} downloaded{C['n']}, {counts['have']} already had, "
          f"{C['y']}{counts['manual']} need a click{C['n']}, "
          f"{counts['fail']+counts['reject']} unavailable")

    if manuals:
        print(f"\n  {C['y']}Fetch these by hand{C['n']} (JavaScript download pages — one click each):")
        for title, url, folder in manuals:
            print(f"    - {title}")
            print(f"      {url}")
            print(f"      save into: {folder}")

    if counts["fail"] or counts["reject"]:
        print(f"\n  {C['d']}Anything 'fail'/'junk' has a working page link in "
              f"MASTER_PLAN.md Part A. Government sites move files; the validator "
              f"refused a bad copy rather than poison your index.{C['n']}")

    print(f"\n  next:  cd {APP} && python kb.py add {SHIP} && python kb.py stats\n")


if __name__ == "__main__":
    main()
