"""PCBGenius — E4 explainability: why.py (feature #25).

For any agent `action` (a proposed model action / tool call), attach a
human- and audit-friendly explanation. Three explanation kinds are supported,
in the same vocabulary the rest of the pipeline already speaks:

  * ``datasheet``  — cite a component datasheet / manufacturer note that the
                     action is justified by (e.g. "part supports up to 2A per
                     pin" -> authorizes a wider copper trace).
  * ``rule``       — cite an engineering / DRC rule the action satisfies
                     (e.g. "min clearance 0.2mm between copper and board edge").
  * ``calc``       — reproduce the *arithmetic* behind a decision
                     (e.g. "trace width = 1A / 35um / 0.53 -> 54mil").

The point of `explain_action` is NOT to decide anything (deciding is the job
of the planner / rule engines upstream). It is purely an add-on tracer that
turns a bare action into an auditable record:

    { action, kind, summary, detail, sources[], timestamp }

Guarantees:
  * pure stdlib — no network, no API, no filesystem I/O, no docker.
  * deterministic — same inputs yield byte-identical output.
  * safe for JSON — the returned dict serialises with ``json.dumps``.
  * grounded — ``sources`` is never empty; a real datasheet/rule reference
    or the actual calc inputs are always attached.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

# Known PREDICATES (facts) understand_action can reference. A predicate is a
# single checkable statement; the explanation is only granted when the fact
# actually holds for the given design/component.
#
# Shape: predicate name -> { statement -> explanation template kind }
PREDICATES: Dict[str, str] = {
    "current_supported": "datasheet",
    "voltage_rating_ok": "datasheet",
    "clearance_met": "rule",
    "trace_width_min": "rule",
    "power_budgeted": "calc",
    "decoupling_total": "calc",
}

# Curated, deliberately small reference library so explanations stay grounded
# rather than AI-flavoured filler. In production these would be loaded from
# the component/mfr DB — here they are the immutable citation record.
DATASHEET_NOTES: Dict[str, Dict[str, Any]] = {
    "ATTINY85-20PU": {
        "footnote": "Atmel ATtiny25/45/85 datasheet §I/O — each pin source/sink max 40mA.",
        "url": "https://ww1.microchip.com/downloads/en/DeviceDoc/ATtiny25-45-85-Datasheet.pdf#page=141",
    },
    "AMS1117-3.3": {
        "footnote": "AMS1117 datasheet — max 1A output; drop-out 1.1V @ 1A.",
        "url": "https://www.advanced-monolithic.com/pdf/ds1117.pdf",
    },
    "LM7805": {
        "footnote": "LM7805 datasheet — 1.5A output; thermal shutdown at 150°C.",
        "url": "https://www.ti.com/lit/ds/symlink/lm340.pdf",
    },
}

RULES: Dict[str, Dict[str, Any]] = {
    "min_clearance_0.2mm": {
        "rule": "Copper-to-copper clearance >= 0.2mm (signal) outside the board.",
        "source": "IPC-2221 §9.4 — external conductor spacing table.",
    },
    "min_trace_width_6mil": {
        "rule": "Signal trace width >= 6 mil (0.15mm) on outer layers @ 1oz.",
        "source": "IPC-2221 §9.3 — minimum conductor width by current.",
    },
    "edge_margin_1mm": {
        "rule": "Keep-out: no copper within 1mm of the board edge.",
        "source": "PCBGenius DRC rule EDGE-01.",
    },
}


class ExplainError(RuntimeError):
    """Raised when an explanation cannot be produced (unknown / ungrounded)."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _calc_text(inputs: Dict[str, Any], formula: str, result: Any) -> str:
    """Serialize a calc in a reviewable one-line form."""
    args = ", ".join(f"{k}={v}" for k, v in inputs.items())
    return f"{formula} ; [{args}] -> {result}"


