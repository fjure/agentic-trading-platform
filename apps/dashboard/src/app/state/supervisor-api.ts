import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import {
  ControlService,
  HistoryService,
  RiskPolicyService,
  SnapshotService,
} from '../api/generated/control-api/api/api';
import type {
  ModeState,
  RiskPolicy,
  SupervisorSnapshot,
} from '../api/generated/control-api/model/models';
import type { HistoryItem, Resource } from './supervisor.models';
import { InvalidPublicDto, projectMode, projectPage, projectPolicy, projectSnapshot } from './public-dto-schema';

type PageData = { items: HistoryItem[]; nextCursor: string | null };
export type ControlCommand = 'pause' | 'resume' | 'kill';

@Injectable({ providedIn: 'root' })
export class SupervisorApi {
  private readonly control = inject(ControlService);
  private readonly history = inject(HistoryService);
  private readonly policies = inject(RiskPolicyService);
  private readonly snapshots = inject(SnapshotService);

  snapshot(): Promise<SupervisorSnapshot> {
    return firstValueFrom(this.snapshots.getSupervisorSnapshot(undefined, 'body', false, { transferCache: false }))
      .then(projectSnapshot);
  }

  async policy(): Promise<RiskPolicy | null> {
    try {
      return projectPolicy(await firstValueFrom(this.policies.getRiskPolicy(undefined, 'body', false, { transferCache: false })));
    } catch (error) {
      if (httpStatus(error) === 404) return null;
      throw error;
    }
  }

  async activate(code: string): Promise<void> {
    await firstValueFrom(this.control.activateLocalSession(
      crypto.randomUUID(), { activationCode: code }, undefined, 'body', false,
      { transferCache: false },
    ));
  }

  async page(resource: Resource, cursor?: string, traceId?: string): Promise<PageData> {
    const h = this.history;
    const options = { transferCache: false };
    let response: unknown;
    switch (resource) {
      case 'market-states': response = await firstValueFrom(h.listMarketStates(cursor, 50, undefined, 'body', false, options)); break;
      case 'decisions': response = await firstValueFrom(h.listDecisions(cursor, 50, undefined, 'body', false, options)); break;
      case 'proposals': response = await firstValueFrom(h.listProposals(cursor, 50, undefined, 'body', false, options)); break;
      case 'risk-decisions': response = await firstValueFrom(h.listRiskDecisions(cursor, 50, undefined, 'body', false, options)); break;
      case 'orders': response = await firstValueFrom(h.listOrders(cursor, 50, undefined, 'body', false, options)); break;
      case 'audit': response = await firstValueFrom(h.listAuditEvents(cursor, 50, undefined, traceId || undefined, 'body', false, options)); break;
      case 'runs': response = await firstValueFrom(h.listAgentRuns(cursor, 50, undefined, 'body', false, options)); break;
      case 'costs': response = await firstValueFrom(h.listApiCosts(cursor, 50, undefined, 'body', false, options)); break;
    }
    return projectPage(resource, response);
  }

  command(kind: ControlCommand, key: string): Promise<ModeState> {
    const reason = { reason: `Supervisor dashboard requested ${kind}` };
    const options = { transferCache: false };
    switch (kind) {
      case 'pause': return firstValueFrom(this.control.pauseNewEntries(key, reason, undefined, 'body', false, options)).then(projectMode);
      case 'resume': return firstValueFrom(this.control.resumeNewEntries(key, reason, undefined, 'body', false, options)).then(projectMode);
      case 'kill': return firstValueFrom(this.control.activateKillSwitch(key, reason, undefined, 'body', false, options)).then(projectMode);
    }
  }
}

export function httpStatus(error: unknown): number | null {
  if (typeof error !== 'object' || error === null || !('status' in error)) return null;
  const status = (error as { status: unknown }).status;
  return typeof status === 'number' ? status : null;
}

const SAFE_CODES: Record<string, string> = {
  CURSOR_EXPIRED: 'Die Ansicht ist abgelaufen. Aktuelle Daten werden geladen.',
  HEALTH_CHECK_FAILED: 'Die Gesundheitsprüfung war nicht erfolgreich.',
  RISK_RECONCILIATION_REQUIRED: 'Der Abgleich muss aktuell sein.',
  RISK_POLICY_INCOMPLETE: 'Die Risk Policy ist unvollständig.',
  MODE_NOT_PAPER: 'Der Paper-Modus ist nicht aktiv.',
  VERSION_CONFLICT: 'Der Zustand hat sich geändert. Bitte erneut laden.',
  PRECONDITION_REQUIRED: 'Eine erforderliche Vorbedingung fehlt.',
  IDEMPOTENCY_CONFLICT: 'Diese Anfrage steht im Konflikt mit einer früheren Anfrage.',
  TEMPORARILY_UNAVAILABLE: 'Der Dienst ist vorübergehend nicht verfügbar.',
  BROKER_STATUS_UNKNOWN: 'Der Brokerstatus ist ungeklärt.',
  KILL_SWITCH_ACTIVE: 'Der Kill-Switch ist aktiv.',
  PAUSED_NEW: 'Neue Einstiege sind pausiert.',
  INVALID_REQUEST: 'Die Anfrage wurde abgelehnt.',
};

export function safeError(error: unknown): string {
  if (error instanceof InvalidPublicDto) return 'Die API-Antwort entsprach nicht dem öffentlichen Vertrag.';
  const status = httpStatus(error);
  if (status === 401 || status === 403) return 'Eine Operator-Sitzung ist erforderlich.';
  if (status === 0) return 'Der Dienst konnte nicht erreicht werden.';
  if (status === 410) return `CURSOR_EXPIRED: ${SAFE_CODES['CURSOR_EXPIRED']}`;
  if (typeof error === 'object' && error !== null && 'error' in error) {
    const body = (error as { error: unknown }).error;
    if (typeof body === 'object' && body !== null && 'code' in body) {
      const code = (body as { code: unknown }).code;
      if (typeof code === 'string' && Object.prototype.hasOwnProperty.call(SAFE_CODES, code)) {
        return `${code}: ${SAFE_CODES[code]}`;
      }
    }
  }
  return 'Die Anfrage konnte nicht abgeschlossen werden.';
}
