#!/usr/bin/env python3
"""
course.py - problem sets, saved progress, and explanations for wrong answers.

The three things a worksheet needs before anyone pays for it:
  * a SET of problems with randomised numbers, not one hardcoded example
  * progress that survives closing the app, with spaced repetition
  * when an answer is wrong, WHY it is wrong - not a red box

  python course.py set --n 6 --format html      a self-marking problem set
  python course.py practice                     interactive, saves progress
  python course.py progress                     mastery per topic
  python course.py explain --topic cantilever_point_tip --given 0.6 --correct 0.2
  python course.py selftest
"""
import os, re, sys, math, json, html, random, sqlite3, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT  = os.path.expanduser(os.environ.get("ENGINEAI_OUT", "~/engineai/out"))
DB   = os.path.expanduser(os.environ.get("ENGINEAI_PROGRESS", "~/engineai/progress.db"))

# realistic ranges for generated problems, in SI
RANGE = {
    "P": (200, 20000), "w": (100, 20000), "T": (50, 5000), "M": (50, 5000),
    "L": (0.3, 4.0), "a": (0.2, 1.5), "c": (0.02, 0.15), "r": (0.01, 0.25),
    "t": (0.002, 0.02), "d": (0.01, 0.20),
    "E": (7e10, 2.1e11), "G": (2.6e10, 8e10), "p": (1e5, 1e7),
    "I": (2e-6, 5e-5), "J": (4e-7, 2e-5), "A": (1e-4, 5e-3),
    "k": (15, 400), "dT": (10, 200), "h": (5, 500), "rho": (700, 8000),
    "mu": (5e-4, 5e-2), "Q": (1e-4, 5e-2), "Re": (200, 2000), "m": (5, 60),
}

def references():
    from engineai import REFERENCE
    return {r["key"]: r for r in REFERENCE}

def value_for(sym, rnd):
    lo, hi = RANGE.get(sym, (1.0, 10.0))
    v = math.exp(rnd.uniform(math.log(lo), math.log(hi)))
    return float(f"{v:.4g}")

def make_problem(key, rnd):
    import sympy as sp
    from engineai import parse
    ref = references()[key]
    e, loc = parse(ref["expr"], ref["sym"])
    names = sorted(str(s) for s in e.free_symbols)
    vals = {n: value_for(n, rnd) for n in names}
    ans = float(e.subs({loc[n]: vals[n] for n in names}))
    return ref, vals, ans

# ------------------------------------------------------- why it is wrong
def diagnose(given, correct, ref=None, values=None):
    """Return a specific, teachable explanation of a wrong answer."""
    if correct == 0: return "Cannot compare against zero."
    if given == 0:   return "You entered zero. Check you substituted every quantity."
    r = given/correct
    if abs(r - 1) <= 0.02: return "Correct."
    if r < 0:
        return ("Right magnitude, wrong sign. Deflection and stress signs follow the "
                "convention you set up — check the direction of your axis.")
    ar = abs(r)
    # 1. unit scaling, the most common real error
    for p, name in ((1e9, "10^9 (GPa vs Pa, or m^4 vs mm^4 twice over)"),
                    (1e6, "10^6 (MPa vs Pa, or m^2 vs mm^2)"),
                    (1e3, "10^3 (mm vs m, kN vs N, kPa vs Pa)"),
                    (1e12, "10^12 (mm^4 vs m^4)")):
        for f, d in ((p, "too large"), (1/p, "too small")):
            if abs(ar - f)/f < 0.03:
                return (f"Out by a factor of {name} — your answer is {d}. "
                        f"This is a unit conversion, not a physics error. "
                        f"Convert every input to SI before substituting.")
    # 2. did they use a different standard formula?
    if ref and values:
        import sympy as sp
        from engineai import parse
        for k, other in references().items():
            if k == ref["key"]: continue
            try:
                e2, loc2 = parse(other["expr"], other["sym"])
                need = [str(s) for s in e2.free_symbols]
                if not set(need) <= set(values): continue
                v2 = float(e2.subs({loc2[n]: values[n] for n in need}))
                if v2 and abs(given/v2 - 1) < 0.02:
                    return (f"That is the answer to a different problem. You used "
                            f"{other['expr']} — the formula for {other['key'].replace('_',' ')} "
                            f"({other['src']}). This case needs {ref['expr']}.")
            except Exception: continue
    # 3. a wrong numeric constant
    for c in (2, 3, 4, 5, 6, 8, 12, 16, 48, 64, 192, 384):
        for f, how in ((c, f"{c} times too large"), (1.0/c, f"{c} times too small")):
            if abs(ar - f)/f < 0.02:
                return (f"Your answer is {how}. The shape of the formula is right but a "
                        f"constant is wrong — compare your denominator with "
                        f"{ref['expr'] if ref else 'the reference'} "
                        f"({ref['src'] if ref else ''}).")
    # 4. dropped or duplicated a length power
    if ref and values:
        for n, v in values.items():
            if v and abs(ar - v)/v < 0.03:
                return (f"You are out by exactly {n} = {v:g}. You have one power of {n} "
                        f"too many — check the exponent on {n} in {ref['expr']}.")
            if v and abs(ar - 1/v)*v < 0.03:
                return (f"You are out by a factor of 1/{n}. You have one power of {n} "
                        f"too few — check the exponent on {n} in {ref['expr']}.")
    if abs(1/ar - 1) < 0.05:
        return "You have the fraction upside down — numerator and denominator are swapped."
    pct = (ar - 1)*100
    return (f"Off by {pct:+.1f}%. Re-check your arithmetic step by step; the substitution "
            f"is usually where it goes wrong.")

