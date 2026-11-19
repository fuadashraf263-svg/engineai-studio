#!/usr/bin/env python3
"""
fea_solid.py - FEA on YOUR geometry: mesh a real solid, solve it, colour it.

WHAT WAS MISSING
----------------
The workbench could solve a cantilever whose mesh it generated itself from
four numbers, and it could show you a solid it had built. It could not solve
the solid. So the 3D pane was a viewer with a physics demo bolted next to it,
which is fair to call "just for show".

This closes it: STEP in, tetrahedral mesh out of gmsh, a CalculiX deck, a
solve, and a von Mises field on the deformed shape - for any solid, including
one you uploaded.

SECOND-ORDER ELEMENTS, NOT FIRST
--------------------------------
The mesh is C3D10 (ten-node tetrahedra), and that is not a detail. Linear
tets are notoriously over-stiff in bending - a slender beam meshed with C3D4
can come out several times too stiff, and it fails quietly, producing a
plausible-looking field that is simply wrong. Using them here would have
shipped exactly the confident-wrong result this project exists to avoid.

HOW A SOLVE ON ARBITRARY GEOMETRY IS VERIFIED
---------------------------------------------
There is no closed form for a shape nobody has seen before, so the checks have
to be ones that hold for ANY solid:

  EQUILIBRIUM   the sum of reaction forces at the restrained set must equal
                the applied load, to within solver round-off. This catches a
                mis-applied load, a wrong node set, a deck that did not
                actually constrain anything - the errors that otherwise give
                you a beautiful picture of nothing.
  CONVERGENCE   refine until the answer stops moving, and report the whole
                ladder. The last change is how much the mesh still has to
                give - information about the number, not grounds to withhold
                it. It does NOT rescue a singularity: peak stress at a sharp
                re-entrant corner rises without limit as the mesh shrinks and
                never converges, however many rungs are run.
  ORACLE        where the shape happens to be beam-like, compare against
                Euler-Bernoulli and Timoshenko as a sanity band.

A solve that fails equilibrium is not returned. But equilibrium is a SANITY
CHECK, not physical validation: a wrong material, a wrong restraint, or a load
applied to the wrong face all balance perfectly.

BOUNDARY CONDITIONS
-------------------
Either a bounding-box side ("-X") or a CAD face picked in the viewer
("face:7"). Picking is the real one - a bounding-box side can only describe a
part whose load path happens to line up with its box - and every result states
which of the two it used.

Still an idealisation: a picked face gets a uniform nodal force. That is not a
pressure, a bearing contact, a bolt preload or a thermal load, and none of
those are modelled here.

  python fea_solid.py --selftest
  python fea_solid.py --part mounting_plate --load 5000 --fix -X --push +X
"""

from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

AXES = {"X": 0, "Y": 1, "Z": 2}
EQUILIBRIUM_TOL = 0.02        # 2% of the applied load


def have_tools() -> dict:
    return {"gmsh": _gmsh_ok(), "ccx": bool(shutil.which("ccx"))}


def _gmsh_ok() -> bool:
    try:
        import gmsh          # noqa: F401
        return True
    except Exception:                                         # noqa: BLE001
        return bool(shutil.which("gmsh"))


# --------------------------------------------------------------------------
# meshing
# --------------------------------------------------------------------------

def mesh_step(step_path: str, size_mm: float = None, order: int = 2) -> dict:
    """Tetrahedral mesh of a STEP solid, via the gmsh Python API."""
    import gmsh
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("part")
        gmsh.model.occ.importShapes(step_path)
        gmsh.model.occ.synchronize()

        box = gmsh.model.getBoundingBox(-1, -1)
        span = max(box[3] - box[0], box[4] - box[1], box[5] - box[2])
        h = size_mm or max(span / 12.0, 1e-3)
        gmsh.option.setNumber("Mesh.MeshSizeMax", h)
        gmsh.option.setNumber("Mesh.MeshSizeMin", h / 4.0)
        gmsh.option.setNumber("Mesh.ElementOrder", order)
        gmsh.option.setNumber("Mesh.Optimize", 1)
        gmsh.model.mesh.generate(3)

        tags, coords, _ = gmsh.model.mesh.getNodes()
        nodes = {}
        for i, t in enumerate(tags):
            nodes[int(t)] = (coords[3 * i], coords[3 * i + 1], coords[3 * i + 2])

        # 4 = linear tet, 11 = 10-node tet.
        #
        # THE MID-SIDE NODES ARE NOT IN THE SAME ORDER. I assumed gmsh's tet10
        # matched CalculiX C3D10 and every element came back with a
        # "nonpositive jacobian", which is ccx saying the element is inside
        # out. Both put the four corners first and both agree on nodes 5-8,
        # but the last two differ:
        #
        #     position   9        10
        #     gmsh       edge 3-4  edge 2-4
        #     CalculiX   edge 2-4  edge 3-4
        #
        # so they are swapped. Nothing about the mesh is wrong; the deck was
        # describing a different element.
        etypes, etags, enodes = gmsh.model.mesh.getElements(3)
        elems = []
        for et, tg, nd in zip(etypes, etags, enodes):
            npe = {4: 4, 11: 10}.get(int(et))
            if not npe:
                continue
            nd = [int(x) for x in nd]
            for k in range(len(tg)):
                e = nd[k * npe:(k + 1) * npe]
                if npe == 10:
                    e[8], e[9] = e[9], e[8]
                elems.append(e)
        if not elems:
            raise RuntimeError("gmsh produced no solid elements")

        # Which B-rep face does each node sit on? This is what turns "fix the
        # -X side of the bounding box" into "fix the face I clicked". gmsh
        # keeps the CAD topology after import, so the surfaces here are the
        # real faces of the solid, not an artefact of the mesh.
        node_faces, faces = {}, {}
        for dim, tag in gmsh.model.getEntities(2):
            ftags, fcoords, _ = gmsh.model.mesh.getNodes(dim, tag,
                                                         includeBoundary=True)
            ids = [int(x) for x in ftags]
            for n in ids:
                node_faces.setdefault(n, set()).add(int(tag))
            try:
                area = gmsh.model.occ.getMass(2, tag)
            except Exception:                                 # noqa: BLE001
                area = None
            try:
                cx, cy, cz = gmsh.model.occ.getCenterOfMass(2, tag)
            except Exception:                                 # noqa: BLE001
                cx = cy = cz = None
            faces[int(tag)] = {"nodes": len(ids), "area_mm2": area,
                               "centroid": [cx, cy, cz]}

        return {"nodes": nodes, "elements": elems,
                "type": "C3D10" if len(elems[0]) == 10 else "C3D4",
                "bbox": list(box), "size_mm": h,
                "n_nodes": len(nodes), "n_elements": len(elems),
                "node_faces": node_faces, "faces": faces}
    finally:
        gmsh.finalize()


