#!/usr/bin/env python3
"""
fetch_papers.py -- legally bulk-download free scientific papers.

WHAT THIS DOES
--------------
This is the part of "download all the data" that CAN be automated, because
these sources are openly licensed. It pulls engineering papers from:

  * arXiv   -- physics/engineering preprints, official bulk API
  * NASA NTRS -- public-domain technical reports

into ~/books/papers, ready for `python library.py add ~/books`. Copyrighted
textbooks are NOT here and cannot be -- see LIBRARY_MANIFEST.md.

It is polite to the APIs (rate-limited, batched) so it cannot get you
blocked. Start with a topic you care about; run it again for more.

Run:  python fetch_papers.py --arxiv "superconducting motor" --max 50
      python fetch_papers.py --arxiv "additive manufacturing lattice" --max 100
      python fetch_papers.py --topics   # download a default engineering set
"""

from __future__ import annotations

import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

BOOKS = Path.home() / "engineai" / "books" / "papers"
ARXIV_API = "http://export.arxiv.org/api/query"

# a sensible default engineering spread -- edit freely
DEFAULT_TOPICS = [
    # --- MECHANICS / STRUCTURES (library gap: only 1 book) ---
    "beam bending deflection analysis",
    "euler buckling column stability",
    "finite element method structural analysis",
    "stress concentration fatigue",
    "vibration modal analysis structures",
    # --- FLUIDS (library gap: 0 books) ---
    "computational fluid dynamics turbulence",
    "pipe flow friction pressure drop",
    "boundary layer aerodynamics",
    "navier stokes numerical solution",
    # --- THERMAL / HEAT (library gap: 0 books) ---
    "heat transfer convection conduction",
    "heat exchanger design analysis",
    "thermal management electronics cooling",
    # --- MATERIALS (library gap: 0 books) ---
    "mechanical behavior materials fatigue fracture",
    "additive manufacturing lattice structure",
    "composite materials mechanics",
    # --- CONTROL / DYNAMICS ---
    "control systems robust design",
    "state space feedback control",
    # --- YOUR FRONTIER PROJECTS ---
    "superconducting REBCO magnet AC loss",
    "gas turbine cooling film",
    "rotordynamics critical speed bearing",
    "eVTOL aircraft propulsion design",
]


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent":
                                "EngineAI-Library/1.0 (research use)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_arxiv(query: str, max_results: int = 50) -> int:
    """Download PDFs of the top arXiv hits for a query. Returns count."""
    BOOKS.mkdir(parents=True, exist_ok=True)
    got = 0
    per_page = min(max_results, 50)
    for start in range(0, max_results, per_page):
        params = {
            "search_query": f"all:{query}",
            "start": start, "max_results": per_page,
            "sortBy": "relevance", "sortOrder": "descending",
        }
        url = ARXIV_API + "?" + urllib.parse.urlencode(params)
        try:
            xml = _get(url)
        except Exception as e:
            print(f"  arXiv query failed: {e}")
            break
        ns = {"a": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(xml)
        entries = root.findall("a:entry", ns)
        if not entries:
            break
        for e in entries:
            title = (e.findtext("a:title", "", ns) or "").strip()
            title = " ".join(title.split())[:80]
            pdf = None
            for link in e.findall("a:link", ns):
                if link.get("title") == "pdf":
                    pdf = link.get("href")
            if not pdf:
                aid = (e.findtext("a:id", "", ns) or "").rsplit("/", 1)[-1]
                pdf = f"https://arxiv.org/pdf/{aid}"
            safe = "".join(c if c.isalnum() or c in " -_" else "_"
                           for c in title)[:70].strip() or "paper"
            dest = BOOKS / f"arxiv_{safe}.pdf"
            if dest.exists():
                print(f"  .. {safe[:50]} (have)")
                continue
            try:
                data = _get(pdf + ".pdf" if not pdf.endswith(".pdf") else pdf,
                            timeout=60)
                dest.write_bytes(data)
                got += 1
                print(f"  ok {safe[:50]}  ({len(data)//1024} KB)")
            except Exception as ex:
                print(f"  FAIL {safe[:40]}: {str(ex)[:40]}")
            time.sleep(3)          # be polite to arXiv
        time.sleep(3)
    return got


def main() -> int:
    a = sys.argv[1:]
    if not a or a[0] == "--help":
        print(__doc__.split("Run:")[-1])
        return 0

    total = 0
    if a[0] == "--arxiv" and len(a) > 1:
        mx = 50
        if "--max" in a:
            mx = int(a[a.index("--max") + 1])
        query = a[1]
        print(f"  arXiv: '{query}', up to {mx} papers -> {BOOKS}\n")
        total = fetch_arxiv(query, mx)
    elif a[0] == "--topics":
        print(f"  downloading default engineering topic set -> {BOOKS}\n")
        for t in DEFAULT_TOPICS:
            print(f"\n  [{t}]")
            total += fetch_arxiv(t, 20)
    else:
        print("usage: --arxiv \"query\" [--max N] | --topics")
        return 1

    print(f"\n  downloaded {total} new paper(s).")
    print("  next:  python library.py add ~/books")
    print("  (they'll be searchable with title + page citations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