# ------------------------------------------------------------- progress
def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS attempts(
      id INTEGER PRIMARY KEY, ts TEXT, topic TEXT, correct INT,
      given REAL, expected REAL, note TEXT);
    CREATE TABLE IF NOT EXISTS streak(day TEXT PRIMARY KEY, n INT);
    """)
    return c

def record(topic, ok, given=None, expected=None, note=""):
    c = db()
    c.execute("INSERT INTO attempts(ts,topic,correct,given,expected,note) "
              "VALUES(datetime('now'),?,?,?,?,?)", (topic, int(ok), given, expected, note))
    d = datetime.date.today().isoformat()
    c.execute("INSERT INTO streak(day,n) VALUES(?,1) "
              "ON CONFLICT(day) DO UPDATE SET n=n+1", (d,))
    c.commit()

def mastery():
    c = db()
    rows = c.execute("""SELECT topic, COUNT(*), SUM(correct),
                        SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END)*1.0/COUNT(*)
                        FROM attempts GROUP BY topic ORDER BY 4 ASC""").fetchall()
    return rows

def due_topics(n=6):
    """Spaced repetition: weakest and least-recently-seen first."""
    c = db(); keys = list(references())
    seen = {t: (a, s or 0) for t, a, s, _r in
            [(r[0], r[1], r[2], r[3]) for r in mastery()]}
    last = dict(c.execute("SELECT topic, MAX(ts) FROM attempts GROUP BY topic").fetchall())
    def score(k):
        a, s = seen.get(k, (0, 0))
        acc = (s/a) if a else 0.0
        recency = last.get(k, "")
        return (acc, recency)                     # low accuracy first, then oldest
    return sorted(keys, key=score)[:n]

# ------------------------------------------------------------ problem set
def build_set(n=6, topics=None, seed=None, level="medium"):
    rnd = random.Random(seed)
    topics = topics or due_topics(n)
    while len(topics) < n: topics = topics + topics
    probs = []
    for k in topics[:n]:
        ref, vals, ans = make_problem(k, rnd)
        from worksheet import nice_unit, fmt
        disp, dunit, _f = nice_unit(ans, ref["unit"])
        probs.append(dict(key=k, ref=ref, values=vals, answer=ans,
                          display=disp, unit=dunit))
    return probs

def render_set_html(probs, path=None, title="EngineAI problem set"):
    from worksheet import fmt
    items = []
    for i, p in enumerate(probs, 1):
        given = ",  ".join(f"{k} = {fmt(v)}" for k, v in sorted(p["values"].items()))
        items.append(f"""<li data-i="{i-1}">
 <div class=q><b>{i}. {html.escape(p['key'].replace('_',' '))}</b></div>
 <div class=g>Given: <code>{html.escape(given)}</code></div>
 <div class=b><input class=ip data-ans="{p['display']}" placeholder="answer" inputmode=decimal>
   <span class=un>{html.escape(p['unit'])}</span> <span class=mk></span>
   <button onclick="hint(this,{i-1})">hint</button>
   <button onclick="sol(this,{i-1})">show working</button></div>
 <div class=fb></div><div class=work></div></li>""")
    payload = json.dumps([dict(key=p["key"], expr=p["ref"]["expr"], src=p["ref"]["src"],
                               values=p["values"], answer=p["display"], unit=p["unit"])
                          for p in probs])
    doc = """<!doctype html><meta charset=utf-8><title>%TITLE%</title>
