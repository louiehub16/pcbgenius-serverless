import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { DrcArgsSchema } from "../validate";
import { stubDrc } from "../stubs";
import { ok, fail } from "../helpers";
import { runSafetyGate } from "../safety";

/**
 * run_drc — Run KiCad design rules check on the current layout.
 * Contract: arguments { netlist, layout }, returns { pass, violations:[{rule,severity,location,message}] }
 * STUB: always passes.
 *
 * [D1-safety call site] DRC only meaningful for designs past safety gates 1–3.
 */
export const drcRoute = new Hono();

drcRoute.post("/", zValidator("json", DrcArgsSchema), async (c) => {
  const { netlist } = c.req.valid("json");

  // [D1-safety] gate 1–3 check before any DRC work.
  const verdict = await runSafetyGate(netlist);
  if (!verdict.pass) {
    return c.json(
      fail("SAFETY_BLOCKED", verdict.gates.refusals.reason ?? "Design blocked by safety layer", verdict.violations)
    );
  }

  return c.json(ok(stubDrc()));
});