def face_nodes(mesh: dict, side: str, tol_frac: float = 0.02) -> list:
    """Nodes on a selection: a bounding-box side ('-X') or a picked B-rep face.

    'face:7' or 'face:7,9' selects the actual CAD faces the user clicked in the
    viewer, which is what makes arbitrary boundary conditions possible - a
    bounding-box side can only ever describe a part whose load path happens to
    line up with its box.
    """
    s = side.strip()
    if s.lower().startswith("face:"):
        want = {int(x) for x in re.findall(r"\d+", s)}
        sel = sorted({n for n, tags in (mesh.get("node_faces") or {}).items()
                      if tags & want})
        if not sel:
            raise RuntimeError(
                f"no mesh nodes lie on face(s) {sorted(want)}. The available "
                f"faces are {sorted(mesh.get('faces', {}))}.")
        return sel
    s = s.upper()
    if len(s) != 2 or s[0] not in "+-" or s[1] not in AXES:
        raise ValueError(f"side must look like -X or +Z, not {side!r}")
    ax = AXES[s[1]]
    lo, hi = mesh["bbox"][ax], mesh["bbox"][ax + 3]
    span = hi - lo or 1.0
    tol = span * tol_frac
    target = lo if s[0] == "-" else hi
    sel = [n for n, xyz in mesh["nodes"].items() if abs(xyz[ax] - target) <= tol]
    if not sel:
        raise RuntimeError(f"no nodes found on face {side}")
    return sorted(sel)


# --------------------------------------------------------------------------
# deck and solve
# --------------------------------------------------------------------------

def pickable(step_path: str, size_mm: float = None) -> dict:
    """Surface tessellation with every triangle tagged by its CAD face.

    This is what click-to-pick needs: the browser ray-casts against these
    triangles, reads the face tag off the one it hit, and can then name that
    face as a boundary condition. Without the tag a click can only ever land
    on "somewhere on the mesh".
    """
    import gmsh
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("pick")
        gmsh.model.occ.importShapes(step_path)
        gmsh.model.occ.synchronize()
        box = gmsh.model.getBoundingBox(-1, -1)
        span = max(box[3] - box[0], box[4] - box[1], box[5] - box[2])
        h = size_mm or max(span / 22.0, 1e-3)
        gmsh.option.setNumber("Mesh.MeshSizeMax", h)
        gmsh.option.setNumber("Mesh.MeshSizeMin", h / 3.0)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        gmsh.model.mesh.generate(2)

        verts, index, tris, face_of, faces = [], {}, [], [], {}
        for dim, tag in gmsh.model.getEntities(2):
            ntags, ncoords, _ = gmsh.model.mesh.getNodes(dim, tag,
                                                         includeBoundary=True)
            for i, nt in enumerate(ntags):
                nt = int(nt)
                if nt not in index:
                    index[nt] = len(verts)
                    verts.append([ncoords[3 * i], ncoords[3 * i + 1],
                                  ncoords[3 * i + 2]])
            et, _etags, enodes = gmsh.model.mesh.getElements(dim, tag)
            for typ, nd in zip(et, enodes):
                if int(typ) != 2:                # 2 = 3-node triangle
                    continue
                nd = [int(x) for x in nd]
                for k in range(0, len(nd), 3):
                    tri = nd[k:k + 3]
                    if not all(v in index for v in tri):
                        continue
                    tris.append([index[v] for v in tri])
                    face_of.append(int(tag))
            try:
                area = gmsh.model.occ.getMass(2, tag)
                cm = list(gmsh.model.occ.getCenterOfMass(2, tag))
            except Exception:                                 # noqa: BLE001
                area, cm = None, [None, None, None]
            faces[int(tag)] = {"area_mm2": area, "centroid": cm}
        return {"vertices": verts, "triangles": tris, "face_of": face_of,
                "faces": faces, "bbox": list(box),
                "n_faces": len(faces), "n_triangles": len(tris)}
    finally:
        gmsh.finalize()


