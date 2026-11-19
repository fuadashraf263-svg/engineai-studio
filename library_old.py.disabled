#!/usr/bin/env python3
"""
library.py -- your engineering books, searchable, offline, with citations.

WHAT THIS IS
------------
Point it at a folder of PDFs. It extracts every page, splits into passages,
and builds a BM25 index in SQLite. Then any question can be answered from
YOUR books, quoting the book title and PAGE NUMBER.

Works with no internet, forever. There is no API call in this file and no
model download: BM25 is implemented here in about sixty lines rather than
pulled from a package, so the index cannot rot and cannot phone home.

THE RULE THAT MAKES IT TRUSTWORTHY
----------------------------------
Retrieval returns a SCORE. Below MIN_SCORE the honest answer is
"not in your library" -- and this returns nothing so the answer layer must
say so. A retrieval system that always returns its best guess turns a
library into a hallucination engine with footnotes, which is worse than
having no library at all: the citation makes the wrong answer credible.

Every passage carries (book, page). A claim you cannot trace to a page is
not an answer from your library, whatever it looks like.

ON WHAT TO PUT IN IT
--------------------
Most engineering textbooks are under copyright. The library works on
whatever PDFs you legitimately hold -- books you own, your own notes and
reports, manufacturer datasheets, and the very large body of free
technical literature: NASA NTRS, NIST publications, DTIC, open textbooks,
university course notes, and standards you are licensed for. That set
alone is bigger than any one engineer can read.

Usage
-----
    python library.py add ~/books              # ingest a folder (or a file)
    python library.py stats
    python library.py search "beam deflection cantilever" -k 5
    python library.py page "Shigley" 412       # show a specific page
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home() / "engineai"
DB = HOME / "library" / "library.db"

CHUNK_CHARS = 1200          # a passage a human would quote
CHUNK_OVERLAP = 200         # so a formula split across a boundary survives
K1, B = 1.5, 0.75           # standard BM25 parameters
# The floor was first set to 3.0 by guess and rejected a legitimate
# two-word query. A raw BM25 score is not comparable across queries -- it
# grows with the number of query terms and with corpus IDF. So: a LOW
# absolute floor to catch noise, plus COVERAGE (what fraction of the
# indexable query terms a passage actually matched), which is the honest
# discriminator and is reported so the answer layer can judge for itself.
MIN_SCORE = 1.5
MIN_COVERAGE = 0.34

G, R, Y, O = "\033[32m", "\033[31m", "\033[33m", "\033[0m"

# Engineering text is full of numbers, units and hyphenated terms. A naive
# \w+ tokenizer destroys "7075-T6", "1.5e-6" and "N/mm2".
# Decimal/exponent branch FIRST so "1.5e-6" is not split, then a general
# alphanumeric-with-hyphen/slash branch so "7075-T6", "Ti-6Al-4V", "C-276",
# "m/s" and "N/mm2" survive as single terms. The first draft anchored on
# [a-z]+ and silently shredded "7075-T6" into "7075","t","6" -- every
# aluminium designation in every book would have been unsearchable.
_TOK = re.compile(r"\d+\.\d+(?:e[-+]?\d+)?|[a-z0-9]+(?:[-/][a-z0-9]+)*")

_STOP = {"the", "and", "for", "with", "that", "this", "from", "are", "was",
         "can", "has", "have", "been", "which", "when", "where", "not",
         "but", "its", "into", "than", "then", "such", "these", "those",
         "also", "may", "will", "shall", "would", "could", "should"}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOK.findall(text.lower())
            if len(t) > 1 and t not in _STOP]


# ------------------------------------------------------------------- db ---

SCHEMA = """
CREATE TABLE IF NOT EXISTS books(
    id INTEGER PRIMARY KEY, path TEXT UNIQUE, title TEXT,
    n_pages INTEGER, sha TEXT, added_at REAL);
CREATE TABLE IF NOT EXISTS chunks(
    id INTEGER PRIMARY KEY, book_id INTEGER, page INTEGER,
    text TEXT, dl INTEGER);
CREATE TABLE IF NOT EXISTS postings(
    term TEXT, chunk_id INTEGER, tf INTEGER);
