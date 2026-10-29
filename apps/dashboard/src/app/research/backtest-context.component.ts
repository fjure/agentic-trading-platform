import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { JsonPipe } from '@angular/common';
import type { BacktestReliabilityReportV1, BacktestSnapshotReference } from '../api/generated/control-api/model/models';

export interface BacktestContextValue {
  researchMode?: string;
  experimentVersion?: string;
  snapshotReference?: BacktestSnapshotReference | null;
  snapshot?: { version: number; committedAt: string };
  resultLimitations?: string[];
}

@Component({
  selector: 'atl-backtest-context',
  imports: [JsonPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <dl aria-label="Forschungskontext">
      <dt>Research Mode</dt><dd>{{ value()?.researchMode || 'STRICT_PIT' }}</dd>
      <dt>Data Snapshot</dt><dd>{{ (value()?.snapshotReference || value()?.snapshot || null) | json }}</dd>
      <dt>Data Quality</dt><dd>
        @if (report(); as report) { INTEGRITY {{ report.dimensions.INTEGRITY.rating }} · CALENDAR {{ report.dimensions.CALENDAR.rating }} }
        @else { {{ retrospective() ? 'Noch nicht geladen / ausgewertet' : 'Kein separater Report (Strict PIT)' }} }
      </dd>
      <dt>Corporate Action Status</dt><dd>{{ report()?.corporateActionStatus || (retrospective() ? 'Noch nicht geladen / ausgewertet' : 'Kein separater Report (Strict PIT)') }}</dd>
      <dt>Result Limitations</dt><dd>{{ value()?.resultLimitations?.join(', ') || (retrospective() ? 'Noch nicht geliefert' : 'Keine retrospektiven Zusatzangaben (Strict PIT)') }}</dd>
      <dt>Experiment Version</dt><dd>{{ value()?.experimentVersion || 'Nicht geliefert' }}</dd>
    </dl>
  `,
  styles: [`:host { display: block; } dl { display: grid; grid-template-columns: minmax(9rem, 1fr) 3fr; gap: .35rem .75rem; } dt { font-weight: 600; } dd { margin: 0; overflow-wrap: anywhere; } @media(max-width: 540px) { dl { grid-template-columns: 1fr; } dd { margin-bottom: .5rem; } }`],
})
export class BacktestContextComponent {
  readonly value = input<BacktestContextValue | null>(null);
  readonly report = input<BacktestReliabilityReportV1 | null>(null);
  retrospective(): boolean { return this.value()?.researchMode === 'RETROSPECTIVE_EXPLORATORY'; }
}
