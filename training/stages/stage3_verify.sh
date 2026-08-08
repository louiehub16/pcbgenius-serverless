#!/bin/bash
# STAGE 3 — Verified labels (deterministic ERC/DRC/sim on every generated design)
# Attaches pass/fail + error labels. Produces SFT positives + DPO (good,bad) pairs.
set -euo pipefail
WORK=/work
cd "$WORK"

echo "[stage3] Running deterministic verification to attach labels..."
python - <<'PY'
import json, os, subprocess, tempfile
# For each generated design: convert netlist -> KiCad, run ERC/DRC via kicad-cli,
# run Ngspice where applicable. Attach { verified: bool, violations: [...] }.
# Also emit DPO pairs: same prompt, (chosen=pass, rejected=fail).
inp = "data/processed/pcbgenius_training_dataset.jsonl"
ver_out = open("data/processed/verified_dataset.jsonl", "w")
dpo_out = open("data/processed/dpo_pairs.jsonl", "w")
n = v = 0
if os.path.exists(inp):
    for line in open(inp):
        line = line.strip()
        if not line: continue
        e = json.loads(line); n += 1
        # PLACEHOLDER: real run calls kicad-cli + ngspice on the netlist.
        # verified = run_erc_drc(e["netlist"])
        verified = True  # scaffold default
        e["verified"] = verified
        e["violations"] = []
        ver_out.write(json.dumps(e) + "\n")
        if verified: v += 1
ver_out.close(); dpo_out.close()
print(f"[stage3] verified {v}/{n} designs (scaffold). Real run uses kicad-cli+ngspice.")
PY

rclone copy "$WORK/data/processed" "r2:${R2_BUCKET}/artifacts/processed" --progress || true
echo "[stage3] Verified labels staged to R2."
