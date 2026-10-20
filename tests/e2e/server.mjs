// Synthetic, loopback-only browser fixture. It never imports an application service or broker adapter.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const browserRoot = join(root, 'apps/dashboard/dist/dashboard/browser');
const exampleRoot = join(root, 'contracts/examples/v1');
const host = '127.0.0.1';
const port = 4200;
const origin = `http://${host}:${port}`;
const traceId = '4bf92f3577b34da6a3ce929d0e0e4736';
const activationCode = 'synthetic-local-activation-code-000001';
const sessionValue = 'synthetic-e2e-session';
const demoRunId = '8c0c8b85-b804-4f0e-89bc-4387a73231b4';
const at = '2026-10-12T14:45:05Z';

async function example(name) {
  const value = JSON.parse(await readFile(join(exampleRoot, `${name}.valid.json`), 'utf8'));
  delete value.schemaVersion;
  return value;
}

const market = await example('market-state');
const decision = await example('decision-result');
const proposal = await example('trade-proposal');
const riskDecision = await example('risk-decision');
const order = {
  id: 'ord_1', proposalId: proposal.id, riskDecisionId: riskDecision.id,
  instrument: 'SPY', status: 'PARTIALLY_FILLED', requestedQuantity: '1.25',
  filledQuantity: '0.50', averageFillPrice: '550.30', reconciliation: 'CURRENT',
  updatedAt: at, traceId,
};
const audit = [
  { id: 'aud_market', occurredAt: at, actor: 'market-worker', action: 'MARKET_OBSERVED', outcome: 'OBSERVED', reasonCode: 'OK', traceId },
  { id: 'aud_decision', occurredAt: at, actor: 'decision-worker', action: 'DECISION_LONG', outcome: 'OBSERVED', reasonCode: 'OK', traceId },
  { id: 'aud_risk', occurredAt: at, actor: 'risk-engine', action: 'PROPOSAL_APPROVED', outcome: 'ACCEPTED', reasonCode: 'OK', policyVersion: 3, traceId },
  { id: 'aud_order', occurredAt: at, actor: 'execution', action: 'PARTIAL_FILL_RECONCILED', outcome: 'OBSERVED', reasonCode: 'OK', traceId },
];
const run = { id: 'run_1', role: 'WORKER', status: 'RUNNING', startedAt: at, heartbeatAt: at, traceId };
const cost = { id: 'cost_1', provider: 'JEV', observedAt: at, currency: 'USD', amount: null, traceId };
const pages = {
  'market-states': [[market], [{ ...market, id: 'mkt_2', instrument: 'QQQ' }]],
  decisions: [[decision], []], proposals: [[proposal], []],
  'risk-decisions': [[riskDecision], []], orders: [[order], []],
  audit: [audit, []], runs: [[run], []], costs: [[cost], []],
};
const policy = {
  maxPositionsPerInstrument: 1, maxOpenPositions: 5,
  maxPositionEquityFraction: '0.10', maxDailyLossFraction: '0.02',
  version: 3, simulationOnly: true, effectiveAt: at, changedAt: at,
  changedBy: 'local-operator', auditEventId: 'aud_policy',
};

const clients = new Set();
let state;
function reset() {
  for (const client of clients) client.end();
  clients.clear();
  state = {
    activated: false, sessionValid: false, mode: {
      mode: 'PAPER', pauseNew: false, killSwitch: false, health: 'HEALTHY',
      reconciliation: 'CURRENT', changedAt: at, traceId,
    },
    snapshotCursor: 'c_000100', snapshotReads: 0, connections: 0,
    events: [], requests: [], writes: [], usedKeys: new Map(),
    demoRuns: [], demoKey: null, demoStatus: 'RUNNING',
    integrationStatus: { alpaca: { status: 'UNCONFIGURED', restartRequired: false }, jev: { status: 'UNCONFIGURED', restartRequired: false } },
    tradingProfile: { budget: '100', currency: 'USD', strategy: 'mock-neutral' },
    refuseResume: false, expireNext: false, streams: 0,
  };
}
reset();

