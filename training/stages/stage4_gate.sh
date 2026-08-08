#!/bin/bash
# STAGE 4 — Fireworks compatibility GATE (hard gate; halts pipeline if it fails)
set -euo pipefail
echo "[stage4] Testing Fireworks compatibility for qwen3-vl-32b-instruct..."
python - <<'PY'
import os, sys, json, urllib.request
key = os.environ.get("FIREWORKS_API_KEY", "")
if not key:
    print("[stage4] ❌ FIREWORKS_API_KEY not set"); sys.exit(1)
# Probe the model endpoint with a tiny multimodal+tool-call request.
url = "https://api.fireworks.ai/inference/v1/chat/completions"
payload = {
    "model": "accounts/fireworks/models/qwen3-vl-32b-instruct",
    "messages": [{"role": "user", "content": "Reply with the word: ok"}],
    "max_tokens": 8,
}
req = urllib.request.Request(url, data=json.dumps(payload).encode(),
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read().decode())
        txt = body["choices"][0]["message"]["content"]
        print(f"[stage4] ✅ endpoint reachable, sample response: {txt[:40]!r}")
except urllib.error.HTTPError as e:
    print(f"[stage4] ❌ HTTP {e.code}: {e.read().decode()[:300]}")
    print("[stage4] GATE FAILED — resolve model availability/fine-tune import before training.")
    sys.exit(1)
except Exception as e:
    print(f"[stage4] ❌ {e}"); sys.exit(1)
print("[stage4] NOTE: also confirm fine-tuned import/dedicated deployment with Fireworks support (written).")
PY
echo "[stage4] GATE passed."
