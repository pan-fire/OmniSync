import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { render, within } from '@testing-library/react';
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

// --- Property 8 ---

// Feature: frontend-dashboard, Property 8: Log entry rendering completeness
describe('Property 8: Log entry rendering completeness', () => {
  // Validates: Requirements 5.1
  it("for any non-empty LogEntry array, the rendered view contains each entry's timestamp, level, and message", () => {
    fc.assert(
      fc.property(nonEmptyLogsArb, (logs: LogEntry[]) => {
        const { unmount, container } = render(
          <I18nProvider>
            <LogViewer logs={logs} onRefresh={() => {}} />
          </I18nProvider>
        );

        const view = within(container);

        for (const entry of logs) {
          // shown in the app locale; the raw ISO value stays in dateTime/title
          expect(view.getAllByTitle(entry.timestamp).length).toBeGreaterThanOrEqual(1);
          expect(view.getAllByText(formatDateTime(entry.timestamp, 'en')).length).toBeGreaterThanOrEqual(1);
          expect(view.getAllByText(entry.level).length).toBeGreaterThanOrEqual(1);
          expect(view.getAllByText(entry.message).length).toBeGreaterThanOrEqual(1);
        }

        unmount();
      }),
      { numRuns: 100 }
    );
  });
});

// --- Property 9 ---

// Feature: frontend-dashboard, Property 9: Log level filtering correctness
describe('Property 9: Log level filtering correctness', () => {
  // Validates: Requirements 5.2
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

// --- Property 10 ---

// Feature: frontend-dashboard, Property 10: Log entry ordering
describe('Property 10: Log entry ordering', () => {
  // Validates: Requirements 5.4
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
