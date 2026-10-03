import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { sortProfileCards } from '@/app/page';
import type { ProfileSummary } from '@/types';

function makeProfile (overrides: Partial<ProfileSummary> = {}): ProfileSummary {
  return {
    slug:             'default',
    name:             'Default',
    state:            'idle',
    last_sync:        null,
    pending_changes:  0,
    intervals_paused: false,
    last_error:       null,
    resync_required:  false,
    ...overrides,
  };
}

describe('Dashboard sortProfileCards', () => {
  it('paused profiles with pending changes come first', () => {
    const profiles = [
      makeProfile({ slug: 'idle', name: 'Idle' }),
      makeProfile({ slug: 'paused', name: 'Paused', intervals_paused: true, pending_changes: 5 }),
      makeProfile({ slug: 'error', name: 'Error', state: 'error' }),
    ];
    const sorted = sortProfileCards(profiles);
    expect(sorted[0].slug).toBe('paused');
  });

  it('error state ranks above idle', () => {
    const profiles = [
      makeProfile({ slug: 'idle', name: 'Idle' }),
      makeProfile({ slug: 'error', name: 'Error', state: 'error' }),
    ];
    const sorted = sortProfileCards(profiles);
    expect(sorted[0].slug).toBe('error');
  });

  it('equal priority sorted by name', () => {
    const profiles = [
      makeProfile({ slug: 'z', name: 'Zebra' }),
      makeProfile({ slug: 'a', name: 'Alpha' }),
    ];
    const sorted = sortProfileCards(profiles);
    expect(sorted[0].name).toBe('Alpha');
    expect(sorted[1].name).toBe('Zebra');
  });

  it('returns a permutation of input (property-based)', () => {
    const profileArb = fc.record({
      slug:             fc.string({ minLength: 1, maxLength: 10 }),
      name:             fc.string({ minLength: 1, maxLength: 20 }),
      state:            fc.constantFrom('idle', 'pushing', 'pulling', 'error'),
      last_sync:        fc.constant(null),
      pending_changes:  fc.integer({ min: 0, max: 100 }),
      intervals_paused: fc.boolean(),
    }) as fc.Arbitrary<ProfileSummary>;

    fc.assert(
      fc.property(
        fc.array(profileArb, { minLength: 0, maxLength: 20 }),
        (profiles) => {
          const sorted = sortProfileCards(profiles);
          // Same length
          expect(sorted).toHaveLength(profiles.length);
          // Same elements (permutation)
          const slugsBefore = profiles.map(p => p.slug).sort();
          const slugsAfter = sorted.map(p => p.slug).sort();
          expect(slugsAfter).toEqual(slugsBefore);
        }
      ),
      { numRuns: 50 }
    );
  });

  it('does not mutate the input array', () => {
    const profiles = [
      makeProfile({ slug: 'b', name: 'B' }),
      makeProfile({ slug: 'a', name: 'A' }),
    ];
    const original = [...profiles];
    sortProfileCards(profiles);
    expect(profiles).toEqual(original);
  });
});
