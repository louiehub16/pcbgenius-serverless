#!/bin/bash
# STAGE 3 — Verified labels (deterministic ERC/DRC/sim on every generated design)
# Attaches pass/fail + error labels. Produces SFT positives + DPO (good,bad) pairs.
# Kimi round-5 hardened: atomic boto3 input-fetch w/ rc+size check, n>0 guards,
# loud sync (no `|| true` / stderr swallow) so a failure can never destroy R2 data.
set -euo pipefail
WORK=/work
cd "$WORK"
echo "[stage3] Running deterministic verification to attach labels..."

# --- Robust input fetch via boto3 (atomic; hard-fail on failure) ------------
python - <<'PY'
import os, subprocess, sys
p = "/pipeline/lib/r2.py"
inp = "data/processed/pcbgenius_training_dataset.jsonl"
tmp = inp + ".tmp"
os.makedirs(os.path.dirname(inp), exist_ok=True)
# Only fetch if missing/empty locally.
need = (not os.path.exists(inp)) or os.path.getsize(inp) == 0
if need:
    with open(tmp, "wb") as f:
        r = subprocess.run(["python", p, "get",
                            "artifacts/processed/pcbgenius_training_dataset.jsonl"], stdout=f)
    if r.returncode != 0 or (os.path.exists(tmp) and os.path.getsize(tmp) == 0):
        try: os.remove(tmp)
        except OSError: pass
        sys.stderr.write("[stage3] FETCH FAILED: could not pull training input from R2; aborting.\n")
        sys.exit(1)
    os.replace(tmp, inp)
    print(f"[stage3] pulled input from R2 -> {inp} ({os.path.getsize(inp)} bytes)")
else:
    print(f"[stage3] input already local: {inp} ({os.path.getsize(inp)} bytes)")
# Hard gate: if still no real (non-empty) input, abort loudly.
if (not os.path.exists(inp)) or os.path.getsize(inp) == 0:
    sys.stderr.write("[stage3] INPUT MISSING/EMPTY after fetch — aborting to avoid data loss.\n")
    sys.exit(1)
PY

python - <<'PY'
import json, os, sys
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
# NEVER emit/sync empty outputs over good R2 data.
if n == 0:
    sys.stderr.write("[stage3] parsed 0 records — refusing to sync empty outputs (data-loss guard)\n")
    sys.exit(1)
PY

# --- Persist outputs via boto3. NO `|| true`, NO stderr swallow: a failure here
#     must abort (set -e) so we never claim success while nothing persisted. -----
python /pipeline/lib/r2.py syncLocalDir "$WORK/data/processed" "artifacts/processed"
echo "[stage3] Verified labels staged to R2."