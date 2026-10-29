import { test, expect, type Page, type BrowserContext, type Locator } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import { execFileSync } from 'node:child_process';
import type { BacktestAssessment, BacktestRun, BacktestRunDetail, BacktestReliabilityReportPage, BacktestSummaryPage } from '../../apps/dashboard/src/app/api/generated/control-api/model/models';

// Real canonical API, PostgreSQL and restricted worker. The startup race case
// delays one real GET; it never substitutes an API response.
test.skip(process.env.BACKTEST_BROWSER_RETROSPECTIVE !== '1' || !process.env.BACKTEST_BROWSER_ACTIVATION,
  'Requires the isolated retrospective browser owner.');
test.describe.configure({ mode: 'serial' });
test.setTimeout(90_000);
const BASE = '/api/v1/research/backtests';
const view = (page: Page) => page.locator('atl-backtesting');
const start = process.env.BACKTEST_BROWSER_RETRO_START!;
const end = process.env.BACKTEST_BROWSER_RETRO_END!;
let cookies: Awaited<ReturnType<BrowserContext['cookies']>> = [];
let assessmentId = '';
let runId = '';
let reportId = '';

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
async function choose(page: Page, scope: Locator, label: string, option: string): Promise<void> {
  await scope.getByLabel(label, { exact: true }).click();
  await page.getByRole('option', { name: option, exact: true }).click();
}
async function configure(page: Page, retrospective = true, first = start, last = end): Promise<void> {
  await choose(page, view(page), 'Backtest Instrument', 'QQQ');
  await choose(page, view(page), 'Forschungsmodus', retrospective ? 'Retrospektiv explorativ' : 'Strict PIT');
  await view(page).getByLabel('Backtest Startcash USD', { exact: true }).fill('1000');
  await view(page).getByLabel('Training Start UTC', { exact: true }).fill(new Date(Date.parse(first) - 30 * 60_000).toISOString());
  await view(page).getByLabel('Training Ende UTC', { exact: true }).fill(first);
  await view(page).getByLabel('Test Start UTC', { exact: true }).fill(first);
  await view(page).getByLabel('Test Ende UTC exklusiv', { exact: true }).fill(last);
}
async function preview(page: Page): Promise<BacktestAssessment> {
  const receipt = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === BASE + '/assessments');
  await view(page).getByRole('button', { name: 'Retrospektive Vorschau erstellen', exact: true }).click();
  const response = await receipt;
  expect(response.status()).toBe(202);
  const result = await response.json() as BacktestAssessment;
  await expect.poll(async () => {
    const current = await page.request.get(BASE + '/assessments/' + result.assessmentId);
    return (await current.json() as BacktestAssessment).status;
  }).not.toMatch(/^(ACCEPTED|RUNNING)$/);
  const persisted = await page.request.get(BASE + '/assessments/' + result.assessmentId);
  return await persisted.json() as BacktestAssessment;
}
async function runs(page: Page): Promise<number> {
  const response = await page.request.get(BASE + '/runs?limit=200');
  expect(response.status()).toBe(200);
  return (await response.json()).items.length;
}
async function context(scope: Locator): Promise<void> {
  for (const label of ['Research Mode', 'Data Snapshot', 'Data Quality', 'Corporate Action Status', 'Result Limitations', 'Experiment Version']) {
    await expect(scope.getByText(label, { exact: true }).first()).toBeVisible();
  }
}
function currentLicense(status: 'UNKNOWN' | 'BLOCKED'): void {
  const owner = process.env.BACKTEST_BROWSER_CONTAINER!;
  if (!/^atl-backtest-browser-[0-9a-f]{20}-app$/.test(owner)) throw new Error('Isolated browser owner required');
  // Test-owner control plane only; the browser/API role has no Research writes.
  const script = `from sqlalchemy import create_engine,text
engine=create_engine('postgresql+psycopg://lab_migration@postgres/lab',hide_parameters=True)
with engine.begin() as connection:
    connection.execute(text('UPDATE research.source_status SET license_status=:status WHERE singleton'),{'status':${JSON.stringify(status)}})
engine.dispose()`;
  execFileSync('docker', ['exec', owner, 'python', '-c', script], { stdio: 'pipe', timeout: 15_000 });
}

