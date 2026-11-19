#!/usr/bin/env python3
"""
materials_db.py - EngineAI materials data WITH provenance, verified.

A small curated set of engineering materials. Every property carries a value,
unit, and cited source, and the self-test cross-checks the stored elastic
constants against the isotropic relation G = E/(2(1+nu)) (an independent physical
law) and checks densities lie in the accepted range for each material class.
Provenance-tracked reference data with internal-consistency verification - NOT a
substitute for certified material certificates or lot-specific test data, and not
"all materials"; just a traceable, checked core set.

ON WHAT THE G CHECK ACTUALLY PROVES. Where G was taken from a source INDEPENDENTLY
of E and nu, agreement with E/(2(1+nu)) is real evidence: three separately measured
numbers had to be mutually consistent, and a transcription error in any one of them
would show up. Where G was DERIVED from E and nu (because the source tabulates only
those two), the same check is a tautology and proves nothing. Those entries are
marked `"derived": True` and the selftest reports them separately rather than
counting them as verification. Conflating the two would be the most flattering and
least honest thing this module could do.
"""
import argparse, json

# nominal room-temperature properties. density kg/m^3, moduli GPa, strength MPa
MATERIALS = {
    "AISI_1018_steel": {
        "class": "carbon steel",
        "density": {"value": 7870, "unit": "kg/m^3", "source": "ASM Metals Handbook Vol.1"},
        "E":  {"value": 200.0, "unit": "GPa", "source": "ASM Handbook / MatWeb"},
        "nu": {"value": 0.29,  "unit": "-",   "source": "ASM Handbook"},
        "G":  {"value": 77.5,  "unit": "GPa", "source": "ASM Handbook"},
        "yield": {"value": 370, "unit": "MPa", "source": "MatWeb 1018 cold-drawn"},
    },
    "AL_6061_T6": {
        "class": "aluminum alloy",
        "density": {"value": 2700, "unit": "kg/m^3", "source": "ASM / Alcoa datasheet"},
        "E":  {"value": 68.9, "unit": "GPa", "source": "MatWeb 6061-T6"},
        "nu": {"value": 0.33, "unit": "-",   "source": "MatWeb 6061-T6"},
        "G":  {"value": 26.0, "unit": "GPa", "source": "MatWeb 6061-T6"},
        "yield": {"value": 276, "unit": "MPa", "source": "MatWeb 6061-T6"},
    },
    "Ti_6Al_4V": {
        "class": "titanium alloy",
        "density": {"value": 4430, "unit": "kg/m^3", "source": "ASM / Timet datasheet"},
        "E":  {"value": 113.8, "unit": "GPa", "source": "ASM Ti-6Al-4V"},
        "nu": {"value": 0.342, "unit": "-",  "source": "ASM Ti-6Al-4V"},
        "G":  {"value": 44.0, "unit": "GPa", "source": "ASM Ti-6Al-4V"},
        "yield": {"value": 880, "unit": "MPa", "source": "ASM Ti-6Al-4V annealed"},
    },
    "copper_C11000": {
        "class": "copper",
        "density": {"value": 8940, "unit": "kg/m^3", "source": "ASM Copper"},
        "E":  {"value": 117.0, "unit": "GPa", "source": "ASM Copper"},
        "nu": {"value": 0.34, "unit": "-",   "source": "ASM Copper"},
        "G":  {"value": 44.0, "unit": "GPa", "source": "ASM Copper"},
        "yield": {"value": 70, "unit": "MPa", "source": "ASM Copper annealed"},
    },
    # ---- added 2026-08-10. Source tabulates E and nu only, so G is DERIVED and the
    # isotropic check on these rows is a tautology - see the module docstring.
    "ASTM_A36_steel": {
        "class": "carbon steel",
        "density": {"value": 7850, "unit": "kg/m^3", "source": "ASTM A36 / conventional"},
        "E":  {"value": 207.0, "unit": "GPa", "source": "Sonelastic ferrous metals table"},
        "nu": {"value": 0.30, "unit": "-",   "source": "Sonelastic ferrous metals table"},
        "G":  {"value": 79.6, "unit": "GPa", "source": "derived: E/(2(1+nu))", "derived": True},
        "yield": {"value": 250, "unit": "MPa", "source": "ASTM A36 minimum"},
    },
    "stainless_304": {
        "class": "stainless steel",
        "density": {"value": 8000, "unit": "kg/m^3", "source": "ASTM A240 / ASM"},
        "E":  {"value": 193.0, "unit": "GPa", "source": "Sonelastic ferrous metals table"},
        "nu": {"value": 0.30, "unit": "-",   "source": "Sonelastic ferrous metals table"},
        "G":  {"value": 74.2, "unit": "GPa", "source": "derived: E/(2(1+nu))", "derived": True},
        "yield": {"value": 215, "unit": "MPa", "source": "ASTM A240 304 annealed minimum"},
    },
    "stainless_316": {
        "class": "stainless steel",
        "density": {"value": 8000, "unit": "kg/m^3", "source": "ASTM A240 / ASM"},
        "E":  {"value": 193.0, "unit": "GPa", "source": "Sonelastic ferrous metals table"},
        "nu": {"value": 0.30, "unit": "-",   "source": "Sonelastic ferrous metals table"},
        "G":  {"value": 74.2, "unit": "GPa", "source": "derived: E/(2(1+nu))", "derived": True},
        "yield": {"value": 205, "unit": "MPa", "source": "ASTM A240 316 annealed minimum"},
    },
    # Ductile iron grades are self-describing: ASTM A536 "60-40-18" means 60 ksi
    # tensile, 40 ksi yield, 18% elongation. The selftest decodes the NAME and checks
    # the stored strengths against it - a real cross-check, not a tautology.
    "ductile_iron_60_40_18": {
        "class": "cast iron",
        "density": {"value": 7100, "unit": "kg/m^3", "source": "ASM ductile iron"},
        "E":  {"value": 169.0, "unit": "GPa", "source": "Sonelastic ferrous metals table"},
        "nu": {"value": 0.29, "unit": "-",   "source": "Sonelastic ferrous metals table"},
        "G":  {"value": 65.5, "unit": "GPa", "source": "derived: E/(2(1+nu))", "derived": True},
        "yield": {"value": 275.8, "unit": "MPa", "source": "ASTM A536 grade 60-40-18 (40 ksi)"},
        "tensile": {"value": 413.7, "unit": "MPa", "source": "ASTM A536 grade 60-40-18 (60 ksi)"},
    },
    "ductile_iron_80_55_06": {
        "class": "cast iron",
        "density": {"value": 7100, "unit": "kg/m^3", "source": "ASM ductile iron"},
        "E":  {"value": 168.0, "unit": "GPa", "source": "Sonelastic ferrous metals table"},
        "nu": {"value": 0.31, "unit": "-",   "source": "Sonelastic ferrous metals table"},
        "G":  {"value": 64.1, "unit": "GPa", "source": "derived: E/(2(1+nu))", "derived": True},
        "yield": {"value": 379.2, "unit": "MPa", "source": "ASTM A536 grade 80-55-06 (55 ksi)"},
        "tensile": {"value": 551.6, "unit": "MPa", "source": "ASTM A536 grade 80-55-06 (80 ksi)"},
    },
    "grey_iron_G4000": {
        "class": "cast iron",
        "brittle": True,   # negligible plastic range: design on tensile, NOT on yield
        "density": {"value": 7150, "unit": "kg/m^3", "source": "ASM grey iron"},
        "E":  {"value": 124.0, "unit": "GPa", "source": "Sonelastic (G4000 range 110-138)"},
        "nu": {"value": 0.26, "unit": "-",   "source": "Sonelastic ferrous metals table"},
        "G":  {"value": 49.2, "unit": "GPa", "source": "derived: E/(2(1+nu))", "derived": True},
        "tensile": {"value": 276.0, "unit": "MPa", "source": "SAE J431 G4000 (40 ksi)"},
    },
}

