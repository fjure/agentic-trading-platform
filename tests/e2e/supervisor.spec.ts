import { test, expect, type APIRequestContext, type Page } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';

const activationCode = 'synthetic-local-activation-code-000001';
const traceId = '4bf92f3577b34da6a3ce929d0e0e4736';

type FixtureState = {
  mode: { pauseNew: boolean; killSwitch: boolean };
  snapshotReads: number;
  connections: number;
  streams: number;
  requests: { method: string; path: string; cursor: string | null; traceId: string | null; lastEventId: string | null; idempotencyKey: string | null }[];
  writes: { kind: string; key: string; status: number }[];
};

async function state(request: APIRequestContext): Promise<FixtureState> {
  const response = await request.get('/__test/state');
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function control(request: APIRequestContext, path: string, data?: object): Promise<void> {
  const response = await request.post(`/__test/${path}`, { data: data ?? {} });
  expect(response.ok()).toBeTruthy();
}

async function activate(page: Page, request: APIRequestContext): Promise<void> {
  await page.goto('/');
  await expect(page.getByLabel('Aktivierungscode')).toBeVisible();
  await page.getByLabel('Aktivierungscode').fill(activationCode);
  await page.getByRole('button', { name: 'Sitzung aktivieren' }).click();
  await expect.poll(async () => (await state(request)).streams).toBeGreaterThan(0);
  await expect(page.getByRole('heading', { name: 'Paper-Supervisor' })).toBeVisible();
}

async function confirm(page: Page, button: string): Promise<void> {
  await page.getByRole('button', { name: button, exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Bestätigen' }).click();
}

test.beforeEach(async ({ request }) => {
  await control(request, 'reset');
});

test('the browser starts unauthenticated, activates locally, and reloads from a fresh snapshot', async ({ page, request }) => {
  await page.goto('/');
  await expect.poll(async () => (await state(request)).requests.some(r => r.path === '/api/v1/snapshot')).toBeTruthy();
  await expect(page.getByLabel('Aktivierungscode')).toBeVisible();
  expect((await state(request)).snapshotReads).toBe(0);

  await page.getByLabel('Aktivierungscode').fill(activationCode);
  await page.getByRole('button', { name: 'Sitzung aktivieren' }).click();
  await expect.poll(async () => (await state(request)).streams).toBeGreaterThan(0);

  const cookies = await page.context().cookies();
  const session = cookies.find(cookie => cookie.name === 'atl_session');
  expect(session).toMatchObject({ httpOnly: true, sameSite: 'Strict' });
  const browserStorage = await page.evaluate(async () => ({
    local: localStorage.length,
    session: sessionStorage.length,
    databases: (await indexedDB.databases()).length,
    codeInDocument: document.body.innerText.includes('synthetic-local-activation-code'),
  }));
  expect(browserStorage).toEqual({ local: 0, session: 0, databases: 0, codeInDocument: false });

  const reads = (await state(request)).snapshotReads;
  await page.reload();
  await expect.poll(async () => (await state(request)).snapshotReads).toBeGreaterThan(reads);
  await expect(page.getByRole('heading', { name: 'Paper-Supervisor' })).toBeVisible();
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('confirmation gates controls and a refused resume leaves new entries paused', async ({ page, request }) => {
  await activate(page, request);

  await page.getByRole('button', { name: 'Neue Einstiege pausieren', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Abbrechen' }).click();
  expect((await state(request)).writes).toHaveLength(0);

  await confirm(page, 'Neue Einstiege pausieren');
  await expect.poll(async () => (await state(request)).mode.pauseNew).toBe(true);
  const pause = (await state(request)).writes.filter(write => write.kind === 'pause');
  expect(pause).toHaveLength(1);
  expect(pause[0].key).toMatch(/^[A-Za-z0-9._:-]{16,128}$/);

  await control(request, 'refuse-resume');
  await confirm(page, 'Fortsetzen anfordern');
  await expect.poll(async () => (await state(request)).writes.some(write => write.kind === 'resume' && write.status === 409)).toBeTruthy();
  expect((await state(request)).mode.pauseNew).toBe(true);
  await expect(page.getByText('Die Gesundheitsprüfung war nicht erfolgreich.')).toBeVisible();
});

test('kill switch requires confirmation and remains latched', async ({ page, request }) => {
  await activate(page, request);
  await confirm(page, 'Kill-Switch aktivieren');
  await expect.poll(async () => (await state(request)).mode.killSwitch).toBe(true);
  expect((await state(request)).mode.pauseNew).toBe(true);
  const writes = (await state(request)).writes;
  expect(writes).toHaveLength(1);
  expect(writes[0].kind).toBe('kill');
});

test('stream reconnects with Last-Event-ID and a 410 starts a new snapshot', async ({ page, request }) => {
  await activate(page, request);
  const first = await request.post('/__test/emit', { data: {} });
  const event = await first.json();
  await expect.poll(async () => (await state(request)).snapshotReads).toBeGreaterThan(1);

  await control(request, 'disconnect');
  await expect.poll(async () => (await state(request)).requests.some(r => r.path === '/api/v1/events' && r.lastEventId === event.cursor)).toBeTruthy();

  const reads = (await state(request)).snapshotReads;
  await control(request, 'expire-next');
  await control(request, 'disconnect');
  await expect.poll(async () => (await state(request)).snapshotReads).toBeGreaterThan(reads);
  await expect.poll(async () => (await state(request)).streams).toBeGreaterThan(2);
});

test('reset-required frame and aggregate version gap both force a fresh snapshot', async ({ page, request }) => {
  await activate(page, request);
  const beforeReset = (await state(request)).snapshotReads;
  await control(request, 'reset-required');
  await expect.poll(async () => (await state(request)).snapshotReads).toBeGreaterThan(beforeReset);
  await expect.poll(async () => (await state(request)).streams).toBeGreaterThan(1);

  const first = await request.post('/__test/emit', { data: { aggregateVersion: 1 } });
  const firstEvent = await first.json();
  await expect.poll(async () => (await state(request)).snapshotReads).toBeGreaterThan(beforeReset + 1);
  const beforeDuplicate = (await state(request)).snapshotReads;
  await control(request, 'emit', { eventId: firstEvent.eventId, aggregateVersion: 1 });
  await page.waitForTimeout(350);
  expect((await state(request)).snapshotReads).toBe(beforeDuplicate);
  const beforeGap = (await state(request)).snapshotReads;
  await control(request, 'emit', { aggregateVersion: 3 });
  await expect.poll(async () => (await state(request)).snapshotReads).toBeGreaterThan(beforeGap);
});

test('45 seconds without heartbeat marks the connection stale', async ({ page, request }) => {
  await page.clock.install({ time: new Date('2026-10-12T14:45:05Z') });
  await activate(page, request);
  await page.clock.fastForward(46_000);
  await expect(page.locator('.connection-pill')).toHaveText('Daten veraltet');
});

test('heartbeats keep the connection current across successive idle periods', async ({ page, request }) => {
  await page.clock.install({ time: new Date('2026-10-12T14:45:05Z') });
  await activate(page, request);
  await page.clock.fastForward(30_000);
  await control(request, 'heartbeat');
  await page.waitForTimeout(100);
  await page.clock.fastForward(30_000);
  await expect(page.locator('.connection-pill')).toHaveText('Aktuell verbunden');
});

test('all supervisor views expose structured synthetic evidence without raw payloads', async ({ page, request }) => {
  await activate(page, request);
  await expect(page.getByText('Synthetic paper account')).toBeVisible();
  await expect(page.getByText('100000.00')).toBeVisible();
  await expect(page.getByText('Heartbeats')).toBeVisible();

  const views = [
    { nav: 'Markt', title: 'SPY', expected: ['Bar Ende', '550.25', 'Evidenzqualität', 'VALID'] },
    { nav: 'Entscheidungen', title: 'LONG', expected: ['Confidence', '0.80', 'Verteilung LONG', 'Regime TREND', '0.7', 'Signalkonflikt'] },
    { nav: 'Vorschläge', title: 'SPY', expected: ['Menge', '1.25', 'Begründungscodes', 'dec_1'] },
    { nav: 'Risiko', title: 'prop_1', expected: ['Freigabe', 'FREIGEGEBEN', 'Reservierungs-ID', 'res_1'] },
    { nav: 'Orders', title: 'SPY', expected: ['PARTIALLY_FILLED', 'Gefüllte Menge', '0.50', 'Reconciliation', 'CURRENT'] },
    { nav: 'Audit', title: 'MARKET_OBSERVED', expected: ['Akteur', 'market-worker', 'Reason-Code'] },
    { nav: 'Worker', title: 'WORKER', expected: ['Letzter Heartbeat', 'RUNNING'] },
    { nav: 'Kosten', title: 'JEV', expected: ['NICHT VERFÜGBAR', 'Betrag', 'nicht verfügbar', 'USD'] },
  ];
  for (const view of views) {
    await page.getByRole('button', { name: view.nav, exact: true }).click();
    const row = page.getByRole('row').filter({ hasText: view.title }).first();
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: `Details ${view.title}` }).click();
    const detail = page.locator('section.detail-panel');
    await expect(detail).toBeVisible();
    for (const value of view.expected) {
      await expect(page.locator('main')).toContainText(value);
    }
  }
});

test('history uses cursor pagination and audit trace filtering', async ({ page, request }) => {
  await activate(page, request);
  await page.getByRole('button', { name: 'Markt', exact: true }).click();
  await expect(page.getByText('SPY').first()).toBeVisible();
  await page.getByRole('button', { name: 'Weitere laden' }).click();
  await expect(page.getByText('QQQ').first()).toBeVisible();
  expect((await state(request)).requests.some(r => r.path === '/api/v1/market-states' && r.cursor === 'c_page2')).toBeTruthy();

  await page.getByRole('button', { name: 'Audit', exact: true }).click();
  await page.getByLabel('Trace-ID').fill(traceId);
  await page.getByRole('button', { name: 'Trace laden' }).click();
  await expect.poll(async () => (await state(request)).requests.some(r => r.path === '/api/v1/audit' && r.traceId === traceId)).toBeTruthy();
  await expect(page.getByText('PARTIAL_FILL_RECONCILED').first()).toBeVisible();
  const chain = page.locator('.trace-chain');
  await expect(chain).toContainText('TRACE-KETTE');
  await expect(chain).toContainText('PARTIALLY_FILLED');
  await expect(chain).toContainText('LONG');
});

test('trace search reaches evidence on later history pages', async ({ page, request }) => {
  await activate(page, request);
  await page.getByRole('button', { name: 'Audit', exact: true }).click();
  await page.getByLabel('Trace-ID').fill(traceId);
  await page.getByRole('button', { name: 'Trace laden' }).click();
  const chain = page.locator('.trace-chain');
  await expect(chain).toContainText('SPY');
  await expect(chain).not.toContainText('QQQ');
  await page.getByRole('button', { name: 'Weitere Trace-Evidenz suchen' }).click();
  await expect(chain).toContainText('QQQ');
  await expect(page.getByText('Alle verfügbaren Historienseiten durchsucht.')).toBeVisible();
  expect((await state(request)).requests.some(r => r.path === '/api/v1/market-states' && r.cursor === 'c_page2')).toBeTruthy();
});

test('mobile navigation and details stay within the viewport', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await activate(page, request);
  await page.getByRole('button', { name: 'Orders', exact: true }).click();
  const row = page.getByRole('row').filter({ hasText: 'SPY' }).first();
  await row.getByRole('button', { name: 'Details SPY' }).click();
  await expect(page.locator('section.detail-panel')).toContainText('PARTIALLY_FILLED');
  const widths = await page.evaluate(() => ({ viewport: window.innerWidth, content: document.documentElement.scrollWidth }));
  expect(widths.content).toBeLessThanOrEqual(widths.viewport + 1);
});

test('a revoked session clears displayed data and requires a new activation', async ({ page, request }) => {
  await activate(page, request);
  await page.getByRole('button', { name: 'Orders', exact: true }).click();
  await expect(page.getByText('PARTIALLY_FILLED').first()).toBeVisible();
  await control(request, 'revoke');
  await expect(page.getByLabel('Aktivierungscode')).toBeVisible();
  await expect(page.getByText('PARTIALLY_FILLED')).toHaveCount(0);
  await expect(page.getByText('Eine Operator-Sitzung ist erforderlich.')).toBeVisible();
});

test('an ambiguous control transport failure retains its idempotency key for retry', async ({ page, request }) => {
  let first = true;
  const keys: string[] = [];
  await page.route('**/api/v1/control/pause', async route => {
    keys.push(route.request().headers()['idempotency-key']);
    if (first) {
      first = false;
      await route.abort('failed');
    } else {
      await route.continue();
    }
  });
  await activate(page, request);
  await confirm(page, 'Neue Einstiege pausieren');
  await expect(page.getByRole('button', { name: 'Vorgang erneut prüfen' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Neue Einstiege pausieren' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Kill-Switch aktivieren' })).toBeEnabled();
  expect((await state(request)).writes).toHaveLength(0);
  await page.getByRole('button', { name: 'Vorgang erneut prüfen' }).click();
  await expect.poll(async () => (await state(request)).mode.pauseNew).toBe(true);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect((await state(request)).writes).toHaveLength(1);
});

test('a malformed success response retries the same command key without a second write', async ({ page, request }) => {
  let first = true;
  const keys: string[] = [];
  await page.route('**/api/v1/control/pause', async route => {
    keys.push(route.request().headers()['idempotency-key']);
    if (first) {
      first = false;
      const response = await route.fetch();
      const malformed = await response.json();
      delete malformed.mode;
      await route.fulfill({ response, json: malformed });
    } else {
      await route.continue();
    }
  });
  await activate(page, request);
  await confirm(page, 'Neue Einstiege pausieren');
  await expect(page.getByRole('button', { name: 'Vorgang erneut prüfen' })).toBeVisible();
  expect((await state(request)).writes).toHaveLength(1);
  await page.getByRole('button', { name: 'Vorgang erneut prüfen' }).click();
  await expect(page.getByRole('button', { name: 'Vorgang erneut prüfen' })).toHaveCount(0);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  expect((await state(request)).writes).toHaveLength(1);
  expect((await state(request)).mode.pauseNew).toBe(true);
});

test('emergency controls remain available after an ambiguous resume', async ({ page, request }) => {
  await activate(page, request);
  await confirm(page, 'Neue Einstiege pausieren');
  await expect.poll(async () => (await state(request)).mode.pauseNew).toBe(true);
  await page.route('**/api/v1/control/resume', route => route.abort('failed'));
  await confirm(page, 'Fortsetzen anfordern');
  await expect(page.getByRole('button', { name: 'Vorgang erneut prüfen' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Neue Einstiege pausieren' })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Kill-Switch aktivieren' })).toBeEnabled();
  await confirm(page, 'Kill-Switch aktivieren');
  await expect.poll(async () => (await state(request)).mode.killSwitch).toBe(true);
  expect((await state(request)).mode.pauseNew).toBe(true);
});

test('unknown provider payload is never rendered by the history detail view', async ({ page, request }) => {
  const canary = 'RAW_PROVIDER_SECRET_CANARY';
  await page.route('**/api/v1/decisions*', async route => {
    const response = await route.fetch();
    const data = await response.json();
    data.items[0].rawProviderPayload = canary;
    await route.fulfill({ response, json: data });
  });
  await activate(page, request);
  await page.getByRole('button', { name: 'Entscheidungen', exact: true }).click();
  const row = page.getByRole('row').filter({ hasText: 'LONG' }).first();
  await row.getByRole('button', { name: 'Details LONG' }).click();
  await expect(page.locator('section.detail-panel')).toContainText('Verteilung LONG');
  await expect(page.locator('main')).not.toContainText(canary);
});
