"""PCBGenius — D1 Bulletproof Beginner Layers test suite.

Validates the three-gate safety layer against 10 designs:
  5 intentionally-GOOD beginner boards that must PASS all gates, and
  5 intentionally-BAD boards that must be caught (one distinct hazard each).

Run with:  python -m pytest tests/test_safety.py
or:        python -m pytest  (from pcbgenius-safety/)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import allowlist  # noqa: E402
import constraints  # noqa: E402
import refusals  # noqa: E402
from __init__ import run_safety  # noqa: E402

CONTRACT_VERSION = "1.0.0"
CREATED_BY = "pcbgenius"


# ── reusable good-board builder ─────────────────────────────────────────────

def _power(name, value, out_net):
    return {
        "ref": name, "type": "power", "value": value, "package": "TO-220",
        "mpn": None, "properties": {},
        "pins": [
            {"number": "1", "name": "IN", "net": (out_net + "_IN")},
            {"number": "2", "name": "OUT", "net": out_net},
            {"number": "3", "name": "GND", "net": "GND"},
        ],
    }


def _cap(ref, net, value="100nF"):
    return {
        "ref": ref, "type": "capacitor", "value": value, "package": "0805",
        "mpn": "CL21B104KBCNNNC", "properties": {},
        "pins": [{"number": "1", "name": "1", "net": net}, {"number": "2", "name": "2", "net": "GND"}],
    }


def _res(ref, net_a, net_b, value="10k"):
    return {
        "ref": ref, "type": "resistor", "value": value, "package": "0805",
        "mpn": "RC0805FR-0710KL", "properties": {},
        "pins": [{"number": "1", "name": "1", "net": net_a}, {"number": "2", "name": "2", "net": net_b}],
    }


def _ic(ref, power_net, gnd_net="GND", extra_pins=()):
    pins = [
        {"number": "1", "name": "VCC", "net": power_net},
        {"number": "2", "name": "GND", "net": gnd_net},
        *extra_pins,
    ]
    return {
        "ref": ref, "type": "ic", "value": "LM358", "package": "SOIC-8",
        "mpn": "LM358DR", "properties": {},
        "pins": pins,
    }


def _good_board(design_name="led-blinker", rail="VCC", rail_v=5.0, with_ic=True, with_led=True, ic_decap=True):
    out_net = rail
    comps = [_power("P1", f"{rail_v}V", out_net)]
    nets = [
        {"name": "GND", "pins": ["P1.GND"], "class": "ground"},
        {"name": out_net + "_IN", "pins": ["P1.IN"], "class": "power"},
        {"name": out_net, "pins": ["P1.OUT"], "class": "power"},
    ]
    if with_ic:
        ic_extra = []
        if with_led:
            ic_extra.append({"number": "3", "name": "OUT", "net": "NET_LED"})
        comps.append(_ic("U1", out_net, extra_pins=ic_extra))
        nets[2]["pins"].append("U1.VCC")
        nets[0]["pins"].append("U1.GND")
        if ic_decap:
            comps.append(_cap("C1", out_net))  # decoupling on the IC rail
            nets[2]["pins"].append("C1.1")
            nets[0]["pins"].append("C1.2")
    if with_led:
        comps.append(_res("R1", out_net, "NET_LED"))
        comps.append({
            "ref": "D1", "type": "led", "value": "red", "package": "0805-LED",
            "mpn": None, "properties": {},
            "pins": [{"number": "A", "name": "A", "net": "NET_LED"}, {"number": "K", "name": "K", "net": "GND"}],
        })
        nets.append({"name": "NET_LED", "pins": ["R1.2", "D1.A"], "class": "signal"})
        nets[0]["pins"].append("D1.K")
        nets[2]["pins"].append("R1.1")
    return {
        "schema_version": CONTRACT_VERSION,
        "metadata": {
            "design_name": design_name,
            "description": "beginner test board",
            "board_layers": 2,
            "created_by": CREATED_BY,
            "target_fab": "jlcpcb",
        },
        "components": comps,
        "nets": nets,
    }


# ── GOOD designs (must ALL pass) ────────────────────────────────────────────
GOOD = [
    _good_board("g0-led-blinker"),  # plain 5V LED blinker with decoupling
    _good_board("g1-3v3-rail", rail="3V3", rail_v=3.3),
    _good_board("g2-12v-rail", rail="VCC_12", rail_v=12.0),
    _good_board("g3-ic-no-led", with_led=False),
    _good_board("g4-dual-cap", with_ic=True, ic_decap=True),
]

# ── BAD designs (each trips exactly one distinct hazard) ────────────────────
def _bad_package():
    b = _good_board("b0-bad-package")
    b["components"].append({**_res("X9", "VCC", "NET_LED"), "package": "BGA-1000"})
    return b


def _bad_destructive_net():
    b = _good_board("b1-destructive-net")
    b["nets"].append({"name": "NET_DELETE", "pins": [], "class": "signal"})
    return b


def _bad_missing_decouple():
    # IC present but NO decoupling cap on its power rail.
    return _good_board("b2-no-decap", ic_decap=False)


def _bad_over_voltage():
    b = _good_board("b3-mains", rail="VCC", rail_v=240.0)
    b["nets"].append({"name": "MAINS", "pins": [], "class": "power"})
    return b


def _bad_broken_link():
    b = _good_board("b4-broken-link")
    b["components"][0]["pins"].append({"number": "9", "name": "OUT", "net": "VCC_EXTRA"})
    return b


BAD = [_bad_package(), _bad_destructive_net(), _bad_missing_decouple(), _bad_over_voltage(), _bad_broken_link()]


# ── tests ───────────────────────────────────────────────────────────────────

def test_good_boards_all_pass():
    for i, board in enumerate(GOOD):
        result = run_safety(board)
        assert result["pass"], f"good board #{i} should pass but failed: {result['violations']}"
        assert result["refused"] is False, f"good board #{i} should not be refused"


def test_bad_boards_all_caught():
    for i, board in enumerate(BAD):
        result = run_safety(board)
        assert result["pass"] is False, f"bad board #{i} should fail but passed"


def test_bad_package_is_blocked():
    vio = allowlist.check(_bad_package())
    rules = [v["rule"] for v in vio]
    assert "SAFETY_PACKAGE_NOT_ALLOWLISTED" in rules
    assert allowlist.is_blocking(vio)


def test_destructive_net_blocked():
    vio = allowlist.check(_bad_destructive_net())
    rules = [v["rule"] for v in vio]
    assert "SAFETY_DESTRUCTIVE_NET_NAME" in rules


def test_missing_decoupling_caught():
    vio = constraints.check(_bad_missing_decouple())
    rules = [v["rule"] for v in vio]
    assert "IC_MISSING_DECOUPLING" in rules
    assert constraints.is_blocking(vio)


def test_mains_refused():
    result = refusals.refuse(_bad_over_voltage())
    assert result["refuse"] is True
    assert "REFUSE_MAINS" in {v["rule"] for v in result["violations"]}


def test_broken_link_refused():
    result = refusals.refuse(_bad_broken_link())
    assert result["refuse"] is True
    assert "REFUSE_UNDEFINED_NET" in {v["rule"] for v in result["violations"]}


def test_clearance_scales_with_voltage():
    # Low rail (<15V) needs 0.13mm; a 12V rail with explicit 0.13mm spacing passes.
    board = _good_board("g-clearance", rail="VCC_12", rail_v=12.0)
    layout = {"spacings": [{"net": "VCC_12", "clearance_mm": 0.13}]}
    passed = constraints.is_blocking(constraints.check(board, layout)) is False
    assert passed

    # High voltage (48V) with too-small spacing must fail.
    v48 = _good_board("b-clearance-48", rail="VCC_48", rail_v=48.0)
    bad_layout = {"spacings": [{"net": "VCC_48", "clearance_mm": 0.2}]}
    assert constraints.is_blocking(constraints.check(v48, bad_layout))


def test_summary_helpers():
    assert "PASS" in allowlist.summarize(allowlist.check(GOOD[0])) or allowlist.check(GOOD[0]) == []
    assert constraints.summarize([]).startswith("CONSTRAINTS PASS")


if __name__ == "__main__":
    # Minimal runner so `python tests/test_safety.py` also works without pytest.
    failures = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                failures += 1
                print(f"  FAIL  {name}: {e}")
    print(f"\n{sum(1 for n in dir() if n.startswith('test_') and callable(globals()[n])) - 0} tests, {failures} failures.")
    sys.exit(1 if failures else 0)