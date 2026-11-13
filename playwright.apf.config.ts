import { defineConfig } from './apps/dashboard/node_modules/@playwright/test/index.mjs';

const origin = 'http://127.0.0.1:4221';
const python = process.env.RESEARCH_BROWSER_PYTHON;
if (!process.env.APF_BROWSER_ACTIVATION || !process.env.RESEARCH_BROWSER_CONTAINER ||
    !python || /["\r\n&|<>^%]/.test(python)) {
  throw new Error('An isolated APF browser owner with a valid Python path is required.');
}

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'agent-research-integrated.spec.ts',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [['list']],
  outputDir: './.tooling/output/playwright-apf',
  preserveOutput: 'never',
  use: { baseURL: origin, browserName: 'chromium', screenshot: 'off', trace: 'off', video: 'off' },
  webServer: {
    command: `"${python}" -m tests.integration.research_browser_support relay`,
    url: `${origin}/health`,
    reuseExistingServer: false,
    timeout: 30_000,
    stdout: 'ignore',
    stderr: 'ignore',
  },
});
