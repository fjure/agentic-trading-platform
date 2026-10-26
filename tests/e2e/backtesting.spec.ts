import { test, expect, type Page, type BrowserContext } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import type { BacktestRun, BacktestRunDetail, BacktestSummaryPage } from '../../apps/dashboard/src/app/api/generated/control-api/model/models';

// The owner starts canonical HTTP + real PostgreSQL + the restricted Backtest worker.
// No API interception, external provider, broker, or private credential is used.
test.skip(!process.env.BACKTEST_BROWSER_CONTAINER || !process.env.BACKTEST_BROWSER_ACTIVATION,
  'Requires isolated real Backtest API/PostgreSQL browser owner.');
test.describe.configure({ mode: 'serial' });
test.setTimeout(90_000);
const view = (page: Page) => page.locator('atl-backtesting');
let cookies: Awaited<ReturnType<BrowserContext['cookies']>> = [];
let successfulRun = '';
const start = process.env.BACKTEST_BROWSER_START ?? '2026-10-19T13:30:00Z';
const end = process.env.BACKTEST_BROWSER_END ?? '2026-10-19T14:30:00Z';

async function activate(page: Page): Promise<void> {
  if (cookies.length) await page.context().addCookies(cookies);
  await page.goto('/');
  if (!cookies.length) {
    await page.getByLabel('Aktivierungscode', { exact: true }).fill(process.env.BACKTEST_BROWSER_ACTIVATION!);
    await page.getByRole('button', { name: 'Sitzung aktivieren', exact: true }).click();
  }
  await expect(view(page).getByRole('heading', { name: 'Lokale Simulation', exact: true })).toBeVisible();
  await expect(view(page).getByLabel('Backtest Instrument', { exact: true })).toContainText('SPY');
  cookies = await page.context().cookies();
}
async function windows(page: Page, first = start, last = end): Promise<void> {
  await view(page).getByLabel('Training Start UTC', { exact: true }).fill(new Date(Date.parse(first) - 86400000).toISOString());
  await view(page).getByLabel('Training Ende UTC', { exact: true }).fill(first);
  await view(page).getByLabel('Test Start UTC', { exact: true }).fill(first);
  await view(page).getByLabel('Test Ende UTC exklusiv', { exact: true }).fill(last);
}
async function create(page: Page): Promise<BacktestRun> {
  const previousRun = new URL(page.url()).searchParams.get('backtestRun');
  const response = page.waitForResponse(reply => reply.request().method() === 'POST'
    && new URL(reply.url()).pathname === '/api/v1/research/backtests/runs');
  await view(page).getByRole('button', { name: 'Lokale Simulation beauftragen', exact: true }).click();
  const reply = await response;
  expect(reply.status()).toBe(202);
  await expect.poll(() => new URL(page.url()).searchParams.get('backtestRun')).not.toBe(previousRun);
  const runId = new URL(page.url()).searchParams.get('backtestRun');
  const persisted = await page.request.get(`/api/v1/research/backtests/runs/${runId}`);
  expect(persisted.status()).toBe(200);
  return (await persisted.json() as BacktestRunDetail).run;
}

