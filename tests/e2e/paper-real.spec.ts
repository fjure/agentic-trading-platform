import { test, expect, type Page } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import type { PaperSnapshot, PaperOperation, PaperOrder, PaperFill } from '../../apps/dashboard/src/app/api/generated/control-api/model/models';

const ORIGIN = 'http://127.0.0.1:4220';
const ACCOUNT = '11111111-1111-4111-8111-111111111111';
const KEY = 'synthetic-paper-browser-key';
const SECRET = 'synthetic-paper-browser-secret';
const owned = !!process.env.PAPER_BROWSER_CONTAINER && !!process.env.PAPER_BROWSER_PYTHON && !!process.env.PAPER_BROWSER_ACTIVATION;
const crash = process.env.PAPER_BROWSER_CRASH === '1';
test.skip(!owned, 'Run scripts/test-paper-browser.ps1 with its isolated actual API/PostgreSQL owner.');
const paper = (page: Page) => page.locator('atl-paper-workflow');
const safe = (value: string): boolean => ![ACCOUNT, KEY, SECRET].some(canary => value.includes(canary));
let providerSequence = 0;
type ObservedReply = { status: number; body: string };

async function observeWrites(page: Page): Promise<void> {
  // Observe a clone inside Chromium: CDP response.text can lose no-store streams
  // consumed by Angular's fetch reader (microsoft/playwright#42742).
  await page.addInitScript(() => {
    const replies = new Map<string, { status: number; body: string }>();
    Object.assign(window, { __paperWriteReplies: replies });
    const original = window.fetch;
    window.fetch = function (...args: Parameters<typeof fetch>) {
      const input = args[0], init = args[1];
      const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
      const url = new URL(input instanceof Request ? input.url : String(input), location.href);
      const selected = url.origin === location.origin && (
        (method === 'PUT' && url.pathname === '/api/v1/integrations/alpaca') ||
        (method === 'PUT' && url.pathname === '/api/v1/paper/trading-profile') ||
        (method === 'POST' && url.pathname.startsWith('/api/v1/paper/'))
      );
      const pending = Reflect.apply(original, this, args) as Promise<Response>;
      if (selected) {
        // One outstanding UI write per path; consumed entries are deleted. No
        // outgoing headers/body or private form values enter this observer.
        void pending.then(response => response.clone().text().then(body => {
          replies.set(method + ' ' + url.pathname, { status: response.status, body });
        }));
      }
      return pending;
    };
  });
}

async function observed(page: Page, method: string, path: string, status: number): Promise<ObservedReply> {
  const key = method + ' ' + path;
  await page.waitForFunction(key => (window as unknown as { __paperWriteReplies: Map<string, ObservedReply> }).__paperWriteReplies.has(key), key);
  const reply = await page.evaluate(key => {
    const replies = (window as unknown as { __paperWriteReplies: Map<string, ObservedReply> }).__paperWriteReplies;
    const value = replies.get(key)!;
    replies.delete(key);
    return value;
  }, key);
  expect(reply.status).toBe(status);
  expect(safe(reply.body)).toBe(true);
  return reply;
}

async function read<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get('/api/v1/paper/' + path);
  expect(response.status()).toBe(200);
  const value = await response.text();
  // Boolean-only assertions never print a response containing a canary.
  expect(safe(value)).toBe(true);
  return JSON.parse(value) as T;
}

async function snapshot(page: Page): Promise<PaperSnapshot> { return read<PaperSnapshot>(page, 'snapshot'); }
async function orders(page: Page): Promise<PaperOrder[]> { return (await read<{ items: PaperOrder[] }>(page, 'orders?limit=50')).items; }
async function fills(page: Page): Promise<PaperFill[]> { return (await read<{ items: PaperFill[] }>(page, 'fills?limit=50')).items; }

async function provider(page: Page, action: 'fill-next' | 'cancel-terminal'): Promise<void> {
  // This changes only the isolated HTTP provider double, never application
  // tables, reservations, RiskDecisions, activation or reconciliation receipts.
  const result = await page.request.post('/api/v1/__test/provider', {
    headers: { Origin: ORIGIN, 'Idempotency-Key': `paper-browser-provider-${++providerSequence}-${action}` },
    data: { action },
  });
  expect(result.status()).toBe(200);
}

