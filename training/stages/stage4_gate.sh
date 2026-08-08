#!/bin/bash
# STAGE 4 — RunPod tuned-model GATE (hard gate; halts pipeline if it fails)
# Per Master Plan: "Fireworks + Together GATEs REMOVED — hosting is RunPod only."
# This gate probes the deployed RunPod vLLM endpoint (RTX 6000 Ada, Qwen3-VL-32B)
# to confirm the model answers a tiny prompt before training continues.
set -euo pipefail
echo "[stage4] RunPod compatibility gate (qwen3-vl-32b)..."
python - <<'PY'
import os, sys, json, urllib.request
url = os.environ.get("RUNPOD_GATE_URL", "")
if not url:
    print("[stage4] ⏭ RUNPOD_GATE_URL not set — skipping gate (training proceeds).")
    sys.exit(0)
try:
    # OpenAI-compatible chat probe
    payload = {
        "model": "QuantTrio/Qwen3-VL-32B-Instruct-AWQ",
        "messages": [{"role": "user", "content": "Reply with the word: ok"}],
        "max_tokens": 8,
    }
    req = urllib.request.Request(url + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 Chrome/126.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = json.loads(r.read().decode())
        txt = body["choices"][0]["message"]["content"]
        print(f"[stage4] ✅ RunPod endpoint reachable, sample: {txt[:40]!r}")
except urllib.error.HTTPError as e:
    print(f"[stage4] ❌ HTTP {e.code}: {e.read().decode()[:300]}")
    print("[stage4] GATE FAILED — resolve RunPod endpoint before training.")
    sys.exit(1)
except Exception as e:
    print(f"[stage4] ❌ {e}"); sys.exit(1)
print("[stage4] GATE PASSED (RunPod hosting confirmed).")
PY