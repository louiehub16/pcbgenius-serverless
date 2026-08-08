"""
PCBGenius — C2 Multi-Layer Verification Rules (feature #15)
===========================================================

Validates 4-layer and 6-layer board layouts against the FROZEN contract
(PcbGenius_FROZEN_Contract_v1.0_2026-07-24.yaml). This module is the RULE
SOURCE for the `run_drc` tool-call. It consumes the `layout` object described
below and returns homogeneous DRC violations matching the contract shape:

    { rule, severity, location, message }

Rule families
-------------
  * STACKUP      — valid layer count (4/6) and valid layer pair / plane pairing.
  * USB_DIFFPAIR — 90 ohm differential impedance + length (skew) matching +
                   common-mode choke presence for USB diff pairs.
  * POWER_PLANE  — power/ground plane layer assignment + thermal reliefs.
  * VIA_STITCH   — ground via-stitching density near board edges.
  * LAYER_COUNT  — consistency between netlist.metadata.board_layers and the
                   actual number of copper layers in the layout.

Layout schema consumed (object|null)
------------------------------------
    layout = {
      "board":  {"edge_mm_x": float, "edge_mm_y": float},
      "stackup": {
          "layer_count": int,                  # actual copper layers (4 or 6)
          "dielectric_mm": float,
          "layers": [                          # ordered top -> bottom
              {"name": str, "role": "signal"|"plane", "plane_net": str|null}
          ],
      },
      "diff_pairs": [                          # only USB pairs are scored
          {"name": str, "net_p": str, "net_n": str,
           "impedance_target_ohm": 90.0, "impedance_measured_ohm": float,
           "impedance_tolerance_ohm": float,
           "length_p_mm": float, "length_n_mm": float,
           "max_skew_mm": float,              # allowed length mismatch
           "common_mode_choke": bool,          # true = a CMC/ferrite is present
           "impedance_checked": bool},
      ],
      "planes": [
          {"layer": str, "net": str, "role": "power"|"ground",
           "has_pour": bool, "thermal_relief": bool,
           "relief_gap_mm": float, "relief_spoke_count": int},
      ],
      "via_stitching": {
          "ground_stitch_vias": [ (x_mm, y_mm), ... ],  # near-edge GND vias
          "max_edge_gap_mm": float,   # foundry spec (edge->via max)
          "max_pitch_mm": float,      # foundry spec (via->via pitch max)
          "min_density": float,       # 0..1 required coverage ratio
      },
    }

Missing optional sections are treated as "rule not applicable" (no violation)
so a 2-layer board or a non-USB board does not spuriously fail. Structural
facts (layer count, plane presence) are still checked.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"

# Only 4 and 6 layers are multi-layer targets for this feature.
SUPPORTED_LAYER_COUNTS = (4, 6)

# USB 2.0 differential impedance target (occupies the 85-95 ohm window).
USB_IMPEDANCE_OHM = 90.0

Violation = Dict[str, Any]


def _v(rule: str, severity: str, location: str, message: str) -> Violation:
    return {"rule": rule, "severity": severity, "location": location,
            "message": message}


# ── STACKUP ────────────────────────────────────────────────────────────────

def check_stackup(layout: Dict[str, Any]) -> List[Violation]:
    """Validate layer count is 4 or 6 and inner-plane pairing is sane.

    4-layer canonical stackup: Sig / GND / PWR / Sig  (planes on In1, In2).
    6-layer canonical stackup: at least one GND and one PWR plane on inner
    layers, signal planes on the outer surfaces.
    """
    out: List[Violation] = []
    stackup = layout.get("stackup") or {}
    count = stackup.get("layer_count")
    if count is None:
        return out  # structurally not provided -> not applicable
    count = int(count)

    if count not in SUPPORTED_LAYER_COUNTS:
        out.append(_v(
            "MLS_STACKUP_UNSUPPORTED", SEVERITY_ERROR, "stackup.layer_count",
            f"Multi-layer stackup must be 4 or 6 layers, got {count}.",
        ))
        return out

    layers = stackup.get("layers") or []
    if len(layers) != count:
        out.append(_v(
            "MLS_STACKUP_COUNT_MISMATCH", SEVERITY_ERROR, "stackup",
            f"stackup.layer_count={count} but stackup.layers lists {len(layers)} entries.",
        ))

    # Inner layers only (skip F.Cu and B.Cu for plane pairing checks).
    inner = layers[1:-1] if len(layers) >= 2 else []
    plane_role = [l.get("role") for l in inner if isinstance(l, dict)]

    power_planes = [l for l in inner if isinstance(l, dict) and l.get("role") == "plane"
                    and (l.get("plane_net") or "").lower().startswith("v")]
    ground_planes = [l for l in inner if isinstance(l, dict) and l.get("role") == "plane"
                     and (l.get("plane_net") or "").upper() in ("GND", "GND0")]

    if count == 4 and len(inner) >= 2:
        # 4-layer: consecutive inner layers should be one ground + one power pair.
        r = [l.get("role") for l in inner[:2]]
        if r != ["plane", "plane"]:
            out.append(_v(
                "MLS_STACKUP_INVALID_PAIR", SEVERITY_WARNING, "stackup.layers[1:3]",
                "4-layer stackups should pair two continuous planes (e.g. GND + PWR) on In1/In2.",
            ))
        elif not ground_planes or not power_planes:
            out.append(_v(
                "MLS_STACKUP_PLANE_MISSING", SEVERITY_WARNING, "stackup",
                "4-layer inner planes must include both a ground and a power plane.",
            ))

    if count == 6:
        if not ground_planes or not power_planes:
            out.append(_v(
                "MLS_STACKUP_PLANE_MISSING", SEVERITY_WARNING, "stackup",
                "6-layer stackups must contain at least one ground plane and one power plane.",
            ))
        for i in range(len(layers) - 1):
            a = layers[i]; b = layers[i + 1]
            if isinstance(a, dict) and isinstance(b, dict) \
                    and a.get("role") == "plane" and b.get("role") == "plane" \
                    and not _adjacent_plane_pairs_ok(a, b):
                out.append(_v(
                    "MLS_STACKUP_INVALID_PAIR", SEVERITY_INFO, f"stackup.layers[{i}]",
                    "Adjacent internal plane layers create a coupled-plane issue; "
                    "add a signal layer between distinct planes.",
                ))
    return out


def _adjacent_plane_pairs_ok(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Plane pairs stacked directly are allowed only for the GND/PWR sandwich."""
    na = (a.get("plane_net") or "").upper()
    nb = (b.get("plane_net") or "").upper()
    return {na[:3], nb[:3]} <= {"GND", "VCC", "3V3", "5V", "PWR"}


