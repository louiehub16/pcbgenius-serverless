"""
PCBGenius — C2 multi-layer DRC tests.

20 good boards (10 four-layer + 10 six-layer) must PASS (no error severity);
2 intentionally-bad boards must FAIL with clear, rule-tagged messages.

Run with:  python -m pytest tests/test_multilayer.py
or:        python tests/test_multilayer.py
"""

from __future__ import annotations

import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from verifier import run_drc  # noqa: E402

CONTRACT_VERSION = "1.0.0"
CREATED_BY = "pcbgenius"


# ── builders ───────────────────────────────────────────────────────────────

def _make_netlist(board_layers: int, design_name: str, extra_nets=()) -> dict:
    """Contract-valid netlist with a USB-C connector (so diff-pair rules apply)."""
    comps = [
        {"ref": "J1", "type": "connector", "value": "USB-C", "package": "USB-C-31",
         "mpn": "USB-C", "properties": {},
         "pins": [
             {"number": "1", "name": "VBUS", "net": "VBUS"},
             {"number": "2", "name": "D+", "net": "USB_DP"},
             {"number": "3", "name": "D-", "net": "USB_DM"},
             {"number": "4", "name": "GND", "net": "GND"},
             {"number": "5", "name": "CC1", "net": "CC1"},
         ]},
        {"ref": "CM1", "type": "inductor", "value": "90ohm", "package": "SMD-0603",
         "mpn": "CMC", "properties": {},
         "pins": [
             {"number": "1", "name": "D+", "net": "USB_DP"},
             {"number": "2", "name": "D-", "net": "USB_DM"},
         ]},
    ]
    nets = [
        {"name": "VBUS", "pins": ["J1.VBUS"], "class": "power"},
        {"name": "GND", "pins": ["J1.GND"], "class": "ground"},
        {"name": "USB_DP", "pins": ["J1.D+", "CM1.D+"], "class": "signal"},
        {"name": "USB_DM", "pins": ["J1.D-", "CM1.D-"], "class": "signal"},
        {"name": "CC1", "pins": ["J1.CC1"], "class": "analog"},
    ]
    nets.extend(extra_nets)
    return {
        "schema_version": CONTRACT_VERSION,
        "metadata": {
            "design_name": design_name,
            "description": "USB-C board (multi-layer DRC fixture)",
            "board_layers": board_layers,
            "created_by": CREATED_BY,
            "target_fab": "jlcpcb",
        },
        "components": comps,
        "nets": nets,
    }


def _edge_vias(w: float, h: float, pitch: float):
    """Sample GND stitching vias around the board perimeter at `pitch`."""
    pts = []
    # top edge
    x = pitch / 2
    while x < w:
        pts.append((round(x, 2), 0.0)); x += pitch
    # right edge
    y = pitch / 2
    while y < h:
        pts.append((float(w), round(y, 2))); y += pitch
    # bottom edge
    x = pitch / 2
    while x < w:
        pts.append((round(x, 2), float(h))); x += pitch
    # left edge
    y = pitch / 2
    while y < h:
        pts.append((0.0, round(y, 2))); y += pitch
    return pts


