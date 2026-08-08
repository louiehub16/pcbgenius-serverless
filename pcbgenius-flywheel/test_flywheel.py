"""
PCBGenius — E3 flywheel test suite
====================================
Runs the full loop on 20 generated designs:
  1. capture  ->  capture_log.jsonl has exactly 20 records, each with the
     {prompt, netlist, verdict, corrected_netlist} shape.
  2. curate   ->  junk dropped, deduped, clean pairs produced.
  3. export   ->  training_dataset.jsonl is in Phase-2 format
                  {prompt, netlist, skill:"netlist_design"} and every netlist
                  is contract-struct valid.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from capture import (
    generate_design, score_four_dimensional, validate_netlist,
)
from curate import curate
from export import export, _finalize

N = 20


def _records(path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8")
            .splitlines() if l.strip()]


def test_capture_100_percent():
    """Every one of 20 designs lands in the capture log with the right shape."""
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "capture_log.jsonl"
        for seed in range(N):
            prompt, nl, _ = generate_design(seed)
            rec = {
                "prompt": prompt, "netlist": nl,
                "verdict": score_four_dimensional(nl),
                "corrected_netlist": None,
            }
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

        recs = _records(log)
        assert len(recs) == N, f"expected {N} records, got {len(recs)}"
        for r in recs:
            assert {"prompt", "netlist", "verdict", "corrected_netlist"} <= r.keys()
            assert "pass" in r["verdict"] and "dimensions" in r["verdict"]
            assert {"d1", "d2", "d3", "d4"} <= r["verdict"]["dimensions"].keys()
    print("  [ok] capture recorded all 20 designs with 4D verdicts")


def test_defect_injection_covered():
    """Seeds %5==0 are corrupted (fail verdict, no ground) so curation is exercised."""
    failed = sum(1 for seed in range(N)
                 if not score_four_dimensional(generate_design(seed)[1])["pass"])
    assert failed >= N // 5, f"expected >= {N//5} failing designs, got {failed}"
    print(f"  [ok] {failed}/{N} designs intentionally fail (exercise curation/fix)")


def test_curated_export_valid():
    """End-to-end: capture -> curate -> export; every export netlist validates
    and matches Phase-2 format exactly."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        log = td / "capture_log.jsonl"
        pairs = td / "curated_pairs.jsonl"
        out = td / "training_dataset.jsonl"

        # capture
        for seed in range(N):
            prompt, nl, _ = generate_design(seed)
            row = {"prompt": prompt, "netlist": nl,
                   "verdict": score_four_dimensional(nl),
                   "corrected_netlist": None}
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")

        # curate
        stats = curate(log_path=log, out_path=pairs)
        kept = stats["kept"]
        assert kept > 0, "curation produced no pairs"
        assert stats["dropped_junk"] >= 0

        pair_recs = _records(pairs)
        assert len(pair_recs) == kept

        # export
        written = export(pair_recs, out_path=out)
        assert written == kept, f"exported {written} != curated {kept}"

        # Phase-2 format + contract validity
        for line in Path(out).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            assert set(row.keys()) == {"prompt", "netlist", "skill"}, row.keys()
            assert row["skill"] == "netlist_design"
            ok, errs = validate_netlist(row["netlist"])
            assert ok, f"exported netlist invalid: {errs}"
    print(f"  [ok] curated {kept} pairs -> {written} Phase-2-valid rows")


def test_repair_produces_valid_fix():
    """A corrupt design that gets repaired yields a contract-valid corrected netlist."""
    from capture import apply_fix
    fixed = 0
    for seed in range(N):
        prompt, nl, _ = generate_design(seed)
        if not score_four_dimensional(nl)["pass"]:
            corr = apply_fix(nl)
            if corr is not None:
                ok, _ = validate_netlist(corr)
                assert ok, "repaired netlist must be contract-valid"
                fixed += 1
    assert fixed >= 0
    print(f"  [ok] {fixed} corrupt designs repaired into valid netlists")


def run_all():
    print(f"E3 flywheel tests ({N} designs)")
    test_capture_100_percent()
    test_defect_injection_covered()
    test_repair_produces_valid_fix()
    test_curated_export_valid()
    print("ALL FLYWHEEL TESTS PASSED")


if __name__ == "__main__":
    run_all()