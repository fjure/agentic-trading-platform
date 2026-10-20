import { ChangeDetectionStrategy, Component, DestroyRef, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatDialog, MatDialogModule, MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { DemoService } from '../api/generated/control-api/api/demo.service';
import { SetupService } from '../api/generated/control-api/api/setup.service';
import { DemoRun } from '../api/generated/control-api/model/demoRun';
import { DemoRunCreateRequest } from '../api/generated/control-api/model/demoRunCreateRequest';
import { DemoStrategy } from '../api/generated/control-api/model/demoStrategy';
import { DemoHistoryResource } from '../api/generated/control-api/model/demoHistoryResource';
import { DemoRunControlRequest } from '../api/generated/control-api/model/demoRunControlRequest';
import { SupervisorSnapshot } from '../api/generated/control-api/model/supervisorSnapshot';
import { IntegrationSaveResult } from '../api/generated/control-api/model/integrationSaveResult';
import { IntegrationStatuses } from '../api/generated/control-api/model/integrationStatuses';
import { TradingProfile } from '../api/generated/control-api/model/tradingProfile';

type Provider = 'alpaca' | 'jev';
type DetailRow = { id: string; title: string; status: string; traceId: string; fields: { label: string; value: string }[] };
type PageView = { loading: boolean; error: string; items: DetailRow[] };
const EMPTY_PAGE = (): PageView => ({ loading: false, error: '', items: [] });

@Component({
  selector: 'atl-credential-dialog',
  standalone: true,
  imports: [ReactiveFormsModule, MatButtonModule, MatDialogModule, MatFormFieldModule, MatInputModule],
  template: `
    <h2 mat-dialog-title>{{ data.provider === 'alpaca' ? 'Alpaca Paper einrichten' : 'Jev einrichten' }}</h2>
    <mat-dialog-content>
      <p>Schlüssel werden nur zum Speichern übertragen. Es wird keine Verbindung geprüft und kein Handel aktiviert.</p>
      @if (data.provider === 'alpaca') {
        <mat-form-field appearance="outline"><mat-label>API Key</mat-label><input matInput type="password" autocomplete="off" spellcheck="false" [formControl]="apiKey" /></mat-form-field>
        <mat-form-field appearance="outline"><mat-label>API Secret</mat-label><input matInput type="password" autocomplete="off" spellcheck="false" [formControl]="apiSecret" /></mat-form-field>
        <mat-form-field appearance="outline"><mat-label>Erwartete Account UUID</mat-label><input matInput autocomplete="off" spellcheck="false" [formControl]="accountId" /></mat-form-field>
      } @else {
        <mat-form-field appearance="outline"><mat-label>Jev API Key</mat-label><input matInput type="password" autocomplete="off" spellcheck="false" [formControl]="apiKey" /></mat-form-field>
      }
      @if (error()) { <p role="alert">Speichern fehlgeschlagen. Eingabe prüfen und erneut versuchen.</p> }
      @if (saving()) { <p role="status">Wird sicher gespeichert…</p> }
    </mat-dialog-content>
    <mat-dialog-actions align="end"><button mat-button type="button" [disabled]="saving()" (click)="cancel()">Abbrechen</button><button mat-flat-button type="button" [disabled]="saving() || !valid()" (click)="submit()">Speichern</button></mat-dialog-actions>`,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CredentialDialogComponent implements OnDestroy {
  readonly data = inject<{ provider: Provider }>(MAT_DIALOG_DATA);
  private readonly dialogRef = inject(MatDialogRef<CredentialDialogComponent, IntegrationSaveResult | undefined>);
  private readonly setup = inject(SetupService);
  private readonly destroyRef = inject(DestroyRef);
  readonly apiKey = new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.maxLength(512)] });
  readonly apiSecret = new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.maxLength(512)] });
  readonly accountId = new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)] });
  readonly saving = signal(false);
  readonly error = signal(false);

  valid(): boolean {
    return this.apiKey.valid && (this.data.provider === 'jev' || (this.apiSecret.valid && this.accountId.valid));
  }

  submit(): void {
    if (!this.valid() || this.saving()) return;
    // Copy only to a one-shot request, then clear every control before HTTP starts.
    const key = this.apiKey.value;
    const secret = this.apiSecret.value;
    const account = this.accountId.value;
    this.clear();
    this.saving.set(true);
    this.error.set(false);
    const request$ = this.data.provider === 'alpaca'
      ? this.setup.saveAlpacaIntegration(crypto.randomUUID(), { apiKey: key, apiSecret: secret, expectedAccountId: account, paperOnly: true })
      : this.setup.saveJevIntegration(crypto.randomUUID(), { apiKey: key });
    request$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: result => { this.saving.set(false); this.dialogRef.close(result); },
      error: () => { this.saving.set(false); this.error.set(true); },
    });
  }

  cancel(): void { this.clear(); this.dialogRef.close(); }

  private clear(): void { this.apiKey.reset(''); this.apiSecret.reset(''); this.accountId.reset(''); }

  ngOnDestroy(): void { this.clear(); }
}

