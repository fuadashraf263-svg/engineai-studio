#!/usr/bin/env python3
"""data_authenticity.py - is this data real?

Before EngineAI researches a dataset a user hands it, it has to answer a
question most analysis tools never ask: is this a record of something that
actually happened?

Three outcomes matter and they are NOT the same thing:

  REAL_MEASURED   carries the fingerprints of measurement - instrument
                  resolution, heavy tails, imperfect correlations, missingness
  SYNTHETIC       computed from equations. Not fraud, often useful, but it
                  cannot validate a model and must never be reported as
                  evidence about the physical world
  SUSPECT         carries indicators associated with fabrication - digit
                  anomalies, impossible precision, duplicated blocks, values
                  outside physical limits

WHAT THIS MODULE WILL NOT DO. It will not call a dataset fraudulent. Every
test here produces an INDICATOR, and indicators are evidence for a human to
weigh, not a verdict on anyone's honesty. Benford's law fails on plenty of
legitimate data with a narrow range; a perfect correlation is a red flag in a
laboratory record and completely normal in a derived column. The module's job
is to say what it noticed and how strongly, and to be explicit that a person
decides.

The one test here that is genuinely EngineAI's own is physical plausibility.
Generic data tools cannot know that a steel yield strength of 5000 MPa is
impossible, that nothing is colder than 0 K, or that a reported efficiency
above 1 violates the first law. EngineAI can, because it carries the constants
and the limits already.
"""
from __future__ import annotations

import hashlib
import math
import sys
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

__all__ = ["benford", "terminal_digits", "duplication", "granularity",
           "synthetic_indicators", "physical_plausibility", "assess",
           "Indicator", "Assessment", "PHYSICAL_LIMITS"]


@dataclass
class Indicator:
    name: str
    flagged: bool
    strength: str            # none | weak | moderate | strong
    detail: str
    statistic: float | None = None

    def __str__(self) -> str:
        mark = {"none": "ok      ", "weak": "note    ",
                "moderate": "CONCERN ", "strong": "STRONG  "}[self.strength]
        return f"    {mark} {self.name:24s} {self.detail}"


@dataclass
class Assessment:
    verdict: str
    confidence: str
    indicators: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def flagged(self) -> list:
        return [i for i in self.indicators if i.flagged]

    def __str__(self) -> str:
        head = f"  VERDICT: {self.verdict}  (confidence: {self.confidence})"
        body = "\n".join(str(i) for i in self.indicators)
        notes = "".join(f"\n  note: {n}" for n in self.notes)
        return f"{head}\n{body}{notes}"


# --------------------------------------------------------------------------
# Benford's law
# --------------------------------------------------------------------------

BENFORD = np.array([math.log10(1 + 1 / d) for d in range(1, 10)])


def benford(x: Sequence[float], min_n: int = 200) -> Indicator:
    """First-digit distribution against Benford's law.

    Benford applies to quantities spanning several orders of magnitude and
    arising from multiplicative processes. It does NOT apply to a narrow
    physical range - human heights, boiling points, a sensor pinned between 4
    and 20 mA - and a tool that flags those as fraud is worse than no tool.
    So applicability is checked FIRST and the test declines when it does not
    apply, rather than producing a meaningless statistic.
    """
    v = np.asarray(x, dtype=float)
    v = np.abs(v[np.isfinite(v) & (v != 0)])
    if len(v) < min_n:
        return Indicator("benford", False, "none",
                         f"declined: {len(v)} usable values, needs {min_n}")
    decades = np.log10(v.max() / v.min()) if v.min() > 0 else 0
    if decades < 2:
        return Indicator("benford", False, "none",
                         f"declined: data spans only {decades:.1f} decades, "
                         "Benford does not apply to a narrow range")
    lead = np.array([int(str(f"{d:.10e}")[0]) for d in v])
    obs = np.array([(lead == d).sum() for d in range(1, 10)], dtype=float)
    exp = BENFORD * obs.sum()
    chi2 = float(((obs - exp) ** 2 / exp).sum())
    mad = float(np.mean(np.abs(obs / obs.sum() - BENFORD)))
    # Nigrini's MAD bands for first digits
    if mad < 0.006:
        s, f = "none", False
    elif mad < 0.012:
        s, f = "weak", False
    elif mad < 0.015:
        s, f = "moderate", True
    else:
        s, f = "strong", True
    return Indicator("benford", f, s,
                     f"MAD {mad:.4f}, chi2 {chi2:.1f} over {len(v)} values "
                     f"spanning {decades:.1f} decades", mad)