async function execution(page: Page, action: 'crash-next-delete' | 'restart'): Promise<void> {
  const reply = await page.request.post('/api/v1/__test/execution', {
    headers: { Origin: ORIGIN, 'Idempotency-Key': `execution-${action}` }, data: { action },
  });
  expect(reply.status()).toBe(200);
}

async function operation(page: Page, value: PaperOperation): Promise<PaperOperation> {
  let result = value;
  await expect.poll(async () => {
    result = await read<PaperOperation>(page, 'operations/' + value.operationId);
    return ['SUCCEEDED', 'BLOCKED', 'FAILED'].includes(result.status);
  }, { timeout: 40_000 }).toBe(true);
  expect(result.status).toBe('SUCCEEDED');
  return result;
}

async function command(page: Page, path: string, button: string, confirmation = false): Promise<PaperOperation> {
  const response = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/v1/paper/' + path && reply.request().method() === 'POST')
    .then(reply => observed(page, 'POST', '/api/v1/paper/' + path, reply.status()));
  await paper(page).getByRole('button', { name: button, exact: true }).click();
  if (confirmation) await page.getByRole('dialog').getByRole('button', { name: 'Bestätigen', exact: true }).click();
  const reply = await response;
  expect(reply.status).toBe(202);
  const body = reply.body;
  expect(safe(body)).toBe(true);
  return JSON.parse(body) as PaperOperation;
}

async function refresh(page: Page): Promise<void> {
  await paper(page).getByRole('button', { name: 'Paper aktualisieren', exact: true }).click();
}

async function approve(page: Page): Promise<void> {
  await refresh(page);
  const intent = await command(page, 'start', 'Paperstart bestätigen', true);
  await operation(page, intent);
  await expect.poll(async () => {
    const data = await snapshot(page);
    return data.status.operatingState === 'RUNNING' && data.status.entryGateOpen;
  }).toBe(true);
  await refresh(page);
}

async function manual(page: Page, side: 'BUY' | 'SELL'): Promise<PaperOrder> {
  await paper(page).getByRole('combobox', { name: 'Orderseite', exact: true }).click();
  await page.getByRole('option', { name: side, exact: true }).click();
  await paper(page).getByLabel('Exakte Ordermenge', { exact: true }).fill('1');
  await paper(page).getByLabel('LIMIT-Preis in USD', { exact: true }).fill(side === 'BUY' ? '15.00' : '14.99');
  const intent = await command(page, 'order-requests', 'LIMIT-Auftrag prüfen', true);
  let row: PaperOrder | undefined;
  await expect.poll(async () => {
    row = (await orders(page)).find(order => order.humanRequestId === intent.operationId);
    return !!row && row.status === 'ACKNOWLEDGED';
  }, { timeout: 40_000 }).toBe(true);
  await operation(page, intent);
  // Honest human source and original immutable terms, not a synthetic decision.
  expect(row!.proposalSource === 'HUMAN_REQUEST' && row!.decisionId === null).toBe(true);
  expect(row!.side === side && row!.quantity === '1' && row!.orderType === 'LIMIT').toBe(true);
  expect(row!.timeInForce === 'DAY' && row!.session === 'REGULAR').toBe(true);
  await refresh(page);
  return row!;
}

async function fill(page: Page, order: PaperOrder, quantity: string): Promise<void> {
  await provider(page, 'fill-next');
  await expect.poll(async () => {
    const row = (await orders(page)).find(item => item.orderId === order.orderId);
    const data = await snapshot(page);
    const position = data.positions?.find(item => item.symbol === 'SPY');
    return row?.status === 'FILLED' && row.filledQuantity === '1' &&
      (position?.quantity ?? '0') === quantity && data.portfolio?.reconciliation === 'CURRENT';
  }, { timeout: 40_000 }).toBe(true);
  const booked = (await fills(page)).filter(item => item.orderId === order.orderId);
  expect(booked.length).toBeGreaterThan(0);
  expect(booked.every(item => item.traceId === order.traceId && item.side === order.side)).toBe(true);
  await refresh(page);
  await expect(paper(page).getByRole('region', { name: 'Paperorders' }).locator('article').filter({ hasText: order.orderId })).toContainText('FILLED');
}

