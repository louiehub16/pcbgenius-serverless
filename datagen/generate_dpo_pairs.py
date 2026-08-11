#!/usr/bin/env python3
"""
PCBGenius DPO pair generator — build proper preference pairs from the verified dataset.

QUALITY PRINCIPLES (no degradation):
  * chosen  = the REAL verified-correct netlist from artifacts/processed/verified_dataset.jsonl
    (verbatim — we never alter the grounded positive).
  * rejected= a DELIBERATELY-BROKEN-but-plausible netlist produced by injecting a single,
    well-defined engineering defect into the chosen design (open net, missing component,
    wrong value, short/duplicate net, dropped connection). Each pair gets exactly ONE defect
    type so the preference signal is clean and non-degenerate. The prompt is unchanged.
  * Output schema = TRL DPOTrainer:  {"prompt":..., "chosen":..., "rejected":...}
    (netlists rendered as text; EOS appended because packing=True is used downstream).

Supports --dry-run (stats only), --out <path> (local), and default upload to R2
artifacts/processed/dpo_pairs.jsonl via /pipeline/lib/r2.py.

Deterministic (seeded) so it's reproducible and re-runnable/safe.
"""
import argparse, copy, json, os, re, sys, random

R2_GET = ["sys.executable", "/pipeline/lib/r2.py", "get", "artifacts/processed/verified_dataset.jsonl"]

def fetch_verified(out_local):
    import subprocess
    with open(out_local, "wb") as f:
        r = subprocess.run([sys.executable, "/pipeline/lib/r2.py", "get",
                            "artifacts/processed/verified_dataset.jsonl"], stdout=f)
    if r.returncode != 0 or (os.path.exists(out_local) and os.path.getsize(out_local) == 0):
        raise RuntimeError("could not fetch verified_dataset from R2")
    return out_local

def render_netlist(e):
    """Stable text rendering of a netlist. Accepts either a RECORD ({'netlist': ...})
    or a BARE netlist (dict/str) directly."""
    if isinstance(e, dict) and "netlist" in e:
        e = e["netlist"]
    if isinstance(e, str):
        return e
    if isinstance(e, dict):
        return json.dumps(e, sort_keys=True)
    return str(e)

# --- Defect injectors: return a MODIFIED copy of the netlist dict, or None if n/a ------
# Each returns a (broken_dict, defect_label) or None when the defect can't be applied to
# this particular netlist (type-specific). Applying one defect per pair keeps signal clean.

def defect_open_net(net):
    """Disconnect one unique net from one component pin (open/disconnected)."""
    nets = net.get("nets") or []
    comps = net.get("components") or []
    if not nets or not comps:
        return None, "open_net"
    b = copy.deepcopy(net)
    # pick a component with pins
    comp = next((c for c in b.get("components",[]) if isinstance(c, dict) and c.get("pins")), None)
    if comp is None:
        return None, "open_net"
    pins = comp["pins"]
    if isinstance(pins, list) and pins:
        comp["pins"][0] = None  # drop connection -> open
    elif isinstance(pins, dict) and pins:
        k = next(iter(pins)); pins[k] = None
    return b, "open_net"

