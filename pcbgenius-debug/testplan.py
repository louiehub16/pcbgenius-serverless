#!/usr/bin/env python3
"""PCBGenius — D8 AI TESTING & DEBUGGING ASSISTANT (feature #12): testplan.

Generates a deterministic TEST PLAN from a (contract-valid) PCB netlist.
It never runs a simulator; the test plan describes *what* to test, the power
rail topology (which rails exist, how they cascade), the power sequencing
order (which rail must be up before the next), and the load steps (a sensible
sequence of load/no-load / insertion checks a technician or bench would run).

Design principles (matching the rest of PCGGenius):
  * PURE + DETERMINISTIC — same netlist in => byte-identical test plan out.
    No LLM, no network, no random. Output is a plain dict (single source of
    truth shape below).
  * Never crashes on a malformed netlist — if structural facts can't be
    inferred we degrade gracefully (report them as "unknown") rather than fail.

Output shape (single source of truth):

    {
      "valid": bool,                 # False when the plan is a best-effort stub
      "rails": [ { "name": str, "class": str, "voltage": str|None,
                   "components": [ref, ...] } , ... ],   # sorted, VIN/3v3 family first
      "sequencing": [                # power-on order; earlier must be up first
          { "order": int, "rail": str, "depends_on": [rail,...], "note": str }, ...
      ],
      "load_steps": [                # ordered bench steps
          { "order": int, "action": str, "target": str, "expect": str, "checks": [str] }, ...
      ],
      "warnings": [str, ...]
    }
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

VALID_NET_CLASSES = {"power", "ground", "signal", "clock", "analog", "digital"}
# Heuristic mapping : rail-name substring -> nominal voltage text, used only
# to make the plan human-readable. It is a *label*, never a measured value.
_RAIL_VOLTAGE_HINTS = {
    "3V3": "3.3V", "3v3": "3.3V", "3.3": "3.3V",
    "5V": "5V", "5v": "5V",
    "12V": "12V", "12v": "12V",
    "24V": "24V", "24v": "24V",
    "VIN": "input rail", "VCC": "3.3V-ish", "VDD": "1.8V-ish",
}


# ─────────────────────────────────────────────────────────────────────────────
# Rail extraction
# ─────────────────────────────────────────────────────────────────────────────
def _rail_voltage(name: str) -> Optional[str]:
    """Return a best-effort nominal voltage label for a rail name, else None."""
    for key, label in _RAIL_VOLTAGE_HINTS.items():
        if key in name:
            return label
    return None


def extract_rails(netlist: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return sorted power rails with the components attached to each."""
    comps = netlist.get("components", [])
    # build ref -> component map for fast lookups
    ref2comp = {c.get("ref"): c for c in comps if isinstance(c, dict)}
    rails: Dict[str, Dict[str, Any]] = {}
    for n in netlist.get("nets", []):
        if not isinstance(n, dict):
            continue
        name = n.get("name")
        cls = n.get("class", "signal")
        if cls not in ("power", "ground"):
            continue
        attached = []
        for rp in n.get("pins", []) or []:
            ref = str(rp).split(".")[0]
            if ref in ref2comp:
                attached.append(ref)
        rails[name] = {
            "name": name,
            "class": cls,
            "voltage": _rail_voltage(name) if cls == "power" else "GND",
            "components": sorted(set(attached)),
        }
    # sort: ground last, then power rails; among power rails try to detect the
    # source (input/VIN/VBUS) first so base rails precede derived rails.
    def _sort_key(item):
        cls = item["class"]
        nm = item["name"]
        if cls == "ground":
            return (2, nm)
        if any(k in nm.upper() for k in ("VIN", "VBUS")):
            return (0, nm)
        return (1, nm)

    return sorted(rails.values(), key=_sort_key)


# ─────────────────────────────────────────────────────────────────────────────
# Power sequencing
# ─────────────────────────────────────────────────────────────────────────────
def _is_source_rail(name: str) -> bool:
    up = name.upper()
    return any(k in up for k in ("VIN", "VBUS", "VCC_MAIN", "VBATT"))


