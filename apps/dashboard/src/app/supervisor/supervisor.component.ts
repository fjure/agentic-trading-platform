import { ChangeDetectionStrategy, Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatDialog, MatDialogModule, MAT_DIALOG_DATA } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatTableModule } from '@angular/material/table';
import { MatToolbarModule } from '@angular/material/toolbar';
import { firstValueFrom } from 'rxjs';
import type { Resource } from '../state/supervisor.models';
import { SupervisorStore } from '../state/supervisor.store';
import { DemoSetupComponent } from '../demo-setup/demo-setup.component';
import { PaperWorkflowComponent } from '../paper-workflow/paper-workflow.component';

type View = 'overview' | Resource;
type FieldSpec = readonly [label: string, path: string];
type Row = { id: string; title: string; subtitle: string; status: string; traceId: string; fields: { label: string; value: string }[] };
type DialogData = { title: string; message: string; danger: boolean };

// Only these contract fields are rendered. The API may add fields without exposing them in the UI.
const SCHEMA: Record<Resource, { title: string; subtitle: string; status: string; fields: FieldSpec[] }> = {
  'market-states': { title: 'instrument', subtitle: 'eventTime', status: 'quality', fields: [
    ['Markt', 'market'], ['Intervall', 'interval'], ['Qualität', 'quality'], ['Spread', 'spread'],
    ['Bar Start', 'bar.startAt'], ['Bar Ende', 'bar.endAt'], ['Open', 'bar.open'], ['High', 'bar.high'], ['Low', 'bar.low'], ['Close', 'bar.close'], ['Volumen', 'bar.volume'],
    ['Evidenzqualität', 'evidence.quality'], ['Quellen', 'evidence.sources'], ['Konflikte', 'evidence.conflicts'], ['Evidenz verfügbar ab', 'evidence.availableAt'],
    ['Feature last_close', 'features.last_close'], ['Feature previous_close', 'features.previous_close'], ['Feature window_volume', 'features.window_volume'], ['Feature return', 'features.return'], ['Feature momentum', 'features.momentum'],
    ['Portfolio Equity', 'portfolio.equity'], ['Portfolio Cash', 'portfolio.cash'], ['Positionsmenge', 'position.quantity'], ['Durchschnittspreis', 'position.averagePrice'],
    ['Empfangen', 'receivedAt'], ['Verfügbar ab', 'availableAt'], ['Stand', 'asOf'],
  ] },
  decisions: { title: 'choice', subtitle: 'marketStateId', status: 'reasonCode', fields: [
    ['Entscheidung', 'choice'], ['Confidence', 'confidence'], ['Verteilung LONG', 'distribution.LONG'], ['Verteilung FLAT', 'distribution.FLAT'], ['Verteilung ABSTAIN', 'distribution.ABSTAIN'],
    ['Regime', 'typedAnswers.regime.choice'], ['Regime Confidence', 'typedAnswers.regime.confidence'], ['Regime TREND', 'typedAnswers.regime.distribution.TREND'], ['Regime RANGE', 'typedAnswers.regime.distribution.RANGE'], ['Regime UNCERTAIN', 'typedAnswers.regime.distribution.UNCERTAIN'],
    ['Evidenz Score', 'typedAnswers.evidenceQuality.score'], ['Evidenz Confidence', 'typedAnswers.evidenceQuality.confidence'],
    ...Array.from({ length: 10 }, (_, index): FieldSpec => [`Evidenz Legende ${index}`, `typedAnswers.evidenceQuality.legend.${index}`]),
    ...Array.from({ length: 10 }, (_, index): FieldSpec => [`Evidenz Verteilung ${index}`, `typedAnswers.evidenceQuality.distribution.${index}`]),
    ['Signalkonflikt', 'typedAnswers.signalConflict.noul'],
    ['Shadow Richtung', 'typedAnswers.shadowDirection.choice'], ['Shadow Confidence', 'typedAnswers.shadowDirection.confidence'], ['Shadow LONG', 'typedAnswers.shadowDirection.distribution.LONG'], ['Shadow FLAT', 'typedAnswers.shadowDirection.distribution.FLAT'], ['Shadow ABSTAIN', 'typedAnswers.shadowDirection.distribution.ABSTAIN'],
    ['Reason-Code', 'reasonCode'], ['Modell', 'modelId'], ['Prompt-Version', 'promptVersion'], ['Fragen-Version', 'questionVersion'], ['Konfiguration', 'configVersion'], ['Latenz ms', 'latencyMs'],
    ['API Units', 'apiUnits'], ['Input Tokens', 'inputTokens'], ['Output Tokens', 'outputTokens'], ['Geschätzte Kosten', 'estimatedCost'], ['Gültig bis', 'validUntil'],
  ] },
  proposals: { title: 'instrument', subtitle: 'createdAt', status: 'target', fields: [
    ['Ziel', 'target'], ['Menge', 'quantity'], ['Begründungscodes', 'rationaleCodes'], ['Entscheidungs-ID', 'decisionId'], ['Marktstatus-ID', 'marketStateId'], ['Erstellt', 'createdAt'], ['Gültig bis', 'validUntil'],
  ] },
  'risk-decisions': { title: 'proposalId', subtitle: 'decidedAt', status: 'approved', fields: [
    ['Freigabe', 'approved'], ['Reason-Code', 'reasonCode'], ['Alle Reason-Codes', 'reasonCodes'], ['Policy-Version', 'policyVersion'],
    ['Equity', 'exposureSnapshot.equity'], ['Brutto-Notional', 'exposureSnapshot.grossNotional'], ['Reserviertes Notional', 'exposureSnapshot.reservedNotional'],
    ['Reservierungs-ID', 'reservationId'], ['Entschieden', 'decidedAt'], ['Gültig bis', 'validUntil'],
  ] },
  orders: { title: 'instrument', subtitle: 'updatedAt', status: 'status', fields: [
    ['Status', 'status'], ['Angeforderte Menge', 'requestedQuantity'], ['Gefüllte Menge', 'filledQuantity'], ['Durchschnittlicher Füllpreis', 'averageFillPrice'],
    ['Reconciliation', 'reconciliation'], ['Vorschlags-ID', 'proposalId'], ['Risk-Entscheidungs-ID', 'riskDecisionId'], ['Aktualisiert', 'updatedAt'],
  ] },
  audit: { title: 'action', subtitle: 'occurredAt', status: 'outcome', fields: [
    ['Akteur', 'actor'], ['Aktion', 'action'], ['Ergebnis', 'outcome'], ['Reason-Code', 'reasonCode'], ['Policy-Version', 'policyVersion'], ['Zeitpunkt', 'occurredAt'],
  ] },
  runs: { title: 'role', subtitle: 'startedAt', status: 'status', fields: [
    ['Rolle', 'role'], ['Status', 'status'], ['Start', 'startedAt'], ['Letzter Heartbeat', 'heartbeatAt'],
  ] },
  costs: { title: 'provider', subtitle: 'observedAt', status: 'amount', fields: [
    ['Betrag', 'amount'], ['Währung', 'currency'], ['Beobachtet', 'observedAt'],
  ] },
};