CREATE INDEX IF NOT EXISTS ix_post_term ON postings(term);
CREATE INDEX IF NOT EXISTS ix_chunk_book ON chunks(book_id);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)
    return c


def _set_meta(c, k, v):
    c.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, json.dumps(v)))


def _get_meta(c, k, default=None):
    r = c.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def _refresh_stats(c):
    n, tot = c.execute("SELECT COUNT(*), COALESCE(SUM(dl),0) "
                       "FROM chunks").fetchone()
    _set_meta(c, "N", n)
    _set_meta(c, "avgdl", (tot / n) if n else 0.0)


# -------------------------------------------------------------- ingest ----

def chunk_page(text: str):
    """Split one page into overlapping passages. Overlap matters: a formula
    and the sentence defining its symbols must not land in different
    chunks."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    if len(text) <= CHUNK_CHARS:
        return [text]
    out, i = [], 0
    while i < len(text):
        piece = text[i:i + CHUNK_CHARS]
        if i + CHUNK_CHARS < len(text):
            cut = max(piece.rfind(". "), piece.rfind("\n"))
            if cut > CHUNK_CHARS * 0.5:
                piece = piece[:cut + 1]
        out.append(piece.strip())
        i += max(len(piece) - CHUNK_OVERLAP, 1)
    return [p for p in out if p]


import signal as _signal


class _PageTimeout(Exception):
    pass


def _extract_one_page(pg, seconds=20):
    """Extract text from ONE page with a hard timeout. A malformed font table
    can make pypdf hang forever on a single page (the _cmap/parse_bfrange
    crash); without this guard one bad page freezes the whole ingest. On
    timeout or error the page is skipped, not fatal."""
    def _handler(signum, frame):
        raise _PageTimeout()
    old = None
    try:
        old = _signal.signal(_signal.SIGALRM, _handler)
        _signal.alarm(seconds)
    except (ValueError, AttributeError):
        old = None  # not on the main thread / not Unix -- no timeout, but try
    try:
        return pg.extract_text() or ""
    except Exception:
        return ""
    finally:
        if old is not None:
            try:
                _signal.alarm(0)
                _signal.signal(_signal.SIGALRM, old)
            except Exception:
                pass


def extract_pages(pdf: Path):
    """(page_number, text), 1-based. If ocr.py is present AND a page has no
    extractable text, OCR it. Each page has a hard timeout so a malformed
    font can't hang the whole ingest. Without ocr.py this is digital-only."""
    import warnings
    warnings.filterwarnings("ignore")          # silence pypdf object spam
    import logging
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        import ocr
        if ocr.engine_status()["can_ocr"]:
            for i, text, _src in ocr.extract_pages_with_ocr(pdf):
                yield i, text
            return
    except Exception:
        pass
    from pypdf import PdfReader
    try:
        r = PdfReader(str(pdf))
    except Exception as e:
        print(f"    (cannot open {pdf.name}: {type(e).__name__}) -- skipped")
        return
    n = len(r.pages)
    for i in range(n):
        try:
            pg = r.pages[i]
            text = _extract_one_page(pg, seconds=20)
        except _PageTimeout:
            print(f"    page {i+1}/{n} timed out (bad font) -- skipped")
            text = ""
        except Exception:
            text = ""
        yield i + 1, text


def add_book(c, pdf: Path, title: str | None = None) -> dict:
    sha = hashlib.sha256(pdf.read_bytes()[:2_000_000]).hexdigest()[:16]
    prev = c.execute("SELECT id, sha FROM books WHERE path=?",
                     (str(pdf),)).fetchone()
    if prev and prev[1] == sha:
        return {"skipped": True, "title": title or pdf.stem}
    if prev:                                   # changed file: re-ingest
        c.execute("DELETE FROM postings WHERE chunk_id IN "
                  "(SELECT id FROM chunks WHERE book_id=?)", (prev[0],))
        c.execute("DELETE FROM chunks WHERE book_id=?", (prev[0],))
        c.execute("DELETE FROM books WHERE id=?", (prev[0],))

    title = title or pdf.stem.replace("_", " ")
    pages = list(extract_pages(pdf))
    cur = c.execute("INSERT INTO books(path,title,n_pages,sha,added_at) "
                    "VALUES (?,?,?,?,?)",
                    (str(pdf), title, len(pages), sha, time.time()))
    bid = cur.lastrowid

    n_chunks = n_empty = 0
    for page, text in pages:
        if not text.strip():
            n_empty += 1
            continue
        for piece in chunk_page(text):
            toks = tokenize(piece)
            if not toks:
                continue
            cid = c.execute(
                "INSERT INTO chunks(book_id,page,text,dl) VALUES (?,?,?,?)",
                (bid, page, piece, len(toks))).lastrowid
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            c.executemany("INSERT INTO postings VALUES (?,?,?)",
                          [(t, cid, f) for t, f in tf.items()])
            n_chunks += 1
    return {"title": title, "pages": len(pages), "chunks": n_chunks,
            "empty_pages": n_empty, "skipped": False}


