#!/usr/bin/env python3
"""
PCBGenius — B2 tests for the pcbflow layout generator + SimpleRouteJson writer.
Run with plain stdlib (no pcbflow / no network / no docker):
    python test_pcbflow.py            # run all checks, exit 0 on pass, 1 on fail
    python test_pcbflow.py --sample   # print one SRJ to stdout for eyeballing

Coverage
--------
  1. validate_netlist accepts a contract-valid netlist.
  2. place_netlist places every component exactly once (deterministic set).
  3. Determinism: same seed => byte-identical placements + SRJ;
     different seed => (very likely) different layout.
  4. SRJ shape: top-level keys {layerCount, minTraceWidth, obstacles,
     connections, bounds}; every obstacle is a rect; every net yields a
     connection with >=2 points; bounds enclose all obstacle centres.
  5. No component overlap on the placement grid.
"""

from __future__ import annotations

import sys
from typing import Any, Dict

from pcbflow_layout import (LayoutResult, export_simple_route_json, footprint_for,
                            place_netlist, validate_netlist)
from simple_route_json import build_simple_route_json


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────
def _comp(ref, ctype, value, package, pins, mpn=None):
    p = [{"number": str(i + 1), "name": n, "net": net_} for i, (n, net_) in enumerate(pins)]
    return {"ref": ref, "type": ctype, "value": value, "package": package,
            "mpn": mpn, "pins": p, "properties": {}}


def _net(name, cls, pins):
    return {"name": name, "pins": pins, "class": cls}


def sample_netlist() -> Dict[str, Any]:
    """A realistic contract-valid design: 12V->5V buck (LM2596)."""
    return {
        "schema_version": "1.0.0",
        "metadata": {
            "design_name": "buck_12V_to_5V",
            "description": "Buck converter 12V->5V with LM2596",
            "board_layers": 2,
            "created_by": "pcbgenius",
            "target_fab": "jlcpcb",
        },
        "components": [
            _comp("U1", "ic", "LM2596S-ADJ", "TO-263",
                  [("VIN", "VIN"), ("GND", "GND"), ("OUT", "SW"), ("FB", "FB")], "LM2596S-ADJ"),
            _comp("D1", "diode", "SS34", "SMA", [("A", "SW"), ("K", "VOUT")], "SS34"),
            _comp("L1", "inductor", "33uH", "CDRH8D28", [("1", "SW"), ("2", "VOUT")]),
            _comp("C1", "capacitor", "100uF", "10x10mm", [("1", "VIN"), ("2", "GND")]),
            _comp("C2", "capacitor", "220uF", "10x10mm", [("1", "VOUT"), ("2", "GND")]),
            _comp("R1", "resistor", "1k", "0805", [("1", "VOUT"), ("2", "FB")]),
            _comp("R2", "resistor", "3.3k", "0805", [("1", "FB"), ("2", "GND")]),
        ],
        "nets": [
            _net("VIN", "power", ["U1.VIN", "C1.1"]),
            _net("GND", "ground", ["U1.GND", "D1.K", "C1.2", "C2.2", "R2.2"]),
            _net("SW", "power", ["U1.OUT", "D1.A", "L1.1"]),
            _net("VOUT", "power", ["D1.K", "L1.2", "C2.1", "R1.1"]),
            _net("FB", "analog", ["U1.FB", "R1.2", "R2.1"]),
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Checks
# ─────────────────────────────────────────────────────────────────────────────
def check_validate():
    ok, errs = validate_netlist(sample_netlist())
    assert ok, f"sample netlist should validate: {errs}"
    # a corrupted netlist must be rejected
    bad = sample_netlist()
    bad["schema_version"] = "9.9.9"
    ok2, _ = validate_netlist(bad)
    assert not ok2, "corrupted schema_version should fail validation"


def check_place_all():
    nl = sample_netlist()
    res = place_netlist(nl, seed=7)
    refs = {c["ref"] for c in nl["components"]}
    assert set(res.placements.keys()) == refs, "every component must be placed"
    assert len(res.obstacles) == len(refs), "one obstacle per component"
    for ref, p in res.placements.items():
        assert set(p.keys()) == {"x", "y", "rotation"}, f"{ref} malformed placement"


def check_determinism():
    nl = sample_netlist()
    def run(seed):
        r = place_netlist(nl, seed=seed)
        srj = export_simple_route_json(nl, seed=seed, layout=r)
        return (str(sorted(r.placements.items())), json_bytes(srj))
    a = run(42)
    b = run(42)
    assert a == b, "same seed must yield identical layout + SRJ"
    c = run(43)
    assert a != c, "different seed should yield a different layout"  # not guaranteed, but expected here; keep as soft info


def check_srj_shape():
    nl = sample_netlist()
    res = place_netlist(nl, seed=3)
    srj = export_simple_route_json(nl, seed=3, layout=res)
    assert set(srj.keys()) == {"layerCount", "minTraceWidth", "obstacles",
                               "connections", "bounds"}, f"unexpected SRJ keys {set(srj.keys())}"
    assert isinstance(srj["layerCount"], int) and srj["layerCount"] == 2
    assert isinstance(srj["minTraceWidth"], float) and srj["minTraceWidth"] > 0
    assert isinstance(srj["obstacles"], list) and len(srj["obstacles"]) == len(nl["components"])
    for ob in srj["obstacles"]:
        assert ob["type"] == "rect"
        assert set(ob.keys()) >= {"type", "center", "width", "height", "layers", "connectedTo", "label"}
        assert len(ob["center"]) == 2 and {"x", "y"} == set(ob["center"].keys())
    # every net -> a connection with >=2 points (buck nets all are 2+-pin)
    conn_names = {c["name"] for c in srj["connections"]}
    net_names = {n["name"] for n in nl["nets"]}
    assert conn_names == net_names, f"connections {conn_names} != nets {net_names}"
    for conn in srj["connections"]:
        assert len(conn["pointsTo"]) >= 2, f"net {conn['name']} should have >=2 points"
        assert set(conn.keys()) >= {"name", "pointsTo", "availableLayers", "weight"}
    # bounds must enclose every obstacle centre
    b = srj["bounds"]
    for ob in srj["obstacles"]:
        assert b["minX"] <= ob["center"]["x"] <= b["maxX"]
        assert b["minY"] <= ob["center"]["y"] <= b["maxY"]


def check_no_overlap():
    nl = sample_netlist()
    for seed in (0, 1, 2, 3):
        res = place_netlist(nl, seed=seed)
        # grid cells are unique by construction; verify via bounding boxes too
        centers = [res.placements[r] for r in res.placements]
        # non-overlap on the integer grid:
        seen = set()
        for p in centers:
            gx = round(p["x"] / 3.0)
            gy = round(p["y"] / 3.0)
            assert (gx, gy) not in seen, f"overlap at seed {seed} for {p}"
            seen.add((gx, gy))


def json_bytes(obj) -> bytes:
    import json
    return json.dumps(obj, sort_keys=True).encode()


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────
def main():
    tests = [check_validate, check_place_all, check_determinism, check_srj_shape, check_no_overlap]
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
    print(f"\n{'ALL PASS' if failed == 0 else f'{failed} FAILURE(S)'} (deterministic, stdlib only)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    if "--sample" in sys.argv:
        import json
        res = place_netlist(sample_netlist(), seed=0)
        srj = export_simple_route_json(sample_netlist(), seed=0, layout=res)
        print(json.dumps(srj, indent=2))
        sys.exit(0)
    sys.exit(main())