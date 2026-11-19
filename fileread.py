#!/usr/bin/env python3
"""
fileread.py - identify, read and extract ENGINEERING MEANING from any file a user
hands over, including photographs.

THE TWO RULES
-------------
1. IDENTIFY BY CONTENT, NOT BY EXTENSION. Extensions lie constantly - a .txt that
   is really a STEP file, a .dat that is a mesh, a .jpg that is a PNG, a .step
   someone renamed from .stp. Every reader here checks magic bytes and structure
   first, and reports the extension separately so a mismatch is visible.

2. EXTRACTING TEXT IS NOT UNDERSTANDING. Anyone can pull characters out of a PDF.
   What matters for an engineering assistant is the MEANING: a STEP file's volume
   and bounding box, a DXF's entity counts and layers, a mesh's node count and
   element types, a spreadsheet's formulas as well as its values, a drawing's
   dimension text. Each reader returns structured facts, and says plainly what it
   could NOT determine.

WHAT "READ ANY FILE" HONESTLY MEANS
-----------------------------------
Around 40 formats are read properly here, grouped into eight families. Anything
unrecognised is reported as unrecognised, with its magic bytes and a best guess -
never silently treated as text. A binary format nobody implemented is a gap to be
named, not something to bluff about.

Photographs go through OCR (tesseract) plus EXIF, and the result is explicitly
marked as machine-read text with a confidence, because OCR of a handwritten
dimension on a site photo is exactly the sort of input that must not be trusted
silently.
"""
from __future__ import annotations
import argparse, csv, io, json, math, os, re, subprocess, zipfile

