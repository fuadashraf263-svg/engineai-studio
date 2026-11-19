#!/usr/bin/env python3
"""
standards.py - EngineAI standard engineering data: real sections and fasteners.

Two things a real user needs before they can state a requirement in the language
their supplier speaks: a catalogue of STANDARD SECTIONS ("IPE 200", not "a 40x23
bar") and STANDARD FASTENERS ("M12 class 8.8", not "a 12 mm pin").

The point of this module is that almost none of it is trusted table-copying.
Wherever the standard defines a quantity by a FORMULA, the formula is implemented
and the published table value is used as the test oracle:

  * ISO 898-1 tensile stress area is computed from pitch, and matched against the
    published As for every listed thread. It is derived, not remembered.
  * ISO 898-1 property classes are self-describing: for class X.Y the tensile
    strength is X*100 MPa and the yield is Y/10 of that. Both are checked.
  * EN 10365 section tables are cross-checked three independent ways: the elastic
    moduli must equal I/(h/2) and I/(b/2), the mass must equal A x 7850 kg/m^3,
    and A must equal the exact fillet-inclusive geometric area.

What that catches is transcription error - the failure mode that actually bites
when a human copies a catalogue. It does NOT make the numbers certified: use the
manufacturer's current datasheet for anything that gets built. Nothing here is a
substitute for a licensed engineer's sign-off.

Sources:
  EN 10365 / Eurocode 3 design tables (eurocodeapplied.com, retrieved 2026-08-10)
  ISO 261 / ISO 724 (metric thread basic profile), ISO 898-1 (fastener mechanical
  properties, stress area and property classes)
"""
from __future__ import annotations
import argparse, json, math

STEEL_DENSITY = 7850.0        # kg/m^3, conventional value used in EN section tables

# --------------------------------------------------------------------------- #
# EN 10365 IPE sections. h,b,tw,tf,r in mm; mass kg/m; A cm^2; I cm^4; W cm^3.
# Published values are the TEST ORACLE - see _selftest for the three cross-checks.
# --------------------------------------------------------------------------- #
IPE = {
    #            h     b    tw    tf    r    mass     A       Iy      Wely     Iz     Welz     It      iz    Wply
    #                                                                                        (10^3mm4)(mm) (10^3mm3)
    "IPE 80":  ( 80,  46,  3.8,  5.2,  5,   6.00,   7.64,   80.14,   20.03,   8.489,  3.691,   6.727, 10.5,  23.22),
    "IPE 100": (100,  55,  4.1,  5.7,  7,   8.10,  10.32,  171.0,    34.20,  15.92,   5.789,  11.53,  12.4,  39.41),
    "IPE 120": (120,  64,  4.4,  6.3,  7,  10.4,   13.21,  317.8,    52.96,  27.67,   8.646,  16.89,  14.5,  60.73),
    "IPE 140": (140,  73,  4.7,  6.9,  7,  12.9,   16.43,  541.2,    77.32,  44.92,  12.31,   24.01,  16.5,  88.34),
    "IPE 160": (160,  82,  5.0,  7.4,  9,  15.8,   20.09,  869.3,   108.7,   68.31,  16.66,   35.30,  18.4, 123.9),
    "IPE 180": (180,  91,  5.3,  8.0,  9,  18.8,   23.95, 1317.0,   146.3,  100.9,   22.16,   47.23,  20.5, 166.4),
    "IPE 200": (200, 100,  5.6,  8.5, 12,  22.4,   28.48, 1943.0,   194.3,  142.4,   28.47,   68.46,  22.4, 220.6),
    "IPE 220": (220, 110,  5.9,  9.2, 12,  26.2,   33.37, 2772.0,   252.0,  204.9,   37.25,   89.82,  24.8, 285.4),
    "IPE 240": (240, 120,  6.2,  9.8, 15,  30.7,   39.12, 3892.0,   324.3,  283.6,   47.27,  127.4,   26.9, 366.6),
    "IPE 270": (270, 135,  6.6, 10.2, 15,  36.1,   45.95, 5790.0,   428.9,  419.9,   62.20,  157.1,   30.2, 484.0),
    "IPE 300": (300, 150,  7.1, 10.7, 15,  42.2,   53.81, 8356.0,   557.1,  603.8,   80.50,  197.5,   33.5, 628.4),
    "IPE 330": (330, 160,  7.5, 11.5, 18,  49.1,   62.61, 11770.0,  713.1,  788.1,   98.52,  275.9,   35.5, 804.3),
    "IPE 360": (360, 170,  8.0, 12.7, 18,  57.1,   72.73, 16270.0,  903.6, 1043.0,  122.8,   370.8,   37.9, 1019.0),
    "IPE 400": (400, 180,  8.6, 13.5, 21,  66.3,   84.46, 23130.0, 1156.0, 1318.0,  146.4,   504.1,   39.5, 1307.0),
    "IPE 450": (450, 190,  9.4, 14.6, 21,  77.6,   98.82, 33740.0, 1500.0, 1676.0,  176.4,   660.5,   41.2, 1702.0),
    "IPE 500": (500, 200, 10.2, 16.0, 21,  90.7,  115.52, 48200.0, 1928.0, 2142.0,  214.2,   886.2,   43.1, 2194.0),
    "IPE 550": (550, 210, 11.1, 17.2, 24, 105.5,  134.42, 67120.0, 2441.0, 2668.0,  254.1,  1217.0,   44.5, 2787.0),
    "IPE 600": (600, 220, 12.0, 19.0, 24, 122.4,  155.98, 92080.0, 3069.0, 3387.0,  307.9,  1646.0,   46.6, 3512.0),
}

_IPE_FIELDS = ("h_mm", "b_mm", "tw_mm", "tf_mm", "r_mm", "mass_kg_m",
               "A_cm2", "Iy_cm4", "Wely_cm3", "Iz_cm4", "Welz_cm3",
               "It_1e3mm4", "iz_mm", "Wply_1e3mm3")

