import { expect, test } from '@playwright/test';
import { expectNoSeriousA11yViolations } from './axe';
import { mockBackend } from './mock-backend';
import { E2E_PASSWORD } from '../playwright.config';

// The login is on for this server (project "login" in playwright.config.ts).
// /auth/* is the UI server's own and is not mocked: these tests log in for real.
//
// In order, one at a time: the server throttles a client after three wrong
// passwords, and the successful login after the wrong one clears that again,
// so repeated runs against the same server keep working.
test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ page }) => {
  await mockBackend(page);
});

test('pages redirect to the login, which is accessible', async ({ page }) => {
  await page.goto('/profiles');
  await expect(page).toHaveURL(/\/login\?next=%2Fprofiles$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Log in to OmniSync' })).toBeVisible();
  await expectNoSeriousA11yViolations(page);
});

test('the API answers 401 without a session', async ({ request }) => {
  const res = await request.get('/api/profiles');
  expect(res.status()).toBe(401);
  expect((await res.json()).code).toBe('login_required');
});

test('a wrong password is refused', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Password').fill('not-the-password');
  await page.getByRole('button', { name: 'Log in' }).click();
  await expect(page.locator('form').getByRole('alert')).toHaveText('Wrong password. Please try again.');
  await expect(page).toHaveURL(/\/login/);
});

test('logging in opens the page asked for, with a logout button', async ({ page }) => {
  await page.goto('/profiles');
  await page.getByLabel('Password').fill(E2E_PASSWORD);
  await page.getByRole('button', { name: 'Log in' }).click();
  await expect(page).toHaveURL(/\/profiles$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Profiles' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Log out' }).first()).toBeAttached();
  await expectNoSeriousA11yViolations(page);
});