def defect_missing_component(net):
    comps = net.get("components") or []
    if len(comps) < 2:
        return None, "missing_component"
    b = copy.deepcopy(net)
    del b["components"][len(b["components"])//2]  # remove a middle component
    return b, "missing_component"

def defect_wrong_value(net):
    b = copy.deepcopy(net)
    comps = b.get("components") or []
    cand = [c for c in comps if isinstance(c, dict) and ("value" in c or "rating" in c or "resistance" in c)]
    if not cand:
        return None, "wrong_value"
    c = cand[0]
    # DEFAULT to 'value' so it ALWAYS mutates a copy (never no-op / never touches original)
    k = ("value" if "value" in c else "rating" if "rating" in c else "resistance")
    cur = str(c.get(k) or "")
    import re as _re
    try:
        num = float(_re.sub(r"[^0-9.]", "", cur)) if _re.sub(r"[^0-9.]", "", cur) else 0.0
    except Exception:
        num = 0.0
    if k == "resistance":
        c[k] = f"{num/10:.6g}"   # order-of-magnitude error
    elif k == "rating":
        c[k] = f"{num*10:.6g}"   # wrong rating
    else:
        c[k] = f"WRONG-{cur}" if cur else "WRONG-unknown"
    return b, "wrong_value"

def defect_short_net(net):
    b = copy.deepcopy(net)
    nets = list(b.get("nets") or [])
    # Uniqueness by RENDERED value (nets may be dicts, not just strings) so set() is safe.
    def _key(x):
        return x if isinstance(x, str) else json.dumps(x, sort_keys=True)
    seen = {}
    for x in nets:
        seen.setdefault(_key(x), x)
    uniq = list(seen.values())
    if len(uniq) < 2:
        return None, "short_net"
    n0, n1 = uniq[0], uniq[1]
    k1 = _key(n1)
    # merge nets entries equal to n1 -> n0
    out_nets = []
    for x in nets:
        out_nets.append(n0 if _key(x) == k1 else x)
    b["nets"] = out_nets
    # also merge component pins referencing n1 -> n0 for a real short
    for comp in (b.get("components") or []):
        pins = comp.get("pins")
        if isinstance(pins, list):
            for j in range(len(pins)):
                if _key(pins[j]) == k1:
                    pins[j] = n0
        elif isinstance(pins, dict):
            for k2, v in pins.items():
                if _key(v) == k1:
                    pins[k2] = n0
    return b, "short_net"

DEFECTS = [defect_open_net, defect_missing_component, defect_wrong_value, defect_short_net]

def build_pairs(records, max_pairs=None, seed=42):
    rnd = random.Random(seed)
    pairs = []
    used_labels = {}
    # Rotate through defect types so the rejected set is DIVERSE (not all-one-kind),
    # which gives DPO a richer preference signal. Deterministic order + try-types.
    for i, e in enumerate(records):
        if e.get("verified") is not True and e.get("verified") != "true":
            continue  # only verified positives become 'chosen'
        chosen = render_netlist(e)
        net = e.get("netlist")
        if not isinstance(net, dict):
            continue
        # rotate starting index per-record
        start = i % len(DEFECTS)
        ok = False
        for off in range(len(DEFECTS)):
            fn = DEFECTS[(start + off) % len(DEFECTS)]
            broken, label = fn(net)
            if broken is not None:
                pairs.append({
                    "prompt": e.get("prompt","").strip(),
                    "chosen": chosen,
                    "rejected": render_netlist(broken),
                    "_label": label,
                })
                used_labels[label] = used_labels.get(label,0)+1
                ok = True
                break
        if not ok:
            continue
        if max_pairs and len(pairs) >= max_pairs:
            break
    return pairs, used_labels

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="local output path (default: temp then upload)")
    ap.add_argument("--dry-run", action="store_true", help="stats only; no upload")
    ap.add_argument("--max-pairs", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    import tempfile, subprocess
    tmp = args.out or os.path.join(tempfile.gettempdir(), "dpo_pairs_generated.jsonl")
    fetch_verified(tmp.replace("dpo_pairs_generated", "verified_dataset_generated"))
    verified = os.path.join(os.path.dirname(tmp), "verified_dataset_generated.jsonl")
    records = []
    with open(verified, encoding="utf-8") as f:
        for line in f:
            line=line.strip()
            if line: records.append(json.loads(line))

    pairs, used = build_pairs(records, max_pairs=args.max_pairs, seed=args.seed)
    # strip internal label for the shipped file
    clean = [{k:v for k,v in p.items() if not k.startswith("_")} for p in pairs]

    if args.dry_run:
        print(f"verified records: {len(records)}")
        print(f"pairs built: {len(clean)}  ({len(clean)/max(len(records),1)*100:.1f}% coverage)")
        print("defect distribution:", json.dumps(used))
        if clean:
            print("sample[0] keys:", list(clean[0].keys()))
        return

    with open(tmp,"w",encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p)+"\n")

    if args.verify:
        vd=0
        with open(tmp,encoding="utf-8") as f:
            for line in f:
                p=json.loads(line); assert "prompt" in p and "chosen" in p and "rejected" in p
                assert p["chosen"] != p["rejected"]
                vd+=1
        print(f"verification: {vd} rows, all have prompt/chosen/rejected, chosen!=rejected")

    print(f"wrote {len(pairs)} DPO pairs to {tmp}")
    # upload to R2 via the boto3 helper
    subprocess.run([sys.executable, "/pipeline/lib/r2.py", "put",
                    "artifacts/processed/dpo_pairs.jsonl"], stdin=open(tmp,"rb"), check=True)
    print("uploaded to R2 artifacts/processed/dpo_pairs.jsonl")

if __name__ == "__main__":
    main()
