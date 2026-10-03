import { expect, test } from '@playwright/test';
import { expectNoSeriousA11yViolations } from './axe';
import { PROFILE, mockBackend } from './mock-backend';
import fa from '../src/i18n/locales/fa.json';

// The login is off on this server (project "app" in playwright.config.ts).

let pageErrors: string[];

test.beforeEach(async ({ page }) => {
  pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await mockBackend(page);
});

test.afterEach(() => {
  expect(pageErrors, 'uncaught errors in the page').toEqual([]);
});

test('the dashboard loads', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
  await expect(page.getByText(PROFILE.name).first()).toBeVisible();
  // The login is off: no logout button.
  await expect(page.getByRole('button', { name: 'Log out' })).toHaveCount(0);
  await expectNoSeriousA11yViolations(page);
});

test('the profile list', async ({ page }) => {
  await page.goto('/profiles');
  await expect(page.getByRole('heading', { level: 1, name: 'Profiles' })).toBeVisible();
  await expect(page.getByRole('link', { name: new RegExp(PROFILE.name) }).first()).toBeVisible();
  await expectNoSeriousA11yViolations(page);
});

test('a profile', async ({ page }) => {
  await page.goto(`/profiles/${PROFILE.slug}`);
  await expect(page.getByRole('heading', { level: 1, name: PROFILE.name })).toBeVisible();
  await expect(page.getByText(PROFILE.local_dir).first()).toBeVisible();
  await expectNoSeriousA11yViolations(page);
});

test('the settings', async ({ page }) => {
  await page.goto('/config');
  await expect(page.getByRole('heading', { level: 1, name: 'Configuration' })).toBeVisible();
  await expect(page.getByLabel('Log level')).toBeVisible();
  await expectNoSeriousA11yViolations(page);
});

test('the login page redirects home while the login is off', async ({ page }) => {
  await page.goto('/login');
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
});

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test('the navigation is a drawer', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
    // The desktop sidebar is hidden; the menu button opens the drawer.
    await expect(page.getByRole('complementary', { name: 'Main navigation' })).toBeHidden();
    await page.getByRole('button', { name: 'Open navigation menu' }).click();
    const drawer = page.getByRole('dialog', { name: 'Main navigation' });
    await expect(drawer).toBeVisible();
    await expectNoSeriousA11yViolations(page);
    await drawer.getByRole('link', { name: 'Profiles' }).click();
    await expect(page).toHaveURL(/\/profiles$/);
    await expect(drawer).toBeHidden();
  });
});

test('Persian renders right to left', async ({ page, baseURL }) => {
  await page.context().addCookies([{ name: 'omnisync-locale', value: 'fa', url: baseURL! }]);
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('html')).toHaveAttribute('lang', 'fa');
  await expect(page.getByRole('heading', { level: 1, name: fa.dashboard.title })).toBeVisible();
  // The layout follows the direction: the sidebar is on the right.
  const sidebar = await page.getByRole('complementary', { name: fa.nav.sidebar }).boundingBox();
  const main = await page.locator('main').boundingBox();
  expect(sidebar && main && sidebar.x > main.x).toBe(true);
  await expectNoSeriousA11yViolations(page);
});
