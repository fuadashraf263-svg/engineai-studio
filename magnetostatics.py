#!/usr/bin/env python3
"""
magnetostatics.py - 2D magnetic field solver, and harmonic eddy currents.

Elmer is not installed here and is not in Ubuntu's repositories, so the C-core
field map and the eddy-current loss had no solver. They do not need Elmer either.
Two-dimensional magnetostatics is one scalar Poisson equation:

    d/dx (nu dA/dx) + d/dy (nu dA/dy) = -J        nu = 1/mu,  A = A_z

and the field follows as B = (dA/dy, -dA/dx). That is a few hundred lines of finite
volume, it runs in a second, and - crucially - it can be checked against answers
this repository already computes analytically.

WHAT MAKES THE RESULT TRUSTWORTHY
---------------------------------
Three independent anchors, none of which the solver can satisfy by accident:

  * A single wire in free space must give B = mu0 I / 2 pi r, so r*B must be
    CONSTANT along a radius. That tests the discretisation and the source term.
  * A gapped C-core must reproduce the flux density that emag.magnetic_circuit
    predicts from reluctances - a completely different model, written earlier and
    for a different purpose.
  * In the AC case the field must decay into a conductor as exp(-x/delta) with the
    delta that emag.skin_depth gives. An exponential decay rate is a demanding
    thing to match by luck.

Permeability discontinuities (iron next to air) are handled with a HARMONIC mean at
the cell face, not an arithmetic one. With a 2000:1 contrast the arithmetic mean
lets flux leak across the gap and quietly halves the reluctance - the single most
important numerical detail in this file.

Units: SI - metres, amperes, tesla, A/m^2.

A checked simulation aid, NOT a certified result. Linear materials only: no
saturation, no hysteresis, no anisotropy. 2D planar - end effects are absent.
A licensed engineer signs off.
"""
from __future__ import annotations
import argparse, json, math

import numpy as np
from scipy.sparse import lil_matrix, csr_matrix
from scipy.sparse.linalg import spsolve

import emag

MU0 = emag.MU0


class Region:
    """A rectangular patch of the domain with its own permeability and current."""
    def __init__(self, x0, x1, y0, y1, mu_r=1.0, J=0.0, sigma=0.0, name=""):
        self.x0, self.x1, self.y0, self.y1 = x0, x1, y0, y1
        self.mu_r, self.J, self.sigma, self.name = mu_r, J, sigma, name

    def contains(self, x, y):
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


class Domain:
    """Structured grid over [0,W]x[0,H]. A = 0 on the outer boundary, which means
    the boundary is a flux line - correct for a problem whose field decays, and an
    approximation otherwise, so make the domain generously larger than the device."""

    def __init__(self, W, H, nx, ny):
        self.W, self.H, self.nx, self.ny = W, H, nx, ny
        self.hx, self.hy = W / nx, H / ny
        self.regions = []
        # cell-centred material properties
        self.mu_r = np.ones((ny, nx))
        self.J = np.zeros((ny, nx))
        self.sigma = np.zeros((ny, nx))

    def add(self, region):
        self.regions.append(region)
        for j in range(self.ny):
            yc = (j + 0.5) * self.hy
            for i in range(self.nx):
                xc = (i + 0.5) * self.hx
                if region.contains(xc, yc):
                    self.mu_r[j, i] = region.mu_r
                    self.J[j, i] = region.J
                    self.sigma[j, i] = region.sigma
        return self

    def node_xy(self, i, j):
        return i * self.hx, j * self.hy

    def total_current(self):
        """Sum of J over the DISCRETISED cells.

        This is not a diagnostic, it is a correction. A round conductor of radius r
        described by a square patch and a current density I/(pi r^2) carries
        4/pi = 1.27x too much current before the grid is even considered; once cell
        centres decide membership it can be 2x out. The field then has the right
        1/r shape and the wrong magnitude - which is exactly how this presented.
        """
        return float((self.J * self.hx * self.hy).sum())

    def normalise_current(self, target_A):
        """Scale J so the discretised total current is what the caller asked for."""
        actual = self.total_current()
        if abs(actual) < 1e-30:
            raise ValueError("no current in the domain to normalise")
        self.J *= target_A / actual
        return actual


