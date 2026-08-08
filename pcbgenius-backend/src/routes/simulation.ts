import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { SimulationArgsSchema } from "../validate";
import { stubSimulation } from "../stubs";
import { ok } from "../helpers";

/**
 * run_simulation — Ngspice simulation. Model configures + interprets; Ngspice computes.
 * Contract: arguments { netlist, sim_type('op'|'dc'|'ac'|'tran'), stimulus, test_points },
 *           returns { converged, measurements:{net:{voltage,current,ripple}}, waveforms_ref }
 * STUB: returns deterministic fake measurements; waveforms_ref always null until real Ngspice.
 */
export const simulationRoute = new Hono();

simulationRoute.post("/", zValidator("json", SimulationArgsSchema), (c) => {
  const { netlist, sim_type, test_points } = c.req.valid("json");
  return c.json(ok(stubSimulation(netlist, sim_type, test_points)));
});