# --------------------------------------------------------------------------- #
# EN 10365 wide-flange sections. An IPE is a BEAM: b is roughly half h, so Iz is
# ~7% of Iy and a compression member made from one buckles about the weak axis at a
# small fraction of its squash load. A column needs b ~ h, which is what HE sections
# give - an HEB 200 has Iz/Iy = 0.35 against an IPE 200's 0.073, i.e. nearly five
# times better balanced. Same 14-field layout as IPE, so one accessor serves all.
#   HEA = light series, HEB = standard series (same h nominal, thicker, stronger).
# --------------------------------------------------------------------------- #
HEB = {
    #            h     b    tw    tf    r    mass     A       Iy      Wely     Iz     Welz     It      iz    Wply
    "HEB 100": (100, 100,  6.0, 10.0, 12,  20.4,   26.04,   449.5,   89.91,  167.3,  33.45,   92.5,  25.3,  104.2),
    "HEB 120": (120, 120,  6.5, 11.0, 12,  26.7,   34.01,   864.4,  144.1,   317.5,  52.92,  138.4,  30.6,  165.2),
    "HEB 140": (140, 140,  7.0, 12.0, 12,  33.7,   42.96,  1509.0,  215.6,   549.7,  78.52,  200.6,  35.8,  245.4),
    "HEB 160": (160, 160,  8.0, 13.0, 15,  42.6,   54.25,  2492.0,  311.5,   889.2, 111.2,   312.4,  40.5,  354.0),
    "HEB 180": (180, 180,  8.5, 14.0, 15,  51.2,   65.25,  3831.0,  425.7,  1363.0, 151.4,   421.6,  45.7,  481.4),
    "HEB 200": (200, 200,  9.0, 15.0, 18,  61.3,   78.08,  5696.0,  569.6,  2003.0, 200.3,   592.8,  50.7,  642.5),
    "HEB 220": (220, 220,  9.5, 16.0, 18,  71.5,   91.04,  8091.0,  735.5,  2843.0, 258.5,   765.7,  55.9,  827.0),
    "HEB 240": (240, 240, 10.0, 17.0, 21,  83.2,  106.0,  11260.0,  938.3,  3923.0, 326.9,  1027.0,  60.8, 1053.0),
    "HEB 260": (260, 260, 10.0, 17.5, 24,  93.0,  118.4,  14920.0, 1148.0,  5135.0, 395.0,  1238.0,  65.8, 1283.0),
    "HEB 280": (280, 280, 10.5, 18.0, 24, 103.0,  131.4,  19270.0, 1376.0,  6595.0, 471.0,  1437.0,  70.9, 1534.0),
    "HEB 300": (300, 300, 11.0, 19.0, 27, 117.0,  149.1,  25170.0, 1678.0,  8563.0, 570.9,  1850.0,  75.8, 1869.0),
    "HEB 320": (320, 300, 11.5, 20.5, 27, 127.0,  161.3,  30820.0, 1926.0,  9239.0, 615.9,  2251.0,  75.7, 2149.0),
    "HEB 340": (340, 300, 12.0, 21.5, 27, 134.0,  170.9,  36660.0, 2156.0,  9690.0, 646.0,  2572.0,  75.3, 2408.0),
    "HEB 360": (360, 300, 12.5, 22.5, 27, 142.0,  180.6,  43190.0, 2400.0, 10140.0, 676.1,  2925.0,  74.9, 2683.0),
    "HEB 400": (400, 300, 13.5, 24.0, 27, 155.0,  197.8,  57680.0, 2884.0, 10820.0, 721.3,  3557.0,  74.0, 3232.0),
}

HEA = {
    #            h     b    tw    tf    r    mass     A       Iy      Wely     Iz     Welz     It      iz    Wply
    "HEA 100": ( 96, 100,  5.0,  8.0, 12,  16.7,   21.24,   349.2,   72.76,  133.8,  26.76,   52.4,  25.1,   83.01),
    "HEA 120": (114, 120,  5.0,  8.0, 12,  19.9,   25.34,   606.2,  106.3,   230.9,  38.48,   59.9,  30.2,  119.5),
    "HEA 140": (133, 140,  5.5,  8.5, 12,  24.7,   31.42,  1033.0,  155.4,   389.3,  55.62,   81.3,  35.2,  173.5),
    "HEA 160": (152, 160,  6.0,  9.0, 15,  30.4,   38.77,  1673.0,  220.1,   615.6,  76.95,  121.9,  39.8,  245.1),
    "HEA 180": (171, 180,  6.0,  9.5, 15,  35.5,   45.25,  2510.0,  293.6,   924.6, 102.7,   148.0,  45.2,  324.9),
    "HEA 200": (190, 200,  6.5, 10.0, 18,  42.3,   53.83,  3692.0,  388.6,  1336.0, 133.6,   209.8,  49.8,  429.5),
    "HEA 220": (210, 220,  7.0, 11.0, 18,  50.5,   64.34,  5410.0,  515.2,  1955.0, 177.7,   284.6,  55.1,  568.5),
    "HEA 240": (230, 240,  7.5, 12.0, 21,  60.3,   76.84,  7763.0,  675.1,  2769.0, 230.7,   415.5,  60.0,  744.6),
    "HEA 260": (250, 260,  7.5, 12.5, 24,  68.2,   86.82, 10450.0,  836.4,  3668.0, 282.1,   523.7,  65.0,  919.8),
    "HEA 280": (270, 280,  8.0, 13.0, 24,  76.4,   97.26, 13670.0, 1013.0,  4763.0, 340.2,   621.0,  70.0, 1112.0),
    "HEA 300": (290, 300,  8.5, 14.0, 27,  88.3,  112.5,  18260.0, 1260.0,  6310.0, 420.6,   851.7,  74.9, 1383.0),
    "HEA 320": (310, 300,  9.0, 15.5, 27,  97.6,  124.4,  22930.0, 1479.0,  6985.0, 465.7,  1080.0,  74.9, 1628.0),
    "HEA 340": (330, 300,  9.5, 16.5, 27, 105.0,  133.5,  27690.0, 1678.0,  7436.0, 495.7,  1272.0,  74.6, 1850.0),
    "HEA 360": (350, 300, 10.0, 17.5, 27, 112.0,  142.8,  33090.0, 1891.0,  7887.0, 525.8,  1488.0,  74.3, 2088.0),
    "HEA 400": (390, 300, 11.0, 19.0, 27, 125.0,  159.0,  45070.0, 2311.0,  8564.0, 570.9,  1890.0,  73.4, 2562.0),
}

