/**
 * Folder choices of the "Choose folders" dialog, and the rclone filter rules
 * they stand for.
 *
 * A choice is stored only where it differs from the folder above it ('' is
 * the synced folder itself, synced unless chosen otherwise). The rules are
 * anchored folder rules, deepest first, because rclone uses the first rule
 * that matches:
 *
 *   everything except Big, but Big/keep:   + /Big/keep/**   - /Big/**
 *   only Docs, without Docs/sub:          - /Docs/sub/**   + /Docs/**   - **
 *
 * Files directly in the synced folder follow the folder itself (synced
 * unless it is unchecked). Two-way profiles keep the sync marker
 * (.omnisync-check) visible on their own: the server puts
 * `+ /.omnisync-check` before the profile's rules.
 */

export type FolderChoice = 'on' | 'off';
/** Folder path relative to the synced folder ('a/b', '' for the folder itself) -> choice. */
export type FolderChoices = Record<string, FolderChoice>;
export type FolderState = FolderChoice | 'mixed';

const SPECIAL = /[\\*?[\]{}]/g;
const UNESCAPED_SPECIAL = /(^|[^\\])(\\\\)*[*?[\]{}]/;
const FOLDER_RULE = /^([+-]) \/(.+)\/\*\*$/;
const EVERYTHING_ELSE = '- **';

/** A literal folder path for an rclone filter rule (wildcard characters escaped). */
export function escapeRulePath (path: string): string {
  return path.replace(SPECIAL, (c) => `\\${c}`);
}

function unescapeRulePath (path: string): string {
  return path.replace(/\\(.)/g, '$1');
}

export function parentOf (path: string): string {
  const i = path.lastIndexOf('/');
  return i < 0 ? '' : path.slice(0, i);
}

function isInside (path: string, folder: string): boolean {
  return folder === '' ? path !== '' : path.startsWith(`${folder}/`);
}

/** The choice that applies to a folder: its own, else the nearest one above it. */
export function effectiveChoice (choices: FolderChoices, path: string): FolderChoice {
  let current = path;
  for (;;) {
    const own = choices[current];
    if (own) return own;
    if (current === '') return 'on';
    current = parentOf(current);
  }
}

/** Checked, unchecked, or mixed (something inside it is chosen differently). */
export function folderState (choices: FolderChoices, path: string): FolderState {
  const own = effectiveChoice(choices, path);
  const differs = Object.entries(choices).some(([p, c]) => isInside(p, path) && c !== own);
  return differs ? 'mixed' : own;
}

/** Choose a folder (and with it everything inside it). */
export function setChoice (choices: FolderChoices, path: string, choice: FolderChoice): FolderChoices {
  const next: FolderChoices = {};
  for (const [p, c] of Object.entries(choices)) {
    if (p !== path && !isInside(p, path)) next[p] = c;
  }
  if (path === '') {
    if (choice === 'off') next[''] = 'off';
  } else if (effectiveChoice(next, parentOf(path)) !== choice) {
    next[path] = choice;
  }
  return next;
}

function depth (path: string): number {
  return path.split('/').length;
}

/** The filter rules for these choices (deepest first, then '- **' if the folder itself is unchecked). */
export function generateRules (choices: FolderChoices): string[] {
  const rules = Object.entries(choices)
    .filter(([p]) => p !== '')
    .sort(([a], [b]) => depth(b) - depth(a) || (a < b ? -1 : a > b ? 1 : 0))
    .map(([p, c]) => `${c === 'on' ? '+' : '-'} /${escapeRulePath(p)}/**`);
  if (choices[''] === 'off') rules.push(EVERYTHING_ELSE);
  return rules;
}

export interface ParsedRules {
  /** The folder choices, or null when the folder rules are not ones this dialog writes. */
  choices: FolderChoices | null;
  /** Rules that are not folder rules (e.g. '- *.tmp'): kept, before the folder rules. */
  other:   string[];
}

/** Read the folder choices back from a profile's rules (one rule per line). */
export function parseRules (lines: string[]): ParsedRules {
  const rules = lines.map((l) => l.trim()).filter(Boolean);
  const other: string[] = [];
  const folderRules: string[] = [];
  const choices: FolderChoices = {};
  let valid = true;
  rules.forEach((rule, i) => {
    // Other rules are written back before the folder rules: one that
    // came after a folder rule would change meaning.
    const afterFolderRule = folderRules.length > 0;
    if (rule === EVERYTHING_ELSE && i === rules.length - 1) {
      folderRules.push(rule);
      choices[''] = 'off';
      return;
    }
    const m = FOLDER_RULE.exec(rule);
    if (!m) {
      other.push(rule);
      // A catch-all before the folder rules would override every one of them.
      if (afterFolderRule || /^[+-] \*\*$/.test(rule)) valid = false;
      return;
    }
    if (UNESCAPED_SPECIAL.test(m[2])) {
      valid = false; // a pattern, not one folder
      return;
    }
    folderRules.push(rule);
    choices[unescapeRulePath(m[2])] = m[1] === '+' ? 'on' : 'off';
  });
  if (!valid) return { choices: null, other };
  // Only rules this dialog would write itself (same order, nothing redundant)
  // are read back; anything else would change meaning when rewritten.
  const again = generateRules(choices);
  const same = again.length === folderRules.length && again.every((r, i) => r === folderRules[i]);
  if (!same) return { choices: null, other };
  return { choices, other };
}
