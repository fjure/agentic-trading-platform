import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import type {
  QuantLabBatch, QuantLabCandidate, QuantLabCandidateResult, QuantLabComparisonReport,
  QuantLabDrawdownPage, QuantLabEquityPage, QuantLabReliabilityReportPage, QuantLabSelectionValues,
} from '../../api/generated/control-api/model/models';

const phases = { training: 'In-Sample', validation: 'Validation', test: 'Walk-forward OOS', holdout: 'Finaler Holdout' };
const roles = {
  SEARCH_CANDIDATE: 'Suchkandidat', REFERENCE_FLAT: 'Flat-Referenz', REFERENCE_BUY_HOLD: 'Buy-and-Hold-Referenz',
};
const outcomes: Record<string, string> = {
  COMPLETE: 'Vollständig', INCOMPLETE: 'Unvollständig', FAILED: 'Fehlgeschlagen', BLOCKED: 'Blockiert',
  NO_ELIGIBLE_CANDIDATE: 'Kein zulässiger Kandidat', SELECTED: 'Auswahl getroffen',
  NOT_EVALUATED_NO_SELECTION: 'Nicht ausgewertet · keine Auswahl',
  NOT_EVALUATED_BY_PROTOCOL: 'Nicht ausgewertet · nicht ausgewählt',
};
const metricLabels = {
  netReturn: 'Nettorendite', maxDrawdown: 'Maximaler Drawdown', turnover: 'Umschlag',
  costBurden: 'Kostenbelastung', annualizedVolatility: 'Annualisierte Volatilität', sharpe: 'Sharpe',
  oosDegradation: 'OOS-Abweichung', netReturnDifferenceBuyHold: 'Renditedifferenz zu Buy-and-Hold',
  netReturnDifferenceFlat: 'Renditedifferenz zu Flat', observationCoverage: 'Beobachtungsabdeckung',
  fees: 'Gebühren (USD)', slippage: 'Slippage (USD)', totalCosts: 'Gesamtkosten (USD)',
} as const;
const activityLabels = {
  decisionCount: 'Entscheidungen', fillCount: 'Fills', buyFillCount: 'Kauf-Fills', sellFillCount: 'Verkauf-Fills',
  entryCount: 'Einstiege', exitCount: 'Ausstiege', endOpenPositionCount: 'Offene Positionen am Ende',
  investedBarCount: 'Investierte Bars', equityBarCount: 'Equity-Bars',
} as const;
const exposureLabels = {
  investedBarRatio: 'Investierter Baranteil', meanGrossExposureRatio: 'Mittlere Bruttoexposition',
  maxGrossExposureRatio: 'Maximale Bruttoexposition',
} as const;
const entries = <T extends Record<string, string>>(values: T) =>
  Object.entries(values) as [keyof T, string][];

function curveGeometry(points: readonly { at: string; value: string }[]): string {
  const display = points.map(point => ({ x: Date.parse(point.at), y: Number(point.value) }));
  if (!display.length) return '';
  let left = Infinity, right = -Infinity, low = Infinity, high = -Infinity;
  for (const { x, y } of display) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return '';
    left = Math.min(left, x);
    right = Math.max(right, x);
    low = Math.min(low, y);
    high = Math.max(high, y);
  }
  return display.map(point => [
    right === left ? 320 : 10 + 620 * (point.x - left) / (right - left),
    high === low ? 90 : 170 - 160 * (point.y - low) / (high - low),
  ].map(pixel => pixel.toFixed(2)).join(',')).join(' ');
}

@Component({
  selector: 'atl-quant-lab-detail',
  imports: [NgTemplateOutlet, MatButtonModule, MatCardModule],
  templateUrl: './quant-lab-detail.component.html',
  styleUrl: './quant-lab-detail.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class QuantLabDetailComponent {
  readonly batch = input<QuantLabBatch | null>(null);
  readonly comparison = input<QuantLabComparisonReport | null>(null);
  readonly candidates = input<readonly QuantLabCandidate[]>([]);
  readonly results = input<readonly QuantLabCandidateResult[]>([]);
  readonly equity = input<QuantLabEquityPage | null>(null);
  readonly drawdown = input<QuantLabDrawdownPage | null>(null);
  readonly reliability = input<QuantLabReliabilityReportPage | null>(null);
  readonly selectedResult = input<QuantLabCandidateResult | null>(null);
  readonly reportHref = input<string | null>(null);
  readonly candidatesMore = input(false);
  readonly resultsMore = input(false);
  readonly historyMore = input(false);
  readonly busy = input(false);
  readonly selectResult = output<QuantLabCandidateResult>();
  readonly showReliability = output<QuantLabCandidateResult>();
  readonly reliabilitySection = output<QuantLabReliabilityReportPage['section']>();
  readonly moreCandidates = output<void>();
  readonly moreResults = output<void>();
  readonly moreEquity = output<void>();
  readonly moreDrawdown = output<void>();
  readonly moreReliability = output<void>();
  readonly moreHistory = output<void>();

  protected readonly phases = phases;
  protected readonly roles = roles;
  protected readonly outcomes = outcomes;
  protected readonly metricLabels = entries(metricLabels);
  protected readonly activityLabels = entries(activityLabels);
  protected readonly exposureLabels = entries(exposureLabels);
  protected readonly foldLabels = entries({ netReturn: metricLabels.netReturn, maxDrawdown: metricLabels.maxDrawdown, ...exposureLabels });
  protected readonly history = computed(() => this.comparison()?.researchHistory ?? this.batch()?.researchHistory);
  protected readonly candidateNames = computed(() => {
    const names = new Map<string, string>();
    for (const row of [...this.candidates(), ...(this.comparison()?.sensitivity ?? [])]) {
      const params = Object.entries(row.configuration.parameters).map(([key, value]) => `${key}=${value}`).join(', ');
      names.set(row.candidateId, [row.configuration.strategyId, params].filter(Boolean).join(' · '));
    }
    return names;
  });
  protected readonly equityPoints = computed(() =>
    curveGeometry((this.equity()?.items ?? []).map(point => ({ at: point.at, value: point.equity }))));
  protected readonly drawdownPoints = computed(() =>
    curveGeometry((this.drawdown()?.items ?? []).map(point => ({ at: point.at, value: point.drawdown }))));
  protected readonly reliabilityDimensions = computed(() => Object.entries(this.reliability()?.report.dimensions ?? {}));

  protected metric(metric: { value: string | number | null; status: string; reasonCode?: string | null }): string {
    if (metric.value !== null) return String(metric.value);
    return [metric.status, metric.reasonCode].filter(Boolean).join(' · ');
  }

  protected exact(value: QuantLabSelectionValues): string {
    return `Nettorendite ${value.netReturn} · Drawdown ${value.maxDrawdown} · Umschlag ${value.turnover} · investierter Baranteil ${value.investedBarRatio} · Einstiege ${value.entryCount} · vollständige Validation-Sitzungen ${value.completeValidationSessions}`;
  }
}
