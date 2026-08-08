#!/bin/bash
# ============================================================================
# COST GUARD — auto-halts the pipeline if estimated Salad spend crosses a cap.
# Sourced by master_pipeline.sh. Reads cost checkpoints written by each stage.
#
# Config via env:
#   COST_CAP_USD   — hard cap; pipeline halts when estimated spend exceeds it.
#                    Default 90 (your $111 deposit with headroom).
#   COST_ALERT_USD — soft alert threshold (log a warning). Default 75.
# ============================================================================

COST_CAP_USD="${COST_CAP_USD:-90}"
COST_ALERT_USD="${COST_ALERT_USD:-75}"
WORK="${WORK:-/work}"
COST_FILE="${WORK}/.cost_ledger"
# R2 location for cumulative ledger (survives fresh-node resumes)
COST_R2_KEY="state/.cost_ledger"
_LEDGER_INIT=0

mkdir -p "$WORK" 2>/dev/null || true

# Lazy init: on first cost operation, pull prior cumulative ledger from R2.
# Deferred because rclone is configured by master AFTER this file is sourced.
_cost_init() {
  [ "$_LEDGER_INIT" = "1" ] && return 0
  _LEDGER_INIT=1
  if command -v rclone >/dev/null 2>&1 && [ -n "${R2_BUCKET:-}" ]; then
    local _prior; _prior=$(rclone cat "r2:${R2_BUCKET}/${COST_R2_KEY}" 2>/dev/null || echo "")
    if [ -n "$_prior" ]; then echo "$_prior" > "$COST_FILE"; fi
  fi
  [ -f "$COST_FILE" ] || echo "0" > "$COST_FILE"
}

# Persist ledger to R2 (best-effort; cumulative across resumes)
_cost_sync() {
  if command -v rclone >/dev/null 2>&1 && [ -n "${R2_BUCKET:-}" ]; then
    rclone copyto "$COST_FILE" "r2:${R2_BUCKET}/${COST_R2_KEY}" 2>/dev/null || true
  fi
}

# cost_add <usd>  — call after each billed unit of work
cost_add() {
  _cost_init
  local amt="$1"
  local cur; cur=$(cat "$COST_FILE" 2>/dev/null || echo 0)
  local new; new=$(awk -v a="$cur" -v b="$amt" 'BEGIN{printf "%.2f", a+b}')
  echo "$new" > "$COST_FILE"
  _cost_sync
  cost_check "$new"
}

# cost_check [current]  — halt if over cap, warn if over alert
cost_check() {
  local cur="${1:-$(cat "$COST_FILE" 2>/dev/null || echo 0)}"
  awk -v c="$cur" -v cap="$COST_CAP_USD" 'BEGIN{exit !(c+0>cap+0)}' && {
    echo "🛑 [cost-guard] SPEND \$${cur} EXCEEDED CAP \$${COST_CAP_USD} — halting pipeline."
    echo "🛑 [cost-guard] State is checkpointed to R2; top up + re-run to resume."
    exit 90
  }
  awk -v c="$cur" -v a="$COST_ALERT_USD" 'BEGIN{exit !(c+0>a+0)}' && {
    echo "⚠️  [cost-guard] spend \$${cur} crossed alert threshold \$${COST_ALERT_USD}"
  }
  return 0
}

# cost_report — print current estimated spend
cost_report() {
  _cost_init
  local cur; cur=$(cat "$COST_FILE" 2>/dev/null || echo 0)
  echo "💰 [cost-guard] estimated Salad spend so far: \$${cur} (cap \$${COST_CAP_USD})"
}
