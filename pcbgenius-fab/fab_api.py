"""
PCBGenius D5 — fab_api.py
=========================
Submission stubs for the two fab houses the contract supports:
  * JLCPCB  (metadata.target_fab == "jlcpcb")
  * PCBWay  (metadata.target_fab == "pcbway")

These are ORDER-SUBMISSION STUBS. Each public function marks its "call site"
— the exact spot where a real integration (API key, signed order call, file
upload) must be wired in — with a `@CALLSITE` marker so the follow-up build
engineer can find every seam in one grep. No real network call is made and no
credentials are stored.

Pipelines land here with a fully-built BOM so the "submit" can validate order
payload shape before anything is sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:  # imported as part of the pcbgenius-fab package
    from .bom import build_bom
    from .cost import estimate_cost
except ImportError:  # imported standalone (tests, scripts on plain sys.path)
    from bom import build_bom
    from cost import estimate_cost

# Human-readable marker for integration seams. Grep the repo for "CALLSITE"
# to find every place an engineer must implement a real fab API call.
CALLSITE = "@CALLSITE"


class FabAPIError(RuntimeError):
    """Raised for validation/ordering errors before any network I/O."""


@dataclass
class OrderResult:
    """Returned by a successful (stub) order submission."""

    order_id: str
    fab: str
    status: str = "submitted"
    bom_rows: int = 0
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "fab": self.fab,
            "status": self.status,
            "bom_rows": self.bom_rows,
            "notes": self.notes,
        }


def _validate_netlist(netlist: Dict[str, Any]) -> None:
    if not isinstance(netlist, dict):
        raise FabAPIError("netlist must be an object")
    comps = netlist.get("components", [])
    if not isinstance(comps, list) or not comps:
        raise FabAPIError("netlist has no components — cannot order empty board")
    # refs must be unique (else BOM grouping collapses distinct parts).
    refs = [c.get("ref") for c in comps]
    if len(refs) != len(set(refs)):
        raise FabAPIError("duplicate component refs in netlist")


# --------------------------------------------------------------------------
# House-specific order builders
# --------------------------------------------------------------------------

def _build_order_payload(netlist: Dict[str, Any], quantity: int = 1) -> Dict[str, Any]:
    """Shared payload: BOM rows + board params, ready for any house."""
    bom_rows = build_bom(netlist)
    md = netlist.get("metadata", {})
    width, height = _board_outline(netlist)
    cost = estimate_cost(
        netlist,
        width_mm=width,
        height_mm=height,
        quantity=quantity,
    )
    return {
        "order": {
            "design": md.get("design_name", "untitled"),
            "target_fab": md.get("target_fab"),
            "quantity": quantity,
            "board": {"width_mm": width, "height_mm": height,
                      "layers": md.get("board_layers", 2)},
            "bom": bom_rows,
        },
        "cost_estimate_usd": cost["total_usd"],
    }


def _board_outline(netlist: Dict[str, Any]) -> tuple:
    """Board size from properties if present, else sane defaults."""
    md = netlist.get("metadata", {})
    p = md.get("properties") or {}
    w = float(p.get("width_mm", 40.0))
    h = float(p.get("height_mm", 30.0))
    return w, h


def submit_jlcpcb(
    netlist: Dict[str, Any],
    quantity: int = 1,
    api_key: Optional[str] = None,
) -> OrderResult:
    """Stub JLCPCB order submission.

    [CALLSITE] SEAM: replace this body with a real JLCPCB OpenLab SMT / JLC
    assembly order request. `api_key` is accepted for drop-in signature parity
    but never sent.
    """
    _validate_netlist(netlist)
    if md := netlist.get("metadata", {}):
        if md.get("target_fab") not in (None, "jlcpcb"):
            raise FabAPIError(f"target_fab={md.get('target_fab')!r} not JLCPCB")

    payload = _build_order_payload(netlist, quantity)
    bom_rows = len(payload["order"]["bom"])

    # --- REAL JLCPCB CALL WOULD GO HERE ---
    # CALLSITE: authed HTTP POST -> https://jlcpcb.com/api/... (upload gerbers + BOM)
    order_id = "JLC-STUB-000001"

    return OrderResult(
        order_id=order_id,
        fab="jlcpcb",
        bom_rows=bom_rows,
        notes=[f"stub payload cost=${payload['cost_estimate_usd']}", CALLSITE],
    )


def submit_pcbway(
    netlist: Dict[str, Any],
    quantity: int = 1,
    api_key: Optional[str] = None,
) -> OrderResult:
    """Stub PCBWay order submission.

    [CALLSITE] SEAM: replace this body with a real PCBWay assembly-order
    request. `api_key` is accepted for signature parity but never sent.
    """
    _validate_netlist(netlist)
    if md := netlist.get("metadata", {}):
        if md.get("target_fab") not in (None, "pcbway"):
            raise FabAPIError(f"target_fab={md.get('target_fab')!r} not PCBWay")

    payload = _build_order_payload(netlist, quantity)
    bom_rows = len(payload["order"]["bom"])

    # --- REAL PCBWAY CALL WOULD GO HERE ---
    # CALLSITE: authed HTTP POST -> https://www.pcbway.com/... (upload gerbers + BOM)
    order_id = "PCBWAY-STUB-000002"

    return OrderResult(
        order_id=order_id,
        fab="pcbway",
        bom_rows=bom_rows,
        notes=[f"stub payload cost=${payload['cost_estimate_usd']}", CALLSITE],
    )


def submit_order(netlist: Dict[str, Any], fab: str, **kw) -> OrderResult:
    """Dispatch by fab string from metadata.target_fab: 'jlcpcb' | 'pcbway'."""
    name = (fab or "").lower()
    if name == "jlcpcb":
        return submit_jlcpcb(netlist, **kw)
    if name == "pcbway":
        return submit_pcbway(netlist, **kw)
    raise FabAPIError(f"unsupported fab: {fab!r} (expected jlcpcb|pcbway)")


def list_call_sites() -> List[str]:
    """Return the marked integration seams (for build engineer / CI audit)."""
    return [
        "submit_jlcpcb  -> real JLCPCB OpenLab SMT order POST",
        "submit_pcbway  -> real PCBWay assembly-order POST",
    ]