"""
PCBGenius D5 — tests/test_bom.py
================================
Verify BOM grouping + fab submission against 3 distinct netlists:
  A) LDO regulator with repeated caps sharing (value,package,mpn) -> grouped.
  B) Buck converter: decoupling caps that DIFFER by package stay separate rows.
  C) Mixed-signal board: filter caps with shared value but different package +
     repeated same part -> correct group counts and quantities.

Each test asserts the NUMBER of BOM rows and the per-row quantities directly
from the files under pcbgenius-fab (not a re-implementation).
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bom import build_bom, write_bom_csv, write_bom_html, csv_bytes, total_unique_parts
from fab_api import submit_order, FabAPIError
from cost import estimate_cost


def _comp(ref, ctype, value, package, mpn=None):
    return {
        "ref": ref,
        "type": ctype,
        "value": value,
        "package": package,
        "mpn": mpn,
        "pins": [{"number": "1", "name": "1", "net": "GND"}],
        "properties": {},
    }


def _netlist(name, components, target_fab=None):
    return {
        "schema_version": "1.0.0",
        "metadata": {
            "design_name": name,
            "description": "bom test",
            "board_layers": 2,
            "created_by": "pcbgenius",
            "target_fab": target_fab,
        },
        "components": components,
        "nets": [{"name": "GND", "pins": [], "class": "ground"},
                 {"name": "VCC", "pins": [], "class": "power"}],
    }


# ---- A) LDO: 2 identical 10uF/0805 caps share one group -----------------
NETLIST_A = _netlist("ldo", [
    _comp("U1", "ic", "AMS1117-3.3", "SOT-223", "AMS1117-3.3"),
    _comp("C1", "capacitor", "10uF", "0805", None),
    _comp("C2", "capacitor", "10uF", "0805", None),
    _comp("R1", "resistor", "10k", "0603", None),
])

# ---- B) Buck: same 100nF value, DIFFERENT packages -> separate rows -----
NETLIST_B = _netlist("buck", [
    _comp("U1", "ic", "TPS54331", "SOIC-8", "TPS54331"),
    _comp("C1", "capacitor", "100nF", "0603", None),
    _comp("C2", "capacitor", "100nF", "0402", None),   # 100nF but smaller pkg
    _comp("C3", "capacitor", "100nF", "0603", None),   # groups with C1
    _comp("L1", "inductor", "10uH", "CDRH4D28", None),
])

# ---- C) Mixed-signal: 100pF appears in two packages + repeated 4.7uF ----
NETLIST_C = _netlist("mixed", [
    _comp("C1", "capacitor", "100pF", "0603", None),
    _comp("C2", "capacitor", "100pF", "0402", None),   # different package
    _comp("C3", "capacitor", "4.7uF", "0805", None),
    _comp("C4", "capacitor", "4.7uF", "0805", None),   # shares group w/ C3
    _comp("C5", "capacitor", "4.7uF", "0805", None),   # 3x total in that group
    _comp("R1", "resistor", "1k", "0603", None),
    _comp("J1", "connector", "USB-C", "USB_C_DFP", None),
])


# --------------------------------------------------------------------------

def _row_by_value(bom, value):
    hits = [r for r in bom if r["Value"] == value]
    return hits


def test_netlistA_groups_identical_caps():
    bom = build_bom(NETLIST_A)
    # 4 parts -> 3 rows: (AMS1117)(10uF/0805 C1,C2Q2)(10k R1)
    assert len(bom) == 3, f"expected 3 rows, got {len(bom)}"
    caps = _row_by_value(bom, "10uF")
    assert len(caps) == 1
    assert caps[0]["Quantity"] == 2
    assert set(caps[0]["Designator"].split(",")) == {"C1", "C2"}
    assert total_unique_parts(bom) == 4


def test_netlistB_package_differentiates_rows():
    bom = build_bom(NETLIST_B)
    # U1 + 100nF/0603(C1,C3 Q2) + 100nF/0402(C2) + 10uH = 4 rows
    assert len(bom) == 4, f"expected 4 rows, got {len(bom)}"
    nfs = [r for r in bom if r["Value"] == "100nF"]
    assert len(nfs) == 2, "100nF appears in two rows because packages differ"
    qty0603 = [r for r in nfs if r["Package"] == "0603"][0]["Quantity"]
    qty0402 = [r for r in nfs if r["Package"] == "0402"][0]["Quantity"]
    assert qty0603 == 2
    assert qty0402 == 1


def test_netlistC_repeated_and_split_counts():
    bom = build_bom(NETLIST_C)
    # C1/C1(0603) + C2(0402) + C3/C4/C5(4.7uF x3) + R1 + J1 = 5 rows
    assert len(bom) == 5, f"expected 5 rows, got {len(bom)}"
    c47 = _row_by_value(bom, "4.7uF")
    assert len(c47) == 1
    assert c47[0]["Quantity"] == 3
    assert c47[0]["Package"] == "0805"
    # two distinct 100pF rows
    assert len([r for r in bom if r["Value"] == "100pF"]) == 2
    assert total_unique_parts(bom) == 7


def test_streams_to_csv_and_html(tmp_path):
    bom = build_bom(NETLIST_A)
    csv_p = write_bom_csv(bom, os.path.join(tmp_path, "bom.csv"))
    html_p = write_bom_html(bom, os.path.join(tmp_path, "bom.html"), "ldo")
    assert os.path.exists(csv_p) and os.path.exists(html_p)
    text = open(csv_p, encoding="utf-8").read()
    assert text.splitlines()[0].startswith("Designator,Value,Package")
    assert "bom.html" in html_p
    assert "Quantity" in open(html_p, encoding="utf-8").read()


def test_csv_bytes_matches_rows():
    bom = build_bom(NETLIST_B)
    text = csv_bytes(bom)
    # header + 4 data rows
    assert len(text.splitlines()) == 1 + len(bom)


# ---- fab_api + cost smoke tests -----------------------------------------

def test_fab_submission_stub():
    nl = _netlist("ldo", [c for c in NETLIST_A["components"]], target_fab="jlcpcb")
    res = submit_order(nl, "jlcpcb")
    assert res.status == "submitted"
    assert res.fab == "jlcpcb"
    assert res.bom_rows == len(build_bom(nl))


def test_fab_rejects_unknown_house():
    nl = _netlist("buck", NETLIST_B["components"], target_fab="pcbway")
    try:
        submit_order(nl, "kicad")
        raise AssertionError("expected FabAPIError for unknown fab")
    except FabAPIError:
        pass


def test_fab_rejects_empty():
    try:
        submit_order(_netlist("empty", []), "jlcpcb")
        raise AssertionError("expected FabAPIError for empty netlist")
    except FabAPIError:
        pass


def test_cost_breaks_down():
    nl = _netlist("ldo", NETLIST_A["components"])
    c = estimate_cost(nl, width_mm=40, height_mm=30, quantity=1)
    assert c["total_usd"] > 0
    assert set(c["breakdown"]) == {
        "parts", "boards", "stencil", "assembly_setup", "shipping",
    }
    assert c["breakdown"]["parts"] > 0  # 1 IC + 2 caps + 1 resistor


def test_imports_available():
    import bom  # noqa: F401
    import cost  # noqa: F401
    import fab_api  # noqa: F401
    assert fab_api.list_call_sites()  # non-empty seam list


def test_marker_present_in_source():
    """Exactly one way to find every marked seam: grep the module source."""
    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(here, "..", "fab_api.py"), encoding="utf-8").read()
    assert "CALLSITE" in src and "REAL JLCPCB CALL WOULD GO HERE" in src