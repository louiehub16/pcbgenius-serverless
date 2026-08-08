#!/usr/bin/env python3
"""
PCBGenius — 3D board export (D3-3d-view, REAL source)
=====================================================
Converts a FROZEN-contract netlist + placement into a **three.js scene JSON**
document that `pcbgenius-frontend/src/components/Board3D.tsx` renders as a
rotatable 3D board (orbit controls, per-type component colours, toggles).

Scene JSON shape (v1.0.0)
-------------------------
    {
      "schema_version": "1.0.0",
      "generator": "pcbgenius-three/board_export.py",
      "unit": "mm",
      "design_name": str,
      "board": {
        "thickness_mm": float,          # extruded board height
        "layers": int,                  # from metadata.board_layers
        "center": {"x": mm, "y": mm},
        "outline": [[x, y], ...],       # CCW rect corners (for ExtrudeGeometry)
        "width_mm": mm, "height_mm": mm
      },
      "components": [
        {
          "ref": str, "type": str, "value": str, "package": str,
          "color": "#rrggbb",           # per-type palette
          "position": {"x": mm, "y": mm, "z": mm},   # z = body_h/2 above board
          "rotation": float,            # degrees CCW about board normal (contract)
          "size": {"w_mm": mm, "h_mm": mm, "body_h_mm": mm}
        }, ...
      ],
      "nets": [ {"name": str, "class": str, "pins": [str, ...]}, ... ]
    }

Coordinate convention (matches pcbflow_layout): millimetres, origin at board
center, +x right, +y up; rotation in degrees counter-clockwise (0/90/180/270).
The viewer maps +y -> scene up (three.js) and places the board in the XZ plane.

Design goals
------------
  * PURE STDLIB — no numpy/three.js/npm. Deterministic placement fallback when
    no placement is supplied (seeded Random, canonical component ordering), so
    the exporter runs anywhere the contract netlist can be produced.
  * CORRECT-BY-SPEC — validates the netlist (same rules as pcbflow_layout +
    datagen) and only emits components whose refs are placed.
  * VIEWER-FRIENDLY — colour + body height are derived from component type, so
    the frontend stays dumb (no geometry policy in the browser).

Author: PCBGenius 3D-board-view agent (Wave-A).
Contract: PCBGenius_FROZEN_Contract_v1.0_2026-07-24.yaml
"""

from __future__ import annotations

import json
import math
import random
from typing import Any, Dict, List, Optional, Tuple

CONTRACT_VERSION = "1.0.0"
SCENE_SCHEMA_VERSION = "1.0.0"
GENERATOR = "pcbgenius-three/board_export.py"

# ─────────────────────────────────────────────────────────────────────────────
# Visual policy (colour per type, body height per type) — mm, sRGB hex
# ─────────────────────────────────────────────────────────────────────────────
TYPE_COLORS: Dict[str, str] = {
    "resistor":   "#c9a13b",  # tan
    "capacitor":  "#5b7db1",  # ceramic blue
    "inductor":   "#8b5f96",  # ferrite purple
    "diode":      "#b23a48",  # red-ish
    "led":        "#e26d2b",  # orange
    "transistor": "#3f6b57",  # green
    "ic":         "#2f3e50",  # dark slate
    "connector":  "#9aa0a6",  # grey
    "power":      "#7d2e2e",  # brick
    "crystal":    "#6f7f3f",  # olive
    "switch":     "#575757",  # charcoal
}

# body height above the board (mm) — plausible nominal package heights
BODY_HEIGHTS: Dict[str, float] = {
    "resistor":   0.55,
    "capacitor":  1.00,
    "inductor":   2.40,
    "diode":      1.10,
    "led":        0.70,
    "transistor": 1.00,
    "ic":         1.40,
    "connector":  2.20,
    "power":      1.20,
    "crystal":    1.00,
    "switch":     1.60,
}

DEFAULT_BOARD_THICKNESS_MM = 1.6   # JLCPCB-class standard
BOARD_MARGIN_MM = 3.0              # outline clearance around placed content
PITCH_MM = 3.0                     # auto-placement grid spacing
FOOTPRINT_MM: Dict[str, Tuple[float, float, int]] = {
    "0805":    (2.0,  1.25, 2),
    "0603":    (1.6,  0.80, 2),
    "0402":    (1.0,  0.50, 2),
    "1206":    (3.2,  1.60, 2),
    "SOT-223": (6.5,  3.60, 4),
    "SOT-23":  (2.9,  1.30, 3),
    "TO-263":  (10.2, 9.0,  4),
    "SMA":     (4.6,  2.6,  2),
    "CDRH8D28": (8.3, 8.3, 2),
    "10X10MM": (10.0, 10.0, 2),
    "DIP-8":   (10.2, 6.6, 8),
    "USB-C-31": (8.8, 7.6, 8),
}
GENERIC_FOOTPRINT: Dict[str, Tuple[float, float, int]] = {
    "resistor": (2.0, 1.25, 2), "capacitor": (2.0, 1.25, 2),
    "inductor": (4.5, 4.5, 2),  "diode": (4.6, 2.6, 2),
    "led": (2.0, 1.25, 2),      "transistor": (2.9, 1.3, 3),
    "ic": (8.0, 6.0, 8),        "connector": (8.0, 8.0, 4),
    "power": (6.0, 6.0, 4),     "crystal": (4.9, 2.0, 2),
    "switch": (6.0, 6.0, 4),
}

