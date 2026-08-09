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
  echo "[bootstrap] installing CUDA-capable torch + unsloth..."
  pip install --upgrade pip
  pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cu124 || \
    pip install --no-cache-dir torch torchvision
  # FIX (realtime-stream, 2026-08-09): pin torchao to a torch-2.6-compatible stable
  # release BEFORE unsloth so the resolver respects it; a too-new torchao (0.10+)
  # calls torch.utils._pytree.register_constant (absent in torch 2.6) -> SFT import
  # crash. HARD-FAIL if the pin fails (wrong torchao is fatal at SFT import, and the
  # $MARKER would otherwise block a retry). No stderr swallow: this log ships to R2.
  pip install --no-cache-dir "torchao==0.9.0" || \
    pip install --no-cache-dir "torchao>=0.9.0,<0.10" || \
    { echo '[bootstrap] FATAL: could not pin torchao (needed for torch 2.6); aborting'; exit 1; }
  pip install --no-cache-dir "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git" || \
    pip install --no-cache-dir unsloth
  pip install --no-cache-dir --no-deps xformers trl peft accelerate bitsandbytes
  # Belt-and-suspenders: unsloth git may force-upgrade torchao post-install; re-assert the pin.
  pip install --no-cache-dir "torchao==0.9.0" || true
  pip install --no-cache-dir datasets huggingface_hub wandb
  pip install --no-cache-dir boto3        # R2 master helper (rclone -> AccessDenied on R2; boto3 works)
  touch "$MARKER"
  echo "[bootstrap] deps installed OK"
else
  echo "[bootstrap] deps already present"
fi
echo "[bootstrap] bootstrap complete — deps ready"
exit 0
