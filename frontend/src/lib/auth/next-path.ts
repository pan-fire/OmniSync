import { LOGIN_PATH } from './config';

const BASE = 'http://omnisync.invalid';

/**
 * Where to go after logging in: `value` if it is a path on this site, else
 * '/'. Refuses absolute and protocol-relative URLs (`//evil`, `/\evil`,
 * which browsers treat alike), control characters, and the login page
 * itself, so `?next=` cannot be used as an open redirect or a loop.
 */
export function safeNextPath (value: string | null | undefined): string {
  if (!value || value.length > 2048 || !value.startsWith('/')) return '/';
  if (value.startsWith('//') || value.startsWith('/\\')) return '/';
  // eslint-disable-next-line no-control-regex -- refusing control characters is the point
  if (/[\u0000-\u001f\u007f\\]/.test(value)) return '/';
  let url: URL;
  try {
    url = new URL(value, BASE);
  } catch {
    return '/';
  }
  if (url.origin !== BASE) return '/';
  if (url.pathname === LOGIN_PATH || url.pathname.startsWith(`${LOGIN_PATH}/`)) return '/';
  return `${url.pathname}${url.search}${url.hash}`;
}

/** The login page address that returns to `path` afterwards. */
export function loginUrl (path: string): string {
  const next = safeNextPath(path);
  return next === '/' ? LOGIN_PATH : `${LOGIN_PATH}?next=${encodeURIComponent(next)}`;
}
