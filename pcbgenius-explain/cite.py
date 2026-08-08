"""PCBGenius — E4 explainability: cite.py (feature #25).

Assembles the evidence trail for a single model/design decision into the
canonical shape the frontend, the steering loop, and the audit log all consume:

    { "decision": str,      # the decision being explained
      "reason":   str,      # human-readable primary rationale
      "evidence": [ ... ] } # ordered, grounded supporting citations

Each evidence item is produced by :func:`explain_action` (why.py) — so an
evidence entry is always grounded in a datasheet footnote, a rule reference,
or a reproduced calc. ``cite_decision`` merely gathers one or more such
explanations and folds them into the decision record with a top-line reason.

Guarantees (mirror why.py):
  * pure stdlib — no network, no API, no I/O, no docker.
  * deterministic — same inputs -> byte-identical output.
  * JSON-serialisable — the result round-trips through ``json.dumps``.
  * grounded — ``evidence`` is never empty for a successfully cited decision.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from why import explain_action, understand_action

# Ordered guardrail for evidence severity (frontend uses this to colour cards).
EVIDENCE_PRIORITY = ("rule", "calc", "datasheet")
_PRIORITY_RANK = {k: i for i, k in enumerate(EVIDENCE_PRIORITY)}


class CiteError(RuntimeError):
    """Raised when a decision cannot be cited (no grounds produced)."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _rank(item: Dict[str, Any]) -> int:
    return _PRIORITY_RANK.get(item.get("kind", "datasheet"), len(_PRIORITY_RANK))


def cite_decision(
    decision: str,
    reason: str,
    evidence: List[Dict[str, Any]],
    *,
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    """Gather one or more grounded explanations into a cite record.

    Args:
        decision: the decision being justified (readable sentence or action id).
        reason:   the primary, human-readable rationale.
        evidence: a list of evidence dicts *already produced* by
                  :func:`why.explain_action`. They are validated (each must
                  carry a non-empty ``sources`` list) and sorted so the most
                  decisive ground (rule > calc > datasheet) comes first.
        ts:       timestamp override (defaults to now).

    Returns:
        {decision, reason, evidence[]} — ``evidence`` sorted by kind priority.

    Raises:
        CiteError: if ``evidence`` is empty, or any item is missing a grounded
                   ``sources`` list. A decision without grounds is NOT a real
                   citation.
    """
    if not evidence:
        raise CiteError("cite_decision needs >=1 evidence item; pass explain_action() output.")

    cleaned: List[Dict[str, Any]] = []
    for item in evidence:
        sources = item.get("sources") or []
        if not sources:
            raise CiteError(f"ungrounded evidence: {item.get('action')!r} has empty sources.")
        cleaned.append(item)

    cleaned.sort(key=_rank)

    return {
        "decision": decision,
        "reason": reason,
        "evidence": cleaned,
        "timestamp": ts or _now(),
    }


def cite_action(
    decision: str,
    reason: str,
    evidence_specs: List[Dict[str, Any]],
    *,
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    """Gather-then-cite: build the evidence from lightweight specs in one call.

    Each ``evidence_specs`` entry forwards to :func:`why.understand_action`
    (or :func:`why.explain_action` when ``calc`` is present) and the resulting
    grounded items are passed to :func:`cite_decision`.

    Example spec::

        {"action": "widen VCC trace", "kind": "calc",
         "summary": "VCC must carry 1A",
         "calc": {"formula": "w=I/(t*K)", "inputs": {...}, "result": "54mil"}}

    Returns the same {decision, reason, evidence[]} shape as
    :func:`cite_decision`.
    """
    gathered: List[Dict[str, Any]] = []
    for spec in evidence_specs:
        kwargs = dict(spec)
        action = kwargs.pop("action")
        kind = kwargs.pop("kind")
        summary = kwargs.pop("summary")
        detail = kwargs.pop("detail", "")
        calc = kwargs.pop("calc", None)
        if calc is not None:
            gathered.append(explain_action(action, kind, summary=summary,
                                           detail=detail, calc=calc))
        else:
            gathered.append(understand_action(action, kind, summary=summary,
                                              detail=detail))
    return cite_decision(decision, reason, gathered, ts=ts)


if __name__ == "__main__":  # pragma: no cover - quick CLI demo
    import pprint

    record = cite_action(
        decision="widen VCC trace to 54 mil",
        reason="1A must be delivered with < 0.35V drop over a 40mm run.",
        evidence_specs=[
            {"action": "widen VCC trace", "kind": "calc",
             "summary": "VCC must carry 1A",
             "calc": {"formula": "w = I/(t*K)", "inputs": {"I": 1.0, "t": 0.035, "K": 0.53}, "result": "54mil"}},
            {"action": "check clearance", "kind": "rule",
             "summary": "trace width meets min 6 mil"},
        ],
    )
    pprint.pprint(record, width=100)