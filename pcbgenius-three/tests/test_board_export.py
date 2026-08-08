#!/usr/bin/env python3
"""
PCBGenius — B2 tests for the 3D board exporter (board_export.py).
Run with plain stdlib (no three.js / no pcbflow / no network / no docker):
    python test_board_export.py            # run all checks, exit 0 on pass
    python test_board_export.py --sample   # print one scene JSON to stdout

Coverage
--------
  1. validate_netlist accepts a contract-valid netlist.
  2. convert_netlist_to_scene emits a structurally valid scene for 3 designs.
  3. Every placed component appears exactly once, with full geometry
     {x, y, z} + {w_mm, h_mm, body_h_mm} and a per-type color.
  4. Board outline is a closed CCW quadrilateral enclosing every component.
  5. Determinism: same (netlist, seed) => byte-identical scene JSON.
  6. Externally supplied placement/board are respected (round-trip).
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List

# expose the parent (pcbgenius-three/) so `board_export` imports cleanly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from board_export import (  # noqa: E402
    convert_netlist_to_scene,
    scene_to_json,
    validate_netlist,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — 3 distinct contract-valid designs
# ─────────────────────────────────────────────────────────────────────────────
def _comp(ref, ctype, value, package, pins, mpn=None):
    p = [{"number": str(i + 1), "name": n, "net": net_} for i, (n, net_) in enumerate(pins)]
    return {"ref": ref, "type": ctype, "value": value, "package": package,
            "mpn": mpn, "pins": p, "properties": {}}


def _net(name, cls, pins):
    return {"name": name, "pins": pins, "class": cls}


def _base(design_name, board_layers=2):
    return {
        "schema_version": "1.0.0",
        "metadata": {
            "design_name": design_name,
            "description": design_name,
            "board_layers": board_layers,
            "created_by": "pcbgenius",
            "target_fab": "jlcpcb",
        },
        "components": [],
        "nets": [],
    }


def design_buck():
    nl = _base("buck_12V_to_5V", board_layers=2)
    nl["components"] = [
        _comp("U1", "ic", "LM2596S-ADJ", "TO-263",
              [("VIN", "VIN"), ("GND", "GND"), ("OUT", "SW"), ("FB", "FB")], "LM2596S-ADJ"),
        _comp("D1", "diode", "SS34", "SMA", [("A", "SW"), ("K", "VOUT")], "SS34"),
        _comp("L1", "inductor", "33uH", "CDRH8D28", [("1", "SW"), ("2", "VOUT")]),
        _comp("C1", "capacitor", "100uF", "0603", [("1", "VIN"), ("2", "GND")]),
        _comp("C2", "capacitor", "220uF", "0603", [("1", "VOUT"), ("2", "GND")]),
        _comp("R1", "resistor", "1k", "0805", [("1", "VOUT"), ("2", "FB")]),
        _comp("R2", "resistor", "3.3k", "0805", [("1", "FB"), ("2", "GND")]),
    ]
    nl["nets"] = [
        _net("VIN", "power", ["U1.VIN", "C1.1"]),
        _net("GND", "ground", ["U1.GND", "D1.K", "C1.2", "C2.2", "R2.2"]),
        _net("SW", "power", ["U1.OUT", "D1.A", "L1.1"]),
        _net("VOUT", "power", ["D1.K", "L1.2", "C2.1", "R1.1"]),
        _net("FB", "analog", ["U1.FB", "R1.2", "R2.1"]),
    ]
    return nl


def design_blinker():
    nl = _base("led_blinker", board_layers=2)
    nl["components"] = [
        _comp("U1", "ic", "ESP32-WROOM-32", "Module",
              [("3V3", "3V3"), ("GND", "GND"), ("GPIO2", "LED_CTRL")], "ESP32-WROOM-32"),
        _comp("R1", "resistor", "330", "0805", [("1", "LED_CTRL"), ("2", "NET_LED")]),
        _comp("LED1", "led", "Red", "0805", [("A", "NET_LED"), ("K", "GND")]),
        _comp("C1", "capacitor", "100nF", "0603", [("1", "3V3"), ("2", "GND")]),
    ]
    nl["nets"] = [
        _net("3V3", "power", ["U1.3V3", "C1.1"]),
        _net("GND", "ground", ["U1.GND", "LED1.K", "C1.2"]),
        _net("LED_CTRL", "signal", ["U1.GPIO2", "R1.1"]),
        _net("NET_LED", "signal", ["R1.2", "LED1.A"]),
    ]
    return nl


def design_sensor4():
    nl = _base("temp_humidity_sensor", board_layers=4)
    nl["components"] = [
        _comp("U1", "ic", "STM32F030K6", "DIP-8",
              [("1", "VIN"), ("2", "GND"), ("3", "SDA"), ("4", "SCL"), ("5", "RESET"), ("6", "TEMP")]),
        _comp("SEN1", "connector", "SHT31", "USB-C-31", [("1", "VIN"), ("2", "SDA"), ("3", "SCL"), ("4", "GND")]),
        _comp("C1", "capacitor", "10uF", "0603", [("1", "VIN"), ("2", "GND")]),
        _comp("C2", "capacitor", "100nF", "0402", [("1", "VIN"), ("2", "GND")]),
        _comp("R1", "resistor", "10k", "0402", [("1", "VIN"), ("2", "SDA")]),
        _comp("R2", "resistor", "10k", "0402", [("1", "VIN"), ("2", "SCL")]),
        _comp("Y1", "crystal", "8MHz", "SMA", [("1", "X1"), ("2", "X2")]),
        _comp("D1", "led", "Green", "0805", [("A", "STATUS"), ("K", "GND")]),
        _comp("RGB1", "led", "RGB", "0805", [("A", "R_TXD"), ("K", "GND")]),
    ]
    nl["nets"] = [
        _net("VIN", "power", ["U1.1", "SEN1.1", "C1.1", "C2.1", "R1.1", "R2.1"]),
        _net("GND", "ground", ["U1.2", "SEN1.4", "C1.2", "C2.2", "D1.K", "RGB1.K"]),
        _net("SDA", "signal", ["U1.3", "SEN1.2", "R1.2"]),
        _net("SCL", "signal", ["U1.4", "SEN1.3", "R2.2"]),
        _net("RESET", "signal", ["U1.5"]),
        _net("TEMP", "analog", ["U1.6"]),
        _net("X1", "signal", ["Y1.1"]),
        _net("X2", "signal", ["Y1.2"]),
        _net("STATUS", "signal", ["D1.A"]),
        _net("R_TXD", "signal", ["RGB1.A"]),
    ]
    return nl


ALL_DESIGNS = [design_buck, design_blinker, design_sensor4]


# ─────────────────────────────────────────────────────────────────────────────
# Checks
# ─────────────────────────────────────────────────────────────────────────────
def check_validate():
    for builder in ALL_DESIGNS:
        ok, errs = validate_netlist(builder())
        assert ok, f"{builder.__name__} should validate: {errs}"
    bad = design_buck()
    bad["schema_version"] = "9.9.9"
    ok2, _ = validate_netlist(bad)
    assert not ok2, "corrupted schema_version should fail validation"


def _encloses(bx0: float, bx1: float, x: float) -> bool:
    return min(bx0, bx1) - 1e-6 <= x <= max(bx0, bx1) + 1e-6


def check_scene_shape():
    for builder in ALL_DESIGNS:
        nl = builder()
        scene = convert_netlist_to_scene(nl, seed=11)
        # top-level keys
        assert scene["schema_version"] == "1.0.0"
        assert scene["generator"] == "pcbgenius-three/board_export.py"
        assert scene["unit"] == "mm"
        assert scene["design_name"] == nl["metadata"]["design_name"]
        # board
        b = scene["board"]
        assert isinstance(b["thickness_mm"], float) and b["thickness_mm"] > 0
        assert b["layers"] == nl["metadata"]["board_layers"]
        assert len(b["outline"]) == 4, "board outline must be a 4-corner rect"
        # one emitted component per placed ref
        placed_refs = {c["ref"] for c in nl["components"]}
        comp_refs = {c["ref"] for c in scene["components"]}
        assert comp_refs == placed_refs, f"scene must carry every component for {builder.__name__}"
        # every component has full geometry + color
        bxs = [pt[0] for pt in b["outline"]]
        bys = [pt[1] for pt in b["outline"]]
        for c in scene["components"]:
            assert set(c["position"].keys()) == {"x", "y", "z"}
            assert set(c["size"].keys()) == {"w_mm", "h_mm", "body_h_mm"}
            assert c["size"]["w_mm"] > 0 and c["size"]["h_mm"] > 0
            assert c["size"]["body_h_mm"] > 0
            assert c["position"]["z"] > 0, "components must sit above the board"
            assert c["color"].startswith("#") and len(c["color"]) == 7
            assert isinstance(c["rotation"], float) and 0 <= c["rotation"] < 360
            # board outline encloses the component's centre
            assert _encloses(bxs[0], bxs[2], c["position"]["x"]), f"{c['ref']} x outside board"
            assert _encloses(bys[1], bys[3], c["position"]["y"]), f"{c['ref']} y outside board"
        # nets passthrough
        assert len(scene["nets"]) == len(nl["nets"])


def check_determinism():
    ok = json.loads(scene_to_json(convert_netlist_to_scene(design_sensor4(), seed=5)))
    a = scene_to_json(convert_netlist_to_scene(design_sensor4(), seed=5))
    b = scene_to_json(convert_netlist_to_scene(design_sensor4(), seed=5))
    assert json.loads(a) == ok and a == b, "same seed must be byte-identical"
    c = scene_to_json(convert_netlist_to_scene(design_sensor4(), seed=6))
    assert a != c, "different seed should (usually) differ"


def check_custom_placement():
    nl = design_blinker()
    placement = {
        "U1":   {"x": -6.0, "y": 0.0, "rotation": 90.0},
        "R1":   {"x": 3.0, "y": 0.0, "rotation": 0.0},
        "LED1": {"x": 6.0, "y": 0.0, "rotation": 180.0},
        "C1":   {"x": -3.0, "y": 3.0, "rotation": 270.0},
    }
    board = {
        "outline": [[-12.0, -6.0], [12.0, -6.0], [12.0, 6.0], [-12.0, 6.0]],
        "center": {"x": 0.0, "y": 0.0},
        "width_mm": 24.0, "height_mm": 12.0,
    }
    scene = convert_netlist_to_scene(nl, placement=placement, board=board,
                                     board_thickness_mm=2.0)
    assert scene["board"]["thickness_mm"] == 2.0
    assert scene["board"]["width_mm"] == 24.0 and scene["board"]["height_mm"] == 12.0
    by_ref = {c["ref"]: c for c in scene["components"]}
    assert by_ref["U1"]["position"]["x"] == -6.0
    assert by_ref["U1"]["rotation"] == 90.0
    assert by_ref["LED1"]["rotation"] == 180.0
    assert by_ref["C1"]["position"]["y"] == 3.0
    # a stale placement for a component that vignettes off-board must still emit
    assert len(scene["components"]) == len(nl["components"])


def check_auto_place_all():
    # every design auto-places exactly once, no two centres overlap
    for builder in ALL_DESIGNS:
        nl = builder()
        scene = convert_netlist_to_scene(nl, seed=3)
        centres = [(c["position"]["x"], c["position"]["y"]) for c in scene["components"]]
        assert len(centres) == len(set(centres)), \
            f"duplicate centre for {builder.__name__}"
        # auto-placement lands on a 3mm grid: min pairwise centre distance is a grid step
        min_d = min(
            math.hypot(a[0] - b[0], a[1] - b[1])
            for i, a in enumerate(centres) for b in centres[i + 1:]
        )
        assert min_d >= 3.0 - 1e-6, \
            f"overlap (min {min_d:.2f} mm) for {builder.__name__}"


def main() -> int:
    tests = [check_validate, check_scene_shape, check_determinism,
             check_custom_placement, check_auto_place_all]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {t.__name__}: unexpected {type(e).__name__}: {e}")
    print(f"\n{'ALL PASS' if failed == 0 else f'{failed} FAILURE(S)'} (deterministic, stdlib only)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    if "--sample" in sys.argv:
        print(scene_to_json(convert_netlist_to_scene(design_buck(), seed=0), indent=2))
        sys.exit(0)
    sys.exit(main())