import { test, expect } from '../../apps/dashboard/node_modules/@playwright/test/index.mjs';
import {
  InvalidPublicDto, projectPage, projectPolicy, projectSnapshot,
} from '../../apps/dashboard/src/app/state/public-dto-schema';

test('public projection strips unknown nested payloads while preserving exact values', async ({ request }) => {
  const response = await request.get('/__test/fixtures');
  expect(response.ok()).toBeTruthy();
  const fixture = await response.json();
  const canary = 'RAW_PROVIDER_SECRET_CANARY';

  fixture.snapshot.rawSession = canary;
  fixture.snapshot.mode.internalToken = canary;
  fixture.snapshot.recentOrders[0].rawBrokerPayload = { accessToken: canary };
  const snapshot = projectSnapshot(fixture.snapshot);
  expect(JSON.stringify(snapshot)).not.toContain(canary);
  expect(snapshot.portfolioEquity).toBe('100000.00');
  expect(snapshot.recentOrders[0].requestedQuantity).toBe('1.25');

  fixture.pages.decisions.items[0].rawProviderPayload = canary;
  fixture.pages.decisions.items[0].typedAnswers.regime.privateReasoning = canary;
  const decisions = projectPage('decisions', fixture.pages.decisions);
  expect(JSON.stringify(decisions)).not.toContain(canary);
  expect(decisions.items[0]).toMatchObject({ confidence: '0.80', estimatedCost: null });
  expect((decisions.items[0] as { typedAnswers: { regime: { distribution: { TREND: string } } } })
    .typedAnswers.regime.distribution.TREND).toBe('0.80');

  const costs = projectPage('costs', fixture.pages.costs);
  expect(costs.items[0]).toMatchObject({ amount: null, currency: 'USD' });
  expect(projectPolicy(fixture.riskPolicy).maxDailyLossFraction).toBe('0.02');
});

test('public projection rejects malformed maps and cannot pollute object prototypes', async ({ request }) => {
  const fixture = await (await request.get('/__test/fixtures')).json();
  const snapshot = projectSnapshot(fixture.snapshot);
  expect(Object.getPrototypeOf(snapshot.connections)).toBeNull();
  expect(Object.getPrototypeOf(snapshot)).toBeNull();
  expect(({} as { polluted?: boolean }).polluted).toBeUndefined();

  for (const key of ['__proto__', 'constructor', 'prototype']) {
    Object.defineProperty(fixture.snapshot.connections, key, {
      value: 'HEALTHY', enumerable: true, configurable: true,
    });
    expect(() => projectSnapshot(fixture.snapshot)).toThrow(InvalidPublicDto);
    Reflect.deleteProperty(fixture.snapshot.connections, key);
  }
  expect(({} as { polluted?: boolean }).polluted).toBeUndefined();

  fixture.snapshot.connections['unsafe key'] = 'HEALTHY';
  expect(() => projectSnapshot(fixture.snapshot)).toThrow(InvalidPublicDto);
  delete fixture.snapshot.connections['unsafe key'];
  fixture.snapshot.connections.broker = 'PRIVATE';
  expect(() => projectSnapshot(fixture.snapshot)).toThrow(InvalidPublicDto);
  fixture.snapshot.connections.broker = 'HEALTHY';
  fixture.snapshot.portfolioEquity = 100000;
  expect(() => projectSnapshot(fixture.snapshot)).toThrow(InvalidPublicDto);
});
