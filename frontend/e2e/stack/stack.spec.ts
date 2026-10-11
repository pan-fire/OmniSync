import { expect, test, type Page } from '@playwright/test';

// The web UI against the real backend (playwright.stack.config.ts): what
// the mocked smoke tests cannot show, that the pages work with the API as
// it really answers. scripts/test-fullstack-e2e.sh sets up the profile and
// the conflict, and checks afterwards on disk and in the audit log what the
// sync started here did.

function env (name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set: run this through scripts/test-fullstack-e2e.sh`);
  return value;
}

const PASSWORD = env('STACK_PASSWORD');
const PROFILE = env('STACK_PROFILE');
const SLUG = env('STACK_SLUG');
const CONFLICT = env('STACK_CONFLICT');

let pageErrors: string[];

/** Logs in through the login page, as a person would, landing on `path`. */
async function logIn (page: Page, path: string) {
  await page.goto(path);
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Log in' }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

test.beforeEach(async ({ page }) => {
  pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
});

test.afterEach(() => {
  expect(pageErrors, 'uncaught errors in the page').toEqual([]);
});

test('the dashboard shows the profile', async ({ page }) => {
  await logIn(page, '/');
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
  await expect(page.getByText(PROFILE).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Log out' }).first()).toBeAttached();
});

test('a sync can be started from the profile page', async ({ page }) => {
  await logIn(page, `/profiles/${SLUG}`);
  await expect(page.getByRole('heading', { level: 1, name: PROFILE })).toBeVisible();
  // The profile is not paused, so "Sync now" starts at once (no dialog).
  const started = page.waitForResponse((r) =>
    r.request().method() === 'POST' && r.url().endsWith(`/api/profiles/${SLUG}/sync/start`));
  await page.getByRole('button', { name: 'Sync now' }).click();
  const response = await started;
  expect([200, 202]).toContain(response.status());
  expect(await response.json()).toMatchObject({ job_id: expect.any(Number) });
});

test('the conflicts page lists the conflict', async ({ page }) => {
  await logIn(page, '/conflicts');
  await expect(page.getByRole('heading', { level: 1, name: 'Conflicts' })).toBeVisible();
  await expect(page.getByText(CONFLICT, { exact: true }).first()).toBeVisible();
  await expect(page.getByText(PROFILE).first()).toBeVisible();
});
