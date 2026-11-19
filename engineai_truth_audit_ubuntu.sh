#!/usr/bin/env bash
# Evidence-based test runner for the installed EngineAI source.
# It does not call paid model providers, start web servers, or alter source.
# Default mode runs quick, local tests; --full also runs solver/acceptance tests
# and can take several minutes.  Results are saved under ~/engineai/out.
#
#   conda activate eng
#   bash engineai_truth_audit_ubuntu.sh
#   bash engineai_truth_audit_ubuntu.sh --full
set -Eeuo pipefail

FULL=0
ROOT="${ENGINEAI_ROOT:-$HOME/engineai}"
for arg in "$@"; do
  case "$arg" in
    --full) FULL=1 ;;
    --root=*) ROOT="${arg#*=}" ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

[[ -d "$ROOT" ]] || { echo "ERROR: EngineAI root not found: $ROOT" >&2; exit 1; }
command -v python >/dev/null 2>&1 || { echo "ERROR: activate the EngineAI Conda environment first." >&2; exit 1; }

OUT="$ROOT/out"
mkdir -p "$OUT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="$OUT/engineai_truth_audit_$STAMP.txt"
PASS=0; FAIL=0; SKIP=0

log() { printf '%s\n' "$*" | tee -a "$REPORT"; }
section() { log ""; log "== $* =="; }
pass() { PASS=$((PASS+1)); log "PASS  $*"; }
fail() { FAIL=$((FAIL+1)); log "FAIL  $*"; }
skip() { SKIP=$((SKIP+1)); log "SKIP  $*"; }

run() {
  local label="$1"; shift
  section "$label"
  log "command: $(printf '%q ' "$@")"
  set +e
  if command -v timeout >/dev/null 2>&1; then
    timeout 180 "$@" >>"$REPORT" 2>&1
  else
    "$@" >>"$REPORT" 2>&1
  fi
  local code=$?
  set -e
  if [[ $code -eq 0 ]]; then pass "$label"; else fail "$label (exit $code; see $REPORT)"; fi
}

run_if_file() {
  local file="$1" label="$2"; shift 2
  if [[ -f "$ROOT/$file" ]]; then run "$label" "$@"; else skip "$label ($file not present)"; fi
}

log "EngineAI truth audit"
log "UTC: $(date -u +%FT%TZ)"
log "root: $ROOT"
log "python: $(command -v python)"
log "full: $FULL"

section "Runtime inventory"
{
  cat /etc/os-release 2>/dev/null | sed -n '1,4p'
  python --version
  for tool in FreeCADCmd gmsh ccx paraview ElmerSolver redis-server blockMesh simpleFoam; do
    printf '%-16s %s\n' "$tool" "$(command -v "$tool" || echo missing)"
  done
  python - <<'PY'
import importlib.util
for name in ("fastapi", "uvicorn", "cadquery", "ifcopenshell", "trimesh", "meshio", "pyvista", "vtk", "mp_api"):
    print(f"{name:<16} {'installed' if importlib.util.find_spec(name) else 'missing'}")
PY
} >>"$REPORT" 2>&1
pass "runtime inventory collected"

run "parse every EngineAI Python file" python -c '
import ast, pathlib, sys
root = pathlib.Path(sys.argv[1]); files = sorted(root.glob("*.py")); bad=[]
for p in files:
    try: ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
    except Exception as e: bad.append(f"{p.name}: {type(e).__name__}: {e}")
print(f"parsed {len(files)} top-level Python files")
if bad: print("\\n".join(bad)); raise SystemExit(1)
' "$ROOT"

run_if_file "providers.py" "provider failover self-test (no real API call)" python "$ROOT/providers.py" --selftest
run_if_file "studio.py" "Studio route self-test" python "$ROOT/studio.py" --selftest
run_if_file "materials.py" "built-in material compare" python "$ROOT/materials.py" --compare Ti6Al4V Inconel718
run_if_file "materials_discovery.py" "materials discovery self-test" python "$ROOT/materials_discovery.py" --selftest
run_if_file "pc_inventory.py" "inventory command interface" python "$ROOT/pc_inventory.py" --help
run_if_file "checkup.py" "EngineAI self-reported gap check" python "$ROOT/checkup.py"
run_if_file "live_workspace.py" "Live Workspace self-test" python "$ROOT/live_workspace.py" --selftest
run_if_file "live_sandbox.py" "Local task runner self-test" python "$ROOT/live_sandbox.py" --selftest
run_if_file "project_hub.py" "Project memory and evidence hub self-test" python "$ROOT/project_hub.py" --selftest
run_if_file "vision_tools.py" "Confirmed vision-payload self-test" python "$ROOT/vision_tools.py"
run_if_file "assurance_chat.py" "Assurance chat transparency self-test" python "$ROOT/assurance_chat.py" --selftest
run_if_file "engineai_import_inspector.py" "CAD/BIM import inspector self-test" python "$ROOT/engineai_import_inspector.py" --selftest

section "Local Studio endpoint (only if it is already running)"
if command -v curl >/dev/null 2>&1; then
  set +e
  health="$(curl --max-time 3 -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8099/api/health)"
  code=$?
  set -e
  if [[ $code -eq 0 && "$health" == "200" ]]; then pass "Studio health endpoint returned 200"; else skip "Studio health not publicly readable (not running or auth enabled; HTTP $health)"; fi
else
  skip "curl not installed; did not probe Studio"
fi

if [[ "$FULL" == 1 ]]; then
  # These create normal EngineAI output artifacts.  They are intentionally
  # opt-in because they can consume CPU and need manual engineering review.
  run_if_file "acceptance_test.py" "acceptance suite" python "$ROOT/acceptance_test.py" --quick
  run_if_file "engineai_fea_benchmark.py" "CalculiX cantilever verification" python "$ROOT/engineai_fea_benchmark.py" --run --out "$OUT/fea_benchmark"
  run_if_file "cfd_validate.py" "CFD mesh-refinement validation" python "$ROOT/cfd_validate.py" --refine
else
  section "Not run in quick mode"
  skip "acceptance_test.py --quick (use --full)"
  skip "engineai_fea_benchmark.py --run (use --full; compare its reported error)"
  skip "cfd_validate.py --refine (use --full; inspect the convergence table)"
fi

section "Conclusion"
log "PASS=$PASS FAIL=$FAIL SKIP=$SKIP"
log "This report proves only executed checks. A PASS is not a certification, manufacturing approval, or solver validation."
log "Report: $REPORT"
[[ $FAIL -eq 0 ]]
