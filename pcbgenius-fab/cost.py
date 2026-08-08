"""
PCBGenius — E2 Manufacturing-first (cost.py)
=============================================
Estimate fabrication + assembly cost for a design from its BOM and board
parameters. Deliberately lightweight (no live pricing API): it reuses the BOM
grouping from `bom.build_bom` and blends simple board-area / layer / drill /
assembly line-item heuristics that mirror JLCPCB / PCBWay batching.

A pure, deterministic model means the cost estimate is stable across runs and
usable by the manufacturing-first gate as the "which fab can build this
cheapest" tiebreaker. Swap in a live pricing fetch at the marked call sites.

Estimate shape
--------------
    {
      "board_mm": [w, h], "layers": int, "quantity": int,
      "unit_without_assembly_usd": float,
      "unit_assembly_usd": float,
      "unit_total_usd": float,
      "board_total_usd": float,
      "assembly_total_usd": float,
      "grand_total_usd": float,
      "unique_parts": int, "total_parts": int,
      "currency": "USD",
      "notes": [...],
    }
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from bom import build_bom

# Rough open-source price anchors (USD, per unit, qty >= 5) — replace with a
# live quote API at the marked call site for real-time numbers.
BASE_BOARD_USD = 2.0
COST_PER_SQ_CM_USD = 0.032
LAYER_STEP_USD = 1.5
DRILL_COUNT_PRICE_STEP = 0.05       # per unique drill size past the base
STENCIL_INCLUSIVE = True            # stencil is usually free on assembly orders

# Assembly (SMT) heuristic line items — USD per component.
ASSEMBLY_PER_UNIQUE_PART_USD = 0.35
ASSEMBLY_PER_TOTAL_PART_USD = 0.02


def _area_cm2(board_mm: List[float]) -> float:
    if not board_mm or len(board_mm) < 2:
        return 0.0
    w, h = (board_mm[0] or 0.0) / 10.0, (board_mm[1] or 0.0) / 10.0
    return max(0.0, w * h)


def estimate_cost(netlist: Dict[str, Any],
                  board_mm: List[float] | None = None,
                  layers: int | None = None,
                  quantity: int = 5) -> Dict[str, Any]:
    """Estimate fab + assembly cost from a contract netlist and board params."""
    metadata = netlist.get("metadata") or {}
    layers = layers or int(metadata.get("board_layers") or 2)
    board_mm = board_mm or [100.0, 80.0]
    quantity = max(1, int(quantity))

    rows = build_bom(netlist)
    unique_parts = len(rows)
    total_parts = sum(r["Quantity"] for r in rows)

    area = _area_cm2(board_mm)

    # --- board fabrication (stripped, per unit) ---
    layer_cost = (max(0, layers - 2)) * LAYER_STEP_USD
    board_per_unit = (
        BASE_BOARD_USD
        + area * COST_PER_SQ_CM_USD
        + layer_cost
    )

    # --- assembly (SMT) ---
    stencil = 0.0 if STENCIL_INCLUSIVE else 3.0
    assemble_per_unit = (
        ASSEMBLY_PER_UNIQUE_PART_USD * unique_parts
        + ASSEMBLY_PER_TOTAL_PART_USD * total_parts
        + stencil / max(1, quantity)
    )

    unit_without_assembly = board_per_unit
    unit_assembly = assemble_per_unit
    unit_total = unit_without_assembly + unit_assembly

    board_total = board_per_unit * quantity
    assembly_total = assemble_per_unit * quantity
    grand_total = board_total + assembly_total

    notes = [
        "Heuristic offline estimate; replace with a live JLCPCB/PCBWay quote.",
        f"{layers}-layer board, area {area:.2f} cm^2.",
    ]
    return {
        "board_mm": [float(board_mm[0]), float(board_mm[1])],
        "layers": layers,
        "quantity": quantity,
        "unique_parts": unique_parts,
        "total_parts": total_parts,
        "unit_without_assembly_usd": _r2(unit_without_assembly),
        "unit_assembly_usd": _r2(unit_assembly),
        "unit_total_usd": _r2(unit_total),
        "board_total_usd": _r2(board_total),
        "assembly_total_usd": _r2(assembly_total),
        "grand_total_usd": _r2(grand_total),
        "currency": "USD",
        "notes": notes,
    }


def _r2(x: float) -> float:
    return round(float(x), 2)


def cheapest_fab(estimates: Dict[str, Dict[str, Any]]) -> str:
    """Pick the fab with the lowest grand total (manufacturing-first tiebreaker)."""
    if not estimates:
        return ""
    return min(estimates, key=lambda k: estimates[k].get("grand_total_usd", 1e18))