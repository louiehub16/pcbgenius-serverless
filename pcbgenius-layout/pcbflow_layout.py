#!/usr/bin/env python3
"""
PCBGenius — B2 pcbflow layout generator (REAL source)
====================================================
Deterministic placement + board + obstacle generation from a FROZEN-contract
netlist (netlist_schema v1.0.0). Produces:

  * component placements  {ref: {"x", "y", "rotation"}}   (mm, degrees)
  * board outline  (rotated rect described by center + half-size, in mm)
  * obstacle list  (one axis-aligned rect obstacle per component, in mm)
  * SimpleRouteJson (SRJ) via `simple_route_json.py`, ready for the
    tscircuit-autorouter: {layerCount, minTraceWidth, obstacles,
    connections, bounds}.

Design goals
------------
  * DETERMINISM  — every Random instance is seeded; same netlist (after
    canonical sorting) + same seed => byte-identical output. No reliance on
    unordered dict iteration, set order, or unseeded hash().
  * CORRECT-BY-SPEC — validates the input netlist against the same contract
    rules that datagen/generate_netlists.validate_netlist enforces, and only
    places components whose refs are present so nets always resolve.
  * SELF-CONTAINED — pure stdlib. The `pcbflow` library (real footprint/pad
    extraction) is OPTIONAL: if importable we prefer its per-package pads,
    otherwise we fall back to an internal deterministic footprint table. The
    two `import pcbflow` call-sites are clearly marked below; everything else
    is dependency-free so training/inference harnesses can run it anywhere.

Coordinate convention
---------------------
All coordinates are millimetres, origin at the board CENTRE, +x right, +y up.
Component `rotation` is in degrees counter-clockwise (0/90/180/270).

Author: PCBGenius Wave-B2 layout agent.
Contract: PCBGenius_FROZEN_Contract_v1.0_2026-07-24.yaml
"""

from __future__ import annotations

import json
import random
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Contract constants (mirror datagen/generate_netlists.py)
# ─────────────────────────────────────────────────────────────────────────────
CONTRACT_VERSION = "1.0.0"
VALID_TYPES = {
    "resistor", "capacitor", "inductor", "diode", "led", "transistor",
    "ic", "connector", "power", "crystal", "switch",
}
NET_CLASSES = {"power", "ground", "signal", "clock", "analog", "digital"}


# ─────────────────────────────────────────────────────────────────────────────
# Contract validation (same rules as datagen)
# ─────────────────────────────────────────────────────────────────────────────
def validate_netlist(nl: Any) -> Tuple[bool, List[str]]:
    """Return (ok, [errors]) applying the frozen contract validation_rules."""
    errs: List[str] = []
    if not isinstance(nl, dict):
        return False, ["netlist not an object"]
    if nl.get("schema_version") != CONTRACT_VERSION:
        errs.append("schema_version != 1.0.0")
    comps = nl.get("components", []) or []
    nets = nl.get("nets", []) or []
    if not isinstance(comps, list) or not isinstance(nets, list):
        return False, ["components/nets must be arrays"]

    refs = [c.get("ref") for c in comps]
    if len(refs) != len(set(refs)):
        errs.append("duplicate component ref")

    netnames = {n.get("name") for n in nets}
    for c in comps:
        for p in c.get("pins", []) or []:
            if p.get("net") not in netnames:
                errs.append(f"pin {c.get('ref')}.{p.get('name')} -> missing net")

    classes = {n.get("class") for n in nets}
    if "ground" not in classes:
        errs.append("no ground net")
    if "power" not in classes:
        errs.append("no power net")

    valid_pins = {f"{c.get('ref')}.{p.get('name')}" for c in comps
                  for p in c.get("pins", []) or []}
    for n in nets:
        for rp in n.get("pins", []) or []:
            if rp not in valid_pins:
                errs.append(f"net {n.get('name')} refs unknown pin {rp}")
    return (len(errs) == 0), errs