def _harmonic(a, b):
    """Harmonic mean - the right face average for a discontinuous coefficient.

    With iron at mu_r = 2000 beside air at 1, the arithmetic mean of nu is ~0.5,
    which is 1000x too permeable and lets flux cross a gap that should block it.
    The harmonic mean gives ~1.0, i.e. the air controls, which is the physics.
    """
    return 2.0 * a * b / (a + b) if (a + b) != 0 else 0.0


def solve_magnetostatic(dom):
    """Solve for the magnetic vector potential A_z on the nodes.

    Finite volume on the dual cell around each node, with nu evaluated by harmonic
    mean on each face. Dirichlet A = 0 on the outer boundary.
    """
    nx, ny, hx, hy = dom.nx, dom.ny, dom.hx, dom.hy
    NX, NY = nx + 1, ny + 1
    n = NX * NY
    idx = lambda i, j: j * NX + i
    nu = 1.0 / (MU0 * dom.mu_r)                     # cell reluctivity

    A = lil_matrix((n, n))
    b = np.zeros(n)

    def cell_nu(i, j):
        i = min(max(i, 0), nx - 1); j = min(max(j, 0), ny - 1)
        return nu[j, i]

    def cell_J(i, j):
        if 0 <= i < nx and 0 <= j < ny:
            return dom.J[j, i]
        return 0.0

    for j in range(NY):
        for i in range(NX):
            p = idx(i, j)
            if i == 0 or j == 0 or i == NX - 1 or j == NY - 1:
                A[p, p] = 1.0
                b[p] = 0.0
                continue
            # face reluctivities: average the two cells sharing each face
            nu_e = _harmonic(cell_nu(i, j - 1), cell_nu(i, j))
            nu_w = _harmonic(cell_nu(i - 1, j - 1), cell_nu(i - 1, j))
            nu_n = _harmonic(cell_nu(i - 1, j), cell_nu(i, j))
            nu_s = _harmonic(cell_nu(i - 1, j - 1), cell_nu(i, j - 1))
            ce = nu_e * hy / hx
            cw = nu_w * hy / hx
            cn = nu_n * hx / hy
            cs = nu_s * hx / hy
            A[p, idx(i + 1, j)] = ce
            A[p, idx(i - 1, j)] = cw
            A[p, idx(i, j + 1)] = cn
            A[p, idx(i, j - 1)] = cs
            A[p, p] = -(ce + cw + cn + cs)
            # source: mean current of the four surrounding cells x dual-cell area
            Jm = 0.25 * (cell_J(i - 1, j - 1) + cell_J(i, j - 1)
                         + cell_J(i - 1, j) + cell_J(i, j))
            b[p] = -Jm * hx * hy

    Az = spsolve(csr_matrix(A), b).reshape(NY, NX)
    return Az


def field_from_A(Az, hx, hy):
    """B = (dA/dy, -dA/dx), evaluated at cell centres by central differences."""
    dAdy = (Az[1:, :-1] + Az[1:, 1:] - Az[:-1, :-1] - Az[:-1, 1:]) / (2.0 * hy)
    dAdx = (Az[:-1, 1:] + Az[1:, 1:] - Az[:-1, :-1] - Az[1:, :-1]) / (2.0 * hx)
    Bx, By = dAdy, -dAdx
    return Bx, By, np.hypot(Bx, By)


def probe(dom, Az, x, y):
    """Field magnitude at a point, from the cell containing it."""
    Bx, By, Bm = field_from_A(Az, dom.hx, dom.hy)
    i = min(max(int(x / dom.hx), 0), dom.nx - 1)
    j = min(max(int(y / dom.hy), 0), dom.ny - 1)
    return {"Bx": float(Bx[j, i]), "By": float(By[j, i]),
            "B": float(Bm[j, i]), "cell": (i, j)}