function sendJson(res, status, body, extra = {}) {
  const serialized = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json',
    'Cache-Control': 'no-store', ...extra,
  });
  res.end(serialized);
}
function problem(res, status, code, title) {
  sendJson(res, status, {
    type: `https://agentic-trading-lab.invalid/problems/${code.toLowerCase().replaceAll('_', '-')}`,
    title, status, code, traceId,
  });
}
function snapshot() {
  return {
    asOf: at, cursor: state.snapshotCursor, mode: { ...state.mode },
    paperAccount: 'Synthetic paper account',
    connections: { broker: 'HEALTHY', data: 'HEALTHY', jev: 'HEALTHY', execution: 'HEALTHY' },
    portfolioEquity: '100000.00', workers: [run], recentOrders: [order],
  };
}
function hasSession(req) {
  return state.sessionValid && req.headers.cookie?.split(';').some(part => part.trim() === `atl_session=${sessionValue}`);
}
function record(req, url) {
  // Keep only protocol metadata. Never record bodies, Cookie, activation code, or arbitrary headers.
  state.requests.push({
    method: req.method, path: url.pathname, cursor: url.searchParams.get('cursor'),
    traceId: url.searchParams.get('traceId'), lastEventId: req.headers['last-event-id'] ?? null,
    idempotencyKey: req.headers['idempotency-key'] ?? null,
  });
}
async function body(req) {
  let value = '';
  for await (const chunk of req) {
    value += chunk;
    if (value.length > 16_384) throw new Error('request too large');
  }
  return JSON.parse(value || '{}');
}
function frame(event) {
  return `id: ${event.cursor}\nevent: ${event.eventType}\ndata: ${JSON.stringify(event)}\n\n`;
}
function publish(event) {
  state.events.push(event);
  state.snapshotCursor = event.cursor;
  for (const client of clients) client.write(frame(event));
}
function makeEvent(input) {
  const number = 101 + state.events.length;
  return {
    eventId: `evt_${number}`, eventType: 'order.updated.v1', occurredAt: at,
    aggregateId: order.id, aggregateVersion: number - 100,
    traceId, cursor: `c_${String(number).padStart(6, '0')}`,
    payload: { orderId: order.id, status: 'PARTIALLY_FILLED' },
    ...input,
  };
}
async function testControl(req, res, url) {
  if (url.pathname === '/__test/ready') return sendJson(res, 200, { ready: true });
  if (url.pathname === '/__test/fixtures') return sendJson(res, 200, {
    snapshot: snapshot(), riskPolicy: policy,
    pages: Object.fromEntries(Object.entries(pages).map(([name, values]) => [name, {
      items: values[0], nextCursor: name === 'market-states' ? 'c_page2' : null,
    }])),
  });
  if (url.pathname === '/__test/reset' && req.method === 'POST') {
    reset();
    return sendJson(res, 200, { reset: true });
  }
  if (url.pathname === '/__test/state') {
    return sendJson(res, 200, {
      mode: state.mode, snapshotReads: state.snapshotReads, connections: state.connections,
      streams: state.streams, requests: state.requests, writes: state.writes,
    });
  }
  if (url.pathname === '/__test/emit' && req.method === 'POST') {
    const input = await body(req);
    const event = makeEvent(input);
    publish(event);
    return sendJson(res, 200, event);
  }
  if (url.pathname === '/__test/heartbeat' && req.method === 'POST') {
    for (const client of clients) client.write(': heartbeat\n\n');
    return sendJson(res, 200, { heartbeat: true });
  }
  if (url.pathname === '/__test/disconnect' && req.method === 'POST') {
    for (const client of clients) client.end();
    clients.clear();
    return sendJson(res, 200, { disconnected: true });
  }
  if (url.pathname === '/__test/expire-next' && req.method === 'POST') {
    state.expireNext = true;
    return sendJson(res, 200, { expireNext: true });
  }
  if (url.pathname === '/__test/reset-required' && req.method === 'POST') {
    const payload = JSON.stringify({
      type: 'https://agentic-trading-lab.invalid/problems/cursor-expired',
      title: 'Cursor expired', status: 410, code: 'CURSOR_EXPIRED', traceId,
    });
    for (const client of clients) {
      client.write(`event: reset-required\ndata: ${payload}\n\n`);
      client.end();
    }
    clients.clear();
    return sendJson(res, 200, { resetRequired: true });
  }
  if (url.pathname === '/__test/refuse-resume' && req.method === 'POST') {
    state.refuseResume = true;
    return sendJson(res, 200, { refuseResume: true });
  }
  if (url.pathname === '/__test/revoke' && req.method === 'POST') {
    state.sessionValid = false;
    for (const client of clients) client.end();
    clients.clear();
    return sendJson(res, 200, { revoked: true });
  }
  problem(res, 404, 'INVALID_REQUEST', 'Unknown test control');
}
async function api(req, res, url) {
  record(req, url);
  if (req.headers.host !== `${host}:${port}`) return problem(res, 400, 'INVALID_REQUEST', 'Invalid host');
  if (url.pathname === '/api/v1/session' && req.method === 'POST') {
    if (req.headers.origin !== origin || !req.headers['idempotency-key'] ||
        !req.headers['content-type']?.startsWith('application/json')) {
      return problem(res, 400, 'INVALID_REQUEST', 'Invalid activation request');
    }
    let request;
    try { request = await body(req); } catch { return problem(res, 400, 'INVALID_REQUEST', 'Invalid request'); }
    if (request.activationCode !== activationCode) return problem(res, 400, 'INVALID_REQUEST', 'Invalid activation code');
    if (state.activated) return problem(res, 409, 'INVALID_REQUEST', 'Activation refused');
    state.activated = true;
    state.sessionValid = true;
    res.writeHead(204, {
      'Set-Cookie': `atl_session=${sessionValue}; HttpOnly; SameSite=Strict; Path=/`,
      'Cache-Control': 'no-store',
    });
    return res.end();
  }
  if (!hasSession(req)) return problem(res, 401, 'INVALID_REQUEST', 'Operator session required');
  if (url.pathname === '/api/v1/demo/runs' && req.method === 'POST') {
    if (req.headers.origin !== origin || !req.headers['idempotency-key'] || !req.headers['content-type']?.startsWith('application/json')) return problem(res, 400, 'INVALID_REQUEST', 'Invalid demo request');
    let input; try { input = await body(req); } catch { return problem(res, 400, 'INVALID_REQUEST', 'Invalid request'); }
    if (state.demoKey && state.demoKey === req.headers['idempotency-key']) return sendJson(res, 202, state.demoRuns[0]);
    const created = { runId: demoRunId, status: 'RUNNING', simulationOnly: true, traceId, initialBudget: input.initialBudget, currency: 'USD', strategy: input.strategy, createdAt: at, updatedAt: at };
    state.demoKey = req.headers['idempotency-key']; state.demoStatus = 'SUCCEEDED'; state.demoRuns = [created];
    return sendJson(res, 202, created);
  }
  if (req.method === 'GET' && url.pathname === '/api/v1/demo/runs') return sendJson(res, 200, { items: state.demoRuns.map(item => ({ ...item, status: state.demoStatus })), nextCursor: null });
  const demoRunMatch = url.pathname.match(/^\/api\/v1\/demo\/runs\/([^/]+)(?:\/(.*))?$/);
  if (demoRunMatch) {
    if (!state.demoRuns.length || demoRunMatch[1] !== demoRunId) return problem(res, 404, 'INVALID_REQUEST', 'Demo run not found');
    const suffix = demoRunMatch[2] ?? '';
    if (req.method === 'GET' && !suffix) return sendJson(res, 200, { ...state.demoRuns[0], status: state.demoStatus });
    if (req.method === 'GET' && suffix === 'snapshot') return sendJson(res, 200, { ...snapshot(), portfolioCash: '99.50', positions: [{ instrument: 'SPY', quantity: '0.50', averagePrice: '1.00' }] });
    const history = suffix.match(/^history\/(market-states|decisions|proposals|risk-decisions|orders|audit|runs|costs)$/);
    if (req.method === 'GET' && history) return sendJson(res, 200, { items: pages[history[1]][0], nextCursor: null });
    if (req.method === 'POST' && /^controls\/(pause|kill)$/.test(suffix)) return sendJson(res, 200, { runId: demoRunId, action: suffix.split('/').at(-1), status: 'PAUSED', simulationOnly: true, traceId });
  }
  if (req.method === 'GET' && url.pathname === '/api/v1/integrations') return sendJson(res, 200, state.integrationStatus);
  const integrationMatch = url.pathname.match(/^\/api\/v1\/integrations\/(alpaca|jev)$/);
  if (integrationMatch && req.method === 'PUT') {
    if (req.headers.origin !== origin || !req.headers['idempotency-key']) return problem(res, 400, 'INVALID_REQUEST', 'Invalid setup request');
    try { await body(req); } catch { return problem(res, 400, 'INVALID_REQUEST', 'Invalid request'); }
    const provider = integrationMatch[1]; state.integrationStatus[provider] = { status: 'CONFIGURED', restartRequired: true };
    return sendJson(res, 200, { status: 'CONFIGURED', restartRequired: true });
  }
  if (url.pathname === '/api/v1/trading-profile' && req.method === 'GET') return sendJson(res, 200, state.tradingProfile);
  if (url.pathname === '/api/v1/trading-profile' && req.method === 'PUT') {
    if (req.headers.origin !== origin || !req.headers['idempotency-key']) return problem(res, 400, 'INVALID_REQUEST', 'Invalid profile request');
    try { state.tradingProfile = await body(req); } catch { return problem(res, 400, 'INVALID_REQUEST', 'Invalid request'); }
    return sendJson(res, 200, state.tradingProfile);
  }
  if (req.method === 'GET' && url.pathname === '/api/v1/snapshot') {
    state.snapshotReads++;
    return sendJson(res, 200, snapshot());
  }
  if (req.method === 'GET' && url.pathname === '/api/v1/mode') return sendJson(res, 200, state.mode);
  if (req.method === 'GET' && url.pathname === '/api/v1/risk-policy') {
    return sendJson(res, 200, policy, { ETag: '"risk-policy-v3"' });
  }
  if (req.method === 'GET' && url.pathname === '/api/v1/events') {
    state.connections++;
    if (state.expireNext) {
      state.expireNext = false;
      return problem(res, 410, 'CURSOR_EXPIRED', 'Cursor expired');
    }
    const cursor = req.headers['last-event-id'] || url.searchParams.get('cursor');
    if (!cursor || !/^c_\d{6}$/.test(cursor)) return problem(res, 410, 'CURSOR_EXPIRED', 'Cursor expired');
    res.writeHead(200, {
      'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', Connection: 'keep-alive',
    });
    state.streams++;
    res.write(': heartbeat\n\n');
    for (const event of state.events) {
      if (event.cursor > cursor) res.write(frame(event));
    }
    clients.add(res);
    res.on('close', () => clients.delete(res));
    return;
  }
  if (req.method === 'GET' && url.pathname.startsWith('/api/v1/')) {
    const resource = url.pathname.slice('/api/v1/'.length);
    if (Object.hasOwn(pages, resource)) {
      let items = pages[resource][url.searchParams.get('cursor') === 'c_page2' ? 1 : 0];
      if (resource === 'audit' && url.searchParams.has('traceId')) {
        items = items.filter(item => item.traceId === url.searchParams.get('traceId'));
      }
      return sendJson(res, 200, {
        items, nextCursor: resource === 'market-states' && !url.searchParams.has('cursor') ? 'c_page2' : null,
      });
    }
  }
  if (req.method === 'POST' && /^\/api\/v1\/control\/(pause|resume|kill)$/.test(url.pathname)) {
    if (req.headers.origin !== origin || !req.headers['content-type']?.startsWith('application/json') ||
        !req.headers['idempotency-key']) return problem(res, 400, 'INVALID_REQUEST', 'Invalid control request');
    let input;
    try { input = await body(req); } catch { return problem(res, 400, 'INVALID_REQUEST', 'Invalid request'); }
    if (typeof input.reason !== 'string' || !input.reason.trim()) {
      return problem(res, 400, 'INVALID_REQUEST', 'Invalid reason');
    }
    const kind = url.pathname.split('/').at(-1);
    const key = `${kind}:${req.headers['idempotency-key']}`;
    const prior = state.usedKeys.get(key);
    if (prior) {
      if (prior.reason !== input.reason) return problem(res, 409, 'IDEMPOTENCY_CONFLICT', 'Key conflict');
      return sendJson(res, prior.status, prior.result);
    }
    if (kind === 'resume' && (state.refuseResume || state.mode.health !== 'HEALTHY' ||
        state.mode.reconciliation !== 'CURRENT' || state.mode.killSwitch)) {
      const result = {
        type: 'https://agentic-trading-lab.invalid/problems/control-refused',
        title: 'Control action refused', status: 409,
        code: state.mode.killSwitch ? 'KILL_SWITCH_ACTIVE' : 'HEALTH_CHECK_FAILED', traceId,
      };
      state.usedKeys.set(key, { reason: input.reason, status: 409, result });
      state.writes.push({ kind, key: req.headers['idempotency-key'], status: 409 });
      return sendJson(res, 409, result);
    }
    state.mode = {
      ...state.mode, pauseNew: kind === 'resume' ? false : true,
      killSwitch: kind === 'kill' ? true : state.mode.killSwitch, changedAt: at,
    };
    const result = { ...state.mode };
    state.usedKeys.set(key, { reason: input.reason, status: 200, result });
    state.writes.push({ kind, key: req.headers['idempotency-key'], status: 200 });
    return sendJson(res, 200, result);
  }
  problem(res, 404, 'INVALID_REQUEST', 'Unknown API route');
}
const mime = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.ico': 'image/x-icon', '.woff2': 'font/woff2',
};
async function staticFile(res, url) {
  const pathname = decodeURIComponent(url.pathname);
  const target = resolve(browserRoot, `.${pathname === '/' ? '/index.html' : pathname}`);
  if (target !== browserRoot && !target.startsWith(browserRoot + sep)) {
    return problem(res, 404, 'INVALID_REQUEST', 'Not found');
  }
  try {
    const data = await readFile(target);
    res.writeHead(200, { 'Content-Type': mime[extname(target)] ?? 'application/octet-stream', 'Cache-Control': 'no-store' });
    return res.end(data);
  } catch {
    return problem(res, 404, 'INVALID_REQUEST', 'Built dashboard missing');
  }
}

createServer(async (req, res) => {
  try {
    if (req.socket.remoteAddress !== host && req.socket.remoteAddress !== '::ffff:127.0.0.1') {
      return problem(res, 403, 'INVALID_REQUEST', 'Loopback only');
    }
    const url = new URL(req.url, origin);
    if (url.pathname.startsWith('/__test/')) return await testControl(req, res, url);
    if (url.pathname.startsWith('/api/v1/')) return await api(req, res, url);
    return await staticFile(res, url);
  } catch {
    problem(res, 500, 'TEMPORARILY_UNAVAILABLE', 'Synthetic fixture failed');
  }
}).listen(port, host);

const heartbeat = setInterval(() => {
  for (const client of clients) client.write(': heartbeat\n\n');
}, 10_000);
heartbeat.unref();