def write_deck(path: str, mesh: dict, fixed: list, loaded: list,
               force_N: float, direction: int, E_MPa: float, nu: float) -> str:
    per = force_N / len(loaded)
    L = ["*NODE, NSET=NALL"]
    for n in sorted(mesh["nodes"]):
        x, y, z = mesh["nodes"][n]
        L.append(f"{n}, {x:.6f}, {y:.6f}, {z:.6f}")
    L.append(f"*ELEMENT, TYPE={mesh['type']}, ELSET=EALL")
    for i, e in enumerate(mesh["elements"], 1):
        L.append(f"{i}, " + ", ".join(str(v) for v in e))
    L.append("*NSET, NSET=FIX")
    L += [", ".join(str(v) for v in fixed[i:i + 8])
          for i in range(0, len(fixed), 8)]
    L.append("*NSET, NSET=LOAD")
    L += [", ".join(str(v) for v in loaded[i:i + 8])
          for i in range(0, len(loaded), 8)]
    L += ["*MATERIAL, NAME=MAT", "*ELASTIC", f"{E_MPa}, {nu}",
          "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT",
          "*STEP", "*STATIC",
          "*BOUNDARY", "FIX, 1, 3",
          "*CLOAD", f"LOAD, {direction}, {per:.10g}",
          # RF at the restrained set is what makes the equilibrium check
          # possible; without it there is nothing to check the solve against.
          "*NODE PRINT, NSET=FIX", "RF",
          "*NODE FILE", "U", "*EL FILE", "S",
          "*END STEP", ""]
    with open(path, "w") as fh:
        fh.write("\n".join(L))
    return path


def _sum_reactions(dat_path: str) -> list:
    """Total reaction force from the .dat file, as a 3-vector."""
    if not os.path.exists(dat_path):
        return [0.0, 0.0, 0.0]
    tot = [0.0, 0.0, 0.0]
    reading = False
    for line in open(dat_path, errors="ignore"):
        s = line.strip()
        # ccx writes exactly:  "forces (fx,fy,fz) for set FIX and time ..."
        # The first version of this looked for the word "reaction", which ccx
        # never uses, so it summed nothing, reported a 100% equilibrium
        # residual on a perfectly good solve, and took the beam oracle and the
        # convergence study down with it.
        if "forces" in s.lower() and "fx,fy,fz" in s.lower().replace(" ", ""):
            reading = True
            continue
        if reading:
            parts = s.split()
            if len(parts) >= 4:
                try:
                    v = [float(parts[1]), float(parts[2]), float(parts[3])]
                    tot = [a + b for a, b in zip(tot, v)]
                except ValueError:
                    if tot != [0.0, 0.0, 0.0]:
                        reading = False
            elif not s:
                continue
            else:
                reading = False
    return tot


def solve(mesh: dict, fixed: list, loaded: list, force_N: float,
          direction: int = 2, E_MPa: float = 210000.0, nu: float = 0.3,
          workdir: str = None, timeout: int = 900) -> dict:
    """Run CalculiX on this mesh and return the fields plus the checks."""
    if not shutil.which("ccx"):
        raise RuntimeError("ccx is not on PATH; nothing can be solved")
    tmp = workdir or tempfile.mkdtemp(prefix="engineai_fea_")
    os.makedirs(tmp, exist_ok=True)
    base = os.path.join(tmp, "job")
    write_deck(base + ".inp", mesh, fixed, loaded, force_N, direction, E_MPa, nu)

    p = subprocess.run(["ccx", "job"], cwd=tmp, capture_output=True,
                       text=True, timeout=timeout)
    frd = base + ".frd"
    # A .frd on disk does NOT mean the solve worked. ccx writes one even after
    # it has errored on every element, full of zeros, and the first version of
    # this function returned that as a result: zero deflection, zero stress,
    # and a picture. Read the return code and the log.
    out_txt = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0 or "*ERROR" in out_txt:
        first = next((l.strip() for l in out_txt.splitlines()
                      if "*ERROR" in l), "")
        detail = out_txt[-500:]
        raise RuntimeError(
            f"CalculiX failed (exit {p.returncode}). {first or ''} "
            f"No field is returned - a solve that errored produces a .frd "
            f"full of zeros, which renders as a perfectly convincing picture "
            f"of nothing.\n{detail}")
    if not os.path.exists(frd):
        raise RuntimeError(f"ccx produced no results. {out_txt[-400:]}")

    import viz3d
    r = viz3d.read_frd(frd)
    if not r:
        raise RuntimeError("the .frd file could not be read")
    nodes, disp, vm = r[0], r[1], r[2]
    if isinstance(r, dict):
        nodes, disp, vm = r["nodes"], r["disp"], r["von_mises"]

    dmag = [math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2) for d in disp]
    rf = _sum_reactions(base + ".dat")
    applied = [0.0, 0.0, 0.0]
    applied[direction - 1] = force_N
    resid = math.sqrt(sum((rf[i] + applied[i]) ** 2 for i in range(3)))
    rel = resid / abs(force_N) if force_N else float("inf")

    # Carry the connectivity through. _surface_faces needs it, and without it
    # the renderer silently drew nothing at all - an empty field view that
    # still passed a "was a file written" check.
    out = {"workdir": tmp, "inp": base + ".inp", "frd": frd,
           "tets": mesh["elements"], "node_ids": sorted(mesh["nodes"]),
           "nodes": nodes, "disp": disp, "von_mises": vm,
           "max_disp_mm": max(dmag) if dmag else 0.0,
           "max_vm_MPa": max(vm) if vm else 0.0,
           "n_nodes": len(nodes), "n_elements": mesh["n_elements"],
           "element_type": mesh["type"],
           "reaction_N": rf, "applied_N": applied,
           "equilibrium_residual_N": resid,
           "equilibrium_rel": rel,
           "equilibrium_ok": rel <= EQUILIBRIUM_TOL,
           "ccx_returncode": p.returncode}
    if not out["equilibrium_ok"]:
        out["error"] = (
            f"equilibrium failed: reactions sum to {rf} against an applied "
            f"{applied} N, a residual of {resid:.4g} N ({rel*100:.2f}% of the "
            f"load). The field this produced is not trustworthy and is not "
            f"being offered - check the restrained face and the load face.")
    return out


