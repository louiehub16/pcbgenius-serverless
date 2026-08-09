#!/bin/bash
# Training-bootstrap (v2): install heavy deps AND emit a startup log to R2 so the
# operator can SEE the pipeline's real stderr (Salad SCE hides container logs;
# this is the diagnostic hook). Writes /tmp/startup.log and copies to R2 logs/.
set -euo pipefail

MARKER="/opt/.deps_installed"
LOG="/tmp/startup.log"
LOG_KEY="logs/training_$(date +%Y%m%d_%H%M%S).log"

exec > >(tee "$LOG") 2>&1
echo "[bootstrap] starting $(date -u +%FT%TZ)"

setup_rclone() {
  if command -v rclone >/dev/null 2>&1; then
    mkdir -p ~/.config/rclone
    cat > ~/.config/rclone/rclone.conf <<EOF
[r2]
type = s3
provider = Cloudflare
access_key_id = ${R2_ACCESS_KEY:-}
secret_access_key = ${R2_SECRET_KEY:-}
endpoint = ${R2_ENDPOINT:-}
acl = private
EOF
  fi
}
ship_log() { python /pipeline/lib/r2.py put "${LOG_KEY:-logs/startup.log}" < "$LOG" 2>/dev/null || true; }
trap ship_log EXIT

setup_rclone

if [ ! -f "$MARKER" ]; then
  echo "[bootstrap] installing CUDA-capable torch + unsloth (RTX 5090 sm_120)..."
  pip install --upgrade pip
  # ── RTX 5090 (sm_120 / Blackwell) runtime deps (Kimi round-7 verified) ──────
  # torch>=2.7 REQUIRED: first release with Blackwell consumer (sm_120) kernels.
  # torch 2.6.x+cu124 only ships sm_50..sm_90 (the sm_120 warning we saw). cu126
  # contains sm_120 for RTX 50-series (cu128 is only for datacenter sm_100/103).
  pip install --no-cache-dir "torch==2.8.0" "torchvision==0.23.0" \
    --index-url https://download.pytorch.org/whl/cu126 || {
    echo '[bootstrap] FATAL: torch 2.8.0+cu126 install failed (sm_120 host requires torch>=2.7)'
    exit 1
  }
  # torchao: register_constant exists in torch>=2.7, so torchao 0.10+ imports cleanly.
  # The round-6 ==0.9.0 pin was for torch 2.6 — replace with the 2.7/2.8 window.
  pip install --no-cache-dir "torchao>=0.10.0,<0.12.0" || {
    echo '[bootstrap] FATAL: could not install torch-compatible torchao'
    exit 1
  }
  # triton>=3.3.1 is REQUIRED for Blackwell per the unsloth Blackwell guide.
  pip install --no-cache-dir "triton>=3.3.1" --index-url https://download.pytorch.org/whl/cu126 || true
  # unsloth git main supports torch 2.7..2.9 (QLoRA NF4 backend = bitsandbytes).
  pip install --no-cache-dir "unsloth[base] @ git+https://github.com/unslothai/unsloth.git" || \
    pip install --no-cache-dir "unsloth[cu126-torch280] @ git+https://github.com/unslothai/unsloth.git" || \
    pip install --no-cache-dir unsloth || {
    echo '[bootstrap] FATAL: unsloth install failed'
    exit 1
  }
  # xformers OPTIONAL on sm_120 (unsloth falls back to native SDPA); --no-deps.
  pip install --no-cache-dir --no-deps xformers 2>/dev/null || true
  pip install --no-cache-dir trl peft accelerate bitsandbytes boto3 datasets huggingface_hub wandb
  # Runtime self-check: fail fast, never silently continue on a wrong-GPU build.
  python - <<'PY'
import torch
assert int(torch.__version__.split('.')[1]) >= 7, f"torch {torch.__version__} too old for sm_120"
if torch.cuda.is_available():
    cap = torch.cuda.get_device_capability(0)
    name = torch.cuda.get_device_name(0)
    print("GPU:", name, "capability:", cap)
    assert cap[0] == 12, f"Expected Blackwell (compute 12.x) but got {cap}"
    from torch.utils import _pytree
    assert hasattr(_pytree, "register_constant"), "torch.utils._pytree.register_constant missing!"
    import torchao
    print("torchao", torchao.__version__, "OK -- register_constant present, GPU ready")
else:
    print("[bootstrap] WARNING: no CUDA visible -- will try CPU path")
PY
  touch "$MARKER"
  echo "[bootstrap] deps installed OK (sm_120/Blackwell verified)"
else
  echo "[bootstrap] deps already present"
fi
echo "[bootstrap] bootstrap complete — deps ready"
exit 0
