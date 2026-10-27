import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { test, expect, type Page, type Route } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import type { CollectionPlan, ReadinessAssessment } from '../../apps/dashboard/src/app/api/generated/control-api/model/models';

const actual = !!process.env.READINESS_BROWSER_CONTAINER;
const view = (page: Page) => page.locator('atl-readiness');
const backtest = (page: Page) => page.locator('atl-backtesting');
const assessmentId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const fixture = async (name: string) => JSON.parse(await readFile(resolve('contracts/examples/v1', `${name}.valid.json`), 'utf8'));

async function activate(page: Page): Promise<void> {
  await page.goto('/');
  await page.getByLabel('Aktivierungscode', { exact: true }).fill(actual
    ? process.env.READINESS_BROWSER_ACTIVATION! : 'synthetic-local-activation-code-000001');
  await page.getByRole('button', { name: 'Sitzung aktivieren', exact: true }).click();
  await expect(view(page).getByRole('heading', { name: 'Datenzulässigkeit', exact: true })).toBeVisible();
}

test.describe('synthetische UIverträge – keine Laufzeit- oder Quellenabnahme', () => {
  test.skip(actual, 'Diese Szenarien verwenden ausdrücklich synthetische HTTPfixtures.');
  test.beforeEach(async ({ request }) => { await request.post('/__test/reset'); });

  async function setup(page: Page, state = 'blocked') {
    const model: { assessment: ReadinessAssessment; plans: CollectionPlan[]; writes: any[]; gets: URL[]; runWrites: number; unauthorized: boolean; conflict: boolean } = {
      assessment: await fixture(`readiness-assessment.${state}`),
      plans: (await fixture('collection-plans.disabled')).items,
      writes: [], gets: [], runWrites: 0, unauthorized: false, conflict: false,
    };
    await page.route('**/api/v1/research/**', async (route: Route) => {
      const request = route.request(), url = new URL(request.url());
      const reply = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
      if (model.unauthorized) return reply(await fixture('readiness-problem.unauthorized'), 401);
      if (url.pathname.endsWith('/backtests/strategies')) return reply(await fixture('backtest-strategy-catalog'));
      if (url.pathname.endsWith('/backtests/runs')) {
        if (request.method() === 'POST') model.runWrites++;
        return reply({ items: [], nextCursor: null });
      }
      if (url.pathname.endsWith('/sources')) return reply(await fixture('research-source-catalog'));
      if (url.pathname.endsWith('/ingestion-jobs')) return reply({ items: [], nextCursor: null });
      if (url.pathname.endsWith('/data-readiness/assessments') && request.method() === 'POST') {
        model.writes.push({ kind: 'diagnosis', body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
        return reply(await fixture('readiness-accepted'), 202);
      }
      if (url.pathname.includes('/data-readiness/assessments/') && !url.pathname.endsWith('/findings')) {
        model.gets.push(url); return reply(model.assessment);
      }
      if (url.pathname.endsWith('/findings')) {
        model.gets.push(url);
        const page = await fixture(state === 'unknown' ? 'readiness-findings.unknown-calendar'
          : url.searchParams.has('cursor') ? 'readiness-findings.last-page' : 'readiness-findings.first-page');
        page.category = url.searchParams.get('category'); return reply(page);
      }
      if (url.pathname.endsWith('/collection/plans')) return reply({ items: model.plans, observedAt: '2026-10-26T12:00:00Z' });
      if (url.pathname.includes('/collection/plans/') && request.method() === 'PUT') {
        model.writes.push({ kind: 'plan', body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
        if (model.conflict) { model.conflict = false; return reply(await fixture('collection-problem.version-conflict'), 409); }
        const plan = await fixture(request.postDataJSON().enabled ? 'collection-plan.blocked' : 'collection-plan.paused-inflight');
        model.plans = model.plans.map(item => item.instrumentId === plan.instrumentId ? plan : item);
        return reply(plan);
      }
      return route.continue();
    });
    return model;
  }

  test('gleiches Experiment, reloadbarer Bericht und keine automatische Simulation', async ({ page }) => {
    const model = await setup(page);
    await activate(page);
    await expect(backtest(page).getByLabel('Backtest Instrument', { exact: true })).toContainText('SPY');
    const button = view(page).getByRole('button', { name: 'Datenzulässigkeit prüfen', exact: true });
    await backtest(page).getByLabel('Backtest Startcash USD', { exact: true }).fill('NaN');
    await expect(button).toBeDisabled();
    await backtest(page).getByLabel('Backtest Startcash USD', { exact: true }).fill('10000.125');
    await button.click();
    await expect(view(page).getByLabel('Diagnosebericht')).toContainText('Datenzulässigkeit: BLOCKED');
    expect(model.writes[0].body.startCash).toBe('10000.125');
    expect(model.writes[0].body.quantityStep).toBe('0.000001');
    expect(model.writes[0].key).toBeTruthy();
    expect(model.runWrites).toBe(0);
    const trust = view(page).getByRole('table', { name: 'Unabhängige Vertrauensdimensionen' }).first();
    for (const label of ['Integrität', 'Quellenauthentizität', 'Zeitliche Authentizität', 'Vollständigkeit', 'Fachliche Qualität']) await expect(trust).toContainText(label);
    expect(new URL(page.url()).searchParams.get('readinessAssessment')).toBe(assessmentId);
    await page.reload();
    await expect(view(page).getByLabel('Diagnosebericht')).toContainText(assessmentId);
    expect(model.writes.filter(row => row.kind === 'diagnosis')).toHaveLength(1);
    expect(model.runWrites).toBe(0);
  });

  test('gebundene Befundseiten, Metriknenner und separate aktuelle Warnung', async ({ page }) => {
    const model = await setup(page, 'revoked');
    await activate(page);
    await view(page).getByRole('button', { name: 'Datenzulässigkeit prüfen', exact: true }).click();
    await expect(view(page).getByLabel('Aktuelle Vertrauenswarnungen')).toContainText(model.assessment.currentRevocation.warnings[0].reason);
    await expect(view(page).getByLabel('Diagnosebericht')).toContainText(`Ursprüngliche Zulässigkeit: ${model.assessment.report.eligibility}`);
    await view(page).getByRole('button', { name: 'Weitere Befunde', exact: true }).click();
    await expect(view(page)).toContainText('CORPORATE_ACTIONS_UNKNOWN');
    await expect(view(page)).toContainText('OBTAIN_TRUSTED_EVIDENCE');
    expect(model.gets.some(url => url.searchParams.has('cursor'))).toBeTruthy();
    await view(page).getByLabel('Befundkategorie', { exact: true }).click();
    await page.getByRole('option', { name: 'TIME_UNPROVEN', exact: true }).click();
    await expect.poll(() => model.gets.some(url => url.searchParams.get('category') === 'TIME_UNPROVEN')).toBeTruthy();
    await view(page).getByText('Erfassungsmetriken und nutzbare Fenster', { exact: true }).click();
    await expect(view(page).getByRole('table', { name: 'Fristen und Nenner' }).first()).toContainText('EXPECTED_BAR_IDENTITIES');
    await expect(view(page).getByRole('table', { name: 'Getrennte Latenzen' }).first()).toContainText('unbekannt / ungültig');
    await expect(view(page)).toContainText('Keine vollständigen Strict-PIT-Experimentfenster nachgewiesen.');
  });

  for (const [state, eligibility] of [['eligible', 'ELIGIBLE'], ['unknown', 'UNKNOWN'], ['not-evaluated', 'NOT_EVALUATED'], ['budget-exhausted', 'UNKNOWN']]) {
    test(`ehrlicher Zulässigkeitszustand ${state}`, async ({ page }) => {
      const model = await setup(page, state);
      await activate(page);
      await view(page).getByRole('button', { name: 'Datenzulässigkeit prüfen', exact: true }).click();
      await expect(view(page).getByLabel('Diagnosebericht')).toContainText(`Datenzulässigkeit: ${eligibility}`);
      if (state === 'unknown') {
        await expect(view(page)).toContainText('CALENDAR_UNKNOWN');
        await expect(view(page)).toContainText('OBSERVE_CALENDAR_BEFORE_SESSION');
      }
      if (state === 'budget-exhausted') await expect(view(page)).toContainText('BUDGET_EXHAUSTED');
      expect(model.runWrites).toBe(0);
    });
  }

  test('versionierte Planänderung, Konflikt ohne Retry und Pause mit laufenden Abrufen', async ({ page }) => {
    const model = await setup(page);
    await activate(page);
    const plan = view(page).getByLabel('Sammlungsplan SPY', { exact: true });
    await plan.getByRole('button', { name: 'Sammlung SPY aktivieren', exact: true }).click();
    await expect(plan).toContainText('BLOCKED');
    expect(model.writes[0].body).toEqual({ enabled: true, expectedVersion: 0 });
    model.conflict = true;
    await plan.getByRole('button', { name: 'Sammlung SPY pausieren', exact: true }).click();
    await expect(view(page).getByRole('alert')).toContainText('Planversion hat sich geändert');
    expect(model.writes.filter(row => row.kind === 'plan')).toHaveLength(2);
    await plan.getByRole('button', { name: 'Sammlung SPY pausieren', exact: true }).click();
    await expect(plan).toContainText('PAUSED');
    await expect(plan).toContainText('Bereits begonnene Abrufe 1');
    expect(model.writes[2].key).not.toBe(model.writes[1].key);
  });

  test('Sitzungsverlust entfernt Berichte und beendet weitere Abfragen', async ({ page }) => {
    const model = await setup(page);
    await activate(page);
    await view(page).getByRole('button', { name: 'Datenzulässigkeit prüfen', exact: true }).click();
    await expect(view(page).getByLabel('Diagnosebericht')).toBeVisible();
    model.unauthorized = true;
    await view(page).getByRole('button', { name: 'Diagnose aktualisieren', exact: true }).click();
    await expect(view(page).getByLabel('Diagnosebericht')).toHaveCount(0);
    await expect(view(page).getByRole('button', { name: 'Datenzulässigkeit prüfen', exact: true })).toBeDisabled();
    await expect(view(page).getByRole('alert')).toContainText('Operator-Sitzung');
    const count = model.gets.length;
    await page.waitForTimeout(2800);
    expect(model.gets).toHaveLength(count);
  });
});

test.describe('echte PostgreSQL-/API-Browserabnahme', () => {
  test.skip(!actual, 'Benötigt den isolierten Readiness-API-/PostgreSQL-Eigentümer.');
  test.setTimeout(90_000);
  test('persistente Diagnose, Reload, sichere Sammlungswünsche und keine Handelswrites', async ({ page }) => {
    const forbidden: string[] = [];
    page.on('request', request => {
      if (request.method() !== 'GET' && /\/orders|\/backtests\/runs|\/mode|\/kill|\/pause/.test(new URL(request.url()).pathname)) forbidden.push(request.url());
    });
    await activate(page);
    await expect(view(page).getByLabel('Sammlungsplan SPY', { exact: true })).toBeVisible();
    await view(page).getByRole('button', { name: 'Datenzulässigkeit prüfen', exact: true }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('readinessAssessment')).not.toBeNull();
    await expect(view(page).getByLabel('Diagnosebericht')).toContainText('SUCCEEDED', { timeout: 45_000 });
    const id = new URL(page.url()).searchParams.get('readinessAssessment');
    await page.reload();
    await expect(view(page).getByLabel('Diagnosebericht')).toContainText(id!);
    await expect(view(page).getByRole('table', { name: 'Unabhängige Vertrauensdimensionen' }).first()).toBeVisible();
    const plan = view(page).getByLabel('Sammlungsplan SPY', { exact: true });
    await plan.getByRole('button', { name: 'Sammlung SPY aktivieren', exact: true }).click();
    await expect(plan).toContainText('BLOCKED');
    await plan.getByRole('button', { name: 'Sammlung SPY pausieren', exact: true }).click();
    await expect(plan).toContainText('PAUSED');
    expect(forbidden).toEqual([]);
  });
});
