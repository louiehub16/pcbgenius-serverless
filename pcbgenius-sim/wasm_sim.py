"""
PCBGenius - D2 Real-time WASM SPICE sim (#6) | wasm_sim.py

Bridge between the PCBGENIUS backend/UI and an in-browser Ngspice compiled to
WebAssembly.

Architecture:

    UI (SimPanel.tsx) ──post──> backend run_simulation ──> wasm_sim.run_simulation()
                                                              │
                                                              ▼
                                                   Ngspice-WASM / spice.js
                                                   (wasm module, runs locally
                                                    in the browser, no server)

The actual WASM engine ships as a separate JS/TS artifact (spice.js +
ngspice.wasm). Those files are loaded in SimPanel.tsx via import; this python
module is the *server-side* orchestration shim. It builds the deck with
netlist_to_spice.netlist_to_deck(), hands it to the WASM engine, and normalises
the raw XML/raw-file output back into the frozen contract shape:

    { converged, measurements: {net: {voltage,current,ripple}},
      waveforms_ref, deck }

To keep this wave testable in pure Python (no WASM, no network), every call
site that would touch the real WASM engine is isolated behind a marker
function `_wasm_engine_*`. The unit tests only exercise deck building and
output post-processing, never the marked load sites.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from netlist_to_spice import netlist_to_deck, node_name, auto_stimulus

# ---------------------------------------------------------------------------
# [WASM LOAD SITE #1] Ngspice-WASM engine handle.
#
# In the real deployment this module is replaced by the JS glue that does:
#
#   const { loadNgspice, run } = await import("/spice.js");       // loads ngspice.wasm
#   const ngspice = await loadNgspice();                          // [WASM LOAD SITE #1]
#   const rawOutput = await ngspice.run(netlistDeck, analysis);   // [WASM EXEC SITE]
#
# The python mirror keeps a lazy reference; see `_get_engine` below.
# ---------------------------------------------------------------------------
_ENGINE: Any = None


def _get_engine() -> Any:
    """[WASM LOAD SITE #1] Return the loaded Ngspice-WASM engine handle.

    Raises RuntimeError until the real WASM glue is wired. This is the single
    choke-point that a future deployment swaps for the actual `loadNgspice()`.
    """
    global _ENGINE
    if _ENGINE is None:
        # [WASM LOAD SITE #1] -- real deployment calls loadNgspice() here.
        raise RuntimeError(
            "Ngspice-WASM engine not loaded. Wire SimPanel's onSimulate to the "
            "spice.js loader (loadNgspice) in the real deployment."
        )
    return _ENGINE


# ---------------------------------------------------------------------------
# raw output parsers (pure python, testable)
# ---------------------------------------------------------------------------

_RAW_LINE_SPLIT = re.compile(r"[\s,;]+")
_MEAS_RE = re.compile(
    r"(?im)^([a-z0-9_]+)\s*(?:#branch)?\s*=\s*([0-9.eE+-]+)"
)


def parse_raw_simulation(raw_output: str) -> Dict[str, Any]:
    """Best-effort parse of a raw Ngspice printed/raw output into a dict.

    Accepts the simple "{node} = {value}" measurement block that ngspice emits
    for `.print` / `.op`. Rows of tabular data (tran) are captured as lists
    keyed by the first header token where possible.

    Returns {"measurements": {...}, "waveforms": {...}}.
    """
    measurements: Dict[str, Any] = {}
    waveforms: Dict[str, List[float]] = {}

    for m in _MEAS_RE.finditer(raw_output):
        key, val = m.group(1), m.group(2)
        try:
            measurements[key] = float(val)
        except ValueError:
            continue

    # heuristic numeric-table capture (very tolerant)
    for line in raw_output.splitlines():
        if not line.strip():
            continue
        toks = _RAW_LINE_SPLIT.split(line.strip())
        # a header-looking row: tokens that are not numbers
        if not any(_is_num(t) for t in toks):
            continue
        nums = [t for t in toks if _is_num(t)]
        if not nums or len(nums) < 2:
            continue
        # first numeric token is the independent variable (time/freq)
        indep = float(nums[0])
        try:
            waveforms.setdefault(line.split()[0], []).append(indep)
        except (IndexError, ValueError):
            pass

    return {"measurements": measurements, "waveforms": waveforms}


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _ripple_estimate(samples: List[float]) -> float:
    """Peak-to-peak ripple estimate for a sampled waveform (pure python)."""
    if not samples:
        return 0.0
    lo, hi = min(samples), max(samples)
    return round(hi - lo, 6)


# ---------------------------------------------------------------------------
# public orchestration (the contract-facing API)
# ---------------------------------------------------------------------------

def run_simulation(netlist: Dict[str, Any],
                   sim_type: str = "op",
                   stimulus: Optional[Dict[str, Any]] = None,
                   test_points: Optional[List[str]] = None,
                   analysis_opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run a real-time simulation and return normalised contract output.

    Behavior when WASM is unavailable:
      - builds the deck (always)
      - attempts engine execution via `_get_engine()`; if that raises
        RuntimeError (unloaded), returns a structured "pending" result so the
        UI can show the deck + a clear wasm-not-loaded message instead of
        crashing. This keeps the wave shippable before the JS glue lands.

    Returns:
      { converged: bool, measurements: {net: {voltage,current,ripple}},
        waveforms_ref: str|null, deck: str }
    """
    deck = netlist_to_deck(netlist, sim_type, stimulus, test_points, analysis_opts)

    try:
        engine = _get_engine()
        return _execute_on_engine(engine, deck, netlist, sim_type, test_points)
    except RuntimeError as exc:
        # [WASM EXEC SITE] not reached while engine is unloaded.
        return {
            "converged": False,
            "measurements": {},
            "waveforms_ref": None,
            "error": str(exc),
            "deck": deck,
        }


def _execute_on_engine(engine: Any, deck: str, netlist: Dict[str, Any],
                       sim_type: str, test_points: List[str]) -> Dict[str, Any]:
    """[WASM EXEC SITE] Drive the loaded engine with the deck, then normalise.

    In the real JS bridge this is the `await ngspice.run(deck, ...)` call.
    """
    # [WASM EXEC SITE #2]
    raw_output = engine.run(deck)  # type: ignore[attr-defined]

    parsed = parse_raw_simulation(raw_output)
    measurements: Dict[str, Any] = {}
    for tp in test_points:
        nm = node_name(tp)
        v = parsed["measurements"].get(nm)
        samples = parsed["waveforms"].get(nm, [])
        measurements[tp] = {
            "voltage": v if v is not None else 0.0,
            "current": 0.0,
            "ripple": _ripple_estimate(samples),
        }

    waveforms_ref = f"waveforms://{sim_type}/{int(time.time())}"
    return {
        "converged": True,
        "measurements": measurements,
        "waveforms_ref": waveforms_ref,
        "deck": deck,
    }


def prepare_run_spec(netlist: Dict[str, Any],
                     sim_type: str = "tran",
                     test_points: Optional[List[str]] = None) -> Dict[str, Any]:
    """Helper for the UI bridge: assemble what the JS glue needs to load+run.

    Returns {deck, stimulus_auto, sim_type, test_points} so SimPanel can pass
    exactly this to the backend / WASM loader.
    """
    return {
        "deck": netlist_to_deck(netlist, sim_type=sim_type, test_points=test_points),
        "stimulus_auto": auto_stimulus(netlist),
        "sim_type": sim_type,
        "test_points": test_points or [],
    }


if __name__ == "__main__":
    import sys
    from netlist_to_spice import netlist_to_deck as _d

    # build a demo deck and print the pending result
    demo = {
        "schema_version": "1.0.0",
        "metadata": {"design_name": "demo_rc"},
        "components": [
            {"ref": "R1", "type": "resistor", "value": "1k", "package": "0805",
             "mpn": None, "properties": {},
             "pins": [{"number": "1", "name": "1", "net": "VIN"},
                      {"number": "2", "name": "2", "net": "OUT"}]},
        ],
        "nets": [
            {"name": "VIN", "pins": ["R1.1"], "class": "power"},
            {"name": "OUT", "pins": ["R1.2"], "class": "signal"},
        ],
    }
    res = run_simulation(demo, sim_type="tran", stimulus={"VIN": 5}, test_points=["OUT"])
    print(json.dumps(res, indent=2))
