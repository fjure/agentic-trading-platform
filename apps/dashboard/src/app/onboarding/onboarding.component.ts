import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { BreakpointObserver } from '@angular/cdk/layout';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatStepperModule } from '@angular/material/stepper';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { firstValueFrom } from 'rxjs';
import { ExperienceService } from '../api/generated/control-api/api/experience.service';
import { DemoService } from '../api/generated/control-api/api/demo.service';
import { ControlService } from '../api/generated/control-api/api/control.service';
import { SetupService } from '../api/generated/control-api/api/setup.service';
import type { InvestmentPreferencesV1 as Preferences, InvestmentPreferencesUpdateV1 as Update, InvestmentOnboardingStepV1 as Step, DemoRun, SupervisorSnapshot, IntegrationStatuses } from '../api/generated/control-api/model/models';
import { projectSnapshot } from '../state/public-dto-schema';
import { httpStatus } from '../state/supervisor-api';

type Field = 'experienceLevel' | 'horizon' | 'lossTolerance';
const STEPS: Step['step'][] = ['EXPERIENCE', 'GOALS', 'HORIZON', 'LOSS_TOLERANCE', 'CONTEXT'];
const ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/;
const utc = (value: unknown) => value === null || typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value) && Number.isFinite(Date.parse(value));
const emptyFields = () => ({ experienceLevel: 'UNSPECIFIED', horizon: 'UNSPECIFIED', lossTolerance: 'UNSPECIFIED' });
@Component({
  selector: 'atl-onboarding',
  imports: [RouterLink, FormsModule, MatButtonModule, MatCardModule, MatStepperModule, MatFormFieldModule, MatInputModule, MatCheckboxModule],
  templateUrl: './onboarding.component.html', styleUrl: './onboarding.component.css', changeDetection: ChangeDetectionStrategy.OnPush,
})
export class OnboardingComponent implements OnInit {
  private readonly api = inject(ExperienceService);
  private readonly demo = inject(DemoService);
  private readonly control = inject(ControlService);
  private readonly setup = inject(SetupService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly destroy = inject(DestroyRef);
  private disposed = false;
  readonly small = toSignal(inject(BreakpointObserver).observe('(max-width: 767px)'));
  readonly current = signal<Preferences | null>(null);
  readonly loading = signal(true);
  readonly saving = signal(false);
  readonly unauthorized = signal(false);
  readonly error = signal('');
  readonly message = signal('');
  readonly integrations = signal<IntegrationStatuses | null>(null);
  readonly runs = signal<DemoRun[]>([]);
  readonly runsError = signal(false);
  readonly snapshot = signal<SupervisorSnapshot | null>(null);
  readonly demoError = signal('');
  readonly chosenRun = signal('');
  readonly selectedStep = signal(0);
  readonly resetReview = signal(false);
  readonly pending = signal<{ key: string; body: Update } | null>(null);
  readonly pendingDemo = signal<{ key: string } | null>(null);
  readonly demoBusy = signal(false);
  readonly demoRouteId = this.route.snapshot.paramMap.get('runId');
  readonly handoff = !!this.demoRouteId;
  readonly canEdit = computed(() => !!this.current() && !this.loading() && !this.saving() && !this.unauthorized() && !this.pending() && !this.pendingDemo());
  readonly savedDemo = computed(() => this.current()?.contextReference.context === 'DEMO' ? this.current()?.contextReference.demoRunId : undefined);
  fields = emptyFields();
  goals: Preferences['goals'] = [];
  activationCode = '';
  readonly fieldSteps: { key: Field; step: Step['step']; label: string; choices: [string, string][] }[] = [
    { key: 'experienceLevel', step: 'EXPERIENCE', label: 'Erfahrung', choices: [['UNSPECIFIED', 'Keine Angabe'], ['BEGINNER', 'Ich steige ein'], ['EXPERIENCED', 'Ich habe Researcherfahrung']] },
    { key: 'horizon', step: 'HORIZON', label: 'Zeithorizont', choices: [['UNSPECIFIED', 'Keine Angabe'], ['SHORT', 'Kurzfristig'], ['MEDIUM', 'Mittelfristig'], ['LONG', 'Langfristig']] },
    { key: 'lossTolerance', step: 'LOSS_TOLERANCE', label: 'Verlusttoleranz', choices: [['UNSPECIFIED', 'Keine Angabe'], ['LOW', 'Niedrig'], ['MEDIUM', 'Mittel'], ['HIGH', 'Hoch']] },
  ];
  readonly goalChoices: { value: Preferences['goals'][number]; label: string }[] = [{ value: 'LEARN', label: 'Grundlagen lernen' }, { value: 'UNDERSTAND_PORTFOLIO', label: 'Portfolio verstehen' }, { value: 'RESEARCH_STRATEGIES', label: 'Strategien untersuchen' }];
  constructor() { this.destroy.onDestroy(() => { this.disposed = true; this.activationCode = ''; this.fields = emptyFields(); this.goals = []; }); }
  ngOnInit(): void { void this.load(); }
  private read<T>(source: import('rxjs').Observable<T>): Promise<T> { return firstValueFrom(source.pipe(takeUntilDestroyed(this.destroy))); }
  private accept(value: Preferences): void {
    if (value.schemaVersion !== '1' || !Number.isSafeInteger(value.version) || value.version < 0 || !utc(value.updatedAt) || !Array.isArray(value.onboardingSteps) || !Array.isArray(value.goals)
      || !['UNSPECIFIED','BEGINNER','EXPERIENCED'].includes(value.experienceLevel) || !['UNSPECIFIED','SHORT','MEDIUM','LONG'].includes(value.horizon)
      || !['UNSPECIFIED','LOW','MEDIUM','HIGH'].includes(value.lossTolerance) || value.goals.some(goal => !['LEARN','UNDERSTAND_PORTFOLIO','RESEARCH_STRATEGIES'].includes(goal))
      || new Set(value.goals).size !== value.goals.length || new Set(value.onboardingSteps.map(step => step.step)).size !== value.onboardingSteps.length
      || value.onboardingSteps.some(step => !STEPS.includes(step.step) || !['UNSEEN','SKIPPED','COMPLETED'].includes(step.state) || !utc(step.updatedAt))
      || !value.contextReference || !['FAKE','DEMO','ALPACA_PAPER'].includes(value.contextReference.context)
      || ({ FAKE: 'fake', DEMO: 'demo', ALPACA_PAPER: 'alpaca-paper' }[value.contextReference.context] !== value.contextReference.namespace)
      || (value.contextReference.context === 'DEMO' && !ID.test(value.contextReference.demoRunId ?? ''))) throw new Error('Invalid preferences');
    this.current.set(value);
    this.fields = { experienceLevel: value.experienceLevel, horizon: value.horizon, lossTolerance: value.lossTolerance };
    this.goals = [...value.goals];
    this.chosenRun.set(value.contextReference.context === 'DEMO' ? value.contextReference.demoRunId! : '');
  }
  async load(): Promise<void> {
    if (this.saving() || this.pending()) return;
    this.loading.set(true); this.error.set('');
    try {
      this.accept(await this.read(this.api.getInvestmentPreferences()));
      this.unauthorized.set(false);
      const first = STEPS.findIndex(step => this.stepState(step) === 'UNSEEN');
      this.selectedStep.set(first < 0 ? 4 : [0,3,1,2,4][first]);
      void this.loadRuns();
      this.setup.getIntegrationStatuses().pipe(takeUntilDestroyed(this.destroy)).subscribe({ next: value => this.integrations.set(['UNCONFIGURED','CONFIGURED'].includes(value.alpaca?.status) && ['UNCONFIGURED','CONFIGURED'].includes(value.jev?.status) ? value : null), error: () => this.integrations.set(null) });
      if (this.handoff) await this.loadDemo();
    } catch (error) { this.handleError(error, false); }
    finally { if (!this.disposed) this.loading.set(false); }
  }
  async activate(): Promise<void> {
    const code = this.activationCode.trim(); this.activationCode = '';
    if (!code || this.loading()) return;
    this.loading.set(true);
    try { await this.read(this.control.activateLocalSession(crypto.randomUUID(), { activationCode: code })); this.loading.set(false); await this.load(); }
    catch (error) { this.handleError(error, false); this.loading.set(false); }
  }
  stepState(step: Step['step']): Step['state'] { return this.current()?.onboardingSteps.find(item => item.step === step)?.state ?? 'UNSEEN'; }
  stateLabel(step: Step['step']): string { return { UNSEEN: 'Noch offen', SKIPPED: 'Bewusst übersprungen', COMPLETED: 'Gespeichert abgeschlossen' }[this.stepState(step)]; }
  toggleGoal(value: Preferences['goals'][number], checked: boolean): void { this.goals = checked ? [...new Set([...this.goals, value])] : this.goals.filter(goal => goal !== value); }
  private body(): Update {
    const value = this.current()!;
    return { expectedVersion: value.version, reset: false, experienceLevel: value.experienceLevel, goals: [...value.goals], horizon: value.horizon, lossTolerance: value.lossTolerance, contextReference: { ...value.contextReference }, onboardingSteps: value.onboardingSteps.map(step => ({ ...step })) };
  }
  async saveStep(step: Step['step'], state: Step['state']): Promise<void> {
    if (!this.canEdit()) return;
    const body = this.body();
    const field = this.fieldSteps.find(item => item.step === step);
    if (field) (body[field.key] as string) = state === 'SKIPPED' ? 'UNSPECIFIED' : this.fields[field.key];
    if (step === 'GOALS') body.goals = state === 'SKIPPED' ? [] : [...this.goals];
    body.onboardingSteps = [...body.onboardingSteps.filter(item => item.step !== step), { step, state, updatedAt: null }];
    await this.send(body);
  }
  async skipAll(): Promise<void> {
    if (!this.canEdit()) return;
    const body = this.body();
    body.experienceLevel = 'UNSPECIFIED'; body.horizon = 'UNSPECIFIED'; body.lossTolerance = 'UNSPECIFIED'; body.goals = [];
    body.onboardingSteps = [...body.onboardingSteps.filter(item => item.step === 'CONTEXT'), ...STEPS.filter(step => step !== 'CONTEXT').map(step => ({ step, state: 'SKIPPED' as const, updatedAt: null }))];
    if (await this.send(body)) this.selectedStep.set(4);
  }
  async saveContext(): Promise<void> {
    if (!this.canEdit() || !this.runs().some(run => run.runId === this.chosenRun())) return;
    const body = this.body(); body.contextReference = { context: 'DEMO', namespace: 'demo', demoRunId: this.chosenRun() };
    body.onboardingSteps = [...body.onboardingSteps.filter(step => step.step !== 'CONTEXT'), { step: 'CONTEXT', state: 'COMPLETED', updatedAt: null }];
    await this.send(body);
  }
  async reset(): Promise<void> { if (!this.canEdit() || !this.resetReview()) return; const body = this.body(); body.reset = true; if (await this.send(body)) { this.resetReview.set(false); this.selectedStep.set(0); } }
  private async send(body: Update, key: string = crypto.randomUUID()): Promise<boolean> {
    this.saving.set(true); this.error.set(''); this.message.set('');
    try {
      const value = await this.read(this.api.updateInvestmentPreferences(key, body));
      if (this.disposed) return false;
      this.accept(value); this.pending.set(null); this.message.set(`Präferenzen in Version ${value.version} bestätigt gespeichert. Keine Handels- oder Datenfreigabe geändert.`); return true;
    } catch (error) {
      const status = httpStatus(error);
      if (status === 409) { this.pending.set(null); this.error.set('Versionskonflikt: Ein anderer Browserstand hat gespeichert. Aktuellen Stand neu laden und Änderungen bewusst erneut prüfen. Es wurde nichts überschrieben.'); this.current.set(null); }
      else if (status === 401) this.handleError(error, true);
      else if (status === null || status === 0 || status >= 500) { this.pending.set({ key, body }); this.error.set('Speicherergebnis ungeklärt. Nur denselben Vorgang mit unverändertem Schlüssel erneut prüfen. Neue Änderungen sind gesperrt.'); }
      else this.error.set('Präferenzen wurden nicht bestätigt. Eingaben und aktuelle Version prüfen; keine Berechtigung wurde geändert.');
      return false;
    } finally { if (!this.disposed) this.saving.set(false); }
  }
  async retry(): Promise<void> { const pending = this.pending(); if (pending && !this.saving()) await this.send(pending.body, pending.key); }
  private handleError(error: unknown, write: boolean): void {
    if (this.disposed) return;
    if (httpStatus(error) === 401) { this.unauthorized.set(true); this.current.set(null); this.pending.set(null); this.pendingDemo.set(null); this.fields = emptyFields(); this.goals = []; this.runs.set([]); this.snapshot.set(null); this.error.set('Lokale Sitzung erforderlich oder abgelaufen. Flüchtige Eingaben sind geleert. Nach Anmeldung wird nur gelesen; keine Aktion wird wiederholt.'); }
    else this.error.set(write ? 'Speichern nicht bestätigt. Aktuellen Stand erneut lesen.' : 'Bestätigter Stand nicht verfügbar oder ungültig. Keine Ersatzdaten wurden eingesetzt. Erneut lesen.');
  }
  async loadRuns(): Promise<void> {
    try { const page = await this.read(this.demo.listDemoRuns()); this.runs.set(page.items.filter(run => this.validRun(run))); this.runsError.set(false); }
    catch (error) { this.runsError.set(true); if (httpStatus(error) === 401) this.handleError(error, false); }
  }
  async createDemo(): Promise<void> {
    if ((!this.canEdit() && !this.pendingDemo()) || this.demoBusy() || this.unauthorized()) return;
    this.demoBusy.set(true); this.demoError.set('');
    const pending = this.pendingDemo() ?? { key: crypto.randomUUID() }; this.pendingDemo.set(pending);
    try {
      const run = await this.read(this.demo.createDemoRun(pending.key, { initialBudget: '100', currency: 'USD', strategy: 'mock-neutral' }));
      if (!this.validRun(run)) throw new Error('Invalid demo');
      this.pendingDemo.set(null); this.runs.update(runs => [run, ...runs.filter(item => item.runId !== run.runId)]); this.chosenRun.set(run.runId); this.message.set('Isolierter synthetischer Demolauf bestätigt angelegt. Noch nicht als Darstellungskontext gespeichert.');
    } catch (error) { if (httpStatus(error) === 401) this.handleError(error, false); else this.demoError.set('Demoanlage nicht bestätigt. Denselben Anlagevorgang erneut prüfen; keine zweite Demo automatisch anlegen.'); }
    finally { if (!this.disposed) this.demoBusy.set(false); }
  }
  private validRun(run: DemoRun): boolean { return run.simulationOnly === true && ID.test(run.runId) && ['RUNNING','SUCCEEDED','FAILED','PAUSED'].includes(run.status) && typeof run.createdAt === 'string' && utc(run.createdAt); }
  async loadDemo(): Promise<void> {
    if (!this.demoRouteId || this.savedDemo() !== this.demoRouteId || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(this.demoRouteId)) { this.demoError.set('Dieser Demolauf ist nicht der gespeicherte explizite Kontext. Bitte zuerst bewusst auswählen.'); return; }
    this.demoBusy.set(true); this.demoError.set('');
    try { const value = await this.read(this.demo.getDemoSnapshot(this.demoRouteId)); this.snapshot.set(projectSnapshot(value)); }
    catch (error) { if (httpStatus(error) === 401) this.handleError(error, false); else this.demoError.set('Der bestätigte Demosnapshot ist noch nicht verfügbar. Kein anderer Lauf wurde gewählt. Erneut lesen.'); }
    finally { if (!this.disposed) this.demoBusy.set(false); }
  }
  openDemo(): void { const id = this.savedDemo(); if (id) void this.router.navigate(['/onboarding/demo', id]); }
}
