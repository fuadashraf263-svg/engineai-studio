#!/usr/bin/env bash
# verify_all.sh - truth audit + recursive syntax gate + module selftests
set -uo pipefail
cd "$(dirname "$0")"
rc=0
echo "==> truth audit";           bash engineai_truth_audit_ubuntu.sh "$@" || rc=1
echo "==> recursive syntax gate"; python syntax_gate.py .                  || rc=1
echo "==> capability probes";     python capabilities.py --selftest        || rc=1
echo "==> task ledger + gate";    python task_ledger.py --selftest         || rc=1
echo "==> agent loop";            python agent_loop.py --selftest          || rc=1
echo "==> fluids selftest";       python engineai_fluids.py --selftest              || rc=1
echo "==> circuits selftest";     python circuits.py --selftest            || rc=1
echo "==> nonlinear circuits";    python circuits_nl.py --selftest         || rc=1
echo "==> cad selftest";          python cad_parts.py --selftest           || rc=1
echo "==> cad drawings";          python drawings_cad.py --selftest        || rc=1
echo "==> gdt drawing";           python gdt.py --selftest                 || rc=1
echo "==> tolerance stack";       python tolerance_stack.py --selftest     || rc=1
echo "==> materials db";          python materials_db.py --selftest        || rc=1
echo "==> standard sections";     python standards.py --selftest           || rc=1
echo "==> pipe + cable";          python pipe_cable.py --selftest          || rc=1
echo "==> steel design EN1993";   python steel_design.py --selftest        || rc=1
echo "==> columns EN1993 6.3.1/3"; python column_design.py --selftest       || rc=1
echo "==> load combinations EN1990"; python loads.py --selftest            || rc=1
echo "==> mechanics of materials"; python mechanics.py --selftest          || rc=1
echo "==> heat transfer";          python thermal.py --selftest            || rc=1
echo "==> electromagnetics";       python emag.py --selftest               || rc=1
echo "==> flow beyond pipes";      python fluid_mech.py --selftest         || rc=1
echo "==> fatigue and fracture";   python fatigue.py --selftest            || rc=1
echo "==> circuits extended";      python circuits_ext.py --selftest       || rc=1
echo "==> FEA method diversity";   python fea.py --selftest                || rc=1
echo "==> multiphysics chains";    python multiphysics.py --selftest       || rc=1
echo "==> dimensioned drawings";   python drawings_dim.py --selftest       || rc=1
echo "==> CFD validated";          python cfd.py --selftest                || rc=1
echo "==> CALPHAD phase diagrams"; python phase_diagram.py --selftest      || rc=1
echo "==> magnetostatics 2D FEM"; python magnetostatics.py --selftest      || rc=1
echo "==> PHOENIX lattice";        python lattice.py --selftest            || rc=1
echo "==> CRFCE superconductor";   python superconductor.py --selftest     || rc=1
echo "==> deliverable file set";   python deliverables.py --selftest       || rc=1
echo "==> multibody PyChrono";     python multibody.py --selftest          || rc=1
echo "==> derive/verify offline";  python derive_offline.py --selftest     || rc=1
echo "==> reference data";         python refdata.py --selftest            || rc=1
echo "==> equation library";       python equations.py --selftest          || rc=1
echo "==> artifact accuracy";      python accuracy.py --selftest           || rc=1
echo "==> chat + languages";       python chat.py --selftest               || rc=1
echo "==> file ingestion + OCR";   python fileread.py --selftest           || rc=1
echo "==> Elmer, third solver";    python elmer.py --selftest              || rc=1
echo "==> request scope gate";     python scope.py --selftest              || rc=1
echo "==> real user requests";     python user_battery.py --selftest       || rc=1
echo "==> drawing sheet";       python drawings_sheet.py --selftest     || rc=1
echo "==> projection engine";    python drawings_project.py --selftest   || rc=1
echo "==> design workflow";     python design_part.py --selftest        || rc=1
echo "==> structural";          python structural.py --selftest         || rc=1
echo "==> unit-safety gate";      python units_gate.py                     || rc=1
echo "==> evidence receipt";      python receipt.py                        || rc=1
echo "==> solve-or-build gate";   python solve_or_build.py                 || rc=1
echo "==> sandbox runner";     python sandbox_runner.py                 || rc=1
echo "==> consensus gate";       python consensus.py                      || rc=1
echo "==> vault rag";           python vault_rag.py --selftest          || rc=1
echo "==> model router";        python model_router.py                   || rc=1
echo "==> research engine";     python research.py --selftest            || rc=1
echo "==> research protocol";   python research_protocol.py --selftest   || rc=1
echo "==> research credibility"; python research_credibility.py --selftest || rc=1
echo "==> data authenticity";   python data_authenticity.py --selftest   || rc=1
echo "==> law rediscovery (B)"; python research_partb.py --selftest      || rc=1
echo "==> real research (A)";   python research_parta.py --selftest      || rc=1
echo "==> research campaign";   python research_campaign.py --selftest   || rc=1
echo "==> independence proof";  python research_prove.py --selftest      || rc=1
echo "==> substring audit";     python substring_audit.py --selftest     || rc=1
echo "==> experiment design";   python experiment.py --selftest          || rc=1
echo "==> validation loop";     python validation_loop.py --selftest     || rc=1
echo "==> data fetch";          python data_fetch.py --selftest          || rc=1
echo "==> field record";        python field_record.py --selftest        || rc=1
echo "==> natural language router"; python nl_router.py --selftest       || rc=1
echo "==> api key config";      python api_keys.py --selftest            || rc=1
echo "==> server auth";         python server_auth.py --selftest         || rc=1
echo "==> data source catalogue"; python data_sources.py --selftest      || rc=1
echo "==> out-of-core data";    python bigdata.py --selftest             || rc=1
echo "==> environment doctor";  python environment.py --selftest         || rc=1
echo "==> source adapters";     python source_adapters.py --selftest     || rc=1
echo "==> reliability";         python reliability.py --selftest         || rc=1
echo "==> self diagnosis";      python self_diagnose.py --selftest       || rc=1
echo "==> metamorphic physics"; python metamorphic.py --selftest         || rc=1
echo "==> mutation testing";    python mutation.py --selftest            || rc=1
echo "==> library coverage";    python coverage_probe.py --selftest      || rc=1
echo "==> resource limits";     python resource_guard.py --selftest      || rc=1
echo "==> materials discovery"; python discovery.py --selftest           || rc=1
echo "==> research advanced";   python research_advanced.py --selftest   || rc=1
echo "==> known-law library";   python research_knowledge.py --selftest  || rc=1
echo "==> experiment campaign"; python experiment_campaign.py --selftest || rc=1
echo "==> $([ $rc -eq 0 ] && echo 'ALL PASS' || echo 'SOMETHING FAILED')"
exit $rc
