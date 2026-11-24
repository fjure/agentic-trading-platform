import { expect, test, type Page, type Route } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';

const AS_OF = '2026-11-24T12:30:00Z';
const OBSERVED_AT = '2026-11-24T13:00:00Z';
const DEMO_RUN = '8c0c8b85-b804-4f0e-89bc-4387a73231b4';
const SOURCE_REFS = [{ kind: 'SNAPSHOT', id: 'source-snapshot', version: 9 }];
type Context = 'FAKE' | 'DEMO' | 'ALPACA_PAPER';
const NAMESPACES = { FAKE: 'fake', DEMO: 'demo', ALPACA_PAPER: 'alpaca-paper' };

const metric = (value: string | null, unit = 'USD', reason = 'VALUATION_NOT_AVAILABLE') => ({
  value, currency: 'USD', unit, methodVersion: 'portfolio-metric-v1',
  availability: value === null ? 'UNAVAILABLE' : 'AVAILABLE',
  reasonCodes: value === null ? [reason] : [], asOf: value === null ? null : AS_OF,
  sourceReferences: value === null ? [] : SOURCE_REFS,
});

const position = (instrument: string, quantity: string) => ({
  instrument, ledgerVersion: 9, quantity: metric(quantity, 'QUANTITY'),
  averagePrice: {
    ...metric(null, 'USD', 'METHOD_INPUTS_UNAVAILABLE'),
    nativeAmount: { value: '17.250000000000000001', currency: 'EUR', asOf: AS_OF, availability: 'AVAILABLE' },
  },
  costBasis: metric(null), realizedPnl: metric(null, 'USD', 'FEE_EVIDENCE_MISSING'),
  marketValue: metric(null), unrealizedPnl: metric(null), weight: metric(null, 'RATIO'),
  sourceReferences: [{ kind: 'POSITION', id: 'position-source', version: 9 }],
});

function portfolio(context: Context, id: string, cash: string | null, positions: ReturnType<typeof position>[] | null) {
  const paper = context === 'ALPACA_PAPER';
  return {
    schemaVersion: '1',
    contextReference: {
      context, namespace: NAMESPACES[context], ...(context === 'DEMO' && { demoRunId: DEMO_RUN }),
    },
    observedAt: OBSERVED_AT, snapshotId: id, snapshotVersion: 9,
    accountGeneration: paper ? 1 : null, portfolioLedgerVersion: cash === null ? null : 9,
    asOf: cash === null ? null : AS_OF, quality: cash === null ? 'UNAVAILABLE' : 'CURRENT',
    reconciliation: paper ? 'CURRENT' : 'NOT_APPLICABLE', ...(paper && { reconciliationAt: AS_OF }),
    metrics: {
      cash: metric(cash), equity: metric(cash), exposure: metric(cash === null ? null : '0'),
      realizedPnl: metric(null, 'USD', 'FEE_EVIDENCE_MISSING'),
      marketValue: metric(null), unrealizedPnl: metric(null),
      dailyReturn: metric(null, 'RATIO', 'PERFORMANCE_HISTORY_NOT_AVAILABLE'),
      totalReturn: metric(null, 'RATIO', 'PERFORMANCE_HISTORY_NOT_AVAILABLE'),
      drawdown: metric(null, 'RATIO', 'PERFORMANCE_HISTORY_NOT_AVAILABLE'),
    },
    positions, marketEvidence: [], blockers: cash === null ? ['LEDGER_NOT_PREPARED'] : [],
    sourceReferences: cash === null ? [] : SOURCE_REFS, nextCursor: null as string | null,
  };
}

async function mock(page: Page, respond: (route: Route, params: URLSearchParams) => Promise<unknown>) {
  const portfolioRequests: string[] = [];
  const writes: string[] = [];
  page.on('request', request => {
    if (request.method() !== 'GET' && new URL(request.url()).pathname.startsWith('/api/')) writes.push(request.url());
  });
  await page.route('**/api/v1/experience/preferences', route => route.fulfill({
    json: { schemaVersion: '1', contextReference: { context: 'DEMO', namespace: 'demo', demoRunId: DEMO_RUN } },
  }));
  await page.route('**/api/v1/experience/portfolio?*', route => {
    const params = new URL(route.request().url()).searchParams;
    portfolioRequests.push(params.toString());
    return respond(route, params);
  });
  return { portfolioRequests, writes };
}

