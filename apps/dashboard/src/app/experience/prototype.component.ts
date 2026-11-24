import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { BreakpointObserver } from '@angular/cdk/layout';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatStepperModule } from '@angular/material/stepper';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { FormsModule } from '@angular/forms';
const STATES = ['ready', 'loading', 'empty', 'offline', 'stale', 'conflict', 'unauthorized', 'unavailable'] as const;
type ViewState = typeof STATES[number];
@Component({
  selector: 'atl-experience-prototype',
  imports: [RouterLink, MatButtonModule, MatCardModule, MatStepperModule, MatFormFieldModule, MatInputModule, MatCheckboxModule, FormsModule],
  templateUrl: './prototype.component.html', styleUrl: './prototype.component.css', changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PrototypeComponent {
  readonly route = inject(ActivatedRoute);
  readonly data = toSignal(this.route.data, { initialValue: this.route.snapshot.data });
  readonly params = toSignal(this.route.paramMap, { initialValue: this.route.snapshot.paramMap });
  readonly query = toSignal(this.route.queryParamMap, { initialValue: this.route.snapshot.queryParamMap });
  readonly small = toSignal(inject(BreakpointObserver).observe('(max-width: 767px)'));
  readonly screen = computed(() => this.data()['screen'] as string);
  readonly title = computed(() => this.data()['title'] as string);
  readonly state = computed<ViewState>(() => { const value = this.query().get('view'); return STATES.includes(value as ViewState) ? value as ViewState : 'ready'; });
  readonly context = computed(() => this.screen() === 'order' ? 'PAPER' : 'DEMO');
  readonly contextLabel = computed(() => {
    if (this.screen() === 'order') return 'PAPER · synthetisches Beispielkonto';
    if (['strategy', 'strategies'].includes(this.screen()) || (this.screen() === 'task' && this.params().get('taskId') !== 'local-analysis')) return 'RESEARCH · historische synthetische Evidenz';
    if (['tasks', 'activity'].includes(this.screen())) return 'DEMO / RESEARCH / PAPER · Quellen getrennt';
    return 'DEMO · prototype-demo-01';
  });
  readonly contextQuery = computed(() => ({ context: this.context(), demoRunId: this.context() === 'DEMO' ? 'prototype-demo-01' : undefined }));
  readonly stateLabels: Record<ViewState, string> = { ready: 'Bestätigtes synthetisches Beispiel', loading: 'Beispiel wird geladen', empty: 'Keine Einträge im Beispiel', offline: 'Verbindung unterbrochen', stale: 'Daten veraltet', conflict: 'Version hat sich geändert', unauthorized: 'Sitzung abgelaufen', unavailable: 'Quelle nicht verfügbar' };
  readonly stateMessages: Record<ViewState, string> = {
    ready: 'Synthetische Daten, keine reale Kontoverbindung.', loading: 'Laden ist simuliert. Zur Wiederaufnahme das bestätigte Beispiel erneut lesen.',
    empty: 'Die Quelle ist erreichbar, enthält aber keine Einträge. Unbekannte Kennzahlen sind keine Nullwerte.',
    offline: 'Letzte Bestätigung: 2026-11-23T09:00:00Z. Neue Freigaben sind gesperrt. Bestehenden Vorgang erneut prüfen.',
    stale: 'Der Frischenachweis fehlt. Letztbestätigte Bestände bleiben sichtbar; neue Paperbestätigung ist gesperrt.',
    conflict: 'Der Entwurf passt nicht mehr zum bestätigten Stand. Aktuellen Stand lesen und bewusst neu prüfen.',
    unauthorized: 'Flüchtige Entwürfe sind verworfen. Die echte Anmeldung ist unter Daten und Betrieb erreichbar. Rückkehr liest nur.',
    unavailable: 'Die Quelle ist nicht bestätigt. Keine Ersatzquelle und kein anderes Konto wurden gewählt.',
  };
  readonly states = STATES;
  readonly healthy = computed(() => this.state() === 'ready');
  readonly knownObject = computed(() => {
    const id = this.params().get('strategyId') ?? this.params().get('taskId');
    const version = this.query().get('version');
    const allowed = this.screen() === 'strategy' ? ['trend-long-flat'] : ['local-analysis', 'tech-recovery', 'quant-example'];
    const source = this.query().get('context');
    return (!id || allowed.includes(id)) && this.query().getAll('version').length <= 1 && (!version || version === '1')
      && (this.screen() !== 'order' || (this.query().getAll('strategy').length <= 1 && (!this.query().has('strategy') || this.query().get('strategy') === 'trend-long-flat')))
      && (!['home', 'portfolio', 'insights'].includes(this.screen()) || ((!source || source === 'DEMO') && (!this.query().has('demoRunId') || this.query().get('demoRunId') === 'prototype-demo-01')));
  });
  readonly taskKind = computed(() => this.params().get('taskId') ?? 'local-analysis');
  readonly phase = computed(() => { const value = this.query().get('phase'); return ['evidence', 'eligibility', 'preview', 'risk', 'confirm', 'status'].includes(value ?? '') ? value : null; });
  readonly orderStatus = computed(() => { const value = this.query().get('status'); return ['PENDING', 'UNKNOWN', 'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING', 'CANCELED'].includes(value ?? '') ? value : 'PENDING'; });
  readonly scenario = signal('ready');
  readonly scenarioMessages: Record<string, string> = {
    ready: 'Aktuelle Beispielbindung erfüllt: menschlicher LIMIT-Auftrag, Paperkonto, SPY, Strategie v1 und frische Reconciliation. Keine reale Riskfreigabe.',
    provider: 'Provider nicht eingerichtet: lokal weiterklären. Keine externe Verarbeitung.',
    budget: 'Budget ausgeschöpft: vorhandene Ergebnisse lesen. Keine automatische Erhöhung oder Ausführung.',
    stale: 'Daten veraltet: Frischenachweis fehlt. Bestätigung gesperrt.',
    rights: 'Datenrechte nicht bestätigt: Nutzungsrechte prüfen. Keine Providerabfrage.',
    unsupported: 'Originalhypothese nicht unterstützt: lokal präzisieren oder getrennte Ersatzhypothese prüfen.',
    grant: 'Quantfreigabe abgelaufen oder Bindung geändert: neue Prüfung nötig. Kein Batchstart.',
    paused: 'Paper ist PAUSED / HALTED: keine neuen Einstiege. Bestehende Pause-/Killwege bleiben erreichbar.',
    session: 'Sitzung abgelaufen: zur lokalen Anmeldung; anschließend nur lesend zurückkehren.',
    unknown: 'Broker UNKNOWN / Provider OUTCOME_UNKNOWN: Ausgang ungeklärt. Bestehenden Vorgang prüfen; keine Ersatzorder und kein Ersatzrun.',
  };
  readonly scenarios = Object.keys(this.scenarioMessages);
  readonly scenarioLabels: Record<string, string> = { ready: 'Belegte Beispiel-Eignung', provider: 'Provider deaktiviert', budget: 'Budget ausgeschöpft', stale: 'Veraltete Daten', rights: 'Datenrechte fehlen', unsupported: 'Nicht unterstützte Hypothese', grant: 'Quantfreigabe ungültig', paused: 'Paper pausiert / HALTED', session: 'Sitzung abgelaufen', unknown: 'Ausgang UNKNOWN' };
  readonly approved = signal(false);
  readonly localConfirmed = signal(false);
  readonly interpretationConfirmed = signal(false);
  readonly approximationAccepted = signal(false);
  readonly notice = signal('');
  experience = 'Neu im Thema'; horizon = 'Noch offen'; instrument = 'SPY'; side = 'BUY'; quantity = '1'; limit = '100.00';
  question = 'Erholen sich Techaktien nach starken Kursrückgängen binnen einer Woche?';
  readonly filter = signal('all');
  readonly orderValid = computed(() => this.healthy() && this.scenario() === 'ready');
  constructor() {
    effect(() => {
      if (this.state() === 'unauthorized') this.clearDrafts();
    });
  }
  validFields(): boolean { return this.instrument === 'SPY' && this.side === 'BUY' && this.quantity === '1' && this.limit === '100.00'; }
  plan(): void { this.approved.set(false); this.notice.set(this.validFields() ? 'Entwurf lokal geprüft: SPY / BUY / 1 / LIMIT 100.00 USD. Keine Reservation und kein Brokerkontakt.' : 'Diese feste Prototypfixture unterstützt ausschließlich SPY / BUY / Menge 1 / LIMIT 100.00 USD. Andere Eingaben werden nicht ersetzt; bitte korrigieren oder verwerfen.'); }
  confirm(): void { if (this.orderValid() && this.approved()) this.notice.set('Nur simuliert: prototype-operation-01 angenommen. Kein Kauf, kein Fill und kein Brokerkontakt. Statusansicht öffnen.'); }
  inspect(): void { this.notice.set('Denselben synthetischen Vorgang prototype-operation-01 erneut gelesen. Ausgang bleibt UNKNOWN; keine Ersatzorder und keine Budgetfreigabe.'); }
  resetInterpretation(): void { this.interpretationConfirmed.set(false); this.approximationAccepted.set(false); }
  clearDrafts(): void { this.question = ''; this.quantity = ''; this.limit = ''; this.approved.set(false); this.localConfirmed.set(false); this.resetInterpretation(); this.notice.set('Flüchtige Entwürfe verworfen. Keine Verarbeitung gestartet.'); }
}