def build_sequencing(rails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derive a deterministic power-on sequence.

    Rule: the source / input rail powers on first; every other power rail
    `depends_on` the source rail (regulators/converters normally derive from
    it). Ground is always last. Rails are ordered source -> derived -> ground.
    """
    seq: List[Dict[str, Any]] = []
    source = None
    for r in rails:
        if r["class"] == "ground":
            continue
        if _is_source_rail(r["name"]):
            source = r
            break
    order = 0
    for r in rails:
        if r["class"] == "ground":
            continue
        order += 1
        if source is None or r["name"] == source["name"]:
            dep: List[str] = []
            note = "primary input rail; energize first from the bench supply"
        else:
            dep = [source["name"]]
            note = f"derived rail; verify only after {source['name']} is stable"
        seq.append({"order": order, "rail": r["name"], "depends_on": dep, "note": note})
    # ground powers on last
    if any(r["class"] == "ground" for r in rails):
        order += 1
        seq.append({"order": order, "rail": "GND",
                    "depends_on": [], "note": "ground/common reference, always tied"})
    return seq


# ─────────────────────────────────────────────────────────────────────────────
# Load steps
# ─────────────────────────────────────────────────────────────────────────────
def build_load_steps(rails: List[Dict[str, Any]],
                     sequencing: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministic bench load-steps: no-load, then per-rail load checks."""
    steps: List[Dict[str, Any]] = []
    order = 0

    for r in rails:
        if r["class"] == "ground":
            continue
        order += 1
        steps.append({
            "order": order,
            "action": "power-on (no load)",
            "target": r["name"],
            "expect": f"{r['voltage']} present at steady state",
            "checks": [
                f"probe {r['name']} against GND",
                "no short reported by the DMM before energizing",
                f"observe {','.join(r['components'][:4]) or 'source'} for hot spots",
            ],
        })

    # a solid load test once all rails are up
    order += 1
    steps.append({
        "order": order,
        "action": "full-assembly load step",
        "target": "all rails",
        "expect": "all rails hold nominal voltage under the intended load",
        "checks": [
            "attach the nominal load per rail",
            "re-measure each rail; drift >5% flags a regulation problem",
            "verify sequencing order matches the plan above",
        ],
    })

    rail_names = [s["rail"] for s in sequencing if s["rail"] != "GND"]
    if len(rail_names) >= 2:
        order += 1
        steps.append({
            "order": order,
            "action": "sequencing check",
            "target": " -> ".join(rail_names),
            "expect": "rails come up in the planned order, no latch-up / brown-out",
            "checks": ["monitor supply inrush on the source rail",
                       "confirm no rail back-powers its predecessor"],
        })
    return steps


def generate_test_plan(netlist: Any) -> Dict[str, Any]:
    """Generate a deterministic test plan from a netlist (dict or JSON string)."""
    warnings: List[str] = []

    if isinstance(netlist, str):
        try:
            netlist = json.loads(netlist)
        except Exception:
            netlist = None
            warnings.append("netlist was a JSON string but failed to parse")

    if not isinstance(netlist, dict):
        warnings.append("netlist is not an object; producing an empty best-effort plan")
        return {"valid": False, "rails": [], "sequencing": [],
                "load_steps": [], "warnings": warnings}

    rails = extract_rails(netlist)
    if not rails:
        warnings.append("no power/ground rails found; check netlist class labels")
    if not any(r["class"] == "ground" for r in rails):
        warnings.append("no ground rail found; loading references may be incomplete")
    if not any(r["class"] == "power" for r in rails):
        warnings.append("no power rail found; sequencing is best-effort")

    seq = build_sequencing(rails)
    steps = build_load_steps(rails, seq)

    return {
        "valid": bool(rails),
        "rails": rails,
        "sequencing": seq,
        "load_steps": steps,
        "warnings": warnings,
    }


# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="PCBGenius test-plan generator")
    ap.add_argument("netlist", help="path to a netlist JSON file (--  or - reads stdin)")
    ap.add_argument("--out", default="", help="optional output JSON path (default: stdout)")
    a = ap.parse_args()

    if a.netlist in ("-", "--"):
        raw = __import__("sys").stdin.read()
    else:
        with open(a.netlist, "r", encoding="utf-8") as fh:
            raw = fh.read()
    nl = json.loads(raw)
    plan = generate_test_plan(nl)
    out = json.dumps(plan, indent=2)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
    else:
        print(out)


if __name__ == "__main__":
    main()