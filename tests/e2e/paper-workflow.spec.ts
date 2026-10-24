import { readFileSync } from 'node:fs';
import { test, expect, type Page, type APIRequestContext } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import type { PaperSnapshot, PaperOrder, PaperFill, PaperOperation } from '../../apps/dashboard/src/app/api/generated/control-api/model/models';

// These browser fixtures exercise presentation only. Actual HTTP/SQL/provider
// doubles are certified separately; no browser test contacts an Alpaca host.
const trace = '4bf92f3577b34da6a3ce929d0e0e4736';
const example = (name: string) => JSON.parse(readFileSync(`contracts/examples/v1/${name}.valid.json`, 'utf8'));
const namespace = 'alpaca-paper';

class PaperFixture {
  snapshot: PaperSnapshot = example('paper-snapshot');
  orders: PaperOrder[] = example('paper-order-page').items;
  moreOrders: PaperOrder[] = [];
  fills: PaperFill[] = example('paper-fill-page').items;
  operations: PaperOperation[] = [];
  writes: { path: string; key: string; body: Record<string, unknown> }[] = [];
  reads: string[] = [];
  readUrls: string[] = [];
  unauthorized = false;
  failManualOnce = false;
  malformed = false;
  holdOperations?: Promise<void>;
  holdManual?: Promise<void>;
  holdProfile?: Promise<void>;
  private count = 1;
  private readonly started = Date.now();
  private readonly base = Date.parse(this.snapshot.observedAt);
  now(): string { return new Date(this.base + Date.now() - this.started).toISOString(); }

  operation(kind: PaperOperation['operationType'], status: PaperOperation['status'] = 'SUCCEEDED'): PaperOperation {
    const operation: PaperOperation = { operationId: `12345678-1234-4234-8234-${String(this.count++).padStart(12, '0')}`, operationType: kind, status, idempotencyKey: `synthetic-paper-${this.count}-intent`, acceptedGeneration: 7, acceptedAt: this.now(), updatedAt: this.now(), traceId: trace };
    this.operations.unshift(operation);
    return operation;
  }

  async install(page: Page): Promise<void> {
    await page.route('**/api/v1/paper/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname.replace('/api/v1/paper/', '');
      if (this.unauthorized) { await route.fulfill({ status: 401, json: { code: 'INVALID_REQUEST' } }); return; }
      if (request.method() === 'GET') {
        this.reads.push(path);
        this.readUrls.push(url.pathname + url.search);
        let json: unknown;
        if (path === 'snapshot') {
          if (!this.malformed) this.snapshot.observedAt = this.now();
          json = this.snapshot;
        } else if (path === 'orders') json = { namespace, items: url.searchParams.has('cursor') ? this.moreOrders : this.orders, nextCursor: this.moreOrders.length && !url.searchParams.has('cursor') ? 'c_page2' : null };
        else if (path === 'fills') json = { namespace, items: this.fills, nextCursor: null };
        else if (path === 'operations') {
          if (this.holdOperations) await this.holdOperations;
          // Same authoritative active-first ordering as the isolated SQL port.
          const active = this.operations.filter(x => ['ACCEPTED', 'RUNNING'].includes(x.status));
          const terminal = this.operations.filter(x => !active.includes(x));
          json = { namespace, items: [...active, ...terminal].slice(0, 25), nextCursor: this.operations.length > 25 ? 'c_more' : null };
        } else if (path.startsWith('operations/')) json = this.operations.find(x => x.operationId === path.split('/')[1]);
        else if (path.startsWith('traces/')) json = { ...example('paper-trace'), complete: false };
        else throw new Error('Unexpected Paper read');
        await route.fulfill({ json }); return;
      }
      const body = request.postDataJSON() as Record<string, unknown>;
      this.writes.push({ path, key: request.headers()['idempotency-key'], body });
      if (path === 'trading-profile') {
        if (this.holdProfile) await this.holdProfile;
        this.snapshot.profile = { ...this.snapshot.profile, configured: true, riskBudget: body['riskBudget'] as string, strategy: body['strategy'] as 'mock-neutral', strategyVersion: `${body['strategy']}.v1` as 'mock-neutral.v1', credentialGeneration: 1, accountGeneration: 1, policyVersion: 1, profileVersion: 1, budgetVersion: 1, limitPolicyVersion: 1, feeAttestation: { scope: 'alpaca-paper-direct-retail-us-equity-zero-v1', confirmed: true } };
        await route.fulfill({ json: this.snapshot.profile }); return;
      }
      if (path === 'order-requests' && this.holdManual) await this.holdManual;
      if (path === 'order-requests' && this.failManualOnce) { this.failManualOnce = false; await route.fulfill({ status: 503, json: { code: 'TEMPORARILY_UNAVAILABLE' } }); return; }
      const kind: PaperOperation['operationType'] = path === 'verify-connection' ? 'VERIFY_CONNECTION' : path === 'prepare-ledger' ? 'PREPARE_LEDGER' : path === 'start' ? 'START' : path === 'order-requests' ? 'PLACE_LIMIT_ORDER' : path.endsWith('/cancel') ? 'CANCEL_ORDER' : body['action'] as PaperOperation['operationType'];
      const operation = this.operation(kind, kind === 'PLACE_LIMIT_ORDER' || kind === 'CANCEL_ORDER' ? 'RUNNING' : 'SUCCEEDED');
      if (kind === 'CANCEL_ORDER') {
        operation.orderId = path.split('/')[1];
        this.orders[0].cancel = { state: 'READY', requestedAt: this.now(), nextAttemptAt: this.now(), deleteAttempts: 0 };
      }
      if (kind === 'VERIFY_CONNECTION') { this.snapshot.status.setupState = 'VERIFIED'; this.snapshot.status.verifiedAt = this.now(); this.snapshot.verificationOperationId = operation.operationId; }
      if (kind === 'PREPARE_LEDGER') this.snapshot.status.setupState = 'PREPARED';
      if (kind === 'START') { this.snapshot.status.operatingState = 'RUNNING'; this.snapshot.status.entryGateOpen = true; this.snapshot.status.blockers = []; }
      if (['PAUSE', 'STOP', 'KILL'].includes(kind)) { this.snapshot.status.operatingState = kind === 'KILL' ? 'HALTED' : kind === 'STOP' ? 'STOPPED' : 'PAUSED'; this.snapshot.status.entryGateOpen = false; }
      await route.fulfill({ status: 202, json: operation });
    });
  }

  running(): void {
    this.snapshot.status.operatingState = 'RUNNING'; this.snapshot.status.entryGateOpen = true; this.snapshot.status.blockers = [];
    for (const market of this.snapshot.markets) if (market.session) {
      market.session.open = true;
      market.session.startsAt = new Date(this.base - 3600000).toISOString();
      market.session.endsAt = new Date(this.base + 3600000).toISOString();
    }
  }
}

