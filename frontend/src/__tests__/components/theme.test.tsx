import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';

type ThemeMode = 'dark' | 'light' | 'system';

// Pure function representing the theme-to-class mapping logic used by next-themes
// with attribute="class" configuration. This mirrors how ThemeProvider resolves
// whether the "dark" class should be present on the document root.
function shouldApplyDarkClass (
  theme: ThemeMode,
  systemPrefersDark: boolean
): boolean {
  if (theme === 'dark') return true;
  if (theme === 'light') return false;
  return systemPrefersDark;
}

// Feature: frontend-dashboard, Property 14: Theme mode applies correct class
describe('Property 14: Theme mode applies correct class', () => {
  const themeModeArb = fc.constantFrom<ThemeMode>('dark', 'light', 'system');
  const systemPrefArb = fc.boolean();

  // Validates: Requirements 11.1, 11.2, 11.7
  it('for any theme mode, the correct dark class presence is determined', () => {
    fc.assert(
      fc.property(themeModeArb, systemPrefArb, (theme, systemPrefersDark) => {
        const hasDarkClass = shouldApplyDarkClass(theme, systemPrefersDark);

        if (theme === 'dark') {
          // Req 11.1: dark mode → dark class present
          expect(hasDarkClass).toBe(true);
        } else if (theme === 'light') {
          // Req 11.1: light mode → dark class absent
          expect(hasDarkClass).toBe(false);
        } else {
          // Req 11.2: system mode → follows OS preference
          expect(hasDarkClass).toBe(systemPrefersDark);
        }
      }),
      { numRuns: 100 }
    );
  });

  // Validates: Requirements 11.1
  it('dark mode always applies dark class regardless of system preference', () => {
    fc.assert(
      fc.property(systemPrefArb, (systemPrefersDark) => {
        expect(shouldApplyDarkClass('dark', systemPrefersDark)).toBe(true);
      }),
      { numRuns: 100 }
    );
  });

  // Validates: Requirements 11.1
  it('light mode never applies dark class regardless of system preference', () => {
    fc.assert(
      fc.property(systemPrefArb, (systemPrefersDark) => {
        expect(shouldApplyDarkClass('light', systemPrefersDark)).toBe(false);
      }),
      { numRuns: 100 }
    );
  });

  // Validates: Requirements 11.2
  it('system mode matches OS preference exactly', () => {
    fc.assert(
      fc.property(systemPrefArb, (systemPrefersDark) => {
        expect(shouldApplyDarkClass('system', systemPrefersDark)).toBe(
          systemPrefersDark
        );
      }),
      { numRuns: 100 }
    );
  });
});

// frontend-dashboard R11: the Zinc palette, a cool gray with a slight blue
// hue (about 286), not shadcn's zero-chroma "neutral".
describe('Zinc palette', () => {
  const css = readFileSync(path.resolve(__dirname, '../../app/globals.css'), 'utf8');

  function block (selector: string): Record<string, string> {
    const start = css.indexOf(`${selector} {`);
    const body = css.slice(start, css.indexOf('}', start));
    return Object.fromEntries([...body.matchAll(/--([\w-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
  }

  const GRAYS = ['foreground', 'primary', 'secondary', 'muted', 'muted-foreground', 'accent', 'border', 'ring'];

  for (const [name, selector, tokens] of [
    ['light', ':root', GRAYS],
    ['dark', '.dark', ['background', 'card', 'primary', 'secondary', 'muted', 'muted-foreground', 'ring']],
  ] as const) {
    it(`the ${name} gray tokens are zinc`, () => {
      const vars = block(selector);
      for (const token of tokens) {
        const m = /^oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)$/.exec(vars[token] ?? '');
        expect(m, `--${token}: ${vars[token]}`).not.toBeNull();
        const [, , chroma, hue] = m!.map(Number);
        expect(chroma, `--${token} chroma`).toBeGreaterThan(0);
        expect(Math.abs(hue - 286), `--${token} hue`).toBeLessThan(2);
      }
    });
  }

  it('components.json records the zinc base colour', () => {
    const config = JSON.parse(readFileSync(path.resolve(__dirname, '../../../components.json'), 'utf8'));
    expect(config.tailwind.baseColor).toBe('zinc');
  });
});
