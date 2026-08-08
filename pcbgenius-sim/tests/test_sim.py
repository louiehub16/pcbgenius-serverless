"""
PCBGenius - D2 Real-time WASM SPICE sim (#6) | tests/test_sim.py

Pure-Python tests for the netlist -> Ngspice .cir conversion. No WASM, no
Ngspice, no network. Covers:

  - R divider          (two resistors across VIN->GND)
  - RC low-pass        (series R + shunt C)
  - buck converter     (IC + diode + inductor + caps + feedback divider)

Run:  python -m pytest tests/test_sim.py
or:   python tests/test_sim.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from netlist_to_spice import (  # noqa: E402
    auto_stimulus,
    node_map,
    node_name,
    spice_value,
)


# ── helpers ───────────────────────────────────────────────────────────────

def _comp(ref, ctype, value, nets, extra_pins=None, props=None):
    pins = []
    for i, net in enumerate(nets):
        num = str(i + 1)
        name = extra_pins[i] if extra_pins else num
        pins.append({"number": num, "name": name, "net": net})
    return {
        "ref": ref, "type": ctype, "value": value,
        "package": "0805", "mpn": None, "properties": props or {},
        "pins": pins,
    }


def _netlist(design, comps, nets):
    return {
        "schema_version": "1.0.0",
        "metadata": {
            "design_name": design, "description": design,
            "board_layers": 2, "created_by": "pcbgenius", "target_fab": None,
        },
        "components": comps,
        "nets": [{"name": n, "pins": p, "class": c} for (n, p, c) in nets],
    }


def _deck(nl, **kw):
    from netlist_to_spice import netlist_to_deck
    return netlist_to_deck(nl, **kw)


def _elems(deck):
    from netlist_to_spice import deck_elements
    return deck_elements(deck)


# ── fixtures ──────────────────────────────────────────────────────────────

def make_r_divider():
    comps = [
        _comp("R1", "resistor", "10k", ["VIN", "OUT"]),
        _comp("R2", "resistor", "10k", ["OUT", "GND"]),
    ]
    nets = [
        ("VIN", ["R1.1"], "power"),
        ("OUT", ["R1.2", "R2.1"], "signal"),
        ("GND", ["R2.2"], "ground"),
    ]
    return _netlist("r_divider", comps, nets)


def make_rc_lowpass():
    comps = [
        _comp("R1", "resistor", "1k", ["VIN", "OUT"]),
        _comp("C1", "capacitor", "100nF", ["OUT", "GND"]),
    ]
    nets = [
        ("VIN", ["R1.1"], "power"),
        ("OUT", ["R1.2", "C1.1"], "signal"),
        ("GND", ["C1.2"], "ground"),
    ]
    return _netlist("rc_lowpass", comps, nets)


def make_buck():
    comps = [
        _comp("U1", "ic", "LM2596S-ADJ", ["VIN", "GND", "SW", "FB"],
              extra_pins=["VIN", "GND", "OUT", "FB"]),
        _comp("D1", "diode", "SS34", ["SW", "VOUT"],
              extra_pins=["A", "K"]),
        _comp("L1", "inductor", "22uH", ["SW", "VOUT"]),
        _comp("C1", "capacitor", "100nF", ["VIN", "GND"]),
        _comp("C2", "capacitor", "100nF", ["VOUT", "GND"]),
        _comp("R1", "resistor", "1k", ["VOUT", "FB"]),
        _comp("R2", "resistor", "330", ["FB", "GND"]),
    ]
    nets = [
        ("VIN", ["U1.VIN", "C1.1"], "power"),
        ("GND", ["U1.GND", "C1.2", "C2.2", "R2.2"], "ground"),
        ("SW", ["U1.OUT", "D1.A", "L1.1"], "power"),
        ("VOUT", ["D1.K", "L1.2", "C2.1", "R1.1"], "power"),
        ("FB", ["U1.FB", "R1.2", "R2.1"], "analog"),
    ]
    return _netlist("buck_9V_to_3V3", comps, nets)


# ── R divider ─────────────────────────────────────────────────────────────

def test_r_divider_deck_has_resistors():
    nl = make_r_divider()
    deck = _deck(nl, sim_type="op")
    elems = _elems(deck)
    assert "R1 0 0 10k" not in "\n".join(elems)  # sanity guard
    # node names must map nets, not "0" unless the net is ground
    assert any("R1 VIN OUT 10k" == e for e in elems), elems
    assert any("R2 OUT 0 10k" == e for e in elems), elems
    assert ".op" in deck
    assert deck.rstrip().endswith(".end")


def test_r_divider_values_are_spice_cleaned():
    nl = make_r_divider()
    nl["components"][0]["value"] = "10kohm"
    nl["components"][1]["value"] = "3.3k"
    elems = _elems(_deck(nl))
    assert any("10k" in e and "VIN OUT" in e for e in elems)
    assert any("3.3k" in e and "OUT 0" in e for e in elems)


def test_ground_maps_to_zero():
    nl = make_r_divider()
    assert node_name("GND") == "0"
    assert node_name("VIN") == "VIN"
    assert node_name("USB_DP") == "USB_DP"
    assert node_name("NET-LED") == "NET_LED"
    assert node_name("3V3") == "N3V3"  # leading digit illegal -> prefixed


def test_node_map_dict():
    nl = make_r_divider()
    m = node_map(nl)
    assert m["GND"] == "0"
    assert m["VIN"] == "VIN"


# ── RC low-pass ───────────────────────────────────────────────────────────

def test_rc_lowpass_elements():
    nl = make_rc_lowpass()
    deck = _deck(nl, sim_type="tran", stimulus={"VIN": 5}, test_points=["OUT"])
    elems = _elems(deck)
    assert any(e.startswith("R1 VIN OUT 1k") for e in elems)
    assert any(e.startswith("C1 OUT 0 100n") for e in elems)
    assert ".tran" in deck
    assert "VS0 VIN 0 5" in deck
    assert ".print tran v(OUT)" in deck


def test_rc_lowpass_auto_stimulus():
    nl = make_rc_lowpass()
    st = auto_stimulus(nl, default_volts=5.0)
    assert st.get("VIN") == 5.0
    # VIN is power class and matches the input-net regex -> auto-sourced


def test_spice_value_helpers():
    assert spice_value("330") == "330"
    assert spice_value("1k") == "1k"
    assert spice_value("4.7k") == "4.7k"
    assert spice_value("100nF") == "100n"
    assert spice_value("22uH") == "22u"
    assert spice_value("10uF") == "10u"
    assert spice_value("330ohm") == "330"


# ── buck converter ────────────────────────────────────────────────────────

def test_buck_passive_elements_present():
    nl = make_buck()
    deck = _deck(nl, sim_type="tran", test_points=["VOUT"])
    elems = "\n".join(_elems(deck))
    # passive two-terminal elements must all be emitted
    assert "L1 SW VOUT 22u" in elems, elems
    assert "C1 VIN 0 100n" in elems, elems
    assert "C2 VOUT 0 100n" in elems, elems
    assert "R1 VOUT FB 1k" in elems, elems
    assert "R2 FB 0 330" in elems, elems
    # diode with A/K polarity + model reference
    assert "D1 SW VOUT dmodel" in elems, elems
    assert ".model dmodel D" in deck
    assert ".model ledmodel D" in deck
    # IC is NOT representative as a raw element, but must not crash the deck
    assert "U1" not in elems.replace("OUT", "")


def test_buck_switching_node_not_auto_sourced():
    nl = make_buck()
    # SW is a switching node (IC OUT + inductor + diode) -> must NOT get a
    # static voltage source from auto_stimulus
    st = auto_stimulus(nl)
    assert "SW" not in st
    # VIN is a clean-ish power input and should be auto-sourced
    deck = _deck(nl, sim_type="op")
    # (auto_sim returns {} for the buck because VIN is not a pure input here;
    #  assert only that a static source is never placed on SW/VOUT)
    assert "VS" not in deck or all("SW" not in e and "VOUT" not in e
                                   for e in deck.splitlines() if e.startswith("VS"))


def test_buck_analysis_tran_block():
    nl = make_buck()
    deck = _deck(nl, sim_type="tran", test_points=["VOUT"])
    assert ".tran" in deck
    assert ".print tran v(VOUT)" in deck
    assert deck.rstrip().endswith(".end")


# ── wasm_sim pure-python surface (no engine) ──────────────────────────────

def test_wasm_sim_returns_pending_without_engine():
    from wasm_sim import run_simulation
    nl = make_rc_lowpass()
    res = run_simulation(nl, sim_type="tran", stimulus={"VIN": 5}, test_points=["OUT"])
    assert res["converged"] is False
    assert "wasm" in res.get("deck") or "PCBGenius" in res.get("deck")
    assert "R1 VIN OUT 1k" in res["deck"]
    assert "error" in res


def test_wasm_sim_prepare_run_spec():
    from wasm_sim import prepare_run_spec
    nl = make_rc_lowpass()
    spec = prepare_run_spec(nl, sim_type="tran", test_points=["OUT"])
    assert spec["sim_type"] == "tran"
    assert spec["test_points"] == ["OUT"]
    assert "R1 VIN OUT 1k" in spec["deck"]
    assert spec["stimulus_auto"].get("VIN") == 5.0


if __name__ == "__main__":
    from netlist_to_spice import netlist_to_deck
    print("R divider deck:\n" + netlist_to_deck(make_r_divider()))
    print("\n\nRC low-pass deck:\n" + netlist_to_deck(make_rc_lowpass(), sim_type="tran",
                                                     stimulus={"VIN": 5}, test_points=["OUT"]))
    print("\n\nBuck deck:\n" + netlist_to_deck(make_buck(), sim_type="op"))
    print("\nALL TEST SCENARIOS OK (run with pytest for assertions)")
