import { ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit, inject, output, signal } from '@angular/core';
import { JsonPipe } from '@angular/common';
import { FormArray, FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatDialog, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatSelectModule } from '@angular/material/select';
import { Observable, Subject, firstValueFrom, takeUntil } from 'rxjs';
import { BacktestingService } from '../api/generated/control-api/api/backtesting.service';
import type { BacktestAssessment, BacktestAssessmentRequest, BacktestReliabilityReportPage, BacktestReliabilityReportV1, BacktestRequest, BacktestRun, BacktestRunDetail, BacktestStrategyCatalog, BacktestStrategyId, BacktestSummaryPage, BacktestEquityPage, BacktestFillsPage } from '../api/generated/control-api/model/models';
import { httpStatus, safeError } from '../state/supervisor-api';
import { ReadinessComponent } from './readiness.component';
import { BacktestContextComponent } from './backtest-context.component';
import { BacktestReportDialogComponent } from './backtest-report-dialog.component';

const ACTIVE = new Set(['ACCEPTED', 'RUNNING']);
const UTC_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const control = (value: string) => new FormControl(value, { nonNullable: true, validators: [Validators.required] });
const time = (value: string) => new FormControl(value, { nonNullable: true, validators: [Validators.required, Validators.pattern(UTC_TIME)] });
const foldForm = () => new FormGroup({
  trainingStart: time(new Date(Date.now() - 3 * 86400000).toISOString()),
  trainingEnd: time(new Date(Date.now() - 2 * 86400000).toISOString()),
  testStart: time(new Date(Date.now() - 86400000).toISOString()),
  testEnd: time(new Date(Date.now() - 3600000).toISOString()),
});
// Exact string validation only; monetary calculations stay in the server domain.
function decimalBound(value: string, max: bigint, positive = false): boolean {
  if (!/^(0|[1-9]\d{0,9})(?:\.\d{1,8})?$/.test(value)) return false;
  const [whole, fraction = ''] = value.split('.');
  const scaled = BigInt(whole) * 100000000n + BigInt(fraction.padEnd(8, '0'));
  return scaled <= max * 100000000n && (!positive || scaled > 0n);
}

