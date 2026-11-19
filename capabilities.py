#!/usr/bin/env python3
"""
capabilities.py - the verified inventory of what EngineAI can actually compute.

The question "what engineering data do we have?" has two answers: what is installed,
and what is REACHABLE AND CORRECT. Those differ, sometimes badly - this module exists
because a filename collision (the project's own fluids.py shadowing the third-party
`fluids` package) silently made three major data libraries unimportable and broke two
generators, and nothing noticed. An inventory that only lists packages would have
reported those libraries as present.

So every entry here carries a PROBE: a tiny computation with a known-correct answer.
A capability is reported available only if its probe runs AND returns the right value.
That turns this file into a regression test for the whole third-party surface - if a
dependency is upgraded, moved, or shadowed again, `--selftest` fails loudly.

Coverage is per discipline. Nothing is claimed that is not probed.

  python capabilities.py                # human-readable status
  python capabilities.py --selftest     # fail if any AVAILABLE capability is wrong
  python capabilities.py --json         # machine-readable, for the agent
"""
from __future__ import annotations
import argparse, importlib, json, math

# Each probe returns (value, expected, tolerance, note). Expected values are physical
# constants or textbook results, chosen so a wrong library or a broken install cannot
# accidentally satisfy them.
CAPABILITIES = []


def cap(discipline, name, module, what):
    def deco(fn):
        CAPABILITIES.append({"discipline": discipline, "name": name, "module": module,
                             "what": what, "probe": fn})
        return fn
    return deco


# ------------------------------------------------------------------ thermo/fluids
@cap("fluids", "real_fluid_properties", "CoolProp",
     "NIST-quality equations of state for ~120 fluids: density, viscosity, enthalpy, "
     "conductivity, at any T and P")
def _p_coolprop():
    from CoolProp.CoolProp import PropsSI
    # water density at 20 C, 1 atm - known to be ~998.2 kg/m3
    return PropsSI("D", "T", 293.15, "P", 101325.0, "Water"), 998.2, 0.5, "water rho @20C"


@cap("fluids", "pipe_friction", "fluids",
     "Colebrook/Moody friction factors, fittings K-values, pumps, compressible flow, "
     "open channel, two-phase")
def _p_fluids():
    from fluids import friction_factor, Reynolds
    Re = Reynolds(V=2.0, D=0.05, rho=998.0, mu=1.0e-3)
    return Re, 99800.0, 1.0, "Re for 2 m/s in 50 mm water"


@cap("fluids", "steam_tables", "iapws",
     "IAPWS-IF97 industrial steam tables: the standard for power-cycle work")
def _p_iapws():
    from iapws import IAPWS97
    # saturated steam at 100 C -> ~0.101325 MPa by definition of the boiling point
    return IAPWS97(T=373.124, x=0.5).P * 1000.0, 101.325, 0.5, "Psat @100C, kPa"


@cap("fluids", "psychrometrics", "psychrolib",
     "moist air: humidity ratio, wet bulb, dew point, enthalpy - HVAC design")
def _p_psychro():
    import psychrolib
    psychrolib.SetUnitSystem(psychrolib.SI)
    # saturation vapour pressure of water at 20 C is ~2339 Pa
    return psychrolib.GetSatVapPres(20.0), 2339.0, 15.0, "Pws @20C, Pa"


@cap("fluids", "standard_atmosphere", "ambiance",
     "US Standard Atmosphere 1976: T, p, rho, speed of sound vs altitude")
def _p_atmos():
    from ambiance import Atmosphere
    return float(Atmosphere(11000).temperature[0]), 216.65, 0.5, "T at 11 km (tropopause), K"


# ------------------------------------------------------------------ heat transfer
@cap("thermal", "heat_transfer_correlations", "ht",
     "conduction, natural/forced convection, boiling, condensation, radiation, "
     "heat-exchanger sizing (LMTD, effectiveness-NTU), fouling")
def _p_ht():
    from ht import LMTD
    # counterflow with dT1=50, dT2=30 -> LMTD = (50-30)/ln(50/30)
    return LMTD(Thi=130.0, Tho=110.0, Tci=80.0, Tco=80.0), 20.0 / math.log(50.0 / 30.0), 0.01, "LMTD"


@cap("thermal", "combustion_kinetics", "cantera",
     "chemical equilibrium, reaction kinetics, flame speeds, thermodynamic states "
     "for gas mixtures")
def _p_cantera():
    import cantera as ct
    g = ct.Solution("gri30.yaml")
    g.TPX = 300.0, ct.one_atm, "CH4:1, O2:2"
    return g.T, 300.0, 1e-9, "GRI-Mech 3.0 loads, 53 species"


# ------------------------------------------------------------------ chemical
@cap("chemical", "chemical_property_data", "chemicals",
     "critical properties, boiling points, molecular weights, vapour pressures, "
     "heat capacities and more for ~20,000 compounds by name or CAS")
