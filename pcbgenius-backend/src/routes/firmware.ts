import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { FirmwareArgsSchema } from "../validate";
import { stubFirmware } from "../stubs";
import { ok } from "../helpers";

/**
 * generate_firmware — Model emits firmware source for the design's MCU (C/C++).
 * Contract: arguments { netlist, mcu, functionality }, returns { language, source, build_notes }
 * STUB: emits a minimal placeholder source; real model-generated firmware lands in a later wave.
 *
 * [D1-safety call site] Refuse to emit firmware for an ambiguous/corrupt design —
 * wire `await runSafetyGate(netlist)` (refusals gate) before generating firmware.
 */
export const firmwareRoute = new Hono();

firmwareRoute.post("/", zValidator("json", FirmwareArgsSchema), (c) => {
  const { mcu, functionality } = c.req.valid("json");
  return c.json(ok(stubFirmware(mcu, functionality)));
});
