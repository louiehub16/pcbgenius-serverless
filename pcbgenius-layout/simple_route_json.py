#!/usr/bin/env python3
"""
PCBGenius — B2 SimpleRouteJson writer (REAL source)
===================================================
Builds the tscircuit-autorouter **SimpleRouteJson** document from a contract
netlist plus a `pcbflow_layout.LayoutResult`.

SRJ document shape (exactly what pcbflow_layout.py produces)
------------------------------------------------------------
    {
      "layerCount": int,           # e.g. 2
      "minTraceWidth": float,      # mm — smallest route the fab tolerates
      "obstacles": [ { "type":"rect",
                       "center": {"x": mm, "y": mm},
                       "width": mm, "height": mm,
                       "layers": [0,1],
                       "connectedTo": ["NET_A", ...],   # nets on this part
                       "label": "R1",
                       "rot": 0 } ],
      "connections": [ { "name": "VCC",
                         "pointsTo": [ {"x": mm, "y": mm}, ... ],
                         "availableLayers": [0,1],
                         "weight": 1.0 } ],
      "bounds": { "minX": mm, "minY": mm, "maxX": mm, "maxY": mm }
    }

All geometry is in millimetres, origin at board centre. `connections` contains
exactly one entry per contract net (via nets[]), with each pin's pad centre as
a point — the autorouter just has to join members of the same net.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pcbflow_layout import LayoutResult, pin_offsets, validate_netlist


# ── policy knobs (mm) ────────────────────────────────────────────────────────
DEFAULT_MIN_TRACE_WIDTH = 0.25      # conservative default for JLCPCB-class fabs
NET_WEIGHT = {
    "power": 8.0,
    "ground": 8.0,
    "clock": 3.0,
    "analog": 1.5,
    "digital": 2.0,
    "signal": 1.0,
}


def _component_by_ref(netlist: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    by_ref: Dict[str, Dict[str, Any]] = {}
    refs_seen: List[str] = []
    for c in netlist.get("components", []) or []:
        if c.get("ref") not in refs_seen:
            refs_seen.append(c.get("ref"))
        by_ref[c.get("ref")] = c
    return by_ref


def _net_centroid(net: Dict[str, Any], by_ref: Dict[str, Dict[str, Any]],
                  placements: Dict[str, Dict[str, float]]) -> Optional[List[Dict[str, float]]]:
    """For a net, return the pad-centre point list based on the placed comps.

    If a component on the net was not placed (shouldn't happen for validated
    netlists but guard anyway), we skip that pin rather than crash.
    """
    points: List[Dict[str, float]] = []
    for refpin in net.get("pins", []) or []:
        if not isinstance(refpin, str) or "." not in refpin:
            continue
        ref, pin = refpin.split(".", 1)
        comp = by_ref.get(ref)
        if comp is None or ref not in placements:
            continue
        px = placements[ref]
        # component centre offset, rotated by the component's placement rotation
        dx = 0.0
        dy = 0.0
        for (ox, oy, name) in pin_offsets(comp):
            if name == pin or name == "":
                dx, dy = ox, oy
                break
        rot = px.get("rotation", 0.0) % 360.0
        rx = dx * _cos_d(rot) - dy * _sin_d(rot)
        ry = dx * _sin_d(rot) + dy * _cos_d(rot)
        points.append({"x": round(px.get("x", 0.0) + rx, 4),
                       "y": round(px.get("y", 0.0) + ry, 4)})
    return points


def _cos_d(d: float) -> float:
    import math
    return math.cos(math.radians(d))


def _sin_d(d: float) -> float:
    import math
    return math.sin(math.radians(d))


def build_simple_route_json(netlist: Dict[str, Any],
                            layout: LayoutResult,
                            min_trace_width: float = DEFAULT_MIN_TRACE_WIDTH,
                            ) -> Dict[str, Any]:
    """Core SRJ builder. Deterministic: pure function of (netlist, layout)."""
    by_ref = _component_by_ref(netlist)
    layers = int(netlist.get("metadata", {}).get("board_layers", 2))
    layer_count = max(1, layers)
    layer_list = list(range(layer_count))

    # obstacles: one rect per placed component, geometry already in layout.
    obstacles: List[Dict[str, Any]] = [dict(o) for o in layout.obstacles]

    # connections: one per net.
    connections: List[Dict[str, Any]] = []
    nets = netlist.get("nets", []) or []
    # stable order so SRJ output order is deterministic regardless of input
    for net in sorted(nets, key=lambda n: n.get("name") or ""):
        points = _net_centroid(net, by_ref, layout.placements)
        if not points:
            continue
        connections.append({
            "name": net.get("name", ""),
            "pointsTo": points,
            "availableLayers": layer_list,
            "weight": NET_WEIGHT.get(net.get("class", "signal"), 1.0),
        })

    # bounds from board outline (board is centred at origin).
    hw, hh = layout.board_half
    bounds = {
        "minX": round(-hw, 4),
        "minY": round(-hh, 4),
        "maxX": round(hw, 4),
        "maxY": round(hh, 4),
    }

    return {
        "layerCount": layer_count,
        "minTraceWidth": min_trace_width,
        "obstacles": obstacles,
        "connections": connections,
        "bounds": bounds,
    }


def generate_simple_route_json(netlist: Dict[str, Any], layout: Optional[LayoutResult] = None,
                               seed: int = 0) -> Dict[str, Any]:
    """Validate + place (if needed) + build SRJ in one call.

    This is the primary entry point consumed by the backend / training share,
    mirroring pcbflow_layout.generate_layout_json but returning only the SRJ.
    """
    ok, errs = validate_netlist(netlist)
    if not ok:
        raise ValueError(f"Invalid netlist: {errs}")
    if layout is None:
        from pcbflow_layout import place_netlist
        layout = place_netlist(netlist, seed=seed)
    return build_simple_route_json(netlist, layout)


if __name__ == "__main__":
    import json
    import sys
    # CLI: python simple_route_json.py netlist.json [seed]
    if len(sys.argv) < 2:
        print("usage: python simple_route_json.py <netlist.json> [seed]")
        sys.exit(2)
    with open(sys.argv[1]) as f:
        netlist = json.load(f)
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    print(json.dumps(generate_simple_route_json(netlist, seed=seed), indent=2))