import { test, expect, type Page } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';

const demo = (page: Page) => page.locator('atl-demo-setup');
type Run = { runId: string; traceId: string; status: string; initialBudget: string; strategy: string };
test.skip(!process.env.DEMO_BROWSER_CONTAINER, 'Run through scripts/test-demo-browser.ps1 with its isolated real API owner.');

async function start(page: Page, budget: string, variant: string): Promise<Run> {
  await demo(page).getByLabel('Startbudget (USD)', { exact: true }).fill(budget);
  await demo(page).getByRole('combobox', { name: 'Strategievariante' }).first().click();
  await page.getByRole('option', { name: variant, exact: true }).click();
  const response = page.waitForResponse(r => r.url().endsWith('/api/v1/demo/runs') && r.request().method() === 'POST');
  await demo(page).getByRole('button', { name: 'Demo starten', exact: true }).click();
  const reply = await response;
  expect(reply.status()).toBe(202);
  await expect(demo(page).getByRole('heading', { name: /Demo-Ergebnis: (SUCCEEDED|FAILED)/ })).toBeVisible();
  const listed = await page.request.get('/api/v1/demo/runs');
  expect(listed.status()).toBe(200);
  return (await listed.json()).items[0] as Run;
}

async function snapshot(page: Page, run: Run) {
  const response = await page.request.get(`/api/v1/demo/runs/${run.runId}/snapshot`);
  expect(response.status()).toBe(200);
  return response.json();
}

