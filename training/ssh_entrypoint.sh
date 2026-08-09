#!/bin/bash
# Entrypoint: start a /healthz HTTP server IMMEDIATELY so Salad's startup probe
# passes while the heavy torch+unsloth bootstrap installs (~10min). Then start
# SSH (diag, optional), run bootstrap (deps + master pipeline), and:
#   - PING completion/error to R2 + kanban when done.
#   - STUCK-DETECTOR: if no progress marker for STUCK_TIMEOUT_SEC, kill self so
#     Salad stops billing instead of hanging forever.
set -u

# ---------------------------------------------------------------------------
# 1) Tiny health server on :8000 (responds before the slow install).
# ---------------------------------------------------------------------------
cat > /tmp/health.py <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/healthz","/"):
            self.send_response(404); self.end_headers(); return
        status = "ok" if os.path.exists("/opt/.pipeline_done") else "loading"
        body = ('{"status":"%s","service":"pcbgenius-training"}' % status).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*a): pass
HTTPServer(("0.0.0.0",8000), H).serve_forever()
PY
echo "[entrypoint] starting /healthz server..."
nohup python /tmp/health.py >/dev/null 2>&1 &

# ---------------------------------------------------------------------------
# 1.5) OOM Protection Policy (RAM headroom check) + resilient R2 self-test
# ---------------------------------------------------------------------------
_TOTAL_RAM_GB=$(free -g 2>/dev/null | awk '/^Mem:/{print $2}' || echo "?")
echo "[entrypoint] host RAM ~${_TOTAL_RAM_GB}GB"
if [ "${_TOTAL_RAM_GB}" != "?" ] && [ "$_TOTAL_RAM_GB" -lt 8 ]; then
  echo "[entrypoint] WARNING low RAM (${_TOTAL_RAM_GB}GB) — reducing thread oversubscription"
  export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
fi
# Prove we can WRITE to R2 on this exact node BEFORE training writes anything.
if [ -n "${R2_BUCKET:-}" ]; then
  # boto3 is installed at Docker build time; defensive fallback in case not.
  python -c 'import boto3' 2>/dev/null || pip install --no-cache-dir boto3 >/dev/null 2>&1 || true
  printf 'ok %s\n' "$(date -u +%FT%TZ)" | python /pipeline/lib/r2.py put "state/conformance.txt" \
    && echo "[entrypoint] R2 write OK (boto3 path)" || echo "[entrypoint] WARNING R2 write FAILED"
fi

# ---------------------------------------------------------------------------
# 2) SSH diagnostics — operator key ALWAYS installed; env PUBLIC_KEY is additive.
# ---------------------------------------------------------------------------
mkdir -p /root/.ssh && chmod 700 /root/.ssh
: > /root/.ssh/authorized_keys
if [ -n "${PUBLIC_KEY:-}" ]; then
  printf '%s\n' "${PUBLIC_KEY}" >> /root/.ssh/authorized_keys
fi
# Baked operator trainer key (Kimi round-2 fix): so SSH works even if env key missing.
printf '%s\n' "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGOW6tMCiFKzR5mXrz8rWdNNeR45gXq7K4hs5UdEl8p3 John Doe@DESKTOP-QMTIMKT" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
chown -R root:root /root/.ssh 2>/dev/null || true   # StrictModes safety
mkdir -p /run/sshd && chmod 755 /run/sshd            # privsep dir (absent -> sshd rc=255)
ssh-keygen -A >/dev/null 2>&1 || true
mkdir -p /etc/ssh/sshd_config.d
printf 'PermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/99-root.conf 2>/dev/null || true
/usr/sbin/sshd -D -e > /tmp/sshd.log 2>&1 &
sleep 2
if ! pgrep -x sshd >/dev/null 2>&1; then
  echo "[entrypoint] WARNING sshd not running:"; head -20 /tmp/sshd.log 2>/dev/null
else
  echo "[entrypoint] SSH enabled"
fi

# ---------------------------------------------------------------------------
# 3) Ping helper (R2 marker + kanban POST) using browser UA (Cloudflare WAF)
# ---------------------------------------------------------------------------
ping_status() { # ping_status <status: done|failed|stuck> <note>
  local st="$1"; local note="$2"
  local ts; ts=$(date -u +%FT%TZ)
  # write marker to R2 via boto3 helper (rclone rcat/copyto -> AccessDenied; boto3 works)
  if [ -n "${R2_BUCKET:-}" ]; then
    printf '%s %s %s\n' "$ts" "$st" "$note" | python /pipeline/lib/r2.py put "state/training_status.txt" 2>/dev/null || true
  fi
  # POST to kanban (browser UA required - Cloudflare returns 403/1010 otherwise)
  if [ -n "${KANBAN_URL:-}" ]; then
    curl -s -X POST "${KANBAN_URL}" -H "Content-Type: application/json" \
      -H "User-Agent: Mozilla/5.0 Chrome/126.0" \
      --data "{\"agent\":\"trainer\",\"status\":\"$st\",\"feature\":\"training\",\"message\":\"$note\"}" >/dev/null 2>&1 || true
  fi
  echo "[entrypoint] PING $st: $note" >&2
}