def terminal_digits(x: Sequence[float], min_n: int = 200) -> Indicator:
    """Last significant digit should be uniform in genuinely measured data.

    People inventing numbers over-use certain digits and avoid others. Real
    instruments do not.
    """
    v = np.asarray(x, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) < min_n:
        return Indicator("terminal digits", False, "none",
                         f"declined: {len(v)} values, needs {min_n}")
    # The recorded precision has to be established FIRST and then held fixed.
    # Formatting with %g strips trailing zeros, so 50.10 reads as last-digit 1
    # and every cleanly rounded dataset looks like it is avoiding zero - the
    # test would then flag exactly the well-behaved instrument data it is
    # supposed to clear.
    s = [f"{d:.10g}" for d in v]
    decs = [len(t.split(".")[1]) for t in s if "." in t]
    if len(decs) < min_n * 0.5:
        return Indicator("terminal digits", False, "none",
                         "declined: values are mostly integers, no terminal digit")
    prec = int(np.bincount(np.array(decs)).argmax())      # modal recorded precision
    if prec == 0:
        return Indicator("terminal digits", False, "none",
                         "declined: modal precision is 0 decimals")
    scaled = np.round(v * 10 ** prec).astype(np.int64)
    last = np.abs(scaled) % 10
    obs = np.array([(last == d).sum() for d in range(10)], dtype=float)
    exp = obs.sum() / 10.0
    chi2 = float(((obs - exp) ** 2 / exp).sum())
    # chi2 with 9 df: 16.9 at p=0.05, 21.7 at p=0.01, 27.9 at p=0.001
    if chi2 < 16.9:
        st, fl = "none", False
    elif chi2 < 21.7:
        st, fl = "weak", False
    elif chi2 < 27.9:
        st, fl = "moderate", True
    else:
        st, fl = "strong", True
    return Indicator("terminal digits", fl, st,
                     f"chi2 {chi2:.1f} on 9 df over {len(last)} values "
                     f"({'uniform' if chi2 < 16.9 else 'NON-uniform'})", chi2)


def duplication(rows: np.ndarray, min_n: int = 20) -> Indicator:
    """Exact duplicate rows, and repeated contiguous blocks.

    A handful of duplicates is normal in real records. A repeated BLOCK is
    copy-paste, and that is a different thing.
    """
    a = np.asarray(rows, dtype=float)
    if a.ndim == 1:
        a = a.reshape(-1, 1)
    good = a[np.all(np.isfinite(a), axis=1)]
    if len(good) < min_n:
        return Indicator("duplication", False, "none", "declined: too few rows")
    uniq = np.unique(good, axis=0)
    dup = len(good) - len(uniq)
    frac = dup / len(good)
    # contiguous repeated blocks of 3+ identical consecutive rows
    blocks = 0
    run = 1
    for i in range(1, len(good)):
        if np.array_equal(good[i], good[i - 1]):
            run += 1
        else:
            if run >= 3:
                blocks += 1
            run = 1
    # Few columns, or coarse integer levels, make repeats INEVITABLE rather
    # than suspicious. A servo table of (pgain, vgain, rise time) over a
    # designed grid repeats by construction, and calling that fabrication
    # would condemn a perfectly ordinary factorial experiment.
    ncol = good.shape[1]
    levels = int(np.prod([min(len(np.unique(good[:, j])), 50) for j in range(ncol)]))
    expected_repeats = ncol < 5 or levels < 4 * len(good)
    if expected_repeats and blocks == 0:
        return Indicator("duplication", False, "none",
                         f"{dup} duplicate rows ({frac*100:.1f}%) - expected: only "
                         f"{ncol} numeric column(s) over ~{levels} distinct "
                         f"combinations, so repeats are structural", frac)

    if frac < 0.01 and blocks == 0:
        s, f = "none", False
    elif frac < 0.05 and blocks == 0:
        s, f = "weak", False
    elif frac < 0.20 or blocks:
        s, f = "moderate", True
    else:
        s, f = "strong", True
    return Indicator("duplication", f, s,
                     f"{dup} duplicate rows ({frac*100:.1f}%), "
                     f"{blocks} repeated blocks of 3+", frac)


