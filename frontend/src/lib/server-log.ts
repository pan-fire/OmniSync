// The web UI server's own events, as structured lines on stdout (what
// `docker compose logs frontend` shows): proxy refusals (Host, Origin,
// Content-Type), logins (success, failure, throttling), logouts, and
// problems with the login settings.
//
// One JSON object per line, in the shape of the backend's
// OMNISYNC_LOG_FORMAT=json lines, so one log shipper parses both:
//   {"ts":"2026-10-04T09:50:30.437Z","level":"WARNING","logger":"web.auth",
//    "msg":"Failed login","fields":{"event":"auth.login_failed","client":"192.0.2.7"}}
//
// These events stay here; they are not forwarded to the backend's log (see
// docs/gem/operations.md, "Logs"). Never pass a password, a session id, a
// cookie or a token: field names that look like one are replaced by ***,
// and string values are masked like the backend masks its lines. Each event
// type is limited to EVENT_BURST lines per minute, so a client cannot flood
// the log with refused requests; the next line says how many were dropped.

/** Every event this server logs. */
export type ServerEvent =
  | 'proxy.refused'
  | 'auth.login'
  | 'auth.login_failed'
  | 'auth.login_throttled'
  | 'auth.login_error'
  | 'auth.logout'
  | 'auth.config';

export type ServerLogLevel = 'INFO' | 'WARNING' | 'ERROR';

type FieldValue = string | number | boolean | null | undefined;

const LOGGERS: Record<ServerEvent, string> = {
  'proxy.refused':        'web.proxy',
  'auth.login':           'web.auth',
  'auth.login_failed':    'web.auth',
  'auth.login_throttled': 'web.auth',
  'auth.login_error':     'web.auth',
  'auth.logout':          'web.auth',
  'auth.config':          'web.auth',
};

export const MAX_VALUE_LENGTH = 200;
export const EVENT_BURST = 20;
const EVENT_WINDOW_MS = 60_000;
const MASK = '***';

// Field names whose values are never written.
const SECRET_NAME = /pass|secret|token|session|cookie|authori[sz]ation|key|credential/i;

// The shapes the backend masks too (backend/logging_setup.py).
const SECRET_PATTERNS: Array<[RegExp, string]> = [
  [/\b(bearer)(\s+)[A-Za-z0-9._~+/=-]{4,}/gi, `$1$2${MASK}`],
  [/(["'](?:[A-Za-z0-9]+[_-])*(?:password\d?|passwd|pass|passphrase|secret|key|token|session|cookie)["']\s*:\s*)("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/gi, `$1"${MASK}"`],
  [/(?<![A-Za-z0-9_-])((?:[A-Za-z0-9]+[_-])*(?:password\d?|passwd|pass|passphrase|secret|key|token|session|cookie))(\s*=\s*)(?![\s{[])[^\s,;&'"}\])]+/gi, `$1$2${MASK}`],
  [/\b([a-z][a-z0-9+.-]*:\/\/[^\s:/@]*):([^\s@/]+)@/gi, `$1:${MASK}@`],
];

/** `text` with credentials replaced by ***. */
export function maskSecrets (text: string): string {
  return SECRET_PATTERNS.reduce((out, [pattern, replacement]) => out.replace(pattern, replacement), text);
}

function clean (value: FieldValue): FieldValue {
  if (typeof value !== 'string') return value;
  // eslint-disable-next-line no-control-regex
  const text = maskSecrets(value.replace(/[\u0000-\u001f\u007f]/g, '?'));
  return text.length > MAX_VALUE_LENGTH ? `${text.slice(0, MAX_VALUE_LENGTH - 1)}…` : text;
}

type Budget = { windowStart: number; count: number; dropped: number };

const STATE_KEY = Symbol.for('omnisync.serverLog');

function budgets (): Map<ServerEvent, Budget> {
  const holder = globalThis as unknown as Record<symbol, Map<ServerEvent, Budget> | undefined>;
  holder[STATE_KEY] ??= new Map();
  return holder[STATE_KEY];
}

/** Forget the rate limits (tests). */
export function resetServerLog (): void {
  budgets().clear();
}

/**
 * Write one event line to stdout. Returns false when the event's rate
 * limit dropped it.
 */
export function logServerEvent (
  level: ServerLogLevel,
  event: ServerEvent,
  msg: string,
  fields: Record<string, FieldValue> = {},
  now: number = Date.now()
): boolean {
  const table = budgets();
  let budget = table.get(event);
  if (!budget || now - budget.windowStart >= EVENT_WINDOW_MS) {
    budget = { windowStart: now, count: 0, dropped: budget?.dropped ?? 0 };
    table.set(event, budget);
  }
  if (budget.count >= EVENT_BURST) {
    budget.dropped += 1;
    return false;
  }
  budget.count += 1;

  const out: Record<string, FieldValue> = { event };
  for (const [name, value] of Object.entries(fields)) {
    if (value === undefined) continue;
    out[name] = SECRET_NAME.test(name) ? MASK : clean(value);
  }
  if (budget.dropped > 0) {
    out.dropped = budget.dropped;
    budget.dropped = 0;
  }
  const line = {
    ts:     new Date(now).toISOString(),
    level,
    logger: LOGGERS[event],
    msg:    clean(msg),
    fields: out,
  };
  console.log(JSON.stringify(line));
  return true;
}