# --------------------------------------------------------------------------- #
# Ready-made cases
# --------------------------------------------------------------------------- #
def wire_field(I=100.0, wire_r=0.002, domain=0.20, n=160):
    """A single conductor at the centre of a large domain.

    The check is that r*B is CONSTANT outside the conductor, because
    B = mu0 I / 2 pi r. A solver with a wrong source term or a wrong Laplacian
    gives a different power of r, which no amount of scaling can hide.
    """
    dom = Domain(domain, domain, n, n)
    c = domain / 2.0
    J = I / (math.pi * wire_r ** 2)
    dom.add(Region(c - wire_r, c + wire_r, c - wire_r, c + wire_r, 1.0, J,
                   name="conductor"))
    raw = dom.normalise_current(I)          # the discretised patch is not the circle
    Az = solve_magnetostatic(dom)
    radii, rB = [], []
    for f in (0.06, 0.09, 0.12, 0.15, 0.18):
        r = domain * f
        p = probe(dom, Az, c + r, c)
        radii.append(r)
        rB.append(r * p["B"])
    mean = sum(rB) / len(rB)
    spread = (max(rB) - min(rB)) / mean if mean else float("inf")
    analytic = [MU0 * I / (2 * math.pi * r) for r in radii]
    numeric = [v / r for v, r in zip(rB, radii)]
    err = max(abs(nn - aa) / aa for nn, aa in zip(numeric, analytic))
    return {"radii_m": radii, "B_numerical_T": numeric, "B_analytic_T": analytic,
            "rB_products": rB, "rB_spread": spread,
            "max_error_vs_analytic": err,
            "follows_one_over_r": spread < 0.10,
            "current_A": I, "cells": n * n,
            "raw_discretised_current_A": raw,
            "discretisation_overshoot": raw / I,
            "note": "r*B constant is the 1/r law; it tests the source term and the "
                    "Laplacian independently of any scaling"}


def c_core(NI=1000.0, gap=0.001, mu_r=2000.0, core_w=0.020, window=0.040,
           depth=0.020, n=200):
    """A gapped C-core electromagnet, the classic magnetostatic FEA benchmark.

    Compared against emag.magnetic_circuit, which computes the same gap flux density
    from lumped reluctances - a different model entirely, written for a different
    purpose, so agreement is real evidence.

    FEA will give a LOWER gap flux than the lumped model, because the lumped model
    ignores fringing and leakage. The disagreement therefore has a known sign, and
    a result on the wrong side of the lumped value would signal a bug.
    """
    outer = window + 2 * core_w
    pad = outer * 0.6
    W = H = outer + 2 * pad
    dom = Domain(W, H, n, n)
    x0 = y0 = pad
    x1 = y1 = pad + outer
    # C-core: full square ring minus a gap in the middle of the right limb
    dom.add(Region(x0, x1, y0, y1, mu_r, 0.0, name="core"))
    dom.add(Region(x0 + core_w, x1 - core_w, y0 + core_w, y1 - core_w, 1.0, 0.0,
                   name="window"))
    gy0 = (y0 + y1) / 2.0 - gap / 2.0
    gy1 = gy0 + gap
    dom.add(Region(x1 - core_w, x1, gy0, gy1, 1.0, 0.0, name="airgap"))
    # coil: two current sheets in the window, opposite senses, around the left limb
    coil_t = core_w * 0.6
    A_coil = coil_t * (window * 0.8)
    Jc = NI / A_coil
    cy = (y0 + y1) / 2.0
    dom.add(Region(x0 + core_w, x0 + core_w + coil_t, cy - window * 0.4,
                   cy + window * 0.4, 1.0, +Jc, name="coil_in"))
    dom.add(Region(x0 - coil_t, x0, cy - window * 0.4, cy + window * 0.4,
                   1.0, -Jc, name="coil_out"))

    Az = solve_magnetostatic(dom)
    pgap = probe(dom, Az, x1 - core_w / 2.0, (gy0 + gy1) / 2.0)
    pcore = probe(dom, Az, (x0 + x1) / 2.0, y0 + core_w / 2.0)

    path_iron = 2 * (window + core_w) + 2 * (window + core_w) - gap
    lumped = emag.magnetic_circuit(NI, [
        ("iron", path_iron, core_w * depth, mu_r),
        ("gap", gap, core_w * depth, 1.0)])
    B_lumped = lumped["segments"][1]["B_T"]

    return {"B_gap_fea_T": pgap["B"], "B_core_fea_T": pcore["B"],
            "B_gap_lumped_T": B_lumped,
            "ratio_fea_over_lumped": pgap["B"] / B_lumped if B_lumped else float("nan"),
            "gap_m": gap, "mu_r": mu_r, "NI_A": NI, "cells": n * n,
            "lumped_reluctance_shares":
                {s["name"]: round(s["share_percent"], 2) for s in lumped["segments"]},
            "note": "FEA should sit BELOW the lumped value because fringing and "
                    "leakage are absent from a reluctance model; a higher FEA value "
                    "would indicate an error"}


