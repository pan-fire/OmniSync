import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { formatBytes } from '@/lib/format';

// Units as Intl formats them in English (unitDisplay: 'short'); EB is ours.
const EN_UNITS = ['byte', 'kB', 'MB', 'GB', 'TB', 'PB', 'EB'];
const SHOWN = /^([\d,]+(?:\.\d)?) (byte|kB|MB|GB|TB|PB|EB)$/;

/** The byte count an English size text stands for, and its unit index. */
function parse (text: string): { bytes: number; unit: number } {
  const match = SHOWN.exec(text);
  expect(match, `unexpected ${JSON.stringify(text)}`).not.toBeNull();
  const unit = EN_UNITS.indexOf(match![2]);
  return { bytes: Number(match![1].replaceAll(',', '')) * 1024 ** unit, unit };
}

describe('Property: formatBytes keeps the size it shows', () => {
  it('the number and unit it prints stand for the given byte count', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: Number.MAX_SAFE_INTEGER }), (bytes) => {
        const { bytes: shown, unit } = parse(formatBytes(bytes, 'en'));
        const scale = 1024 ** unit;
        // bytes are exact; larger units round to one decimal
        expect(Math.abs(shown - bytes)).toBeLessThanOrEqual(unit === 0 ? 0 : 0.05 * scale);
        // the largest unit that keeps the number below 1024 (EB has no ceiling)
        if (unit < EN_UNITS.length - 1) expect(bytes).toBeLessThan(1024 ** (unit + 1));
        if (unit > 0) expect(bytes).toBeGreaterThanOrEqual(scale);
      }),
      { numRuns: 200 }
    );
  });

  it('past 1 PiB it uses PB, then EB, and clamps at EB', () => {
    fc.assert(
      fc.property(fc.double({ min: 1024 ** 5, max: 1024 ** 8, noNaN: true }), (bytes) => {
        const { bytes: shown, unit } = parse(formatBytes(bytes, 'en'));
        const scale = 1024 ** unit;
        expect(unit).toBeGreaterThanOrEqual(5);
        expect(Math.abs(shown - bytes)).toBeLessThanOrEqual(0.05 * scale + 1e-12 * bytes);
        if (unit === 5) expect(bytes).toBeLessThan(1024 ** 6);
        if (unit === 6) expect(bytes).toBeGreaterThanOrEqual(1024 ** 6);
      }),
      { numRuns: 200 }
    );
    expect(formatBytes(1024 ** 5, 'en')).toBe('1 PB');
    expect(formatBytes(1.5 * 1024 ** 6, 'en')).toBe('1.5 EB');
    expect(formatBytes(2048 * 1024 ** 6, 'en')).toBe('2,048 EB');
  });

  it('uses the locale\'s digits, Persian included', () => {
    expect(formatBytes(1.5 * 1024 ** 6, 'fa')).toBe('۱٫۵ EB');
    expect(formatBytes(1.5 * 1024 ** 5, 'de')).toBe('1,5 PB');
    expect(formatBytes(1.5 * 1024 ** 3, 'fa')).toMatch(/^۱٫۵\s/);
  });

  it('an unknown size shows the fallback', () => {
    fc.assert(
      fc.property(fc.constantFrom(null, undefined), fc.string(), (bytes, fallback) => {
        expect(formatBytes(bytes, 'en', fallback)).toBe(fallback);
      })
    );
  });
});
