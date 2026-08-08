#!/usr/bin/env python3
"""
PCBGenius — C1 tests for the autorouter stack (REAL source)
============================================================
Exercises the pure-Python helper logic of `autorouter.py` — net ordering and
the obstacle/trace collision checker — plus the deterministic router itself,
on 5 small SRJ fixtures. Asserts that a TRIVIAL fixture routes >90% of its
nets.

Run with plain stdlib (no npm, no docker, no CUDA, no network):
    python autorouter_test.py

Coverage
--------
  1. order_connections returns a deterministic, stable net order (same input
     twice => same list).
  2. net_class_order honours an explicit net->class map (analog before digital).
  3. trace_clear: a clean segment clears obstacles; one that crosses an obstacle
     is rejected; clearance enforcement catches near-misses.
  4. Pure-Python router routes the 5 fixtures and reports correct
     {routed, drc_pass, unrouted_nets, layout_ref} contract shape.
  5. TRIVIAL case: >90% (here 100%) of nets route, all three strategies agree,
     and results are deterministic across runs.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List

from autorouter import (order_connections, net_class_order, route,
                        route_purepython, strategy_a, strategy_b, strategy_c,
                        trace_clear, router_strategy_names)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: small SRJ documents
# ─────────────────────────────────────────────────────────────────────────────
def _pt(x: float, y: float) -> Dict[str, float]:
    return {"x": x, "y": y}


def _srj(connections: List[Dict[str, Any]],
         obstacles: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    return {
        "layerCount": 2,
        "minTraceWidth": 0.25,
        "obstacles": obstacles or [],
        "connections": connections,
        "bounds": {"minX": -15.0, "minY": -15.0, "maxX": 15.0, "maxY": 15.0},
    }


def _conn(name: str, pts: List[Dict[str, float]], w: float = 1.0,
          cls: str = "signal") -> Dict[str, Any]:
    return {"name": name, "pointsTo": pts, "availableLayers": [0, 1], "weight": w}


# 5 fixtures: a trivial one (all short, obstacle-free) plus 4 increasingly
# cluttered boards.
def fixture_trivial() -> Dict[str, Any]:
    """4 two-pin nets, no obstacles -> should route 100%."""
    return _srj([
        _conn("NET_A", [_pt(-10, -5), _pt(-4, -5)], 2.0, "signal"),
        _conn("NET_B", [_pt(-10, 5), _pt(-4, 5)], 2.0, "power"),
        _conn("NET_C", [_pt(4, -5), _pt(10, -5)], 1.0, "analog"),
        _conn("NET_D", [_pt(4, 5), _pt(10, 5)], 2.0, "ground"),
    ])


def fixture_single_blocked() -> Dict[str, Any]:
    """Obstacle sits directly on one net's straight line -> one L detour needed."""
    obstacle = {"type": "rect", "center": {"x": 3.0, "y": 0.0},
                "width": 1.0, "height": 1.0, "layers": [0, 1],
                "connectedTo": [], "label": "B1", "rot": 0}
    return _srj([
        _conn("N1", [_pt(-6, 0), _pt(8, 0)], 8.0),
        _conn("N2", [_pt(-6, 6), _pt(8, 6)], 1.0, "signal"),
        _conn("N3", [_pt(0, -8), _pt(0, 8)], 5.0, "power"),
    ], [obstacle])


def fixture_grid_of_obstacles() -> Dict[str, Any]:
    """A 3x3 field of small blocked cells between two net banks."""
    obs = []
    for gx in range(-3, 4, 3):
        for gy in range(-3, 4, 3):
            obs.append({"type": "rect", "center": {"x": float(gx), "y": float(gy)},
                        "width": 1.0, "height": 1.0, "layers": [0, 1],
                        "connectedTo": [], "label": f"B{gx}_{gy}", "rot": 0})
    return _srj([
        _conn("GA", [_pt(-10, -6), _pt(8, -6)], 8.0, "ground"),
        _conn("GB", [_pt(-10, 6), _pt(8, 6)], 8.0, "ground"),
        _conn("S1", [_pt(-10, 0), _pt(8, 0)], 1.0, "signal"),
    ], obs)


def fixture_rotated_obstacle() -> Dict[str, Any]:
    """A rotated rect cross-cutting the corridor tests SAT collision handling."""
    obs = {"type": "rect", "center": {"x": 0.0, "y": 0.0},
           "width": 8.0, "height": 2.0, "layers": [0, 1],
           "connectedTo": [], "label": "ROT", "rot": 45}
    return _srj([
        _conn("R1", [_pt(-8, -4), _pt(8, -4)], 8.0, "ground"),
        _conn("R2", [_pt(-8, -8), _pt(8, -8)], 2.0, "analog"),
        _conn("R3", [_pt(-8, 4), _pt(8, 4)], 2.0, "signal"),
    ], [obs])


def fixture_dense() -> Dict[str, Any]:
    """A dense layout: 6 nets weaving between many obstacles / part footprints."""
    obs = []
    labels = ["U1", "U2", "C1", "C2", "R1"]
    coords = [(0, 0), (8, 0), (-8, 0), (-4, -4), (6, 4)]
    for (cx, cy), lab in zip(coords, labels):
        obs.append({"type": "rect", "center": {"x": cx, "y": cy},
                    "width": 5.0, "height": 5.0, "layers": [0, 1],
                    "connectedTo": [], "label": lab, "rot": 0})
    return _srj([
        _conn("VIN", [_pt(-12, -8), _pt(-6, -2)], 8.0, "power"),
        _conn("GND", [_pt(-12, -9), _pt(12, -9), _pt(4, -5)], 8.0, "ground"),
        _conn("OUT", [_pt(12, 8), _pt(6, 2)], 8.0, "power"),
        _conn("FB", [_pt(-4, 2), _pt(4, -2)], 1.0, "analog"),
        _conn("CLK", [_pt(10, -8), _pt(10, 8)], 3.0, "clock"),
        _conn("SIG", [_pt(-10, 8), _pt(10, 4)], 1.0, "signal"),
    ], obs)


