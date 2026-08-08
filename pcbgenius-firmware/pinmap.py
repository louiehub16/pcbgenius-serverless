#!/usr/bin/env python3
"""
PCBGenius D4 — derive_firmware_pinmap
=====================================
Given a FROZEN CONTRACT netlist (schema_version 1.0.0) this module derives the
MCU -> peripheral pin map: for every net the microcontroller is connected to,
which MCU pin(s) land on it and which other ("peripheral") refs share the net.

Contract note (Wave A / run+behavior netlists): the microcontroller is always a
component of type "ic" whose `value` names the part (e.g. "ATtiny85", "U1").
The optional `mcu` argument (a free-form string from the generate_firmware
arguments, e.g. "arduino-nano" or "ATtiny85") is matched case-insensitively
against component ref/value; when it matches nothing we fall back to the first
`ic` component.

This module is pure stdlib and deterministic — same netlist -> same pin map.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Net classes that are power/ground and are *not* GPIO peripherals we drive.
POWER_CLASSES = {"power", "ground"}


@dataclass
class PinAssignment:
    """One MCU pin's role on a net."""

    pin: str                        # MCU pin name, e.g. "PB0"
    net: str                        # net name, e.g. "NET_LED"
    net_class: str                  # net class, e.g. "signal"
    role: str                       # "gpio" | "power" | "ground"
    peripherals: List[str] = field(default_factory=list)  # other refs on the net


@dataclass
class PinMap:
    """Full MCU wiring summary."""

    mcu_ref: str
    mcu_value: str
    assignments: List[PinAssignment] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mcu_ref": self.mcu_ref,
            "mcu_value": self.mcu_value,
            "assignments": [
                {
                    "pin": a.pin,
                    "net": a.net,
                    "net_class": a.net_class,
                    "role": a.role,
                    "peripherals": a.peripherals,
                }
                for a in self.assignments
            ],
        }


def _refs_with_pin_on_net(netlist: Dict[str, Any], net_name: str) -> List[str]:
    """All "ref.pin" of a given net, as raw strings from nets[].pins."""
    net = next((n for n in netlist.get("nets", []) if n.get("name") == net_name), None)
    if net is None:
        return []
    return list(net.get("pins", []))


def _ref_of(ref_pin: str) -> str:
    """Split "U1.PB0" -> "U1" (dotted, first segment wins)."""
    return ref_pin.split(".", 1)[0]


def find_mcu(netlist: Dict[str, Any], mcu: Optional[str] = None) -> Dict[str, Any]:
    """Locate the microcontroller component.

    Priority:
      1. an `ic` component whose ref == mcu (case-insensitive)
      2. an `ic` component whose value == mcu
      3. an `ic` component whose value contains mcu as a substring
      4. the first `ic` component in the netlist

    Raises ValueError when no usable microcontroller is present.
    """
    comps = [c for c in netlist.get("components", []) if c.get("type") == "ic"]
    if mcu:
        m = mcu.strip().lower()
        for c in comps:
            if c.get("ref", "").lower() == m:
                return c
        for c in comps:
            if c.get("value", "").lower() == m:
                return c
        for c in comps:
            if m and m in c.get("value", "").lower():
                return c
    if not comps:
        raise ValueError(
            "netlist has no `ic` (microcontroller) component; generate_firmware "
            "requires a design with an MCU to derive a pin map."
        )
    return comps[0]


def derive_pinmap(netlist: Dict[str, Any], mcu: Optional[str] = None) -> PinMap:
    """Derive the MCU pin map from a contract netlist.

    For every MCU `ic` pin we look up the net it is bound to (`Pin.net`) and the
    net's class. Everything else on that net is a peripheral driven/sensed via
    that MCU pin. Power and ground nets are recorded with role "power"/"ground"
    and no peripherals (they are supplies, not GPIO endpoints).
    """
    mcu_comp = find_mcu(netlist, mcu)
    mcu_ref = mcu_comp["ref"]
    net_class_by_name = {n.get("name"): n.get("class") for n in netlist.get("nets", [])}

    # Index: ref.pin -> component ref, so we never attribute an MCU net endpoint
    # to the MCU itself.
    assignments: List[PinAssignment] = []
    for pin in mcu_comp.get("pins", []):
        net_name = pin.get("net")
        if not net_name:
            continue
        net_class = net_class_by_name.get(net_name, "signal")
        other_refs = list(dict.fromkeys(
            _ref_of(rp) for rp in _refs_with_pin_on_net(netlist, net_name)
            if _ref_of(rp) != mcu_ref
        ))
        role = "ground" if net_class == "ground" else "power" if net_class == "power" else "gpio"
        assignments.append(
            PinAssignment(
                pin=pin.get("name") or pin.get("number"),
                net=net_name,
                net_class=net_class,
                role=role,
                peripherals=other_refs if role == "gpio" else [],
            )
        )

    return PinMap(mcu_ref=mcu_ref, mcu_value=mcu_comp.get("value", ""), assignments=assignments)


def format_pinmap(pinmap: PinMap) -> str:
    """Human/LLM-friendly rendering of the pin map, suitable for prompt building."""
    lines = [
        f"MCU: {pinmap.mcu_ref} ({pinmap.mcu_value})",
        "Pin map (MCU pin -> net -> role -> peripherals):",
    ]
    if not pinmap.assignments:
        lines.append("  (no connected pins)")
    for a in pinmap.assignments:
        peri = ", ".join(a.peripherals) if a.peripherals else "-"
        lines.append(f"  {a.pin or '?'} -> {a.net} [{a.net_class}, {a.role}] -> {peri}")
    return "\n".join(lines)


def load_netlist(path: str) -> Dict[str, Any]:
    """Load a netlist from a JSON file. Accepts both a bare netlist object and a
    {.., "netlist": {...}} wrapper row (as emitted by the data generator)."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "netlist" in data and "components" not in data:
        data = data["netlist"]
    return data


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Derive an MCU pin map from a contract netlist.")
    ap.add_argument("netlist", help="path to netlist JSON (bare or wrapped row)")
    ap.add_argument("--mcu", help="optional MCU ref/value to select the component")
    a = ap.parse_args()
    print(format_pinmap(derive_pinmap(load_netlist(a.netlist), a.mcu)))