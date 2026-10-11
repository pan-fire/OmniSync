import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { render } from '@testing-library/react';
import { I18nProvider } from '@/i18n';
import { formatDateTime } from '@/lib/format';
import { LogViewer, filterLogsByLevel, sortLogsReverseChronological } from '@/components/logs/log-viewer';
import type { LogEntry } from '@/types';

// --- Arbitraries ---

const logLevelArb = fc.constantFrom('DEBUG', 'INFO', 'WARNING', 'ERROR');

const logEntryArb: fc.Arbitrary<LogEntry> = fc.record({
  timestamp: fc.integer({ min: 1577836800000, max: 1893456000000 }).map((ms) => new Date(ms).toISOString()),
  level:     logLevelArb,
  message:   fc.stringMatching(/^[a-zA-Z][a-zA-Z0-9._-]{0,49}$/),
});

const nonEmptyLogsArb = fc.array(logEntryArb, { minLength: 1, maxLength: 10 });

// Mounting the viewer (Radix tabs, scroll area) per run made 100 runs take
// over 5 s under coverage on a loaded machine: mount once, rerender per run,
// and read each entry once. 50 runs of up to 10 entries still cover every
// level many times over.
const RENDER_RUNS = 50;

function Viewer ({ logs }: { logs: LogEntry[] }) {
  return <I18nProvider><LogViewer logs={logs} onRefresh={() => {}} /></I18nProvider>;
}

/** [raw timestamp, shown timestamp, level, message] of each rendered entry. */
function renderedEntries (container: HTMLElement): string[][] {
  return Array.from(container.querySelectorAll('time'), (time) => {
    const entry = time.parentElement as HTMLElement;
    return [
      time.getAttribute('title') ?? '',
      time.textContent ?? '',
      time.nextElementSibling?.textContent ?? '',
      entry.querySelector('span[dir="auto"]')?.textContent ?? '',
    ];
  });
}

const byFields = (a: string[], b: string[]) => a.join('\0').localeCompare(b.join('\0'));

describe('Log entry rendering completeness', () => {
  it("for any non-empty LogEntry array, the rendered view contains each entry's timestamp, level, and message", () => {
    const { container, rerender } = render(<Viewer logs={[]} />);
    fc.assert(
      fc.property(nonEmptyLogsArb, (logs: LogEntry[]) => {
        rerender(<Viewer logs={logs} />);

        // Every entry once, nothing else: shown in the app locale, with the
        // raw ISO value kept in dateTime/title.
        const expected = logs.map((entry) => [
          entry.timestamp,
          formatDateTime(entry.timestamp, 'en'),
          entry.level,
          entry.message,
        ]);
        expect(renderedEntries(container).sort(byFields)).toEqual(expected.sort(byFields));
      }),
      { numRuns: RENDER_RUNS }
    );
  });
});

describe('Log level filtering correctness', () => {
  it('for any LogEntry array and selected filter level, filtered entries all match the selected level', () => {
    const filterLevelArb = fc.constantFrom('ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR');

    fc.assert(
      fc.property(
        fc.array(logEntryArb, { minLength: 0, maxLength: 20 }),
        filterLevelArb,
        (logs: LogEntry[], level: string) => {
          const filtered = filterLogsByLevel(logs, level);

          if (level === 'ALL') {
            // ALL returns every entry
            expect(filtered.length).toBe(logs.length);
          } else {
            // Every filtered entry matches the selected level
            for (const entry of filtered) {
              expect(entry.level.toUpperCase()).toBe(level.toUpperCase());
            }
            // No matching entries were dropped
            const expected = logs.filter((e) => e.level.toUpperCase() === level.toUpperCase());
            expect(filtered.length).toBe(expected.length);
          }
        }
      ),
      { numRuns: 100 }
    );
  });
});

describe('Log entry ordering', () => {
  it('for any LogEntry array, sorted entries are in reverse chronological order', () => {
    fc.assert(
      fc.property(
        fc.array(logEntryArb, { minLength: 0, maxLength: 20 }),
        (logs: LogEntry[]) => {
          const sorted = sortLogsReverseChronological(logs);

          // Each entry's timestamp should be >= the next entry's timestamp
          for (let i = 0; i < sorted.length - 1; i++) {
            const current = new Date(sorted[i].timestamp).getTime();
            const next = new Date(sorted[i + 1].timestamp).getTime();
            expect(current).toBeGreaterThanOrEqual(next);
          }

          // Length is preserved
          expect(sorted.length).toBe(logs.length);
        }
      ),
      { numRuns: 100 }
    );
  });
});
