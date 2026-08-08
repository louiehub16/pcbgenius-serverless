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
ship_log() { rclone copyto "$LOG" "r2:${R2_BUCKET:-/}/$LOG_KEY" 2>/dev/null || true; }
trap ship_log EXIT

setup_rclone

if [ ! -f "$MARKER" ]; then
  echo "[bootstrap] installing CUDA-capable torch + unsloth..."
  pip install --upgrade pip
  pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cu124 || \
    pip install --no-cache-dir torch torchvision
  pip install --no-cache-dir "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git" || \
    pip install --no-cache-dir unsloth
  pip install --no-cache-dir --no-deps xformers trl peft accelerate bitsandbytes
  pip install --no-cache-dir datasets huggingface_hub wandb
  touch "$MARKER"
  echo "[bootstrap] deps installed OK"
else
  echo "[bootstrap] deps already present"
fi
echo "[bootstrap] bootstrap complete — starting master pipeline"
bash /pipeline/master_pipeline.sh
