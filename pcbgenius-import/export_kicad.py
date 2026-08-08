#!/usr/bin/env python3
"""
PCBGenius — D11 KiCad schematic exporter (REAL source)
=======================================================
Writes a PCBGenius FROZEN-contract netlist back out to a KiCad `.kicad_sch`
schematic document (kicad_pcb / S-expression format, KiCad 6+).

This is the round-trip half of the EDA import pipeline:
    .kicad_sch ─► kicad.parse_kicad_sch ─► contract netlist ─► export_kicad ─► .kicad_sch

The exporter produces a valid, self-consistent schematic:
  * one `(symbol "Ref" ... (property "Reference" ...) (property "Value" ...)
     (property "Footprint" ...) ...)` per contract component, with default
    2-pin/4-pin pad layouts derived from the contract pin list;
  * one top-level `(net <id> <name>)` entry per contract net (so KiCad's net
    resolver sees a consistent id->name map);
  * each symbol pin carries `(nets (net <id> <NETNAME>))` linking it to the
    net id, mirroring how KiCad stores connectivity on-sheet.

Determinism / correctness guarantees
------------------------------------
  * `refs` and `nets` are taken straight from the input contract, so a
    round-trip (import ─► export) preserves every reference designator and
    every net name exactly — that is what `test_import.py` asserts.
  * output is byte-deterministic for a given netlist (components/nets are
    emitted in sorted order regardless of input dict order).
  * pure stdlib: no KiCad, no `sexpdata`, no network.

No external tooling is invoked by this module.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

CONTRACT_VERSION = "1.0.0"

_ESCAPE = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t"}


def _q(s: Any) -> str:
    """S-expression quoted string with escaping."""
    s = str(s if s is not None else "")
    return '"' + "".join(_ESCAPE.get(ch, ch) for ch in s) + '"'


def _pin_layout(names: List[str]) -> List[Tuple[float, float]]:
    """Deterministic default pad positions for a component's pins (mm grid)."""
    if len(names) <= 2:
        return [(-2.54, 0.0), (2.54, 0.0)][:len(names)]
    left = [(-2.54, 1.27 - 2.54 * i) for i in range((len(names) + 1) // 2)]
    right = [(2.54, 1.27 - 2.54 * i) for i in range(len(names) // 2)]
    pts: List[Tuple[float, float]] = []
    for i in range(len(names)):
        pts.append(left.pop(0) if i % 2 == 0 else right.pop(0))
    return pts


def _pin_net(pins: List[Dict[str, Any]], pname: str) -> str:
    for p in pins:
        if (p.get("name") or "") == pname:
            return p.get("net") or ""
    return ""


def _render_symbol(sym: List[Any], indent: int = 1) -> str:
    """Render a fully-stringified symbol node with consistent indentation."""
    pad = "  " * indent
    head = f"{pad}({sym[0]} {_q(sym[1])} {sym[2]}"
    body = [head]
    for item in sym[3:]:
        body.append(pad + "  " + item)
    body.append(pad + ")")  # close symbol; caller adds final ")"
    inner = "\n".join(body)
    return inner + "\n" + pad + ")"


def _symbol_node(comp: Dict[str, Any], net_ids: Dict[str, str]) -> str:
    """Render a single contract component as a full (symbol ...) node string."""
    ref = comp.get("ref") or ""
    value = comp.get("value") or ""
    footprint = comp.get("package") or ""
    ctype = comp.get("type") or "ic"
    props = comp.get("properties") or {}
    mpn = comp.get("mpn") or props.get("MPN")

    pins = comp.get("pins") or []
    pin_names = [p.get("name") or f"pin_{i + 1}" for i, p in enumerate(pins)]
    positions = _pin_layout(pin_names)

    inner: List[str] = []
    inner.append('  (unit "0")')
    inner.append(f'  (property "Reference" {_q(ref)})')
    inner.append(f'  (property "Value" {_q(value)})')
    inner.append(f'  (property "Footprint" {_q(footprint)})')
    if mpn:
        inner.append(f'  (property "MPN" {_q(mpn)})')
    if ctype:
        inner.append(f'  (property "Type" {_q(ctype)})')

    for i, pname in enumerate(pin_names):
        x, y = positions[i] if i < len(positions) else (0.0, 0.0)
        net_for_pin = _pin_net(pins, pname)
        nid = net_ids.get(net_for_pin, "0")
        inner.append(
            f'  (pin {_q(pname)} {_q(str(i + 1))} "passive" '
            f'(at {x:g} {y:g} 0) (length 2.54) '
            f'(nets (net {nid} {_q(net_for_pin or "NC")})))'
        )

    return "(symbol " + _q(ref) + ' "0"\n' + "\n".join(inner) + "\n)"


def build_kicad_sch(netlist: Dict[str, Any]) -> str:
    """Render a contract netlist as `.kicad_sch` text.

    Pure function of `netlist`; deterministic and refs/nets-preserving.
    """
    meta = netlist.get("metadata", {}) or {}
    comps = netlist.get("components", []) or []
    nets = netlist.get("nets", []) or []
    design = (meta.get("design_name") or "design").replace("-", "_")[:20]

    # stable, sorted id -> name map
    net_ids: Dict[str, str] = {}
    for idx, n in enumerate(sorted(nets, key=lambda x: x.get("name") or "")):
        name = str(n.get("name") or f"net_{idx + 1}")
        net_ids[name] = str(idx + 1)

    lines: List[str] = []
    lines.append('(kicad_sch (version 20230121) (generator "pcbgenius-import")')
    lines.append(f'  (generator_version "1.0.0")')
    lines.append(f'  (uuid 00000000-0000-0000-0000-{design:0<12})')
    lines.append('  (paper "A4")')
    lines.append("")

    for name, nid in net_ids.items():
        lines.append(f'(net {nid} {_q(name)})')

    lines.append("")
    for comp in sorted(comps, key=lambda c: c.get("ref") or ""):
        lines.append(_symbol_node(comp, net_ids))
        lines.append("")

    lines.append(")")
    return "\n".join(lines) + "\n"


def export_kicad(netlist: Dict[str, Any], dest: str) -> str:
    """Write a contract netlist to `dest` as `.kicad_sch`. Returns dest."""
    text = build_kicad_sch(netlist)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)
    return dest


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 3:
        print("usage: python export_kicad.py <netlist.json> <out.kicad_sch>")
        sys.exit(2)
    with open(sys.argv[1]) as f:
        nl = json.load(f)
    export_kicad(nl, sys.argv[2])
    print(f"wrote {sys.argv[2]}")