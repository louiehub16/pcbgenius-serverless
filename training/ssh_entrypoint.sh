#!/bin/bash
# Entrypoint: start SSH (diagnostics) if a PUBLIC_KEY is provided, then run
# bootstrap (heavy deps + startup log to R2) and the master pipeline. Never exits.
set -u

# Optional SSH for exec-in diagnostics (no key = skip, still runs training)
if [ -n "${PUBLIC_KEY:-}" ]; then
  echo "${PUBLIC_KEY}" > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  ssh-keygen -A >/dev/null 2>&1 || true
  /usr/sbin/sshd >/dev/null 2>&1 || true
  echo "[entrypoint] SSH enabled for diagnostics"
fi

echo "[entrypoint] running bootstrap (deps + R2 startup log), then master pipeline..."
bash /bootstrap.sh || { echo "[entrypoint] bootstrap failed"; exit 1; }
echo "[entrypoint] bootstrap done — master pipeline will have run (master runs stages itself)"
# bootstrap.sh already invokes master_pipeline.sh at its end; keep this process alive
# long enough for SSH diagnostics / artifact upload, then the pod/container lifecycle
# handles termination. Loop to keep SSH reachable.
while true; do sleep 3600; done
