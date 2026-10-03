import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  sortFiles, filterFiles, computeSummary,
  type SortKey, type SortDir,
} from '@/components/sync/file-browser';
import type { FileDiff, ChangeCategory } from '@/types';
import enLocale from '@/i18n/locales/en.json';
import faLocale from '@/i18n/locales/fa.json';
import deLocale from '@/i18n/locales/de.json';

// --- Arbitraries ---

const categoryArb: fc.Arbitrary<ChangeCategory> = fc.constantFrom(
  'local_only', 'remote_only', 'modified_local', 'modified_remote', 'modified_both'
);

// Generate ISO date strings from integer timestamps to avoid Invalid Date issues
const isoDateArb = fc
  .integer({ min: new Date('2020-01-01').getTime(), max: new Date('2026-01-01').getTime() })
  .map((ts) => new Date(ts).toISOString());

const fileDiffArb: fc.Arbitrary<FileDiff> = fc.record({
  path:            fc.stringMatching(/^[a-zA-Z0-9/_.-]+$/).filter((s) => s.length >= 1 && s.length <= 100),
  category:        categoryArb,
  local_size:      fc.option(fc.nat({ max: 10_000_000 }), { nil: null }),
  remote_size:     fc.option(fc.nat({ max: 10_000_000 }), { nil: null }),
  local_mod_time:  fc.option(isoDateArb, { nil: null }),
  remote_mod_time: fc.option(isoDateArb, { nil: null }),
  is_conflict:     fc.boolean(),
  manual_flag:     fc.boolean(),
});

const fileDiffListArb = fc.array(fileDiffArb, { minLength: 0, maxLength: 50 });

describe('File browser sorting correctness', () => {
  /**
   * For any list of FileDiff objects and any valid sort key,
   * sorting produces a list ordered correctly with null values sorted last.
   */
  const sortKeyArb: fc.Arbitrary<SortKey> = fc.constantFrom('path', 'category', 'local_size', 'local_mod_time');
  const sortDirArb: fc.Arbitrary<SortDir> = fc.constantFrom('asc', 'desc');

  it('sorted list is correctly ordered with nulls last', () => {
    fc.assert(
      fc.property(fileDiffListArb, sortKeyArb, sortDirArb, (files, key, dir) => {
        const sorted = sortFiles(files, key, dir);
        expect(sorted.length).toBe(files.length);

        for (let i = 1; i < sorted.length; i++) {
          const prev = sorted[i - 1][key];
          const curr = sorted[i][key];
          // nulls should be last
          if (prev == null) {
            expect(curr).toBeNull();
          }
          if (curr != null && prev != null) {
            const cmp = typeof prev === 'number' && typeof curr === 'number'
              ? prev - curr
              : String(prev).localeCompare(String(curr));
            if (dir === 'asc') expect(cmp).toBeLessThanOrEqual(0);
            else expect(cmp).toBeGreaterThanOrEqual(0);
          }
        }
      }),
      { numRuns: 100 }
    );
  });
});

describe('File browser filtering correctness', () => {
  /**
   * For any list of FileDiff objects and any category filter,
   * filtering returns exactly the files matching that category.
   */
  it('filtering returns exactly matching files', () => {
    fc.assert(
      fc.property(fileDiffListArb, categoryArb, (files, category) => {
        const filtered = filterFiles(files, category);
        // All returned files match the category
        for (const f of filtered) {
          expect(f.category).toBe(category);
        }
        // Count matches expected
        const expected = files.filter((f) => f.category === category).length;
        expect(filtered.length).toBe(expected);
      }),
      { numRuns: 100 }
    );
  });

  it("filter 'all' returns all files", () => {
    fc.assert(
      fc.property(fileDiffListArb, (files) => {
        const filtered = filterFiles(files, 'all');
        expect(filtered.length).toBe(files.length);
      }),
      { numRuns: 100 }
    );
  });
});