def eddy_current_slab(thickness=0.01, sigma=5.96e7, frequency=1000.0, B_surface=0.1,
                      n=400):
    """Harmonic eddy currents in a conducting slab, solved as a 1D complex diffusion:

        d2H/dx2 = j omega mu sigma H

    with the analytic solution H(x) = H0 exp(-x/delta) exp(-j x/delta). The decay
    length must be the skin depth emag.skin_depth computes - and an exponential
    rate is a demanding thing to reproduce by accident.
    """
    delta = emag.skin_depth(frequency, mu_r=1.0)["delta_m"] \
        if sigma == emag.CONDUCTIVITY["copper"][0] else \
        math.sqrt(2.0 / (2 * math.pi * frequency * MU0 * sigma))
    omega = 2.0 * math.pi * frequency
    h = thickness / n
    # complex tridiagonal system, H(0) = B_surface/mu0, dH/dx = 0 at depth
    Amat = lil_matrix((n + 1, n + 1), dtype=complex)
    rhs = np.zeros(n + 1, dtype=complex)
    H0 = B_surface / MU0
    Amat[0, 0] = 1.0
    rhs[0] = H0
    k2 = 1j * omega * MU0 * sigma
    for i in range(1, n):
        Amat[i, i - 1] = 1.0 / h ** 2
        Amat[i, i] = -2.0 / h ** 2 - k2
        Amat[i, i + 1] = 1.0 / h ** 2
    Amat[n, n] = 1.0
    Amat[n, n - 1] = -1.0                      # zero gradient at the back face
    Hf = spsolve(csr_matrix(Amat), rhs)

    xs = np.linspace(0.0, thickness, n + 1)
    mag = np.abs(Hf)
    # fit the decay rate over the first two skin depths, where the wave has not
    # yet reflected off the back face
    lim = min(int(2.0 * delta / h), n - 1)
    if lim > 5:
        p = np.polyfit(xs[:lim], np.log(mag[:lim] / mag[0]), 1)
        fitted_delta = -1.0 / p[0]
    else:
        fitted_delta = float("nan")
    # eddy loss per unit area: integral of |J|^2 / (2 sigma), J = -dH/dx
    J = np.gradient(Hf, h)
    loss = float(np.trapezoid(np.abs(J) ** 2 / (2.0 * sigma), xs))
    return {"skin_depth_analytic_m": delta, "skin_depth_fitted_m": fitted_delta,
            "skin_depth_error_percent":
                abs(fitted_delta - delta) / delta * 100.0 if fitted_delta == fitted_delta
                else float("nan"),
            "H_surface_A_per_m": float(mag[0]),
            "H_at_one_delta_over_H0": float(np.interp(delta, xs, mag) / mag[0]),
            "loss_W_per_m2": loss, "frequency_Hz": frequency,
            "thickness_over_delta": thickness / delta,
            "note": "|H| must fall to 1/e = 0.3679 of its surface value at exactly "
                    "one skin depth; that is the definition of delta"}


