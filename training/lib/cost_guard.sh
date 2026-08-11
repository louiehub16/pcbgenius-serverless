#!/bin/bash
# ============================================================================
# COST GUARD — DISABLED (cap removed 2026-08-10 per user decision).
# We are no longer using the spend cap. The pipeline must NOT halt on spend.
# This file now ONLY keeps a cumulative cost LEDGER for visibility/reporting;
# it never exits non-zero and never halts training.
#
# Removed behavior (was): COST_CAP_USD hard-cap → `exit 90` halted the pipeline.
# Now: cost_check() always returns 0; cost_add() only records to the ledger so
# the running cost stays visible on R2 (state/.cost_ledger) and in cost_report.
# ============================================================================

COST_CAP_USD="${COST_CAP_USD:-0}"      # unused (cap disabled)
COST_ALERT_USD="${COST_ALERT_USD:-0}"  # unused (alert disabled)
WORK="${WORK:-/work}"
COST_FILE="${WORK}/.cost_ledger"
COST_R2_KEY="state/.cost_ledger"
_LEDGER_INIT=0

mkdir -p "$WORK" 2>/dev/null || true

# Lazy init: pull prior cumulative ledger from R2 for visibility continuity.
_cost_init() {
  [ "$_LEDGER_INIT" = "1" ] && return 0
  _LEDGER_INIT=1
  if command -v rclone >/dev/null 2>&1 && [ -n "${R2_BUCKET:-}" ]; then
    local _prior; _prior=$(rclone cat "r2:${R2_BUCKET}/${COST_R2_KEY}" 2>/dev/null || echo "")
    if [ -n "$_prior" ]; then echo "$_prior" > "$COST_FILE"; fi
  fi
  [ -f "$COST_FILE" ] || echo "0" > "$COST_FILE"
}

_cost_sync() {
  if command -v rclone >/dev/null 2>&1 && [ -n "${R2_BUCKET:-}" ]; then
    rclone copyto "$COST_FILE" "r2:${R2_BUCKET}/${COST_R2_KEY}" 2>/dev/null || true
  fi
}

# cost_add <usd> — record spend to the ledger ONLY. Never halts. Always returns 0.
cost_add() {
  _cost_init
  local amt="$1"
  local cur; cur=$(cat "$COST_FILE" 2>/dev/null || echo 0)
  local new; new=$(awk -v a="$cur" -v b="$amt" 'BEGIN{printf "%.2f", a+b}')
  echo "$new" > "$COST_FILE"
  _cost_sync
  reachable_cost_check "$new"
  return 0
}

# cost_check — cap DISABLED. Always returns 0 (never halts). Kept for API parity.
cost_check() {
  _cost_init
  return 0
}

# reachable_cost_check — cap DISABLED. Logs spend only; never halts.
reachable_cost_check() {
  local cur="${1:-$(cat "$COST_FILE" 2>/dev/null || echo 0)}"
  echo "💰 [cost-guard] cumulative spend so far: \$${cur} (cap disabled)"
  return 0
}

# cost_report — print current cumulative spend
cost_report() {
  _cost_init
  local cur; cur=$(cat "$COST_FILE" 2>/dev/null || echo 0)
  echo "💰 [cost-guard] cumulative spend so far: \$${cur} (cap disabled)"
}
