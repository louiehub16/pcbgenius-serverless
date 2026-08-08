#!/usr/bin/env python3
"""
PCBGenius — D11 Eagle / Altium importer (pyEDB stub) (REAL source)
=================================================================
Import path for Eagle (.sch/.brd) and Altium (.SchDoc/.PcbDoc) projects via
PyAEDT's `pyaedt.Edb` / `pyedb.Edb` library. This is a **stub**: the pyedb
dependency is heavy and is only present on import-rig machines, so the whole
module is import-guarded and degrades gracefully to a documented
`NotImplementedError` when pyedb is not installed.

Design (so the real implementation can land in-place):
  * `parse_edb(project_path, ...)` — open the EDB project, walk components +
    nets, and emit the SAME frozen-contract netlist that `kicad.py`
    produces, so the rest of the pipeline (layout, SRJ, verification) is
    format-agnostic.
  * `pyedb_available()` — cheap probe so callers can branch without raising.

Round-trip note: Eagle/Altium export-back is out of scope for D11 (KiCad is
the export target). This module is the *import* complement to
`export_kicad.py`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

CONTRACT_VERSION = "1.0.0"

try:  # heavy / optional dependency — guard whole module
    import pyedb  # type: ignore
    from pyedb import Edb as _Edb  # type: ignore
    _PYEDB = True
except Exception:  # pragma: no cover - env-dependent
    pyedb = None  # type: ignore
    _Edb = None  # type: ignore
    _PYEDB = False


def pyedb_available() -> bool:
    """Return True if the `pyedb` library is importable on this machine."""
    return _PYEDB


def _net_class(name: str) -> str:
    low = (name or "").lower()
    if low in ("gnd", "ground", "0v", "vss"):
        return "ground"
    if low.startswith(("vcc", "vdd", "vin", "vout", "pwr", "+")):
        return "power"
    if "clk" in low:
        return "clock"
    return "signal"


def parse_edb(project_path: str, design_name: str = "",
              board_layers: int = 2,
              target_fab: str = "jlcpcb") -> Tuple[Dict[str, Any], List[str]]:
    """Open an EDB project (Eagle/Altium import targets) and return a contract
    netlist.

    This is the stub body. In a rig where `pyedb` is installed, the real walk
    (components -> pads -> nets) goes here and fills the empty arrays below.
    Reflection-style access keeps this from hard-crashing if the pyedb API
    differs across versions.
    """
    warnings: List[str] = []
    if not _PYEDB:
        raise NotImplementedError(
            "pyedb (PyAEDT EDB) not installed on this machine. "
            "Install `pyedb` on an import-rig to enable Eagle/Altium import."
        )

    comps: List[Dict[str, Any]] = []
    nets_agg: Dict[str, List[str]] = {}

    edb = _Edb(project_path, edbversion=None)
    try:
        # Components: pyedb.edb.components -> component_list / comp_instances
        comp_obj = getattr(edb, "components", None)
        if comp_obj is None:
            raise NotImplementedError("pyedb API shape changed: no .components")
        for comp in _iter_components(comp_obj):
            ref = getattr(comp, "name", "") or ""
            pins_rows = getattr(comp, "pins", None)
            pins: List[Dict[str, Any]] = []
            for pin in (_iter_pins(pins_rows) if pins_rows is not None else []):
                pname = getattr(pin, "name", None) or ""
                netname = getattr(pin, "net", None) or ""
                if netname:
                    nets_agg.setdefault(str(netname), []).append(f"{ref}.{pname}")
                pins.append({"number": str(getattr(pin, "number", pname)),
                             "name": pname, "net": str(netname)})
            comps.append({
                "ref": ref,
                "type": "ic",   # refined from properties/part-type in full impl
                "value": getattr(comp, "type", "") or "",
                "package": "",
                "mpn": None,
                "pins": pins,
                "properties": {},
            })
        nets = [
            {"name": name, "pins": pins, "class": _net_class(name)}
            for name, pins in sorted(nets_agg.items(), key=lambda kv: kv[0])
        ]
        warnings.append(f"pyedb parsed {len(comps)} components, {len(nets)} nets")
    finally:
        try:
            edb.close_edb()
        except Exception:  # pragma: no cover
            pass

    return {
        "schema_version": CONTRACT_VERSION,
        "metadata": {
            "design_name": design_name or "", "description": "",
            "board_layers": int(board_layers), "created_by": "pyedb-import",
            "target_fab": target_fab,
        },
        "components": sorted(comps, key=lambda c: c["ref"]),
        "nets": nets,
    }, warnings


def _iter_components(comp_obj: Any):
    """Best-effort iteration over a pyedb components container (version agnostic)."""
    # try iterator protocol first, then known attribute names
    attr_names = ("component_list", "instances", "all_components", "components")
    for attr in attr_names:
        container = getattr(comp_obj, attr, None)
        if container is None:
            continue
        try:
            return list(container.values()) if hasattr(container, "values") else list(container)
        except Exception:
            continue
    try:
        return list(comp_obj)
    except Exception:
        return []


def _iter_pins(pins_rows: Any) -> list:
    try:
        from collections.abc import Mapping
        if isinstance(pins_rows, Mapping):
            return list(pins_rows.values())
        return list(pins_rows)
    except Exception:
        return []


def import_eagle(basename: str, design_name: str = "", board_layers: int = 2):
    """Convenience wrapper for Eagle .sch/.brd — both resolve to an EDB export."""
    return parse_edb(basename, design_name=design_name, board_layers=board_layers)


def import_altium(basename: str, design_name: str = "", board_layers: int = 2):
    """Convenience wrapper for Altium .SchDoc/.PcbDoc — both resolve to EDB."""
    return parse_edb(basename, design_name=design_name, board_layers=board_layers)


if __name__ == "__main__":
    import json
    import sys
    print(f"pyedb available: {pyedb_available()}")
    if len(sys.argv) >= 2:
        try:
            nl, warn = parse_edb(sys.argv[1])
            for w in warn:
                print("# " + w)
            print(json.dumps(nl, indent=2))
        except NotImplementedError as e:
            print(f"!! {e}")
            sys.exit(3)
    else:
        print("usage: python pyedb.py <project.edb|.brd|.PcbDoc>")