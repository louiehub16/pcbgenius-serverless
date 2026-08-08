"""
PCBGenius — E3 Self-Improving Flywheel: export
================================================
Emits the curated training pairs in EXACTLY the same format as the Phase-2
training dataset (`datagen/baseline_10k.jsonl`):

    { "prompt": str, "netlist": { ... }, "skill": "netlist_design" }

One JSON object per line. Every exported netlist is contract-struct valid.
Reads `curated_pairs.jsonl` (from curate.py) by default; to export straight off
a fresh capture log, point --source at a capture_log.jsonl and curate is run
in-process first.
"""

from __future__ import annotations

import json
from pathlib import Path

from curate import curate, read_records
from curate import DEFAULT_PAIRS

DEFAULT_EXPORT = Path(__file__).parent / "training_dataset.jsonl"
SKILL = "netlist_design"


def _finalize(pair: dict) -> dict:
    return {"prompt": pair["prompt"], "netlist": pair["netlist"], "skill": SKILL}


def export(pairs: list[dict], out_path=DEFAULT_EXPORT) -> int:
    """Write Phase-2-format training lines. Returns number of rows written."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out, "w", encoding="utf-8") as f:
        for pair in pairs:
            if not pair.get("netlist"):
                continue
            f.write(json.dumps(_finalize(pair), ensure_ascii=False) + "\n")
            n += 1
    print(f"[export] wrote {n} training rows -> {out}")
    return n


def export_from_pairs(pairs_path=DEFAULT_PAIRS, out_path=DEFAULT_EXPORT):
    pairs = [r for r in read_records(pairs_path)
             if "netlist" in r]          # read_records requires 'prompt' too; ok
    # curate's pair schema has netlist+prompt+corrected
    return export(pairs, out_path)


def export_from_log(log_path, pairs_path=DEFAULT_PAIRS, out_path=DEFAULT_EXPORT):
    """Curate a capture log in-process, then export the clean pairs."""
    stats = curate(log_path=log_path, out_path=pairs_path)
    pairs = [{"prompt": r["prompt"], "netlist": r["netlist"]}
             for r in read_records(pairs_path)]
    return export(pairs, out_path)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="PCBGenius flywheel export")
    ap.add_argument("--source", choices=["pairs", "log"], default="pairs",
                    help="export from curated pairs (default) or a capture log")
    ap.add_argument("--log", default=str(Path(__file__).parent / "capture_log.jsonl"))
    ap.add_argument("--pairs", default=str(DEFAULT_PAIRS))
    ap.add_argument("--out", default=str(DEFAULT_EXPORT))
    a = ap.parse_args()

    if a.source == "log":
        export_from_log(log_path=a.log, pairs_path=a.pairs, out_path=a.out)
    else:
        export_from_pairs(pairs_path=a.pairs, out_path=a.out)


if __name__ == "__main__":
    main()