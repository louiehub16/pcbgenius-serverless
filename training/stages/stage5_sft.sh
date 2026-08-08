#!/bin/bash
# STAGE 5 — SFT fine-tune Qwen3-VL-32B (Unsloth + Liger + packing, checkpoints->R2)
set -euo pipefail
WORK=/work
cd "$WORK"
echo "[stage5] Starting SFT fine-tune on H100..."
python - <<'PY'
import os
# Full Unsloth QLoRA SFT recipe. Vision encoder frozen, LoRA on LLM layers.
# Accelerators: use_liger_kernel, packing, group_by_length, bf16, grad ckpt.
def main():
    from unsloth import FastVisionModel
    from trl import SFTTrainer
    from transformers import TrainingArguments
    from datasets import load_dataset
    import torch

    max_seq = int(os.environ.get("MAX_SEQ", "4096"))
    model, tokenizer = FastVisionModel.from_pretrained(
        "Qwen/Qwen3-VL-32B-Instruct",
        max_seq_length=max_seq,
        load_in_4bit=True,
    )
    model = FastVisionModel.get_peft_model(
        model, r=64, lora_alpha=32, lora_dropout=0.05, bias="none",
        target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
        use_gradient_checkpointing="unsloth",
    )
    ds = load_dataset("json", data_files="data/processed/verified_dataset.jsonl", split="train")
    args = TrainingArguments(
        output_dir="./checkpoints",
        per_device_train_batch_size=int(os.environ.get("BATCH", "4")),
        gradient_accumulation_steps=int(os.environ.get("GRAD_ACC", "8")),
        warmup_steps=50, max_steps=int(os.environ.get("MAX_STEPS", "1000")),
        learning_rate=2e-4, bf16=True, logging_steps=10,
        optim="adamw_8bit", weight_decay=0.01, lr_scheduler_type="cosine",
        save_steps=100, save_total_limit=3,
        use_liger_kernel=True, group_by_length=True,
    )
    trainer = SFTTrainer(model=model, tokenizer=tokenizer, train_dataset=ds,
                         dataset_text_field="text", max_seq_length=max_seq,
                         packing=True, args=args)
    print("[stage5] training (resume if checkpoint exists)...")
    trainer.train(resume_from_checkpoint=os.path.isdir("./checkpoints") and len(os.listdir("./checkpoints"))>0)
    model.save_pretrained("pcbgenius_final_model")
    tokenizer.save_pretrained("pcbgenius_final_model")
    print("[stage5] model saved to ./pcbgenius_final_model")

try:
    main()
except ImportError as e:
    print(f"[stage5] SCAFFOLD: unsloth/trl not present in this image ({e}).")
    print("[stage5] In the H100 image this runs the real QLoRA SFT.")
PY

# sync checkpoints + final model to R2 (crash-safe)
rclone copy "$WORK/checkpoints" "r2:${R2_BUCKET}/artifacts/checkpoints" --progress 2>/dev/null || true
rclone copy "$WORK/pcbgenius_final_model" "r2:${R2_BUCKET}/artifacts/pcbgenius_final_model" --progress 2>/dev/null || true
echo "[stage5] SFT artifacts synced to R2."