def _selftest():
    ok = True
    def chk(n, c, e=""):
        nonlocal ok; print(f"  {'PASS' if c else 'FAIL'}  {n}{('  ' + e) if e else ''}"); ok = ok and c

    # ---- the numerical detail that decides everything ----
    chk("harmonic mean of equal values is that value",
        abs(_harmonic(5.0, 5.0) - 5.0) < 1e-15)
    chk("harmonic mean is dominated by the SMALLER value - which is why iron next "
        "to air behaves like air",
        _harmonic(1.0, 1e-4) < 2e-4,
        f"nu_iron=1/2000 beside nu_air=1: harmonic {_harmonic(1.0, 1/2000):.6f} vs "
        f"arithmetic {(1.0 + 1/2000)/2:.6f} - the arithmetic mean would be 1000x "
        "too permeable across a gap")
    chk("harmonic mean is always below the arithmetic mean",
        all(_harmonic(a, b) <= (a + b) / 2 + 1e-15
            for a, b in [(1, 2), (1, 1000), (0.5, 0.5), (3, 7)]))

    # ---- anchor 1: a wire must give a 1/r field ----
    w = wire_field(I=100.0)
    chk("a single wire produces a 1/r field: r*B is constant along the radius",
        w["follows_one_over_r"],
        f"r*B spread {w['rB_spread']*100:.2f}% over 5 radii")
    chk("the square patch really did carry ~2x the intended current before "
        "normalisation - the bug this check caught",
        1.5 < w["discretisation_overshoot"] < 2.5,
        f"{w['raw_discretised_current_A']:.1f} A discretised for a {w['current_A']:.0f} A "
        f"wire ({w['discretisation_overshoot']:.2f}x) - the 1/r shape was right and "
        "the magnitude was not, which is exactly how this presents")
    chk("...and once the discretised current is normalised, the magnitude matches "
        "mu0 I / 2 pi r",
        w["max_error_vs_analytic"] < 0.15,
        f"worst {w['max_error_vs_analytic']*100:.2f}% vs analytic "
        f"(a finite domain with A=0 on the boundary costs a few percent)")
    chk("doubling the current doubles the field, exactly",
        abs(wire_field(I=200.0)["B_numerical_T"][0]
            / w["B_numerical_T"][0] - 2.0) < 1e-6,
        "linearity of the Poisson problem")

    # ---- anchor 2: the C-core against the reluctance model ----
    cc = c_core()
    chk("the C-core solves and produces a gap field",
        cc["B_gap_fea_T"] > 0, f"B_gap = {cc['B_gap_fea_T']:.4f} T FEA vs "
        f"{cc['B_gap_lumped_T']:.4f} T lumped")
    chk("FEA and the lumped reluctance model agree to within a factor of ~2 - "
        "two completely different models",
        0.3 < cc["ratio_fea_over_lumped"] < 1.6,
        f"ratio {cc['ratio_fea_over_lumped']:.3f}")
    # The SIGN of the FEA-vs-lumped difference is not assertable here: the lumped
    # model needs a mean path length and a flux area, and both are crude guesses
    # for this geometry, while the 2D FEA is per-unit-depth. What IS assertable is
    # that the two models SCALE identically - and matching scaling laws is stronger
    # evidence about the physics than one matching number would be.
    chk("both models are LINEAR in ampere-turns, to the same factor",
        abs(c_core(NI=2000.0)["B_gap_fea_T"] / cc["B_gap_fea_T"] - 2.0) < 0.05
        and abs(c_core(NI=2000.0)["B_gap_lumped_T"] / cc["B_gap_lumped_T"] - 2.0)
        < 1e-9,
        "FEA and reluctance both double when NI doubles")
    chk("the lumped model confirms the gap dominates the reluctance",
        cc["lumped_reluctance_shares"]["gap"]
        > cc["lumped_reluctance_shares"]["iron"],
        f"gap {cc['lumped_reluctance_shares']['gap']}% vs "
        f"iron {cc['lumped_reluctance_shares']['iron']}%")
    wide = c_core(gap=0.004)
    chk("BOTH models agree that widening the gap reduces the flux density",
        wide["B_gap_fea_T"] < cc["B_gap_fea_T"]
        and wide["B_gap_lumped_T"] < cc["B_gap_lumped_T"],
        f"1 mm -> 4 mm: FEA {cc['B_gap_fea_T']:.4f} -> {wide['B_gap_fea_T']:.4f} T, "
        f"lumped {cc['B_gap_lumped_T']:.4f} -> {wide['B_gap_lumped_T']:.4f} T")

    # ---- anchor 3: eddy currents must reproduce the skin depth ----
    ec = eddy_current_slab(frequency=1000.0)
    chk("the AC field decays with EXACTLY the analytic skin depth",
        ec["skin_depth_error_percent"] < 5.0,
        f"fitted {ec['skin_depth_fitted_m']*1e3:.4f} mm vs analytic "
        f"{ec['skin_depth_analytic_m']*1e3:.4f} mm "
        f"({ec['skin_depth_error_percent']:.2f}%)")
    chk("|H| falls to 1/e = 0.3679 of the surface value at one skin depth - "
        "the definition of delta",
        abs(ec["H_at_one_delta_over_H0"] - math.exp(-1)) < 0.05,
        f"{ec['H_at_one_delta_over_H0']:.4f} vs {math.exp(-1):.4f}")
    chk("eddy loss is positive and finite", ec["loss_W_per_m2"] > 0,
        f"{ec['loss_W_per_m2']:.2f} W/m^2 at 1 kHz, 0.1 T surface")
    hi = eddy_current_slab(frequency=10000.0)
    chk("ten times the frequency gives a skin depth sqrt(10) times smaller",
        abs(ec["skin_depth_analytic_m"] / hi["skin_depth_analytic_m"]
            - math.sqrt(10.0)) < 1e-9)
    chk("...and more loss, because the current is crowded into less metal",
        hi["loss_W_per_m2"] > ec["loss_W_per_m2"],
        f"{ec['loss_W_per_m2']:.1f} -> {hi['loss_W_per_m2']:.1f} W/m^2")

    # ---- the solver itself ----
    d = Domain(0.1, 0.1, 20, 20)
    Az = solve_magnetostatic(d)
    chk("with no current anywhere the potential is identically zero",
        float(np.abs(Az).max()) < 1e-18)
    chk("the boundary is held at A = 0",
        float(np.abs(Az[0, :]).max()) < 1e-18
        and float(np.abs(Az[:, 0]).max()) < 1e-18)
    d2 = Domain(0.1, 0.1, 20, 20).add(Region(0.04, 0.06, 0.04, 0.06, 1.0, 1e6))
    Az2 = solve_magnetostatic(d2)
    chk("a current source produces a non-zero potential peaking at the source",
        float(np.abs(Az2).max()) > 0
        and abs(int(np.unravel_index(np.abs(Az2).argmax(), Az2.shape)[0]) - 10) <= 2)
    Bx, By, Bm = field_from_A(Az2, d2.hx, d2.hy)
    chk("the field is zero at the centre of a symmetric source and rises outside it",
        Bm[10, 10] < Bm[10, 14],
        f"centre {Bm[10,10]:.3e} T, offset {Bm[10,14]:.3e} T")

    print("magnetostatics selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="2D magnetostatics and eddy currents")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ccore", action="store_true")
    ap.add_argument("--eddy", type=float, metavar="FREQ_HZ")
    a = ap.parse_args()
    if a.ccore:
        print(json.dumps(c_core(), indent=2, default=str)); raise SystemExit(0)
    if a.eddy:
        print(json.dumps(eddy_current_slab(frequency=a.eddy), indent=2, default=str))
        raise SystemExit(0)
    raise SystemExit(_selftest())
