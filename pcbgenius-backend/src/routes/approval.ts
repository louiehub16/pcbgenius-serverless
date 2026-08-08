import { Hono } from "hono";
import { zValidator } from "@hono/zod-validator";
import { ApprovalArgsSchema } from "../validate";
import { stubApproval } from "../stubs";
import { ok } from "../helpers";

/**
 * request_approval — Pause and ask the human to approve a consequential action.
 * Contract: arguments { action, summary, risk('low'|'medium'|'high') }, returns { approved, user_note }
 * STUB: approves immediately. Real flow pauses and surfaces a yes/no to the human in the
 * desktop app before any consequential action executes (contract Section 3 approval_required_for).
 */
export const approvalRoute = new Hono();

approvalRoute.post("/", zValidator("json", ApprovalArgsSchema), (c) => {
  const { action, risk } = c.req.valid("json");
  return c.json(ok(stubApproval(action, risk)));
});
