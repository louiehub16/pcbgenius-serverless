"""
PCBGenius D5 — bom.py
======================
Turn a CONTRACT netlist into a Bill of Materials.

Grouping key is (value, package, mpn) — this is the unit a fab house mounts and
bills. Components are grouped by identical value+package+mpn; each BOM line lists
the refs that share that group.

Outputs:
  * CSV   — flat BOM consumable by JLCPCB / PCBWay import tools & spreadsheets.
  * HTML  — self-contained InteractiveHtmlBom-style viewer (searchable/bom-rows).
            The renderer first tries to import the `InteractiveHtmlBom` package;
            if it is not installed we fall back to a dependency-free HTML table so
            the tool never hard-fails on an empty venv.

Shape of a netlist element (frozen contract):
    { "ref", "type", "value", "package", "mpn", "pins": [...], "properties": {} }
"""

from __future__ import annotations

import csv
import datetime
import html
import io
from collections import OrderedDict
from typing import Any, Dict, List, Tuple

# Grouping key = (value, package, mpn). None-safe.
_GROUP_KEY = Tuple[str, str, str]
BOM_HEADERS = [
    "Designator",
    "Value",
    "Package",
    "MPN",
    "Quantity",
    "Type",
]


def _norm(value: Any) -> str:
    """Stringify a field for grouping; empty/None become '' (stable sorting)."""
    if value is None:
        return ""
    return str(value).strip()


def group_components(components: List[Dict[str, Any]]) -> Dict[_GROUP_KEY, Dict]:
    """Group components by (value, package, mpn); each row holds refs + count.

    Returns an insertion-ordered mapping key -> row dict. Ref order preserved
    across the source list so the CSV reads naturally.
    """
    groups: "OrderedDict[_GROUP_KEY, Dict]" = OrderedDict()
    for comp in components:
        key = (
            _norm(comp.get("value")),
            _norm(comp.get("package")),
            _norm(comp.get("mpn")),
        )
        row = groups.setdefault(
            key,
            {
                "refs": [],
                "value": _norm(comp.get("value")),
                "package": _norm(comp.get("package")),
                "mpn": _norm(comp.get("mpn")),
                "type": _norm(comp.get("type")),
            },
        )
        row["refs"].append(_norm(comp.get("ref")))
    return groups


def build_bom(netlist: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build a BOM row list from a contract netlist.

    Each row: {Designator, Value, Package, MPN, Quantity, Type}.
    Rows are sorted by (Value, Package) for stable diffs.
    """
    components = netlist.get("components", []) or []
    groups = group_components(components)

    rows: List[Dict[str, Any]] = []
    for key, g in groups.items():
        rows.append(
            {
                "Designator": ",".join(g["refs"]),
                "Value": g["value"],
                "Package": g["package"],
                "MPN": g["mpn"],
                "Quantity": len(g["refs"]),
                "Type": g["type"],
            }
        )
    rows.sort(key=lambda r: (r["Value"].upper(), r["Package"].upper()))
    return rows


def total_unique_parts(bom: List[Dict[str, Any]]) -> int:
    """Count of reference designators across all rows (sum of quantities)."""
    return sum(r["Quantity"] for r in bom)


def write_bom_csv(rows: List[Dict[str, Any]], path: str) -> str:
    """Write BOM rows to a CSV file, return the destination path."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=BOM_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def render_bom_html(rows: List[Dict[str, Any]], design_name: str = "") -> str:
    """Try InteractiveHtmlBom first, else an offline HTML-table fallback."""
    try:  # optional third-party viewer
        from InteractiveHtmlBom.ibom import generate
        from InteractiveHtmlBom.drawing import Drawing  # noqa: F401

        # Not all dists expose a doc-string API, so wrap any shape mismatch.
        record = {"design_name": design_name}
        # Best-effort: produce HTML even if the lib's surface drifted.
        try:
            return generate(record)  # type: ignore[arg-type]
        except Exception:
            # InteractiveHtmlBom may need full part/kicad structs unavailable
            # here (no KicadModTree, etc.). Fall through to the embedded viewer.
            pass
    except ImportError:
        pass

    # ---- Dependency-free embedded viewer --------------------------------
    esc = html.escape
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows_html = "\n".join(
        "<tr>"
        f"<td>{esc(r['Designator'])}</td>"
        f"<td>{esc(r['Value'])}</td>"
        f"<td>{esc(r['Package'])}</td>"
        f"<td>{esc(r['MPN'])}</td>"
        f"<td class='qty'>{r['Quantity']}</td>"
        f"<td>{esc(r['Type'])}</td>"
        "</tr>"
        for r in rows
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>BOM — {esc(design_name or 'design')}</title>
<style>
  body{{font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;margin:2rem;color:#1c2733}}
  h1{{font-size:1.5rem;margin-bottom:.25rem}}
  .meta{{color:#5b6b7b;font-size:.85rem;margin-bottom:1rem}}
  table{{border-collapse:collapse;width:100%;max-width:1100px;font-size:.9rem}}
  th,td{{text-align:left;padding:.5rem .75rem;border-bottom:1px solid #e2e8f0}}
  th{{background:#f1f5f9;font-weight:600;position:sticky;top:0}}
  td.qty{{font-weight:700;color:#0f766e}}
  tr:hover td{{background:#f8fafc}}
  #search{{margin-bottom:1rem;padding:.5rem .75rem;width:280px;border:1px solid #cbd5e1;border-radius:8px}}
</style></head>
<body>
<h1>Bill of Materials</h1>
<div class="meta">{esc(design_name or 'design')} · generated {now} ·
 {len(rows)} unique part(s) / {sum(r['Quantity'] for r in rows)} total ref designator(s)</div>
<input id="search" type="search" placeholder="Filter value / ref / pkg..." oninput="flt(this.value)">
<table>
<thead><tr><th>Designator</th><th>Value</th><th>Package</th><th>MPN</th>
<th>Quantity</th><th>Type</th></tr></thead>
<tbody>{rows_html}</tbody>
</table>
<script>
function flt(q){{q=q.toLowerCase();
 document.querySelectorAll('tbody tr').forEach(function(tr){{tr.style.display=tr.textContent.toLowerCase().includes(q)?'':'none';}});}}
</script>
</body></html>"""


def write_bom_html(rows: List[Dict[str, Any]], path: str, design_name: str = "") -> str:
    """Write BOM HTML viewer to `path`, return the destination path."""
    content = render_bom_html(rows, design_name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def csv_bytes(rows: List[Dict[str, Any]]) -> str:
    """Return BOM as a CSV string (in-memory, for API responses)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=BOM_HEADERS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()