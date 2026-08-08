#!/bin/bash
# ============================================================================
# PCBGenius — MASTER PIPELINE ORCHESTRATOR (100% auto after env is set)
# Runs the full standalone model-training pipeline end-to-end on Salad Cloud.
# State is checkpointed to R2 after every stage → spot-restart safe, resumable.
# Re-running skips already-completed stages (idempotent).
#
# REQUIRED ENV (set these once at deploy time — see deploy.json):
#   R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_BUCKET
#   OPENROUTER_API_KEY, FIREWORKS_API_KEY, HF_TOKEN (optional), WANDB_API_KEY (optional)
# ============================================================================
set -euo pipefail

STAGES_DIR="/pipeline/stages"
LIB_DIR="/pipeline/lib"
STATE_KEY="state/pipeline_state.txt"          # on R2
LOG_KEY="logs/pipeline_$(date +%Y%m%d_%H%M%S).log"
WORK="/work"
mkdir -p "$WORK"

# Cost guard (auto-halt on overspend)
# shellcheck source=lib/cost_guard.sh
[ -f "$LIB_DIR/cost_guard.sh" ] && source "$LIB_DIR/cost_guard.sh"

# ----------------------------------------------------------------------------
# R2 helper (rclone) — configured from env
# ----------------------------------------------------------------------------
setup_rclone() {
  mkdir -p ~/.config/rclone
  cat > ~/.config/rclone/rclone.conf <<EOF
[r2]
type = s3
provider = Cloudflare
access_key_id = ${R2_ACCESS_KEY}
secret_access_key = ${R2_SECRET_KEY}
endpoint = ${R2_ENDPOINT}
acl = private
EOF
}

log() { echo "[$(date +%H:%M:%S)] $*"; }

# Read last completed stage (0 if none)
get_state() {
  rclone cat "r2:${R2_BUCKET}/${STATE_KEY}" 2>/dev/null || echo "0"
}
set_state() {
  echo "$1" | rclone rcat "r2:${R2_BUCKET}/${STATE_KEY}"
}

require_env() {
  local missing=0
  for v in R2_ACCESS_KEY R2_SECRET_KEY R2_ENDPOINT R2_BUCKET OPENROUTER_API_KEY FIREWORKS_API_KEY; do
    if [ -z "${!v:-}" ]; then log "❌ MISSING env: $v"; missing=1; fi
  done
  if [ "$missing" = "1" ]; then log "Set all required env vars and re-run."; exit 2; fi
}

run_stage() {
  local num="$1"; local name="$2"; local script="$3"; local est_cost="${4:-0}"
  local done_stage; done_stage=$(get_state)
  if [ "$done_stage" -ge "$num" ]; then
    log "⏭️  STAGE $num ($name) already complete — skipping."
    return 0
  fi
  log "▶️  STAGE $num: $name (est \$${est_cost})"
  if bash "$STAGES_DIR/$script"; then
    command -v cost_add >/dev/null 2>&1 && cost_add "$est_cost" || true
    set_state "$num"
    log "✅ STAGE $num complete."
    command -v cost_report >/dev/null 2>&1 && cost_report || true
  else
    log "❌ STAGE $num FAILED. State preserved at stage $((num-1)). Fix and re-run."
    exit 1
  fi
}

# ----------------------------------------------------------------------------
main() {
  log "======================================================"
  log " PCBGenius standalone model pipeline — START"
  log "======================================================"
  require_env
  setup_rclone

  # Install heavy training deps at container startup (GPU node disk), 
  # so the image itself stays small and GH-build-friendly.
  if [ -f /bootstrap.sh ]; then
    log "▶ bootstrap: installing training deps..."
    bash /bootstrap.sh || log "⚠ bootstrap reported failure (may be fine if deps present)"
  fi

  run_stage 1 "Seed data pull"                 "stage1_seed.sh"        "0"
  run_stage 2 "Synthetic data generation"      "stage2_datagen.sh"     "18"
  run_stage 3 "Verified labels (KiCad/Ngspice)" "stage3_verify.sh"      "1"
  run_stage 4 "Fireworks compatibility GATE"   "stage4_gate.sh"        "0"
  run_stage 5 "SFT fine-tune (H100)"           "stage5_sft.sh"         "45"
  run_stage 6 "DPO + export + upload"          "stage6_dpo_export.sh"  "6"

  log "======================================================"
  log " 🎉 PIPELINE COMPLETE — model artifact on R2 + Fireworks"
  log "======================================================"
  # ship the log
  rclone copyto /var/log/pipeline.log "r2:${R2_BUCKET}/${LOG_KEY}" 2>/dev/null || true
}

main "$@" 2>&1 | tee -a /var/log/pipeline.log
