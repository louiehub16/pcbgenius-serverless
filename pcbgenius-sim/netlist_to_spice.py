"""
PCBGenius - D2 Real-time WASM SPICE sim (#6) | netlist_to_spice.py

Convert a PCBGenius contract netlist (schema v1.0.0) into an Ngspice .cir deck.

Input netlist shape (the FROZEN INTERFACE CONTRACT, mirror of
pcbgenius-frontend/src/contractTypes.ts):

    {
      "schema_version": "1.0.0",
      "metadata": { "design_name": "...", ... },
      "components": [
        { "ref": "R1", "type": "resistor", "value": "1k",
          "package": "0805", "mpn": null,
          "pins": [ {"number":"1","name":"1","net":"VOUT"},
                    {"number":"2","name":"2","net":"FB"} ],
          "properties": {} },
        ... capacitor / inductor / diode / led / ic / connector ...
      ],
      "nets": [ {"name":"GND","pins":["R1.2",...],"class":"ground"}, ... ]
    }

This module is 100% pure Python (no Ngspice, no WASM). It only builds the text
deck. Transient / operating-point / sweep / probe control is attached here too;
*executing* the deck happens in wasm_sim.py, which bridges to Ngspice-WASM.

Run tests:  python -m pytest tests/test_sim.py
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

CONTRACT_VERSION = "1.0.0"

# ── SPICE value cleaning ──────────────────────────────────────────────────
# Netlist values are human strings like "1k", "330", "10uF", "22uH", "4.7k",
# "330ohm", "100nF". Ngspice already understands SI suffixes (k/MEG/u/n/p), so
# we keep the leading number + prefix and just strip the physical-unit word.
_UNIT_RE = re.compile(r"(?i)(ohm|ohms|farad|f|henry|henries|h)$")

# Net names that physically map to the SPICE ground node (0).
_GROUND_ALIASES = {"GND", "0", "GND0", "GROUND", "AGND"}


def spice_value(value: Any) -> str:
    """Normalise a component value string to Ngspice syntax.

    >>> spice_value("1k")
    '1k'
    >>> spice_value("330")
    '330'
    >>> spice_value("330ohm")
    '330'
    >>> spice_value("10uF")
    '10u'
    >>> spice_value("22uH")
    '22u'
    """
    s = str(value).strip()
    if not s:
        return "1"
    cleaned = _UNIT_RE.sub("", s).strip()
    return cleaned if cleaned else s


def node_name(net: str) -> str:
    """Map a contract net name to a legal SPICE node name.

    The ground net becomes the reserved node 0; every other net is sanitised
    to [A-Za-z0-9_]. Dots/parens/hyphens are illegal inside node names.
    """
    if net in _GROUND_ALIASES:
        return "0"
    n = re.sub(r"[^A-Za-z0-9_]", "_", str(net))
    # Ngspice forbids node names starting with a digit too.
    if n and n[0].isdigit():
        n = "N" + n
    return n


# ── per-component element emission ────────────────────────────────────────

def _pin_nets(comp: Dict[str, Any]) -> List[str]:
    pins = comp.get("pins") or []
    return [p.get("net", "0") for p in pins]


def _first_net(comp: Dict[str, Any], fallback: str = "0") -> str:
    nets = _pin_nets(comp)
    return nets[0] if nets else fallback


def _second_net(comp: Dict[str, Any], fallback: str = "0") -> str:
    nets = _pin_nets(comp)
    return nets[1] if len(nets) > 1 else fallback


def _diode_terminals(comp: Dict[str, Any]) -> tuple[str, str]:
    """Return (anode_net, cathode_net) for a diode / LED by pin name.

    Falls back to first-pin=anode, second-pin=cathode (contract convention
    for diodes is A/K)."""
    pins = comp.get("pins") or []
    anode = cathode = None
    for p in pins:
        name = str(p.get("name", "")).strip().upper()
        net = p.get("net", "0")
        if name in {"A", "ANODE", "P", "1", "+"}:
            anode = net
        elif name in {"K", "CATHODE", "N", "-"}:
            cathode = net
    if comp.get("type") == "led":
        # LEDs: pin 1 is anode, pin 2 is cathode in the generator contract.
        if anode is None:
            anode = _first_net(comp)
        if cathode is None:
            cathode = _second_net(comp)
        return anode, cathode
    if anode is None:
        anode = _first_net(comp)
    if cathode is None:
        cathode = _second_net(comp)
    return anode, cathode


def component_line(comp: Dict[str, Any]) -> Optional[str]:
    """Emit the Ngspice element line for one component.

    Passive two-terminal parts (R/C/L) map 1:1. Diodes + LEDs become SPICE
    diode elements with a named model. ICs / connectors / transistors / power
    / crystal / switch are NOT directly simulable -> returned as None (caller
    decides how to stub or inject them)."""
    ctype = comp.get("type")
    ref = comp.get("ref", "X")
    nets = _pin_nets(comp)

    if ctype == "resistor":
        return f"{ref} {node_name(nets[0])} {node_name(nets[1])} {spice_value(comp.get('value'))}"
    if ctype == "capacitor":
        return f"{ref} {node_name(nets[0])} {node_name(nets[1])} {spice_value(comp.get('value'))}"
    if ctype == "inductor":
        return f"{ref} {node_name(nets[0])} {node_name(nets[1])} {spice_value(comp.get('value'))}"
    if ctype in ("diode", "led"):
        a, k = _diode_terminals(comp)
        model = "ledmodel" if ctype == "led" else "dmodel"
        return f"{ref} {node_name(a)} {node_name(k)} {model}"
    # IC / connector / transistor / power / crystal / switch: skip element,
    # they are usually driven by external stimulus (see wasm_sim.py).
    return None


def component_lines(components: Iterable[Dict[str, Any]]) -> List[str]:
    return [ln for ln in (component_line(c) for c in components) if ln]


# ── stimulus (voltage sources) ────────────────────────────────────────────

def inject_sources(stimulus: Optional[Dict[str, Any]]) -> List[str]:
    """Emit `V.. net 0 <volt>` lines for each {net: voltage} stimulus entry.

    Stimulus is keyed by contract net name; the ground net is skipped. Each
    source is named uniquely (VSnn) so multiple nets can be driven."""
    out: List[str] = []
    if not stimulus:
        return out
    for i, (net, volts) in enumerate(stimulus.items()):
        if net in _GROUND_ALIASES:
            continue
        v = spice_value(volts)
        out.append(f"VS{i} {node_name(net)} 0 {v}")
    return out


_INPUT_NET_RE = re.compile(r"(?i)^(v?(in|cc|dd|bus|sup|pwr)|[0-9.]+v|v[0-9.]+)$")


def auto_stimulus(netlist: Dict[str, Any], default_volts: float = 5.0) -> Dict[str, Any]:
    """Heuristic input detection for the deck.

    Returns {net: default_volts} for the first net that looks like a power
    input (VIN/VCC/VDD/VBUS/VSUP/PWR or a voltage-named net) and is NOT a
    buck/boost switching node (not also touched by an inductor or diode).
    Used so a passive RC / divider netlist is runnable without hand-writing
    stimulus. Returns {} for switching regulators (caller should supply one).
    """
    comps = netlist.get("components", [])
    switching_nets = set()
    for c in comps:
        if c.get("type") in ("inductor", "diode"):
            for p in c.get("pins", []):
                switching_nets.add(p.get("net", ""))
        # ICs are the switching element in a buck; flag their power-adjacent nets
        if c.get("type") == "ic":
            for p in c.get("pins", []):
                if str(p.get("name", "")).upper() in {"OUT", "SW", "LX", "PH"}:
                    switching_nets.add(p.get("net", ""))

    nets = netlist.get("nets", [])
    for n in nets:
        name = n.get("name", "")
        cls = n.get("class", "")
        if cls != "power" or name in _GROUND_ALIASES:
            continue
        if name in switching_nets:
            continue
        if _INPUT_NET_RE.match(name):
            # try to read a voltage out of the net name (e.g. "24V")
            m = re.match(r"(?i)^([0-9.]+)\s*v", name)
            if m:
                return {name: float(m.group(1))}
            return {name: default_volts}
    return {}


# ── probing / analysis ────────────────────────────────────────────────────

def _analysis_lines(sim_type: str, test_points: List[str],
                    analysis_opts: Optional[Dict[str, Any]]) -> List[str]:
    """Build the Ngspice control block for the requested simulation type."""
    opts = analysis_opts or {}
    probes = list(test_points) if test_points else []
    for p in list(probes):
        if p in _GROUND_ALIASES:
            probes.remove(p)

    lines: List[str] = []
    if sim_type == "op":
        lines.append(".op")
        for p in probes:
            nm = node_name(p)
            lines.append(f".print op v({nm})")
    elif sim_type == "dc":
        # Sweep the first source VS0 (see inject_sources); if none, just .op.
        start = opts.get("start", 0)
        stop = opts.get("stop", 12)
        step = opts.get("step", 0.5)
        lines.append(f".dc VS0 {spice_value(start)} {spice_value(stop)} {spice_value(step)}")
        for p in probes:
            nm = node_name(p)
            lines.append(f".print dc v({nm})")
    elif sim_type == "ac":
        start = opts.get("start", 1)
        stop = opts.get("stop", 1e6)
        points = opts.get("dec", 20)
        lines.append(f".ac dec {points} {spice_value(start)} {spice_value(stop)}")
        for p in probes:
            nm = node_name(p)
            lines.append(f".print ac v({nm})")
    elif sim_type == "tran":
        tstep = opts.get("tstep", 1e-6)
        tstop = opts.get("tstop", 1e-3)
        lines.append(f".tran {spice_value(tstep)} {spice_value(tstop)}")
        for p in probes:
            nm = node_name(p)
            lines.append(f".print tran v({nm})")
    else:
        lines.append(".op")
    return lines


# ── deck builder ──────────────────────────────────────────────────────────

# Diode models: built-in names referenced by component_line.
MODELS = [
    ".model dmodel D (IS=1e-14 N=1.0)",
    ".model ledmodel D (IS=1e-14 N=2.0)",
]


def netlist_to_deck(netlist: Dict[str, Any],
                    sim_type: str = "op",
                    stimulus: Optional[Dict[str, Any]] = None,
                    test_points: Optional[List[str]] = None,
                    analysis_opts: Optional[Dict[str, Any]] = None,
                    auto_source: bool = True) -> str:
    """Build a complete Ngspice .cir deck from a contract netlist.

    - elements:  every passive R/C/L/D/LED
    - sources:   explicit `stimulus` is honoured; otherwise `auto_source`
                 heuristically picks an input power net (passive circuits).
    - analysis:  op / dc / ac / tran block with per-net probes.
    """
    meta = netlist.get("metadata", {}) or {}
    design = meta.get("design_name", "pcbgenius_design")

    lines: List[str] = []
    lines.append(f"* PCBGenius deck [design={design}] (schema {netlist.get('schema_version', CONTRACT_VERSION)})")
    lines.append(f".title {design}")

    comps = netlist.get("components", [])
    lines.extend(component_lines(comps))

    if stimulus is None and auto_source:
        stimulus = auto_stimulus(netlist)
    if stimulus:
        lines.append("* ---- stimulus (voltage sources) ----")
        lines.extend(inject_sources(stimulus))

    lines.append("* ---- diode models ----")
    lines.extend(MODELS)

    lines.append("* ---- analysis ----")
    lines.extend(_analysis_lines(sim_type, test_points or [], analysis_opts))
    lines.append(".end")

    return "\n".join(lines) + "\n"


# ── helpers for the bridge / tests ────────────────────────────────────────

def deck_elements(deck: str) -> List[str]:
    """Return the nettist element lines (R/C/L/D...) from a built deck."""
    out: List[str] = []
    for ln in deck.splitlines():
        s = ln.strip()
        if not s or s.startswith(("*", ".", "$")):
            continue
        if re.match(r"^[RCLD]", s):
            out.append(s)
    return out


def node_map(netlist: Dict[str, Any]) -> Dict[str, str]:
    """Shipping net name -> SPICE node name mapping (for debugging / UI)."""
    nets = netlist.get("nets", [])
    out: Dict[str, str] = {}
    for n in nets:
        out[n.get("name", "")] = node_name(n.get("name", "N_NODE"))
    return out


if __name__ == "__main__":
    import json
    import sys

    src = sys.argv[1] if len(sys.argv) > 1 else None
    if src:
        with open(src, "r", encoding="utf-8") as fh:
            nl = json.load(fh)
    else:
        nl = {
            "schema_version": "1.0.0",
            "metadata": {"design_name": "demo_rc"},
            "components": [
                {"ref": "R1", "type": "resistor", "value": "1k", "package": "0805",
                 "mpn": None, "properties": {},
                 "pins": [{"number": "1", "name": "1", "net": "VIN"},
                          {"number": "2", "name": "2", "net": "OUT"}]},
                {"ref": "C1", "type": "capacitor", "value": "100nF", "package": "0603",
                 "mpn": None, "properties": {},
                 "pins": [{"number": "1", "name": "1", "net": "OUT"},
                          {"number": "2", "name": "2", "net": "GND"}]},
            ],
            "nets": [
                {"name": "VIN", "pins": ["R1.1"], "class": "power"},
                {"name": "OUT", "pins": ["R1.2", "C1.1"], "class": "signal"},
                {"name": "GND", "pins": ["C1.2"], "class": "ground"},
            ],
        }
    print(netlist_to_deck(nl, sim_type="tran",
                          stimulus={"VIN": 5},
                          test_points=["OUT"]))
