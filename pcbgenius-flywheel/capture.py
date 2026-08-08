"""
PCBGenius — E3 Self-Improving Flywheel: capture
================================================
Appends one JSONL record per design interaction to `capture_log.jsonl`:

    {
      "prompt": str,
      "netlist": { ... contract v1.0.0 ... },
      "verdict": {
         "pass": bool,
         "score": float,
         "dimensions": { <d1|d2|d3|d4>: {"pass": bool, "score": float, "issues": [str]} },
         "summary": str
      },
      "corrected_netlist": { ... } | null
    }

The 4D verdict scores a design on four independent axes:
    d1 struct        — schema_version + shape integrity (refs unique, valid keys)
    d2 connectivity  — >=1 power net, >=1 ground net, every pin->net resolves,
                       every net.pins -> ref.pin resolves
    d3 components    — component types in the contract set, refs/values non-empty
    d4 fab / drc     — heuristic fab sanity (power & ground nets not single-pin,
                       no dangling single-pin signal nets, board_layers present)

`corrected_netlist` is present when the design fails and a deterministic repair
could be produced (drop-junk + re-tie heuristic). All writes are append-only.
"""

from __future__ import annotations

import json
import os
import random
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

CONTRACT_VERSION = "1.0.0"
CREATED_BY = "pcbgenius"
DEFAULT_LOG = Path(__file__).parent / "capture_log.jsonl"

VALID_TYPES = {
    "resistor", "capacitor", "inductor", "diode", "led", "transistor",
    "ic", "connector", "power", "crystal", "switch",
}
NET_CLASSES = {"power", "ground", "signal", "clock", "analog", "digital"}
DIMENSIONS = ("d1", "d2", "d3", "d4")


# --------------------------------------------------------------------------- helpers
def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dim(pass_: bool, issues: list[str]) -> dict:
    return {"pass": bool(pass_), "score": 1.0 if pass_ else 0.0, "issues": list(issues)}


def score_four_dimensional(netlist) -> dict:
    """Return the 4D verdict for a netlist. Never raises on malformed input."""
    issues: dict[str, list[str]] = {k: [] for k in DIMENSIONS}
    nl = netlist if isinstance(netlist, dict) else {}
    comps = nl.get("components", [])
    nets = nl.get("nets", [])
    classes = {n.get("class") for n in nets if isinstance(n, dict)}
    class_of = {n.get("name"): n.get("class") for n in nets if isinstance(n, dict)}
    name_of_net = {n.get("name") for n in nets if isinstance(n, dict)}

    # --- d1 struct ---------------------------------------------------------
    if nl.get("schema_version") != CONTRACT_VERSION:
        issues["d1"].append("schema_version != 1.0.0")
    if not isinstance(comps, list):
        issues["d1"].append("components is not a list")
    if not isinstance(nets, list):
        issues["d1"].append("nets is not a list")
    refs = [c.get("ref") for c in comps if isinstance(c, dict)]
    if len(refs) != len(set(refs)):
        issues["d1"].append("duplicate component ref")

    # --- d2 connectivity ---------------------------------------------------
    if "ground" not in classes:
        issues["d2"].append("no ground net")
    if "power" not in classes:
        issues["d2"].append("no power net")
    for c in comps:
        if not isinstance(c, dict):
            continue
        for p in c.get("pins", []):
            if not isinstance(p, dict):
                continue
            if p.get("net") not in name_of_net:
                issues["d2"].append(f"{c.get('ref')}.{p.get('name')} -> missing net")
    valid_pins = {
        f"{c.get('ref')}.{p.get('name')}"
        for c in comps if isinstance(c, dict)
        for p in c.get("pins", []) if isinstance(p, dict)
    }
    for n in nets:
        if not isinstance(n, dict):
            continue
        for rp in n.get("pins", []):
            if rp not in valid_pins:
                issues["d2"].append(f"net {n.get('name')} refs unknown pin {rp}")
    for n in nets:
        if isinstance(n, dict) and not n.get("pins"):
            issues["d2"].append(f"net {n.get('name')} has no pins")

    # --- d3 components -----------------------------------------------------
    for c in comps:
        if not isinstance(c, dict):
            issues["d3"].append("component is not an object")
            continue
        if c.get("type") not in VALID_TYPES:
            issues["d3"].append(f"{c.get('ref')}: invalid type {c.get('type')}")
        if not c.get("ref"):
            issues["d3"].append("component missing ref")
        if not c.get("value"):
            issues["d3"].append(f"{c.get('ref')}: missing value")
        if not isinstance(c.get("properties"), dict):
            issues["d3"].append(f"{c.get('ref')}: properties not an object")

    # --- d4 fab / drc ------------------------------------------------------
    if not nl.get("metadata", {}).get("board_layers"):
        issues["d4"].append("metadata missing board_layers")
    # power/ground nets should not be single-pin
    for n in nets:
        if isinstance(n, dict) and n.get("class") in ("power", "ground") \
                and len(n.get("pins", [])) < 2:
            issues["d4"].append(f"{n.get('name')} net has <2 pins")
    # no dangling single-pin signal nets
    for n in nets:
        if isinstance(n, dict) and n.get("class") not in ("power", "ground") \
                and len(n.get("pins", [])) == 1:
            issues["d4"].append(f"dangling single-pin net {n.get('name')}")

    dims = {k: _dim(not issues[k], issues[k]) for k in DIMENSIONS}
    overall = all(d["pass"] for d in dims.values())
    score = sum(d["score"] for d in dims.values()) / len(dims)
    summary = "PASS" if overall else "FAIL: " + "; ".join(
        f"{k}={d['pass']}" for k, d in dims.items() if not d["pass"])
    return {
        "pass": overall,
        "score": round(score, 3),
        "dimensions": dims,
        "summary": summary,
    }


