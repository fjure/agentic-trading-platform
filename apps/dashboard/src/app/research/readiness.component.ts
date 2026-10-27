import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, input, output, signal } from '@angular/core';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { Observable, Subject, firstValueFrom, takeUntil } from 'rxjs';
import { ResearchService } from '../api/generated/control-api/api/research.service';
import { ReadinessCategory } from '../api/generated/control-api/model/readinessCategory';
import type { BacktestRequest, CollectionPlan, ReadinessAssessment, ReadinessFinding, ReadinessFindingPage, ReadinessReport } from '../api/generated/control-api/model/models';
import { httpStatus, safeError } from '../state/supervisor-api';
import { ReadinessMetricsComponent } from './readiness-metrics.component';
import { ReadinessTrustComponent } from './readiness-trust.component';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ACTIVE = new Set(['ACCEPTED', 'RUNNING']);

@Component({
  selector: 'atl-readiness',
  imports: [ReactiveFormsModule, MatButtonModule, MatCardModule, MatFormFieldModule, MatSelectModule, ReadinessMetricsComponent, ReadinessTrustComponent],
  templateUrl: './readiness.component.html',
  styleUrl: './readiness.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReadinessComponent implements OnInit, OnDestroy {
  private readonly api = inject(ResearchService);
  readonly request = input<BacktestRequest | null>(null);
  readonly sessionLost = output<void>();
  readonly authorized = signal(true);
  readonly assessment = signal<ReadinessAssessment | null>(null);
  readonly report = computed(() => this.assessment()?.report as ReadinessReport | null ?? null);
  readonly findings = signal<ReadinessFinding[]>([]);
  readonly nextCursor = signal<string | null>(null);
  readonly totalMatching = signal(0);
  readonly plans = signal<CollectionPlan[]>([]);
  readonly error = signal('');
  readonly notice = signal('');
  readonly sending = signal(false);
  readonly loading = signal(false);
  readonly findingsLoading = signal(false);
  readonly plansLoading = signal(false);
  readonly planSending = signal<string | null>(null);
  readonly categories = Object.values(ReadinessCategory);
  readonly category = new FormControl<ReadinessCategory | ''>('', { nonNullable: true });
  private readonly stop = new Subject<void>();
  private disposed = false;
  private selection = 0;
  private findingSelection = 0;
  private selectedId: string | null = null;
  private pending: { payload: string; key: string } | null = null;
  private readonly pendingPlans = new Map<string, { payload: string; key: string }>();
  private timer?: ReturnType<typeof setTimeout>;
  private readonly visibility = () => { this.cancelPoll(); this.schedule(); };

  ngOnInit(): void {
    this.category.valueChanges.pipe(takeUntil(this.stop)).subscribe(() => {
      this.findingSelection++; this.findings.set([]); this.nextCursor.set(null);
      this.findingsLoading.set(false); void this.loadFindings();
    });
    document.addEventListener('visibilitychange', this.visibility);
    window.addEventListener('offline', this.visibility); window.addEventListener('online', this.visibility);
    void this.refreshPlans();
    const selected = new URL(location.href).searchParams.get('readinessAssessment');
    if (selected && UUID.test(selected)) void this.select(selected);
  }
  ngOnDestroy(): void {
    this.disposed = true; this.selection++; this.findingSelection++; this.cancelPoll();
    this.stop.next(); this.stop.complete();
    document.removeEventListener('visibilitychange', this.visibility);
    window.removeEventListener('offline', this.visibility); window.removeEventListener('online', this.visibility);
  }
  private alive(ticket = this.selection): boolean { return !this.disposed && this.authorized() && ticket === this.selection; }
  private read<T>(request: Observable<T>): Promise<T> { return firstValueFrom(request.pipe(takeUntil(this.stop))); }
  private failed(error: unknown): void {
    if (this.disposed || !this.authorized()) return;
    this.error.set(safeError(error));
    if (httpStatus(error) === 401) {
      this.authorized.set(false); this.selection++; this.findingSelection++; this.cancelPoll(); this.stop.next();
      this.assessment.set(null); this.findings.set([]); this.plans.set([]); this.nextCursor.set(null);
      this.pending = null; this.pendingPlans.clear(); this.sessionLost.emit();
    }
  }
  async diagnose(): Promise<void> {
    const request = this.request();
    if (!request || !this.alive() || this.sending()) return;
    const payload = JSON.stringify(request);
    if (this.pending?.payload !== payload) this.pending = { payload, key: crypto.randomUUID() };
    this.sending.set(true); this.error.set(''); this.notice.set('');
    try {
      const accepted = await this.read(this.api.createReadinessAssessment(this.pending.key, request));
      if (!this.alive()) return;
      this.pending = null;
      this.notice.set('Diagnose dauerhaft beauftragt. Der Auftrag startet keine Simulation.');
      await this.select(accepted.assessmentId);
    } catch (error) { this.failed(error); }
    finally { if (!this.disposed) this.sending.set(false); }
  }
  async select(id: string): Promise<void> {
    if (!this.alive() || !UUID.test(id)) return;
    this.selectedId = id; this.selection++; this.findingSelection++; this.cancelPoll();
    this.assessment.set(null); this.findings.set([]); this.nextCursor.set(null); this.category.setValue('');
    this.loading.set(false);
    this.findingsLoading.set(false); this.error.set('');
    const url = new URL(location.href); url.searchParams.set('readinessAssessment', id);
    history.replaceState(null, '', url);
    await this.refreshAssessment();
  }
  async refreshAssessment(): Promise<void> {
    const id = this.selectedId, ticket = this.selection;
    if (!id || !this.alive(ticket) || this.loading()) return;
    this.loading.set(true);
    try {
      const result = await this.read(this.api.getReadinessAssessment(id));
      if (!this.alive(ticket)) return;
      const previousHash = this.report()?.reportHash;
      this.assessment.set(result);
      if (result.report && result.publicationComplete && result.report.reportHash !== previousHash) {
        this.findingSelection++; this.findings.set([]); this.nextCursor.set(null);
        this.findingsLoading.set(false); await this.loadFindings();
      }
    } catch (error) { if (this.alive(ticket) || httpStatus(error) === 401) this.failed(error); }
    finally { if (this.alive(ticket)) { this.loading.set(false); this.schedule(); } }
  }
  async loadFindings(append = false): Promise<void> {
    const report = this.report(), ticket = this.selection, filterTicket = this.findingSelection;
    if (!report || !this.alive(ticket) || this.findingsLoading()) return;
    const cursor = append ? this.nextCursor() ?? undefined : undefined;
    if (append && !cursor) return;
    this.findingsLoading.set(true);
    try {
      const page: ReadinessFindingPage = await this.read(this.api.listReadinessFindings(
        report.assessmentId, 100, cursor, this.category.value || undefined));
      if (!this.alive(ticket) || filterTicket !== this.findingSelection) return;
      if (page.assessmentId !== report.assessmentId || page.reportHash !== report.reportHash
        || page.snapshot.snapshotVersion !== report.snapshot.snapshotVersion) throw new Error('INVALID_READINESS_PAGE');
      this.findings.set(append ? [...this.findings(), ...page.items] : page.items);
      this.nextCursor.set(page.nextCursor); this.totalMatching.set(page.totalMatching);
    } catch (error) { if (this.alive(ticket) || httpStatus(error) === 401) this.failed(error); }
    finally { if (this.alive(ticket) && filterTicket === this.findingSelection) this.findingsLoading.set(false); }
  }
  async refreshPlans(): Promise<void> {
    if (!this.alive() || this.plansLoading()) return;
    this.plansLoading.set(true);
    try {
      const page = await this.read(this.api.listCollectionPlans());
      if (this.alive()) this.plans.set(page.items);
    } catch (error) { this.failed(error); }
    finally { if (!this.disposed) this.plansLoading.set(false); }
  }
  async updatePlan(plan: CollectionPlan, enabled: boolean): Promise<void> {
    if (!this.alive() || this.planSending()) return;
    const request = { enabled, expectedVersion: plan.version }, payload = JSON.stringify(request);
    let pending = this.pendingPlans.get(plan.instrumentId);
    if (pending?.payload !== payload) { pending = { payload, key: crypto.randomUUID() }; this.pendingPlans.set(plan.instrumentId, pending); }
    this.planSending.set(plan.instrumentId); this.error.set(''); this.notice.set('');
    try {
      const result = await this.read(this.api.updateCollectionPlan(plan.instrumentId, pending.key, request));
      if (!this.alive()) return;
      this.pendingPlans.delete(plan.instrumentId);
      this.plans.update(items => items.map(item => item.instrumentId === result.instrumentId ? result : item));
      this.notice.set(enabled ? 'Sammlungswunsch gespeichert. Globale Freigaben und Quellenrechte bleiben verbindlich.'
        : `Pause gespeichert. Bereits begonnene Abrufe: ${result.outstandingStartedDispatches}.`);
    } catch (error) {
      if (httpStatus(error) === 409 && this.alive()) {
        this.pendingPlans.delete(plan.instrumentId);
        this.error.set('Planversion hat sich geändert. Pläne werden neu geladen; Änderung anschließend ausdrücklich erneut ausführen.');
        await this.refreshPlans();
      } else this.failed(error);
    } finally { if (!this.disposed) this.planSending.set(null); }
  }
  private cancelPoll(): void { if (this.timer) clearTimeout(this.timer); this.timer = undefined; }
  private schedule(): void {
    if (this.timer || !this.alive() || document.hidden || !navigator.onLine
      || !ACTIVE.has(this.assessment()?.status ?? '')) return;
    this.timer = setTimeout(() => { this.timer = undefined; void this.refreshAssessment(); }, 2500);
  }
}