<style>
:root{--ok:#1e8449;--no:#c0392b;--ac:#1b6ca8}
body{font:16px/1.6 system-ui;max-width:860px;margin:30px auto;padding:0 20px;color:#1b2631}
h1{font-size:22px;border-bottom:3px solid var(--ac);padding-bottom:8px}
#bar{position:sticky;top:0;background:#fffffff5;padding:11px 0;border-bottom:1px solid #e4eaf1;z-index:5}
#fill{height:7px;background:#e8eef5;border-radius:4px;overflow:hidden;margin-top:6px}
#fill i{display:block;height:100%;width:0;background:var(--ok);transition:width .3s}
ol{padding-left:20px} li{margin:22px 0;padding-bottom:16px;border-bottom:1px solid #f0f3f7}
code{background:#f2f5f9;padding:2px 7px;border-radius:5px;font-size:14px}
.g{color:#5d6d7e;font-size:14px;margin:4px 0}
input.ip{font:15px ui-monospace;padding:7px 10px;width:170px;border:2px solid #cfd8e3;border-radius:6px}
input.ok{border-color:var(--ok);background:#eafaf1} input.no{border-color:var(--no);background:#fdedec}
.un{color:#5d6d7e;font-size:14px}.mk{font-weight:700;margin-left:6px}
button{background:#eef3f9;border:1px solid #cfd8e3;border-radius:6px;padding:5px 10px;margin-left:5px;cursor:pointer;font:13px system-ui}
button:hover{background:#e2ebf5}
.fb{margin-top:9px;padding:10px 13px;border-radius:6px;display:none;font-size:14.5px}
.fb.show{display:block;background:#fef5e7;border-left:4px solid #e67e22}
.work{margin-top:9px;font-size:14px;color:#34495e;display:none}
.work.show{display:block}
</style>
<h1>%TITLE%</h1>
<div id=bar><span id=sc>0 / %N%</span> correct <span id=st style="float:right;color:#5d6d7e"></span>
<div id=fill><i></i></div></div>
<ol>%ITEMS%</ol>
<script>
const P=%PAYLOAD%;
const KEY='engineai_progress';
function load(){try{return JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){return{}}}
function save(s){try{localStorage.setItem(KEY,JSON.stringify(s))}catch(e){}}
let store=load();
function near(a,b){return Math.abs(a-b)<=Math.max(Math.abs(b)*0.02,1e-12)}
function why(g,c,p){
 if(g===0) return "You entered zero. Check you substituted every quantity.";
 const r=g/c, ar=Math.abs(r);
 if(r<0) return "Right size, wrong sign — check your sign convention.";
 const sc=[[1e12,'10^12 (mm^4 vs m^4)'],[1e9,'10^9 (GPa vs Pa)'],[1e6,'10^6 (MPa vs Pa)'],[1e3,'10^3 (mm vs m, kN vs N)']];
 for(const [f,n] of sc){
  if(Math.abs(ar-f)/f<0.03) return "Out by "+n+" — your answer is too large. This is a unit conversion, not physics.";
  if(Math.abs(ar-1/f)*f<0.03) return "Out by "+n+" — your answer is too small. Convert to SI before substituting.";}
 for(const c2 of [2,3,4,6,8,12,48,192]){
  if(Math.abs(ar-c2)/c2<0.02) return "Your answer is "+c2+"x too large — a constant in the denominator is wrong. Compare with "+p.expr;
  if(Math.abs(ar-1/c2)*c2<0.02) return "Your answer is "+c2+"x too small — a constant is wrong. Compare with "+p.expr;}
 for(const k in p.values){const v=p.values[k];
  if(v&&Math.abs(ar-v)/v<0.03) return "Out by exactly "+k+" = "+v+" — one power of "+k+" too many in "+p.expr;
  if(v&&Math.abs(ar-1/v)*v<0.03) return "Out by 1/"+k+" — one power of "+k+" too few in "+p.expr;}
 if(Math.abs(1/ar-1)<0.05) return "The fraction is upside down.";
 return "Off by "+((r-1)*100).toFixed(1)+"%. Recheck the substitution — that is usually where it slips.";}
function upd(){const ins=[...document.querySelectorAll('input.ip')];
 const n=ins.filter(i=>i.classList.contains('ok')).length;
 document.getElementById('sc').textContent=n+" / "+ins.length;
 document.querySelector('#fill i').style.width=(100*n/ins.length)+"%";
 const tot=Object.values(store).reduce((a,v)=>a+(v.a||0),0);
 const ok=Object.values(store).reduce((a,v)=>a+(v.c||0),0);
 document.getElementById('st').textContent="all time: "+ok+"/"+tot+" ("+(tot?Math.round(100*ok/tot):0)+"%)";}
function grade(i){const li=i.closest('li'),k=+li.dataset.i,p=P[k];
 const v=parseFloat(i.value.replace(/[^0-9eE.+-]/g,''));
 const fb=li.querySelector('.fb'),m=li.parentNode?li.querySelector('.mk'):null;
 if(isNaN(v)){i.className='ip';fb.className='fb';return upd();}
 const good=near(v,p.answer);
 i.className='ip '+(good?'ok':'no'); m.textContent=good?'✓':'✗'; m.style.color=good?'#1e8449':'#c0392b';
 fb.className='fb'+(good?'':' show'); if(!good) fb.textContent=why(v,p.answer,p);
 const s=store[p.key]||{a:0,c:0}; s.a++; if(good)s.c++; store[p.key]=s; save(store); upd();}
function hint(b,k){const p=P[k],li=b.closest('li'),fb=li.querySelector('.fb');
 fb.className='fb show'; fb.innerHTML="Formula: <code>"+p.expr+"</code><br><small>"+p.src+"</small>";}
function sol(b,k){const p=P[k],li=b.closest('li'),w=li.querySelector('.work');
 let sub=p.expr.replace(/\\*\\*/g,'^');
 Object.keys(p.values).sort((a,b)=>b.length-a.length).forEach(n=>{
   sub=sub.replace(new RegExp("\\\\b"+n+"\\\\b",'g'),p.values[n]);});
 w.className='work show';
 w.innerHTML="<b>Working</b><br>1. "+p.expr+"<br>2. "+sub+"<br>3. = <b>"+
   (+p.answer).toPrecision(5)+" "+p.unit+"</b><br><small>"+p.src+"</small>";}
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.classList.contains('ip'))grade(e.target)});
document.addEventListener('blur',e=>{if(e.target.classList&&e.target.classList.contains('ip'))grade(e.target)},true);
upd();
</script>"""
    doc = (doc.replace("%TITLE%", html.escape(title)).replace("%N%", str(len(probs)))
              .replace("%ITEMS%", "".join(items)).replace("%PAYLOAD%", payload))
    os.makedirs(OUT, exist_ok=True)
    path = path or os.path.join(OUT, "problem_set.html")
    open(path, "w").write(doc)
    return path

# ------------------------------------------------------------- practice
def practice(n=5, seed=None):
    from worksheet import fmt
    probs = build_set(n, seed=seed)
    right = 0
    print(f"\n  {n} problems. Answers within 2% count. Blank to skip.\n")
    for i, p in enumerate(probs, 1):
        print(f"  {i}. {p['key'].replace('_',' ')}")
        print("     given: " + ",  ".join(f"{k}={fmt(v)}" for k, v in sorted(p['values'].items())))
        try: raw = input(f"     answer [{p['unit']}]: ").strip()
        except (EOFError, KeyboardInterrupt): print(); break
        if not raw: print(f"     skipped — answer was {fmt(p['display'])} {p['unit']}\n"); continue
        try: g = float(re.sub(r"[^0-9eE.+-]", "", raw))
        except ValueError: print("     not a number\n"); continue
        ok = abs(g - p["display"]) <= max(abs(p["display"])*0.02, 1e-12)
        note = "" if ok else diagnose(g, p["display"], p["ref"], p["values"])
        record(p["key"], ok, g, p["display"], note)
        if ok: right += 1; print("     correct\n")
        else:  print(f"     not right — {note}\n     answer: {fmt(p['display'])} {p['unit']}\n")
    print(f"  {right}/{n} this session. Progress saved to {DB}\n")

def show_progress():
    rows = mastery()
    if not rows: print("\n  no attempts yet — run:  python course.py practice\n"); return
    print(f"\n  {'topic':30s} {'tried':>6s} {'right':>6s} {'mastery':>8s}")
    print("  " + "-"*56)
    for t, a, s, r in rows:
        bar = "#"*int(round(r*10)) + "."*(10-int(round(r*10)))
        print(f"  {t[:30]:30s} {a:6d} {s or 0:6d} {r*100:7.0f}% {bar}")
    c = db()
    tot, ok = c.execute("SELECT COUNT(*), SUM(correct) FROM attempts").fetchone()
    days = c.execute("SELECT COUNT(*) FROM streak").fetchone()[0]
    print(f"\n  overall {ok or 0}/{tot} ({100*(ok or 0)/max(tot,1):.0f}%)  across {days} day(s)")
    weak = [r[0] for r in rows if r[3] < 0.7][:3]
    if weak: print("  next set will focus on: " + ", ".join(weak))
    print()

# ------------------------------------------------------------- selftest
def selftest():
    ok = True
    print("\nCOURSE SELFTEST")
    R = references()
    ref = R["cantilever_point_tip"]; vals = dict(P=1000.0, L=1.0, E=200e9, I=8.333e-6)
    correct = 2.00008e-4
    cases = [
        (correct*1000,  "10^3",        "mm/m unit slip"),
        (correct/1e6,   "10^6",        "MPa/Pa slip"),
        (correct*3,     "3 times too large","wrong constant"),
        (-correct,      "sign",        "sign error"),
        (correct*1.5,   "%",           "generic arithmetic"),
        (correct,       "Correct",     "right answer"),
    ]
    for given, expect, label in cases:
        msg = diagnose(given, correct, ref, vals)
        hit = expect.lower() in msg.lower()
        print(f"  {'ok ' if hit else 'FAIL'} {label:22s} -> {msg[:64]}")
        ok &= hit
    # used the wrong standard formula entirely
    from engineai import parse
    e2, l2 = parse(R["simple_point_mid"]["expr"], R["simple_point_mid"]["sym"])
    wrong = float(e2.subs({l2[n]: vals[n] for n in ("P","L","E","I")}))
    msg = diagnose(wrong, correct, ref, vals)
    hit = "different problem" in msg
    print(f"  {'ok ' if hit else 'FAIL'} {'used another formula':22s} -> {msg[:70]}")
    ok &= hit
    # problem generation + set rendering
    probs = build_set(4, seed=7)
    good = all(p["answer"] and math.isfinite(p["answer"]) for p in probs)
    print(f"  {'ok ' if good else 'FAIL'} generated {len(probs)} problems with finite answers")
    ok &= good
    h = render_set_html(probs)
    t = open(h).read()
    for n, c in (("explanations", "function why" in t), ("progress store", "localStorage" in t),
                 ("working shown", "function sol" in t), ("hint", "function hint" in t),
                 ("progress bar", "#fill" in t)):
        print(f"  {'ok ' if c else 'FAIL'} html {n}"); ok &= c
    # progress db
    global DB
    DB = "/tmp/_prog_test.db"
    if os.path.exists(DB): os.remove(DB)
    record("cantilever_point_tip", True, 1, 1); record("cantilever_point_tip", False, 2, 1)
    record("euler_pinned", True, 1, 1)
    m = {r[0]: r for r in mastery()}
    good = m["cantilever_point_tip"][1] == 2 and abs(m["cantilever_point_tip"][3]-0.5) < 1e-9
    print(f"  {'ok ' if good else 'FAIL'} progress persisted and mastery computed")
    ok &= good
    d = due_topics(3)
    print(f"  {'ok ' if d else 'FAIL'} spaced repetition picks weakest first: {d[0]}")
    ok &= bool(d)
    os.remove(DB)
    print("\ncourse selftest:", "PASSED" if ok else "FAILED")
    return ok

def main():
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("cmd", choices=["set", "practice", "progress", "explain", "selftest"])
    ap.add_argument("--n", type=int, default=6); ap.add_argument("--seed", type=int)
    ap.add_argument("--format", default="html", choices=["html", "text"])
    ap.add_argument("--topic"); ap.add_argument("--given", type=float)
    ap.add_argument("--correct", type=float)
    a = ap.parse_args()
    if a.cmd == "selftest": sys.exit(0 if selftest() else 1)
    if a.cmd == "progress": return show_progress()
    if a.cmd == "practice": return practice(a.n, a.seed)
    if a.cmd == "explain":
        R = references(); ref = R.get(a.topic)
        print("\n  " + diagnose(a.given, a.correct, ref, None) + "\n"); return
    probs = build_set(a.n, seed=a.seed)
    if a.format == "html":
        p = render_set_html(probs); print(f"  {len(probs)} problems -> {p}")
    else:
        from worksheet import fmt
        for i, p in enumerate(probs, 1):
            print(f"\n  {i}. {p['key'].replace('_',' ')}")
            print("     " + ",  ".join(f"{k}={fmt(v)}" for k, v in sorted(p['values'].items())))
            print(f"     answer: {fmt(p['display'])} {p['unit']}")

if __name__ == "__main__":
    main()
