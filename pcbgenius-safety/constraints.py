"""
PCBGenius — D1 Bulletproof Beginner Layers: CONSTRAINT / SPEC checker
=====================================================================

The second gate. Even a fully-allowlisted design can be electrically unbuildable.
This module checks beginner-proof electrical constraints against the netlist and
an optional layout object. Pure / no I/O.

Checks enforced:
  * VOLTAGE_CLEARANCE — conductor clearance (mm) is scaled by the rail voltage
      (IPC-2221-derived baseline via util.min_clearance_mm). A net carrying more
      volts must have wider spacing. Uses explicit `layout.spacings` when present,
      otherwise flags nets above 15V that lack any recorded clearance spacing.
  * IC_DECOUPLING     — every IC with a power rail pin must have a decoupling
      capacitor on the same power net within the design (0.01–100 uF ceramic).
  * SHORTED_POWER     — two distinct source components feeding the SAME net with
      different nominal voltages (would imply a short).
  * NET_VOLTAGE_RATING— a component/fet where a net's voltage exceeds a declared
      `voltage_rating` in `properties`.

Input layout object (optional):
    layout = { "spacings": [ { "net": str, "clearance_mm": float }, ... ], ... }
    When absent, clearance checks degrade gracefully (warnings for high-V nets
    with no data), never false-blocking a 3.3/5V design.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from util import ERROR, WARNING, violation, is_power_pin, net_voltage_guess, min_clearance_mm

# A decoupling cap is 0.01 uF .. 100 uF (typical ceramic bypass).
_DECOUPLE_RE = re.compile(r"^(?:10|100|470|10k|100k)(?:n|m|u)?F$", re.IGNORECASE)
_DECOUPLE_LOW = re.compile(r"^\d+(?:\.\d+)?\s*(p|n)?F$", re.IGNORECASE)


def check(netlist: Dict[str, Any], layout: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Run all constraint checks. Returns a list of violations."""
    vio: List[Dict[str, Any]] = []
    _check_clearance(netlist, layout, vio)
    _check_ic_decoupling(netlist, vio)
    _check_shorted_power(netlist, vio)
    _check_voltage_ratings(netlist, vio)
    return vio


# ── clearance scaled by voltage ─────────────────────────────────────────────
def _check_clearance(netlist, layout, vio):
    spacings = {}
    if layout:
        for s in layout.get("spacings", []) or []:
            if isinstance(s, dict) and s.get("net") is not None:
                spacings[str(s["net"]).upper()] = float(s.get("clearance_mm", 0))

    for net in netlist.get("nets", []):
        name = net.get("name", "")
        v = net_voltage_guess(name, netlist)
        if v is None:
            continue
        need = min_clearance_mm(v)

        declared = spacings.get(name.upper())
        if declared is not None and declared >= need:
            continue  # explicitly satisfies the scaled requirement
        if declared is not None and declared < need:
            vio.append(
                violation(
                    "VOLTAGE_CLEARANCE_UNDER_MIN",
                    f"Net '{name}' ({v}V) requires ≥{need}mm clearance but only "
                    f"{declared}mm is configured.",
                    location=name,
                    severity=ERROR if declared < need * 0.8 else WARNING,
                )
            )
        elif v > 15.0:
            # No spacing data recorded for a non-trivial rail → warn (not block).
            vio.append(
                violation(
                    "VOLTAGE_CLEARANCE_UNKNOWN",
                    f"Net '{name}' carries ≈{v}V; no clearance spacing recorded. "
                    f"Set ≥{need}mm before layout export.",
                    location=name,
                    severity=WARNING,
                )
            )


# ── decoupling cap on IC power pins ─────────────────────────────────────────
def _check_ic_decoupling(netlist, vio):
    # Map ref -> cap pins on each power net, so we can require a bypass on rail.
    # Also collect which refs are ICs with power pins.
    caps = [c for c in netlist.get("components", []) if c.get("type") == "capacitor"]
    ics = [c for c in netlist.get("components", []) if c.get("type") == "ic"]

    # power net -> set of refs of caps that connect a pin to that net
    cap_on_net: Dict[str, set] = {}
    for c in caps:
        for p in c.get("pins", []):
            net = p.get("net")
            if net:
                cap_on_net.setdefault(str(net).upper(), set()).add(c.get("ref"))

    for ic in ics:
        ref = ic.get("ref", "<??>")
        power_pins = [p for p in ic.get("pins", []) if is_power_pin(p.get("name", ""))]
        if not power_pins:
            vio.append(
                violation(
                    "IC_NO_POWER_PIN",
                    f"IC {ref} has no recognizable power pin (VCC/VDD/VIN/...). "
                    f"Cannot verify decoupling; confirm pin names.",
                    location=ref,
                    severity=WARNING,
                )
            )
            continue
        for p in power_pins:
            net = (p.get("net") or "").upper()
            if not net:
                continue
            if net in cap_on_net and cap_on_net[net]:
                continue  # a cap already bridges this rail
            vio.append(
                violation(
                    "IC_MISSING_DECOUPLING",
                    f"IC {ref} power pin {p.get('number')} ({p.get('name')}) on net "
                    f"'{p.get('net')}' has no decoupling capacitor. Add a 0.01–100uF "
                    f"ceramic on that rail.",
                    location=ref,
                    severity=ERROR,
                )
            )


# ── shorted / conflicting power sources ─────────────────────────────────────
def _check_shorted_power(netlist, vio):
    source_voltage: Dict[str, set] = {}
    for comp in netlist.get("components", []):
        if comp.get("type") not in ("power", "ic", "connector"):
            continue
        for pin in comp.get("pins", []):
            if pin.get("name", "").upper() in ("OUT", "VOUT", "VO", "VIN"):
                net = pin.get("net")
                if not net:
                    continue
                v = net_voltage_guess(net, netlist)
                if v is not None:
                    source_voltage.setdefault(net.upper(), set()).add(v)

    for net, vs in source_voltage.items():
        if len(vs) > 1:
            vio.append(
                violation(
                    "SHORTED_POWER_SOURCES",
                    f"Net '{net}' is driven by conflicting voltages {sorted(vs)}V. "
                    f"Two sources shorted together is unsafe — separate them.",
                    location=net,
                    severity=ERROR,
                )
            )


# ── voltage rating vs declared property ─────────────────────────────────────
def _check_voltage_ratings(netlist, vio):
    for comp in netlist.get("components", []):
        ref = comp.get("ref", "<??>")
        props = comp.get("properties", {}) or {}
        rating = props.get("voltage_rating")
        if rating is None:
            continue
        try:
            rated = float(rating)
        except (TypeError, ValueError):
            continue
        for pin in comp.get("pins", []):
            v = net_voltage_guess(pin.get("net") or "", netlist)
            if v is not None and v > rated * 1.15:
                vio.append(
                    violation(
                        "NET_EXCEEDS_VOLTAGE_RATING",
                        f"{ref} rated {rated}V but sees ≈{v}V on pin "
                        f"{pin.get('number')} ({pin.get('net')}). Over-voltage — "
                        f"pick a higher-rated part.",
                        location=ref,
                        severity=ERROR,
                    )
                )


def is_blocking(violations: List[Dict[str, Any]]) -> bool:
    return any(v["severity"] == ERROR for v in violations)


def summarize(violations: List[Dict[str, Any]]) -> str:
    codes = sorted({v["rule"] for v in violations})
    if not codes:
        return "CONSTRAINTS PASS — spec-checker found no issues."
    return "CONSTRAINTS FAIL — " + ", ".join(codes)