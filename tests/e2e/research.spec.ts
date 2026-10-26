import { test, expect, type Page, type BrowserContext } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import type { ResearchBarPage, ResearchJobPage, ResearchSourceCatalog } from '../../apps/dashboard/src/app/api/generated/control-api/model/models';

// Dedicated owner must provide the real Control API + PostgreSQL + local provider
// double. Never intercept this application's API or treat the generic fake server
// as integration evidence. No private provider credentials are consumed here.
test.skip(!process.env.RESEARCH_BROWSER_CONTAINER || !process.env.RESEARCH_BROWSER_ACTIVATION,
  'Requires an isolated real Research API/PostgreSQL browser owner.');
test.setTimeout(60_000);
test.describe.configure({ mode: 'serial' });
let cookies: Awaited<ReturnType<BrowserContext['cookies']>> = [];
const research = (page: Page) => page.locator('atl-research');
const start = process.env.RESEARCH_BROWSER_START ?? '2026-10-23T13:30:00Z';
const end = process.env.RESEARCH_BROWSER_END ?? '2026-10-23T20:00:00Z';
const knowledge = process.env.RESEARCH_BROWSER_KNOWLEDGE ?? '2026-10-25T12:00:00Z';

async function activate(page: Page): Promise<void> {
  if (cookies.length) await page.context().addCookies(cookies);
  await page.goto('/');
  if (!cookies.length) {
    await page.getByLabel('Aktivierungscode', { exact: true }).fill(process.env.RESEARCH_BROWSER_ACTIVATION!);
    await page.getByRole('button', { name: 'Sitzung aktivieren', exact: true }).click();
  }
  await expect(research(page).getByRole('heading', { name: 'Research', exact: true })).toBeVisible();
  cookies = await page.context().cookies();
}

async function filters(page: Page): Promise<void> {
  await research(page).getByLabel('Research Start UTC', { exact: true }).fill(start);
  await research(page).getByLabel('Research Ende UTC exklusiv', { exact: true }).fill(end);
  await research(page).getByLabel('Bewertung UTC', { exact: true }).fill(knowledge);
  await research(page).getByLabel('Wissensstand UTC', { exact: true }).fill(knowledge);
}

test('actual catalog and stored PIT bars preserve exact values, evidence, gaps and filter isolation', async ({ page }) => {
  const writes: string[] = [];
  page.on('request', request => { if (request.method() !== 'GET') writes.push(new URL(request.url()).pathname); });
  await activate(page);
  await expect(page.getByRole('navigation')).toContainText('Alpaca Paper');
  await expect(page.getByRole('heading', { name: 'Paper-Supervisor', exact: true })).toBeVisible();
  for (const state of ['BLOCKED', 'RETRY_WAIT', 'PARTIAL']) {
    await expect(research(page).locator('article.job').filter({ hasText: state })).toBeVisible();
  }
  await expect(research(page)).toContainText('BLOCKED_CREDENTIALS');
  await expect(research(page)).toContainText('PROVIDER_RATE_LIMIT');
  await expect(research(page)).toContainText('BUDGET_EXHAUSTED');
  const sourceResponse = await page.request.get('/api/v1/research/sources');
  expect(sourceResponse.status()).toBe(200);
  const catalog = await sourceResponse.json() as ResearchSourceCatalog;
  for (const source of catalog.items) {
    await expect(research(page)).toContainText(source.sourceId);
    await expect(research(page)).toContainText(source.state);
    await expect(research(page)).toContainText(source.licenseStatus);
    await expect(research(page)).toContainText(source.providerVerification);
  }
  const instrument = catalog.items.find(x => x.kind === 'BAR')?.instruments[0];
  expect(instrument, 'Real owner must seed a registered research instrument').toBeTruthy();
  await filters(page);
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/v1/research/bars');
  await research(page).getByRole('button', { name: 'Gespeicherte Historie abfragen', exact: true }).click();
  const reply = await response;
  expect(reply.status()).toBe(200);
  const query = new URL(reply.url());
  expect(query.searchParams.get('knowledgeAt')).toBe(knowledge);
  expect(query.searchParams.get('valuationAt')).toBe(knowledge);
  const direct = await page.request.get(query.pathname + query.search);
  const bars = await direct.json() as ResearchBarPage;
  expect(bars.items.length, 'Real owner must seed visible immutable bars').toBeGreaterThan(0);
  const table = research(page).getByRole('table', { name: 'Research OHLCV' });
  for (const bar of bars.items) {
    const row = table.getByRole('row', { name: new RegExp('^' + bar.start.replaceAll('.', '\\.') + '\\s') });
    await expect(row).toContainText(bar.open);
    await expect(row).toContainText(bar.high);
    await expect(row).toContainText(bar.low);
    await expect(row).toContainText(bar.close);
    await expect(row).toContainText(bar.volume);
  }
  await expect(research(page)).toContainText('Snapshot:');
  await expect(research(page)).toContainText('Fehlende Quellen:');
  const detailReply = page.waitForResponse(r => new URL(r.url()).pathname.includes('/research/evidence/'));
  await table.getByRole('button', { name: /Evidenz/ }).first().click();
  const detail = await detailReply;
  expect(detail.status()).toBe(200);
  expect(new URL(detail.url()).searchParams.get('knowledgeAt')).toBe(knowledge);
  await expect(research(page).getByRole('article', { name: 'Researchevidenz' })).toContainText(bars.items[0].evidenceId);
  await research(page).getByLabel('Wissensstand UTC', { exact: true }).fill('2026-10-22T12:00:00Z');
  await expect(table).toHaveCount(0);
  await expect(research(page).getByRole('article', { name: 'Researchevidenz' })).toHaveCount(0);
  await research(page).getByRole('button', { name: 'Gespeicherte Historie abfragen', exact: true }).click();
  await expect(research(page)).toContainText('Keine zum Wissensstand verfügbaren Bars');
  expect(writes.filter(path => /orders|paper|mode|kill|pause/.test(path))).toEqual([]);
});

