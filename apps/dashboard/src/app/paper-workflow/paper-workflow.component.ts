import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, output, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MAT_DIALOG_DATA, MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { firstValueFrom } from 'rxjs';
import { PaperService } from '../api/generated/control-api/api/paper.service';
import type { PaperSnapshot, PaperOrder, PaperFill, PaperOperation, PaperTrace, PaperLimitOrderRequest, PaperStartRequest, PaperTradingProfileUpdateRequest } from '../api/generated/control-api/model/models';
import { CredentialDialogComponent } from '../demo-setup/demo-setup.component';
import { httpStatus, safeError } from '../state/supervisor-api';
import { paperSnapshot, paperOrders, paperFills, paperOperations, paperOperation, paperTrace } from './paper-view';
import { PaperErrorCode } from '../api/generated/control-api/model/paperErrorCode';

type Strategy = 'mock-neutral' | 'neutral-baseline' | 'jev';
type Confirmation = { title: string; lines: string[]; danger?: boolean };
type Pending = { slot: string; label: string; key: string; send: () => Promise<PaperOperation> };
const ACTIVE = new Set(['ACCEPTED', 'RUNNING']);
const BUDGET = /^(?=.*[1-9])(?:(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,18})?|1000000(?:\.0{1,18})?)$/;
const REASONS: Partial<Record<PaperErrorCode, string>> = {
  BUDGET_EXCEEDED: 'Cash, Riskbudget oder ein Positionslimit reicht nicht aus.',
  INSUFFICIENT_POSITION: 'Die freie bestätigte Longmenge reicht für diesen Verkauf nicht aus.',
  STALE_QUOTE: 'Der Quote-Beleg ist abgelaufen. Neue Marktdaten abwarten.',
  APPROVAL_EXPIRED: 'Die ursprüngliche Freigabe ist abgelaufen; sie wird nicht verlängert.',
  CAPABILITY_UNCONFIRMED: 'Die benötigte Orderfähigkeit ist nicht frisch bestätigt.',
  SESSION_CLOSED: 'Es liegt keine offene reguläre US-Sitzung vor.',
  VERSION_CONFLICT: 'Der Zustand oder eine Version hat sich geändert. Neu laden und bewusst erneut prüfen.',
  RECONCILIATION_REQUIRED: 'Der finanzielle Brokerabgleich ist noch nicht vollständig bestätigt.',
  CREDENTIALS_INVALID: 'Die privaten Zugangsdaten konnten nicht bestätigt werden.',
  CONNECTION_NOT_VERIFIED: 'Die gespeicherte Verbindung muss zuerst geprüft werden.',
  ENTRY_PAUSED: 'Neue Einstiege sind pausiert. Bestehende Verpflichtungen bleiben im Abgleich.',
  KILL_SWITCH_ACTIVE: 'Der dauerhafte Killzustand sperrt neue Einstiege.',
  BROKER_STATUS_UNKNOWN: 'Der Brokerzustand ist ungeklärt. Die Verpflichtung bleibt erhalten.',
  PROVIDER_RATE_LIMITED: 'Der Provider begrenzt Anfragen; der persistente Backoff bleibt erhalten.',
};

