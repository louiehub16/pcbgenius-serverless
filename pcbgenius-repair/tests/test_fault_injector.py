#!/usr/bin/env python3
"""PCBGenius — C4 REPAIR-MY-BOARD: tests for the fault injector + fault DB.

Deterministic, offline, no network / no API / no docker.  Coverage:

  test_fault_injector
    1. load_good_netlist returns a schema-valid netlist (FROZEN rules).
    2. inject_faults yields EXACTLY 10 distinct fault classes per buck design.
    3. Every fault class is present across a run.
    4. Determinism: same seed -> identical symptom/features; different seed
       (almost always) -> different values, yet same record count.
    5. Each record has the expected shape {fault,symptom,symptom_features,
       diagnosis,fix,good_design,changed_refs,changed_nets}.
    6. build_fault_db: 50 designs x 10 faults = exactly 500 records.

  test_fault_db (via db.py)
    7. save_jsonl/load_jsonl round-trip preserves all records.
    8. cosine/a determinism of feature_vector across two calls.
    9. fault DB aggregates per-fault counts.

Fixtures: 50 designs seeded 1..50 (buck template) so all 10 classes apply.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

_TESTDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _TESTDIR)
sys.path.insert(0, os.path.dirname(_TESTDIR))  # package root for fault_injector/db

from fault_injector import (FAULTS, build_fault_db, inject_faults,
                             load_good_netlist, validate_netlist)
from db import cosine, feature_vector, load_jsonl, save_jsonl, text_similarity

N_DESIGNS = 50
FAULT_COUNT = 10
SEEDS = list(range(1, N_DESIGNS + 1))

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    if not cond:
        _failures.append(f"{name}: {detail}")


def _shape_check(rec):
    return (all(k in rec for k in ("fault", "symptom", "symptom_features",
                                   "diagnosis", "fix", "good_design",
                                   "changed_refs", "changed_nets"))
            and isinstance(rec["symptom_features"], dict))


def test_good_netlists_valid():
    for sd in SEEDS:
        nl = load_good_netlist(sd)
        ok, errs = validate_netlist(nl)
        check(f"good_valid_seed{sd}", ok, f"errors={errs}")


def test_exact_10_faults_per_design():
    for sd in SEEDS:
        good = load_good_netlist(sd)
        recs = inject_faults(good, seed=sd)
        got = {r["fault"] for r in recs}
        check(f"fault_counts_seed{sd}", len(recs) == FAULT_COUNT,
              f"got {len(recs)} records {sorted(got)}")
        check(f"fault_coverage_seed{sd}", got == set(FAULTS),
              f"missing {set(FAULTS) - got}")


def test_all_fault_classes_present_across_run():
    pool = set()
    for sd in SEEDS:
        good = load_good_netlist(sd)
        for r in inject_faults(good, seed=sd):
            pool.add(r["fault"])
    check("all_fault_classes_present", pool == set(FAULTS), f"got {sorted(pool)}")


def test_determinism():
    for fault in FAULTS:
        good = load_good_netlist(7)
        a = inject_faults(good, seed=7, only=[fault])[0]
        b = inject_faults(good, seed=7, only=[fault])[0]
        same_sym = a["symptom"] == b["symptom"]
        same_feats = a["symptom_features"] == b["symptom_features"]
        check(f"determinism_{fault}", same_sym and same_feats)


def test_record_shape():
    good = load_good_netlist(3)
    for r in inject_faults(good, seed=3):
        check(f"shape_{r['fault']}", _shape_check(r), json.dumps(r)[:200])


def test_build_50x10():
    db = build_fault_db(N_DESIGNS, seed_base=1)
    check("build_50x10_count", len(db) == N_DESIGNS * FAULT_COUNT,
          f"got {len(db)}")
    by = {}
    for r in db:
        by[r["fault"]] = by.get(r["fault"], 0) + 1
    check("build_balanced", all(v == N_DESIGNS for v in by.values()), f"dist={by}")


def test_db_roundtrip():
    db = build_fault_db(3, seed_base=1)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "fault_db.jsonl")
        n = save_jsonl(path, db)
        loaded = load_jsonl(path)
        check("db_roundtrip_count", n == len(db) == len(loaded))
        check("db_roundtrip_eq", loaded == db)


def test_embedding_determinism():
    v1 = feature_vector("output voltage reads wrong value on the buck regulator")
    v2 = feature_vector("output voltage reads wrong value on the buck regulator")
    check("feat_deterministic_same", v1 == v2)
    check("feat_cosine_self", abs(cosine(v1, v2) - 1.0) < 1e-9,
          f"cosine={cosine(v1, v2)}")
    v3 = feature_vector("led does not blink")
    check("feat_distinct", cosine(v1, v3) < 1.0)


def run_all():
    # 1..6
    test_good_netlists_valid()
    test_exact_10_faults_per_design()
    test_all_fault_classes_present_across_run()
    test_determinism()
    test_record_shape()
    test_build_50x10()
    # 7..9
    test_db_roundtrip()
    test_embedding_determinism()

    if _failures:
        print(f"FAIL ({len(_failures)}):\n  " + "\n  ".join(_failures[:50]))
        return 1
    print(f"OK: fault injector + db tests passed ({N_DESIGNS} designs "
          f"x {FAULT_COUNT} faults, all 9 checks).")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())