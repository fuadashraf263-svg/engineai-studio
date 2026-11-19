# EngineAI - Verified Capabilities

_Last verified: 2026-08-07, dev machine (WSL Ubuntu, conda env `eng`)._

Every item was reproduced with a passing check on this machine. A PASS means the
result was produced and cross-checked against an independent reference - it is
NOT certification, manufacturing approval, or solver validation. Re-run all with
`./verify_all.sh --full`.

## Engineering analysis (each carries an analytical cross-check)
- Structural FEA (CalculiX, C3D8I): cantilever tip deflection 0.48% vs beam theory. `python engineai_fea_benchmark.py --run`
- CFD (OpenFOAM): Poiseuille mesh convergence 1.96% -> 0.41% centreline error. `python cfd_validate.py --refine`
- Fluid mechanics: Darcy vs Hagen-Poiseuille to ~1e-14%, Colebrook vs Blasius/Haaland, entrance length matches CFD. `python engineai_fluids.py --selftest`
- Linear circuits: MNA vs closed form to ~1e-14, cross-checked vs ngspice (divider, RC low-pass, RLC resonance). `python circuits.py --selftest`
- Nonlinear circuits: diode operating point Newton == bisection to 1e-9, cross-checked vs ngspice. `python circuits_nl.py --selftest`
- Parametric CAD: CadQuery volume == closed form to ~1e-16, STEP round-trip preserved, exports STEP/STL. `python cad_parts.py --selftest`
- Tool registry: pipe_flow, rc_filter, plate_with_holes registered in engine_tools.py with analytical cross-checks. `python engine_tools.py rc_filter`

## Platform / services
- Five local services healthy (HTTP 200): 8101 Live Workspace, 8102 task runner, 8103 Project Hub, 8104 assurance chat, 8105 browser worker.
- Sandbox isolation (Bubblewrap): code runs with host filesystem hidden and network blocked.
- Browser worker: launches Chromium, navigates, screenshots; localhost-only, fresh context, per-action confirmation, no login/download automation.
- Model path: live OpenRouter request succeeds. `python keypool.py test`

## Integrity guards
- Generator (propose.py) refuses to write invalid/truncated Python.
- Recursive syntax gate scans all repo .py (303 files, 0 broken). `python syntax_gate.py .`

## NOT verified / still open
See LACKINGS.md and UNVERIFIED.md: editable CAD import, 2D drawings/GD&T/BOM, KiCad PCB layout, provenance-backed materials data, multimodal validation, and manufacturing-grade sign-off (tolerance stack-up, fatigue, certification). No result here is design certification.
