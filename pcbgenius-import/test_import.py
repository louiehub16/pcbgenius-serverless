#!/usr/bin/env python3
"""
PCBGenius — D11 EDA import round-trip test (REAL source)
========================================================
Verifies the KiCad import/export round-trip keeps refs and nets identical:

    contract netlist ──► export_kicad.build_kicad_sch ──► .kicad_sch text
                  ──► kicad.parse_kicad_sch ──► round-trip contract

Checks
------
  1. Exported text is parse-able and starts with a valid kicad_sch header.
  2. Every component ref in the source netlist appears in the round-tripped one.
  3. Every net name in the source netlist appears in the round-tripped one.
  4. Importing a *board* (.kicad_pcb) recovers nets from per-pad assignments.
  5. pyedb import raises NotImplementedError gracefully when pyedb is absent
     (it is on this machine), proving the module is import-safe.

Run with plain stdlib (no network / no docker / no KiCad):
    python test_import.py            # exit 0 on pass, 1 on fail
"""

from __future__ import annotations

import sys
from typing import Any, Dict

from kicad import parse_kicad_pcb, parse_kicad_sch
from export_kicad import build_kicad_sch


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────
def _comp(ref, ctype, value, package, pins):
    p = [{"number": str(i + 1), "name": n, "net": net_} for i, (n, net_) in enumerate(pins)]
    return {"ref": ref, "type": ctype, "value": value, "package": package,
            "mpn": None, "pins": p, "properties": {}}


def _net(name, cls, pins):
    return {"name": name, "pins": pins, "class": cls}


def sample_netlist() -> Dict[str, Any]:
    """A small, contract-valid netlist (3 comps / 3 nets) used for round-trip."""
    return {
        "schema_version": "1.0.0",
        "metadata": {
            "design_name": "d11_rt", "description": "round-trip fixture",
            "board_layers": 2, "created_by": "pcbgenius", "target_fab": "jlcpcb",
        },
        "components": [
            _comp("U1", "ic", "LDO5V", "SOIC-8", [("VIN", "VIN"), ("GND", "GND"), ("OUT", "VOUT")]),
            _comp("C1", "capacitor", "1uF", "0805", [("1", "VIN"), ("2", "GND")]),
            _comp("R1", "resistor", "10k", "0603", [("1", "VOUT"), ("2", "GND")]),
        ],
        "nets": [
            _net("VIN", "power", ["U1.VIN", "C1.1"]),
            _net("GND", "ground", ["U1.GND", "C1.2", "R1.2"]),
            _net("VOUT", "power", ["U1.OUT", "R1.1"]),
        ],
    }


_BOARD_SAMPLE = """\
(kicad_pcb (version 20221018) (generator "pcbnew")
  (net 0 "")
  (net 1 "GND")
  (net 2 "VIN")
  (footprint "R1"
    (property "Reference" "R1")
    (property "Value" "10k")
    (property "Footprint" "Resistor_SMD:R_0805")
    (pad 1 smd roundrect (at -1 0) (size 1 1.2) (net 2 "VIN"))
    (pad 2 smd roundrect (at 1 0) (size 1 1.2) (net 1 "GND")))
  (footprint "C1"
    (property "Reference" "C1")
    (property "Value" "1uF")
    (property "Footprint" "Capacitor_SMD:C_0805")
    (pad 1 smd roundrect (at -1 0) (size 1 1.2) (net 2 "VIN"))
    (pad 2 smd roundrect (at 1 0) (size 1 1.2) (net 1 "GND"))))
"""


# ─────────────────────────────────────────────────────────────────────────────
# Checks
# ─────────────────────────────────────────────────────────────────────────────
def check_header() -> None:
    text = build_kicad_sch(sample_netlist())
    assert text.lstrip().startswith("(kicad_sch"), "export should begin with kicad_sch"


def check_refs_preserved() -> None:
    src = sample_netlist()
    text = build_kicad_sch(src)
    rt, _warn = parse_kicad_sch(text, design_name="d11_rt")
    src_refs = {c["ref"] for c in src["components"]}
    rt_refs = {c["ref"] for c in rt["components"]}
    assert src_refs == rt_refs, f"refs differ: {src_refs} != {rt_refs}"


def check_nets_preserved() -> None:
    src = sample_netlist()
    text = build_kicad_sch(src)
    rt, _warn = parse_kicad_sch(text, design_name="d11_rt")
    src_nets = {n["name"] for n in src["nets"]}
    rt_nets = {n["name"] for n in rt["nets"]}
    assert src_nets == rt_nets, f"nets differ: {src_nets} != {rt_nets}"


def check_pcb_nets() -> None:
    """Board import recovers nets from per-pad (net <id> <name>) entries."""
    nl, _warn = parse_kicad_pcb(_BOARD_SAMPLE, design_name="brd")
    net_names = {n["name"] for n in nl["nets"]}
    assert {"GND", "VIN"} <= net_names, f"board nets missing: got {net_names}"
    refs = {c["ref"] for c in nl["components"]}
    assert refs == {"R1", "C1"}, f"board refs differ: {refs}"
    # R1 pin connected to VIN must appear in the VIN net's pin list
    vin = next(n for n in nl["nets"] if n["name"] == "VIN")
    assert any(p.startswith("R1.") for p in vin["pins"]), "R1 pad not on VIN net"
    # every parsed component must carry >=1 pin so SRJ resolve passes later
    for comp in nl["components"]:
        assert len(comp["pins"]) >= 1, f"{comp['ref']} has no pins"


def check_pyedb_stub() -> None:
    """pyedb module must import safely and raise NotImplementedError w/o pyedb."""
    import pyedb  # noqa: F401  (shadows the true library when absent here)
    assert callable(pyedb.pyedb_available)
    try:
        pyedb.parse_edb("x.edb")
        # On a rig WITH pyedb, parse may raise for other reasons; only assert
        # when we know pyedb is missing.
        if not pyedb.pyedb_available():
            raise AssertionError("parse_edb should raise NotImplementedError w/o pyedb")
    except NotImplementedError:
        pass  # expected when the optional dependency is absent
    print("    (pyedb available on this machine: %s)" % pyedb.pyedb_available())


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    tests = [check_header, check_refs_preserved, check_nets_preserved,
             check_pcb_nets, check_pyedb_stub]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {t.__name__}: unexpected {type(e).__name__}: {e}")
    print(f"\n{'ALL PASS' if failed == 0 else f'{failed} FAILURE(S)'} (stdlib only, no KiCad)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())