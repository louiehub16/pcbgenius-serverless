# pcbgenius-fab — D5 BOM Export + Fab Integration

Python source tier for PCBGenius feature **#9 (D5 BOM export + fab integration)**.
Consumes the **FROZEN INTERFACE CONTRACT v1.0.0** netlist shape (mirrors
`pcbgenius-backend/src/types.ts`).

## Modules

| File         | Purpose                                                                   |
|--------------|---------------------------------------------------------------------------|
| `bom.py`     | Group contract netlist by `(value, package, mpn)` → BOM rows; export CSV + InteractiveHtmlBom-style HTML view (dependency-free fallback). |
| `cost.py`    | Transparent board-cost estimate: parts + bare-board + stencil + setup + shipping. |
| `fab_api.py` | JLCPCB / PCBWay order-submission **stubs**; every real-API seam is marked `@CALLSITE` (see `list_call_sites()`). |
| `tests/`     | `test_bom.py` — 11 tests, 3 distinct netlists assert correct group counts. |

## Usage

```python
from bom import build_bom, write_bom_csv, write_bom_html
from cost import estimate_cost
from fab_api import submit_order

netlist = {...}                       # contract v1.0.0 netlist
bom = build_bom(netlist)              # grouped rows
write_bom_csv(bom, "bom.csv")
write_bom_html(bom, "bom.html", design_name="my_ldo")

est = estimate_cost(netlist, width_mm=40, height_mm=30, quantity=1)
print(est["total_usd"], est["breakdown"])

res = submit_order(netlist, "jlcpcb") # stub; read res.as_dict()
```

## Run tests

```bash
python -m pytest tests/test_bom.py -q        # needs pytest
# or dependency-free:
python -c "import importlib.util,os,tempfile; \
s=importlib.util.spec_from_file_location('t','tests/test_bom.py');\
m=importlib.util.module_from_spec(s);s.loader.exec_module(m);\
[f() for f in [getattr(m,n) for n in dir(m) if n.startswith('test_')]]"
```

## Integration seams (for the next build pass)

Grep for `CALLSITE` in `fab_api.py` — these are the exact spots to plug in the
real JLCPCB OpenLab SMT and PCBWay assembly-order HTTP calls.
