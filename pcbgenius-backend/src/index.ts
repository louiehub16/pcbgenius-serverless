import { Hono } from "hono";
import { cors } from "hono/cors";
import { logger } from "hono/logger";
import { fail } from "./helpers";

import { ercRoute } from "./routes/erc";
import { drcRoute } from "./routes/drc";
import { simulationRoute } from "./routes/simulation";
import { autoLayoutRoute } from "./routes/auto-layout";
import { componentDbRoute } from "./routes/component-db";
import { datasheetRoute } from "./routes/datasheet";
import { fabRulesRoute } from "./routes/fab-rules";
import { exportGerberRoute } from "./routes/export-gerber";
import { firmwareRoute } from "./routes/firmware";
import { approvalRoute } from "./routes/approval";

/**
 * PCBGenius — Wave-A Core Backend Gateway.
 *
 * A Hono app on Cloudflare Workers exposing the 10 FROZEN CONTRACT tool-call
 * endpoints (Section 1 + 2 of PCBGenius_FROZEN_Contract_v1.0_2026-07-24.yaml).
 *
 * Every route is a STUB returning the contract-documented `returns` shape so the
 * frontend/desktop/verification layers can build against the real interface now,
 * and the actual integrations (KiCad, Ngspice, Freerouting, RAG, fab APIs) plug
 * in later waves without touching the wire contract.
 */

const app = new Hono();

// ── Middleware ────────────────────────────────────────────────────────────

// Human-readable request logging in dev.
app.use("*", logger());

// CORS for the V1 frontend dev server (http://localhost:5173).
// Allowed origins could be extended for the desktop client in later waves.
app.use(
  "*",
  cors({
    origin: [process.env.CORS_ORIGIN ?? "http://localhost:5173"],
    allowMethods: ["GET", "POST", "OPTIONS"],
    allowHeaders: ["Content-Type", "Authorization"],
    maxAge: 86400,
  })
);

// Simple in-memory token-bucket rate limiter keyed by client IP.
// NOTE: in-memory on Workers is per-isolate; good enough for the V1 dev gate.
// Upgrade to a Workers KV/Durable Object limiter in later waves if needed.
const RATE_LIMIT_MAX = Number(process.env.RATE_LIMIT_MAX ?? 120);
const RATE_LIMIT_WINDOW_MS = Number(process.env.RATE_LIMIT_WINDOW_MS ?? 60_000);

interface Bucket {
  count: number;
  resetAt: number;
}
const buckets = new Map<string, Bucket>();

function rateLimited(ip: string): boolean {
  const now = Date.now();
  let b = buckets.get(ip);
  if (!b || now > b.resetAt) {
    b = { count: 0, resetAt: now + RATE_LIMIT_WINDOW_MS };
    buckets.set(ip, b);
  }
  b.count += 1;
  return b.count > RATE_LIMIT_MAX;
}

app.use("*", async (c, next) => {
  // The CF-Connecting-IP header is what Workers sees from Cloudflare's edge.
  const ip = c.req.header("cf-connecting-ip") ?? c.req.header("x-forwarded-for") ?? "unknown";
  if (rateLimited(ip)) {
    return c.json(fail("RATE_LIMITED", "Too many requests. Please slow down."), 429);
  }
  await next();
});

// ── Contract tool-call endpoints ──────────────────────────────────────────
// POST /run_erc, /run_drc, ... — path matches the tool-call `name` in Section 2
// exactly. The agent's <tool_call> JSON maps 1:1 onto these routes.

app.route("/run_erc", ercRoute);
app.route("/run_drc", drcRoute);
app.route("/run_simulation", simulationRoute);
app.route("/run_auto_layout", autoLayoutRoute);
app.route("/query_component_db", componentDbRoute);
app.route("/get_datasheet_spec", datasheetRoute);
app.route("/check_fab_rules", fabRulesRoute);
app.route("/export_gerber", exportGerberRoute);
app.route("/generate_firmware", firmwareRoute);
app.route("/request_approval", approvalRoute);

// ── Misc ──────────────────────────────────────────────────────────────────

// Health/liveness probe.
app.get("/", (c) => c.json({ ok: true, service: "pcbgenius-backend", contract: "1.0.0" }));

// Zod validation failures bubble up here as a friendly 400 instead of a raw 422.
app.onError((err, c) => {
  console.error("pcbgenius error:", err);
  return c.json(fail("INTERNAL_ERROR", "An unexpected error occurred."), 500);
});

app.notFound((c) => c.json(fail("NOT_FOUND", `No route for ${c.req.method} ${c.req.path}`), 404));

export default app;
