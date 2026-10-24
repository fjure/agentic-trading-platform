import type { PaperSnapshot, PaperOrderPage, PaperFillPage, PaperOperationPage, PaperOperation, PaperTrace } from '../api/generated/control-api/model/models';
import { PaperErrorCode } from '../api/generated/control-api/model/paperErrorCode';
import { InvalidPublicDto } from '../state/public-dto-schema';

// The generated client is typed but does not validate JSON. Only this closed
// public projection enters component state; provider payloads are never rendered.
type Reader = (value: unknown) => unknown;
const text = (max: number, pattern?: RegExp): Reader => value => {
  if (typeof value !== 'string' || value.length > max || (pattern && !pattern.test(value))) throw new InvalidPublicDto();
  return value;
};
const enumeration = (...values: readonly unknown[]): Reader => value => {
  if (!values.includes(value)) throw new InvalidPublicDto();
  return value;
};
const integer: Reader = value => {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new InvalidPublicDto();
  return value;
};
const version: Reader = value => { const result = integer(value); if ((result as number) < 1) throw new InvalidPublicDto(); return result; };
const attempts: Reader = value => { const result = integer(value); if ((result as number) > 3) throw new InvalidPublicDto(); return result; };
const boolean = enumeration(true, false);
const nullable = (read: Reader): Reader => value => value === null ? null : read(value);
const optional = (read: Reader): Reader => value => value === undefined ? undefined : read(value);
const array = (read: Reader, max: number): Reader => value => {
  if (!Array.isArray(value) || value.length > max) throw new InvalidPublicDto();
  return value.map(read);
};
const object = (fields: Record<string, Reader>): Reader => value => {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !Object.hasOwn(fields, key))) throw new InvalidPublicDto();
  const result: Record<string, unknown> = {};
  for (const [key, read] of Object.entries(fields)) {
    const projected = read((value as Record<string, unknown>)[key]);
    if (projected !== undefined) result[key] = projected;
  }
  return result;
};
const namespace = enumeration('alpaca-paper');
const decimal = text(40, /^-?(0|[1-9][0-9]{0,19})(\.[0-9]{1,18})?$/);
const positive = text(39, /^(?=.*[1-9])(0|[1-9][0-9]{0,19})(\.[0-9]{1,18})?$/);
const nonnegative = text(39, /^(0|[1-9][0-9]{0,19})(\.[0-9]{1,18})?$/);
const utc: Reader = value => {
  const result = text(30, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$/)(value);
  const timestamp = Date.parse(result as string);
  if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 19) !== (result as string).slice(0, 19)) throw new InvalidPublicDto();
  return result;
};
const id = text(255, /^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$/);
const uuid = text(36, /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
const traceId = text(32, /^[0-9a-f]{32}$/);
const currency = enumeration('USD');
const symbol = enumeration('SPY', 'QQQ');
const strategy = enumeration('mock-neutral', 'neutral-baseline', 'jev');
const errorCode = enumeration(...Object.values(PaperErrorCode));
const errorCodes: Reader = value => { const result = array(errorCode, 37)(value) as string[]; if (new Set(result).size !== result.length) throw new InvalidPublicDto(); return result; };
const fee = object({ scope: enumeration('alpaca-paper-direct-retail-us-equity-zero-v1'), confirmed: enumeration(true) });
const status = object({ namespace, environment: enumeration('PAPER'), setupState: enumeration('NOT_CONFIGURED', 'SAVED', 'VERIFIED', 'PREPARED'), operatingState: enumeration('DISABLED', 'PAUSED', 'RUNNING', 'STOPPED', 'HALTED'), entryGateOpen: boolean, credentialGeneration: integer, accountGeneration: integer, accountLabel: nullable(text(64, /^paper-[0-9a-f]{8}$/)), currency, cash: optional(decimal), riskBudget: optional(positive), verifiedAt: nullable(utc), reconciledAt: nullable(utc), observedAt: utc, marketDataFeed: enumeration('UNVERIFIED', 'IEX', 'SIP'), providerVerification: enumeration('UNVERIFIED', 'CONFIRMED'), blockers: errorCodes, traceId });
const profile = object({ namespace, configured: boolean, currency, riskBudget: nullable(positive), strategy: nullable(strategy), strategyVersion: nullable(enumeration('mock-neutral.v1', 'neutral-baseline.v1', 'jev.v1')), credentialGeneration: integer, accountGeneration: integer, policyVersion: integer, profileVersion: integer, budgetVersion: integer, limitPolicyVersion: version, feeAttestation: nullable(fee), traceId });
const quote = object({ quoteId: id, sourceId: enumeration('alpaca-market-data:iex'), bid: positive, ask: positive, observedAt: utc, providerTime: utc, validUntil: utc, sourceCapabilityVersion: version });
const capability = object({ version: version, observedAt: utc, validUntil: utc, limitOrderConfirmed: boolean, fractionalConfirmed: boolean, quantityStep: positive, priceTick: positive });
const session = object({ version: version, open: boolean, observedAt: utc, validUntil: utc, startsAt: utc, endsAt: utc });
const market = object({ symbol, marketStateId: nullable(id), quote: nullable(quote), capability: nullable(capability), session: nullable(session) });
const portfolio = object({ currency, equity: nonnegative, cash: nonnegative, exposure: nonnegative, ledgerVersion: version, asOf: utc, quality: enumeration('VALID', 'STALE', 'GAP', 'CONFLICT', 'INVALID'), reconciliation: enumeration('CURRENT', 'PENDING', 'MISMATCH') });
const position = object({ symbol, quantity: nonnegative, averagePrice: nullable(positive), costBasis: nullable(nonnegative), realizedPnl: decimal, ledgerVersion: version });
const snapshot = object({ namespace, observedAt: utc, verificationOperationId: nullable(uuid), status, profile, portfolio: nullable(portfolio), positions: nullable(array(position, 2)), markets: array(market, 2) });
const cancel = object({ state: enumeration('READY', 'BACKOFF', 'LOOKUP_ONLY', 'FINISHED'), requestedAt: utc, nextAttemptAt: nullable(utc), deleteAttempts: attempts });
const order = object({ orderId: uuid, proposalId: id, proposalSource: enumeration('MODEL_DECISION', 'HUMAN_REQUEST'), decisionId: nullable(id), humanRequestId: nullable(uuid), traceId, symbol, side: enumeration('BUY', 'SELL'), orderType: enumeration('MARKET', 'LIMIT'), timeInForce: nullable(enumeration('DAY')), session: nullable(enumeration('REGULAR')), quantity: positive, limitPrice: nullable(positive), status: enumeration('CREATED', 'RISK_RESERVED', 'SUBMITTING', 'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED', 'REJECTED', 'CANCEL_PENDING', 'CANCELED', 'UNKNOWN'), filledQuantity: nonnegative, averageFillPrice: nullable(positive), updatedAt: utc, cancel: nullable(cancel), clientOrderId: id });
const fill = object({ fillId: id, orderId: uuid, symbol, side: enumeration('BUY', 'SELL'), quantity: positive, price: positive, occurredAt: utc, fee: nullable(nonnegative), currency, traceId });
const operation = object({ operationId: uuid, operationType: enumeration('VERIFY_CONNECTION', 'PREPARE_LEDGER', 'START', 'PAUSE', 'STOP', 'KILL', 'PLACE_LIMIT_ORDER', 'CANCEL_ORDER'), status: enumeration('ACCEPTED', 'RUNNING', 'SUCCEEDED', 'BLOCKED', 'FAILED'), idempotencyKey: text(128, /^[A-Za-z0-9._:-]{16,128}$/), acceptedGeneration: version, acceptedAt: utc, updatedAt: utc, traceId, errorCode: optional(errorCode), orderId: optional(uuid) });
const page = (item: Reader): Reader => object({ namespace, items: array(item, 50), nextCursor: nullable(text(1024, /^[A-Za-z0-9_-]+$/)) });
const event = object({ eventId: id, occurredAt: utc, role: enumeration('CONTROL_API', 'WORKER', 'EXECUTION', 'WATCHDOG', 'SYSTEM'), action: enumeration('OPERATION_ACCEPTED', 'OPERATION_UPDATED', 'PROPOSAL_CREATED', 'RISK_APPROVED', 'RISK_REJECTED', 'ORDER_UPDATED', 'EXECUTION_REPORTED', 'RECONCILIATION_OBSERVED', 'CANCEL_LOOKUP', 'CANCEL_DELETE', 'CANCEL_OBSERVED'), entityId: id, entityVersion: version, reasonCodes: errorCodes });

export const paperSnapshot = (value: unknown): PaperSnapshot => {
  const result = snapshot(value) as PaperSnapshot;
  const s = result.status;
  const p = result.profile;
  const bindings = [p.credentialGeneration, p.accountGeneration, p.profileVersion, p.budgetVersion];
  const present = [p.riskBudget, p.strategy, p.strategyVersion, p.feeAttestation];
  if ((result.portfolio === null) !== (result.positions === null) ||
      (p.configured ? present.some(x => x === null) || bindings.some(x => x < 1) || p.policyVersion < 1 || p.strategyVersion !== p.strategy + '.v1' : present.some(x => x !== null) || bindings.some(x => x !== 0)) ||
      (s.entryGateOpen && (s.setupState !== 'PREPARED' || s.operatingState !== 'RUNNING' || s.providerVerification !== 'CONFIRMED' || s.blockers.length > 0))) throw new InvalidPublicDto();
  for (const budget of [s.riskBudget, p.riskBudget]) {
    if (budget !== null && budget !== undefined && BigInt(budget.split('.')[0]) >= 1000000n && (BigInt(budget.split('.')[0]) > 1000000n || /[1-9]/.test(budget.split('.')[1] ?? ''))) throw new InvalidPublicDto();
  }
  return result;
};
export const paperOrders = (value: unknown): PaperOrderPage => {
  const result = page(order)(value) as PaperOrderPage;
  for (const item of result.items) {
    if ((item.proposalSource === 'HUMAN_REQUEST') !== (item.decisionId === null && item.humanRequestId !== null) || (item.proposalSource === 'MODEL_DECISION') !== (item.decisionId !== null && item.humanRequestId === null) || (item.orderType === 'LIMIT') !== (item.limitPrice !== null)) throw new InvalidPublicDto();
  }
  return result;
};
export const paperFills = (value: unknown): PaperFillPage => page(fill)(value) as PaperFillPage;
export const paperOperation = (value: unknown): PaperOperation => {
  const result = operation(value) as PaperOperation;
  if (['BLOCKED', 'FAILED'].includes(result.status) !== Object.hasOwn(result, 'errorCode')) throw new InvalidPublicDto();
  return result;
};
export const paperOperations = (value: unknown): PaperOperationPage => {
  const result = page(operation)(value) as PaperOperationPage;
  result.items = result.items.map(paperOperation);
  return result;
};
export const paperTrace = (value: unknown): PaperTrace => object({ namespace, traceId, complete: boolean, items: array(event, 32) })(value) as PaperTrace;
