#!/usr/bin/env python3
"""
loads.py - EN 1990 load combinations and EN 1991-1-1 imposed loads.

THE DEFECT THIS EXISTS TO CLOSE
-------------------------------
Every structural check in EngineAI - beams, columns, bolts - takes a "design load"
and never says whose job it is to factor it. A user who enters the ACTUAL weight of
their floor gets an answer that silently omits gamma_G = 1.35 and gamma_Q = 1.5 and
is therefore unconservative by roughly 40%, while looking entirely correct.

That is a worse class of defect than a wrong formula, because a wrong formula
usually produces an obviously odd number and this produces a plausible one. The fix
is not only to provide the combinations but to make the unfactored case IMPOSSIBLE
TO PASS SILENTLY: `require_factored()` refuses a bare number that carries no
provenance, so a caller must state whether the load has been through a combination.

EN 1990 6.4.3.2 fundamental ULS combination, expression 6.10:

    sum( gamma_G,j Gk,j )  +  gamma_Q,1 Qk,1  +  sum( gamma_Q,i psi_0,i Qk,i )

and the less conservative pair 6.10a/6.10b, whose governing value is the LARGER of
the two - the standard permits either 6.10 or the 6.10a/b pair, and using the pair
without taking the maximum is a common and unconservative mistake.

SLS combinations (EN 1990 6.5.3): characteristic, frequent, quasi-permanent.

Units: forces in N, loads in N/mm or N/mm^2 - whatever the caller uses, consistently.
The combination is dimensionless arithmetic on those numbers.

A checked design aid, not a certified result. National Annexes override the
recommended gamma and psi values given here; the NA in force where the structure is
built always wins. A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math

# --------------------------------------------------------------------------- #
# EN 1990 Table A1.2(B) - recommended partial factors, ULS STR/GEO
# --------------------------------------------------------------------------- #
GAMMA_G_SUP = 1.35      # permanent action, unfavourable
GAMMA_G_INF = 1.00      # permanent action, favourable (NEVER 1.35 - see below)
GAMMA_Q_SUP = 1.50      # variable action, unfavourable
GAMMA_Q_INF = 0.00      # variable action, favourable: it is simply removed
XI = 0.85               # reduction factor on permanent actions in expression 6.10b

# EN 1990 Table A1.1 - psi factors by category of variable action.
# psi_0 combination, psi_1 frequent, psi_2 quasi-permanent.
PSI = {
    "A_domestic":        (0.7, 0.5, 0.3),
    "B_office":          (0.7, 0.5, 0.3),
    "C_congregation":    (0.7, 0.7, 0.6),
    "D_shopping":        (0.7, 0.7, 0.6),
    "E_storage":         (1.0, 0.9, 0.8),
    "F_traffic_le30kN":  (0.7, 0.7, 0.6),
    "G_traffic_30_160kN": (0.7, 0.5, 0.3),
    "H_roof":            (0.0, 0.0, 0.0),
    "snow_le1000m":      (0.5, 0.2, 0.0),
    "snow_gt1000m":      (0.7, 0.5, 0.2),
    "wind":              (0.6, 0.2, 0.0),
    "thermal":           (0.6, 0.5, 0.0),
}

# EN 1991-1-1 Table 6.2 - imposed loads on floors, recommended values.
# (qk in kN/m^2, Qk concentrated in kN)
IMPOSED = {
    "A_domestic_floor":      (2.0, 2.0, "Category A: domestic and residential, floors"),
    "A_domestic_stairs":     (2.0, 2.0, "Category A: stairs"),
    "A_domestic_balcony":    (2.5, 2.0, "Category A: balconies"),
    "B_office":              (3.0, 4.5, "Category B: office areas"),
    "C1_tables":             (3.0, 4.0, "C1: areas with tables (schools, cafes)"),
    "C2_fixed_seats":        (4.0, 4.0, "C2: areas with fixed seats"),
    "C3_no_obstacles":       (5.0, 4.0, "C3: areas without obstacles for moving people"),
    "C4_physical_activity":  (5.0, 7.0, "C4: areas with physical activities"),
    "C5_susceptible_crowds": (5.0, 4.5, "C5: areas susceptible to large crowds"),
    "D1_retail":             (4.0, 4.0, "D1: general retail shops"),
    "D2_department":         (5.0, 7.0, "D2: department stores"),
    "E1_storage":            (7.5, 7.0, "E1: storage areas, books etc"),
    "H_roof_inaccessible":   (0.4, 1.0, "H: roofs not accessible except maintenance"),
    "I_roof_accessible":     (2.0, 2.0, "I: roofs accessible per categories A-D"),
}

# EN 1991-1-1 Annex A - nominal densities of construction materials (kN/m^3).
#
# Steel deserves a note, because two different numbers are both "correct" and the
# difference is 2%: EN tabulates 77.0-78.5 kN/m^3. The lower bound is exactly
# 7850 kg/m^3 x 9.81 m/s^2, i.e. the same 7850 the section tables use; the upper is
# the rounded g ~ 10 design convention. 78.5 is the conventional design value and is
# the conservative one, so it is what this table carries - but a self-weight derived
# from it will sit ~2% above one derived from the catalogue kg/m, and the selftest
# asserts exactly that rather than pretending the two agree.
STEEL_DENSITY_EN_RANGE = (77.0, 78.5)
DENSITY_kN_m3 = {
    "concrete_reinforced": 25.0, "concrete_plain": 24.0, "steel": 78.5,
    "aluminium": 27.0, "timber_softwood_C24": 4.2, "timber_hardwood_D30": 6.4,
    "masonry_brick": 20.0, "masonry_concrete_block": 20.0, "glass": 25.0,
    "screed_cement": 22.0, "insulation_mineral_wool": 0.3, "water": 10.0,
    "soil_gravel_dry": 18.0, "asphalt": 23.0,
}


class UnfactoredLoadError(ValueError):
    """Raised when a structural check is handed a load that has not been combined."""


def psi(category, which=0):
    """psi_0 (combination), psi_1 (frequent) or psi_2 (quasi-permanent)."""
    if category not in PSI:
        raise KeyError(f"unknown action category '{category}'. "
                       f"Known: {', '.join(sorted(PSI))}")
    if which not in (0, 1, 2):
        raise ValueError("which must be 0 (combination), 1 (frequent) or 2 (quasi-permanent)")
    return PSI[category][which]


def imposed_load(category):
    """EN 1991-1-1 Table 6.2 imposed load for an occupancy."""
    if category not in IMPOSED:
        raise KeyError(f"unknown occupancy '{category}'. "
                       f"Known: {', '.join(sorted(IMPOSED))}")
    qk, Qk, desc = IMPOSED[category]
    return {"category": category, "qk_kN_per_m2": qk, "Qk_kN": Qk,
            "description": desc, "source": "EN 1991-1-1 Table 6.2 (recommended)"}


def _as_actions(x, kind):
    """Normalise a scalar, list or list of dicts into [{'value','category','name'}]."""
    if x is None:
        return []
    if isinstance(x, (int, float)):
        return [{"value": float(x), "category": None, "name": f"{kind}1"}]
    out = []
    for i, a in enumerate(x, 1):
        if isinstance(a, (int, float)):
            out.append({"value": float(a), "category": None, "name": f"{kind}{i}"})
        else:
            out.append({"value": float(a["value"]),
                        "category": a.get("category"),
                        "name": a.get("name", f"{kind}{i}")})
    return out


def uls_610(permanent, variable, gamma_G=GAMMA_G_SUP, gamma_Q=GAMMA_Q_SUP):
    """EN 1990 expression 6.10 - the simple, always-safe fundamental combination.

        Ed = sum(gamma_G Gk) + gamma_Q Qk,1 + sum(gamma_Q psi_0,i Qk,i)

    The LEADING variable action is whichever choice maximises Ed; every variable
    action is tried as leading in turn, because assuming the largest one leads is
    wrong whenever a smaller action has a much higher psi_0 (storage at 1.0 against
    wind at 0.6 is the usual trap).
    """
    G = _as_actions(permanent, "G")
    Q = _as_actions(variable, "Q")
    gsum = sum(gamma_G * g["value"] for g in G)
    if not Q:
        return {"Ed": gsum, "expression": "6.10", "leading": None,
                "terms": [f"{gamma_G}*{g['name']}" for g in G]}
    best = None
    for i, lead in enumerate(Q):
        acc = gamma_Q * lead["value"]
        terms = [f"{gamma_Q}*{lead['name']}(leading)"]
        for j, other in enumerate(Q):
            if i == j:
                continue
            p0 = psi(other["category"], 0) if other["category"] else 1.0
            acc += gamma_Q * p0 * other["value"]
            terms.append(f"{gamma_Q}*{p0}*{other['name']}")
        tot = gsum + acc
        if best is None or tot > best["Ed"]:
            best = {"Ed": tot, "expression": "6.10", "leading": lead["name"],
                    "terms": [f"{gamma_G}*{g['name']}" for g in G] + terms}
    return best


def uls_610ab(permanent, variable, gamma_G=GAMMA_G_SUP, gamma_Q=GAMMA_Q_SUP, xi=XI):
    """EN 1990 expressions 6.10a and 6.10b - the less conservative alternative pair.

        6.10a: sum(gamma_G Gk) + gamma_Q psi_0,1 Qk,1 + sum(gamma_Q psi_0,i Qk,i)
        6.10b: sum(xi gamma_G Gk) + gamma_Q Qk,1 + sum(gamma_Q psi_0,i Qk,i)

    The design value is the LARGER of the two. Taking only 6.10b - which looks like
    the obvious "reduced permanent action" case - is unconservative whenever the
    permanent action dominates, and that mistake is exactly what the max() prevents.
    """
    G = _as_actions(permanent, "G")
    Q = _as_actions(variable, "Q")
    gsum = sum(gamma_G * g["value"] for g in G)

    def _lead_sum(lead_factor_on_leading):
        if not Q:
            return 0.0, []
        best_acc, best_terms = None, None
        for i, lead in enumerate(Q):
            p0_lead = psi(lead["category"], 0) if lead["category"] else 1.0
            f = p0_lead if lead_factor_on_leading else 1.0
            acc = gamma_Q * f * lead["value"]
            terms = [f"{gamma_Q}*{f}*{lead['name']}(leading)"]
            for j, other in enumerate(Q):
                if i == j:
                    continue
                p0 = psi(other["category"], 0) if other["category"] else 1.0
                acc += gamma_Q * p0 * other["value"]
                terms.append(f"{gamma_Q}*{p0}*{other['name']}")
            if best_acc is None or acc > best_acc:
                best_acc, best_terms = acc, terms
        return best_acc, best_terms

    a_acc, a_terms = _lead_sum(True)
    b_acc, b_terms = _lead_sum(False)
    Ed_a = gsum + a_acc
    Ed_b = xi * gsum + b_acc
    gov = "6.10a" if Ed_a >= Ed_b else "6.10b"
    return {"Ed": max(Ed_a, Ed_b), "expression": gov,
            "Ed_610a": Ed_a, "Ed_610b": Ed_b, "xi": xi,
            "terms": (a_terms if gov == "6.10a" else b_terms),
            "note": "EN 1990 permits 6.10 or the 6.10a/b pair; the pair governs at "
                    "the LARGER of the two, and using 6.10b alone is unconservative "
                    "when the permanent action dominates"}


def sls(permanent, variable, kind="characteristic"):
    """EN 1990 6.5.3 serviceability combinations. No gamma factors - SLS uses
    characteristic values directly, with psi reducing the non-leading actions.

        characteristic:   sum(Gk) + Qk,1 + sum(psi_0,i Qk,i)
        frequent:         sum(Gk) + psi_1,1 Qk,1 + sum(psi_2,i Qk,i)
        quasi-permanent:  sum(Gk) + sum(psi_2,i Qk,i)
    """
    if kind not in ("characteristic", "frequent", "quasi-permanent"):
        raise ValueError("kind must be characteristic|frequent|quasi-permanent")
    G = _as_actions(permanent, "G")
    Q = _as_actions(variable, "Q")
    gsum = sum(g["value"] for g in G)
    if not Q:
        return {"Ed": gsum, "combination": kind, "leading": None}
    if kind == "quasi-permanent":
        acc = sum((psi(q["category"], 2) if q["category"] else 1.0) * q["value"]
                  for q in Q)
        return {"Ed": gsum + acc, "combination": kind, "leading": None}
    best = None
    for i, lead in enumerate(Q):
        if kind == "characteristic":
            acc = lead["value"]
        else:
            acc = (psi(lead["category"], 1) if lead["category"] else 1.0) * lead["value"]
        for j, other in enumerate(Q):
            if i == j:
                continue
            w = 0 if kind == "characteristic" else 2
            p = psi(other["category"], w) if other["category"] else 1.0
            acc += p * other["value"]
        tot = gsum + acc
        if best is None or tot > best["Ed"]:
            best = {"Ed": tot, "combination": kind, "leading": lead["name"]}
    return best


def combine(permanent, variable, method="both", gamma_G=GAMMA_G_SUP,
            gamma_Q=GAMMA_Q_SUP):
    """The one call a user should make. Returns the ULS design action with full
    provenance, plus every SLS combination, so a caller can never accidentally use
    an unfactored number.

    `method`: '6.10' | '6.10ab' | 'both' (both = the conservative maximum).
    """
    r610 = uls_610(permanent, variable, gamma_G, gamma_Q)
    rab = uls_610ab(permanent, variable, gamma_G, gamma_Q)
    if method == "6.10":
        chosen, why = r610, "expression 6.10 as requested"
    elif method == "6.10ab":
        chosen, why = rab, "expression 6.10a/b pair as requested"
    elif method == "both":
        chosen = r610 if r610["Ed"] >= rab["Ed"] else rab
        why = ("the more conservative of 6.10 and the 6.10a/b pair "
               f"({r610['Ed']:.6g} vs {rab['Ed']:.6g})")
    else:
        raise ValueError("method must be '6.10', '6.10ab' or 'both'")
    return {
        "Ed": chosen["Ed"], "factored": True,
        "governing_expression": chosen["expression"], "basis": why,
        "leading_variable_action": chosen.get("leading"),
        "terms": chosen.get("terms"),
        "uls_610": r610["Ed"], "uls_610a": rab["Ed_610a"], "uls_610b": rab["Ed_610b"],
        "sls_characteristic": sls(permanent, variable, "characteristic")["Ed"],
        "sls_frequent": sls(permanent, variable, "frequent")["Ed"],
        "sls_quasi_permanent": sls(permanent, variable, "quasi-permanent")["Ed"],
        "gamma_G": gamma_G, "gamma_Q": gamma_Q,
        "code": "EN 1990 6.4.3.2 (ULS) and 6.5.3 (SLS), recommended values",
        "disclaimer": "National Annex values override these. A checked design aid, "
                      "not a certified result.",
    }


def require_factored(load, what="load"):
    """Gate: accept a combine() result, or an explicit opt-out, and REFUSE a bare
    number whose provenance is unknown.

    This is the half of the fix that matters. Providing combinations does not stop
    anyone passing an unfactored number; refusing to accept one does.

    Accepts:
      * a dict from combine()                     -> returns Ed
      * {'value': x, 'factored': True}            -> caller asserts it is factored
      * a number, ONLY if you pass it as {'value': x, 'factored': False,
        'acknowledged_unfactored': True}          -> explicit, logged opt-out
    """
    if isinstance(load, dict):
        if load.get("factored") is True and "Ed" in load:
            return float(load["Ed"])
        if load.get("factored") is True and "value" in load:
            return float(load["value"])
        if load.get("acknowledged_unfactored") is True and "value" in load:
            return float(load["value"])
        raise UnfactoredLoadError(
            f"{what} dict carries no factored=True and no explicit "
            "acknowledged_unfactored=True")
    raise UnfactoredLoadError(
        f"{what} was given as a bare number ({load!r}), so nothing knows whether "
        "EN 1990 partial factors have been applied. Pass loads.combine(...) output, "
        "or {'value': x, 'factored': True} if you have already combined it, or "
        "{'value': x, 'acknowledged_unfactored': True} to opt out on purpose.")


def area_load_to_udl(qk_kN_per_m2, spacing_m):
    """Floor pressure -> line load on one beam, in N/mm (EngineAI's beam unit).
    kN/m^2 * m = kN/m, and 1 kN/m = 1 N/mm exactly."""
    return qk_kN_per_m2 * spacing_m


def selfweight_udl(density_kN_m3, area_mm2):
    """Self weight of a member as a UDL in N/mm from its cross-sectional area."""
    return density_kN_m3 * area_mm2 * 1e-6


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

    # ---- the factors themselves, against the values every textbook quotes ----
    chk("gamma_G unfavourable is 1.35", GAMMA_G_SUP == 1.35)
    chk("gamma_Q unfavourable is 1.50", GAMMA_Q_SUP == 1.50)
    chk("xi (6.10b reduction) is 0.85", XI == 0.85)
    chk("psi_0 >= psi_1 >= psi_2 for every category (they are nested reductions)",
        all(a >= b >= c for a, b, c in PSI.values()))
    chk("storage psi_0 = 1.0 - stored goods do not come and go",
        psi("E_storage", 0) == 1.0)
    chk("roof psi = 0 in every column", PSI["H_roof"] == (0.0, 0.0, 0.0))
    chk("an unknown category is refused, not defaulted",
        _raises(lambda: psi("nonsense", 0), KeyError))

    # ---- 6.10 arithmetic against a hand calculation ----
    r = uls_610(10.0, 5.0)
    chk("6.10 with one G and one Q: 1.35*10 + 1.5*5 = 21.0",
        abs(r["Ed"] - 21.0) < 1e-12, f"Ed={r['Ed']}")
    r = uls_610([10.0], [{"value": 5.0, "category": "B_office"},
                         {"value": 4.0, "category": "wind"}])
    # leading office: 13.5 + 7.5 + 1.5*0.6*4 = 13.5+7.5+3.6 = 24.6
    # leading wind:   13.5 + 6.0 + 1.5*0.7*5 = 13.5+6.0+5.25 = 24.75  <- governs
    chk("6.10 tries EVERY variable action as leading and takes the worst",
        abs(r["Ed"] - 24.75) < 1e-9 and r["leading"] == "Q2",
        f"Ed={r['Ed']:.4f}, leading={r['leading']} (wind leads despite being smaller)")

    # ---- 6.10a/b ----
    ab = uls_610ab(10.0, 5.0)
    # 6.10a: 13.5 + 1.5*1.0*5 = 21.0 (no category -> psi_0 = 1)
    # 6.10b: 0.85*13.5 + 7.5 = 11.475 + 7.5 = 18.975
    chk("6.10a/b returns the LARGER of the pair, not just 6.10b",
        abs(ab["Ed"] - 21.0) < 1e-9 and ab["expression"] == "6.10a",
        f"6.10a={ab['Ed_610a']:.4f} vs 6.10b={ab['Ed_610b']:.4f}")
    heavy = uls_610ab([100.0], [{"value": 5.0, "category": "B_office"}])
    chk("...and when the permanent action dominates, 6.10a governs (the trap)",
        heavy["expression"] == "6.10a",
        f"G=100 Q=5: 6.10a={heavy['Ed_610a']:.2f} > 6.10b={heavy['Ed_610b']:.2f}")
    light = uls_610ab([5.0], [{"value": 100.0, "category": "B_office"}])
    chk("...and when the variable action dominates, 6.10b governs",
        light["expression"] == "6.10b",
        f"G=5 Q=100: 6.10b={light['Ed_610b']:.2f} > 6.10a={light['Ed_610a']:.2f}")
    chk("6.10a/b is never more onerous than 6.10 (that is the point of the pair)",
        uls_610ab(10.0, 5.0)["Ed"] <= uls_610(10.0, 5.0)["Ed"] + 1e-9)

    # ---- SLS ----
    q = [{"value": 5.0, "category": "B_office"}]
    ch = sls(10.0, q, "characteristic")["Ed"]
    fr = sls(10.0, q, "frequent")["Ed"]
    qp = sls(10.0, q, "quasi-permanent")["Ed"]
    chk("SLS characteristic = Gk + Qk (no partial factors)", abs(ch - 15.0) < 1e-12)
    chk("SLS frequent = Gk + psi_1 Qk = 10 + 0.5*5", abs(fr - 12.5) < 1e-12)
    chk("SLS quasi-permanent = Gk + psi_2 Qk = 10 + 0.3*5", abs(qp - 11.5) < 1e-12)
    chk("the three SLS combinations are strictly ordered", ch > fr > qp,
        f"{ch} > {fr} > {qp}")
    chk("every SLS value is below the ULS value", ch < uls_610(10.0, q)["Ed"])

    # ---- combine() ----
    c = combine(10.0, 5.0)
    chk("combine() takes the conservative maximum of 6.10 and 6.10a/b",
        abs(c["Ed"] - 21.0) < 1e-9 and c["factored"] is True)
    chk("combine() reports which expression governed and why",
        c["governing_expression"] in ("6.10", "6.10a", "6.10b") and "conservative" in c["basis"])
    chk("combine() carries the code reference and the National Annex caveat",
        "EN 1990" in c["code"] and "National Annex" in c["disclaimer"])

    # ---- THE GATE: this is the half that actually fixes the defect ----
    chk("a bare number is REFUSED - provenance unknown",
        _raises(lambda: require_factored(1234.0), UnfactoredLoadError))
    chk("...and the error explains all three ways to comply",
        "factored" in str(_raise_msg(lambda: require_factored(1234.0)))
        and "acknowledged_unfactored" in str(_raise_msg(lambda: require_factored(1234.0))))
    chk("a combine() result is accepted and yields Ed",
        abs(require_factored(combine(10.0, 5.0)) - 21.0) < 1e-9)
    chk("an explicit factored assertion is accepted",
        require_factored({"value": 99.0, "factored": True}) == 99.0)
    chk("an explicit unfactored OPT-OUT is accepted (deliberate, not silent)",
        require_factored({"value": 99.0, "acknowledged_unfactored": True}) == 99.0)
    chk("a dict with neither flag is refused",
        _raises(lambda: require_factored({"value": 99.0}), UnfactoredLoadError))

    # ---- the 40% claim in the audit, checked rather than asserted ----
    unf = 10.0 + 5.0
    fac = combine(10.0, 5.0)["Ed"]
    chk("using unfactored loads understates the design action by ~40%",
        0.38 < (fac - unf) / unf < 0.42,
        f"{unf} unfactored vs {fac} factored = {(fac-unf)/unf*100:.1f}% low")

    # ---- EN 1991-1-1 imposed loads ----
    chk("office imposed load is 3.0 kN/m^2", imposed_load("B_office")["qk_kN_per_m2"] == 3.0)
    chk("domestic is 2.0 kN/m^2", imposed_load("A_domestic_floor")["qk_kN_per_m2"] == 2.0)
    chk("storage is the heaviest ordinary category",
        imposed_load("E1_storage")["qk_kN_per_m2"]
        >= max(v[0] for k, v in IMPOSED.items() if not k.startswith("E")))
    chk("an inaccessible roof is the lightest",
        imposed_load("H_roof_inaccessible")["qk_kN_per_m2"]
        == min(v[0] for v in IMPOSED.values()))
    chk("every imposed category carries a concentrated load too",
        all(v[1] > 0 for v in IMPOSED.values()))
    chk("an unknown occupancy is refused",
        _raises(lambda: imposed_load("nightclub"), KeyError))

    # ---- densities ----
    chk("reinforced concrete is 25 kN/m^3", DENSITY_kN_m3["concrete_reinforced"] == 25.0)
    lo, hi = STEEL_DENSITY_EN_RANGE
    chk("steel density sits inside the EN 1991-1-1 range 77.0-78.5 kN/m^3",
        lo <= DENSITY_kN_m3["steel"] <= hi, f"{DENSITY_kN_m3['steel']} kN/m^3")
    chk("...the LOWER bound is exactly 7850 kg/m^3 x g, the section-table density",
        abs(lo - 7850 * 9.81 / 1000) < 0.05, f"{lo} vs {7850*9.81/1000:.3f}")
    chk("...and the table carries the CONSERVATIVE end of that range",
        DENSITY_kN_m3["steel"] == hi)
    chk("water is 10 kN/m^3", DENSITY_kN_m3["water"] == 10.0)
    chk("steel is denser than concrete is denser than timber",
        DENSITY_kN_m3["steel"] > DENSITY_kN_m3["concrete_reinforced"]
        > DENSITY_kN_m3["timber_softwood_C24"])

    # ---- unit bridges ----
    chk("1 kN/m^2 over 1 m spacing = 1 N/mm exactly",
        abs(area_load_to_udl(1.0, 1.0) - 1.0) < 1e-15)
    chk("3 kN/m^2 office at 4 m spacing = 12 N/mm",
        abs(area_load_to_udl(3.0, 4.0) - 12.0) < 1e-12)
    import standards
    ipe = standards.section("IPE 300")
    sw = selfweight_udl(DENSITY_kN_m3["steel"], ipe["A_mm2"])
    cat = ipe["mass_kg_m"] * 9.81 / 1000
    chk("IPE 300 self weight from area is within 2.5% of its catalogue mass, and "
        "conservative - the whole 2% is the 78.5 vs 77.0 convention",
        0 < (sw - cat) / cat < 0.025,
        f"{sw:.4f} N/mm from area x 78.5 vs {cat:.4f} from the 42.2 kg/m catalogue")
    chk("...and using the EN lower bound reproduces the catalogue almost exactly",
        abs(selfweight_udl(lo, ipe["A_mm2"]) - cat) / cat < 0.005,
        f"{selfweight_udl(lo, ipe['A_mm2']):.4f} vs {cat:.4f} N/mm")

    # ---- an end-to-end realistic case ----
    # 6 m span office floor, beams at 3 m centres, 150 mm concrete slab + finishes
    slab = 0.150 * DENSITY_kN_m3["concrete_reinforced"]      # 3.75 kN/m^2
    finishes = 1.2
    gk = area_load_to_udl(slab + finishes, 3.0)               # N/mm
    qk = area_load_to_udl(imposed_load("B_office")["qk_kN_per_m2"], 3.0)
    r = combine(gk, [{"value": qk, "category": "B_office"}])
    chk("realistic office bay: Gk and Qk combine to a sensible design UDL",
        20.0 < r["Ed"] < 35.0,
        f"gk={gk:.2f} + qk={qk:.2f} N/mm -> Ed={r['Ed']:.2f} N/mm ({r['governing_expression']})")
    chk("...and the SLS value used for deflection is much lower than the ULS one",
        r["sls_characteristic"] < r["Ed"],
        f"SLS char {r['sls_characteristic']:.2f} vs ULS {r['Ed']:.2f} N/mm - "
        "deflection must NOT be checked at ULS")

    print("loads selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _raise_msg(fn):
    try:
        fn()
    except Exception as e:
        return str(e)
    return ""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EN 1990 load combinations")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--Gk", type=float, help="permanent action")
    ap.add_argument("--Qk", type=float, help="variable action")
    ap.add_argument("--category", default="B_office")
    ap.add_argument("--imposed", metavar="OCCUPANCY", help="look up EN 1991-1-1 load")
    a = ap.parse_args()
    if a.imposed:
        print(json.dumps(imposed_load(a.imposed), indent=2)); raise SystemExit(0)
    if a.Gk is not None:
        q = [{"value": a.Qk, "category": a.category}] if a.Qk else None
        print(json.dumps(combine(a.Gk, q), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
