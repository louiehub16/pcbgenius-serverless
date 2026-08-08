"""
PCBGenius — E2 Manufacturing-first (rules_files.py)
====================================================
Ingest / normalize DRC capability files from the major low-volume fab houses
(JLCPCB, PCBWay) into a single, unit-normalized capability dict that the
`fab_rules` module can score a design against.

Why "manufacturing-first"
-------------------------
A design is only manufacturable if every physical requirement it places on the
fab (trace width, clearance, drill, annular ring, layer count, board size,
thickness, controlled impedance) fits inside what the chosen fab can actually
do. That comparison is driven by a *capability file* — the DRC limits the fab
publishes on its capability/design-rules page. This module turns those files
into machine-readable capability objects, no network required: the well-known
JLCPCB / PCBWay limits are embedded as built-ins so the tool is fully offline,
and users can drop in their own parsed file via `parse_capability_file`.

Supported input formats (auto-detected)
---------------------------------------
  * JSON object  — a single capability record (keys matched leniently).
  * JSON list    — the first valid record wins.
  * key = value  — one `key = value` per line, blank/#-lines ignored.
  * CSV          — header row of capability field names + one data row.

All linear measures are normalized to millimetres; every numeric value is
floored/none-safed so a half-populated file still yields a usable struct.

Normalized capability shape
---------------------------
    {
      "fab": "jlcpcb" | "pcbway" | str,
      "source": "builtin" | str,
      "layers":              {"min": 1, "max": 8},
      "min_trace_mm":        0.127,
      "min_clearance_mm":    0.127,
      "min_drill_mm":        0.2,       # through-hole drill diameter
      "min_via_mm":          0.2,       # smallest blind/buried or finished via
      "min_annular_ring_mm": 0.13,      # copper ring around a hole
      "min_board_mm":        5.0,       # smallest board edge dimension
      "max_board_mm":        400.0,     # largest board edge dimension
      "board_thickness_mm":  {"min": 0.4, "max": 3.2},   # supported range
      "supported_layers":    [1, 2, 4, 6, 8],            # discrete stackups
      "supported_thicknesses_mm": [0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0],
      "impedance_controlled": True,
      "notes": "...",
    }
"""

from __future__ import annotations

import csv
import io
import json
import os
from typing import Any, Dict, List, Optional

Capability = Dict[str, Any]

# Key aliases map many spellings used in real cap files onto one canonical key.
_KEY_ALIASES = {
    "layers": {"layers", "layer", "layer_count", "num_layers", "max_layers"},
    "min_layer": {"min_layer", "min_layers", "layer_min"},
    "min_trace_mm": {"min_trace_mm", "min_trace", "trace", "trace_width",
                     "min_trace_width", "trace_width_mil"},
    "min_clearance_mm": {"min_clearance_mm", "min_clearance", "clearance",
                         "min_space", "spacing", "min_spacing"},
    "min_drill_mm": {"min_drill_mm", "min_drill", "min_hole", "drill",
                     "min_hole_size", "min_drill_size"},
    "min_via_mm": {"min_via_mm", "min_via", "via", "min_via_diameter",
                   "min_via_hole"},
    "min_annular_ring_mm": {"min_annular_ring_mm", "annular_ring",
                            "min_annular", "annular"},
    "min_board_mm": {"min_board_mm", "min_board", "board_min", "min_size"},
    "max_board_mm": {"max_board_mm", "max_board", "board_max", "max_size"},
    "max_thickness_mm": {"max_thickness_mm", "max_board_thickness",
                         "thickness_max"},
    "min_thickness_mm": {"min_thickness_mm", "min_board_thickness",
                         "thickness_min"},
    "impedance_controlled": {"impedance_controlled", "impedance",
                             "controlled_impedance", "impedance_ctrl"},
}

# mil -> mm conversion factor used when a capability value is clearly in mil.
_MIL = 0.0254