def validate_netlist(nl):
    """Struct-conformance check mirroring the frozen contract rules (bool, errs)."""
    v = score_four_dimensional(nl)
    errs = [i for d in v["dimensions"].values() for i in d["issues"]]
    return v["pass"], errs


# --------------------------------------------------------------------------- design fixtures
def _comp(ref, ctype, value, package, pins, mpn=None):
    p = [{"number": str(i + 1), "name": n, "net": net_} for i, (n, net_) in enumerate(pins)]
    return {"ref": ref, "type": ctype, "value": value, "package": package,
            "mpn": mpn, "pins": p, "properties": {}}


def _net(name, cls, pins):
    return {"name": name, "pins": pins, "class": cls}


def generate_design(seed: int):
    """Deterministic contract-valid design, cycled through fixture templates.

    Every 5th seed is deliberately corrupted so the curation/fix path is exercised.
    Returns (prompt: str, netlist: dict, skill: str).
    """
    templates = [_ldo, _led_blinker, _buck, _usb_power]
    prompt, nl, skill = templates[seed % len(templates)](seed)
    if seed % 5 == 0:                       # inject a defect for curation coverage
        _corrupt(nl)
    return prompt, nl, skill


def _corrupt(nl):
    """Make an otherwise-valid netlist fail the 4D verdict (drop its ground net)."""
    nets = nl.get("nets", [])
    ground = next((i for i, n in enumerate(nets) if n.get("class") == "ground"), None)
    if ground is not None:
        del nets[ground]
    # orphan pin refs now point at a missing net name -> d2/d1 failures
    for c in nl.get("components", []):
        c["properties"]["corrupted"] = True


def _ldo(seed):
    vin = ["5V", "12V", "24V"][seed % 3]
    comps = [
        _comp("U1", "ic", "AMS1117-3.3", "SOT-223",
              [("VIN", "VIN"), ("GND", "GND"), ("VOUT", "VCC_3V3")], "AMS1117-3.3"),
        _comp("C1", "capacitor", "10uF", "0805", [("1", "VIN"), ("2", "GND")]),
        _comp("C2", "capacitor", "10uF", "0805", [("1", "VCC_3V3"), ("2", "GND")]),
    ]
    nets = [
        _net("VIN", "power", ["U1.VIN", "C1.1"]),
        _net("GND", "ground", ["U1.GND", "C1.2", "C2.2"]),
        _net("VCC_3V3", "power", ["U1.VOUT", "C2.1"]),
    ]
    nl = {"schema_version": CONTRACT_VERSION,
          "metadata": {"design_name": f"ldo_{vin}_3v3", "board_layers": 2,
                       "description": f"Linear regulator {vin}->3.3V",
                       "created_by": CREATED_BY, "target_fab": None},
          "components": comps, "nets": nets}
    prompt = f"Design a {vin} to 3.3V linear regulator using an AMS1117 with bypass caps."
    return prompt, nl, "netlist_design"


