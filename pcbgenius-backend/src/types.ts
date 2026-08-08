/**
 * PCBGenius — Shared TypeScript types mirroring the FROZEN INTERFACE CONTRACT v1.0.0.
 * These shapes are the canonical contract and MUST NOT change (change = retrain +$32-53).
 * See PCBGenius_FROZEN_Contract_v1.0_2026-07-24.yaml, Section 1 (netlist schema).
 */

/** Reference designator + pin, e.g. "R1.1" or "LED1.A" (as used in nets[].pins). */
export type RefPin = string;

export interface Pin {
  /** Pin number/name as a string, e.g. "1", "A1". */
  number: string;
  /** Pin function, e.g. "VCC", "GND", "OUT", "GPIO4". */
  name: string;
  /** Name of the net this pin connects to (matches a Net.name). */
  net: string;
}

export type ComponentType =
  | "resistor"
  | "capacitor"
  | "inductor"
  | "diode"
  | "led"
  | "transistor"
  | "ic"
  | "connector"
  | "power"
  | "crystal"
  | "switch";

export interface Component {
  /** Reference designator, e.g. "R1", "C3", "U1" — unique across the design. */
  ref: string;
  type: ComponentType;
  /** e.g. "10k", "100nF", "LM358", "ESP32-WROOM-32". */
  value: string;
  /** e.g. "0805", "SOT-23", "DIP-8", "QFN-48". */
  package: string;
  /** Manufacturer part number, or null for generic parts. */
  mpn: string | null;
  pins: Pin[];
  /** Optional extra attributes: tolerance, voltage_rating, power, ... */
  properties: Record<string, unknown>;
}

export type NetClass = "power" | "ground" | "signal" | "clock" | "analog" | "digital";

export interface Net {
  name: string;
  /** List of "ref.pin" strings, e.g. ["R1.1", "LED1.A"]. */
  pins: RefPin[];
  class: NetClass;
}

export interface NetlistMetadata {
  design_name: string;
  description: string;
  board_layers: number;
  created_by: string;
  target_fab: "jlcpcb" | "pcbway" | null;
}

export interface Netlist {
  schema_version: string; // MUST equal "1.0.0"
  metadata: NetlistMetadata;
  components: Component[];
  nets: Net[];
}

/* ── Return shapes for each tool call (contract Section 2) ───────────────── */

export type Severity = "error" | "warning" | "info";

export interface ErcViolation {
  rule: string;
  severity: Severity;
  pins: RefPin[];
  message: string;
}

export interface ErcResult {
  pass: boolean;
  violations: ErcViolation[];
}

export interface DrcViolation {
  rule: string;
  severity: Severity;
  location: string;
  message: string;
}

export interface DrcResult {
  pass: boolean;
  violations: DrcViolation[];
}

export interface Measurement {
  voltage: number | null;
  current: number | null;
  ripple: number | null;
}

export interface SimulationResult {
  converged: boolean;
  measurements: Record<string, Measurement>;
  waveforms_ref: string | null;
}

export interface AutoLayoutResult {
  routed: boolean;
  drc_pass: boolean;
  unrouted_nets: string[];
  layout_ref: string;
}

export interface ComponentDbHit {
  mpn: string;
  manufacturer: string;
  specs: Record<string, unknown>;
  stock: number;
  price: number;
  package: string;
}

export interface ComponentDbResult {
  results: ComponentDbHit[];
}

export interface DatasheetResult {
  mpn: string;
  specs: Record<string, unknown>;
}

export interface FabViolation {
  rule: string;
  message: string;
}

export interface FabRulesResult {
  fabricable: boolean;
  violations: FabViolation[];
  est_cost_usd: number | null;
}

export interface ExportGerberResult {
  success: boolean;
  files: string[];
  package_ref: string;
}

export interface FirmwareResult {
  language: string;
  source: string;
  build_notes: string;
}

export interface ApprovalResult {
  approved: boolean;
  user_note: string | null;
}
