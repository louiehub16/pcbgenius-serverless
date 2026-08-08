"""
PCBGenius — E3 Self-Improving Flywheel: curate
================================================
Reads the append-only `capture_log.jsonl`, dedupes and drops junk, and produces
clean training pairs (`curated_pairs.jsonl`). A pair is the effect of the whole
flywheel loop on one design interaction:

    {
      "prompt": str,
      "netlist": { ... },            # corrected netlist when a fix was applied
      "corrected": bool,             # True if this pair uses the repaired netlist
      "dropped_junk": bool,
    }

Curation rules (k=normative):
  1. Drop records that are not JSON objects / lack a prompt / lack a netlist.
  2. Drop "junk": records whose verdict fails AND could not be repaired
     (corrected_netlist is null) — they carry no signal worth training on.
  3. Drop exact-duplicate pairs on (prompt, canonical netlist hash).
  4. When both an original and a corrected netlist exist, prefer the corrected
     (self-improving: the fix becomes the training target).
  5. Netlists in surviving pairs must still satisfy the frozen contract
     (validate before emitting).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from capture import DEFAULT_LOG, validate_netlist

DEFAULT_PAIRS = Path(__file__).parent / "curated_pairs.jsonl"


def _canon_hash(netlist) -> str:
    """Stable hash so order-insensitive-but-logically-equal netlists dedupe."""
    if not isinstance(netlist, dict):
        return hashlib.sha256(json.dumps(netlist, sort_keys=True)
                              .encode()).hexdigest()
    nl = {k: netlist.get(k) for k in ("schema_version", "components", "nets")}
    return hashlib.sha256(json.dumps(nl, sort_keys=True).encode()).hexdigest()


def read_records(log_path=DEFAULT_LOG):
    """Yield dict records from a jsonl capture log; skip unparseable lines."""
    for line in Path(log_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and "prompt" in rec and "netlist" in rec:
            yield rec


def curate(log_path=DEFAULT_LOG, out_path=DEFAULT_PAIRS) -> dict:
    """Produce clean training pairs. Returns a short stats dict."""
    dropped_junk = 0
    dropped_dup = 0
    dropped_invalid = 0
    kept = 0
    seen = set()
    pairs = []

    for rec in read_records(log_path):
        verdict = rec.get("verdict") or {}
        pass_ = bool(verdict.get("pass"))
        corrected = rec.get("corrected_netlist")

        # 1/2. junk: failed and not repaired -> drop
        if not pass_ and not corrected:
            dropped_junk += 1
            continue

        # 4. prefer corrected netlist (the flywheel's improvement)
        netlist = corrected if corrected is not None else rec["netlist"]
        ok, _ = validate_netlist(netlist)
        if not ok:
            dropped_invalid += 1
            continue

        # 3. dedupe on (prompt, netlist hash)
        key = (rec["prompt"].strip(), _canon_hash(netlist))
        if key in seen:
            dropped_dup += 1
            continue
        seen.add(key)

        pairs.append({
            "prompt": rec["prompt"].strip(),
            "netlist": netlist,
            "corrected": corrected is not None,
            "dropped_junk": False,
        })
        kept += 1

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for pair in pairs:
            f.write(json.dumps(pair) + "\n")

    stats = {
        "kept": kept, "dropped_junk": dropped_junk,
        "dropped_dup": dropped_dup, "dropped_invalid": dropped_invalid,
        "out": str(out),
    }
    print(f"[curate] {json.dumps(stats)}")
    return stats


def main():
    import argparse
    ap = argparse.ArgumentParser(description="PCBGenius flywheel curate")
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--out", default=str(DEFAULT_PAIRS))
    a = ap.parse_args()
    curate(log_path=a.log, out_path=a.out)


if __name__ == "__main__":
    main()