test('IEF03 keeps sources separate while switching and reads only the saved demo', async ({ page }) => {
  let resolvePaper!: () => void;
  const holdPaper = new Promise<void>(resolve => { resolvePaper = resolve; });
  const { portfolioRequests, writes } = await mock(page, async (route, query) => {
    const context = query.get('context');
    if (context === 'ALPACA_PAPER') {
      await holdPaper;
      return route.fulfill({ json: portfolio('ALPACA_PAPER', 'paper-snapshot', '991', []) });
    }
    if (context === 'DEMO') {
      expect(query.get('demoRunId')).toBe(DEMO_RUN);
      return route.fulfill({ json: portfolio('DEMO', 'demo-snapshot', '101.550000000000000001', [position('SPY', '1.25')]) });
    }
    expect(query.has('demoRunId')).toBe(false);
    return route.fulfill({ json: portfolio('FAKE', 'fake-snapshot', '70.5000', []) });
  });
  const readCash = (value: string) => page.getByText(value, { exact: true }).first();
  const source = page.getByRole('navigation', { name: 'Portfolioquelle' });
  await page.goto('/portfolio');
  await expect(page.getByText('Wählen Sie Fake-Ledger, Ihre gespeicherte Demo oder Alpaca Paper.')).toBeVisible();
  expect(portfolioRequests).toHaveLength(0);
  await source.getByRole('link', { name: 'Fake-Ledger' }).click();
  await expect(readCash('70.5000 USD')).toBeVisible();
  await source.getByRole('link', { name: 'Alpaca Paper' }).click();
  await expect(page.getByRole('status')).toHaveText('Portfolio wird geladen.');
  await expect(readCash('70.5000 USD')).toHaveCount(0);
  await source.getByRole('link', { name: 'Gespeicherte Demo' }).click();
  try {
    await expect(readCash('101.550000000000000001 USD')).toBeVisible();
  } finally {
    resolvePaper();
  }
  await expect(readCash('991 USD')).toHaveCount(0);
  await expect(source.getByRole('link', { name: 'Gespeicherte Demo' })).toHaveAttribute('aria-current', 'true');
  await page.reload();
  await expect(readCash('101.550000000000000001 USD')).toBeVisible();
  const previous = portfolioRequests.length;
  await page.goto('/portfolio?context=DEMO&demoRunId=foreign-run');
  await expect(page.getByRole('link', { name: 'Demo in der Einführung wählen' })).toBeVisible();
  expect(portfolioRequests).toHaveLength(previous);
  expect(writes).toEqual([]);
});