def granularity(x: Sequence[float]) -> Indicator:
    """Recorded precision should reflect an instrument, not a random generator.

    A measured column has a resolution: readings land on multiples of the
    instrument's least count. A generated column usually carries full double
    precision on every value, which is a strong sign nothing was measured.
    """
    v = np.asarray(x, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) < 50:
        return Indicator("granularity", False, "none", "declined: too few values")
    decs = []
    for d in v[:5000]:
        t = f"{d:.12g}"
        decs.append(len(t.split(".")[1]) if "." in t else 0)
    decs = np.array(decs)
    frac_full = float(np.mean(decs >= 10))
    modal = int(np.bincount(decs).argmax())
    consistent = float(np.mean(decs <= modal + 1))
    if frac_full > 0.5:
        return Indicator("granularity", True, "moderate",
                         f"{frac_full*100:.0f}% of values carry 10+ decimals - "
                         f"no instrument reports that; consistent with computation",
                         frac_full)
    return Indicator("granularity", False, "none",
                     f"modal precision {modal} decimals, {consistent*100:.0f}% "
                     f"within one place of it - consistent with an instrument", frac_full)


def synthetic_indicators(cols: dict[str, np.ndarray]) -> Indicator:
    """Does any pair of columns sit on a noiseless functional relationship?

    Real measurement always carries scatter. A correlation of exactly 1.000000
    between two measured quantities does not happen; between a quantity and a
    column derived from it, it happens every time. Finding several is the
    clearest signature that a table was computed rather than observed.
    """
    names = [k for k, v in cols.items()
             if np.isfinite(np.asarray(v, float)).sum() > 50
             and np.nanstd(np.asarray(v, float)) > 0]
    perfect = []
    for i, a in enumerate(names):
        va = np.asarray(cols[a], float)
        for b in names[i + 1:]:
            vb = np.asarray(cols[b], float)
            m = np.isfinite(va) & np.isfinite(vb)
            if m.sum() < 50:
                continue
            r = abs(np.corrcoef(va[m], vb[m])[0, 1])
            if r > 1 - 1e-12:
                perfect.append(f"{a}~{b}")
            else:                                # also catch log-linear exactness
                p = m & (va > 0) & (vb > 0)
                if p.sum() > 50 and np.std(np.log(va[p])) > 0 and np.std(np.log(vb[p])) > 0:
                    rl = abs(np.corrcoef(np.log(va[p]), np.log(vb[p]))[0, 1])
                    if rl > 1 - 1e-12:
                        perfect.append(f"log({a})~log({b})")
    # Pairwise correlation cannot see a MULTIVARIATE derivation. I = b*h^3/12
    # is exactly determined by b and h, yet correlates imperfectly with either
    # one alone - so the pairwise test above passes it, and a table of derived
    # engineering columns would be waved through as measurement. Fitting each
    # column as a monomial of the others catches what pairs cannot.
    # SUBSETS, not all-columns-at-once. Regressing a column on twelve others
    # fits almost anything, so an exact residual would prove nothing; a column
    # that is an exact monomial of THREE specific others is a derivation.
    # A BUDGET, because the search is combinatorial. An 82-column table has
    # 85,320 three-subsets per column; running them all would take hours and
    # the honest move is to say the search was truncated rather than to hang
    # or to silently test less than advertised.
    # Rows are subsampled too. An EXACT relationship is exact on any 400 rows;
    # carrying 21,000 through every candidate fit buys no extra certainty and
    # costs the whole search.
    import itertools as _it
    budget, truncated = 40000, False
    n_all = len(next(iter(cols.values())))
    rs = np.random.default_rng(0)
    pick = rs.choice(n_all, min(400, n_all), replace=False)
    sub_cols = {k: np.asarray(v, float)[pick] for k, v in cols.items()}

    for name in names:
        if budget <= 0:
            truncated = True
            break
        others = [o for o in names if o != name]
        if not others:
            continue
        y = sub_cols[name]
        found = False
        for k in (1, 2, 3):
            if found or k > len(others) or budget <= 0:
                break
            for sub in _it.combinations(others, k):
                if budget <= 0:
                    truncated = True
                    break
                budget -= 1
                X = np.column_stack([sub_cols[o] for o in sub])
                m = np.isfinite(y) & (y > 0) & np.all(np.isfinite(X) & (X > 0), axis=1)
                if m.sum() < 40:
                    continue
                A = np.column_stack([np.ones(m.sum()), np.log(X[m])])
                try:
                    coef, *_ = np.linalg.lstsq(A, np.log(y[m]), rcond=None)
                except np.linalg.LinAlgError:
                    continue
                if float(np.max(np.abs(np.log(y[m]) - A @ coef))) < 1e-10:
                    terms = " ".join(f"{o}^{c:.3g}" for o, c in zip(sub, coef[1:])
                                     if abs(c) > 1e-9)
                    perfect.append(f"{name}={math.exp(coef[0]):.3g}*{terms}")
                    found = True
                    break

    n = len(perfect)
    trunc_note = " (search truncated at the budget)" if truncated else ""
    if n == 0:
        return Indicator("exact relationships", False, "none",
                         "no noiseless relationship found - consistent with "
                         "measurement" + trunc_note)
    s = "strong" if n >= 3 else "moderate"
    return Indicator("exact relationships", True, s,
                     f"{n} noiseless pair(s): {', '.join(perfect[:4])}"
                     + (" ..." if n > 4 else "")
                     + " - computed, not measured", float(n))