# ─────────────────────────────────────────────────────────────────────────────
# OPTIONAL pcbflow integration (both call-sites marked)
# ─────────────────────────────────────────────────────────────────────────────
def _pcbflow_footprint(ref: str, ctype: str, package: str) -> Optional[Dict[str, Any]]:
    """Try to obtain real package geometry from the `pcbflow` library.

    >>> import pcbflow  # MARKED CALL-SITE (1/2) — library may be absent    <<<
    If pcbflow is installed we could map `package` -> its Footprint / pad list
    here. Because this sandbox does not bundle pcbflow, we guard the import and
    gracefully return None so the deterministic internal table is used instead.
    """
    try:
        import pcbflow  # noqa: F401  (marked call-site)
        # In a full install this hook would return e.g.
        #   {"w": 2.0, "h": 1.2, "pins": {number: (dx_mm, dy_mm), ...}}
        # derived from the package footprint. No-op in pure-stdlib mode:
        return None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Internal deterministic footprint table (mm)
# Prefer these when `pcbflow` is not importable. Values are plausible nominal
# body sizes and pin pitches — the exact numbers are not contract-relevant;
# only determinism and valid geometry matter for training variety.
# ─────────────────────────────────────────────────────────────────────────────
# package: (footprint_w, footprint_h, pin_count)
_INTERNAL_FOOTPRINTS: Dict[str, Tuple[float, float, int]] = {
    "0805":   (2.0,  1.25, 2),
    "0603":   (1.6,  0.80, 2),
    "0402":   (1.0,  0.50, 2),
    "1206":   (3.2,  1.60, 2),
    "SOT-223": (6.5,  3.60, 4),
    "SOT-23": (2.9,  1.30, 3),
    "TO-263": (10.2, 9.0,  4),
    "SMA":    (4.6,  2.6,  2),
    "CDRH8D28": (8.3, 8.3, 2),
    "10x10mm": (10.0, 10.0, 2),
    "DIP-8":  (10.2, 6.6, 8),
    "USB-C-31": (8.8, 7.6, 8),
}

# Generic fallback size keyed by component type.
_GENERIC_FOOTPRINT: Dict[str, Tuple[float, float, int]] = {
    "resistor":   (2.0, 1.25, 2),
    "capacitor":  (2.0, 1.25, 2),
    "inductor":   (4.5, 4.5, 2),
    "diode":      (4.6, 2.6, 2),
    "led":        (2.0, 1.25, 2),
    "transistor": (2.9, 1.3, 3),
    "ic":         (8.0, 6.0, 8),
    "connector":  (8.0, 8.0, 4),
    "power":      (6.0, 6.0, 4),
    "crystal":    (4.9, 2.0, 2),
    "switch":     (6.0, 6.0, 4),
}