test('Chromium click → canonical API → persistent PostgreSQL: budgets, traces, reload, setup and closed failure', async ({ page }) => {
  const external: string[] = [];
  page.on('request', request => {
    if (!request.url().startsWith('http://127.0.0.1:4210/')) external.push(new URL(request.url()).origin);
  });
  await page.goto('/');
  await expect(page.getByLabel('Aktivierungscode')).toBeVisible();
  await page.getByLabel('Aktivierungscode').fill(process.env.DEMO_BROWSER_ACTIVATION!);
  await page.getByRole('button', { name: 'Sitzung aktivieren' }).click();
  await expect(demo(page).getByRole('heading', { name: 'Demo & Einrichtung' })).toBeVisible();
  await expect(demo(page).getByRole('note')).toContainText('Simulation – keine Brokerverbindung');

  const runs: Run[] = [];
  for (const [budget, variant, expectedCash] of [
    ['100', 'Mock neutral', '95'],
    ['200', 'Deterministic neutral baseline', '195'],
  ]) {
    const run = await start(page, budget, variant);
    runs.push(run);
    await expect(demo(page).getByRole('heading', { name: 'Demo-Ergebnis: SUCCEEDED', exact: true })).toBeVisible();
    const result = demo(page).locator('.result-card');
    await expect(result).toContainText(run.traceId);
    await expect(result.locator('.portfolio')).toContainText('FILLED');
    await expect(result.locator('.portfolio > div').filter({ hasText: 'Demo-Cash' })).toContainText(new RegExp(`${expectedCash}(?:\\.0+)? USD`));
    await expect(result.getByRole('region', { name: 'Demo-Positionen' })).toContainText('SPY');
    await expect(result.getByRole('region', { name: 'Demo-Positionen' })).toContainText('Menge 1');
    const before = await snapshot(page, run);
    expect(before.portfolioCash).toMatch(new RegExp(`^${expectedCash}(?:\\.0+)?$`));
    expect(before.mode.pauseNew).toBe(true);
    expect(before.recentOrders).toHaveLength(1);
    expect(before.recentOrders[0]).toMatchObject({ status: 'FILLED', traceId: run.traceId });
    expect(before.recentOrders[0].filledQuantity).toMatch(/^1(?:\.0+)?$/);
    for (const resource of ['market-states', 'decisions', 'proposals', 'risk-decisions', 'orders', 'audit']) {
      const response = await page.request.get(`/api/v1/demo/runs/${run.runId}/history/${resource}?traceId=${run.traceId}`);
      expect(response.status()).toBe(200);
      const history = await response.json();
      expect(history.items.length).toBeGreaterThan(0);
      expect(history.items.every((item: { traceId: string }) => item.traceId === run.traceId)).toBe(true);
    }
    for (const section of ['Orders', 'Entscheidungen', 'Risk', 'Audit']) {
      const evidence = result.locator('section.evidence').filter({ has: page.getByRole('heading', { name: section, exact: true }) });
      await expect(evidence.locator('details').first()).toBeVisible();
      await evidence.locator('summary').first().click();
      await expect(evidence).toContainText(run.traceId);
    }
    await page.reload();
    await expect(demo(page).locator('.run-id')).toContainText(run.runId);
    await expect(demo(page).locator('.result-card')).toContainText(run.traceId);
    await expect(demo(page).locator('.portfolio')).toContainText('FILLED');
    const restored = await snapshot(page, run);
    expect(restored.recentOrders[0].id).toBe(before.recentOrders[0].id);
    expect(restored.portfolioCash).toMatch(new RegExp(`^${expectedCash}(?:\\.0+)?$`));
    expect(restored.positions).toEqual(before.positions);
  }
  expect(runs[0].runId).not.toBe(runs[1].runId);

  // Only synthetic canaries. No body, value, screenshot, trace or video is logged.
  for (const provider of ['alpaca', 'jev']) {
    await demo(page).getByRole('button', { name: provider === 'alpaca' ? 'Alpaca einrichten' : 'Jev einrichten' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel(provider === 'alpaca' ? 'API Key' : 'Jev API Key', { exact: true }).fill('synthetic-demo-canary-key');
    if (provider === 'alpaca') {
      await dialog.getByLabel('API Secret', { exact: true }).fill('synthetic-demo-canary-secret');
      await dialog.getByLabel('Erwartete Account UUID').fill('2bc296c4-e33d-4fea-a6cd-292e51498204');
    }
    const saved = page.waitForResponse(r => r.url().endsWith(`/api/v1/integrations/${provider}`) && r.request().method() === 'PUT');
    await dialog.getByRole('button', { name: 'Speichern', exact: true }).click();
    const savedReply = await saved;
    expect(savedReply.status()).toBe(200);
    // Avoid printing any body even if a future regression echoes credentials.
    expect((await savedReply.text()).includes('synthetic-demo-canary')).toBe(false);
    await expect(dialog).toHaveCount(0);
    await demo(page).getByRole('button', { name: provider === 'alpaca' ? 'Alpaca einrichten' : 'Jev einrichten' }).click();
    // Boolean-only comparisons keep a failed assertion from exposing field values.
    expect(await page.getByRole('dialog').locator('input').evaluateAll(inputs => inputs.every(input => (input as HTMLInputElement).value === ''))).toBe(true);
    await page.getByRole('dialog').getByRole('button', { name: 'Abbrechen' }).click();
  }
  expect(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]).includes('synthetic-demo-canary'))).toBe(false);
  expect((await page.locator('body').innerText()).includes('synthetic-demo-canary')).toBe(false);
  const integrationReply = await page.request.get('/api/v1/integrations');
  expect((await integrationReply.text()).includes('synthetic-demo-canary')).toBe(false);
  expect(await integrationReply.json()).toMatchObject({ alpaca: { status: 'CONFIGURED' }, jev: { status: 'CONFIGURED' } });

  const failed = await start(page, '0.01', 'Deterministic neutral baseline');
  await expect(demo(page).getByRole('heading', { name: 'Demo-Ergebnis: FAILED', exact: true })).toBeVisible();
  const closed = await snapshot(page, failed);
  expect(closed.mode.pauseNew).toBe(true);
  expect(closed.recentOrders).toHaveLength(0);
  expect(closed.portfolioCash).toMatch(/^0\.010*$/);
  await page.reload();
  await expect(demo(page).getByRole('heading', { name: 'Demo-Ergebnis: FAILED', exact: true })).toBeVisible();
  await expect(demo(page).locator('.run-id')).toContainText(failed.runId);
  // Earlier runs stay selectable with the same durable order after failure.
  await demo(page).getByRole('button', { name: new RegExp(runs[0].runId) }).click();
  await expect(demo(page).locator('.run-id')).toContainText(runs[0].runId);
  await expect(demo(page).locator('.portfolio')).toContainText('FILLED');
  expect(external).toEqual([]);
});