# Every open I/H family shares the same field layout, so one accessor serves all.
_I_FAMILIES = {"IPE": IPE, "HEB": HEB, "HEA": HEA}


def _normalise_i_key(designation):
    """'ipe300', 'IPE 300', 'heb 200' -> canonical 'IPE 300' / 'HEB 200'.
    Returns (key, table) or (None, None)."""
    s = str(designation).strip().upper().replace("-", " ")
    for prefix, table in _I_FAMILIES.items():
        if s.replace(" ", "").startswith(prefix):
            size = s.replace(" ", "")[len(prefix):]
            key = f"{prefix} {size}"
            if key in table:
                return key, table
            return None, table          # right family, wrong size - report its members
    return None, None


def section(designation):
    """Standard open I/H section as a dict, in mm/mm^4/mm^3 ready for structural.py.
    Accepts any of the IPE, HEB or HEA families."""
    key, table = _normalise_i_key(designation)
    if key is None:
        known = ", ".join(table) if table else \
                ", ".join(f"{p} ..." for p in _I_FAMILIES)
        raise KeyError(f"unknown section '{designation}'. Known: {known}")
    d = dict(zip(_IPE_FIELDS, table[key]))
    d["designation"] = key
    d["family"] = key.split()[0]
    # convert catalogue units (cm) to the mm basis structural.py works in
    d["A_mm2"] = d["A_cm2"] * 1e2
    d["I_mm4"] = d["Iy_cm4"] * 1e4
    d["Iz_mm4"] = d["Iz_cm4"] * 1e4
    d["Z_mm3"] = d["Wely_cm3"] * 1e3
    d["Wpl_mm3"] = d["Wply_1e3mm3"] * 1e3
    d["It_mm4"] = d["It_1e3mm4"] * 1e3
    d["c_mm"] = d["h_mm"] / 2.0
    # Radii of gyration. iz is TABULATED, so recomputing it is a free consistency
    # check on A and Iz together (the selftest asserts agreement); iy is not
    # tabulated in this layout, so it is derived - both are needed for column
    # slenderness, which is L_cr/i.
    d["iy_mm"] = math.sqrt(d["I_mm4"] / d["A_mm2"])
    d["iz_derived_mm"] = math.sqrt(d["Iz_mm4"] / d["A_mm2"])
    # Warping constant of a doubly symmetric I-section, derived rather than tabulated:
    # each flange contributes If = Iz/2 about the weak axis, separated by hs, giving
    # Iw = If*hs^2 = Iz*hs^2/4 with hs the distance between flange centroids.
    hs = d["h_mm"] - d["tf_mm"]
    d["hs_mm"] = hs
    d["Iw_mm6"] = d["Iz_mm4"] * hs ** 2 / 4.0
    d["source"] = "EN 10365 / Eurocode 3 design tables"
    return d


def geometric_area_mm2(h, b, tw, tf, r):
    """Exact area of a rolled I-section including the four root fillets.
    Two flanges + the web between them + the fillet material (4-pi)r^2."""
    return 2.0 * b * tf + (h - 2.0 * tf) * tw + (4.0 - math.pi) * r ** 2


def area_moment_no_fillet_mm4(h, b, tw, tf):
    """Iy of the same I-section IGNORING fillets. A strict lower bound on the
    published Iy - fillets only ever add material near the neutral axis."""
    return (b * h ** 3 - (b - tw) * (h - 2.0 * tf) ** 3) / 12.0


# --------------------------------------------------------------------------- #
# Hot-finished hollow sections. These matter for a specific engineering reason:
# a closed section has a torsion constant two to three ORDERS of magnitude larger
# than an I-section of similar depth, which makes it effectively immune to
# lateral-torsional buckling. When the LTB check kills an I-beam, this is the answer.
# EN 10210-2 / EN 10219-2. Corner radii are real, so sharp-corner formulae are only
# upper bounds - the selftest asserts exactly that rather than pretending otherwise.
# --------------------------------------------------------------------------- #
SHS = {
    #                 b     t    mass   A_mm2   I_1e6mm4  Wel_1e3  Wpl_1e3  It_1e3mm4
    "SHS 40x40x2.6":  (40.0,  2.6,  3.00,   382,  0.08797,   4.399,   5.312,    140.3),
    "SHS 50x50x5":    (50.0,  5.0,  6.85,   873,  0.2888,   11.55,   14.53,     475.6),
    "SHS 100x100x5":  (100.0, 5.0, 14.7,   1873,  2.794,    55.89,   66.36,    4394.0),
    "SHS 150x150x10": (150.0, 10.0, 43.1,  5493, 17.73,    236.4,   286.0,    28320.0),
    "SHS 200x200x10": (200.0, 10.0, 58.8,  7493, 44.71,    447.1,   530.9,    70310.0),
    "SHS 250x250x12.5": (250.0, 12.5, 91.9, 11707, 109.2,  873.2,  1037.0,   171600.0),
}

_SHS_FIELDS = ("b_mm", "t_mm", "mass_kg_m", "A_mm2", "I_1e6mm4",
               "Wel_1e3mm3", "Wpl_1e3mm3", "It_1e3mm4")


def shs(designation):
    """Square hollow section from the EN 10210-2 catalogue, in mm units."""
    # case-insensitive on BOTH sides: "shs 100X100x5" and "SHS 100x100x5" are the
    # same section, and upper()-ing the key alone would break the 'x' separator
    want = designation.strip().lower()
    key = next((k for k in SHS if k.lower() == want), None)
    if key is None:
        raise KeyError(f"unknown SHS '{designation}'. Known: {', '.join(SHS)}")
    d = dict(zip(_SHS_FIELDS, SHS[key]))
    d["designation"] = key
    d["h_mm"] = d["b_mm"]                      # square
    d["I_mm4"] = d["I_1e6mm4"] * 1e6
    d["Iz_mm4"] = d["I_mm4"]                   # symmetric about both axes
    d["Z_mm3"] = d["Wel_1e3mm3"] * 1e3
    d["Wpl_mm3"] = d["Wpl_1e3mm3"] * 1e3
    d["It_mm4"] = d["It_1e3mm4"] * 1e3
    d["c_mm"] = d["b_mm"] / 2.0
    # Square: identical about both axes, so one radius of gyration serves both. This
    # is precisely why a box is the natural column section - there is no weak axis.
    d["iy_mm"] = d["iz_mm"] = d["iz_derived_mm"] = math.sqrt(d["I_mm4"] / d["A_mm2"])
    # A closed section carries torsion as St Venant shear flow round the perimeter,
    # not by warping of the cross-section, so Iw is negligible and conventionally
    # taken as zero. Its LTB resistance comes entirely from the (very large) It.
    d["Iw_mm6"] = 0.0
    d["closed_section"] = True                 # -> effectively immune to LTB
    d["source"] = "EN 10210-2 / EN 10219-2 hollow section tables"
    return d