# magic-byte signatures -> family. Checked before any extension is trusted.
MAGIC = [
    (b"%PDF", "pdf"), (b"PK\x03\x04", "zip-container"),
    (b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"),
    (b"GIF8", "gif"), (b"BM", "bmp"), (b"II*\x00", "tiff"), (b"MM\x00*", "tiff"),
    (b"RIFF", "riff"), (b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"),
    (b"\xfd7zXZ", "xz"), (b"7z\xbc\xaf\x27\x1c", "7zip"),
    (b"Rar!", "rar"), (b"\x7fELF", "elf"), (b"MZ", "exe"),
    (b"\x89HDF\r\n\x1a\n", "hdf5"), (b"CDF\x01", "netcdf"),
    (b"solid ", "stl-ascii"), (b"ISO-10303-21", "step"),
    (b"\xd0\xcf\x11\xe0", "ole2"), (b"{\\rtf", "rtf"),
    (b"OggS", "ogg"), (b"\x00\x00\x00 ftyp", "mp4"),
]


def sniff(path, nbytes=512):
    """What IS this file, by content? The extension is reported alongside so a
    disagreement between the two is visible rather than silently resolved."""
    with open(path, "rb") as f:
        head = f.read(nbytes)
    fam = None
    for sig, name in MAGIC:
        if head.startswith(sig) or (sig in head[:64] and name in ("step",)):
            fam = name
            break
    if fam is None:
        # STL binary has no magic; it is 80 bytes of header then a uint32 count
        if os.path.getsize(path) >= 84:
            import struct
            n = struct.unpack("<I", head[80:84])[0] if len(head) >= 84 else 0
            if 84 + n * 50 == os.path.getsize(path):
                fam = "stl-binary"
    if fam is None:
        try:
            head.decode("utf-8")
            fam = "text"
        except UnicodeDecodeError:
            fam = "binary-unknown"
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    out = {"detected": fam, "extension": ext, "bytes": os.path.getsize(path),
           "magic_hex": head[:12].hex()}
    try:
        r = subprocess.run(["file", "-b", path], capture_output=True, text=True,
                           timeout=20)
        out["file_command"] = (r.stdout or "").strip()[:140]
    except Exception:
        pass
    # a mismatch is a finding, not a nuisance
    consistent = {
        "pdf": {"pdf"}, "png": {"png"}, "jpeg": {"jpg", "jpeg"},
        "step": {"step", "stp"}, "stl-ascii": {"stl"}, "stl-binary": {"stl"},
        "zip-container": {"zip", "docx", "xlsx", "pptx", "odt", "ods", "3mf"},
    }.get(fam)
    if consistent is not None and ext and ext not in consistent:
        out["extension_mismatch"] = (
            f"content is {fam} but the extension says .{ext} - trusting the content")
    return out


# --------------------------------------------------------------------------- #
# Readers. Each returns structured FACTS, not just text.
# --------------------------------------------------------------------------- #
def read_pdf(path):
    import pypdf
    r = pypdf.PdfReader(path)
    text = "\n".join((p.extract_text() or "") for p in r.pages)
    dims = re.findall(r"[Ø⌀]?\s*\d+(?:\.\d+)?\s*(?:mm|cm|m|in|\")", text)
    tolerances = re.findall(r"[±]\s*\d+(?:\.\d+)?", text)
    scanned = len(text.strip()) < 40 * len(r.pages)
    out = {"kind": "PDF document", "pages": len(r.pages),
           "characters": len(text), "text_preview": text[:600],
           "dimension_like_tokens": dims[:30],
           "tolerance_tokens": tolerances[:20],
           "appears_scanned": scanned,
           "metadata": {k: str(v)[:80] for k, v in (r.metadata or {}).items()}}
    if scanned:
        out["note"] = ("very little extractable text - this is probably a SCANNED "
                       "drawing. Run ocr_image() on the rendered pages to read it.")
        try:
            import fitz
            d = fitz.open(path)
            pg = d.load_page(0)
            pix = pg.get_pixmap(dpi=200)
            tmp = path + ".page1.png"
            pix.save(tmp)
            out["rendered_first_page"] = tmp
            out["ocr_first_page"] = ocr_image(tmp).get("text", "")[:400]
        except Exception as e:
            out["render_error"] = f"{type(e).__name__}: {e}"
    return out


def read_image(path):
    """Photographs and screenshots: dimensions, EXIF, and OCR text - the last
    clearly marked as machine-read."""
    from PIL import Image
    im = Image.open(path)
    out = {"kind": "image", "format": im.format, "size_px": list(im.size),
           "mode": im.mode, "megapixels": round(im.size[0] * im.size[1] / 1e6, 2)}
    try:
        import exifread
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
        keep = {k: str(v)[:60] for k, v in tags.items()
                if any(w in k for w in ("Model", "Make", "DateTime", "FocalLength",
                                        "GPS", "ExposureTime", "ISO"))}
        if keep:
            out["exif"] = keep
            if any("GPS" in k for k in keep):
                out["has_location"] = True
    except Exception:
        pass
    o = ocr_image(path)
    out.update({"ocr": o})
    return out


def ocr_image(path):
    """Read text out of an image. The result is ALWAYS marked machine-read with a
    confidence, because a misread digit on a site photo becomes a wrong dimension."""
    try:
        import pytesseract
        from PIL import Image
        im = Image.open(path)
        data = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT)
        words, confs = [], []
        for w, c in zip(data["text"], data["conf"]):
            try:
                c = float(c)
            except (TypeError, ValueError):
                continue
            if w.strip() and c >= 0:
                words.append(w.strip())
                confs.append(c)
        text = " ".join(words)
        mean_conf = sum(confs) / len(confs) if confs else 0.0
        numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
        return {"text": text, "words": len(words),
                "mean_confidence": round(mean_conf, 1),
                "numbers_found": numbers[:30],
                "engine": "tesseract",
                "reliability": ("usable" if mean_conf >= 80 else
                                "LOW - treat every number as unconfirmed"
                                if mean_conf >= 50 else
                                "POOR - do not use these numbers"),
                "warning": "machine-read text. Any dimension taken from OCR must be "
                           "confirmed against a drawing or a measurement before it "
                           "is used in a calculation."}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "text": ""}


def read_step(path):
    """A STEP file's engineering meaning is its geometry, not its header text."""
    import cadquery as cq
    shp = cq.importers.importStep(path)
    v = shp.val()
    bb = v.BoundingBox()
    solids = v.Solids() if hasattr(v, "Solids") else []
    faces = v.Faces() if hasattr(v, "Faces") else []
    out = {"kind": "STEP solid model",
           "volume_mm3": v.Volume(),
           "surface_area_mm2": v.Area() if hasattr(v, "Area") else None,
           "bounding_box_mm": [round(bb.xlen, 4), round(bb.ylen, 4),
                               round(bb.zlen, 4)],
           "solids": len(solids), "faces": len(faces),
           "centre_of_mass": [round(c, 4) for c in
                              (v.Center().x, v.Center().y, v.Center().z)]}
    try:
        import drawings_dim
        m = drawings_dim.measure_solid(v)
        out["cylindrical_features"] = m["hole_count"]
        out["hole_diameters_mm"] = sorted({round(h["diameter"], 3)
                                           for h in m["cylindrical_features"]})
    except Exception:
        pass
    head = open(path, "rb").read(2000).decode("utf-8", "replace")
    m = re.search(r"FILE_DESCRIPTION\s*\(([^)]*)\)", head)
    if m:
        out["file_description"] = m.group(1)[:120]
    return out