def _led_blinker(seed):
    comps = [
        _comp("U1", "ic", "ATtiny85", "DIP-8",
              [("VCC", "VCC"), ("GND", "GND"), ("PB0", "NET_LED")], "ATTINY85-20PU"),
        _comp("R1", "resistor", "330", "0805", [("1", "NET_LED"), ("2", "VCC")]),
        _comp("LED1", "led", "red", "0805", [("A", "NET_LED"), ("K", "GND")]),
        _comp("C1", "capacitor", "100nF", "0603", [("1", "VCC"), ("2", "GND")]),
    ]
    nets = [
        _net("VCC", "power", ["U1.VCC", "R1.2", "C1.1"]),
        _net("GND", "ground", ["U1.GND", "LED1.K", "C1.2"]),
        _net("NET_LED", "signal", ["U1.PB0", "R1.1", "LED1.A"]),
    ]
    nl = {"schema_version": CONTRACT_VERSION,
          "metadata": {"design_name": "led_blinker", "board_layers": 2,
                       "description": "ATtiny85 blinks an LED",
                       "created_by": CREATED_BY, "target_fab": None},
          "components": comps, "nets": nets}
    prompt = "Design an LED blink circuit with an ATtiny85 and current-limiting resistor."
    return prompt, nl, "netlist_design"


def _buck(seed):
    vin = ["9V", "12V", "24V"][seed % 3]
    comps = [
        _comp("U1", "ic", "LM2596S-ADJ", "TO-263",
              [("VIN", "VIN"), ("GND", "GND"), ("OUT", "SW"), ("FB", "FB")], "LM2596S-ADJ"),
        _comp("D1", "diode", "SS34", "SMA", [("A", "SW"), ("K", "VOUT")], "SS34"),
        _comp("L1", "inductor", "33uH", "CDRH8D28", [("1", "SW"), ("2", "VOUT")]),
        _comp("C1", "capacitor", "100uF", "10x10mm", [("1", "VIN"), ("2", "GND")]),
        _comp("C2", "capacitor", "220uF", "10x10mm", [("1", "VOUT"), ("2", "GND")]),
        _comp("R1", "resistor", "1k", "0805", [("1", "VOUT"), ("2", "FB")]),
        _comp("R2", "resistor", "3.3k", "0805", [("1", "FB"), ("2", "GND")]),
    ]
    nets = [
        _net("VIN", "power", ["U1.VIN", "C1.1"]),
        _net("GND", "ground", ["U1.GND", "D1.K", "C1.2", "C2.2", "R2.2"]),
        _net("SW", "power", ["U1.OUT", "D1.A", "L1.1"]),
        _net("VOUT", "power", ["D1.K", "L1.2", "C2.1", "R1.1"]),
        _net("FB", "analog", ["U1.FB", "R1.2", "R2.1"]),
    ]
    nl = {"schema_version": CONTRACT_VERSION,
          "metadata": {"design_name": f"buck_{vin}_5v", "board_layers": 2,
                       "description": f"Buck converter {vin}->5V with LM2596",
                       "created_by": CREATED_BY, "target_fab": "jlcpcb"},
          "components": comps, "nets": nets}
    prompt = f"Design a {vin} to 5V buck converter with LM2596S, Schottky diode and output inductor."
    return prompt, nl, "netlist_design"


def _usb_power(seed):
    comps = [
        _comp("J1", "connector", "USB-C", "USB-C-31",
              [("VBUS", "VBUS"), ("GND", "GND"), ("CC1", "CC1"), ("CC2", "CC2")], "USB-C"),
        _comp("F1", "resistor", "0ohm", "0805", [("1", "VBUS"), ("2", "VBUS_F")], ""),
        _comp("C1", "capacitor", "10uF", "0805", [("1", "VBUS_F"), ("2", "GND")]),
        _comp("C2", "capacitor", "100nF", "0603", [("1", "VBUS_F"), ("2", "GND")]),
        _comp("D1", "diode", "ESD", "SOT-23", [("1", "VBUS_F"), ("2", "GND")], "USBLC6-2SC6"),
    ]
    nets = [
        _net("VBUS", "power", ["J1.VBUS", "F1.1"]),
        _net("GND", "ground", ["J1.GND", "C1.2", "C2.2", "D1.2"]),
        _net("VBUS_F", "power", ["F1.2", "C1.1", "C2.1", "D1.1"]),
        _net("CC1", "analog", ["J1.CC1"]),
        _net("CC2", "analog", ["J1.CC2"]),
    ]
    nl = {"schema_version": CONTRACT_VERSION,
          "metadata": {"design_name": "usb_power", "board_layers": 2,
                       "description": "USB-C 5V power input with ESD + filtering",
                       "created_by": CREATED_BY, "target_fab": None},
          "components": comps, "nets": nets}
    prompt = "Design a USB-C power input giving clean 5V with ESD protection and filtering."
    return prompt, nl, "netlist_design"


