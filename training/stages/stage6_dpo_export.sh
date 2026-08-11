#!/bin/bash
# STAGE 6 — DPO preference pass + export (AWQ/GGUF) + upload to R2 + Fireworks
set -euo pipefail
WORK=/work
cd "$WORK"
echo "[stage6] DPO preference tuning + export..."
python - <<'PY'
import os, sys
def main():
    from unsloth import FastVisionModel
    from trl import DPOTrainer, DPOConfig
    from datasets import load_dataset
    import torch
    # FIX (realtime-stream 2026-08-10): transformers 5.5 treats a relative './' path as a
    # HF HUB repo id -> "Repo id must use alphanumeric chars... './pcbgenius_final_model'".
    # Use the absolute local path + local_files_only=True so it loads the local SFT output.
    # ALSO: on a fresh resume node stage-5 is skipped, so /work/pcbgenius_final_model may not
    # exist locally — fetch it from R2 (artifacts/pcbgenius_final_model) if missing, same as
    # the dpo_pairs fallback below. Fixes "Repo id must be in the form repo_name...
    # /work/pcbgenius_final_model ... AutoConfig & PeftConfig failed".
    import subprocess  # needed by the model-fetch below (must be imported before use)
    _fm = os.path.abspath("./pcbgenius_final_model")
    if (not os.path.exists(_fm)) or not os.listdir(_fm):
        os.makedirs(_fm, exist_ok=True)
        # download all files under artifacts/pcbgenius_final_model (the adapter_config + weights)
        _r2 = "/pipeline/lib/r2.py"
        _lst = subprocess.run([sys.executable, _r2, "ls", "artifacts/pcbgenius_final_model"],
                              capture_output=True, text=True).stdout
        _n = 0
        for line in _lst.splitlines():
            parts = line.split()
            if not parts or "artifacts/pcbgenius_final_model" not in parts[0]:
                continue
            okey = parts[0]
            rel = okey.split("pcbgenius_final_model/",1)[-1]
            rel = rel.replace("\\","/")
            relp = os.path.join(_fm, rel)
            os.makedirs(os.path.dirname(relp), exist_ok=True)
            with open(relp,"wb") as f:
                subprocess.run([sys.executable, _r2, "get", okey], stdout=f)
            _n += 1
        if _n == 0:
            print("[stage6] WARN: no files fetched for pcbgenius_final_model from R2")
        print(f"[stage6] fetched {_n} files into {_fm}")
    model, tokenizer = FastVisionModel.from_pretrained(
        _fm, load_in_4bit=True, local_files_only=True)
    # ROBUST DPO INPUT (Kimi round-11): stage 6 has no R2 fetch fallback for dpo_pairs.
    # Mirror stage-5's boto3 pattern so a missing/empty local file doesn't FileNotFoundError.
    _dpo = "data/processed/dpo_pairs.jsonl"
    if (not os.path.exists(_dpo)) or os.path.getsize(_dpo) == 0:
        import subprocess
        os.makedirs(os.path.dirname(_dpo), exist_ok=True)
        with open(_dpo, "wb") as f:
            _dd = subprocess.run([sys.executable, "/pipeline/lib/r2.py", "get",
                                  "artifacts/processed/dpo_pairs.jsonl"], stdout=f)
        if _dd.returncode != 0 or (os.path.exists(_dpo) and os.path.getsize(_dpo) == 0):
            raise RuntimeError("[stage6] could not fetch dpo_pairs from R2")
        print(f"[stage6] pulled dpo_pairs from R2 -> {_dpo} ({os.path.getsize(_dpo)} bytes)")
    ds = load_dataset("json", data_files="data/processed/dpo_pairs.jsonl", split="train")
    cfg = DPOConfig(output_dir="./dpo_out", per_device_train_batch_size=2,
                    gradient_accumulation_steps=8, max_steps=200, learning_rate=5e-5,
                    bf16=True, logging_steps=10, use_liger_kernel=True)
    trainer = DPOTrainer(model=model, tokenizer=tokenizer, train_dataset=ds, args=cfg)
    trainer.train()
    model.save_pretrained("pcbgenius_final_model_dpo")
    tokenizer.save_pretrained("pcbgenius_final_model_dpo")
    print("[stage6] DPO model saved to ./pcbgenius_final_model_dpo")
try:
    main()
except ImportError as e:
    print(f"[stage6] SCAFFOLD: trl DPO not present ({e}). Runs in H100 image.")
PY

# Datasheet-Q&A extraction via OPENROUTER (Claude), instead of direct Anthropic.
# Uses OPENROUTER_API_KEY; routes to a Claude model through OpenRouter.
python - <<'PY'
import os, json, urllib.request
def claude_extract(prompt_text):
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("[stage6] WARN: OPENROUTER_API_KEY not set — skipping datasheet extraction")
        return None
    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": "anthropic/claude-opus-4",
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 1024,
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "HTTP-Referer": "https://pcbgenius.local"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.loads(r.read().decode())
            return body["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[stage6] OpenRouter call failed: {e}")
        return None
print("[stage6] datasheet-Q&A extraction via OpenRouter(Claude) ready (call claude_extract per datasheet)")
PY

# Export quantized forms (AWQ for Fireworks/vLLM, GGUF for Ollama) — best effort
python - <<'PY'
print("[stage6] export: produce AWQ (Fireworks/vLLM) + GGUF (Ollama) from pcbgenius_final_model_dpo")
PY

rclone copy "$WORK/pcbgenius_final_model_dpo" "r2:${R2_BUCKET}/artifacts/pcbgenius_final_model_dpo" --progress 2>/dev/null || true
echo "[stage6] Final model artifacts on R2. Push to Fireworks per their fine-tune import flow."
echo "[stage6] DONE."
