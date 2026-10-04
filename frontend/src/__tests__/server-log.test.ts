import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EVENT_BURST, MAX_VALUE_LENGTH, logServerEvent, maskSecrets, resetServerLog } from '@/lib/server-log';

function lines (): Array<Record<string, unknown> & { fields: Record<string, unknown> }> {
  return vi.mocked(console.log).mock.calls.map(([line]) => JSON.parse(String(line)));
}

beforeEach(() => {
  resetServerLog();
  vi.spyOn(console, 'log').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('server event log', () => {
  it('writes one JSON line in the shape of the backend JSON format', () => {
    logServerEvent('WARNING', 'auth.login_failed', 'Failed login', { client: '192.0.2.7', retry_after: 2 }, Date.UTC(2026, 9, 4, 9, 0));
    expect(vi.mocked(console.log)).toHaveBeenCalledTimes(1);
    expect(lines()[0]).toEqual({
      ts:     '2026-10-04T09:00:00.000Z',
      level:  'WARNING',
      logger: 'web.auth',
      msg:    'Failed login',
      fields: { event: 'auth.login_failed', client: '192.0.2.7', retry_after: 2 },
    });
  });

  it('never writes secret-named fields and masks secrets in values', () => {
    logServerEvent('WARNING', 'proxy.refused', 'refused', {
      password:   'hunter2hunter2',
      session_id: 'abc',
      cookie:     'omnisync-session=xyz',
      path:       '/api/x?token=QUERYTOKEN',
      origin:     'https://user:URLPASS@evil.example',
      host:       'evil.example\n{"forged": true}',
      long:       'x'.repeat(500),
    });
    const [line] = lines();
    expect(line.fields).toMatchObject({ password: '***', session_id: '***', cookie: '***' });
    const text = vi.mocked(console.log).mock.calls[0][0] as string;
    for (const secret of ['hunter2', 'omnisync-session=xyz', 'QUERYTOKEN', 'URLPASS']) {
      expect(text).not.toContain(secret);
    }
    expect(line.fields.host).toBe('evil.example?{"forged": true}');
    expect(String(line.fields.long).length).toBe(MAX_VALUE_LENGTH);
    expect(text.split('\n')).toHaveLength(1);
  });

  it('limits each event type per minute and reports what it dropped', () => {
    const t0 = 1_000_000;
    for (let i = 0; i < EVENT_BURST + 5; i++) logServerEvent('WARNING', 'proxy.refused', 'refused', {}, t0 + i);
    // Another event type has its own budget.
    expect(logServerEvent('INFO', 'auth.login', 'Login', {}, t0)).toBe(true);
    expect(lines().filter((l) => l.fields.event === 'proxy.refused')).toHaveLength(EVENT_BURST);
    logServerEvent('WARNING', 'proxy.refused', 'refused', {}, t0 + 60_000);
    expect(lines().at(-1)?.fields).toEqual({ event: 'proxy.refused', dropped: 5 });
  });

  it('masks the shapes the backend masks', () => {
    expect(maskSecrets('Authorization: Bearer abc.def-123')).toBe('Authorization: Bearer ***');
    expect(maskSecrets('{"password": "x1", "token":"y2"}')).toBe('{"password": "***", "token":"***"}');
    expect(maskSecrets('client_secret=abc&scope=drive')).toBe('client_secret=***&scope=drive');
    expect(maskSecrets('Login from 192.0.2.7')).toBe('Login from 192.0.2.7');
  });
});
