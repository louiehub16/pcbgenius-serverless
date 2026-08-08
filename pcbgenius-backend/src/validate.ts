import { z } from "zod";

/**
 * PCBGenius — Zod schemas mirroring the FROZEN INTERFACE CONTRACT v1.0.0 (Section 1 + 2).
 * Every endpoint validates its request body against the exact argument shape
 * documented in the contract. These schemas ARE the contract, expressed as code.
 */

const COMPONENT_TYPES = [
  "resistor",
  "capacitor",
  "inductor",
  "diode",
  "led",
  "transistor",
  "ic",
  "connector",
  "power",
  "crystal",
  "switch",
] as const;

const NET_CLASSES = ["power", "ground", "signal", "clock", "analog", "digital"] as const;

export const PinSchema = z.object({
  number: z.string(),
  name: z.string(),
  net: z.string(),
});

export const ComponentSchema = z.object({
  ref: z.string(),
  type: z.enum(COMPONENT_TYPES),
  value: z.string(),
  package: z.string(),
  mpn: z.string().nullable(),
  pins: z.array(PinSchema),
  properties: z.record(z.string(), z.unknown()).default({}),
});

export const NetSchema = z.object({
  name: z.string(),
  pins: z.array(z.string()),
  class: z.enum(NET_CLASSES),
});

export const MetadataSchema = z.object({
  design_name: z.string(),
  description: z.string(),
  board_layers: z.number().int().default(2),
  created_by: z.string().default("pcbgenius"),
  target_fab: z.enum(["jlcpcb", "pcbway"]).nullable().default(null),
});

/** Full netlist — the model's primary output and the body of most tool calls. */
export const NetlistSchema = z.object({
  schema_version: z.string().default("1.0.0"),
  metadata: MetadataSchema,
  components: z.array(ComponentSchema),
  nets: z.array(NetSchema),
});

/* ── Per-tool argument schemas (contract Section 2, `arguments`) ────────── */

/** run_erc */
export const ErcArgsSchema = z.object({ netlist: NetlistSchema });

/** run_drc */
export const DrcArgsSchema = z.object({
  netlist: NetlistSchema,
  layout: z.unknown().nullable().default(null),
});

/** run_simulation */
export const SimulationArgsSchema = z.object({
  netlist: NetlistSchema,
  sim_type: z.enum(["op", "dc", "ac", "tran"]),
  stimulus: z.record(z.string(), z.unknown()).default({}),
  test_points: z.array(z.string()).default([]),
});

/** run_auto_layout */
export const AutoLayoutArgsSchema = z.object({
  netlist: NetlistSchema,
  placement_hints: z.record(z.string(), z.unknown()).default({}),
  routing_rules: z.record(z.string(), z.unknown()).default({}),
});

/** query_component_db */
export const ComponentDbArgsSchema = z.object({
  query: z.string(),
  limit: z.number().int().min(1).max(100).default(5),
});

/** get_datasheet_spec */
export const DatasheetArgsSchema = z.object({
  mpn: z.string(),
  fields: z.array(z.string()).default([]),
});

/** check_fab_rules */
export const FabRulesArgsSchema = z.object({
  netlist: NetlistSchema,
  fab: z.enum(["jlcpcb", "pcbway"]),
});

/** export_gerber */
export const ExportGerberArgsSchema = z.object({
  netlist: NetlistSchema,
  layout: z.unknown(),
  fab: z.string(),
});

/** generate_firmware */
export const FirmwareArgsSchema = z.object({
  netlist: NetlistSchema,
  mcu: z.string(),
  functionality: z.string(),
});

/** request_approval */
export const ApprovalArgsSchema = z.object({
  action: z.string(),
  summary: z.string(),
  risk: z.enum(["low", "medium", "high"]),
});
