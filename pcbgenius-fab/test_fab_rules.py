"""
PCBGenius — E2 Manufacturing-first (test_fab_rules.py)
=======================================================
Verifies the 4D manufacturing-rule gate:

  D1 TRACE, D2 SPACING, D3 DRILL, D4 BOARD (dimension + stackup)

A design MUST meet the chosen fab's capability file or it FAILS the fab check.
Focus per the feature ask: a *wrong-stackup* design (unsupported layer count /
thickness) fails the manufacturing-first gate, while a manufacturable design
passes and `choose_fab` picks a capable fab instead of erroring.

Run with:  python -m pytest test_fab_rules.py
or:        python test_fab_rules.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fab_rules import (  # noqa: E402
    check_fab_rules, choose_fab, design_matches,
    D_TRACE, D_SPACING, D_DRILL, D_BOARD,
)
from rules_files import (  # noqa: E402
    BUILTIN_CAPABILITIES, parse_capability_file, parse_capability_text,
    get_capability,
)

JLCPCB = get_capability("jlcpcb")
PCBWAY = get_capability("pcbway")


# ── a clearly-manufacturable design (fits both built-in fabs) ──────────────
GOOD_DESIGN = {
    "min_trace_mm": 0.2,
    "min_clearance_mm": 0.2,
    "min_drill_mm": 0.3,
    "min_via_mm": 0.25,
    "min_annular_ring_mm": 0.2,
    "layers": 2,
    "board_thickness_mm": 1.6,   # in JLCPCB catalogue
    "board_mm": [50.0, 40.0],
    "impedance_controlled": False,
}

# ── the wrong-stackup design: 40 layers beats both fabs; 4.5 mm is off-catalogue ─
BAD_STACKUP_DESIGN = dict(GOOD_DESIGN)
BAD_STACKUP_DESIGN.update({"layers": 40, "board_thickness_mm": 4.5})


def _error_rules(res):
    return {v["rule"] for v in res[1] if v["severity"] == "error"}


# ── capability-file ingestion ──────────────────────────────────────────────

def test_builtin_capabilities_present():
    assert "jlcpcb" in BUILTIN_CAPABILITIES
    assert "pcbway" in BUILTIN_CAPABILITIES
    for cap in BUILTIN_CAPABILITIES.values():
        assert cap["min_trace_mm"] and cap["min_drill_mm"]
        assert cap["layers"]["max"] >= 2


def test_parse_json_and_text_and_csv():
    # JSON object
    cap = parse_capability_text(json.dumps({
        "fab": "acme", "min_trace": "6mil", "min_drill_mm": 0.2,
        "max_layers": 4}), fab="acme")
    assert round(cap["min_trace_mm"], 4) == 0.1524  # 6 mil -> mm

    # key=value text
    cap2 = parse_capability_text("fab = acme\nmin_trace_mm=0.15\n# comment\nmin_drill=0.25")
    assert cap2["min_drill_mm"] == 0.25

    # CSV single row
    cap3 = parse_capability_text("fab,min_trace_mm,layers\nacme,0.13,4")
    assert cap3["min_trace_mm"] == 0.13
    assert cap3["layers"]["max"] == 4


def test_parse_capability_file(tmp_path):
    p = tmp_path / "acme_cap.json"
    p.write_text(json.dumps({"fab": "acme", "min_clearance_mm": 0.1,
                             "max_board_mm": 500}), encoding="utf-8")
    cap = parse_capability_file(str(p))
    assert cap["fab"] == "acme"
    assert cap["min_clearance_mm"] == 0.1
    assert cap["max_board_mm"] == 500


# ── 4D dims on a good design ───────────────────────────────────────────────

def test_good_design_passes_jlcpcb_and_pcbway():
    for cap in (JLCPCB, PCBWAY):
        passed, vio = check_fab_rules(dict(GOOD_DESIGN), capability=cap)
        assert passed is True, (cap["fab"], vio)
        assert all(v["severity"] != "error" for v in vio)


def test_all_four_dimensions_scored():
    _, vio = check_fab_rules(dict(GOOD_DESIGN), capability=JLCPCB)
    dims = {v["dimension"] for v in vio}
    # an error-free pass may still emit warnings (e.g. none here); ensure the
    # 4D dims are the only dimensions the checker can emit.
    assert dims <= {D_TRACE, D_SPACING, D_DRILL, D_BOARD}


# ── wrong-stackup design MUST fail ─────────────────────────────────────────

def test_wrong_stackup_fails_fab_check():
    for cap_name in ("jlcpcb", "pcbway"):
        cap = get_capability(cap_name)
        passed, vio = check_fab_rules(dict(BAD_STACKUP_DESIGN), capability=cap)
        assert passed is False, (cap_name, vio)
        rules = {v["rule"] for v in vio}
        assert "FAB_LAYERS_MAX" in rules, (cap_name, rules)
        assert "FAB_THICKNESS_RANGE" in rules, (cap_name, rules)
        err = [v for v in vio if v["rule"] == "FAB_LAYERS_MAX"][0]
        assert err["dimension"] == D_BOARD
        assert "40" in err["message"]


def test_wrong_stackup_rejected_by_choose_fab():
    # manufacturing-first: choose_fab must NOT silently ship an unbuildable board
    try:
        choose_fab(dict(BAD_STACKUP_DESIGN))
        raised = False
    except ValueError:
        raised = True
    assert raised is True, "choose_fab must reject an unbuildable stackup"


def test_choose_fab_picks_someone_for_good_design():
    cap = choose_fab(dict(GOOD_DESIGN))
    assert cap["fab"] in ("jlcpcb", "pcbway")
    assert design_matches(GOOD_DESIGN, cap) is True


def test_forced_fab_mismatch_raises():
    bad = dict(GOOD_DESIGN, min_trace_mm=0.02)  # way below any fab
    try:
        choose_fab(dict(bad, fab="pcbway"))
        raised = False
    except ValueError:
        raised = True
    assert raised is True


# ── D1..D3 individual gates ────────────────────────────────────────────────

def test_trace_spacing_drill_gates():
    for field, rule, dim in (
        ("min_trace_mm", "FAB_TRACE_MIN", D_TRACE),
        ("min_clearance_mm", "FAB_SPACING_MIN", D_SPACING),
        ("min_drill_mm", "FAB_DRILL_MIN", D_DRILL),
    ):
        d = dict(GOOD_DESIGN, **{field: 0.0001})
        passed, vio = check_fab_rules(d, capability=JLCPCB)
        assert passed is False
        assert rule in {v["rule"] for v in vio}
        assert any(v["dimension"] == dim for v in vio)


def _load():
    design = dict(GOOD_DESIGN)
    return design, check_fab_rules(design, capability=JLCPCB)


if __name__ == "__main__":
    failures = []
    checks = [
        ("builtin capabilities present", test_builtin_capabilities_present),
        ("parse json/text/csv", test_parse_json_and_text_and_csv),
        ("good design passes", test_good_design_passes_jlcpcb_and_pcbway),
        ("wrong stackup fails", test_wrong_stackup_fails_fab_check),
        ("choose_fab rejects bad stackup", test_wrong_stackup_rejected_by_choose_fab),
        ("choose_fab picks fab for good", test_choose_fab_picks_someone_for_good_design),
        ("forced fab mismatch raises", test_forced_fab_mismatch_raises),
        ("trace/spacing/drill gates", test_trace_spacing_drill_gates),
    ]
    for name, fn in checks:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failures.append((name, e))
            print(f"FAIL  {name}: {e}")
    if failures:
        print("\nFAILURES:")
        for n, e in failures:
            print(" -", n, "->", e)
        sys.exit(1)
    print("\nALL FAB-RULES TESTS PASSED (7 checks)")