@Component({
  selector: 'atl-demo-setup',
  standalone: true,
  imports: [ReactiveFormsModule, MatButtonModule, MatCardModule, MatDialogModule, MatFormFieldModule, MatInputModule, MatSelectModule, MatProgressSpinnerModule],
  templateUrl: './demo-setup.component.html',
  styleUrl: './demo-setup.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DemoSetupComponent implements OnInit, OnDestroy {
  private readonly demo = inject(DemoService);
  private readonly setup = inject(SetupService);
  private readonly dialog = inject(MatDialog);
  private readonly destroyRef = inject(DestroyRef);
  readonly budget = new FormControl('100', { nonNullable: true, validators: [Validators.required, Validators.pattern(/^(?=.*[1-9])(?:(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,18})?|1000000(?:\.0{1,18})?)$/)] });
  readonly strategy = new FormControl<DemoStrategy>(DemoStrategy.MockNeutral, { nonNullable: true });
  readonly profileBudget = new FormControl('100', { nonNullable: true, validators: [Validators.required, Validators.pattern(/^(?=.*[1-9])(?:(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,18})?|1000000(?:\.0{1,18})?)$/)] });
  readonly profileStrategy = new FormControl<DemoStrategy>(DemoStrategy.MockNeutral, { nonNullable: true });
  readonly run = signal<DemoRun | null>(null);
  readonly runs = signal<DemoRun[]>([]);
  readonly snapshot = signal<SupervisorSnapshot | null>(null);
  readonly pages = signal<Record<string, PageView>>({});
  readonly integrations = signal<IntegrationStatuses | null>(null);
  readonly profile = signal<TradingProfile | null>(null);
  readonly loading = signal(false);
  readonly message = signal('');
  readonly error = signal('');
  private polling?: ReturnType<typeof setInterval>;
  private disposed = false;
  private pollBusy = false;
  readonly requiredHistory: DemoHistoryResource[] = [DemoHistoryResource.Orders, DemoHistoryResource.Decisions, DemoHistoryResource.RiskDecisions, DemoHistoryResource.Audit];

  ngOnInit(): void {
    this.loadSetup();
    this.demo.listDemoRuns().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: result => { this.runs.set(result.items); const latest = result.items[0]; if (latest) { this.run.set(latest); this.resumeRun(latest); } },
      error: () => this.error.set('Demo-Läufe konnten nicht geladen werden.'),
    });
  }

  startDemo(): void {
    if (!this.budget.valid || this.loading()) return;
    this.loading.set(true); this.error.set(''); this.message.set('');
    const body: DemoRunCreateRequest = { initialBudget: this.budget.value, currency: DemoRunCreateRequest.CurrencyEnum.Usd, strategy: this.strategy.value };
    this.demo.createDemoRun(crypto.randomUUID(), body).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: run => { this.loading.set(false); this.run.set(run); this.runs.update(items => [run, ...items.filter(x => x.runId !== run.runId)]); this.resumeRun(run); },
      error: () => { this.loading.set(false); this.error.set('Der Demolauf konnte nicht gestartet werden.'); },
    });
  }

  openCredentials(provider: Provider): void {
    const ref = this.dialog.open(CredentialDialogComponent, { data: { provider }, ariaLabel: provider === 'alpaca' ? 'Alpaca Paper einrichten' : 'Jev einrichten', width: 'min(92vw, 460px)', disableClose: false });
    ref.afterClosed().pipe(takeUntilDestroyed(this.destroyRef)).subscribe(result => {
      if (result?.status === 'CONFIGURED') { this.message.set('Gespeichert – Verbindung noch nicht geprüft; Aktivierung separat.'); this.loadSetup(); }
    });
  }

  saveProfile(): void {
    if (!this.profileBudget.valid) return;
    this.setup.updateTradingProfile(crypto.randomUUID(), { budget: this.profileBudget.value, currency: 'USD', strategy: this.profileStrategy.value })
      .pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: profile => { this.profile.set(profile); this.message.set('Paper-Budget gespeichert. Aktivierung erfolgt separat.'); }, error: () => this.error.set('Das Paper-Budget konnte nicht gespeichert werden.') });
  }

  controlRun(action: 'pause' | 'kill'): void {
    const current = this.run();
    if (!current || current.status !== 'RUNNING') return;
    const body: DemoRunControlRequest = { reason: action === 'pause' ? 'Operator paused demo run' : 'Operator stopped demo run' };
    this.demo.controlDemoRun(crypto.randomUUID(), current.runId, action, body).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: result => { this.run.update(value => value ? { ...value, status: result.status === 'FAILED' ? 'FAILED' : 'PAUSED' } : value); if (this.polling) clearInterval(this.polling); this.polling = undefined; this.loadSnapshot(current.runId); this.requiredHistory.forEach(resource => this.loadHistory(current.runId, resource)); },
      error: () => this.error.set('Demo-Kontrollaktion konnte nicht bestätigt werden.'),
    });
  }

  rows(resource: DemoHistoryResource): DetailRow[] { return this.pages()[resource]?.items ?? []; }
  resumeSelection(run: DemoRun): void { this.resumeRun(run); }
  historyTitle(resource: DemoHistoryResource): string { return ({ 'orders': 'Orders', 'decisions': 'Entscheidungen', 'risk-decisions': 'Risk', 'audit': 'Audit', 'market-states': 'Portfolio', 'proposals': 'Vorschläge', 'runs': 'Runs', 'costs': 'Kosten' } as Record<string, string>)[resource] ?? resource; }
  private loadSetup(): void {
    this.setup.getIntegrationStatuses().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: value => this.integrations.set(value), error: () => this.error.set('Einrichtungsstatus ist nicht verfügbar.') });
    this.setup.getTradingProfile().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: value => { this.profile.set(value); this.profileBudget.setValue(value.budget); this.profileStrategy.setValue(value.strategy); }, error: () => { /* Keep defaults until a profile is available. */ } });
  }

  private resumeRun(run: DemoRun): void {
    this.pollRun(run.runId);
    this.loadSnapshot(run.runId);
    this.requiredHistory.forEach(resource => this.loadHistory(run.runId, resource));
    if (run.status === 'RUNNING') this.polling = setInterval(() => this.pollRun(run.runId), 1500);
  }

  private pollRun(runId: string): void {
    if (this.disposed || this.pollBusy) return;
    this.pollBusy = true;
    this.demo.getDemoRun(runId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: value => { this.pollBusy = false; this.run.set(value); if (value.status !== 'RUNNING' && this.polling) { clearInterval(this.polling); this.polling = undefined; this.loadSnapshot(runId); this.requiredHistory.forEach(resource => this.loadHistory(runId, resource)); } },
      error: () => { this.pollBusy = false; this.error.set('Demostatus ist vorübergehend nicht verfügbar.'); },
    });
  }

  private loadSnapshot(runId: string): void {
    this.demo.getDemoSnapshot(runId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: value => this.snapshot.set(value), error: () => this.error.set('Demo-Ergebnis ist noch nicht verfügbar.') });
  }

  private loadHistory(runId: string, resource: DemoHistoryResource): void {
    this.pages.update(pages => ({ ...pages, [resource]: { ...EMPTY_PAGE(), loading: true } }));
    this.demo.getDemoHistory(runId, resource, undefined, undefined, 50).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: page => {
        const items = (page as unknown as { items?: unknown[] }).items ?? [];
        const rows = items.map((item, index) => this.toHistoryRow(resource, item, index));
        this.pages.update(pages => ({ ...pages, [resource]: { loading: false, error: '', items: rows } }));
      },
      error: () => this.pages.update(pages => ({ ...pages, [resource]: { loading: false, error: 'Historie nicht verfügbar.', items: [] } })),
    });
  }

  private toHistoryRow(resource: DemoHistoryResource, item: unknown, index: number): DetailRow {
    const paths: Record<string, [string, string][]> = {
      orders: [['Status', 'status'], ['Instrument', 'instrument'], ['Gefüllt', 'filledQuantity'], ['Füllpreis', 'averageFillPrice'], ['Trace', 'traceId']],
      decisions: [['Entscheidung', 'choice'], ['Grund', 'reasonCode'], ['Trace', 'traceId']],
      'risk-decisions': [['Freigegeben', 'approved'], ['Grund', 'reasonCode'], ['Trace', 'traceId']],
      audit: [['Aktion', 'action'], ['Ergebnis', 'outcome'], ['Trace', 'traceId']],
      'market-states': [['Cash USD', 'portfolio.cash'], ['Position', 'position.quantity'], ['Equity', 'portfolio.equity'], ['Trace', 'traceId']],
    };
    const fields = (paths[resource] ?? [['Trace', 'traceId']]).map(([label, path]) => ({ label, value: this.readPath(item, path) }));
    return { id: this.readPath(item, 'id') || `${resource}-${index}`, title: this.readPath(item, resource === 'orders' ? 'instrument' : resource === 'audit' ? 'action' : 'choice') || this.historyTitle(resource), status: this.readPath(item, 'status') || this.readPath(item, 'reasonCode') || 'Erfasst', traceId: this.readPath(item, 'traceId'), fields };
  }

  private readPath(item: unknown, path: string): string {
    let value: unknown = item;
    for (const part of path.split('.')) { if (!value || typeof value !== 'object') return 'nicht verfügbar'; value = (value as Record<string, unknown>)[part]; }
    return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : 'nicht verfügbar';
  }

  ngOnDestroy(): void { this.disposed = true; if (this.polling) clearInterval(this.polling); this.dialog.closeAll(); }
}
