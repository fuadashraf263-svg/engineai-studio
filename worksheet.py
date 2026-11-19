#!/usr/bin/env python3
"""
worksheet.py - turn any verified answer into a fill-in-the-blank exercise.

This is the student-facing product. EngineAI already knows the cited formula and
the correct number; a worksheet hides chosen steps and lets the student work them
out, checking each entry against the verified value with a tolerance.

  python worksheet.py "cantilever tip deflection" --P 1000 --L 1.0 --E 200e9 --I 8.333e-6
  python worksheet.py ... --level hard --format html      interactive, self-marking
  python worksheet.py ... --format pdf                    printable + answer key
  python worksheet.py --selftest
"""
import os, re, sys, math, json, html, argparse, datetime

OUT = os.path.expanduser(os.environ.get("ENGINEAI_OUT", "~/engineai/out"))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# friendlier display units, so answers are human-sized
NICE = {"m": [("mm", 1e3), ("m", 1)], "Pa": [("MPa", 1e-6), ("kPa", 1e-3), ("Pa", 1)],
        "N": [("kN", 1e-3), ("N", 1)], "W": [("kW", 1e-3), ("W", 1)],
        "rad": [("deg", 180/math.pi), ("rad", 1)], "Hz": [("Hz", 1)], "-": [("-", 1)]}

def nice_unit(value, unit):
    for name, f in NICE.get(unit, [(unit, 1)]):
        v = value*f
        if 0.05 <= abs(v) < 1e5 or name == NICE.get(unit, [(unit, 1)])[-1][0]:
            return v, name, f
    return value, unit, 1.0

def fmt(x, sig=5):
    if x == 0: return "0"
    if 1e-3 <= abs(x) < 1e5: return f"{x:.{sig}g}"
    return f"{x:.{sig-1}e}"

def build_steps(ref, values):
    """Given a reference entry and inputs, produce the worked solution."""
    import sympy as sp
    from engineai import parse
    e, loc = parse(ref["expr"], ref["sym"])
    names = sorted((str(s) for s in e.free_symbols))
    result = float(e.subs({loc[n]: values[n] for n in names}))
    disp, dunit, factor = nice_unit(result, ref["unit"])
    pretty = ref["expr"].replace("**", "^").replace("*", "·")
    subbed = pretty
    for n in sorted(names, key=len, reverse=True):
        subbed = re.sub(rf"\b{n}\b", fmt(values[n]), subbed)
    steps = [
        dict(id="given", label="Given", prompt="List the known quantities",
             shown=",  ".join(f"{n} = {fmt(values[n])}" for n in names),
             answer=None, unit=""),
        dict(id="formula", label="Governing equation", prompt="Write the formula",
             shown=pretty, answer=None, unit="",
             note=f"source: {ref['src']}"),
        dict(id="substitute", label="Substitute", prompt="Put the numbers in",
             shown=subbed, answer=None, unit=""),
        dict(id="evaluate", label="Evaluate", prompt="Compute the value in SI units",
             shown=fmt(result), answer=result, unit=ref["unit"]),
        dict(id="convert", label="Express sensibly", prompt=f"Convert to {dunit}",
             shown=f"{fmt(disp)} {dunit}", answer=disp, unit=dunit),
    ]
    return steps, result, disp, dunit

LEVELS = {"easy": ("convert",),
          "medium": ("substitute", "evaluate", "convert"),
          "hard": ("formula", "substitute", "evaluate", "convert")}

def make(question, values, level="medium"):
    from engineai import lookup
    hits = lookup(question)
    if not hits: raise ValueError(f"no reference formula matches: {question}")
    ref = hits[0]
    missing = [n for n in ref["sym"].split() if n not in values]
    import sympy as sp
    from engineai import parse
    e, _loc = parse(ref["expr"], ref["sym"])
    need = sorted(str(s) for s in e.free_symbols)
    miss = [n for n in need if n not in values]
    if miss: raise ValueError("missing values for: " + ", ".join(miss))
    steps, result, disp, dunit = build_steps(ref, values)
    blanks = LEVELS.get(level, LEVELS["medium"])
    for s in steps: s["blank"] = s["id"] in blanks
    return dict(question=question, ref=ref, level=level, steps=steps,
                result=result, display=disp, display_unit=dunit, values=values)

# ------------------------------------------------------------------- text
def render_text(w, key=False):
    L = [f"\n{'='*68}", f"  {w['question'].upper()}", f"  level: {w['level']}", "="*68]
    for i, s in enumerate(w["steps"], 1):
        L.append(f"\n  {i}. {s['label']}   — {s['prompt']}")
        if s.get("note"): L.append(f"     ({s['note']})")
        if s["blank"] and not key:
            L.append("     " + "_"*46 + (f"  [{s['unit']}]" if s["unit"] else ""))
        else:
            L.append(f"     {s['shown']}")
    if key:
        L.append(f"\n  ANSWER: {fmt(w['display'])} {w['display_unit']}")
    else:
        L.append("\n  Check your work:  add --key  (or use the HTML version, which marks itself)")
    return "\n".join(L) + "\n"

