#!/bin/bash
# Entrypoint: start SSH (if a key is provided) then run the supervisor forever.
set -e

if [ -n "${PUBLIC_KEY}" ]; then
  echo "${PUBLIC_KEY}" > /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  # generate host keys + start sshd in background
  ssh-keygen -A >/dev/null 2>&1 || true
  /usr/sbin/sshd >/dev/null 2>&1 || true
  echo "SSH debug enabled"
fi

echo "[entrypoint] starting supervisor"
exec python -u /app/supervisor.py
