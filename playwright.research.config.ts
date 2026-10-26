import { defineConfig } from './apps/dashboard/node_modules/@playwright/test/index.mjs';

const python = process.env.RESEARCH_BROWSER_PYTHON;
const owned = !!process.env.RESEARCH_BROWSER_CONTAINER && !!python && !!process.env.RESEARCH_BROWSER_ACTIVATION;
if (owned && /["\r\n&|<>^%]/.test(python!)) throw new Error('Invalid isolated Research browser Python path.');
process.env.PLAYWRIGHT_NO_COPY_PROMPT = '1';

export default defineConfig({
  testDir: './tests/e2e', testMatch: 'research.spec.ts', timeout: 90_000,
  expect: { timeout: 20_000 }, workers: 1, fullyParallel: false, retries: 0,
  reporter: [['list']], outputDir: './.tooling/output/playwright-research-real', preserveOutput: 'never',
  use: { baseURL: 'http://127.0.0.1:4221', browserName: 'chromium', screenshot: 'off', trace: 'off', video: 'off' },
  webServer: owned ? {
    command: `"${python}" -m tests.integration.research_browser_support relay`,
    url: 'http://127.0.0.1:4221/health', reuseExistingServer: false, timeout: 30_000,
    stdout: 'ignore', stderr: 'ignore',
  } : undefined,
});