test('saved history loads all quality overviews without overloading bounded admission', async ({ page }) => {
  const failures: number[] = [];
  page.on('response', response => {
    if (response.url().includes(BASE + '/reliability-reports/') && response.status() === 503) failures.push(response.status());
  });
  await activate(page);
  const saved = view(page).getByRole('article', { name: /^Simulation / });
  await expect(saved).toHaveCount(4);
  for (let index = 0; index < 4; index++) {
    await expect(saved.nth(index).locator('atl-backtest-context')).toContainText('INTEGRITY');
  }
  expect(failures).toEqual([]);
});

test('Strict remains default and the same current-ingested backfill blocks without automatic fallback', async ({ page }) => {
  await activate(page);
  await expect(view(page).getByLabel('Forschungsmodus', { exact: true })).toContainText('Strict PIT');
  await configure(page, false);
  const writes: string[] = [];
  page.on('request', request => { if (request.method() === 'POST' && request.url().includes(BASE)) writes.push(new URL(request.url()).pathname); });
  const receipt = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === BASE + '/runs');
  await view(page).getByRole('button', { name: 'Lokale Simulation beauftragen', exact: true }).click();
  const response = await receipt;
  expect(response.status()).toBe(202);
  const run = await response.json() as BacktestRun;
  const detail = view(page).locator('mat-card[aria-label="Simulationsdetail"]');
  await expect(detail).toContainText(run.runId);
  await expect(detail).toContainText('BLOCKED');
  await expect(view(page).getByLabel('Forschungsmodus', { exact: true })).toContainText('Strict PIT');
  await page.waitForTimeout(1200);
  expect(writes).toEqual([BASE + '/runs']);
  await expect(view(page).getByRole('article', { name: 'Foldresultat 0', exact: true })).toHaveCount(0);
});

test('preview does not create a run, form changes invalidate its binding, and a fresh preview persists', async ({ page }) => {
  await activate(page);
  await configure(page);
  const before = await runs(page);
  const first = await preview(page);
  expect(first.status).toBe('SUCCEEDED');
  expect(first.eligibility).toBe('EXPLORATORY_WITH_LIMITATIONS');
  await expect(view(page).getByRole('button', { name: 'Retrospektive Simulation beauftragen', exact: true })).toBeEnabled();
  expect(await runs(page)).toBe(before);
  await view(page).getByLabel('Backtest Gebühr pro Fill USD', { exact: true }).fill('1');
  await expect(view(page).getByRole('button', { name: 'Retrospektive Simulation beauftragen', exact: true })).toBeDisabled();
  await expect.poll(() => new URL(page.url()).searchParams.get('backtestAssessment')).not.toBe(first.assessmentId);
  await view(page).getByLabel('Backtest Gebühr pro Fill USD', { exact: true }).fill('0');
  const current = await preview(page);
  expect(current.status).toBe('SUCCEEDED');
  expect(current.assessmentId).not.toBe(first.assessmentId);
  assessmentId = current.assessmentId;
  await expect.poll(() => new URL(page.url()).searchParams.get('backtestAssessment')).toBe(assessmentId);
  expect(await runs(page)).toBe(before);
});

