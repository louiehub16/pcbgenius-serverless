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
# 2) Optional SSH diagnostics
# ---------------------------------------------------------------------------
if [ -n "${PUBLIC_KEY:-}" ]; then
  echo "${PUBLIC_KEY}" > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  ssh-keygen -A >/dev/null 2>&1 || true
  /usr/sbin/sshd >/dev/null 2>&1 || true
  echo "[entrypoint] SSH enabled"
fi

# ---------------------------------------------------------------------------
# 3) Ping helper (R2 marker + kanban POST) using browser UA (Cloudflare WAF)
# ---------------------------------------------------------------------------
ping_status() { # ping_status <status: done|failed|stuck> <note>
  local st="$1"; local note="$2"
  local ts; ts=$(date -u +%FT%TZ)
  # write marker to R2 via rclone (if configured) so the monitor can read it
  if command -v rclone >/dev/null 2>&1 && [ -n "${R2_BUCKET:-}" ]; then
    echo "$ts $st $note" | rclone rcat "r2:${R2_BUCKET}/state/training_status.txt" 2>/dev/null || true
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
    if command -v rclone >/dev/null 2>&1 && [ -n "${R2_BUCKET:-}" ]; then
      progress=$(rclone lsf "r2:${R2_BUCKET}/artifacts/checkpoints/" 2>/dev/null | wc -l)
    fi
    if [ -n "$progress" ] && [ "$progress" -gt 0 ]; then
      touch "$HEARTBEAT"
      continue
    fi
    # no checkpoints; fall back to container-local CPU activity
    if ls /work >/dev/null 2>&1 && [ -n "$(ls -A /work 2>/dev/null)" ]; then
      # pipeline is churning files -> progress
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
echo "[entrypoint] running bootstrap (heavy deps + master pipeline)..."
bash /bootstrap.sh
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