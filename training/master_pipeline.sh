#!/bin/bash
# ============================================================================
# PCBGenius — MASTER PIPELINE ORCHESTRATOR (100% auto after env is set)
# Runs the full standalone model-training pipeline end-to-end on Salad Cloud.
# State is checkpointed to R2 after every stage → spot-restart safe, resumable.
# Re-running skips already-completed stages (idempotent).
#
# REQUIRED ENV (set these once at deploy time — see deploy.json):
#   R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_BUCKET
#   OPENROUTER_API_KEY, HF_TOKEN (optional), WANDB_API_KEY (optional)
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

# Ship the running pipeline log to R2 (boto3) so an operator can live-tail progress
# mid-run WITHOUT SSH. Called after every stage + periodically. Kept current at
# logs/pipeline_live.log so the watcher only has to poll one key.
ship_live_log() {
  local dst="logs/pipeline_live.log"
  python /pipeline/lib/r2.py put "${dst}" < /var/log/pipeline.log 2>/dev/null || true
}

# Read last completed stage (0 if none). Empty-safe: `|| echo` attached to a
# PIPELINE (python|tr) never fires because `tr` succeeds on empty input; must
# validate + default in the shell instead of relying on pipeline rc.
get_state() {
  local s
  # Guard the assignment itself: under `set -euo pipefail`, a non-zero python rc
  # (e.g. missing state key on a fresh deploy) would abort before the regex guard.
  s="$(python /pipeline/lib/r2.py get "${STATE_KEY}" 2>/dev/null | tr -d '[:space:]')" || s=""
  if [[ "${s:-}" =~ ^[0-9]+$ ]]; then echo "$s"; else echo "0"; fi
}
set_state() {
  # State/heartbeat/status writes go through the boto3 helper (r2.py), NOT rclone.
  # Diagnosed 2026-08-09: `rclone rcat`/`copyto` → AccessDenied on Cloudflare R2,
  # while boto3 SigV4 PUT with the same creds WORKS. r2.py uses boto3 => persists.
  printf '%s\n' "$1" | python /pipeline/lib/r2.py put "${STATE_KEY}" || log "⚠ set_state $1 failed"
}

require_env() {
  local missing=0
  for v in R2_ACCESS_KEY R2_SECRET_KEY R2_ENDPOINT R2_BUCKET OPENROUTER_API_KEY; do
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
    # NO `|| true` here: cost_add exits non-zero (90) when spend exceeds cap, and
    # that MUST propagate so the pipeline halts instead of bleeding past the cap.
    command -v cost_add >/dev/null 2>&1 && cost_add "$est_cost"
    set_state "$num"
    log "✅ STAGE $num complete."
    command -v ship_live_log >/dev/null 2>&1 && ship_live_log || true   # live-tail mid-run
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

  # NOTE: heavy deps are installed by /bootstrap.sh, which the ENTRYPOINT runs
  # BEFORE calling this master_pipeline.sh. Do NOT re-invoke bootstrap here —
  # that causes bootstrap <-> master infinite recursion (Kimi review 2026-08-09).
  if [ -n "${BOOTSTRAP_DONE:-}" ]; then
    log "▶ bootstrap skipped (BOOTSTRAP_DONE=$BOOTSTRAP_DONE, entrypoint installed deps)"
  else
    log "▶ entrypoint already ran /bootstrap.sh before calling master"
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
  # ship the log via boto3 helper (rclone WRITE to R2 = AccessDenied)
  python /pipeline/lib/r2.py put "${LOG_KEY}" 2>/dev/null < /var/log/pipeline.log || true
}

main "$@" 2>&1 | tee -a /var/log/pipeline.log