test('editing during initial history loading prevents stale URL assessment restoration', async ({ page }) => {
  await activate(page);
  let release!: () => void;
  let received!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const requested = new Promise<void>(resolve => { received = resolve; });
  const restores: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname === BASE + '/assessments/' + assessmentId) restores.push(request.url());
  });
  await page.route('**/api/v1/research/backtests/runs?*', async route => {
    received();
    await held;
    await route.continue();
  }, { times: 1 });
  try {
    await page.goto('/?backtestAssessment=' + assessmentId);
    await requested;
    await view(page).getByLabel('Backtest Startcash USD', { exact: true }).fill('3210');
    const loaded = page.waitForResponse(response => response.request().method() === 'GET'
      && new URL(response.url()).pathname === BASE + '/runs');
    release();
    expect((await loaded).status()).toBe(200);
    await expect(view(page).getByRole('article', { name: /^Simulation / })).toHaveCount(5);
    await expect(view(page).getByLabel('Backtest Startcash USD', { exact: true })).toHaveValue('3210');
    await expect(view(page).getByLabel('Forschungsmodus', { exact: true })).toContainText('Strict PIT');
    expect(new URL(page.url()).searchParams.has('backtestAssessment')).toBe(false);
    expect(restores).toEqual([]);
  } finally {
    release();
  }
});

test('preview and run reload with honest context, exact results and paginated immutable report', async ({ page }) => {
  await activate(page);
  await page.goto('/?backtestAssessment=' + assessmentId);
  await expect(view(page).getByRole('button', { name: 'Retrospektive Simulation beauftragen', exact: true })).toBeEnabled();
  await expect(view(page).getByLabel('Forschungsmodus', { exact: true })).toContainText('Retrospektiv explorativ');
  const receipt = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === BASE + '/runs')
    .then(async response => ({ status: response.status(), run: await response.json() as BacktestRun }));
  await view(page).getByRole('button', { name: 'Retrospektive Simulation beauftragen', exact: true }).click();
  const response = await receipt;
  expect(response.status).toBe(202);
  runId = response.run.runId;
  const detail = view(page).locator('mat-card[aria-label="Simulationsdetail"]');
  await expect(detail).toContainText('SUCCEEDED');
  await expect(detail).toContainText(runId);
  await context(detail);
  await expect(detail).toContainText('RETROSPECTIVE_EXPLORATORY');
  const persisted = await page.request.get(BASE + '/runs/' + runId);
  const value = await persisted.json() as BacktestRunDetail;
  reportId = value.run.reliabilityReportId!;
  expect(value.manifest).toBeNull();
  expect(value.retrospectiveManifest).toBeTruthy();
  const summary = await page.request.get(BASE + '/runs/' + runId + '/results');
  const metrics = await summary.json() as BacktestSummaryPage;
  expect(metrics.items).toEqual([]);
  expect(metrics.retrospectiveItems![0].metrics.endEquity).toMatch(/^1000(?:\.0+)?$/);
  expect(metrics.retrospectiveItems![0].coverage.corporateActionStatus).toBe('UNKNOWN');
  await expect(view(page).getByRole('article', { name: 'Foldresultat 0', exact: true })).toContainText('1000');
  await expect(view(page).getByRole('table', { name: 'Backtest Fills', exact: true })).toContainText('Simulierter Marktcutoff (kein damaliger Datenbeleg)');
  await expect(view(page).getByRole('table', { name: 'Backtest Fills', exact: true })).not.toContainText('Beobachtungscutoff');
  await detail.getByRole('button', { name: 'Qualitätsreport öffnen', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: 'Qualitäts- und Grenzenreport', exact: true });
  await expect(dialog).toBeVisible();
  await context(dialog);
  await expect(dialog).toContainText('NOT_PROVEN');
  await expect(dialog).toContainText('UNKNOWN');
  await page.screenshot({ path: '.tooling/output/rrb07-report.png' });
  const reportResponse = await page.request.get(BASE + '/reliability-reports/' + reportId);
  const hash = (await reportResponse.json() as BacktestReliabilityReportPage).report.reportHash;
  await choose(page, dialog, 'Reportbereich', 'Revisionen');
  await expect(dialog.getByRole('article', { name: 'Reportrevision', exact: true })).toHaveCount(20);
  await dialog.getByRole('button', { name: 'Weitere Reportzeilen', exact: true }).click();
  await expect.poll(() => dialog.getByRole('article', { name: 'Reportrevision', exact: true }).count()).toBeGreaterThan(20);
  const filtered = page.waitForResponse(reply => reply.request().method() === 'GET' && new URL(reply.url()).pathname === BASE + '/reliability-reports/' + reportId && new URL(reply.url()).searchParams.get('category') === 'LICENSE');
  await choose(page, dialog, 'Reportkategorie', 'LICENSE');
  expect(new URL((await filtered).url()).searchParams.has('cursor')).toBe(false);
  await expect(dialog.getByRole('article', { name: 'Reportrevision', exact: true })).toHaveCount(0);
  await expect.poll(() => new URL(page.url()).searchParams.get('backtestReport')).toBe(reportId);
  await page.reload();
  dialog = page.getByRole('dialog', { name: 'Qualitäts- und Grenzenreport', exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(hash);
  await dialog.getByRole('button', { name: 'Report schließen', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.reload();
  await expect(view(page).locator('mat-card[aria-label="Simulationsdetail"]')).toContainText(runId);
  await expect(view(page).getByRole('article', { name: 'Foldresultat 0', exact: true })).toContainText('1000');
});

test('explicit start refreshes current warnings and refuses a newly denied license before POST', async ({ page }) => {
  await activate(page);
  await page.goto('/?backtestAssessment=' + assessmentId);
  const submit = view(page).getByRole('button', { name: 'Retrospektive Simulation beauftragen', exact: true });
  await expect(submit).toBeEnabled();
  const before = await runs(page);
  const writes: string[] = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === BASE + '/runs') writes.push(request.url());
  });
  try {
    currentLicense('BLOCKED');
    await submit.click();
    await expect(view(page)).toContainText('LICENSE_DENIED');
    await expect(submit).toBeDisabled();
    expect(writes).toEqual([]);
    expect(await runs(page)).toBe(before);
  } finally {
    currentLicense('UNKNOWN');
  }
});