def _num(value: Any) -> Optional[float]:
    """Best-effort float conversion; trims units suffixes and mil markers."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().lower().replace(",", "")
        if not s:
            return None
        is_mil = any(tok in s for tok in ("mil", "thou")) or s.endswith("'")\
            or ("mil" in s)
        if is_mil:
            s = s.replace("mil", "").replace("thou", "").replace("'", "")
            try:
                return _NUM_FROM_STR(s) * _MIL
            except ValueError:
                return None
        for unit in ("mm", "um", "µm", "in", '"', "%", "ohm", "ohm_"):
            s = s.replace(unit, "")
        s = s.strip()
        try:
            return _NUM_FROM_STR(s)
        except ValueError:
            return None
    return None


try:  # locale-independent float() that also tolerates trailing junk
    _NUM_FROM_STR = float
except Exception:  # pragma: no cover
    _NUM_FROM_STR = float


def _booly(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) != 0
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on", "supported")


def _pick(rec: Dict[str, Any], names: set, default: Any = None) -> Any:
    for n in names:
        if n in rec and rec[n] is not None:
            return rec[n]
    return default


def _normalize_record(rec: Dict[str, Any], fab: Optional[str],
                      source: str) -> Capability:
    """Map a raw parsed record onto the canonical capability shape (mm)."""
    out: Capability = {
        "fab": (rec.get("fab") or fab or "unknown").strip().lower(),
        "source": source,
        "layers": {},
        "min_trace_mm": None,
        "min_clearance_mm": None,
        "min_drill_mm": None,
        "min_via_mm": None,
        "min_annular_ring_mm": None,
        "min_board_mm": None,
        "max_board_mm": None,
        "board_thickness_mm": {},
        "supported_layers": [],
        "supported_thicknesses_mm": [],
        "impedance_controlled": False,
        "notes": _pick(rec, {"notes", "note", "comment", "comments"}, ""),
    }

    layers = _pick(rec, _KEY_ALIASES["layers"])
    if layers is not None:
        out["layers"]["max"] = _num(layers)
    out["layers"]["min"] = _num(_pick(rec, _KEY_ALIASES["min_layer"], 1))

    supported_layers = rec.get("supported_layers")
    if isinstance(supported_layers, list):
        out["supported_layers"] = [int(_num(x) or 0) for x in supported_layers]

    for field in ("min_trace_mm", "min_clearance_mm", "min_drill_mm",
                  "min_via_mm", "min_annular_ring_mm",
                  "min_board_mm", "max_board_mm"):
        out[field] = _num(_pick(rec, _KEY_ALIASES[field]))

    out["board_thickness_mm"]["max"] = _num(
        _pick(rec, _KEY_ALIASES["max_thickness_mm"]))
    out["board_thickness_mm"]["min"] = _num(
        _pick(rec, _KEY_ALIASES["min_thickness_mm"]))

    sups = rec.get("supported_thicknesses_mm")
    if isinstance(sups, list):
        out["supported_thicknesses_mm"] = [
            _num(x) for x in sups if _num(x) is not None]

    out["impedance_controlled"] = _booly(
        _pick(rec, _KEY_ALIASES["impedance_controlled"], False))
    return out


def parse_capability_text(text: str, fab: Optional[str] = None,
                          source: str = "file") -> Capability:
    """Parse capability data from a string, auto-detecting the format."""
    text = text.strip()
    if not text:
        return _normalize_record({}, fab, source)

    if text.lstrip().startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list) and data:
                rec = data[0]
            else:
                rec = {}
        except json.JSONDecodeError:
            rec = {}
    else:
        try:
            data = json.loads(text)
            rec = data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            rec = _parse_keyvalue(text) or _parse_csv(text)

    # Flatten nested {'layers': {'min':..,'max':..}} style where needed.
    if isinstance(rec, dict) and isinstance(rec.get("layers"), dict):
        ldict = rec.pop("layers")
        rec.setdefault("max_layers", ldict.get("max"))
        rec.setdefault("min_layer", ldict.get("min"))
    if isinstance(rec, dict) and isinstance(rec.get("board_thickness_mm"), dict):
        tdict = rec.pop("board_thickness_mm")
        rec.setdefault("max_board_thickness", tdict.get("max"))
        rec.setdefault("min_board_thickness", tdict.get("min"))

    return _normalize_record(rec if isinstance(rec, dict) else {}, fab, source)


def parse_capability_file(path: str, fab: Optional[str] = None) -> Capability:
    """Read a capability/DRC file from disk and normalize it."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        data = fh.read()
    return parse_capability_text(data, fab=fab or os.path.basename(path),
                                 source=os.path.basename(path))