# --------------------------------------------------------------------------
# physical plausibility - the check only an engineering system can make
# --------------------------------------------------------------------------

# (low, high, unit, why). Deliberately generous: these are IMPOSSIBILITY
# bounds, not typical ranges, so tripping one is a genuine problem rather
# than an unusual but legal value.
PHYSICAL_LIMITS = {
    "temperature_K":      (0.0, 1e8, "K", "nothing is colder than absolute zero"),
    "temperature_C":      (-273.15, 1e8, "C", "nothing is colder than absolute zero"),
    "pressure_abs_Pa":    (0.0, 1e14, "Pa", "absolute pressure cannot be negative"),
    "density_kg_m3":      (0.0, 25000.0, "kg/m3", "denser than osmium (22590) is not a bulk material"),
    "youngs_modulus_Pa":  (1e5, 1.3e12, "Pa", "stiffer than diamond (~1.2 TPa) is not a material"),
    "yield_strength_Pa":  (1e4, 2e10, "Pa", "above ~20 GPa exceeds the strongest known bulk metals"),
    "efficiency":         (0.0, 1.0, "-", "an efficiency above 1 violates the first law"),
    "poisson_ratio":      (-1.0, 0.5, "-", "isotropic elasticity bounds Poisson's ratio"),
    "mass_fraction":      (0.0, 1.0, "-", "a fraction cannot exceed unity"),
    "relative_humidity":  (0.0, 105.0, "%", "RH above ~105% is not physical"),
    "speed_m_s":          (0.0, 299792458.0, "m/s", "nothing exceeds the speed of light"),
    "thermal_cond_W_mK":  (0.0, 3000.0, "W/mK", "above diamond (~2200) is not a bulk solid"),
}

# How a column name is recognised as one of the quantities above.
#
# These are matched on WORD BOUNDARIES, never as bare substrings. Substring
# matching put "nu" inside "number_of_elements" and "eta" inside "zeta", so a
# count of chemical elements was held to Poisson's ratio and an overdamped
# damping ratio was condemned for exceeding an efficiency of 1. Both were
# nonsense, and both were reported with high confidence - which is worse than
# not checking at all.
_NAME_HINTS = [
    (("temp", "temperature", "tit", "tat"), "temperature_C"),
    (("kelvin",), "temperature_K"),
    (("density", "densities", "rho"), "density_kg_m3"),
    (("youngs", "young", "emod", "elastic_modulus", "e_pa"), "youngs_modulus_Pa"),
    (("yield", "yield_strength"), "yield_strength_Pa"),
    (("efficiency", "eff"), "efficiency"),
    (("poisson", "poissons_ratio"), "poisson_ratio"),
    (("humidity", "rh"), "relative_humidity"),
    (("thermalconductivity", "thermal_conductivity"), "thermal_cond_W_mK"),
]


