import { defineConfig, devices } from '@playwright/test';

// The real-browser smoke against a running OmniSync (the images built from
// this checkout, the real backend and its real API token): nothing is
// mocked and no server is started here. scripts/test-fullstack-e2e.sh
// installs the stack, sets the web UI login, creates the profile and the
// conflict these tests look for, and runs them with
//
//   STACK_URL         the web UI, e.g. http://127.0.0.1:13180
//   STACK_PASSWORD    its login password
//   STACK_PROFILE     the profile's name, STACK_SLUG its slug
//   STACK_CONFLICT    the path of the profile's unresolved conflict
//
// Browsers: `pnpm exec playwright install chromium` (CI adds --with-deps).

export default defineConfig({
  testDir:       './e2e/stack',
  fullyParallel: false,
  workers:       1,
  forbidOnly:    !!process.env.CI,
  retries:       0,
  reporter:      'list',
  timeout:       60_000,
  use:           {
    ...devices['Desktop Chrome'],
    baseURL: process.env.STACK_URL,
    trace:   'retain-on-failure',
  },
  outputDir: 'test-results/stack',
});