@Component({
  selector: 'atl-backtesting',
  imports: [JsonPipe, ReactiveFormsModule, MatButtonModule, MatCardModule, MatFormFieldModule, MatInputModule, MatSelectModule, MatDialogModule, ReadinessComponent, BacktestContextComponent],
  templateUrl: './backtesting.component.html',
  styleUrl: './backtesting.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BacktestingComponent implements OnInit, OnDestroy {
  private readonly api = inject(BacktestingService);
  private readonly dialogs = inject(MatDialog);
  private readonly change = inject(ChangeDetectorRef);
  readonly assessment = signal<BacktestAssessment | null>(null);
  readonly previewBound = signal(false);
  readonly reports = signal<Record<string, BacktestReliabilityReportV1>>({});
  readonly warnings = signal<Record<string, BacktestReliabilityReportPage['currentWarnings']>>({});
  private previewGeneration = 0;
  private previewPayload: string | null = null;
  private pendingPreview: { payload: string; key: string } | null = null;
  private readonly overviewQueue: { id: string; resolve: (loaded: boolean) => void }[] = [];
  private readonly overviewReads = new Map<string, Promise<boolean>>();
  private overviewRunning = false;
  private readonly assessmentStop = new Subject<void>();
  private reportDialog?: MatDialogRef<BacktestReportDialogComponent>;
  readonly sessionLost = output<void>();
  readonly catalog = signal<BacktestStrategyCatalog | null>(null);
  readonly runs = signal<BacktestRun[]>([]);
  readonly nextRuns = signal<string | null>(null);
  readonly detail = signal<BacktestRunDetail | null>(null);
  readonly summary = signal<BacktestSummaryPage | null>(null);
  readonly equity = signal<BacktestEquityPage | null>(null);
  readonly fills = signal<BacktestFillsPage | null>(null);
  readonly selectedFold = signal(0);
  readonly error = signal('');
  readonly sending = signal(false);
  readonly loading = signal(false);
  readonly authorized = signal(true);
  readonly form = new FormGroup({
    researchMode: new FormControl<'STRICT_PIT' | 'RETROSPECTIVE_EXPLORATORY'>('STRICT_PIT', { nonNullable: true }),
    instrumentId: control(''), strategyId: new FormControl<BacktestStrategyId>('buy-hold-v1', { nonNullable: true }),
    startCash: control('10000'), feeUsd: control('0'), slippageBps: control('0'),
    fast: control('5'), slow: control('20'), folds: new FormArray([foldForm()]),
  });
  private readonly stop = new Subject<void>();
  private disposed = false;
  private selection = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private pending: { payload: string; key: string } | null = null;
  private readonly visibility = () => { this.cancelPoll(); this.schedule(); };

  ngOnInit(): void {
    document.addEventListener('visibilitychange', this.visibility);
    window.addEventListener('offline', this.visibility); window.addEventListener('online', this.visibility);
    this.form.valueChanges.pipe(takeUntil(this.stop)).subscribe(() => {
      this.previewGeneration++; this.previewBound.set(false); this.previewPayload = null;
      this.assessmentStop.next(); this.assessment.set(null);
      const url = new URL(location.href); url.searchParams.delete('backtestAssessment'); history.replaceState(null, '', url);
      this.cancelPoll(); this.schedule();
      this.pending = null; this.pendingPreview = null; this.change.markForCheck();
    });
    void this.initialize();
  }
  ngOnDestroy(): void {
    this.reportDialog?.close(); this.disposed = true; this.selection++; this.cancelPoll(); this.stop.next(); this.stop.complete();
    this.assessmentStop.next(); this.assessmentStop.complete();
    document.removeEventListener('visibilitychange', this.visibility);
    window.removeEventListener('offline', this.visibility); window.removeEventListener('online', this.visibility);
  }
  private alive(ticket = this.selection): boolean { return !this.disposed && this.authorized() && ticket === this.selection; }
  private read<T>(request: Observable<T>): Promise<T> { return firstValueFrom(request.pipe(takeUntil(this.stop))); }
  sessionExpired(): void { this.failed({ status: 401 }); }
  private failed(error: unknown): void {
    if (this.disposed || !this.authorized()) return;
    this.error.set(safeError(error));
    if (httpStatus(error) === 401) {
      this.authorized.set(false); this.selection++; this.cancelPoll(); this.stop.next();
      this.catalog.set(null); this.runs.set([]); this.detail.set(null); this.clearResults();
      this.pending = null; this.pendingPreview = null; this.previewGeneration++; this.assessment.set(null); this.previewBound.set(false); this.reports.set({}); this.warnings.set({}); this.reportDialog?.close(); this.sessionLost.emit();
    }
  }
  private async initialize(): Promise<void> {
    const params = new URL(location.href).searchParams;
    const previewTicket = this.previewGeneration;
    try {
      const catalog = await this.read(this.api.listBacktestStrategies());
      if (!this.alive()) return;
      this.catalog.set(catalog); this.form.controls.instrumentId.setValue(catalog.instruments[0]?.instrumentId ?? '', { emitEvent: false });
      await this.refreshRuns();
      const selected = new URL(location.href).searchParams.get('backtestRun');
      if (selected && /^[0-9a-f-]{36}$/.test(selected) && this.alive()) await this.select(selected);
      const assessmentId = params.get('backtestAssessment');
      if (assessmentId && /^[0-9a-f-]{36}$/.test(assessmentId) && this.alive()
        && previewTicket === this.previewGeneration) await this.loadAssessment(assessmentId, true);
      const reportId = params.get('backtestReport');
      if (reportId && /^[0-9a-f-]{36}$/.test(reportId) && this.alive()) this.openReport(reportId);
    } catch (error) { this.failed(error); }
  }
  addFold(): void { if (this.form.controls.folds.length < 12) this.form.controls.folds.push(foldForm()); }
  removeFold(index: number): void { if (this.form.controls.folds.length > 1) this.form.controls.folds.removeAt(index); }
  get validRequest(): boolean {
    if (this.form.invalid || !this.authorized() || !this.catalog()) return false;
    const value = this.form.getRawValue();
    if (!this.catalog()!.instruments.some(i => i.instrumentId === value.instrumentId)
      || !this.catalog()!.items.some(i => i.strategyId === value.strategyId && i.version === '1')) return false;
    if (!decimalBound(value.startCash, 1000000000n, true) || !decimalBound(value.feeUsd, 1000n)
      || !decimalBound(value.slippageBps, 100n)) return false;
    if (value.strategyId === 'sma-cross-v1' && (!/^\d{1,3}$/.test(value.fast) || !/^\d{1,3}$/.test(value.slow)
      || +value.fast < 2 || +value.fast >= +value.slow || +value.slow > 100)) return false;
    let previousEnd = -Infinity; let first = Infinity; let last = -Infinity;
    for (const fold of value.folds) {
      const [a, b, c, d] = [fold.trainingStart, fold.trainingEnd, fold.testStart, fold.testEnd].map(Date.parse);
      if (![a, b, c, d].every(Number.isFinite) || !(a < b && b <= c && c < d)
        || c < previousEnd || d > Date.now()) return false;
      previousEnd = d; first = Math.min(first, a); last = Math.max(last, d);
    }
    return last - first <= 366 * 86400000;
  }
  get diagnosticRequest(): BacktestRequest | null {
    return !this.retrospective && this.validRequest ? this.experimentRequest() : null;
  }
  private experimentRequest(): BacktestRequest {
    const value = this.form.getRawValue();
    return {
      instrumentId: value.instrumentId, strategyId: value.strategyId, version: '1',
      parameters: value.strategyId === 'sma-cross-v1' ? { fast: +value.fast, slow: +value.slow } : {},
      startCash: value.startCash, feeUsd: value.feeUsd, slippageBps: value.slippageBps,
      quantityStep: '0.000001', folds: value.folds,
    };
  }
  get retrospective(): boolean { return this.form.controls.researchMode.value === 'RETROSPECTIVE_EXPLORATORY'; }
  private assessmentRequest(): BacktestAssessmentRequest {
    return { ...this.experimentRequest(), researchMode: 'RETROSPECTIVE_EXPLORATORY', selectionPolicyVersion: 'retrospective-v1' };
  }
  get canRunRetrospective(): boolean {
    const assessment = this.assessment();
    const report = this.reportFor(assessment?.reliabilityReportId);
    return this.retrospective && this.validRequest && this.previewBound() && !!assessment?.assessmentHash
      && assessment.status === 'SUCCEEDED' && assessment.eligibility === 'EXPLORATORY_WITH_LIMITATIONS'
      && !!assessment.reliabilityReportId && !!report && report.phase === 'PREVIEW'
      && report.assessmentId === assessment.assessmentId && report.protocolHash === assessment.inputHash
      && report.overallStatus === 'EXPLORATORY_WITH_LIMITATIONS'
      && Object.hasOwn(this.warnings(), assessment.reliabilityReportId)
      && !this.warnings()[assessment.reliabilityReportId]?.some(warning => warning.blocksNewExecution)
      && this.previewPayload === JSON.stringify(this.assessmentRequest());
  }
  link(key: string, id: string): string { const url = new URL(location.href); url.searchParams.set(key, id); return url.pathname + url.search; }
  private remember(key: string, id: string): void { history.replaceState(null, '', this.link(key, id)); }
  reportFor(id: string | null | undefined): BacktestReliabilityReportV1 | null { return id ? this.reports()[id] ?? null : null; }
  private cachePage(page: BacktestReliabilityReportPage): void {
    const entries = Object.entries(this.reports()).filter(([id]) => id !== page.report.reportId).slice(-99);
    this.reports.set({ ...Object.fromEntries(entries), [page.report.reportId]: page.report });
    const warnings = Object.fromEntries(Object.entries(this.warnings()).filter(([id]) => entries.some(([key]) => key === id)));
    this.warnings.set({ ...warnings, [page.report.reportId]: page.currentWarnings });
  }
  private loadOverview(id: string, fresh = false): Promise<boolean> {
    if (!this.alive()) return Promise.resolve(false);
    if (!fresh && this.reports()[id]) return Promise.resolve(true);
    const existing = this.overviewReads.get(id);
    if (!fresh && existing) return existing;
    if (fresh) {
      const warnings = { ...this.warnings() }; delete warnings[id]; this.warnings.set(warnings);
    }
    const pending = new Promise<boolean>(resolve => {
      const entry = { id, resolve };
      if (fresh) this.overviewQueue.unshift(entry); else this.overviewQueue.push(entry);
    });
    if (!fresh) this.overviewReads.set(id, pending);
    void this.drainOverviews();
    return pending;
  }
  private async drainOverviews(): Promise<void> {
    if (this.overviewRunning) return;
    this.overviewRunning = true;
    try {
      while (this.overviewQueue.length) {
        const entry = this.overviewQueue.shift()!;
        let loaded = false;
        try {
          if (this.alive()) {
            const page = await this.read(this.api.getBacktestReliabilityReport(entry.id, 1));
            if (this.alive()) {
              const previous = this.reports()[entry.id];
              if (page.report.reportId !== entry.id || (previous && previous.reportHash !== page.report.reportHash)) throw new Error('INVALID_REPORT_ID');
              this.cachePage(page); loaded = true;
            }
          }
        } catch (error) { this.failed(error); }
        finally { this.overviewReads.delete(entry.id); entry.resolve(loaded); }
      }
    } finally { this.overviewRunning = false; }
  }
  openReport(id: string): void {
    if (!this.alive()) return;
    this.reportDialog?.close(); this.remember('backtestReport', id);
    const ref = this.dialogs.open(BacktestReportDialogComponent, {
      data: { reportId: id }, width: '1000px', maxWidth: '96vw', ariaLabel: 'Qualitäts- und Grenzenreport', restoreFocus: true,
      enterAnimationDuration: 0, exitAnimationDuration: 0,
    });
    this.reportDialog = ref;
    ref.componentInstance.sessionLost.subscribe(() => this.failed({ status: 401 }));
    ref.componentInstance.pageLoaded.subscribe(page => { if (this.alive()) this.cachePage(page); });
    ref.afterClosed().pipe(takeUntil(this.stop)).subscribe(() => {
      if (this.reportDialog !== ref) return;
      this.reportDialog = undefined;
      const url = new URL(location.href); url.searchParams.delete('backtestReport'); history.replaceState(null, '', url);
    });
  }
  async preview(): Promise<void> {
    if (!this.alive() || !this.retrospective || !this.validRequest || this.sending()) return;
    const request = this.assessmentRequest(), payload = JSON.stringify(request), ticket = this.previewGeneration;
    if (this.pendingPreview?.payload !== payload) this.pendingPreview = { payload, key: crypto.randomUUID() };
    this.sending.set(true); this.error.set(''); this.previewBound.set(false);
    try {
      const assessment = await this.read(this.api.createBacktestAssessment(this.pendingPreview.key, request));
      if (!this.alive() || ticket !== this.previewGeneration) return;
      this.pendingPreview = null; this.previewPayload = payload; this.previewBound.set(true);
      this.assessment.set(assessment); this.remember('backtestAssessment', assessment.assessmentId);
      if (assessment.reliabilityReportId) await this.loadOverview(assessment.reliabilityReportId);
      this.schedule();
    } catch (error) { this.failed(error); }
    finally { if (this.alive()) this.sending.set(false); }
  }
  private async loadAssessment(id: string, restore = false): Promise<void> {
    const ticket = this.previewGeneration;
    try {
      const assessment = await firstValueFrom(this.api.getBacktestAssessment(id).pipe(takeUntil(this.stop), takeUntil(this.assessmentStop)));
      if (!this.alive() || ticket !== this.previewGeneration) return;
      if (assessment.assessmentId !== id) throw new Error('INVALID_ASSESSMENT_ID');
      if (restore) {
        const request = assessment.request;
        this.form.patchValue({
          researchMode: 'RETROSPECTIVE_EXPLORATORY', instrumentId: request.instrumentId, strategyId: request.strategyId,
          startCash: request.startCash, feeUsd: request.feeUsd, slippageBps: request.slippageBps,
          fast: 'fast' in request.parameters ? String(request.parameters.fast) : '5',
          slow: 'slow' in request.parameters ? String(request.parameters.slow) : '20',
        }, { emitEvent: false });
        this.form.controls.folds.clear({ emitEvent: false });
        for (const fold of request.folds) {
          const group = foldForm(); group.setValue(fold, { emitEvent: false });
          this.form.controls.folds.push(group, { emitEvent: false });
        }
        this.previewPayload = JSON.stringify(this.assessmentRequest()); this.previewBound.set(true);
        this.change.markForCheck();
      }
      this.assessment.set(assessment);
      if (assessment.reliabilityReportId) await this.loadOverview(assessment.reliabilityReportId, restore);
      this.schedule();
    } catch (error) { if (ticket === this.previewGeneration || httpStatus(error) === 401) this.failed(error); }
  }
  async submit(): Promise<void> {
    if (!this.alive() || !this.validRequest || this.sending()) return;
    if (this.retrospective && !this.canRunRetrospective) return;
    const bound = this.assessment();
    const request: BacktestRequest = this.retrospective && bound ? {
      ...bound.request, assessmentId: bound.assessmentId, assessmentHash: bound.assessmentHash!,
    } : this.experimentRequest();
    const payload = JSON.stringify(request);
    const ticket = this.previewGeneration;
    this.sending.set(true); this.error.set('');
    try {
      if (this.retrospective) {
        if (!bound?.reliabilityReportId || !await this.loadOverview(bound.reliabilityReportId, true)
          || ticket !== this.previewGeneration || !this.alive() || !this.canRunRetrospective) return;
      }
      if (this.pending?.payload !== payload) this.pending = { payload, key: crypto.randomUUID() };
      const run = await this.read(this.api.createBacktestRun(this.pending.key, request));
      if (!this.alive()) return;
      this.pending = null; await this.refreshRuns(); await this.select(run.runId);
    } catch (error) { this.failed(error); }
    finally { if (this.alive()) this.sending.set(false); }
  }
  async refreshRuns(append = false): Promise<void> {
    if (!this.alive()) return;
    const cursor = append ? this.nextRuns() ?? undefined : undefined;
    if (append && !cursor) return;
    try {
      const result = await this.read(this.api.listBacktestRuns(50, cursor));
      if (!this.alive()) return;
      this.runs.set(append ? [...this.runs(), ...result.items] : result.items);
      this.nextRuns.set(result.nextCursor);
      for (const run of result.items) if (run.reliabilityReportId) void this.loadOverview(run.reliabilityReportId);
      this.schedule();
    } catch (error) { this.failed(error); }
  }
  private clearResults(): void { this.summary.set(null); this.equity.set(null); this.fills.set(null); }
  async select(runId: string): Promise<void> {
    if (!this.alive()) return;
    const ticket = ++this.selection; this.cancelPoll(); this.loading.set(true); this.error.set('');
    this.detail.set(null); this.clearResults(); this.selectedFold.set(0);
    const url = new URL(location.href); url.searchParams.set('backtestRun', runId); history.replaceState(null, '', url);
    await this.loadDetail(runId, ticket);
  }
  private async loadDetail(runId: string, ticket: number): Promise<void> {
    try {
      const detail = await this.read(this.api.getBacktestRun(runId));
      if (!this.alive(ticket)) return;
      this.detail.set(detail);
      if (detail.run.reliabilityReportId) await this.loadOverview(detail.run.reliabilityReportId);
      await this.loadResults('summary'); await this.loadResults('equity'); await this.loadResults('fills');
    } catch (error) { if (this.alive(ticket) || httpStatus(error) === 401) this.failed(error); }
    finally { if (this.alive(ticket)) { this.loading.set(false); this.schedule(); } }
  }
  async loadResults(kind: 'summary' | 'equity' | 'fills', append = false): Promise<void> {
    const selected = this.detail(); const ticket = this.selection;
    if (!selected || !this.alive(ticket)) return;
    const current = kind === 'summary' ? this.summary() : kind === 'equity' ? this.equity() : this.fills();
    const cursor = append && typeof current?.nextCursor === 'string' ? current.nextCursor : undefined;
    if (append && !cursor) return;
    try {
      const result = await this.read(this.api.getBacktestResults(selected.run.runId, 200, cursor, kind));
      if (!this.alive(ticket)) return;
      if (result.resultType !== kind) throw new Error('INVALID_BACKTEST_RESULT');
      if (kind === 'summary') {
        const page = result as BacktestSummaryPage;
        this.summary.set(append && this.summary() ? { ...page, items: [...this.summary()!.items, ...page.items], retrospectiveItems: [...(this.summary()!.retrospectiveItems ?? []), ...(page.retrospectiveItems ?? [])] } : page);
      } else if (kind === 'equity') {
        const page = result as BacktestEquityPage;
        this.equity.set(append && this.equity() ? { ...page, items: [...this.equity()!.items, ...page.items] } : page);
      } else {
        const page = result as BacktestFillsPage;
        this.fills.set(append && this.fills() ? { ...page, items: [...this.fills()!.items, ...page.items], retrospectiveItems: [...(this.fills()!.retrospectiveItems ?? []), ...(page.retrospectiveItems ?? [])] } : page);
      }
    } catch (error) { if (this.alive(ticket) || httpStatus(error) === 401) this.failed(error); }
  }
  foldRows() { const page = this.summary(); return page?.researchMode === 'RETROSPECTIVE_EXPLORATORY' ? page.retrospectiveItems ?? [] : page?.items ?? []; }
  fillRows() { const page = this.fills(); return page?.researchMode === 'RETROSPECTIVE_EXPLORATORY' ? page.retrospectiveItems ?? [] : page?.items ?? []; }
  fillCutoff(fill: { observationCutoff?: string; simulatedMarketCutoff?: string }): string { return fill.simulatedMarketCutoff ?? fill.observationCutoff ?? ''; }
  curve(): string {
    const rows = this.equity()?.items.filter(x => x.foldIndex === this.selectedFold()
      && x.strategyId === this.detail()?.run.request.strategyId) ?? [];
    if (!rows.length) return '';
    const values = rows.map(x => Number(x.equity)); // Display coordinates only.
    const low = Math.min(...values), high = Math.max(...values), span = high - low || 1;
    return values.map((value, i) => `${10 + i * 580 / Math.max(1, values.length - 1)},${170 - (value - low) * 150 / span}`).join(' ');
  }
  private cancelPoll(): void { if (this.timer) clearTimeout(this.timer); this.timer = undefined; }
  private schedule(): void {
    if (this.timer || !this.alive() || document.hidden || !navigator.onLine) return;
    if (!this.runs().some(run => ACTIVE.has(run.status)) && !ACTIVE.has(this.detail()?.run.status ?? '') && !ACTIVE.has(this.assessment()?.status ?? '')) return;
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void (async () => {
        const assessment = this.assessment(); if (assessment && ACTIVE.has(assessment.status)) await this.loadAssessment(assessment.assessmentId);
        await this.refreshRuns(); const selected = this.detail();
        if (selected && ACTIVE.has(selected.run.status)) await this.loadDetail(selected.run.runId, this.selection);
        this.schedule();
      })();
    }, 2500);
  }
}
