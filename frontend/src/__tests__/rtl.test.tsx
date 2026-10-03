import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { render, screen } from '@testing-library/react';
import { Direction } from 'radix-ui';
import { I18nProvider } from '@/i18n';

// Persian renders right-to-left. Layout uses
// logical properties (ms-/pe-/start-/text-start ...), which flip with
// dir=rtl, and icons that point along the reading direction are mirrored.

const SRC = path.resolve(__dirname, '..');

function sourceFiles (dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return name === '__tests__' ? [] : sourceFiles(full);
    return name.endsWith('.tsx') ? [full] : [];
  });
}

// A physical left/right utility (margin, padding, inset, border, radius,
// text alignment), with any variant prefix, inside a string literal.
const PHYSICAL = /(?<![\w-])(?:[\w\-[\]&:>*=.()/]+:)?-?(?:ml|mr|pl|pr|left|right|border-[lr]|rounded-(?:tl|tr|bl|br|[lr])|text-(?:left|right))(?:-[\w./[\]%()-]+)?(?![\w-])(?=[^'"`\n]*['"`])/g;

// Centred with translate-x: the same in both directions.
const SYMMETRIC = new Set(['left-[50%]']);

function physicalClasses (text: string): string[] {
  return [...text.matchAll(PHYSICAL)]
    .map((m) => m[0])
    // a bare word "left"/"right" is prose or a prop value, not a class
    .filter((c) => !/^-?(left|right)$/.test(c.replace(/^.*:/, '')) && !SYMMETRIC.has(c));
}

describe('RTL layout', () => {
  const files = sourceFiles(SRC);

  it('uses logical instead of physical left/right utilities', () => {
    const offenders: Record<string, string[]> = {};
    for (const file of files) {
      const rel = path.relative(SRC, file).split(path.sep).join('/');
      const found = physicalClasses(readFileSync(file, 'utf8'));
      if (found.length > 0) offenders[rel] = found;
    }
    expect(offenders).toEqual({});
  });

  it('mirrors icons that point along the reading direction', () => {
    const icon = /<(ChevronRight|ChevronLeft|ChevronRightIcon|ChevronLeftIcon|ArrowLeft|ArrowRight|PanelLeftOpen|PanelLeftClose)\b[^>]*>/g;
    const unmirrored: string[] = [];
    for (const file of files) {
      for (const m of readFileSync(file, 'utf8').matchAll(icon)) {
        if (!/rtl:(-scale-x-100|rotate-180)/.test(m[0])) unmirrored.push(`${path.relative(SRC, file)}: ${m[0]}`);
      }
    }
    expect(unmirrored).toEqual([]);
  });

  it('finds the physical classes it looks for', () => {
    expect(physicalClasses("cn('ml-2 sm:pr-4 text-left data-[inset]:pl-8 absolute right-2', x)"))
      .toEqual(['ml-2', 'sm:pr-4', 'text-left', 'data-[inset]:pl-8', 'right-2']);
    expect(physicalClasses("cn('ms-2 pe-4 text-start left-[50%]') // align left")).toEqual([]);
  });
});

function DirProbe () {
  return <span data-testid="dir">{Direction.useDirection()}</span>;
}

describe('Radix direction', () => {
  it('is rtl for Persian and ltr otherwise', () => {
    const { unmount } = render(<I18nProvider initialLocale="fa"><DirProbe /></I18nProvider>);
    expect(screen.getByTestId('dir')).toHaveTextContent('rtl');
    unmount();
    render(<I18nProvider initialLocale="de"><DirProbe /></I18nProvider>);
    expect(screen.getByTestId('dir')).toHaveTextContent('ltr');
  });
});