test('IEF03 reports stale readings, unknown positions, and the difference between unknown cash and zero', async ({ page }) => {
  const { writes } = await mock(page, async (route, query) => {
    if (query.get('context') === 'ALPACA_PAPER') {
      const response = portfolio('ALPACA_PAPER', 'paper-empty', '0', []);
      response.quality = 'STALE';
      response.metrics.cash.availability = 'STALE';
      response.metrics.cash.reasonCodes = ['STALE_EVIDENCE'];
      return route.fulfill({ json: response });
    }
    return route.fulfill({ json: portfolio('FAKE', 'fake-unavailable', null, null) });
  });
  await page.goto('/portfolio?context=FAKE');
  await expect(page.getByText('Der Positionsbestand ist unbekannt.')).toBeVisible();
  await expect(page.getByText('0 USD', { exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'Alpaca Paper' }).click();
  await expect(page.getByRole('status')).toHaveText('Veralteter Datenstand');
  await expect(page.getByText('0 USD', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Für diesen Bestand sind keine offenen Positionen bestätigt.')).toBeVisible();
  await expect(page.getByText(`Prüfzeit ${AS_OF}`)).toBeVisible();
  await page.getByText('Bewertung und Performance', { exact: true }).click();
  await expect(page.getByText('Die belegte Historie einschließlich Zahlungsflüssen fehlt.').first()).toBeVisible();
  await expect(page.getByText('Gebühren sind nicht vollständig belegt.')).toBeVisible();
  expect(writes).toEqual([]);
});

test('IEF03 binds positions to a snapshot and restarts from the first page on conflict', async ({ page }) => {
  let changed = false;
  const { portfolioRequests } = await mock(page, async (route, query) => {
    const cursor = query.get('cursor');
    expect(query.get('limit')).toBe('20');
    if (cursor === 'cursor-one') {
      return route.fulfill({
        json: { ...portfolio('FAKE', 'initial-snapshot', '20', [position('QQQ', '1')]), nextCursor: 'cursor-two' },
      });
    }
    if (cursor === 'cursor-two') {
      changed = true;
      return route.fulfill({ status: 409, json: { code: 'CURSOR_EXPIRED' } });
    }
    return route.fulfill({
      json: changed ? portfolio('FAKE', 'new-snapshot', '22', [position('VTI', '3')])
        : { ...portfolio('FAKE', 'initial-snapshot', '20', [position('SPY', '2')]), nextCursor: 'cursor-one' },
    });
  });
  await page.goto('/portfolio?context=FAKE');
  await expect(page.getByRole('button', { name: 'SPY · Positionsdetails' })).toBeVisible();
  const more = page.getByRole('button', { name: 'Weitere Positionen laden' });
  await more.click();
  await expect(page.getByRole('button', { name: 'QQQ · Positionsdetails' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'SPY · Positionsdetails' })).toBeVisible();
  await more.click();
  await expect(page.getByRole('alert')).toContainText('ab der ersten Seite');
  await expect(page.getByText('20 USD', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Aktuellen Stand neu laden' }).click();
  await expect(page.getByRole('button', { name: 'VTI · Positionsdetails' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'SPY · Positionsdetails' })).toHaveCount(0);
  expect(portfolioRequests.map(query => new URLSearchParams(query).get('cursor'))).toEqual([null, 'cursor-one', 'cursor-two', null]);
});

test('IEF03 renders data and provenance with keyboard controls on mobile and keeps native currency separate', async ({ page }) => {
  const { writes } = await mock(page, async route =>
    route.fulfill({ json: portfolio('FAKE', 'fixture-snapshot', '100', [position('SPY', '0.123456789012345678')]) }),
  );
  for (const width of [320, 360, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/portfolio?context=FAKE');
    const details = page.getByRole('button', { name: 'SPY · Positionsdetails' });
    await expect(details).toBeVisible();
    await details.focus();
    await page.keyboard.press('Enter');
    await expect(details).toHaveAttribute('aria-expanded', 'true');
    await expect(page.getByRole('heading', { name: 'SPY · Positionsdetails' })).toBeVisible();
    await expect(page.getByText('Originalwährung · 17.250000000000000001 EUR')).toBeVisible();
    await expect(page.getByText('0.123456789012345678', { exact: true }).first()).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Menge' })).toHaveCount(width < 768 ? 0 : 1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  }
  await page.getByText('Datenherkunft und Revisionen', { exact: true }).click();
  await expect(page.getByText('fixture-snapshot · Version 9')).toBeVisible();
  expect(writes).toEqual([]);
});

test('IEF03 resolves network errors and rejects wrong-context responses before displaying any values', async ({ page }) => {
  let result: 'network' | 'other-context' | 'unsigned' = 'network';
  const { writes } = await mock(page, route => {
    if (result === 'network') return route.abort('failed');
    if (result === 'unsigned') return route.fulfill({ status: 401, json: { code: 'SESSION_REQUIRED' } });
    return route.fulfill({ json: portfolio('ALPACA_PAPER', 'wrong-snapshot', '450', []) });
  });
  await page.goto('/portfolio?context=FAKE');
  await expect(page.getByRole('alert')).toContainText('derzeit nicht erreichbar');
  result = 'other-context';
  await page.getByRole('button', { name: 'Aktuellen Stand neu laden' }).click();
  await expect(page.getByRole('alert')).toContainText('konnte nicht bestätigt werden');
  await expect(page.getByText('450 USD', { exact: true })).toHaveCount(0);
  result = 'unsigned';
  await page.getByRole('button', { name: 'Aktuellen Stand neu laden' }).click();
  await expect(page.getByRole('link', { name: 'Lokale Sitzung aktivieren' })).toBeVisible();
  expect(writes).toEqual([]);
});