test('explicit import is idempotent, reloads persisted status and stops on session loss', async ({ page }) => {
  await activate(page); await filters(page);
  const keys: string[] = [];
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/research/ingestion-jobs') keys.push(request.headers()['idempotency-key'] ?? '');
  });
  for (let attempt = 0; attempt < 2; attempt++) {
    const response = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/v1/research/ingestion-jobs');
    await research(page).getByRole('button', { name: 'Historienimport ausdrücklich starten', exact: true }).click();
    expect((await response).status()).toBe(202);
    await expect(research(page).getByRole('status')).toContainText('dauerhaft angenommen');
  }
  expect(keys).toHaveLength(2); expect(keys[0]).toBeTruthy(); expect(keys[1]).toBe(keys[0]);
  const response = await page.request.get('/api/v1/research/ingestion-jobs?limit=50');
  expect(response.status()).toBe(200);
  const jobs = await response.json() as ResearchJobPage;
  const job = jobs.items.find(x => x.request.start === start && x.request.end === end);
  expect(job).toBeTruthy();
  await page.reload();
  await expect(research(page)).toContainText(job!.jobId);
  await expect(research(page)).toContainText('Retry UTC:');
  const persisted = await (await page.request.get('/api/v1/research/ingestion-jobs?limit=50')).json() as ResearchJobPage;
  const partial = persisted.items.find(item => item.status === 'PARTIAL');
  expect(partial).toBeTruthy();
  expect(partial!.pages).toBe(1); expect(partial!.bars).toBe(2);
  const partialCard = research(page).locator('article.job').filter({ hasText: partial!.jobId });
  const resumedResponse = page.waitForResponse(reply => reply.request().method() === 'POST'
    && new URL(reply.url()).pathname === '/api/v1/research/ingestion-jobs');
  await partialCard.getByRole('button', { name: 'Fortsetzung ausdrücklich starten', exact: true }).click();
  const resumedReply = await resumedResponse;
  expect(resumedReply.status()).toBe(202);
  const resumed = await resumedReply.json();
  expect(resumed.jobId).toBe(partial!.jobId); expect(resumed.status).toBe('ACCEPTED');
  expect(resumed.pages).toBe(partial!.pages); expect(resumed.bars).toBe(partial!.bars);
  await expect(partialCard).toContainText('ACCEPTED');
  await expect(partialCard.getByRole('button', { name: 'Fortsetzung ausdrücklich starten', exact: true })).toHaveCount(0);
  const resumeKey = resumedReply.request().headers()['idempotency-key'];
  expect(resumeKey).toBeTruthy();
  const repeatedResume = await page.request.post('/api/v1/research/ingestion-jobs', {
    data: { action: 'RESUME', jobId: partial!.jobId },
    headers: { Origin: new URL(page.url()).origin, 'Idempotency-Key': resumeKey },
  });
  expect(repeatedResume.status()).toBe(202);
  expect((await repeatedResume.json()).jobId).toBe(partial!.jobId);
  const afterResume = await (await page.request.get('/api/v1/research/ingestion-jobs?limit=50')).json() as ResearchJobPage;
  expect(afterResume.items.length).toBe(persisted.items.length);
  let polls = 0;
  page.on('request', request => { if (request.method() === 'GET'
    && new URL(request.url()).pathname === '/api/v1/research/ingestion-jobs') polls++; });
  await page.context().clearCookies();
  await research(page).getByRole('button', { name: 'Importaufträge neu laden', exact: true }).click();
  await expect(page.getByLabel('Aktivierungscode', { exact: true })).toBeVisible();
  await expect(research(page)).toHaveCount(0);
  const stoppedAt = polls;
  await page.waitForTimeout(5_500);
  expect(polls, 'Research polling must stop after session loss').toBe(stoppedAt);
});
