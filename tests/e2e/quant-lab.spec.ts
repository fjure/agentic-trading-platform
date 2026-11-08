import { test, expect, type Page, type BrowserContext, type Route } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import { execFileSync } from 'node:child_process';
import type {
  QuantLabBatch, QuantLabBatchStartRequest, QuantLabExperimentDefinition, QuantLabResultPage, QuantLabValidation,
} from '../../apps/dashboard/src/app/api/generated/control-api/model/models';

const activation = process.env.QSL_BROWSER_ACTIVATION;
test.skip(!activation, 'Requires the isolated Quant Lab browser owner.');
test.describe.configure({ mode: 'serial' });
const root = '/api/v1/research';
const lab = (page: Page) => page.locator('atl-quant-lab');
const periods = JSON.parse(process.env.QSL_BROWSER_PERIODS ?? '{}') as Record<string, [string, string]>;
const fullSessions = process.env.QSL_BROWSER_FULL === '1';
const hypothesis = fullSessions ? 'Synthetische EMA-Vollsitzungen' : 'Konstante synthetische EMA-Preise';
let cookies: Awaited<ReturnType<BrowserContext['cookies']>> = [];
let batchId = '';
let definitionId = '';
let startRequest: QuantLabBatchStartRequest;

async function activate(page: Page): Promise<void> {
  if (cookies.length) await page.context().addCookies(cookies);
  await page.goto('/');
  if (!cookies.length) {
    await page.getByLabel('Aktivierungscode', { exact: true }).fill(activation!);
    await page.getByRole('button', { name: 'Sitzung aktivieren', exact: true }).click();
  }
  await page.getByRole('button', { name: 'Quant Strategy Lab öffnen', exact: true }).click();
  await expect(lab(page).getByRole('heading', { name: 'Quant Strategy Lab', exact: true })).toBeVisible();
  cookies = await page.context().cookies();
}

async function submit<T>(page: Page, path: string, click: () => Promise<void>) {
  // Tee the real server reply. Chromium can evict response bodies from its
  // CDP buffer after the app updates the URL. The browser gets the same reply.
  const matches = (url: URL) => url.pathname === root + path;
  let captured!: { status: number; body: T };
  const handler = async (route: Route) => {
    if (route.request().method() !== 'POST') return route.continue();
    const upstream = await route.fetch({ maxRedirects: 0 });
    captured = { status: upstream.status(), body: await upstream.json() as T };
    await route.fulfill({ response: upstream });
  };
  await page.route(matches, handler);
  const delivered = page.waitForResponse(response =>
    response.request().method() === 'POST' && matches(new URL(response.url())),
  );
  try {
    await click();
    await delivered;
    return captured;
  } finally {
    await page.unroute(matches, handler);
  }
}