test('strict form prevents invalid writes and real worker publishes exact independent fold metrics', async ({ page }) => {
  const tradingWrites: string[] = [];
  page.on('request', request => {
    if (request.method() !== 'GET' && /orders|paper|mode|kill|pause/.test(new URL(request.url()).pathname)) tradingWrites.push(request.url());
  });
  await activate(page); await windows(page);
  await expect(page.getByRole('navigation')).toContainText('Alpaca Paper');
  await expect(page.getByRole('heading', { name: 'Paper-Supervisor', exact: true })).toBeVisible();
  const submit = view(page).getByRole('button', { name: 'Lokale Simulation beauftragen', exact: true });
  await view(page).getByLabel('Backtest Startcash USD', { exact: true }).fill('1e4');
  await expect(submit).toBeDisabled();
  await view(page).getByLabel('Backtest Startcash USD', { exact: true }).fill('1000000000.00000001');
  await expect(submit).toBeDisabled();
  await view(page).getByLabel('Backtest Startcash USD', { exact: true }).fill('10000');
  await view(page).getByLabel('Test Ende UTC exklusiv', { exact: true }).fill('2099-10-19T14:30:00Z');
  await expect(submit).toBeDisabled(); await windows(page);
  await view(page).getByRole('combobox', { name: 'Backtest Strategie', exact: true }).click();
  await page.getByRole('option', { name: 'sma-cross-v1 · Version 1', exact: true }).click();
  await expect(page.getByRole('listbox', { name: 'Backtest Strategie', exact: true })).toHaveCount(0);
  await view(page).getByLabel('SMA fast', { exact: true }).fill('20');
  await view(page).getByLabel('SMA slow', { exact: true }).fill('20');
  await expect(submit).toBeDisabled();
  await view(page).getByRole('combobox', { name: 'Backtest Strategie', exact: true }).click();
  await page.getByRole('option', { name: 'buy-hold-v1 · Version 1', exact: true }).click();
  await expect(page.getByRole('listbox', { name: 'Backtest Strategie', exact: true })).toHaveCount(0);
  await expect(submit).toBeEnabled();
  const receipt = await create(page); successfulRun = receipt.runId;
  expect(receipt.request.startCash).toBe('10000');
  await expect(view(page).getByRole('region', { name: 'Simulationsdetail' }).or(view(page).locator('mat-card[aria-label="Simulationsdetail"]'))).toContainText('SUCCEEDED');
  const detailReply = await page.request.get(`/api/v1/research/backtests/runs/${successfulRun}`);
  expect(detailReply.status()).toBe(200);
  const detail = await detailReply.json() as BacktestRunDetail;
  expect(detail.run.publicationComplete).toBe(true);
  expect(detail.manifest?.outcomeInputs.length).toBeGreaterThan(0);
  const summaries = await page.request.get(`/api/v1/research/backtests/runs/${successfulRun}/results?resultType=summary`);
  const result = await summaries.json() as BacktestSummaryPage;
  expect(result.items).toHaveLength(1);
  expect(Number(result.items[0].metrics.endEquity)).toBe(12000); // 1000 shares * 12.
  expect(Number(result.items[0].metrics.netReturn)).toBe(.2);
  expect(result.items[0].references.map(x => x.strategyId).sort()).toEqual(['buy-hold-v1', 'flat-v1']);
  await expect(view(page).getByRole('article', { name: 'Foldresultat 0' })).toContainText(result.items[0].metrics.endEquity);
  await expect(view(page).getByRole('img', { name: 'Backtest Equitykurve' })).toBeVisible();
  await expect(view(page).getByRole('table', { name: 'Backtest Fills' })).toContainText('BUY');
  expect(tradingWrites).toEqual([]);
});

test('reload restores the persisted run, manifest and fixed fold references', async ({ page }) => {
  await activate(page);
  await page.goto(`/?backtestRun=${successfulRun}`);
  await expect(view(page).locator('mat-card[aria-label="Simulationsdetail"]')).toContainText(successfulRun);
  await expect(view(page).locator('mat-card[aria-label="Simulationsdetail"]')).toContainText('SUCCEEDED');
  await view(page).getByText('Unveränderliches Manifest mit Daten- und Warm-up-Belegen', { exact: true }).click();
  const manifest = view(page).getByLabel('Backtest Manifest', { exact: true });
  await expect(manifest).toContainText('backtest-simulator-v1');
  await expect(manifest).toContainText('observationCutoff');
  await expect(view(page)).toContainText('Referenz flat-v1');
  await expect(view(page)).toContainText('Referenz buy-hold-v1');
  await page.reload();
  await expect(view(page).getByRole('article', { name: 'Foldresultat 0' })).toContainText('12000');
});

test('missing historical proofs remain BLOCKED without success metrics', async ({ page }) => {
  await activate(page);
  await windows(page, '2025-10-20T13:30:00Z', '2025-10-20T14:30:00Z');
  const receipt = await create(page);
  const detail = view(page).locator('mat-card[aria-label="Simulationsdetail"]');
  await expect(detail).toContainText(receipt.runId);
  await expect(detail).toContainText('BLOCKED');
  await expect(detail).toContainText('Keine vollständigen Ergebniszeilen veröffentlicht');
  await expect(view(page).getByRole('article', { name: 'Foldresultat 0' })).toHaveCount(0);
  const response = await page.request.get(`/api/v1/research/backtests/runs/${receipt.runId}/results?resultType=summary`);
  const result = await response.json() as BacktestSummaryPage;
  expect(result.status).toBe('BLOCKED'); expect(result.items).toEqual([]);
});

test('expired session clears backtest results and stops backtest polling', async ({ page }) => {
  await activate(page);
  await page.context().clearCookies();
  const denied = page.waitForResponse(r => r.status() === 401 && r.url().includes('/research/backtests/'));
  await view(page).getByRole('button', { name: 'Simulationen aktualisieren', exact: true }).click();
  await denied;
  await expect(view(page).getByRole('article', { name: 'Foldresultat 0' })).toHaveCount(0);
  await expect(page.getByLabel('Aktivierungscode', { exact: true })).toBeVisible();
  const requests: string[] = [];
  page.on('request', r => { if (r.url().includes('/research/backtests/')) requests.push(r.url()); });
  await page.waitForTimeout(3000);
  expect(requests).toEqual([]);
});
