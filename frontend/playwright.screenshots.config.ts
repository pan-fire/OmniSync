import { defineConfig, devices } from '@playwright/test';

// The README screenshots (pnpm screenshots), kept apart from the smoke
// tests: `pnpm test:e2e` uses playwright.config.ts and never runs them.
// Like the smoke tests they drive the production build (run `pnpm build`
// first) against the mocked backend (e2e/mock-backend.ts). The raw captures
// land in test-results/screenshots/; scripts/optimize-screenshots.py then
// writes the compressed copies to docs/images/.

const PORT = Number(process.env.SCREENSHOTS_PORT ?? 3110);

export default defineConfig({
  testDir:   './e2e',
  // One page at a time: the captures do not race each other for the CPU.
  workers:   1,
  reporter:  'list',
  // Not test-results/ itself, which Playwright empties on each run.
  outputDir: 'test-results/screenshots-run',
  projects:  [{
    name:      'screenshots',
    testMatch: 'screenshots.spec.ts',
    use:       {
      ...devices['Desktop Chrome'],
      baseURL:           `http://127.0.0.1:${PORT}`,
      viewport:          { width: 1280, height: 800 },
      deviceScaleFactor: 2,
      locale:            'en-US',
      timezoneId:        'UTC',
      colorScheme:       'light',
    },
  }],
  webServer: {
    command: `node e2e/serve.mjs ${PORT}`,
    url:     `http://127.0.0.1:${PORT}/healthz`,
    env:     {
      BACKEND_URL:             'http://127.0.0.1:9',
      NEXT_TELEMETRY_DISABLED: '1',
    },
    reuseExistingServer: !process.env.CI,
    timeout:             60_000,
    stdout:              'pipe',
  },
});