async function activate(page: Page, request: APIRequestContext): Promise<void> {
  await request.post('/__test/reset');
  await page.goto('/#alpaca-paper');
  await page.getByLabel('Aktivierungscode').fill('synthetic-local-activation-code-000001');
  await page.getByRole('button', { name: 'Sitzung aktivieren' }).click();
  await expect(page.getByRole('heading', { name: 'Paperhandel', exact: true })).toBeVisible();
  await expect(page.getByText('paper-11111111', { exact: true })).toBeVisible();
}

async function confirm(page: Page): Promise<void> { await page.getByRole('dialog').getByRole('button', { name: 'Bestätigen', exact: true }).click(); }

test('isolated exact financial views and pending operations survive reload without writes', async ({ page, request }) => {
  const f = new PaperFixture();
  f.snapshot.portfolio!.cash = '99992.500000000000000001';
  const pending = f.operation('PLACE_LIMIT_ORDER', 'RUNNING');
  for (let i = 0; i < 26; i++) f.operation('PAUSE');
  f.orders[0].status = 'FILLED'; f.orders[0].filledQuantity = '1'; f.orders[0].cancel!.state = 'FINISHED';
  await f.install(page); await activate(page, request);
  await expect(page.getByText('99992.500000000000000001', { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Dauerhafte Papervorgänge' }).getByText(pending.operationId, { exact: false })).toBeVisible();
  const orders = page.getByRole('region', { name: 'Paperorders' });
  await expect(orders.getByText('FILLED', { exact: true })).toBeVisible();
  await expect(orders.getByText('Cancel FINISHED', { exact: false })).toBeVisible();
  await expect(orders.getByRole('button', { name: 'Stornierung anfordern' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'LIMIT-Auftrag prüfen' })).toBeDisabled();
  await page.reload();
  await expect(page.getByRole('region', { name: 'Dauerhafte Papervorgänge' }).getByText(pending.operationId, { exact: false })).toBeVisible();
  expect(f.writes).toHaveLength(0);
  await page.getByRole('button', { name: 'Übersicht', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Demo & Einrichtung', exact: true })).toBeVisible();
});

test('guided commands bind current versions and authoritative verification ID without automatic start', async ({ page, request }) => {
  const f = new PaperFixture(); f.snapshot.status.setupState = 'VERIFIED'; f.snapshot.profile = example('paper-snapshot.unconfigured').profile;
  await f.install(page); await activate(page, request);
  await page.getByRole('button', { name: 'Verbindung prüfen', exact: true }).click();
  await expect.poll(() => f.writes.length).toBe(1);
  await page.getByRole('button', { name: 'Paperledger vorbereiten' }).click(); await confirm(page);
  await expect.poll(() => f.writes.length).toBe(2);
  expect(f.writes[1].body['verificationOperationId']).toBe(f.snapshot.verificationOperationId);
  expect(f.writes.some(x => x.path === 'start')).toBe(false);
  await page.getByLabel('Paper-Riskbudget in USD').fill('100.000000000000000001');
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Paperprofil speichern' }).click();
  await expect.poll(() => f.writes.length).toBe(3);
  expect(f.writes[2].body['riskBudget']).toBe('100.000000000000000001');
  await page.getByRole('button', { name: 'Paperstart bestätigen' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Abbrechen' }).click();
  expect(f.writes.some(x => x.path === 'start')).toBe(false);
  await page.getByRole('button', { name: 'Paperstart bestätigen' }).click(); await confirm(page);
  await expect.poll(() => f.writes.filter(x => x.path === 'start').length).toBe(1);
  const body = f.writes.find(x => x.path === 'start')!.body;
  expect(body).toMatchObject({ credentialGeneration: 1, accountGeneration: 1, profileVersion: 1, budgetVersion: 1, policyVersion: 1, limitPolicyVersion: 1, confirmation: 'START_PAPER' });
});

test('ambiguous manual transport retries identical key terms and expiry while pause remains available', async ({ page, request }) => {
  const f = new PaperFixture(); f.running(); f.failManualOnce = true;
  await f.install(page); await activate(page, request);
  await page.getByLabel('Exakte Ordermenge').fill('0.123456789');
  await page.getByLabel('LIMIT-Preis in USD').fill('15.00');
  await page.getByRole('button', { name: 'LIMIT-Auftrag prüfen' }).click(); await confirm(page);
  await expect(page.getByRole('button', { name: 'Unveränderte Anfrage erneut prüfen' })).toBeVisible();
  await expect(page.getByLabel('Exakte Ordermenge')).toHaveValue('');
  await page.getByRole('button', { name: 'Paper pausieren', exact: true }).click(); await confirm(page);
  await expect.poll(() => f.writes.some(x => x.path === 'control')).toBe(true);
  await page.getByRole('button', { name: 'Unveränderte Anfrage erneut prüfen' }).click();
  await expect.poll(() => f.writes.filter(x => x.path === 'order-requests').length).toBe(2);
  const [first, retry] = f.writes.filter(x => x.path === 'order-requests');
  expect(retry.key).toBe(first.key); expect(retry.body).toEqual(first.body);
  expect(first.body).toMatchObject({ quantity: '0.123456789', limitPrice: '15.00', orderType: 'LIMIT', timeInForce: 'DAY', session: 'REGULAR' });
  const count = f.writes.length; await page.reload();
  await expect(page.getByRole('region', { name: 'Dauerhafte Papervorgänge' }).getByText('PLACE_LIMIT_ORDER', { exact: true })).toBeVisible();
  expect(f.writes.length).toBe(count);
});

test('session loss closes the reused private setup dialog and clears forms without browser persistence', async ({ page, request }) => {
  const f = new PaperFixture(); f.running(); await f.install(page); await activate(page, request);
  await page.getByLabel('Exakte Ordermenge').fill('0.5');
  await page.getByRole('button', { name: 'Paper-Schlüssel einrichten', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('API Key', { exact: true }).fill('synthetic-browser-private-canary');
  await dialog.getByLabel('API Secret').fill('synthetic-browser-secret-canary');
  f.unauthorized = true;
  await expect(dialog).toBeHidden({ timeout: 8000 });
  await expect(page.getByRole('button', { name: 'Paper-Kill', exact: true })).toBeDisabled();
  f.unauthorized = false;
  await page.getByRole('button', { name: 'Paper aktualisieren' }).click();
  await expect(page.getByLabel('Exakte Ordermenge')).toHaveValue('');
  const state = await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length, content: document.body.innerText }));
  expect(state.local + state.session).toBe(0);
  expect(state.content).not.toContain('synthetic-browser'); expect(f.writes).toHaveLength(0);
});

test('SELL terms and positive cancellation remain exact while trace and older pages keep historical truth', async ({ page, request }) => {
  const f = new PaperFixture(); f.running();
  f.orders[0].status = 'ACKNOWLEDGED'; f.orders[0].cancel = null;
  f.moreOrders = [{ ...f.orders[0], orderId: '22345678-1234-4234-8234-123456789012', clientOrderId: 'historic-market-client', proposalSource: 'MODEL_DECISION', decisionId: 'historic-decision', humanRequestId: null, orderType: 'MARKET', limitPrice: null, timeInForce: null, session: null }];
  await f.install(page); await activate(page, request);
  await page.getByLabel('Orderseite').click(); await page.getByRole('option', { name: 'SELL', exact: true }).click();
  await page.getByLabel('Exakte Ordermenge').fill('0.500000000'); await page.getByLabel('LIMIT-Preis in USD').fill('14.99');
  await page.getByRole('button', { name: 'LIMIT-Auftrag prüfen' }).click();
  await expect(page.getByRole('dialog')).toContainText('SELL 0.500000000 SPY zu mindestens 14.99 USD');
  await confirm(page);
  await expect.poll(() => f.writes.filter(x => x.path === 'order-requests').length).toBe(1);
  expect(f.writes[0].body).toMatchObject({ side: 'SELL', quantity: '0.500000000', limitPrice: '14.99' });
  const orders = page.getByRole('region', { name: 'Paperorders' });
  await orders.getByRole('button', { name: 'Stornierung anfordern', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Bestätigen', exact: true }).dblclick();
  await expect.poll(() => f.writes.filter(x => x.path.endsWith('/cancel')).length).toBe(1);
  const cancel = f.writes.find(x => x.path.endsWith('/cancel'))!;
  expect(cancel.path).toBe(`orders/${f.orders[0].orderId}/cancel`);
  expect(cancel.body).toEqual({ accountGeneration: 1, confirmation: 'CANCEL_PAPER_ORDER' });
  expect(cancel.key).toMatch(/^[A-Za-z0-9._:-]{16,128}$/);
  await expect(orders.getByText('ACKNOWLEDGED', { exact: true })).toBeVisible();
  await expect(orders.getByText('Cancel READY', { exact: false })).toBeVisible();
  await expect(orders.getByRole('button', { name: 'Stornierung anfordern', exact: true })).toBeDisabled();
  await orders.getByRole('button', { name: 'Ordertrace ansehen' }).click();
  await expect(page.getByRole('region', { name: 'Papertrace' })).toContainText('Ansicht begrenzt');
  await page.getByRole('button', { name: 'Weitere Paperorders' }).click();
  await expect(orders.getByText('historic-market-client', { exact: false })).toBeVisible();
  expect(f.readUrls.some(x => x.includes('cursor=c_page2') && x.includes('limit=25'))).toBe(true);
  const before = f.reads.filter(x => x === 'snapshot').length;
  await expect.poll(() => f.reads.filter(x => x === 'snapshot').length, { timeout: 7000 }).toBeGreaterThan(before);
  await expect(orders.getByText('historic-market-client', { exact: false })).toBeVisible();
  f.orders[0].status = 'FILLED'; f.orders[0].filledQuantity = f.orders[0].quantity; f.orders[0].cancel!.state = 'FINISHED';
  await page.getByRole('button', { name: 'Paper aktualisieren' }).click();
  await expect(orders.getByText('FILLED', { exact: true })).toBeVisible();
  await expect(orders.getByText('Cancel FINISHED', { exact: false })).toBeVisible();
  expect(f.writes.filter(x => x.path.endsWith('/cancel'))).toHaveLength(1);
});

test('STOP and KILL require confirmation and blocked or failed operations expose only public reasons', async ({ page, request }) => {
  const f = new PaperFixture();
  const blocked = f.operation('PLACE_LIMIT_ORDER', 'BLOCKED'); blocked.errorCode = 'BUDGET_EXCEEDED';
  const failed = f.operation('VERIFY_CONNECTION', 'FAILED'); failed.errorCode = 'CREDENTIALS_INVALID';
  await f.install(page); await activate(page, request);
  const operations = page.getByRole('region', { name: 'Dauerhafte Papervorgänge' });
  await expect(operations.getByText('BUDGET_EXCEEDED:', { exact: false })).toBeVisible();
  await expect(operations.getByText('CREDENTIALS_INVALID:', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Paper stoppen', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Abbrechen' }).click();
  expect(f.writes).toHaveLength(0);
  await page.getByRole('button', { name: 'Paper stoppen', exact: true }).click(); await confirm(page);
  await expect.poll(() => f.writes.length).toBe(1);
  expect(f.writes[0].body).toEqual({ action: 'STOP' });
  await page.getByRole('button', { name: 'Paper-Kill', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('keine automatische Liquidation');
  await confirm(page);
  await expect.poll(() => f.writes.length).toBe(2);
  expect(f.writes[1].body).toEqual({ action: 'KILL' });
  await expect(page.getByLabel('Gespeicherter Paperzustand')).toContainText('HALTED');
});

test('delayed pending-operation read keeps start and manual entry closed while controls remain independent', async ({ page, request }) => {
  const f = new PaperFixture(); f.running(); f.operation('PLACE_LIMIT_ORDER', 'RUNNING');
  let release!: () => void;
  f.holdOperations = new Promise<void>(resolve => { release = resolve; });
  await f.install(page); await activate(page, request);
  await page.getByLabel('Exakte Ordermenge').fill('1'); await page.getByLabel('LIMIT-Preis in USD').fill('15');
  await expect(page.getByRole('button', { name: 'LIMIT-Auftrag prüfen' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Paperstart bestätigen' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Paper pausieren', exact: true })).toBeEnabled();
  release();
  await expect(page.getByRole('region', { name: 'Dauerhafte Papervorgänge' }).getByText('PLACE_LIMIT_ORDER', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'LIMIT-Auftrag prüfen' })).toBeDisabled();
  expect(f.writes).toHaveLength(0);
});

test('in-flight manual and profile writes close all new-entry controls until their outcomes are known', async ({ page, request }) => {
  const f = new PaperFixture(); f.running();
  let releaseManual!: () => void;
  f.holdManual = new Promise<void>(resolve => { releaseManual = resolve; });
  await f.install(page); await activate(page, request);
  await page.getByLabel('Exakte Ordermenge').fill('1'); await page.getByLabel('LIMIT-Preis in USD').fill('15');
  await page.getByRole('button', { name: 'LIMIT-Auftrag prüfen' }).click(); await confirm(page);
  await expect.poll(() => f.writes.length).toBe(1);
  await expect(page.getByRole('button', { name: 'Paperstart bestätigen' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Paper-Kill', exact: true })).toBeEnabled();
  releaseManual();
  await expect(page.getByRole('region', { name: 'Dauerhafte Papervorgänge' }).getByText('PLACE_LIMIT_ORDER', { exact: true })).toBeVisible();
  f.operations = [];
  await page.getByRole('button', { name: 'Paper aktualisieren' }).click();
  let releaseProfile!: () => void;
  f.holdProfile = new Promise<void>(resolve => { releaseProfile = resolve; });
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Paperprofil speichern' }).click();
  await expect.poll(() => f.writes.length).toBe(2);
  await expect(page.getByRole('button', { name: 'LIMIT-Auftrag prüfen' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Paperstart bestätigen' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Paper pausieren', exact: true })).toBeEnabled();
  releaseProfile();
});

test('expired stored proofs remain visibly expired and cannot enable a manual order', async ({ page, request }) => {
  const f = new PaperFixture(); f.running();
  const market = f.snapshot.markets[0];
  for (const proof of [market.quote, market.capability, market.session]) proof!.validUntil = '2026-10-23T23:59:59Z';
  await f.install(page); await activate(page, request);
  await expect(page.getByText('abgelaufen oder unbestätigt', { exact: true })).toBeVisible();
  await expect(page.getByText('Reguläre Sitzung Sitzungsbeleg abgelaufen', { exact: false })).toBeVisible();
  await page.getByLabel('Exakte Ordermenge').fill('1'); await page.getByLabel('LIMIT-Preis in USD').fill('15');
  await expect(page.getByRole('button', { name: 'LIMIT-Auftrag prüfen' })).toBeDisabled();
});

test('strict public projection rejects private extras invalid dates and forged human provenance', async ({ page, request }) => {
  const f = new PaperFixture(); f.running(); await f.install(page); await activate(page, request);
  f.orders[0].decisionId = 'fabricated-model-decision';
  await page.getByRole('button', { name: 'Paper aktualisieren' }).click();
  await expect(page.getByRole('alert')).toContainText('öffentlichen Vertrag');
  f.orders = []; f.snapshot.observedAt = '2026-02-30T01:00:00Z'; f.malformed = true;
  await page.getByRole('button', { name: 'Paper aktualisieren' }).click();
  await expect(page.getByRole('alert')).toContainText('öffentlichen Vertrag');
  f.malformed = false; f.snapshot = { ...f.snapshot, privateAccountId: 'private-canary' } as PaperSnapshot;
  await page.getByRole('button', { name: 'Paper aktualisieren' }).click();
  await expect(page.getByRole('alert')).toContainText('öffentlichen Vertrag');
  expect(await page.locator('body').innerText()).not.toContain('private-canary');
  expect(f.writes).toHaveLength(0);
});
