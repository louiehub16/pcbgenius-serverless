import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { AutoLayoutArgsSchema } from "../validate";
import { stubAutoLayout } from "../stubs";
import { ok } from "../helpers";

/**
 * run_auto_layout — Deterministic placement + routing (Freerouting). Model sets strategy/constraints only.
 * Contract: arguments { netlist, placement_hints, routing_rules },
 *           returns { routed, drc_pass, unrouted_nets, layout_ref }
 * STUB: reports unrouted nets for any 'clock'-class net; layout_ref is a stub URI.
 *
 * [D1-safety call site] Do not auto-place/route an unsafe or ambiguous design —
 * wire `await runSafetyGate(netlist)` and refuse to layout when pass:false.
 */
export const autoLayoutRoute = new Hono();

autoLayoutRoute.post("/", zValidator("json", AutoLayoutArgsSchema), (c) => {
  const { netlist } = c.req.valid("json");
  return c.json(ok(stubAutoLayout(netlist)));
});