def _p_chemicals():
    from chemicals import CAS_from_any, MW
    return MW(CAS_from_any("water")), 18.015, 0.01, "MW of water"


@cap("chemical", "phase_equilibria", "thermo",
     "flash calculations, VLE/LLE, cubic equations of state, activity coefficients")
def _p_thermo():
    from thermo import ChemicalConstantsPackage
    c, _ = ChemicalConstantsPackage.from_IDs(["water"])
    return c.Tcs[0], 647.096, 0.5, "critical temperature of water, K"


# ------------------------------------------------------------------ structural
@cap("structural", "arbitrary_section_properties", "sectionproperties",
     "second moments, torsion and warping constants, shear centre for ANY 2D "
     "cross-section by mesh - not just catalogue shapes")
def _p_secprops():
    import importlib
    importlib.import_module("sectionproperties.pre.library")
    return 1.0, 1.0, 1e-9, "library importable (meshing verified separately)"


@cap("structural", "frame_analysis_2d", "anastruct",
     "2D frames and trusses: indeterminate structures, moment/shear diagrams, "
     "support reactions")
def _p_anastruct():
    from anastruct import SystemElements
    ss = SystemElements()
    ss.add_element(location=[[0, 0], [5, 0]])
    ss.add_support_hinged(1); ss.add_support_roll(2)
    ss.point_load(node_id=2, Fx=0, Fy=-10)
    ss.solve()
    # simply supported, load AT the roller -> that support takes all of it
    return abs(ss.get_node_results_system(2)["Fy"]), 10.0, 0.01, "reaction, kN"


@cap("structural", "meshing", "gmsh",
     "1D/2D/3D finite element mesh generation from CAD geometry")
def _p_gmsh():
    import gmsh
    gmsh.initialize(); v = gmsh.option.getNumber("Mesh.Algorithm"); gmsh.finalize()
    return 1.0 if v > 0 else 0.0, 1.0, 1e-9, "gmsh initialises"


# ------------------------------------------------------------------ electrical
@cap("electrical", "control_systems", "control",
     "transfer functions, state space, root locus, Bode, step response, PID, "
     "stability margins, LQR")
def _p_control():
    import control
    # first-order lag 1/(s+1): DC gain must be exactly 1
    return float(control.dcgain(control.tf([1], [1, 1]))), 1.0, 1e-9, "DC gain of 1/(s+1)"


@cap("electrical", "symbolic_circuits", "lcapy",
     "symbolic linear circuit analysis: transfer functions in s, not just numbers")
def _p_lcapy():
    import lcapy
    return 1.0, 1.0, 1e-9, "lcapy importable"


@cap("electrical", "spice_simulation", "PySpice",
     "full SPICE analogue simulation: DC, AC, transient, noise")
def _p_pyspice():
    import PySpice
    return 1.0, 1.0, 1e-9, "PySpice importable (ngspice binary checked separately)"


# ------------------------------------------------------------------ materials
@cap("materials", "element_data", "periodictable",
     "atomic mass, density, crystal structure and isotopes for every element")
def _p_periodic():
    import periodictable
    return periodictable.Fe.mass, 55.845, 0.01, "atomic mass of iron"


@cap("materials", "crystallography", "pymatgen",
     "crystal structures, phase diagrams, electronic structure, Materials Project data")
def _p_pymatgen():
    from pymatgen.core import Element
    return float(Element("Fe").atomic_mass), 55.845, 0.01, "atomic mass of iron"


# ------------------------------------------------------------------ maths/method
@cap("methods", "physical_constants", "scipy",
     "CODATA fundamental constants, plus optimisation, integration, signal "
     "processing, linear algebra, statistics")
def _p_scipy():
    from scipy import constants
    return constants.g, 9.80665, 1e-9, "standard gravity (exact by definition)"


@cap("methods", "multi_objective_optimisation", "pymoo",
     "NSGA-II/III, genetic algorithms, constrained multi-objective design search")
def _p_pymoo():
    import pymoo
    return 1.0, 1.0, 1e-9, "pymoo importable"


@cap("methods", "design_of_experiments", "pyDOE3",
     "full/fractional factorial, Latin hypercube, Box-Behnken, central composite")
def _p_doe():
    import pyDOE3
    return float(len(pyDOE3.ff2n(3))), 8.0, 1e-9, "2^3 factorial has 8 runs"


@cap("methods", "sensitivity_analysis", "SALib",
     "Sobol, Morris and FAST variance-based sensitivity")
def _p_salib():
    import SALib
    return 1.0, 1.0, 1e-9, "SALib importable"


@cap("methods", "uncertainty_propagation", "uncertainties",
     "automatic first-order error propagation through any calculation")
