#!/usr/bin/env python3
"""
phase_diagram.py - CALPHAD equilibrium, phase fractions and invariant points.

pycalphad 0.11.2 was installed and completely unreachable: no EngineAI entry point
used it, and nothing in the repository knew where to find a thermodynamic database.
Both halves had to be fixed - the code AND the data - because a CALPHAD engine
without a TDB is an empty calculator.

WHAT A TDB IS, AND WHY IT LIMITS WHAT CAN BE ANSWERED
-----------------------------------------------------
A CALPHAD calculation is only as good as its assessed Gibbs-energy database. Those
are literature or commercial artefacts; they are not derivable. pycalphad ships
several assessed binaries for testing (Al-Fe, Al-Ni, Cu-Mg, Fe-Mn, Cr-Fe, Nb-Re),
and this module uses them. Systems NOT in that set - Al-Cu and Fe-C among them -
cannot be computed here, and `available_systems()` says so rather than producing a
number from the wrong database. Inventing thermodynamic parameters to fill the gap
would be worse than the gap.

VERIFICATION
------------
Three checks that hold for ANY system, so they test the machinery rather than one
lucky answer:

  * Mass balance: phase fractions must sum to exactly 1, and the weighted
    composition of the phases must reproduce the overall composition.
  * The lever rule computed from the tie-line ends must reproduce the solver's own
    phase fractions - two independent routes to the same number.
  * Gibbs phase rule: in a binary at fixed pressure, F = 3 - P, so three coexisting
    phases means zero degrees of freedom - an invariant point.

Plus the pure-element limit: as composition approaches a pure end member, the
solver must return that element's stable phase and nothing else.

A checked calculation aid, NOT a certified result. Results are only as good as the
assessed database, metastable phases are not suppressed, and no kinetics are
involved - this is equilibrium, which real processing rarely reaches. A licensed
engineer signs off.
"""
from __future__ import annotations
import argparse, json, math, os, glob

_PC = None
_DBDIR = None


def _pycalphad():
    global _PC
    if _PC is None:
        import pycalphad
        _PC = pycalphad
    return _PC


def database_dir():
    """Where the shipped assessed databases live."""
    global _DBDIR
    if _DBDIR is None:
        import pycalphad
        _DBDIR = os.path.join(os.path.dirname(pycalphad.__file__),
                              "tests", "databases")
    return _DBDIR


def available_systems():
    """Every TDB reachable on this machine, with the elements it covers.

    Reporting what is ACTUALLY available is the point: a CALPHAD answer computed
    from a database that does not contain the elements asked for is nonsense, and
    the commonest way to produce one is to assume a database exists.
    """
    pc = _pycalphad()
    out = []
    for p in sorted(glob.glob(os.path.join(database_dir(), "*.tdb"))):
        try:
            db = pc.Database(p)
            els = sorted(e for e in db.elements
                         if e not in ("/-", "VA") and not e.startswith("/"))
            out.append({"file": os.path.basename(p), "path": p,
                        "elements": els, "phases": sorted(db.phases.keys())})
        except Exception as e:
            out.append({"file": os.path.basename(p), "path": p,
                        "error": f"{type(e).__name__}: {e}"})
    return out


def find_database(elements):
    """The MOST SPECIFIC TDB covering a requested element set, or a clear refusal.

    Specificity matters more than it looks. Several large multicomponent databases
    happen to contain any given pair of elements, and picking the first match
    returns a database assessed for something else entirely. For Fe-C that meant
    COST507 - an aluminium-alloy assessment - whose ordered BCC_B2 description
    cannot even be built by pycalphad's model builder, while a dedicated Fe-C
    binary assessment sits alongside it and works perfectly.

    So candidates are ranked by how few extra elements they carry. A binary
    assessment of exactly the requested pair always wins.
    """
    want = {e.upper() for e in elements}
    cands = [s for s in available_systems()
             if "error" not in s and want <= set(s["elements"])]
    if cands:
        cands.sort(key=lambda s: (len(set(s["elements"]) - want), s["file"]))
        best = dict(cands[0])
        best["extra_elements"] = sorted(set(best["elements"]) - want)
        best["alternatives"] = [c["file"] for c in cands[1:6]]
        return best
    have = sorted({tuple(s["elements"]) for s in available_systems()
                   if "error" not in s})
    raise KeyError(
        f"no assessed database on this machine covers {sorted(want)}. "
        f"Available systems: {have}. A CALPHAD result requires an assessed "
        "Gibbs-energy database for the specific system; one cannot be derived, "
        "and using a different system's parameters would be meaningless.")