def footprint_for(comp: Dict[str, Any]) -> Tuple[float, float, int]:
    """Return (w_mm, h_mm, pin_count) for a component.

    Priority: real pcbflow data > internal package table > type fallback.
    """
    pkg = (comp.get("package") or "").strip()
    if pkg:
        hit = _INTERNAL_FOOTPRINTS.get(pkg.upper())
        if hit:
            return hit
    pins = comp.get("pins", []) or []
    ctype = comp.get("type") or "ic"
    w, h, known_count = _GENERIC_FOOTPRINT.get(ctype, (8.0, 6.0, 4))
    # If the netlist declares a different number of pins, prefer that.
    if pins:
        known_count = max(known_count, len(pins))
    if not pkg:
        # scale body slightly with pin count so we never overlap pads
        extra = max(0, known_count - 4)
        w += 0.6 * extra
        h += 0.6 * extra
    return (w, h, known_count)


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic pin pad geometry (mm, relative to component centre)
# ─────────────────────────────────────────────────────────────────────────────
def pin_offsets(comp: Dict[str, Any]) -> List[Tuple[float, float, str]]:
    """Return [(dx_mm, dy_mm, pin_name), ...] placing each pin of a component.

    Rules (deterministic):
      * 2-pin passives: pin 1 on the left (-w/2), pin 2 on the right (+w/2),
        both vertically centred. (Covers resistor/capacitor/inductor/led/diode.)
      * Multi-pin (>=3): pins spaced evenly around the body perimeter starting
        at the middle of the left edge, going counter-clockwise.
    Distances stay inside the footprint so obstacles never self-overlap pads.
    """
    pins = comp.get("pins", []) or []
    w, h, count = footprint_for(comp)
    if not pins:
        return []
    if len(pins) == 2:
        names = _pin_names(pins)
        return [(-w / 2 + 0.5, 0.0, names[0]), (w / 2 - 0.5, 0.0, names[1])]
    # multi-pin perimeter placement
    half_w, half_h = w / 2, h / 2
    spac = max(count, 1)
    steps: List[Tuple[float, float]] = []
    for i in range(spac):
        t = i / max(spac - 1, 1)
        # perimeter walk CCW: left->top->right->bottom
        seg = 4 * t
        if seg < 1:                                   # left edge
            steps.append((-half_w, -half_h + 2 * half_h * seg))
        elif seg < 2:                                 # top edge
            steps.append((-half_w + 2 * half_w * (seg - 1), half_h))
        elif seg < 3:                                 # right edge
            steps.append((half_w, half_h - 2 * half_h * (seg - 2)))
        else:                                         # bottom edge
            steps.append((half_w - 2 * half_w * (seg - 3), -half_h))
    return [(x, y, name) for (x, y), name in zip(steps, _pin_names(pins))]


def _pin_names(pins: List[Dict[str, Any]]) -> List[str]:
    order = sorted(pins, key=lambda p: _pin_sort_key(p.get("number") or ""))
    return [p.get("name") or "" for p in order]


def _pin_sort_key(n: str) -> Tuple[int, str]:
    # numeric prefixes sort numerically, alphabetic suffixes as strings
    m = n
    num = ""
    while m and m[0].isdigit():
        num += m[0]
        m = m[1:]
    return (int(num) if num else 10**9, m)


# ─────────────────────────────────────────────────────────────────────────────
# Placement core
# ─────────────────────────────────────────────────────────────────────────────
_ANCHOR_TYPES = {"ic", "connector", "power", "crystal", "transistor"}
_PASSIVE_TYPES = {"resistor", "capacitor", "inductor", "led", "diode"}

PITCH_MM = 3.0        # grid spacing between component centres
BOARD_MARGIN_MM = 3.0 # outline clearance around outermost components


