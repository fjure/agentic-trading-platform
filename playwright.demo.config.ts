import { defineConfig } from './apps/dashboard/node_modules/@playwright/test/index.mjs';

// The Python owner starts this server and owns its UUID database/container cleanup.
// Playwright still verifies exclusive ownership instead of attaching to a user lab.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = '1';
export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'demo-real.spec.ts',
  timeout: 180_000,
  expect: { timeout: 30_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  outputDir: './.tooling/output/playwright-demo-real',
  preserveOutput: 'never',
  use: {
    baseURL: 'http://127.0.0.1:4210',
    browserName: 'chromium',
    screenshot: 'off',
    trace: 'off',
    video: 'off',
  },
  webServer: {
    command: '"' + process.env.DEMO_BROWSER_PYTHON + '" -m tests.integration.demo_browser_support relay',
    url: 'http://127.0.0.1:4210/health',
    reuseExistingServer: false,
    timeout: 30_000,
    stdout: 'ignore',
    stderr: 'ignore',
  },
});