test('a known split blocks even flat preview and never creates a simulation', async ({ page }) => {
  await activate(page);
  await configure(page, true, process.env.BACKTEST_BROWSER_SPLIT_START!, process.env.BACKTEST_BROWSER_SPLIT_END!);
  await choose(page, view(page), 'Backtest Strategie', 'flat-v1 · Version 1');
  const before = await runs(page);
  const current = await preview(page);
  expect(current.status).toBe('BLOCKED');
  expect(current.blockers.some(finding => finding.code === 'STRUCTURAL_CORPORATE_ACTION')).toBe(true);
  await expect(view(page)).toContainText('STRUCTURAL_CORPORATE_ACTION');
  await expect(view(page).getByRole('button', { name: 'Retrospektive Simulation beauftragen', exact: true })).toBeDisabled();
  expect(await runs(page)).toBe(before);
});

test('session loss closes report, clears research data and stops polling without POST retries', async ({ page }) => {
  await activate(page);
  await page.goto('/?backtestRun=' + runId + '&backtestReport=' + reportId);
  const dialog = page.getByRole('dialog', { name: 'Qualitäts- und Grenzenreport', exact: true });
  await expect(dialog).toBeVisible();
  await page.context().clearCookies();
  const denied = page.waitForResponse(response => response.status() === 401 && response.url().includes(BASE));
  await dialog.getByRole('button', { name: 'Report aktualisieren', exact: true }).click();
  await denied;
  await expect(dialog).toHaveCount(0);
  await expect(page.getByLabel('Aktivierungscode', { exact: true })).toBeVisible();
  await expect(page.locator('atl-backtesting')).toHaveCount(0);
  const requests: string[] = [];
  page.on('request', request => { if (request.url().includes(BASE)) requests.push(request.method()); });
  await page.waitForTimeout(3000);
  expect(requests).toEqual([]);
});
