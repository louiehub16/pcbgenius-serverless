#!/bin/bash
# Bootstrap: install heavy training deps at container STARTUP (GPU node 50GB+ disk),
# not at GH build time. Runs once (idempotent via marker).
set -euo pipefail

MARKER="/opt/.deps_installed"
if [ -f "$MARKER" ]; then
  echo "[bootstrap] deps already installed, skipping"
  exit 0
fi

echo "[bootstrap] installing CUDA-capable torch + unsloth (can take several minutes)..."
# cu124 torch (works on RTX 5090/Ada/H100 family)
pip install --upgrade pip
pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cu124 || \
  pip install --no-cache-dir torch torchvision

pip install --no-cache-dir "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git" || \
  pip install --no-cache-dir unsloth
pip install --no-cache-dir --no-deps xformers trl peft accelerate bitsandbytes
pip install --no-cache-dir datasets huggingface_hub wandb

touch "$MARKER"
echo "[bootstrap] deps installed OK"
