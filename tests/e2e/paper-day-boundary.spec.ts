import { test, expect, type Page } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import type { PaperSnapshot, PaperOperation, PaperOrder } from '../../apps/dashboard/src/app/api/generated/control-api/model/models';

const ORIGIN = 'http://127.0.0.1:4220';
const MIDNIGHT = Date.parse('2026-10-27T00:00:00Z');
const ACCOUNT = '11111111-1111-4111-8111-111111111111';
const KEY = 'synthetic-paper-browser-key';
const SECRET = 'synthetic-paper-browser-secret';
const owned = process.env.PAPER_BROWSER_DAY_BOUNDARY === '1' &&
  !!process.env.PAPER_BROWSER_CONTAINER && !!process.env.PAPER_BROWSER_PYTHON &&
  !!process.env.PAPER_BROWSER_ACTIVATION;
test.skip(!owned, 'Requires the isolated day-boundary owner and its joint PostgreSQL/Python clock.');
test.setTimeout(120_000);
const paper = (page: Page) => page.locator('atl-paper-workflow');
const safe = (value: string) => ![ACCOUNT, KEY, SECRET].some(canary => value.includes(canary));

async function read<T>(page: Page, path: string): Promise<T> {
  const reply = await page.request.get('/api/v1/paper/' + path);
  expect(reply.status()).toBe(200);
  const body = await reply.text();
  expect(safe(body)).toBe(true);
  return JSON.parse(body) as T;
}

async function snapshot(page: Page): Promise<PaperSnapshot> {
  return read<PaperSnapshot>(page, 'snapshot');
}

async function command(page: Page, type: PaperOperation['operationType'], path: string,
                       label: string, confirmation = false): Promise<void> {
  const previous = new Set((await read<{ items: PaperOperation[] }>(page, 'operations?limit=50')).items.map(item => item.operationId));
  // CDP body reads can lose Angular no-store fetch streams. Use the actual HTTP
  // status, then recover the new immutable operation via its canonical GET.
  const response = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/v1/paper/' + path && reply.request().method() === 'POST');
  await paper(page).getByRole('button', { name: label, exact: true }).click();
  if (confirmation) await page.getByRole('dialog').getByRole('button', { name: 'Bestätigen', exact: true }).click();
  expect((await response).status()).toBe(202);
  let operation: PaperOperation | undefined;
  await expect.poll(async () => {
    operation = (await read<{ items: PaperOperation[] }>(page, 'operations?limit=50')).items.find(item => item.operationType === type && !previous.has(item.operationId));
    return operation?.status;
  }, { timeout: 20_000 }).toBe('SUCCEEDED');
  expect(operation?.operationType).toBe(type);
}

test('FIRST_DAY preparation before midnight stays closed across the actual UTC boundary', async ({ page }, info) => {
  expect(info.project.use.baseURL === ORIGIN).toBe(true);
  const external: string[] = [];
  page.on('request', request => {
    if (!request.url().startsWith(ORIGIN + '/')) external.push(new URL(request.url()).origin);
  });
  await page.goto('/#alpaca-paper');
  await page.getByLabel('Aktivierungscode', { exact: true }).fill(process.env.PAPER_BROWSER_ACTIVATION!);
  await page.getByRole('button', { name: 'Sitzung aktivieren', exact: true }).click();
  await expect(paper(page).getByRole('heading', { name: 'Paperhandel', exact: true })).toBeVisible();
  const initial = await snapshot(page);
  expect(initial.status.setupState).toBe('NOT_CONFIGURED');
  expect(Date.parse(initial.observedAt) < MIDNIGHT).toBe(true);

  await paper(page).getByRole('button', { name: 'Paper-Schlüssel einrichten', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('API Key', { exact: true }).fill(KEY);
  await dialog.getByLabel('API Secret', { exact: true }).fill(SECRET);
  await dialog.getByLabel('Erwartete Account UUID', { exact: true }).fill(ACCOUNT);
  const saved = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/v1/integrations/alpaca' && reply.request().method() === 'PUT');
  await dialog.getByRole('button', { name: 'Speichern', exact: true }).click();
  expect((await saved).status()).toBe(200);
  await expect(dialog).toHaveCount(0);
  await expect.poll(async () => (await snapshot(page)).status.setupState).toBe('SAVED');
  await paper(page).getByRole('button', { name: 'Paper aktualisieren', exact: true }).click();
  await command(page, 'VERIFY_CONNECTION', 'verify-connection', 'Verbindung prüfen');
  await expect.poll(async () => (await snapshot(page)).status.setupState).toBe('VERIFIED');
  await paper(page).getByRole('button', { name: 'Paper aktualisieren', exact: true }).click();
  await command(page, 'PREPARE_LEDGER', 'prepare-ledger', 'Paperledger vorbereiten', true);

  const prepared = await snapshot(page);
  expect(prepared.status.setupState).toBe('PREPARED');
  // Missing the pre-midnight setup window invalidates this scenario; it cannot
  // be converted into a post-midnight preparation or a shortened fake day.
  expect(Date.parse(prepared.observedAt) < MIDNIGHT).toBe(true);
  expect(prepared.portfolio?.cash === '100000').toBe(true);
  expect(prepared.status.entryGateOpen).toBe(false);
  expect((await read<{ items: PaperOrder[] }>(page, 'orders?limit=50')).items.length).toBe(0);

  let after = prepared;
  await expect.poll(async () => {
    after = await snapshot(page);
    return Date.parse(after.observedAt) >= MIDNIGHT;
  }, { timeout: 65_000, intervals: [500, 1000] }).toBe(true);
  expect(after.status.operatingState).toBe('PAUSED');
  expect(after.status.entryGateOpen).toBe(false);
  expect(after.portfolio?.cash === '100000').toBe(true);
  expect(after.positions?.every(position => position.quantity === '0') ?? true).toBe(true);
  expect((await read<{ items: PaperOrder[] }>(page, 'orders?limit=50')).items.length).toBe(0);
  await paper(page).getByRole('button', { name: 'Paper aktualisieren', exact: true }).click();
  await expect(paper(page).getByLabel('Gespeicherter Paperzustand')).toContainText('PAUSED');
  await expect(paper(page).getByLabel('Gespeicherter Paperzustand')).toContainText('Gesperrt');
  expect(external.length).toBe(0);
  expect(safe(await page.locator('body').innerText())).toBe(true);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});
