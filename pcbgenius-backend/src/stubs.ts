import { Netlist, RefPin } from "./types";

/**
 * PCBGenius — Stub data generators.
 *
 * Every endpoint in Wave-A returns a STUB response that matches the contract's
 * documented `returns` shape EXACTLY. These are clearly marked as placeholder
 * data so they are easy to spot and replace in later waves when the real
 * integrations land (KiCad CLI, Ngspice, Freerouting, component RAG, fab APIs,
 * etc.). The SHAPES are final (contract-frozen); only the VALUES are fake.
 */

/** A tiny deterministic pseudo-random source so stubs are stable across calls. */
function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function pick<T>(arr: readonly T[], seed: number): T {
  return arr[seed % arr.length];
}

/** Basic sanity: must have at least one ground net (contract validation rule). */
export function hasGroundNet(netlist: Netlist): boolean {
  return netlist.nets.some((n) => n.class === "ground");
}

/** Collect every "ref.pin" that the netlist's nets actually reference. */
export function allRefPins(netlist: Netlist): RefPin[] {
  return netlist.nets.flatMap((n) => n.pins);
}

/* ── ERC stub ──────────────────────────────────────────────────────────── */
export interface ErcStubResult {
  pass: boolean;
  violations: Array<{ rule: string; severity: string; pins: RefPin[]; message: string }>;
}

export function stubErc(netlist: Netlist, ok = true): ErcStubResult {
  if (ok) return { pass: true, violations: [] };
  const ground = hasGroundNet(netlist);
  return {
    pass: false,
    violations: [
      {
        rule: "ERC_PIN_NOT_CONNECTED",
        severity: "warning",
        pins: allRefPins(netlist).slice(0, 1),
        message: ground
          ? "STUB: one pin flagged as a warning (real ERC runs KiCad electrical rules)."
          : "STUB: no ground net found (contract requires one).",
      },
    ],
  };
}

/* ── DRC stub ──────────────────────────────────────────────────────────── */
export interface DrcStubResult {
  pass: boolean;
  violations: Array<{ rule: string; severity: string; location: string; message: string }>;
}

export function stubDrc(ok = true): DrcStubResult {
  if (ok) return { pass: true, violations: [] };
  return {
    pass: false,
    violations: [
      {
        rule: "DRC_SILKSCREEN_OVERLAP",
        severity: "error",
        location: "U1",
        message: "STUB: silkscreen overlaps pad (real DRC runs KiCad design rules).",
      },
    ],
  };
}

/* ── Simulation stub ───────────────────────────────────────────────────── */
export interface SimulationStubResult {
  converged: boolean;
  measurements: Record<string, { voltage: number | null; current: number | null; ripple: number | null }>;
  waveforms_ref: string | null;
}

export function stubSimulation(netlist: Netlist, simType: string, testPoints: string[]): SimulationStubResult {
  const seed = hash(netlist.metadata.design_name + simType);
  const measurements: SimulationStubResult["measurements"] = {};
  const points = testPoints.length ? testPoints : netlist.nets.slice(0, 3).map((n) => n.name);
  for (const tp of points) {
    const v = 1 + (seed % 40) / 10; // deterministic 1.0 – 5.0 V
    measurements[tp] = { voltage: v, current: v / 1000, ripple: simType === "tran" ? 0.02 : null };
  }
  return {
    converged: true,
    measurements,
    waveforms_ref: null, // STUB: real ngspice would emit a .raw/.png reference
  };
}

/* ── Auto-layout stub ──────────────────────────────────────────────────── */
export interface AutoLayoutStubResult {
  routed: boolean;
  drc_pass: boolean;
  unrouted_nets: string[];
  layout_ref: string;
}

export function stubAutoLayout(netlist: Netlist): AutoLayoutStubResult {
  const unrouted = netlist.nets.filter((n) => n.class === "clock").map((n) => n.name);
  return {
    routed: unrouted.length === 0,
    drc_pass: true,
    unrouted_nets: unrouted,
    layout_ref: "stub://layout/" + encodeURIComponent(netlist.metadata.design_name) + ".kicad_pcb",
  };
}

/* ── Component DB stub ─────────────────────────────────────────────────── */
export interface ComponentDbStubResult {
  results: Array<{
    mpn: string;
    manufacturer: string;
    specs: Record<string, unknown>;
    stock: number;
    price: number;
    package: string;
  }>;
}

