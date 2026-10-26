import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, inject, output, signal } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { Observable, Subject, firstValueFrom, takeUntil } from 'rxjs';
import { ResearchService } from '../api/generated/control-api/api/research.service';
import type { ResearchBarPage, ResearchContext, ResearchEvidenceResult, ResearchInstrument, ResearchInterval, ResearchJob, ResearchJobRequest, ResearchSourceCatalog } from '../api/generated/control-api/model/models';
import { httpStatus, safeError } from '../state/supervisor-api';
import { BacktestingComponent } from './backtesting.component';

const ACTIVE = new Set(['ACCEPTED', 'RUNNING', 'RETRY_WAIT']);
const UTC_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const timestamp = (value: string) => new FormControl(value, { nonNullable: true, validators: [Validators.required, Validators.pattern(UTC_TIME)] });
type Bound = { instrumentId: string; start: string; end: string; interval: ResearchInterval; valuationAt: string; knowledgeAt: string };

@Component({
  selector: 'atl-research',
  imports: [ReactiveFormsModule, MatButtonModule, MatCardModule, MatFormFieldModule, MatInputModule, MatSelectModule, BacktestingComponent],
  templateUrl: './research.component.html',
  styleUrl: './research.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ResearchComponent implements OnInit, OnDestroy {
  private readonly api = inject(ResearchService);
  readonly sessionLost = output<void>();
  readonly catalog = signal<ResearchSourceCatalog | null>(null);
  readonly instruments = signal<ResearchInstrument[]>([]);
  readonly bars = signal<ResearchBarPage | null>(null);
  readonly context = signal<ResearchContext | null>(null);
  readonly evidence = signal<ResearchEvidenceResult | null>(null);
  readonly jobs = signal<ResearchJob[]>([]);
  readonly jobsMore = signal<string | null>(null);
  readonly error = signal('');
  readonly notice = signal('');
  readonly loading = signal(false);
  readonly sending = signal(false);
  readonly jobsLoading = signal(false);
  readonly authorized = signal(true);
  readonly polling = signal(false);
  private readonly now = new Date().toISOString();
  readonly form = new FormGroup({
    instrumentId: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    start: timestamp(new Date(Date.now() - 86400000).toISOString()),
    end: timestamp(this.now),
    interval: new FormControl<ResearchInterval>('15Min', { nonNullable: true }),
    valuationAt: timestamp(this.now),
    knowledgeAt: timestamp(this.now),
    mode: new FormControl<'BACKFILL' | 'UPDATE'>('BACKFILL', { nonNullable: true }),
  });
  private readonly stop = new Subject<void>();
  private readonly changes = this.form.valueChanges.subscribe(() => {
    this.generation++; this.evidenceGeneration++; this.bars.set(null); this.context.set(null); this.evidence.set(null);
    this.bound = null; this.loading.set(false);
    const payload = JSON.stringify(this.request());
    if (payload !== this.pending?.payload) this.pending = null;
  });
  private pending: { payload: string; key: string } | null = null;
  private readonly resumeKeys = new Map<string, string>();
  private bound: Bound | null = null;
  private generation = 0;
  private evidenceGeneration = 0;
  private disposed = false;
  private timer?: ReturnType<typeof setTimeout>;
  private readonly visibility = () => { if (document.hidden || !navigator.onLine) this.stopPolling(); else this.schedule(); };

  ngOnInit(): void {
    document.addEventListener('visibilitychange', this.visibility);
    window.addEventListener('offline', this.visibility);
    window.addEventListener('online', this.visibility);
    void this.refreshCatalog(); void this.refreshJobs();
  }
  ngOnDestroy(): void {
    this.disposed = true; this.generation++; this.stopPolling();
    this.stop.next(); this.stop.complete(); this.changes.unsubscribe();
    document.removeEventListener('visibilitychange', this.visibility);
    window.removeEventListener('offline', this.visibility);
    window.removeEventListener('online', this.visibility);
  }
  private read<T>(value: Observable<T>): Promise<T> { return firstValueFrom(value.pipe(takeUntil(this.stop))); }
  private alive(generation?: number): boolean { return !this.disposed && this.authorized() && (generation === undefined || generation === this.generation); }
  private failed(error: unknown): void {
    if (!this.alive()) return;
    this.error.set(safeError(error));
    if (httpStatus(error) === 401) {
      this.authorized.set(false); this.generation++; this.stopPolling(); this.stop.next();
      this.bars.set(null); this.context.set(null); this.evidence.set(null); this.catalog.set(null); this.jobs.set([]);
      this.pending = null; this.sessionLost.emit();
    }
  }
  async refreshCatalog(): Promise<void> {
    if (!this.alive()) return;
    try {
      const result = await this.read(this.api.listResearchSources());
      if (!this.alive()) return;
      this.catalog.set(result);
      const found = new Map<string, ResearchInstrument>();
      for (const source of result.items.filter(x => x.kind === 'BAR')) for (const instrument of source.instruments) found.set(instrument.instrumentId, instrument);
      this.instruments.set([...found.values()]);
      if (!found.has(this.form.controls.instrumentId.value)) this.form.controls.instrumentId.setValue(found.keys().next().value ?? '');
    } catch (error) { this.failed(error); }
  }
  private bounds(): Bound {
    const value = this.form.getRawValue();
    if (this.form.invalid || !this.instruments().some(x => x.instrumentId === value.instrumentId)) throw new Error('INVALID_RESEARCH_FILTER');
    const times = [value.start, value.end, value.valuationAt, value.knowledgeAt].map(Date.parse);
    if (times.some(x => !Number.isFinite(x)) || times[1] <= times[0] || times[1] - times[0] > 366 * 86400000 || times[3] > times[2]) throw new Error('INVALID_RESEARCH_FILTER');
    return value;
  }
  private request(): ResearchJobRequest {
    const value = this.form.getRawValue();
    return { instrumentId: value.instrumentId, start: value.start, end: value.end, interval: value.interval, feed: 'iex', adjustment: 'raw', mode: value.mode };
  }
  async loadBars(append = false): Promise<void> {
    if (!this.alive() || this.loading()) return;
    const generation = this.generation;
    let bound: Bound;
    try { bound = append && this.bound ? this.bound : this.bounds(); }
    catch { this.error.set('Zeitraum prüfen: UTC mit Z, höchstens 366 Tage; Wissensstand ≤ Bewertung.'); return; }
    const cursor = append ? this.bars()?.nextCursor ?? undefined : undefined;
    if (append && !cursor) return;
    this.loading.set(true); this.error.set(''); this.notice.set(''); this.evidence.set(null); this.evidenceGeneration++;
    if (!append) { this.bars.set(null); this.context.set(null); }
    try {
      const result = await this.read(this.api.listResearchBars(bound.instrumentId, bound.start, bound.end, bound.interval, 'iex', 'raw', bound.valuationAt, bound.knowledgeAt, 100, cursor));
      if (!this.alive(generation)) return;
      this.bound = bound;
      const previous = this.bars();
      this.bars.set(append && previous ? { ...result, items: [...previous.items, ...result.items] } : result);
      if (!append) {
        const context = await this.read(this.api.getResearchContext(bound.instrumentId, bound.start, bound.end, bound.interval, 'iex', 'raw', bound.valuationAt, bound.knowledgeAt));
        if (!this.alive(generation)) return;
        if (context.snapshot.snapshotVersion === result.snapshot.snapshotVersion) this.context.set(context);
        else this.notice.set('Datenstand hat sich geändert. Für gemeinsamen Kontext neu abfragen.');
      }
    } catch (error) { if (this.alive(generation) || httpStatus(error) === 401) this.failed(error); }
    finally { if (this.alive(generation)) this.loading.set(false); }
  }
  async showEvidence(id: string): Promise<void> {
    const bound = this.bound, snapshot = this.bars()?.snapshot;
    if (!bound || !snapshot || !this.alive()) return;
    const generation = this.generation;
    const evidenceGeneration = ++this.evidenceGeneration;
    this.evidence.set(null);
    try {
      const result = await this.read(this.api.getResearchEvidence(id, bound.valuationAt, bound.knowledgeAt));
      if (!this.alive(generation) || evidenceGeneration !== this.evidenceGeneration) return;
      if (result.snapshot.snapshotVersion !== snapshot.snapshotVersion) { this.notice.set('Evidenzdatenstand hat sich geändert. Bitte neu abfragen.'); return; }
      this.evidence.set(result);
    } catch (error) { if (this.alive(generation) || httpStatus(error) === 401) this.failed(error); }
  }
  async importHistory(): Promise<void> {
    if (!this.alive() || this.sending()) return;
    try { this.bounds(); } catch { this.error.set('Gültiges Instrument und UTC-Zeitraum erforderlich.'); return; }
    const request = this.request(), payload = JSON.stringify(request);
    if (this.pending?.payload !== payload) this.pending = { payload, key: crypto.randomUUID() };
    const key = this.pending.key;
    this.sending.set(true); this.error.set('');
    try {
      const result = await this.read(this.api.createResearchIngestionJob(key, request));
      if (!this.alive()) return;
      this.jobs.update(items => [result, ...items.filter(x => x.jobId !== result.jobId)].slice(0, 200));
      this.notice.set(`Importauftrag dauerhaft angenommen: ${result.jobId}`); this.schedule();
    } catch (error) { this.failed(error); }
    finally { if (this.alive()) this.sending.set(false); }
  }
  async resumeHistory(job: ResearchJob): Promise<void> {
    if (!this.alive() || this.sending() || !['PARTIAL', 'BLOCKED'].includes(job.status)) return;
    let key = this.resumeKeys.get(job.jobId);
    if (!key) { key = crypto.randomUUID(); this.resumeKeys.set(job.jobId, key); }
    this.sending.set(true); this.error.set('');
    try {
      const result = await this.read(this.api.createResearchIngestionJob(key, { action: 'RESUME', jobId: job.jobId }));
      if (!this.alive()) return;
      this.resumeKeys.delete(job.jobId);
      this.jobs.update(items => [result, ...items.filter(x => x.jobId !== result.jobId)].slice(0, 200));
      this.notice.set(`Fortsetzung ausdrücklich angenommen: ${result.jobId}`); this.schedule();
    } catch (error) { this.failed(error); }
    finally { if (this.alive()) this.sending.set(false); }
  }
  async refreshJobs(append = false): Promise<void> {
    if (!this.alive() || this.jobsLoading()) return;
    const cursor = append ? this.jobsMore() ?? undefined : undefined;
    if (append && !cursor) return;
    this.stopPolling(); this.jobsLoading.set(true);
    try {
      const result = await this.read(this.api.listResearchIngestionJobs(50, cursor));
      if (!this.alive()) return;
      this.jobs.set(append ? [...this.jobs(), ...result.items].slice(0, 200) : result.items);
      this.jobsMore.set(result.nextCursor);
    } catch (error) { this.failed(error); }
    finally { if (this.alive()) { this.jobsLoading.set(false); this.schedule(); } }
  }
  private stopPolling(): void { if (this.timer) clearTimeout(this.timer); this.timer = undefined; this.polling.set(false); }
  private schedule(): void {
    this.stopPolling();
    if (!this.alive() || document.hidden || !navigator.onLine || !this.jobs().some(x => ACTIVE.has(x.status))) return;
    this.polling.set(true);
    this.timer = setTimeout(() => void this.pollJobs(), 5000);
  }
  private async pollJobs(): Promise<void> {
    this.stopPolling();
    if (!this.alive() || document.hidden || !navigator.onLine || this.jobsLoading()) return;
    this.jobsLoading.set(true);
    try {
      for (const job of this.jobs().filter(x => ACTIVE.has(x.status)).slice(0, 20)) {
        if (!this.alive() || document.hidden || !navigator.onLine) break;
        const result = await this.read(this.api.getResearchIngestionJob(job.jobId));
        if (!this.alive()) return;
        this.jobs.update(items => items.map(x => x.jobId === result.jobId ? result : x));
      }
    } catch (error) { this.failed(error); }
    finally { if (this.alive()) { this.jobsLoading.set(false); this.schedule(); } }
  }
}
