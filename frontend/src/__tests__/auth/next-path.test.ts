import { describe, expect, it } from 'vitest';
import { loginUrl, safeNextPath } from '@/lib/auth/next-path';

describe('safeNextPath', () => {
  it.each([
    ['/', '/'],
    ['/profiles', '/profiles'],
    ['/jobs/12?tab=files#top', '/jobs/12?tab=files#top'],
    ['/profiles/a%20b', '/profiles/a%20b'],
    ['/a/../config', '/config'],
  ])('keeps the same-site path %j', (value, expected) => {
    expect(safeNextPath(value)).toBe(expected);
  });

  it.each([
    null, undefined, '', 'profiles', 'https://evil.example/', '//evil.example/x', '/\\evil.example',
    '\\\\evil.example', 'javascript:alert(1)', '/\t/evil.example', '/\n/evil', ' /profiles',
    '/login', '/login?next=/login', '/login/', `/${'a'.repeat(3000)}`,
  ])('falls back to / for %j', (value) => {
    expect(safeNextPath(value)).toBe('/');
  });
});

describe('loginUrl', () => {
  it('carries the page to return to', () => {
    expect(loginUrl('/jobs?x=1')).toBe('/login?next=%2Fjobs%3Fx%3D1');
    expect(loginUrl('/')).toBe('/login');
    expect(loginUrl('//evil.example')).toBe('/login');
  });
});
