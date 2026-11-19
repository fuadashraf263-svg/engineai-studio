#!/usr/bin/env python3
"""
ocr.py -- read SCANNED books, so your uploaded labs stop being invisible.

THE PROBLEM THIS SOLVES
-----------------------
Your Lab_3_2-merged.pdf extracts zero text through pypdf -- it is a scanned
image, not born-digital. library.py silently indexed nothing from it. Every
scanned book you own has the same fate until this runs.

HOW IT BEHAVES
--------------
  * It OCRs ONLY the pages pypdf could not read. A born-digital page is
    never re-rendered -- OCR is ~1-3 seconds per page and pointless on text
    that already extracted cleanly.
  * If tesseract is not installed it prints how to get it and SKIPS. It
    never crashes an ingest. A missing OCR engine degrades to "no text from
    scanned pages", not to a stack trace.
  * It reports, per book, how many pages were digital vs OCR'd vs blank, so
    you know exactly what got in.

WHY NOT OCR EVERYTHING
----------------------
Because most engineering books you buy new ARE born-digital, and OCR on a
600-page book is 15-30 minutes for no benefit. Selective OCR is the
difference between a library that ingests in minutes and one that takes a
day. The rule is the same as everywhere else in this system: do the
expensive thing only when the cheap thing failed, and measure the result.

Run:  python ocr.py --check                    # is the engine present?
      python ocr.py page scanned.pdf 3         # OCR one page, show text
      python ocr.py scan ~/books/scanned.pdf   # OCR a whole file, report

Then re-ingest:  python library.py add ~/books   (library.py calls this)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

MIN_CHARS_DIGITAL = 40      # a real text page has more than this; below it,
                            # treat as scanned and try OCR
OCR_DPI = 300               # 300 is the tesseract sweet spot for text


def engine_status() -> dict:
    """What is available, and what is missing."""
    tess = shutil.which("tesseract")
    have_pytess = have_pdf2image = have_fitz = False
    try:
        import pytesseract  # noqa
        have_pytess = True
    except ImportError:
        pass
    try:
        import fitz  # noqa   (pymupdf: pure-python render, no poppler)
        have_fitz = True
    except ImportError:
        pass
    try:
        import pdf2image  # noqa
        have_pdf2image = True
    except ImportError:
        pass
    can = bool(tess) and have_pytess and (have_fitz or have_pdf2image)
    return {"tesseract": bool(tess), "pytesseract": have_pytess,
            "pymupdf": have_fitz, "pdf2image": have_pdf2image,
            "can_ocr": can}


def _how_to_install(st: dict) -> str:
    tips = []
    if not st["tesseract"]:
        tips.append("sudo apt install -y tesseract-ocr")
    if not st["pytesseract"]:
        tips.append("pip install pytesseract")
    if not (st["pymupdf"] or st["pdf2image"]):
        tips.append("pip install pymupdf   (or: pip install pdf2image "
                    "&& sudo apt install -y poppler-utils)")
    return "  " + "\n  ".join(tips) if tips else "  (all present)"


def _render_page(pdf: Path, page_index0: int):
    """Return a PIL image of one page. Prefer pymupdf (no system dep),
    fall back to pdf2image+poppler."""
    try:
        import fitz
        doc = fitz.open(str(pdf))
        pg = doc.load_page(page_index0)
        zoom = OCR_DPI / 72.0
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        from PIL import Image
        import io
        return Image.open(io.BytesIO(pix.tobytes("png")))
    except ImportError:
        from pdf2image import convert_from_path
        imgs = convert_from_path(str(pdf), dpi=OCR_DPI,
                                 first_page=page_index0 + 1,
                                 last_page=page_index0 + 1)
        return imgs[0] if imgs else None


def ocr_page(pdf: Path, page_number1: int) -> str:
    """OCR a single 1-based page. Empty string on any failure."""
    st = engine_status()
    if not st["can_ocr"]:
        return ""
    try:
        import pytesseract
        img = _render_page(pdf, page_number1 - 1)
        if img is None:
            return ""
        return pytesseract.image_to_string(img) or ""
    except Exception:
        return ""


def extract_pages_with_ocr(pdf: Path):
    """Yield (page_number, text, source) where source is 'digital' or
    'ocr' or 'blank'. This is the function library.py uses in place of its
    digital-only extractor when OCR is available."""
    import warnings, logging
    warnings.filterwarnings("ignore")
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    import signal as _sig
    from pypdf import PdfReader
    try:
        r = PdfReader(str(pdf))
    except Exception:
        return
    st = engine_status()
    def _to(s, f): raise TimeoutError()
    for i, pg in enumerate(r.pages, start=1):
        try:
            try:
                old = _sig.signal(_sig.SIGALRM, _to); _sig.alarm(20)
            except Exception:
                old = None
            try:
                txt = pg.extract_text() or ""
            finally:
                if old is not None:
                    try: _sig.alarm(0); _sig.signal(_sig.SIGALRM, old)
                    except Exception: pass
        except Exception:
            txt = ""
        if len(txt.strip()) >= MIN_CHARS_DIGITAL:
            yield i, txt, "digital"
        elif st["can_ocr"]:
            otext = ocr_page(pdf, i)
            yield i, otext, ("ocr" if otext.strip() else "blank")
        else:
            yield i, txt, "blank"


def scan_report(pdf: Path) -> dict:
    counts = {"digital": 0, "ocr": 0, "blank": 0}
    total_chars = 0
    for _, text, src in extract_pages_with_ocr(pdf):
        counts[src] += 1
        total_chars += len(text)
    return {"file": pdf.name, **counts, "chars": total_chars}


def main() -> int:
    a = sys.argv[1:]
    st = engine_status()

    if not a or a[0] == "--check":
        print("\n  OCR engine status:")
        for k in ("tesseract", "pytesseract", "pymupdf", "pdf2image"):
            print(f"    {k:14s} {'present' if st[k] else 'MISSING'}")
        print(f"\n  can OCR: {st['can_ocr']}")
        if not st["can_ocr"]:
            print("\n  to enable, install:")
            print(_how_to_install(st))
        else:
            print("  ready -- scanned pages will be read on ingest.")
        return 0 if st["can_ocr"] else 1

    if not st["can_ocr"]:
        print("  OCR engine not available. Install:")
        print(_how_to_install(st))
        return 1

    if a[0] == "page" and len(a) > 2:
        text = ocr_page(Path(a[1]).expanduser(), int(a[2]))
        if not text.strip():
            print("  (no text recovered from that page)")
            return 1
        print(f"\n  OCR of {a[1]} page {a[2]}:\n  " + "-" * 58)
        print("  " + text.replace("\n", "\n  ")[:2000])
        return 0

    if a[0] == "scan" and len(a) > 1:
        pdf = Path(a[1]).expanduser()
        print(f"  OCR-scanning {pdf.name} (only non-digital pages) ...")
        r = scan_report(pdf)
        print(f"\n  {r['digital']} digital, {r['ocr']} OCR'd, "
              f"{r['blank']} blank, {r['chars']} chars total")
        if r["ocr"]:
            print("  -> re-run: python library.py add ~/books   to index these")
        return 0

    print("commands: --check | page <pdf> <n> | scan <pdf>")
    return 1


if __name__ == "__main__":
    sys.exit(main())