_ANCHOR_TYPES = {"ic", "connector", "power", "crystal", "transistor"}
_PASSIVE_TYPES = {"resistor", "capacitor", "inductor", "led", "diode"}


# ─────────────────────────────────────────────────────────────────────────────
# Contract validation (mirrors pcbflow_layout.validate_netlist)
# ─────────────────────────────────────────────────────────────────────────────
def validate_netlist(nl: Any) -> Tuple[bool, List[str]]:
    """Return (ok, [errors]) applying the frozen contract validation rules."""
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
# Footprint / body geometry
# ─────────────────────────────────────────────────────────────────────────────
def footprint_for(comp: Dict[str, Any]) -> Tuple[float, float, int]:
    """Return (w_mm, h_mm, pin_count) for a component (package > type fallback)."""
    pkg = (comp.get("package") or "").strip()
    if pkg:
        hit = FOOTPRINT_MM.get(pkg.upper())
        if hit:
            return hit
    ctype = comp.get("type") or "ic"
    w, h, known = GENERIC_FOOTPRINT.get(ctype, (8.0, 6.0, 4))
    pins = comp.get("pins", []) or []
    if pins:
        known = max(known, len(pins))
    if not pkg:
        extra = max(0, known - 4)
        w += 0.6 * extra
        h += 0.6 * extra
    return (w, h, known)


def body_height(comp: Dict[str, Any]) -> float:
    """Body height above the board per component type (mm)."""
    return float(BODY_HEIGHTS.get(comp.get("type") or "ic", 1.0))


