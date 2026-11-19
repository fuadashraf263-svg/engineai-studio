#!/usr/bin/env bash
# run_bank.sh - the question-bank battery, in the right env, on a proven model.
#
# Two guards before a single question is asked, both of which have already been
# needed once: the interpreter must be the one EngineAI is built in, and the
# model must have answered a question out loud from cold. A run that skips
# either produces a number that describes the apparatus rather than the system.
set -euo pipefail

source ~/miniconda3/etc/profile.d/conda.sh 2>/dev/null || \
source ~/anaconda3/etc/profile.d/conda.sh 2>/dev/null
conda activate eng
cd ~/engineai

python -c "import fastapi, cadquery" || { echo "wrong env"; exit 1; }
python model_harness.py --ensure "${ENGINEAI_MODEL:-qwen2.5:7b-instruct-q4_K_M}"

exec python -u question_bank.py "$@"