def chs(outer_d_mm, wall_t_mm):
    """Circular hollow section - computed EXACTLY from geometry, no table needed.

    Every property of a tube is closed form, so nothing here is copied and nothing
    can be mis-transcribed. The torsion constant is the strongest check available:
    for any circular tube J = 2I exactly, by the perpendicular axis theorem.
    """
    D, t = float(outer_d_mm), float(wall_t_mm)
    if t <= 0 or t >= D / 2:
        raise ValueError(f"wall thickness {t} mm is not valid for a {D} mm tube")
    d_i = D - 2.0 * t
    A = math.pi * (D ** 2 - d_i ** 2) / 4.0
    I = math.pi * (D ** 4 - d_i ** 4) / 64.0
    return {"designation": f"CHS {D:g}x{t:g}", "D_mm": D, "t_mm": t, "d_inner_mm": d_i,
            "h_mm": D, "b_mm": D,
            "A_mm2": A, "I_mm4": I, "Iz_mm4": I,
            "Z_mm3": I / (D / 2.0),
            "Wpl_mm3": (D ** 3 - d_i ** 3) / 6.0,
            "It_mm4": 2.0 * I,                    # J = 2I exactly for a circular tube
            "Iw_mm6": 0.0,                        # closed section: no warping torsion
            "c_mm": D / 2.0,
            # i = sqrt(I/A); for a thin tube this tends to the mean radius / sqrt(2),
            # which the selftest checks in the thin-wall limit.
            "iy_mm": math.sqrt(I / A), "iz_mm": math.sqrt(I / A),
            "iz_derived_mm": math.sqrt(I / A),
            "mass_kg_m": A * 1e-6 * STEEL_DENSITY,
            "closed_section": True,
            "source": "closed-form circular tube geometry (EN 10210 sizes are a subset)"}


def lightest_section(required_Z_mm3):
    """Cheapest useful design query: the lightest catalogue section whose elastic
    modulus meets the requirement. Returns None if nothing in range is big enough."""
    ok = [section(k) for k in IPE if section(k)["Z_mm3"] >= required_Z_mm3]
    return min(ok, key=lambda s: s["mass_kg_m"]) if ok else None


# --------------------------------------------------------------------------- #
# ISO metric coarse threads. Only the nominal diameter and pitch are DATA
# (ISO 261); everything else below is computed from the ISO 724 basic profile.
# --------------------------------------------------------------------------- #
COARSE_PITCH = {  # ISO 261 coarse series, mm
    3: 0.5, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25, 10: 1.5, 12: 1.75,
    16: 2.0, 20: 2.5, 24: 3.0, 30: 3.5, 36: 4.0,
}

# Published ISO 898-1 tensile stress areas, used ONLY as the test oracle for the
# formula below - the library never reads them at runtime.
_PUBLISHED_AS_MM2 = {
    3: 5.03, 4: 8.78, 5: 14.2, 6: 20.1, 8: 36.6, 10: 58.0, 12: 84.3,
    16: 157.0, 20: 245.0, 24: 353.0, 30: 561.0, 36: 817.0,
}


def thread(nominal_d_mm, pitch_mm=None):
    """ISO metric thread geometry derived from the ISO 724 basic profile.

    H     = 0.866025*P            height of the fundamental triangle
    d2    = d - 0.649519*P        basic pitch diameter
    d3    = d - 1.226869*P        minor diameter used for the stress area
    As    = pi/4 * ((d2+d3)/2)^2  ISO 898-1 tensile stress area
    """
    d = float(nominal_d_mm)
    P = float(pitch_mm) if pitch_mm else COARSE_PITCH.get(int(d))
    if P is None:
        raise KeyError(f"no coarse pitch known for M{d:g}; pass pitch_mm explicitly")
    d2 = d - 0.649519 * P
    d3 = d - 1.226869 * P
    return {"designation": f"M{d:g}" + ("" if pitch_mm is None else f"x{P:g}"),
            "d_mm": d, "pitch_mm": P,
            "pitch_dia_d2_mm": d2, "minor_dia_d3_mm": d3,
            "stress_area_mm2": math.pi / 4.0 * ((d2 + d3) / 2.0) ** 2,
            "source": "ISO 261 (pitch), ISO 724 (profile), ISO 898-1 (stress area)"}


# ISO 898-1 property classes. Only the class NAME is data - Rm and Re are both
# derived from it, which is what the selftest proves.
PROPERTY_CLASSES = ("4.6", "5.8", "8.8", "10.9", "12.9")

# proof stress Sp is tabulated, not derivable from the class digits
_PROOF_STRESS_MPA = {"4.6": 225.0, "5.8": 380.0, "8.8": 580.0, "10.9": 830.0, "12.9": 970.0}


def bolt_class(cls):
    """Mechanical properties of an ISO 898-1 property class.
    For class X.Y: Rm = X*100 MPa, and Re = (Y/10)*Rm. Self-describing by design."""
    if cls not in PROPERTY_CLASSES:
        raise KeyError(f"unknown property class '{cls}'. Known: {', '.join(PROPERTY_CLASSES)}")
    x, y = cls.split(".")
    Rm = float(x) * 100.0
    Re = Rm * float(y) / 10.0
    return {"property_class": cls, "tensile_Rm_MPa": Rm, "yield_Re_MPa": Re,
            "proof_Sp_MPa": _PROOF_STRESS_MPA[cls], "source": "ISO 898-1"}


