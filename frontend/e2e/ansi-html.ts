// Turns the ANSI output of the TUI (its View(), see
// tui/test/ui/screenshot_test.go) into an HTML page that looks like a
// terminal, so screenshots.spec.ts can capture it as a PNG. It handles the
// SGR codes Lip Gloss writes: reset, bold, faint, italic, underline,
// reverse, and 16-colour, 256-colour and true-colour fore- and backgrounds.

const BACKGROUND = '#1e1e2e';
const FOREGROUND = '#cdd6f4';

// The basic 16 colours (Catppuccin Mocha, close to the TUI's dark theme).
const BASIC = [
  '#45475a', '#f38ba8', '#a6e3a1', '#f9e2af', '#89b4fa', '#f5c2e7', '#94e2d5', '#bac2de',
  '#585b70', '#f38ba8', '#a6e3a1', '#f9e2af', '#89b4fa', '#f5c2e7', '#94e2d5', '#a6adc8',
];

function color256 (n: number): string {
  if (n < 16) return BASIC[n];
  if (n >= 232) {
    const level = 8 + (n - 232) * 10;
    return `rgb(${level},${level},${level})`;
  }
  const i = n - 16;
  const steps = [0, 95, 135, 175, 215, 255];
  return `rgb(${steps[Math.floor(i / 36)]},${steps[Math.floor(i / 6) % 6]},${steps[i % 6]})`;
}

interface Style {
  bold?:      boolean;
  faint?:     boolean;
  italic?:    boolean;
  underline?: boolean;
  reverse?:   boolean;
  fg?:        string;
  bg?:        string;
}

/** Apply one SGR sequence's parameters to `style`. */
function applySgr (style: Style, params: number[]): Style {
  let next = { ...style };
  for (let i = 0; i < params.length; i++) {
    const p = params[i];
    if (p === 0) next = {};
    else if (p === 1) next.bold = true;
    else if (p === 2) next.faint = true;
    else if (p === 3) next.italic = true;
    else if (p === 4) next.underline = true;
    else if (p === 7) next.reverse = true;
    else if (p === 22) { next.bold = false; next.faint = false; } else if (p === 23) next.italic = false;
    else if (p === 24) next.underline = false;
    else if (p === 27) next.reverse = false;
    else if (p >= 30 && p <= 37) next.fg = BASIC[p - 30];
    else if (p >= 90 && p <= 97) next.fg = BASIC[p - 90 + 8];
    else if (p >= 40 && p <= 47) next.bg = BASIC[p - 40];
    else if (p >= 100 && p <= 107) next.bg = BASIC[p - 100 + 8];
    else if (p === 39) delete next.fg;
    else if (p === 49) delete next.bg;
    else if (p === 38 || p === 48) {
      const key = p === 38 ? 'fg' : 'bg';
      if (params[i + 1] === 5) {
        next[key] = color256(params[i + 2]);
        i += 2;
      } else if (params[i + 1] === 2) {
        next[key] = `rgb(${params[i + 2]},${params[i + 3]},${params[i + 4]})`;
        i += 4;
      }
    }
  }
  return next;
}

function escapeHtml (text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function span (style: Style, text: string): string {
  if (!text) return '';
  const fg = style.reverse ? (style.bg ?? BACKGROUND) : style.fg;
  const bg = style.reverse ? (style.fg ?? FOREGROUND) : style.bg;
  const css = [
    fg && `color:${fg}`,
    bg && `background:${bg}`,
    style.bold && 'font-weight:700',
    style.faint && 'opacity:.6',
    style.italic && 'font-style:italic',
    style.underline && 'text-decoration:underline',
  ].filter(Boolean).join(';');
  return css ? `<span style="${css}">${escapeHtml(text)}</span>` : escapeHtml(text);
}

/** The ANSI text as the body of a terminal-like HTML page. */
export function ansiToHtml (ansi: string, title: string): string {
  let style: Style = {};
  let html = '';
  // eslint-disable-next-line no-control-regex
  const pattern = /\x1b\[([0-9;:]*)m|\x1b\[[0-9;?]*[A-Za-z]/g;
  let last = 0;
  for (const match of ansi.matchAll(pattern)) {
    html += span(style, ansi.slice(last, match.index));
    last = match.index + match[0].length;
    if (match[1] !== undefined) {
      const params = match[1] === '' ? [0] : match[1].split(/[;:]/).map(Number);
      style = applySgr(style, params);
    }
  }
  html += span(style, ansi.slice(last));
  return `<!doctype html>
<html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>
<style>
  html, body { margin: 0; background: transparent; }
  .window { display: inline-block; border-radius: 10px; overflow: hidden; background: ${BACKGROUND}; }
  .bar { height: 28px; background: #181825; display: flex; align-items: center; gap: 8px; padding: 0 12px; }
  .bar i { width: 12px; height: 12px; border-radius: 50%; display: inline-block; }
  pre { margin: 0; padding: 14px 18px; color: ${FOREGROUND};
        font: 14px/1.35 "DejaVu Sans Mono", "Liberation Mono", Menlo, Consolas, monospace; }
</style></head>
<body><div class="window"><div class="bar"><i style="background:#f38ba8"></i><i style="background:#f9e2af"></i><i style="background:#a6e3a1"></i></div><pre>${html}</pre></div></body></html>`;
}