@Component({
  selector: 'atl-paper-confirm',
  imports: [MatDialogModule, MatButtonModule],
  template: `<h2 mat-dialog-title>{{ data.title }}</h2><mat-dialog-content>@for (line of data.lines; track $index) { <p>{{ line }}</p> }</mat-dialog-content><mat-dialog-actions align="end"><button mat-button [mat-dialog-close]="false">Abbrechen</button><button mat-flat-button [mat-dialog-close]="true">Bestätigen</button></mat-dialog-actions>`,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PaperConfirmComponent { readonly data = inject<Confirmation>(MAT_DIALOG_DATA); }

@Component({
  selector: 'atl-paper-workflow',
  imports: [ReactiveFormsModule, MatButtonModule, MatCardModule, MatCheckboxModule, MatDialogModule, MatFormFieldModule, MatInputModule, MatSelectModule],
  templateUrl: './paper-workflow.component.html',
  styleUrl: './paper-workflow.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PaperWorkflowComponent implements OnInit, OnDestroy {
  private readonly api = inject(PaperService);
  private readonly dialog = inject(MatDialog);
  readonly sessionLost = output<void>();
  readonly snapshot = signal<PaperSnapshot | null>(null);
  readonly orders = signal<PaperOrder[]>([]);
  readonly fills = signal<PaperFill[]>([]);
  readonly operations = signal<PaperOperation[]>([]);
  readonly operationsKnown = signal(false);
  readonly trace = signal<PaperTrace | null>(null);
  readonly error = signal('');
  readonly notice = signal('');
  readonly connected = signal(false);
  readonly authorized = signal(true);
  readonly historyPinned = signal(false);
  readonly loading = signal(false);
  readonly sending = signal<ReadonlySet<string>>(new Set());
  readonly uncertain = signal<Pending[]>([]);
  readonly uncertainProfile = signal<{ key: string; body: PaperTradingProfileUpdateRequest } | null>(null);
  readonly more = signal<Record<string, string | null>>({ orders: null, fills: null, operations: null });
  readonly profileBudget = new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(BUDGET)] });
  readonly strategy = new FormControl<Strategy>('mock-neutral', { nonNullable: true });
  readonly feeConfirmed = new FormControl(false, { nonNullable: true });
  readonly symbol = new FormControl<'SPY' | 'QQQ'>('SPY', { nonNullable: true });
  private readonly selectedSymbol = signal<'SPY' | 'QQQ'>('SPY');
  private readonly symbolChanges = this.symbol.valueChanges.subscribe(value => this.selectedSymbol.set(value));
  readonly side = new FormControl<'BUY' | 'SELL'>('BUY', { nonNullable: true });
  readonly quantity = new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(/^(?=.*[1-9])(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,9})?$/)] });
  readonly price = new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(/^(?=.*[1-9])(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,6})?$/)] });
  readonly symbols = new FormControl<('SPY' | 'QQQ')[]>(['SPY'], { nonNullable: true });
  readonly traceId = new FormControl('', { nonNullable: true, validators: [Validators.pattern(/^[0-9a-f]{32}$/)] });
  readonly entryPending = computed(() => !this.operationsKnown() || this.sending().size > 0 || this.uncertain().length > 0 || this.uncertainProfile() !== null || this.operations().some(x => ACTIVE.has(x.status)));
  readonly profileCurrent = computed(() => {
    const data = this.snapshot();
    return !!data?.profile.configured && data.profile.credentialGeneration === data.status.credentialGeneration && data.profile.accountGeneration === data.status.accountGeneration;
  });
  readonly canEnter = computed(() => this.connected() && this.profileCurrent() && this.snapshot()?.status.entryGateOpen === true && !this.entryPending() && this.marketFresh(this.selectedSymbol()));
  private readonly clockTick = signal(0);
  private timer?: ReturnType<typeof setInterval>;
  private clockTimer?: ReturnType<typeof setInterval>;
  private disposed = false;
  private epoch = 0;
  private readGeneration = 0;
  private serverClock = { at: 0, received: 0 };
  private readonly dialogs = new Set<MatDialogRef<unknown>>();
  private confirming = false;

  ngOnInit(): void { void this.refresh(); this.timer = setInterval(() => void this.refresh(true), 5000); this.clockTimer = setInterval(() => this.clockTick.update(x => x + 1), 1000); }
  ngOnDestroy(): void {
    this.disposed = true; this.epoch++;
    if (this.timer) clearInterval(this.timer);
    if (this.clockTimer) clearInterval(this.clockTimer);
    this.symbolChanges.unsubscribe();
    this.clearForms(); this.uncertain.set([]); this.uncertainProfile.set(null);
    for (const ref of this.dialogs) ref.close();
  }

  private clearForms(): void {
    this.profileBudget.reset(''); this.quantity.reset(''); this.price.reset('');
    this.feeConfirmed.reset(false); this.traceId.reset('');
    this.strategy.reset('mock-neutral'); this.symbol.reset('SPY'); this.side.reset('BUY'); this.symbols.reset(['SPY']);
  }

  private failed(error: unknown): void {
    if (this.disposed) return;
    const body = error && typeof error === 'object' && 'error' in error ? error.error : null;
    const code = body && typeof body === 'object' && 'code' in body ? body.code : null;
    this.error.set(typeof code === 'string' && Object.values(PaperErrorCode).includes(code as PaperErrorCode) ? this.reason(code as PaperErrorCode) : safeError(error));
    const status = httpStatus(error);
    if (status === null || status === 0 || status >= 500) {
      this.epoch++; this.connected.set(false); this.clearForms();
      for (const ref of this.dialogs) ref.close();
    }
    if ([401, 403].includes(httpStatus(error) ?? 0)) {
      this.epoch++; this.authorized.set(false); this.connected.set(false); this.clearForms(); this.uncertain.set([]); this.uncertainProfile.set(null);
      this.snapshot.set(null); this.orders.set([]); this.fills.set([]); this.operations.set([]); this.operationsKnown.set(false); this.trace.set(null);
      for (const ref of this.dialogs) ref.close();
      this.sessionLost.emit();
    }
  }

  async refresh(poll = false): Promise<void> {
    if (this.loading() || this.disposed) return;
    const epoch = this.epoch;
    this.loading.set(true);
    this.readGeneration++;
    this.operationsKnown.set(false);
    if (!poll) this.historyPinned.set(false);
    try {
      const data = paperSnapshot(await firstValueFrom(this.api.getPaperSnapshot()));
      if (this.disposed || epoch !== this.epoch) return;
      const previous = this.snapshot();
      if (previous && (previous.status.credentialGeneration !== data.status.credentialGeneration || previous.status.accountGeneration !== data.status.accountGeneration)) this.feeConfirmed.reset(false);
      this.snapshot.set(data); this.authorized.set(true); this.connected.set(true); this.error.set('');
      this.serverClock = { at: Date.parse(data.observedAt), received: performance.now() };
      if (!this.profileBudget.dirty) this.profileBudget.setValue(data.profile.riskBudget ?? '');
      if (!this.strategy.dirty && data.profile.strategy) this.strategy.setValue(data.profile.strategy);
      await Promise.all((poll && this.historyPinned() ? ['operations'] : ['orders', 'fills', 'operations']).map(kind => this.page(kind, false, poll)));
    } catch (error) { this.connected.set(false); this.failed(error); }
    finally { if (!this.disposed) this.loading.set(false); }
  }

  async page(kind: string, append = true, poll = false): Promise<void> {
    const epoch = this.epoch;
    const readGeneration = this.readGeneration;
    if (append && this.loading()) return;
    const cursor = append ? this.more()[kind] ?? undefined : undefined;
    if (append && !cursor) return;
    try {
      if (kind === 'orders') {
        const result = paperOrders(await firstValueFrom(this.api.listPaperOrders(25, cursor)));
        if (this.disposed || epoch !== this.epoch || readGeneration !== this.readGeneration) return;
        this.orders.set(append ? this.merge(this.orders(), result.items, x => x.orderId) : result.items);
        this.more.update(x => ({ ...x, orders: result.nextCursor }));
      } else if (kind === 'fills') {
        const result = paperFills(await firstValueFrom(this.api.listPaperFills(25, cursor)));
        if (this.disposed || epoch !== this.epoch || readGeneration !== this.readGeneration) return;
        this.fills.set(append ? this.merge(this.fills(), result.items, x => x.fillId) : result.items);
        this.more.update(x => ({ ...x, fills: result.nextCursor }));
      } else {
        const result = paperOperations(await firstValueFrom(this.api.listPaperOperations(25, cursor)));
        if (this.disposed || epoch !== this.epoch || readGeneration !== this.readGeneration) return;
        this.operations.set(append ? this.merge(this.operations(), result.items, x => x.operationId) : poll && this.historyPinned() ? this.merge(this.operations().filter(x => !ACTIVE.has(x.status)), result.items, x => x.operationId) : result.items);
        if (!append) this.operationsKnown.set(true);
        if (!(poll && this.historyPinned())) this.more.update(x => ({ ...x, operations: result.nextCursor }));
      }
      if (append) this.historyPinned.set(true);
    } catch (error) { this.failed(error); }
  }

  private merge<T>(old: T[], current: T[], id: (item: T) => string): T[] { return [...new Map([...old, ...current].map(x => [id(x), x])).values()]; }
  busy(slot: string): boolean { return this.sending().has(slot) || this.uncertain().some(x => x.slot === slot); }
  reason(code: PaperErrorCode): string { return `${code}: ${REASONS[code] ?? 'Das Paperbackend meldet diesen gespeicherten Zustand.'}`; }
  cancelBusy(orderId: string): boolean { return this.busy('cancel:' + orderId) || this.operations().some(x => x.operationType === 'CANCEL_ORDER' && x.orderId === orderId && ACTIVE.has(x.status)); }

  private async confirm(data: Confirmation): Promise<boolean> {
    if (this.confirming || this.disposed) return false;
    this.confirming = true;
    const ref = this.dialog.open(PaperConfirmComponent, { data, width: 'min(92vw, 520px)', ariaLabel: data.title });
    this.dialogs.add(ref);
    try { return await firstValueFrom(ref.afterClosed()) === true && !this.disposed; }
    finally { this.dialogs.delete(ref); this.confirming = false; }
  }

  openSetup(provider: 'alpaca' | 'jev'): void {
    const ref = this.dialog.open(CredentialDialogComponent, { data: { provider }, width: 'min(92vw, 460px)', ariaLabel: provider === 'alpaca' ? 'Alpaca Paper einrichten' : 'Jev einrichten' });
    this.dialogs.add(ref);
    ref.afterClosed().subscribe(() => { this.dialogs.delete(ref); if (!this.disposed) void this.refresh(); });
  }

  private async send(slot: string, label: string, invoke: (key: string) => Promise<PaperOperation>): Promise<void> {
    if (this.busy(slot) || this.disposed) return;
    const key = crypto.randomUUID();
    await this.issue({ slot, label, key, send: () => invoke(key) });
  }

  async issue(pending: Pending): Promise<void> {
    if (this.sending().has(pending.slot) || this.disposed) return;
    this.sending.update(x => new Set([...x, pending.slot]));
    const epoch = this.epoch;
    try {
      const operation = paperOperation(await pending.send());
      if (this.disposed || epoch !== this.epoch) return;
      this.operations.update(x => this.merge(x, [operation], item => item.operationId));
      this.uncertain.update(x => x.filter(item => item.key !== pending.key));
      this.notice.set('Anfrage dauerhaft angenommen. Das Ergebnis wird vom Backend ermittelt.');
      void this.refresh();
    } catch (error) {
      if (this.disposed || epoch !== this.epoch) return;
      const status = httpStatus(error);
      if (status === null || status === 0 || status >= 500) {
        this.uncertain.update(x => [...x.filter(item => item.key !== pending.key), pending]);
        this.notice.set('Antwort ungeklärt. Nur dieselbe Anfrage mit unveränderten Bedingungen erneut prüfen.');
      } else this.uncertain.update(x => x.filter(item => item.key !== pending.key));
      this.failed(error);
    } finally { this.sending.update(x => new Set([...x].filter(item => item !== pending.slot))); }
  }

  async verify(): Promise<void> {
    const status = this.snapshot()?.status;
    if (!status || !this.connected()) return;
    const body = Object.freeze({ credentialGeneration: status.credentialGeneration });
    await this.send('verify', 'Verbindung prüfen', key => firstValueFrom(this.api.verifyPaperConnection(key, body)));
  }

  async prepare(): Promise<void> {
    const data = this.snapshot();
    const status = data?.status;
    if (!status || !data.verificationOperationId || !this.connected()) return;
    if (!await this.confirm({ title: 'Paperledger vorbereiten', lines: ['Cash und bestehende Positionen werden aus bestätigten Brokerbelegen einmalig übernommen.', 'Dieser Schritt aktiviert keinen Handel.'] })) return;
    const body = Object.freeze({ credentialGeneration: status.credentialGeneration, accountGeneration: status.accountGeneration, verificationOperationId: data.verificationOperationId });
    await this.send('prepare', 'Ledger vorbereiten', key => firstValueFrom(this.api.preparePaperLedger(key, body)));
  }

  async saveProfile(): Promise<void> {
    const data = this.snapshot();
    if (!data || data.status.accountGeneration < 1 || data.status.credentialGeneration < 1 || !this.connected() || !this.profileBudget.valid || !this.feeConfirmed.value || this.busy('profile') || this.uncertainProfile()) return;
    const body: PaperTradingProfileUpdateRequest = Object.freeze<PaperTradingProfileUpdateRequest>({ riskBudget: this.profileBudget.value, currency: 'USD', strategy: this.strategy.value, credentialGeneration: data.status.credentialGeneration, accountGeneration: data.status.accountGeneration, expectedProfileVersion: data.profile.profileVersion, confirmation: 'SAVE_PAPER_PROFILE', feeAttestation: Object.freeze({ scope: 'alpaca-paper-direct-retail-us-equity-zero-v1', confirmed: true }) });
    await this.writeProfile({ key: crypto.randomUUID(), body });
  }

  async retryProfile(): Promise<void> { const attempt = this.uncertainProfile(); if (attempt) await this.writeProfile(attempt); }

  private async writeProfile(attempt: { key: string; body: PaperTradingProfileUpdateRequest }): Promise<void> {
    if (this.sending().has('profile') || this.disposed) return;
    this.sending.update(x => new Set([...x, 'profile']));
    const epoch = this.epoch;
    try { await firstValueFrom(this.api.updatePaperTradingProfile(attempt.key, attempt.body)); if (this.disposed || epoch !== this.epoch) return; this.uncertainProfile.set(null); this.feeConfirmed.reset(false); this.profileBudget.markAsPristine(); this.strategy.markAsPristine(); await this.refresh(); }
    catch (error) { if (!this.disposed && epoch === this.epoch) { const status = httpStatus(error); this.uncertainProfile.set(status === null || status === 0 || status >= 500 ? attempt : null); this.failed(error); } }
    finally { this.sending.update(x => new Set([...x].filter(key => key !== 'profile'))); }
  }

  private terms() {
    const data = this.snapshot();
    if (!data?.profile.configured || !data.profile.riskBudget || !this.connected() || data.profile.credentialGeneration !== data.status.credentialGeneration || data.profile.accountGeneration !== data.status.accountGeneration) return null;
    const profile = data.profile;
    return { credentialGeneration: data.status.credentialGeneration, accountGeneration: data.status.accountGeneration, policyVersion: profile.policyVersion, profileVersion: profile.profileVersion, limitPolicyVersion: profile.limitPolicyVersion, budgetVersion: profile.budgetVersion, riskBudget: profile.riskBudget!, currency: 'USD' as const };
  }

  private expiry(): string { return new Date(this.serverClock.at + performance.now() - this.serverClock.received + 20000).toISOString(); }

  private expired(expiresAt: string): boolean {
    if (Date.parse(expiresAt) > this.serverClock.at + performance.now() - this.serverClock.received) return false;
    this.notice.set('Bestätigung abgelaufen. Bedingungen erneut prüfen; die ursprüngliche Freigabe wird nicht verlängert.');
    return true;
  }

  fresh(observedAt: string, validUntil: string): boolean {
    this.clockTick();
    const now = this.serverClock.at + performance.now() - this.serverClock.received;
    return Number.isFinite(now) && Date.parse(observedAt) <= now && Date.parse(validUntil) > now;
  }

  private marketFresh(symbol: string): boolean {
    const market = this.snapshot()?.markets.find(item => item.symbol === symbol);
    return !!market?.quote && !!market.capability && !!market.session &&
      this.fresh(market.quote.observedAt, market.quote.validUntil) && this.fresh(market.capability.observedAt, market.capability.validUntil) && this.sessionOpen(market.session);
  }

  sessionOpen(session: { open: boolean; observedAt: string; validUntil: string; startsAt: string; endsAt: string }): boolean {
    const now = this.serverClock.at + performance.now() - this.serverClock.received;
    return session.open && this.fresh(session.observedAt, session.validUntil) && Date.parse(session.startsAt) <= now && now < Date.parse(session.endsAt);
  }

  async start(): Promise<void> {
    const terms = this.terms();
    if (!terms || this.entryPending() || !this.symbols.value.length || this.busy('start')) return;
    const strategy = this.snapshot()!.profile.strategy;
    if (!strategy) return;
    const body: PaperStartRequest = Object.freeze({ ...terms, symbols: [...this.symbols.value], strategy, expiresAt: this.expiry(), confirmation: 'START_PAPER' });
    if (!await this.confirm({ title: 'Paperbetrieb ausdrücklich freigeben', lines: [`Strategie: ${strategy} · Symbole: ${body.symbols.join(', ')}`, `Riskbudget: ${terms.riskBudget} USD`, `Freigabe gültig bis ${body.expiresAt}. Keine automatische Verlängerung.`] })) return;
    if (this.expired(body.expiresAt)) return;
    await this.send('start', 'Paperstart', key => firstValueFrom(this.api.startPaperWorkflow(key, body)));
  }

  async manual(): Promise<void> {
    const terms = this.terms();
    if (!terms || !this.canEnter() || !this.quantity.valid || !this.price.valid || this.busy('manual')) return;
    const body: PaperLimitOrderRequest = Object.freeze({ ...terms, symbol: this.symbol.value, side: this.side.value, quantity: this.quantity.value, limitPrice: this.price.value, orderType: 'LIMIT', timeInForce: 'DAY', session: 'REGULAR', expiresAt: this.expiry(), confirmation: 'PLACE_PAPER_LIMIT' });
    if (!await this.confirm({ title: 'Manuellen Paper-LIMIT bestätigen', lines: [`${body.side} ${body.quantity} ${body.symbol} zu ${body.side === 'BUY' ? 'höchstens' : 'mindestens'} ${body.limitPrice} USD.`, `LIMIT · DAY · reguläre US-Sitzung · gültig bis ${body.expiresAt}`, 'Unveränderte Menge und Preis durchlaufen dieselben Risk- und Executionprüfungen.'] })) return;
    if (this.expired(body.expiresAt)) return;
    await this.send('manual', `${body.side} ${body.quantity} ${body.symbol} · ${body.limitPrice} USD · bis ${body.expiresAt}`, key => firstValueFrom(this.api.createPaperLimitOrderRequest(key, body)));
  }

  async control(action: 'PAUSE' | 'STOP' | 'KILL'): Promise<void> {
    if (!this.authorized()) return;
    if (!await this.confirm({ title: action === 'KILL' ? 'Paper-Kill auslösen' : action === 'STOP' ? 'Paperbetrieb stoppen' : 'Paper-Einstiege pausieren', lines: [action === 'KILL' ? 'HALTED bleibt dauerhaft. Eigene offene Orders werden zur Stornierung vorgemerkt.' : 'Neue Einstiege werden gesperrt; bestehende Verpflichtungen werden weiter abgeglichen.', 'Es erfolgt keine automatische Liquidation.'] })) return;
    const body = Object.freeze({ action });
    await this.send('control:' + action, action, key => firstValueFrom(this.api.controlPaperWorkflow(key, body)));
  }

  async cancel(order: PaperOrder): Promise<void> {
    if (!this.authorized() || this.cancelBusy(order.orderId)) return;
    const generation = this.snapshot()?.status.accountGeneration;
    if (!generation || !await this.confirm({ title: 'Eigene Paperorder stornieren', lines: [`Order ${order.orderId}`, 'Die Anforderung garantiert keine Stornierung. Fills können weiterhin eintreffen; Reservationen bleiben bis zum belegten Abschluss bestehen.'] })) return;
    const body = Object.freeze({ accountGeneration: generation, confirmation: 'CANCEL_PAPER_ORDER' as const });
    await this.send('cancel:' + order.orderId, 'Order stornieren', key => firstValueFrom(this.api.cancelPaperOrder(key, order.orderId, body)));
  }

  async loadTrace(id = this.traceId.value): Promise<void> {
    if (!/^[0-9a-f]{32}$/.test(id)) return;
    this.traceId.setValue(id);
    const epoch = this.epoch;
    try { const result = paperTrace(await firstValueFrom(this.api.getPaperTrace(id))); if (!this.disposed && epoch === this.epoch) this.trace.set(result); }
    catch (error) { this.failed(error); }
  }
}