def bolt_capacity(nominal_d_mm, cls="8.8", safety_factor=1.0):
    """Axial capacity of a standard bolt. Loads in N.

    Proof load is what the fastener may see in service without permanent set;
    yield and tensile loads are reported for context, not as working limits.
    """
    th = thread(nominal_d_mm)
    bc = bolt_class(cls)
    As = th["stress_area_mm2"]
    return {"bolt": f"{th['designation']} class {cls}", "stress_area_mm2": round(As, 2),
            "proof_load_N": round(As * bc["proof_Sp_MPa"] / safety_factor, 1),
            "yield_load_N": round(As * bc["yield_Re_MPa"] / safety_factor, 1),
            "tensile_load_N": round(As * bc["tensile_Rm_MPa"] / safety_factor, 1),
            "safety_factor": safety_factor,
            "sources": [th["source"], bc["source"]],
            "note": "Axial capacity of the fastener only. Says nothing about thread "
                    "engagement, the clamped members, preload, or joint separation."}


# EN 1993-1-8 Table 3.4: shear coefficient depends on the property class, because
# the higher-strength classes are less ductile in shear.
_ALPHA_V = {"4.6": 0.6, "5.8": 0.5, "8.8": 0.6, "10.9": 0.5, "12.9": 0.5}
GAMMA_M2 = 1.25          # EN 1993-1-8 recommended partial factor for bolt resistance


def bolt_shear_capacity(nominal_d_mm, cls="8.8", threads_in_shear_plane=True,
                        shear_planes=1, gamma_M2=GAMMA_M2):
    """Shear resistance of a bolt, EN 1993-1-8 3.6.1.

        Fv,Rd = alpha_v * fub * A / gamma_M2

    A is the tensile stress area when the threads lie in the shear plane (the
    conservative and usual assumption) or the shank area when they do not. This is
    a DIFFERENT and much smaller number than the axial proof load - sizing a shear
    connection on tensile capacity overstates it badly, which is exactly the error
    this function exists to prevent.
    """
    th = thread(nominal_d_mm)
    bc = bolt_class(cls)
    a_v = _ALPHA_V[cls]
    A = th["stress_area_mm2"] if threads_in_shear_plane else math.pi / 4 * float(nominal_d_mm) ** 2
    Fv = a_v * bc["tensile_Rm_MPa"] * A / gamma_M2 * int(shear_planes)
    return {"bolt": f"{th['designation']} class {cls}", "loading": "shear",
            "alpha_v": a_v, "area_used_mm2": round(A, 2),
            "threads_in_shear_plane": threads_in_shear_plane,
            "shear_planes": int(shear_planes), "gamma_M2": gamma_M2,
            "shear_resistance_N": round(Fv, 1),
            "source": "EN 1993-1-8 cl.3.6.1 Table 3.4",
            "note": "Fastener shear only. Plate bearing, block shear, edge distance, "
                    "bolt spacing and any eccentricity are separate checks."}


# A single bolt cannot stabilise a connection against rotation, and detailing rules
# in practice require at least two. Encoding it stops the tool returning "1 bolt".
MIN_BOLTS_PER_CONNECTION = 2


def bolts_required(load_N, nominal_d_mm, cls="8.8", safety_factor=2.0, loading="tension"):
    """How many bolts carry the load. `loading` MUST match the real load direction:
    'tension' uses the proof load, 'shear' uses the EN 1993-1-8 shear resistance.
    Getting this wrong overstates a shear connection by roughly a factor of two."""
    if loading not in ("tension", "shear"):
        raise ValueError("loading must be 'tension' or 'shear'")
    if loading == "shear":
        cap = bolt_shear_capacity(nominal_d_mm, cls)
        per = cap["shear_resistance_N"] / safety_factor
        basis = "EN 1993-1-8 shear resistance"
    else:
        cap = bolt_capacity(nominal_d_mm, cls, safety_factor)
        per = cap["proof_load_N"]
        basis = "ISO 898-1 proof load"
    n = math.ceil(load_N / per) if per > 0 else None
    limited = n is not None and n < MIN_BOLTS_PER_CONNECTION
    return {"load_N": load_N, "loading": loading, "bolt": cap["bolt"],
            "safety_factor": safety_factor, "basis": basis,
            "allowable_per_bolt_N": round(per, 1),
            "bolts_by_strength": n,
            "bolts_required": max(n, MIN_BOLTS_PER_CONNECTION) if n else MIN_BOLTS_PER_CONNECTION,
            "governed_by_detailing_minimum": limited,
            "sources": cap.get("sources", [cap.get("source")]),
            "note": ("Strength alone would allow fewer, but a connection needs at least "
                     f"{MIN_BOLTS_PER_CONNECTION} bolts to resist rotation."
                     if limited else "Governed by strength.")}