def ingest(target: Path) -> int:
    c = connect()
    pdfs = ([target] if target.is_file() and target.suffix.lower() == ".pdf"
            else sorted(target.rglob("*.pdf")))
    if not pdfs:
        print(f"  no PDFs under {target}")
        return 1
    print(f"  {len(pdfs)} PDF(s) under {target}\n")
    for p in pdfs:
        try:
            r = add_book(c, p)
        except Exception as exc:
            print(f"  {R}FAIL{O}  {p.name}: {type(exc).__name__}: "
                  f"{str(exc)[:70]}")
            continue
        if r["skipped"]:
            print(f"  {Y}  ..{O}  {r['title'][:52]:54s} unchanged")
        else:
            warn = ""
            if r["pages"] and r["empty_pages"] / r["pages"] > 0.5:
                warn = (f"   {Y}! {r['empty_pages']}/{r['pages']} pages have "
                        f"no text -- likely a SCANNED book, needs OCR{O}")
            print(f"  {G}  ok{O}  {r['title'][:52]:54s} "
                  f"{r['pages']:5d} pages  {r['chunks']:6d} passages{warn}")
        c.commit()
    _refresh_stats(c)
    c.commit()
    stats()
    return 0


# -------------------------------------------------------------- search ----

def search(query: str, k: int = 5, min_score: float = MIN_SCORE):
    """BM25. Returns [] when nothing clears the floor -- that is a real
    answer ('not in your library'), not a failure."""
    c = connect()
    N = _get_meta(c, "N", 0)
    avgdl = _get_meta(c, "avgdl", 0.0) or 1.0
    if not N:
        return []
    qt = tokenize(query)
    if not qt:
        return []

    scores: dict[int, float] = {}
    matched: dict[int, set] = {}
    for term in set(qt):
        # JOIN, do not loop. The first version issued one
        # "SELECT dl FROM chunks WHERE id=?" per posting row -- an N+1 query
        # that made search time LINEAR in the size of the whole library.
        # Measured: 0.034 s at 2 books, 0.557 s at 44 books, and minutes at
        # the thousand-book scale this is meant to reach. One JOIN fixes it.
        rows = c.execute(
            "SELECT p.chunk_id, p.tf, ch.dl FROM postings p "
            "JOIN chunks ch ON ch.id = p.chunk_id WHERE p.term = ?",
            (term,)).fetchall()
        df = len(rows)
        if df == 0:
            continue
        idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
        for cid, tf, dl in rows:
            denom = tf + K1 * (1 - B + B * dl / avgdl)
            scores[cid] = scores.get(cid, 0.0) + idf * tf * (K1 + 1) / denom
            matched.setdefault(cid, set()).add(term)

    # how many query terms exist ANYWHERE in the library? Terms the library
    # has never seen cannot be held against a passage.
    uq = sorted(set(qt))
    present = {r[0] for r in c.execute(
        "SELECT DISTINCT term FROM postings WHERE term IN "
        f"({','.join('?' * len(uq))})", uq)} if uq else set()
    n_present = max(len(present), 1)

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:k * 5]
    out = []
    for cid, sc in ranked:
        cov = len(matched[cid] & present) / n_present
        if sc < min_score or cov < MIN_COVERAGE:
            continue
        page, text, bid = c.execute(
            "SELECT page, text, book_id FROM chunks WHERE id=?",
            (cid,)).fetchone()
        title = c.execute("SELECT title FROM books WHERE id=?",
                          (bid,)).fetchone()[0]
        out.append({"book": title, "page": page, "score": round(sc, 3),
                    "coverage": round(cov, 3),
                    "query_terms_in_library": sorted(present),
                    "query_terms_absent": sorted(set(qt) - present),
                    "matched_terms": sorted(matched[cid]), "text": text,
                    "provenance": "LIBRARY"})
        if len(out) >= k:
            break
    return out


