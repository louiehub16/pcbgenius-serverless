"""
PCBGenius — run_drc verifier (C2 multi-layer, feature #15)
==========================================================

Maps the multi-layer rule checks onto the FROZEN CONTRACT `run_drc` return
shape exactly:

    { "pass": bool,
      "violations": [ { "rule", "severity", "location", "message" } ] }

    (contract Section 2, tool_call `run_drc`)

`run_drc` is called as `run_drc(netlist, layout)`; `layout` is object|null.
This verifier never raises on malformed input — it degrades to a safe,
applicable subset of rules (a missing layout short-circuits to pass).
"""

from __future__ import annotations

from typing import Any, Dict

from rules.multilayer_rules import check_multilayer


def run_drc(netlist: Dict[str, Any], layout: Dict[str, Any] | None) -> Dict[str, Any]:
    """Contract run_drc entry point → { pass, violations:[{rule,severity,location,message}] }.

    Args:
        netlist: the netlist_schema object (metadata.board_layers drives the
            layer-count consistency check).
        layout:  the layout/board object (or None). See rules.multilayer_rules
            for the consumed schema.

    Returns:
        Contract-shaped result. `pass` is True when there are no high-severity
        (error) violations. Violations carry every field the frontend and the
        backend DRC route consume / emit.
    """
    pass_, violations = check_multilayer(netlist, layout)
    return {
        "pass": pass_,
        "violations": [
            {
                "rule": v["rule"],
                "severity": v["severity"],
                "location": v["location"],
                "message": v["message"],
            }
            for v in violations
        ],
    }


def summarize(result: Dict[str, Any]) -> str:
    """Short human-readable summary (useful for logs / CLI)."""
    n = len(result["violations"])
    if result["pass"] and n == 0:
        return "DRC PASS — no multi-layer violations."
    codes = ", ".join(sorted({v["rule"] for v in result["violations"]}))
    if result["pass"]:
        return f"DRC PASS (warnings only) — {codes}"
    return f"DRC FAIL — errors: {codes}"


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    nl = json.loads(open(sys.argv[1]).read())
    layout = json.loads(open(sys.argv[2]).read()) if len(sys.argv) > 2 else None
    print(summarize(run_drc(nl, layout)))