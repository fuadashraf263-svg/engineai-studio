#!/usr/bin/env python3
"""
engine_tools.py (v2) -- the body: multi-domain tool library + registry.

ACTIVE DOMAINS TONIGHT (all verified against analytical solutions):
    structural   cantilever / lattice_strut / hollow_strut   (CalculiX static)
    thermal      heat_rod                                    (CalculiX steady heat)
    modal        modal_beam                                  (CalculiX frequency)

SCAFFOLDED DOMAINS (registry knows them; wrappers activate when the
backend is installed -- see install_domains.sh):
    cfd, materials_thermo, electromagnetics, circuits, multibody,
    optimization, am_slicing

Contract (unchanged from v1, now enforced across three physics):
    files between stages -- flat JSON result dicts -- scene artifacts --
    an analytical cross-check inside every tool that has one.

Units everywhere: mm, N, MPa, tonne/mm^3, W, K, s.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HOME = Path.home() / "engineai"
SCENE = HOME / "scene"
WORK = HOME / "work"

MAX_NODES = 350_000          # 16 GB guard: fail loudly, not by freezing
GMSH_TO_CCX_TET10 = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]


# ---------------------------------------------------------------- geometry --

def geom_box(length, width, height, step: Path):
    import cadquery as cq
    cq.exporters.export(
        cq.Workplane("XY").box(length, width, height,
                               centered=(False, True, True)), str(step))


def geom_cylinder(length, diameter, step: Path):
    import cadquery as cq
    cq.exporters.export(
        cq.Workplane("YZ").circle(diameter / 2).extrude(length), str(step))


def geom_tube(length, outer_d, wall, step: Path) -> str:
    """Annulus extrude. Falls back to solid if the bore would vanish."""
    import cadquery as cq
    inner_d = outer_d - 2.0 * wall
    if inner_d < 0.8:
        geom_cylinder(length, outer_d, step)
        return "solid"
    cq.exporters.export(
        cq.Workplane("YZ").circle(outer_d / 2).circle(inner_d / 2)
        .extrude(length), str(step))
    return "tube"


# -------------------------------------------------------------------- mesh --

def mesh_step(step: Path, mesh_size: float):
    import gmsh
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.merge(str(step))
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size * 0.4)
        gmsh.model.mesh.generate(3)
        gmsh.model.mesh.setOrder(2)

        tags, xyz, _ = gmsh.model.mesh.getNodes()
        coords = np.asarray(xyz, float).reshape(-1, 3)
        order = np.argsort(tags)
        tags = np.asarray(tags)[order]
        coords = coords[order]
        t2i = {int(t): i for i, t in enumerate(tags)}

        conn = None
        etypes, _, enodes = gmsh.model.mesh.getElements(dim=3)
        for et, raw in zip(etypes, enodes):
            if et == 11:
                block = np.asarray(raw, np.int64).reshape(-1, 10)
                conn = np.vectorize(t2i.get)(block)[:, GMSH_TO_CCX_TET10]
                break
        if conn is None:
            raise RuntimeError("no tet10 elements -- mesh_size too large?")
        if len(coords) > MAX_NODES:
            raise RuntimeError(
                f"{len(coords)} nodes exceeds MAX_NODES={MAX_NODES}; "
                f"increase mesh_size (RAM guard)")
        return coords, conn
    finally:
        gmsh.finalize()


# ----------------------------------------------------------- deck emitters --

def _emit_nodes_elems(coords, conn):
    L = ["*NODE, NSET=Nall"]
    for i, (x, y, z) in enumerate(coords, 1):
        L.append(f"{i}, {x:.8e}, {y:.8e}, {z:.8e}")
    L.append("*ELEMENT, TYPE=C3D10, ELSET=Eall")
    for e, row in enumerate(conn, 1):
        L.append(f"{e}, " + ", ".join(str(int(n) + 1) for n in row))
    return L


def _emit_nset(L, name, idx):
    L.append(f"*NSET, NSET={name}")
    for c in (idx[i:i + 8] for i in range(0, len(idx), 8)):
        L.append(", ".join(str(int(n) + 1) for n in c) + ",")


def _faces_x(coords, span, tol=1e-6):
    return (np.where(coords[:, 0] < tol)[0],
            np.where(coords[:, 0] > span - tol)[0])


def deck_static(coords, conn, span, force, E, nu, inp: Path):
    fixed, loaded = _faces_x(coords, span)
    if not len(fixed) or not len(loaded):
        raise RuntimeError("empty boundary node set")
    L = _emit_nodes_elems(coords, conn)
    _emit_nset(L, "FIX", fixed)
    _emit_nset(L, "LOAD", loaded)
    L += ["*MATERIAL, NAME=MAT", "*ELASTIC", f"{E:.6e}, {nu}",
          "*SOLID SECTION, ELSET=Eall, MATERIAL=MAT",
          "*STEP", "*STATIC", "*BOUNDARY", "FIX, 1, 3",
          "*CLOAD", f"LOAD, 3, {(-force / len(loaded)):.8e}",
          "*NODE FILE", "U", "*EL FILE", "S", "*END STEP", ""]
    inp.write_text("\n".join(L))


def deck_thermal(coords, conn, span, k_cond, t_hot, t_cold, inp: Path):
    hot, cold = _faces_x(coords, span)
    if not len(hot) or not len(cold):
        raise RuntimeError("empty thermal boundary set")
    L = _emit_nodes_elems(coords, conn)
    _emit_nset(L, "HOT", hot)
    _emit_nset(L, "COLD", cold)
    L += ["*MATERIAL, NAME=MAT", "*CONDUCTIVITY", f"{k_cond:.6e}",
          "*SOLID SECTION, ELSET=Eall, MATERIAL=MAT",
          "*INITIAL CONDITIONS, TYPE=TEMPERATURE", f"Nall, {t_cold}",
          "*STEP", "*HEAT TRANSFER, STEADY STATE",
          "*BOUNDARY", f"HOT, 11, 11, {t_hot}", f"COLD, 11, 11, {t_cold}",
          "*NODE FILE", "NT", "*END STEP", ""]
    inp.write_text("\n".join(L))


def deck_modal(coords, conn, span, E, nu, rho_tonne_mm3, n_modes, inp: Path):
    fixed, _ = _faces_x(coords, span)
    if not len(fixed):
        raise RuntimeError("empty fixed set")
    L = _emit_nodes_elems(coords, conn)
    _emit_nset(L, "FIX", fixed)
    L += ["*MATERIAL, NAME=MAT", "*ELASTIC", f"{E:.6e}, {nu}",
          "*DENSITY", f"{rho_tonne_mm3:.6e}",
          "*SOLID SECTION, ELSET=Eall, MATERIAL=MAT",
          "*STEP", "*FREQUENCY", f"{n_modes}",
          "*BOUNDARY", "FIX, 1, 3",
          "*NODE FILE", "U", "*END STEP", ""]
    inp.write_text("\n".join(L))


# -------------------------------------------------------------------- solve --

def run_ccx(inp: Path):
    exe = (shutil.which("ccx") or shutil.which("ccx_2.21")
           or shutil.which("ccx_2.20"))
    if exe is None:
        raise RuntimeError("CalculiX 'ccx' not on PATH")
    p = subprocess.run([exe, inp.with_suffix("").name], cwd=inp.parent,
                       capture_output=True, text=True, timeout=1800)
    frd = inp.with_suffix(".frd")
    if not frd.exists() or frd.stat().st_size == 0:
        raise RuntimeError("ccx produced no results:\n"
                           + (p.stdout or "")[-1200:])


# -------------------------------------------------------------------- parse --

def _frd_fields(seg: str):
    """E12.5 values in FIXED 12-char columns; negatives fuse without this."""
    out = []
    for i in range(0, len(seg) - 11, 12):
        c = seg[i:i + 12].strip()
        if c:
            try:
                out.append(float(c))
            except ValueError:
                pass
    return out


def parse_frd(frd: Path, n: int) -> dict:
    """Generic: returns {'DISP': (n,3), 'STRESS': (n,6), 'NDTEMP': (n,1) ...}
    Only the FIRST block of each name is kept (modal writes one per mode;
    mode 1 is what we display)."""
    blocks: dict[str, np.ndarray] = {}
    cur = None
    with frd.open() as fh:
        for line in fh:
            if line.startswith(" -4"):
                nm = line.split()[1].upper()
                cur = None if nm in blocks else nm
                if cur:
                    blocks[cur] = None  # reserve; size on first data line
            elif line.startswith(" -3"):
                cur = None
            elif cur and line.startswith(" -1"):
                body = line.rstrip("\n")
                try:
                    node = int(body[3:13]) - 1
                except ValueError:
                    continue
                vals = _frd_fields(body[13:])
                if not vals or not (0 <= node < n):
                    continue
                if blocks[cur] is None:
                    blocks[cur] = np.zeros((n, len(vals)))
                arr = blocks[cur]
                arr[node, :min(len(vals), arr.shape[1])] = \
                    vals[:arr.shape[1]]
    return {k: v for k, v in blocks.items() if v is not None}


def parse_dat_frequencies(dat: Path):
    """First eigenfrequencies (Hz) from the CalculiX .dat file."""
    freqs = []
    if not dat.exists():
        return freqs
    grab = False
    for line in dat.read_text().splitlines():
        u = line.upper()
        if "E I G E N V A L U E" in u:
            grab = True
            continue
        if grab:
            parts = line.split()
            if len(parts) >= 4 and parts[0].isdigit():
                try:
                    freqs.append(float(parts[3]))   # CYCLES/TIME column
                except ValueError:
                    pass
            elif freqs and not parts:
                break
    return freqs


def von_mises(s):
    a, b, c, d, e, f = (s[:, i] for i in range(6))
    return np.sqrt(0.5 * ((a - b) ** 2 + (b - c) ** 2 + (c - a) ** 2)
                   + 3.0 * (d ** 2 + e ** 2 + f ** 2))


# -------------------------------------------------------------------- scene --

def write_scene(coords, conn, point_data: dict, iteration, tool,
                params, result, mission=""):
    import meshio
    SCENE.mkdir(parents=True, exist_ok=True)
    mesh = meshio.Mesh(points=coords, cells=[("tetra10", conn)],
                       point_data=point_data)
    vtu = SCENE / f"iter_{iteration:04d}.vtu"
    meshio.write(vtu, mesh)
    manifest = {"iteration": iteration, "timestamp": time.time(),
                "tool": tool, "mission": mission, "parameters": params,
                "result": result, "mesh_file": vtu.name}
    (SCENE / f"iter_{iteration:04d}.json").write_text(
        json.dumps(manifest, indent=2))
    (SCENE / "latest.json").write_text(json.dumps(manifest, indent=2))


# -------------------------------------------------------------------- tools --

def _static_solve(step, span, force, E, nu, mesh_size):
    WORK.mkdir(parents=True, exist_ok=True)
    inp = WORK / "model.inp"
    coords, conn = mesh_step(step, mesh_size)
    deck_static(coords, conn, span, force, E, nu, inp)
    run_ccx(inp)
    b = parse_frd(inp.with_suffix(".frd"), len(coords))
    disp = b.get("DISP", np.zeros((len(coords), 3)))[:, :3]
    stress = b.get("STRESS", np.zeros((len(coords), 6)))
    return coords, conn, disp, von_mises(stress)


def cantilever(length=100.0, width=10.0, height=10.0, force=100.0,
               E=210000.0, nu=0.3, mesh_size=3.0, iteration=0, mission=""):
    t0 = time.time()
    step = WORK / "beam.step"
    WORK.mkdir(parents=True, exist_ok=True)
    geom_box(length, width, height, step)
    coords, conn, disp, vm = _static_solve(step, length, force, E, nu,
                                           mesh_size)
    tip = float(np.abs(disp[:, 2]).max())
    I = width * height ** 3 / 12.0
    exact = force * length ** 3 / (3.0 * E * I)
    result = dict(tool="cantilever", tip_deflection_mm=tip,
                  analytical_deflection_mm=exact,
                  deflection_error_pct=abs(tip - exact) / exact * 100,
                  max_von_mises_MPa=float(vm.max()),
                  n_nodes=len(coords), n_elements=len(conn),
                  wall_time_s=round(time.time() - t0, 2), converged=True)
    write_scene(coords, conn,
                {"displacement": disp,
                 "displacement_magnitude": np.linalg.norm(disp, axis=1),
                 "von_mises": vm},
                iteration, "cantilever",
                dict(length=length, width=width, height=height, force=force,
                     E=E, nu=nu, mesh_size=mesh_size), result, mission)
    return result


def lattice_strut(diameter=6.0, length=25.0, force=200.0, E=71700.0,
                  nu=0.33, density_kg_m3=2810.0, mesh_size=1.2,
                  iteration=1, mission=""):
    t0 = time.time()
    step = WORK / "strut.step"
    WORK.mkdir(parents=True, exist_ok=True)
    geom_cylinder(length, diameter, step)
    coords, conn, disp, vm = _static_solve(step, length, force, E, nu,
                                           mesh_size)
    tip = float(np.abs(disp[:, 2]).max())
    I = np.pi * diameter ** 4 / 64.0
    exact = force * length ** 3 / (3.0 * E * I)
    mass_g = np.pi * (diameter / 2) ** 2 * length * density_kg_m3 * 1e-6
    result = dict(tool="lattice_strut", diameter_mm=diameter,
                  mass_g=round(float(mass_g), 4),
                  max_von_mises_MPa=float(vm.max()),
                  analytical_bending_stress_MPa=32 * force * length
                  / (np.pi * diameter ** 3),
                  tip_deflection_mm=tip, analytical_deflection_mm=exact,
                  deflection_error_pct=abs(tip - exact) / exact * 100,
                  n_nodes=len(coords), n_elements=len(conn),
                  wall_time_s=round(time.time() - t0, 2), converged=True)
    write_scene(coords, conn,
                {"displacement": disp,
                 "displacement_magnitude": np.linalg.norm(disp, axis=1),
                 "von_mises": vm},
                iteration, "lattice_strut",
                dict(diameter=diameter, length=length, force=force, E=E,
                     nu=nu, mesh_size=mesh_size), result, mission)
    return result


def hollow_strut(outer_diameter=8.0, wall=1.0, length=25.0, force=200.0,
                 E=71700.0, nu=0.33, density_kg_m3=2810.0, mesh_size=1.0,
                 iteration=1, mission=""):
    """PHOENIX v2 mission tool: hollow tube in bending. Two design
    variables. Effective mesh refines with thin walls (>=2 elems/wall)."""
    t0 = time.time()
    step = WORK / "tube.step"
    WORK.mkdir(parents=True, exist_ok=True)
    kind = geom_tube(length, outer_diameter, wall, step)
    inner_d = 0.0 if kind == "solid" else outer_diameter - 2 * wall
    eff_mesh = max(0.35, min(mesh_size, wall / 1.5))
    coords, conn, disp, vm = _static_solve(step, length, force, E, nu,
                                           eff_mesh)
    tip = float(np.abs(disp[:, 2]).max())
    I = np.pi * (outer_diameter ** 4 - inner_d ** 4) / 64.0
    exact = force * length ** 3 / (3.0 * E * I)
    sigma = force * length * (outer_diameter / 2) / I
    area = np.pi / 4 * (outer_diameter ** 2 - inner_d ** 2)
    mass_g = area * length * density_kg_m3 * 1e-6
    result = dict(tool="hollow_strut", geometry=kind,
                  outer_diameter_mm=outer_diameter, wall_mm=wall,
                  inner_diameter_mm=round(inner_d, 3),
                  mass_g=round(float(mass_g), 4),
                  max_von_mises_MPa=float(vm.max()),
                  analytical_bending_stress_MPa=float(sigma),
                  tip_deflection_mm=tip, analytical_deflection_mm=exact,
                  deflection_error_pct=abs(tip - exact) / exact * 100,
                  effective_mesh_size=eff_mesh,
                  n_nodes=len(coords), n_elements=len(conn),
                  wall_time_s=round(time.time() - t0, 2), converged=True)
    write_scene(coords, conn,
                {"displacement": disp,
                 "displacement_magnitude": np.linalg.norm(disp, axis=1),
                 "von_mises": vm},
                iteration, "hollow_strut",
                dict(outer_diameter=outer_diameter, wall=wall, length=length,
                     force=force, E=E, nu=nu, mesh_size=mesh_size),
                result, mission)
    return result


def heat_rod(length=100.0, width=10.0, height=10.0, k_cond=0.05,
             t_hot=100.0, t_cold=0.0, mesh_size=3.0, iteration=0,
             mission=""):
    """THERMAL domain. Steady conduction in a bar; exact linear profile.
    k in W/(mm*K) (steel ~0.05). Checks midpoint temperature and flux."""
    t0 = time.time()
    step = WORK / "rod.step"
    WORK.mkdir(parents=True, exist_ok=True)
    geom_box(length, width, height, step)
    inp = WORK / "model.inp"
    coords, conn = mesh_step(step, mesh_size)
    deck_thermal(coords, conn, length, k_cond, t_hot, t_cold, inp)
    run_ccx(inp)
    b = parse_frd(inp.with_suffix(".frd"), len(coords))
    T = b.get("NDTEMP", np.zeros((len(coords), 1)))[:, 0]
    x = coords[:, 0]
    T_lin = t_cold + (t_hot - t_cold) * (1 - x / length)
    max_dev = float(np.abs(T - T_lin).max())
    span_T = abs(t_hot - t_cold)
    flux_W = k_cond * width * height * span_T / length
    result = dict(tool="heat_rod",
                  max_profile_deviation_K=max_dev,
                  profile_error_pct=max_dev / span_T * 100,
                  analytical_flux_W=flux_W,
                  t_hot=t_hot, t_cold=t_cold,
                  n_nodes=len(coords), n_elements=len(conn),
                  wall_time_s=round(time.time() - t0, 2), converged=True)
    write_scene(coords, conn, {"temperature": T},
                iteration, "heat_rod",
                dict(length=length, width=width, height=height,
                     k_cond=k_cond, t_hot=t_hot, t_cold=t_cold,
                     mesh_size=mesh_size), result, mission)
    return result


def modal_beam(length=100.0, width=5.0, height=5.0, E=210000.0, nu=0.3,
               density_kg_m3=7850.0, n_modes=5, mesh_size=2.5,
               iteration=0, mission=""):
    """MODAL domain. First bending frequency vs Euler-Bernoulli:
    f1 = (1.8751^2 / 2 pi L^2) * sqrt(E I / (rho A))."""
    t0 = time.time()
    rho = density_kg_m3 * 1e-12          # tonne/mm^3
    step = WORK / "mbeam.step"
    WORK.mkdir(parents=True, exist_ok=True)
    geom_box(length, width, height, step)
    inp = WORK / "model.inp"
    coords, conn = mesh_step(step, mesh_size)
    deck_modal(coords, conn, length, E, nu, rho, n_modes, inp)
    run_ccx(inp)
    freqs = parse_dat_frequencies(inp.with_suffix(".dat"))
    b = parse_frd(inp.with_suffix(".frd"), len(coords))
    disp = b.get("DISP", np.zeros((len(coords), 3)))[:, :3]
    I = width * height ** 3 / 12.0
    A = width * height
    f1_exact = (1.8751 ** 2 / (2 * np.pi * length ** 2)) * \
        np.sqrt(E * I / (rho * A))
    f1 = freqs[0] if freqs else 0.0
    result = dict(tool="modal_beam",
                  f1_Hz=float(f1),
                  analytical_f1_Hz=float(f1_exact),
                  f1_error_pct=abs(f1 - f1_exact) / f1_exact * 100
                  if f1_exact else 999.0,
                  frequencies_Hz=[round(f, 2) for f in freqs[:n_modes]],
                  n_nodes=len(coords), n_elements=len(conn),
                  wall_time_s=round(time.time() - t0, 2),
                  converged=bool(freqs))
    write_scene(coords, conn,
                {"displacement": disp,
                 "displacement_magnitude": np.linalg.norm(disp, axis=1)},
                iteration, "modal_beam",
                dict(length=length, width=width, height=height, E=E,
                     nu=nu, density_kg_m3=density_kg_m3,
                     mesh_size=mesh_size), result, mission)
    return result



# --- EngineAI extended tools (fluids / circuits / CAD), analytical cross-checks ---
def pipe_flow(diameter_m=0.02, velocity_m_s=0.05, length_m=3.0, fluid="water",
              roughness_m=0.0, iteration=0, mission=""):
    """Pipe pressure drop; laminar Darcy vs Hagen-Poiseuille cross-check."""
    import time, math, fluids
    t0 = time.time(); pr = fluids.FLUIDS[fluid]
    Re = fluids.reynolds(velocity_m_s, diameter_m, rho=pr["rho"], mu=pr["mu"])
    regime = fluids.flow_regime(Re)
    f = fluids.friction_factor(Re, roughness_m / diameter_m)
    dp = fluids.pressure_drop(f, length_m, diameter_m, pr["rho"], velocity_m_s)
    r = dict(tool="pipe_flow", fluid=fluid, reynolds=float(Re), regime=regime,
             friction_factor=float(f), pressure_drop_Pa=float(dp),
             wall_time_s=round(time.time() - t0, 4), converged=True)
    if regime == "laminar":
        Q = velocity_m_s * math.pi * diameter_m ** 2 / 4.0
        dp_hp = fluids.poiseuille_dp(pr["mu"], length_m, Q, diameter_m)
        r["analytical_pressure_drop_Pa"] = float(dp_hp)
        r["pressure_drop_error_pct"] = float(abs(dp - dp_hp) / dp_hp * 100)
    return r


def rc_filter(r_ohm=1000.0, c_farad=1e-6, freq_hz=0.0, iteration=0, mission=""):
    """RC low-pass magnitude: MNA solve cross-checked vs closed form."""
    import time, circuits
    t0 = time.time(); fc = circuits.rc_lowpass_fc(r_ohm, c_farad)
    f = fc if freq_hz <= 0 else freq_hz
    mag = abs(circuits.rc_lowpass(r_ohm, c_farad).solve_ac(f)["out"])
    ref = circuits.rc_lowpass_mag(f, r_ohm, c_farad)
    return dict(tool="rc_filter", cutoff_hz=float(fc), freq_hz=float(f),
                magnitude_mna=float(mag), analytical_magnitude=float(ref),
                magnitude_error_pct=float(abs(mag - ref) / ref * 100),
                wall_time_s=round(time.time() - t0, 4), converged=True)


def plate_with_holes(L=120.0, W=80.0, T=10.0, hole_d=8.0, margin=12.0,
                     iteration=0, mission=""):
    """Parametric CAD plate: CAD volume cross-checked vs closed form; exports STEP/STL."""
    import time, tempfile, os, cad_parts
    t0 = time.time()
    wp, params = cad_parts.build("mounting_plate", L=L, W=W, T=T, hole_d=hole_d, margin=margin)
    v = cad_parts._volume(wp); ve = cad_parts.mounting_plate_volume(**params)
    step, stl = cad_parts.export(wp, os.path.join(tempfile.mkdtemp(), "mounting_plate"))
    return dict(tool="plate_with_holes", volume_mm3=float(v),
                analytical_volume_mm3=float(ve),
                volume_error_pct=float(abs(v - ve) / ve * 100),
                step=step, stl=stl,
                wall_time_s=round(time.time() - t0, 4), converged=True)


TOOLS = {t.__name__: t for t in
         (cantilever, lattice_strut, hollow_strut, heat_rod, modal_beam, pipe_flow, rc_filter, plate_with_holes)}


# ----------------------------------------------------------------- registry --

def _has(exe):
    return shutil.which(exe) is not None


def registry() -> dict:
    return {
        "geometry": {"backend": "CadQuery/OpenCASCADE", "status": "active"},
        "meshing": {"backend": "Gmsh", "status": "active"},
        "structural_fea": {"backend": "CalculiX",
                           "status": "active" if _has("ccx") else "missing ccx",
                           "tools": ["cantilever", "lattice_strut",
                                     "hollow_strut"]},
        "thermal_fea": {"backend": "CalculiX",
                        "status": "active" if _has("ccx") else "missing ccx",
                        "tools": ["heat_rod"]},
        "modal": {"backend": "CalculiX",
                  "status": "active" if _has("ccx") else "missing ccx",
                  "tools": ["modal_beam"]},
        "cfd": {"backend": "OpenFOAM",
                "status": "active" if _has("simpleFoam") else
                "not installed -- install_domains.sh sec 1"},
        "materials_thermo": {"backend": "pycalphad",
                             "status": _py("pycalphad")},
        "electromagnetics": {"backend": "Elmer",
                             "status": "active" if _has("ElmerSolver") else
                             "not installed -- install_domains.sh sec 3"},
        "circuits": {"backend": "ngspice",
                     "status": "active" if _has("ngspice") else
                     "not installed -- install_domains.sh sec 4"},
        "multibody": {"backend": "pychrono", "status": _py("pychrono")},
        "optimization": {"backend": "scipy (local) / OpenMDAO",
                         "status": "scipy active; openmdao " + _py("openmdao")},
        "am_slicing": {"backend": "custom (per-voxel composition)",
                       "status": "design pending -- DED printer stage"},
    }


def _py(mod):
    try:
        __import__(mod)
        return "active"
    except ImportError:
        return "not installed -- install_domains.sh"


# ------------------------------------------------------- verification suite --

def verification_suite():
    """Fast analytical checks, one per active physics domain."""
    return [
        ("structural", lambda: cantilever(mesh_size=3.0),
         "deflection_error_pct"),
        ("thermal", lambda: heat_rod(mesh_size=4.0), "profile_error_pct"),
        ("modal", lambda: modal_beam(mesh_size=2.5), "f1_error_pct"),
    ]


# ---------------------------------------------------------------------- cli --

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("tool", choices=TOOLS)
    ap.add_argument("--params", default="{}")
    ap.add_argument("--iteration", type=int, default=0)
    a = ap.parse_args()
    try:
        kw = json.loads(a.params)
        kw["iteration"] = a.iteration
        print(json.dumps(TOOLS[a.tool](**kw), indent=2))
    except Exception as exc:
        print(json.dumps({"converged": False, "error": str(exc)}),
              file=sys.stderr)
        sys.exit(1)
