import { Netlist } from "../src/types";

/**
 * A minimal contract-valid netlist used across tests — matches the shape in
 * Section 1 of the frozen contract (schema_version, metadata, components, nets).
 */
export const sampleNetlist: Netlist = {
  schema_version: "1.0.0",
  metadata: {
    design_name: "test_ldo",
    description: "Test 5V to 3.3V regulator",
    board_layers: 2,
    created_by: "pcbgenius",
    target_fab: null,
  },
  components: [
    {
      ref: "U1",
      type: "ic",
      value: "AMS1117-3.3",
      package: "SOT-223",
      mpn: "AMS1117-3.3",
      pins: [
        { number: "1", name: "VIN", net: "VIN" },
        { number: "2", name: "GND", net: "GND" },
        { number: "3", name: "VOUT", net: "VCC_3V3" },
      ],
      properties: {},
    },
    {
      ref: "C1",
      type: "capacitor",
      value: "10uF",
      package: "0805",
      mpn: null,
      pins: [
        { number: "1", name: "1", net: "VIN" },
        { number: "2", name: "2", net: "GND" },
      ],
      properties: {},
    },
  ],
  nets: [
    { name: "VIN", pins: ["U1.VIN", "C1.1"], class: "power" },
    { name: "GND", pins: ["U1.GND", "C1.2"], class: "ground" },
    { name: "VCC_3V3", pins: ["U1.VOUT"], class: "power" },
  ],
};
