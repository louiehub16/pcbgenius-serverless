"""
PCBGenius — E2 Manufacturing-first (fab_rules.py)
==================================================
Turn a design's physical requirements into four "4D" manufacturing-rule checks
whose limits come from a chosen fab's capability file, so a design only PASSES
if it is actually buildable at that fab.

The four manufacturing dimensions ("4D")
----------------------------------------
The full manufacturing-first gate is expressed as four independent rule
families, each mapping one group of design requirements onto the fab
capability file:

  D1  TRACE   — min trace width  (design.min_trace_mm  >= fab.min_trace_mm)
  D2  SPACING — min copper clearance (design.min_clearance_mm >= fab.min_clearance_mm)
  D3  DRILL   — drill / via / annular ring
                  min drill      >= fab.min_drill_mm
                  min via        >= fab.min_via_mm
                  min annular    >= fab.min_annular_ring_mm
  D4  BOARD   — board dimension + stackup (layer count, thickness)
                  layers            within fab.layer range / supported set
                  thickness         within fab thickness range / supported set
                  board edges       within [min_board_mm, max_board_mm]

Violation shape (matches the FROZEN DRC contract from pcbgenius-verification)
-----------------------------------------------------------------------------
    { rule, severity, location, message, dimension }

A design PASSES only when it has no ERROR-severity violation. Missing optional
inputs are treated as "not applicable" (no violation), so a partial design
never spuriously fails unless the violated limit is actually known.

`choose_fab` implements the manufacturing-first default: given a design and a
set of candidate capability files, it returns the cheapest/most-appropriate
fab whose full capability envelope satisfies every requirement, and errors if
NO fab can build the design — proving manufacturability *before* committing
to any order.

Design "requirements" input shape
---------------------------------
    {
      "fab": "jlcpcb" | "pcbway" | None,     # chose to force a specific fab
      "min_trace_mm": 0.12,
      "min_clearance_mm": 0.12,
      "min_drill_mm": 0.3,
      "min_via_mm": 0.25,
      "min_annular_ring_mm": 0.2,
      "layers": 4,
      "board_thickness_mm": 1.6,
      "board_mm": [100.0, 60.0],              # edge dimensions [x, y]
      "impedance_controlled": True,
    }
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from rules_files import BUILTIN_CAPABILITIES, Capability, get_capability

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"

Violation = Dict[str, Any]

# Dimension tags for the 4D families.
D_TRACE = "TRACE"
D_SPACING = "SPACING"
D_DRILL = "DRILL"
D_BOARD = "BOARD"
DIMENSIONS = (D_TRACE, D_SPACING, D_DRILL, D_BOARD)

# Rules that count as hard-failing (default: everything is a hard gate except
# controlled-impedance which is advisory on non-USB designs).
HARD_RULES = {
    "FAB_TRACE_MIN", "FAB_SPACING_MIN", "FAB_DRILL_MIN", "FAB_VIA_MIN",
    "FAB_ANNULAR_MIN", "FAB_LAYERS_MAX", "FAB_LAYER_LIST",
    "FAB_THICKNESS_RANGE", "FAB_THICKNESS_LIST", "FAB_BOARD_MIN",
    "FAB_BOARD_MAX", "FAB_NO_CAPABILITY",
}


def _v(rule: str, severity: str, location: str, message: str,
       dimension: str) -> Violation:
    return {"rule": rule, "severity": severity, "location": location,
            "message": message, "dimension": dimension}


# ── D1 TRACE ───────────────────────────────────────────────────────────────

def check_trace(design: Dict[str, Any], cap: Capability) -> List[Violation]:
    out: List[Violation] = []
    need = design.get("min_trace_mm")
    limit = cap.get("min_trace_mm")
    if need is not None and limit is not None and need < limit:
        out.append(_v(
            "FAB_TRACE_MIN", SEVERITY_ERROR, "min_trace_mm",
            f"Design needs {need} mm minimum trace width; {cap['fab'].upper()} "
            f"supports only down to {limit} mm.", D_TRACE))
    return out


# ── D2 SPACING ─────────────────────────────────────────────────────────────

def check_spacing(design: Dict[str, Any], cap: Capability) -> List[Violation]:
    out: List[Violation] = []
    need = design.get("min_clearance_mm")
    limit = cap.get("min_clearance_mm")
    if need is not None and limit is not None and need < limit:
        out.append(_v(
            "FAB_SPACING_MIN", SEVERITY_ERROR, "min_clearance_mm",
            f"Design needs {need} mm minimum copper clearance; {cap['fab'].upper()} "
            f"supports only down to {limit} mm.", D_SPACING))
    return out


# ── D3 DRILL (drill / via / annular ring) ──────────────────────────────────

def check_drill(design: Dict[str, Any], cap: Capability) -> List[Violation]:
    out: List[Violation] = []
    for field, rule, label in (
        ("min_drill_mm", "FAB_DRILL_MIN", "through-hole drill"),
        ("min_via_mm", "FAB_VIA_MIN", "via"),
        ("min_annular_ring_mm", "FAB_ANNULAR_MIN", "annular ring"),
    ):
        need = design.get(field)
        limit = cap.get(field)
        if need is not None and limit is not None and need < limit:
            out.append(_v(
                rule, SEVERITY_ERROR, field,
                f"Design needs a {need} mm minimum {label}; {cap['fab'].upper()} "
                f"supports only down to {limit} mm.", D_DRILL))
    return out


# ── D4 BOARD (dimension + stackup) ─────────────────────────────────────────

def check_board(design: Dict[str, Any], cap: Capability) -> List[Violation]:
    out: List[Violation] = []
    layers = design.get("layers")
    if layers is not None:
        lmax = (cap.get("layers") or {}).get("max")
        lmin = (cap.get("layers") or {}).get("min")
        if lmin is not None and lmax is not None:
            if not (lmin <= layers <= lmax):
                out.append(_v(
                    "FAB_LAYERS_MAX", SEVERITY_ERROR, "layers",
                    f"Design has {layers} layers; {cap['fab'].upper()} supports "
                    f"{lmin}-{lmax}.", D_BOARD))
        sups = cap.get("supported_layers") or []
        if sups and layers not in sups:
            out.append(_v(
                "FAB_LAYER_LIST", SEVERITY_ERROR, "layers",
                f"Design has {layers} layers; {cap['fab'].upper()} only stacks "
                f"{sorted(set(sups))}.", D_BOARD))

    thickness = design.get("board_thickness_mm")
    if thickness is not None:
        tr = cap.get("board_thickness_mm") or {}
        tmin, tmax = tr.get("min"), tr.get("max")
        if tmin is not None and tmax is not None:
            if not (tmin <= thickness <= tmax):
                out.append(_v(
                    "FAB_THICKNESS_RANGE", SEVERITY_ERROR, "board_thickness_mm",
                    f"Board thickness {thickness} mm is outside {cap['fab'].upper()} "
                    f"supported {tmin}-{tmax} mm.", D_BOARD))
        tsups = cap.get("supported_thicknesses_mm") or []
        if tsups and not any(abs(thickness - t) < 1e-9 for t in tsups):
            out.append(_v(
                "FAB_THICKNESS_LIST", SEVERITY_WARNING, "board_thickness_mm",
                f"{thickness} mm is not one of {cap['fab'].upper()} catalogue "
                f"thicknesses {sorted(set(tsups))} mm; pick an exact option.", D_BOARD))

    edges = design.get("board_mm")
    if isinstance(edges, (list, tuple)) and len(edges) >= 2:
        bmin, bmax = cap.get("min_board_mm"), cap.get("max_board_mm")
        for i, edge in enumerate(edges):
            loc = f"board_mm[{i}]"
            if edge is None:
                continue
            if bmin is not None and edge < bmin:
                out.append(_v(
                    "FAB_BOARD_MIN", SEVERITY_ERROR, loc,
                    f"Board edge ({edge} mm) is smaller than {cap['fab'].upper()} "
                    f"minimum {bmin} mm.", D_BOARD))
            if bmax is not None and edge > bmax:
                out.append(_v(
                    "FAB_BOARD_MAX", SEVERITY_ERROR, loc,
                    f"Board edge ({edge} mm) exceeds {cap['fab'].upper()} "
                    f"maximum {bmax} mm.", D_BOARD))

    # Controlled impedance: only enforced when the design actually requires it;
    # a design that needs it must pick a fab that offers it (hard-ish gate).
    if design.get("impedance_controlled") and not cap.get("impedance_controlled"):
        out.append(_v(
            "FAB_IMPEDANCE_UNSUPPORTED", SEVERITY_ERROR, "impedance_controlled",
            f"Design requires controlled impedance; {cap['fab'].upper()} does "
            f"not provide it.", D_BOARD))
    return out


# ── MASTER ENTRY ───────────────────────────────────────────────────────────

_VALID_KEYS = ("fab", "min_trace_mm", "min_clearance_mm", "min_drill_mm",
               "min_via_mm", "min_annular_ring_mm", "layers",
               "board_thickness_mm", "board_mm", "impedance_controlled")


def check_fab_rules(design: Dict[str, Any], fab: Optional[str] = None,
                    capability: Optional[Capability] = None):
    """Score a design's 4D requirements against a fab capability.

    `fab` names a built-in (jlcpcb/pcbway); `capability` overrides with a
    parsed capability file. Returns (pass: bool, violations: list[Violation]).
    """
    if capability is None:
        capability = get_capability(fab)  # falls back to JLCPCB built-in

    violations: List[Violation] = []
    violations += check_trace(design, capability)
    violations += check_spacing(design, capability)
    violations += check_drill(design, capability)
    violations += check_board(design, capability)

    has_error = any(v["severity"] == SEVERITY_ERROR for v in violations)
    return (not has_error), violations


def design_matches(design: Dict[str, Any], capability: Capability) -> bool:
    """True if the design fits fully inside a fab capability (no errors)."""
    passed, violations = check_fab_rules(
        design, capability=capability)
    return passed and all(v["severity"] != SEVERITY_ERROR
                          for v in violations)


FAB_PREFERENCE = ("jlcpcb", "pcbway")


def choose_fab(design: Dict[str, Any],
               capabilities: Optional[Dict[str, Capability]] = None) -> Capability:
    """Manufacturing-first default: pick the first fab that can build the design.

    Raises ValueError when no candidate fab satisfies every manufacturing
    requirement — the design must be revised before it can be manufactured.
    """
    caps = capabilities or BUILTIN_CAPABILITIES

    forced = (design.get("fab") or "").strip().lower()
    if forced:
        cap = caps.get(forced)
        if cap is None:
            raise ValueError(f"No capability file for fab '{forced}'.")
        if not design_matches(design, cap):
            _, violations = check_fab_rules(design, capability=cap)
            raise ValueError(
                f"Design does not meet {forced.upper()} manufacturing rules: "
                f"{[v['rule'] for v in violations]}.")
        return cap

    for name in FAB_PREFERENCE:
        cap = caps.get(name)
        if cap and design_matches(design, cap):
            return cap
    for name in caps:
        cap = caps[name]
        if cap and design_matches(design, cap):
            return cap
    _err = _describe_gaps(design, [caps[n] for n in caps if caps.get(n)])
    raise ValueError(
        "No candidate fab can manufacture this design (manufacturing-first "
        f"gate). Gaps: {_err}.")


def _describe_gaps(design: Dict[str, Any], caps: List[Capability]) -> str:
    """Human summary of why the cheapest-fab strategy rejected the design."""
    if not caps:
        return "no capability files available"
    lines = []
    for cap in caps:
        _, vio = check_fab_rules(design, capability=cap)
        rules = [v["rule"] for v in vio if v["severity"] == SEVERITY_ERROR]
        if not rules:
            rules = [v["rule"] for v in vio]
        lines.append(f"{cap['fab'].upper()}: {', '.join(rules) or 'ok'}")
    return "; ".join(lines)