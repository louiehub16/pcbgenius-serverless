"""
PCBGenius E5 — fab_estimate.py
==============================
PCB fabrication cost (from board AREA + layer STACK) and a transparent
"your time" engineering-cost estimate — the two non-BOM halves of designing a
board.

What this adds over the D5 cost stub
------------------------------------
* Bare-board fabrication priced from board AREA (cm^2) and layer STACK
  (2L baseline, multilayer surcharge), with JLCPCB-class rates by default.
* A "your time" estimate: how many engineer hours the design realistically
  takes (schematic + layout + review) at a configurable billable rate, shown
  beside the fab lines so the designer sees that their own hours usually
  dwarf the board and parts.

Everything is a pure function of its inputs and fully deterministic. No model,
no network. Optional rate/fab overrides keep the numbers honest and auditable.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Bare-board $/cm^2 baseline for 2-layer prototyping (JLCPCB-class).
DEFAULT_FAB_RATE_PER_CM2 = 0.05
# Layered stack multiplier, relative to the 2-layer baseline. Priced per
# extra layer-PAIR (a more honest fab model than per-layer): 4L = 1.5x,
# 6L = 2.0x, 8L = 2.5x, ...
LAYER_SURCHARGE_PER_EXTRA_PAIR = 0.5
# One-time setup/stencil fees (flat, per order).
DEFAULT_SETUP_COST = 3.00
DEFAULT_STENCIL_COST = 8.00
# Edge margins so small boards aren't charged at literal zero area.
EDGE_MARGIN_MM = 2.0

# ---- "your time" defaults ------------------------------------------------
# Nominal hours by design complexity (engineering judgement; override freely).
TIME_BUDGET_HOURS: Dict[str, Dict[str, float]] = {
    "simple":   {"schematic": 1.0, "layout": 1.5, "review": 0.5},  # 3.0 h
    "medium":   {"schematic": 2.0, "layout": 3.5, "review": 1.0},  # 6.5 h
    "complex":  {"schematic": 4.0, "layout": 8.0, "review": 2.0},  # 14.0 h
}
DEFAULT_HOURLY_RATE = 75.0  # USD / hour of engineer time


def _board_area_cm2(width_mm: float, height_mm: float) -> float:
    """Board area cm^2 including a nominal edge margin."""
    w = max(0.0, width_mm + 2 * EDGE_MARGIN_MM)
    h = max(0.0, height_mm + 2 * EDGE_MARGIN_MM)
    return (w * h) / 100.0  # mm^2 -> cm^2


def layer_multiplier(layers: Optional[int]) -> float:
    """Fabrication surcharge for multilayer stacks (2L baseline).

    Costs scale per extra layer-PAIR: 2L = 1.0x, 4L = 1.5x, 6L = 2.0x, ...
    """
    n = int(layers or 2)
    if n < 2:
        n = 2
    return 1.0 + LAYER_SURCHARGE_PER_EXTRA_PAIR * max(0, (n - 2) // 2)


def estimate_pcb(
    width_mm: float,
    height_mm: float,
    layers: Optional[int] = None,
    quantity: int = 1,
    fab_rate_per_cm2: Optional[float] = None,
    setup_cost: Optional[float] = None,
    stencil_cost: Optional[float] = None,
) -> Dict[str, Any]:
    """Fabrication cost for `quantity` bare boards of a given size + stack.

    Returns a line-item breakdown (boards, stencil, setup) plus total.
    """
    quantity = max(1, int(quantity))
    area_cm2 = _board_area_cm2(width_mm, height_mm)
    n = int(layers or 2)
    rate = fab_rate_per_cm2 if fab_rate_per_cm2 is not None else DEFAULT_FAB_RATE_PER_CM2
    mult = layer_multiplier(n)

    board_unit = area_cm2 * rate * mult
    boards = round(board_unit * quantity, 4)
    stencil = (DEFAULT_STENCIL_COST if stencil_cost is None else stencil_cost) if area_cm2 > 0 else 0.0
    setup = setup_cost if setup_cost is not None else DEFAULT_SETUP_COST

    total = round(boards + stencil + setup, 4)
    return {
        "currency": "USD",
        "width_mm": width_mm,
        "height_mm": height_mm,
        "area_cm2": round(area_cm2, 4),
        "layers": n,
        "layer_mult": round(mult, 3),
        "quantity": quantity,
        "breakdown": {
            "boards": boards,
            "stencil": stencil,
            "setup": setup,
        },
        "total_usd": total,
    }


def time_estimate(
    complexity: str = "medium",
    hourly_rate: Optional[float] = None,
    hours_override: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Estimate the engineer-hours ("your time") and its cost for a design.

    Params:
      complexity     : 'simple' | 'medium' | 'complex' -> TIME_BUDGET_HOURS.
      hourly_rate    : billable $/hr (default 75).
      hours_override : optional {task: hours} to fully control the breakdown.

    Returns hours split + labor cost total.
    """
    key = complexity if complexity in TIME_BUDGET_HOURS else "medium"
    hours = dict(TIME_BUDGET_HOURS[key])
    if hours_override:
        hours.update({k: max(0.0, float(v)) for k, v in hours_override.items()})
    rate = hourly_rate if hourly_rate is not None else DEFAULT_HOURLY_RATE

    total_hours = round(sum(hours.values()), 2)
    return {
        "complexity": key,
        "hourly_rate_usd": rate,
        "hours": hours,
        "total_hours": total_hours,
        "labor_cost_usd": round(total_hours * rate, 2),
    }


def estimate_board(
    board: Dict[str, Any],
    complexity: str = "medium",
    hourly_rate: Optional[float] = None,
    fab_rate_per_cm2: Optional[float] = None,
) -> Dict[str, Any]:
    """One-call combiner from a board dict.

    Accepts either:
      {width_mm, height_mm, layers?, quantity?, netlist?}
    or a full contract netlist + a properties block for the outline. Kept
    dependency-light: does NOT import cost.py or meter.py so it can be used
    standalone for the pure "board + your time" estimate.
    """
    # Resolve outline + quantity from the dict or netlist metadata.
    nl = board.get("netlist") or board
    md = nl.get("metadata") or {}
    props = md.get("properties") or {}
    width = float(board.get("width_mm", props.get("width_mm", 40.0)))
    height = float(board.get("height_mm", props.get("height_mm", 30.0)))
    layers = board.get("layers") or md.get("board_layers")
    quantity = int(board.get("quantity", 1))

    pcb = estimate_pcb(width, height, layers, quantity, fab_rate_per_cm2=fab_rate_per_cm2)
    your_time = time_estimate(complexity, hourly_rate)

    return {
        "currency": "USD",
        "pcb": pcb,
        "your_time": your_time,
        "design_total_usd": round(pcb["total_usd"] + your_time["labor_cost_usd"], 2),
    }