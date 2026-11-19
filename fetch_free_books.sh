#!/usr/bin/env bash
# fetch_free_books.sh -- download the LEGALLY FREE engineering corpus.
#
# WHAT THIS DOES AND DOES NOT DO
# ------------------------------
# It downloads ONLY openly-licensed or public-domain / US-government works:
# NASA, NIST, DTIC, open textbooks, and university open courseware. These
# are free to download and redistribute. This script contains NO source for
# copyrighted textbooks (Shigley, Hibbeler, Incropera, etc). Those you buy
# and place in ~/books yourself; library.py will index them alongside these.
#
# It is deliberately CONSERVATIVE: it fetches curated, known-good documents
# rather than scraping, so it cannot pull in junk or break a site's terms.
# Extend the URL lists with your own legally-obtained material.
#
# Run:  bash fetch_free_books.sh
#       bash fetch_free_books.sh --dry-run     # list what it would fetch
#
# After it finishes:  python library.py add ~/books

set -u
BOOKS="${HOME}/books"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
mkdir -p "$BOOKS"/{nasa,nist,opentext,ocw,standards,your_own}

GRN='\033[32m'; YEL='\033[33m'; RED='\033[31m'; OFF='\033[0m'
ok(){ echo -e "${GRN}  ok${OFF}  $1"; }
skip(){ echo -e "${YEL}  ..${OFF}  $1"; }
err(){ echo -e "${RED}FAIL${OFF}  $1"; }

get(){  # get <url> <dest-subdir> <filename>
  local url="$1" sub="$2" fn="$3"
  local dest="$BOOKS/$sub/$fn"
  if [ -s "$dest" ]; then skip "$fn (already have)"; return; fi
  if [ "$DRY" = "1" ]; then echo "  would fetch: $sub/$fn"; return; fi
  if curl -fSL --retry 3 --connect-timeout 20 -o "$dest" "$url" 2>/dev/null \
     && [ -s "$dest" ]; then
      ok "$fn ($(du -h "$dest" | cut -f1))"
  else
      err "$fn  <- source may have moved; skip and continue"
      rm -f "$dest"
  fi
}

echo "=================================================================="
echo "  FREE engineering corpus -> $BOOKS"
echo "  (openly licensed / public-domain / US-government only)"
echo "=================================================================="

# ---- NASA technical reports (public domain) --------------------------
# NASA NTRS hosts hundreds of thousands of free reports. A few anchors:
echo; echo "[NASA NTRS]"
get "https://ntrs.nasa.gov/api/citations/19930090976/downloads/19930090976.pdf" \
    nasa "NASA_Theory_of_Wing_Sections.pdf"
get "https://ntrs.nasa.gov/api/citations/19980237070/downloads/19980237070.pdf" \
    nasa "NASA_Structural_Analysis_Guide.pdf"
skip "browse https://ntrs.nasa.gov and drop any report PDF into $BOOKS/nasa"

# ---- NIST (US-government, free) --------------------------------------
echo; echo "[NIST]"
skip "NIST handbooks: https://www.nist.gov/publications -- free PDFs"
skip "  e.g. NIST/SEMATECH e-Handbook of Statistical Methods"

# ---- Open textbooks (Creative Commons) -------------------------------
echo; echo "[OPEN TEXTBOOKS -- Creative Commons]"
skip "OpenStax (openstax.org): University Physics, Calculus vols 1-3 -- CC"
skip "  download the PDFs and place in $BOOKS/opentext"
skip "LibreTexts Engineering (eng.libretexts.org): full CC engineering set"
skip "Engineering Mechanics / Statics & Dynamics open texts"

# ---- MIT / open courseware notes -------------------------------------
echo; echo "[OPEN COURSEWARE]"
skip "MIT OCW (ocw.mit.edu): lecture notes for 2.001, 2.006, 6.002, 16.xx"
skip "  many courses publish full PDF notes under CC-BY-NC-SA"

# ---- DTIC (US Defense Technical Information Center, cleared docs) -----
echo; echo "[DTIC -- cleared, public]"
skip "DTIC (discover.dtic.mil): free cleared technical reports"

echo
echo "=================================================================="
echo "  NEXT STEPS"
echo "  1. The skips above are SOURCES, not failures -- visit each, grab"
echo "     the PDFs you want, drop them in the matching ~/books subfolder."
echo "  2. Put books you have PURCHASED in $BOOKS/your_own"
echo "  3. Then:  python library.py add ~/books"
echo "=================================================================="
echo
echo "  For a curated list of exactly which books to get, see"
echo "  BOOK_LIST.md (shipped alongside this script)."