test('real isolated UI → private setup → Paper SQL → LIMIT/FILL/SELL/cancel and durable controls', async ({ page }, info) => {
  expect(info.project.use.baseURL === ORIGIN).toBe(true);
  await observeWrites(page);
  const external: string[] = [];
  page.on('request', request => {
    if (!request.url().startsWith(ORIGIN + '/')) external.push(new URL(request.url()).origin);
  });
  await page.goto('/#alpaca-paper');
  await expect(page.getByLabel('Aktivierungscode', { exact: true })).toBeVisible();
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
  const saved = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/v1/integrations/alpaca' && reply.request().method() === 'PUT')
    .then(reply => observed(page, 'PUT', '/api/v1/integrations/alpaca', reply.status()));
  await dialog.getByRole('button', { name: 'Speichern', exact: true }).click();
  const savedReply = await saved;
  expect(savedReply.status).toBe(200); expect(safe(savedReply.body)).toBe(true);
  await expect(dialog).toHaveCount(0);
  await expect.poll(async () => (await snapshot(page)).status.setupState).toBe('SAVED');
  // Reopening must never restore the just-submitted private values.
  await paper(page).getByRole('button', { name: 'Paper-Schlüssel einrichten', exact: true }).click();
  expect(await page.getByRole('dialog').locator('input').evaluateAll(inputs => inputs.every(input => (input as HTMLInputElement).value === ''))).toBe(true);
  await page.getByRole('dialog').getByRole('button', { name: 'Abbrechen', exact: true }).click();
  await refresh(page);

  await operation(page, await command(page, 'verify-connection', 'Verbindung prüfen'));
  await expect.poll(async () => (await snapshot(page)).status.setupState).toBe('VERIFIED');
  await refresh(page);
  await operation(page, await command(page, 'prepare-ledger', 'Paperledger vorbereiten', true));
  await expect.poll(async () => (await snapshot(page)).status.setupState).toBe('PREPARED');
  await refresh(page);
  await paper(page).getByLabel('Paper-Riskbudget in USD', { exact: true }).fill('100');
  await paper(page).getByRole('combobox', { name: 'Paperstrategie', exact: true }).click();
  await page.getByRole('option', { name: 'Mock neutral', exact: true }).click();
  await paper(page).getByRole('checkbox').check();
  const profile = page.waitForResponse(reply => new URL(reply.url()).pathname === '/api/v1/paper/trading-profile' && reply.request().method() === 'PUT')
    .then(reply => observed(page, 'PUT', '/api/v1/paper/trading-profile', reply.status()));
  await paper(page).getByRole('button', { name: 'Paperprofil speichern', exact: true }).click();
  const profileReply = await profile;
  expect(profileReply.status).toBe(200);
  expect(safe(profileReply.body)).toBe(true);
  await expect.poll(async () => (await snapshot(page)).profile.configured).toBe(true);
  expect((await orders(page)).length).toBe(0); // Mock-neutral never forces a decision/order.

  await approve(page);
  const buy = await manual(page, 'BUY');
  await fill(page, buy, '1');
  const afterBuy = await snapshot(page);
  expect(afterBuy.portfolio?.cash === '99985').toBe(true);
  await page.reload();
  await expect(paper(page)).toBeVisible();
  expect((await orders(page)).some(item => item.orderId === buy.orderId && item.status === 'FILLED')).toBe(true);

  await approve(page);
  const sell = await manual(page, 'SELL');
  await fill(page, sell, '0');
  expect((await snapshot(page)).portfolio?.cash === '99999.99').toBe(true);

  await approve(page);
  const cancelOrder = await manual(page, 'BUY');
  const row = paper(page).getByRole('region', { name: 'Paperorders' }).locator('article').filter({ hasText: cancelOrder.orderId });
  if (crash) await execution(page, 'crash-next-delete');
  const requested = page.waitForResponse(reply => new URL(reply.url()).pathname === `/api/v1/paper/orders/${cancelOrder.orderId}/cancel` && reply.request().method() === 'POST')
    .then(reply => observed(page, 'POST', `/api/v1/paper/orders/${cancelOrder.orderId}/cancel`, reply.status()));
  await row.getByRole('button', { name: 'Stornierung anfordern', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Bestätigen', exact: true }).click();
  const cancelReply = await requested;
  expect(cancelReply.status).toBe(202);
  const cancelBody = cancelReply.body; expect(safe(cancelBody)).toBe(true);
  const cancel = JSON.parse(cancelBody) as PaperOperation;
  if (crash) {
    await expect.poll(async () => {
      const reply = await page.request.get('/api/v1/__test/evidence');
      const value = await reply.json();
      return value.execution.crashes === 1 && value.execution.alive === false;
    }).toBe(true);
    expect((await read<PaperOperation>(page, 'operations/' + cancel.operationId)).status).toBe('RUNNING');
    await execution(page, 'restart');
  }
  await expect.poll(async () => (await orders(page)).find(item => item.orderId === cancelOrder.orderId)?.cancel?.state, { timeout: 60_000 }).toBe('LOOKUP_ONLY');
  expect((await read<PaperOperation>(page, 'operations/' + cancel.operationId)).status).toBe('RUNNING');
  await page.reload();
  await expect(paper(page).getByRole('region', { name: 'Dauerhafte Papervorgänge' })).toContainText(cancel.operationId);
  await provider(page, 'cancel-terminal');
  await operation(page, cancel);
  await expect.poll(async () => {
    const actual = (await orders(page)).find(item => item.orderId === cancelOrder.orderId);
    return actual?.status === 'CANCELED' && actual.cancel?.state === 'FINISHED';
  }).toBe(true);
  expect((await snapshot(page)).portfolio?.cash === '99999.99').toBe(true);
  await refresh(page);
  const terminal = paper(page).getByRole('region', { name: 'Paperorders' }).locator('article').filter({ hasText: cancelOrder.orderId });
  await expect(terminal).toContainText('CANCELED');
  await terminal.getByRole('button', { name: 'Ordertrace ansehen', exact: true }).click();
  await expect(paper(page).getByRole('region', { name: 'Papertrace' })).toContainText(cancelOrder.traceId);

  for (const [action, label] of [['PAUSE', 'Paper pausieren'], ['STOP', 'Paper stoppen'], ['KILL', 'Paper-Kill']] as const) {
    await operation(page, await command(page, 'control', label, true));
    await expect.poll(async () => (await snapshot(page)).status.operatingState).toBe(action === 'KILL' ? 'HALTED' : action === 'STOP' ? 'STOPPED' : 'PAUSED');
  }
  await page.reload();
  await expect(paper(page).getByLabel('Gespeicherter Paperzustand')).toContainText('HALTED');
  const finalOrders = await orders(page);
  expect(finalOrders.length).toBe(3);
  expect(new Set(finalOrders.map(item => item.clientOrderId)).size).toBe(3);
  expect(finalOrders.every(item => item.proposalSource === 'HUMAN_REQUEST' && item.decisionId === null)).toBe(true);
  expect(safe(await page.locator('body').innerText())).toBe(true);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  expect(external.length).toBe(0);
  const evidence = await page.request.get('/api/v1/__test/evidence');
  expect(evidence.status()).toBe(200);
  const report = await evidence.text(); expect(safe(report)).toBe(true);
  const proof = JSON.parse(report) as {
    noDuplicateSubmit: boolean; noExternal: boolean; normalFinanceUnchanged: boolean;
    provider: { posts: number; fills: number; deletes: number; cash: string };
  };
  expect(proof.noDuplicateSubmit === true && proof.noExternal === true && proof.normalFinanceUnchanged === true).toBe(true);
  expect(proof.provider.posts === 3 && proof.provider.fills === 2 && proof.provider.deletes === 1 && proof.provider.cash === '99999.99').toBe(true);
});