FIXTURES: List[tuple[str, Dict[str, Any]]] = [
    ("trivial", fixture_trivial()),
    ("single_blocked", fixture_single_blocked()),
    ("grid_of_obstacles", fixture_grid_of_obstacles()),
    ("rotated_obstacle", fixture_rotated_obstacle()),
    ("dense", fixture_dense()),
]


# ─────────────────────────────────────────────────────────────────────────────
# Checks
# ─────────────────────────────────────────────────────────────────────────────
def check_net_order_stable():
    srj = fixture_dense()
    a = [c["name"] for c in order_connections(srj)]
    b = [c["name"] for c in order_connections(srj)]
    assert a == b, "net ordering must be deterministic (same input -> same order)"


def check_net_class_order():
    srj = fixture_dense()
    km = {"VIN": "power", "GND": "ground", "OUT": "power",
          "FB": "analog", "CLK": "clock", "SIG": "signal"}
    ordered = net_class_order(srj["connections"], km)
    names = [c["name"] for c in ordered]
    # analog (FB) must precede digital/signal and ground last
    assert names[0] == "FB", f"analog should route first, got {names}"
    assert names[-1] == "GND", f"ground should route last, got {names}"


def check_collision():
    obstacle = {"type": "rect", "center": {"x": 0.0, "y": 0.0},
                "width": 2.0, "height": 2.0, "layers": [0, 1],
                "connectedTo": [], "label": "O", "rot": 0}
    # clean segment well clear of the obstacle
    assert trace_clear([(-10.0, 5.0), (10.0, 5.0)], [obstacle], 0.2) is True
    # segment crossing the obstacle must be rejected
    assert trace_clear([(-10.0, 0.0), (10.0, 0.0)], [obstacle], 0.2) is False
    # clearance violation: passes outside the rect but within clearance mm
    assert trace_clear([(-10.0, 1.1), (10.0, 1.1)], [obstacle], 0.2) is False
    assert trace_clear([(-10.0, 2.0), (10.0, 2.0)], [obstacle], 0.2) is True


def check_all_fixtures_route():
    # every fixture must return the frozen contract shape
    for name, srj in FIXTURES:
        res = route(srj, strategy="c")
        assert set(res.keys()) >= {"routed", "drc_pass", "unrouted_nets", "layout_ref"}, f"{name} missing keys"
        assert isinstance(res["routed"], bool)
        assert isinstance(res["drc_pass"], bool)
        assert isinstance(res["unrouted_nets"], list)
        assert isinstance(res["layout_ref"], str) and res["layout_ref"].startswith("layout:")


def check_trivial_gt_90_percent():
    """Core acceptance: the trivial fixture must route >90% of nets."""
    srj = fixture_trivial()
    total = len(srj["connections"])
    res = route(srj, strategy="c")
    routed = total - len(res["unrouted_nets"])
    ratio = routed / total
    assert ratio > 0.90, f"trivial case routed {routed}/{total} = {ratio:.0%} (need >90%)"
    # and for the empty-obstacle trivial board it should be a full router pass
    assert res["routed"] is True, f"trivial case should be fully routed, got {res}"


def check_strategies_agree_deterministic():
    """All three strategies degrade to the same deterministic result in sandbox."""
    srj = fixture_trivial()
    out_a = route(srj, strategy="a")
    out_b = route(srj, strategy="b")
    out_c = route(srj, strategy="c")
    out_c2 = route(srj, strategy="c")
    assert out_a["layout_ref"] == out_c["layout_ref"], "A/C fallback must match"
    assert out_b["layout_ref"] == out_c["layout_ref"], "B/C fallback must match"
    assert out_c["layout_ref"] == out_c2["layout_ref"], "deterministic across runs"
    assert router_strategy_names() == ["strategy_a", "strategy_b", "strategy_c"]


def check_helper_functions_exist():
    from autorouter import (order_connections, net_class_order, route_purepython,
                            strategy_a, strategy_b, strategy_c, trace_clear)
    assert callable(order_connections)
    assert callable(net_class_order)
    assert callable(route_purepython)
    assert callable(strategy_a)
    assert callable(strategy_b)
    assert callable(strategy_c)
    assert callable(trace_clear)


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────
def main():
    tests = [check_net_order_stable, check_net_class_order, check_collision,
             check_all_fixtures_route, check_trivial_gt_90_percent,
             check_strategies_agree_deterministic, check_helper_functions_exist]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {t.__name__}: unexpected {type(e).__name__}: {e}")
    # report per-fixture route ratio as info
    for name, srj in FIXTURES:
        res = route(srj, strategy="c")
        routed = len(srj["connections"]) - len(res["unrouted_nets"])
        print(f"  INFO  fixture {name!r}: routed {routed}/{len(srj['connections'])}")
    print(f"\n{'ALL PASS' if failed == 0 else f'{failed} FAILURE(S)'} (deterministic, stdlib only)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
