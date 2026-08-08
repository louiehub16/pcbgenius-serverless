"""Tests for the D4 firmware generation package."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pinmap import find_mcu, derive_pinmap, format_pinmap
from gen import (
    build_prompt,
    template_fallback,
    generate_firmware,
    _strip_markdown,
    OPENROUTER_CALL_START,
    OPENROUTER_CALL_END,
)

# Contract-valid LED blinker netlist (ATtiny85). Mirrors the data generator.
MCU_NETLIST = {
    "schema_version": "1.0.0",
    "metadata": {
        "design_name": "led_blinker",
        "description": "ATtiny85 blinks an LED",
        "board_layers": 2,
        "created_by": "pcbgenius",
        "target_fab": None,
    },
    "components": [
        {
            "ref": "U1", "type": "ic", "value": "ATtiny85", "package": "DIP-8",
            "mpn": "ATTINY85-20PU",
            "pins": [
                {"number": "1", "name": "VCC", "net": "VCC"},
                {"number": "2", "name": "GND", "net": "GND"},
                {"number": "3", "name": "PB0", "net": "NET_LED"},
            ],
            "properties": {},
        },
        {
            "ref": "R1", "type": "resistor", "value": "330", "package": "0805",
            "mpn": None,
            "pins": [
                {"number": "1", "name": "1", "net": "NET_LED"},
                {"number": "2", "name": "2", "net": "VCC"},
            ],
            "properties": {},
        },
        {
            "ref": "LED1", "type": "led", "value": "red", "package": "0805",
            "mpn": None,
            "pins": [
                {"number": "1", "name": "A", "net": "NET_LED"},
                {"number": "2", "name": "K", "net": "GND"},
            ],
            "properties": {},
        },
        {
            "ref": "C1", "type": "capacitor", "value": "100nF", "package": "0603",
            "mpn": None,
            "pins": [
                {"number": "1", "name": "1", "net": "VCC"},
                {"number": "2", "name": "2", "net": "GND"},
            ],
            "properties": {},
        },
    ],
    "nets": [
        {"name": "VCC", "pins": ["U1.VCC", "R1.2", "C1.1"], "class": "power"},
        {"name": "GND", "pins": ["U1.GND", "LED1.K", "C1.2"], "class": "ground"},
        {"name": "NET_LED", "pins": ["U1.PB0", "R1.1", "LED1.A"], "class": "signal"},
    ],
}


def test_find_mcu_by_value():
    c = find_mcu(MCU_NETLIST, "attiny85")
    assert c["ref"] == "U1"


def test_find_mcu_fallback_to_ic():
    c = find_mcu(MCU_NETLIST, None)
    assert c["type"] == "ic"


def test_derive_pinmap_roles():
    pm = derive_pinmap(MCU_NETLIST, "ATtiny85")
    assert pm.mcu_ref == "U1"
    by_pin = {a.pin: a for a in pm.assignments}
    assert by_pin["PB0"].role == "gpio"
    assert by_pin["PB0"].net == "NET_LED"
    assert "LED1" in by_pin["PB0"].peripherals
    assert by_pin["VCC"].role == "power"
    assert by_pin["VCC"].peripherals == []
    assert by_pin["GND"].role == "ground"


def test_template_fallback_compiles_shape():
    pm = derive_pinmap(MCU_NETLIST)
    src = template_fallback(pm, "blink LED")
    assert "void setup(void)" in src
    assert "void loop(void)" in src
    assert "pinMode(" in src
    assert "digitalWrite(" in src
    assert "PB0" in src


def test_generate_firmware_no_model_falls_back():
    res = generate_firmware(MCU_NETLIST, "ATtiny85", "blink LED", use_model=False)
    assert res["language"] == "C++ (Arduino)"
    assert "void setup(void)" in res["source"]
    assert "fallback" in res["build_notes"]


def test_build_prompt_contains_context():
    pm = derive_pinmap(MCU_NETLIST)
    p = build_prompt(MCU_NETLIST, pm, "blink the LED")
    assert "ATtiny85" in p
    assert "blink the LED" in p
    assert "PB0" in p


def test_api_site_sentinels_present_in_source():
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gen.py")) as f:
        src = f.read()
    assert OPENROUTER_CALL_START in src
    assert OPENROUTER_CALL_END in src
    # exactly one live call site block
    assert src.count("urlopen") == 1


def test_strip_markdown():
    assert _strip_markdown("```cpp\nint x;\n```") == "int x;"
    assert _strip_markdown("plain") == "plain"


def test_pinmap_format():
    pm = derive_pinmap(MCU_NETLIST)
    txt = format_pinmap(pm)
    assert "MCU: U1 (ATtiny85)" in txt
    assert "PB0" in txt


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failures += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    sys.exit(1 if failures else 0)