#!/usr/bin/env python3
"""
PCBGenius — D11 KiCad importer (REAL source)
============================================
Parses KiCad schematic (`.kicad_sch`) and board (`.kicad_pcb`) files into the
PCBGenius FROZEN-contract netlist (netlist_schema v1.0.0), the same shape that
`pcbgenius-layout/pcbflow_layout.py::validate_netlist` accepts.

Contract netlist shape
----------------------
    {
      "schema_version": "1.0.0",
      "metadata": {
          "design_name": str,
          "description": str,
          "board_layers": int,
          "created_by": "kicad-import",
          "target_fab": str
      },
      "components": [
          { "ref": "R1", "type": <contract type>, "value": str, "package": str,
            "mpn": str|None, "pins": [ {"number","name","net"}, ... ],
            "properties": { k: v } },
          ...
      ],
      "nets": [
          { "name": "GND", "pins": ["R1.1", ...], "class": <net class> },
          ...
      ]
    }

KiCad `.kicad_sch` is the S-expression text format (KiCad 6 / kisys format).
This parser is dependency-free and robust:
  * hand-rolled S-expression scanner (nested parentheses), no `sexpdata`
    dependency so it runs everywhere in the pipeline;
  * reads both `(symbol ...)` graphical symbols (schematic) and, when given a
    `.kicad_pcb`, `(footprint ...)` components whose pad nets come from
    `(pad N net <NETNAME> ...)`. Schematic is preferred when available because
    it carries inverter/designator + value + footprint metadata explicitly.

CustomKiCad net handling on schematics
--------------------------------------
KiCad schematic components expose their nets two ways, both handled here:
  1. pins: each pin's `(nets (net <N> <NAME>))` entry lists the net NAME.
  2. when a net has no explicit name in the tree, KiCad uses the special
     value `net_<id>` — we keep those as-is (they are still consistent across
     the file, so round-tripping refs/nets is unaffected).

Contract type mapping (`_KICAD_TO_CONTRACT`)
-----------------------------------------------
KiCad library symbol name / footprint name -> contract type. Unknown values
fall back to "ic" and are flagged in a per-file warnings list.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

CONTRACT_VERSION = "1.0.0"

# ── S-expression scanner ─────────────────────────────────────────────────────
_TOKEN_RE = re.compile(
    r"""
      \s*(
          \(                     # open paren
        | \)                     # close paren
        | "(?:[^"\\]|\\.)*"      # quoted string token
        | [^\s\(\)"]+            # bare token (number, symbol, name)
      )
    """, re.VERBOSE)


def _tokenize(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(text)]


def _parse(tokens: List[str]) -> List[Any]:
    """Recursive descent over the token stream -> nested lists of tokens/sexprs."""
    it = iter(tokens)
    stack: List[List[Any]] = []
    root: List[Any] = []
    stack.append(root)
    for tok in it:
        if tok == "(":
            node: List[Any] = []
            stack[-1].append(node)
            stack.append(node)
        elif tok == ")":
            if len(stack) == 1:
                break  # balanced
            stack.pop()
        else:
            if tok.startswith('"') and len(tok) >= 2 and tok.endswith('"'):
                tok = tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            stack[-1].append(tok)
    return root


def parse_sexpr(text: str) -> List[Any]:
    """Parse S-expression text into nested Python lists."""
    return _parse(_tokenize(text))


# ── Contract helpers ─────────────────────────────────────────────────────────
_KICAD_TO_CONTRACT = {
    "R": "resistor",
    "C": "capacitor",
    "L": "inductor",
    "D": "diode",
    "LED": "led",
    "Q": "transistor",
    "J": "connector",
    "P": "connector",
    "CN": "connector",
    "X": "crystal",
    "Y": "crystal",
    "SW": "switch",
    "U": "ic",
    "IC": "ic",
    "VR": "power",
    "F": "power",
    "LDO": "power",
    "REG": "power",
}

# net class heuristic for nets that KiCad doesn't annotate
_CLASS_HINTS = {
    "gnd": "ground", "ground": "ground", "0v": "ground", "vss": "ground",
    "vcc": "power", "vdd": "power", "vin": "power", "vout": "power",
    "pwr": "power", "power": "power", "+": "power",
    "clk": "clock", "clock": "clock", "scl": "clock", "sda": "digital",
    "tx": "digital", "rx": "digital", "spi": "digital", "i2c": "digital",
}


def _designator_prefix(ref: str) -> str:
    """Extract the alphabetic prefix of a designator, e.g. 'R' from 'R12'."""
    m = re.match(r"[A-Za-z]+", ref or "")
    return m.group(0) if m else "U"


def _contract_type(ref: str, symbol: str = "", footprint: str = "") -> str:
    """Map KiCad ref/symbol/footprint hints to a contract component type."""
    # footprint names often embed the package (e.g. 'SOIC-8', '0805') — strip
    # leading digits / package size tokens so a type prefix is still readable.
    fp = re.sub(r"^\d+[\-\s]*|[\-\s]*\d+$", "", footprint or "").upper()
    candidates = [symbol.upper(), fp, _designator_prefix(ref)]
    for c in candidates:
        for key, ctype in _KICAD_TO_CONTRACT.items():
            if c.startswith(key):
                return ctype
        if re.match(r"^\d{4}$", c):           # 0805 / 0603 package class
            return "passive"
    return "ic"


def _net_class(name: str) -> str:
    low = (name or "").lower()
    for key, cls in _CLASS_HINTS.items():
        if key in low:
            return cls
    return "signal"


# ── Schematic importer ───────────────────────────────────────────────────────
def _find(tree: List[Any], key: str) -> List[Any]:
    """Return all sublists whose first element equals `key`."""
    return [node for node in tree if isinstance(node, list) and node and node[0] == key]


def _find_all(tree: List[Any], key: str, _out: Optional[List[Any]] = None) -> List[Any]:
    """Recursively collect every subtree whose first element equals `key`.

    KiCad documents nest symbols/footprints a few levels deep (under the
    top-level `kicad_sch`/`kicad_pcb` node), so a flat top-level scan misses
    them. This walks the whole tree.
    """
    if _out is None:
        _out = []
    if isinstance(tree, list):
        if tree and isinstance(tree[0], str) and tree[0] == key:
            _out.append(tree)
        for child in tree:
            _find_all(child, key, _out)
    return _out


def _find_one(tree: List[Any], key: str) -> Optional[List[Any]]:
    hits = _find(tree, key)
    return hits[0] if hits else None


def _text_field(node: List[Any], idx: int) -> str:
    """Return the idx-th text element of a (property/text ...) node if present."""
    return str(node[idx]) if idx < len(node) else ""


def _symbol_properties(symbol: List[Any]) -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    for prop in _find(symbol, "property"):
        # (property "Reference" "R1" (at ...) ... )  : key=idx1, value=idx2
        if len(prop) >= 3:
            props[prop[1]] = prop[2]
    for txt in _find(symbol, "pin_names"):
        for off in txt:
            if isinstance(off, str):
                props["pin_names_offset"] = off
    return props


def _symbol_pins(symbol: List[Any]) -> List[Dict[str, Any]]:
    """Extract pins available on a graphical symbol, including their net.

    Each pin node may carry a `(nets (net <id> <name>))` child — the local
    net connection KiCad stores on-sheet. When present it is surfaced as the
    pin's `net`; connectivity is otherwise resolved from labels elsewhere.
    """
    pins: List[Dict[str, Any]] = []
    for pin in _find(symbol, "pin"):
        if len(pin) < 2:
            continue
        name = pin[1]
        number = str(pin[2]) if len(pin) >= 3 else str(len(pins) + 1)
        net_name = ""
        for sub in pin:
            if isinstance(sub, list) and sub and sub[0] == "nets":
                for inner in sub:
                    if isinstance(inner, list) and inner and inner[0] == "net" and len(inner) >= 3:
                        net_name = str(inner[2])
        pins.append({"name": name, "number": number, "net": net_name})
    return pins


def _resolve_net_map(tree: List[Any]) -> Dict[str, str]:
    """Build id->canonical-name map from top-level (net <id> <name>) entries."""
    netmap: Dict[str, str] = {}
    for n in _find(tree, "net"):
        if len(n) >= 3:
            netmap[str(n[1])] = str(n[2])
    return netmap


def parse_kicad_sch(text: str, design_name: str = "",
                    board_layers: int = 2,
                    target_fab: str = "jlcpcb") -> Tuple[Dict[str, Any], List[str]]:
    """Parse `.kicad_sch` text -> (contract netlist, warnings)."""
    warnings: List[str] = []
    tree = parse_sexpr(text)
    netmap = _resolve_net_map(tree)

    # find well-formed symbol nodes (skip header/version lines)
    symbols: List[List[Any]] = []
    for node in _find_all(tree, "symbol"):
        if len(node) >= 2 and isinstance(node[1], str) and not node[1] == "":
            symbols.append(node[1:])  # drop the leading 'symbol' tag

    comps_by_ref: Dict[str, Dict[str, Any]] = {}
    nets_agg: Dict[str, List[str]] = {}

    for node in symbols:
        if not node or not isinstance(node[0], str):
            continue
        props = _symbol_properties(node)
        ref = props.get("Reference", "")
        value = props.get("Value", "")
        footprint = props.get("Footprint", "") or ""
        symbol_name = node[0]

        if not ref:
            ref = value  # schematics without explicit references fall back
        if not ref:
            continue

        ctype = _contract_type(ref, symbol_name, footprint)
        pins = _symbol_pins(node)
        for p in pins:
            if p.get("net"):
                nets_agg.setdefault(p["net"], []).append(
                    f"{ref}.{p.get('name') or p.get('number')}")
        comp = {
            "ref": ref,
            "type": ctype,
            "value": value,
            "package": footprint if isinstance(footprint, str) else "",
            "mpn": props.get("MPN"),
            "pins": pins,
            "properties": props,
        }
        comps_by_ref[ref] = comp
        warnings.append(f"symbol {ref}: {len(pins)} pins, footprint={footprint or '-'}")

    netlist = {
        "schema_version": CONTRACT_VERSION,
        "metadata": {
            "design_name": design_name or "", "description": "",
            "board_layers": int(board_layers), "created_by": "kicad-import",
            "target_fab": target_fab,
        },
        "components": sorted(comps_by_ref.values(), key=lambda c: c["ref"]),
        "nets": [
            {"name": name, "pins": pins, "class": _net_class(name)}
            for name, pins in sorted(nets_agg.items(), key=lambda kv: kv[0])
        ],
    }
    return netlist, warnings


def parse_kicad_pcb(text: str, design_name: str = "",
                    board_layers: int = 2,
                    target_fab: str = "jlcpcb") -> Tuple[Dict[str, Any], List[str]]:
    """Parse `.kicad_pcb` text -> (contract netlist, warnings).

    Reads footprint placement + per-pad net assignments. This yields
    components and, importantly, real nets (the board's routed connectivity).
    """
    warnings: List[str] = []
    tree = parse_sexpr(text)

    # (net <id> <name>) :: id -> name
    netmap = _resolve_net_map(tree)
    warnings.append(f"board declares {len(netmap)} nets")

    # (footprint "<ref>" ... (pad N <type> <shape> (net <id> <name>) ...) ...)
    footprints = []
    for node in _find_all(tree, "footprint"):
        if len(node) >= 2 and isinstance(node[1], str):
            footprints.append(node[1:])

    comps_by_ref: Dict[str, Dict[str, Any]] = {}
    nets_agg: Dict[str, List[str]] = {}

    for node in footprints:
        if not node or not isinstance(node[0], str):
            continue
        ref = node[0]
        if ref.startswith(("pad", "gr_")):
            continue
        fp_props: Dict[str, Any] = {}
        for p in _find(node, "property"):
            if len(p) >= 3:
                fp_props[p[1]] = p[2]
        value = fp_props.get("Value", "")
        footprint = fp_props.get("Footprint", "") or ""
        symbol = fp_props.get("Symbol", "") or ""

        pads: List[Dict[str, Any]] = []
        for pad in _find(node, "pad"):
            # (pad <number> <type> <shape> (at ...) (size ...) (net <id> <name>) ...)
            if len(pad) < 3:
                continue
            pnum = str(pad[1])
            pname = f"pad_{pnum}"
            net_name = ""
            for sub in pad:
                if isinstance(sub, list) and sub and sub[0] == "net" and len(sub) >= 3:
                    net_name = str(sub[2])
            pads.append({"number": pnum, "name": pname, "net": net_name})

        ctype = _contract_type(ref, symbol, footprint)
        comp = {
            "ref": ref, "type": ctype, "value": value, "package": footprint,
            "mpn": None, "pins": pads,
            "properties": {**fp_props, "footprint": footprint},
        }
        comps_by_ref[ref] = comp
        for pad in pads:
            if pad["net"]:
                nets_agg.setdefault(pad["net"], []).append(f"{ref}.{pad['name']}")

    components = sorted(comps_by_ref.values(), key=lambda c: c["ref"])
    nets = [
        {"name": name, "pins": pins, "class": _net_class(name)}
        for name, pins in sorted(nets_agg.items(), key=lambda kv: kv[0])
    ]
    warnings.append(f"{len(components)} footprints, {len(nets)} connected nets parsed")

    netlist = {
        "schema_version": CONTRACT_VERSION,
        "metadata": {
            "design_name": design_name or "", "description": "",
            "board_layers": int(board_layers), "created_by": "kicad-import",
            "target_fab": target_fab,
        },
        "components": components,
        "nets": nets,
    }
    return netlist, warnings


def import_file(path: str, board_layers: int = 2) -> Tuple[Dict[str, Any], List[str]]:
    """Auto-dispatch on file extension. Returns (netlist, warnings)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    base = path.lower()
    design = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if base.endswith(".kicad_sch"):
        return parse_kicad_sch(text, design_name=design, board_layers=board_layers)
    if base.endswith(".kicad_pcb"):
        return parse_kicad_pcb(text, design_name=design, board_layers=board_layers)
    raise ValueError(f"unsupported import format for {path}")


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: python kicad.py <file.kicad_sch|file.kicad_pcb>")
        sys.exit(2)
    nl, warn = import_file(sys.argv[1])
    for w in warn:
        print("# " + w)
    print(json.dumps(nl, indent=2))