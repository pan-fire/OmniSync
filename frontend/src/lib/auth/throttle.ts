// Brute-force protection for the login, in memory.
//
// Two layers, because the client address is only as good as the
// X-Forwarded-For header: without a reverse proxy in front, the browser
// sets it (Next.js only fills it in when absent), so an attacker can claim
// a new address for every attempt.
//
// 1. Per client: after FREE_FAILURES wrong passwords, each further attempt
//    must wait 1 s, 2 s, 4 s, ... up to MAX_LOCK_MS. A success clears it.
// 2. Global: wrong passwords from all clients together drain a bucket of
//    GLOBAL_BURST that refills one per GLOBAL_REFILL_MS. This caps guessing
//    at ~14,400 a day however addresses are spoofed; the price is that such
//    an attack also makes the real user wait a few seconds per attempt.
//    Existing sessions are not affected.
//
// Plus a cap on concurrent password checks, since each scrypt call uses
// 32 MiB and a few hundred milliseconds of CPU.

export const FREE_FAILURES = 3;
export const MAX_LOCK_MS = 15 * 60 * 1000;
export const GLOBAL_BURST = 10;
export const GLOBAL_REFILL_MS = 6000;
export const MAX_CONCURRENT_CHECKS = 2;
/** A client's record is forgotten this long after its last failure. */
const FORGET_AFTER_MS = 24 * 60 * 60 * 1000;
/** At most this many clients are tracked; beyond, they share one record. */
const MAX_CLIENTS = 10_000;
const OVERFLOW_KEY = '\0overflow';

type ClientRecord = { failures: number; lockedUntil: number; lastFailure: number };

export class LoginThrottle {
  private readonly clients = new Map<string, ClientRecord>();
  private tokens = GLOBAL_BURST;
  private refilledAt: number;
  private running = 0;

  constructor (private readonly clock: () => number = Date.now) {
    this.refilledAt = clock();
  }

  private refill (now: number): void {
    const gained = Math.floor((now - this.refilledAt) / GLOBAL_REFILL_MS);
    if (gained > 0) {
      this.tokens = Math.min(GLOBAL_BURST, this.tokens + gained);
      this.refilledAt = this.tokens === GLOBAL_BURST ? now : this.refilledAt + gained * GLOBAL_REFILL_MS;
    }
  }

  private key (client: string): string {
    if (this.clients.has(client) || this.clients.size < MAX_CLIENTS) return client;
    this.prune();
    return this.clients.size < MAX_CLIENTS ? client : OVERFLOW_KEY;
  }

  /**
   * Milliseconds `client` must wait before its next attempt, 0 when it may
   * try now. An attempt that may proceed must be followed by exactly one
   * of success(), failure() or release().
   */
  check (client: string): number {
    const now = this.clock();
    const record = this.clients.get(this.key(client));
    if (record && record.lockedUntil > now) return record.lockedUntil - now;
    this.refill(now);
    if (this.tokens < 1) return this.refilledAt + GLOBAL_REFILL_MS - now;
    if (this.running >= MAX_CONCURRENT_CHECKS) return 1000;
    this.running += 1;
    return 0;
  }

  /** Ends an attempt without counting it (e.g. the check threw). */
  release (): void {
    this.running = Math.max(0, this.running - 1);
  }

  success (client: string): void {
    this.release();
    this.clients.delete(client);
  }

  /** Records a wrong password; returns how long `client` must now wait (ms). */
  failure (client: string): number {
    this.release();
    const now = this.clock();
    this.refill(now);
    this.tokens = Math.max(0, this.tokens - 1);
    const key = this.key(client);
    const record = this.clients.get(key) ?? { failures: 0, lockedUntil: 0, lastFailure: 0 };
    record.failures += 1;
    record.lastFailure = now;
    const extra = record.failures - FREE_FAILURES;
    record.lockedUntil = extra > 0 ? now + Math.min(1000 * 2 ** (extra - 1), MAX_LOCK_MS) : 0;
    this.clients.set(key, record);
    return Math.max(0, record.lockedUntil - now);
  }

  prune (): void {
    const now = this.clock();
    for (const [key, record] of this.clients) {
      if (record.lockedUntil <= now && now - record.lastFailure > FORGET_AFTER_MS) this.clients.delete(key);
    }
  }
}
