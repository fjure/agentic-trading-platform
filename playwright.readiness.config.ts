import { defineConfig } from './apps/dashboard/node_modules/@playwright/test/index.mjs';

const python = process.env.READINESS_BROWSER_PYTHON;
const actual = !!process.env.READINESS_BROWSER_CONTAINER && !!python && !!process.env.READINESS_BROWSER_ACTIVATION;
if (actual && /["\r\n&|<>^%]/.test(python!)) throw new Error('Invalid isolated Readiness Python path.');
process.env.PLAYWRIGHT_NO_COPY_PROMPT = '1';
export default defineConfig({
  testDir: './tests/e2e', testMatch: 'readiness.spec.ts', timeout: 45_000,
  expect: { timeout: 15_000 }, workers: 1, fullyParallel: false, retries: 0,
  reporter: [['list']], outputDir: './.tooling/output/playwright-readiness', preserveOutput: 'never',
  use: { baseURL: actual ? 'http://127.0.0.1:4224' : 'http://127.0.0.1:4200', browserName: 'chromium', screenshot: 'off', trace: 'off', video: 'off' },
  webServer: actual ? {
    command: `"${python}" -m tests.integration.readiness_browser_support relay`,
    url: 'http://127.0.0.1:4224/health', reuseExistingServer: false, timeout: 30_000,
    stdout: 'ignore', stderr: 'ignore',
  } : {
    command: 'node tests/e2e/server.mjs', url: 'http://127.0.0.1:4200/__test/ready',
    reuseExistingServer: false, timeout: 15_000,
  },
});
