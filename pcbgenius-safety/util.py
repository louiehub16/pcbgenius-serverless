"""
PCBGenius — D1 Bulletproof Beginner Layers: shared helpers
===========================================================

Small pure helpers shared across `allowlist`, `constraints`, and `refusals`.
No I/O, no network, no filesystem — safe to call from any context (backend
routes, CLI, tests, fine-tuned model tool plumbing).

Violation shape used throughout the safety layer (mirrors the backend DRC/ERC
shape so it can ride the same wire envelope):

    { "rule": str, "severity": "error"|"warning"|"info",
      "location": str, "message": str }
"""

from __future__ import annotations

import re
from typing import Any, Dict

# Contract tool-called severity strings (contract Section 2).
ERROR = "error"
WARNING = "warning"
INFO = "info"

# Pin functions that denote a power rail on an IC.
_POWER_PIN_RE = re.compile(
    r"^(VCC|VDD|V\+|VIN|VBAT|VBUS|3V3|5V|AVCC|VEE|VSS2?|VPP|VS?SV)",
    re.IGNORECASE,
)
# Pin functions that denote ground.
_GROUND_PIN_RE = re.compile(r"^(GND|VSS|VEE|AGND|DGND|PGND|0V)", re.IGNORECASE)


def violation(
    rule: str,
    message: str,
    location: str = "design",
    severity: str = ERROR,
) -> Dict[str, Any]:
    """Build a single homogeneous safety violation dict."""
    return {
        "rule": rule,
        "severity": severity,
        "location": location,
        "message": message,
    }


def is_power_pin(pin_name: str) -> bool:
    """True if a pin's function name looks like a power-rail pin (VCC/VDD/VIN...)."""
    return bool(_POWER_PIN_RE.match((pin_name or "").strip()))


def is_ground_pin(pin_name: str) -> bool:
    """True if a pin's function name looks like a ground pin (GND/VSS/AGND...)."""
    return bool(_GROUND_PIN_RE.match((pin_name or "").strip()))


def power_pins_of(component: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Return the power-rail pins of a component (requires an explicit power pin)."""
    return [p for p in component.get("pins", []) if is_power_pin(p.get("name", ""))]


def net_voltage_guess(net_name: str, netlist: Dict[str, Any]) -> float | None:
    """Best-effort rail voltage (volts) for a net by name/class.

    Explicit source: look for a `power` component or an `ic` voltage-regulator
    whose output net matches, then fall back to decoding common rail names
    (3V3, 5V, 12V, VBUS=5V, VIN=12V...). Returns None when unknown — callers
    treat unknown rails as "apply baseline clearance" (no false alarms).
    """
    name = (net_name or "").upper()
    # Explicit net voltage annotation, if the model/editor provided one.
    for net in netlist.get("nets", []):
        if (net.get("name") or "").upper() == name and net.get("voltage") is not None:
            return float(net["voltage"])

    # Source components physically impose a rail voltage.
    for comp in netlist.get("components", []):
        if comp.get("type") == "power" and comp.get("value"):
            # e.g. "5V", "12V", "3.3V" — tie to whichever net its OUT pin feeds.
            for pin in comp.get("pins", []):
                if pin.get("name", "").upper() in ("OUT", "VOUT", "VO") and (
                    pin.get("net") or ""
                ).upper() == name:
                    v = _decode_voltage(str(comp["value"]))
                    if v is not None:
                        return v

    # Common rail-name decode as a last resort.
    m = re.search(r"(\d+(?:\.\d+)?)\s*V", name)
    if m:
        volts = float(m.group(1))
        if 0.0 < volts <= 60.0:
            return volts
    if name in ("VBUS", "USB_5V", "USB5V", "+5V"):
        return 5.0
    if name in ("VIN", "VCC_12", "12V"):
        return 12.0
    if name in ("3V3", "3.3V", "VCC_3V3"):
        return 3.3
    return None


def _decode_voltage(value: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*V", str(value).upper())
    return float(m.group(1)) if m else None


def min_clearance_mm(voltage: float | None) -> float:
    """IPC-2221-derived minimum copper clearance (mm) scaled by rail voltage.

    Blockchain/beginner-safe minimums used by the spec-checker when no explicit
    layout spacing data is present. Coarse but deterministic and conservative.
    """
    if voltage is None:
        return 0.2  # baseline / unknown rail
    if voltage < 15.0:
        return 0.13
    if voltage < 30.0:
        return 0.25
    if voltage < 50.0:
        return 0.5
    if voltage < 100.0:
        return 1.0
    return 2.0  # anything ≥100V — too hot for a beginner board