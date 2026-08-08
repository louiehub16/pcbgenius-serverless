import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { DrcArgsSchema } from "../validate";
import { stubDrc } from "../stubs";
import { ok } from "../helpers";

/**
 * run_drc — Run KiCad design rules check on the current layout.
 * Contract: arguments { netlist, layout }, returns { pass, violations:[{rule,severity,location,message}] }
 * STUB: always passes.
 */
export const drcRoute = new Hono();

drcRoute.post("/", zValidator("json", DrcArgsSchema), (c) => {
  return c.json(ok(stubDrc()));
});
