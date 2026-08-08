#!/usr/bin/env python3
"""PCBGenius — D8 AI TESTING & DEBUGGING ASSISTANT (feature #12): triage.

Feeds raw simulator / EDA failure output (Ngspice netlist errors, KiCad DRC
violations, DC operating-point failures) to an LLM for root-cause analysis, with
a fully DETERMINISTIC FALLBACK so the assistant never depends on the network.

Design principles:
  * OPT-OUT MODEL: `triage` tries an LLM only if the caller passes a
    `model` + `api_key` AND `use_model=True`. Deterministic fallback runs
    otherwise — pure stdlib, offline, unit-testable.
  * The fallback is a regex/pattern classifier that maps the failing tool's
    text onto one of a fixed set of root-cause subsystems. Every call is
    recorded into a list passed out by the caller (`triaged` history).
  * Never raises. A mess of a log still yields a root cause (`unknown`) plus
    the raw excerpt so a human can eyeball it.

Output shape (single source of truth):

    {
      "tool": str,                 # 'ngspice' | 'kicad' | 'other'
      "root_cause": str,           # one of the subsystem keys in ROOT_CAUSES
      "subsystem": str,            # human label for that subsystem
      "confidence": float,         # 0.0..1.0 (keyword-hit confidence of fallback)
      "explanation": str,          # sentence(s) linking the observed log to the cause
      "matched": [str],            # the exact log lines that fired (fallback)
      "used_llm": bool,            # whether the answer came from the model call
    }
"""

from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ─────────────────────────────────────────────────────────────────────────────
# Root-cause subsystem catalogue  (the fixed set the fallback maps onto)
# ─────────────────────────────────────────────────────────────────────────────
ROOT_CAUSES: Dict[str, Dict[str, Any]] = {
    "missing_net": {
        "subsystem": "Connectivity / missing net",
        "keywords": ("no such net", "unknown net", "net not found", "missing net",
                     "floating net", "unconnected", "no connection for"),
        "expl": "The simulator cannot resolve a referenced net. Typically a "
                "net name typo, a pin named differently than its net, or a "
                "net that was dropped during netlist export.",
    },
    "short_circuit": {
        "subsystem": "Power short / short circuit",
        "keywords": ("short", "short to ground", "closed circuit", "contradictory",
                     "overlap", "unrouted_short", "pcbnew.*shorts"),
        "expl": "Two independent nets are connected together, usually two power "
                "rails or a rail shorted to ground, or copper/DRC overlap.",
    },
    "missing_component": {
        "subsystem": "Missing component / device",
        "keywords": ("no such model", "unknown device", "component not found",
                     "library.*error", "missing footprint", "not in library",
                     "no footprint"),
        "expl": "A referenced component/device has no model or footprint in the "
                "active library — symbol/footprint bindings are broken or the "
                "part was never placed.",
    },
    "variable_mismatch": {
        "subsystem": "Parameter / value mismatch",
        "keywords": ("no convergence", "singular", "value out of range",
                     "parameter.*error", "invalid value", "impossible value",
                     "zero divisor", "divide by zero"),
        "expl": "An electrical parameter is impossible or non-convergent — a "
                "9V rail is asked for from a 3.3V supply, a resistor is 0 ohm, "
                "or a device operating point can't be found.",
    },
    "metadata_issue": {
        "subsystem": "Metadata / schema issue",
        "keywords": ("schema", "missing field", "not an object", "expected",
                     "validation failed"),
        "expl": "The netlist or design JSON does not satisfy the expected "
                "schema — a required field is missing or mistyped.",
    },
}

# order matters: more specific classifiers try before generic 'unknown'
_DEFAULTS = ("missing_net", "short_circuit", "missing_component",
             "variable_mismatch", "metadata_issue")


def detect_tool(log: str) -> str:
    """Classify which sim/EDA tool produced the log."""
    low = log.lower()
    if "ngspice" in low or "spice" in low or "no such net" in low or "dc operating point" in low:
        return "ngspice"
    if "kicad" in low or "drc" in low or "pcbnew" in low or "footprint" in low:
        return "kicad"
    return "other"


