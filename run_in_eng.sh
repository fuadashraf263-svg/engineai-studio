#!/usr/bin/env bash
# run_in_eng.sh - run anything inside the conda env EngineAI is actually built in.
#
# WHY THIS EXISTS. A battery was started under /usr/bin/python3, which has no
# cadquery and no fastapi. It ran, it produced numbers, and the numbers would
# have described a crippled EngineAI rather than the real one. Same family of
# bug as the cold-model A/B: the measurement apparatus was wrong, not the thing
# being measured. The env check is a hard failure here, not a warning.
set -euo pipefail

source ~/miniconda3/etc/profile.d/conda.sh 2>/dev/null || \
source ~/anaconda3/etc/profile.d/conda.sh 2>/dev/null || {
  echo "no conda found" >&2; exit 1; }
conda activate eng

python - <<'PY'
import sys, importlib.util
need = ["fastapi", "cadquery", "numpy", "scipy"]
missing = [m for m in need if importlib.util.find_spec(m) is None]
if missing:
    sys.exit(f"WRONG ENVIRONMENT: {sys.prefix} is missing {missing}. "
             "Measuring here would describe a crippled EngineAI.")
print(f"  env {sys.prefix}")
PY

cd ~/engineai
exec "$@"
