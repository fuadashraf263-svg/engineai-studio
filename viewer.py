#!/usr/bin/env python3
"""
viewer.py (v2) -- the live 3D sandbox, now multi-physics aware.

Auto-detects what each iteration IS: stress field (structural), temperature
field (thermal), or mode shape (modal) -- and colors accordingly.

    conda activate eng && python viewer.py
    -> http://localhost:8080   (or http://<desktop-ip>:8080 from the laptop)
"""

from __future__ import annotations

import json
from pathlib import Path

import pyvista as pv
from pyvista.trame.ui import plotter_ui
from trame.app import get_server, asynchronous
from trame.ui.vuetify3 import SinglePageLayout

SCENE = Path.home() / "engineai" / "scene"
POLL_S = 2.0
FIELD_PRIORITY = [("von_mises", "von Mises (MPa)", "turbo"),
                  ("temperature", "Temperature (K)", "coolwarm"),
                  ("displacement_magnitude", "Mode shape |u|", "viridis")]

pv.OFF_SCREEN = True
server = get_server()
state, ctrl = server.state, server.controller
pl = pv.Plotter(off_screen=True)
pl.set_background("white")
state.info_text = "waiting for first iteration ..."
_last = 0.0


def load_latest():
    global _last
    mpath = SCENE / "latest.json"
    if not mpath.exists():
        return False
    mt = mpath.stat().st_mtime
    if mt <= _last:
        return False
    try:
        man = json.loads(mpath.read_text())
        mesh = pv.read(SCENE / man["mesh_file"])
    except Exception:
        return False
    _last = mt

    scalars, title, cmap = None, "", "turbo"
    for key, t, c in FIELD_PRIORITY:
        if key in mesh.point_data:
            scalars, title, cmap = key, t, c
            break

    pl.clear()
    pl.add_mesh(mesh, scalars=scalars, cmap=cmap, show_edges=False,
                scalar_bar_args=dict(title=title))
    pl.add_axes()
    pl.reset_camera()

    r, p = man.get("result", {}), man.get("parameters", {})
    bits = [f"iter {man.get('iteration')}", man.get("tool", ""),
            man.get("mission", "")]
    for label, key, fmt in (("D", "outer_diameter", "{:.2f}mm"),
                            ("t", "wall", "{:.2f}mm"),
                            ("d", "diameter", "{:.2f}mm")):
        if key in p:
            bits.append(f"{label}={fmt.format(p[key])}")
    for key, fmt in (("max_von_mises_MPa", "vm={:.1f}MPa"),
                     ("mass_g", "m={:.2f}g"),
                     ("f1_Hz", "f1={:.1f}Hz"),
                     ("max_profile_deviation_K", "dT={:.2f}K")):
        if key in r:
            bits.append(fmt.format(r[key]))
    state.info_text = "   |   ".join(str(b) for b in bits if b)
    return True


@asynchronous.task
async def watch():
    import asyncio
    while True:
        with state:
            if load_latest():
                ctrl.view_update()
        await asyncio.sleep(POLL_S)


with SinglePageLayout(server) as layout:
    layout.title.set_text("EngineAI Sandbox v2")
    with layout.toolbar:
        from trame.widgets import vuetify3 as v3
        v3.VSpacer()
        v3.VLabel("{{ info_text }}")
    with layout.content:
        view = plotter_ui(pl)
        ctrl.view_update = view.update

if __name__ == "__main__":
    load_latest()
    watch()
    print("Sandbox v2 at  http://localhost:8080")
    server.start()