export function stubComponentDb(query: string, limit: number): ComponentDbStubResult {
  const base = [
    { mpn: "RC0805FR-0710KL", manufacturer: "Yageo", specs: { resistance: "10k", tolerance: "1%" }, stock: 5400, price: 0.002, package: "0805" },
    { mpn: "CL21B104KBCNNNC", manufacturer: "Samsung", specs: { capacitance: "100nF", voltage: "50V" }, stock: 3200, price: 0.008, package: "0805" },
    { mpn: "LM358DR", manufacturer: "TI", specs: { type: "Dual Op-Amp" }, stock: 900, price: 0.21, package: "SOIC-8" },
  ];
  const seed = hash(query);
  const rotated = [...base.slice(seed % base.length), ...base.slice(0, seed % base.length)];
  return { results: rotated.slice(0, limit) };
}

/* ── Datasheet stub ────────────────────────────────────────────────────── */
export interface DatasheetStubResult {
  mpn: string;
  specs: Record<string, unknown>;
}

export function stubDatasheet(mpn: string, fields: string[]): DatasheetStubResult {
  const all: Record<string, unknown> = {
    absolute_maximum_ratings: { vin: "20V", vout: "20V", power: "1.5W" },
    pinout: ["VIN", "GND", "VOUT"],
    electrical_characteristics: { vdrop: "1.1V @ 1A", quiescent: "5mA" },
  };
  const specs: Record<string, unknown> = {};
  const wanted = fields.length ? fields : Object.keys(all);
  for (const f of wanted) if (f in all) specs[f] = all[f];
  return { mpn, specs };
}

/* ── Fab rules stub ────────────────────────────────────────────────────── */
export interface FabRulesStubResult {
  fabricable: boolean;
  violations: Array<{ rule: string; message: string }>;
  est_cost_usd: number | null;
}

export function stubFabRules(netlist: Netlist, fab: "jlcpcb" | "pcbway"): FabRulesStubResult {
  const minTrace = fab === "jlcpcb" ? 0.15 : 0.13; // mm minimum trace width
  const components = netlist.components.length;
  const violations: FabRulesStubResult["violations"] = [];
  if (components === 0) violations.push({ rule: "FAB_EMPTY_BOARD", message: "No components placed." });
  return {
    fabricable: violations.length === 0,
    violations,
    est_cost_usd: violations.length === 0 ? 2 + components * 0.05 : null,
  };
}

/* ── Export Gerber stub ────────────────────────────────────────────────── */
export interface ExportGerberStubResult {
  success: boolean;
  files: string[];
  package_ref: string;
}

export function stubExportGerber(netlist: Netlist, fab: string): ExportGerberStubResult {
  const name = netlist.metadata.design_name || "design";
  return {
    success: true,
    files: [
      `${name}-F.Cu.gbr`,
      `${name}-B.Cu.gbr`,
      `${name}-F.SilkS.gbr`,
      `${name}-Edge.Cuts.gbr`,
      `${name}.drl`,
    ],
    package_ref: `stub://gerber/${encodeURIComponent(name)}_${fab}.zip`,
  };
}

/* ── Firmware stub ─────────────────────────────────────────────────────── */
export interface FirmwareStubResult {
  language: string;
  source: string;
  build_notes: string;
}

export function stubFirmware(mcu: string, functionality: string): FirmwareStubResult {
  const isArduino = /(atmega|uno|nano)/i.test(mcu);
  const language = isArduino ? "C++ (Arduino)" : "C/C++ (ESP-IDF)";
  const source = [
    `// STUB firmware for ${mcu} — real source emitted by the fine-tuned model in later waves.`,
    `int main(void) {`,
    `  // functionality: ${functionality.replace(/\n/g, " ")}`,
    `  for (;;) { /* loop */ }`,
    `  return 0;`,
    `}`,
  ].join("\n");
  return { language, source, build_notes: `STUB: build with ${isArduino ? "arduino-cli" : "idf.py build"}` };
}

/* ── Approval stub ─────────────────────────────────────────────────────── */
export interface ApprovalStubResult {
  approved: boolean;
  user_note: string | null;
}

export function stubApproval(action: string, risk: string): ApprovalStubResult {
  // STUB: approval is always granted in simulation. In production the request
  // would pause and surface to the human in the desktop app for a yes/no.
  return { approved: true, user_note: `STUB: assumed approved for "${action}" (risk=${risk}).` };
}
