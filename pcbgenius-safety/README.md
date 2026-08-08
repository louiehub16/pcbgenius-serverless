# PCBGenius — D1 Bulletproof Beginner Layers (`pcbgenius-safety`)

A three-gate **pure-Python** safety layer that guards the FROZEN CONTRACT
netlist before it may proceed to simulation, layout, export, or fabrication.

```
┌────────────────┐   ┌──────────────────┐   ┌─────────────────┐
│ 1 allowlist.py │ → │ 2 constraints.py │ → │ 3 refusals.py   │
│ component/     │   │ spec-checker:    │   │ hard-refusal for│
│ package/net    │   │ voltage-scaled   │   │ impossible /    │
│ allowlist      │   │ clearance + IC   │   │ unsafe /        │
│                │   │ decoupling       │   │ ambiguous       │
└────────────────┘   └──────────────────┘   └─────────────────┘
```

## Files

| File                  | Purpose                                                        |
|-----------------------|----------------------------------------------------------------|
| `allowlist.py`        | Enforce contractor safety allowlist: allowed component types, packages, net name/class. Anything outside is blocked with a clear message. |
| `constraints.py`      | Spec-checker: clearances scaled by rail voltage (IPC-2221-derived) + decoupling cap required on every IC power pin. |
| `refusals.py`         | Hard-refusal for impossible / unsafe / ambiguous designs (empty, no ground, mains, over-voltage >24V, broken pin links, unverifiable). |
| `util.py`             | Shared helpers (violation factory, voltage guess, clearance model). |
| `__init__.py`         | Package entry: `run_safety()` full gate verdict + per-gate runs. |
| `tests/test_safety.py`| 5 good + 5 bad designs + focused unit tests (9 tests). |

## Usage

```python
from pcbgenius_safety import run_safety

verdict = run_safety(netlist, context=None, layout=None)
# => { "version", "pass", "refused", "gates": {...}, "violations": [...] }
```

Entry points:
- `run_allowlist(netlist)`        → Gate 1 verdict
- `run_constraints(netlist, layout=None)` → Gate 2 verdict
- `run_refusals(netlist, context=None)`   → Gate 3 verdict
- `run_safety(netlist, context=None, layout=None)` → combined

`context` may carry `approval_granted` (bool) and `erc_available` (bool) so the
refusal gate can honor explicit human approval for over-voltage rails.

`layout` (for constraints) may carry `layout.spacings = [{"net","clearance_mm"}]`.

The `voltage` field on a net (or the nominal value of a `power` component whose
`OUT` pin feeds it) is honored for clearance scaling when present.

## Run tests

```bash
python -m pytest tests/test_safety.py   # if pytest available
python tests/test_safety.py             # standalone runner (no deps)
python __init__.py <netlist.json>       # CLI verdict on a netlist file
```

Pure, no I/O, no network, no filesystem — safe to call from the backend routes,
CLI, tests, or the fine-tuned model's tool plumbing.

## Backend wiring

The backend (`pcbgenius-backend`) marks the safety call sites in its routes —
see `src/safety.ts` and the `// [D1-safety]` markers in the route files. The
gate never mutates the contract response envelope; it adds a `safety` field and
can hard-refuse a request before it reaches the stub handler.