KSI_TO_MPA = 6.894757

DENSITY_RANGE = {  # accepted physical ranges by class, kg/m^3
    "carbon steel": (7700, 8050),
    "stainless steel": (7700, 8100),
    "cast iron": (6800, 7400),
    "aluminum alloy": (2600, 2850),
    "titanium alloy": (4400, 4550),
    "copper": (8800, 9000),
}


def isotropic_G(E, nu):
    return E / (2.0 * (1.0 + nu))


def compare(a, b):
    def row(m):
        d = MATERIALS[m]
        r = {k: d[k]["value"] for k in ("density", "E", "nu", "G", "yield")}
        r["class"] = d["class"]
        return r
    return {a: row(a), b: row(b)}


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c
    independent, derived = [], []
    for name, d in MATERIALS.items():
        props = [k for k in d if k not in ("class", "brittle")]
        complete = all("value" in d[k] and "unit" in d[k] and d[k].get("source") for k in props)
        chk(f"{name}: value+unit+source on every property", complete)
        E, nu, G = d["E"]["value"], d["nu"]["value"], d["G"]["value"]
        Gc = isotropic_G(E, nu)
        agree = abs(G - Gc) / Gc < 0.05
        if d["G"].get("derived"):
            derived.append(name)
            # still assert it, to catch a stale hand-typed value that no longer matches
            chk(f"{name}: G consistent with E,nu [DERIVED - tautology, not evidence]",
                agree, f"{G} vs {Gc:.1f} GPa")
        else:
            independent.append(name)
            chk(f"{name}: G == E/(2(1+nu)) [independent source - real cross-check]",
                agree, f"{G} vs {Gc:.1f} GPa")
        lo, hi = DENSITY_RANGE[d["class"]]
        chk(f"{name}: density in class range", lo <= d["density"]["value"] <= hi, f"{d['density']['value']} kg/m^3")
        # a brittle material must not advertise a yield strength for ductile design
        if d.get("brittle"):
            chk(f"{name}: brittle material carries tensile, not yield",
                "tensile" in d and "yield" not in d)

    # ASTM A536 ductile iron grades encode their own strengths: XX-YY-ZZ is XX ksi
    # tensile, YY ksi yield, ZZ% elongation. Decoding the NAME and checking the stored
    # numbers is genuine verification - it would catch a mis-typed strength.
    for name in MATERIALS:
        if not name.startswith("ductile_iron_"):
            continue
        ts, ys, _el = name.replace("ductile_iron_", "").split("_")
        d = MATERIALS[name]
        chk(f"{name}: grade name decodes to the stored strengths",
            abs(d["tensile"]["value"] - int(ts) * KSI_TO_MPA) < 1.0
            and abs(d["yield"]["value"] - int(ys) * KSI_TO_MPA) < 1.0,
            f"{ts} ksi -> {int(ts)*KSI_TO_MPA:.1f} MPa, {ys} ksi -> {int(ys)*KSI_TO_MPA:.1f} MPa")
        chk(f"{name}: tensile exceeds yield", d["tensile"]["value"] > d["yield"]["value"])

    print(f"\n  {len(MATERIALS)} materials: {len(independent)} with an independently sourced G "
          f"(real cross-check), {len(derived)} with G derived from E and nu (tautology).")
    print("materials_db selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI materials data with provenance")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    a = ap.parse_args()
    if a.list:
        for m in MATERIALS:
            print(m, "-", MATERIALS[m]["class"])
        raise SystemExit(0)
    if a.compare:
        print(json.dumps(compare(*a.compare), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
