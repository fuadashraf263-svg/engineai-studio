#!/usr/bin/env python3
"""
dashboard.py -- the launch face: a read-only mission dashboard.

Zero dependencies (Python stdlib only), zero write paths (GET only) --
deliberately boring so it cannot corrupt the system it displays.

    python dashboard.py        ->  http://localhost:8090
                                   http://<desktop-ip>:8090  (laptop)

Pages:  /         scorecard + latest iteration + links
        /report   the mission report
        /iters    every recorded iteration
The live 3D sandbox stays at :8080 (viewer.py) -- link on the front page.
"""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCENE = Path.home() / "engineai" / "scene"
PORT = 8090

STYLE = ("<style>body{font-family:monospace;max-width:900px;margin:2em "
         "auto;background:#111;color:#ddd}a{color:#7cf}h1,h2{color:#fff}"
         "pre{background:#1a1a1a;padding:1em;overflow-x:auto}"
         "td,th{padding:.2em .8em;text-align:left}</style>")


def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def page_index() -> str:
    parts = [STYLE, "<h1>EngineAI</h1>",
             '<p><a href="/report">mission report</a> | '
             '<a href="/iters">all iterations</a> | '
             '3D sandbox: <a href="http://localhost:8080">:8080</a></p>']
    sc = _read_json(SCENE / "agi_scorecard.json")
    if sc:
        parts.append("<h2>AGI scorecard</h2><pre>"
                     + html.escape(json.dumps(sc, indent=2)) + "</pre>")
    latest = _read_json(SCENE / "latest.json")
    if latest:
        parts.append("<h2>latest iteration</h2><pre>"
                     + html.escape(json.dumps(latest, indent=2)) + "</pre>")
    else:
        parts.append("<p>no iterations yet -- run a mission.</p>")
    return "".join(parts)


def page_report() -> str:
    p = SCENE / "report.md"
    body = (html.escape(p.read_text()) if p.exists()
            else "no report yet -- run: python agent.py report")
    return STYLE + "<h1>report</h1><pre>" + body + "</pre>"


def page_iters() -> str:
    rows = ["<tr><th>iter</th><th>tool</th><th>mission</th>"
            "<th>parameters</th><th>key results</th></tr>"]
    for p in sorted(SCENE.glob("iter_*.json")):
        m = _read_json(p)
        if not m:
            continue
        r = m.get("result", {})
        keys = {k: round(v, 3) for k, v in r.items()
                if isinstance(v, (int, float))
                and k in ("max_von_mises_MPa", "mass_g", "f1_Hz",
                          "liquidus_K", "fc_Hz", "residual_pct",
                          "deflection_error_pct")}
        rows.append("<tr><td>{}</td><td>{}</td><td>{}</td>"
                    "<td>{}</td><td>{}</td></tr>".format(
                        m.get("iteration"), html.escape(str(m.get("tool"))),
                        html.escape(str(m.get("mission", ""))),
                        html.escape(str(m.get("parameters"))),
                        html.escape(str(keys))))
    return (STYLE + "<h1>iterations</h1><table>" + "".join(rows)
            + "</table>")


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        route = {"/": page_index, "/report": page_report,
                 "/iters": page_iters}.get(self.path.split("?")[0])
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        body = route().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                 # quiet
        pass


if __name__ == "__main__":
    print(f"EngineAI dashboard at http://localhost:{PORT}")
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