def _make_4_layer(rng: random.Random) -> tuple[dict, dict]:
    w = rng.choice([50, 60, 70]); h = rng.choice([40, 50])
    n = _make_netlist(4, f"good_4l_{w}x{h}")
    impedance = rng.uniform(85, 93)
    lp = rng.uniform(30, 45); ln = lp + rng.uniform(-0.05, 0.05)
    vias = _edge_vias(w, h, 2.0)
    layout = {
        "board": {"edge_mm_x": w, "edge_mm_y": h},
        "stackup": {
            "layer_count": 4, "dielectric_mm": 1.6,
            "layers": [
                {"name": "F.Cu", "role": "signal"},
                {"name": "In1.Cu", "role": "plane", "plane_net": "GND"},
                {"name": "In2.Cu", "role": "plane", "plane_net": "VCC_3V3"},
                {"name": "B.Cu", "role": "signal"},
            ],
        },
        "diff_pairs": [
            {"name": "USB_D", "net_p": "USB_DP", "net_n": "USB_DM",
             "impedance_target_ohm": 90.0, "impedance_measured_ohm": round(impedance, 1),
             "impedance_tolerance_ohm": 10.0,
             "length_p_mm": round(lp, 2), "length_n_mm": round(ln, 2),
             "max_skew_mm": 1.0, "common_mode_choke": True, "impedance_checked": True},
        ],
        "planes": [
            {"layer": "In1.Cu", "net": "GND", "role": "ground",
             "has_pour": True, "thermal_relief": True,
             "relief_gap_mm": 0.4, "relief_spoke_count": 4},
            {"layer": "In2.Cu", "net": "VCC_3V3", "role": "power",
             "has_pour": True, "thermal_relief": True,
             "relief_gap_mm": 0.4, "relief_spoke_count": 4},
        ],
        "via_stitching": {
            "ground_stitch_vias": vias,
            "max_edge_gap_mm": 3.0, "max_pitch_mm": 2.5, "min_density": 0.8,
        },
    }
    return n, layout


def _make_6_layer(rng: random.Random) -> tuple[dict, dict]:
    w = rng.choice([60, 80, 100]); h = rng.choice([50, 60, 80])
    n = _make_netlist(6, f"good_6l_{w}x{h}")
    impedance = rng.uniform(87, 94)
    lp = rng.uniform(40, 60); ln = lp + rng.uniform(-0.08, 0.08)
    vias = _edge_vias(w, h, 2.5)
    layout = {
        "board": {"edge_mm_x": w, "edge_mm_y": h},
        "stackup": {
            "layer_count": 6, "dielectric_mm": 1.6,
            "layers": [
                {"name": "F.Cu", "role": "signal"},
                {"name": "In1.Cu", "role": "signal"},
                {"name": "In2.Cu", "role": "plane", "plane_net": "GND"},
                {"name": "In3.Cu", "role": "signal"},
                {"name": "In4.Cu", "role": "plane", "plane_net": "VCC_3V3"},
                {"name": "B.Cu", "role": "signal"},
            ],
        },
        "diff_pairs": [
            {"name": "USB_D", "net_p": "USB_DP", "net_n": "USB_DM",
             "impedance_target_ohm": 90.0, "impedance_measured_ohm": round(impedance, 1),
             "impedance_tolerance_ohm": 10.0,
             "length_p_mm": round(lp, 2), "length_n_mm": round(ln, 2),
             "max_skew_mm": 1.0, "common_mode_choke": True, "impedance_checked": True},
        ],
        "planes": [
            {"layer": "In2.Cu", "net": "GND", "role": "ground",
             "has_pour": True, "thermal_relief": True,
             "relief_gap_mm": 0.4, "relief_spoke_count": 4},
            {"layer": "In4.Cu", "net": "VCC_3V3", "role": "power",
             "has_pour": True, "thermal_relief": True,
             "relief_gap_mm": 0.4, "relief_spoke_count": 4},
        ],
        "via_stitching": {
            "ground_stitch_vias": vias,
            "max_edge_gap_mm": 3.0, "max_pitch_mm": 3.0, "min_density": 0.8,
        },
    }
    return n, layout


def make_good_designs(n_each=10) -> list[tuple[dict, dict]]:
    rng = random.Random(1234)
    designs = [_make_4_layer(rng) for _ in range(n_each)]
    designs += [_make_6_layer(rng) for _ in range(n_each)]
    return designs


# ── intentionally-bad designs ──────────────────────────────────────────────
def make_bad_layer_count_mismatch() -> tuple[dict, dict]:
    n = _make_netlist(4, "bad_layer_mismatch")          # claims 4 layers...
    _, layout = _make_4_layer(random.Random(1))         # ...force the stackup to 6
    layout["stackup"]["layer_count"] = 6
    layout["stackup"]["layers"] = [
        {"name": "F.Cu", "role": "signal"},
        {"name": "In1.Cu", "role": "signal"},
        {"name": "In2.Cu", "role": "plane", "plane_net": "GND"},
        {"name": "In3.Cu", "role": "signal"},
        {"name": "In4.Cu", "role": "plane", "plane_net": "VCC_3V3"},
        {"name": "B.Cu", "role": "signal"},
    ]
    return n, layout


