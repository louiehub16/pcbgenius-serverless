import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { ComponentDbArgsSchema } from "../validate";
import { stubComponentDb } from "../stubs";
import { ok } from "../helpers";

/**
 * query_component_db — Search the component knowledge base (RAG) for parts/specs/stock.
 * Contract: arguments { query, limit(default 5) }, returns { results:[{mpn,manufacturer,specs,stock,price,package}] }
 * STUB: returns hardcoded catalog entries; real RAG index lands in a later wave.
 */
export const componentDbRoute = new Hono();

componentDbRoute.post("/", zValidator("json", ComponentDbArgsSchema), (c) => {
  const { query, limit } = c.req.valid("json");
  return c.json(ok(stubComponentDb(query, limit)));
});
