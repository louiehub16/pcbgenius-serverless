"""PCBGenius D5 — BOM / fabrication source.

Bundle exporting a design's bill of materials and estimating fab cost from the
FROZEN INTERFACE CONTRACT netlist shape (mirrors pcbgenius-backend/src/types.ts):

    schema_version, metadata{design_name, description, board_layers, created_by, target_fab},
    components[{ref, type, value, package, mpn, pins[{number,name,net}], properties}],
    nets[{name, pins[], class}]

Modules
-------
bom        — group components into a BOM (CSV + InteractiveHtmlBom HTML).
cost       — cost estimate from BOM + board params.
rules_files— ingest JLCPCB / PCBWay DRC capability files (offline built-ins).
fab_rules  — map design requirements into 4D manufacturing rule checks
             (TRACE / SPACING / DRILL / BOARD) so a design only passes when
             it meets the chosen fab's capability file (manufacturing-first).
"""

from .bom import build_bom, write_bom_csv, write_bom_html
from .cost import estimate_cost
from .rules_files import (
    BUILTIN_CAPABILITIES, parse_capability_file, parse_capability_text,
    load_capabilities, get_capability,
)
from .fab_rules import check_fab_rules, choose_fab, design_matches

__all__ = [
    "build_bom",
    "write_bom_csv",
    "write_bom_html",
    "estimate_cost",
    "BUILTIN_CAPABILITIES",
    "parse_capability_file",
    "parse_capability_text",
    "load_capabilities",
    "get_capability",
    "check_fab_rules",
    "choose_fab",
    "design_matches",
]

__version__ = "2.0.0"