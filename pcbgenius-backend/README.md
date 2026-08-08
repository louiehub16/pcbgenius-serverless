# PCBGenius Backend — Cloudflare Workers Gateway (Wave-A Core)

Exposes the **10 FROZEN CONTRACT tool-call endpoints** (Section 2 of
`PCBGenius_FROZEN_Contract_v1.0_2026-07-24.yaml`) as a Hono worker.

Every endpoint currently returns a **STUB** response that matches the contract's
documented `returns` shape **exactly**, so the frontend, desktop, and verification
layers can build against the real interface now. Real integrations (KiCad, Ngspice,
Freerouting, component RAG, fab APIs) plug in later waves **without touching the wire
contract**.

## Endpoints

| Method | Path               | Contract tool-call     |
|--------|--------------------|------------------------|
| POST   | `/run_erc`         | `run_erc`              |
| POST   | `/run_drc`         | `run_drc`              |
| POST   | `/run_simulation`  | `run_simulation`       |
| POST   | `/run_auto_layout` | `run_auto_layout`      |
| POST   | `/query_component_db` | `query_component_db` |
| POST   | `/get_datasheet_spec` | `get_datasheet_spec` |
| POST   | `/check_fab_rules` | `check_fab_rules`      |
| POST   | `/export_gerber`   | `export_gerber`        |
| POST   | `/generate_firmware` | `generate_firmware`  |
| POST   | `/request_approval` | `request_approval`    |

## Response envelope

- Success: `{ ok: true, data: <contract return shape> }`
- Error:   `{ ok: false, error: { code, message, details? } }`

## Stack

- Hono + Zod (`@hono/zod-validator`) — the zod schemas in `src/validate.ts` mirror
  the contract's netlist shape and per-tool `arguments`.
- CORS for `http://localhost:5173`
- In-memory rate limiter (per IP, 120 req / 60s)
- Vitest tests covering all 10 endpoints + validation/error envelopes

## Run

```bash
npm install
npm run dev       # wrangler dev
npm test          # vitest
npm run deploy    # wrangler deploy (needs Cloudflare auth)
```

> Wave-A only: **do not deploy yet.** No real integrations, no billing.
