import type {
  AgentRun, ApiCost, AuditEvent, Decision, MarketState, Order,
  RiskDecision, TradeProposal,
} from '../api/generated/control-api/model/models';

export type Resource =
  | 'market-states'
  | 'decisions'
  | 'proposals'
  | 'risk-decisions'
  | 'orders'
  | 'audit'
  | 'runs'
  | 'costs';

export type HistoryItem =
  | MarketState | Decision | TradeProposal | RiskDecision
  | Order | AuditEvent | AgentRun | ApiCost;

export interface Page<T extends HistoryItem = HistoryItem> {
  items: readonly T[];
  nextCursor: string | null;
  loading: boolean;
  error: string | null;
}

export const RESOURCES: readonly Resource[] = [
  'market-states', 'decisions', 'proposals', 'risk-decisions',
  'orders', 'audit', 'runs', 'costs',
];