# ------------------------------------------------------------------- html
def render_html(w, path=None):
    rows = []
    for i, s in enumerate(w["steps"], 1):
        note = f"<div class=note>{html.escape(s['note'])}</div>" if s.get("note") else ""
        if s["blank"]:
            ans = s["answer"]
            if ans is None:      # a text step: reveal, don't grade
                body = (f"<button class=rev onclick=\"this.outerHTML='<code>"
                        f"{html.escape(s['shown'])}</code>'\">show this step</button>")
            else:
                body = (f"<input class=ip data-ans='{ans}' data-unit='{html.escape(s['unit'])}' "
                        f"placeholder='your answer' inputmode=decimal> "
                        f"<span class=un>{html.escape(s['unit'])}</span> "
                        f"<span class=mk></span> "
                        f"<button class=rev onclick=\"reveal(this)\">show</button>")
        else:
            body = f"<code>{html.escape(s['shown'])}</code>"
        rows.append(f"<li><b>{html.escape(s['label'])}</b> — {html.escape(s['prompt'])}"
                    f"{note}<div class=b>{body}</div></li>")
    doc = """<!doctype html><meta charset=utf-8><title>%TITLE%</title>
<style>
:root{--ok:#1e8449;--no:#c0392b;--ac:#1b6ca8}
body{font:16px/1.65 system-ui;max-width:820px;margin:34px auto;padding:0 20px;color:#1b2631}
h1{font-size:22px;border-bottom:3px solid var(--ac);padding-bottom:8px}
.meta{color:#5d6d7e;font-size:14px;margin-bottom:18px}
ol{padding-left:22px} li{margin:16px 0}
code{background:#f2f5f9;padding:3px 8px;border-radius:5px;font-size:15px}
.b{margin-top:7px}
.note{color:#7f8c8d;font-size:13px;font-style:italic}
input.ip{font:15px ui-monospace;padding:7px 10px;width:190px;border:2px solid #cfd8e3;border-radius:6px}
input.ok{border-color:var(--ok);background:#eafaf1} input.no{border-color:var(--no);background:#fdedec}
.un{color:#5d6d7e;font-size:14px} .mk{font-weight:700;margin-left:7px}
button{background:#eef3f9;border:1px solid #cfd8e3;border-radius:6px;padding:5px 11px;cursor:pointer;font:13px system-ui}
button:hover{background:#e2ebf5}
#score{position:sticky;top:0;background:#fffffff2;padding:11px 0;font-weight:700;border-bottom:1px solid #e4eaf1}
.src{margin-top:26px;padding-top:14px;border-top:1px solid #e4eaf1;color:#5d6d7e;font-size:13px}
</style>
<h1>%TITLE%</h1>
<div class=meta>EngineAI worksheet · level %LEVEL% · %DATE%<br>
Type each answer and press Enter. Within 2% counts as correct.</div>
<div id=score>0 / %N% correct</div>
<ol>%ROWS%</ol>
<div class=src>Verified against: %SRC%<br>
Every value here was computed by EngineAI and checked against the cited closed-form solution.</div>
<script>
function near(a,b){return Math.abs(a-b)<=Math.max(Math.abs(b)*0.02,1e-12);}
function upd(){const ins=[...document.querySelectorAll('input.ip')];
 const n=ins.filter(i=>i.classList.contains('ok')).length;
 document.getElementById('score').textContent=n+" / "+ins.length+" correct"+
  (n===ins.length&&ins.length?"  — all correct, well done":"");}
function grade(i){const v=parseFloat(i.value.replace(/[^0-9eE.+-]/g,''));
 const a=parseFloat(i.dataset.ans);const m=i.parentNode.querySelector('.mk');
 if(isNaN(v)){i.className='ip';m.textContent='';upd();return;}
 if(near(v,a)){i.className='ip ok';m.textContent='✓';m.style.color='#1e8449';}
 else{i.className='ip no';m.textContent='✗ try again';m.style.color='#c0392b';}
 upd();}
function reveal(b){const i=b.parentNode.querySelector('input.ip');
 i.value=(+i.dataset.ans).toPrecision(5);grade(i);b.remove();}
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.classList.contains('ip'))grade(e.target)});
document.addEventListener('blur',e=>{if(e.target.classList&&e.target.classList.contains('ip'))grade(e.target)},true);
upd();
</script>"""
    n_grade = sum(1 for s in w["steps"] if s["blank"] and s["answer"] is not None)
    doc = (doc.replace("%TITLE%", html.escape(w["question"]))
              .replace("%LEVEL%", w["level"]).replace("%N%", str(n_grade))
              .replace("%DATE%", datetime.date.today().isoformat())
              .replace("%ROWS%", "".join(rows))
              .replace("%SRC%", html.escape(w["ref"]["src"])))
    os.makedirs(OUT, exist_ok=True)
    path = path or os.path.join(OUT, "worksheet_" + re.sub(r"\W+", "_", w["question"].lower())[:38] + ".html")
    open(path, "w").write(doc)
    return path

