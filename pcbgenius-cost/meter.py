"""
PCBGenius E5 — meter.py
=======================
Live cost-of-design meter (feature #26): per-component price lookup with
VENDOR PRICE BREAKS + a running bill-of-materials total.

This is the live "as you edit the schematic the price nudges" meter. Two
sources of truth, in priority order:

1. LIVE LOOKUP (seam / CALLSITE)
   A callable `price_lookup(mpn, type, qty) -> float|None` passed to the
   meter. Production wires this to Octopart / JLCPCB price-break endpoints
   (Octopart Parts API returns per-quantity breaks like
   ``{1: 0.10, 100: 0.06, 1000: 0.04}``; JLCPCB SKU pricing is the same
   ladder). The exact HTTP seam is marked @CALLSITE and never faked here —
   the meter prioritises the configured lookup, then falls through.

2. DETERMINISTIC IN-MEMORY FALLBACK
   A static, per-component-type price-break table. When no live lookup is
   configured (authoring tool, tests, offline) the meter uses this so the
   meter is ALWAYS deterministic and reproducible. Same code path, same
   price-break semantics, just a fixed table.

Runner / total
--------------
The meter aggregates parts by their (value, package, mpn) group — exactly the
same unit a fab house mounts and bills — so identical parts that share an MPN
accumulate their quantity and enjoy the volume price break (economies of
scale). ``total()`` returns line-by-line breakdown plus a grand total.

Every mutation (``set_part`` / ``remove_part``) returns an explicit delta so
the UI can call attention to the cost moving.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Deterministic fallback price breaks (USD) per component type.
# Each entry is [(min_qty, unit_price), ...] ascending; a part buying `qty`
# units pays the highest break whose min_qty <= qty.
# --------------------------------------------------------------------------
FALLBACK_PRICE_BREAKS: Dict[str, List[Tuple[int, float]]] = {
    "resistor":   [(1, 0.018), (100, 0.009), (1000, 0.006)],
    "capacitor":  [(1, 0.026), (100, 0.013), (1000, 0.009)],
    "inductor":   [(1, 0.070), (100, 0.045), (500, 0.030)],
    "diode":      [(1, 0.040), (100, 0.022), (1000, 0.014)],
    "led":        [(1, 0.055), (100, 0.030), (500, 0.020)],
    "transistor": [(1, 0.090), (100, 0.055), (1000, 0.035)],
    "ic":         [(1, 0.500), (50, 0.320), (250, 0.210)],
    "connector":  [(1, 0.300), (50, 0.210), (250, 0.150)],
    "power":      [(1, 0.250), (25, 0.170), (100, 0.110)],
    "crystal":    [(1, 0.200), (25, 0.140), (100, 0.095)],
    "switch":     [(1, 0.400), (25, 0.280), (100, 0.190)],
}
FALLBACK_OTHER = [(1, 0.150), (100, 0.090), (1000, 0.060)]

# Caller-facing seam marker (git-grep "CALLSITE" for every integration point).
CALLSITE = "@CALLSITE"

_Part = Dict[str, str]
_GroupKey = Tuple[str, str, str]


def _norm(value: Any) -> str:
    """Stringify + strip for grouping; None -> '' (stable, None-safe)."""
    if value is None:
        return ""
    return str(value).strip()


def _breaks_for(ctype: str) -> List[Tuple[int, float]]:
    return FALLBACK_PRICE_BREAKS.get(ctype, FALLBACK_OTHER)


def price_at_breaks(breaks: List[Tuple[int, float]], qty: int) -> float:
    """Unit price paid when buying `qty` units under an ascending break list.

    Highest break whose min_qty <= qty wins. Empty/None breaks => 0.0.
    """
    if not breaks:
        return 0.0
    qty = max(0, int(qty))
    price = breaks[0][1]
    for min_qty, unit in breaks:
        if qty >= min_qty:
            price = unit
        else:
            break
    return price


# --------------------------------------------------------------------------
# The meter itself.
# --------------------------------------------------------------------------

class CostMeter:
    """Live running-BOM cost meter.

    Holds the parts of a design keyed by reference designator. Recomputed
    deterministically from those parts on every read, so there is no hidden
    incremental state to drift — the "running total" is always correct and
    reproducible.

    Example
    -------
        meter = CostMeter()                      # in-memory fallback pricing
        meter.add_part("R1", {"type": "resistor", "value": "10k", "package": "0603"})
        meter.add_part("R2", {"type": "resistor", "value": "10k", "package": "0603"})  # shares group -> qty 2
        d = meter.set_part("U1", {"type": "ic", "value": "ATtiny85", "package": "DIP-8", "mpn": "ATTINY85-20PU"})
        print(d["delta_usd"])                    # how much that one change moved the build
    """

    def __init__(
        self,
        price_lookup: Optional[Callable[[str, str, int], Optional[float]]] = None,
    ):
        # param `price_lookup(mpn, type, qty) -> unit price USD | None`.
        self._lookup = price_lookup
        self._parts: Dict[str, _Part] = {}
        self._sequence: List[str] = []  # insertion order of refs

    # ---- population -----------------------------------------------------

    def add_part(self, ref: str, comp: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a part, return the total-impact delta record."""
        return self.set_part(ref, comp)

    def set_part(self, ref: str, comp: Dict[str, Any]) -> Dict[str, Any]:
        """Add OR replace part `ref`; return {ref, old_total, new_total, delta}."""
        old_total = self.total()["grand_total_usd"]
        self._parts[ref] = self._normalize(comp)
        if ref not in self._sequence:
            self._sequence.append(ref)
        return self._delta(ref, old_total)

    def remove_part(self, ref: str) -> Dict[str, Any]:
        """Remove part `ref` if present; return {ref, old_total, new_total, delta}."""
        old_total = self.total()["grand_total_usd"]
        if ref in self._parts:
            del self._parts[ref]
            if ref in self._sequence:
                self._sequence.remove(ref)
        return self._delta(ref, old_total)

    def from_netlist(self, netlist: Dict[str, Any]) -> "CostMeter":
        """Load components from a contract netlist; returns self (chainable)."""
        self._parts = {}
        self._sequence = []
        for comp in netlist.get("components", []) or []:
            ref = _norm(comp.get("ref"))
            if ref:
                self._parts[ref] = self._normalize(comp)
                self._sequence.append(ref)
        return self

    # ---- pricing --------------------------------------------------------

    def _unit_price(self, group: Dict[str, Any], qty: int) -> float:
        """Unit price for a group: live lookup first, deterministic fallback else."""
        mpn = group["mpn"]
        ctype = group["type"]
        if self._lookup is not None:
            try:
                live = self._lookup(mpn, ctype, qty)
                if live is not None and live >= 0.0:
                    return float(live)
            except Exception:
                pass  # surface problems as readable fallback, never crash the meter
        # --- deterministic in-memory fallback ----------------------------
        # [CALLSITE] replace with a real Octopart/JLCPCB price-break call:
        #   GET https://octopart.com/api/v4/parts/search?mpn=<mpn>&include[]=prices
        #   -> unit = breaks[qty] (prices ladder); JLCPCB SKU endpoint is parallel.
        return price_at_breaks(_breaks_for(ctype), qty)

    # ---- grouping / totals ---------------------------------------------

    def _normalize(self, comp: Dict[str, Any]) -> _Part:
        """Project a contract component onto the fields cost cares about."""
        return {
            "type": _norm(comp.get("type")) or "other",
            "value": _norm(comp.get("value")),
            "package": _norm(comp.get("package")),
            "mpn": _norm(comp.get("mpn")),
        }

    def groups(self) -> List[Dict[str, Any]]:
        """Aggregate parts by (value, package, mpn) with quantity and unit price.

        Same grouping unit as the BOM house bills, so price breaks land where
        the real savings are (the volume of identical mounted parts).
        """
        qty_by_key: Dict[_GroupKey, int] = defaultdict(int)
        first: Dict[_GroupKey, _Part] = {}
        for ref in self._sequence:
            p = self._parts[ref]
            key = (p["value"], p["package"], p["mpn"])
            qty_by_key[key] += 1
            first.setdefault(key, p)

        rows: List[Dict[str, Any]] = []
        for key in first:
            ctype = first[key]["type"]
            qty = qty_by_key[key]
            mpn = first[key]["mpn"] or (first[key]["value"] + ":" + first[key]["package"])
            unit = self._unit_price(first[key], qty)
            rows.append(
                {
                    "mpn": mpn,
                    "type": ctype,
                    "value": first[key]["value"],
                    "package": first[key]["package"],
                    "qty": qty,
                    "unit_price_usd": round(unit, 6),
                    "line_usd": round(unit * qty, 4),
                }
            )
        rows.sort(key=lambda r: (r["value"].upper(), r["package"].upper(), r["mpn"].upper()))
        return rows

    def total(self) -> Dict[str, Any]:
        """Full meter read: {currency, num_groups, lines, grand_total_usd}."""
        rows = self.groups()
        return {
            "currency": "USD",
            "num_refs": len(self._parts),
            "num_groups": len(rows),
            "lines": rows,
            "grand_total_usd": round(sum(r["line_usd"] for r in rows), 4),
        }

    def _delta(self, ref: str, old_total: float) -> Dict[str, Any]:
        new_total = self.total()["grand_total_usd"]
        return {
            "ref": ref,
            "old_total_usd": round(old_total, 4),
            "new_total_usd": round(new_total, 4),
            "delta_usd": round(new_total - old_total, 4),
        }