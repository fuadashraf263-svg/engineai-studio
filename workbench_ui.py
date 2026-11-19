"""workbench_ui.py - the page for workbench.py.

Kept in its own module because the WebGL viewer is a real program, not a
snippet, and burying 300 lines of shader and matrix code inside the server file
made both harder to read. No CDN: every byte the browser runs is here, so the
page works with the network unplugged, which is what "local only" has to mean.
"""

PAGE = r"""<!-- EngineAI Workbench -->
<title>EngineAI Workbench</title>
<style>
:root{
  --bg:#0b0f14; --panel:#111820; --panel2:#0d141c; --line:#1e2a36;
  --ink:#e6edf3; --muted:#7d8ea1; --accent:#39c5cf; --accent2:#7c9cff;
  --ok:#2ea043; --bad:#f85149; --warn:#d29922; --purple:#bc8cff;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
  overflow:hidden}
/* Explicit ROWS matter as much as columns. The first version declared only
   columns and gave the stage `height:60vh` in the narrow media query. With
   `height:100vh` on the grid and `overflow:hidden` on the body, the untyped
   second row was sized by its content and the whole 3D stage was clipped off
   the bottom of the page - which is what "the website has no view" was. Any
   window under 1200px, including VS Code's Simple Browser, hit it.
   minmax(0,...) everywhere so a grid child can actually shrink and scroll
   instead of being forced to its content height. */
.app{display:grid;height:100vh;
  grid-template-columns:250px minmax(0,1fr) minmax(0,1.25fr);
  grid-template-rows:minmax(0,1fr)}
.rail,.mid,.stage{min-height:0;min-width:0}
@media(max-width:1200px){
  .app{grid-template-columns:200px minmax(0,1fr);
       grid-template-rows:minmax(0,1fr) minmax(0,58vh)}
  .stage{grid-column:1/-1;grid-row:2}}
@media(max-width:720px){
  .app{grid-template-columns:minmax(0,1fr);
       grid-template-rows:minmax(0,1fr) minmax(0,55vh);overflow-y:auto}
  .rail{display:none}
  .mid{grid-column:1;grid-row:1}
  .stage{grid-column:1;grid-row:2}}

/* ---------------- left rail ---------------- */
.rail{background:var(--panel2);border-right:1px solid var(--line);
  display:flex;flex-direction:column;overflow:hidden}
.brand{padding:14px 16px;border-bottom:1px solid var(--line);display:flex;
  align-items:center;gap:9px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--accent);
  box-shadow:0 0 10px var(--accent)}
.brand b{font-size:14px;letter-spacing:.2px}
.brand span{font-size:10.5px;color:var(--muted);display:block;font-weight:400}
.navs{padding:8px}
.navs button{display:flex;width:100%;gap:9px;align-items:center;
  background:transparent;border:1px solid transparent;color:var(--muted);
  padding:8px 10px;border-radius:8px;cursor:pointer;font:inherit;font-size:13px;
  text-align:left;margin-bottom:2px}
.navs button:hover{background:#ffffff08;color:var(--ink)}
.navs button.on{background:var(--panel);border-color:var(--line);color:var(--ink)}
.navs .ic{width:16px;text-align:center;opacity:.85}
.railhead{padding:10px 16px 6px;font-size:10.5px;text-transform:uppercase;
  letter-spacing:.7px;color:var(--muted);font-weight:600}
.jobs{flex:1;overflow-y:auto;padding:0 8px 10px}
.jobrow{padding:7px 10px;border-radius:7px;cursor:pointer;font-size:12.5px;
  border:1px solid transparent;margin-bottom:2px}
.jobrow:hover{background:#ffffff08}
.jobrow.on{background:var(--panel);border-color:var(--line)}
.jobrow .k{color:var(--ink);display:block;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.jobrow .m{color:var(--muted);font-size:11px}
.railfoot{border-top:1px solid var(--line);padding:9px 14px;font-size:11px;
  color:var(--muted);display:flex;justify-content:space-between}

/* ---------------- centre ---------------- */
.mid{display:flex;flex-direction:column;overflow:hidden;
  border-right:1px solid var(--line)}
.midhead{padding:12px 18px;border-bottom:1px solid var(--line);
  display:flex;align-items:center;gap:10px}
.midhead h1{font-size:14.5px;margin:0;font-weight:650}
.midhead .sub{font-size:11.5px;color:var(--muted)}
.midbody{flex:1;overflow-y:auto;padding:16px 18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:11px;
  padding:15px;margin-bottom:13px}
.card h2{font-size:13px;margin:0 0 3px;font-weight:650}
.card .sub{color:var(--muted);font-size:12px;margin-bottom:12px}
label.f{display:block;font-size:11.5px;color:var(--muted);margin:9px 0 4px}
input,select,textarea{width:100%;background:#0a1017;color:var(--ink);
  border:1px solid var(--line);border-radius:8px;padding:8px 11px;font:inherit;
  font-size:13px;outline:none}
input:focus,select:focus,textarea:focus{border-color:var(--accent)}
textarea{font-family:ui-monospace,Consolas,monospace;font-size:12px;min-height:110px;
  resize:vertical}
button.go{background:var(--accent);color:#04121f;border:0;border-radius:8px;
  padding:9px 16px;font-weight:650;cursor:pointer;font-size:13px}
button.go:disabled{opacity:.45;cursor:default}
button.ghost{background:transparent;color:var(--ink);border:1px solid var(--line);
  border-radius:8px;padding:8px 13px;cursor:pointer;font:inherit;font-size:12.5px}
button.ghost:hover{border-color:var(--accent)}
.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}

/* sliders */
.pw{margin-bottom:11px}
.pw .top{display:flex;justify-content:space-between;font-size:11.5px;
  margin-bottom:3px}
.pw .nm{color:var(--muted)}
.pw .vl{color:var(--accent);font-family:ui-monospace,monospace}
input[type=range]{-webkit-appearance:none;appearance:none;height:4px;padding:0;
  background:#1b2735;border-radius:99px;border:0}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:15px;
  height:15px;border-radius:50%;background:var(--accent);cursor:grab;
  border:2px solid #0b0f14}
input[type=range]::-moz-range-thumb{width:13px;height:13px;border-radius:50%;
  background:var(--accent);cursor:grab;border:2px solid #0b0f14}

/* ---------------- stage ---------------- */
.stage{display:flex;flex-direction:column;overflow:hidden;background:var(--panel2)}
.stagehead{padding:10px 16px;border-bottom:1px solid var(--line);display:flex;
  align-items:center;gap:10px;min-height:46px}
.stagehead .t{font-size:13px;font-weight:650}
.stagehead .s{font-size:11.5px;color:var(--muted);flex:1;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.viewport{flex:1;position:relative;min-height:180px;background:
  radial-gradient(ellipse at 50% 35%,#16202b 0%,#0a0e13 70%)}
canvas{display:block;width:100%;height:100%;cursor:grab}
canvas:active{cursor:grabbing}
.vempty{position:absolute;inset:0;display:flex;align-items:center;
  justify-content:center;color:var(--muted);font-size:12.5px;text-align:center;
  padding:30px;flex-direction:column;gap:8px}
.hud{position:absolute;left:12px;top:11px;font-size:11px;color:var(--muted);
  font-family:ui-monospace,monospace;background:#0b0f14bb;padding:5px 9px;
  border-radius:7px;border:1px solid var(--line);pointer-events:none}
.vcheck{position:absolute;right:12px;top:11px;font-size:11px;padding:5px 10px;
  border-radius:7px;font-weight:600;border:1px solid}
.vcheck.ok{color:var(--ok);border-color:#2ea04355;background:#2ea04318}
.vcheck.bad{color:var(--bad);border-color:#f8514955;background:#f8514918}
.vcheck.warnb{color:var(--warn);border-color:#d2992255;background:#d2992218}
/* chat */
.msg{margin-bottom:11px;max-width:100%}
.msg .who{font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;
  color:var(--muted);margin-bottom:4px}
.msg .bub{background:var(--panel);border:1px solid var(--line);
  border-radius:11px;padding:12px 14px;font-size:13px;white-space:pre-wrap}
.msg.me .bub{background:#39c5cf14;border-color:#39c5cf44}
.msg.ask .bub{border-color:#d2992255;background:#d2992212}
.msg.doing .bub{border-color:#7c9cff55;background:#7c9cff12}
.toolrun{margin-top:8px;font-family:ui-monospace,Consolas,monospace;
  font-size:11.5px;color:var(--muted);border-left:2px solid var(--line);
  padding-left:9px}
.toolrun summary{cursor:pointer;color:var(--accent2)}
.lv-tool{color:var(--accent2)} .lv-answer{color:var(--ok)}
.qrow{margin-top:9px}
.qrow label{display:block;font-size:12px;color:var(--warn);margin-bottom:5px}
.qrow input{margin-bottom:4px}
.chip{background:#ffffff08;border:1px solid var(--line);color:var(--muted);
  border-radius:99px;padding:5px 12px;font:inherit;font-size:11.5px;
  cursor:pointer}
.chip:hover{color:var(--ink);border-color:var(--accent)}
.basis{display:inline-block;font-size:9.5px;font-weight:700;letter-spacing:.6px;
  padding:2px 7px;border-radius:4px;margin-left:7px;vertical-align:middle}
.b-COMPUTED{background:#2ea04326;color:var(--ok)}
/* ROUTED is deliberately NOT green. It means the right solver was identified
   and not run - no number exists. Colouring it like COMPUTED would give a
   promise the same visual weight as a verified result. */
.b-ROUTED{background:#7c9cff26;color:var(--accent2)}
.b-EXPLAINED{background:#bc8cff26;color:var(--purple)}
.b-REFERENCE{background:#58a6ff26;color:var(--accent2)}
.b-CLARIFY,.b-MODEL_REQUIRED{background:#d2992226;color:var(--warn)}
.b-CONVERSATIONAL,.b-ERROR{background:#6e768126;color:var(--muted)}
.whybox{margin-top:7px;padding:8px 11px;border-left:2px solid var(--warn);
  background:#d2992212;color:var(--ink);font-size:12px;line-height:1.5;
  border-radius:0 6px 6px 0}
.vtools{position:absolute;right:12px;bottom:12px;display:flex;gap:6px}
.vtools button{background:#0b0f14cc;border:1px solid var(--line);color:var(--muted);
  border-radius:7px;padding:5px 9px;font-size:11px;cursor:pointer}
.vtools button:hover{color:var(--ink);border-color:var(--accent)}
.imgwrap{position:absolute;inset:0;overflow:auto;display:flex;
  align-items:center;justify-content:center;padding:14px;background:#fff}
.imgwrap img{max-width:100%;max-height:100%}
.docwrap{position:absolute;inset:0;overflow:auto;padding:14px}
.docwrap pre{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;
  white-space:pre-wrap;color:var(--ink);margin:0}
.docwrap table{width:100%;border-collapse:collapse;font-size:11.5px}
.docwrap th,.docwrap td{border-bottom:1px solid var(--line);padding:4px 7px;
  text-align:left}
.docwrap th{color:var(--muted);position:sticky;top:0;background:var(--panel2)}
iframe.pdf{position:absolute;inset:0;width:100%;height:100%;border:0;
  background:#fff}

/* artifact strip + log */
.tabs{display:flex;border-top:1px solid var(--line);background:var(--panel2)}
.tabs button{flex:1;background:transparent;border:0;border-bottom:2px solid transparent;
  color:var(--muted);padding:8px;font:inherit;font-size:12px;cursor:pointer}
.tabs button.on{color:var(--ink);border-bottom-color:var(--accent)}
.drawer{height:210px;overflow-y:auto;background:var(--panel2);
  border-top:1px solid var(--line);padding:10px 14px}
.afile{display:flex;gap:10px;align-items:center;padding:7px 9px;border-radius:8px;
  cursor:pointer;border:1px solid transparent}
.afile:hover{background:#ffffff08}
.afile.on{background:var(--panel);border-color:var(--line)}
.afile .ext{width:38px;height:30px;border-radius:6px;background:#16202b;
  display:flex;align-items:center;justify-content:center;font-size:9.5px;
  font-weight:700;letter-spacing:.4px;color:var(--accent2);flex:none}
.afile .nm{flex:1;min-width:0}
.afile .nm b{display:block;font-size:12.5px;font-weight:550;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.afile .nm span{font-size:11px;color:var(--muted);display:block;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.badge{font-size:9.5px;font-weight:700;letter-spacing:.5px;padding:3px 7px;
  border-radius:5px;flex:none}
.badge.ok{background:#2ea04322;color:var(--ok)}
.badge.bad{background:#f8514922;color:var(--bad)}
.badge.wait{background:#7d8ea122;color:var(--muted)}
.ev{display:flex;gap:9px;font-size:12px;padding:3px 0;
  font-family:ui-monospace,Consolas,monospace}
.ev .tt{color:var(--muted);flex:none;width:52px;text-align:right}
.ev .lv{flex:none;width:64px;font-weight:700;font-size:10px;padding-top:2px}
.lv-step{color:var(--accent2)} .lv-check{color:var(--ok)}
.lv-artifact{color:var(--purple)} .lv-error{color:var(--bad)}
.lv-warn{color:var(--warn)} .lv-done,.lv-end{color:var(--ok)}
.lv-start{color:var(--muted)} .lv-verdict{color:var(--accent)}
.lv-found{color:var(--purple)} .lv-attack{color:var(--muted)}
.ev .ms{flex:1;color:var(--ink);word-break:break-word}
.spin{display:inline-block;width:10px;height:10px;border:2px solid var(--line);
  border-top-color:var(--accent);border-radius:50%;animation:s .7s linear infinite}
@keyframes s{to{transform:rotate(360deg)}}
.muted{color:var(--muted)} .ok{color:var(--ok)} .bad{color:var(--bad)}
.mono{font-family:ui-monospace,Consolas,monospace}
</style>

<noscript><div style="padding:20px;color:#f85149;font:14px system-ui">
  This page is an application, not a document: it needs JavaScript. Nothing
  here is loaded from the network &mdash; the script is inline in this file.
</div></noscript>

<div id="fatal" style="display:none;position:fixed;inset:0;z-index:99;
  background:#0b0f14;color:#e6edf3;padding:26px 30px;overflow:auto;
  font:13.5px/1.6 ui-sans-serif,system-ui">
  <div style="max-width:760px">
    <div id="fataltitle" style="font-weight:700;font-size:16px;
      margin-bottom:10px;color:#f85149">The workbench failed to start</div>
    <div id="fatalmsg" style="margin-bottom:14px"></div>
    <pre id="fataldetail" style="font-family:ui-monospace,Consolas,monospace;
      font-size:12px;white-space:pre-wrap;background:#111820;border:1px solid
      #1e2a36;border-radius:8px;padding:12px;color:#7d8ea1"></pre>
    <div id="fatalfix" style="display:none;margin-top:16px">
      <div style="margin-bottom:7px">Paste the token here once and this
        browser will stay signed in:</div>
      <div style="display:flex;gap:8px;max-width:560px">
        <input id="tokin" placeholder="token from ~/.engineai/token"
          style="flex:1;background:#0a1017;color:#e6edf3;border:1px solid #1e2a36;
          border-radius:8px;padding:9px 12px;font:13px ui-monospace,monospace">
        <button id="tokgo" style="background:#39c5cf;color:#04121f;border:0;
          border-radius:8px;padding:9px 18px;font-weight:650;cursor:pointer">
          Sign in</button>
      </div>
    </div>
    <div id="fatalenv" style="color:#7d8ea1;font-size:12px;margin-top:12px"></div>
  </div>
</div>

<div class="app">
  <!-- ============ left rail ============ -->
  <div class="rail">
    <div class="brand"><div class="dot"></div>
      <div><b>EngineAI</b><span>Workbench &middot; local only</span></div></div>
    <div class="navs">
      <button class="on" data-p="chat"><span class="ic">&#9993;</span>Command</button>
      <button data-p="cad"><span class="ic">&#9673;</span>Parametric CAD</button>
      <button data-p="script"><span class="ic">&#9998;</span>Script editor</button>
      <button data-p="fea"><span class="ic">&#9648;</span>Stress field</button>
      <button data-p="setup"><span class="ic">&#9678;</span>Pick faces &amp; solve</button>
      <button data-p="deliver"><span class="ic">&#9635;</span>Deliverable set</button>
      <button data-p="research"><span class="ic">&#9701;</span>Law discovery</button>
      <button data-p="about"><span class="ic">&#9432;</span>What is verified</button>
    </div>
    <div class="railhead">Queue</div>
    <div id="queuebox" style="padding:0 14px 10px;font-size:11.5px"
         class="muted">checking…</div>
    <div class="railhead">Runs</div>
    <div class="jobs" id="jobs"><div class="muted"
      style="padding:8px 10px;font-size:12px">nothing yet</div></div>
    <div class="railfoot"><span id="envs">&hellip;</span><span id="clock"></span></div>
  </div>

  <!-- ============ centre ============ -->
  <div class="mid">
    <div class="midhead"><h1 id="mt">Parametric CAD</h1>
      <span class="sub" id="ms">drag a parameter and the solid rebuilds</span></div>
    <div class="midbody">

      <div class="pane on" id="p-chat">
        <div id="thread"></div>
        <div class="card">
          <h2>Tell it what you want</h2>
          <div class="sub">It reads the command, fills geometry from the part
            registry and <b>says which defaults it took</b> &mdash; but it will
            not invent a load. Where the calculation needs something it was not
            given, it asks.</div>
          <textarea id="cmd" style="min-height:64px" placeholder="build a flange with a 120 mm outer diameter&#10;stress in a 400 mm cantilever, 25 x 45 mm, under 1.2 kN&#10;export the deliverables for 4 kN over 300 mm"></textarea>
          <div class="row" style="margin-top:10px">
            <button class="go" id="cmdgo">Send</button>
            <button class="ghost" id="attach">Attach a file</button>
            <input type="file" id="fileinput" style="display:none"
                   accept=".step,.stp,.stl,.csv,.txt,.json,.md">
            <span class="muted" id="cmdstat"></span>
          </div>
          <div class="row" style="margin-top:10px;gap:6px">
            <button class="chip" data-ex="build a flange with a 120 mm outer diameter">build a flange</button>
            <button class="chip" data-ex="how much will this beam deflect">a question it must ask about</button>
            <button class="chip" data-ex="stress in a 400 mm cantilever, 25 x 45 mm, under 1.2 kN">a full stress problem</button>
          </div>
        </div>
      </div>

      <div class="pane" id="p-cad" hidden>
        <div class="card">
          <h2>Drive the geometry</h2>
          <div class="sub">Every rebuild re-runs the invariant: the triangulated
            volume must equal the closed-form volume. The badge on the viewport
            is live &mdash; if the kernel ever disagrees with the algebra you
            will see it while you drag.</div>
          <label class="f">Part</label>
          <select id="part"></select>
          <div id="params" style="margin-top:12px"></div>
          <div class="row" style="margin-top:12px">
            <button class="go" id="commit">Export STEP + STL</button>
            <button class="ghost" id="reset">Reset</button>
            <label class="muted" style="font-size:12px;display:flex;
                   align-items:center;gap:6px;margin:0">
              <input type="checkbox" id="autoverify" style="width:auto;margin:0">
              auto-verify when I stop editing</label>
            <span class="muted mono" id="cadstat"></span>
          </div>
          <div id="tierbox" class="muted" style="margin-top:10px;font-size:12px"></div>
        </div>
      </div>

      <div class="pane" id="p-script" hidden>
        <div class="card">
          <h2>Edit the model as code</h2>
          <div class="sub">Sliders dial a fixed part; this builds whatever you
            write. <b>cq</b> and <b>math</b> are bound &mdash; assign the finished
            solid to <b>result</b>. Ctrl+Enter rebuilds. It runs in a child
            process, so a bad sweep that crashes the geometry kernel loses the
            build, not the server.</div>
          <div class="row" style="margin-bottom:9px">
            <select id="tmpl" style="max-width:180px"></select>
            <button class="ghost" id="loadtmpl">Load as script</button>
            <select id="mat" style="max-width:180px"></select>
          </div>
          <textarea id="src" spellcheck="false" style="min-height:270px"></textarea>
          <div class="row" style="margin-top:11px">
            <button class="go" id="srun">Build (Ctrl+Enter)</button>
            <button class="ghost" id="scommit">Export this solid</button>
            <span class="muted mono" id="sstat"></span>
          </div>
        </div>
        <div class="card" id="mcard" hidden>
          <h2>Measured off the solid</h2>
          <div class="sub">Taken from the B-rep, not from the triangles.</div>
          <div id="meas"></div>
        </div>
      </div>

      <div class="pane" id="p-fea" hidden>
        <div class="card">
          <h2>Stress on the deformed shape</h2>
          <div class="sub">A cantilever solved three ways &mdash;
            Euler-Bernoulli, Timoshenko, and a real CalculiX run on a hex mesh.
            If the solver and the algebra disagree by more than the mesh can
            explain, no field is produced: a coloured picture of an unverified
            result is worse than none.</div>
          <div class="grid2">
            <div><label class="f">Load P (N)</label><input id="f_P" value="1000"></div>
            <div><label class="f">Length (mm)</label><input id="f_L" value="500"></div>
            <div><label class="f">Width b (mm)</label><input id="f_b" value="20"></div>
            <div><label class="f">Depth h (mm)</label><input id="f_h" value="40"></div>
          </div>
          <div class="row" style="margin-top:13px">
            <button class="go" id="fgo">Solve and render</button>
            <span class="muted" id="fstat"></span>
          </div>
        </div>
      </div>

      <div class="pane" id="p-setup" hidden>
        <div class="card">
          <h2>Pick the faces, then solve</h2>
          <div class="sub">Load the geometry, <b>click a face in the viewport</b>
            and say what it is. Boundary conditions are then the real CAD faces
            of your part, not sides of its bounding box &mdash; so a load path
            that does not line up with the box is no longer a problem.</div>
          <div class="row">
            <select id="su_part" style="max-width:190px"></select>
            <button class="ghost" id="su_load">Load geometry</button>
            <button class="ghost" id="su_upload">Use my uploaded STEP</button>
            <span class="muted mono" id="su_stat"></span>
          </div>
          <div id="su_faces" class="muted" style="margin-top:11px;font-size:12px">
            Nothing loaded yet.</div>
          <div class="grid2" style="margin-top:12px">
            <div><label class="f">Load (N)</label><input id="su_force" value="1000"></div>
            <div><label class="f">Direction</label>
              <select id="su_dir"><option value="1">X</option>
              <option value="2" selected>Y</option>
              <option value="3">Z</option></select></div>
            <div><label class="f">Young's modulus (MPa)</label>
              <input id="su_E" value="210000"></div>
            <div><label class="f">Refine until change under (%)</label>
              <input id="su_tol" value="2"></div>
          </div>
          <div class="row" style="margin-top:12px">
            <button class="go" id="su_solve" disabled>Solve</button>
            <button class="ghost" id="su_clear">Clear picks</button>
            <span class="muted" id="su_msg"></span>
          </div>
        </div>
      </div>

      <div class="pane" id="p-deliver" hidden>
        <div class="card">
          <h2>Produce the whole deliverable set</h2>
          <div class="sub">One requirement in; STEP, STL, DXF, dimensioned SVG,
            XLSX, DOCX, PDF, CSV and a receipt out. Each file is then RE-OPENED
            and checked, and anything that fails to read back is marked failed
            rather than counted.</div>
          <div class="grid2">
            <div><label class="f">Load (N)</label><input id="d_load" value="4000"></div>
            <div><label class="f">Span (mm)</label><input id="d_span" value="300"></div>
            <div><label class="f">Width (mm)</label><input id="d_width" value="50"></div>
            <div><label class="f">Target FoS</label><input id="d_fos" value="2.5"></div>
          </div>
          <div class="row" style="margin-top:13px">
            <button class="go" id="dgo">Build the set</button>
            <span class="muted" id="dstat"></span>
          </div>
        </div>
      </div>

      <div class="pane" id="p-research" hidden>
        <div class="card">
          <h2>Discover a law, then attack it</h2>
          <div class="sub">Paste CSV with a header row. The engine fits a
            monomial law and then tries to break it five ways. REFUTED is a
            real and useful outcome. At least 30 strictly-positive rows.</div>
          <textarea id="csv" placeholder="h,g,v&#10;1.0,9.81,4.43&#10;..."></textarea>
          <div class="row" style="margin-top:11px">
            <input id="target" placeholder="target column (default: last)"
                   style="max-width:250px">
            <button class="go" id="rgo">Discover and attack</button>
            <button class="ghost" id="rsample">Load 40-row sample</button>
            <span class="muted" id="rstat"></span>
          </div>
        </div>
      </div>

      <div class="pane" id="p-about" hidden>
        <div class="card"><h2>What this page proves, and what it does not</h2>
          <div class="sub">Read this before trusting a number on it.</div>
          <div id="aboutbody" class="muted" style="font-size:12.5px"></div></div>
      </div>

    </div>
  </div>

  <!-- ============ stage ============ -->
  <div class="stage">
    <div class="stagehead">
      <span class="t" id="st">Viewport</span>
      <span class="s" id="ss">no artifact selected</span>
      <a id="dl" class="ghost" style="display:none;text-decoration:none;
         padding:6px 11px;font-size:12px" download>Download</a>
    </div>
    <div class="viewport" id="vp">
      <canvas id="gl"></canvas>
      <div class="hud" id="hud" style="display:none"></div>
      <div class="vcheck" id="vchk" style="display:none"></div>
      <div class="vtools" id="vtools" style="display:none">
        <button id="vfit">Fit</button><button id="vspin">Spin</button>
        <button id="vwire">Edges</button><button id="vclip">Section: off</button>
        <input type="range" id="vslice" min="0" max="1" step="0.005" value="1"
               style="width:96px;display:none"></div>
      <div class="vempty" id="vempty">
        <div style="font-size:26px;opacity:.35">&#9673;</div>
        <div>Pick a part on the left. The solid appears here and follows
             your sliders.</div></div>
    </div>
    <div class="tabs">
      <button class="on" data-d="files">Artifacts <span id="nf"></span></button>
      <button data-d="log">Live log</button>
      <button data-d="checks">Verification</button>
    </div>
    <div class="drawer" id="d-files"><div class="muted" style="font-size:12px">
      Artifacts appear here as they are written.</div></div>
    <div class="drawer" id="d-log" hidden></div>
    <div class="drawer" id="d-checks" hidden><div class="muted"
      style="font-size:12px">Checks appear per artifact.</div></div>
  </div>
</div>

<script>
/* The token arrives once in the URL and is then stripped from the address bar.
   Keeping it in sessionStorage means a reload does not throw you back to the
   unauthorised JSON blob - which is exactly what happened the first time this
   page was opened. sessionStorage, not localStorage: it dies with the tab. */
const TOK = new URLSearchParams(location.search).get('token')
         || sessionStorage.getItem('engineai_wb') || '';
if (TOK) { sessionStorage.setItem('engineai_wb', TOK);
           history.replaceState({}, '', location.pathname); }
const H = {'Content-Type':'application/json','X-EngineAI-Token':TOK};
/* NOTHING FAILS SILENTLY. A blank page tells you nothing, and the first
   report of this tool was "the website has no view" with no way to tell
   whether it was auth, a script error, WebGL, or layout. Every one of those
   now paints its own reason into the page, because the browser console is
   not somewhere a user should have to look. */
/* Two different states, and they must not look alike. Needing to sign in is
   NORMAL; the first version dressed it in the red "failed to start" banner and
   it read like a crash. A red alarm for an ordinary door is a lie about
   severity, and it trains you to ignore the banner when something really has
   broken. */
function panel(title, msg, detail, opts){
  opts = opts || {};
  const el = document.getElementById('fatal');
  if(!el) return;
  const t = document.getElementById('fataltitle');
  t.textContent = title;
  t.style.color = opts.calm ? '#39c5cf' : '#f85149';
  document.getElementById('fatalmsg').innerHTML = msg;
  document.getElementById('fataldetail').textContent = detail || '';
  document.getElementById('fatalenv').textContent = envLine();
  document.getElementById('fatalfix').style.display =
    opts.signIn ? 'block' : 'none';
  el.style.display = 'block';
  if(opts.signIn){ const i = document.getElementById('tokin'); if(i) i.focus(); }
}
function fatal(msg, detail){ panel('The workbench failed to start', msg, detail); }
/* Signing in reloads with ?token=..., which the server turns into an HttpOnly
   cookie. After that the bare address works, which is the thing a person
   actually types. */
addEventListener('DOMContentLoaded', () => {
  const go = document.getElementById('tokgo');
  if(!go) return;
  const submit = () => {
    const t = document.getElementById('tokin').value.trim();
    if(t) location.href = location.pathname + '?token=' + encodeURIComponent(t);
  };
  go.onclick = submit;
  document.getElementById('tokin').addEventListener('keydown',
    e => { if(e.key === 'Enter') submit(); });
});
function envLine(){
  let webgl = 'no';
  try{ const c = document.createElement('canvas');
    webgl = (c.getContext('webgl') || c.getContext('experimental-webgl'))
      ? 'yes' : 'no'; }catch(e){ webgl = 'blocked: ' + e.message; }
  return `WebGL ${webgl} | EventSource `
    + (typeof EventSource === 'function' ? 'yes' : 'no')
    + ` | window ${innerWidth}x${innerHeight} | ${navigator.userAgent}`;
}
addEventListener('error', e => fatal(
  'A script error stopped the page before it finished loading.',
  `${e.message}\n  at ${e.filename}:${e.lineno}:${e.colno}`));
addEventListener('unhandledrejection', e => fatal(
  'A request failed and nothing handled it.', String(e.reason)));

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s??'').replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = n => n<1024 ? n+' B' : n<1048576 ? (n/1024).toFixed(1)+' KB'
                                             : (n/1048576).toFixed(2)+' MB';

/* ==================================================================
   WebGL mesh viewer. Written here rather than pulled from a CDN so the
   page is genuinely offline-capable - a "local only" tool that needs
   the internet to draw a triangle is not local only.
   ================================================================== */
const V = (() => {
  const cv = $('#gl');
  let gl, prog, buf = {}, nTri = 0, raf = 0;
  let yaw = -0.7, pitch = -0.45, dist = 3, target = [0,0,0], radius = 1;
  let spinning = false, edges = false, dragging = false, lx = 0, ly = 0;
  let clipAxis = 0, clipPos = 1.0, bmin = [0,0,0], bmax = [0,0,0];

  /* `fid` carries the CAD face each triangle belongs to, so a picked face can
     be lit without re-uploading geometry. -1 when we are showing a plain STL,
     which has no face topology at all. */
  const VS = `attribute vec3 p; attribute vec3 n; attribute float fid;
    uniform mat4 mvp; uniform mat4 mv;
    varying vec3 vn; varying vec3 vp; varying vec3 wp; varying float vf;
    void main(){ vn = mat3(mv)*n; vp = (mv*vec4(p,1.0)).xyz; wp = p; vf = fid;
                 gl_Position = mvp*vec4(p,1.0); }`;
  /* The section plane is a fragment discard against a world-space plane. It
     cuts the picture, not the solid - the cut face is open, and the readout
     says so, because a hollow-looking cut is exactly how people talk
     themselves into believing a part is thinner than it is. */
  const FS = `precision mediump float;
    varying vec3 vn; varying vec3 vp; varying vec3 wp; varying float vf;
    uniform vec3 col; uniform vec4 clip; uniform float clipOn;
    uniform float fixF; uniform float loadF; uniform float hovF;
    void main(){
      if (clipOn > 0.5 && dot(wp, clip.xyz) > clip.w) discard;
      vec3 N = normalize(vn);
      if (!gl_FrontFacing) N = -N;
      vec3 base = col;
      if (vf >= 0.0) {
        if (abs(vf - fixF)  < 0.5) base = vec3(0.95, 0.35, 0.30);
        else if (abs(vf - loadF) < 0.5) base = vec3(0.25, 0.80, 0.85);
        else if (abs(vf - hovF)  < 0.5) base = vec3(0.85, 0.70, 0.25);
      }
      vec3 L1 = normalize(vec3(0.4,0.75,0.55));
      vec3 L2 = normalize(vec3(-0.6,-0.25,0.4));
      float d = max(dot(N,L1),0.0)*0.78 + max(dot(N,L2),0.0)*0.30;
      vec3 V = normalize(-vp);
      float rim = pow(1.0-max(dot(N,V),0.0), 2.6)*0.45;
      float spec = pow(max(dot(reflect(-L1,N),V),0.0), 26.0)*0.30;
      vec3 c = base*(0.20+d) + vec3(0.35,0.72,0.80)*rim + vec3(spec);
      gl_FragColor = vec4(c,1.0); }`;

  function sh(t,src){ const s=gl.createShader(t); gl.shaderSource(s,src);
    gl.compileShader(s);
    if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))
      throw new Error(gl.getShaderInfoLog(s)); return s; }

  let why = '';
  function init(){
    try{
      gl = cv.getContext('webgl',{antialias:true,alpha:false})
        || cv.getContext('experimental-webgl');
    }catch(e){ why = e.message; gl = null; }
    if(!gl){
      // Silently returning here is how a viewer shows nothing and explains
      // nothing. Some embedded browsers - VS Code's Simple Browser among them
      // - do not give an iframe a GPU context.
      why = why || 'this browser gave no WebGL context';
      $('#vempty').style.display = 'flex';
      $('#vempty').innerHTML = '<div style="font-size:26px;opacity:.35">&#9888;</div>'
        + '<div><b>No 3D context</b><br>' + why + '.<br><br>'
        + 'Everything else on this page still works &mdash; artifacts, '
        + 'measurements, checks and downloads.<br>For the 3D view, open this '
        + 'same URL in Edge, Chrome or Firefox rather than an embedded '
        + 'browser panel.</div>';
      return false;
    }
    prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER,VS));
    gl.attachShader(prog, sh(gl.FRAGMENT_SHADER,FS));
    gl.linkProgram(prog); gl.useProgram(prog);
    buf.p = gl.createBuffer(); buf.n = gl.createBuffer();
    buf.f = gl.createBuffer();
    gl.enable(gl.DEPTH_TEST);
    let downAt = null;
    cv.addEventListener('mousedown', e=>{dragging=true;lx=e.clientX;ly=e.clientY;
      downAt=[e.clientX,e.clientY];});
    addEventListener('mouseup', e=>{
      // A click is a press that did not travel. Orbiting must not select.
      if(downAt && PICK && Math.hypot(e.clientX-downAt[0], e.clientY-downAt[1]) < 4){
        const r = cv.getBoundingClientRect();
        if(e.clientX>=r.left && e.clientX<=r.right &&
           e.clientY>=r.top && e.clientY<=r.bottom){
          const f = pick(e.clientX-r.left, e.clientY-r.top);
          if(f >= 0 && typeof onFacePick === 'function') onFacePick(f);
        }
      }
      downAt=null; dragging=false;});
    addEventListener('mousemove', e=>{ if(!dragging) return;
      yaw += (e.clientX-lx)*0.01; pitch += (e.clientY-ly)*0.01;
      pitch = Math.max(-1.55, Math.min(1.55,pitch));
      lx=e.clientX; ly=e.clientY; draw(); });
    cv.addEventListener('wheel', e=>{ e.preventDefault();
      dist *= e.deltaY>0 ? 1.12 : 0.89;
      dist = Math.max(radius*1.05, Math.min(radius*14, dist)); draw();
    }, {passive:false});
    return true;
  }

  /* --- matrices --- */
  function persp(f,a,n,fa){ const t=1/Math.tan(f/2);
    return [t/a,0,0,0, 0,t,0,0, 0,0,(fa+n)/(n-fa),-1, 0,0,2*fa*n/(n-fa),0]; }
  function look(e,c,u){
    let z=[e[0]-c[0],e[1]-c[1],e[2]-c[2]];
    let l=Math.hypot(...z)||1; z=z.map(v=>v/l);
    let x=[u[1]*z[2]-u[2]*z[1], u[2]*z[0]-u[0]*z[2], u[0]*z[1]-u[1]*z[0]];
    l=Math.hypot(...x)||1; x=x.map(v=>v/l);
    const y=[z[1]*x[2]-z[2]*x[1], z[2]*x[0]-z[0]*x[2], z[0]*x[1]-z[1]*x[0]];
    return [x[0],y[0],z[0],0, x[1],y[1],z[1],0, x[2],y[2],z[2],0,
      -(x[0]*e[0]+x[1]*e[1]+x[2]*e[2]),
      -(y[0]*e[0]+y[1]*e[1]+y[2]*e[2]),
      -(z[0]*e[0]+z[1]*e[1]+z[2]*e[2]), 1];
  }
  function mul(a,b){ const o=new Array(16);
    for(let i=0;i<4;i++) for(let j=0;j<4;j++){ let s=0;
      for(let k=0;k<4;k++) s+=a[k*4+j]*b[i*4+k]; o[i*4+j]=s; } return o; }

  /* --- STL --- */
  function parse(ab){
    const dv = new DataView(ab);
    let n = ab.byteLength>=84 ? dv.getUint32(80,true) : 0;
    if(ab.byteLength === 84 + n*50) return binary(dv,n);
    return ascii(new TextDecoder().decode(ab));
  }
  function binary(dv,n){
    const P=new Float32Array(n*9), N=new Float32Array(n*9);
    for(let i=0;i<n;i++){ const o=84+i*50;
      const nx=dv.getFloat32(o,true), ny=dv.getFloat32(o+4,true),
            nz=dv.getFloat32(o+8,true);
      for(let v=0;v<3;v++){ const q=o+12+v*12, k=i*9+v*3;
        P[k]=dv.getFloat32(q,true); P[k+1]=dv.getFloat32(q+4,true);
        P[k+2]=dv.getFloat32(q+8,true);
        N[k]=nx; N[k+1]=ny; N[k+2]=nz; } }
    return {P,N,n};
  }
  function ascii(txt){
    const nums=[], nrm=[];
    const re=/facet\s+normal\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)([\s\S]*?)endfacet/g;
    let m;
    while((m=re.exec(txt))){
      const vs=[...m[4].matchAll(/vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)/g)];
      if(vs.length<3) continue;
      for(let v=0;v<3;v++){ nums.push(+vs[v][1],+vs[v][2],+vs[v][3]);
        nrm.push(+m[1],+m[2],+m[3]); }
    }
    return {P:new Float32Array(nums), N:new Float32Array(nrm), n:nums.length/9};
  }

  /* ---- pickable geometry: triangles that know their CAD face ---- */
  let PICK = null;              // {P, F, tris, verts} kept for ray casting
  let sel = {fix:-1, load:-1, hov:-1};

  function loadPickable(d){
    if(!gl && !init()) return;
    const tris = d.triangles, V = d.vertices, FO = d.face_of;
    const n = tris.length;
    // Flat, non-indexed: a vertex shared between two faces must be able to
    // take two different face colours, which an indexed buffer cannot do.
    const P = new Float32Array(n*9), NR = new Float32Array(n*9),
          F = new Float32Array(n*3);
    for(let i=0;i<n;i++){
      const [a,b,c] = tris[i], A=V[a], B=V[b], C=V[c];
      const u=[B[0]-A[0],B[1]-A[1],B[2]-A[2]], w=[C[0]-A[0],C[1]-A[1],C[2]-A[2]];
      let nx=u[1]*w[2]-u[2]*w[1], ny=u[2]*w[0]-u[0]*w[2], nz=u[0]*w[1]-u[1]*w[0];
      const L=Math.hypot(nx,ny,nz)||1; nx/=L; ny/=L; nz/=L;
      [A,B,C].forEach((v,k)=>{
        const o=i*9+k*3; P[o]=v[0]; P[o+1]=v[1]; P[o+2]=v[2];
        NR[o]=nx; NR[o+1]=ny; NR[o+2]=nz; F[i*3+k]=FO[i];
      });
    }
    PICK = {tris, verts:V, face_of:FO};
    upload(P, NR, F, n);
    return d;
  }

  function upload(P, N, F, n){
    nTri = n;
    let mn=[1e30,1e30,1e30], mx=[-1e30,-1e30,-1e30];
    for(let i=0;i<P.length;i+=3) for(let k=0;k<3;k++){
      if(P[i+k]<mn[k]) mn[k]=P[i+k]; if(P[i+k]>mx[k]) mx[k]=P[i+k]; }
    bmin=mn; bmax=mx;
    target = [0,1,2].map(k=>(mn[k]+mx[k])/2);
    radius = Math.max(1e-6, Math.hypot(mx[0]-mn[0],mx[1]-mn[1],mx[2]-mn[2])/2);
    if(dist < radius*1.2 || dist > radius*12) dist = radius*3.1;
    gl.bindBuffer(gl.ARRAY_BUFFER,buf.p); gl.bufferData(gl.ARRAY_BUFFER,P,gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER,buf.n); gl.bufferData(gl.ARRAY_BUFFER,N,gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER,buf.f);
    gl.bufferData(gl.ARRAY_BUFFER, F || new Float32Array(n*3).fill(-1),
                  gl.STATIC_DRAW);
    $('#vempty').style.display='none';
    $('#hud').style.display=''; $('#vtools').style.display='';
    const span = [0,1,2].map(k=>(mx[k]-mn[k]).toFixed(1));
    $('#hud').textContent = `${n.toLocaleString()} triangles   ${span.join(' x ')} mm`;
    draw();
  }

  /* Moller-Trumbore against every surface triangle. A few thousand is
     nothing, and it avoids needing a BVH or a colour-index render pass. */
  function pick(px, py){
    if(!PICK) return -1;
    const w = cv.clientWidth||1, h = cv.clientHeight||1;
    const ndcx = (px/w)*2-1, ndcy = 1-(py/h)*2;
    const cy=Math.cos(yaw), sy=Math.sin(yaw), cp=Math.cos(pitch), sp=Math.sin(pitch);
    const eye=[target[0]+dist*cp*sy, target[1]+dist*sp, target[2]+dist*cp*cy];
    // camera basis
    let f=[target[0]-eye[0],target[1]-eye[1],target[2]-eye[2]];
    let l=Math.hypot(...f)||1; f=f.map(v=>v/l);
    let up=[0,1,0];
    let r=[f[1]*up[2]-f[2]*up[1], f[2]*up[0]-f[0]*up[2], f[0]*up[1]-f[1]*up[0]];
    l=Math.hypot(...r)||1; r=r.map(v=>v/l);
    const u=[r[1]*f[2]-r[2]*f[1], r[2]*f[0]-r[0]*f[2], r[0]*f[1]-r[1]*f[0]];
    const tanF = Math.tan(0.9/2), ar = w/h;
    const dir = [0,1,2].map(k => f[k] + r[k]*ndcx*tanF*ar + u[k]*ndcy*tanF);
    l = Math.hypot(...dir);
    const D = dir.map(v=>v/l);
    let best = Infinity, bestFace = -1;
    const V = PICK.verts;
    for(let i=0;i<PICK.tris.length;i++){
      const [ia,ib,ic] = PICK.tris[i];
      const A=V[ia], B=V[ib], C=V[ic];
      const e1=[B[0]-A[0],B[1]-A[1],B[2]-A[2]];
      const e2=[C[0]-A[0],C[1]-A[1],C[2]-A[2]];
      const pv=[D[1]*e2[2]-D[2]*e2[1], D[2]*e2[0]-D[0]*e2[2], D[0]*e2[1]-D[1]*e2[0]];
      const det = e1[0]*pv[0]+e1[1]*pv[1]+e1[2]*pv[2];
      if(Math.abs(det) < 1e-12) continue;
      const inv = 1/det;
      const tv=[eye[0]-A[0], eye[1]-A[1], eye[2]-A[2]];
      const uu = (tv[0]*pv[0]+tv[1]*pv[1]+tv[2]*pv[2])*inv;
      if(uu < -1e-6 || uu > 1+1e-6) continue;
      const qv=[tv[1]*e1[2]-tv[2]*e1[1], tv[2]*e1[0]-tv[0]*e1[2], tv[0]*e1[1]-tv[1]*e1[0]];
      const vv = (D[0]*qv[0]+D[1]*qv[1]+D[2]*qv[2])*inv;
      if(vv < -1e-6 || uu+vv > 1+1e-6) continue;
      const tt = (e2[0]*qv[0]+e2[1]*qv[1]+e2[2]*qv[2])*inv;
      if(tt > 1e-6 && tt < best){ best = tt; bestFace = PICK.face_of[i]; }
    }
    return bestFace;
  }

  function load(ab){
    if(!gl && !init()) return;
    PICK = null; sel = {fix:-1, load:-1, hov:-1};
    const {P,N,n} = parse(ab);
    nTri = n;
    if(!n){ return; }
    let mn=[1e30,1e30,1e30], mx=[-1e30,-1e30,-1e30];
    for(let i=0;i<P.length;i+=3) for(let k=0;k<3;k++){
      if(P[i+k]<mn[k]) mn[k]=P[i+k]; if(P[i+k]>mx[k]) mx[k]=P[i+k]; }
    bmin = mn; bmax = mx;
    target = [0,1,2].map(k=>(mn[k]+mx[k])/2);
    radius = Math.max(1e-6, Math.hypot(mx[0]-mn[0],mx[1]-mn[1],mx[2]-mn[2])/2);
    if(dist < radius*1.2 || dist > radius*12) dist = radius*3.1;
    gl.bindBuffer(gl.ARRAY_BUFFER,buf.p); gl.bufferData(gl.ARRAY_BUFFER,P,gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER,buf.n); gl.bufferData(gl.ARRAY_BUFFER,N,gl.STATIC_DRAW);
    $('#vempty').style.display='none';
    $('#hud').style.display=''; $('#vtools').style.display='';
    const span = [0,1,2].map(k=>(mx[k]-mn[k]).toFixed(1));
    $('#hud').textContent = `${n.toLocaleString()} triangles   ${span.join(' x ')} mm`;
    draw();
  }

  function draw(){
    if(!gl || !nTri) return;
    const w = cv.clientWidth||1, h = cv.clientHeight||1;
    if(cv.width!==w || cv.height!==h){ cv.width=w; cv.height=h; }
    gl.viewport(0,0,w,h);
    gl.clearColor(0.043,0.055,0.075,1);
    gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    const cy=Math.cos(yaw), sy=Math.sin(yaw), cp=Math.cos(pitch), sp=Math.sin(pitch);
    const eye=[target[0]+dist*cp*sy, target[1]+dist*sp, target[2]+dist*cp*cy];
    const mv = look(eye,target,[0,1,0]);
    const mvp = mul(persp(0.9, w/h, radius*0.02, radius*60), mv);
    gl.useProgram(prog);
    gl.uniformMatrix4fv(gl.getUniformLocation(prog,'mvp'),false,new Float32Array(mvp));
    gl.uniformMatrix4fv(gl.getUniformLocation(prog,'mv'),false,new Float32Array(mv));
    gl.uniform3f(gl.getUniformLocation(prog,'col'),0.52,0.58,0.66);
    const ax=[[1,0,0],[0,1,0],[0,0,1]][Math.max(0,clipAxis-1)];
    const lo=bmin[Math.max(0,clipAxis-1)], hi=bmax[Math.max(0,clipAxis-1)];
    gl.uniform4f(gl.getUniformLocation(prog,'clip'),
      ax[0],ax[1],ax[2], lo + (hi-lo)*clipPos);
    gl.uniform1f(gl.getUniformLocation(prog,'clipOn'), clipAxis?1:0);
    gl.uniform1f(gl.getUniformLocation(prog,'fixF'),  sel.fix);
    gl.uniform1f(gl.getUniformLocation(prog,'loadF'), sel.load);
    gl.uniform1f(gl.getUniformLocation(prog,'hovF'),  sel.hov);
    const ap=gl.getAttribLocation(prog,'p'), an=gl.getAttribLocation(prog,'n'),
          af=gl.getAttribLocation(prog,'fid');
    gl.bindBuffer(gl.ARRAY_BUFFER,buf.p); gl.enableVertexAttribArray(ap);
    gl.vertexAttribPointer(ap,3,gl.FLOAT,false,0,0);
    gl.bindBuffer(gl.ARRAY_BUFFER,buf.n); gl.enableVertexAttribArray(an);
    gl.vertexAttribPointer(an,3,gl.FLOAT,false,0,0);
    if(af >= 0){ gl.bindBuffer(gl.ARRAY_BUFFER,buf.f);
      gl.enableVertexAttribArray(af);
      gl.vertexAttribPointer(af,1,gl.FLOAT,false,0,0); }
    gl.drawArrays(gl.TRIANGLES,0,nTri*3);
    if(edges){ gl.uniform3f(gl.getUniformLocation(prog,'col'),0.05,0.09,0.12);
      for(let i=0;i<nTri;i++) gl.drawArrays(gl.LINE_LOOP,i*3,3); }
  }

  function spin(){ if(!spinning) return; yaw += 0.006; draw();
    raf = requestAnimationFrame(spin); }

  addEventListener('resize', draw);
  return {
    load, draw, clear(){ nTri=0; $('#vempty').style.display='';
      $('#hud').style.display='none'; $('#vtools').style.display='none';
      if(gl){ gl.clearColor(0.043,0.055,0.075,1);
              gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT); } },
    fit(){ dist = radius*3.1; yaw=-0.7; pitch=-0.45; draw(); },
    toggleSpin(){ spinning=!spinning; if(spinning) spin(); else cancelAnimationFrame(raf);
      return spinning; },
    toggleEdges(){ edges=!edges; draw(); return edges; },
    cycleClip(){ clipAxis = (clipAxis+1) % 4; draw();
      return ['off','X','Y','Z'][clipAxis]; },
    setClip(v){ clipPos = v; draw(); },
    clipping(){ return clipAxis !== 0; },
    loadPickable, pickable(){ return !!PICK; },
    mark(kind, face){ sel[kind] = face; draw(); },
    marks(){ return {...sel}; },
    clearMarks(){ sel = {fix:-1, load:-1, hov:-1}; draw(); }
  };
})();

/* ================= pick faces and solve ================= */
let FACEDATA = null, PICKSRC = null;
let onFacePick = null;

async function loadPickGeometry(body, label){
  $('#su_stat').innerHTML = '<span class="spin"></span> tessellating';
  let d;
  try{
    d = await fetch('/api/faces',{method:'POST',headers:H,
      body:JSON.stringify(body)}).then(r=>r.json());
  }catch(e){ $('#su_stat').innerHTML='<span class="bad">'+esc(e.message)+'</span>';
    return; }
  if(d.error){ $('#su_stat').innerHTML='<span class="bad">'+esc(d.error)+'</span>';
    return; }
  FACEDATA = d; PICKSRC = body;
  V.loadPickable(d);
  $('#gl').style.display=''; $('#vempty').style.display='none';
  $('#vchk').style.display='none';
  $('#st').textContent = label;
  $('#ss').textContent = `${d.n_faces} CAD faces, ${d.n_triangles} triangles `
    + '- click one in the viewport';
  $('#su_stat').innerHTML = `<span class="ok">${d.n_faces} faces</span>`;
  renderPicks();
}
$('#su_load').onclick = () => loadPickGeometry(
  {part: $('#su_part').value, params: {}}, $('#su_part').value);
$('#su_upload').onclick = () => loadPickGeometry({}, 'uploaded STEP');

onFacePick = f => {
  const m = V.marks();
  // First click fixes, second loads, clicking the same face again clears it.
  if(m.fix === f)       V.mark('fix', -1);
  else if(m.load === f) V.mark('load', -1);
  else if(m.fix < 0)    V.mark('fix', f);
  else                  V.mark('load', f);
  renderPicks();
};
function renderPicks(){
  const m = V.marks();
  const info = f => {
    if(f < 0) return '<span class="muted">not set</span>';
    const a = (FACEDATA && FACEDATA.faces[f] && FACEDATA.faces[f].area_mm2);
    return `face ${f}` + (a ? ` &middot; ${a.toFixed(0)} mm&sup2;` : '');
  };
  $('#su_faces').innerHTML = FACEDATA
    ? `<div><span style="color:#f2593c">&#9632;</span> fixed &nbsp; ${info(m.fix)}</div>`
      + `<div><span style="color:#40ccd9">&#9632;</span> loaded &nbsp; ${info(m.load)}</div>`
      + (m.fix >= 0 && m.fix === m.load
          ? '<div class="bad" style="margin-top:6px">'
            + 'the same face cannot be both fixed and loaded, that measures nothing'
            + '</div>' : '')
    : 'Nothing loaded yet.';
  const ready = FACEDATA && m.fix >= 0 && m.load >= 0 && m.fix !== m.load;
  $('#su_solve').disabled = !ready;
  $('#su_msg').textContent = ready ? '' :
    (FACEDATA ? 'click a face to fix, then another to load' : '');
}
$('#su_clear').onclick = () => { V.clearMarks(); renderPicks(); };
$('#su_solve').onclick = () => {
  const m = V.marks();
  launch('solidfea', {...(PICKSRC||{}),
    fix: 'face:'+m.fix, push: 'face:'+m.load,
    force_N: +$('#su_force').value, direction: +$('#su_dir').value,
    E_MPa: +$('#su_E').value, converge: +$('#su_tol').value || 2});
};

$('#vfit').onclick = ()=>V.fit();
$('#vspin').onclick = e=>e.target.style.color = V.toggleSpin() ? 'var(--accent)' : '';
$('#vwire').onclick = e=>e.target.style.color = V.toggleEdges() ? 'var(--accent)' : '';
$('#vclip').onclick = e => { const m = V.cycleClip();
  e.target.textContent = 'Section: ' + m;
  e.target.style.color = V.clipping() ? 'var(--accent)' : '';
  $('#vslice').style.display = V.clipping() ? '' : 'none'; };
$('#vslice').oninput = e => V.setClip(+e.target.value);

/* ================= panes ================= */
const TITLES = {chat:['Command','say what you want; it asks when it does not know'],
  cad:['Parametric CAD','drag a parameter and the solid rebuilds'],
  script:['Script editor','build whatever you can write, in a child process'],
  fea:['Stress field','solved three ways, and they have to agree'],
  setup:['Pick faces & solve','boundary conditions on the real CAD faces'],
  deliver:['Deliverable set','every file re-opened and checked before it counts'],
  research:['Law discovery','fit a law, then spend five attacks trying to break it'],
  about:['What is verified','the honest scope of this page']};
$$('.navs button').forEach(b => b.onclick = () => {
  $$('.navs button').forEach(x=>x.classList.toggle('on', x===b));
  const p = b.dataset.p;
  $$('.pane').forEach(x => x.hidden = (x.id !== 'p-'+p));
  $('#mt').textContent = TITLES[p][0]; $('#ms').textContent = TITLES[p][1];
  if(p === 'chat') $('#cmd').focus();
});
$$('.tabs button').forEach(b => b.onclick = () => {
  $$('.tabs button').forEach(x=>x.classList.toggle('on', x===b));
  ['files','log','checks'].forEach(d => $('#d-'+d).hidden = (d !== b.dataset.d));
});

/* ================= CAD live driving ================= */
let SCHEMA = [], CUR = {}, PART = null, timer = null, seq = 0;

async function loadParts(){
  const r = await fetch('/api/parts',{headers:H}).then(r=>r.json());
  SCHEMA = r.parts || [];
  $('#part').innerHTML = SCHEMA.map(s=>`<option>${esc(s.part)}</option>`).join('');
  if(SCHEMA.length) selectPart(SCHEMA[0].part);
}
function selectPart(name){
  PART = SCHEMA.find(s=>s.part===name); if(!PART) return;
  CUR = {}; PART.params.forEach(p=>CUR[p.name]=p.default);
  $('#params').innerHTML = PART.params.map(p=>`
    <div class="pw"><div class="top"><span class="nm">${esc(p.name)}</span>
      <span class="vl" id="v_${p.name}">${p.default}</span></div>
      <input type="range" id="s_${p.name}" min="${p.min}" max="${p.max}"
             step="${p.step}" value="${p.default}"></div>`).join('');
  PART.params.forEach(p=>{
    const s = $('#s_'+p.name);
    s.oninput = () => { CUR[p.name] = p.integer ? Math.round(+s.value) : +s.value;
      $('#v_'+p.name).textContent = CUR[p.name];
      clearTimeout(timer); timer = setTimeout(live, 130); };
  });
  live();
}
$('#part').onchange = e => selectPart(e.target.value);
$('#reset').onclick = () => selectPart(PART.part);

async function live(){
  const my = ++seq;
  $('#cadstat').innerHTML = '<span class="spin"></span> rebuilding';
  try{
    const res = await fetch('/api/cad/live',{method:'POST',headers:H,
      body:JSON.stringify({part:PART.part, params:CUR})});
    if(my !== seq) return;                 // a newer drag already superseded this
    if(!res.ok){
      const j = await res.json().catch(()=>({}));
      const c = $('#vchk'); c.style.display=''; c.className = 'vcheck warnb';
      c.textContent = j.unverifiable ? 'cannot be checked' : 'build failed';
      // Say WHY, in the part's own language. The badge used to accuse the
      // geometry kernel, which was behaving correctly every time; what
      // actually stops applying is the closed form, once holes merge or a
      // bolt circle leaves the flange. (Naming the old wording here verbatim
      // would defeat the test that checks it is gone - that self-referential
      // trap has now cost three test cycles.)
      $('#cadstat').innerHTML = '<span class="bad">'+esc(j.error||res.status)+'</span>'
        + ((j.why||[]).length
            ? '<div class="whybox">' + j.why.map(w=>'&bull; '+esc(w)).join('<br>')
              + '</div>'
            : '');
      return; }
    const meta = JSON.parse(res.headers.get('X-Meta'));
    V.load(await res.arrayBuffer());
    $('#st').textContent = PART.part;
    $('#ss').textContent = Object.entries(CUR)
      .map(([k,v])=>k+'='+v).join('  ');
    const c = $('#vchk'); c.style.display='';
    c.className = 'vcheck ' + (meta.agrees ? 'ok':'bad');
    c.textContent = meta.agrees
      ? `volume verified  ${meta.mesh_mm3.toFixed(0)} mm³`
      : `mesh and closed form differ by ${meta.error_pct.toFixed(3)}%`;
    // Re-fence the sliders to what is currently buildable. The valid range of
    // one parameter depends on the others, so this has to happen after every
    // rebuild rather than once when the part is chosen.
    if(meta.bounds) for(const [k, [lo, hi]] of Object.entries(meta.bounds)){
      const s = $('#s_' + k);
      if(s){ s.min = lo; s.max = hi;
             s.title = `buildable range ${lo} to ${hi}`; }
    }
    $('#cadstat').innerHTML = `<span class="ok">ok</span> ${meta.seconds}s, `
      + `${meta.triangles.toLocaleString()} triangles`;
    showTier(meta);
    scheduleVerify();
  }catch(e){ if(my===seq) $('#cadstat').innerHTML =
    '<span class="bad">'+esc(e.message)+'</span>'; }
}

/* ================= the two tiers, and the gap between them =================
   Dragging gives you a closed-form ESTIMATE in microseconds. The solver
   answer takes seconds to minutes, so it is queued once you STOP moving -
   900 ms of stillness. The preview never pretends to be the verified number:
   it shows its published error band, and the panel says plainly which tier
   you are looking at. */
let verifyTimer = null, lastVerified = null;

function showTier(meta){
  const b = $('#tierbox');
  if(!b) return;
  const v = lastVerified;
  let h = `<b style="color:var(--warn)">ESTIMATE</b> &nbsp;`
        + `${meta.mesh_mm3 ? (meta.mesh_mm3/1000).toFixed(2)+' cm³' : ''}`
        + ` &middot; closed form, microseconds`;
  if(v){
    h += `<div style="margin-top:5px"><b style="color:var(--ok)">VERIFIED</b>`
       + ` &nbsp;${esc(v.summary)}`
       + `<span class="muted"> &middot; ${esc(v.basis)}, receipt `
       + `${esc((v.run_id||'').slice(0,8))}</span></div>`;
  } else {
    h += '<div class="muted" style="margin-top:5px">'
       + 'no verified run for this shape yet'
       + ' &mdash; press Export, or tick auto-verify</div>';
  }
  b.innerHTML = h;
}

function scheduleVerify(){
  if(!$('#autoverify') || !$('#autoverify').checked) return;
  clearTimeout(verifyTimer);
  $('#tierbox').insertAdjacentHTML('beforeend',
    '<div class="muted" id="pending" style="margin-top:5px">'
    + '<span class="spin"></span> verified run queues when you stop editing…</div>');
  // Long enough that a drag does not queue a solve per frame; short enough
  // that stopping feels like it triggered something.
  verifyTimer = setTimeout(() => {
    launch('solidfea', {part: PART.part, params: CUR,
                        force_N: 1000.0, converge: 2}, {silent: true});
  }, 900);
}

$('#autoverify').onchange = e => {
  if(e.target.checked) scheduleVerify(); else clearTimeout(verifyTimer);
};

$('#commit').onclick = () => launch('cad',
  {part:PART.part, params:CUR, tolerance:0.05, drawing:true});

/* ================= script editor ================= */
async function loadStarter(part){
  const r = await fetch('/api/script?part='+encodeURIComponent(part),
    {headers:H}).then(r=>r.json());
  $('#src').value = r.src;
  if(!$('#mat').options.length)
    $('#mat').innerHTML = '<option value="">no material (no mass)</option>'
      + r.materials.map(m=>`<option>${esc(m)}</option>`).join('');
}
$('#loadtmpl').onclick = () => loadStarter($('#tmpl').value);
$('#src').addEventListener('keydown', e => {
  if(e.key === 'Enter' && (e.ctrlKey || e.metaKey)){ e.preventDefault(); runScript(); }
});
$('#srun').onclick = runScript;

async function runScript(){
  $('#sstat').innerHTML = '<span class="spin"></span> building in a child process';
  try{
    const res = await fetch('/api/script',{method:'POST',headers:H,
      body:JSON.stringify({src:$('#src').value, material:$('#mat').value||null})});
    if(!res.ok){ const j = await res.json().catch(()=>({}));
      $('#sstat').innerHTML = `<span class="bad">${esc(j.stage||'error')}</span>`;
      $('#mcard').hidden = true;
      $('#d-log').innerHTML = `<div class="ev"><span class="lv lv-error">ERROR</span>`
        + `<span class="ms">${esc(j.error||res.status)}</span></div>`;
      $$('.tabs button').forEach(x=>x.classList.toggle('on',x.dataset.d==='log'));
      ['files','log','checks'].forEach(d=>$('#d-'+d).hidden = (d!=='log'));
      return; }
    const meta = JSON.parse(res.headers.get('X-Meta'));
    V.load(await res.arrayBuffer());
    $('#gl').style.display=''; $('#vempty').style.display='none';
    $('#st').textContent = 'scripted solid';
    $('#ss').textContent = `${meta.solids} solid(s), `
      + meta.bbox_mm.map(x=>x.toFixed(1)).join(' x ') + ' mm';
    $('#vchk').style.display='none';
    showMeasure(meta);
    $('#sstat').innerHTML = `<span class="ok">built</span> `
      + `${meta.triangles.toLocaleString()} triangles`;
  }catch(e){ $('#sstat').innerHTML = '<span class="bad">'+esc(e.message)+'</span>'; }
}
function showMeasure(m){
  $('#mcard').hidden = false;
  const row = (k,v) => `<div class="row" style="justify-content:space-between;
    border-bottom:1px solid var(--line);padding:4px 0"><span class="muted"
    style="font-size:12px">${k}</span><span class="mono"
    style="font-size:12.5px">${v}</span></div>`;
  let h = row('volume', m.volume_cm3.toFixed(3)+' cm³')
        + row('surface area', (m.area_mm2/100).toFixed(2)+' cm²')
        + row('bounding box', m.bbox_mm.map(x=>x.toFixed(2)).join(' × ')+' mm')
        + row('centre of mass', m.center_mm.map(x=>x.toFixed(2)).join(', ')+' mm')
        + row('solids', m.solids);
  if(m.mass_g !== undefined)
    h += row(esc(m.material)+' mass',
      m.mass_g > 1000 ? (m.mass_g/1000).toFixed(3)+' kg' : m.mass_g.toFixed(1)+' g');
  if(m.material_note) h += `<div class="bad" style="font-size:11.5px;
    padding-top:6px">${esc(m.material_note)}</div>`;
  $('#meas').innerHTML = h;
}
$('#scommit').onclick = () => launch('script',
  {src: $('#src').value, material: $('#mat').value || null, tolerance: 0.05});

$('#fgo').onclick = () => launch('fea', {
  P_N:+$('#f_P').value, L_mm:+$('#f_L').value,
  b_mm:+$('#f_b').value, h_mm:+$('#f_h').value});
$('#dgo').onclick = () => launch('deliverables', {
  load_N:+$('#d_load').value, span_mm:+$('#d_span').value,
  width_mm:+$('#d_width').value, target_fos:+$('#d_fos').value});
$('#rgo').onclick = () => {
  const rows = parseCSV($('#csv').value);
  if(!rows) { $('#rstat').innerHTML='<span class="bad">could not parse CSV</span>';
    return; }
  launch('research', {rows, target: $('#target').value || null});
};
/* The pane demanded 30+ rows and shipped none, so the first click anyone ever
   made returned an error. A pane whose only reachable state is a refusal is a
   broken pane. This is free-fall: v = sqrt(2 g h), which the engine should
   recover with the exponents 1/2 and 1/2 and a constant of sqrt(2). */
$('#rsample').onclick = () => {
  // g VARIES across the rows. With a single gravity the engine correctly
  // returns v = 4.42945*h^(1/2), folding the constant sqrt(2g) into C,
  // because an input that never changes is not identifiable - good behaviour,
  // but a poor demonstration. Four different gravities let it separate both
  // exponents and recover v = sqrt(2) * h^(1/2) * g^(1/2).
  const GS = [1.62, 3.71, 9.81, 24.79];   // Moon, Mars, Earth, Jupiter
  let rows = ['h,g,v'];
  for(let i = 0; i < 40; i++){
    const h = 0.5 + (i % 10) * 1.3, g = GS[i % 4];
    rows.push(`${h.toFixed(3)},${g},${Math.sqrt(2*g*h).toFixed(6)}`);
  }
  $('#csv').value = rows.join('\n');
  $('#target').value = 'v';
  $('#rstat').textContent = '40 rows, four gravities. Press Discover.';
};
function parseCSV(t){
  const lines = t.trim().split(/\r?\n/).filter(x=>x.trim());
  if(lines.length < 2) return null;
  const head = lines[0].split(',').map(s=>s.trim());
  return lines.slice(1).map(l=>{ const c=l.split(',');
    const o={}; head.forEach((h,i)=>o[h]=parseFloat(c[i])); return o; });
}

/* ================= the command pane ================= */
let PENDING = null;                 // the intent waiting on your answers

function say(who, html, cls){
  const d = document.createElement('div');
  d.className = 'msg ' + (cls || '');
  d.innerHTML = `<div class="who">${esc(who)}</div><div class="bub">${html}</div>`;
  $('#thread').appendChild(d);
  d.scrollIntoView({block:'nearest'});
  return d;
}
$$('.chip').forEach(b => b.onclick = () => {
  $('#cmd').value = b.dataset.ex; send();
});
$('#cmdgo').onclick = () => send();
$('#cmd').addEventListener('keydown', e => {
  if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); send(); }
});

async function send(answers){
  const text = $('#cmd').value.trim();
  if(!answers && !text) return;
  if(!answers){ say('you', esc(text), 'me'); $('#cmd').value = ''; }
  $('#cmdstat').innerHTML = '<span class="spin"></span> thinking';
  let r;
  try{
    r = await fetch('/api/command',{method:'POST',headers:H,
      body:JSON.stringify(answers
        ? {intent: PENDING, answers}
        : {text})}).then(x=>x.json());
  }catch(e){ $('#cmdstat').innerHTML='<span class="bad">'+esc(e.message)+'</span>';
    return; }
  $('#cmdstat').textContent = '';

  if(r.kind === 'clarify'){
    PENDING = r.intent;
    // The questions are the POINT. An assistant that guesses a load and
    // returns a factor of safety has produced something worse than silence.
    let h = esc(r.reason ? 'For ' + r.reason + ' I still need:' : 'I need:');
    r.questions.forEach(q => {
      h += `<div class="qrow"><label>${esc(q.question)}</label>`;
      if(q.options)
        h += `<select data-f="${esc(q.field)}">`
           + q.options.map(o=>`<option>${esc(o)}</option>`).join('') + '</select>';
      else
        h += `<input data-f="${esc(q.field)}" placeholder="${esc(q.unit||'')}">`;
      h += '</div>';
    });
    h += '<div class="row" style="margin-top:10px">'
       + '<button class="go" id="ansgo">Answer</button></div>';
    const node = say('engineai', h, 'ask');
    node.querySelector('#ansgo').onclick = () => {
      const a = {};
      node.querySelectorAll('[data-f]').forEach(el => a[el.dataset.f] = el.value);
      say('you', Object.entries(a).map(([k,v])=>`${esc(k)} = ${esc(v)}`).join('<br>'), 'me');
      send(a);
    };
    node.querySelector('input,select')?.focus();
    return;
  }

  if(r.kind === 'answer'){
    say('engineai', esc(r.answer)
      + (r.basis ? `<span class="basis b-${esc(r.basis)}">${esc(r.basis)}</span>` : '')
      + (r.source ? `<div class="muted" style="font-size:11.5px;margin-top:7px">`
                    + esc(r.source) + '</div>' : ''));
    return;
  }

  if(r.kind === 'job'){
    PENDING = null;
    let h = esc(r.doing);
    if((r.assumed||[]).length)
      h += `<div class="muted" style="font-size:11.5px;margin-top:7px">`
         + `registry defaults used for ${esc(r.assumed.join(', '))}</div>`;
    const node = say('engineai', h + '<div class="agentwork"></div>', 'doing');
    openJob(r.id); refreshJobs();
    // A conversational turn must END in an answer in the thread. Sending the
    // user to a log pane and calling that a reply is what made this feel like
    // a control panel: the tool calls were visible and the conclusion was not.
    if(r.conversational) followAgent(r.id, node);
    return;
  }
  if(r.error) say('engineai', '<span class="bad">'+esc(r.error)+'</span>');
}

/* Watch an agent job and land its ANSWER back in the conversation. */
function followAgent(id, node){
  const work = node.querySelector('.agentwork');
  const es = new EventSource('/api/stream/'+id
    + (TOK?'?token='+encodeURIComponent(TOK):''));
  work.innerHTML = '<div class="muted" style="font-size:11.5px;margin-top:8px">'
    + '<span class="spin"></span> working</div>';
  let tools = [];
  es.onmessage = e => {
    const ev = JSON.parse(e.data);
    if(ev.level === 'tool' || ev.level === 'step'){
      tools.push(ev.message);
      work.innerHTML = '<div class="toolrun">'
        + tools.map(t=>'<div>&rsaquo; '+esc(t)+'</div>').join('') + '</div>';
    }
    if(ev.level === 'answer'){
      es.close();
      work.innerHTML = tools.length
        ? '<details class="toolrun"><summary>'+tools.length
          +' tool call(s)</summary>'
          + tools.map(t=>'<div>&rsaquo; '+esc(t)+'</div>').join('') + '</details>'
        : '';
      node.classList.remove('doing');
      say('engineai', esc(ev.message));
    }
    if(ev.level === 'end' || ev.level === 'error'){ es.close();
      if(work.querySelector('.spin')) work.innerHTML =
        '<span class="bad" style="font-size:12px">'+esc(ev.message)+'</span>'; }
  };
  es.onerror = () => es.close();
}

/* ---- attachments ---- */
$('#attach').onclick = () => $('#fileinput').click();
$('#fileinput').onchange = async e => {
  const f = e.target.files[0];
  if(!f) return;
  say('you', `attached <b>${esc(f.name)}</b> (${fmt(f.size)})`, 'me');
  $('#cmdstat').innerHTML = '<span class="spin"></span> reading it';
  const fd = new FormData(); fd.append('file', f);
  let r;
  try{
    // No Content-Type header: the browser must set the multipart boundary.
    r = await fetch('/api/upload',{method:'POST',
      headers: TOK ? {'X-EngineAI-Token':TOK} : {}, body: fd}).then(x=>x.json());
  }catch(err){ $('#cmdstat').innerHTML='<span class="bad">'+esc(err.message)+'</span>';
    return; }
  $('#cmdstat').textContent = '';
  e.target.value = '';
  if(r.error){ say('engineai', '<span class="bad">'+esc(r.error)+'</span>'); return; }
  let h = esc(r.detail);
  if(r.view_only)
    h += `<div class="whybox">Imported for <b>viewing and measuring</b>. There
          is no feature recognition here, so this cannot be re-driven by
          parameters &mdash; rebuild it in the script editor if you need it
          parametric.</div>`;
  say('engineai', h, 'doing');
  openJob(r.id); refreshJobs();
};

/* ================= jobs ================= */
let JOB = null, ES = null;

async function launch(kind, params, opts){
  opts = opts || {};
  const r = await fetch('/api/job',{method:'POST',headers:H,
    body:JSON.stringify({kind,params})}).then(r=>r.json());
  if(r.error){ if(!opts.silent) alert(r.error); return; }
  // Say where it went. "queued on celery, 1 worker" and "running in a thread
  // because redis is down" are different facts and the UI must not blur them.
  if(r.queue){
    const el = $('#pending');
    if(el) el.innerHTML = `<span class="spin"></span> ${esc(r.queue)}`
      + (r.workers ? ` &middot; ${r.workers} worker(s)` : '')
      + (r.note && !r.workers ? ` &middot; <span class="warn">`
          + esc(r.note) + '</span>' : '');
  }
  openJob(r.id); refreshJobs(); refreshQueue();
  if(kind === 'solidfea') watchVerified(r.id);
}

/* When the verified run lands, put its answer beside the estimate. */
async function watchVerified(id){
  for(let i = 0; i < 400; i++){
    await new Promise(s => setTimeout(s, 1500));
    let j;
    try { j = await fetch('/api/job/'+id,{headers:H}).then(x=>x.json()); }
    catch(e){ return; }
    if(j.status === 'running' || j.status === 'queued') continue;
    const el = $('#pending'); if(el) el.remove();
    if(j.status === 'failed'){
      $('#tierbox').insertAdjacentHTML('beforeend',
        '<div class="bad" style="margin-top:5px">verified run failed: '
        + esc(String(j.error).slice(0,120)) + '</div>');
      return;
    }
    const ans = (j.params && j.params.answer) || '';
    const vm = /peak von Mises\s+([\d.eE+-]+)/.exec(ans);
    const dd = /max deflection\s+([\d.eE+-]+)/.exec(ans);
    lastVerified = {
      basis: (j.params && j.params.basis) || 'COMPUTED',
      run_id: id,
      summary: (dd ? `${(+dd[1]).toPrecision(4)} mm` : '')
             + (vm ? ` · ${(+vm[1]).toPrecision(4)} MPa peak` : ''),
    };
    showTier({mesh_mm3: 0});
    refreshQueue();
    return;
  }
}

async function refreshQueue(){
  let q;
  try { q = await fetch('/api/queue',{headers:H}).then(r=>r.json()); }
  catch(e){ return; }
  const box = $('#queuebox'); if(!box) return;
  const dot = q.workers ? 'var(--ok)' : (q.broker_up ? 'var(--warn)' : 'var(--bad)');
  box.innerHTML =
    `<div><span style="color:${dot}">&#9679;</span> `
    + (q.broker_up ? `celery &middot; ${q.workers} worker(s)` : 'no broker')
    + `</div>`
    + (q.running.length
        ? q.running.map(j => `<div class="muted">&rsaquo; ${esc(j.label)} `
            + `<span class="spin"></span></div>`).join('')
        : '<div class="muted">idle</div>')
    + (q.workers ? '' : `<div class="warn" style="margin-top:4px">`
        + esc(q.note) + '</div>');
}
async function openJob(id){
  JOB = id;
  if(ES) ES.close();
  $('#d-log').innerHTML = '';
  $('#d-files').innerHTML = '<div class="muted" style="font-size:12px">waiting…</div>';
  $$('.tabs button').forEach(x=>x.classList.toggle('on', x.dataset.d==='log'));
  ['files','log','checks'].forEach(d=>$('#d-'+d).hidden = (d!=='log'));
  ES = new EventSource('/api/stream/'+id+(TOK?'?token='+encodeURIComponent(TOK):''));
  ES.onmessage = e => { const ev = JSON.parse(e.data); addEvent(ev);
    if(ev.level==='artifact' || ev.level==='end') refreshJob(id); };
  ES.onerror = () => { ES.close(); refreshJob(id); refreshJobs(); };
}
function addEvent(ev){
  const d = document.createElement('div');
  d.className = 'ev';
  d.innerHTML = `<span class="tt">${ev.t.toFixed(2)}s</span>`
    + `<span class="lv lv-${esc(ev.level)}">${esc(ev.level.toUpperCase())}</span>`
    + `<span class="ms">${esc(ev.message)}</span>`;
  $('#d-log').appendChild(d);
  $('#d-log').scrollTop = 1e9;
}
async function refreshJob(id){
  const j = await fetch('/api/job/'+id,{headers:H}).then(r=>r.json());
  renderFiles(j); renderChecks(j);
  $('#nf').textContent = j.artifact_count ? `(${j.verified_count}/${j.artifact_count})` : '';
  const s = {cad:'#cadstat', deliverables:'#dstat', research:'#rstat',
             fea:'#fstat'}[j.kind];
  if(s && $(s)) $(s).innerHTML = j.status==='running'
    ? '<span class="spin"></span> running'
    : j.status==='failed' ? '<span class="bad">'+esc(j.error||'failed')+'</span>'
    : `<span class="ok">done</span> ${j.seconds}s`;
}
function renderFiles(j){
  if(!j.artifacts.length){ $('#d-files').innerHTML =
    '<div class="muted" style="font-size:12px">no artifacts yet</div>'; return; }
  $('#d-files').innerHTML = j.artifacts.map(a=>{
    const ext = (a.name.split('.').pop()||'?').toUpperCase();
    const b = a.verified===true ? '<span class="badge ok">VERIFIED</span>'
      : a.verified===false ? '<span class="badge bad">FAILED</span>'
      : '<span class="badge wait">WRITTEN</span>';
    return `<div class="afile" data-a="${a.id}"><div class="ext">${esc(ext)}</div>
      <div class="nm"><b>${esc(a.name)}</b><span>${fmt(a.size)} &middot; ${esc(a.detail||a.kind)}</span></div>
      ${b}</div>`;}).join('');
  $$('#d-files .afile').forEach(el => el.onclick = () => {
    $$('#d-files .afile').forEach(x=>x.classList.toggle('on',x===el));
    show(j.artifacts.find(a=>a.id===el.dataset.a)); });
}
function renderChecks(j){
  const rows = [];
  j.artifacts.forEach(a => (a.checks||[]).forEach(c => rows.push([a.name,c])));
  $('#d-checks').innerHTML = rows.length ? rows.map(([n,c])=>
    `<div class="ev"><span class="lv ${c.ok?'lv-check':'lv-error'}">`
    + `${c.ok?'PASS':'FAIL'}</span><span class="ms">${esc(n)} &mdash; `
    + `${esc(c.name)}. ${esc(c.detail||'')}</span></div>`).join('')
    : '<div class="muted" style="font-size:12px">no checks recorded</div>';
}

/* ================= artifact viewing ================= */
async function show(a){
  if(!a) return;
  $('#st').textContent = a.name;
  $('#ss').textContent = a.detail || a.kind;
  const dl = $('#dl'); dl.style.display=''; dl.href = url(a); dl.download = a.name;
  ['#imgv','#docv','#pdfv'].forEach(s => { const e=$(s); if(e) e.remove(); });
  $('#gl').style.display='none'; $('#vchk').style.display='none';
  $('#hud').style.display='none'; $('#vtools').style.display='none';
  $('#vempty').style.display='none';
  const vp = $('#vp');

  if(a.view==='mesh'){
    $('#gl').style.display='';
    V.load(await fetch(url(a),{headers:H}).then(r=>r.arrayBuffer()));
    $('#vtools').style.display='';
  } else if(a.view==='image'){
    const d=document.createElement('div'); d.className='imgwrap'; d.id='imgv';
    d.innerHTML = `<img src="${url(a)}">`; vp.appendChild(d);
  } else if(a.view==='pdf' || a.view==='html'){
    const f=document.createElement('iframe'); f.className='pdf'; f.id='pdfv';
    if(a.view==='html') f.style.background='#0e1319';
    f.src = url(a); vp.appendChild(f);
  } else if(a.view==='table' || a.view==='text' || a.view==='json'){
    const t = await fetch(url(a),{headers:H}).then(r=>r.text());
    const d=document.createElement('div'); d.className='docwrap'; d.id='docv';
    d.innerHTML = a.view==='table' ? csvTable(t)
      : `<pre>${esc(t.slice(0,200000))}</pre>`;
    vp.appendChild(d);
  } else {
    const d=document.createElement('div'); d.className='docwrap'; d.id='docv';
    d.innerHTML = `<pre>${esc(a.name)}\n\n${esc(a.kind)}\n${fmt(a.size)}\n\n`
      + `${esc(a.detail||'')}\n\nNo inline viewer for this format. `
      + `It was written and checked; use Download to open it in its own tool.</pre>`;
    vp.appendChild(d);
  }
}
function url(a){ return '/api/artifact/'+a.id + (TOK?'?token='+encodeURIComponent(TOK):''); }
function csvTable(t){
  const rows = t.trim().split(/\r?\n/).slice(0,400).map(r=>r.split(','));
  if(!rows.length) return '<pre>empty</pre>';
  return '<table><thead><tr>' + rows[0].map(h=>`<th>${esc(h)}</th>`).join('')
    + '</tr></thead><tbody>' + rows.slice(1).map(r=>'<tr>'
    + r.map(c=>`<td>${esc(c)}</td>`).join('') + '</tr>').join('')
    + '</tbody></table>';
}

/* ================= chrome ================= */
async function refreshJobs(){
  const r = await fetch('/api/jobs',{headers:H}).then(r=>r.json());
  if(!r.jobs.length) return;
  $('#jobs').innerHTML = r.jobs.map(j=>`
    <div class="jobrow ${j.id===JOB?'on':''}" data-j="${j.id}">
      <span class="k">${esc(j.label)}</span>
      <span class="m">${esc(j.status)} &middot; ${j.verified}/${j.artifacts} verified</span>
    </div>`).join('');
  $$('#jobs .jobrow').forEach(el => el.onclick = () => openJob(el.dataset.j));
}
async function boot(){
  let s;
  try{
    const r = await fetch('/api/status',{headers:H});
    if(r.status === 401 || r.status === 403){
      panel('Sign in to the workbench',
        'Nothing is wrong &mdash; this browser just has not been given the '
        + 'token yet. It is a one-time step.',
        'Fastest way: run this in PowerShell to copy the token, then paste it '
        + 'below.\n\n'
        + '    Get-Content \\\\wsl.localhost\\Ubuntu\\home\\fuad_ashraf'
        + '\\.engineai\\token | Set-Clipboard\n\n'
        + 'From WSL the same file is  ~/.engineai/token\n\n'
        + 'Or skip the box entirely and open the pre-filled link:\n\n'
        + '    Start-Process (Get-Content \\\\wsl.localhost\\Ubuntu\\home'
        + '\\fuad_ashraf\\engineai\\data\\workbench_url.txt)\n\n'
        + 'Either way the server sets an HttpOnly cookie, so after this, '
        + 'typing\n127.0.0.1:8100 on its own works in this browser.',
        {calm: true, signIn: true});
      return;
    }
    s = await r.json();
  }catch(e){
    fatal('The page loaded but could not reach the server.',
      String(e) + '\n\nIs workbench.py still running? Check with:\n'
      + '    pgrep -af workbench.py');
    return;
  }
  $('#envs').textContent = (s.cadquery ? 'CAD ready' : 'no cadquery');
  $('#aboutbody').innerHTML =
    `<p><b>Verified</b> on an artifact card means the file was written, then
     <i>re-opened and checked</i>: a STEP solid's re-imported volume, a DXF's
     entity count after an audit, a PDF's page count, a spreadsheet's formula
     recomputing after its input changes.</p>
     <p>The viewport badge is a different check. On every rebuild the
     triangulated volume is compared with the part's closed-form volume. It is
     live while you drag, so a kernel that produces a shape the algebra does not
     recognise turns it red immediately.</p>
     <p>The stress field is solved three ways &mdash; Euler-Bernoulli,
     Timoshenko and CalculiX &mdash; and the job <i>fails rather than renders</i>
     if they disagree by more than the mesh can explain.</p>
     <p>Measurements come off the B-rep, not the triangles. Mass is only
     reported for a material whose density is known; an unrecognised material
     gets no mass rather than a guessed one.</p>
     <p class="warn">The section plane cuts the <i>picture</i>, not the solid.
     The cut face is open, so a sectioned part looks hollow &mdash; do not read
     wall thickness off it.</p>
     <p>A scripted solid exports STEP, STL, its own source and its
     measurements. There is no closed form to check an arbitrary script
     against, so the STEP is <i>re-opened and re-measured</i> instead; if it
     does not read back at the volume it was written at, no file is offered.</p>
     <p class="bad">Still not here: no assembly tree, no dimension-driven
     sketch constraints, and no B-rep editing of an imported STEP, which stays
     view-only. The script editor runs Python in a child process with imports
     refused and CPU, memory and file-size ceilings applied. That bounds the
     <i>accidents</i> &mdash; a sweep that never converges, a loft that
     allocates until the machine swaps. It is <i>not</i> a security sandbox
     and must not be read as one: it is acceptable only because this binds
     127.0.0.1 for one trusted user.</p>
     <p>Nothing here calls a metered API.
     Solvers present: ${esc((s.solvers||[]).join(', ')||'none')}.</p>
     <p style="font-family:ui-monospace,monospace;font-size:11px;
        border-top:1px solid var(--line);padding-top:9px">
        ${esc(envLine())}</p>`;
  setInterval(()=>$('#clock').textContent =
    new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}), 1000);
  refreshQueue(); setInterval(refreshQueue, 4000);
  await loadParts();
  $('#tmpl').innerHTML = SCHEMA.map(x=>`<option>${esc(x.part)}</option>`).join('');
  $('#su_part').innerHTML = SCHEMA.map(x=>`<option>${esc(x.part)}</option>`).join('');
  if(SCHEMA.length) await loadStarter(SCHEMA[0].part);
  refreshJobs();
}
boot();
</script>
"""