# -------------------------------------------------------------------- pdf
def render_pdf(w, path=None):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    ss = getSampleStyleSheet()
    mono = ParagraphStyle("m", parent=ss["Code"], fontSize=11, leading=15)
    os.makedirs(OUT, exist_ok=True)
    path = path or os.path.join(OUT, "worksheet_" + re.sub(r"\W+", "_", w["question"].lower())[:38] + ".pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=22*mm, rightMargin=22*mm, topMargin=20*mm)
    st = [Paragraph(w["question"], ss["Title"]),
          Paragraph(f"EngineAI worksheet — level {w['level']} — {datetime.date.today():%d %B %Y}", ss["Italic"]),
          Spacer(1, 6*mm)]
    for i, s in enumerate(w["steps"], 1):
        st.append(Paragraph(f"<b>{i}. {s['label']}</b> — {s['prompt']}", ss["BodyText"]))
        if s.get("note"): st.append(Paragraph(f"<i>{s['note']}</i>", ss["Italic"]))
        st.append(Paragraph("_"*62 + (f"  [{s['unit']}]" if s["unit"] else ""), mono)
                  if s["blank"] else Paragraph(s["shown"], mono))
        st.append(Spacer(1, 5*mm))
    st += [PageBreak(), Paragraph("Answer key", ss["Title"]), Spacer(1, 4*mm)]
    for i, s in enumerate(w["steps"], 1):
        st.append(Paragraph(f"<b>{i}. {s['label']}</b>", ss["BodyText"]))
        st.append(Paragraph(s["shown"], mono)); st.append(Spacer(1, 3*mm))
    st.append(Paragraph(f"<b>Answer: {fmt(w['display'])} {w['display_unit']}</b>", ss["Heading3"]))
    st.append(Paragraph(f"Source: {w['ref']['src']}", ss["Italic"]))
    doc.build(st)
    return path

# --------------------------------------------------------------- selftest
def selftest():
    ok = True
    print("\nWORKSHEET SELFTEST")
    vals = dict(P=1000.0, L=1.0, E=200e9, I=8.333e-6)
    w = make("cantilever tip deflection point load free end", vals, "medium")
    exact = 1000*1.0**3/(3*200e9*8.333e-6)
    good = abs(w["result"] - exact)/exact < 1e-9
    print(f"  computed {w['result']:.6e} m vs closed form {exact:.6e} m   {'ok' if good else 'FAIL'}")
    ok &= good
    print(f"  displayed as {fmt(w['display'])} {w['display_unit']}  (mm, human-sized)")
    ok &= w["display_unit"] == "mm" and abs(w["display"] - exact*1000) < 1e-9
    for lvl, n in (("easy", 1), ("medium", 3), ("hard", 4)):
        ww = make("cantilever tip deflection point load free end", vals, lvl)
        b = sum(1 for s in ww["steps"] if s["blank"])
        print(f"  level {lvl:6s} blanks {b}  {'ok' if b == n else 'FAIL expected '+str(n)}")
        ok &= b == n
    h = render_html(w)
    t = open(h).read()
    checks = [("grading js", "function grade" in t), ("2% tolerance", "0.02" in t),
              ("answer embedded", "data-ans" in t), ("score counter", "id=score" in t),
              ("self-contained", "<script" in t and "http" not in t.split("<script")[1][:400])]
    for n, c in checks:
        print(f"  html {n:18s} {'ok' if c else 'FAIL'}"); ok &= c
    print(f"  html written {os.path.basename(h)}  {os.path.getsize(h)/1024:.1f} KB")
    try:
        p = render_pdf(w)
        import fitz; d = fitz.open(p)
        has_key = "Answer key" in d[1].get_text() if d.page_count > 1 else False
        print(f"  pdf  {d.page_count} pages, answer key on page 2: {'ok' if has_key else 'FAIL'}")
        ok &= has_key; d.close()
    except ImportError:
        print("  pdf  skipped (reportlab/fitz missing)")
    print("\nworksheet selftest:", "PASSED" if ok else "FAILED")
    return ok

def main():
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("question", nargs="*")
    ap.add_argument("--level", default="medium", choices=list(LEVELS))
    ap.add_argument("--format", default="text", choices=["text", "html", "pdf", "all"])
    ap.add_argument("--key", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a, extra = ap.parse_known_args()
    if a.selftest: sys.exit(0 if selftest() else 1)
    try: from units import parse_value
    except Exception: parse_value = None
    vals = {}; i = 0
    while i < len(extra):
        if extra[i].startswith("--") and i+1 < len(extra):
            n, raw = extra[i][2:], extra[i+1]
            try:
                vals[n] = parse_value(raw, n)[0] if parse_value else float(raw); i += 2; continue
            except (ValueError, TypeError): pass
        i += 1
    w = make(" ".join(a.question), vals, a.level)
    if a.format in ("text", "all"): print(render_text(w, a.key))
    if a.format in ("html", "all"): print("  html:", render_html(w))
    if a.format in ("pdf", "all"):  print("  pdf: ", render_pdf(w))

if __name__ == "__main__":
    main()