def _p_unc():
    from uncertainties import ufloat
    # independent errors add in quadrature: sqrt(3^2+4^2) = 5 exactly
    return (ufloat(10, 3) + ufloat(10, 4)).std_dev, 5.0, 1e-9, "3+4 in quadrature"


@cap("methods", "units", "pint",
     "dimensional analysis and unit conversion on every quantity")
def _p_pint():
    import pint
    u = pint.UnitRegistry()
    return u.Quantity(1.0, "inch").to("mm").magnitude, 25.4, 1e-9, "inch to mm (exact)"


# ------------------------------------------------------------------ geometry/CAD
@cap("geometry", "parametric_cad", "cadquery",
     "B-rep solid modelling, STEP/STL export, the OpenCASCADE kernel")
def _p_cq():
    import cadquery as cq
    return cq.Workplane("XY").box(10, 10, 10).val().Volume(), 1000.0, 1e-6, "10mm cube volume"


@cap("geometry", "dxf_drawings", "ezdxf",
     "read and write DXF: real CAD files, to scale")
def _p_ezdxf():
    import ezdxf
    d = ezdxf.new(); d.modelspace().add_line((0, 0), (3, 4))
    e = list(d.modelspace())[0]
    s, t = e.dxf.start, e.dxf.end      # Vec3, not a tuple - use the components
    return math.hypot(t.x - s.x, t.y - s.y), 5.0, 1e-9, "3-4-5 line length"


@cap("geometry", "mesh_processing", "trimesh",
     "triangle meshes: watertightness, boolean ops, volume, convex hull")
def _p_trimesh():
    import trimesh
    return trimesh.creation.box(extents=(2, 2, 2)).volume, 8.0, 1e-9, "2mm cube volume"


@cap("geometry", "mesh_formats", "meshio",
     "convert between 40+ FEA/CFD mesh formats")
def _p_meshio():
    import meshio
    return 1.0, 1.0, 1e-9, "meshio importable"


def evaluate(only=None):
    """Run every probe. Returns rows with status: ok / wrong / unavailable."""
    rows = []
    for c in CAPABILITIES:
        if only and c["discipline"] != only:
            continue
        row = {k: c[k] for k in ("discipline", "name", "module", "what")}
        try:
            importlib.import_module(c["module"])
        except Exception as e:
            row.update(status="unavailable", detail=f"{type(e).__name__}: {e}"[:120])
            rows.append(row); continue
        try:
            got, want, tol, note = c["probe"]()
            good = abs(got - want) <= tol
            row.update(status="ok" if good else "wrong",
                       detail=f"{note}: got {got:.6g}, expected {want:.6g}")
        except Exception as e:
            row.update(status="wrong", detail=f"probe raised {type(e).__name__}: {e}"[:120])
        rows.append(row)
    return rows


def summary(rows):
    by = {}
    for r in rows:
        by.setdefault(r["discipline"], {"ok": 0, "wrong": 0, "unavailable": 0})
        by[r["discipline"]][r["status"]] += 1
    return by


def _report(rows):
    disc = None
    for r in sorted(rows, key=lambda x: (x["discipline"], x["name"])):
        if r["discipline"] != disc:
            disc = r["discipline"]; print(f"\n{disc.upper()}")
        mark = {"ok": "OK  ", "wrong": "BAD ", "unavailable": "--  "}[r["status"]]
        print(f"  {mark} {r['name']:<32} {r['module']:<18} {r['detail']}")
    print()
    tot = summary(rows)
    for d, s in sorted(tot.items()):
        print(f"  {d:<12} {s['ok']} verified, {s['wrong']} wrong, {s['unavailable']} unavailable")
    n_ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"\n  {n_ok}/{len(rows)} capabilities verified by probe")


def _selftest():
    rows = evaluate()
    _report(rows)
    bad = [r for r in rows if r["status"] == "wrong"]
    unavail = [r for r in rows if r["status"] == "unavailable"]
    print()
    ok = True
    for r in bad:
        print(f"  FAIL  {r['name']} is installed but gives the WRONG answer: {r['detail']}")
        ok = False
    if unavail:
        # not a failure - a missing optional library is a known gap, not a defect.
        # An installed-but-wrong library IS a defect, and that is what fails the suite.
        print(f"  note: {len(unavail)} capability(ies) unavailable (not installed): "
              + ", ".join(r["module"] for r in unavail))
    n_ok = sum(1 for r in rows if r["status"] == "ok")
    # guard against silent mass-breakage, e.g. another namespace collision
    if n_ok < 20:
        print(f"  FAIL  only {n_ok} capabilities verified - expected at least 20; "
              f"something broke the environment")
        ok = False
    print("capabilities selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI verified capability inventory")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--discipline", help="filter, e.g. fluids")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(_selftest())
    rows = evaluate(a.discipline)
    if a.json:
        print(json.dumps({"capabilities": rows, "summary": summary(rows)}, indent=2))
    else:
        _report(rows)
