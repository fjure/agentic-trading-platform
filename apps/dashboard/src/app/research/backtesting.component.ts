import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, inject, output, signal } from '@angular/core';
import { JsonPipe } from '@angular/common';
import { FormArray, FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { Observable, Subject, firstValueFrom, takeUntil } from 'rxjs';
import { BacktestingService } from '../api/generated/control-api/api/backtesting.service';
import type { BacktestRequest, BacktestRun, BacktestRunDetail, BacktestStrategyCatalog, BacktestStrategyId, BacktestSummaryPage, BacktestEquityPage, BacktestFillsPage } from '../api/generated/control-api/model/models';
import { httpStatus, safeError } from '../state/supervisor-api';
import { ReadinessComponent } from './readiness.component';

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
  imports: [JsonPipe, ReactiveFormsModule, MatButtonModule, MatCardModule, MatFormFieldModule, MatInputModule, MatSelectModule, ReadinessComponent],
  templateUrl: './backtesting.component.html',
  styleUrl: './backtesting.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BacktestingComponent implements OnInit, OnDestroy {
  private readonly api = inject(BacktestingService);
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
    void this.initialize();
  }
  ngOnDestroy(): void {
    this.disposed = true; this.selection++; this.cancelPoll(); this.stop.next(); this.stop.complete();
    document.removeEventListener('visibilitychange', this.visibility);
    window.removeEventListener('offline', this.visibility); window.removeEventListener('online', this.visibility);
  }
  private alive(ticket = this.selection): boolean { return !this.disposed && this.authorized() && ticket === this.selection; }
  private read<T>(request: Observable<T>): Promise<T> { return firstValueFrom(request.pipe(takeUntil(this.stop))); }
  private failed(error: unknown): void {
    if (this.disposed || !this.authorized()) return;
    this.error.set(safeError(error));
    if (httpStatus(error) === 401) {
      this.authorized.set(false); this.selection++; this.cancelPoll(); this.stop.next();
      this.catalog.set(null); this.runs.set([]); this.detail.set(null); this.clearResults();
      this.pending = null; this.sessionLost.emit();
    }
  }
  private async initialize(): Promise<void> {
    try {
      const catalog = await this.read(this.api.listBacktestStrategies());
      if (!this.alive()) return;
      this.catalog.set(catalog); this.form.controls.instrumentId.setValue(catalog.instruments[0]?.instrumentId ?? '');
      await this.refreshRuns();
      const selected = new URL(location.href).searchParams.get('backtestRun');
      if (selected && /^[0-9a-f-]{36}$/.test(selected) && this.alive()) await this.select(selected);
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
    return this.validRequest ? this.experimentRequest() : null;
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
  async submit(): Promise<void> {
    if (!this.alive() || !this.validRequest || this.sending()) return;
    const request = this.experimentRequest();
    const payload = JSON.stringify(request);
    if (this.pending?.payload !== payload) this.pending = { payload, key: crypto.randomUUID() };
    this.sending.set(true); this.error.set('');
    try {
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
      this.nextRuns.set(result.nextCursor); this.schedule();
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
        this.summary.set(append && this.summary() ? { ...page, items: [...this.summary()!.items, ...page.items] } : page);
      } else if (kind === 'equity') {
        const page = result as BacktestEquityPage;
        this.equity.set(append && this.equity() ? { ...page, items: [...this.equity()!.items, ...page.items] } : page);
      } else {
        const page = result as BacktestFillsPage;
        this.fills.set(append && this.fills() ? { ...page, items: [...this.fills()!.items, ...page.items] } : page);
      }
    } catch (error) { if (this.alive(ticket) || httpStatus(error) === 401) this.failed(error); }
  }
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
    if (!this.runs().some(run => ACTIVE.has(run.status)) && !ACTIVE.has(this.detail()?.run.status ?? '')) return;
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void (async () => {
        await this.refreshRuns(); const selected = this.detail();
        if (selected && ACTIVE.has(selected.run.status)) await this.loadDetail(selected.run.runId, this.selection);
        this.schedule();
      })();
    }, 2500);
  }
}