# --------------------------------------------------------------------------- repair
def apply_fix(original) -> dict | None:
    """Deterministic repair heuristic. Returns a corrected netlist or None.

    Strategy: drop junk — remove dangling single-pin signal nets, remove
    unknown-pin refs, drop non-object entries, drop corrupted metadata flags —
    then re-establish a ground net if missing by tying it to a free pin so the
    repaired design re-passes the 4D verdict.
    """
    if not isinstance(original, dict):
        return None
    nl = deepcopy(original)
    nl.setdefault("schema_version", CONTRACT_VERSION)
    nl.setdefault("metadata", {"board_layers": 2, "design_name": "repaired",
                               "description": "auto-repaired", "created_by": CREATED_BY,
                               "target_fab": None})
    nl["metadata"]["board_layers"] = nl["metadata"].get("board_layers") or 2
    comps = [c for c in nl.get("components", []) if isinstance(c, dict)]
    # strip corrupted flags
    for c in comps:
        c["properties"].pop("corrupted", None)
    # drop component-orphan pins (missing net)
    for c in comps:
        c["pins"] = [p for p in c.get("pins", []) if isinstance(p, dict)
                     and p.get("net") is not None]

    # Preserve the declared class per net name; fall back to a name-based
    # convention so a DELETED declaration (e.g. ground) is restored correctly.
    class_of = {n.get("name"): n.get("class")
                for n in nl.get("nets", []) if isinstance(n, dict)}

    def classify(name: str) -> str:
        c = class_of.get(name)
        if c in NET_CLASSES:
            return c
        up = name.upper()
        if up == "GND" or up.startswith("GND"):
            return "ground"
        if any(up == p or up.startswith(p) for p in ("VCC", "VIN", "VOUT", "VBUS",
                                                     "PWR", "SW", "3V3", "5V")):
            return "power"
        return "signal"

    # rebuild net.pins from component pins that reference each net (resolves d2)
    valid_pins = {
        f"{c['ref']}.{p['name']}": p["net"]
        for c in comps for p in c.get("pins", [])
    }
    by_name: dict[str, list[str]] = {}
    for pin, net in valid_pins.items():
        by_name.setdefault(net, []).append(pin)

    rebuilt = []
    for name, pins in by_name.items():
        cls = classify(name)
        # drop dangling single-pin signal nets, but keep power/ground even sparse
        if cls not in ("power", "ground") and len(pins) <= 1:
            continue
        rebuilt.append(_net(name, cls, pins))

    # Re-establish a power/ground net if the repair left either missing.
    classes = {n.get("class") for n in rebuilt}
    if "ground" not in classes:
        gname = next((nm for nm, cls in class_of.items() if cls == "ground"), "GND")
        gpins = [p for p in valid_pins if classify(valid_pins[p]) == "ground"]
        if gpins:
            for n in rebuilt:
                if n["name"] == gname:
                    n["class"] = "ground"
                    break
            else:
                rebuilt.append(_net(gname, "ground", gpins))
    if "power" not in classes:
        pname = next((nm for nm, cls in class_of.items() if cls == "power"), "VCC")
        ppins = [p for p in valid_pins if classify(valid_pins[p]) == "power"]
        if ppins:
            for n in rebuilt:
                if n["name"] == pname:
                    n["class"] = "power"
                    break
            else:
                rebuilt.append(_net(pname, "power", ppins))

    nl["components"], nl["nets"] = comps, rebuilt
    ok, _ = validate_netlist(nl)
    return nl if ok else None


# --------------------------------------------------------------------------- capture
def capture(prompt: str, netlist, verdict: dict | None = None,
            corrected_netlist=None, log_path=DEFAULT_LOG):
    """Append a design+verdict+fix record to the capture log (jsonl)."""
    if verdict is None:
        verdict = score_four_dimensional(netlist)
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "prompt": prompt,
        "netlist": netlist,
        "verdict": verdict,
        "corrected_netlist": corrected_netlist,
        "captured_at": now_utc(),
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def capture_design(prompt: str, netlist, log_path=DEFAULT_LOG):
    """Capture a design end-to-end: score it, attempt a fix if it fails.

    Mirrors the real flywheel loop (design -> 4D verdict -> corrective fix).
    Returns the appended record.
    """
    verdict = score_four_dimensional(netlist)
    corrected = None
    if not verdict["pass"]:
        corrected = apply_fix(netlist)
        if corrected is not None:
            verdict = score_four_dimensional(corrected)
    return capture(prompt, netlist, verdict=verdict,
                   corrected_netlist=corrected, log_path=log_path)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="PCBGenius flywheel capture")
    ap.add_argument("--count", type=int, default=20, help="designs to capture")
    ap.add_argument("--log", default=str(DEFAULT_LOG), help="capture log path")
    a = ap.parse_args()
    written = 0
    for seed in range(a.count):
        prompt, nl, _ = generate_design(seed)
        capture_design(prompt, nl, log_path=a.log)
        written += 1
    print(f"[capture] captured {written} designs -> {a.log}")


if __name__ == "__main__":
    main()