test('authenticated Material builder runs nine real strategies and restores the bound report', async ({ page }) => {
  test.setTimeout(360_000);
  await activate(page);
  const scope = lab(page);
  await expect(scope.getByLabel('Forschungsmodus', { exact: true })).toContainText('Strict PIT');
  const instrument = scope.getByLabel('Instrument', { exact: true });
  await instrument.click();
  await page.getByRole('option', { name: /^SPY\b/ }).click();
  await scope.getByLabel('Forschungshypothese', { exact: true }).fill(hypothesis);
  await expect(scope.getByLabel('Strategie für Raster 1', { exact: true })).toContainText(/EMA/i);
  await scope.getByLabel('Fast-Werte (Raster 1)', { exact: true }).fill('5,10,15');
  await scope.getByLabel('Slow-Werte (Raster 1)', { exact: true }).fill('30,40,50');
  for (const [name, key] of [['Training', 'training'], ['Validation', 'validation'], ['Test', 'test'], ['Holdout', 'holdout']]) {
    const suffix = name === 'Holdout' ? '' : ' (Fold 1)';
    await scope.getByLabel(name + ' Start UTC' + suffix, { exact: true }).fill(periods[key][0]);
    await scope.getByLabel(name + ' Ende UTC' + suffix, { exact: true }).fill(periods[key][1]);
  }
  await scope.getByLabel('Maximaler Drawdown', { exact: true }).fill('0.15');
  await scope.getByLabel('Minimale Validation-Sitzungen', { exact: true }).fill('20');
  await scope.getByLabel('Minimale Entries', { exact: true }).fill(fullSessions ? '50' : '1');
  await scope.getByLabel('Gebühr (USD)', { exact: true }).fill('1');
  const draftResponse = await submit<QuantLabExperimentDefinition>(page, '/experiments', () =>
    scope.getByRole('button', { name: 'Entwurf speichern', exact: true }).click());
  expect(draftResponse.status).toBe(201);
  const draft = draftResponse.body;
  expect(draft.candidateCount).toBe(9);
  expect(draft.definition.researchMode).toBe('STRICT_PIT');
  expect(draft.fittingStatus).toBe('NOT_TRAINABLE');
  expect(draft.definition.strategySelectionPolicy.minimumInvestedBarRatio).toBeNull();

  const validationResponse = await submit<QuantLabValidation>(page, '/experiments/' + draft.id + '/validations', () =>
    scope.getByRole('button', { name: 'Entwurf validieren', exact: true }).click());
  expect(validationResponse.status).toBe(201);
  const validation = validationResponse.body;
  expect(validation.status).toBe('VALIDATED');
  expect(validation.budgetEstimate.reservedQueueSlots).toBe(11);
  expect(validation.budgetEstimate.calendarConfirmed).toBe(true);
  await expect(scope).toContainText('EXTERNAL_KNOWLEDGE_UNKNOWN');
  definitionId = draft.id;
  startRequest = {
    schemaVersion: 'quant-lab-v1', definitionVersion: draft.definitionVersion,
    definitionHash: draft.definitionHash, validationId: validation.id,
    validationHash: validation.validationHash,
  };

  const startResponse = await submit<QuantLabBatch>(page, '/experiments/' + draft.id + '/batches', () =>
    scope.getByRole('button', { name: 'Batch starten', exact: true }).click());
  expect(startResponse.status).toBe(202);
  const batch = startResponse.body;
  batchId = batch.id;
  await expect(scope).toContainText('SUCCEEDED', { timeout: 310_000 });
  await expect(scope).toContainText('NO_ELIGIBLE_CANDIDATE');
  await expect(scope).toContainText('MINIMUM_ENTRIES_NOT_MET');
  await expect(scope).toContainText('NOT_TRAINABLE');
  await expect(scope).toContainText('NO_EXPOSURE');
  await expect(scope.getByText('Validation-Sensitivität', { exact: true })).toBeVisible();

  const more = scope.getByRole('button', { name: 'Weitere Ergebnisse', exact: true });
  await expect(more).toBeVisible();
  const paged = page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname === root + '/experiment-batches/' + batchId + '/results' && url.searchParams.has('cursor');
  });
  await more.click();
  expect((await paged).status()).toBe(200);
  await expect(scope).toContainText('NOT_EVALUATED_NO_SELECTION');
  const training = await page.request.get(root + '/experiment-batches/' + batchId + '/results?phase=training&fold=0');
  expect(training.status()).toBe(200);
  const results = (await training.json() as QuantLabResultPage).items;
  const buyHold = results.find(row => row.role === 'REFERENCE_BUY_HOLD');
  if (fullSessions) {
    expect(Number(buyHold?.metrics?.netReturn.value)).toBeGreaterThan(0);
  } else {
    expect(buyHold?.metrics?.netReturn.value).toMatch(/^-/);
  }

  await scope.getByRole('button', { name: 'Kurven ansehen', exact: true }).first().click();
  await expect(scope.getByText('Equity und Drawdown', { exact: true })).toBeVisible();
  await expect(scope.getByRole('img', { name: 'Equity an den gelieferten Bar-Schlüssen' })).toBeVisible();
  await expect(scope.getByRole('img', { name: 'Drawdown an den gelieferten Bar-Schlüssen' })).toBeVisible();
  await scope.getByRole('button', { name: 'Reliability ansehen', exact: true }).first().click();
  await expect(scope.getByText('Reliability-Bericht', { exact: true })).toBeVisible();
  await expect(scope).toContainText('STRICT_PIT_EVIDENCE_COMPLETE');
  await expect.poll(() => new URL(page.url()).searchParams.get('quantReport')).not.toBeNull();
  await page.reload();
  await expect(scope.getByText('Reliability-Bericht', { exact: true })).toBeVisible();
  await expect(scope).toContainText('STRICT_PIT_EVIDENCE_COMPLETE');
  await expect(scope.getByLabel('Forschungshypothese', { exact: true })).toHaveValue(hypothesis);
  expect(new URL(page.url()).searchParams.get('quantBatch')).toBe(batchId);
});

test('session loss discards a delayed real response and stops Quant Lab requests', async ({ page }) => {
  await activate(page);
  const scope = lab(page);
  const owner = process.env.BACKTEST_BROWSER_CONTAINER ?? '';
  if (!/^atl-backtest-browser-[0-9a-f]{20}-app$/.test(owner)) throw new Error('Isolated Quant Lab owner required');
  execFileSync('docker', ['exec', owner, 'python', '-c', 'from pathlib import Path; Path("/tmp/qsl-browser-worker-paused").touch()'],
    { stdio: 'pipe', timeout: 15_000 });
  const active = await page.evaluate(async ({ path, body }) => {
    const response = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify(body),
    });
    if (response.status !== 202) throw new Error('Could not queue isolated follow-up batch');
    return response.json() as Promise<QuantLabBatch>;
  }, { path: root + '/experiments/' + definitionId + '/batches', body: startRequest });
  expect(active.state).toBe('ACCEPTED');
  await page.goto('/?quantBatch=' + active.id);
  await expect(scope.getByRole('button', { name: 'Batchstatus aktualisieren', exact: true })).toBeVisible();
  await expect(scope.getByText('REGISTERED_REUSE', { exact: true })).toBeVisible();
  await expect(scope.getByRole('cell', { name: 'WALK_FORWARD_OOS / WALK_FORWARD_OOS' })).toBeVisible();
  await expect(scope.getByRole('cell', { name: 'FINAL_HOLDOUT / FINAL_HOLDOUT' })).toBeVisible();

  let release!: () => void;
  let received!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const requested = new Promise<void>(resolve => { received = resolve; });
  await page.route(url => url.pathname === root + '/experiment-batches/' + active.id, async route => {
    const upstream = await route.fetch();
    expect(upstream.status()).toBe(200);
    received();
    await held;
    await route.fulfill({ response: upstream }).catch(() => {});
  }, { times: 1 });

  try {
    // The batch is held in ACCEPTED by the test owner. This response is the
    // actual scheduled poll, and its successful response predates logout.
    await requested;
    await page.context().clearCookies();
    const denied = page.waitForResponse(response =>
      new URL(response.url()).pathname === root + '/sources' && response.status() === 401,
    );
    await page.getByRole('button', { name: 'Quellen neu laden', exact: true }).click();
    await denied;
    await expect(page.getByLabel('Aktivierungscode', { exact: true })).toBeVisible();
    await expect(scope).toHaveCount(0);
  } finally {
    release();
  }
  const future: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith(root + '/experiment')) future.push(request.method());
  });
  await page.waitForTimeout(5500);
  expect(future).toEqual([]);
  await expect(scope).toHaveCount(0);
});
