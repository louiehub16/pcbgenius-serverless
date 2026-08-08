import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { ErcArgsSchema } from "../validate";
import { stubErc } from "../stubs";
import { ok, fail } from "../helpers";
import { runSafetyGate } from "../safety";

/**
 * run_erc — Run KiCad electrical rules check on the current schematic.
 * Contract: arguments { netlist }, returns { pass, violations:[{rule,severity,pins,message}] }
 * STUB: always passes unless the netlist is malformed (validation enforced by zod).
 *
 * [D1-safety call site] ERC must only run on an allowlisted, non-refused design.
 * The safety gate runs FIRST; a blocking verdict short-circuits the stub handler.
 */
export const ercRoute = new Hono();

ercRoute.post("/", zValidator("json", ErcArgsSchema), async (c) => {
  const { netlist } = c.req.valid("json");

  // [D1-safety] gate 1–3 check before any ERC work.
  const verdict = await runSafetyGate(netlist);
  if (!verdict.pass) {
    return c.json(
      fail("SAFETY_BLOCKED", verdict.gates.refusals.reason ?? "Design blocked by safety layer", verdict.violations)
    );
  }

  return c.json(ok(stubErc(netlist)));
});
