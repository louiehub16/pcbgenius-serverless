/**
 * PCBGenius — Netlist types mirroring FROZEN INTERFACE CONTRACT v1.0.0 (Section 1).
 * These match the backend (pcbgenius-backend/src/types.ts) so the canvas renders
 * exactly what the model emits and what the backend validates.
 */

export interface Pin {
  number: string;
  name: string;
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
  ref: string;
  type: ComponentType;
  value: string;
  package: string;
  mpn: string | null;
  pins: Pin[];
  properties: Record<string, unknown>;
}

export type NetClass = "power" | "ground" | "signal" | "clock" | "analog" | "digital";

export interface Net {
  name: string;
  pins: string[]; // "ref.pin" strings
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
  schema_version: string;
  metadata: NetlistMetadata;
  components: Component[];
  nets: Net[];
}

/** Frontend-only wrapper: a violation shown in the stub violations panel. */
export interface Violation {
  rule: string;
  severity: "error" | "warning" | "info";
  source: string;
  message: string;
}
