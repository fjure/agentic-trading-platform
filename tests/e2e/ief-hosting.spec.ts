import { test, expect } from '../../apps/dashboard/node_modules/@playwright/test';

test('IEF hosting: nested screen reloads serve the production shell', async ({ request }) => {
  for (const path of ['/onboarding', '/portfolio/insights', '/paper/orders/new']) {
    const response = await request.get(path);
    expect(response.status()).toBe(200);
    expect(await response.text()).toContain('<atl-root');
    expect(response.headers()['cache-control']).toBe('no-store');
  }
});

test('IEF hosting: API errors cannot become successful SPA pages', async ({ request }) => {
  for (const path of ['/api', '/api/unknown', '/%61pi/v1/missing', '/api%2fv1/missing']) {
    const response = await request.get(path);
    expect(response.status()).toBe(404);
    expect(await response.text()).not.toContain('<atl-root');
  }
});

test('IEF hosting: malformed paths, missing assets and UI mutations fail safely', async ({ request }) => {
  expect((await request.get('/%ZZ')).status()).toBe(400);
  expect((await request.get('/missing.js')).status()).toBe(404);
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    const response = await request.fetch('/home', { method, data: { confirm: true } });
    expect(response.status()).toBe(405);
    expect(await response.text()).not.toContain('<atl-root');
  }
});
