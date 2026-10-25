import { test, expect, type Page } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import type { PaperSnapshot, PaperOperation, PaperOrder } from '../../apps/dashboard/src/app/api/generated/control-api/model/models';

const ORIGIN = 'http://127.0.0.1:4220';
const ACCOUNT = '11111111-1111-4111-8111-111111111111';
const KEY = 'synthetic-paper-browser-key';
const SECRET = 'synthetic-paper-browser-secret';
const owned = process.env.PAPER_BROWSER_SUBMIT_CRASH === '1' && !!process.env.PAPER_BROWSER_CONTAINER &&
  !!process.env.PAPER_BROWSER_PYTHON && !!process.env.PAPER_BROWSER_ACTIVATION;
test.skip(!owned, 'Requires the disposable actual Execution-process submit-crash owner.');
test.setTimeout(180_000);
const paper = (page: Page) => page.locator('atl-paper-workflow');
const safe = (value: string) => ![ACCOUNT, KEY, SECRET].some(canary => value.includes(canary));
type Evidence = {
  execution: { crashes: number; alive: boolean; restarts: number };
  provider: { posts: number; fills: number; deletes: number; cash: string };
  noExternal: boolean; noDuplicateSubmit: boolean; normalFinanceUnchanged: boolean;
};

async function read<T>(page: Page, path: string): Promise<T> {
  const reply = await page.request.get(path);
  expect(reply.status()).toBe(200);
  const body = await reply.text();
  expect(safe(body)).toBe(true);
  return JSON.parse(body) as T;
}
const snapshot = (page: Page) => read<PaperSnapshot>(page, '/api/v1/paper/snapshot');
const orders = async (page: Page) => (await read<{ items: PaperOrder[] }>(page, '/api/v1/paper/orders?limit=50')).items;
const evidence = (page: Page) => read<Evidence>(page, '/api/v1/__test/evidence');

async function complete(page: Page, operation: PaperOperation, timeout = 30_000): Promise<PaperOperation> {
  let current = operation;
  await expect.poll(async () => {
    current = await read<PaperOperation>(page, '/api/v1/paper/operations/' + operation.operationId);
    return current.status;
  }, { timeout }).toBe('SUCCEEDED');
  expect(current.acceptedAt === operation.acceptedAt && current.idempotencyKey === operation.idempotencyKey).toBe(true);
  return current;
}

async function command(page: Page, type: PaperOperation['operationType'], path: string,
                       label: string, confirmation = false): Promise<PaperOperation> {
  const previous = new Set((await read<{ items: PaperOperation[] }>(page, '/api/v1/paper/operations?limit=50')).items.map(item => item.operationId));
  const response = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/v1/paper/' + path && reply.request().method() === 'POST');
  await paper(page).getByRole('button', { name: label, exact: true }).click();
  if (confirmation) await page.getByRole('dialog').getByRole('button', { name: 'Bestätigen', exact: true }).click();
  expect((await response).status()).toBe(202);
  let operation: PaperOperation | undefined;
  await expect.poll(async () => {
    operation = (await read<{ items: PaperOperation[] }>(page, '/api/v1/paper/operations?limit=50')).items.find(item => item.operationType === type && !previous.has(item.operationId));
    return !!operation;
  }).toBe(true);
  return operation!;
}

async function control(page: Page, lane: 'execution' | 'provider', action: string, key: string): Promise<void> {
  const response = await page.request.post('/api/v1/__test/' + lane, {
    headers: { Origin: ORIGIN, 'Idempotency-Key': key }, data: { action },
  });
  expect(response.status()).toBe(200);
}