function knownField(item: unknown, path: string): string {
  let current: unknown = item;
  for (const part of path.split('.')) {
    if (!current || typeof current !== 'object' || !Object.prototype.hasOwnProperty.call(current, part)) return 'nicht verfügbar';
    current = (current as Record<string, unknown>)[part];
  }
  if (Array.isArray(current)) return current.length && current.every(x => typeof x === 'string') ? current.join(' · ') : 'nicht verfügbar';
  if (current === null || current === undefined || current === '') return 'nicht verfügbar';
  if (typeof current === 'boolean') return current ? 'Ja' : 'Nein';
  return typeof current === 'string' || typeof current === 'number' ? String(current) : 'nicht verfügbar';
}

@Component({
  selector: 'atl-confirm-dialog',
  imports: [MatButtonModule, MatDialogModule],
  template: `<h2 mat-dialog-title>{{ data.title }}</h2><mat-dialog-content><p>{{ data.message }}</p></mat-dialog-content><mat-dialog-actions align="end"><button mat-button [mat-dialog-close]="false">Abbrechen</button><button mat-flat-button [class.danger-button]="data.danger" [mat-dialog-close]="true">Bestätigen</button></mat-dialog-actions>`,
  styles: [`.danger-button { background: #a72b36; color: white; } mat-dialog-content { max-width: 32rem; }`],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ConfirmDialogComponent { readonly data = inject<DialogData>(MAT_DIALOG_DATA); }

@Component({
  selector: 'atl-supervisor',
  imports: [ReactiveFormsModule, MatButtonModule, MatCardModule, MatDialogModule, MatFormFieldModule, MatInputModule, MatTableModule, MatToolbarModule, DemoSetupComponent, PaperWorkflowComponent],
  templateUrl: './supervisor.component.html',
  styleUrl: './supervisor.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SupervisorComponent implements OnInit {
  readonly store = inject(SupervisorStore);
  private readonly dialog = inject(MatDialog);
  readonly active = signal<View>('overview');
  readonly paperActive = signal(typeof location !== 'undefined' && location.hash === '#alpaca-paper');
  readonly expandedId = signal<string | null>(null);
  readonly traceSearchBusy = signal(false);
  readonly activationCode = new FormControl('', { nonNullable: true });
  readonly traceFilter = new FormControl('', { nonNullable: true });
  readonly columns = ['entity', 'status', 'trace', 'detail'];
  readonly navigation: { view: View; label: string }[] = [
    { view: 'overview', label: 'Übersicht' }, { view: 'market-states', label: 'Markt' }, { view: 'decisions', label: 'Entscheidungen' },
    { view: 'proposals', label: 'Vorschläge' }, { view: 'risk-decisions', label: 'Risiko' }, { view: 'orders', label: 'Orders' },
    { view: 'audit', label: 'Audit' }, { view: 'runs', label: 'Worker' }, { view: 'costs', label: 'Kosten' },
  ];
  private readonly traceResources: readonly Resource[] = ['market-states', 'decisions', 'proposals', 'risk-decisions', 'orders', 'runs', 'costs'];
  readonly activeLabel = computed(() => this.navigation.find(item => item.view === this.active())?.label ?? 'Übersicht');
  readonly traceHasMore = computed(() => this.traceResources.some(resource => !!this.store.pages()[resource].nextCursor));
  readonly tracePageErrors = computed(() => this.traceResources.some(resource => !!this.store.pages()[resource].error));
  readonly tracePagesLoading = computed(() => this.traceResources.some(resource => this.store.pages()[resource].loading));
  readonly currentPage = computed(() => {
    const view = this.active();
    return view === 'overview' ? null : this.store.pages()[view];
  });
  readonly rows = computed(() => {
    const view = this.active();
    const page = this.currentPage();
    if (view === 'overview' || !page) return [];
    return page.items.map(item => this.toRow(view, item));
  });
  readonly traceLinks = computed(() => {
    const traceId = this.store.filters().traceId;
    if (!traceId || this.active() !== 'audit') return [];
    return this.navigation.filter(nav => nav.view !== 'overview' && nav.view !== 'audit').map(nav => ({
      label: nav.label,
      rows: this.store.pages()[nav.view as Resource].items.filter(item => knownField(item, 'traceId') === traceId).map(item => this.toRow(nav.view as Resource, item)),
    })).filter(group => group.rows.length);
  });

  private toRow(resource: Resource, item: unknown): Row {
    const schema = SCHEMA[resource];
    return {
      id: knownField(item, 'id'), title: knownField(item, schema.title), subtitle: knownField(item, schema.subtitle),
      status: resource === 'risk-decisions' ? (knownField(item, 'approved') === 'Ja' ? 'FREIGEGEBEN' : 'ABGELEHNT') : resource === 'costs' ? (knownField(item, 'amount') === 'nicht verfügbar' ? 'NICHT VERFÜGBAR' : 'BEOBACHTET') : knownField(item, schema.status),
      traceId: knownField(item, 'traceId'), fields: schema.fields.map(([label, path]) => ({ label, value: knownField(item, path) })),
    };
  }

  ngOnInit(): void { void this.store.initialize(); }

  async activate(): Promise<void> {
    const code = this.activationCode.value.trim();
    this.activationCode.reset('');
    if (code) await this.store.activate(code);
  }

  async select(view: View): Promise<void> {
    this.paperActive.set(false);
    history.replaceState(null, '', location.pathname + location.search);
    this.active.set(view);
    this.expandedId.set(null);
    if (view !== 'overview' && !this.store.pages()[view].items.length) await this.store.loadPage(view);
  }

  selectPaper(): void {
    this.paperActive.set(true);
    history.replaceState(null, '', '#alpaca-paper');
  }

  paperSessionLost(): void { void this.store.refresh(); }

  async loadMore(): Promise<void> {
    const view = this.active();
    if (view !== 'overview') await this.store.loadPage(view, true);
  }

  async applyTrace(): Promise<void> {
    if (this.traceSearchBusy()) return;
    this.traceSearchBusy.set(true);
    this.store.filterChanged(this.traceFilter.value.trim());
    this.expandedId.set(null);
    try {
      // Initial pages are usually loaded with the snapshot. Fill any missing first pages on demand.
      await Promise.all(this.traceResources.filter(resource => {
        const page = this.store.pages()[resource];
        return !page.items.length && !page.loading && !page.nextCursor;
      }).map(resource => this.store.loadPage(resource)));
      await this.store.loadPage('audit');
    } finally {
      this.traceSearchBusy.set(false);
    }
  }

  async continueTraceSearch(): Promise<void> {
    if (this.traceSearchBusy()) return;
    this.traceSearchBusy.set(true);
    try {
      await Promise.all(this.traceResources.filter(resource => !!this.store.pages()[resource].nextCursor)
        .map(resource => this.store.loadPage(resource, true)));
    } finally {
      this.traceSearchBusy.set(false);
    }
  }

  async openTrace(traceId: string): Promise<void> {
    if (!traceId || traceId === 'nicht verfügbar') return;
    this.traceFilter.setValue(traceId);
    this.active.set('audit');
    await this.applyTrace();
  }

  async confirmAction(action: 'pause' | 'resume' | 'kill'): Promise<void> {
    const content: Record<typeof action, DialogData> = {
      pause: { title: 'Neue Einstiege pausieren', message: 'Neue Einstiege werden gesperrt. Bereits laufende Reconciliation bleibt möglich.', danger: false },
      resume: { title: 'Fortsetzen anfordern', message: 'Das Backend prüft Health und Reconciliation erneut. Eine Ablehnung wird mit ihrem Reason-Code angezeigt.', danger: false },
      kill: { title: 'Kill-Switch aktivieren', message: 'Neue Einstiege werden sofort gesperrt. Diese Aktion ist sicherheitskritisch.', danger: true },
    };
    const confirmed = await firstValueFrom(this.dialog.open(ConfirmDialogComponent, { data: content[action], ariaLabel: content[action].title, width: 'min(92vw, 440px)' }).afterClosed());
    if (!confirmed) return;
    if (action === 'pause') await this.store.pauseRequested();
    else if (action === 'resume') await this.store.resumeRequested();
    else await this.store.killRequested();
  }

  statusTone(status: string): string {
    if (['HEALTHY', 'CURRENT', 'VALID', 'RUNNING', 'ACCEPTED', 'FILLED', 'SUCCEEDED', 'FREIGEGEBEN'].includes(status)) return 'good';
    if (['UNAVAILABLE', 'MISMATCH', 'UNKNOWN', 'FAILED', 'DEAD_LETTER', 'REFUSED', 'INVALID', 'ABGELEHNT', 'NICHT VERFÜGBAR'].includes(status)) return 'bad';
    return 'warn';
  }

  connectionLabel(): string {
    return ({ connecting: 'Verbindung wird aufgebaut', connected: 'Aktuell verbunden', stale: 'Daten veraltet', disconnected: 'Verbindung getrennt', unauthorized: 'Sitzung erforderlich' } as const)[this.store.connectionStatus()];
  }
}