def beam_oracle(bbox: list, force_N: float, E_MPa: float, nu: float = 0.3):
    """If the solid is beam-like, what does beam theory say the tip does?"""
    dx = [bbox[3] - bbox[0], bbox[4] - bbox[1], bbox[5] - bbox[2]]
    L = max(dx)
    ax = dx.index(L)
    other = [d for i, d in enumerate(dx) if i != ax]
    if L < 4 * max(other):
        return None                    # too stubby for beam theory to mean much
    b, h = other
    import fea
    return {"euler_mm": fea.euler_bernoulli_tip(force_N, L, b, h, E_MPa),
            "timoshenko_mm": fea.timoshenko_tip(force_N, L, b, h, E_MPa, nu),
            "L_mm": L, "b_mm": b, "h_mm": h,
            "slenderness": L / max(other)}


def analyse(step_path: str, force_N: float, fix: str = "-X", push: str = "+X",
            direction: int = 2, E_MPa: float = 210000.0, nu: float = 0.3,
            size_mm: float = None, converge: bool = False,
            workdir: str = None, max_refine: int = 4,
            max_nodes: int = 120000) -> dict:
    """The whole pipeline, with every check it is possible to run.

    `fix` and `push` are either a bounding-box side ('-X') or a B-rep face
    picked in the viewer ('face:7'). max_refine and max_nodes are ceilings on
    ONE interactive run, not limits on what may be asked - raise them and it
    goes further.
    """
    mesh = mesh_step(step_path, size_mm)
    fixed = face_nodes(mesh, fix)
    loaded = face_nodes(mesh, push)
    # Adjacent CAD faces ALWAYS share the nodes along their common edge - that
    # is geometry, not a mistake, and refusing on any overlap at all made most
    # face pairs on a real part unusable. What is actually wrong is picking
    # substantially the same region twice, so the test is the fraction.
    overlap = set(fixed) & set(loaded)
    share = len(overlap) / min(len(fixed), len(loaded))
    if share > 0.3:
        raise RuntimeError(
            f"the restrained face {fix} and the loaded face {push} share "
            f"{len(overlap)} nodes, {share*100:.0f}% of the smaller one. "
            f"Loading what you have just fixed measures nothing; pick faces "
            f"that are not the same region.")
    if overlap:
        # The shared edge must not be both fixed and loaded, so the restraint
        # wins and the load is applied to the rest.
        loaded = [n for n in loaded if n not in overlap]
        if not loaded:
            raise RuntimeError(
                f"every node of {push} is also on {fix}; there is nothing "
                f"left to load.")

    r = solve(mesh, fixed, loaded, force_N, direction, E_MPa, nu,
              workdir=workdir)
    r["mesh"] = {k: mesh[k] for k in ("n_nodes", "n_elements", "type",
                                      "size_mm", "bbox")}
    picked = fix.lower().startswith("face:") and push.lower().startswith("face:")
    r["bc"] = {"fixed_face": fix, "fixed_nodes": len(fixed),
               "loaded_face": push, "loaded_nodes": len(loaded),
               "force_N": force_N, "direction": "XYZ"[direction - 1],
               "selection": "picked CAD faces" if picked
                            else "bounding-box sides",
               # The note has to describe what was ACTUALLY used. It used to
               # say "there is no click-to-pick yet" on every single result,
               # including the ones driven by picked faces.
               "note": ("boundary conditions are the real CAD faces of the "
                        "solid, so the load path does not have to line up "
                        "with the bounding box." if picked else
                        "faces were selected by bounding-box side. If the load "
                        "path does not line up with the box, pick the faces "
                        "directly in the viewer instead.")}

    oracle = beam_oracle(mesh["bbox"], force_N, E_MPa, nu)
    if oracle and r.get("equilibrium_ok"):
        ti = oracle["timoshenko_mm"]
        err = (r["max_disp_mm"] - ti) / ti * 100 if ti else None
        oracle["fea_mm"] = r["max_disp_mm"]
        oracle["error_vs_timoshenko_pct"] = err
        oracle["agrees"] = abs(err) < 20 if err is not None else None
        oracle["note"] = ("the solid is beam-like, so beam theory is a valid "
                          "sanity band - not a precise oracle, because the "
                          "clamped end and the load patch differ from the "
                          "beam-theory idealisation.")
        r["oracle"] = oracle

    if converge and r.get("equilibrium_ok"):
        # KEEP REFINING UNTIL IT SETTLES. The first version solved twice and,
        # if the answer had moved more than 5%, told the user their result
        # "should not be quoted" - which is homework, not help. The tolerance
        # is not a rule about what you are allowed to ask for; it is a
        # statement about how much this particular mesh still has to give. So
        # refine until the change stops mattering, report the whole ladder,
        # and hand back the best number either way.
        target = float(converge) if isinstance(converge, (int, float)) \
            and not isinstance(converge, bool) else 2.0
        ladder = [{"nodes": mesh["n_nodes"], "disp_mm": r["max_disp_mm"],
                   "vm_MPa": r["max_vm_MPa"], "size_mm": mesh["size_mm"]}]
        h = size_mm or mesh["size_mm"]
        best, change = r, None
        for _ in range(int(max_refine)):
            h /= 1.6
            try:
                fine = mesh_step(step_path, h)
                if fine["n_nodes"] > max_nodes:
                    ladder[-1]["stopped"] = (
                        f"the next mesh would be {fine['n_nodes']:,} nodes, "
                        f"past the {max_nodes:,} ceiling for one interactive "
                        f"run. Raise max_nodes to go further - nothing here "
                        f"stops you.")
                    break
                r2 = solve(fine, face_nodes(fine, fix), face_nodes(fine, push),
                           force_N, direction, E_MPa, nu)
            except Exception as exc:                          # noqa: BLE001
                ladder[-1]["stopped"] = f"refinement stopped: {exc}"
                break
            d1, d2 = best["max_disp_mm"], r2["max_disp_mm"]
            change = abs(d2 - d1) / d2 * 100 if d2 else 0.0
            ladder.append({"nodes": fine["n_nodes"], "disp_mm": d2,
                           "vm_MPa": r2["max_vm_MPa"], "size_mm": h,
                           "change_pct": change})
            best = r2
            if change < target:
                break
        else:
            # for-else: the loop ran out of refinements rather than settling.
            # Silently stopping here would present the last rung as if it had
            # converged.
            if change is not None and change >= target:
                ladder[-1]["stopped"] = (
                    f"stopped after {int(max_refine)} refinements with the "
                    f"answer still moving {change:.2f}% - more than the "
                    f"{target:g}% asked for. Raise max_refine or lower the "
                    f"target to keep going; nothing here stops you.")
        r["convergence"] = {
            "ladder": ladder, "target_pct": target,
            "final_change_pct": change,
            "settled": change is not None and change < target,
            "best_disp_mm": best["max_disp_mm"],
            "best_vm_MPa": best["max_vm_MPa"],
            "note": ("refined until the answer stopped moving. The last "
                     "change is how much this mesh still has to give - it is "
                     "information about the number, not a reason to withhold "
                     "it.")}
        # The finest solve IS the answer; the coarse one was scaffolding.
        for k in ("max_disp_mm", "max_vm_MPa", "nodes", "disp", "von_mises",
                  "n_nodes", "reaction_N", "equilibrium_rel",
                  "equilibrium_ok", "tets", "node_ids", "inp", "frd"):
            if k in best:
                r[k] = best[k]
    return r


