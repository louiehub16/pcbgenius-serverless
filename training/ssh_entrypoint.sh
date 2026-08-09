#!/bin/bash
# Entrypoint: start a /healthz HTTP server IMMEDIATELY so Salad's startup probe
# passes while the heavy torch+unsloth bootstrap installs (which takes ~10min).
# Then run SSH (diagnostics, optional), bootstrap (deps + master pipeline).
# Never exits — keeps the health endpoint alive for the container's lifetime.
set -u

# 1) Tiny health server on :8000 — responds before the slow install begins.
#    Salad's probe hits /healthz; we answer "ok:false" while loading, "ok:true"
#    once bootstrap + pipeline finish. Server stays up so liveness passes.
cat > /tmp/health.py <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        ready = "ok" if self.path in ("/healthz", "/") else "404"
        if ready == "404":
            self.send_response(404); self.end_headers(); return
        status = "ok" if os.path.exists("/opt/.pipeline_done") else "loading"
        body = ('{"status":"%s","service":"pcbgenius-training"}' % status).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
HTTPServer(("0.0.0.0", 8000), H).serve_forever()
PY
echo "[entrypoint] starting /healthz server..."
nohup python /tmp/health.py >/dev/null 2>&1 &
HEALTH_PID=$!
echo "[entrypoint] health server pid=$HEALTH_PID"

# 2) Optional SSH diagnostics
if [ -n "${PUBLIC_KEY:-}" ]; then
  echo "${PUBLIC_KEY}" > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  ssh-keygen -A >/dev/null 2>&1 || true
  /usr/sbin/sshd >/dev/null 2>&1 || true
  echo "[entrypoint] SSH enabled"
fi

# 3) Bootstrap: heavy deps + master pipeline. This prints the real stderr to the
#    R2 startup log (bootstrap.sh ships logs/training_*.log) so we can debug.
echo "[entrypoint] running bootstrap (heavy deps + master pipeline)..."
bash /bootstrap.sh
BOOT_RC=$?
touch /opt/.pipeline_done
if [ "$BOOT_RC" -eq 0 ]; then
  echo "[entrypoint] pipeline COMPLETE — shutting down to stop Salad billing."
  exit 0   # container exits -> Salad stops billing automatically
else
  echo "[entrypoint] pipeline FAILED (rc=$BOOT_RC) — staying alive for SSH/debug traces."
  while true; do sleep 3600; done   # keep alive ONLY on failure for diagnosis
fi