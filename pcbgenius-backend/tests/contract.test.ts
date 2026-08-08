import { describe, it, expect } from "vitest";
import app from "../src/index";
import { sampleNetlist } from "./fixtures/netlist";

/**
 * Helper: POST a JSON body to a route and return parsed JSON.
 * Uses Hono's app.request() so no live server is needed.
 */
async function post(path: string, body: unknown): Promise<{ status: number; json: any }> {
  const res = await app.request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { status: res.status, json: await res.json() };
}

describe("PCBGenius backend gateway — contract endpoints", () => {
  it("health check returns ok", async () => {
    const res = await app.request("/");
    const json = await res.json();
    expect(json.ok).toBe(true);
    expect(json.service).toBe("pcbgenius-backend");
  });

  it("run_erc returns { pass, violations } shape", async () => {
    const { status, json } = await post("/run_erc", { netlist: sampleNetlist });
    expect(status).toBe(200);
    expect(json.ok).toBe(true);
    expect(json.data).toHaveProperty("pass");
    expect(Array.isArray(json.data.violations)).toBe(true);
  });

  it("run_drc returns { pass, violations } shape", async () => {
    const { status, json } = await post("/run_drc", { netlist: sampleNetlist, layout: null });
    expect(status).toBe(200);
    expect(json.ok).toBe(true);
    expect(json.data).toHaveProperty("pass");
    expect(Array.isArray(json.data.violations)).toBe(true);
  });

  it("run_simulation returns contract shape with measurements", async () => {
    const { status, json } = await post("/run_simulation", {
      netlist: sampleNetlist,
      sim_type: "tran",
      stimulus: { vin: "5V", freq: "1kHz" },
      test_points: ["VCC_3V3", "VIN"],
    });
    expect(status).toBe(200);
    expect(json.ok).toBe(true);
    expect(json.data.converged).toBe(true);
    expect(json.data.measurements["VCC_3V3"]).toHaveProperty("voltage");
    expect(json.data.measurements["VCC_3V3"]).toHaveProperty("current");
    expect(json.data.measurements["VCC_3V3"]).toHaveProperty("ripple");
    expect(json.data).toHaveProperty("waveforms_ref");
  });

  it("run_auto_layout returns contract shape", async () => {
    const { status, json } = await post("/run_auto_layout", {
      netlist: sampleNetlist,
      placement_hints: { keep_near: [["U1", "C1"]] },
      routing_rules: { power_width_mm: 0.5, signal_width_mm: 0.25 },
    });
    expect(status).toBe(200);
    expect(json.ok).toBe(true);
    expect(json.data).toHaveProperty("routed");
    expect(json.data).toHaveProperty("drc_pass");
    expect(Array.isArray(json.data.unrouted_nets)).toBe(true);
    expect(json.data).toHaveProperty("layout_ref");
  });

  it("query_component_db returns results array", async () => {
    const { status, json } = await post("/query_component_db", { query: "10k resistor 0805", limit: 5 });
    expect(status).toBe(200);
    expect(json.ok).toBe(true);
    expect(json.data.results.length).toBeLessThanOrEqual(5);
    expect(json.data.results[0]).toHaveProperty("mpn");
    expect(json.data.results[0]).toHaveProperty("stock");
  });

  it("get_datasheet_spec returns { mpn, specs }", async () => {
    const { status, json } = await post("/get_datasheet_spec", { mpn: "LM358DR", fields: ["pinout"] });
    expect(status).toBe(200);
    expect(json.ok).toBe(true);
    expect(json.data.mpn).toBe("LM358DR");
    expect(json.data.specs).toHaveProperty("pinout");
  });

  it("check_fab_rules returns contract shape for jlcpcb", async () => {
    const { status, json } = await post("/check_fab_rules", { netlist: sampleNetlist, fab: "jlcpcb" });
    expect(status).toBe(200);
    expect(json.ok).toBe(true);
    expect(json.data).toHaveProperty("fabricable");
    expect(Array.isArray(json.data.violations)).toBe(true);
    expect(json.data).toHaveProperty("est_cost_usd");
  });

  it("export_gerber returns files + package_ref", async () => {
    const { status, json } = await post("/export_gerber", {
      netlist: sampleNetlist,
      layout: {},
      fab: "jlcpcb",
    });
    expect(status).toBe(200);
    expect(json.ok).toBe(true);
    expect(json.data.success).toBe(true);
    expect(Array.isArray(json.data.files)).toBe(true);
    expect(json.data.files.some((f: string) => f.endsWith(".gbr"))).toBe(true);
    expect(json.data).toHaveProperty("package_ref");
  });

  it("generate_firmware returns { language, source, build_notes }", async () => {
    const { status, json } = await post("/generate_firmware", {
      netlist: sampleNetlist,
      mcu: "ESP32",
      functionality: "Read ADC and toggle LED",
    });
    expect(status).toBe(200);
    expect(json.ok).toBe(true);
    expect(typeof json.data.language).toBe("string");
    expect(typeof json.data.source).toBe("string");
    expect(typeof json.data.build_notes).toBe("string");
  });

  it("request_approval returns { approved, user_note }", async () => {
    const { status, json } = await post("/request_approval", {
      action: "export gerber and send to fab",
      summary: "Send the board to JLCPCB for fabrication.",
      risk: "high",
    });
    expect(status).toBe(200);
    expect(json.ok).toBe(true);
    expect(json.data).toHaveProperty("approved");
    expect(json.data).toHaveProperty("user_note");
  });
});

describe("validation + envelope errors", () => {
  it("returns 400 envelope on invalid body (missing netlist)", async () => {
    const { status, json } = await post("/run_erc", {});
    expect(status).toBe(400);
    expect(json.ok).toBe(false);
    expect(json.error).toHaveProperty("code");
    expect(json.error).toHaveProperty("message");
  });

  it("returns 404 envelope for unknown route", async () => {
    const res = await app.request("/nope", { method: "POST" });
    const json = await res.json();
    expect(res.status).toBe(404);
    expect(json.ok).toBe(false);
    expect(json.error.code).toBe("NOT_FOUND");
  });

  it("rejects bad sim_type", async () => {
    const { status, json } = await post("/run_simulation", {
      netlist: sampleNetlist,
      sim_type: "quantum",
    });
    expect(status).toBe(400);
    expect(json.ok).toBe(false);
  });
});
