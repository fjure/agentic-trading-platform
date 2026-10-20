import { test, expect, type APIRequestContext, type Page } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';

const activationCode = 'synthetic-local-activation-code-000001';

async function control(request: APIRequestContext, path: string): Promise<void> {
  const response = await request.post(`/__test/${path}`);
  expect(response.ok()).toBeTruthy();
}

async function activate(page: Page, request: APIRequestContext): Promise<void> {
  await page.goto('/');
  await page.getByLabel('Aktivierungscode').fill(activationCode);
  await page.getByRole('button', { name: 'Sitzung aktivieren' }).click();
  await expect(page.getByRole('heading', { name: 'Paper-Supervisor' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Demo & Einrichtung' })).toBeVisible();
}

test.beforeEach(async ({ request }) => control(request, 'reset'));

test('demo starts through the API, shows persisted trace evidence after reload and keeps paper out of the simulation', async ({ page, request }) => {
  await activate(page, request);
  await expect(page.getByText('Simulation – keine Brokerverbindung').first()).toBeVisible();
  await page.getByLabel('Startbudget (USD)').fill('125.75');
  await page.getByLabel('Strategievariante').first().click();
  await page.getByRole('option', { name: 'Deterministic neutral baseline' }).click();
  await page.getByRole('button', { name: 'Demo starten' }).click();
  await expect(page.getByText('Demo-Ergebnis: SUCCEEDED')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('125.75 USD', { exact: true })).toBeVisible();
  await expect(page.getByText('4bf92f3577b34da6a3ce929d0e0e4736').first()).toBeVisible();
  await expect(page.getByText('PARTIALLY_FILLED').first()).toBeVisible();
  await expect(page.getByText('PROPOSAL_APPROVED').first()).toBeVisible();
  await expect(page.getByText('99.50 USD', { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Demo-Positionen' }).getByText('SPY', { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByRole('heading', { name: 'Paper-Supervisor' })).toBeVisible();
  await expect(page.getByText('Demo-Ergebnis: SUCCEEDED')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('125.75 USD', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('Alpaca and Jev credentials clear on submit and cancel without entering browser storage or status output', async ({ page, request }) => {
  await activate(page, request);
  const canaries: string[] = [];
  page.on('request', req => {
    if (req.url().includes('/api/v1/integrations/') && req.method() === 'PUT') canaries.push(req.postData() ?? '');
  });

  await page.getByRole('button', { name: 'Alpaca einrichten' }).click();
  await page.getByLabel('API Key').fill('synthetic-alpaca-key-canary');
  await page.getByLabel('API Secret').fill('synthetic-alpaca-secret-canary');
  await page.getByLabel('Erwartete Account UUID').fill('8c0c8b85-b804-4f0e-89bc-4387a73231b4');
  await page.getByRole('dialog').getByRole('button', { name: 'Speichern' }).click();
  await expect(page.getByText('Gespeichert – Verbindung noch nicht geprüft; Aktivierung separat.')).toBeVisible();
  await expect(page.getByText('Gespeichert – Verbindung noch nicht geprüft').first()).toBeVisible();
  await expect(page.getByText('synthetic-alpaca-key-canary', { exact: false })).toHaveCount(0);
  await expect(page.getByText('synthetic-alpaca-secret-canary', { exact: false })).toHaveCount(0);
  expect(canaries.some(body => body.includes('synthetic-alpaca-key-canary') && body.includes('synthetic-alpaca-secret-canary'))).toBeTruthy();

  await page.getByRole('button', { name: 'Alpaca einrichten' }).click();
  await expect(page.getByLabel('API Key')).toHaveValue('');
  await expect(page.getByLabel('API Secret')).toHaveValue('');
  await page.getByRole('dialog').getByRole('button', { name: 'Abbrechen' }).click();

  await page.getByRole('button', { name: 'Jev einrichten' }).click();
  await page.getByLabel('Jev API Key').fill('synthetic-jev-key-canary');
  await page.getByRole('dialog').getByRole('button', { name: 'Abbrechen' }).click();
  await page.getByRole('button', { name: 'Jev einrichten' }).click();
  await expect(page.getByLabel('Jev API Key')).toHaveValue('');
  await page.getByRole('dialog').getByRole('button', { name: 'Abbrechen' }).click();

  const storage = await page.evaluate(async () => ({ local: localStorage.length, session: sessionStorage.length, databases: (await indexedDB.databases()).length, text: document.body.innerText }));
  expect(storage.local + storage.session + storage.databases).toBe(0);
  expect(storage.text).not.toContain('synthetic-alpaca-key-canary');
  expect(storage.text).not.toContain('synthetic-alpaca-secret-canary');
  expect(storage.text).not.toContain('synthetic-jev-key-canary');
  await expect.poll(async () => (await (await request.get('/__test/state')).json()).requests.some((entry: { path: string }) => entry.path === '/api/v1/integrations/alpaca')).toBeTruthy();
});
