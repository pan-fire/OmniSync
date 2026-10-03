import { defineConfig, devices } from '@playwright/test';

// Browser smoke tests (pnpm test:e2e) against the production build, served
// the way the image serves it (e2e/serve.mjs): run `pnpm build` first. The
// backend is mocked in the browser (e2e/mock-backend.ts),
// so BACKEND_URL points at a closed port: a request that slips past the mock
// fails instead of reaching a real backend.
//
// Two servers run the same build: one with the optional login off, one with
// it on (a plain OMNISYNC_UI_PASSWORD, so no hash is needed).
//
// Browsers: `pnpm exec playwright install chromium` (CI adds --with-deps).

// E2E_PORT moves both servers (default 3100 and 3101).
const APP_PORT = Number(process.env.E2E_PORT ?? 3100);
const LOGIN_PORT = APP_PORT + 1;
export const E2E_PASSWORD = 'e2e-password';

const serverEnv = {
  BACKEND_URL:             'http://127.0.0.1:9',
  NEXT_TELEMETRY_DISABLED: '1',
};

export default defineConfig({
  testDir:       './e2e',
  fullyParallel: true,
  forbidOnly:    !!process.env.CI,
  retries:       process.env.CI ? 1 : 0,
  workers:       process.env.CI ? 2 : undefined,
  reporter:      process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use:           {
    ...devices['Desktop Chrome'],
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name:      'app',
      testMatch: 'smoke.spec.ts',
      use:       { baseURL: `http://127.0.0.1:${APP_PORT}` },
    },
    {
      name:      'login',
      testMatch: 'login.spec.ts',
      use:       { baseURL: `http://127.0.0.1:${LOGIN_PORT}` },
    },
  ],
  webServer: [
    {
      command:             `node e2e/serve.mjs ${APP_PORT}`,
      url:                 `http://127.0.0.1:${APP_PORT}/healthz`,
      env:                 serverEnv,
      reuseExistingServer: !process.env.CI,
      timeout:             60_000,
      stdout:              'pipe',
    },
    {
      command: `node e2e/serve.mjs ${LOGIN_PORT}`,
      url:     `http://127.0.0.1:${LOGIN_PORT}/healthz`,
      env:     {
        ...serverEnv,
        OMNISYNC_UI_PASSWORD:       E2E_PASSWORD,
        OMNISYNC_UI_SESSION_SECRET: 'e2e-session-secret-at-least-32-characters',
      },
      reuseExistingServer: !process.env.CI,
      timeout:             60_000,
      stdout:              'pipe',
    },
  ],
});