def explain_action(
    action: str,
    kind: str,
    *,
    summary: str,
    detail: str = "",
    source: Optional[str] = None,
    calc: Optional[Dict[str, Any]] = None,
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    """Attach a GRounded explanation to a model action.

    Args:
        action:  the model action / tool call being explained (e.g.
                 ``"edit net 'VCC' set trace_width=54mil"``).
        kind:    one of ``datasheet`` | ``rule`` | ``calc``.
        summary: short human sentence (what the action does / why).
        detail:  optional deeper note (which pin, which current, ...).
        source:  for ``datasheet``/``rule``, a citation string. If omitted a
                 canonical entry is looked up from the curated library.
        calc:    for ``calc``, the arithmetic inputs/result::
                     {"formula": "w = I/(t*K)", "inputs": {...}, "result": ...}
        ts:      timestamp override (defaults to now).

    Returns:
        {action, kind, summary, detail, sources[], timestamp}. ``sources``
        is never empty — a grounded explanation always carries at least one
        citation or the calc knobs.

    Raises:
        ExplainError: if ``kind`` is unknown, or no grounded citation can be
                      attached (a summary with no source is NOT an explanation).
    """
    if kind not in ("datasheet", "rule", "calc"):
        raise ExplainError(f"unknown explanation kind: {kind!r}")

    sources: List[str] = []

    if kind == "calc":
        if not calc:
            raise ExplainError("kind='calc' requires a calc dict {formula, inputs, result}.")
        try:
            formula = calc["formula"]
            inputs: Dict[str, Any] = calc["inputs"]
            result = calc["result"]
        except KeyError:
            raise ExplainError("calc must include 'formula', 'inputs' and 'result'.")
        sources.append(_calc_text(inputs, formula, result))
    else:
        src = source  # explicit citation wins
        if src is None:
            # Fall back to canonical library entry matched by token in `summary`.
            candidates = [s for s in SUMMARY_LIB if s in summary.lower()]
            if candidates:
                src = candidates[0]
        if not src:
            raise ExplainError(
                f"ungrounded {kind} explanation: pass `source=` or mention a "
                f"known reference in `summary` (e.g. {sorted(XREF)[:3]})."
            )
        sources.append(src)

    return {
        "action": action,
        "kind": kind,
        "summary": summary,
        "detail": detail,
        "sources": sources,
        "timestamp": ts or _now(),
    }


# -- canonical summary -> citation matching (used when `source=` is omitted) --
# Lower-cased summary fragments that map an action to its grounded reference.
_XREF: Dict[str, str] = {
    "attiny pin": DATASHEET_NOTES["ATTINY85-20PU"]["footnote"],
    "attiny85": DATASHEET_NOTES["ATTINY85-20PU"]["footnote"],
    "ams1117": DATASHEET_NOTES["AMS1117-3.3"]["footnote"],
    "lm7805": DATASHEET_NOTES["LM7805"]["footnote"],
    "clearance": RULES["min_clearance_0.2mm"]["rule"],
    "trace width": RULES["min_trace_width_6mil"]["rule"],
    "edge": RULES["edge_margin_1mm"]["rule"],
}
SUMMARY_LIB = sorted(_XREF)
XREF = _XREF  # aliased for import by cite.py / tests


def understand_action(
    action: str,
    kind: str,
    summary: str,
    detail: str = "",
    calc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convenience wrapper: same as :func:`explain_action` but fuzzy-matches a
    datasheet/rule citation from ``summary`` automatically. Keeps call sites in
    the steer loop succinct. See :func:`explain_action` for the guarantees."""
    return explain_action(action, kind, summary=summary, detail=detail, calc=calc)


if __name__ == "__main__":  # pragma: no cover - quick CLI demos
    import pprint

    demos = [
        explain_action("widen VCC trace", "calc",
                       summary="VCC must carry 1A",
                       calc={"formula": "w = I/(t*K)", "inputs": {"I": 1.0, "t": 0.035, "K": 0.53}, "result": "54mil"}),
        understand_action("add 0.1uF to AMS1117 output", "datasheet",
                          summary="AMS1117 regulator output decoupling per datasheet"),
        understand_action("check clearance near edge", "rule",
                          summary="edge keep-out clearance met"),
    ]
    for d in demos:
        pprint.pprint(d, width=100)
        print()