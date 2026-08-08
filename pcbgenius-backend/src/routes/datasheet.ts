import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { DatasheetArgsSchema } from "../validate";
import { stubDatasheet } from "../stubs";
import { ok } from "../helpers";

/**
 * get_datasheet_spec — Retrieve a specific spec from a part's extracted datasheet data.
 * Contract: arguments { mpn, fields }, returns { mpn, specs }
 * STUB: returns canned spec blocks; datasheet extraction pipeline lands in a later wave.
 */
export const datasheetRoute = new Hono();

datasheetRoute.post("/", zValidator("json", DatasheetArgsSchema), (c) => {
  const { mpn, fields } = c.req.valid("json");
  return c.json(ok(stubDatasheet(mpn, fields)));
});
