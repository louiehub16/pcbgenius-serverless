# PCBGenius Frontend — Schematic Canvas (Wave-A Core)

A beginner-friendly React + TypeScript + Vite + React Flow (`@xyflow/react`) app
that renders a **frozen-contract netlist** (Section 1 of
`PCBGenius_FROZEN_Contract_v1.0_2026-07-24.yaml`) as interactive schematic nodes.

## What it does

- **Renders** every `Component` as a node with one handle per `Pin`, and every
  `Net` as a hub node — pins connect to their net node. Dark + light mode.
- **Import JSON** / **Export JSON** — read/write a netlist matching the contract
  shape (downloads as `<design_name>.netlist.json`).
- **Property inspector** — click any component/net to see its full contract fields.
- **Stub violations panel** — a client-side validator mirrors the contract's
  `validation_rules` (unique refs, resolvable pins, must-have ground/power nets).
  In later waves this same list is fed by the real backend `run_erc`/`run_drc`.

The wire shape is identical to the backend's zod schemas, so anything this canvas
edits round-trips cleanly through the gateway.

## Run

```bash
npm install
npm run dev     # http://localhost:5173 (the origin the backend CORS allows)
npm run build
```

Backend dev server expected at `http://localhost:8787` (wrangler dev) for real
endpoint calls in later waves. Wave-A ships with a stub-only UI (no live calls),
so the canvas works 100% offline.
