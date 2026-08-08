import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { FabRulesArgsSchema } from "../validate";
import { stubFabRules } from "../stubs";
import { ok } from "../helpers";

/**
 * check_fab_rules — Validate design against a fab house's manufacturing capabilities.
 * Contract: arguments { netlist, fab('jlcpcb'|'pcbway') }, returns { fabricable, violations:[{rule,message}], est_cost_usd }
 * STUB: fabricable if components exist; cost is a flat estimate.
 *
 * [D1-safety call site] A non-allowlisted package should not reach fab-cost
 * estimation — wire `await runSafetyGate(netlist)` and block non-allowlisted parts.
 */
export const fabRulesRoute = new Hono();

fabRulesRoute.post("/", zValidator("json", FabRulesArgsSchema), (c) => {
  const { netlist, fab } = c.req.valid("json");
  return c.json(ok(stubFabRules(netlist, fab)));
});
