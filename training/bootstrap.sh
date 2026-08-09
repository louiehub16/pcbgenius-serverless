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
  # ── RTX 5090 (sm_120 / Blackwell) runtime deps (Kimi round-7 + round-8 refined) ──
  # torch>=2.7 adds Blackwell (sm_120) kernels; unsloth_zoo pins torchao>=0.13, but ONLY
  # torchao 0.18 imports torch.nn.functional.ScalingType (which needs torch 2.10+). Since we
  # stay on torch 2.9.1 (has sm_120 + register_constant), we MUST pin torchao >=0.13,<0.18
  # (0.13-0.17 are torch-2.9.1-compatible and use their own internal ScalingType). cu126 is
  # correct for consumer RTX 50-series. torch 2.9.1+cu126 cp310 wheel verified present.
  pip install --no-cache-dir "torch==2.9.1" "torchvision==0.24.1" \
    --index-url https://download.pytorch.org/whl/cu126 || {
    echo '[bootstrap] FATAL: torch 2.9.1+cu126 install failed'
    exit 1
  }
  # torchao: 0.13-0.17 imports cleanly on torch 2.9.1; 0.18+ needs torch>=2.10 (ScalingType).
  pip install --no-cache-dir "torchao>=0.13,<0.18" || {
    echo '[bootstrap] FATAL: could not install torch-compatible torchao'
    exit 1
  }
  # triton>=3.3 requirement handled by the torch wheel's triton dep; no separate clobbering install.
  # unsloth: try the torch-2.9.1-matched extra FIRST so pip doesn't upgrade torch away from 2.9.1.
  pip install --no-cache-dir "unsloth[cu126-torch291] @ git+https://github.com/unslothai/unsloth.git" || \
    pip install --no-cache-dir "unsloth[base] @ git+https://github.com/unslothai/unsloth.git" || \
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
    # NOTE: F.ScalingType is deliberately NOT asserted here — it only exists in torch 2.10+,
    # and torchao 0.13-0.17 (what we pin) use their own internal ScalingType. Avoid asserting
    # a torch-2.10-only symbol on our torch 2.9.1 host (Kimi round-8).
    import torchao
    assert tuple(int(x) for x in torchao.__version__.split('.')[:2]) >= (0,13), f"torchao too old: {torchao.__version__}"
    print("torchao", torchao.__version__, "OK -- GPU ready (sm_120 + register_constant verified)")
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
