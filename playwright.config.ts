import { defineConfig } from './apps/dashboard/node_modules/@playwright/test/index.mjs';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.spec.ts',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  workers: 1,
  fullyParallel: false,
  reporter: [['list']],
  outputDir: './.tooling/output/playwright',
  use: {
    baseURL: 'http://127.0.0.1:4200',
    browserName: 'chromium',
    screenshot: 'off',
    trace: 'off',
    video: 'off',
  },
  webServer: {
    command: 'node tests/e2e/server.mjs',
    url: 'http://127.0.0.1:4200/__test/ready',
    reuseExistingServer: false,
    timeout: 15_000,
  },
});
