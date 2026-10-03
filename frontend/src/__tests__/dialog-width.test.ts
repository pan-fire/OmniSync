import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

// DialogContent's base classes keep a 1rem margin on phones
// (max-w-[calc(100%-2rem)]) and widen it from sm: up. An unprefixed named
// width such as max-w-lg replaces that margin, so the dialog touches the
// screen edges on a phone: widths must be sm:max-w-* (or md:, lg:...).
// Arbitrary values (max-w-[95vw]) are deliberate phone widths and allowed.

const SRC = path.resolve(__dirname, '..');

function sourceFiles (dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return name === '__tests__' ? [] : sourceFiles(full);
    return /\.tsx$/.test(name) ? [full] : [];
  });
}

/** The className of every <DialogContent ...> in `text` (single or multi-line tags). */
function dialogContentClasses (text: string): string[] {
  return [...text.matchAll(/<DialogContent\b[^>]*?className=(?:"([^"]*)"|\{`([^`]*)`\}|\{cn\(([^)]*)\)\})/g)]
    .map((m) => m[1] ?? m[2] ?? m[3] ?? '');
}

/** Unprefixed named max-w-* utilities in a class list. */
function unprefixedMaxWidths (classes: string): string[] {
  return classes.split(/[\s'"`,]+/).filter((c) => /^max-w-(?!\[)/.test(c));
}

describe('Dialog widths keep the phone margin', () => {
  it('the scanner flags an unprefixed width and passes sm: and arbitrary ones', () => {
    expect(unprefixedMaxWidths('max-w-lg max-h-[80vh]')).toEqual(['max-w-lg']);
    expect(unprefixedMaxWidths('sm:max-w-lg md:max-w-2xl')).toEqual([]);
    expect(unprefixedMaxWidths('w-[95vw] max-w-[95vw] lg:max-w-[82vw]')).toEqual([]);
    expect(dialogContentClasses('<DialogContent\n  className="max-w-sm">')).toEqual(['max-w-sm']);
  });

  it('no DialogContent uses an unprefixed named max-w', () => {
    const offenders = sourceFiles(SRC).flatMap((file) =>
      dialogContentClasses(readFileSync(file, 'utf8'))
        .flatMap((classes) => unprefixedMaxWidths(classes))
        .map((c) => `${path.relative(SRC, file)}: ${c}`));
    expect(offenders).toEqual([]);
  });

  it('finds the dialogs it checks', () => {
    const found = sourceFiles(SRC).filter((file) => dialogContentClasses(readFileSync(file, 'utf8')).length > 0);
    expect(found.length).toBeGreaterThan(10);
  });
});
