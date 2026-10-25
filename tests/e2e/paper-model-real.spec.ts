import { test, expect, type Page } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import type { PaperSnapshot, PaperOperation, PaperOrder, PaperFill } from '../../apps/dashboard/src/app/api/generated/control-api/model/models';

const ORIGIN = 'http://127.0.0.1:4220';
const ACCOUNT = '11111111-1111-4111-8111-111111111111';
const KEY = 'synthetic-paper-browser-key';
const SECRET = 'synthetic-paper-browser-secret';
const owned = process.env.PAPER_BROWSER_MODEL === '1' && !!process.env.PAPER_BROWSER_CONTAINER &&
  !!process.env.PAPER_BROWSER_PYTHON && !!process.env.PAPER_BROWSER_ACTIVATION;
test.skip(!owned, 'Requires the isolated model owner with its immutable rising provider dataset.');
test.setTimeout(120_000);
const paper = (page: Page) => page.locator('atl-paper-workflow');
const safe = (value: string) => ![ACCOUNT, KEY, SECRET].some(canary => value.includes(canary));
// Exact decimal positivity; no binary floating-point amount conversion.
const positive = (value: string) => /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value) && /[1-9]/.test(value);

async function read<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get('/api/v1/paper/' + path);
  expect(response.status()).toBe(200);
  const body = await response.text();
  expect(safe(body)).toBe(true);
  return JSON.parse(body) as T;
}

const snapshot = (page: Page) => read<PaperSnapshot>(page, 'snapshot');
const orders = async (page: Page) => (await read<{ items: PaperOrder[] }>(page, 'orders?limit=50')).items;

async function command(page: Page, type: PaperOperation['operationType'], path: string,
                       label: string, confirmation = false): Promise<PaperOperation> {
  const prior = new Set((await read<{ items: PaperOperation[] }>(page, 'operations?limit=50')).items.map(item => item.operationId));
  // Actual response status plus an immutable operation GET avoids the known
  // Chromium CDP no-store/fetch-reader body issue without changing traffic.
  const response = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/v1/paper/' + path && reply.request().method() === 'POST');
  await paper(page).getByRole('button', { name: label, exact: true }).click();
  if (confirmation) await page.getByRole('dialog').getByRole('button', { name: 'Bestätigen', exact: true }).click();
  expect((await response).status()).toBe(202);
  let operation: PaperOperation | undefined;
  await expect.poll(async () => {
    operation = (await read<{ items: PaperOperation[] }>(page, 'operations?limit=50')).items.find(item => item.operationType === type && !prior.has(item.operationId));
    return operation?.status;
  }, { timeout: 30_000 }).toBe('SUCCEEDED');
  return operation!;
}

test('real Worker model decision → bounded Paper LIMIT → financial FILL → trace and KILL', async ({ page }, info) => {
  expect(info.project.use.baseURL === ORIGIN).toBe(true);
  const external: string[] = [];
  page.on('request', request => {
    if (!request.url().startsWith(ORIGIN + '/')) external.push(new URL(request.url()).origin);
  });
  await page.goto('/#alpaca-paper');
  await page.getByLabel('Aktivierungscode', { exact: true }).fill(process.env.PAPER_BROWSER_ACTIVATION!);
  await page.getByRole('button', { name: 'Sitzung aktivieren', exact: true }).click();
  await expect(paper(page).getByRole('heading', { name: 'Paperhandel', exact: true })).toBeVisible();
  expect((await snapshot(page)).status.setupState).toBe('NOT_CONFIGURED');
  expect((await orders(page)).length).toBe(0);
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
  await expect.poll(async () => (await snapshot(page)).status.setupState).toBe('PREPARED');
  await paper(page).getByRole('button', { name: 'Paper aktualisieren', exact: true }).click();
  await paper(page).getByLabel('Paper-Riskbudget in USD', { exact: true }).fill('100');
  await paper(page).getByRole('combobox', { name: 'Paperstrategie', exact: true }).click();
  await page.getByRole('option', { name: 'Mock neutral', exact: true }).click();
  await paper(page).getByRole('checkbox').check();
  const savedProfile = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/v1/paper/trading-profile' && reply.request().method() === 'PUT');
  await paper(page).getByRole('button', { name: 'Paperprofil speichern', exact: true }).click();
  expect((await savedProfile).status()).toBe(200);
  await expect.poll(async () => (await snapshot(page)).profile.configured).toBe(true);
  // No manual endpoint is invoked; the real Worker owns the decision/proposal.
  expect((await orders(page)).length).toBe(0);
  await paper(page).getByRole('button', { name: 'Paper aktualisieren', exact: true }).click();
  await command(page, 'START', 'start', 'Paperstart bestätigen', true);
  let order: PaperOrder | undefined;
  await expect.poll(async () => {
    const rows = await orders(page);
    expect(rows.every(row => row.proposalSource === 'MODEL_DECISION' && row.humanRequestId === null && row.decisionId !== null)).toBe(true);
    order = rows.find(row => row.status === 'ACKNOWLEDGED');
    return !!order;
  }, { timeout: 40_000 }).toBe(true);
  expect(order!.symbol === 'SPY' && order!.side === 'BUY').toBe(true);
  expect(positive(order!.quantity)).toBe(true);
  expect(order!.orderType === 'LIMIT' && order!.timeInForce === 'DAY' && order!.session === 'REGULAR').toBe(true);
  expect(order!.limitPrice !== null && positive(order!.limitPrice!)).toBe(true);

  const filled = await page.request.post('/api/v1/__test/provider', {
    headers: { Origin: ORIGIN, 'Idempotency-Key': 'paper-browser-model-fill-1' },
    data: { action: 'fill-next' },
  });
  expect(filled.status()).toBe(200);
  await expect.poll(async () => {
    const stored = (await orders(page)).find(row => row.orderId === order!.orderId);
    const state = await snapshot(page);
    return stored?.status === 'FILLED' && stored.filledQuantity === stored.quantity &&
      state.portfolio?.reconciliation === 'CURRENT' &&
      !!state.positions?.some(position => position.symbol === 'SPY' && positive(position.quantity));
  }, { timeout: 40_000 }).toBe(true);
  const fills = (await read<{ items: PaperFill[] }>(page, 'fills?limit=50')).items;
  expect(fills.some(fill => fill.orderId === order!.orderId && fill.traceId === order!.traceId && positive(fill.quantity))).toBe(true);
  await paper(page).getByRole('button', { name: 'Paper aktualisieren', exact: true }).click();
  const visible = paper(page).getByRole('region', { name: 'Paperorders' }).locator('article').filter({ hasText: order!.orderId });
  await expect(visible).toContainText('FILLED');
  await visible.getByRole('button', { name: 'Ordertrace ansehen', exact: true }).click();
  await expect(paper(page).getByRole('region', { name: 'Papertrace' })).toContainText(order!.traceId);
  await expect(paper(page).getByRole('region', { name: 'Papertrace' })).toContainText('RISK_APPROVED');
  await command(page, 'KILL', 'control', 'Paper-Kill', true);
  await expect.poll(async () => (await snapshot(page)).status.operatingState).toBe('HALTED');
  await page.reload();
  await expect(paper(page).getByLabel('Gespeicherter Paperzustand')).toContainText('HALTED');
  const finalOrders = await orders(page);
  expect(finalOrders.length).toBe(1);
  expect(finalOrders.every(row => row.proposalSource === 'MODEL_DECISION' && row.humanRequestId === null && row.decisionId !== null && row.status === 'FILLED')).toBe(true);
  expect(external.length).toBe(0);
  expect(safe(await page.locator('body').innerText())).toBe(true);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});
