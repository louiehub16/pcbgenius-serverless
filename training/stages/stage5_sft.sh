#!/bin/bash
# STAGE 5 — SFT fine-tune Qwen3-VL-32B (Unsloth + Liger + packing, checkpoints->R2)
set -euo pipefail
WORK=/work
cd "$WORK"
echo "[stage5] Starting SFT fine-tune on H100..."
python - <<'PY'
import os, sys
sys.path.insert(0, "/pipeline/lib")
import train_loop
# OPTIMIZATIONS (2026-08-09): CUDA/CPU env caps set in Dockerfile; here we add the
# eviction-safe, atomic, resumable checkpoint + async upload + VRAM/OOM guards.
train_loop.arm_eviction_handlers()          # Salad SIGTERM -> emergency save
train_loop.oom_headroom_check(min_free_mem_gb=4.0)
train_loop.start_async_uploader()

def main():
    from unsloth import FastVisionModel
    from trl import SFTTrainer
    from transformers import TrainingArguments
    from datasets import load_dataset
    import torch

    max_seq = min(int(os.environ.get("MAX_SEQ", "2048")), 2048)
    free_gb = train_loop.vram_safety_sweep(min_free_gb=2.0)
    print(f"[stage5] VRAM free {free_gb:.2f}GB — proceeding at low-VRAM bs=1")
    # Model id configurable: default 32B (for 80GB A100/H100 on Hyperstack). On the 32GB
    # RTX 5090 (Salad validation) use a smaller model — 32B QLoRA provably can't fit 32GB.
    model_id = os.environ.get("MODEL_NAME", "Qwen/Qwen3-VL-8B-Instruct")

    model, tokenizer = FastVisionModel.from_pretrained(
        model_id,
        max_seq_length=max_seq,
        load_in_4bit=True,
        device_map="auto",
    )
    model = FastVisionModel.get_peft_model(
        model, r=64, lora_alpha=32, lora_dropout=0.05, bias="none",
        target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
        use_gradient_checkpointing="unsloth",
    )

    # Prefer the verified dataset if stage3 produced it, else the raw training set.
    # (NOTE: do NOT `import os.path` here — it makes `os` a function-local var and
    #  breaks the earlier `os.environ` reads with UnboundLocalError. os is module-level.)
    # ROBUST INPUT FETCH (realtime-stream 2026-08-10): stage 3 may be skipped on resume,
    # and auto-resume's rclone copy of artifacts/processed can silently fail (the flaky
    # path), leaving /work/data/processed empty -> datasets FileNotFoundError. Fetch the
    # training input via boto3 r2.py if missing, exactly the proven stage-3 pattern.
    _inp = "data/processed/pcbgenius_training_dataset.jsonl"
    if (not os.path.exists(_inp)) or os.path.getsize(_inp) == 0:
        import subprocess
        os.makedirs(os.path.dirname(_inp), exist_ok=True)
        with open(_inp, "wb") as f:
            _rd = subprocess.run([sys.executable, "/pipeline/lib/r2.py", "get",
                                  "artifacts/processed/pcbgenius_training_dataset.jsonl"], stdout=f)
        if _rd.returncode != 0 or (os.path.exists(_inp) and os.path.getsize(_inp) == 0):
            raise RuntimeError("[stage5] could not fetch training input from R2")
        print(f"[stage5] pulled training input from R2 -> {_inp} ({os.path.getsize(_inp)} bytes)")
    # Prefer verified dataset, but ALSO size-check (an empty verified file, the original
    # rclone silent-failure cause, must not slip through) and fetch it via boto3 if empty.
    # (Kimi round-10 MEDIUM)
    src = "data/processed/verified_dataset.jsonl"
    if (not os.path.exists(src)) or (os.path.getsize(src) == 0):
        import subprocess
        if os.path.exists(os.path.dirname(src)):
            with open(src, "wb") as f:
                _vrd = subprocess.run([sys.executable, "/pipeline/lib/r2.py", "get",
                                       "artifacts/processed/verified_dataset.jsonl"], stdout=f)
                if _vrd.returncode == 0 and os.path.getsize(src) > 0:
                    print(f"[stage5] pulled verified dataset from R2 -> {src} ({os.path.getsize(src)} bytes)")
                else:
                    src = _inp  # fall back to the fetched training input
        else:
            src = _inp
    ds = load_dataset("json", data_files=src, split="train")
    # CRITICAL (Kimi round-10): dataset columns are {prompt, netlist, skill} — there is NO
    # "text" column, but SFTTrainer uses dataset_text_field="text" -> KeyError at construct.
    # Build a "text" column with an EOS terminator (required because packing=True).
    tok_eos = tokenizer.eos_token or ""
    ds = ds.map(lambda ex: {"text": f"{ex.get('prompt','')}\n{ex.get('netlist','')}{tok_eos}"})
    print(f"[stage5] dataset loaded: {len(ds)} rows from {src} (columns: {ds.column_names})")

    args = TrainingArguments(
        output_dir="./checkpoints",
        per_device_train_batch_size=1,          # low-VRAM: bs=1
        gradient_accumulation_steps=int(os.environ.get("GRAD_ACC", "16")),
        warmup_steps=25, max_steps=int(os.environ.get("MAX_STEPS", "600")),
        learning_rate=2e-4, bf16=True, fp16=False,
        logging_steps=10, optim="adamw_8bit", weight_decay=0.01,
        lr_scheduler_type="cosine", save_steps=100, save_total_limit=3,
        use_liger_kernel=True,
    )
    trainer = SFTTrainer(model=model, tokenizer=tokenizer, train_dataset=ds,
                         dataset_text_field="text", max_seq_length=max_seq,
                         packing=True, args=args)

    # Eviction-graceful: on SIGTERM (Salad preempt), emergency-save a checkpoint
    # with bundled seeds so the next resume continues where it stopped.
    def _emergency_save():
        try:
            ck = os.path.join("./checkpoints", f"evict_step{int(trainer.state.global_step)}")
            trainer.save_model(ck)
            train_loop.save_atomic_checkpoint(
                {"step": trainer.state.global_step, **train_loop.bundle_seeds()},
                "./checkpoints", name="resume_state.pt")
            print(f"[stage5] emergency checkpoint saved at step {trainer.state.global_step}", flush=True)
        except Exception as e:
            print(f"[stage5] emergency save FAILED: {e}", flush=True)
    train_loop.register_eviction_handler(_emergency_save)

    print("[stage5] training (resume if checkpoint exists)...")
    # Only resume if a REAL trainer checkpoint dir (checkpoint-*) exists. Checking
    # mere dir-non-empty is wrong: the emergency-save path leaves resume_state.pt /
    # evict_stepN (which are NOT resumable) -> resume_from_checkpoint=True would raise
    # ValueError "Can't find a valid checkpoint" (Kimi round-9). Use glob for checkpoint-*.
    import glob
    ckdirs = glob.glob("./checkpoints/checkpoint-*")
    resume = len(ckdirs) > 0
    if resume:
        print(f"[stage5] resuming from {ckdirs[-1]}")
    trainer.train(resume_from_checkpoint=resume)
    # stop async uploader then do one final atomic save
    train_loop.stop_async_uploader()
    model.save_pretrained("pcbgenius_final_model")
    tokenizer.save_pretrained("pcbgenius_final_model")
    print("[stage5] model saved to ./pcbgenius_final_model")

try:
    main()
except ImportError as e:
    print(f"[stage5] SCAFFOLD: unsloth/trl not present in this image ({e}).")
    print("[stage5] In the GPU image this runs the real QLoRA SFT.")
PY

# sync checkpoints + final model to R2 via boto3 helper (rclone write -> AccessDenied)
# LOUD (no `|| true` / 2>/dev/null): a silent swallow here lost the whole SFT model once
# (old A100 was preempted, model never persisted). Fail loudly if it can't upload.
python /pipeline/lib/r2.py syncLocalDir "$WORK/checkpoints" "artifacts/checkpoints"
python /pipeline/lib/r2.py syncLocalDir "$WORK/pcbgenius_final_model" "artifacts/pcbgenius_final_model"
echo "[stage5] SFT artifacts synced to R2 (loud)."