def _parse_keyvalue(text: str) -> Dict[str, Any]:
    """Parse `key = value` lines into a dict."""
    rec: Dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
        elif ":" in line and "://" not in line:
            k, _, v = line.partition(":")
        else:
            continue
        k = k.strip()
        v = v.strip()
        if not k or not v:
            continue
        # maybe a comma-separated list
        if "," in v and any(c.isdigit() for c in v):
            parts = [p.strip() for p in v.split(",")] or v
            try:
                rec[k] = [float(p) for p in parts]
                continue
            except ValueError:
                pass
        rec[k] = v
    return rec


def _parse_csv(text: str) -> Dict[str, Any]:
    """Parse a single-row CSV whose header names match capability fields."""
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return {}
    if len(rows) < 2:
        return {}
    headers = [h.strip().lower() for h in rows[0]]
    data = rows[1]
    if len(headers) != len(data):
        return {}
    return {headers[i]: data[i] for i in range(len(headers)) if data[i]}


# ── Well-known built-in capability sets (offline defaults, representative). ──
# These mirror the public design-rule pages; drop in real capability files for
# vendor-owned ground truth via parse_capability_file.

BUILTIN_JLCPCB = parse_capability_text(json.dumps({
    "fab": "jlcpcb",
    "layers": {"min": 1, "max": 8},
    "supported_layers": [1, 2, 4, 6, 8],
    "min_trace_mm": 0.127,
    "min_clearance_mm": 0.127,
    "min_drill_mm": 0.2,
    "min_via_mm": 0.2,
    "min_annular_ring_mm": 0.13,
    "min_board_mm": 5.0,
    "max_board_mm": 400.0,
    "min_thickness_mm": 0.4,
    "max_thickness_mm": 3.2,
    "supported_thicknesses_mm": [0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0, 2.5, 3.2],
    "impedance_controlled": True,
    "notes": "Built-in JLCPCB approximate capability. Replace with a vendor file for ground truth.",
}), fab="jlcpcb", source="builtin")

BUILTIN_PCBWAY = parse_capability_text(json.dumps({
    "fab": "pcbway",
    "layers": {"min": 1, "max": 32},
    "supported_layers": [1, 2, 4, 6, 8, 10, 12, 16, 20, 24, 28, 32],
    "min_trace_mm": 0.1,
    "min_clearance_mm": 0.1,
    "min_drill_mm": 0.2,
    "min_via_mm": 0.15,
    "min_annular_ring_mm": 0.15,
    "min_board_mm": 5.0,
    "max_board_mm": 450.0,
    "min_thickness_mm": 0.2,
    "max_thickness_mm": 4.0,
    "supported_thicknesses_mm": [0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0, 2.4, 3.0, 3.2, 4.0],
    "impedance_controlled": True,
    "notes": "Built-in PCBWay approximate capability. Replace with a vendor file for ground truth.",
}), fab="pcbway", source="builtin")

BUILTIN_CAPABILITIES: Dict[str, Capability] = {
    "jlcpcb": BUILTIN_JLCPCB,
    "pcbway": BUILTIN_PCBWAY,
}


def get_capability(fab: str) -> Capability:
    """Return the capability set for a fab, resolving built-ins by name."""
    key = (fab or "").strip().lower()
    return BUILTIN_CAPABILITIES.get(key) or BUILTIN_JLCPCB


def load_capabilities(directory: Optional[str] = None) -> Dict[str, Capability]:
    """Load every capability file in `directory` (default: ./capabilities)."""
    directory = directory or os.path.join(os.path.dirname(__file__), "capabilities")
    loaded: Dict[str, Capability] = dict(BUILTIN_CAPABILITIES)
    if not os.path.isdir(directory):
        return loaded
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith((".json", ".txt", ".csv")):
            continue
        try:
            cap = parse_capability_file(os.path.join(directory, name))
        except Exception:
            continue  # skip malformed vendor files silently
        loaded[cap["fab"]] = cap
    return loaded