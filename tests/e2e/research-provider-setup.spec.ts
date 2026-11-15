import { expect, test } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';

const api = '/api/v1/integrations/research-setup/providers';
const grant = 'a'.repeat(43);
const credential = 'synthetic-research-browser-only';

test('Research setup separates local consent, credential rotation and revocation without replay', async ({ page }) => {
  await page.request.post('/__test/reset');
  let state = 'NOT_CONFIGURED';
  let version = 1;
  let generation: number | undefined;
  const mutations: string[] = [];
  let loseResponse = false;
  await page.route(`**${api}/**`, async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const update = request.method() !== 'GET';
    if (update) {
      const action = path.endsWith('/rotate') ? 'rotate' : path.endsWith('/revoke') ? 'revoke' : 'save';
      expect(request.headers()['x-research-setup-grant']).toBe(grant);
      expect(request.postDataJSON()).toEqual({
        expectedCredentialVersion: version,
        ...(action === 'revoke' ? { credentialGeneration: generation } : { credential }),
        ...(action === 'rotate' ? { expectedCredentialGeneration: generation } : {}),
      });
      mutations.push(action);
      state = action === 'revoke' ? 'REVOKED' : 'STORED';
      if (action !== 'revoke') generation = (generation ?? 0) + 1;
      version += 1;
      if (loseResponse) return route.abort();
    }
    return route.fulfill({ json: {
      state, credentialVersion: version, credentialGeneration: generation,
      updatedAt: '2026-01-01T00:00:00Z', providerRevocationState: 'NOT_CONFIRMED',
    } });
  });
  await page.goto('/');
  await page.getByLabel('Aktivierungscode').fill('synthetic-local-activation-code-000001');
  await page.getByRole('button', { name: 'Sitzung aktivieren' }).click();
  const launch = page.getByRole('button', { name: 'Research-Schlüssel verwalten' });
  await launch.click();
  const panel = page.locator('atl-research-provider-setup');
  const dialog = page.getByRole('dialog');
  const open = (name: string) => panel.getByRole('button', { name, exact: true }).click();
  const submit = async (name: string, withCredential: boolean) => {
    if (withCredential) await dialog.getByLabel('Provider-Schlüssel').fill(credential);
    await dialog.getByLabel('Einmalcode für Setup').fill(grant);
    await dialog.getByRole('button', { name, exact: true }).click();
    await expect(dialog).toHaveCount(0);
  };

  await open('Schlüsselstatus abrufen');
  await expect(panel.getByRole('status')).toContainText('Schlüssel fehlt');
  await open('Schlüssel speichern');
  await expect(dialog.getByLabel('Provider-Schlüssel')).toHaveAttribute('type', 'password');
  await dialog.getByLabel('Provider-Schlüssel').fill(credential);
  await dialog.getByRole('button', { name: 'Abbrechen' }).click();
  expect(mutations).toEqual([]);

  await open('Schlüssel speichern');
  await expect(dialog.getByLabel('Provider-Schlüssel')).toHaveValue('');
  await submit('Schlüssel speichern', true);
  await expect(panel.getByRole('status')).toContainText('Gespeichert, gesperrt · Version 2 · Generation 1');
  await open('Schlüssel ersetzen');
  loseResponse = true;
  await submit('Schlüssel ersetzen', true);
  await expect(panel.getByRole('alert')).toContainText('Setup-Anfrage ungeklärt');
  await expect(panel.getByRole('status')).toContainText('Generation 2');
  loseResponse = false;
  await open('Schlüssel lokal widerrufen');
  await expect(dialog.getByLabel('Provider-Schlüssel')).toHaveCount(0);
  await submit('Schlüssel lokal widerrufen', false);
  await expect(panel.getByRole('status')).toContainText('Lokal widerrufen');
  await expect(panel.getByText('Providerwiderruf unbestätigt.')).toBeVisible();

  await page.reload();
  await launch.click();
  await expect(panel.getByRole('heading', { name: 'Research-Schlüssel' })).toBeVisible();
  await expect(dialog).toHaveCount(0);
  await open('Schlüsselstatus abrufen');
  await expect(panel.getByRole('status')).toContainText('Lokal widerrufen');
  await expect(panel.getByRole('button', { name: 'Schlüssel ersetzen' })).toBeVisible();
  expect(mutations).toEqual(['save', 'rotate', 'revoke']);
  const stored = await page.evaluate(() => JSON.stringify([localStorage, sessionStorage, document.cookie]));
  expect(stored).not.toContain(credential);
  expect(stored).not.toContain(grant);
});