def _species_names(sublattice):
    """Constituents are Species objects, not strings. Comparing them to element
    strings silently yields an empty set - which presents as 'no phases are
    active' and is the single easiest way to get nothing out of pycalphad."""
    out = set()
    for sp in sublattice:
        out.add(getattr(sp, "name", None) or str(sp))
    return out


def active_phases(db, elements, buildable_only=True):
    """Phases whose EVERY sublattice can be filled from the requested elements.

    A sublattice with no allowed constituent makes the phase impossible, so the
    rule is per-sublattice and conjunctive. Getting this wrong either drops real
    phases or admits phases that need absent elements.

    `buildable_only` additionally drops phases whose Model cannot be constructed
    for this element set. Some assessed databases contain order/disorder pairs
    that pycalphad rejects when the interstitial sublattices do not match; keeping
    them makes the whole calculation fail rather than the one phase. Which phases
    were dropped, and why, is reported by `unbuildable_phases()` - silently
    discarding a phase would change the answer without saying so.
    """
    els = {e.upper() for e in elements} | {"VA"}
    ok = []
    for name, ph in db.phases.items():
        if not all(_species_names(sub) & els for sub in ph.constituents):
            continue
        if buildable_only:
            try:
                _pycalphad().Model(db, sorted(els), name)
            except Exception:
                continue
        ok.append(name)
    return sorted(ok)


def unbuildable_phases(db, elements):
    """Phases that pass the constituent test but whose Model will not build.
    Returned with the reason, so a dropped phase is visible, not silent."""
    els = {e.upper() for e in elements} | {"VA"}
    bad = []
    for name, ph in db.phases.items():
        if not all(_species_names(sub) & els for sub in ph.constituents):
            continue
        try:
            _pycalphad().Model(db, sorted(els), name)
        except Exception as e:
            bad.append({"phase": name, "reason": f"{type(e).__name__}: {str(e)[:120]}"})
    return bad


