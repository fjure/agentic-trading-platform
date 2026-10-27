import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import type { ReadinessExpectedBarRatio, ReadinessLatencyMetric, ReadinessTimelinessMetrics, ReadinessWindows, ReadinessWindowsBarSignalInner } from '../api/generated/control-api/model/models';

@Component({
  selector: 'atl-readiness-metrics',
  templateUrl: './readiness-metrics.component.html',
  styleUrl: './readiness.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReadinessMetricsComponent {
  readonly metrics = input<ReadinessTimelinessMetrics[]>([]);
  readonly windows = input.required<ReadinessWindows>();
  ratios(metric: ReadinessTimelinessMetrics): { label: string; value: ReadinessExpectedBarRatio }[] {
    return [
      { label: 'Empfangen bis +60s', value: metric.receivedBySignalDeadline },
      { label: 'Empfangen bis +300s', value: metric.receivedByOutcomeDeadline },
      { label: 'Verwendbar bis +60s', value: metric.usableBySignalDeadline },
      { label: 'Verwendbar bis +300s', value: metric.usableByOutcomeDeadline },
      { label: 'Fehlende Bars', value: metric.missingBars },
      { label: 'Verspätet für Signal', value: metric.lateForSignal },
      { label: 'Verspätet für Outcome', value: metric.lateForOutcome },
    ];
  }
  latencies(metric: ReadinessTimelinessMetrics): { label: string; value: ReadinessLatencyMetric }[] {
    return [
      { label: 'Empfang minus Barende', value: metric.receptionMinusClose },
      { label: 'DBaufnahme minus Empfang', value: metric.ingestionMinusReception },
      { label: 'Commitbestätigung minus DBaufnahme', value: metric.confirmationMinusIngestion },
    ];
  }
  windowGroups(): { label: string; items: ReadinessWindowsBarSignalInner[] }[] {
    const value = this.windows();
    return [
      { label: 'Barfenster für Signale', items: value.barSignal },
      { label: 'Barfenster für Outcomes', items: value.barOutcome },
      { label: 'Strict-PIT-Signalfenster', items: value.strictSignal },
      { label: 'Strict-PIT-Outcomefenster', items: value.strictOutcome },
      { label: 'Vollständige Experimentfenster', items: value.completeExperiment },
    ];
  }
}