# ── BOARD LAYER COUNT CONSISTENCY ──────────────────────────────────────────

def check_layer_count_consistency(netlist: Dict[str, Any],
                                  layout: Dict[str, Any]) -> List[Violation]:
    """metadata.board_layers must equal the actual copper layer count."""
    out: List[Violation] = []
    meta_layers = (netlist.get("metadata") or {}).get("board_layers")
    stackup = layout.get("stackup") or {}
    actual = stackup.get("layer_count")

    if meta_layers is None or actual is None:
        return out  # partial data, defer
    meta_layers = int(meta_layers)
    actual = int(actual)
    if meta_layers != actual:
        out.append(_v(
            "MLS_LAYER_COUNT_MISMATCH", SEVERITY_ERROR,
            f"metadata.board_layers={meta_layers}",
            f"netlist.metadata.board_layers={meta_layers} does not match the "
            f"{actual} copper layers in the layout stackup.",
        ))
    return out


# ── USB DIFF-PAIR ──────────────────────────────────────────────────────────

def check_usb_diff_pairs(layout: Dict[str, Any]) -> List[Violation]:
    """90 ohm impedance, length matching, common-mode choke for USB pairs."""
    out: List[Violation] = []
    pairs = layout.get("diff_pairs") or []
    if not pairs:
        return out  # no USB interface -> rule not applicable

    for i, dp in enumerate(pairs):
        loc = dp.get("name") or f"diff_pairs[{i}]"
        target = dp.get("impedance_target_ohm", USB_IMPEDANCE_OHM)
        tol = dp.get("impedance_tolerance_ohm")
        measured = dp.get("impedance_measured_ohm")
        checked = dp.get("impedance_checked", True)

        if checked and measured is not None and tol:
            if not (target - tol <= measured <= target + tol):
                out.append(_v(
                    "MLS_USB_IMPEDANCE", SEVERITY_ERROR, loc,
                    f"Differential impedance for '{loc}' is {measured} ohm; "
                    f"expected {target} +/- {tol} ohm (90 ohm USB target).",
                ))

        lp = dp.get("length_p_mm"); ln = dp.get("length_n_mm")
        max_skew = dp.get("max_skew_mm")
        if lp is not None and ln is not None and max_skew is not None \
                and abs(lp - ln) > max_skew:
            out.append(_v(
                "MLS_USB_LENGTH_MATCH", SEVERITY_WARNING, loc,
                f"Differential pair '{loc}' length mismatch is {abs(lp - ln):.2f} mm "
                f"(P={lp:.1f}, N={ln:.1f}); max allowed skew is {max_skew} mm.",
            ))

        # Common-mode choke strongly recommended on USB 2.0 data pair.
        if not dp.get("common_mode_choke", False):
            out.append(_v(
                "MLS_USB_CMC_MISSING", SEVERITY_WARNING, loc,
                f"USB diff pair '{loc}' has no common-mode choke; add one on "
                "the data lines near the connector for EMI suppression.",
            ))
    return out