def _name_matches(column: str, hint: str) -> bool:
    """Token-boundary match, so 'nu' cannot fire inside 'number_of_elements'."""
    import re as _re
    return bool(_re.search(r"(^|[^a-z])" + _re.escape(hint) + r"([^a-z]|$)",
                           column.lower()))


def physical_plausibility(cols: dict[str, np.ndarray],
                          declared: dict[str, str] | None = None) -> Indicator:
    """Check values against hard physical limits.

    Column names are matched conservatively by hint, and the caller can
    declare the mapping explicitly. A column that cannot be identified is
    NOT checked - guessing what a column means and then condemning it is
    worse than staying quiet.
    """
    mapping = dict(declared or {})
    for name in cols:
        if name in mapping:
            continue
        low = name.lower()
        # A STATISTIC of a property is not the property. "mean_ThermalConductivity"
        # is a composition descriptor, not a conductivity, and holding it to a
        # material's physical limits invents a violation that is not there.
        if any(low.startswith(p) or f"_{p}" in low for p in
               ("mean", "wtd", "gmean", "std", "range", "entropy", "log", "delta")):
            continue
        for hints, key in _NAME_HINTS:
            if any(_name_matches(low, h) for h in hints):
                mapping[name] = key
                break

    violations, checked = [], 0
    for name, key in mapping.items():
        if key not in PHYSICAL_LIMITS or name not in cols:
            continue
        lo, hi, unit, why = PHYSICAL_LIMITS[key]
        v = np.asarray(cols[name], float)
        v = v[np.isfinite(v)]
        if not len(v):
            continue
        # Sentinel missing-value codes are a DATA CONVENTION, not a physics
        # violation. -200 in the air-quality record means "no reading", and
        # reporting it as a temperature below absolute zero would be a false
        # alarm on a perfectly sound dataset.
        for sent in (-200.0, -999.0, -9999.0, -99.0):
            n_sent = int((v == sent).sum())
            if n_sent >= max(10, 0.005 * len(v)):
                v = v[v != sent]
        if not len(v):
            continue
        checked += 1
        bad = int(((v < lo) | (v > hi)).sum())
        if bad:
            violations.append(f"{name}: {bad} value(s) outside [{lo:g}, {hi:g}] "
                              f"{unit} - {why}")
    if not checked:
        return Indicator("physical limits", False, "none",
                         "no column could be identified with a known physical "
                         "quantity - not checked rather than assumed fine")
    if not violations:
        return Indicator("physical limits", False, "none",
                         f"{checked} identified quantit(y/ies) all inside physical limits")
    return Indicator("physical limits", True, "strong",
                     "; ".join(violations[:3]), float(len(violations)))


# --------------------------------------------------------------------------
# the overall assessment
# --------------------------------------------------------------------------