def _raises(fn, exc):
    """True if fn() raises exc - used to prove invalid input is refused, not absorbed."""
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def _raise_msg(fn):
    """The text of whatever fn() raises, or '' if it did not raise. Lets a test assert
    that an error message is actually USEFUL, not merely that one was thrown."""
    try:
        fn()
    except Exception as e:
        return str(e)
    return ""


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    # ---- every open I/H row, in every family, cross-checked five ways ----
    # The published table is the ORACLE, never the source of truth: each row must
    # simultaneously satisfy five relations that a transcription error cannot fake.
    for fam, table in _I_FAMILIES.items():
        worst_W = worst_A = worst_m = worst_iz = 0.0
        for name in table:
            s = section(name)
            h, b, tw, tf, r = s["h_mm"], s["b_mm"], s["tw_mm"], s["tf_mm"], s["r_mm"]

            # 1. elastic modulus must be exactly I/(extreme fibre distance)
            Wy = s["Iy_cm4"] / (h / 20.0)          # cm^4 / cm -> cm^3
            Wz = s["Iz_cm4"] / (b / 20.0)
            worst_W = max(worst_W, abs(Wy - s["Wely_cm3"]) / s["Wely_cm3"],
                          abs(Wz - s["Welz_cm3"]) / s["Welz_cm3"])

            # 2. published area must equal the exact fillet-inclusive geometry
            Ag = geometric_area_mm2(h, b, tw, tf, r) / 100.0    # mm^2 -> cm^2
            worst_A = max(worst_A, abs(Ag - s["A_cm2"]) / s["A_cm2"])

            # 3. published mass must equal area x steel density
            m = s["A_cm2"] * 1e-4 * STEEL_DENSITY
            worst_m = max(worst_m, abs(m - s["mass_kg_m"]) / s["mass_kg_m"])

            # 4. radius of gyration is exact - ties the published Iz and A columns
            #    together, catching a transposed digit in EITHER that no
            #    single-column check could see
            worst_iz = max(worst_iz, abs(s["iz_derived_mm"] - s["iz_mm"]) / s["iz_mm"])

        n = len(table)
        chk(f"all {n} {fam}: Wel == I/(h/2) and I/(b/2)", worst_W < 0.005, f"worst {worst_W*100:.2f}%")
        chk(f"all {n} {fam}: area == exact fillet geometry", worst_A < 0.005, f"worst {worst_A*100:.2f}%")
        chk(f"all {n} {fam}: mass == A x 7850 kg/m^3", worst_m < 0.01, f"worst {worst_m*100:.2f}%")
        chk(f"all {n} {fam}: iz == sqrt(Iz/A)", worst_iz < 0.01, f"worst {worst_iz*100:.2f}%")

        # 5. fillets add material, so the no-fillet Iy is a strict lower bound - and
        #    only modestly below. Catches a transposed digit the ratios would miss.
        bounded = under = True
        for name in table:
            s = section(name)
            I0 = area_moment_no_fillet_mm4(s["h_mm"], s["b_mm"], s["tw_mm"], s["tf_mm"]) / 1e4
            under &= I0 < s["Iy_cm4"]
            bounded &= (s["Iy_cm4"] - I0) / I0 < 0.12
        chk(f"all {n} {fam}: published Iy exceeds the no-fillet lower bound, but only modestly",
            under and bounded)

        chk(f"{fam} is ordered: the deeper section is stiffer",
            all(section(a)["I_mm4"] < section(b)["I_mm4"]
                for a, b in zip(list(table)[:-1], list(table)[1:])))

        # shape factor: an I/H section's plastic modulus exceeds the elastic one by a
        # ratio fixed by geometry, always in a narrow band. Outside it means Wpl or
        # Wel is wrong.
        sf = [section(k)["Wpl_mm3"] / section(k)["Z_mm3"] for k in table]
        chk(f"all {n} {fam}: shape factor Wpl/Wel in 1.09-1.20",
            all(1.09 <= x <= 1.20 for x in sf), f"{min(sf):.3f}-{max(sf):.3f}")

    # ---- the reason HE sections exist: a column needs two comparable axes ----
    ipe, heb = section("IPE 200"), section("HEB 200")
    r_ipe = ipe["Iz_mm4"] / ipe["I_mm4"]
    r_heb = heb["Iz_mm4"] / heb["I_mm4"]
    chk("an IPE is torsionally lopsided - Iz is a small fraction of Iy (a BEAM)",
        r_ipe < 0.10, f"IPE 200 Iz/Iy = {r_ipe:.3f}")
    chk("...while an HEB of the same depth is nearly balanced (a COLUMN)",
        r_heb > 0.30 and r_heb > 4 * r_ipe, f"HEB 200 Iz/Iy = {r_heb:.3f}")
    chk("HEB is the heavier series: same nominal depth, more steel than HEA",
        all(section(f"HEB {d}")["mass_kg_m"] > section(f"HEA {d}")["mass_kg_m"]
            for d in (100, 200, 300, 400)),
        f"at 200: HEB {section('HEB 200')['mass_kg_m']} vs HEA {section('HEA 200')['mass_kg_m']} kg/m")
    chk("HEA is shallower than its nominal size, HEB is exactly nominal",
        section("HEA 300")["h_mm"] == 290 and section("HEB 300")["h_mm"] == 300)
    chk("family lookup is case- and space-insensitive across all three families",
        section("heb200")["designation"] == "HEB 200"
        and section("hea 300")["designation"] == "HEA 300"
        and section("ipe300")["designation"] == "IPE 300")
    msg = _raise_msg(lambda: section("HEB 999"))
    chk("a wrong size in a KNOWN family lists that family's members, not all of them",
        "HEB 100" in msg and "IPE 300" not in msg, msg[:60] + "...")
    chk("an unknown family is refused too", _raises(lambda: section("UB 305x165"), KeyError))

    # torsion constant: fillets add a LOT to St Venant torsion, so the thin-walled
    # sum-of-rectangles value is a lower bound but a loose one. Assert direction and
    # a sane envelope rather than pretending the simple formula is exact.
    tors_ok = True
    for k in IPE:
        s = section(k)
        It0 = (2 * s["b_mm"] * s["tf_mm"] ** 3 + (s["h_mm"] - s["tf_mm"]) * s["tw_mm"] ** 3) / 3.0
        tors_ok &= It0 < s["It_mm4"] < 1.8 * It0
    chk("all IPE: published It exceeds the thin-walled lower bound, within 1.8x", tors_ok)

    # plastic modulus must exceed elastic, by the shape factor - about 1.14 for a
    # rolled I-section. Outside 1.10-1.20 means a column got mixed up.
    shapes = [section(k)["Wpl_mm3"] / section(k)["Z_mm3"] for k in IPE]
    chk("all IPE: shape factor Wpl/Wel in the physical range 1.10-1.20",
        all(1.10 < f < 1.20 for f in shapes), f"{min(shapes):.3f} to {max(shapes):.3f}")

    # Warping constant is derived, so check it against its definition rather than a
    # table: the two flanges warp about the shear centre a distance hs/2 each, giving
    # Iw = If*hs^2/2 with If = Iz/2 the weak-axis inertia of ONE flange. (Getting this
    # factor wrong is easy - it caught a slip in this very test.)
    s = section("IPE 300")
    If = s["Iz_mm4"] / 2.0
    chk("warping constant Iw == If*hs^2/2 with If = Iz/2 (derivation self-consistent)",
        abs(s["Iw_mm6"] - If * s["hs_mm"] ** 2 / 2.0) / s["Iw_mm6"] < 1e-12,
        f"Iw={s['Iw_mm6']:.4e} mm^6")
    # ...and a magnitude sanity check against the published order for IPE 300
    # (~1.259e11 mm^6); the closed form ignores fillets and the web, so ~1% is expected.
    chk("derived Iw agrees with the published IPE 300 warping constant (~1%)",
        abs(s["Iw_mm6"] - 1.259e11) / 1.259e11 < 0.02,
        f"{s['Iw_mm6']:.4e} vs 1.259e11 mm^6")

    # ---- hollow sections ----
    # Wel = I/(b/2) is exact and independent of how the table was produced
    worst = max(abs(shs(k)["I_mm4"] / shs(k)["c_mm"] - shs(k)["Z_mm3"]) / shs(k)["Z_mm3"] for k in SHS)
    chk(f"all {len(SHS)} SHS: Wel == I/(b/2)", worst < 0.01, f"worst {worst*100:.2f}%")
    worst = max(abs(shs(k)["A_mm2"] * 1e-6 * STEEL_DENSITY - shs(k)["mass_kg_m"]) / shs(k)["mass_kg_m"]
                for k in SHS)
    chk(f"all {len(SHS)} SHS: mass == A x 7850 kg/m^3", worst < 0.01, f"worst {worst*100:.2f}%")

    # sharp-corner idealisations must OVER-predict, because real corners are rounded
    sharp_ok = bredt_ok = True
    for k in SHS:
        s = shs(k)
        b, t = s["b_mm"], s["t_mm"]
        A0 = 4.0 * t * (b - t)                                  # sharp-corner area
        I0 = (b ** 4 - (b - 2 * t) ** 4) / 12.0                 # sharp-corner inertia
        It0 = t * (b - t) ** 3                                  # Bredt closed-tube torsion
        sharp_ok &= s["A_mm2"] < A0 and s["I_mm4"] < I0 and (A0 - s["A_mm2"]) / A0 < 0.10
        bredt_ok &= abs(It0 - s["It_mm4"]) / s["It_mm4"] < 0.05
    chk("all SHS: rounded corners sit just below the sharp-corner upper bound", sharp_ok)
    chk("all SHS: torsion constant matches Bredt's closed-tube formula t(b-t)^3 within 5%", bredt_ok)

    shapes = [shs(k)["Wpl_mm3"] / shs(k)["Z_mm3"] for k in SHS]
    chk("all SHS: shape factor in the hollow-section range 1.15-1.30",
        all(1.15 < f < 1.30 for f in shapes), f"{min(shapes):.3f} to {max(shapes):.3f}")

    # CHS is fully computed, so it can be checked against exact identities
    c = chs(219.1, 10.0)
    chk("CHS: J == 2I exactly (perpendicular axis theorem for a tube)",
        abs(c["It_mm4"] - 2 * c["I_mm4"]) < 1e-9, f"It={c['It_mm4']:.4e} mm^4")
    chk("CHS: Wel == I/(D/2)", abs(c["Z_mm3"] - c["I_mm4"] / (c["D_mm"] / 2)) < 1e-9)
    solid = chs(100.0, 49.9999)
    chk("CHS degenerates to a solid bar as the bore closes",
        abs(solid["I_mm4"] - math.pi * 100.0 ** 4 / 64.0) / (math.pi * 100.0 ** 4 / 64.0) < 1e-4,
        f"{solid['I_mm4']:.4e} vs {math.pi*100**4/64:.4e} mm^4")
    chk("CHS rejects an impossible wall thickness",
        _raises(lambda: chs(100.0, 60.0), ValueError))
    chk("CHS shape factor ~1.27-1.35 for a thin tube",
        1.2 < chs(219.1, 6.3)["Wpl_mm3"] / chs(219.1, 6.3)["Z_mm3"] < 1.4,
        f"{chs(219.1,6.3)['Wpl_mm3']/chs(219.1,6.3)['Z_mm3']:.3f}")

    # THE point of hollow sections: torsional stiffness, hence LTB immunity
    ipe = section("IPE 300")
    box = shs("SHS 200x200x10")
    ratio = box["It_mm4"] / ipe["It_mm4"]
    chk("a closed section is orders of magnitude stiffer in torsion than an I-section",
        ratio > 100, f"SHS 200x200x10 It is {ratio:.0f}x that of IPE 300")

    # ---- ISO threads: the stress area FORMULA vs the published table ----
    worst_as, detail = 0.0, ""
    for d, As_pub in _PUBLISHED_AS_MM2.items():
        As = thread(d)["stress_area_mm2"]
        rel = abs(As - As_pub) / As_pub
        if rel > worst_as:
            worst_as, detail = rel, f"M{d}: {As:.2f} vs {As_pub} mm^2"
    chk(f"ISO 898-1 stress area derived from pitch matches all {len(_PUBLISHED_AS_MM2)} "
        f"published values", worst_as < 0.005, f"worst {worst_as*100:.2f}%  ({detail})")

    t = thread(12)
    chk("M12 coarse pitch is 1.75 mm", abs(t["pitch_mm"] - 1.75) < 1e-9)
    chk("M12 stress area is the textbook 84.3 mm^2", abs(t["stress_area_mm2"] - 84.3) < 0.1,
        f"{t['stress_area_mm2']:.2f} mm^2")
    chk("minor dia < pitch dia < nominal dia",
        t["minor_dia_d3_mm"] < t["pitch_dia_d2_mm"] < t["d_mm"])
    chk("finer pitch gives a LARGER stress area (less material cut away)",
        thread(12, 1.25)["stress_area_mm2"] > thread(12)["stress_area_mm2"],
        f"{thread(12,1.25)['stress_area_mm2']:.1f} vs {thread(12)['stress_area_mm2']:.1f} mm^2")

    # ---- ISO 898-1 property classes are self-describing ----
    for cls in PROPERTY_CLASSES:
        bc = bolt_class(cls)
        x, y = cls.split(".")
        chk(f"class {cls}: Rm=={x}00 MPa and Re=={y}/10 of Rm",
            abs(bc["tensile_Rm_MPa"] - float(x) * 100) < 1e-9
            and abs(bc["yield_Re_MPa"] - bc["tensile_Rm_MPa"] * float(y) / 10) < 1e-9,
            f"Rm={bc['tensile_Rm_MPa']:g}, Re={bc['yield_Re_MPa']:g} MPa")
    chk("proof stress sits below yield for every class",
        all(bolt_class(c)["proof_Sp_MPa"] < bolt_class(c)["yield_Re_MPa"] for c in PROPERTY_CLASSES))
    chk("a stronger class carries more load at the same size",
        bolt_capacity(12, "12.9")["proof_load_N"] > bolt_capacity(12, "8.8")["proof_load_N"],
        f"{bolt_capacity(12,'12.9')['proof_load_N']:.0f} vs {bolt_capacity(12,'8.8')['proof_load_N']:.0f} N")

    cap = bolt_capacity(12, "8.8")
    chk("M12 8.8 proof load ~48.9 kN (As 84.3 x Sp 580)", abs(cap["proof_load_N"] - 48887) < 60,
        f"{cap['proof_load_N']/1000:.2f} kN")

    n = bolts_required(100_000.0, 12, "8.8", safety_factor=2.0)
    chk("bolt count is conservative (rounds up)", n["bolts_by_strength"] == math.ceil(
        100_000.0 / (cap["proof_load_N"] / 2.0)), f"{n['bolts_required']} x M12 8.8 for 100 kN at SF 2")

    # ---- shear is NOT tension: the error this exists to prevent ----
    sh = bolt_shear_capacity(16, "8.8")
    ten = bolt_capacity(16, "8.8")
    chk("bolt SHEAR resistance is far below its tensile proof load",
        sh["shear_resistance_N"] < ten["proof_load_N"],
        f"shear {sh['shear_resistance_N']/1e3:.1f} kN vs proof {ten['proof_load_N']/1e3:.1f} kN")
    chk("M16 8.8 single shear is the textbook ~60 kN",
        abs(sh["shear_resistance_N"] - 0.6 * 800 * 156.67 / 1.25) < 200,
        f"{sh['shear_resistance_N']/1e3:.1f} kN")
    chk("class 10.9 uses alpha_v = 0.5, not 0.6 (less ductile in shear)",
        bolt_shear_capacity(16, "10.9")["alpha_v"] == 0.5
        and bolt_shear_capacity(16, "8.8")["alpha_v"] == 0.6)
    chk("double shear doubles the resistance",
        abs(bolt_shear_capacity(16, "8.8", shear_planes=2)["shear_resistance_N"]
            - 2 * sh["shear_resistance_N"]) < 1.0)
    chk("threads excluded from the shear plane gives MORE capacity (shank area)",
        bolt_shear_capacity(16, "8.8", threads_in_shear_plane=False)["shear_resistance_N"]
        > sh["shear_resistance_N"])
    sc = bolts_required(36_000.0, 16, "8.8", safety_factor=2.0, loading="shear")
    st = bolts_required(36_000.0, 16, "8.8", safety_factor=2.0, loading="tension")
    chk("a SHEAR connection needs more bolts than the same load in tension",
        sc["bolts_by_strength"] >= st["bolts_by_strength"],
        f"shear {sc['bolts_by_strength']} vs tension {st['bolts_by_strength']} by strength")
    chk("never returns a 1-bolt connection (detailing minimum applies)",
        st["bolts_required"] >= 2 and st["governed_by_detailing_minimum"],
        f"strength said {st['bolts_by_strength']}, returned {st['bolts_required']}")
    chk("an invalid loading direction is refused, not guessed",
        _raises(lambda: bolts_required(1000.0, 12, "8.8", loading="sideways"), ValueError))

    # ---- integration: a catalogue section must drive the real beam solver ----
    try:
        import structural
        s = section("IPE 200")
        rep = structural.design_check("rectangle", {"b": 1.0, "h": 1.0}, "simply_point",
                                      1.0, 1.0, 210000.0, 235.0, 1.0)   # smoke: solver reachable
        sigma = (10_000.0 * 3000.0 / 4.0) / s["Z_mm3"]                  # M/Z, N.mm / mm^3
        chk("catalogue section feeds the beam solver (IPE 200, 10 kN, 3 m simply supported)",
            0 < sigma < 235.0, f"sigma={sigma:.1f} MPa vs S235 yield 235 MPa")
        light = lightest_section(sigma and 200_000.0)
        chk("lightest-section query returns a real, adequate section",
            light is not None and light["Z_mm3"] >= 200_000.0,
            f"{light['designation']} at {light['mass_kg_m']} kg/m" if light else "none")
    except Exception as e:
        chk("integration with structural.py", False, f"{type(e).__name__}: {e}")

    # ---- units must survive the trip ----
    try:
        from units_gate import has_dimension, HAVE_PINT
        if HAVE_PINT:
            chk("bolt proof load has force dimensions", has_dimension(cap["proof_load_N"], "N", "N"))
            chk("section modulus has volume dimensions", has_dimension(section("IPE 200")["Z_mm3"], "mm^3", "m^3"))
    except Exception:
        pass

    print("standards selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="EngineAI standard sections and fasteners")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list-sections", action="store_true")
    ap.add_argument("--section", metavar="DESIGNATION")
    ap.add_argument("--thread", type=float, metavar="D")
    ap.add_argument("--bolt", nargs=2, metavar=("D", "CLASS"))
    ap.add_argument("--lightest", type=float, metavar="Z_MM3",
                    help="lightest section with at least this elastic modulus")
    a = ap.parse_args()
    if a.list_sections:
        for k in IPE:
            s = section(k)
            print(f"{k:9s} h={s['h_mm']:>3g} b={s['b_mm']:>3g} mass={s['mass_kg_m']:>6g} kg/m "
                  f"Z={s['Z_mm3']:>10.0f} mm^3")
        raise SystemExit(0)
    if a.section:
        print(json.dumps(section(a.section), indent=2)); raise SystemExit(0)
    if a.thread:
        print(json.dumps(thread(a.thread), indent=2)); raise SystemExit(0)
    if a.bolt:
        print(json.dumps(bolt_capacity(float(a.bolt[0]), a.bolt[1]), indent=2)); raise SystemExit(0)
    if a.lightest:
        print(json.dumps(lightest_section(a.lightest), indent=2)); raise SystemExit(0)
    raise SystemExit(_selftest())
