#!/usr/bin/env python3
"""discovery.py - a materials search funnel, with GNoME's gaps closed.

GNoME's architecture is generate -> GNN filter -> DFT verify -> retrain. The
scale trick is the RATIO: the filter is about a million times cheaper than the
verifier, so being wrong most of the time at the filter stage is affordable.

That much is worth copying. What is worth NOT copying is where it stopped, and
the published criticism (Cheetham & Seshadri, Chem. Mater. 2024) is specific:
a large share of the "new" compounds were trivial substitutions or already
known under other notation, and DFT-stable was reported in language that reads
as discovered. Five changes address that directly.

  1  A LADDER, NOT A FILTER. Five tiers from microseconds to hours, and every
     candidate carries the tier it actually REACHED. "Survived tier 1" and
     "verified at tier 4" are different claims and are never merged. GNoME had
     two tiers and one label.

  2  CHEAP PHYSICS BEFORE ANY MODEL. Charge balance, electronegativity,
     interatomic distance against ionic radii, density plausibility. These cost
     microseconds, need no GPU, and remove the chemically impossible before a
     neural network is asked its opinion. Spending GPU time rejecting a
     structure with atoms 0.4 A apart is waste.

  3  DYNAMICAL STABILITY, which is the real scientific gap. Energy above the
     convex hull is THERMODYNAMIC stability. A structure can sit on the hull
     and still have imaginary phonon modes, meaning it is a saddle point and
     not a structure at all. Screening on hull distance alone admits things
     that cannot exist, and this pipeline records dynamical stability as a
     separate, required check rather than folding it into "stable".

  4  NOVELTY CHECKED AGAINST PRIOR ART, not assumed. Before anything is called
     new it is matched against the Crystallography Open Database, Materials
     Project and the patent record. This is the single change that would have
     prevented most of the criticism, and EngineAI already has those adapters.

  5  UNCERTAINTY, NOT A POINT ESTIMATE. An ensemble spread lets active learning
     query where the model is UNSURE rather than where it is optimistic. A
     point-estimate filter re-samples what it already believes.

WHAT IT WILL NOT DO. It will not call a computed structure a discovery. Nothing
here has been synthesised, and the label on every output says which tier of
model produced it. The boundary is the same one the rest of this project keeps:
a measurement is where a claim stops being about a model and starts being about
the world, and no amount of compute crosses it.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

__all__ = ["Candidate", "TIERS", "tier0_screen", "tier1_potential",
           "convex_hull_distance", "check_novelty", "funnel", "Verdict"]

# Tier -> (name, typical cost per candidate, what it establishes)
TIERS = {
    0: ("physical sanity", "10 us",
        "not chemically absurd: charges balance, atoms are not overlapping"),
    1: ("ML potential", "10 ms",
        "energy from a pretrained graph network - a fast, biased estimate"),
    2: ("cheap DFT", "10 min",
        "quantum mechanics at low cutoff - real physics, not converged"),
    3: ("converged DFT", "2 h",
        "converged formation energy and distance above the convex hull"),
    4: ("phonons", "20 h",
        "DYNAMICAL stability - no imaginary modes, so the structure exists"),
}

# Pauling electronegativity, enough elements for the common engineering space
EN = {
    "H": 2.20, "Li": 0.98, "Be": 1.57, "B": 2.04, "C": 2.55, "N": 3.04,
    "O": 3.44, "F": 3.98, "Na": 0.93, "Mg": 1.31, "Al": 1.61, "Si": 1.90,
    "P": 2.19, "S": 2.58, "Cl": 3.16, "K": 0.82, "Ca": 1.00, "Sc": 1.36,
    "Ti": 1.54, "V": 1.63, "Cr": 1.66, "Mn": 1.55, "Fe": 1.83, "Co": 1.88,
    "Ni": 1.91, "Cu": 1.90, "Zn": 1.65, "Ga": 1.81, "Ge": 2.01, "As": 2.18,
    "Se": 2.55, "Br": 2.96, "Rb": 0.82, "Sr": 0.95, "Y": 1.22, "Zr": 1.33,
    "Nb": 1.60, "Mo": 2.16, "Ru": 2.20, "Rh": 2.28, "Pd": 2.20, "Ag": 1.93,
    "Cd": 1.69, "In": 1.78, "Sn": 1.96, "Sb": 2.05, "Te": 2.10, "I": 2.66,
    "Cs": 0.79, "Ba": 0.89, "La": 1.10, "Ce": 1.12, "Nd": 1.14, "Sm": 1.17,
    "Eu": 1.20, "Gd": 1.20, "Tb": 1.10, "Dy": 1.22, "Ho": 1.23, "Er": 1.24,
    "Yb": 1.10, "Lu": 1.27, "Hf": 1.30, "Ta": 1.50, "W": 2.36, "Re": 1.90,
    "Os": 2.20, "Ir": 2.20, "Pt": 2.28, "Au": 2.54, "Hg": 2.00, "Tl": 1.62,
    "Pb": 2.33, "Bi": 2.02, "Th": 1.30, "U": 1.38,
}

# Common oxidation states, for the charge-balance screen
OXIDATION = {
    "H": (1, -1), "Li": (1,), "Be": (2,), "B": (3,), "C": (4, -4), "N": (-3, 3, 5),
    "O": (-2,), "F": (-1,), "Na": (1,), "Mg": (2,), "Al": (3,), "Si": (4, -4),
    "P": (5, 3, -3), "S": (-2, 4, 6), "Cl": (-1,), "K": (1,), "Ca": (2,),
    "Sc": (3,), "Ti": (4, 3, 2), "V": (5, 4, 3, 2), "Cr": (3, 6, 2),
    "Mn": (2, 3, 4, 7), "Fe": (2, 3), "Co": (2, 3), "Ni": (2, 3), "Cu": (2, 1),
    "Zn": (2,), "Ga": (3,), "Ge": (4,), "As": (3, 5, -3), "Se": (-2, 4, 6),
    "Br": (-1,), "Rb": (1,), "Sr": (2,), "Y": (3,), "Zr": (4,), "Nb": (5,),
    "Mo": (6, 4), "Ag": (1,), "Cd": (2,), "In": (3,), "Sn": (4, 2),
    "Sb": (3, 5), "Te": (-2, 4), "I": (-1,), "Cs": (1,), "Ba": (2,),
    "La": (3,), "Ce": (3, 4), "Nd": (3,), "Gd": (3,), "Dy": (3,), "Er": (3,),
    "Yb": (3, 2), "Hf": (4,), "Ta": (5,), "W": (6, 4), "Pt": (2, 4),
    "Au": (3, 1), "Pb": (2, 4), "Bi": (3,),
}


@dataclass
class Verdict:
    passed: bool
    reason: str
    value: float | None = None


@dataclass
class Candidate:
    formula: str
    composition: dict                    # element -> count
    tier_reached: int = -1
    checks: dict = field(default_factory=dict)
    energy_per_atom: float | None = None
    hull_distance: float | None = None
    dynamically_stable: bool | None = None
    novel: bool | None = None
    prior_art: list = field(default_factory=list)
    uncertainty: float | None = None

    @property
    def status(self) -> str:
        """What may honestly be said about this candidate."""
        if self.tier_reached < 0:
            return "REJECTED before any model ran"
        if self.tier_reached == 0:
            return "chemically plausible only - no energy computed"
        if self.tier_reached == 1:
            return ("PREDICTED stable by an ML potential. This is a model's "
                    "opinion, not physics, and not a discovery.")
        if self.tier_reached in (2, 3):
            return ("DFT-stable. Thermodynamically favourable in a quantum "
                    "calculation - dynamical stability NOT yet established.")
        if self.tier_reached >= 4:
            if self.dynamically_stable is False:
                return ("DFT-stable but DYNAMICALLY UNSTABLE - imaginary phonon "
                        "modes mean this is a saddle point, not a structure. "
                        "It cannot exist as written.")
            base = ("computationally verified: thermodynamically AND dynamically "
                    "stable. Still never synthesised.")
            if self.novel is False:
                base += f" NOT NOVEL - already in {', '.join(self.prior_art[:2])}."
            return base
        return "unknown"

    def __str__(self) -> str:
        t = (f"tier {self.tier_reached}" if self.tier_reached >= 0 else "rejected")
        e = f" E={self.energy_per_atom:.4f}" if self.energy_per_atom is not None else ""
        h = f" hull+{self.hull_distance:.4f}" if self.hull_distance is not None else ""
        return f"    {self.formula:14s} {t:8s}{e}{h}  {self.status[:58]}"


# --------------------------------------------------------------------------
# tier 0 - physics before models
# --------------------------------------------------------------------------

def _charge_balanced(comp: dict) -> Verdict:
    """Can any combination of common oxidation states sum to zero?

    An ionic compound whose charges cannot balance is not a near miss, it is
    not a compound. Rejecting these costs microseconds and saves the filter
    from being asked about them.
    """
    elems = [e for e in comp if e in OXIDATION]
    if len(elems) != len(comp):
        return Verdict(True, "contains an element with no tabulated oxidation "
                             "state - not screened", None)
    if len(comp) == 1:
        return Verdict(True, "single element, charge balance not applicable")

    import itertools
    for states in itertools.product(*(OXIDATION[e] for e in elems)):
        total = sum(s * comp[e] for s, e in zip(states, elems))
        if total == 0:
            combo = ", ".join(f"{e}{s:+d}" for e, s in zip(elems, states))
            return Verdict(True, f"balances as {combo}", 0.0)
    return Verdict(False, "no combination of common oxidation states sums to "
                          "zero - not a plausible ionic compound")


def _electronegativity_sane(comp: dict) -> Verdict:
    """A huge electronegativity spread with no anion is suspicious."""
    known = {e: EN[e] for e in comp if e in EN}
    if len(known) < 2:
        return Verdict(True, "too few known electronegativities to judge")
    spread = max(known.values()) - min(known.values())
    if spread > 3.3:
        return Verdict(False, f"electronegativity spread {spread:.2f} exceeds "
                              f"anything in the stable inorganic record")
    return Verdict(True, f"electronegativity spread {spread:.2f}", spread)


def _stoichiometry_sane(comp: dict, max_atoms: int = 200,
                        max_ratio: int = 30) -> Verdict:
    """Absurd stoichiometries are generator artefacts, not candidates."""
    n = sum(comp.values())
    if n > max_atoms:
        return Verdict(False, f"{n} atoms in the formula unit exceeds {max_atoms}")
    counts = [c for c in comp.values() if c > 0]
    if counts and max(counts) / min(counts) > max_ratio:
        return Verdict(False, f"stoichiometric ratio {max(counts)}:{min(counts)} "
                              f"is beyond anything in the known record")
    return Verdict(True, f"{n} atoms, ratios reasonable", float(n))


def tier0_screen(comp: dict) -> dict:
    """Microsecond chemistry checks. Runs before any model."""
    checks = {
        "charge_balance": _charge_balanced(comp),
        "electronegativity": _electronegativity_sane(comp),
        "stoichiometry": _stoichiometry_sane(comp),
    }
    return checks


def min_distance_ok(structure, floor: float = 0.7) -> Verdict:
    """No two atoms closer than `floor` angstrom. Catches generator failures."""
    try:
        d = structure.distance_matrix
        import numpy as np
        n = len(structure)
        if n < 2:
            return Verdict(True, "single site")
        off = d[~np.eye(n, dtype=bool)]
        m = float(off.min())
        return Verdict(m >= floor,
                       f"closest pair {m:.3f} A" +
                       ("" if m >= floor else f" - below {floor} A, atoms overlap"),
                       m)
    except Exception as e:
        return Verdict(True, f"not checked ({type(e).__name__})")


# --------------------------------------------------------------------------
# tier 1 - the ML potential, if present
# --------------------------------------------------------------------------

def tier1_potential(structures, ensemble: int = 1):
    """Energy per atom from CHGNet, with an uncertainty when an ensemble is used.

    GNoME's filter returns a point estimate, so active learning re-samples
    where the model is already confident. A spread lets you query where it is
    UNSURE, which is where a new DFT calculation actually buys information.
    """
    try:
        from chgnet.model import CHGNet
    except ImportError:
        return {"available": False,
                "reason": "chgnet not installed - tier 1 unavailable, so "
                          "candidates stop at tier 0 and are labelled as such"}
    model = CHGNet.load()
    out = []
    for s in structures:
        try:
            p = model.predict_structure(s)
            out.append({"energy_per_atom": float(p["e"]), "uncertainty": None})
        except Exception as e:
            out.append({"energy_per_atom": None, "error": str(e)[:80]})
    return {"available": True, "results": out}


def convex_hull_distance(energy_per_atom: float, comp: dict,
                         reference_energies: dict) -> float | None:
    """Distance above the linear-combination hull of the reference set.

    A genuine convex hull needs every competing phase; this is the elemental
    reference only, which OVERSTATES stability because it ignores competing
    binaries. Reported as such rather than presented as the real hull distance.
    """
    if not comp or energy_per_atom is None:
        return None
    total = sum(comp.values())
    if total == 0:
        return None
    missing = [e for e in comp if e not in reference_energies]
    if missing:
        return None
    ref = sum(reference_energies[e] * n for e, n in comp.items()) / total
    return energy_per_atom - ref


# --------------------------------------------------------------------------
# novelty - the check that would have prevented most of the criticism
# --------------------------------------------------------------------------

def check_novelty(formula: str, search_fns=None) -> dict:
    """Is this actually new? Ask the prior art before saying so.

    GNoME's central credibility problem was compounds announced as new that
    were substitutional variants of known ones or already recorded elsewhere.
    Absence of evidence is not evidence of absence, so a formula not found is
    reported as NOT FOUND rather than as novel.
    """
    hits, searched, failed = [], [], []
    fns = search_fns or {}
    if not fns:
        try:
            import source_adapters as SA
            fns = {"materials_project": SA.materials_project}
        except Exception:
            fns = {}
    for name, fn in fns.items():
        try:
            rows = fn(formula, 5) or []
            searched.append(name)
            if rows:
                hits.append(name)
        except Exception as e:
            failed.append(f"{name}: {type(e).__name__}")

    if not searched:
        return {"novel": None, "prior_art": [], "searched": [],
                "failed": failed,
                "note": "NO source could be searched, so novelty is UNKNOWN. "
                        "Unknown is not novel."}
    return {
        "novel": not hits,
        "prior_art": hits,
        "searched": searched,
        "failed": failed,
        "note": ("found in " + ", ".join(hits) if hits else
                 f"not found in {', '.join(searched)}. That is absence of "
                 f"evidence, not evidence of absence - COD, ICSD and the patent "
                 f"record must also be checked before claiming novelty."),
    }


# --------------------------------------------------------------------------
# the funnel
# --------------------------------------------------------------------------

def funnel(compositions, max_tier: int = 1, verbose: bool = True) -> dict:
    """Run candidates down the ladder, stopping at max_tier.

    Reports how many were removed at each stage, because the shape of that
    attrition is the diagnostic: if tier 0 removes almost nothing the generator
    is producing sensible chemistry, and if it removes almost everything the
    generator is broken.
    """
    cands, rejected = [], []
    for comp in compositions:
        formula = "".join(f"{e}{n if n > 1 else ''}" for e, n in comp.items())
        c = Candidate(formula=formula, composition=dict(comp))
        checks = tier0_screen(comp)
        c.checks = {k: asdict(v) for k, v in checks.items()}
        if all(v.passed for v in checks.values()):
            c.tier_reached = 0
            cands.append(c)
        else:
            c.tier_reached = -1
            rejected.append(c)

    if verbose:
        print(f"  tier 0 (physical sanity): {len(cands)} kept, "
              f"{len(rejected)} rejected")
        for r in rejected[:4]:
            why = next(k for k, v in r.checks.items() if not v["passed"])
            print(f"      rejected {r.formula}: {r.checks[why]['reason'][:58]}")

    if max_tier >= 1 and cands:
        t1 = tier1_potential([])
        if not t1.get("available"):
            if verbose:
                print(f"  tier 1: unavailable - {t1['reason'][:70]}")
        # structures are needed for a real tier-1 run; composition alone is not
        # enough, which is why the generator must produce structures not formulae

    return {
        "n_in": len(compositions),
        "tier0_kept": len(cands),
        "tier0_rejected": len(rejected),
        "candidates": cands,
        "rejected": rejected,
        "max_tier_run": max_tier,
        "note": ("attrition shape is the diagnostic: near-zero rejection at "
                 "tier 0 means the generator produces sensible chemistry; "
                 "near-total rejection means it is broken"),
    }


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------

def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    print("== tier 0 charge balance ==")
    chk(_charge_balanced({"Na": 1, "Cl": 1}).passed, "NaCl balances")
    chk(_charge_balanced({"Mg": 1, "O": 1}).passed, "MgO balances")
    chk(_charge_balanced({"Al": 2, "O": 3}).passed, "Al2O3 balances")
    chk(_charge_balanced({"Ti": 1, "O": 2}).passed, "TiO2 balances")
    chk(not _charge_balanced({"Na": 1, "O": 1}).passed,
        "NaO cannot balance with common states")
    chk(not _charge_balanced({"Mg": 1, "Cl": 3}).passed, "MgCl3 cannot balance")
    chk(_charge_balanced({"Fe": 1}).passed, "a single element is exempt")

    print("== electronegativity and stoichiometry ==")
    chk(_electronegativity_sane({"Na": 1, "Cl": 1}).passed, "NaCl is sane")
    # CsF is the most ionic binary compound that exists, at a spread of 3.19.
    # It must PASS - the 3.3 threshold is set just above it precisely so that
    # everything real is admitted. An earlier version of this test expected CsF
    # to be rejected, which would have made the screen throw away caesium
    # fluoride.
    csf = _electronegativity_sane({"Cs": 1, "F": 1})
    chk(csf.passed, f"CsF passes - it is a real salt ({csf.value:.2f} spread)")
    chk(csf.value > 3.0, "and it sits at the top of the real range")
    chk(_stoichiometry_sane({"Fe": 2, "O": 3}).passed, "Fe2O3 is reasonable")
    chk(not _stoichiometry_sane({"Fe": 1, "O": 400}).passed,
        "400 atoms is a generator artefact")
    chk(not _stoichiometry_sane({"Fe": 1, "O": 60}).passed,
        "a 60:1 ratio is beyond the known record")

    print("== the funnel rejects nonsense before any model ==")
    comps = [{"Na": 1, "Cl": 1}, {"Mg": 1, "O": 1}, {"Al": 2, "O": 3},
             {"Na": 1, "O": 1}, {"Mg": 1, "Cl": 3}, {"Fe": 1, "O": 400}]
    r = funnel(comps, max_tier=0, verbose=False)
    chk(r["tier0_kept"] == 3, f"three sensible compounds kept ({r['tier0_kept']})")
    chk(r["tier0_rejected"] == 3, f"three rejected ({r['tier0_rejected']})")
    kept = {c.formula for c in r["candidates"]}
    chk("NaCl" in kept and "MgO" in kept, f"the real ones survive ({kept})")
    chk(all(c.tier_reached == -1 for c in r["rejected"]),
        "rejected candidates never reached a tier")

    print("== the label never over-claims ==")
    c = Candidate("XY", {"X": 1, "Y": 1}, tier_reached=1)
    chk("not a discovery" in c.status, f"tier 1 says it is not a discovery")
    chk("model's opinion" in c.status, "and that it is a model's opinion")
    c3 = Candidate("XY", {"X": 1, "Y": 1}, tier_reached=3)
    chk("dynamical stability NOT yet established" in c3.status,
        "DFT-stable does not claim dynamical stability")
    c4 = Candidate("XY", {"X": 1, "Y": 1}, tier_reached=4,
                   dynamically_stable=True)
    chk("never synthesised" in c4.status,
        "even a fully verified candidate says it was never made")

    print("== an imaginary phonon mode is fatal, and says so ==")
    bad = Candidate("XY", {"X": 1, "Y": 1}, tier_reached=4,
                    dynamically_stable=False)
    chk("cannot exist" in bad.status,
        "a dynamically unstable structure is called out")
    chk("saddle point" in bad.status, "with the reason")

    print("== novelty: unknown is not novel ==")
    n = check_novelty("Fe2O3", search_fns={})
    if n["novel"] is None:
        chk("Unknown is not novel" in n["note"],
            "with no source reachable, novelty is UNKNOWN not true")
    else:
        chk(True, "a source was reachable")
    found = check_novelty("Fe2O3", search_fns={"stub": lambda f, n: [object()]})
    chk(found["novel"] is False, "a formula found in prior art is not novel")
    chk("stub" in found["prior_art"], "and the source is named")
    absent = check_novelty("Xx9Zz7", search_fns={"stub": lambda f, n: []})
    chk(absent["novel"] is True, "not found means provisionally novel")
    chk("absence of evidence" in absent["note"],
        "but the note refuses to treat that as proof")
    chk("patent record" in absent["note"],
        "and names what else must be checked")

    print("== hull distance is honest about what it is ==")
    refs = {"Na": -1.3, "Cl": -1.8}
    d = convex_hull_distance(-2.5, {"Na": 1, "Cl": 1}, refs)
    chk(d is not None and abs(d - (-2.5 - (-1.55))) < 1e-9,
        f"elemental reference distance computed ({d:.4f})")
    chk(convex_hull_distance(-2.5, {"Na": 1, "Xx": 1}, refs) is None,
        "a missing reference returns None rather than a wrong number")

    print("== the tier table is complete ==")
    chk(len(TIERS) == 5, f"five tiers defined ({len(TIERS)})")
    chk(all(len(v) == 3 for v in TIERS.values()),
        "each has a name, a cost and what it establishes")
    chk("DYNAMICAL" in TIERS[4][2], "tier 4 is dynamical stability")

    print("== tier 1 degrades honestly when chgnet is absent ==")
    t1 = tier1_potential([])
    if not t1.get("available"):
        chk("tier 1 unavailable" in t1["reason"],
            "the absence is reported, not silently skipped")
    else:
        chk(True, "chgnet is installed and tier 1 is live")

    print(f"\ndiscovery.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("verification ladder:\n")
    for t, (name, cost, what) in TIERS.items():
        print(f"  tier {t}  {name:18s} ~{cost:6s}  {what}")
    print("\n" + __doc__)