def assess(cols: dict[str, np.ndarray], target: str | None = None,
           declared_quantities: dict[str, str] | None = None,
           provenance: dict | None = None) -> Assessment:
    """Run the full battery and reach a verdict about what this data IS.

    The verdict separates SYNTHETIC from SUSPECT deliberately. Generated data
    is legitimate and common; it simply cannot serve as evidence about the
    physical world, and quietly treating it as measurement is how a modelling
    exercise turns into a false empirical claim.
    """
    arr = {k: np.asarray(v, float) for k, v in cols.items()
           if np.asarray(v).dtype.kind in "if"}
    if not arr:
        return Assessment("UNKNOWN", "none", [],
                          ["no numeric columns to assess"])

    inds = []
    # pick the widest-ranging column for the digit tests
    spread = {k: (np.nanmax(v) / np.nanmin(np.abs(v[v != 0])))
              if np.any(v != 0) and np.nanmin(np.abs(v[v != 0])) > 0 else 1.0
              for k, v in arr.items()}
    widest = max(spread, key=spread.get)
    inds.append(benford(arr[widest]))
    inds.append(terminal_digits(arr[widest]))
    stack = np.column_stack(list(arr.values()))
    inds.append(duplication(stack))
    inds.append(granularity(arr[widest]))
    inds.append(synthetic_indicators(arr))
    inds.append(physical_plausibility(arr, declared_quantities))

    prov = provenance or {}
    if prov:
        has = [k for k in ("doi", "url", "sha256", "license") if prov.get(k)]
        inds.append(Indicator("provenance", not has, "none" if has else "moderate",
                              f"declared: {', '.join(has)}" if has
                              else "no DOI, URL, checksum or licence supplied"))
    else:
        inds.append(Indicator("provenance", True, "moderate",
                              "NO provenance supplied - origin unverifiable"))

    exact = next(i for i in inds if i.name == "exact relationships")
    gran = next(i for i in inds if i.name == "granularity")
    phys = next(i for i in inds if i.name == "physical limits")
    dup = next(i for i in inds if i.name == "duplication")

    # DIGIT TESTS ARE INFORMATIONAL ONLY on engineering data, and cannot on
    # their own move the verdict. Benford assumes a multiplicative process;
    # engineering records routinely violate it for honest reasons - a designed
    # log-spaced frequency sweep, unit conversion, instrument quantisation,
    # values pinned inside a control band. Tested against this corpus they
    # flagged genuine UCI measurement sets as suspicious. Keeping them as
    # decisive evidence would mean accusing real data of being fake, which is
    # a far worse error than missing a fabrication.
    DECISIVE = {"physical limits", "exact relationships", "granularity", "duplication"}
    strong = [i for i in inds if i.flagged and i.strength == "strong"
              and i.name in DECISIVE]

    notes = []
    if phys.flagged:
        verdict, conf = "SUSPECT", "high"
        notes.append("values violate hard physical limits - this cannot be a "
                     "correct record of a physical measurement, whatever its origin")
    elif exact.flagged or gran.flagged:
        # Full double precision on most values, or any exact functional
        # relationship, is the fingerprint of computation. Neither happens
        # when an instrument writes the number down.
        why = []
        if exact.flagged:
            why.append("exact functional relationships between columns")
        if gran.flagged:
            why.append("full double precision where an instrument would round")

        # MIXED is a real and common case: a measured target with computed
        # features around it - descriptor sets, engineered features, derived
        # ratios. Calling the whole table SYNTHETIC would throw away a genuine
        # measurement; calling it REAL would licence treating computed columns
        # as observations. Both are wrong, so it gets its own verdict.
        tgt_is_measured = False
        if target and target in arr:
            tv = arr[target]
            tv = tv[np.isfinite(tv)]
            if len(tv):
                t_dec = [len(f"{d:.12g}".split(".")[1]) if "." in f"{d:.12g}" else 0
                         for d in tv[:2000]]
                tgt_is_measured = float(np.mean(np.array(t_dec) >= 10)) < 0.2
        if tgt_is_measured and gran.flagged:
            verdict, conf = "MIXED", "moderate"
            notes.append(
                f"the target '{target}' is recorded at instrument precision while "
                "the features are computed (" + " and ".join(why) + "). Treat the "
                "target as measurement and the features as derived quantities - "
                "a model fitted here predicts a real observable from computed "
                "descriptors, which is legitimate, but the FEATURES are not evidence "
                "about the physical world.")
        else:
            verdict = "SYNTHETIC"
            conf = "high" if (exact.flagged and gran.flagged) else "moderate"
            notes.append("computed from equations (" + " and ".join(why) + "). "
                         "Legitimate for benchmarking and law-rediscovery, but it "
                         "CANNOT validate a model against the physical world and "
                         "must not be reported as if it did.")
    elif len(strong) >= 2 or (dup.flagged and dup.strength == "strong"):
        verdict, conf = "SUSPECT", "moderate"
        notes.append("multiple strong indicators - route to a human reviewer")
    elif any(i.flagged for i in inds):
        verdict, conf = "REAL_MEASURED", "moderate"
        notes.append("flags present but none decisive; consistent with real data "
                     "being messy, which real data is")
    else:
        verdict, conf = "REAL_MEASURED", "high"

    digits = [i for i in inds if i.name in ("benford", "terminal digits") and i.flagged]
    if digits:
        notes.append("digit-distribution tests flagged (" +
                     ", ".join(i.name for i in digits) + ") but were NOT used to "
                     "decide: engineering data breaks them for honest reasons such "
                     "as designed sweeps, rounding and unit conversion. Reported "
                     "for a reviewer, not counted as evidence.")

    notes.append("these are INDICATORS, not proof. No test here can establish "
                 "fraud, and none is offered as an accusation - a person decides.")
    return Assessment(verdict, conf, inds, notes)


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

    rng = np.random.default_rng(11)

    print("== Benford ==")
    # multiplicative process over many decades follows Benford
    bx = np.exp(rng.uniform(0, 12, 3000))
    chk(not benford(bx).flagged, f"natural multiplicative data passes ({benford(bx).detail})")
    # a narrow range must be DECLINED, not condemned
    narrow = rng.normal(100, 2, 3000)
    chk("declined" in benford(narrow).detail, "narrow-range data is declined, not flagged")
    # Digits invented across many decades, but with leading digits drawn
    # uniformly instead of logarithmically - the classic fabrication signature.
    # It must span decades or the test correctly declines to judge it.
    mant = rng.uniform(1, 10, 3000)                 # uniform leading digits
    faked = mant * 10.0 ** rng.integers(0, 8, 3000)
    chk(benford(faked).flagged, f"digit-skewed data is flagged ({benford(faked).detail})")

    print("== terminal digits ==")
    real = np.round(rng.normal(50, 10, 2000), 2)
    chk(not terminal_digits(real).flagged, "instrument-rounded data passes")
    # a human avoiding 0 and 5 in the last place
    picked = np.array([round(v, 2) for v in rng.normal(50, 10, 2000)])
    picked = picked + np.where((picked * 100 % 10).astype(int) % 5 == 0, 0.01, 0)
    chk(terminal_digits(picked).flagged, "digit-avoidant data is flagged")

    print("== duplication ==")
    clean = rng.normal(0, 1, (500, 4))
    chk(not duplication(clean).flagged, "unique rows pass")
    pasted = np.vstack([clean, np.repeat(clean[:5], 6, axis=0)])
    chk(duplication(pasted).flagged, "a copy-pasted block is flagged")

    print("== granularity ==")
    measured = np.round(rng.normal(20, 5, 1000), 1)
    chk(not granularity(measured).flagged, "0.1-resolution readings look measured")
    computed = rng.normal(20, 5, 1000)                    # full double precision
    chk(granularity(computed).flagged, "full-precision floats are flagged as computed")

    print("== synthetic detection ==")
    b = rng.uniform(0.02, 0.2, 400)
    h = rng.uniform(0.05, 0.4, 400)
    syn = {"b": b, "h": h, "I": b * h ** 3 / 12, "A": b * h}
    si = synthetic_indicators(syn)
    chk(si.flagged, f"derived columns are caught ({si.detail[:50]})")
    noisy = {"b": b, "h": h, "I": b * h ** 3 / 12 * rng.normal(1, 0.03, 400)}
    chk(not synthetic_indicators(noisy).flagged,
        "the same physics WITH measurement scatter is not flagged")

    print("== physical limits ==")
    good = {"temperature_C": rng.uniform(-40, 200, 500),
            "density": rng.uniform(1000, 8000, 500)}
    chk(not physical_plausibility(good).flagged, "legal values pass")
    bad = {"temperature_C": np.concatenate([rng.uniform(20, 30, 499), [-400.0]]),
           "density": rng.uniform(1000, 8000, 500)}
    pv = physical_plausibility(bad)
    chk(pv.flagged and "absolute zero" in pv.detail,
        f"below absolute zero is caught ({pv.detail[:60]})")
    bad2 = {"efficiency": np.concatenate([rng.uniform(0.3, 0.6, 499), [1.4]])}
    chk(physical_plausibility(bad2).flagged, "efficiency above 1 is caught")
    # an unidentifiable column must NOT be condemned
    chk(not physical_plausibility({"xyz_blob": rng.normal(0, 1, 300)}).flagged,
        "an unidentified column is left unchecked, not condemned")

    print("== end-to-end verdicts ==")
    a_syn = assess(syn, provenance={"url": "generated"})
    chk(a_syn.verdict == "SYNTHETIC", f"generated table -> SYNTHETIC (got {a_syn.verdict})")
    chk(any("cannot validate" in n or "CANNOT validate" in n for n in a_syn.notes),
        "and says it cannot validate against the physical world")

    real_tbl = {"load_N": np.round(rng.uniform(100, 9000, 800), 1),
                "defl_mm": np.round(rng.uniform(0.1, 12, 800), 3),
                "temperature_C": np.round(rng.uniform(15, 30, 800), 1)}
    a_real = assess(real_tbl, provenance={"doi": "10.1234/x", "sha256": "ab", "license": "CC-BY"})
    chk(a_real.verdict == "REAL_MEASURED", f"messy table -> REAL_MEASURED (got {a_real.verdict})")

    imposs = dict(real_tbl)
    imposs["temperature_C"] = np.concatenate([np.round(rng.uniform(15, 30, 799), 1), [-500.0]])
    a_bad = assess(imposs, provenance={"doi": "10.1234/x"})
    chk(a_bad.verdict == "SUSPECT", f"impossible physics -> SUSPECT (got {a_bad.verdict})")
    chk(a_bad.confidence == "high", "and with high confidence")

    a_noprov = assess(real_tbl)
    chk(any(i.name == "provenance" and i.flagged for i in a_noprov.indicators),
        "missing provenance is itself flagged")
    chk(all("INDICATORS, not proof" in n for n in [a_bad.notes[-1]]),
        "every assessment ends by saying a person decides")

    print("== against the real corpus, where the origin is documented ==")
    # The strongest available test: 14 files whose provenance is recorded in
    # PROVENANCE.md, classified without being told which is which.
    #
    # Two of these labels were WRONG when first written. electrical_grid_stability
    # is the DSGC simulation, not a measurement, and the superconductivity file
    # is 81 computed descriptors around a measured Tc. The module said so before
    # the labels were corrected - it was right and the expectation was wrong.
    truth = {
        "data/aero_airfoil_self_noise": ("REAL_MEASURED", "scaled-sound-pressure"),
        "data/chemical_gas_turbine_co_nox": ("REAL_MEASURED", "NOX"),
        "data/civil_concrete_compressive_strength": ("REAL_MEASURED",
                                                     "Concrete compressive strength"),
        "data/control_servo_system": ("REAL_MEASURED", "class"),
        "data/electrical_grid_stability": ("SYNTHETIC", "stab"),
        "data/energy_combined_cycle_power_plant": ("REAL_MEASURED", "PE"),
        "data/environmental_air_quality": ("REAL_MEASURED", "CO(GT)"),
        "data/materials_superconductivity_tc": ("MIXED", "critical_temp"),
        "data/naval_yacht_hydrodynamics": ("REAL_MEASURED",
                                           "residuary_resistance_per_unit_weight"),
        "physics_generated/physics_beam_euler_bernoulli": ("SYNTHETIC", None),
        "physics_generated/physics_heat_conduction_1d": ("SYNTHETIC", None),
        "physics_generated/physics_kepler_orbits": ("SYNTHETIC", None),
        "physics_generated/physics_projectile_drag": ("SYNTHETIC", None),
        "physics_generated/physics_rlc_circuit": ("SYNTHETIC", None),
    }
    from pathlib import Path as _P
    corpus = _P(__file__).parent / "research_corpus"
    if not corpus.exists():
        print("  (corpus absent - corpus checks skipped)")
    else:
        try:
            import pandas as pd
        except ImportError:
            pd = None
        if pd is None:
            print("  (pandas absent - corpus checks skipped)")
        else:
            hits = 0
            for rel, (want, tgt) in truth.items():
                df = pd.read_csv(corpus / f"{rel}.csv")
                c = {k: df[k].to_numpy() for k in df.columns if df[k].dtype.kind in "if"}
                got = assess(c, target=tgt,
                             provenance={"url": "uci", "license": "see page"}).verdict
                hits += got == want
                if got != want:
                    print(f"    {rel.split('/')[-1]}: wanted {want}, got {got}")
            chk(hits == len(truth),
                f"classified {hits}/{len(truth)} corpus files by documented origin")
            chk(hits >= 13, "and at least 13 of 14 correct")

    print(f"\ndata_authenticity.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(__doc__)
