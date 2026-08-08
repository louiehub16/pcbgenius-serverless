import { Netlist } from "./contractTypes";

/**
 * A valid starter netlist so the canvas opens with something on screen.
 * Shape matches the frozen contract exactly (a simple LED-blinker using an
 * ESP32 dev board + resistor + LED + decoupling cap).
 */
export const sampleNetlist: Netlist = {
  schema_version: "1.0.0",
  metadata: {
    design_name: "led_blinker",
    description: "Blink an LED from an ESP32 GPIO pin",
    board_layers: 2,
    created_by: "pcbgenius",
    target_fab: null,
  },
  components: [
    {
      ref: "U1",
      type: "ic",
      value: "ESP32-WROOM-32",
      package: "Module",
      mpn: "ESP32-WROOM-32",
      pins: [
        { number: "3V3", name: "3V3", net: "VCC_3V3" },
        { number: "GND", name: "GND", net: "GND" },
        { number: "GPIO2", name: "GPIO2", net: "LED_CTRL" },
      ],
      properties: { voltage_rating: "3.3V" },
    },
    {
      ref: "R1",
      type: "resistor",
      value: "330",
      package: "0805",
      mpn: null,
      pins: [
        { number: "1", name: "1", net: "LED_CTRL" },
        { number: "2", name: "2", net: "NET_LED" },
      ],
      properties: { tolerance: "5%", power: "0.125W" },
    },
    {
      ref: "LED1",
      type: "led",
      value: "Red",
      package: "0805",
      mpn: null,
      pins: [
        { number: "A", name: "A", net: "NET_LED" },
        { number: "K", name: "K", net: "GND" },
      ],
      properties: {},
    },
    {
      ref: "C1",
      type: "capacitor",
      value: "100nF",
      package: "0603",
      mpn: null,
      pins: [
        { number: "1", name: "1", net: "VCC_3V3" },
        { number: "2", name: "2", net: "GND" },
      ],
      properties: { voltage_rating: "16V" },
    },
  ],
  nets: [
    { name: "VCC_3V3", pins: ["U1.3V3", "C1.1"], class: "power" },
    { name: "GND", pins: ["U1.GND", "LED1.K", "C1.2"], class: "ground" },
    { name: "LED_CTRL", pins: ["U1.GPIO2", "R1.1"], class: "signal" },
    { name: "NET_LED", pins: ["R1.2", "LED1.A"], class: "signal" },
  ],
};
