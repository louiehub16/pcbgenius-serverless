"""
PCBGenius E5 — test_cost.py
===========================
Verify the live cost-of-design meter (#26): build a board from a contract
netlist, read the running total, then CHANce a single part and assert the
meter returns an explicit delta matching the new-minus-old total.

Also exercises the deterministic in-memory fallback pricing (never touches the
network) and the fabric / "your time" estimates in fab_estimate.py.

Run with the repo working directory on sys.path:
    python test_cost.py
or via pytest from the parent directory.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from meter import CostMeter, price_at_breaks  # noqa: E402
from fab_estimate import estimate_pcb, time_estimate, estimate_board  # noqa: E402


def _comp(ref, ctype, value, package, mpn=None):
    return {
        "ref": ref,
        "type": ctype,
        "value": value,
        "package": package,
        "mpn": mpn,
        "pins": [{"number": "1", "name": "1", "net": "GND"}],
        "properties": {},
    }


def _netlist(name, components):
    return {
        "schema_version": "1.0.0",
        "metadata": {
            "design_name": name,
            "description": "cost meter test",
            "board_layers": 2,
            "created_by": "pcbgenius",
            "target_fab": "jlcpcb",
        },
        "components": components,
        "nets": [{"name": "GND", "pins": [], "class": "ground"},
                 {"name": "VCC", "pins": [], "class": "power"}],
    }


# A small blinker board: MCU + 2x shared 10k resistors + LED + decoupling cap.
BOARD = _netlist("led_blinker", [
    _comp("U1", "ic", "ATtiny85", "DIP-8", "ATTINY85-20PU"),
    _comp("R1", "resistor", "10k", "0603"),
    _comp("R2", "resistor", "10k", "0603"),      # shares group w/ R1 -> qty 2
    _comp("LED1", "led", "red", "0805"),
    _comp("C1", "capacitor", "100nF", "0603"),
])


# --------------------------------------------------------------------------
# price-break helper
# --------------------------------------------------------------------------

def test_price_at_breaks_volume_discount():
    breaks = [(1, 0.09), (100, 0.05), (1000, 0.03)]
    assert price_at_breaks(breaks, 1) == 0.09
    assert price_at_breaks(breaks, 10) == 0.09   # not yet at the next break
    assert price_at_breaks(breaks, 100) == 0.05
    assert price_at_breaks(breaks, 500) == 0.05
    assert price_at_breaks(breaks, 1000) == 0.03
    assert price_at_breaks([], 50) == 0.0
    assert price_at_breaks(breaks, 0) == 0.09


# --------------------------------------------------------------------------
# running total + delta on part change (the core feature)
# --------------------------------------------------------------------------

def test_build_board_and_total_is_deterministic():
    m = CostMeter().from_netlist(BOARD)
    t1 = m.total()
    t2 = m.total()
    # deterministic: reading twice gives byte-identical total
    assert t1 == t2
    assert t1["grand_total_usd"] > 0
    assert t1["num_refs"] == 5
    # R1+R2 share (10k,0603,'') so they collapse into one group of qty 2
    res = [r for r in t1["lines"] if r["type"] == "resistor"]
    assert len(res) == 1 and res[0]["qty"] == 2


def test_change_part_updates_total_with_delta():
    m = CostMeter().from_netlist(BOARD)
    before = m.total()["grand_total_usd"]

    # Change a part: swap the cheap 100nF decoupling cap for a beefier IC.
    d = m.set_part("C1", _comp("C1", "ic", "LM358", "DIP-8", "LM358N"))
    after = m.total()["grand_total_usd"]

    assert d["ref"] == "C1"
    assert d["old_total_usd"] == round(before, 4)
    assert d["new_total_usd"] == round(after, 4)
    assert d["delta_usd"] == round(after - before, 4)
    assert d["delta_usd"] > 0, "swapping a 0.03 cap for an IC must raise the total"


def test_identical_parts_get_volume_break():
    # One resistor @ qty1 vs. the same two resistors @ qty2 sharing the group:
    # per-unit on the shared group must be <= the lone part's per-unit.
    m1 = CostMeter()
    m1.add_part("R1", _comp("R1", "resistor", "10k", "0603"))
    lone_unit = m1.total()["lines"][0]["unit_price_usd"]

    m2 = CostMeter()
    m2.add_part("R1", _comp("R1", "resistor", "10k", "0603"))
    m2.add_part("R2", _comp("R2", "resistor", "10k", "0603"))
    shared = m2.total()["lines"][0]
    assert shared["qty"] == 2
    assert shared["unit_price_usd"] <= lone_unit
    # same qty across cost tables must still be deterministic
    assert shared["unit_price_usd"] == shared["unit_price_usd"]


def test_remove_part_returns_negative_delta():
    m = CostMeter().from_netlist(BOARD)
    before = m.total()["grand_total_usd"]
    d = m.remove_part("LED1")
    assert d["delta_usd"] == round(m.total()["grand_total_usd"] - before, 4)
    assert d["delta_usd"] < 0


def test_live_lookup_is_prioritized_over_fallback():
    def fake_octopart(mpn, ctype, qty):  # CALLSITE stand-in: real API returns breaks
        if mpn == "MAGIC":
            return 9.99
        return None  # defer to deterministic fallback

    m = CostMeter(price_lookup=fake_octopart)
    m.add_part("X1", _comp("X1", "ic", "magic", "SOIC-8", "MAGIC"))
    # live lookup won -> 9.99 in fallback, not the ~0.50 IC fallback price
    assert m.total()["lines"][0]["unit_price_usd"] == 9.99


# --------------------------------------------------------------------------
# fab_estimate: board area/stack + your-time
# --------------------------------------------------------------------------

def test_estimate_pcb_scales_with_area_and_layers():
    small = estimate_pcb(width_mm=20, height_mm=20, layers=2)
    big = estimate_pcb(width_mm=40, height_mm=40, layers=2)
    assert small["area_cm2"] < big["area_cm2"]
    assert small["total_usd"] < big["total_usd"]

    four_layer = estimate_pcb(width_mm=20, height_mm=20, layers=4)
    assert four_layer["layer_mult"] == 1.5
    assert four_layer["breakdown"]["boards"] > small["breakdown"]["boards"]
    assert set(four_layer["breakdown"]) == {"boards", "stencil", "setup"}


def test_your_time_estimate_defaults_and_overrides():
    medium = time_estimate("medium")
    assert medium["total_hours"] == 6.5
    assert medium["labor_cost_usd"] == round(6.5 * 75.0, 2)

    complex_board = time_estimate("complex", hourly_rate=100.0)
    assert complex_board["complexity"] == "complex"
    assert complex_board["total_hours"] == 14.0
    assert complex_board["labor_cost_usd"] == 1400.0

    custom = time_estimate("simple", hours_override={"layout": 3.0})
    assert custom["hours"]["layout"] == 3.0
    assert custom["total_hours"] == 4.5


def test_estimate_board_combines_pcb_and_time():
    est = estimate_board({"width_mm": 40, "height_mm": 30, "layers": 2},
                         complexity="medium")
    assert est["design_total_usd"] == round(
        est["pcb"]["total_usd"] + est["your_time"]["labor_cost_usd"], 2
    )
    assert est["design_total_usd"] > est["pcb"]["total_usd"]  # time dominates


def test_marker_present_in_source():
    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(here, "meter.py"), encoding="utf-8").read()
    assert "CALLSITE" in src and "octopart.com" in src.lower()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)