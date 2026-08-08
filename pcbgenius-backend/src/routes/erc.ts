import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { ErcArgsSchema } from "../validate";
import { stubErc } from "../stubs";
import { ok } from "../helpers";

/**
 * run_erc — Run KiCad electrical rules check on the current schematic.
 * Contract: arguments { netlist }, returns { pass, violations:[{rule,severity,pins,message}] }
 * STUB: always passes unless the netlist is malformed (validation enforced by zod).
 */
export const ercRoute = new Hono();

ercRoute.post("/", zValidator("json", ErcArgsSchema), (c) => {
  const { netlist } = c.req.valid("json");
  return c.json(ok(stubErc(netlist)));
});
