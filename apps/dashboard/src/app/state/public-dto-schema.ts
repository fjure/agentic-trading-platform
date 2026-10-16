import type {
  ModeState, RiskPolicy, SupervisorSnapshot,
} from '../api/generated/control-api/model/models';
import { ReasonCode } from '../api/generated/control-api/model/reasonCode';
import type { HistoryItem, Resource } from './supervisor.models';

/** The generated client types do not validate JSON at runtime. Only projected fields enter shared state. */
type Shape =
  | { kind: 'text'; max: number; pattern?: RegExp }
  | { kind: 'integer'; min: number }
  | { kind: 'boolean' }
  | { kind: 'enum'; values: readonly (string | boolean)[] }
  | { kind: 'object'; fields: Record<string, Shape> }
  | { kind: 'array'; item: Shape; max: number }
  | { kind: 'map'; item: Shape; max: number; key: RegExp }
  | { kind: 'nullable'; item: Shape }
  | { kind: 'optional'; item: Shape };

const text = (max = 1024, pattern?: RegExp): Shape => ({ kind: 'text', max, pattern });
const integer = (min = 0): Shape => ({ kind: 'integer', min });
const boolean: Shape = { kind: 'boolean' };
const enumeration = (...values: (string | boolean)[]): Shape => ({ kind: 'enum', values });
const object = (fields: Record<string, Shape>): Shape => ({ kind: 'object', fields });
const array = (item: Shape, max = 200): Shape => ({ kind: 'array', item, max });
const map = (item: Shape, max = 128, key = /^[A-Za-z0-9_.-]{1,64}$/): Shape => ({ kind: 'map', item, max, key });
const nullable = (item: Shape): Shape => ({ kind: 'nullable', item });
const optional = (item: Shape): Shape => ({ kind: 'optional', item });

const decimal = text(64, /^-?(0|[1-9][0-9]{0,19})(\.[0-9]{1,18})?$/);
const fraction = text(32, /^(0(\.[0-9]{1,18})?|1(\.0{1,18})?)$/);
const timestamp = text(64, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/);
const traceId = text(32, /^[0-9a-f]{32}$/);
const cursor = text(256);
const reasonCode = { kind: 'enum', values: Object.values(ReasonCode) } as const satisfies Shape;
const quality = enumeration('VALID', 'STALE', 'GAP', 'CONFLICT', 'INVALID');

const modeState = object({
  mode: enumeration('DISABLED', 'REPLAY', 'SHADOW', 'PAPER'),
  pauseNew: boolean,
  killSwitch: boolean,
  health: enumeration('HEALTHY', 'DEGRADED', 'UNAVAILABLE'),
  reconciliation: enumeration('CURRENT', 'PENDING', 'MISMATCH'),
  changedAt: timestamp,
  traceId,
});

const riskPolicy = object({
  maxPositionsPerInstrument: integer(1),
  maxOpenPositions: integer(1),
  maxPositionEquityFraction: fraction,
  maxDailyLossFraction: fraction,
  version: integer(1),
  simulationOnly: enumeration(true),
  effectiveAt: timestamp,
  changedAt: timestamp,
  changedBy: text(256),
  auditEventId: text(256),
});

const agentRun = object({
  id: text(256), role: enumeration('MARKET_INTELLIGENCE', 'QUANT_RESEARCH', 'PORTFOLIO_PLANNER', 'WORKER'),
  status: enumeration('RUNNING', 'SUCCEEDED', 'FAILED', 'DEAD_LETTER'),
  startedAt: timestamp, heartbeatAt: timestamp, traceId,
});

const order = object({
  id: text(256), proposalId: text(256), riskDecisionId: text(256), instrument: text(256),
  status: enumeration('CREATED', 'RISK_RESERVED', 'SUBMITTING', 'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED', 'REJECTED', 'CANCEL_PENDING', 'CANCELED', 'UNKNOWN'),
  requestedQuantity: decimal, filledQuantity: decimal, averageFillPrice: optional(decimal),
  reconciliation: enumeration('CURRENT', 'PENDING', 'MISMATCH'), updatedAt: timestamp, traceId,
});

const snapshot = object({
  asOf: timestamp, cursor, mode: modeState, paperAccount: text(256),
  connections: map(enumeration('HEALTHY', 'DEGRADED', 'UNAVAILABLE'), 32),
  portfolioEquity: decimal, workers: array(agentRun), recentOrders: array(order),
});

const marketState = object({
  id: text(256), instrument: text(256), market: enumeration('US_EQUITY', 'CRYPTO'),
  interval: enumeration('PT15M'),
  bar: object({
    startAt: timestamp, endAt: timestamp, open: decimal, high: decimal,
    low: decimal, close: decimal, volume: decimal, closed: enumeration(true),
  }),
  eventTime: timestamp, receivedAt: timestamp, availableAt: timestamp, asOf: timestamp,
  features: map(decimal),
  evidence: object({
    neutral: enumeration(true), sources: array(text(256), 100), availableAt: timestamp,
    conflicts: array(text(256), 100), quality,
  }),
  spread: decimal, quality,
  portfolio: object({ equity: decimal, cash: decimal }),
  position: object({ quantity: decimal, averagePrice: optional(decimal) }),
  traceId,
});