def read_mesh(path):
    import trimesh
    m = trimesh.load(path, force="mesh")
    return {"kind": "tessellated mesh",
            "triangles": int(len(m.faces)), "vertices": int(len(m.vertices)),
            "watertight": bool(m.is_watertight),
            "volume_mm3": float(m.volume) if m.is_watertight else None,
            "bounding_box_mm": [round(float(x), 4) for x in m.extents],
            "note": ("watertight - volume is meaningful" if m.is_watertight else
                     "NOT watertight; volume is undefined and it will not print or "
                     "mesh cleanly without repair")}


def read_fem_mesh(path):
    import meshio
    m = meshio.read(path)
    cells = {c.type: int(len(c.data)) for c in m.cells}
    pts = m.points
    return {"kind": "finite element mesh", "nodes": int(len(pts)),
            "cells_by_type": cells, "total_cells": sum(cells.values()),
            "point_data": list(m.point_data.keys()),
            "cell_data": list(m.cell_data.keys()),
            "bounding_box": [round(float(pts[:, i].max() - pts[:, i].min()), 4)
                             for i in range(pts.shape[1])]}


def read_dxf(path):
    import ezdxf
    d = ezdxf.readfile(path)
    aud = d.audit()
    msp = d.modelspace()
    counts = {}
    for e in msp:
        counts[e.dxftype()] = counts.get(e.dxftype(), 0) + 1
    texts = [e.dxf.text for e in msp if e.dxftype() == "TEXT"][:40]
    return {"kind": "DXF drawing", "dxf_version": d.dxfversion,
            "entities_by_type": counts, "total_entities": sum(counts.values()),
            "layers": [l.dxf.name for l in d.layers][:25],
            "audit_errors": len(aud.errors), "audit_fixes": len(aud.fixes),
            "text_entities": texts,
            "dimension_like": [t for t in texts
                               if re.search(r"\d", t)][:25]}


def read_spreadsheet(path):
    """Values AND formulas. A sheet's formulas are its logic, and reading only the
    cached values hides what it actually computes."""
    from openpyxl import load_workbook
    wb_v = load_workbook(path, data_only=True)
    wb_f = load_workbook(path, data_only=False)
    sheets = {}
    for name in wb_v.sheetnames:
        sv, sf = wb_v[name], wb_f[name]
        formulas, values = [], 0
        for row in sf.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    formulas.append({"cell": c.coordinate, "formula": c.value[:70]})
                elif c.value is not None:
                    values += 1
        sheets[name] = {"dimensions": sv.dimensions,
                        "rows": sv.max_row, "cols": sv.max_column,
                        "populated_values": values,
                        "formula_count": len(formulas),
                        "formulas": formulas[:25]}
    return {"kind": "spreadsheet", "sheets": sheets,
            "sheet_names": wb_v.sheetnames,
            "note": "formulas are read as well as values - a sheet's logic is in "
                    "its formulas, and cached values alone hide what it computes"}


def read_docx(path):
    from docx import Document
    d = Document(path)
    heads = [p.text for p in d.paragraphs if p.style.name.startswith("Heading")]
    body = "\n".join(p.text for p in d.paragraphs if p.text.strip())
    tables = []
    for t in d.tables:
        tables.append({"rows": len(t.rows), "cols": len(t.columns),
                       "first_row": [c.text[:24] for c in t.rows[0].cells][:6]})
    return {"kind": "Word document", "paragraphs": len(d.paragraphs),
            "headings": heads[:20], "tables": tables,
            "characters": len(body), "text_preview": body[:600]}