def make_bad_usb_impedance() -> tuple[dict, dict]:
    n, layout = _make_4_layer(random.Random(2))
    # Push the USB differential impedance way off-target -> hard error.
    layout["diff_pairs"][0]["impedance_measured_ohm"] = 130.0
    return n, layout


# ── tests ──────────────────────────────────────────────────────────────────

def test_contract_shape():
    n, layout = make_good_designs(1)[0]
    res = run_drc(n, layout)
    assert set(res.keys()) == {"pass", "violations"}
    for v in res["violations"]:
        assert set(v.keys()) == {"rule", "severity", "location", "message"}
        assert v["severity"] in ("info", "warning", "error")


def test_20_good_designs_pass():
    designs = make_good_designs(10)
    assert len(designs) == 20
    for i, (n, layout) in enumerate(designs):
        res = run_drc(n, layout)
        assert res["pass"] is True, f"design {i} should pass, got {res['violations']}"
        # warning-only passes are fine, but no errors allowed
        assert all(v["severity"] != "error" for v in res["violations"])
    # confirm both layer counts represented
    counts = {layout["stackup"]["layer_count"] for _, layout in designs}
    assert counts == {4, 6}


def test_bad_layer_count_mismatch_fails():
    n, layout = make_bad_layer_count_mismatch()
    res = run_drc(n, layout)
    assert res["pass"] is False
    rules = {v["rule"] for v in res["violations"]}
    assert "MLS_LAYER_COUNT_MISMATCH" in rules
    msg = [v["message"] for v in res["violations"] if v["rule"] == "MLS_LAYER_COUNT_MISMATCH"][0]
    assert "board_layers=4" in msg and "6 copper layers" in msg


def test_bad_usb_impedance_fails():
    n, layout = make_bad_usb_impedance()
    res = run_drc(n, layout)
    assert res["pass"] is False
    rules = {v["rule"] for v in res["violations"]}
    assert "MLS_USB_IMPEDANCE" in rules
    msg = [v["message"] for v in res["violations"] if v["rule"] == "MLS_USB_IMPEDANCE"][0]
    assert "130.0 ohm" in msg and "90 ohm" in msg


def test_missing_layout_passes():
    # No layout -> run_drc must not raise and should pass (nothing to check).
    n, _ = make_good_designs(1)[0]
    assert run_drc(n, None)["pass"] is True


def test_null_layout_passes():
    n, _ = make_good_designs(1)[0]
    assert run_drc(n, {})["pass"] is True


if __name__ == "__main__":
    failures = []

    good = make_good_designs(10)
    passed = 0
    for i, (n, layout) in enumerate(good):
        res = run_drc(n, layout)
        if res["pass"]:
            passed += 1
        else:
            failures.append(f"good#{i} FAILED: {res['violations']}")
    print(f"good designs: {passed}/{len(good)} passed")

    bad_specs = [
        ("bad_layer_mismatch", make_bad_layer_count_mismatch, ["MLS_LAYER_COUNT_MISMATCH"]),
        ("bad_usb_impedance", make_bad_usb_impedance, ["MLS_USB_IMPEDANCE"]),
    ]
    for name, fn, expect_rules in bad_specs:
        n, layout = fn()
        res = run_drc(n, layout)
        rules = {v["rule"] for v in res["violations"]}
        if not res["pass"] and expect_rules and expect_rules[0] in rules:
            print(f"bad '{name}' correctly FAILED with {sorted(rules)}")
        else:
            failures.append(f"bad '{name}' did not fail as expected: {res}")

    if not failures:
        print("ALL TESTS PASSED (20 good + 2 bad)")
    else:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        print(f"JSON fixture dump -> {len(good)} good designs emitted.")
        with open(os.path.join(os.path.dirname(__file__), "_sample_good.json"), "w") as f:
            json.dump(good[0], f, indent=2)
        sys.exit(1)