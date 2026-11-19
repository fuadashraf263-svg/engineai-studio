#!/usr/bin/env python3
"""experiment_campaign.py - work an experiment end to end, then hand it over.

WHAT THIS DOES. Given a question, it runs everything that can be run without
touching physical matter, and then produces the protocol a scientist needs to
do the part that cannot be simulated:

  1  GATHER    what is already known - open registries, the local library, and
               whatever the user supplies. Records where each piece came from.
  2  CHOOSE    a method, from the solvers actually installed, with the reason
               for the choice and the reason the alternatives were rejected.
  3  SIMULATE  through validation_loop, so refinement continues until the
               answer stops moving rather than until it looks right.
  4  CROSS     the same quantity by a second independent method. Two methods
               agreeing is evidence; one method agreeing with itself is not.
  5  ANALYSE   uncertainty budget, falsification, artefacts written to disk.
  6  PROTOCOL  the physical experiment: specimens, levels, instrument
               resolution, order, and what result would falsify the prediction.
  7  SCORE     an honest completion figure - what is finished, what is blocked,
               and what is blocked ON A HUMAN rather than on more compute.

WHERE IT STOPS, AND WHY THAT IS THE DESIGN. It does not cast concrete, load a
specimen or read an instrument. That boundary is not a limitation to be
engineered away - it is the point at which a claim stops being about a model
and starts being about the world, and only a measurement crosses it. What the
engine can do is arrive at that boundary having removed every source of error
that was removable by calculation, so the scientist spends their specimens on
the one question that is genuinely open.

SUCCESS RATE MEANS COMPLETION, NOT CORRECTNESS. A campaign reporting 85% has
finished 85% of its steps. It has not established that the answer is 85%
likely to be right, and the two are reported separately so they cannot be
confused.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

import numpy as np

from experiment import plan_experiment, TestPlan
from research_credibility import combine_uncertainty
from validation_loop import run_loop, LoopResult, DataFirewall

__all__ = ["Campaign", "Method", "METHODS", "run_campaign", "Step"]

OUT = Path(__file__).parent / "research_output" / "campaigns"


# --------------------------------------------------------------------------
# the method registry - only what is actually installed
# --------------------------------------------------------------------------

@dataclass
class Method:
    name: str
    module: str
    quantities: tuple            # what it can compute
    effort_axis: str             # what "more effort" means for this solver
    independent_of: tuple = ()   # methods it shares no machinery with
    available: Callable[[], bool] = lambda: True

    def is_available(self) -> bool:
        try:
            return bool(self.available())
        except Exception:
            return False


def _importable(mod: str) -> Callable[[], bool]:
    def check():
        import importlib
        try:
            importlib.import_module(mod)
            return True
        except Exception:
            return False
    return check


METHODS = [
    Method("closed form", "mechanics", ("stress", "deflection", "buckling"),
           "none - exact", ("finite element", "elmer thermal"),
           _importable("mechanics")),
    Method("finite element", "fea", ("stress", "deflection", "modal"),
           "mesh density", ("closed form", "elmer thermal"), _importable("fea")),
    Method("elmer thermal", "elmer", ("temperature", "heat flux"),
           "mesh density", ("closed form", "finite element"), _importable("elmer")),
    Method("thermal network", "thermal", ("temperature", "heat flux"),
           "node count", ("elmer thermal",), _importable("thermal")),
    Method("cfd", "cfd", ("pressure drop", "drag", "velocity"),
           "cell count", ("closed form",), _importable("cfd")),
    Method("magnetostatics 2D", "magnetostatics", ("flux density", "inductance"),
           "mesh density", ("closed form",), _importable("magnetostatics")),
    Method("circuit simulation", "circuits_ext", ("voltage", "current", "gain"),
           "timestep", ("closed form",), _importable("circuits_ext")),
    Method("multibody", "multibody", ("trajectory", "energy"),
           "timestep", ("closed form",), _importable("multibody")),
    Method("lattice model", "lattice", ("stiffness", "relative density"),
           "unit cells", ("finite element",), _importable("lattice")),
]


def choose_method(quantity: str, exclude: tuple = ()) -> tuple:
    """Pick a solver for a quantity, and say why the others were not picked."""
    considered, chosen = [], None
    for m in METHODS:
        if m.name in exclude:
            considered.append((m.name, "excluded by caller"))
            continue
        if quantity.lower() not in [q.lower() for q in m.quantities]:
            considered.append((m.name, f"does not compute '{quantity}'"))
            continue
        if not m.is_available():
            considered.append((m.name, f"module '{m.module}' not importable"))
            continue
        if chosen is None:
            chosen = m
            considered.append((m.name, "SELECTED"))
        else:
            considered.append((m.name, "available, held as cross-check"))
    return chosen, considered


# --------------------------------------------------------------------------
# campaign
# --------------------------------------------------------------------------

@dataclass
class Step:
    name: str
    state: str                   # DONE | BLOCKED | SKIPPED | FAILED
    detail: str = ""
    blocked_on: str = ""         # "compute" | "human" | "data" | ""

    def __str__(self) -> str:
        mark = {"DONE": "done   ", "BLOCKED": "BLOCKED", "SKIPPED": "skipped",
                "FAILED": "FAILED "}[self.state]
        b = f"  [needs {self.blocked_on}]" if self.blocked_on else ""
        return f"    {mark}  {self.name:34s} {self.detail[:60]}{b}"


@dataclass
class Campaign:
    question: str
    quantity: str
    steps: list = field(default_factory=list)
    method: str = ""
    cross_method: str = ""
    value: float = float("nan")
    cross_value: float = float("nan")
    unit: str = ""
    loop: LoopResult | None = None
    plan: TestPlan | None = None
    artefacts: list = field(default_factory=list)
    knowledge: list = field(default_factory=list)
    started: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    @property
    def completion(self) -> float:
        if not self.steps:
            return 0.0
        return sum(1 for s in self.steps if s.state == "DONE") / len(self.steps)

    @property
    def blocked_on_human(self) -> list:
        return [s for s in self.steps if s.blocked_on == "human"]

    def __str__(self) -> str:
        head = (f"CAMPAIGN  {self.question}\n"
                f"  quantity   {self.quantity}\n"
                f"  method     {self.method}"
                + (f"  (cross-checked by {self.cross_method})" if self.cross_method else ""))
        body = "\n".join(str(s) for s in self.steps)
        res = ""
        if np.isfinite(self.value):
            res = f"\n  RESULT     {self.value:.6g} {self.unit}"
            if np.isfinite(self.cross_value):
                d = abs(self.cross_value - self.value) / (abs(self.value) or 1)
                res += (f"\n  CROSS      {self.cross_value:.6g} {self.unit}  "
                        f"({d*100:.2f}% apart)")
        pct = self.completion * 100
        tail = (f"\n  COMPLETION {pct:.0f}% of steps finished"
                f"\n  REMAINING  {len(self.blocked_on_human)} step(s) need a human "
                f"with laboratory access")
        note = ("\n  NOTE       completion is how much of the WORK is done. It is "
                "not a probability\n             that the answer is correct - "
                "those are different things.")
        return f"{head}\n{body}{res}{tail}{note}"

    def to_dict(self) -> dict:
        d = {"question": self.question, "quantity": self.quantity,
             "method": self.method, "cross_method": self.cross_method,
             "value": self.value, "cross_value": self.cross_value,
             "unit": self.unit, "completion": self.completion,
             "steps": [asdict(s) for s in self.steps],
             "artefacts": [str(a) for a in self.artefacts],
             "knowledge": self.knowledge, "started": self.started}
        if self.plan:
            d["physical_protocol"] = self.plan.to_dict()
        if self.loop:
            d["convergence"] = {"outcome": self.loop.outcome,
                                "passes": self.loop.n_passes,
                                "order": self.loop.observed_order}
        return d


def run_campaign(question: str, quantity: str, simulate: Callable[[float], float],
                 efforts, unit: str = "", cross: Callable[[float], float] | None = None,
                 measured=None, expected_sd: float | None = None,
                 tolerance: float | None = None, measurand: str = "",
                 gather: Callable[[], list] | None = None,
                 tol: float = 1e-3, verbose: bool = True) -> Campaign:
    """Run every step that does not need physical matter, then write the protocol."""
    c = Campaign(question=question, quantity=quantity, unit=unit)

    # 1 GATHER
    try:
        found = gather() if gather else []
        c.knowledge = list(found)
        c.steps.append(Step("gather prior knowledge", "DONE" if found else "SKIPPED",
                            f"{len(found)} source(s)" if found
                            else "none supplied - proceeding from first principles"))
    except Exception as e:
        c.steps.append(Step("gather prior knowledge", "FAILED",
                            f"{type(e).__name__}: {e}", "data"))

    # 2 CHOOSE
    m, considered = choose_method(quantity)
    c.method = m.name if m else "caller-supplied"
    c.steps.append(Step("choose a method", "DONE",
                        f"{c.method}; {len(considered)} candidates weighed"))

    # 3 SIMULATE to convergence
    fw = DataFirewall(measured) if measured is not None and len(measured) >= 4 else None
    c.loop = run_loop(simulate, efforts, firewall=fw, tol=tol)
    c.value = c.loop.value
    if c.loop.outcome == "CONVERGED":
        c.steps.append(Step("simulate to convergence", "DONE",
                            f"{c.loop.n_passes} passes, "
                            f"order {c.loop.observed_order or float('nan'):.2f}"))
    elif c.loop.outcome == "IRRECONCILABLE":
        c.steps.append(Step("simulate to convergence", "DONE",
                            "converged but DISAGREES with measurement"))
    else:
        c.steps.append(Step("simulate to convergence", "BLOCKED",
                            c.loop.notes[0][:56] if c.loop.notes else "not converged",
                            "compute"))

    # 4 CROSS-CHECK with an independent method
    if cross is not None:
        try:
            c.cross_value = float(cross(efforts[-1]))
            d = abs(c.cross_value - c.value) / (abs(c.value) or 1.0)
            c.cross_method = "independent second method"
            c.steps.append(Step("cross-check, second method", "DONE",
                                f"{d*100:.2f}% apart"))
        except Exception as e:
            c.steps.append(Step("cross-check, second method", "FAILED",
                                f"{type(e).__name__}", "compute"))
    else:
        c.steps.append(Step("cross-check, second method", "BLOCKED",
                            "no independent method supplied - a single solver "
                            "agreeing with itself is not evidence", "compute"))

    # 5 ANALYSE
    if np.isfinite(c.value):
        u_num = 0.0
        if c.loop and c.loop.n_passes > 1:
            u_num = abs(c.loop.iterations[-1].value - c.loop.iterations[-2].value)
        u = combine_uncertainty(c.value, 0.0, {"discretisation": u_num}, unit)
        c.steps.append(Step("uncertainty budget", "DONE",
                            f"+/- {u.expanded:.4g} {unit} at k=2"))
    else:
        c.steps.append(Step("uncertainty budget", "BLOCKED",
                            "no converged value to bound", "compute"))

    # 6 PHYSICAL PROTOCOL - the handover
    if np.isfinite(c.value) and expected_sd and tolerance:
        c.plan = plan_experiment(
            title=question[:60], prediction=f"simulated {quantity}",
            predicted_value=c.value, unit=unit,
            measurand=measurand or quantity, levels=[1],
            expected_sd=expected_sd, tolerance=tolerance)
        c.steps.append(Step("write physical test protocol", "DONE",
                            f"{c.plan.total_specimens} specimens, resolution "
                            f"{c.plan.resolution_required:.3g} {unit}"))
        c.steps.append(Step("perform the physical experiment", "BLOCKED",
                            f"{c.plan.total_specimens} specimens must be made and "
                            f"measured", "human"))
        c.steps.append(Step("validate against measurement", "BLOCKED",
                            "waiting on the measurements above", "human"))
    else:
        c.steps.append(Step("write physical test protocol", "BLOCKED",
                            "needs expected scatter and a tolerance that matters",
                            "human"))

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"campaign_{abs(hash(question)) % 10**8}.json"
    p.write_text(json.dumps(c.to_dict(), indent=2, default=str))
    c.artefacts.append(p)
    if verbose:
        print(c)
        print(f"  written to {p}")
    return c


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

    print("== the method registry reflects what is installed ==")
    avail = [m.name for m in METHODS if m.is_available()]
    chk(len(avail) >= 5, f"{len(avail)} solvers importable: {avail[:4]}")
    m, considered = choose_method("deflection")
    chk(m is not None, f"a method is chosen for deflection ({m.name if m else None})")
    chk(any(r == "SELECTED" for _n, r in considered), "the choice is marked")
    chk(any("does not compute" in r for _n, r in considered),
        "and the rejected candidates carry a reason")
    m2, _ = choose_method("unicorn density")
    chk(m2 is None, "no solver is invented for a quantity nothing computes")

    print("== a converging campaign ==")
    truth = 12.5
    camp = run_campaign(
        "deflection of a cantilever under tip load", "deflection",
        simulate=lambda e: truth + 50.0 / e**2, efforts=[10, 20, 40, 80, 160, 320, 640],
        unit="mm", cross=lambda e: truth * 1.001,
        expected_sd=0.5, tolerance=0.5, measurand="tip deflection by dial gauge",
        verbose=False)
    chk(camp.loop.outcome == "CONVERGED", f"it converges ({camp.loop.outcome})")
    chk(abs(camp.value - truth) < 0.01, f"onto the right value ({camp.value:.4f})")
    chk(np.isfinite(camp.cross_value), "the cross-check ran")
    chk(camp.plan is not None, "a physical protocol was written")
    chk(camp.plan.total_specimens >= 3, "with a real specimen count")

    print("== it stops at the physical boundary, and says so ==")
    human = camp.blocked_on_human
    chk(len(human) >= 2, f"{len(human)} steps blocked on a human")
    chk(any("perform the physical experiment" in s.name for s in human),
        "performing the experiment is explicitly not done")
    chk(0.0 < camp.completion < 1.0,
        f"completion is partial and honest ({camp.completion*100:.0f}%)")
    chk("not a probability" in str(camp),
        "and completion is not dressed up as confidence")

    print("== a campaign that cannot converge does not pretend ==")
    rng = np.random.default_rng(1)
    bad = run_campaign("noisy thing", "deflection",
                       simulate=lambda e: float(rng.normal(10, 5)),
                       efforts=[10, 20, 40], unit="mm", verbose=False)
    chk(bad.loop.outcome == "BUDGET_SPENT", "reported as not converged")
    chk(any(s.state == "BLOCKED" and s.blocked_on == "compute" for s in bad.steps),
        "and the blockage is attributed to compute, not to a human")
    chk(bad.completion < camp.completion, "and scores lower than the good campaign")

    print("== no cross-check is a stated weakness, not a silent one ==")
    solo = run_campaign("single method", "deflection",
                        simulate=lambda e: 5.0 + 1.0 / e**2,
                        efforts=[10, 20, 40, 80, 160, 320, 640], unit="mm",
                        verbose=False)
    xs = [s for s in solo.steps if "cross-check" in s.name]
    chk(xs and xs[0].state == "BLOCKED", "the missing cross-check is BLOCKED")
    chk("agreeing with itself" in xs[0].detail, "and explains why that matters")

    print("== artefacts are written ==")
    chk(camp.artefacts and Path(camp.artefacts[0]).exists(),
        "a campaign record lands on disk")
    d = json.loads(Path(camp.artefacts[0]).read_text())
    chk("physical_protocol" in d, "including the protocol a scientist would follow")
    chk(d["completion"] < 1.0, "and the honest completion figure")

    print(f"\nexperiment_campaign.py: {ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(__doc__)