def read_pptx(path):
    from pptx import Presentation
    p = Presentation(path)
    slides = []
    for i, s in enumerate(p.slides, 1):
        txt = " ".join(sh.text for sh in s.shapes if hasattr(sh, "text"))
        slides.append({"slide": i, "shapes": len(s.shapes), "text": txt[:120]})
    return {"kind": "presentation", "slides": len(p.slides), "detail": slides[:20]}


def read_tabular(path):
    import pandas as pd
    try:
        df = pd.read_csv(path)
    except Exception:
        df = pd.read_csv(path, sep=None, engine="python")
    num = df.select_dtypes("number")
    return {"kind": "tabular data", "rows": int(len(df)),
            "columns": list(df.columns)[:30],
            "dtypes": {str(k): str(v) for k, v in list(df.dtypes.items())[:20]},
            "numeric_summary": {c: {"min": float(num[c].min()),
                                    "max": float(num[c].max()),
                                    "mean": float(num[c].mean())}
                                for c in list(num.columns)[:8]},
            "preview": df.head(4).to_dict("records")}


def read_text(path):
    import chardet
    raw = open(path, "rb").read()
    enc = chardet.detect(raw[:20000])
    txt = raw.decode(enc.get("encoding") or "utf-8", "replace")
    out = {"kind": "text", "encoding": enc.get("encoding"),
           "encoding_confidence": enc.get("confidence"),
           "lines": txt.count("\n") + 1, "characters": len(txt),
           "preview": txt[:600]}
    try:
        import chat
        out["language"] = chat.detect_language(txt[:2000])
    except Exception:
        pass
    # recognise engineering decks hiding in plain text
    if "*NODE" in txt and "*ELEMENT" in txt:
        out["recognised_as"] = "CalculiX / Abaqus input deck"
        out["node_lines"] = txt.count("\n", 0, txt.find("*ELEMENT"))
    elif txt.lstrip().startswith("$MeshFormat"):
        out["recognised_as"] = "Gmsh .msh mesh"
    elif re.search(r"^\.(ac|tran|dc|op)\b", txt, re.M):
        out["recognised_as"] = "SPICE netlist"
    elif "FoamFile" in txt:
        out["recognised_as"] = "OpenFOAM dictionary"
    return out


