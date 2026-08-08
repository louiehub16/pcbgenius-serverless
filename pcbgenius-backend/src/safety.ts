/**
 * PCBGenius — D1 Bulletproof Beginner Layers: backend SAFETY call-site wiring.
 *
 * The three-gate python safety layer (`pcbgenius-safety/`) is the authority for
 * bulletproofing beginner designs. This module is the TypeScript SINGLE POINT OF
 * TRUTH for *where* that layer is invoked from the backend routes.
 *
 * Wave-A reality: the python gate is not yet reachable from a Cloudflare Worker
 * without a worker-bindings sidecar or a `wrangler dev` uv/python bridge, so
 * today this module:
 *   1. documents every route that MUST call the safety gate, and
 *   2. provides `safetyCallSites()` so later waves can enumerate and wire them.
 *
 * The contract response envelope is NEVER mutated here. When the python layer
 * is connected, a route should run `runSafetyGate(netlist)` first, and if the
 * verdict is `pass:false` return `fail("SAFETY_BLOCKED", summary)` instead of
 * the stub result. Search for `[D1-safety]` markers in `routes/*.ts`.
 */

import { Netlist } from "./types";

/**
 * Contract PASS/FAIL envelope the python gate returns. Mirrors
 * `run_safety()` in pcbgenius-safety/__init__.py.
 */
export interface SafetyVerdict {
  version: string;
  pass: boolean;
  refused: boolean;
  gates: {
    allowlist: { pass: boolean; violations: SafetyViolation[]; summary: string };
    constraints: { pass: boolean; violations: SafetyViolation[]; summary: string };
    refusals: { refuse: boolean; reason: string | null; violations: SafetyViolation[] };
  };
  violations: SafetyViolation[];
}

export interface SafetyViolation {
  rule: string;
  severity: "error" | "warning" | "info";
  location: string;
  message: string;
}

/** Routes that MUST run the safety gate before executing their contract handler. */
export type SafetyCallSite = {
  route: string;
  tool: string;
  gate: "allowlist" | "constraints" | "refusals" | "all";
  /** Why this route needs the gate. */
  rationale: string;
};

export const SAFETY_CALL_SITES: SafetyCallSite[] = [
  {
    route: "/run_erc",
    tool: "run_erc",
    gate: "all",
    rationale: "FK electrical rules check must only run on an allowlisted, non-refused design.",
  },
  {
    route: "/run_drc",
    tool: "run_drc",
    gate: "all",
    rationale: "Design rules check only meaningful for designs that pass safety gates 1–3.",
  },
  {
    route: "/run_auto_layout",
    tool: "run_auto_layout",
    gate: "all",
    rationale: "Do not auto-place/route an unsafe or ambiguous design.",
  },
  {
    route: "/check_fab_rules",
    tool: "check_fab_rules",
    gate: "allowlist",
    rationale: "A non-allowlisted package should not reach fab-cost estimation.",
  },
  {
    route: "/export_gerber",
    tool: "export_gerber",
    gate: "all",
    rationale: "Never export manufacturing files for a refused/unsafe design.",
  },
  {
    route: "/generate_firmware",
    tool: "generate_firmware",
    gate: "refusals",
    rationale: "Refuse to emit firmware for an ambiguous/corrupt design.",
  },
];

/** Enumerate the safety call sites (for docs, tests, wiring utils). */
export function safetyCallSites(): SafetyCallSite[] {
  return SAFETY_CALL_SITES;
}

/**
 * WIRING FOR LATER WAVES — STAND-IN.
 * When the python `run_safety()` bridge is available, implement this to invoke
 * it (via a Worker binding, a `command` POST to a python microservice, or a
 * bundled wasm reimplementation) and return its real verdict. Until then the
 * backend stubs CONTINUE to return contract-shaped results, and routes marked
 * with `[D1-safety]` short-circuit their stub when this returns pass:false.
 *
 * This form is kept so the call-site shape is an easy find/replace target.
 */
export async function runSafetyGate(_netlist: Netlist): Promise<SafetyVerdict> {
  // D1-safety: not yet bridged — always pass so Wave-A stubs keep working.
  return {
    version: "1.0.0",
    pass: true,
    refused: false,
    gates: {
      allowlist: { pass: true, violations: [], summary: "ALLOWLIST PASS — bridge not wired." },
      constraints: { pass: true, violations: [], summary: "CONSTRAINTS PASS — bridge not wired." },
      refusals: { refuse: false, reason: null, violations: [] },
    },
    violations: [],
  };
}

/** True once the python bridge is connected (flip when wired). */
export function safetyBridgeWired(): boolean {
  return false;
}