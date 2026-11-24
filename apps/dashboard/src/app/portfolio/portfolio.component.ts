import { NgTemplateOutlet } from '@angular/common';
import { BreakpointObserver } from '@angular/cdk/layout';
import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { ActivatedRoute, ParamMap, RouterLink } from '@angular/router';
import { Observable, Subscription, map, of, switchMap } from 'rxjs';
import { ExperienceService } from '../api/generated/control-api/api/experience.service';
import { InvestmentContextV1 as Context } from '../api/generated/control-api/model/investmentContextV1';
import type { InvestmentReasonCodeV1 as Reason } from '../api/generated/control-api/model/investmentReasonCodeV1';
import {
  InvalidPublicDto, PortfolioMetric, PortfolioPosition, PortfolioView, projectInvestmentPortfolio,
} from '../state/public-dto-schema';
import { httpStatus } from '../state/supervisor-api';

const PAGE_SIZE = 20;
const DEMO_ID = /^[A-Za-z0-9._:-]{1,128}$/;
const NAMESPACES = { FAKE: 'fake', DEMO: 'demo', ALPACA_PAPER: 'alpaca-paper' };
type Source = { context: Context; demoRunId?: string };
type Selection = Source | null;
type Failure = 'invalid-link' | 'missing-demo' | 'unauthorized' | 'unavailable' | 'offline' | 'conflict';

class MissingDemo extends Error {}
class SnapshotChanged extends Error {}