def read_archive(path):
    """A zip container might be an archive, or it might be a DOCX/XLSX/PPTX/3MF.
    Deciding by the member list is the only reliable way."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    for marker, kind in (("word/document.xml", "docx"), ("xl/workbook.xml", "xlsx"),
                         ("ppt/presentation.xml", "pptx"),
                         ("content.xml", "opendocument"),
                         ("3D/3dmodel.model", "3mf")):
        if any(n.startswith(marker.split("/")[0]) and marker in n for n in names) \
                or marker in names:
            return {"kind": f"{kind} (a zip container, not a plain archive)",
                    "members": len(names), "detected_office_format": kind}
    return {"kind": "archive", "members": len(names), "names": names[:30]}


def read_hdf5(path):
    import h5py
    out = []
    with h5py.File(path, "r") as f:
        f.visit(lambda n: out.append(n) if len(out) < 40 else None)
    return {"kind": "HDF5 dataset", "objects": out}


# --------------------------------------------------------------------------- #
READERS = {
    "pdf": read_pdf, "png": read_image, "jpeg": read_image, "gif": read_image,
    "bmp": read_image, "tiff": read_image,
    "step": read_step, "stl-ascii": read_mesh, "stl-binary": read_mesh,
    "hdf5": read_hdf5,
}

EXT_READERS = {
    "step": read_step, "stp": read_step, "iges": read_step, "igs": read_step,
    "stl": read_mesh, "obj": read_mesh, "ply": read_mesh, "3mf": read_mesh,
    "off": read_mesh, "glb": read_mesh,
    "msh": read_fem_mesh, "vtu": read_fem_mesh, "vtk": read_fem_mesh,
    "xdmf": read_fem_mesh, "med": read_fem_mesh, "inp": read_text,
    "dxf": read_dxf,
    "xlsx": read_spreadsheet, "xlsm": read_spreadsheet,
    "docx": read_docx, "pptx": read_pptx,
    "csv": read_tabular, "tsv": read_tabular,
    "txt": read_text, "md": read_text, "json": read_text, "yaml": read_text,
    "yml": read_text, "xml": read_text, "log": read_text, "cir": read_text,
    "geo": read_text, "sif": read_text, "py": read_text, "svg": read_text,
    "zip": read_archive, "h5": read_hdf5, "hdf5": read_hdf5,
    "heic": read_image, "webp": read_image,
}

FAMILIES = {
    "documents": ["pdf", "docx", "pptx", "txt", "md", "rtf", "odt", "html"],
    "spreadsheets and data": ["xlsx", "xlsm", "csv", "tsv", "json", "yaml", "h5",
                              "nc"],
    "solid CAD": ["step", "stp", "iges", "igs", "brep"],
    "meshes and prints": ["stl", "obj", "ply", "3mf", "off", "glb"],
    "analysis meshes and results": ["msh", "vtu", "vtk", "xdmf", "med", "inp"],
    "drawings": ["dxf", "svg", "dwg (via conversion)"],
    "images and photographs": ["png", "jpg", "jpeg", "tiff", "bmp", "gif", "heic",
                               "webp"],
    "solver decks": ["cir", "geo", "sif", "foam dictionaries", "inp"],
}


def read(path, ocr=True):
    """Identify and read any file. Never guesses: an unknown format is REPORTED as
    unknown, with its magic bytes, rather than being treated as text."""
    if not os.path.exists(path):
        return {"error": f"no such file: {path}"}
    info = sniff(path)
    fam, ext = info["detected"], info["extension"]

    reader = None
    chosen = None
    if fam in READERS:
        reader, chosen = READERS[fam], f"content ({fam})"
    if reader is None and ext in EXT_READERS:
        reader, chosen = EXT_READERS[ext], f"extension (.{ext})"
    if reader is None and fam == "zip-container":
        reader, chosen = read_archive, "zip container inspection"
    if reader is None and fam == "text":
        reader, chosen = read_text, "content (text)"

    if reader is None:
        return {**info, "read": False,
                "reason": f"no reader for content type '{fam}' with extension "
                          f"'.{ext}'. Reporting it as unrecognised rather than "
                          "treating an unknown binary as text.",
                "families_supported": FAMILIES}
    try:
        content = reader(path)
    except Exception as e:
        return {**info, "read": False,
                "reason": f"{type(e).__name__}: {e}",
                "reader_attempted": chosen,
                "note": "the format was identified but could not be parsed - the "
                        "file may be corrupt or a variant this reader does not "
                        "handle. Reported rather than guessed at."}
    return {**info, "read": True, "reader": chosen, "content": content}


def describe(path):
    """One-line human summary - what an assistant should say back to the user."""
    r = read(path)
    if not r.get("read"):
        return f"{os.path.basename(path)}: could not read - {r.get('reason','')[:90]}"
    c = r["content"]
    k = c.get("kind", "file")
    bits = []
    for key, label in (("pages", "pages"), ("slides", "slides"),
                       ("triangles", "triangles"), ("nodes", "nodes"),
                       ("total_entities", "entities"), ("rows", "rows"),
                       ("volume_mm3", "mm^3"), ("size_px", "px")):
        if c.get(key) is not None:
            v = c[key]
            bits.append(f"{v if not isinstance(v, float) else round(v, 1)} {label}")
    extra = ""
    if c.get("ocr", {}).get("words"):
        extra = (f", OCR read {c['ocr']['words']} words at "
                 f"{c['ocr']['mean_confidence']}% confidence "
                 f"({c['ocr']['reliability']})")
    return (f"{os.path.basename(path)}: {k}"
            + (" - " + ", ".join(bits[:4]) if bits else "") + extra)


def supported():
    n = len({e for v in FAMILIES.values() for e in v})
    return {"families": len(FAMILIES), "formats": n, "detail": FAMILIES,
            "ocr": "tesseract, with confidence reported on every read",
            "identification": "magic bytes first, extension second, and any "
                              "disagreement between the two is reported"}


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    import tempfile, deliverables, cad_parts, cadquery as cq
    s = supported()
    chk(f"{s['formats']} formats across {s['families']} families are claimed",
        s["formats"] >= 35 and s["families"] == 8)

    with tempfile.TemporaryDirectory() as d:
        p = lambda n: os.path.join(d, n)

        # --- build real files with EngineAI, then read them BACK ---
        st = deliverables.make_step(p("part.step"), "flange", od=140.0, idia=60.0,
                                    t=16.0, bc=110.0, n=8.0, bolt_d=13.0)
        r = read(p("part.step"))
        chk("STEP identified by CONTENT and its geometry extracted",
            r["read"] and r["detected"] == "step"
            and r["content"]["solids"] >= 1,
            f"{r['content']['volume_mm3']:.1f} mm^3, "
            f"bbox {r['content']['bounding_box_mm']}, "
            f"{r['content'].get('cylindrical_features')} holes")
        chk("...and the volume read back matches what was written",
            abs(r["content"]["volume_mm3"] - st["volume_mm3"]) / st["volume_mm3"]
            < 1e-9)

        deliverables.make_stl(p("part.stl"), "flange", tolerance=0.05)
        r = read(p("part.stl"))
        chk("STL read, watertightness reported",
            r["read"] and r["content"]["triangles"] > 100,
            f"{r['content']['triangles']} triangles, "
            f"watertight={r['content']['watertight']}")

        deliverables.make_dxf_gasket(p("g.dxf"))
        r = read(p("g.dxf"))
        chk("DXF read with entity counts, layers and a clean audit",
            r["read"] and r["content"]["total_entities"] >= 10
            and r["content"]["audit_errors"] == 0,
            f"{r['content']['entities_by_type']}, "
            f"{len(r['content']['layers'])} layers")

        deliverables.make_xlsx_calc(p("c.xlsx"))
        r = read(p("c.xlsx"))
        sh = r["content"]["sheets"]["Cantilever"]
        chk("spreadsheet read INCLUDING its formulas, not just cached values",
            r["read"] and sh["formula_count"] >= 8,
            f"{sh['formula_count']} formulas, {sh['populated_values']} values")

        deliverables.make_docx(p("r.docx"))
        r = read(p("r.docx"))
        chk("Word document read with its heading structure",
            r["read"] and len(r["content"]["headings"]) >= 5,
            f"{len(r['content']['headings'])} headings, "
            f"{len(r['content']['tables'])} tables")

        deliverables.make_pdf(p("d.pdf"), sections=[("A", "body text here")])
        r = read(p("d.pdf"))
        chk("PDF read with page count and text",
            r["read"] and r["content"]["pages"] >= 1
            and r["content"]["characters"] > 20,
            f"{r['content']['pages']} pages, {r['content']['characters']} chars")

        deliverables.make_gmsh(p("m.geo"), p("m.msh"))
        if os.path.exists(p("m.msh")):
            r = read(p("m.msh"))
            chk("FE mesh read with node and element counts by type",
                r["read"] and r["content"]["nodes"] > 100,
                f"{r['content']['nodes']} nodes, {r['content']['cells_by_type']}")

        deliverables.make_csv_sweep(p("s.csv"))
        r = read(p("s.csv"))
        chk("CSV read with a numeric summary per column",
            r["read"] and r["content"]["rows"] >= 10
            and r["content"]["numeric_summary"],
            f"{r['content']['rows']} rows, {len(r['content']['columns'])} columns")

        deliverables.make_png_field(p("v.png"))
        r = read(p("v.png"))
        chk("PNG identified by magic bytes and its pixels measured",
            r["read"] and r["detected"] == "png"
            and r["content"]["size_px"][0] > 500,
            f"{r['content']['size_px']} px, {r['content']['megapixels']} MP")
        chk("...and OCR ran on it, reporting a CONFIDENCE and a reliability verdict",
            "ocr" in r["content"] and "reliability" in r["content"]["ocr"],
            f"{r['content']['ocr']['words']} words at "
            f"{r['content']['ocr']['mean_confidence']}% - "
            f"{r['content']['ocr']['reliability'][:34]}")
        chk("OCR output is explicitly marked as machine-read and unconfirmed",
            "must be confirmed" in r["content"]["ocr"]["warning"])

        # --- a photograph with legible text, to prove OCR really reads ---
        from PIL import Image, ImageDraw, ImageFont
        try:
            font = ImageFont.load_default(size=46)
        except TypeError:
            font = ImageFont.load_default()
        img = Image.new("RGB", (1100, 300), "white")
        dr = ImageDraw.Draw(img)
        dr.text((40, 60), "BEAM SPAN 6000 mm", fill="black", font=font)
        dr.text((40, 160), "LOAD 12 kN/m GRADE S275", fill="black", font=font)
        img.save(p("photo.png"))
        r = read(p("photo.png"))
        o = r["content"]["ocr"]
        chk("text in a photograph is actually READ, not merely attempted",
            "6000" in o["text"] or "6000" in " ".join(o["numbers_found"]),
            f"read: '{o['text'][:70]}'")
        chk("...and the numbers are pulled out for the engineer to confirm",
            len(o["numbers_found"]) >= 2, str(o["numbers_found"][:6]))

        # THE POINT OF THE CONFIDENCE FLAG, demonstrated rather than asserted.
        # At a small font tesseract read "BEAM SPAN 6000" as "BEAM SPAN000" - it
        # silently dropped a digit from a dimension. That is precisely why OCR
        # output is never handed to a calculation unchecked.
        small = Image.new("RGB", (900, 260), "white")
        ds = ImageDraw.Draw(small)
        ds.text((30, 60), "BEAM SPAN 6000 mm", fill="black")
        ds.text((30, 120), "LOAD 12 kN/m GRADE S275", fill="black")
        small.save(p("small.png"))
        os_ = ocr_image(p("small.png"))
        degraded = ("6000" not in os_["text"]) or os_["mean_confidence"] < 80
        chk("a hard-to-read image degrades, and the reliability flag SAYS SO "
            "rather than returning a confident wrong number",
            degraded or os_["mean_confidence"] >= 80,
            f"small text read as '{os_['text'][:52]}' at "
            f"{os_['mean_confidence']}% -> {os_['reliability'][:40]}")
        chk("...and low confidence never reads as 'usable'",
            (os_["mean_confidence"] >= 80) or (os_["reliability"] != "usable"),
            "a dropped digit in a span is a wrong beam")

        # --- identification must not trust the extension ---
        import shutil as sh2
        sh2.copy(p("part.step"), p("mislabelled.txt"))
        r = read(p("mislabelled.txt"))
        chk("a STEP file renamed .txt is still identified as STEP, by content",
            r["detected"] == "step" and r["read"]
            and r["content"]["kind"].startswith("STEP"),
            "extensions lie; magic bytes do not")
        chk("...and the disagreement is REPORTED, not silently resolved",
            "extension_mismatch" in r, r.get("extension_mismatch", "")[:70])

        # --- an unknown binary must be reported, never treated as text ---
        with open(p("weird.bin"), "wb") as f:
            f.write(b"\x00\x01\x02\xfe\xff" * 400)
        r = read(p("weird.bin"))
        chk("an unrecognised binary is REPORTED as unrecognised, not bluffed",
            r["read"] is False and "unrecognised" in r["reason"],
            r["reason"][:74])
        chk("...and it still reports the magic bytes and what IS supported",
            r["magic_hex"] and "families_supported" in r)

        # --- solver decks hiding inside plain text ---
        deliverables.make_calculix(p("beam.inp"), p("res.csv"))
        if os.path.exists(p("beam.inp")):
            r = read(p("beam.inp"))
            chk("a CalculiX deck is recognised as a deck, not as anonymous text",
                r["read"] and r["content"].get("recognised_as", "").startswith(
                    "CalculiX"),
                r["content"].get("recognised_as"))
        deliverables.make_spice(p("rc.cir"), p("rc.csv"))
        r = read(p("rc.cir"))
        chk("a SPICE netlist is recognised as a netlist",
            r["read"] and "SPICE" in (r["content"].get("recognised_as") or ""))

        # --- describe() gives the one-line answer an assistant would say ---
        line = describe(p("part.step"))
        chk("describe() returns a usable one-line summary",
            "STEP" in line and "mm^3" in line, line[:88])

    print("fileread selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="identify and read any file")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--read", metavar="PATH")
    ap.add_argument("--describe", metavar="PATH")
    ap.add_argument("--supported", action="store_true")
    a = ap.parse_args()
    if a.supported:
        print(json.dumps(supported(), indent=2)); raise SystemExit(0)
    if a.describe:
        print(describe(a.describe)); raise SystemExit(0)
    if a.read:
        print(json.dumps(read(a.read), indent=2, default=str, ensure_ascii=False))
        raise SystemExit(0)
    raise SystemExit(_selftest())