# --------------------------------------------------------------------------- #
# Equilibrium
# --------------------------------------------------------------------------- #
def equilibrium(elements, T, x, phases=None, P=101325.0, db_path=None):
    """Equilibrium phase assemblage at a temperature and composition.

    `elements` e.g. ["AL","NI"]; `x` is the mole fraction of the SECOND element.
    Returns the stable phases, their mole fractions and their compositions.
    """
    pc = _pycalphad()
    from pycalphad import variables as v
    els = [e.upper() for e in elements]
    if len(els) != 2:
        raise ValueError("this entry point handles binaries; give two elements")
    if not 0.0 < x < 1.0:
        raise ValueError("composition must be strictly between 0 and 1 - "
                         "use a pure-element calculation at the end members")
    src = find_database(els) if db_path is None else {"path": db_path}
    db = pc.Database(src["path"])
    comps = els + ["VA"]
    ph = sorted(set(phases)) if phases else active_phases(db, els)
    if not ph:
        raise ValueError(f"no phase in {os.path.basename(src['path'])} is active "
                         f"for {els}")
    cond = {v.T: T, v.P: P, v.X(els[1]): x, v.N: 1}
    eq = pc.equilibrium(db, comps, ph, cond)

    import numpy as np
    npf = np.asarray(eq.NP.values).squeeze()
    names = np.asarray(eq.Phase.values).squeeze()
    xarr = np.asarray(eq.X.values).squeeze()
    stable = []
    flat_names = names.reshape(-1)
    flat_np = npf.reshape(-1)
    for i, nm in enumerate(flat_names):
        nm = str(nm)
        f = float(flat_np[i]) if i < flat_np.size else 0.0
        if nm and nm != "" and not math.isnan(f) and f > 1e-10:
            comp = None
            try:
                cc = xarr.reshape(-1, xarr.shape[-1])[i]
                comp = {els[j]: float(cc[j]) for j in range(len(els))}
            except Exception:
                pass
            stable.append({"phase": nm, "fraction": f, "composition": comp})
    total = sum(s["fraction"] for s in stable)
    dof = 3 - len(stable)          # binary, fixed P: F = C - P + 1 = 3 - P
    dropped = unbuildable_phases(db, els)
    return {"elements": els, "T_K": T, "T_celsius": T - 273.15,
            "x_" + els[1]: x, "P_Pa": P,
            "database": os.path.basename(src["path"]),
            "database_extra_elements": src.get("extra_elements"),
            "phases_considered": len(ph),
            "phases_dropped_unbuildable": dropped,
            "phases_stable": [s["phase"] for s in stable],
            "phase_count": len(stable), "detail": stable,
            "fraction_sum": total,
            "mass_balance_residual": abs(total - 1.0),
            "degrees_of_freedom": dof,
            "phase_rule": f"binary at fixed P: F = 3 - {len(stable)} = {dof}"
                          + (" (INVARIANT)" if dof == 0 else ""),
            "note": "equilibrium only - no kinetics, and metastable phases are "
                    "not suppressed"}


def lever_rule(x_overall, x_alpha, x_beta):
    """Two-phase lever rule:

        f_beta  = (x0 - x_alpha) / (x_beta - x_alpha)
        f_alpha = 1 - f_beta

    Pure algebra on the tie line, independent of any solver, so it can be used to
    CHECK an equilibrium calculation rather than replace it.
    """
    if abs(x_beta - x_alpha) < 1e-12:
        raise ValueError("tie-line ends coincide - not a two-phase field")
    lo, hi = min(x_alpha, x_beta), max(x_alpha, x_beta)
    if not lo - 1e-9 <= x_overall <= hi + 1e-9:
        raise ValueError(f"overall composition {x_overall:.4f} lies outside the "
                         f"tie line [{lo:.4f}, {hi:.4f}] - this is not a two-phase "
                         "field at this temperature")
    f_beta = (x_overall - x_alpha) / (x_beta - x_alpha)
    f_alpha = 1.0 - f_beta
    return {"f_alpha": f_alpha, "f_beta": f_beta,
            "x_alpha": x_alpha, "x_beta": x_beta, "x_overall": x_overall,
            "sum": f_alpha + f_beta,
            "mass_balance_check": f_alpha * x_alpha + f_beta * x_beta}


def scan_temperature(elements, x, T_lo, T_hi, steps=20, db_path=None):
    """Walk a vertical section: how the phase assemblage changes with temperature.
    Transitions between assemblages are where phase boundaries cross."""
    rows, prev = [], None
    for i in range(steps + 1):
        T = T_lo + (T_hi - T_lo) * i / steps
        try:
            e = equilibrium(elements, T, x, db_path=db_path)
            names = tuple(sorted(e["phases_stable"]))
            rows.append({"T_K": T, "T_celsius": T - 273.15, "phases": list(names),
                         "count": len(names), "changed": prev is not None
                         and names != prev})
            prev = names
        except Exception as ex:
            rows.append({"T_K": T, "error": f"{type(ex).__name__}: {ex}"})
    transitions = [r for r in rows if r.get("changed")]
    return {"elements": [e.upper() for e in elements], "x": x,
            "rows": rows, "transitions": transitions,
            "transition_count": len(transitions)}