# ---------------------------------------------------------------------------
# 3.5) HEARTBEAT daemon: burn a liveness tick to R2 state/heartbeat.txt every
#      30s so an operator can confirm from outside that the pipeline is advancing
#      (not blind). Uses boto3 r2.py (proven to write to R2; rclone got AccessDenied).
# ---------------------------------------------------------------------------
(
  while true; do
    _ts=$(date -u +%FT%TZ)
    _st=$(cat /work/pipeline_state.txt 2>/dev/null || echo "?")
    _co=$(cat /work/.cost_ledger 2>/dev/null || echo "0")
    # ALSO capture the last pipeline-log lines so the operator sees exactly where
    # master is (or the last error) even if it's mid-stage / crashed. This closes
    # the "alive but blind" gap (continuous visibility, not just stage boundaries).
    _plast=""
    if [ -f /var/log/pipeline.log ]; then
      _plast=$(tail -c 1500 /var/log/pipeline.log 2>/dev/null | tr '\n' '|')
    fi
    { printf '%s stage=%s cost=%s alive\n' "$_ts" "$_st" "$_co";
      [ -n "$_plast" ] && printf 'PLOG: %s\n' "$_plast";
    } | python /pipeline/lib/r2.py put "state/heartbeat.txt" 2>/dev/null || true
    sleep 30
  done
) &
HB_PID=$!

# ---------------------------------------------------------------------------
# 4) STUCK-DETECTOR watchdog: monitors a progress heartbeat. If model weights
#    or checkpoints stop appearing on R2 for STUCK_TIMEOUT_SEC, kill self so
#    Salad stops billing (no unbounded hang + bill).
#    - Writes a heartbeat file whenever progress is detected (stats).
#    - On timeout without progress -> ping stuck -> exit non-zero -> Salad stops.
# ---------------------------------------------------------------------------
STUCK_TIMEOUT_SEC="${STUCK_TIMEOUT_SEC:-900}"   # 15 min default
HEARTBEAT="/tmp/last_progress"
touch "$HEARTBEAT"   # start the clock now (bootstrap is progress)
(
  while true; do
    sleep 60
    # progress = any checkpoint/model files appeared on R2 (or local /work)
    progress=""
    if [ -n "${R2_BUCKET:-}" ]; then
      progress=$(python /pipeline/lib/r2.py ls "artifacts/checkpoints/" 2>/dev/null | wc -l)
    fi
    if [ -n "$progress" ] && [ "$progress" -gt 0 ]; then
      touch "$HEARTBEAT"
      continue
    fi
    # no R2 checkpoints; use a RECENCY test on /work, NOT mere non-emptiness.
    # (Auto-resume under section 5.0 guarantees /work is non-empty from the first
    # poll, so `ls -A /work` would touch the heartbeat forever and the stuck-abort
    # could never fire -> unbounded billing. Kimi re-review 2026-08-09.)
    if [ -d /work ] && find /work -type f -mmin -10 2>/dev/null | head -1 | grep -q .; then
      touch "$HEARTBEAT"; continue
    fi
    # any elapsed > timeout with no progress -> stuck
    if [ -f "$HEARTBEAT" ] && [ $(( $(date +%s) - $(stat -c %Y "$HEARTBEAT" 2>/dev/null || date +%s) )) -ge "$STUCK_TIMEOUT_SEC" ]; then
      ping_status "stuck" "no progress for ${STUCK_TIMEOUT_SEC}s - aborting to stop billing"
      echo "[entrypoint] STUCK-DETECTOR: no progress ${STUCK_TIMEOUT_SEC}s -> terminating"
      # use a sub-shell to send SIGTERM to our own PID so sshd/health end too
      ( kill -TERM $$ 2>/dev/null; sleep 2; kill -KILL $$ 2>/dev/null ) &
      exit 1
    fi
  done
) &
WATCHDOG_PID=$!

# ---------------------------------------------------------------------------
# 5) Bootstrap: heavy deps + master pipeline (ships logs/training_*.log to R2)
# ---------------------------------------------------------------------------
# 5.0) RESILIENT AUTO-RESUME: pull prior artifacts/data so an interrupted run
#      resumes from its last checkpointed state instead of starting fresh.
if [ -n "${R2_BUCKET:-}" ]; then
  echo "[entrypoint] auto-resume: syncing prior artifacts/data/state from R2..."
  python /pipeline/lib/r2.py ls "artifacts/data/" 2>/dev/null | head -20 || true
  # pull data dir (rclone read works even though write was AccessDenied)
  command -v rclone >/dev/null 2>&1 && mkdir -p /work && \
    rclone copy "r2:${R2_BUCKET}/artifacts/data" /work/data --exclude "*.DS_Store" 2>/dev/null || true
  python /pipeline/lib/r2.py get "state/pipeline_state.txt" 2>/dev/null > /work/pipeline_state_r2.txt && \
    echo "[entrypoint] last stage on R2: $(cat /work/pipeline_state_r2.txt)" || \
    echo "[entrypoint] no prior pipeline state — fresh run"
fi
echo "[entrypoint] running bootstrap (install deps) ..."
bash /bootstrap.sh
echo "[entrypoint] starting master pipeline..."
bash /pipeline/master_pipeline.sh
BOOT_RC=$?
touch /opt/.pipeline_done

if [ "$BOOT_RC" -eq 0 ]; then
  ping_status "done" "pipeline complete; model on R2"
  echo "[entrypoint] pipeline COMPLETE - shutting down"
  exit 0           # exit -> Salad stops billing
else
  ping_status "failed" "pipeline failed rc=${BOOT_RC}"
  echo "[entrypoint] pipeline FAILED (rc=$BOOT_RC) - staying alive for SSH/debug"
  while true; do sleep 3600; done   # keep alive on FAILURE for diagnosis
fi