# ── POWER PLANES ───────────────────────────────────────────────────────────

def check_power_planes(layout: Dict[str, Any]) -> List[Violation]:
    """Plane layer assignment (pour present, net defined) + thermal reliefs."""
    out: List[Violation] = []
    planes = layout.get("planes") or []
    if not planes:
        return out

    for i, pl in enumerate(planes):
        loc = pl.get("layer") or f"planes[{i}]"
        if not pl.get("has_pour", False):
            out.append(_v(
                "MLS_PLANE_NO_POUR", SEVERITY_WARNING, loc,
                f"Plane layer '{loc}' declared for {pl.get('role')} has no copper pour.",
            ))
        if not pl.get("net"):
            out.append(_v(
                "MLS_PLANE_NO_NET", SEVERITY_ERROR, loc,
                f"Plane layer '{loc}' ({pl.get('role')}) must be assigned to an "
                "existing power/ground net.",
            ))
        # Thermal reliefs are mandatory on through-hole/plane connections to
        # keep solder attach reliable; report missing relief as a warning.
        if not pl.get("thermal_relief", False):
            out.append(_v(
                "MLS_THERMAL_RELIEF_MISSING", SEVERITY_WARNING, loc,
                f"Plane '{loc}' has no thermal reliefs; through-hole pads "
                "connecting to this plane may not solder reliably.",
            ))
    return out


# ── VIA STITCHING ──────────────────────────────────────────────────────────

def check_via_stitching(layout: Dict[str, Any]) -> List[Violation]:
    """Ground via density near board edges: coverage + max on-edge pitch."""
    out: List[Violation] = []
    cfg = layout.get("via_stitching")
    if not cfg:
        return out

    board = layout.get("board") or {}
    w = board.get("edge_mm_x")
    h = board.get("edge_mm_y")
    vias = cfg.get("ground_stitch_vias") or []
    max_edge_gap = cfg.get("max_edge_gap_mm")
    max_pitch = cfg.get("max_pitch_mm")
    min_density = cfg.get("min_density", 0.8)

    if w and h and max_edge_gap:
        perimeter = 2.0 * (float(w) + float(h))
        required = int(math.ceil(perimeter / max_edge_gap))
        actual = len(vias)
        if actual < required * float(min_density):
            out.append(_v(
                "MLS_VIA_STITCHING", SEVERITY_WARNING, "via_stitching",
                f"GND via-stitching density is {actual}/{required} minimum vias "
                f"near edges (perimeter {perimeter:.0f} mm, max edge gap "
                f"{max_edge_gap} mm).",
            ))

    if max_pitch and len(vias) > 1:
        # Estimate worst on-edge spacing: distribute along perimeter.
        worst_pitch = _worst_pitch(w, h, len(vias), max_pitch)
        if worst_pitch > max_pitch:
            out.append(_v(
                "MLS_VIA_STITCH_PITCH", SEVERITY_WARNING, "via_stitching",
                f"Estimated worst GND via pitch is {worst_pitch:.2f} mm; "
                f"max allowed is {max_pitch} mm.",
            ))
    return out


def _worst_pitch(w: float, h: float, n: int, max_pitch: float) -> float:
    """If we know the board outline, estimate max adjacent via spacing."""
    if not (w and h and n):
        return 0.0
    perimeter = 2.0 * (float(w) + float(h))
    return perimeter / float(n)


# ── MASTER ENTRY ───────────────────────────────────────────────────────────

def check_multilayer(netlist: Dict[str, Any], layout: Dict[str, Any]):
    """Run all multi-layer rules. Returns (pass: bool, violations: list)."""
    if not layout:
        # No layout provided to run_drc -> nothing physical to check.
        return True, []
    violations: List[Violation] = []
    violations += check_layer_count_consistency(netlist, layout)
    violations += check_stackup(layout)
    violations += check_usb_diff_pairs(layout)
    violations += check_power_planes(layout)
    violations += check_via_stitching(layout)
    # A design passes only when there are no ERROR severity violations.
    has_error = any(v["severity"] == SEVERITY_ERROR for v in violations)
    return (not has_error), violations