#!/usr/bin/env bash
# Install PhysicsNeMo into an ISOLATED env so the verified `eng` environment
# (24 modules, all passing) cannot be broken by a torch-driven numpy downgrade.
# Logs itself and writes a DONE marker. Run detached (setsid) - a plain background
# job gets its process tree killed when the launcher is reaped, which silently
# aborted the first attempt mid-download.
exec > /tmp/pnemo_install.log 2>&1
set -x
rm -f /tmp/pnemo_install.done
source ~/miniconda3/etc/profile.d/conda.sh
conda activate pnemo || { conda create -y -n pnemo python=3.12; conda activate pnemo; }
# unpiped so progress lands in the log and nothing buffers
python -m pip install --no-input torch --index-url https://download.pytorch.org/whl/cu130
echo "TORCH_RC=$?"
python -m pip install --no-input nvidia-physicsnemo
echo "PNEMO_RC=$?"
echo "=== VERIFY ==="
python - <<'PY'
import torch
print("torch", torch.__version__, "| cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    print("device:", torch.cuda.get_device_name(0), "|", round(p.total_memory/1e9, 1), "GB")
    a = torch.randn(3000, 3000, device="cuda")
    b = (a @ a).sum().item()
    print("gpu matmul ran, sum finite:", b == b)
else:
    print("NO CUDA - would fall back to CPU, which makes PhysicsNeMo training impractical")
try:
    import physicsnemo
    print("physicsnemo", getattr(physicsnemo, "__version__", "?"))
except Exception as e:
    print("physicsnemo import FAILED:", type(e).__name__, e)
PY
echo "ALL_DONE"
touch /tmp/pnemo_install.done
