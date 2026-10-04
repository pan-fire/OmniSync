import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { ansiToHtml } from './ansi-html';
import { NOW, PROFILE, mockBackend } from './mock-backend';

// The README screenshots (pnpm screenshots, playwright.screenshots.config.ts).
// Each test opens one page on the mocked backend, waits until its data has
// rendered, and saves a capture to test-results/screenshots/<name>.png.
// scripts/optimize-screenshots.py compresses them into docs/images/.

const OUT = 'test-results/screenshots';

let unmocked: string[];
let pageErrors: string[];

test.beforeEach(async ({ page }) => {
  pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  // Relative times ("14 minutes ago") match the data, whenever this runs.
  await page.clock.setFixedTime(NOW);
  unmocked = await mockBackend(page);
});

test.afterEach(() => {
  // A request the mock does not answer would show up as an error or an
  // empty card in the picture.
  expect(unmocked, 'requests the mock does not answer').toEqual([]);
  expect(pageErrors, 'uncaught errors in the page').toEqual([]);
});

/** Open `path` with animations off, so nothing is caught half-way. */
async function open (page: Page, path: string): Promise<void> {
  await page.goto(path);
  await page.addStyleTag({
    content: '*, *::before, *::after { transition: none !important; animation: none !important; caret-color: transparent !important; }',
  });
}

/** Wait for the fonts, check no toast is up, then save the capture. */
async function capture (page: Page, name: string): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  await expect(page.locator('[data-sonner-toast]')).toHaveCount(0);
  await page.screenshot({ path: `${OUT}/${name}.png` });
}

test.describe('the dashboard', () => {
  // 160px taller than the other pages, so all four profiles fit.
  test.use({ viewport: { width: 1280, height: 960 } });

  test('dashboard', async ({ page }) => {
    await open(page, '/');
    await expect(page.getByTestId(`dashboard-profile-${PROFILE.slug}`).getByTestId('sync-progress')).toBeVisible();
    await expect(page.getByTestId('version-info')).toBeVisible();
    await capture(page, 'dashboard');
  });

  test('dashboard, dark', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await open(page, '/');
    await expect(page.locator('html')).toHaveClass(/dark/);
    await expect(page.getByTestId(`dashboard-profile-${PROFILE.slug}`).getByTestId('sync-progress')).toBeVisible();
    await capture(page, 'dashboard-dark');
  });
});

test('profile', async ({ page }) => {
  await open(page, `/profiles/${PROFILE.slug}`);
  await expect(page.getByRole('heading', { level: 1, name: PROFILE.name })).toBeVisible();
  await expect(page.getByTestId('sync-progress').first()).toBeVisible();
  await capture(page, 'profile');
});

test('differences', async ({ page }) => {
  await open(page, `/profiles/${PROFILE.slug}?tab=differences`);
  await expect(page.getByText('Work/budget-2026.xlsx').first()).toBeVisible();
  await capture(page, 'differences');
});

test('remotes', async ({ page }) => {
  await open(page, '/remotes');
  await expect(page.getByRole('heading', { level: 1, name: 'Remotes' })).toBeVisible();
  await expect(page.getByText(/ used of /)).toHaveCount(3);
  await capture(page, 'remotes');
});

test('backups', async ({ page }) => {
  await open(page, `/profiles/${PROFILE.slug}?tab=backups`);
  await page.getByRole('button', { name: 'History' }).click();
  await expect(page.getByRole('button', { name: /Browse/ })).toHaveCount(4);
  await capture(page, 'backups');
});

test.describe('the snapshot browser', () => {
  // The dialog is taller than 800px: show it whole, restore button included.
  test.use({ viewport: { width: 1280, height: 960 } });

  test('snapshot browser', async ({ page }) => {
    await open(page, `/profiles/${PROFILE.slug}?tab=backups`);
    await page.getByRole('button', { name: 'History' }).click();
    await page.getByRole('button', { name: /Browse/ }).first().click();
    const browser = page.getByRole('dialog');
    await browser.getByRole('checkbox', { name: /Taxes/ }).check();
    await browser.getByRole('checkbox', { name: /address-book\.vcf/ }).check();
    await expect(browser.getByText('2 selected')).toBeVisible();
    await capture(page, 'snapshot-browser');
  });
});

test('wizard, OAuth step', async ({ page }) => {
  await open(page, '/remotes');
  await page.getByRole('button', { name: 'Setup Wizard' }).click();
  const wizard = page.getByRole('dialog');
  await wizard.getByRole('button', { name: /Google Drive/ }).click();
  await wizard.getByLabel('Remote name').fill('gdrive-shared');
  await wizard.getByRole('button', { name: /next/i }).click();
  await wizard.getByLabel(/Client ID/).fill('123456789012-example.apps.googleusercontent.com');
  await wizard.getByLabel(/Client Secret/).fill('example-client-secret');
  await expect(wizard.getByTestId('own-app-redirect-uri')).toContainText('/api/wizard/oauth/callback');
  await capture(page, 'wizard-oauth');
});

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });

  test('dashboard, mobile', async ({ page }) => {
    await open(page, '/');
    await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible();
    await expect(page.getByText('dropbox reachable')).toBeVisible();
    await capture(page, 'dashboard-mobile');
  });
});

// The terminal UI: its Go test writes the dashboard's ANSI output (same
// made-up data), rendered here as a terminal window. Skipped without Go.
test('terminal UI dashboard', async ({ page }) => {
  const ansiFile = resolve(OUT, 'tui-dashboard.ansi');
  mkdirSync(OUT, { recursive: true });
  try {
    execFileSync('go', ['test', './test/ui', '-run', '^TestScreenshot_Dashboard$', '-count=1'], {
      cwd:   resolve('../tui'),
      env:   { ...process.env, OMNISYNC_TUI_SCREENSHOT: ansiFile },
      stdio: 'pipe',
    });
  } catch (error) {
    test.skip((error as { code?: string }).code === 'ENOENT', 'Go is not installed');
    throw error;
  }
  await page.setContent(ansiToHtml(readFileSync(ansiFile, 'utf8'), 'osync'));
  await page.locator('.window').screenshot({ path: `${OUT}/tui-dashboard.png`, omitBackground: true });
});
