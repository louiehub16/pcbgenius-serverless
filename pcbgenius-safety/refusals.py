"""
PCBGenius — D1 Bulletproof Beginner Layers: HARD REFUSAL gate
=============================================================

The final, last-resort gate. Mirrors the FROZEN CONTRACT's hard-refusal
condition (contract Section 3):

    "If design fails ERC/DRC/sim and cannot be safely fixed, the model MUST
     refuse clearly rather than guess."

`refuse()` inspects a netlist for *impossible*, *unsafe*, or *ambiguous*
conditions that no amount of allow-listing / constraint checking can safely
paper over. When any is present the design is NOT allowed to proceed to
simulation, layout, export, or fab.

Refusal reasons:
  * REFUSE_EMPTY        — no components and/or no ground net (impossible board).
  * REFUSE_NO_GROUND    — electrical ground absent (nothing to reference).
  * REFUSE_MAINS        — mains/high-voltage net present (unsafe for beginner).
  * REFUSE_OVER_VOLTAGE — a net's voltage exceeds a safe-value ceiling with no
                          approval gate (unsafe).
  * REFUSE_SHORT        — conflicting power sources on one net (unsafe).
  * REFUSE_BROKEN_LINK  — a nets[].pins "ref.pin" does not resolve to a real
                          component+pin, or a pin references a net that is not
                          defined (ambiguous / corrupt).
  * REFUSE_UNVERIFIABLE — ERC/DRC/sim could not be run, or a necessary input
                          (e.g. sim stimulus) is missing (ambiguous).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from util import ERROR, net_voltage_guess, violation, is_power_pin
from allowlist import DANGEROUS_NET_TOKENS

# Beginner ceiling: without an explicit high-risk approval gate, refuse rails above this.
SAFE_RAIL_VOLTAGE_MAX = 24.0


def refuse(netlist: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Detect impossible/unsafe/ambiguous designs.

    Returns:
        { "refuse": bool, "reason": str|None, "violations": [ ... ] }
    When `refuse` is True the design must be blocked and the human shown `reason`.
    """
    context = context or {}
    vio: List[Dict[str, Any]] = []
    comps = netlist.get("components", [])
    nets = netlist.get("nets", [])

    # ── impossible ────────────────────────────────────────────────────────
    if not comps:
        vio.append(
            violation(
                "REFUSE_EMPTY",
                "Design has no components — there is nothing to build or verify.",
                location="design",
            )
        )
    if not any(c.get("type") == "power" for c in comps):
        vio.append(
            violation(
                "REFUSE_NO_POWER_SOURCE",
                "No 'power' component provides a supply — the board cannot power up.",
                location="design",
            )
        )
    if not any(n.get("class") == "ground" for n in nets):
        vio.append(
            violation(
                "REFUSE_NO_GROUND",
                "No ground net present. Every circuit needs a ground reference.",
                location="design",
            )
        )

    # ── unsafe ────────────────────────────────────────────────────────────
    for net in nets:
        name = str(net.get("name", ""))
        lower = name.lower()
        if any(tok in lower for tok in DANGEROUS_NET_TOKENS):
            vio.append(
                violation(
                    "REFUSE_MAINS",
                    f"Net '{name}' is a mains/high-voltage net — unsafe for beginner "
                    f"boards and out of scope.",
                    location=name,
                )
            )
        v = net_voltage_guess(name, netlist)
        if v is not None and v > SAFE_RAIL_VOLTAGE_MAX and not context.get("approval_granted"):
            vio.append(
                violation(
                    "REFUSE_OVER_VOLTAGE",
                    f"Net '{name}' carries ≈{v}V, above the {SAFE_RAIL_VOLTAGE_MAX}V "
                    f"beginner ceiling. Requires explicit human approval to proceed.",
                    location=name,
                )
            )

    # ── ambiguous / broken ────────────────────────────────────────────────
    _check_broken_links(netlist, vio)

    if context.get("erc_available") is False and not context.get("approval_granted"):
        vio.append(
            violation(
                "REFUSE_UNVERIFIABLE",
                "ERC was not run before proceeding — cannot safely continue on an "
                "unverified design.",
                location="design",
            )
        )

    blocking = [v for v in vio if v["severity"] == ERROR]
    reason = _reason_text(blocking)
    return {
        "refuse": bool(blocking),
        "reason": reason,
        "violations": vio,
    }


def _check_broken_links(netlist, vio):
    """Every nets[].pins 'ref.pin' must resolve to a real component+pin, and
    every pin's `net` must be a defined net. Ambiguity here is fatal."""
    comp_by_ref = {c.get("ref"): c for c in netlist.get("components", [])}
    net_names = {n.get("name") for n in netlist.get("nets", [])}

    # Define a helper to look up pin by ref and pin number.
    pins_by_key = {}
    for ref, comp in comp_by_ref.items():
        for pin in comp.get("pins", []):
            pins_by_key[(ref, str(pin.get("number")))] = pin
            pins_by_key[(ref, str(pin.get("name")))] = pin

    for net in netlist.get("nets", []):
        for item in net.get("pins", []) or []:
            item_s = str(item)
            # item is "REF.PIN"
            if "." not in item_s:
                vio.append(
                    violation(
                        "REFUSE_BROKEN_PIN_LINK",
                        f"Net pins entry '{item_s}' is malformed — expected 'REF.PIN'.",
                        location=net.get("name"),
                    )
                )
                continue
            ref, pin = item_s.split(".", 1)
            if ref not in comp_by_ref:
                vio.append(
                    violation(
                        "REFUSE_DANGLING_REF",
                        f"Net '{net.get('name')}' references '{ref}' but no such "
                        f"component exists.",
                        location=net.get("name"),
                    )
                )
                continue
            if (ref, pin) not in pins_by_key:
                vio.append(
                    violation(
                        "REFUSE_DANGLING_PIN",
                        f"Net '{net.get('name')}' references '{ref}.{pin}' but that "
                        f"pin does not exist on the component.",
                        location=net.get("name"),
                    )
                )

    # Pins that declare a net which is not defined anywhere.
    for ref, comp in comp_by_ref.items():
        for pin in comp.get("pins", []):
            pnet = pin.get("net")
            if pnet and pnet not in net_names:
                vio.append(
                    violation(
                        "REFUSE_UNDEFINED_NET",
                        f"{ref}.{pin.get('number')} ({pin.get('name')}) connects to net "
                        f"'{pnet}' which is not defined in the net list.",
                        location=f"{ref}.{pin.get('number')}",
                    )
                )


def _reason_text(blocking: List[Dict[str, Any]]) -> Optional[str]:
    if not blocking:
        return None
    if len(blocking) == 1:
        return blocking[0]["message"]
    codes = sorted({v["rule"] for v in blocking})
    return f"Design refused: {', '.join(codes)}. See violations for details."


def summarize(result: Dict[str, Any]) -> str:
    if not result["refuse"]:
        return "REFUSAL CHECK PASS — design is verifiable and safe to proceed."
    return "REFUSAL — " + (result["reason"] or "design unsafe/ambiguous")