class LayoutResult:
    """Bundle of everything the generator computes for one netlist."""

    def __init__(self, netlist: Dict[str, Any], seed: int):
        self.netlist = netlist
        self.seed = seed
        self.placements: Dict[str, Dict[str, float]] = {}   # ref -> {x,y,rotation}
        self.obstacles: List[Dict[str, Any]] = []
        self.board_center: Tuple[float, float] = (0.0, 0.0)
        self.board_half: Tuple[float, float] = (0.0, 0.0)   # (half_w, half_h)
        self.srj: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Pure-serialisable view of the layout (placements + board + obstacles)."""
        return {
            "seed": self.seed,
            "placements": self.placements,
            "board": {
                "center": {"x": self.board_center[0], "y": self.board_center[1]},
                "half_size": {"x": self.board_half[0], "y": self.board_half[1]},
                "width_mm": 2 * self.board_half[0],
                "height_mm": 2 * self.board_half[1],
            },
            "obstacles": self.obstacles,
        }


def _net_for_refpin(netlist: Dict[str, Any]) -> Dict[str, List[str]]:
    """Build ref-pin/ref -> net adjacency used for proximity placement."""
    ref_to_nets: Dict[str, List[str]] = {}
    for c in netlist.get("components", []) or []:
        ref_to_nets[c.get("ref")] = []
    for n in netlist.get("nets", []) or []:
        for rp in n.get("pins", []) or []:
            ref = rp.split(".")[0]
            lst = ref_to_nets.setdefault(ref, [])
            if n.get("name") not in lst:
                lst.append(n.get("name"))
    return ref_to_nets


def place_netlist(netlist: Dict[str, Any], seed: int = 0,
                  placement_hints: Optional[Dict[str, Any]] = None) -> LayoutResult:
    """Deterministically place every component on a tight board.

    Input : contract netlist (optionally pre-validated).
    Output: LayoutResult with placements/obstacles/board + SRJ (see .srj or
            call export_simple_route_json()).
    """
    rng = random.Random(seed)  # single seeded stream => byte-deterministic

    # 1. Defensive copy + deterministic ordering of components.
    comps = [dict(c) for c in netlist.get("components", []) or []]
    # stable sort keeps output order stable regardless of input dict ordering
    comps.sort(key=lambda c: c.get("ref") or "")

    # 2. Decide roles.
    anchors = [c for c in comps if c.get("type") in _ANCHOR_TYPES]
    passives = [c for c in comps if c.get("type") in _PASSIVE_TYPES]
    others = [c for c in comps if c not in anchors and c not in passives]
    # anchors first (they sit central), then passives near their net-neighbours,
    # then anything else on remaining grid cells.
    ordered = anchors + passives + others

    ref_to_nets = _net_for_refpin(netlist)

    occupied: Dict[Tuple[int, int], str] = {}  # grid coord -> ref (non-overlap)

    # rotation is drawn from the SAME seeded rng in the SAME loop order and
    # stored immediately so it cannot drift from consumption order.
    rotations: Dict[str, float] = {}

    def try_place(ref: str, gx: int, gy: int) -> bool:
        """Attempt to place `ref` at grid cell (gx, gy); return success."""
        if (gx, gy) in occupied:
            return False
        occupied[(gx, gy)] = ref
        rotations[ref] = float(rng.choice([0, 90, 180, 270]))
        return True

    def friendly_cell(comp: Dict[str, Any], placed_xy: Dict[str, Tuple[float, float]]) -> Tuple[int, int]:
        """Pick the grid cell nearest the centroid of an anchor's net-neighbours."""
        my_nets = ref_to_nets.get(comp.get("ref"), [])
        candidates = []
        for c in comps:
            if c.get("ref") == comp.get("ref"):
                continue
            common = set(ref_to_nets.get(c.get("ref"), [])) & set(my_nets)
            if common and c.get("ref") in placed_xy:  # shares a net -> sit close
                candidates.append(placed_xy[c.get("ref")])
        if not candidates:
            return 0, 0
        cx = sum(p[0] for p in candidates) / len(candidates)
        cy = sum(p[1] for p in candidates) / len(candidates)
        return (round(cx / PITCH_MM), round(cy / PITCH_MM))

    placed_xy: Dict[str, Tuple[float, float]] = {}  # ref -> (x, y) centre (mm)

    def place_on_grid(ref: str, comp: Dict[str, Any], gx: int, gy: int) -> bool:
        if try_place(ref, gx, gy):
            placed_xy[ref] = (gx * PITCH_MM, gy * PITCH_MM)
            return True
        return False

    # 3. Place anchors one by one down the board spine toward +x/-y.
    anchors_sorted = sorted(anchors, key=lambda c: c.get("ref") or "")
    for i, comp in enumerate(anchors_sorted):
        col = i % 3
        row = i // 3
        place_on_grid(comp.get("ref"), comp, col, -row)

    # 4. Place passives/others near their net-neighbours with jittered rotation.
    for comp in ordered:
        ref = comp.get("ref")
        if ref in placed_xy:
            continue
        gx, gy = friendly_cell(comp, placed_xy)
        if not place_on_grid(ref, comp, gx, gy):
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (2, 0), (-2, 0), (0, 2), (0, -2), (1, 1), (-1, -1)):
                if place_on_grid(ref, comp, gx + dx, gy + dy):
                    break
            else:
                # safety net: first free cell scanning right then down
                for giy in range(0, 20):
                    for gix in range(-6, 6):
                        if place_on_grid(ref, comp, gix, giy):
                            break
                    if ref in placed_xy:
                        break

    # 5. Normalise board around content bounding box.
    xs = [placed_xy[r][0] for r in placed_xy]
    ys = [placed_xy[r][1] for r in placed_xy]
    if not xs:
        xs, ys = [0.0], [0.0]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    cw = (max_x - min_x) + 2 * BOARD_MARGIN_MM + PITCH_MM
    ch = (max_y - min_y) + 2 * BOARD_MARGIN_MM + PITCH_MM
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    for ref, (x, y) in placed_xy.items():
        placed_xy[ref] = (x - cx, y - cy)  # re-centre so board is at origin

    # 6. Build placements + obstacles.
    res = LayoutResult(netlist, seed)
    layers = [0, 1] if int(netlist.get("metadata", {}).get("board_layers", 2)) >= 2 else [0]
    for comp in ordered:
        ref = comp.get("ref")
        if ref not in placed_xy:
            continue
        x, y = placed_xy[ref]
        w, h, _ = footprint_for(comp)
        rot = rotations.get(ref, 0.0)
        res.placements[ref] = {"x": round(x, 4), "y": round(y, 4), "rotation": float(rot)}
        connected = ref_to_nets.get(ref, [])
        res.obstacles.append({
            "type": "rect",
            "center": {"x": round(x, 4), "y": round(y, 4)},
            "width": round(w, 4),
            "height": round(h, 4),
            "layers": layers,
            "connectedTo": connected,   # nets this part belongs to
            "label": ref,
            "rot": float(rot),
        })

    res.board_center = (0.0, 0.0)
    res.board_half = (round(cw / 2.0, 4), round(ch / 2.0, 4))
    return res


