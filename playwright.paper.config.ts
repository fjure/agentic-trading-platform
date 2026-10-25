import { defineConfig } from './apps/dashboard/node_modules/@playwright/test/index.mjs';

// The isolated Python owner creates and tears down every container/database.
// Never attach this acceptance suite to an already running user application.
const python = process.env.PAPER_BROWSER_PYTHON;
const owned = !!process.env.PAPER_BROWSER_CONTAINER && !!python && !!process.env.PAPER_BROWSER_ACTIVATION;
if (owned && /["\r\n&|<>^%]/.test(python!)) throw new Error('Invalid isolated Paper browser Python path.');
process.env.PLAYWRIGHT_NO_COPY_PROMPT = '1';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: process.env.PAPER_BROWSER_DAY_BOUNDARY === '1' ? 'paper-day-boundary.spec.ts' : process.env.PAPER_BROWSER_MODEL === '1' ? 'paper-model-real.spec.ts' : process.env.PAPER_BROWSER_SUBMIT_CRASH === '1' ? 'paper-submit-crash.spec.ts' : 'paper-real.spec.ts',
  timeout: 240_000,
  expect: { timeout: 30_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  outputDir: './.tooling/output/playwright-paper-real',
  preserveOutput: 'never',
  use: {
    baseURL: 'http://127.0.0.1:4220',
    browserName: 'chromium',
    screenshot: 'off',
    trace: 'off',
    video: 'off',
  },
  webServer: owned ? {
    command: `"${python}" -m tests.integration.paper_browser_support relay`,
    url: 'http://127.0.0.1:4220/health',
    reuseExistingServer: false,
    timeout: 30_000,
    stdout: 'ignore',
    stderr: 'ignore',
  } : undefined,
});