def get_page(book_query: str, page: int):
    c = connect()
    row = c.execute("SELECT id, title FROM books WHERE title LIKE ?",
                    (f"%{book_query}%",)).fetchone()
    if not row:
        return None
    rows = c.execute("SELECT text FROM chunks WHERE book_id=? AND page=? "
                     "ORDER BY id", (row[0], page)).fetchall()
    return {"book": row[1], "page": page,
            "text": "\n".join(r[0] for r in rows)} if rows else None


def stats() -> int:
    c = connect()
    nb, = c.execute("SELECT COUNT(*) FROM books").fetchone()
    nc, = c.execute("SELECT COUNT(*) FROM chunks").fetchone()
    npg, = c.execute("SELECT COALESCE(SUM(n_pages),0) FROM books").fetchone()
    nt, = c.execute("SELECT COUNT(DISTINCT term) FROM postings").fetchone()
    print(f"\n  LIBRARY  {DB}")
    print(f"    books        {nb}")
    print(f"    pages        {npg}")
    print(f"    passages     {nc}")
    print(f"    vocabulary   {nt} distinct terms")
    print(f"    avg passage  {_get_meta(c,'avgdl',0):.0f} tokens")
    if nb:
        print("\n    titles:")
        for t, p in c.execute("SELECT title, n_pages FROM books "
                              "ORDER BY title"):
            print(f"      {t[:58]:60s} {p:5d} pages")
    print()
    return 0


# ------------------------------------------------------------ selftest ----

