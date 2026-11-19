#!/usr/bin/env bash
# ============================================================================
#  EngineAI installer — clean Ubuntu (or WSL2 Ubuntu) to a working install.
#
#  Safe to re-run. Every step checks before it acts, so a second run repairs
#  a broken install instead of duplicating one.
#
#    bash install.sh              full install
#    bash install.sh --check      report only, change nothing
#    bash install.sh --no-apt     skip apt (no sudo available)
#    bash install.sh --minimal    core only, skip the heavy optional stack
# ============================================================================
set -uo pipefail

HOME_DIR="${HOME}"
APP="${ENGINEAI_HOME:-$HOME_DIR/engineai}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYBIN=""
MODE="install"
DO_APT=1
MINIMAL=0

for a in "$@"; do
  case "$a" in
    --check)   MODE="check" ;;
    --no-apt)  DO_APT=0 ;;
    --minimal) MINIMAL=1 ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}"; exit 0 ;;
  esac
done

G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[36m'; N=$'\e[0m'
ok()   { echo "  ${G}ok${N}   $*"; }
warn() { echo "  ${Y}warn${N} $*"; }
bad()  { echo "  ${R}FAIL${N} $*"; FAILED=1; }
step() { echo; echo "${B}== $*${N}"; }
FAILED=0
SKIPPED=()

# ---------------------------------------------------------------- 1. system
step "1/8  system"

if [ -r /etc/os-release ]; then
  . /etc/os-release
  ok "$PRETTY_NAME"
else
  warn "cannot read /etc/os-release — proceeding anyway"
fi

grep -qi microsoft /proc/version 2>/dev/null && ok "running under WSL" || true

CORES=$(nproc 2>/dev/null || echo 1)
MEMGB=$(awk '/MemTotal/ {printf "%.1f", $2/1048576}' /proc/meminfo 2>/dev/null || echo "?")
DISKGB=$(df -BG --output=avail "$HOME_DIR" 2>/dev/null | tail -1 | tr -dc '0-9' || echo "?")
ok "${CORES} cores, ${MEMGB} GB RAM, ${DISKGB} GB free on \$HOME"

[ "${CORES}" -lt 4 ] 2>/dev/null && warn "fewer than 4 cores — CalculiX and OpenFOAM will be slow"
[ "${DISKGB:-999}" -lt 20 ] 2>/dev/null && warn "under 20 GB free — the book index alone wants ~5 GB"

# WSL memory check: a default .wslconfig starves the solvers
if grep -qi microsoft /proc/version 2>/dev/null; then
  if awk '/MemTotal/ {exit !($2/1048576 < 8)}' /proc/meminfo 2>/dev/null; then
    warn "under 8 GB visible to WSL. On Windows, edit C:\\Users\\<you>\\.wslconfig:"
    echo "         [wsl2]"
    echo "         memory=12GB"
    echo "         processors=${CORES}"
  fi
fi

# ---------------------------------------------------------------- 2. python
step "2/8  python"

for c in python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    V=$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
    if "$c" -c 'import sys;sys.exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
      PYBIN="$c"; ok "$c (Python $V)"; break
    fi
  fi
done

if [ -z "$PYBIN" ]; then
  bad "no Python >= 3.9 found. Install it:  sudo apt install python3 python3-venv python3-pip"
  exit 1
fi

if ! "$PYBIN" -c 'import venv' 2>/dev/null; then
  warn "python3-venv missing — will try apt below"
fi

# ---------------------------------------------------------------- 3. apt
step "3/8  system packages"

APT_WANT=(
  build-essential          # compilers; several wheels build from source
  python3-venv python3-dev
  git curl unzip
  calculix-ccx             # the FEA solver — sdf_fea.py needs this
  libglu1-mesa             # gmsh / pyvista shared object
  libgl1                   # matplotlib Agg fallback + trimesh
  qpdf                     # pdf skill uses it
)

if [ "$DO_APT" = "0" ]; then
  warn "--no-apt: skipping. Missing solvers will be reported at the end."
elif ! command -v apt-get >/dev/null 2>&1; then
  warn "no apt-get — not a Debian/Ubuntu system. Install these yourself: ${APT_WANT[*]}"
else
  MISSING=()
  for p in "${APT_WANT[@]}"; do
    dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p")
  done
  if [ ${#MISSING[@]} -eq 0 ]; then
    ok "all ${#APT_WANT[@]} system packages already present"
  elif [ "$MODE" = "check" ]; then
    warn "would install: ${MISSING[*]}"
  elif [ "$(id -u)" = "0" ]; then
    apt-get update -qq && apt-get install -y -qq "${MISSING[@]}" && ok "installed ${MISSING[*]}"
  elif sudo -n true 2>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y -qq "${MISSING[@]}" && ok "installed ${MISSING[*]}"
  else
    warn "need sudo for: ${MISSING[*]}"
    echo "         run:  sudo apt-get install -y ${MISSING[*]}"
    SKIPPED+=("apt: ${MISSING[*]}")
  fi
fi

# ---------------------------------------------------------------- 4. venv
step "4/8  virtual environment"

VENV="$APP/.venv"
if [ "$MODE" = "check" ]; then
  [ -x "$VENV/bin/python" ] && ok "venv exists at $VENV" || warn "would create $VENV"
else
  mkdir -p "$APP"
  if [ -x "$VENV/bin/python" ]; then
    ok "reusing $VENV"
  elif "$PYBIN" -m venv "$VENV" 2>/dev/null; then
    ok "created $VENV"
  else
    bad "could not create venv. Install python3-venv:  sudo apt install python3-venv"
    exit 1
  fi
  PYBIN="$VENV/bin/python"
  "$PYBIN" -m pip install -q --upgrade pip setuptools wheel 2>/dev/null && ok "pip upgraded"
fi

# ---------------------------------------------------------------- 5. python deps
step "5/8  python packages"

CORE=(
  numpy scipy sympy matplotlib
  scikit-image        # marching cubes — sdf_kernel.py
  pint                # units.py dimensional audit
  pymupdf             # kb.py, the tolerant PDF reader
  fastapi "uvicorn[standard]"
  requests python-dotenv
  trimesh meshio ezdxf
  python-docx openpyxl python-pptx reportlab pypdf
)
EXTRA=(
  cadquery            # B-rep CAD / STEP
  CoolProp fluids ht thermo
  sectionproperties control
  pymoo pyDOE3 SALib
  handcalcs uncertainties
  pandas
)

pipq() { "$PYBIN" -m pip install -q "$@" 2>&1 | tail -2; }

if [ "$MODE" = "check" ]; then
  for m in numpy scipy sympy skimage pint fitz fastapi trimesh ezdxf docx openpyxl pptx reportlab; do
    "$PYBIN" -c "import $m" 2>/dev/null && ok "$m" || warn "$m missing"
  done
else
  echo "  installing ${#CORE[@]} core packages (a few minutes on first run)..."
  if pipq "${CORE[@]}"; then ok "core packages"; else warn "some core packages failed — see above"; fi

  if [ "$MINIMAL" = "1" ]; then
    warn "--minimal: skipped the optional stack (${#EXTRA[@]} packages)"
    SKIPPED+=("optional python stack")
  else
    echo "  installing ${#EXTRA[@]} optional packages (cadquery is large)..."
    for p in "${EXTRA[@]}"; do
      "$PYBIN" -m pip install -q "$p" >/dev/null 2>&1 \
        && echo "    + $p" || echo "    ${Y}-${N} $p (skipped, not fatal)"
    done
    ok "optional stack attempted"
  fi

  # pin what we ended up with — reproducibility, and it blocks a future
  # install from silently pulling a different version
  "$PYBIN" -m pip freeze > "$APP/requirements.lock" 2>/dev/null \
    && ok "pinned $(wc -l < "$APP/requirements.lock") packages to requirements.lock"
fi

# ---------------------------------------------------------------- 6. app files
step "6/8  application files"

FILES=(
  engineai.py units.py owner.py kb.py keypool.py providers.py
  direct.py agent_loop.py studio.py brain_local.py limits.py
  sdf_kernel.py sdf_fea.py coupled.py cem.py viz3d.py
  generators.py generators_pro.py drawings.py render_image.py
  worksheet.py course.py journal.py checkup.py sysinfo.py
  cfd_validate.py pre_push.py studio_doctor.py
  LICENSE TERMS.md
)

if [ "$MODE" = "check" ]; then
  n=0; for f in "${FILES[@]}"; do [ -f "$APP/$f" ] && n=$((n+1)); done
  ok "$n/${#FILES[@]} application files present in $APP"
else
  mkdir -p "$APP"/{out,work,library,books}
  COPIED=0; ABSENT=()
  for f in "${FILES[@]}"; do
    if [ -f "$SRC/$f" ]; then
      cp -p "$SRC/$f" "$APP/$f" && COPIED=$((COPIED+1))
    elif [ ! -f "$APP/$f" ]; then
      ABSENT+=("$f")
    fi
  done
  ok "copied $COPIED file(s) into $APP"
  [ ${#ABSENT[@]} -gt 0 ] && warn "not found in source dir: ${ABSENT[*]}"

  # .env — created once, never overwritten, and locked down
  if [ ! -f "$APP/.env" ]; then
    cat > "$APP/.env" <<'ENVEOF'
# EngineAI API keys.  chmod 600.  NEVER commit this file.
# Fill in the providers you have; keypool.py skips blanks and fails over.
GROQ_API_KEY=
CEREBRAS_API_KEY=
GOOGLE_API_KEY=
MISTRAL_API_KEY=
TOGETHER_API_KEY=
OPENROUTER_API_KEY=
DEEPSEEK_API_KEY=
COHERE_API_KEY=
NVIDIA_API_KEY=
HF_TOKEN=
# Paid — ignored unless ENGINEAI_ALLOW_PAID=1
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
ENVEOF
    chmod 600 "$APP/.env"
    ok "created $APP/.env (chmod 600) — add your keys"
  else
    chmod 600 "$APP/.env"
    ok ".env present, permissions set to 600"
  fi

  # git — the single cheapest protection against a bad edit
  if [ ! -d "$APP/.git" ]; then
    ( cd "$APP" && git init -q 2>/dev/null \
      && printf 'out/\nwork/\nlibrary/\nbooks/\n.venv/\n*.db\n.env\n.owner.json\naudit.log\n__pycache__/\n*.pyc\nrequirements.lock\n' > .gitignore \
      && git add -A >/dev/null 2>&1 \
      && git -c user.email=engineai@local -c user.name=EngineAI commit -qm "install" 2>/dev/null ) \
      && ok "git repository initialised" || warn "git init skipped"
  else
    ok "git repository already present"
  fi
fi

# ---------------------------------------------------------------- 7. solvers
step "7/8  solvers"

check_bin() {
  if command -v "$1" >/dev/null 2>&1; then ok "$1 — $2"; return 0
  else warn "$1 not found — $3"; SKIPPED+=("$1"); return 1; fi
}
check_bin ccx        "CalculiX, FEA"                "sudo apt install calculix-ccx"
check_bin blockMesh  "OpenFOAM, CFD"                "optional: openfoam.org install guide"
command -v ollama >/dev/null 2>&1 \
  && ok "ollama — local model available" \
  || ok "no ollama — API-only mode (this is your chosen configuration)"

# ---------------------------------------------------------------- 8. smoke test
step "8/8  smoke test"

if [ "$MODE" = "check" ]; then
  echo "  (check mode — not run)"
else
  cd "$APP" || exit 1
  PASS=0; TOTAL=0

  run_test() {
    TOTAL=$((TOTAL+1))
    if out=$("$PYBIN" "$@" 2>&1); then PASS=$((PASS+1)); ok "$1 $2"
    else bad "$1 $2"; echo "$out" | tail -4 | sed 's/^/         /'; fi
  }

  # syntax first — a file that does not parse fails every later test confusingly
  BADSYN=0
  for f in "$APP"/*.py; do
    "$PYBIN" -c "import ast,sys;ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$f" 2>/dev/null \
      || { bad "$(basename "$f") does not parse"; BADSYN=$((BADSYN+1)); }
  done
  [ "$BADSYN" = "0" ] && ok "all $(ls "$APP"/*.py 2>/dev/null | wc -l) python files parse"

  for m in units owner sdf_kernel sdf_fea direct engineai; do
    [ -f "$APP/$m.py" ] && run_test "$m.py" --selftest
  done

  echo
  if [ "$PASS" = "$TOTAL" ] && [ "$BADSYN" = "0" ]; then
    ok "smoke test $PASS/$TOTAL"
  else
    bad "smoke test $PASS/$TOTAL"
  fi
fi

# ---------------------------------------------------------------- summary
echo
echo "${B}════════════════════════════════════════════════════════════${N}"
if [ "$MODE" = "check" ]; then
  echo "  check complete — nothing was changed"
elif [ "$FAILED" = "0" ]; then
  echo "  ${G}EngineAI installed${N} at $APP"
else
  echo "  ${R}installed with failures${N} — see FAIL lines above"
fi

if [ ${#SKIPPED[@]} -gt 0 ]; then
  echo
  echo "  not installed:"
  for s in "${SKIPPED[@]}"; do echo "    - $s"; done
fi

cat <<EOF

  start it:
    cd $APP
    .venv/bin/python studio.py                 # then open http://127.0.0.1:8099

  first run:
    .venv/bin/python owner.py --init           # set your password
    nano .env                                  # add at least one API key
    .venv/bin/python kb.py add ~/engineai/books
    .venv/bin/python checkup.py                # what is still missing

  Keep it on 127.0.0.1 until TLS is in front of it.
EOF
echo "${B}════════════════════════════════════════════════════════════${N}"
exit $FAILED
