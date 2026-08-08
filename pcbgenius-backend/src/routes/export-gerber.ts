import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { ExportGerberArgsSchema } from "../validate";
import { stubExportGerber } from "../stubs";
import { ok } from "../helpers";

/**
 * export_gerber — Export manufacturing files (Gerber/Drill) via KiCad CLI.
 * Contract: arguments { netlist, layout, fab }, returns { success, files, package_ref }
 * STUB: lists expected Gerber filenames; real KiCad CLI export lands in a later wave.
 * NOTE: contract Section 3 marks export_gerber as approval_required — the client should
 * call request_approval first (handled at the app layer, not enforced here).
 */
export const exportGerberRoute = new Hono();

exportGerberRoute.post("/", zValidator("json", ExportGerberArgsSchema), (c) => {
  const { netlist, fab } = c.req.valid("json");
  return c.json(ok(stubExportGerber(netlist, fab)));
});
