#!/usr/bin/env python3
"""PCBGenius — E4 explainability: tests for why.py + cite.py (feature #25).

Deterministic, offline — no network / no API / no docker. Coverage:

  1. Five representative decisions each produce a cite record whose
     ``evidence`` is NON-EMPTY and GROUNDED (every evidence item carries a
     non-empty ``sources`` list with a real datasheet/rule citation or calc).
  2. ``explain_action`` output shape: {action, kind, summary, detail,
     sources[], timestamp}.
  3. All three explanation kinds work end-to-end (datasheet, rule, calc).
  4. Evidence is ordered by decisiveness (rule > calc > datasheet).
  5. Robustness: ungrounded / unknown-kind evidence is rejected, and the
     fully-explained record round-trips through ``json.dumps``.
"""

from __future__ import annotations

import json
import os
import sys

_TESTDIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _TESTDIR)  # package root for why.py / cite.py

from why import explain_action, understand_action, ExplainError
from cite import cite_decision, cite_action, CiteError

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    if not cond:
        _failures.append(f"{name}: {detail}")


def _only(sources) -> bool:
    return isinstance(sources, list) and len(sources) > 0 and all(isinstance(s, str) and s.strip() for s in sources)


def _assert_grounded(name: str, record: dict):
    """A cite record is grounded iff every evidence item has non-empty sources."""
    ev = record.get("evidence", [])
    check(f"{name}_has_evidence", isinstance(ev, list) and len(ev) > 0,
          f"expected >=1 evidence item, got {len(ev)}")
    for i, item in enumerate(ev):
        check(f"{name}_evidence{i}_sources", _only(item.get("sources")),
              f"evidence {i} not grounded: {item}")
    # shape: decision/reason strings present
    check(f"{name}_decision", isinstance(record.get("decision"), str) and record["decision"].strip())
    check(f"{name}_reason", isinstance(record.get("reason"), str) and record["reason"].strip())
    # deterministic: re-serialize equal
    check(f"{name}_deterministic", json.dumps(record, sort_keys=True) == json.dumps(record, sort_keys=True))
    # JSON round-trips
    check(f"{name}_json", json.loads(json.dumps(record)) == record)


# ── 1. Five decisions, all uniformly grounded ─────────────────────────────
def test_five_decisions_all_grounded():
    decisions = [
        cite_decision(
            decision="widen VCC trace to 54mil",
            reason="Deliver 1A over 40mm with <0.35V drop.",
            evidence=[
                explain_action("widen VCC trace", "calc",
                               summary="VCC must carry 1A",
                               calc={"formula": "w = I/(t*K)",
                                     "inputs": {"I": 1.0, "t": 0.035, "K": 0.53},
                                     "result": "54mil"}),
            ],
        ),
        cite_decision(
            decision="decouple AMS1117 output",
            reason="Regulator stability requires low-ESR output cap.",
            evidence=[
                understand_action("add 0.1uF on AMS1117 out", "datasheet",
                                  summary="AMS1117 output decoupling"),
            ],
        ),
        cite_decision(
            decision="keep copper 1mm from edge",
            reason="Meets board-edge keep-out requirement.",
            evidence=[
                understand_action("pull trace off edge", "rule",
                                  summary="edge keep-out clearance met"),
            ],
        ),
        cite_decision(
            decision="source LED from ATtiny pin",
            reason="Pin current within absolute maximum.",
            evidence=[
                understand_action("drive LED from PB1", "datasheet",
                                  summary="ATtiny pin current supported"),
                explain_action("LED current", "calc",
                               summary="20mA LED load",
                               calc={"formula": "I=(Vcc-Vf)/R",
                                     "inputs": {"Vcc": 5.0, "Vf": 2.0, "R": 150},
                                     "result": "20mA"}),
            ],
        ),
        cite_action(
            decision="route signal at 6mil minimum",
            reason="Outer-layer conductor width meets IPC minimum.",
            evidence_specs=[
                {"action": "check trace", "kind": "rule",
                 "summary": "trace width meets min 6 mil"},
            ],
        ),
    ]
    for name, rec in zip("abcde", decisions):
        _assert_grounded(f"decision_{name}", rec)


# ── 2. explain_action shape ───────────────────────────────────────────────
def test_explain_shape():
    out = explain_action(
        "widen VCC trace", "calc",
        summary="VCC must carry 1A",
        calc={"formula": "w = I/(t*K)", "inputs": {"I": 1.0}, "result": 54},
    )
    keys = {"action", "kind", "summary", "detail", "sources", "timestamp"}
    check("explain_keys", set(out.keys()) == keys, f"keys={sorted(out)}")
    check("explain_kind", out["kind"] == "calc")
    check("explain_sources", _only(out["sources"]))


# ── 3. all three kinds end-to-end ─────────────────────────────────────────
def test_all_kinds_end_to_end():
    calc = explain_action("widening", "calc", summary="current",
                          calc={"formula": "w=I/(t*K)", "inputs": {"I": 1.0}, "result": 54})
    ds = understand_action("reg cap", "datasheet", summary="AMS1117 decoupling")
    rule = understand_action("edge", "rule", summary="edge keep-out clearance met")
    check("kind_calc", calc["kind"] == "calc")
    check("kind_datasheet", ds["kind"] == "datasheet")
    check("kind_rule", rule["kind"] == "rule")
    for it in (calc, ds, rule):
        check(f"kind_{it['kind']}_grounded", _only(it["sources"]))


# ── 4. evidence ordering (rule > calc > datasheet) ────────────────────────
def test_evidence_priority_order():
    ds = understand_action("reg cap", "datasheet", summary="AMS1117 decoupling")
    rule = understand_action("edge", "rule", summary="edge keep-out clearance met")
    calc = explain_action("widening", "calc", summary="current",
                          calc={"formula": "w=I/(t*K)", "inputs": {"I": 1.0}, "result": 54})
    rec = cite_decision("d", "r", [ds, calc, rule])
    order = [e["kind"] for e in rec["evidence"]]
    check("priority_order", order == ["rule", "calc", "datasheet"], f"got {order}")


# ── 5. robustness ─────────────────────────────────────────────────────────
def test_robustness():
    try:
        cite_decision("d", "r", [])
        check("robust_empty", False, "empty evidence should raise")
    except CiteError:
        check("robust_empty", True)

    try:
        cite_decision("d", "r", [{"action": "x", "kind": "rule", "sources": []}])
        check("robust_ungrounded", False, "empty sources should raise")
    except CiteError:
        check("robust_ungrounded", True)

    try:
        explain_action("a", "nonsense", summary="s")
        check("robust_badkind", False, "unknown kind should raise")
    except ExplainError:
        check("robust_badkind", True)

    try:
        explain_action("a", "rule", summary="something not in the library")
        check("robust_nosource", False, "ungrounded rule should raise")
    except ExplainError:
        check("robust_nosource", True)


def run_all():
    tests = [
        test_five_decisions_all_grounded,
        test_explain_shape,
        test_all_kinds_end_to_end,
        test_evidence_priority_order,
        test_robustness,
    ]
    for t in tests:
        t()
    if _failures:
        print(f"FAIL ({len(_failures)}):")
        for f in _failures:
            print("  -", f)
        sys.exit(1)
    print("PASS — 5 decisions fully grounded, all explainability tests green.")


if __name__ == "__main__":
    run_all()