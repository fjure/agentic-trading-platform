import { JsonPipe } from '@angular/common';
import { afterNextRender, ChangeDetectionStrategy, Component, computed, DestroyRef, ElementRef, inject, Injector, OnInit, output, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { firstValueFrom, forkJoin } from 'rxjs';
import { AgentResearchService } from '../api/generated/control-api/api/agentResearch.service';
import { ResearchService } from '../api/generated/control-api/api/research.service';
import type {
  AgentCreateDialogueTurnV1, AgentDialogueTurnV1, AgentDialogueV1, AgentIdeaVersionV1,
  AgentDialogueDecisionV1, AgentHypothesisField, AgentHypothesisProposalV1, AgentLocalHypothesisQuestion,
  AgentProviderCapabilityV1, AgentResearchHypothesis, ProcessingDecisionV1, ProcessingPreviewV1,
  AgentQuantApprovalDecisionV1, AgentQuantApprovalPageV1, AgentQuantApprovalV1, AgentReportPageV1, AgentReportV1,
  AgentProcessingState, AgentRegistryPageV1, AgentReportPhaseV1, AgentRunEventPageV1, AgentRunPageV1, AgentScheduleV1,
  AgentTestabilityClass,
} from '../api/generated/control-api/model/models';
import { httpStatus, safeError } from '../state/supervisor-api';

const RESEARCH_ERRORS: ReadonlyMap<string, string> = new Map([
  ['VERSION_CONFLICT', 'Der Researchstand wurde geändert. Lade den aktuellen Stand.'],
  ['MODEL_CONTEXT_UNCLASSIFIED', 'Der Text konnte nicht sicher klassifiziert werden. Formuliere ihn ohne vertrauliche Angaben neu oder kläre die Frage lokal.'],
  ['MODEL_CONTEXT_DENIED', 'Die Datenrichtlinie sperrt diese Übermittlung. Die lokale Klärung bleibt verfügbar.'],
  ['PROCESSING_GRANT_INVALID', 'Die Sendefreigabe ist ungültig. Lade den Dialog und prüfe eine neue Vorschau.'],
  ['PROCESSING_GRANT_REVOKED', 'Die Sendefreigabe wurde widerrufen.'],
  ['PROCESSING_PREVIEW_UNAVAILABLE', 'Für diesen Beitrag ist keine sendbare Vorschau verfügbar. Prüfe den aktuellen Dialogstatus.'],
  ['APPROVAL_EXPIRED', 'Die Quant-Freigabe ist abgelaufen.'],
  ['APPROVAL_STALE', 'Die Quant-Freigabe gehört zu einem früheren Researchstand. Lade die Freigaben neu.'],
  ['APPROVAL_ALREADY_DECIDED', 'Für diese Quant-Freigabe liegt bereits eine Entscheidung vor.'],
]);

function researchError(error: unknown): string {
  if (typeof error === 'object' && error !== null && 'error' in error) {
    const body = error.error;
    if (typeof body === 'object' && body !== null && 'code' in body && typeof body.code === 'string') {
      const description = RESEARCH_ERRORS.get(body.code);
      if (description) return `${body.code} · ${description}`;
    }
  }
  return safeError(error);
}

@Component({
  selector: 'atl-agent-companion',
  imports: [JsonPipe, ReactiveFormsModule, MatButtonModule, MatCardModule, MatFormFieldModule, MatInputModule],
  templateUrl: './agent-companion.component.html',
  styles: `
    form { display: flex; align-items: start; flex-wrap: wrap; gap: .75rem; }
    form fieldset { flex-basis: 100%; }
    section, article { margin-block: 1rem; }
    pre, code { white-space: pre-wrap; overflow-wrap: anywhere; }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AgentCompanionComponent implements OnInit {
  private readonly api = inject(AgentResearchService);
  private readonly quant = inject(ResearchService);
  private readonly destroy = inject(DestroyRef);
  private readonly injector = inject(Injector);
  private readonly ideaHeading = viewChild<ElementRef<HTMLElement>>('ideaHeading');
  private readonly previewHeading = viewChild<ElementRef<HTMLElement>>('previewHeading');
  private requestKey: { title: string; originalText: string; key: string } | null = null;
  private turnKey: { fingerprint: string; key: string } | null = null;
  private active = true;
  readonly sessionLost = output<void>();
  readonly ideas = signal<AgentIdeaVersionV1[]>([]);
  readonly selected = signal<AgentIdeaVersionV1 | null>(null);
  readonly dialogue = signal<AgentDialogueV1 | null>(null);
  readonly activeProcessing = computed(() => {
    const dialogue = this.dialogue();
    return dialogue?.turns.some(turn => turn.dialogueVersion === dialogue.version &&
      (turn.processing.state === 'SEND_AUTHORIZED' || turn.processing.state === 'IN_FLIGHT')) ?? false;
  });
  readonly providers = signal<AgentProviderCapabilityV1[]>([]);
  readonly providerCursor = signal<string | undefined>(undefined);
  readonly preview = signal<ProcessingPreviewV1 | null>(null);
  readonly advanced = signal(false);
  readonly approvals = signal<AgentQuantApprovalPageV1 | null>(null);
  readonly approval = signal<AgentQuantApprovalV1 | null>(null);
  readonly reports = signal<AgentReportPageV1 | null>(null);
  readonly report = signal<AgentReportV1 | null>(null);
  readonly diagnostics = signal<{ registry: AgentRegistryPageV1; runs: AgentRunPageV1; schedule: AgentScheduleV1 } | null>(null);
  readonly runEvents = signal<AgentRunEventPageV1 | null>(null);
  readonly previewExpired = signal(false);
  private expiryTimer?: ReturnType<typeof setTimeout>;
  readonly processingLabels: Record<AgentProcessingState, string> = {
    LOCAL_CLARIFICATION: 'Lokale Klärung',
    AWAITING_DECISION: 'Übermittlung wartet auf deine Entscheidung',
    SEND_AUTHORIZED: 'Übermittlung freigegeben',
    IN_FLIGHT: 'Provider verarbeitet die freigegebene Fassung',
    PROPOSAL_VALIDATED: 'Hypothesenvorschlag liegt zur Prüfung vor',
    BLOCKED: 'Übermittlung blockiert',
    DECLINED: 'Übermittlung abgelehnt',
    REVOKED: 'Sendefreigabe widerrufen',
  };
  readonly testabilityLabels: Record<AgentTestabilityClass, string> = {
    EXACTLY_TESTABLE: 'Exakt testbar',
    TESTABLE_WITH_DECLARED_APPROXIMATION: 'Mit deklarierter Approximation testbar',
    REQUIRES_NEW_STRATEGY: 'Benötigt eine neue registrierte Strategie',
    INSUFFICIENT_DATA: 'Unzureichende Daten',
    UNSUPPORTED: 'Derzeit nicht unterstützt',
  };
  readonly hypothesisFields: ReadonlyArray<{ field: AgentHypothesisField; key: keyof AgentResearchHypothesis; label: string }> = [
    { field: 'MARKET', key: 'market', label: 'Markt' },
    { field: 'TRIGGER', key: 'trigger', label: 'Auslöser' },
    { field: 'SIGNAL', key: 'signal', label: 'Signal' },
    { field: 'OBSERVATION_WINDOW', key: 'observationWindow', label: 'Beobachtungsfenster' },
    { field: 'TARGET', key: 'target', label: 'Zielgröße' },
    { field: 'MEASUREMENT_HORIZON', key: 'measurementHorizon', label: 'Messhorizont' },
    { field: 'BENCHMARK', key: 'benchmark', label: 'Benchmark' },
    { field: 'EVALUATION_CRITERION', key: 'evaluationCriterion', label: 'Bewertungskriterium' },
  ];
  readonly cursor = signal<string | null>(null);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly followUp = new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.maxLength(2048)] });
  readonly form = new FormGroup({
    title: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.maxLength(160)] }),
    originalText: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.maxLength(2048)] }),
  });

  ngOnInit(): void {
    this.destroy.onDestroy(() => {
      this.active = false;
      clearTimeout(this.expiryTimer);
    });
    void this.reload();
  }

  async reload(append = false): Promise<void> {
    await this.perform(async () => {
      const page = await firstValueFrom(this.api.listAgentIdeas(append ? this.cursor() ?? undefined : undefined, 30).pipe(takeUntilDestroyed(this.destroy)));
      if (!this.active) return;
      const items = append ? [...this.ideas(), ...page.items] : page.items;
      this.ideas.set([...new Map(items.map(idea => [idea.id, idea])).values()]);
      this.cursor.set(page.nextCursor);
      if (!append) {
        const selection = new URL(window.location.href).searchParams;
        const id = selection.get('researchIdea');
        if (id) await this.loadIdea(id);
        const approval = selection.get('researchApproval');
        if (approval && this.active) await this.readApproval(approval);
        const report = selection.get('researchReport');
        const version = Number(selection.get('researchReportVersion'));
        if (report && this.active && Number.isSafeInteger(version) && version > 0) await this.readReport(report, version);
      }
    });
  }

  async open(id: string): Promise<void> {
    await this.perform(async () => {
      await this.loadIdea(id);
      afterNextRender(() => this.ideaHeading()?.nativeElement.focus(), { injector: this.injector });
    });
  }

  private async loadIdea(id: string): Promise<void> {
    const idea = await firstValueFrom(this.api.getAgentIdea(id).pipe(takeUntilDestroyed(this.destroy)));
    if (!this.active) return;
    const dialogue = idea.dialogueId
      ? await firstValueFrom(this.api.getAgentDialogue(idea.dialogueId).pipe(takeUntilDestroyed(this.destroy)))
      : null;
    if (!this.active) return;
    if (this.selected()?.id !== id) {
      this.followUp.reset();
      this.turnKey = null;
    }
    this.preview.set(null);
    this.selected.set(idea);
    this.dialogue.set(dialogue);
    this.ideas.update(items => items.map(item => item.id === idea.id ? idea : item));
    this.rememberSelection({ researchIdea: id });
  }

  async create(): Promise<void> {
    if (this.form.invalid) return;
    const raw = this.form.getRawValue();
    const payload = { title: raw.title.trim(), originalText: raw.originalText };
    if (!payload.title || !payload.originalText.trim()) return;
    await this.perform(async () => {
      if (this.requestKey?.title !== payload.title || this.requestKey.originalText !== payload.originalText) {
        this.requestKey = { ...payload, key: crypto.randomUUID() };
      }
      const idea = await firstValueFrom(this.api.createAgentIdea(this.requestKey.key, payload).pipe(takeUntilDestroyed(this.destroy)));
      if (!this.active) return;
      this.requestKey = null;
      this.form.reset();
      this.ideas.update(items => [idea, ...items.filter(item => item.id !== idea.id)]);
      await this.loadIdea(idea.id);
      afterNextRender(() => this.ideaHeading()?.nativeElement.focus(), { injector: this.injector });
    });
  }

  async bindExperiment(id: string): Promise<void> {
    const idea = this.selected();
    const dialogue = this.dialogue();
    if (!idea || !id.trim() || dialogue?.version !== 1 || dialogue.expertExperiment) return;
    await this.perform(async () => {
      const definition = await firstValueFrom(this.quant.getQuantLabExperiment(id.trim())
        .pipe(takeUntilDestroyed(this.destroy)));
      if (!this.active) return;
      const payload = {
        ideaId: idea.id, expectedIdeaVersion: idea.version,
        expertExperiment: {
          definitionId: definition.id,
          definitionVersion: definition.definitionVersion,
          definitionHash: definition.definitionHash,
        },
      };
      await firstValueFrom(this.api.createAgentDialogue(this.keyFor(payload), payload)
        .pipe(takeUntilDestroyed(this.destroy)));
      await this.loadIdea(idea.id);
    });
  }

  async loadMoreTurns(): Promise<void> {
    const dialogue = this.dialogue();
    if (!dialogue?.nextCursor) return;
    await this.perform(async () => {
      const page = await firstValueFrom(this.api.getAgentDialogue(dialogue.id, dialogue.nextCursor ?? undefined)
        .pipe(takeUntilDestroyed(this.destroy)));
      if (this.active) this.dialogue.set({ ...page, turns: [...dialogue.turns, ...page.turns] });
    });
  }

  async addTurn(useOriginal = false): Promise<void> {
    const dialogue = this.dialogue();
    const text = useOriginal ? this.selected()?.originalText : this.followUp.value;
    if (!dialogue || dialogue.nextCursor || this.activeProcessing() || !text?.trim() ||
      (!useOriginal && this.followUp.invalid)) return;
    await this.saveTurn(dialogue, {
      kind: 'FREE_TEXT_RESEARCH', text,
      expectedDialogueVersion: dialogue.version, expectedIdeaVersion: dialogue.ideaVersion,
    });
  }

  async answer(question: AgentLocalHypothesisQuestion, value: string): Promise<void> {
    const dialogue = this.dialogue();
    if (!dialogue || this.activeProcessing() || !value.trim() || value.length > 240) return;
    await this.saveTurn(dialogue, {
      kind: 'LOCAL_STRUCTURED_ANSWER', questionId: question.id, field: question.field, value: value.trim(),
      expectedDialogueVersion: dialogue.version, expectedIdeaVersion: dialogue.ideaVersion,
    });
  }

  async loadProviders(append = false): Promise<void> {
    await this.perform(async () => {
      const page = await firstValueFrom(this.api.listAgentProviders(append ? this.providerCursor() : undefined, 30)
        .pipe(takeUntilDestroyed(this.destroy)));
      if (this.active) {
        this.providers.update(items => append ? [...items, ...page.items] : page.items);
        this.providerCursor.set(page.nextCursor);
      }
    });
  }

  async loadDiagnostics(): Promise<void> {
    await this.perform(async () => {
      const result = await firstValueFrom(forkJoin({
        registry: this.api.getAgentRegistry(undefined, 30),
        runs: this.api.listResearchAgentRuns(undefined, 30),
        schedule: this.api.getAgentSchedule(),
      }).pipe(takeUntilDestroyed(this.destroy)));
      if (this.active) this.diagnostics.set(result);
    });
  }

  async loadRunEvents(id: string): Promise<void> {
    await this.perform(async () => {
      const events = await firstValueFrom(this.api.listAgentRunEvents(id, undefined, 30)
        .pipe(takeUntilDestroyed(this.destroy)));
      if (this.active) this.runEvents.set(events);
    });
  }

  async loadApprovals(append = false): Promise<void> {
    await this.perform(async () => {
      const prior = append ? this.approvals() : null;
      const page = await firstValueFrom(this.api.listAgentQuantApprovals(prior?.nextCursor ?? undefined, 30)
        .pipe(takeUntilDestroyed(this.destroy)));
      if (this.active) this.approvals.set({ ...page, items: [...(prior?.items ?? []), ...page.items] });
    });
  }

  async openApproval(id: string): Promise<void> {
    await this.perform(() => this.readApproval(id));
  }

  private async readApproval(id: string): Promise<void> {
    const approval = await firstValueFrom(this.api.getAgentQuantApproval(id).pipe(takeUntilDestroyed(this.destroy)));
    if (this.active) {
      this.approval.set(approval);
      this.rememberSelection({ researchApproval: approval.approvalId });
      this.approvals.update(page => page && {
        ...page, items: page.items.map(item => item.approvalId === approval.approvalId ? approval : item),
      });
    }
  }

  async decideQuant(action: AgentQuantApprovalDecisionV1['action']): Promise<void> {
    const approval = this.approval();
    if (!approval || approval.state !== 'PENDING') return;
    await this.perform(async () => {
      const payload = { action, expectedApprovalVersion: approval.approvalVersion, bindingHash: approval.bindingHash };
      await firstValueFrom(this.api.decideAgentQuantApproval(approval.approvalId,
        this.keyFor([approval.approvalId, payload]), payload).pipe(takeUntilDestroyed(this.destroy)));
      await this.readApproval(approval.approvalId);
    });
  }

  async loadReports(append = false): Promise<void> {
    await this.perform(async () => {
      const prior = append ? this.reports() : null;
      const page = await firstValueFrom(this.api.listAgentResearchReports(prior?.nextCursor ?? undefined, 30)
        .pipe(takeUntilDestroyed(this.destroy)));
      if (this.active) this.reports.set({ ...page, items: [...(prior?.items ?? []), ...page.items] });
    });
  }

  async openReport(id: string, version: number): Promise<void> {
    await this.perform(() => this.readReport(id, version));
  }

  private async readReport(id: string, version: number): Promise<void> {
    const report = await firstValueFrom(this.api.getAgentResearchReport(id, version)
      .pipe(takeUntilDestroyed(this.destroy)));
    if (this.active) {
      this.report.set(report);
      this.rememberSelection({ researchReport: id, researchReportVersion: String(version) });
    }
  }

  private rememberSelection(selection: Record<string, string>): void {
    const url = new URL(window.location.href);
    for (const [name, value] of Object.entries(selection)) url.searchParams.set(name, value);
    window.history.replaceState(window.history.state, '', url);
  }

  quantLink(experiment: string, batch?: string): string {
    const url = new URL(window.location.href);
    url.searchParams.set('quantExperiment', experiment);
    url.searchParams.delete('quantReport');
    if (batch) url.searchParams.set('quantBatch', batch);
    else url.searchParams.delete('quantBatch');
    return url.pathname + url.search + url.hash;
  }

  reportPhases(report: AgentReportV1): Array<{ label: string; phase: AgentReportPhaseV1 }> {
    return [
      { label: 'In-Sample', phase: report.phases.inSample },
      { label: 'Validierung', phase: report.phases.validation },
      { label: 'Walk-forward Out-of-Sample', phase: report.phases.walkForwardOos },
      { label: 'Holdout', phase: report.phases.holdout },
    ];
  }

  async showPreview(turn: AgentDialogueTurnV1, profileId: string): Promise<void> {
    const dialogue = this.dialogue();
    if (!dialogue || turn.dialogueVersion !== dialogue.version) return;
    await this.perform(async () => {
      this.preview.set(null);
      const payload = {
        expectedDialogueVersion: dialogue.version, expectedTurnVersion: turn.processing.turnVersion, profileId,
      };
      const preview = await firstValueFrom(this.api.createAgentProcessingPreview(dialogue.id, turn.turnId,
        this.keyFor(['preview', dialogue.id, turn.turnId, payload]), payload).pipe(takeUntilDestroyed(this.destroy)));
      if (!this.active) return;
      this.preview.set(preview);
      this.turnKey = null;
      afterNextRender(() => this.previewHeading()?.nativeElement.focus(), { injector: this.injector });
      clearTimeout(this.expiryTimer);
      const remaining = Date.parse(preview.expiresAt) - Date.now();
      this.previewExpired.set(!(remaining > 0));
      if (remaining > 0) this.expiryTimer = setTimeout(() => this.previewExpired.set(true), remaining);
    });
  }

  async decideProcessing(turn: AgentDialogueTurnV1, action: ProcessingDecisionV1['action']): Promise<void> {
    const dialogue = this.dialogue();
    if (!dialogue || turn.dialogueVersion !== dialogue.version) return;
    const preview = this.preview();
    const receipt = turn.processing.receipt;
    const versions = { expectedTurnVersion: turn.processing.turnVersion, expectedDialogueVersion: dialogue.version };
    let payload: ProcessingDecisionV1;
    switch (action) {
      case 'SEND':
        if (!preview || preview.turnId !== turn.turnId || !(Date.parse(preview.expiresAt) > Date.now())) return;
        payload = { action, expectedTurnVersion: preview.turnVersion, expectedDialogueVersion: preview.dialogueVersion,
          previewId: preview.previewId, projectionBinding: preview.projectionBinding };
        break;
      case 'DECLINE':
        payload = { action, ...versions };
        break;
      case 'REVOKE':
        if (!receipt) return;
        payload = { action, ...versions, receiptId: receipt.receiptId };
        break;
    }
    await this.perform(async () => {
      await firstValueFrom(this.api.decideAgentProcessing(dialogue.id, turn.turnId,
        this.keyFor([dialogue.id, turn.turnId, payload]), payload).pipe(takeUntilDestroyed(this.destroy)));
      await this.loadIdea(dialogue.ideaId);
    });
  }

  async confirmHypothesis(proposal: AgentHypothesisProposalV1, form: HTMLFormElement): Promise<void> {
    const selections = new FormData(form);
    const confirmedFields = this.hypothesisFields.flatMap(({ field, key }) => {
      const value = proposal.interpretation.hypothesis[key];
      return value && selections.has(field) ? [{ field, value }] : [];
    });
    if (!confirmedFields.length) {
      this.error.set('Wähle mindestens ein Hypothesenfeld aus.');
      return;
    }
    const confirmedAssumptionIds = proposal.interpretation.assumptions
      .filter(assumption => selections.getAll('assumption').includes(assumption.id))
      .map(assumption => assumption.id);
    await this.saveHypothesisDecision(proposal, {
      action: 'CONFIRM_HYPOTHESIS', ...this.proposalBinding(proposal), confirmedFields, confirmedAssumptionIds,
    });
  }

  async acceptApproximation(proposal: AgentHypothesisProposalV1): Promise<void> {
    const approximation = proposal.approximation;
    if (!approximation) return;
    await this.saveHypothesisDecision(proposal, {
      action: 'ACCEPT_APPROXIMATION', ...this.proposalBinding(proposal),
      approximationId: approximation.approximationId, approximationHash: approximation.approximationHash,
    });
  }

  async requestChanges(proposal: AgentHypothesisProposalV1, form: HTMLFormElement): Promise<void> {
    const selection = new FormData(form);
    const clarification = String(selection.get('clarification') ?? '').trim();
    const fields = this.hypothesisFields.filter(({ field }) => selection.has(field)).map(({ field }) => field);
    if (!fields.length || !clarification || clarification.length > 320) {
      this.error.set('Wähle die betroffenen Felder aus und beschreibe die gewünschte Änderung.');
      return;
    }
    // The generated Set represents a JSON array with uniqueItems.
    const requestedFields = fields as unknown as Set<AgentHypothesisField>;
    await this.saveHypothesisDecision(proposal, {
      action: 'REQUEST_CHANGES', ...this.proposalBinding(proposal), requestedFields, clarification,
    });
  }

  private proposalBinding(proposal: AgentHypothesisProposalV1) {
    return {
      expectedDialogueVersion: proposal.dialogueVersion, expectedIdeaVersion: proposal.ideaVersion,
      proposalId: proposal.proposalId, proposalHash: proposal.proposalHash,
    };
  }

  private async saveHypothesisDecision(proposal: AgentHypothesisProposalV1, payload: AgentDialogueDecisionV1): Promise<void> {
    const dialogue = this.dialogue();
    if (!dialogue || dialogue.version !== proposal.dialogueVersion || dialogue.ideaVersion !== proposal.ideaVersion) return;
    await this.perform(async () => {
      await firstValueFrom(this.api.decideAgentHypothesis(dialogue.id, this.keyFor([dialogue.id, payload]), payload)
        .pipe(takeUntilDestroyed(this.destroy)));
      await this.loadIdea(dialogue.ideaId);
    });
  }

  private async saveTurn(dialogue: AgentDialogueV1, payload: AgentCreateDialogueTurnV1): Promise<void> {
    await this.perform(async () => {
      await firstValueFrom(this.api.createAgentDialogueTurn(dialogue.id, this.keyFor([dialogue.id, payload]), payload)
        .pipe(takeUntilDestroyed(this.destroy)));
      if (!this.active) return;
      this.turnKey = null;
      this.followUp.reset();
      await this.loadIdea(dialogue.ideaId);
    });
  }

  private keyFor(request: unknown): string {
    const fingerprint = JSON.stringify(request);
    if (this.turnKey?.fingerprint !== fingerprint) {
      this.turnKey = { fingerprint, key: crypto.randomUUID() };
    }
    return this.turnKey.key;
  }

  private async perform(action: () => Promise<void>): Promise<void> {
    if (this.busy() || !this.active) return;
    this.busy.set(true);
    this.error.set('');
    try {
      await action();
    } catch (error) {
      if (!this.active) return;
      this.error.set(researchError(error));
      if (httpStatus(error) === 401) {
        this.active = false;
        this.ideas.set([]);
        this.selected.set(null);
        this.dialogue.set(null);
        this.providers.set([]);
        this.providerCursor.set(undefined);
        this.preview.set(null);
        this.approvals.set(null);
        this.approval.set(null);
        this.reports.set(null);
        this.report.set(null);
        this.diagnostics.set(null);
        this.runEvents.set(null);
        this.requestKey = null;
        this.turnKey = null;
        this.followUp.reset();
        this.form.reset();
        this.sessionLost.emit();
      }
    } finally {
      this.busy.set(false);
    }
  }
}
