import type { ErrorResponse } from '@/types';

// The error envelope shared by the backend and this server's own /api and
// /auth answers (docs/api-errors.md):
//   { "detail": "<message>", "code": "<snake_case>", "details": { ... } }

export type ErrorDetails = Record<string, unknown>;

/** A parsed error answer. `code` is missing only for a body that is not an envelope. */
export type ParsedApiError = {
  message:  string;
  code?:    string;
  details?: ErrorDetails;
};

function isRecord (value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

/** Read an error answer's body; anything that is not an envelope falls back to the status. */
export function parseApiError (status: number, body: unknown): ParsedApiError {
  if (!isRecord(body) || typeof body.detail !== 'string' || !body.detail) {
    return { message: `Request failed (${status})` };
  }
  return {
    message: body.detail,
    code:    typeof body.code === 'string' ? body.code : undefined,
    details: isRecord(body.details) ? body.details : undefined,
  };
}

/** The body of an error answer this server sends itself. */
export function errorBody (code: string, message: string, details?: ErrorDetails): ErrorResponse {
  const body: ErrorResponse = { detail: message, code };
  if (details) {
    const present = Object.fromEntries(Object.entries(details).filter(([, v]) => v !== undefined));
    if (Object.keys(present).length > 0) body.details = present;
  }
  return body;
}

/** `details.retry_after` in seconds, when the answer carries a positive one. */
export function retryAfterSeconds (body: unknown): number | null {
  const seconds = parseApiError(0, body).details?.retry_after;
  return typeof seconds === 'number' && seconds > 0 ? seconds : null;
}
