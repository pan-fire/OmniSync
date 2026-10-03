import { describe, expect, it } from 'vitest';
import {
  FREE_FAILURES,
  GLOBAL_BURST,
  GLOBAL_REFILL_MS,
  LoginThrottle,
  MAX_CONCURRENT_CHECKS,
  MAX_LOCK_MS,
} from '@/lib/auth/throttle';

function setup () {
  let now = 1_000_000;
  const throttle = new LoginThrottle(() => now);
  return {
    throttle,
    advance: (ms: number) => { now += ms; },
    /** One wrong password from `client`; returns the resulting lock (ms). */
    fail:    (client: string) => {
      expect(throttle.check(client)).toBe(0);
      return throttle.failure(client);
    },
  };
}

describe('LoginThrottle', () => {
  it('lets the first few wrong passwords through, then backs off exponentially', () => {
    const { throttle, fail, advance } = setup();
    for (let i = 0; i < FREE_FAILURES; i++) expect(fail('10.0.0.1')).toBe(0);
    expect(fail('10.0.0.1')).toBe(1000);
    expect(throttle.check('10.0.0.1')).toBe(1000);
    advance(1000);
    expect(fail('10.0.0.1')).toBe(2000);
    advance(2000);
    expect(fail('10.0.0.1')).toBe(4000);
  });

  it('caps the lock', () => {
    const { throttle, advance } = setup();
    let lock = 0;
    for (let i = 0; i < 40; i++) {
      advance(lock + GLOBAL_REFILL_MS * GLOBAL_BURST);
      expect(throttle.check('10.0.0.1')).toBe(0);
      lock = throttle.failure('10.0.0.1');
    }
    expect(lock).toBe(MAX_LOCK_MS);
  });

  it('keeps clients apart and clears a client on success', () => {
    const { throttle, fail } = setup();
    for (let i = 0; i <= FREE_FAILURES; i++) fail('10.0.0.1');
    expect(throttle.check('10.0.0.1')).toBeGreaterThan(0);
    expect(throttle.check('10.0.0.2')).toBe(0);
    throttle.success('10.0.0.2');
    expect(fail('10.0.0.3')).toBe(0);
    throttle.check('10.0.0.3');
    throttle.success('10.0.0.3');
    for (let i = 0; i < FREE_FAILURES; i++) expect(fail('10.0.0.3')).toBe(0);
  });

  it('limits guessing globally when every attempt claims a new address', () => {
    const { throttle, advance } = setup();
    for (let i = 0; i < GLOBAL_BURST; i++) {
      expect(throttle.check(`spoofed-${i}`)).toBe(0);
      throttle.failure(`spoofed-${i}`);
    }
    const wait = throttle.check('spoofed-new');
    expect(wait).toBeGreaterThan(0);
    expect(wait).toBeLessThanOrEqual(GLOBAL_REFILL_MS);
    advance(GLOBAL_REFILL_MS);
    expect(throttle.check('spoofed-new')).toBe(0);
    throttle.failure('spoofed-new');
    expect(throttle.check('spoofed-newer')).toBeGreaterThan(0);
  });

  it('runs only a few password checks at a time', () => {
    const { throttle } = setup();
    for (let i = 0; i < MAX_CONCURRENT_CHECKS; i++) expect(throttle.check(`c${i}`)).toBe(0);
    expect(throttle.check('one-more')).toBe(1000);
    throttle.release();
    expect(throttle.check('one-more')).toBe(0);
  });
});
