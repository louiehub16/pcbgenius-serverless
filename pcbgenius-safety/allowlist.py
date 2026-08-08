"""
PCBGenius — D1 Bulletproof Beginner Layers: component/package/net ALLOWLIST
==========================================================================

The safety allowlist is the first gate a design must clear. Beginners are
shielded from exotic parts, hard-to-solder packages, mains-voltage nets, and
destructive / dangerous net names by constraining the design to a well-understood
subset of the FROZEN CONTRACT's component vocabulary.

Rules enforced here:
  * COMPONENT_TYPE  — `type` must be one of the 11 contract component types.
  * PACKAGE         — `package` must match a known-solderable beginner family.
  * NET_NAME        — net names must be a safe token (alnum + `_`), must not
                      contain a destructive keyword (contract Section 3
                      `destructive_keyword_blacklist`), and ordinary named nets
                      must carry the sanctioned `NET_` prefix. Power/ground
                      rails use the documented rail-name convention.
  * NET_CLASS       — `class` must be one of the 6 contract net classes.

Anything outside the allowlist is BLOCKED with a clear, actionable message.
This module is pure (no I/O) and is safe to import anywhere.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from util import ERROR, WARNING, violation

# Contract Section 1 component type enum — hard allowlist.
ALLOWED_COMPONENT_TYPES = {
    "resistor",
    "capacitor",
    "inductor",
    "diode",
    "led",
    "transistor",
    "ic",
    "connector",
    "power",
    "crystal",
    "switch",
}

# Contract Section 1 net class enum — hard allowlist.
ALLOWED_NET_CLASSES = {"power", "ground", "signal", "clock", "analog", "digital"}

# Beginner-solderable package families (scaled substrate, DIP, TO-xxx, etc.).
# Each entry is a regex matched case-insensitively against the whole package string.
ALLOWED_PACKAGE_PATTERNS = [
    # Chip resistors / ceramic caps / small inductors (imperial size codes).
    r"^(0?201|0?402|0?603|0805|1206|1210|1812)(-.*)?$",
    r"^(SMD-?)?0?603$",
    r"^(SMD-?)?(0805|1206|1210)$",
    r"^(0603|0805|1206)-(LED|R|C|L)$",
    # SOT / SOIC / TSSOP / DIP thru-hole and SMD ICs.
    r"^SOT-23(-[0-9])?$",
    r"^SOT-323$",
    r"^SOT-223$",
    r"^SOIC-(8|14|16)$",
    r"^SOP-(8|14|16)$",
    r"^TSSOP-(8|14|16|20)$",
    r"^DIP-(4|6|8|14|16|20)$",
    r"^TO-(92|220|251|252|263)$",
    # Diodes / LEDs.
    r"^(SOD-(123|323|523)|SMA|SMB|SMC)$",
    r"^LED-(3mm|5mm)$",
    # Connectors common in beginner kits.
    r"^USB-C(-31)?$",
    r"^JST-XH-[0-9](-P)?$",
    r"^Pin-Header-[12]x[0-9]+$",
    r"^THT-?Pin-?Header.*$",
    r"^TERMINAL-[0-9]+$",
    # Crystals.
    r"^(HC-49|[0-9]{4}|2520|3225)$",
    # Switches.
    r"^(SPDT|SPST|Push-Button|Tact|6mm)[-_].*$",
]

# Contract Section 3 — destructive keyword blacklist (lowercased substring check).
DESTRUCTIVE_KEYWORD_BLACKLIST = [
    "delete",
    "remove",
    "purge",
    "archive",
    "deactivate",
    "wipe",
    "cancel",
]

# DANGEROUS_HIGH_VOLTAGE: things a beginner board must never label a net.
DANGEROUS_NET_TOKENS = ["mains", "230v", "110v", "haz", "live", "neutral", "earth_hot"]

# Safe net naming: rails (documented) + generic signal nets with the NET_ prefix.
_RAIL_NET_RE = re.compile(r"^(GND|VCC|VDD|VSS|VIN|VOUT|VBUS|3V3|5V|12V|[+_-]?5?V|AGND|DGND)(_IN|_OUT)?$", re.IGNORECASE)
_SIGNAL_NET_RE = re.compile(r"^NET_[A-Z0-9_]+$")
_GENERIC_SAFE_RE = re.compile(r"^[A-Za-z0-9_]+$")
# Sanctioned USB/GPIO style nets are explicitly fine (common in contract fixtures).
_COMMON_NETS = {"USB_DP", "USB_DM", "USB_5V", "CC1", "EN", "SDA", "SCL", "TX", "RX"}


def check(netlist: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate a netlist against the allowlist. Returns a list of violations.

    Every violating item is reported; the caller decides pass/fail (any `error`
    severity blocks the design). Pure / side-effect free.
    """
    vio: List[Dict[str, Any]] = []
    comps = netlist.get("components", [])
    nets = netlist.get("nets", [])
    seen_refs = set()

    for comp in comps:
        ref = comp.get("ref", "<??>")
        ctype = comp.get("type", "")

        if ctype not in ALLOWED_COMPONENT_TYPES:
            vio.append(
                violation(
                    "SAFETY_TYPE_NOT_ALLOWLISTED",
                    f"Component {ref} uses type '{ctype}' which is outside the beginner "
                    f"allowlist. Allowed: {sorted(ALLOWED_COMPONENT_TYPES)}.",
                    location=ref,
                )
            )

        pkg = comp.get("package", "")
        if pkg and not _package_allowed(pkg):
            vio.append(
                violation(
                    "SAFETY_PACKAGE_NOT_ALLOWLISTED",
                    f"Component {ref} package '{pkg}' is not in the beginner-solderable "
                    f"allowlist. Pick a standard 0603/0805/1206/SOT-23/SOIC/DIP family.",
                    location=ref,
                )
            )

        if ref in seen_refs:
            vio.append(
                violation(
                    "SAFETY_DUPLICATE_REF",
                    f"Reference designator '{ref}' appears more than once (must be unique).",
                    location=ref,
                )
            )
        seen_refs.add(ref)

    for net in nets:
        name = net.get("name", "")
        nclass = net.get("class", "")

        if nclass not in ALLOWED_NET_CLASSES:
            vio.append(
                violation(
                    "SAFETY_NET_CLASS_NOT_ALLOWLISTED",
                    f"Net '{name}' uses class '{nclass}', outside the allowlist "
                    f"{sorted(ALLOWED_NET_CLASSES)}.",
                    location=name,
                )
            )

        lower = name.lower()
        if any(kw in lower for kw in DESTRUCTIVE_KEYWORD_BLACKLIST):
            vio.append(
                violation(
                    "SAFETY_DESTRUCTIVE_NET_NAME",
                    f"Net '{name}' contains a destructive keyword "
                    f"({DESTRUCTIVE_KEYWORD_BLACKLIST}) and is blocked by the contract.",
                    location=name,
                )
            )
        if any(tok in lower for tok in DANGEROUS_NET_TOKENS):
            vio.append(
                violation(
                    "SAFETY_HIGH_VOLTAGE_NET",
                    f"Net '{name}' looks like a mains/high-voltage net, blocked for "
                    f"beginner boards.",
                    location=name,
                    severity=ERROR,
                )
            )
        if _RAIL_NET_RE.match(name):
            continue  # documented rails are always allowed
        if name.upper() in _COMMON_NETS:
            continue
        if not _SIGNAL_NET_RE.match(name):
            vio.append(
                violation(
                    "SAFETY_NET_NOT_ALLOWLISTED",
                    f"Net '{name}' is not an allowlisted net. Use a documented rail "
                    f"(GND/VCC/VDD/3V3/5V/VBUS) or a signal net named like 'NET_FOO'.",
                    location=name,
                    severity=WARNING,
                )
            )
        elif not _GENERIC_SAFE_RE.match(name):
            vio.append(
                violation(
                    "SAFETY_NET_BAD_CHARSET",
                    f"Net '{name}' contains characters outside [A-Za-z0-9_].",
                    location=name,
                )
            )
    return vio


def _package_allowed(package: str) -> bool:
    p = package.strip()
    if not p:
        return False
    return any(re.match(pat, p, re.IGNORECASE) for pat in ALLOWED_PACKAGE_PATTERNS)


def is_blocking(violations: List[Dict[str, Any]]) -> bool:
    """True if any violation is error-severity (blocks the design from proceeding)."""
    return any(v["severity"] == ERROR for v in violations)


def summarize(violations: List[Dict[str, Any]]) -> str:
    """Short human-readable summary (logged / surfaced to the model)."""
    codes = sorted({v["rule"] for v in violations})
    if not codes:
        return "ALLOWLIST PASS — everything inside the beginner allowlist."
    return "ALLOWLIST FAIL — " + ", ".join(codes)