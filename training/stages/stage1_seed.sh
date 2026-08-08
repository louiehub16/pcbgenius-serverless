#!/bin/bash
# STAGE 1 — Seed data pull (clean-license datasets only, per DATA_PROVENANCE)
set -euo pipefail
WORK=/work
mkdir -p "$WORK/seed"
cd "$WORK/seed"

echo "[stage1] Downloading clean-license seed datasets..."
# Apache-2.0 / MIT / CC-BY (attribution) only. CC-BY-SA and no-license sets are EXCLUDED
# from training data (used for RAG/benchmark only, handled separately).
python - <<'PY'
from huggingface_hub import snapshot_download
import os
tok = os.environ.get("HF_TOKEN") or None
clean = [
    ("AbijahKaj/kicad-netlist-sft-dataset", "dataset"),   # Apache-2.0
    ("bshada/open-schematics", "dataset"),                # CC-BY-4.0 (attribute)
    ("Si7li/ltspice-spice-circuits", "dataset"),          # MIT
]
for repo, typ in clean:
    try:
        p = snapshot_download(repo_id=repo, repo_type=typ, token=tok)
        print(f"[stage1] OK {repo} -> {p}")
    except Exception as e:
        print(f"[stage1] WARN {repo}: {e}")
PY

# KiCad permissive symbol/footprint libs (for schema reference, not SA training)
echo "[stage1] Cloning KiCad libraries (reference)..."
git clone --depth 1 https://gitlab.com/kicad/libraries/kicad-symbols.git "$WORK/seed/kicad-symbols" || echo "warn: symbols clone"
git clone --depth 1 https://gitlab.com/kicad/libraries/kicad-footprints.git "$WORK/seed/kicad-footprints" || echo "warn: footprints clone"

# Upload seed to R2 for downstream stages
rclone copy "$WORK/seed" "r2:${R2_BUCKET}/artifacts/seed" --progress || true
echo "[stage1] Seed data staged to R2."