def color_for(comp: Dict[str, Any]) -> str:
    return TYPE_COLORS.get(comp.get("type") or "ic", "#999999")


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic auto-placement fallback (used when no placement is supplied)
# ─────────────────────────────────────────────────────────────────────────────
def auto_place(netlist: Dict[str, Any], seed: int = 0) -> Dict[str, Dict[str, float]]:
    """Place every component on a 3mm grid; returns {ref: {x, y, rotation}}.

    Mirrors pcbflow_layout placement policy: anchors (ic/connector/power/
    crystal/transistor) first along the board spine, passives near net-sharing
    neighbours, everything else on remaining free cells. Pure function of
    (netlist, seed) -> byte-deterministic.
    """
    rng = random.Random(seed)
    comps = [dict(c) for c in netlist.get("components", []) or []]
    comps.sort(key=lambda c: c.get("ref") or "")

    anchors = [c for c in comps if c.get("type") in _ANCHOR_TYPES]
    passives = [c for c in comps if c.get("type") in _PASSIVE_TYPES]
    others = [c for c in comps if c not in anchors and c not in passives]
    ordered = anchors + passives + others

    placed: Dict[str, Dict[str, float]] = {}
    occupied: Dict[Tuple[int, int], str] = {}

    def try_cell(ref: str, gx: int, gy: int) -> bool:
        if (gx, gy) in occupied:
            return False
        occupied[(gx, gy)] = ref
        rot = float(rng.choice([0, 90, 180, 270]))
        placed[ref] = {"x": float(gx * PITCH_MM), "y": float(gy * PITCH_MM),
                       "rotation": rot}
        return True

    for i, comp in enumerate(anchors):
        try_cell(comp.get("ref"), i % 3, -(i // 3))

    for comp in ordered:
        ref = comp.get("ref")
        if ref in placed:
            continue
        gx, gy = 0, 0
        if not try_cell(ref, gx, gy):
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (2, 0), (-2, 0), (0, 2), (0, -2), (1, 1), (-1, -1)):
                if try_cell(ref, gx + dx, gy + dy):
                    break
            else:
                for giy in range(0, 24):
                    for gix in range(-8, 8):
                        if try_cell(ref, gix, giy):
                            break
                    if ref in placed:
                        break

    # re-centre so the board sits at the origin
    xs = [p["x"] for p in placed.values()]
    ys = [p["y"] for p in placed.values()]
    cx = (min(xs) + max(xs)) / 2.0 if xs else 0.0
    cy = (min(ys) + max(ys)) / 2.0 if ys else 0.0
    for p in placed.values():
        p["x"] = round(p["x"] - cx, 4)
        p["y"] = round(p["y"] - cy, 4)
    return placed


def _board_rect(placements: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    """Compute a tight rectangular board outline (CCW) around the placement."""
    xs = [p["x"] for p in placements.values()] or [0.0]
    ys = [p["y"] for p in placements.values()] or [0.0]
    w = (max(xs) - min(xs)) + 2 * BOARD_MARGIN_MM + PITCH_MM
    h = (max(ys) - min(ys)) + 2 * BOARD_MARGIN_MM + PITCH_MM
    hw, hh = w / 2.0, h / 2.0
    return {
        "outline": [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]],
        "center": {"x": 0.0, "y": 0.0},
        "width_mm": round(w, 4),
        "height_mm": round(h, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Core conversion
# ─────────────────────────────────────────────────────────────────────────────
def convert_netlist_to_scene(
    netlist: Dict[str, Any],
    placement: Optional[Dict[str, Dict[str, float]]] = None,
    board: Optional[Dict[str, Any]] = None,
    seed: int = 0,
    board_thickness_mm: float = DEFAULT_BOARD_THICKNESS_MM,
) -> Dict[str, Any]:
    """Convert a contract netlist (+ optional placement/board) into a scene.

    `placement` : {ref: {"x": mm, "y": mm, "rotation": deg}} — the shape the
                  pcbflow_layout generator emits; may be omitted to auto-place.
    `board`     : {"outline": [[x,y],...], ...} — may be omitted to auto-fit.
    Returns the three.js scene JSON document (pure data, JSON-serialisable).
    """
    ok, errs = validate_netlist(netlist)
    if not ok:
        raise ValueError(f"Invalid netlist: {errs}")

    if placement is None:
        placement = auto_place(netlist, seed=seed)
    if board is None:
        board = _board_rect(placement)

    by_ref = {c.get("ref"): c for c in netlist.get("components", []) or []}

    components: List[Dict[str, Any]] = []
    # stable order so the JSON is deterministic regardless of dict ordering
    for ref in sorted(placement.keys()):
        comp = by_ref.get(ref)
        if comp is None:
            continue
        w, h, _ = footprint_for(comp)
        bh = body_height(comp)
        p = placement[ref]
        components.append({
            "ref": ref,
            "type": comp.get("type") or "ic",
            "value": comp.get("value") or "",
            "package": comp.get("package") or "",
            "color": color_for(comp),
            "position": {
                "x": round(float(p.get("x", 0.0)), 4),
                "y": round(float(p.get("y", 0.0)), 4),
                "z": round(bh / 2.0, 4),
            },
            "rotation": float(p.get("rotation", 0.0)) % 360.0,
            "size": {
                "w_mm": round(w, 4),
                "h_mm": round(h, 4),
                "body_h_mm": round(bh, 4),
            },
        })

    nets: List[Dict[str, Any]] = []
    for n in sorted(netlist.get("nets", []) or [], key=lambda x: x.get("name") or ""):
        nets.append({"name": n.get("name") or "",
                     "class": n.get("class") or "signal",
                     "pins": [p for p in n.get("pins", []) or []]})

    return {
        "schema_version": SCENE_SCHEMA_VERSION,
        "generator": GENERATOR,
        "unit": "mm",
        "design_name": netlist.get("metadata", {}).get("design_name") or "design",
        "board": {
            "thickness_mm": float(board_thickness_mm),
            "layers": int(netlist.get("metadata", {}).get("board_layers", 2)),
            "center": {"x": float(board.get("center", {}).get("x", 0.0)),
                       "y": float(board.get("center", {}).get("y", 0.0))},
            "outline": [[round(float(px), 4), round(float(py), 4)]
                        for px, py in (board.get("outline") or [])],
            "width_mm": float(board.get("width_mm", 0.0)),
            "height_mm": float(board.get("height_mm", 0.0)),
        },
        "components": components,
        "nets": nets,
    }


def convert_layout_json(layout: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience: consume a pcbflow_layout `generate_layout_json` dict
    (which already carries {placements, board, obstacles, simple_route_json})
    and produce the matching scene JSON."""
    nl = layout.get("netlist")
    if nl is None:
        raise ValueError("layout dict must carry the source 'netlist'")
    return convert_netlist_to_scene(nl,
                                    placement=layout.get("placements"),
                                    board=layout.get("board"),
                                    seed=int(layout.get("seed", 0)))


def scene_to_json(scene: Dict[str, Any], indent: Optional[int] = 2) -> str:
    return json.dumps(scene, indent=indent, sort_keys=False)


# ─────────────────────────────────────────────────────────────────────────────
# CLI: python board_export.py <netlist.json> [placement.json] [-o out.json] [--seed N]
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    with open(sys.argv[1], encoding="utf-8") as f:
        nl = json.load(f)
    placement = None
    seed = 0
    out_path = None
    i = 2
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == "--seed":
            seed = int(sys.argv[i + 1]); i += 2
        elif a == "-o":
            out_path = sys.argv[i + 1]; i += 2
        elif not a.startswith("-"):
            with open(a, encoding="utf-8") as f:
                placement = json.load(f)
            i += 1
        else:
            i += 1
    scene = convert_netlist_to_scene(nl, placement=placement, seed=seed)
    text = scene_to_json(scene)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"wrote {out_path}")
    else:
        print(text)