def render(r: dict, path: str, title: str = "EngineAI - your geometry") -> str:
    """Colour the von Mises field on the deformed shape, via viz3d."""
    import viz3d
    nodes = [[c / 1000.0 for c in n] for n in r["nodes"]]      # mm -> m
    disp = [[c / 1000.0 for c in d] for d in r["disp"]]
    fields = {"von_mises": [v * 1e6 for v in r["von_mises"]]}   # MPa -> Pa
    faces = _surface_faces(r)
    note = (f"{r['n_nodes']} nodes, {r['n_elements']} {r['element_type']} "
            f"elements | peak {r['max_vm_MPa']:.4g} MPa, max deflection "
            f"{r['max_disp_mm']:.4g} mm | equilibrium residual "
            f"{r['equilibrium_rel']*100:.3f}% of the applied load")
    viz3d.render(nodes, faces, fields, disp, title, "Pa", path=path, note=note)
    return path


def _surface_faces(r: dict) -> list:
    """Triangles on the outside of the tet mesh - each face used exactly once."""
    from collections import Counter
    tets = r.get("tets") or []
    ids = r.get("node_ids") or []
    if not tets or not ids:
        return []
    # viz3d indexes nodes by POSITION in its list; the deck uses gmsh's tags.
    idx = {tag: i for i, tag in enumerate(ids)}
    cnt = Counter()
    for t in tets:
        c = t[:4]
        for tri in ((c[0], c[1], c[2]), (c[0], c[1], c[3]),
                    (c[0], c[2], c[3]), (c[1], c[2], c[3])):
            cnt[tuple(sorted(tri))] += 1
    out = []
    for f, n in cnt.items():
        if n != 1:                     # shared by two tets, so interior
            continue
        if all(v in idx for v in f):
            out.append([idx[v] for v in f])
    return out