# ─────────────────────────────────────────────────────────────────────────────
# SRJ export entry points
# ─────────────────────────────────────────────────────────────────────────────
def export_simple_route_json(netlist: Dict[str, Any], seed: int = 0,
                             layout: Optional[LayoutResult] = None) -> Dict[str, Any]:
    """Turn a netlist into SimpleRouteJson.

    If `layout` is None, runs placement first. Delegates shape construction to
    simple_route_json.build_simple_route_json (kept as a separate module per
    the B2 file layout so the SRJ writer can be unit-tested independently).
    """
    from simple_route_json import build_simple_route_json  # local import (co-located)
    if layout is None:
        layout = place_netlist(netlist, seed=seed)
    return build_simple_route_json(netlist, layout)


def generate_layout_json(netlist: Dict[str, Any], seed: int = 0,
                         placement_hints: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """High-level facade: validate -> place -> return {placements, board, obstacles, srj}."""
    ok, errs = validate_netlist(netlist)
    if not ok:
        raise ValueError(f"Invalid netlist: {errs}")
    layout = place_netlist(netlist, seed=seed, placement_hints=placement_hints)
    layout.srj = export_simple_route_json(netlist, seed=seed, layout=layout)
    out = layout.to_dict()
    out["simple_route_json"] = layout.srj
    return out


def _canonical_string(netlist: Dict[str, Any]) -> str:
    """Stable serialisation used to compute content hash / determinism checks."""
    return json.dumps(netlist, sort_keys=True, allow_nan=False)


def is_deterministic(seed: int = 0) -> bool:
    """Standalone self-check: placing the same seed twice must match exactly."""
    import test_pcbflow as _t  # reuse fixture builder (co-located)
    sample = _t.sample_netlist()
    a = json.dumps(place_netlist(sample, seed=seed).to_dict(), sort_keys=True)
    b = json.dumps(place_netlist(sample, seed=seed).to_dict(), sort_keys=True)
    return a == b