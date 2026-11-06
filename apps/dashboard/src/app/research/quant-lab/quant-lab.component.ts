import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, inject, input, output, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { Observable, Subject, firstValueFrom, takeUntil } from 'rxjs';
import { ResearchService } from '../../api/generated/control-api/api/research.service';
import type {
  QuantLabBatch, QuantLabCandidate, QuantLabCandidateResult, QuantLabComparisonReport,
  QuantLabDrawdownPage, QuantLabEquityPage, QuantLabExperimentComparison, QuantLabExperimentCreateRequest,
  QuantLabExperimentDefinition, QuantLabReliabilityReportPage, QuantLabStrategyCatalog, QuantLabValidation,
  ResearchInstrument,
} from '../../api/generated/control-api/model/models';
import { httpStatus, safeError } from '../../state/supervisor-api';
import { QuantLabBuilderComponent } from './quant-lab-builder.component';
import { QuantLabDetailComponent } from './quant-lab-detail.component';

const PAGE_SIZE = 20;
const ID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const ACTIVE = new Set(['ACCEPTED', 'RUNNING']);
type Section = QuantLabReliabilityReportPage.SectionEnum;

@Component({
  selector: 'atl-quant-lab',
  imports: [MatButtonModule, MatCardModule, MatCheckboxModule, QuantLabBuilderComponent, QuantLabDetailComponent],
  templateUrl: './quant-lab.component.html',
  styleUrl: './quant-lab.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class QuantLabComponent implements OnInit, OnDestroy {
  private readonly api = inject(ResearchService);
  readonly instruments = input<readonly ResearchInstrument[]>([]);
  readonly sessionLost = output<void>();
  readonly catalog = signal<QuantLabStrategyCatalog | null>(null);
  readonly experiments = signal<readonly QuantLabExperimentDefinition[]>([]);
  readonly experiment = signal<QuantLabExperimentDefinition | null>(null);
  readonly validation = signal<QuantLabValidation | null>(null);
  readonly batches = signal<readonly QuantLabBatch[]>([]);
  readonly batch = signal<QuantLabBatch | null>(null);
  readonly comparison = signal<QuantLabComparisonReport | null>(null);
  readonly candidates = signal<readonly QuantLabCandidate[]>([]);
  readonly results = signal<readonly QuantLabCandidateResult[]>([]);
  readonly selectedResult = signal<QuantLabCandidateResult | null>(null);
  readonly equity = signal<QuantLabEquityPage | null>(null);
  readonly drawdown = signal<QuantLabDrawdownPage | null>(null);
  readonly reliability = signal<QuantLabReliabilityReportPage | null>(null);
  readonly reportHref = signal<string | null>(null);
  readonly compareIds = signal<readonly string[]>([]);
  readonly crossBatch = signal<QuantLabExperimentComparison | null>(null);
  readonly experimentCursor = signal<string | null>(null);
  readonly batchCursor = signal<string | null>(null);
  readonly candidateCursor = signal<string | null>(null);
  readonly resultCursor = signal<string | null>(null);
  readonly error = signal('');
  readonly dirty = signal(false);
  readonly busy = signal(false);
  readonly active = signal(true);
  private readonly stop = new Subject<void>();
  private requestTail: Promise<unknown> = Promise.resolve();
  private readonly pendingKeys = new Map<string, { payload: string; key: string }>();
  private experimentGeneration = 0;
  private batchGeneration = 0;
  private resultGeneration = 0;
  private reportGeneration = 0;
  private editGeneration = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private refreshing = false;
  private selectingBatch: string | null = null;
  private reportTarget: string | null = null;
  private readonly visibility = () => {
    if (document.hidden || !navigator.onLine) this.stopPolling();
    else this.schedule();
  };

  ngOnInit(): void {
    document.addEventListener('visibilitychange', this.visibility);
    window.addEventListener('online', this.visibility);
    window.addEventListener('offline', this.visibility);
    const query = new URL(window.location.href).searchParams;
    const [definition, batch, report] = ['quantExperiment', 'quantBatch', 'quantReport']
      .map(key => query.get(key)).map(value => value && ID.test(value) ? value : null);
    void this.loadCatalog();
    void this.loadExperiments();
    if (batch) void this.openBatch(batch, report);
    else if (definition) void this.openExperiment(definition);
  }

  ngOnDestroy(): void {
    this.active.set(false);
    this.stopPolling();
    this.stop.next();
    this.stop.complete();
    document.removeEventListener('visibilitychange', this.visibility);
    window.removeEventListener('online', this.visibility);
    window.removeEventListener('offline', this.visibility);
  }

  private read<T>(request: Observable<T>, current = () => true): Promise<T | undefined> {
    const value = this.requestTail.then(() => this.readCurrent(request, current));
    this.requestTail = value;
    return value;
  }

  private async readCurrent<T>(request: Observable<T>, current: () => boolean): Promise<T | undefined> {
    if (!this.active() || !current()) return;
    try {
      const value = await firstValueFrom(request.pipe(takeUntil(this.stop)));
      return this.active() && current() ? value : undefined;
    } catch (error) {
      if (!this.active()) return;
      if (httpStatus(error) === 401) {
        this.active.set(false);
        this.stopPolling();
        this.stop.next();
        this.pendingKeys.clear();
        this.catalog.set(null);
        this.experiments.set([]);
        this.experiment.set(null);
        this.validation.set(null);
        this.batches.set([]);
        this.clearBatch();
        this.compareIds.set([]);
        this.crossBatch.set(null);
        this.sessionLost.emit();
      } else if (current()) this.error.set(safeError(error));
      return undefined;
    }
  }

  private async loadCatalog(): Promise<void> {
    const value = await this.read(this.api.getQuantLabStrategies());
    if (value) this.catalog.set(value);
  }

  async loadExperiments(append = false): Promise<void> {
    const cursor = append ? this.experimentCursor() : null;
    if (append && !cursor) return;
    const page = await this.read(this.api.listQuantLabExperiments(cursor ?? undefined, PAGE_SIZE),
      () => !append || this.experimentCursor() === cursor);
    if (page) {
      this.experiments.set(append ? [...this.experiments(), ...page.items] : page.items);
      this.experimentCursor.set(page.nextCursor);
    }
  }

  edited(): void {
    this.editGeneration++;
    this.dirty.set(true);
    this.validation.set(null);
  }

  private key(action: string, payload: unknown): string {
    const serialized = JSON.stringify(payload);
    const stored = this.pendingKeys.get(action);
    if (stored?.payload === serialized) return stored.key;
    const key = crypto.randomUUID();
    this.pendingKeys.set(action, { payload: serialized, key });
    return key;
  }

  async saveDraft(form: QuantLabExperimentCreateRequest): Promise<void> {
    if (this.busy() || !this.active()) return;
    const previous = this.experiment();
    if (previous && !this.dirty()) return;
    const body: QuantLabExperimentCreateRequest = {
      ...form,
      predecessorDefinitionId: previous?.definition.instrumentId === form.instrumentId ? previous.id : null,
    };
    this.busy.set(true);
    this.error.set('');
    const generation = this.editGeneration;
    const value = await this.read(this.api.createQuantLabExperiment(this.key('draft', body), body),
      () => generation === this.editGeneration);
    this.busy.set(false);
    if (value) {
      this.pendingKeys.delete('draft');
      this.useExperiment(value);
      this.dirty.set(false);
      void this.loadExperiments();
    }
  }

  async openExperiment(id: string): Promise<void> {
    if (this.busy() || !this.active()) return;
    const generation = ++this.experimentGeneration;
    this.error.set('');
    const value = await this.read(this.api.getQuantLabExperiment(id), () => generation === this.experimentGeneration);
    if (value) this.useExperiment(value);
  }

  private useExperiment(value: QuantLabExperimentDefinition): void {
    this.experimentGeneration++;
    this.experiment.set(value);
    this.dirty.set(false);
    this.validation.set(null);
    this.batches.set([]);
    this.batchCursor.set(null);
    this.clearBatch();
    this.link(value.id);
    void this.loadBatches();
  }

  async loadBatches(append = false): Promise<void> {
    const definition = this.experiment();
    const cursor = append ? this.batchCursor() : null;
    if (!definition || (append && !cursor)) return;
    const generation = this.experimentGeneration;
    const page = await this.read(this.api.listQuantLabExperimentBatches(definition.id, cursor ?? undefined, PAGE_SIZE),
      () => generation === this.experimentGeneration && (!append || this.batchCursor() === cursor));
    if (page) {
      this.batches.set(append ? [...this.batches(), ...page.items] : page.items);
      this.batchCursor.set(page.nextCursor);
    }
  }

  async validateDraft(): Promise<void> {
    const draft = this.experiment();
    if (!draft || this.dirty() || this.busy()) return;
    const body = { schemaVersion: 'quant-lab-v1' as const, definitionVersion: draft.definitionVersion, definitionHash: draft.definitionHash };
    const current = () => this.experiment()?.id === draft.id && !this.dirty();
    this.busy.set(true);
    this.error.set('');
    const value = await this.read(this.api.validateQuantLabExperiment(draft.id, this.key('validation', body), body), current);
    this.busy.set(false);
    if (value) {
      this.pendingKeys.delete('validation');
      this.validation.set(value);
    }
  }

  async startBatch(): Promise<void> {
    const draft = this.experiment(), validation = this.validation();
    if (!draft || validation?.status !== 'VALIDATED' || validation.definitionHash !== draft.definitionHash ||
      validation.definitionId !== draft.id || this.dirty() || this.busy()) return;
    const body = {
      schemaVersion: 'quant-lab-v1' as const, definitionVersion: draft.definitionVersion, definitionHash: draft.definitionHash,
      validationId: validation.id, validationHash: validation.validationHash,
    };
    this.busy.set(true);
    this.error.set('');
    const value = await this.read(this.api.startQuantLabExperimentBatch(draft.id, this.key('start', body), body),
      () => this.experiment()?.id === draft.id);
    this.busy.set(false);
    if (value) {
      this.pendingKeys.delete('start');
      await this.openBatch(value.id);
      void this.loadBatches();
    }
  }

  private clearBatch(): void {
    this.batchGeneration++;
    this.selectingBatch = null;
    this.stopPolling();
    this.batch.set(null);
    this.comparison.set(null);
    this.candidates.set([]);
    this.results.set([]);
    this.candidateCursor.set(null);
    this.resultCursor.set(null);
    this.resultGeneration++;
    this.selectedResult.set(null);
    this.equity.set(null);
    this.drawdown.set(null);
    this.reportGeneration++;
    this.reportTarget = null;
    this.reliability.set(null);
    this.reportHref.set(null);
  }

  async openBatch(id: string, report: string | null = null): Promise<void> {
    this.clearBatch();
    const generation = this.batchGeneration;
    this.selectingBatch = id;
    const current = () => generation === this.batchGeneration;
    const value = await this.read(this.api.getQuantLabExperimentBatch(id, undefined, PAGE_SIZE), current);
    if (!value) return;
    this.batch.set(value);
    this.link(value.definitionId, id, report);
    if (this.experiment()?.id !== value.definitionId) {
      this.experimentGeneration++;
      this.validation.set(null);
      this.batches.set([]);
      this.batchCursor.set(null);
      const definition = await this.read(this.api.getQuantLabExperiment(value.definitionId), current);
      if (definition) {
        this.experiment.set(definition);
        this.dirty.set(false);
        void this.loadBatches();
      }
    }
    if (!current()) return;
    await this.loadOutputs(value, current);
    if (report && current()) void this.openReport(report);
    this.schedule();
  }

  private async loadOutputs(batch: QuantLabBatch, current: () => boolean): Promise<void> {
    await Promise.all([
      this.loadCandidates(), this.loadResults(),
      batch.reportId ? this.read(this.api.getQuantLabComparison(batch.id, undefined, PAGE_SIZE), current)
        .then(value => { if (value) this.comparison.set(value); }) : Promise.resolve(),
    ]);
  }

  async refreshBatch(): Promise<void> {
    const id = this.selectingBatch;
    if (this.refreshing || !id) return;
    this.refreshing = true;
    const generation = this.batchGeneration;
    const current = () => generation === this.batchGeneration;
    try {
      const before = this.batch();
      const value = await this.read(this.api.getQuantLabExperimentBatch(id, undefined, PAGE_SIZE), current);
      if (!value) return;
      this.batch.set(value);
      this.batches.update(rows => rows.map(row => row.id === value.id ? value : row));
      if (before?.updatedAt !== value.updatedAt) await this.loadOutputs(value, current);
    } finally {
      this.refreshing = false;
      this.schedule();
    }
  }

  private schedule(): void {
    this.stopPolling();
    const batch = this.batch();
    if (!batch || !this.active() || !ACTIVE.has(batch.state) || document.hidden || !navigator.onLine) return;
    this.timer = setTimeout(() => void this.refreshBatch(), 2000);
  }

  private stopPolling(): void {
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = undefined;
  }

  async loadCandidates(append = false): Promise<void> {
    const id = this.batch()?.id, cursor = append ? this.candidateCursor() : null;
    if (!id || (append && !cursor)) return;
    const generation = this.batchGeneration;
    const page = await this.read(this.api.listQuantLabCandidates(id, cursor ?? undefined, PAGE_SIZE),
      () => generation === this.batchGeneration && (!append || this.candidateCursor() === cursor));
    if (page) {
      this.candidates.set(append ? [...this.candidates(), ...page.items] : page.items);
      this.candidateCursor.set(page.nextCursor);
    }
  }

  async loadResults(append = false): Promise<void> {
    const id = this.batch()?.id, cursor = append ? this.resultCursor() : null;
    if (!id || (append && !cursor)) return;
    const generation = this.batchGeneration;
    const page = await this.read(this.api.listQuantLabResults(id, cursor ?? undefined, PAGE_SIZE),
      () => generation === this.batchGeneration && (!append || this.resultCursor() === cursor));
    if (page) {
      this.results.set(append ? [...this.results(), ...page.items] : page.items);
      this.resultCursor.set(page.nextCursor);
    }
  }

  async loadHistory(): Promise<void> {
    const batch = this.batch(), report = this.comparison();
    const history = report?.researchHistory ?? batch?.researchHistory;
    if (!batch || !history?.nextOverlapCursor) return;
    const generation = this.batchGeneration;
    const cursor = history.nextOverlapCursor;
    const current = () => generation === this.batchGeneration &&
      (this.comparison()?.researchHistory ?? this.batch()?.researchHistory)?.nextOverlapCursor === cursor;
    if (report) {
      const page = await this.read(this.api.getQuantLabComparison(batch.id, cursor, PAGE_SIZE), current);
      if (page) this.comparison.set({ ...page, researchHistory: {
        ...page.researchHistory, overlaps: [...history.overlaps, ...page.researchHistory.overlaps],
      } });
    } else {
      const page = await this.read(this.api.getQuantLabExperimentBatch(batch.id, cursor, PAGE_SIZE), current);
      if (page) this.batch.set({ ...page, researchHistory: {
        ...page.researchHistory, overlaps: [...history.overlaps, ...page.researchHistory.overlaps],
      } });
    }
  }

  selectResult(row: QuantLabCandidateResult): void {
    if (this.batch()?.id !== row.batchId) return;
    this.resultGeneration++;
    this.selectedResult.set(row);
    this.equity.set(null);
    this.drawdown.set(null);
    if (row.evaluationStatus === 'COMPLETE' && this.batch()?.reportId) {
      void this.loadEquity();
      void this.loadDrawdown();
    }
  }

  async loadEquity(append = false): Promise<void> {
    const row = this.selectedResult(), cursor = append ? this.equity()?.nextCursor : null;
    if (!row || row.evaluationStatus !== 'COMPLETE' || (append && !cursor)) return;
    const generation = this.resultGeneration;
    const value = await this.read(this.api.getQuantLabEquity(row.batchId, row.candidateId, row.phase,
      cursor ?? undefined, PAGE_SIZE, row.fold ?? undefined, row.role),
      () => generation === this.resultGeneration && (!append || this.equity()?.nextCursor === cursor));
    if (value) this.equity.set(append ? { ...value, items: [...(this.equity()?.items ?? []), ...value.items] } : value);
  }

  async loadDrawdown(append = false): Promise<void> {
    const row = this.selectedResult(), cursor = append ? this.drawdown()?.nextCursor : null;
    if (!row || row.evaluationStatus !== 'COMPLETE' || (append && !cursor)) return;
    const generation = this.resultGeneration;
    const value = await this.read(this.api.getQuantLabDrawdown(row.batchId, row.candidateId, row.phase,
      cursor ?? undefined, PAGE_SIZE, row.fold ?? undefined, row.role),
      () => generation === this.resultGeneration && (!append || this.drawdown()?.nextCursor === cursor));
    if (value) this.drawdown.set(append ? { ...value, items: [...(this.drawdown()?.items ?? []), ...value.items] } : value);
  }

  showReliability(row: QuantLabCandidateResult): void {
    if (row.reliability) void this.openReport(row.reliability.reportId);
  }

  private async openReport(id: string): Promise<void> {
    const batch = this.batch();
    if (!batch) return;
    this.reportTarget = id;
    this.reportGeneration++;
    this.reliability.set(null);
    this.reportHref.set(this.link(batch.definitionId, batch.id, id));
    await this.loadReliability();
  }

  async loadReliability(section: Section = 'FINDINGS', append = false): Promise<void> {
    const id = this.batch()?.id, report = this.reportTarget;
    const cursor = append ? this.reliability()?.nextCursor : null;
    if (!id || !report || (append && !cursor)) return;
    const generation = ++this.reportGeneration;
    const value = await this.read(this.api.getQuantLabReliabilityReport(id, report, cursor ?? undefined, PAGE_SIZE, section),
      () => generation === this.reportGeneration);
    if (value) {
      const prior = this.reliability();
      this.reliability.set(append && prior ? { ...value,
        findings: [...prior.findings, ...value.findings],
        revisionReferences: [...prior.revisionReferences, ...value.revisionReferences],
      } : value);
    }
  }

  moreReliability(): void {
    const section = this.reliability()?.section;
    if (section) void this.loadReliability(section, true);
  }

  toggleCompare(id: string, checked: boolean): void {
    this.crossBatch.set(null);
    this.compareIds.update(ids => checked
      ? (ids.length < 4 && !ids.includes(id) ? [...ids, id] : ids)
      : ids.filter(value => value !== id));
  }

  async compareBatches(): Promise<void> {
    const ids = this.compareIds();
    if (ids.length < 2) return;
    const value = await this.read(this.api.compareQuantLabExperiments([...ids]), () => this.compareIds() === ids);
    if (value) this.crossBatch.set(value);
  }

  private link(experiment: string, batch?: string, report?: string | null): string {
    const url = new URL(window.location.href);
    url.searchParams.set('quantExperiment', experiment);
    for (const [key, value] of [['quantBatch', batch], ['quantReport', report]] as const) {
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
    const relative = url.pathname + url.search + url.hash;
    history.replaceState(history.state, '', relative);
    return relative;
  }
}