@Component({
  selector: 'atl-portfolio',
  imports: [MatButtonModule, MatCardModule, NgTemplateOutlet, RouterLink],
  templateUrl: './portfolio.component.html',
  styleUrl: './portfolio.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioComponent implements OnInit {
  private readonly api = inject(ExperienceService);
  private readonly route = inject(ActivatedRoute);
  private readonly destroy = inject(DestroyRef);
  private activeRequest?: Subscription;
  private selection: Selection = null;

  readonly context = signal<Context | null>(null);
  readonly view = signal<PortfolioView | null>(null);
  readonly loading = signal(false);
  readonly failure = signal<Failure | null>(null);
  readonly selectedPosition = signal<PortfolioPosition | null>(null);
  readonly mobile = toSignal(inject(BreakpointObserver).observe('(max-width: 767px)'));

  readonly contextNames: Record<Context, string> = {
    FAKE: 'Fake-Ledger', DEMO: 'Gespeicherte Demo', ALPACA_PAPER: 'Alpaca Paper',
  };
  readonly sources = [Context.Fake, Context.Demo, Context.AlpacaPaper] as const;
  readonly qualityLabels = {
    CURRENT: 'Bestätigter Datenstand', STALE: 'Veralteter Datenstand',
    UNAVAILABLE: 'Datenbasis nicht verfügbar', INCONSISTENT: 'Datenstand nicht konsistent',
  };
  readonly reconciliationLabels = {
    CURRENT: 'Abgleich bestätigt', REQUIRED: 'Abgleich erforderlich',
    UNKNOWN: 'Abgleich nicht bestätigt', NOT_APPLICABLE: 'Kein Brokerabgleich für diese Quelle',
  };
  readonly failureLabels: Record<Failure, string> = {
    'invalid-link': 'Der Link enthält eine ungültige Quellenauswahl. Wählen Sie eine Quelle.',
    'missing-demo': 'Für diese Ansicht ist keine passende Demo gespeichert. Wählen Sie eine Demo in der Einführung.',
    unauthorized: 'Ihre lokale Sitzung fehlt oder ist abgelaufen. Aktivieren Sie eine Sitzung und öffnen Sie das Portfolio erneut.',
    unavailable: 'Die gewählte Quelle konnte nicht bestätigt werden. Sie können den Bestand erneut laden.',
    offline: 'Die gewählte Quelle ist derzeit nicht erreichbar. Versuchen Sie es erneut.',
    conflict: 'Der Snapshot hat sich geändert oder ist abgelaufen. Laden Sie den aktuellen Stand ab der ersten Seite.',
  };
  readonly primaryMetrics = [
    ['cash', 'Cash'], ['equity', 'Eigenkapital'], ['exposure', 'Bruttoexposure'],
  ] as const;
  readonly additionalMetrics = [
    ['marketValue', 'Marktwert'], ['realizedPnl', 'Realisiertes Ergebnis'],
    ['unrealizedPnl', 'Unrealisiertes Ergebnis'], ['dailyReturn', 'Tagesrendite'],
    ['totalReturn', 'Gesamtrendite'], ['drawdown', 'Historischer Drawdown'],
  ] as const;
  readonly positionMetrics = [
    ['quantity', 'Menge'], ['averagePrice', 'Durchschnittlicher Einstandspreis'],
    ['costBasis', 'Kostenbasis'], ['marketValue', 'Marktwert'],
    ['realizedPnl', 'Realisiertes Ergebnis'], ['unrealizedPnl', 'Unrealisiertes Ergebnis'],
    ['weight', 'Portfoliogewicht'],
  ] as const;

  ngOnInit(): void {
    this.route.queryParamMap.pipe(takeUntilDestroyed(this.destroy)).subscribe(params => this.open(params));
  }

  private open(params: ParamMap): void {
    const context = params.get('context');
    const demoRunId = params.get('demoRunId');
    const valid = params.getAll('context').length <= 1 && params.getAll('demoRunId').length <= 1 &&
      (context === null || Object.values(Context).includes(context as Context)) &&
      (demoRunId === null || (context === Context.Demo && DEMO_ID.test(demoRunId)));
    this.selection = valid && context ? { context: context as Context, demoRunId: demoRunId ?? undefined } : null;
    this.context.set(this.selection?.context ?? null);
    this.reload();
    if (!valid) this.failure.set('invalid-link');
  }

  reload(): void {
    this.activeRequest?.unsubscribe();
    this.view.set(null);
    this.selectedPosition.set(null);
    this.failure.set(null);
    this.loading.set(false);
    if (this.selection) this.read(this.resolveSource(this.selection));
  }

  private resolveSource(selection: Source): Observable<Source> {
    if (selection.context !== Context.Demo) return of(selection);
    return this.api.getInvestmentPreferences('body', false, { transferCache: false }).pipe(map(preferences => {
      const reference = preferences.contextReference;
      const run = reference?.demoRunId;
      if (preferences.schemaVersion !== '1' || reference?.context !== Context.Demo ||
          reference.namespace !== NAMESPACES.DEMO || !run || !DEMO_ID.test(run) ||
          (selection.demoRunId !== undefined && run !== selection.demoRunId)) throw new MissingDemo();
      return { context: Context.Demo, demoRunId: run };
    }));
  }

  morePositions(): void {
    const view = this.view();
    if (!view?.nextCursor || this.loading()) return;
    this.read(of(view.contextReference), view);
  }

  private read(source: Observable<Source>, previous?: PortfolioView): void {
    this.loading.set(true);
    this.activeRequest = source.pipe(
      switchMap(({ context, demoRunId }) => this.api.getInvestmentPortfolio(
        context, demoRunId, PAGE_SIZE, previous?.nextCursor ?? undefined, 'body', false, { transferCache: false },
      ).pipe(map(response => {
        const view = projectInvestmentPortfolio(response);
        const reference = view.contextReference;
        if (reference.context !== context || reference.namespace !== NAMESPACES[context] ||
            reference.demoRunId !== demoRunId) throw new InvalidPublicDto();
        if (!previous) return view;
        if (previous.snapshotId !== view.snapshotId || previous.snapshotVersion !== view.snapshotVersion ||
            previous.portfolioLedgerVersion !== view.portfolioLedgerVersion ||
            previous.accountGeneration !== view.accountGeneration || previous.asOf !== view.asOf ||
            view.nextCursor === previous.nextCursor || !previous.positions || !view.positions?.length) {
          throw new SnapshotChanged();
        }
        return { ...previous, nextCursor: view.nextCursor, positions: [...previous.positions, ...view.positions] };
      }))),
      takeUntilDestroyed(this.destroy),
    ).subscribe({
      next: view => {
        this.view.set(view);
        this.loading.set(false);
      },
      error: error => {
        this.view.set(null);
        this.selectedPosition.set(null);
        this.failure.set(this.classify(error));
        this.loading.set(false);
      },
    });
  }

  private classify(error: unknown): Failure {
    const status = httpStatus(error);
    if (error instanceof MissingDemo) return 'missing-demo';
    if (status === 401) return 'unauthorized';
    if (status === 409 || error instanceof SnapshotChanged) return 'conflict';
    if (status === 0 || (status !== null && status >= 500)) return 'offline';
    return 'unavailable';
  }

  togglePosition(position: PortfolioPosition): void {
    this.selectedPosition.update(selected => selected === position ? null : position);
  }

  metricValue(metric: PortfolioMetric): string {
    if (metric.value === null) return 'Nicht verfügbar';
    return metric.unit === 'USD' ? `${metric.value} ${metric.currency}` : metric.value;
  }

  reasonLabel(reason: Reason): string {
    const labels: Partial<Record<Reason, string>> = {
      LEDGER_NOT_PREPARED: 'Für diese Quelle ist keine Finanzbasis bestätigt.',
      VALUATION_NOT_AVAILABLE: 'Eine passende Bewertung oder Kostenbasis fehlt.',
      PERFORMANCE_HISTORY_NOT_AVAILABLE: 'Die belegte Historie einschließlich Zahlungsflüssen fehlt.',
      ADMISSION_PREVIEW_REQUIRED: 'Verfügbare Mittel und Mengen erfordern eine gesonderte Prüfung.',
      FEE_EVIDENCE_MISSING: 'Gebühren sind nicht vollständig belegt.',
      SNAPSHOT_INCONSISTENT: 'Die Quellrevisionen passen nicht zusammen.',
      SOURCE_REVISION_UNAVAILABLE: 'Die Quellrevision ist nicht belegt.',
      STALE_EVIDENCE: 'Der letzte Beleg ist veraltet.',
      RECONCILIATION_REQUIRED: 'Der Kontoabgleich steht aus.',
      METHOD_INPUTS_UNAVAILABLE: 'Belegte Eingabedaten für die Berechnung fehlen.',
    };
    return labels[reason] ?? 'Der Quellbeleg ist für diese Kennzahl nicht ausreichend.';
  }
}