def selftest() -> int:
    """Build a tiny library from known text and check the RANKING."""
    global DB
    import tempfile
    fails = 0
    with tempfile.TemporaryDirectory() as td:
        DB = Path(td) / "t.db"
        c = connect()

        docs = {
            "Roark Formulas": [
                (100, "Cantilever beam with end load. The maximum "
                      "deflection at the free end is delta = P L cubed "
                      "divided by three E I. Slope at the tip is P L "
                      "squared over two E I."),
                (101, "Simply supported beam central point load gives "
                      "deflection P L cubed over forty eight E I.")],
            "Crane TP-410 Flow of Fluids": [
                (55, "The Darcy Weisbach equation gives head loss as f "
                     "times L over D times V squared over two g. The "
                     "friction factor is read from the Moody diagram."),
                (56, "Reynolds number determines the flow regime. Below "
                     "2300 the flow is laminar and f equals 64 over "
                     "Reynolds number.")],
            "REBCO Tape Handbook": [
                (12, "The Hastelloy C276 substrate carries the REBCO "
                     "layer. Substrate magnetisation is the dominant AC "
                     "loss mechanism at low frequency.")],
        }
        bid_of = {}
        for title, pages in docs.items():
            cur = c.execute("INSERT INTO books(path,title,n_pages,sha,"
                            "added_at) VALUES (?,?,?,?,?)",
                            (title, title, len(pages), "x", 0.0))
            bid_of[title] = cur.lastrowid
            for page, text in pages:
                toks = tokenize(text)
                cid = c.execute("INSERT INTO chunks(book_id,page,text,dl) "
                                "VALUES (?,?,?,?)",
                                (cur.lastrowid, page, text,
                                 len(toks))).lastrowid
                tf = {}
                for t in toks:
                    tf[t] = tf.get(t, 0) + 1
                c.executemany("INSERT INTO postings VALUES (?,?,?)",
                              [(t, cid, f) for t, f in tf.items()])
        _refresh_stats(c)
        c.commit()

        print("\n== LIBRARY SELF-TEST " + "=" * 45)
        print("  3 books, 5 pages, known content\n")

        def expect(query, book, page, note=""):
            nonlocal fails
            r = search(query, k=3)
            if not r:
                print(f"  {R}FAIL{O}  {query[:40]:42s} -> nothing returned")
                fails += 1
                return
            top = r[0]
            good = top["book"] == book and top["page"] == page
            fails += (not good)
            print(f"  {(G+'PASS'+O) if good else (R+'FAIL'+O)}  "
                  f"{query[:40]:42s} -> {top['book'][:22]:24s} p{top['page']} "
                  f"score {top['score']:6.2f}  {note}")

        expect("cantilever end load deflection", "Roark Formulas", 100)
        expect("simply supported central load", "Roark Formulas", 101)
        expect("Darcy Weisbach head loss friction", 
               "Crane TP-410 Flow of Fluids", 55)
        expect("laminar Reynolds 2300", "Crane TP-410 Flow of Fluids", 56)
        expect("Hastelloy substrate AC loss", "REBCO Tape Handbook", 12)

        print()
        # the floor must hold
        for q in ("photosynthesis chlorophyll", "quarterly revenue forecast"):
            r = search(q, k=3)
            good = not r
            fails += (not good)
            print(f"  {(G+'PASS'+O) if good else (R+'FAIL'+O)}  "
                  f"off-topic refused: {q[:34]:36s} -> "
                  f"{len(r)} result(s)")

        # citations must be complete
        r = search("cantilever deflection", k=1)
        has = r and all(k in r[0] for k in
                        ("book", "page", "score", "coverage", "text",
                         "query_terms_absent"))
        fails += (not has)
        print(f"  {(G+'PASS'+O) if has else (R+'FAIL'+O)}  "
              f"every hit carries book + page + score + text")

        # chunking must not lose text
        long_page = "Sentence number %d about stress and strain. " % 0 * 200
        pieces = chunk_page(long_page)
        joined = "".join(pieces)
        ok_len = len(joined) >= len(long_page.strip())
        fails += (not ok_len)
        print(f"  {(G+'PASS'+O) if ok_len else (R+'FAIL'+O)}  "
              f"chunking loses no text ({len(pieces)} passages, overlap kept)")

        # tokenizer must survive engineering notation
        toks = tokenize("Al 7075-T6 at 1.5e-6 m/s and N/mm2")
        keep = {"7075-t6", "1.5e-6", "m/s", "n/mm2"}
        got = keep & set(toks)
        fails += (got != keep)
        print(f"  {(G+'PASS'+O) if got==keep else (R+'FAIL'+O)}  "
              f"tokenizer keeps engineering notation: {sorted(got)}")

    print(f"\n  {'ALL CHECKS PASS' if not fails else str(fails)+' FAILURE(S)'}")
    return 1 if fails else 0


# ----------------------------------------------------------------- cli ----

def main() -> int:
    a = sys.argv[1:]
    if not a:
        print(__doc__.split("Usage")[-1])
        return 0
    if a[0] == "--selftest":
        return selftest()
    if a[0] == "add" and len(a) > 1:
        return ingest(Path(a[1]).expanduser())
    if a[0] == "stats":
        return stats()
    if a[0] == "page" and len(a) > 2:
        r = get_page(a[1], int(a[2]))
        if not r:
            print("  not found")
            return 1
        print(f"\n  {r['book']}, page {r['page']}\n  " + "-" * 60)
        print("  " + r["text"].replace("\n", "\n  "))
        return 0
    if a[0] == "search":
        k = 5
        if "-k" in a:
            k = int(a[a.index("-k") + 1])
            a = a[:a.index("-k")]
        q = " ".join(a[1:])
        hits = search(q, k=k)
        if not hits:
            print(f"\n  NOT IN YOUR LIBRARY -- nothing scored above "
                  f"{MIN_SCORE}.")
            print("  Add the relevant book, or answer from handbook.py "
                  "instead.\n")
            return 1
        print(f"\n  {len(hits)} passage(s) for: {q}\n")
        for h in hits:
            print(f"  [{h['book']}, p.{h['page']}]  score {h['score']}"
                  f"  matched {h['matched_terms']}")
            body = h["text"][:400].replace("\n", " ")
            print(f"    {body}{'...' if len(h['text'])>400 else ''}\n")
        return 0
    print("commands: add <path> | search <query> [-k N] | page <book> <n> "
          "| stats | --selftest")
    return 1


if __name__ == "__main__":
    sys.exit(main())
