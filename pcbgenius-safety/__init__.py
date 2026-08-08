"""
PCBGenius — D1 Bulletproof Beginner Layers (package entry)
==========================================================

A three-gate safety layer that guards the FROZEN CONTRACT netlist before it is
allowed to proceed to simulation, layout, export, or fab:

    Gate 1  allowlist   — component type / package / net allowlist (Section 1+3).
    Gate 2  constraints — spec-checker: voltage-scaled clearance + IC decoupling.
    Gate 3  refusals    — hard-refusal for impossible / unsafe / ambiguous designs.

Public entry points
-------------------
    run_safety(netlist, context=None, layout=None) -> dict
        Full gate stack. Returns contract-friendly verdict.
    run_allowlist(netlist)        -> Gate 1 verdict.
    run_constraints(netlist, layout=None) -> Gate 2 verdict.
    run_refusals(netlist, context=None)   -> Gate 3 verdict.

Every function is pure (no I/O, no network, no filesystem) and safe to call
from the backend routes, CLI, tests, or the fine-tuned model's tool plumbing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import allowlist as _allowlist
import constraints as _constraints
import refusals as _refusals

__all__ = [
    "run_safety",
    "run_allowlist",
    "run_constraints",
    "run_refusals",
    "VERSION",
]

VERSION = "1.0.0"


def run_allowlist(netlist: Dict[str, Any]) -> Dict[str, Any]:
    vio = _allowlist.check(netlist)
    return {
        "pass": not _allowlist.is_blocking(vio),
        "violations": vio,
        "summary": _allowlist.summarize(vio),
    }


def run_constraints(
    netlist: Dict[str, Any],
    layout: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    vio = _constraints.check(netlist, layout)
    return {
        "pass": not _constraints.is_blocking(vio),
        "violations": vio,
        "summary": _constraints.summarize(vio),
    }


def run_refusals(
    netlist: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _refusals.refuse(netlist, context)


def run_safety(
    netlist: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    layout: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the full three-gate safety stack.

    Returns a single verdict the backend can return directly:
        { "version", "pass", "refused",
          "gates": { "allowlist", "constraints", "refusals" },
          "violations": [ ... all violations ... ] }

    `pass` is False if ANY gate blocks or refuses. The frontend/backend shows the
    user the violations and, when refused, the refusal reason.
    """
    allow = run_allowlist(netlist)
    constr = run_constraints(netlist, layout)
    refus = run_refusals(netlist, context)

    all_vio = allow["violations"] + constr["violations"] + refus["violations"]
    passed = allow["pass"] and constr["pass"] and not refus["refuse"]
    return {
        "version": VERSION,
        "pass": passed,
        "refused": refus["refuse"],
        "gates": {
            "allowlist": allow,
            "constraints": constr,
            "refusals": refus,
        },
        "violations": all_vio,
    }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    nl = json.loads(open(sys.argv[1]).read())
    result = run_safety(nl)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["pass"] else 1)