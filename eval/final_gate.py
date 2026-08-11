#!/usr/bin/env python3
"""
PCBGenius FINAL-MODEL GATE — automated inference + benchmark with Kimi-as-judge.

Purpose: after training completes, prove the fine-tuned model is actually GOOD.
   1. Load the fine-tuned LoRA adapter (adir) merged onto the base (or the saved model dir).
   2. Hold out a sample of REAL verified netlist prompts (NOT used to judge memorization — we
      use the verified dataset's prompts but the value is generalization on fresh held-out prompts;
      by default sample prompts the model did not train on, from a reserved holdout set).
   3. Run inference: prompt -> model netlist text.
   4. Structural self-check: parse the generated netlist; check it's valid JSON with nets/components,
      components have pins, nets referenced exist, no obviously-broken dangling pins.
   5. Kimi K3 judges each (prompt, ground_truth, model_output): PASS/FAIL + reason (quality,
      correctness of the netlist, plausibility). Kimi is the quality judge (rounds 13+).
   6. Report a benchmark table + pass rate.

Used as the final gate after the SFT/DPO model is produced (files on /work volume + R2
artifacts/pcbgenius_final_model[_dpo]). Runs inside the training container (has torch+unsloth)
or on a Modal/Hyperstack GPU with the image. No file modifications to training source.

Usage:
  python final_gate.py --model ./pcbgenius_final_model_dpo \
        [--base Qwen/Qwen3-VL-32B-Instruct] [--holdout N] [--sample K] \
        [--out final_gate_report.md]           # --dry-run: prep only, no inference
"""
import argparse, json, os, re, sys, random, urllib.request

KIMI_KEY = os.environ.get("OPENROUTER_API_KEY", "")
KIMI_MODEL = "moonshotai/kimi-k3"

def kimi_judge(prompt, truth, output):
    """Ask Kimi K3 to judge the model output for a PCB netlist prompt."""
    if not KIMI_KEY:
        return {"verdict": "SKIP", "reason": "no OPENROUTER_API_KEY"}
    sys_msg = ("You are Kimi K3, a senior PCB/schematic engineer judge. Given a PCB design "
               "prompt, the ground-truth netlist, and a model-generated netlist, judge if the "
               "model output is correct/plausible. Reply STRICT JSON only: "
               "{\"verdict\":\"PASS\"|\"FAIL\",\"reason\":\"<one line>\"}")
    user = json.dumps({"prompt": prompt, "ground_truth": truth, "model_output": output})[:8000]
    body = {"model": KIMI_MODEL,
            "messages":[{"role":"system","content":sys_msg},{"role":"user","content":user}],
            "max_tokens":500, "temperature":0.2}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KIMI_KEY}", "Content-Type":"application/json",
                 "User-Agent":"Mozilla/5.0 Chrome/126.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            c = json.loads(r.read().decode())["choices"][0]["message"].get("content","")
        c = c.strip()
        if c.startswith("```"):  # strip fences
            c = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", c).strip()
        j = json.loads(c)
        return j
    except Exception as e:
        return {"verdict":"ERR","reason":str(e)[:120]}

def parse_netlist_text(txt):
    """Try to extract a JSON netlist object from text output."""
    # if it's already a json object/array, parse directly
    try:
        o = json.loads(txt); return o
    except Exception:
        pass
    # find {...} block
    m = re.search(r"\{.*\}", txt, re.S)
    if m:
        try: return json.loads(m.group(0))
        except Exception: return None
    return None