const typedAnswers = object({
  regime: object({
    kind: enumeration('CHOICE'), questionVersion: text(256),
    choice: enumeration('TREND', 'RANGE', 'UNCERTAIN'), confidence: fraction,
    distribution: object({ TREND: fraction, RANGE: fraction, UNCERTAIN: fraction }),
  }),
  evidenceQuality: object({
    kind: enumeration('SCORE'), questionVersion: text(256),
    score: text(32, /^([0-8](\.[0-9]{1,18})?|9(\.0{1,18})?)$/),
    legend: map(text(256), 10, /^[0-9]$/),
    distribution: map(fraction, 10, /^[0-9]$/), confidence: fraction,
  }),
  signalConflict: object({ kind: enumeration('NOUL'), questionVersion: text(256), noul: fraction }),
  shadowDirection: optional(object({
    kind: enumeration('CHOICE'), questionVersion: text(256),
    choice: enumeration('LONG', 'FLAT', 'ABSTAIN'), confidence: fraction,
    distribution: object({ LONG: fraction, FLAT: fraction, ABSTAIN: fraction }),
  })),
});

const decision = object({
  id: text(256), marketStateId: text(256), choice: enumeration('LONG', 'FLAT', 'ABSTAIN'),
  typedAnswers: nullable(typedAnswers), confidence: nullable(fraction),
  distribution: nullable(map(fraction, 16)),
  regime: nullable(enumeration('TREND', 'RANGE', 'UNCERTAIN')),
  evidenceQuality: nullable(text(256)), signalConflict: nullable(text(256)),
  shadowChoice: optional(enumeration('LONG', 'FLAT', 'ABSTAIN')),
  modelId: nullable(text(256)), promptVersion: text(256), questionVersion: text(256),
  configVersion: text(256), inputHash: text(256), latencyMs: integer(),
  apiUnits: nullable(decimal), inputTokens: optional(nullable(integer())),
  outputTokens: optional(nullable(integer())), estimatedCost: nullable(decimal),
  validUntil: timestamp, traceId, reasonCode,
});

const proposal = object({
  id: text(256), decisionId: text(256), marketStateId: text(256), instrument: text(256),
  target: enumeration('LONG', 'FLAT'), quantity: decimal,
  rationaleCodes: optional(array(reasonCode, 50)), createdAt: timestamp, validUntil: timestamp, traceId,
});

const riskDecision = object({
  id: text(256), proposalId: text(256), approved: boolean, reasonCode,
  reasonCodes: array(reasonCode, 50), policyVersion: integer(1),
  exposureSnapshot: object({ equity: decimal, grossNotional: decimal, reservedNotional: decimal }),
  reservationId: optional(text(256)), decidedAt: timestamp, validUntil: timestamp, traceId,
});

const audit = object({
  id: text(256), occurredAt: timestamp, actor: text(256), action: text(256),
  outcome: enumeration('ACCEPTED', 'REFUSED', 'OBSERVED'), reasonCode,
  policyVersion: optional(integer(1)), traceId,
});

const cost = object({
  id: text(256), provider: text(256), observedAt: timestamp,
  currency: text(3, /^[A-Z]{3}$/), amount: nullable(decimal), traceId,
});

const itemShapes: Record<Resource, Shape> = {
  'market-states': marketState, decisions: decision, proposals: proposal,
  'risk-decisions': riskDecision, orders: order, audit, runs: agentRun, costs: cost,
};

export class InvalidPublicDto extends Error {
  constructor() { super('Invalid public DTO'); }
}

function project(shape: Shape, input: unknown): unknown {
  if (shape.kind === 'optional') return project(shape.item, input);
  if (shape.kind === 'nullable') return input === null ? null : project(shape.item, input);
  if (shape.kind === 'text') {
    if (typeof input !== 'string' || input.length > shape.max ||
        (shape.pattern && !shape.pattern.test(input))) throw new InvalidPublicDto();
    return input;
  }
  if (shape.kind === 'integer') {
    if (typeof input !== 'number' || !Number.isSafeInteger(input) || input < shape.min) throw new InvalidPublicDto();
    return input;
  }
  if (shape.kind === 'boolean') {
    if (typeof input !== 'boolean') throw new InvalidPublicDto();
    return input;
  }
  if (shape.kind === 'enum') {
    if (!shape.values.includes(input as string | boolean)) throw new InvalidPublicDto();
    return input;
  }
  if (shape.kind === 'array') {
    if (!Array.isArray(input) || input.length > shape.max) throw new InvalidPublicDto();
    return input.map(value => project(shape.item, value));
  }
  if (typeof input !== 'object' || input === null || Array.isArray(input)) throw new InvalidPublicDto();
  const source = input as Record<string, unknown>;
  const output: Record<string, unknown> = Object.create(null);
  if (shape.kind === 'map') {
    const keys = Object.keys(source);
    if (keys.length > shape.max) throw new InvalidPublicDto();
    for (const key of keys) {
      if (!shape.key.test(key) || key === '__proto__' || key === 'constructor' || key === 'prototype') {
        throw new InvalidPublicDto();
      }
      output[key] = project(shape.item, source[key]);
    }
    return output;
  }
  for (const [key, field] of Object.entries(shape.fields)) {
    if (!Object.prototype.hasOwnProperty.call(source, key)) {
      if (field.kind === 'optional') continue;
      throw new InvalidPublicDto();
    }
    output[key] = project(field, source[key]);
  }
  return output;
}

export function projectSnapshot(input: unknown): SupervisorSnapshot {
  return project(snapshot, input) as SupervisorSnapshot;
}

export function projectPolicy(input: unknown): RiskPolicy {
  return project(riskPolicy, input) as RiskPolicy;
}

export function projectMode(input: unknown): ModeState {
  return project(modeState, input) as ModeState;
}

export function projectPage(resource: Resource, input: unknown): { items: HistoryItem[]; nextCursor: string | null } {
  return project(object({ items: array(itemShapes[resource]), nextCursor: nullable(cursor) }), input) as {
    items: HistoryItem[]; nextCursor: string | null;
  };
}