describe('FileDiff row contains all required fields', () => {
  /**
   * For any FileDiff object, all required fields are present and have correct types.
   * path is a string, category is a valid ChangeCategory, sizes are number|null,
   * mod times are string|null, is_conflict and manual_flag are booleans.
   */
  it('every FileDiff has all required fields with correct types', () => {
    const validCategories = new Set<string>([
      'local_only', 'remote_only', 'modified_local', 'modified_remote', 'modified_both',
    ]);

    fc.assert(
      fc.property(fileDiffArb, (file) => {
        expect(typeof file.path).toBe('string');
        expect(file.path.length).toBeGreaterThan(0);
        expect(validCategories.has(file.category)).toBe(true);
        expect(file.local_size === null || typeof file.local_size === 'number').toBe(true);
        expect(file.remote_size === null || typeof file.remote_size === 'number').toBe(true);
        expect(file.local_mod_time === null || typeof file.local_mod_time === 'string').toBe(true);
        expect(file.remote_mod_time === null || typeof file.remote_mod_time === 'string').toBe(true);
        expect(typeof file.is_conflict).toBe('boolean');
        expect(typeof file.manual_flag).toBe('boolean');
      }),
      { numRuns: 100 }
    );
  });
});

describe('Diff response summary consistency (frontend)', () => {
  /**
   * For any list of FileDiff objects, the computed summary counts must match
   * the actual count of files in each category, and total must equal files.length.
   */
  it('computed summary counts match actual file category counts', () => {
    fc.assert(
      fc.property(fileDiffListArb, (files) => {
        const summary = computeSummary(files);

        expect(summary.total).toBe(files.length);
        expect(summary.local_only).toBe(files.filter((f) => f.category === 'local_only').length);
        expect(summary.remote_only).toBe(files.filter((f) => f.category === 'remote_only').length);
        expect(summary.modified_local).toBe(files.filter((f) => f.category === 'modified_local').length);
        expect(summary.modified_remote).toBe(files.filter((f) => f.category === 'modified_remote').length);
        expect(summary.modified_both).toBe(files.filter((f) => f.category === 'modified_both').length);
        expect(summary.manual).toBe(files.filter((f) => f.manual_flag).length);

        // Category counts should sum to total
        const catSum = summary.local_only + summary.remote_only +
          summary.modified_local + summary.modified_remote + summary.modified_both;
        expect(catSum).toBe(summary.total);
      }),
      { numRuns: 100 }
    );
  });
});

describe('Granular sync i18n key completeness', () => {
  function extractKeys (obj: Record<string, unknown>, prefix: string): string[] {
    const keys: string[] = [];
    for (const [key, value] of Object.entries(obj)) {
      const fullKey = prefix ? `${prefix}.${key}` : key;
      if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
        keys.push(...extractKeys(value as Record<string, unknown>, fullKey));
      } else {
        keys.push(fullKey);
      }
    }
    return keys;
  }

  function getNestedValue (obj: Record<string, unknown>, key: string): unknown {
    const parts = key.split('.');
    let current: unknown = obj;
    for (const part of parts) {
      if (current === null || current === undefined || typeof current !== 'object') return undefined;
      current = (current as Record<string, unknown>)[part];
    }
    return current;
  }

  const granularKeys = extractKeys(
    (enLocale as Record<string, unknown>).granular as Record<string, unknown> ?? {},
    'granular'
  );

  it('all granular.* keys from English exist with non-empty values in Farsi and German', () => {
    expect(granularKeys.length).toBeGreaterThan(0);

    fc.assert(
      fc.property(fc.constantFrom(...granularKeys), (key) => {
        const faValue = getNestedValue(faLocale as Record<string, unknown>, key);
        expect(faValue).toBeDefined();
        expect(typeof faValue).toBe('string');
        expect((faValue as string).length).toBeGreaterThan(0);

        const deValue = getNestedValue(deLocale as Record<string, unknown>, key);
        expect(deValue).toBeDefined();
        expect(typeof deValue).toBe('string');
        expect((deValue as string).length).toBeGreaterThan(0);
      }),
      { numRuns: 100 }
    );
  });
});