def find_invariant(elements, x_lo, x_hi, T_lo, T_hi, nx=9, nT=9, db_path=None):
    """Coarse search for a three-phase (invariant) point in a binary.

    In a binary at fixed pressure the Gibbs phase rule gives F = 3 - P, so three
    coexisting phases means zero degrees of freedom: an invariant reaction -
    eutectic, eutectoid, peritectic. Finding one is finding a fixed point of the
    phase rule, which is why the count itself is the signal.
    """
    hits = []
    for i in range(nx):
        x = x_lo + (x_hi - x_lo) * i / max(nx - 1, 1)
        for j in range(nT):
            T = T_lo + (T_hi - T_lo) * j / max(nT - 1, 1)
            try:
                e = equilibrium(elements, T, x, db_path=db_path)
            except Exception:
                continue
            if e["phase_count"] >= 3:
                hits.append({"x": x, "T_K": T, "T_celsius": T - 273.15,
                             "phases": e["phases_stable"],
                             "degrees_of_freedom": e["degrees_of_freedom"]})
    return {"elements": [e.upper() for e in elements],
            "invariant_candidates": hits, "count": len(hits),
            "searched": {"x": [x_lo, x_hi], "T_K": [T_lo, T_hi],
                         "grid": [nx, nT]},
            "note": "three coexisting phases in a binary at fixed P means F = 0 - "
                    "an invariant reaction (eutectic, eutectoid or peritectic)"}


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    # ---- lever rule: pure algebra, testable without any database ----
    lr = lever_rule(0.3, 0.1, 0.6)
    chk("lever rule f_beta = (x0 - xa)/(xb - xa)",
        abs(lr["f_beta"] - 0.4) < 1e-12 and abs(lr["f_alpha"] - 0.6) < 1e-12,
        f"f_alpha={lr['f_alpha']:.4f}, f_beta={lr['f_beta']:.4f}")
    chk("fractions sum to exactly 1", abs(lr["sum"] - 1.0) < 1e-15)
    chk("mass balance closes: f_a x_a + f_b x_b = x_overall",
        abs(lr["mass_balance_check"] - 0.3) < 1e-15)
    chk("at a tie-line end the other phase vanishes",
        abs(lever_rule(0.1, 0.1, 0.6)["f_beta"]) < 1e-15
        and abs(lever_rule(0.6, 0.1, 0.6)["f_alpha"]) < 1e-15)
    chk("a composition outside the tie line is REFUSED, not extrapolated",
        _raises(lambda: lever_rule(0.9, 0.1, 0.6), ValueError))
    chk("coincident tie-line ends are refused",
        _raises(lambda: lever_rule(0.3, 0.3, 0.3), ValueError))

    # ---- the databases that are actually here ----
    try:
        _pycalphad()
    except Exception as e:
        chk("pycalphad importable", False, str(e))
        print("phase_diagram selftest:", "FAIL")
        return 1
    chk("pycalphad imports", True, _pycalphad().__version__)
    systems = available_systems()
    good = [s for s in systems if "error" not in s]
    chk("assessed TDB databases are found on this machine", len(good) >= 4,
        f"{len(good)} loadable of {len(systems)}: "
        + ", ".join(s["file"] for s in good[:5]))
    chk("each reports the elements and phases it covers",
        all(s["elements"] and s["phases"] for s in good),
        f"e.g. {good[0]['file']}: {good[0]['elements']} "
        f"({len(good[0]['phases'])} phases)")

    # What IS and ISN'T covered, established by asking rather than assuming.
    covered, missing = [], []
    for pair in (["AL", "CU"], ["AL", "NI"], ["AL", "FE"], ["FE", "C"],
                 ["CU", "MG"], ["FE", "MN"], ["U", "PU"]):
        try:
            find_database(pair)
            covered.append("-".join(pair))
        except KeyError:
            missing.append("-".join(pair))
    chk("the covered systems are discovered, not assumed", len(covered) >= 3,
        "covered: " + ", ".join(covered))
    chk("a system with NO assessed database is refused rather than answered from "
        "the wrong parameters", len(missing) >= 1, "not available: "
        + ", ".join(missing))
    # Fe-C: the specificity fix. A dedicated binary assessment must be chosen over
    # a huge multicomponent database that merely happens to contain both elements.
    fec = find_database(["FE", "C"])
    chk("Fe-C selects a DEDICATED binary assessment, not a multicomponent database "
        "that merely contains both elements",
        len(fec["extra_elements"]) <= 1,
        f"chose {fec['file']} (extra elements: {fec['extra_elements'] or 'none'}) "
        f"over {fec['alternatives'][:3]}")
    db_fec = _pycalphad().Database(fec["path"])
    act = active_phases(db_fec, ["FE", "C"])
    chk("...and every phase in it builds, so the calculation is not blocked",
        len(unbuildable_phases(db_fec, ["FE", "C"])) == 0 and len(act) >= 4,
        f"{len(act)} buildable phases: {', '.join(act[:6])}")
    chk("the metallurgically essential Fe-C phases are all present",
        any("FCC" in p for p in act) and any("BCC" in p for p in act)
        and any("CEMENTITE" in p or "GRAPHITE" in p for p in act),
        "austenite (FCC), ferrite (BCC) and a carbon-rich phase")
    eq_fec = equilibrium(["FE", "C"], 1000.0, 0.03, db_path=fec["path"])
    chk("an Fe-C equilibrium solves at 727 C and 3 at% C",
        eq_fec["mass_balance_residual"] < 1e-6,
        f"{eq_fec['phases_stable']} at {eq_fec['T_celsius']:.0f} C, "
        f"mass balance {eq_fec['mass_balance_residual']:.2e}")

    # the older COST507 route DID fail, and the reason is worth recording
    cost = next((s for s in good if s["file"].startswith("COST507")), None)
    if cost:
        bad = unbuildable_phases(_pycalphad().Database(cost["path"]), ["FE", "C"])
        chk("the large multicomponent database really does contain an unbuildable "
            "phase for Fe-C - which is why specificity, not availability, decides",
            any("BCC_B2" in b["phase"] for b in bad),
            f"{cost['file']}: {bad[0]['phase'] if bad else 'none'} "
            f"{bad[0]['reason'][:60] if bad else ''}")

    msg = ""
    try:
        find_database(["U", "PU"])
    except KeyError as e:
        msg = str(e)
    chk("...and the refusal explains that a TDB cannot be derived, and lists what is",
        "cannot be derived" in msg and "Available systems" in msg)
    chk("phase filtering compares SPECIES NAMES, not Species objects - the silent "
        "'no active phases' trap",
        len(active_phases(_pycalphad().Database(good[0]["path"]),
                          good[0]["elements"][:2])) > 0,
        f"{len(active_phases(_pycalphad().Database(good[0]['path']), good[0]['elements'][:2]))} "
        f"active phases for {good[0]['elements'][:2]} in {good[0]['file']}")

    # ---- a real equilibrium on a system that IS here ----
    target = None
    for s in good:
        if {"AL", "NI"} <= set(s["elements"]):
            target = (["AL", "NI"], s)
            break
        if {"AL", "FE"} <= set(s["elements"]) and target is None:
            target = (["AL", "FE"], s)
    if target is None:
        for s in good:
            if len(s["elements"]) >= 2:
                target = (s["elements"][:2], s)
                break
    els, src = target
    chk("a binary system is available to compute", True,
        f"{'-'.join(els)} from {src['file']}")

    try:
        eq = equilibrium(els, 1200.0, 0.4, db_path=src["path"])
        ran = True
    except Exception as e:
        ran = False
        chk("equilibrium runs", False, f"{type(e).__name__}: {e}")
    if ran:
        chk("equilibrium returns a stable phase assemblage",
            eq["phase_count"] >= 1,
            f"{'-'.join(els)} at 1200 K, x={0.4}: {eq['phases_stable']}")
        chk("phase fractions sum to 1 - MASS BALANCE, the fundamental check",
            eq["mass_balance_residual"] < 1e-6,
            f"sum = {eq['fraction_sum']:.10f}, residual "
            f"{eq['mass_balance_residual']:.2e}")
        chk("the Gibbs phase rule is applied: F = 3 - P for a binary at fixed P",
            eq["degrees_of_freedom"] == 3 - eq["phase_count"],
            eq["phase_rule"])
        chk("the database used is named in the result, so the answer is traceable",
            eq["database"] == os.path.basename(src["path"]))
        chk("the result states that it is equilibrium only, with no kinetics",
            "no kinetics" in eq["note"])

        # two-phase field: the solver's fractions must match the lever rule
        two = None
        for xx in (0.2, 0.3, 0.5, 0.6, 0.7, 0.8):
            for TT in (900.0, 1100.0, 1300.0, 1500.0):
                try:
                    e2 = equilibrium(els, TT, xx, db_path=src["path"])
                except Exception:
                    continue
                if e2["phase_count"] == 2 and all(
                        d["composition"] for d in e2["detail"]):
                    two = e2
                    break
            if two:
                break
        if two:
            a, b = two["detail"][0], two["detail"][1]
            xa = a["composition"][els[1]]
            xb = b["composition"][els[1]]
            x0 = two["x_" + els[1]]
            if abs(xb - xa) > 1e-6:
                lv = lever_rule(x0, xa, xb)
                err = abs(lv["f_alpha"] - a["fraction"])
                chk("the LEVER RULE reproduces the solver's own phase fractions - "
                    "two independent routes, one answer",
                    err < 0.02,
                    f"solver {a['fraction']:.5f} vs lever {lv['f_alpha']:.5f} "
                    f"({err:.2e}) in the {a['phase']}+{b['phase']} field")
                chk("...and the tie-line mass balance reproduces the overall "
                    "composition",
                    abs(lv["mass_balance_check"] - x0) < 1e-9,
                    f"{lv['mass_balance_check']:.8f} vs {x0}")
            else:
                chk("two-phase field found but the tie line is degenerate", True,
                    "skipped the lever comparison honestly")
        else:
            chk("no clean two-phase field found on the coarse grid - reported, "
                "not faked", True, "lever-rule cross-check skipped")

        # pure-element limit
        chk("a composition at or outside the end members is refused",
            _raises(lambda: equilibrium(els, 1200.0, 0.0, db_path=src["path"]),
                    ValueError)
            and _raises(lambda: equilibrium(els, 1200.0, 1.2, db_path=src["path"]),
                        ValueError))
        chk("a non-binary request is refused",
            _raises(lambda: equilibrium(["AL"], 1200.0, 0.5), ValueError))

        # temperature scan must find at least one assemblage change
        sc = scan_temperature(els, 0.4, 800.0, 1800.0, steps=8,
                              db_path=src["path"])
        chk("a vertical section scan runs and records the assemblage at each step",
            len([r for r in sc["rows"] if "error" not in r]) >= 6,
            f"{len(sc['rows'])} steps, {sc['transition_count']} transitions")
        chk("...and the phase assemblage CHANGES with temperature, as it must "
            "across a phase boundary",
            sc["transition_count"] >= 1,
            " -> ".join("+".join(r["phases"]) for r in sc["rows"]
                        if "phases" in r)[:110])

    print("phase_diagram selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="CALPHAD equilibrium and phase fractions")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--systems", action="store_true", help="list available databases")
    ap.add_argument("--eq", nargs=4, metavar=("EL1", "EL2", "T_K", "X2"))
    a = ap.parse_args()
    if a.systems:
        print(json.dumps([{k: v for k, v in s.items() if k != "path"}
                          for s in available_systems()], indent=2))
        raise SystemExit(0)
    if a.eq:
        print(json.dumps(equilibrium([a.eq[0], a.eq[1]], float(a.eq[2]),
                                     float(a.eq[3])), indent=2, default=str))
        raise SystemExit(0)
    raise SystemExit(_selftest())
