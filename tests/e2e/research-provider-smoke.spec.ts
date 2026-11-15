import { expect, test } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';

test('provider smoke needs a current unblocked quote and an explicit click after reload', async ({ page }) => {
  const grantId = '98798d51-cd5d-4d95-bb25-f3f60ff50862';
  const commitment = 'a'.repeat(64);
  let blocked = true;
  let attempts = 0;
  let previews = 0;
  await page.route(/\/api\/v1\/research\/agent\/providers(?:\?.*)?$/, route => route.fulfill({
    json: { items: [{
      binding: { provider: 'OPENAI', profileId: 'fixture', profileGeneration: 1,
        modelId: 'synthetic', modelGeneration: 1 },
      credential: { state: 'STORED', credentialGeneration: 2 }, availability: 'BLOCKED',
      probeState: attempts ? 'QUEUED' : 'NOT_TESTED',
    }] },
  }));
  await page.route('**/api/v1/research/agent/providers/OPENAI/probe-previews', route => {
    previews++;
    expect(route.request().postDataJSON()).toEqual({
      profileId: 'fixture', profileGeneration: 1, modelGeneration: 1, credentialGeneration: 2,
    });
    return route.fulfill({ json: { grantId, commitment, maximumCost: '0.71',
      operatorLimit: blocked ? '0.65' : '0.80', tariffRevision: 3, currency: 'USD',
      inputBoundKind: 'MODEL', expiresAt: new Date(Date.now() + 60_000).toISOString(),
      ...(blocked ? { blockedReason: 'BUDGET_EXHAUSTED' } : {}),
    } });
  });
  await page.route('**/api/v1/research/agent/providers/OPENAI/probe', route => {
    attempts++;
    expect(route.request().postDataJSON()).toMatchObject({ grantId, commitment, credentialGeneration: 2 });
    return route.fulfill({ status: 202, json: { status: 'QUEUED' } });
  });

  await page.request.post('/__test/reset');
  await page.goto('/');
  await page.getByLabel('Aktivierungscode').fill('synthetic-local-activation-code-000001');
  await page.getByRole('button', { name: 'Sitzung aktivieren' }).click();
  const open = page.getByRole('button', { name: 'Research Companion öffnen' });
  const panel = page.locator('atl-research-provider-smoke');
  const refresh = panel.getByRole('button', { name: 'Verbindungstest-Status laden' });
  const preview = panel.getByRole('button', { name: 'Testvorschau laden' });
  const confirm = panel.getByRole('button', { name: 'Verbindungstest einmalig freigeben' });

  await open.click();
  await refresh.click();
  await preview.click();
  await expect(panel.getByText('0.71 USD')).toBeVisible();
  await expect(panel.getByText('0.65 USD')).toBeVisible();
  await expect(panel.getByText('Modellobergrenze')).toBeVisible();
  await expect(confirm).toBeDisabled();
  expect(attempts).toBe(0);

  blocked = false;
  await page.reload();
  await open.click();
  await refresh.click();
  await expect(confirm).toHaveCount(0);
  expect(previews).toBe(1);
  expect(attempts).toBe(0);
  await preview.click();
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(panel.getByText('Teststatus: QUEUED')).toBeVisible();
  await expect(preview).toBeDisabled();
  expect(attempts).toBe(1);
});
