#!/usr/bin/env python3
"""
kb.py - EngineAI knowledge base. Self-contained, tolerant PDF ingester + BM25 search.

Fixes every known library.py bug at once:
  * uses PyMuPDF (tolerant) instead of pypdf (strict)  -> no PdfStreamError
  * dedupes by content fingerprint                     -> no double-adding
  * strips surrogates                                  -> no UnicodeEncodeError
  * detects hollow/zero-filled PDFs and skips them     -> no silent empty books
  * BM25 ranking via SQLite FTS5                       -> no custom postings table

Usage:
  python kb.py add <folder-or-pdf> [more...]   ingest PDFs (recursive)
  python kb.py stats                            what's in the library
  python kb.py search "cantilever deflection"   BM25 search, cited results
  python kb.py doctor <folder>                  triage PDFs without ingesting
"""
import os, sys, re, sqlite3, hashlib, argparse, time

DB = os.environ.get("ENGINEAI_KB", os.path.expanduser("~/engineai/library/kb.db"))

def _fitz():
    try:
        import fitz; fitz.TOOLS.mupdf_display_errors(False); return fitz
    except ImportError:
        sys.exit("PyMuPDF missing.  pip install pymupdf")

def clean(t: str) -> str:
    """Strip surrogates and control junk that crash sqlite/print."""
    t = t.encode("utf-8", "replace").decode("utf-8", "replace")
    t = t.replace("\x00", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", t)

def fingerprint(path: str) -> str:
    """Fast content fingerprint: size + head + tail. Catches copies and re-adds."""
    sz = os.path.getsize(path)
    h = hashlib.sha256(str(sz).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        if sz > 2 << 20:
            f.seek(-(1 << 20), os.SEEK_END); h.update(f.read())
    return h.hexdigest()

def connect():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS docs(
      id INTEGER PRIMARY KEY, fp TEXT UNIQUE, path TEXT, title TEXT,
      pages INT, chunks INT, added TEXT, field TEXT DEFAULT 'unknown');
    CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
      body, doc_id UNINDEXED, page UNINDEXED, tokenize='porter unicode61');
    """)
    try: c.execute("ALTER TABLE docs ADD COLUMN field TEXT DEFAULT 'unknown'")
    except sqlite3.OperationalError: pass          # already migrated
    return c

def triage(path, fitz):
    """Return (pages, text_ratio, note). text_ratio<=0.3 means hollow/scanned."""
    try:
        d = fitz.open(path)
    except Exception as e:
        return 0, 0.0, f"unreadable ({type(e).__name__})"
    n = d.page_count
    if n == 0:
        d.close(); return 0, 0.0, "zero pages"
    step = max(1, n // 25)
    hits = tot = 0
    for i in range(int(n * .1), max(int(n * .9), 1), step):
        if len(d[i].get_text().strip()) > 100: hits += 1
        tot += 1
    d.close()
    r = hits / max(1, tot)
    note = "ok" if r > .3 else ("hollow - no text layer (blank or scanned)" if r < .05 else "sparse text")
    return n, r, note

# ---------------------------------------------------------------- fields
ENG = ("stress","strain","beam","shaft","bending","torsion","thermodynamic","entropy",
       "enthalpy","fluid","viscosity","reynolds","laminar","turbulent","heat transfer",
       "conduction","convection","modulus","yield strength","alloy","microstructure",
       "buckling","moment of inertia","shear force","kinematics","statics","dynamics",
       "deflection","truss","bearing","gear","weld","fatigue","boiler","turbine",
       "nusselt","specific heat","kpa","mpa","kn/m","free body diagram")
PROG = ("javascript","python","java ","docker","kubernetes","react","django","flutter",
        "vue","git ","html","css","npm","api endpoint","function(","const ","import react",
        "compiler","refactor","unit test","repository","container","framework","async",
        "database query","source code","programmer","developpeur","langage")

# titles that decide the question on their own, whatever the body text says
PROG_TITLE = ("javascript","java ","-java-","python","docker","kubernetes","react","django",
              "flutter","vue","nuxt","git","linux","command line","design pattern","agile",
              "microservice","cheat-sheet","cheatsheet","programm","coding","coder","software",
              "web ","css","html","node","typescript","php","ruby","rails","devops","scrum",
              "leadership","approximation algorithms","algorithmes","algorithmique","pyqt",
              "succinctly","for kids","clean code","refactor","es6","ecmascript","for dummies",
              "jquery","angular","spring boot","kotlin","swift ","golang","rust lang")
ENG_TITLE  = ("mechanics of materials","strength of materials","machine design","mechanical engineering",
              "thermodynamic","heat and mass","heat transfer","fluid mechanics","fluid dynamics",
              "materials science","structural analysis","statics","dynamics","vector mechanics",
              "elasticity","instrumentation","circuit theory","electronic devices","machine element",
              "manufacturing","metallurgy","aerodynam","combustion","turbomachin","vibration",
              "control system","differential equations","engineering heat","applied elasticity",
              "boundary value","numerical methods","engineering mathematics","mech of materials",
              "stat and mech","strength of material","solid mechanics","machine elements",
              "engineering drawing","fluid power","refrigeration","internal combustion")

def classify(title, sample):
    """engineering / programming / other. Title markers decide first, because
    'Design Patterns in Java' and 'Agile Software Development' both look like
    engineering to a naive keyword count."""
    t = re.sub(r"\s+", " ", re.sub(r"[-_.]+", " ", title.lower())).strip()   # normalise separators
    s = sample.lower()
    for k in PROG_TITLE:
        if k in t: return "programming"
    for k in ENG_TITLE:
        if k in t: return "engineering"
    e = sum(s.count(k) for k in ENG)
    p = sum(s.count(k) for k in PROG)
    if e < 20 and p < 20: return "other"          # too little signal to claim either
    if e >= 25 and e > p*1.4: return "engineering"
    if p >= 25 and p > e*1.4: return "programming"
    return "engineering" if e > p*1.2 else ("programming" if p > e*1.2 else "other")

def sample_text(path, fitz, n=14):
    d = fitz.open(path); tot = d.page_count; out = []
    for i in range(int(tot*.15), int(tot*.85), max(1, tot//n)):
        out.append(d[i].get_text())
    d.close(); return clean(" ".join(out))[:60000]

def add(paths):
    fitz = _fitz(); c = connect()
    pdfs = []
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            for root, _, fs in os.walk(p):
                pdfs += [os.path.join(root, f) for f in fs if f.lower().endswith(".pdf")]
        elif p.lower().endswith(".pdf"):
            pdfs.append(p)
    pdfs.sort()
    print(f"{len(pdfs)} PDF(s) found\n")
    added = skipped = failed = 0
    for p in pdfs:
        name = os.path.splitext(os.path.basename(p))[0][:58]
        try:
            fp = fingerprint(p)
        except Exception as e:
            print(f"  FAIL     {name}  ({type(e).__name__})"); failed += 1; continue
        if c.execute("SELECT 1 FROM docs WHERE fp=?", (fp,)).fetchone():
            print(f"  dup      {name}"); skipped += 1; continue
        pages, ratio, note = triage(p, fitz)
        if note != "ok":
            print(f"  SKIP     {name}  <- {note}"); skipped += 1; continue
        t0 = time.time()
        try:
            d = fitz.open(p)
            field = classify(name, sample_text(p, fitz))
            cur = c.execute(
                "INSERT INTO docs(fp,path,title,pages,chunks,added,field) "
                "VALUES(?,?,?,?,0,datetime('now'),?)", (fp, p, name, pages, field))
            did = cur.lastrowid
            rows = []
            for i in range(pages):
                txt = clean(d[i].get_text())
                if len(txt.strip()) < 40: continue
                # one chunk per page keeps citations exact and honest
                rows.append((txt, did, i + 1))
            d.close()
            c.executemany("INSERT INTO chunks(body,doc_id,page) VALUES(?,?,?)", rows)
            c.execute("UPDATE docs SET chunks=? WHERE id=?", (len(rows), did))
            c.commit()
            print(f"  added    {name}  ({pages}p, {len(rows)} chunks, {field}, {time.time()-t0:.1f}s)")
            added += 1
        except Exception as e:
            c.rollback(); print(f"  FAIL     {name}  ({type(e).__name__}: {str(e)[:40]})"); failed += 1
    print(f"\nadded {added}   skipped {skipped}   failed {failed}")
    stats()

def stats():
    c = connect()
    nb, np_, nc = c.execute(
        "SELECT COUNT(*), COALESCE(SUM(pages),0), COALESCE(SUM(chunks),0) FROM docs").fetchone()
    size = os.path.getsize(DB) / 1e6 if os.path.exists(DB) else 0
    print(f"\nLIBRARY  {DB}\n  books {nb}   pages {np_}   chunks {nc}   size {size:.0f} MB")
    for f, n2, p2 in c.execute("SELECT field,COUNT(*),SUM(pages) FROM docs GROUP BY field"):
        print(f"    {f or 'unknown':14s} {n2:3d} books  {p2 or 0:6d} pages")
    for t, p, f in c.execute("SELECT title,pages,field FROM docs ORDER BY field,title"):
        print(f"    {(f or '?')[:11]:12s} {t[:52]:54s} {p:5d}p")

FLOOR = float(os.environ.get("ENGINEAI_KB_FLOOR", "9.0"))   # min |bm25| to cite
STOP = set("""a an the of in on at to for with from by is are was were be been being am
this that these those it its as and or but if then than so such not no nor can could
will would shall should may might must do does did done have has had having i you he
she we they me him her them my your his their our what which who whom whose when where
why how all any both each few more most other some only own same too very just about
into over under again further once here there also does what's you're i'm let us get
give show tell make take want need know think see use using used way thing things""".split())

def search(query, k=5, quiet=False, field="engineering", floor=None):
    """Cite only when the match is strong AND from the right kind of book.
    field=None searches everything."""
    c = connect()
    floor = FLOOR if floor is None else floor
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9_]+", query.lower())
             if w not in STOP and len(w) > 2]
    # a question made only of common words ("who are you") has no content to match;
    # matching it anyway is how a preface page ends up cited as engineering fact
    if not words: return []
    q = " OR ".join(f'"{w}"' for w in words)
    try:
        rows = c.execute("""
          SELECT d.title, x.page, x.s, x.score, x.body FROM (
            SELECT doc_id, page, body,
                   snippet(chunks,0,'>>','<<','...',18) AS s,
                   bm25(chunks) AS score
            FROM chunks WHERE chunks MATCH ? ORDER BY score LIMIT ?
          ) x JOIN docs d ON d.id = x.doc_id
          WHERE (? IS NULL OR d.field = ?) ORDER BY x.score""",
          (q, max(k*8, 40), field, field)).fetchall()
        rows = [r for r in rows if -r[3] >= floor][:k]
    except sqlite3.OperationalError as e:
        print("query error:", e); return []
    if not quiet:
        for t, pg, s, sc, _ in rows:
            print(f"\n  [{-sc:5.1f}] {t[:58]}  p.{pg}\n      {clean(s)[:220]}")
        if not rows: print(f"  no match above the relevance floor ({floor}) - EngineAI should say it does not know")
    return rows

def reclassify():
    """Label the books already in the db. No re-indexing, seconds not minutes."""
    fitz = _fitz(); c = connect()
    rows = c.execute("SELECT id,path,title FROM docs").fetchall()
    n = {"engineering": 0, "programming": 0, "other": 0}
    for i, path, title in rows:
        try:
            f = classify(title, sample_text(path, fitz)) if os.path.exists(path) \
                else classify(title, "")
        except Exception:
            f = classify(title, "")
        c.execute("UPDATE docs SET field=? WHERE id=?", (f, i)); n[f] = n.get(f, 0) + 1
        print(f"  {f:12s} {title[:58]}")
    c.commit()
    print("\n  " + "   ".join(f"{k}: {v}" for k, v in n.items()))
    print("  searches now cite engineering books only.")

def remove(patterns, field=None, apply=False):
    """Drop documents (and their FTS chunks) by title substring or by field.

    A dry run by default — it prints what it *would* remove — because deleting
    from an index you spent an hour building deserves a confirmation.

        python kb.py remove "DOE-HDBK-1012-1"     # what would go (dry run)
        python kb.py remove "DOE-HDBK-1012-1" --apply
        python kb.py remove --field programming --apply   # purge a whole field
    """
    c = connect()
    where, args = [], []
    for p in patterns:
        where.append("title LIKE ?"); args.append(f"%{p}%")
    if field:
        where.append("field = ?"); args.append(field)
    if not where:
        print("  nothing specified. Give a title substring or --field <name>."); return
    clause = " OR ".join(where) if not field or patterns else " AND ".join(where)
    rows = c.execute(f"SELECT id,title,pages,field FROM docs WHERE {clause}", args).fetchall()
    if not rows:
        print("  no matching documents."); return
    print(f"\n  {'REMOVING' if apply else 'would remove'} {len(rows)} document(s):")
    for did, title, pages, f in rows:
        print(f"    [{f or '?':11s}] {title[:56]:58s} {pages:5d}p")
    if not apply:
        print("\n  dry run — re-run with --apply to delete.")
        return
    ids = [r[0] for r in rows]
    q = ",".join("?" * len(ids))
    c.execute(f"DELETE FROM chunks WHERE doc_id IN ({q})", ids)
    c.execute(f"DELETE FROM docs   WHERE id     IN ({q})", ids)
    c.commit()
    print(f"\n  removed {len(ids)} document(s). Run: python kb.py stats")


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("cmd", choices=["add", "stats", "search", "doctor", "reclassify", "remove"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--field", default=None, help="for remove: match a whole field")
    ap.add_argument("--apply", action="store_true", help="for remove: actually delete")
    a = ap.parse_args()
    if a.cmd == "add": add(a.args or ["~/books"])
    elif a.cmd == "stats": stats()
    elif a.cmd == "reclassify": reclassify()
    elif a.cmd == "remove": remove(a.args, field=a.field, apply=a.apply)
    elif a.cmd == "search": search(" ".join(a.args))
    elif a.cmd == "doctor":
        fitz = _fitz()
        for root in (a.args or ["~/books"]):
            for dp, _, fs in os.walk(os.path.expanduser(root)):
                for f in sorted(fs):
                    if f.lower().endswith(".pdf"):
                        p = os.path.join(dp, f)
                        n, r, note = triage(p, fitz)
                        tag = "OK  " if note == "ok" else "BAD "
                        print(f"{tag} {n:5d}p {r*100:3.0f}% text  {note:42s} {f[:50]}")

if __name__ == "__main__":
    main()
