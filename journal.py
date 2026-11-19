#!/usr/bin/env python3
"""
journal.py - your beta-testing record.

You are the first tester, so the thing that matters is not "did it run" but
"what did I ask, what came back, and was it right". Every question through the
studio is recorded with its route, the tools used, whether anything was assumed
or unverified, and how long it took. You mark verdicts afterwards.

  python journal.py                 recent sessions
  python journal.py stats           what is working and what is not
  python journal.py mark 14 bad "gave 27 mm, should be 2.7 mm"
  python journal.py open            list entries you flagged, oldest first
  python journal.py --selftest
"""
import os, sys, json, time, sqlite3, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.expanduser(os.environ.get("ENGINEAI_JOURNAL",
                                       os.path.join(HERE, "journal.db")))

def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS runs(
      id INTEGER PRIMARY KEY, ts TEXT, question TEXT, route TEXT, tools TEXT,
      answer TEXT, evidence TEXT, files INT, seconds REAL,
      assumed INT DEFAULT 0, unverified INT DEFAULT 0, failed INT DEFAULT 0,
      verdict TEXT, note TEXT);
    """)
    return c

def record(question, route, tools, answer, evidence, files=0, seconds=0.0,
           assumed=False, unverified=False, failed=False):
    c = db()
    c.execute("""INSERT INTO runs(ts,question,route,tools,answer,evidence,files,seconds,
                 assumed,unverified,failed) VALUES(datetime('now'),?,?,?,?,?,?,?,?,?,?)""",
              (question[:400], route, ",".join(tools)[:300], str(answer)[:400],
               str(evidence)[:400], files, round(seconds, 2),
               int(assumed), int(unverified), int(failed)))
    c.commit()
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]

def recent(n=15):
    c = db()
    rows = c.execute("""SELECT id,ts,question,route,answer,files,seconds,assumed,unverified,
                        failed,verdict FROM runs ORDER BY id DESC LIMIT ?""", (n,)).fetchall()
    if not rows: print("\n  nothing recorded yet — ask the studio something\n"); return
    print(f"\n  {'id':>4s} {'when':8s} {'route':9s} {'q':44s} {'flags':7s} {'v':4s} answer")
    print("  " + "-"*112)
    for i, ts, q, route, ans, nf, sec, asu, unv, fail, verdict in rows:
        fl = ("A" if asu else ".") + ("U" if unv else ".") + ("X" if fail else ".") + \
             (f"{nf}f" if nf else "  ")
        print(f"  {i:4d} {ts[11:19]:8s} {(route or '?')[:9]:9s} {q[:44]:44s} {fl:7s} "
              f"{(verdict or '-')[:4]:4s} {str(ans)[:34]}")
    print("\n  flags: A assumed a value · U unverified · X failed · Nf files produced")
    print("  mark one:  python journal.py mark <id> good|bad|unsure \"note\"\n")

def stats():
    c = db()
    tot = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    if not tot: print("\n  nothing recorded yet\n"); return
    print(f"\n  {tot} question(s) asked\n")
    print("  BY ROUTE")
    for route, n, f, sec in c.execute("""SELECT route,COUNT(*),SUM(failed),AVG(seconds)
                                         FROM runs GROUP BY route ORDER BY 2 DESC"""):
        print(f"    {(route or '?'):12s} {n:4d} runs   {f or 0:2d} failed   "
              f"{sec or 0:5.1f}s average")
    print("\n  QUALITY")
    for label, sql in (("answers with an assumed value", "assumed=1"),
                       ("answers marked unverified", "unverified=1"),
                       ("runs that failed", "failed=1"),
                       ("runs that produced files", "files>0")):
        n = c.execute(f"SELECT COUNT(*) FROM runs WHERE {sql}").fetchone()[0]
        print(f"    {label:34s} {n:4d}  ({100*n/tot:4.0f}%)")
    print("\n  YOUR VERDICTS")
    v = dict(c.execute("SELECT COALESCE(verdict,'unmarked'),COUNT(*) FROM runs GROUP BY 1"))
    for k in ("good", "bad", "unsure", "unmarked"):
        if v.get(k): print(f"    {k:34s} {v[k]:4d}")
    bad = c.execute("SELECT id,question,note FROM runs WHERE verdict='bad' ORDER BY id DESC "
                    "LIMIT 6").fetchall()
    if bad:
        print("\n  MOST RECENT PROBLEMS")
        for i, q, note in bad: print(f"    #{i:<4d} {q[:52]:54s} {note or ''}")
    print()

def mark(rid, verdict, note=""):
    c = db()
    if not c.execute("SELECT 1 FROM runs WHERE id=?", (rid,)).fetchone():
        sys.exit(f"no run #{rid}")
    c.execute("UPDATE runs SET verdict=?, note=? WHERE id=?", (verdict, note, rid)); c.commit()
    print(f"  #{rid} marked {verdict}" + (f": {note}" if note else ""))

def open_items():
    c = db()
    rows = c.execute("""SELECT id,ts,question,note FROM runs
                        WHERE verdict IN ('bad','unsure') ORDER BY id""").fetchall()
    if not rows: print("\n  nothing flagged\n"); return
    print(f"\n  {len(rows)} open item(s), oldest first\n")
    for i, ts, q, note in rows:
        print(f"  #{i:<4d} {ts[:16]}  {q[:60]}")
        if note: print(f"        {note}")
    print()

def selftest():
    global DB
    ok = True
    DB = "/tmp/_journal_test.db"
    if os.path.exists(DB): os.remove(DB)
    print("\nJOURNAL SELFTEST")
    a = record("cantilever deflection", "direct", ["compute"], "1.728 mm", "Roark",
               files=4, seconds=1.2, assumed=True)
    b = record("what is 3+4", "model", ["final_answer"], "7", "", unverified=True)
    record("broken thing", "model", [], "", "", failed=True)
    c = db()
    n = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    print(f"  {'ok  ' if n==3 else 'FAIL'} recorded {n} runs"); ok &= n == 3
    r = c.execute("SELECT assumed,files,route FROM runs WHERE id=?", (a,)).fetchone()
    good = r == (1, 4, "direct")
    print(f"  {'ok  ' if good else 'FAIL'} flags stored: assumed={r[0]} files={r[1]} route={r[2]}")
    ok &= good
    mark(a, "good", "matches hand calc")
    mark(b, "bad", "should not answer arithmetic")
    v = dict(c.execute("SELECT verdict,COUNT(*) FROM runs WHERE verdict IS NOT NULL GROUP BY 1"))
    good = v.get("good") == 1 and v.get("bad") == 1
    print(f"  {'ok  ' if good else 'FAIL'} verdicts recorded: {v}"); ok &= good
    rows = c.execute("SELECT id FROM runs WHERE verdict IN ('bad','unsure')").fetchall()
    print(f"  {'ok  ' if len(rows)==1 else 'FAIL'} open items surfaced: {len(rows)}")
    ok &= len(rows) == 1
    print("\n  the stats view:")
    stats()
    os.remove(DB)
    print("journal selftest:", "PASSED" if ok else "FAILED")
    return ok

if __name__ == "__main__":
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("cmd", nargs="?", default="recent",
                    choices=["recent", "stats", "mark", "open"])
    ap.add_argument("args", nargs="*"); ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    if a.cmd == "recent": recent(int(a.args[0]) if a.args else 15)
    elif a.cmd == "stats": stats()
    elif a.cmd == "open": open_items()
    elif a.cmd == "mark":
        if len(a.args) < 2: sys.exit('usage: journal.py mark <id> good|bad|unsure "note"')
        mark(int(a.args[0]), a.args[1], " ".join(a.args[2:]))