test('real submit process death retains one attempted order and restores by lookup after restart', async ({ page }, info) => {
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
  await complete(page, await command(page, 'VERIFY_CONNECTION', 'verify-connection', 'Verbindung prüfen'));
  await paper(page).getByRole('button', { name: 'Paper aktualisieren', exact: true }).click();
  await complete(page, await command(page, 'PREPARE_LEDGER', 'prepare-ledger', 'Paperledger vorbereiten', true));
  await paper(page).getByRole('button', { name: 'Paper aktualisieren', exact: true }).click();
  await paper(page).getByLabel('Paper-Riskbudget in USD', { exact: true }).fill('100');
  await paper(page).getByRole('combobox', { name: 'Paperstrategie', exact: true }).click();
  await page.getByRole('option', { name: 'Mock neutral', exact: true }).click();
  await paper(page).getByRole('checkbox').check();
  const profile = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/v1/paper/trading-profile' && reply.request().method() === 'PUT');
  await paper(page).getByRole('button', { name: 'Paperprofil speichern', exact: true }).click();
  expect((await profile).status()).toBe(200);
  await expect.poll(async () => (await snapshot(page)).profile.configured).toBe(true);
  await paper(page).getByRole('button', { name: 'Paper aktualisieren', exact: true }).click();
  await complete(page, await command(page, 'START', 'start', 'Paperstart bestätigen', true));
  expect((await orders(page)).length).toBe(0);
  await control(page, 'execution', 'crash-next-submit', 'paper-browser-arm-submit-crash');
  await paper(page).getByRole('button', { name: 'Paper aktualisieren', exact: true }).click();
  await paper(page).getByLabel('Exakte Ordermenge', { exact: true }).fill('1');
  await paper(page).getByLabel('LIMIT-Preis in USD', { exact: true }).fill('15.00');
  // Capture only public human-order terms, never the private setup request.
  const outgoing = page.waitForRequest(request => new URL(request.url()).pathname === '/api/v1/paper/order-requests' && request.method() === 'POST');
  const original = await command(page, 'PLACE_LIMIT_ORDER', 'order-requests', 'LIMIT-Auftrag prüfen', true);
  const terms = (await outgoing).postDataJSON() as { expiresAt: string; quantity: string; limitPrice: string };
  expect(terms.quantity === '1' && terms.limitPrice === '15.00').toBe(true);
  await expect.poll(async () => {
    const value = await evidence(page);
    return value.execution.crashes === 1 && value.execution.alive === false && value.provider.posts === 1;
  }, { timeout: 30_000 }).toBe(true);
  const attempted = (await orders(page))[0];
  expect((await orders(page)).length).toBe(1);
  expect(['SUBMITTING', 'UNKNOWN'].includes(attempted.status)).toBe(true);
  expect(attempted.humanRequestId === original.operationId && attempted.decisionId === null).toBe(true);
  expect((await read<PaperOperation>(page, '/api/v1/paper/operations/' + original.operationId)).status).toBe('RUNNING');
  await page.reload();
  const reloaded = (await orders(page))[0];
  expect(reloaded.orderId === attempted.orderId && reloaded.clientOrderId === attempted.clientOrderId && reloaded.proposalId === attempted.proposalId).toBe(true);
  expect(reloaded.quantity === '1' && /^15(?:\.0+)?$/.test(reloaded.limitPrice ?? '') && reloaded.side === 'BUY').toBe(true);
  await control(page, 'execution', 'restart', 'paper-browser-submit-restart');
  await expect.poll(async () => {
    const rows = await orders(page);
    expect(rows.length).toBe(1);
    expect((await evidence(page)).provider.posts).toBe(1);
    return rows[0].status;
  }, { timeout: 120_000 }).toBe('ACKNOWLEDGED');
  await complete(page, original, 120_000);
  expect(Date.parse((await snapshot(page)).observedAt) > Date.parse(terms.expiresAt)).toBe(true);
  const restored = (await orders(page))[0];
  expect(restored.orderId === attempted.orderId && restored.clientOrderId === attempted.clientOrderId && restored.quantity === attempted.quantity && restored.limitPrice === attempted.limitPrice).toBe(true);
  await control(page, 'provider', 'fill-next', 'paper-browser-submit-fill');
  await expect.poll(async () => {
    const row = (await orders(page))[0], state = await snapshot(page);
    return row.status === 'FILLED' && row.filledQuantity === '1' &&
      state.portfolio?.reconciliation === 'CURRENT' && state.portfolio.cash === '99985' &&
      !!state.positions?.some(position => position.symbol === 'SPY' && position.quantity === '1');
  }, { timeout: 40_000 }).toBe(true);
  await complete(page, await command(page, 'KILL', 'control', 'Paper-Kill', true));
  await expect.poll(async () => (await snapshot(page)).status.operatingState).toBe('HALTED');
  const final = await evidence(page);
  expect(final.provider.posts === 1 && final.provider.fills === 1 && final.provider.deletes === 0).toBe(true);
  expect(final.execution.crashes === 1 && final.execution.restarts === 1).toBe(true);
  expect(final.noDuplicateSubmit && final.noExternal && final.normalFinanceUnchanged).toBe(true);
  expect(external.length).toBe(0);
  expect(safe(await page.locator('body').innerText())).toBe(true);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});