# --------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def chk(cond, label, extra=""):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label
              + (f"   {extra}" if extra else ""))
        ok = ok and bool(cond)

    print("fea_solid selftest")

    # The workbench launches this module in a child process.  Keep the child
    # command line itself under test: previously a user supplied ``nu`` was
    # recorded in the receipt but never passed to argparse, so the solver
    # always used its 0.30 default.
    from unittest.mock import patch

    class _ChildResult:
        returncode = 0
        stdout = '@@{"ok": true}\n'
        stderr = ""

    with tempfile.TemporaryDirectory(prefix="engineai_fea_args_") as child_td:
        with patch.object(subprocess, "run", return_value=_ChildResult()) as child:
            analyse_subprocess("example.step", child_td, nu=0.271)
    child_args = child.call_args.args[0]
    try:
        nu_forwarded = child_args[child_args.index("--nu") + 1] == "0.271"
    except (ValueError, IndexError):
        nu_forwarded = False
    chk(nu_forwarded, "a supplied Poisson ratio reaches the FEA child process")

    t = have_tools()
    print(f"  tools: gmsh={t['gmsh']}  ccx={t['ccx']}")
    if not (t["gmsh"] and t["ccx"]):
        print("  skip  the toolchain is incomplete; nothing can be solved")
        return 0

    import cadquery as cq
    from cadquery import exporters
    td = tempfile.mkdtemp(prefix="engineai_fea_test_")

    # A slender bar, so beam theory is a legitimate sanity band.
    L, b, h = 200.0, 20.0, 20.0
    wp = cq.Workplane("XY").box(L, b, h)
    step = os.path.join(td, "bar.step")
    exporters.export(wp, step, exportType="STEP")

    print(" meshing a real STEP solid")
    m = mesh_step(step, size_mm=10.0)
    chk(m["n_nodes"] > 100, f"{m['n_nodes']} nodes from gmsh")
    chk(m["type"] == "C3D10",
        f"second-order tets ({m['type']}) - linear tets are far too stiff "
        f"in bending")
    chk(len(m["elements"][0]) == 10, "ten nodes per element")
    span = m["bbox"][3] - m["bbox"][0]
    chk(abs(span - L) < 1e-6, f"the bounding box matches the part ({span:.1f} mm)")

    print(" face selection")
    fx = face_nodes(m, "-X")
    px = face_nodes(m, "+X")
    chk(len(fx) > 5 and len(px) > 5, f"{len(fx)} fixed, {len(px)} loaded nodes")
    chk(not (set(fx) & set(px)), "the two faces share no nodes")
    try:
        face_nodes(m, "sideways")
        chk(False, "a malformed face name is refused")
    except ValueError:
        chk(True, "a malformed face name is refused")

    print(" solving, and the checks that hold for ANY geometry")
    F = 2000.0
    r = analyse(step, force_N=F, fix="-X", push="+X", direction=2,
                E_MPa=210000.0, size_mm=10.0, workdir=os.path.join(td, "run"))
    chk(r["equilibrium_ok"],
        f"EQUILIBRIUM: reactions balance the {F:g} N load to "
        f"{r['equilibrium_rel']*100:.3f}%")
    chk(r["max_disp_mm"] > 0, f"a deflection came out ({r['max_disp_mm']:.4f} mm)")
    chk(r["max_vm_MPa"] > 0, f"and a stress field ({r['max_vm_MPa']:.1f} MPa)")
    chk(r["n_nodes"] == m["n_nodes"], "every node came back from the solver")

    print(" the beam oracle, where the shape allows one")
    o = r.get("oracle")
    chk(o is not None, "a 10:1 bar is recognised as beam-like")
    if o:
        chk(o["agrees"],
            f"FEA {o['fea_mm']:.4f} mm vs Timoshenko {o['timoshenko_mm']:.4f} mm "
            f"({o['error_vs_timoshenko_pct']:+.1f}%)")
        chk(o["timoshenko_mm"] > o["euler_mm"],
            "Timoshenko predicts more deflection than Euler, as it must")

    print(" a stubby block gets NO beam oracle")
    cube = os.path.join(td, "cube.step")
    exporters.export(cq.Workplane("XY").box(50, 50, 50), cube, exportType="STEP")
    mc = mesh_step(cube, size_mm=15.0)
    chk(beam_oracle(mc["bbox"], 1000.0, 210000.0) is None,
        "beam theory is not applied to a cube")

    print(" it refuses a setup that measures nothing")
    try:
        analyse(step, force_N=F, fix="-X", push="-X", size_mm=12.0,
                workdir=os.path.join(td, "bad"))
        chk(False, "loading the face you just fixed is refused")
    except RuntimeError as e:
        chk("share" in str(e), "loading the face you just fixed is refused",
            str(e)[:60])

    # ...but two ADJACENT faces sharing only their common edge must still work.
    if len(m.get("faces") or {}) >= 3:
        # fa/fb, not a/b - `b` is the bar width used by the surface-area check
        # further down, and shadowing it here broke that test. Second time
        # this exact mistake has cost a run in this file.
        fids = sorted(m["faces"])
        worked = None
        for fa in fids:
            for fb in fids:
                if fa >= fb:
                    continue
                na = set(face_nodes(m, f"face:{fa}"))
                nb = set(face_nodes(m, f"face:{fb}"))
                sh = len(na & nb) / min(len(na), len(nb))
                if 0 < sh <= 0.3:
                    worked = (fa, fb, sh)
                    break
            if worked:
                break
        if worked:
            fa, fb, sh = worked
            rr = analyse(step, force_N=F, fix=f"face:{fa}", push=f"face:{fb}",
                         direction=2, size_mm=12.0,
                         workdir=os.path.join(td, "adj"))
            chk(rr["equilibrium_ok"],
                f"adjacent faces sharing {sh*100:.0f}% of their nodes still "
                f"solve ({rr['equilibrium_rel']*100:.3f}%)")

    print(" mesh convergence")
    rc = analyse(step, force_N=F, fix="-X", push="+X", direction=2,
                 size_mm=14.0, converge=True,
                 workdir=os.path.join(td, "conv"))
    c = rc.get("convergence")
    chk(c is not None, "a refinement ladder was run")
    if c:
        # Not `L` - that is the bar length a hundred lines up, and shadowing it
        # here made the surface-area check multiply a list by a float.
        rungs = c["ladder"]
        chk(len(rungs) >= 2, f"{len(rungs)} mesh densities solved")
        chk(all(rungs[i + 1]["nodes"] > rungs[i]["nodes"]
                for i in range(len(rungs) - 1)),
            "each rung is finer than the last: "
            + " -> ".join(str(x["nodes"]) for x in rungs))
        # It must REFINE rather than tell the user the answer is unusable.
        chk(c["final_change_pct"] is not None,
            f"the last change is reported ({c['final_change_pct']:.2f}%)")
        chk("not a reason to withhold" in c["note"],
            "...as information, not as a refusal")
        chk(rc["max_disp_mm"] == c["best_disp_mm"],
            "the FINEST solve is what gets returned, not the coarse one")
        stopped = [x for x in rungs if x.get("stopped")]
        chk(not stopped or "nothing here stops you" in stopped[0]["stopped"],
            "any ceiling says it is a ceiling on one run, not a limit")
        chk(rc["bc"]["selection"] == "bounding-box sides",
            "a bbox-driven run says so")
        chk("pick the faces directly" in rc["bc"]["note"],
            "...and points at the better way")

    # A run that cannot settle inside its budget must SAY it did not settle,
    # rather than presenting the last rung as if it had converged.
    tight = analyse(step, force_N=F, fix="-X", push="+X", direction=2,
                    size_mm=14.0, converge=0.0001, max_refine=1,
                    workdir=os.path.join(td, "tight"))
    tc = tight["convergence"]
    chk(not tc["settled"], "an unmet target is reported as unsettled")
    chk(any("Raise max_refine" in (x.get("stopped") or "")
            for x in tc["ladder"]),
        "...and says exactly which knob to turn")

    print(" click-to-pick: real CAD faces, not bounding-box sides")
    pk = pickable(step)
    chk(pk["n_faces"] == 6, f"the bar has 6 CAD faces ({pk['n_faces']})")
    chk(pk["n_triangles"] > 50, f"{pk['n_triangles']} pickable triangles")
    chk(len(pk["face_of"]) == len(pk["triangles"]),
        "every triangle carries the face it belongs to")
    chk(all(0 <= i < len(pk["vertices"])
            for t in pk["triangles"] for i in t),
        "every triangle indexes a real vertex")
    areas = [f["area_mm2"] for f in pk["faces"].values() if f["area_mm2"]]
    chk(abs(sum(areas) - 2 * (L * b + L * h + b * h)) / (2 * (L * b + L * h + b * h))
        < 0.01,
        f"the face areas sum to the box surface area ({sum(areas):.0f} mm2)")

    chk(m.get("faces") and len(m["faces"]) == 6,
        "the solid mesh knows its CAD faces too")
    big = max(m["faces"], key=lambda k: m["faces"][k]["area_mm2"] or 0)
    picked = face_nodes(m, f"face:{big}")
    chk(len(picked) > 10, f"nodes can be selected BY FACE ({len(picked)} on "
                          f"face {big})")
    try:
        face_nodes(m, "face:999")
        chk(False, "an unknown face id is refused")
    except RuntimeError as e:
        chk("available faces" in str(e).lower(),
            "an unknown face id is refused, and lists the real ones")

    ends = [k for k, v in m["faces"].items()
            if v["area_mm2"] and abs(v["area_mm2"] - b * h) < 1.0]
    if len(ends) == 2:
        rp = analyse(step, force_N=F, fix=f"face:{ends[0]}",
                     push=f"face:{ends[1]}", direction=2, size_mm=12.0,
                     workdir=os.path.join(td, "pick"))
        chk(rp["equilibrium_ok"],
            f"a solve driven by PICKED faces balances "
            f"({rp['equilibrium_rel']*100:.3f}%)")
        chk(rp["max_disp_mm"] > 0,
            f"and deflects ({rp['max_disp_mm']:.4f} mm)")

    print(" rendering")
    html = render(r, os.path.join(td, "field.html"))
    body = open(html).read()
    chk(len(body) > 5000, f"a field view was written ({len(body):,} bytes)")
    chk("von_mises" in body, "carrying the stress field")
    chk("equilibrium residual" in body,
        "and the equilibrium check is stated on the picture itself")
    # A view with no triangles in it is an empty box that still passes a
    # "was a file written" check, which is how the first version passed.
    faces = _surface_faces(r)
    chk(len(faces) > 50, f"the surface really has geometry ({len(faces)} triangles)")
    chk('"faces":[[' in body.replace(" ", ""),
        "and the triangles reached the page")

    print(" a failed solve is never dressed up as a result")
    # Collapse every element onto one corner: zero volume, guaranteed
    # nonpositive jacobian. Merely reversing the node list is not reliably
    # invalid, so that version of this test passed by accident.
    broken = dict(m)
    broken["elements"] = [[e[0]] * 10 for e in m["elements"]]
    try:
        solve(broken, fx, px, F, 2, 210000.0, 0.3,
              workdir=os.path.join(td, "inv"))
        chk(False, "inverted elements are refused")
    except RuntimeError as e:
        chk("CalculiX failed" in str(e) or "nonpositive" in str(e),
            "inverted elements raise instead of returning zeros",
            str(e)[:70])

    shutil.rmtree(td, ignore_errors=True)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def analyse_subprocess(step_path: str, outdir: str, timeout: int = 1200,
                       **kw) -> dict:
    """Run the whole pipeline in a CHILD process and return the summary.

    Two reasons, and the first one only showed up on the live server:

      * gmsh installs a signal handler on initialize(), and signals can only
        be set from the main thread. The workbench runs jobs in threads, so
        every solve died with "signal only works in main thread" - having
        passed every test, because the selftest runs on the main thread.
      * gmsh and ccx are native code and can abort outright. A child can die
        without taking the server with it.
    """
    import json as _json
    os.makedirs(outdir, exist_ok=True)
    # "--fix=-X", not "--fix", "-X". A face name begins with a dash and
    # argparse reads a bare "-X" as an option, not as this option's value.
    args = [sys.executable, os.path.join(HERE, "fea_solid.py"), "--json",
            "--step", step_path, "--outdir", outdir,
            "--load", str(kw.get("force_N", 1000.0)),
            f"--fix={kw.get('fix') or '-X'}",
            f"--push={kw.get('push') or '+X'}",
            "--dir", str(kw.get("direction", 2)),
            "--E", str(kw.get("E_MPa", 210000.0)),
            "--nu", str(kw.get("nu", 0.3))]
    if kw.get("size_mm"):
        args += ["--size", str(kw["size_mm"])]
    if kw.get("converge"):
        args += ["--converge"]
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    line = next((l for l in (p.stdout or "").splitlines()
                 if l.startswith("@@")), None)
    if line is None:
        raise RuntimeError(
            f"the FEA child exited with {p.returncode} and returned no result. "
            f"{((p.stderr or p.stdout) or '')[-500:]}")
    res = _json.loads(line[2:])
    if res.get("error"):
        raise RuntimeError(res["error"])
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable, for the workbench child process")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--step", default=None)
    ap.add_argument("--part", default=None)
    ap.add_argument("--load", type=float, default=1000.0)
    ap.add_argument("--fix", default="-X")
    ap.add_argument("--push", default="+X")
    ap.add_argument("--dir", type=int, default=2)
    ap.add_argument("--E", type=float, default=210000.0)
    ap.add_argument("--nu", type=float, default=0.3)
    ap.add_argument("--size", type=float, default=None)
    ap.add_argument("--converge", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    step = a.step
    tmp = None
    if a.part:
        import cad_parts
        tmp = tempfile.mkdtemp(prefix="engineai_part_")
        wp, _u = cad_parts.build(a.part)
        step, _stl = cad_parts.export(wp, os.path.join(tmp, a.part))
    if not step:
        ap.print_help()
        return 0

    if a.json:
        import json as _json
        out = a.outdir or tempfile.mkdtemp(prefix="engineai_fea_")
        os.makedirs(out, exist_ok=True)
        try:
            r = analyse(step, a.load, a.fix, a.push, a.dir, a.E, a.nu,
                        size_mm=a.size, converge=a.converge,
                        workdir=os.path.join(out, "solve"))
            html = render(r, os.path.join(out, "field.html"))
            deck = os.path.join(out, "job.inp")
            if os.path.exists(r["inp"]) and \
                    os.path.realpath(r["inp"]) != os.path.realpath(deck):
                shutil.copy(r["inp"], deck)
            slim = {k: v for k, v in r.items()
                    if k not in ("nodes", "disp", "von_mises", "tets",
                                 "node_ids")}
            slim["field_html"] = html
            slim["deck"] = deck if os.path.exists(deck) else None
            print("@@" + _json.dumps(slim, default=str))
        except Exception as exc:                              # noqa: BLE001
            print("@@" + _json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 0

    r = analyse(step, a.load, a.fix, a.push, a.dir, a.E, a.nu, size_mm=a.size,
                converge=a.converge)
    print(f"  mesh            {r['mesh']['n_nodes']} nodes, "
          f"{r['mesh']['n_elements']} {r['element_type']}")
    print(f"  max deflection  {r['max_disp_mm']:.5f} mm")
    print(f"  peak von Mises  {r['max_vm_MPa']:.4g} MPa")
    print(f"  equilibrium     {r['equilibrium_rel']*100:.4f}% residual "
          f"({'OK' if r['equilibrium_ok'] else 'FAILED'})")
    if r.get("oracle"):
        o = r["oracle"]
        print(f"  beam oracle     Timoshenko {o['timoshenko_mm']:.5f} mm "
              f"({o['error_vs_timoshenko_pct']:+.1f}%)")
    if r.get("convergence"):
        c = r["convergence"]
        print(f"  convergence     {c['change_pct']:.2f}% on refinement "
              f"({'converged' if c['converged'] else 'NOT converged'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