def structural_check(o):
    """Real PCBGenius schema: nets=[{name,pins,class}...], components=[{ref,type,value,
    pins:[{number,name,net}...]}...]. Validate: components present with pins, each pin's
    'net' references a defined net name (unless null/None = open/floating)."""
    if not isinstance(o, dict): return False, "not a JSON object"
    nets = o.get("nets", []); comps = o.get("components", [])
    if not isinstance(nets, list): return False, "nets not a list"
    if not isinstance(comps, list) or not comps: return False, "missing/empty components"
    # net names defined
    known = set()
    for n in nets:
        name = n.get("name") if isinstance(n, dict) else (n if isinstance(n, str) else None)
        if name: known.add(name)
    if not known: return False, "no net names defined"
    ncomp = len(comps); npins = 0; dangling = 0
    for c in comps:
        if not isinstance(c, dict): return False, "component not object"
        pins = c.get("pins")
        if pins is None: return False, f"component {c.get('ref', c.get('mpn', '?'))} missing pins"
        if not isinstance(pins, list): return False, "component pins not a list"
        for p in pins:
            if isinstance(p, dict):
                npins += 1
                nv = p.get("net")
                if nv is not None and nv not in known:
                    dangling += 1
            # string pin refs like "U1.VIN" — accept if component ref present
            elif isinstance(p, str):
                npins += 1
    if npins == 0: return False, "no pins connected"
    return dangling == 0, f"ok ({len(known)} nets, {ncomp} comps, {npins} pins, {dangling} dangling)"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--base", default="Qwen/Qwen3-VL-32B-Instruct")
    ap.add_argument("--holdout", type=int, default=200, help="top-N verified prompts as holdout candidates")
    ap.add_argument("--sample", type=int, default=10, help="how many to benchmark")
    ap.add_argument("--out", default="final_gate_report.md")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--verified", default="artifacts/processed/verified_dataset.jsonl")
    args = ap.parse_args()

    import torch
    from unsloth import FastVisionModel
    print(f"[final-gate] device cuda={torch.cuda.is_available()}")

    # ---------- 0. load model ----------
    if args.dry_run:
        print("[final-gate] DRY-RUN: model load + prompt prep only (no inference)")
    else:
        print(f"[final-gate] loading model {args.model} (base {args.base})")
        model, tok = FastVisionModel.from_pretrained(
            os.path.abspath(args.model), device_map="auto", load_in_4bit=True, local_files_only=False)
        FastVisionModel.for_inference(model)

    # ---------- 1. holdout prompts (fresh, reserved) ----------
    # Verified dataset shard; reserve a holdout slice. Use the R2-fetch path (the generator's).
    import subprocess, tempfile
    vf = os.path.join(tempfile.gettempdir(), "hg_verified.jsonl")
    with open(vf,"wb") as f:
        r = subprocess.run([sys.executable,"/pipeline/lib/r2.py","get",args.verified], stdout=f)
    rows=[]
    if os.path.exists(vf) and os.path.getsize(vf)>0:
        for line in open(vf,encoding="utf-8"):
            line=line.strip()
            if line: rows.append(json.loads(line))
    rnd = random.Random(args.seed)
    if len(rows)>args.holdout:
        rows = rows[-args.holdout:]  # tail = reserved holdout (not in the training head slice)
    prompts = [{"prompt": e.get("prompt",""), "truth": (e.get("netlist") if isinstance(e.get("netlist"),str) else json.dumps(e.get("netlist"),sort_keys=True))} for e in rows if e.get("prompt")]
    sample = rnd.sample(prompts, min(args.sample, len(prompts)))
    print(f"[final-gate] holdout pool={len(prompts)} sampled={len(sample)}")

    report = []
    if args.dry_run:
        print("[final-gate] dry-run: ready. Would run inference on", len(sample), "prompts.")
        # still do structural check on ground truths to validate harness
        ok=0
        for s in sample:
            o=parse_netlist_text(s["truth"])
            good,_=structural_check(o) if o else (False,"n/a")
            ok += bool(good)
        print(f"[final-gate] dry-run: ground-truth structural pass {ok}/{len(sample)}")
        return

    # ---------- 2. run + judge ----------
    import torch
    pass_kimi=pass_struct=total=0
    for s in sample:
        msgs=[{"role":"user","content":s["prompt"]}]
        toks = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True, return_tensors="pt").to("cuda")
        out = model.generate(input_ids=toks, max_new_tokens=1024, temperature=0.8, do_sample=True)
        text = tok.decode(out[0][toks.shape[1]:], skip_special_tokens=True)
        obj = parse_netlist_text(text)
        good, reason = structural_check(obj) if obj else (False,"unparseable")
        pass_struct += 1 if good else 0
        j = kimi_judge(s["prompt"], s["truth"], text)
        pass_kimi += 1 if j.get("verdict")=="PASS" else 0
        total += 1
        report.append({"prompt": s["prompt"][:80], "structural": good, "struct_reason": reason,
                       "kimi": j.get("verdict"), "kimi_reason": j.get("reason","")})
        print(f"  [{total}] struct={good} kimi={j.get('verdict')} ({s['prompt'][:50]}...)")

    # ---------- 3. report ----------
    lines = ["# PCBGenius Final-Model Gate Report", ""]
    lines.append(f"- Model: {args.model}")
    lines.append(f"- Sampled: {total} held-out prompts")
    lines.append(f"- **Structural pass rate: {pass_struct}/{total} ({pass_struct/max(total,1)*100:.0f}%)**")
    lines.append(f"- **Kimi-judged pass rate: {pass_kimi}/{total} ({pass_kimi/max(total,1)*100:.0f}%)**")
    lines.append("")
    lines.append("| # | prompt | struct | kimi | reason |")
    lines.append("|---|--------|--------|------|--------|")
    for i,r in enumerate(report,1):
        lines.append(f"| {i} | {r['prompt']} | {r['structural']} | {r['kimi']} | {r.get('kimi_reason','')} |")
    open(args.out,"w",encoding="utf-8").write("\n".join(lines))
    print(f"[final-gate] report written: {args.out}")
    print(f"[final-gate] GATE: structural {pass_struct}/{total} | kimi {pass_kimi}/{total}")

if __name__ == "__main__":
    main()