def _fallback_triage(log: str, tool: str) -> Tuple[str, float, List[str]]:
    """Deterministic classifier. Returns (cause, confidence, matched_lines)."""
    low = log.lower()
    matched: List[str] = []
    best_cause: Optional[str] = None
    best_hits = 0

    # lowest-confidence fire first so higher-hit causes win ties
    for cause in _DEFAULTS:
        info = ROOT_CAUSES[cause]
        hits = [line for line in log.splitlines()
                if any(kw in line.lower() for kw in info["keywords"])
                or any(kw in low for kw in info["keywords"]
                       if len(line.strip()) == 0)]  # no-op safeguard
        # simpler per-keyword scoring across whole text, counts each keyword once
        n = sum(1 for kw in info["keywords"] if kw in low)
        if n > best_hits:
            best_hits = n
            best_cause = cause
            # collect the raw lines that contain any keyword
            matched = [ln for ln in log.splitlines()
                       if any(kw in ln.lower() for kw in info["keywords"])]

    if best_cause is None:
        return "unknown", 0.0, []
    # confidence: modest base + a bump for multiple keyword hits, capped at 0.95
    conf = min(0.95, 0.55 + 0.15 * (best_hits - 1))
    return best_cause, round(conf, 3), matched


def _call_llm(log: str, tool: str, model: str, api_key: str) -> Optional[str]:
    """One OpenRouter call; returns a raw root-cause label or None on failure."""
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": (
                "You are a PCB debug assistant. Given the output from a "
                f"{tool} simulation/EDA tool below, reply with EXACTLY ONE "
                f"short root-cause label from "
                f"{{{', '.join(_DEFAULTS) + ', unknown'}}} followed by a short "
                "explanation on the same line. Output:\n"
                f"{log[:3000]}"
            ),
        }],
        "max_tokens": 120,
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/126.0.0.0 Safari/537.36")},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            content = json.loads(r.read().decode())["choices"][0]["message"]["content"]
        # normalize to a known label if the model complied
        low = content.lower()
        for c in _DEFAULTS:
            if c.replace("_", " ") in low or c in low:
                return c
        return "unknown"
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
            IndexError, json.JSONDecodeError, Exception) as e:  # noqa: B014
        return None  # caller falls back deterministically


def triage(log: str, *,
           tool: Optional[str] = None,
           model: Optional[str] = None,
           api_key: Optional[str] = None,
           use_model: bool = False,
           history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Analyze one simulator/EDA failure log and return its root cause.

    Parameters
    ----------
    log        : str   raw output (Ngspice error, KiCad DRC report, ...). May be empty.
    tool       : str   override tool detection ('ngspice'|'kicad'|'other').
    model      : str   OpenRouter model name (only used when use_model=True).
    api_key    : str   OpenRouter key (only used when use_model=True).
    use_model  : bool  when True, attempt an LLM call and fall back on failure.
    history    : list  optional caller-owned list; each triage call is appended
                       so the assistant keeps an auditable record.

    Returns the output dict described in the module docstring.
    """
    log = log or ""
    tool = detect_tool(log) if not tool else tool

    used_llm = False
    cause, conf, matched = _fallback_triage(log, tool)
    explanation = ROOT_CAUSES.get(cause, {}).get("expl", "") if cause != "unknown" \
        else "No known pattern matched; review the raw log excerpt manually."

    if use_model and model and api_key:
        llm_cause = _call_llm(log, tool, model, api_key)
        if llm_cause is not None:
            used_llm = True
            cause = llm_cause
            conf = 0.9 if cause != "unknown" else 0.3
            explanation = ROOT_CAUSES.get(cause, {}).get("expl",
                "Root cause identified by the model reviewer.") if cause != "unknown" \
                else "Model could not classify; treat as needs-human-review."

    result = {
        "tool": tool,
        "root_cause": cause,
        "subsystem": ROOT_CAUSES.get(cause, {}).get("subsystem", "Unknown"),
        "confidence": conf,
        "explanation": explanation,
        "matched": matched,
        "used_llm": used_llm,
    }
    if isinstance(history, list):
        history.append(result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="PCBGenius failure triage")
    ap.add_argument("--log", default="", help="failure log text")
    ap.add_argument("--tool", choices=["ngspice", "kicad", "other"], default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--use-model", action="store_true")
    a = ap.parse_args()

    result = triage(a.log, tool=a.tool, model=a.model, api_key=a.api_key,
                    use_model=a.use_model)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()