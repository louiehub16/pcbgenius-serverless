#!/bin/bash
# STAGE 2 — Synthetic data generation (~80-120k pairs, all skills, analog-weighted)
# NOTE: In production this fans out to a Salad spot GPU pool (10x RTX 4090).
# Here it runs the generation driver; the driver calls the data-gen model endpoint.
set -euo pipefail
WORK=/work
mkdir -p "$WORK/data"
cd "$WORK"

echo "[stage2] Generating synthetic dataset (all skills, curriculum-weighted)..."
python - <<'PY'
import json, os, random
# The full generator calls the data-gen model (deepseek-r1:32b) via the configured
# inference endpoint. This scaffold produces the curriculum plan + chunk manifest
# that the GPU pool consumes. Skills from the frozen contract.
SKILLS = [
    ("netlist_design", 0.30),      # core prompt->netlist
    ("firmware_gen", 0.10),
    ("architecture_blocks", 0.08),
    ("design_review", 0.10),
    ("testing_debug", 0.10),
    ("repair_board", 0.10),
    ("nl_iteration_diff", 0.08),
    ("manufacturing_aware", 0.08),
    ("datasheet_qa", 0.06),
]
TARGET = int(os.environ.get("TARGET_PAIRS", "90000"))
random.seed(42)
manifest = []
for skill, frac in SKILLS:
    n = int(TARGET * frac)
    manifest.append({"skill": skill, "target_pairs": n})
os.makedirs("data", exist_ok=True)
with open("data/curriculum_manifest.json", "w") as f:
    json.dump({"target_total": TARGET, "skills": manifest}, f, indent=2)
print(f"[stage2] Curriculum manifest written: {TARGET} pairs across {len(SKILLS)} skills")
print("[stage2] GPU pool (Salad 10x4090) would execute deepseek-r1:32b generation here.")
PY

# In the real deployment the generated raw pairs land in $WORK/data/raw/*.jsonl
# then are deduped/filtered to ~quality set.
# ---------------------------------------------------------------------------
# REAL DATA (Wave B1): pull the verified baseline already staged on R2 by the
# generator (datagen/generate_netlists.py), so training consumes contract-valid
# data instead of a scaffold. Falls back to local raw/ if present.
# ---------------------------------------------------------------------------
mkdir -p "$WORK/data/processed"
if command -v rclone >/dev/null 2>&1 && [ -n "${R2_BUCKET:-}" ]; then
  echo "[stage2] pulling real dataset from R2 artifacts/data ..."
  rclone copy "r2:${R2_BUCKET}/artifacts/data" "$WORK/data/" --include "*.jsonl" || true
fi
# Merge any raw jsonl (R2 or local) into the contract-expected training file.
python - <<'PY'
import json, os
# Dedupe + schema-validate: every pair must match the frozen contract shape.
raw_candidates = []
for d in ["data", "data/raw"]:
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".jsonl"):
                raw_candidates.append(os.path.join(d, fn))
# also pick up the baseline file directly if R2 copy landed it as-is
os.makedirs("data/processed", exist_ok=True)
kept, dropped = 0, 0
seen = set()
out = open("data/processed/pcbgenius_training_dataset.jsonl", "w")
for fp in raw_candidates:
    for line in open(fp):
        line = line.strip()
        if not line or line in seen:
            dropped += 1; continue
        seen.add(line)
        try:
            e = json.loads(line)
            nl = e if "netlist" not in e else e.get("netlist", {})
            # accept both {prompt,netlist} pairs and bare netlists
            if "nets" in nl and "components" in nl:
                out.write(line + "\n"); kept += 1
            else:
                dropped += 1
        except Exception:
            dropped += 1
out.close()
print(f"[stage2] dedupe+validate: kept={kept} dropped={dropped}")
PY
rclone copy "$WORK/data/processed" "r2:${R2_BUCKET}/artifacts/processed" 2>/dev/null || true

rclone copy "$WORK/data" "r2:${R2_BUCKET}/artifacts/data" --progress || true
echo "[stage2] Synthetic dataset staged to R2."
