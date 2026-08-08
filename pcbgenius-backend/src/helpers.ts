/**
 * PCBGenius — Response envelope helpers.
 *
 * Every endpoint returns a consistent envelope:
 *   success: { ok: true,  data: <contract return shape> }
 *   error:   { ok: false, error: { code, message, details? } }
 * The frontend/desktop clients unwrap `ok` first, then `data` / `error`.
 */

export interface ErrorEnvelope {
  ok: false;
  error: {
    code: string;
    message: string;
    details?: unknown;
  };
}

export function ok<T>(data: T): { ok: true; data: T } {
  return { ok: true, data };
}

export function fail(code: string, message: string, details?: unknown): ErrorEnvelope {
  const error: ErrorEnvelope["error"] = { code, message };
  if (details !== undefined) error.details = details;
  return { ok: false, error };
}
