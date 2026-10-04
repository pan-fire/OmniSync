import { expect, test, type Locator } from '@playwright/test';
import { expectNoSeriousA11yViolations } from './axe';
import { PROFILE, SNAPSHOTS, mockBackend } from './mock-backend';
import fa from '../src/i18n/locales/fa.json';
import packageJson from '../package.json';

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
  // The mocked backend runs the UI's version: no mismatch note.
  const version = page.getByTestId('version-info');
  await expect(version).toContainText(packageJson.version);
  await expect(version).not.toContainText('Backend');
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

/** Whether `inner` lies wholly inside `outer` (a pixel of rounding allowed). */
async function inside (inner: Locator, outer: Locator): Promise<boolean> {
  const a = await inner.boundingBox();
  const b = await outer.boundingBox();
  return !!a && !!b && a.x >= b.x - 1 && a.y >= b.y - 1 &&
    a.x + a.width <= b.x + b.width + 1 && a.y + a.height <= b.y + b.height + 1;
}

test('a long snapshot list scrolls inside its card', async ({ page }) => {
  await page.goto(`/profiles/${PROFILE.slug}?tab=backups`);
  await page.getByRole('button', { name: 'History' }).click();
  const restores = page.getByRole('button', { name: 'Restore', exact: true });
  await expect(restores).toHaveCount(SNAPSHOTS.length);
  const area = page.locator('[data-slot="scroll-area"]').filter({ has: restores.first() });
  const viewport = area.locator('[data-slot="scroll-area-viewport"]');
  const card = page.locator('[data-slot="card"]').filter({ has: area });
  // Capped and scrolling, and the card holds the whole list.
  expect(await viewport.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
  expect(await inside(area, card)).toBe(true);
  // The keyboard reaches the last snapshot, which scrolls into view.
  const last = restores.last();
  await restores.first().focus();
  for (let i = 0; i < 4 * SNAPSHOTS.length && !(await last.evaluate((el) => el === document.activeElement)); i++) {
    await page.keyboard.press('Tab');
  }
  await expect(last).toBeFocused();
  expect(await inside(last, area)).toBe(true);
  await expectNoSeriousA11yViolations(page);
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

  // The profile header wraps instead of cutting the name off, and the tab
  // list starts at its first tab (or shows the active one), in both
  // directions.
  for (const locale of ['en', 'fa'] as const) {
    test(`the profile header and tabs fit (${locale})`, async ({ page, baseURL }) => {
      await page.context().addCookies([{ name: 'omnisync-locale', value: locale, url: baseURL! }]);
      await page.goto(`/profiles/${PROFILE.slug}`);
      const title = page.getByRole('heading', { level: 1, name: PROFILE.name });
      await expect(title).toBeVisible();
      expect(await title.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
      expect(await inside(title, page.locator('main'))).toBe(true);
      const tabs = page.getByRole('tablist');
      expect(await inside(page.getByRole('tab').first(), tabs)).toBe(true);
      // Nothing makes the page itself scroll sideways.
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await expectNoSeriousA11yViolations(page);

      await page.goto(`/profiles/${PROFILE.slug}?tab=trash`);
      const trash = page.getByRole('tab', { selected: true });
      await expect(trash).toBeVisible();
      await expect.poll(() => inside(trash, tabs)).toBe(true);
    });
  }
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
