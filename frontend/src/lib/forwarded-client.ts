import { createHmac } from 'node:crypto';

/**
 * The browser's address, vouched for to the backend (for its audit trail).
 *
 * Every /api request reaches the backend from this server, so the backend
 * sees this container as the client. The proxy therefore sends the address
 * it saw the browser at (clientAddress in lib/auth/gate.ts) in this header:
 *
 *   v1;<address>;<unix seconds>;<hex HMAC-SHA256 of "v1\n<address>\n<seconds>">
 *
 * keyed with the API token, which only this server and the backend hold.
 * The backend (backend/api/forwarded_client.py) believes it only with the
 * right token, a matching HMAC, at most 60 s old, and a plain IP address,
 * and uses it for the audit trail only, never for authentication or
 * throttling. A value the browser sends itself is always replaced.
 */
export const FORWARDED_CLIENT_HEADER = 'x-omnisync-client';

/** The header value for `address` at `nowMs` (milliseconds since the epoch). */
export function signClientAddress (token: string, address: string, nowMs: number = Date.now()): string {
  const seconds = Math.floor(nowMs / 1000);
  const mac = createHmac('sha256', token).update(`v1\n${address}\n${seconds}`).digest('hex');
  return `v1;${address};${seconds};${mac}`;
}
