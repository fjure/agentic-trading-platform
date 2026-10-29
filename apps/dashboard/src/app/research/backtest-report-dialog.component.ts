import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, inject, output, signal } from '@angular/core';
import { JsonPipe } from '@angular/common';
import { FormControl, ReactiveFormsModule } from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { Subject, firstValueFrom, merge, takeUntil } from 'rxjs';
import { BacktestingService } from '../api/generated/control-api/api/backtesting.service';
import { BacktestReliabilityDimensionName } from '../api/generated/control-api/model/backtestReliabilityDimensionName';
import type { BacktestReliabilityReportPage, BacktestReliabilityReportV1, BacktestReliabilityFinding, BacktestReliabilityReference } from '../api/generated/control-api/model/models';
import { httpStatus, safeError } from '../state/supervisor-api';
import { BacktestContextComponent } from './backtest-context.component';

@Component({
  selector: 'atl-backtest-report-dialog',
  imports: [JsonPipe, ReactiveFormsModule, MatDialogModule, MatButtonModule, MatFormFieldModule, MatSelectModule, BacktestContextComponent],
  templateUrl: './backtest-report-dialog.component.html',
  styleUrl: './backtest-report-dialog.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class BacktestReportDialogComponent implements OnInit, OnDestroy {
  private readonly api = inject(BacktestingService);
  private readonly ref = inject(MatDialogRef<BacktestReportDialogComponent>);
  readonly data = inject<{ reportId: string }>(MAT_DIALOG_DATA);
  readonly sessionLost = output<void>();
  readonly pageLoaded = output<BacktestReliabilityReportPage>();
  readonly report = signal<BacktestReliabilityReportV1 | null>(null);
  readonly findings = signal<BacktestReliabilityFinding[]>([]);
  readonly revisions = signal<BacktestReliabilityReference[]>([]);
  readonly warnings = signal<BacktestReliabilityReportPage['currentWarnings']>([]);
  readonly nextCursor = signal<string | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly section = new FormControl<'findings' | 'revisions'>('findings', { nonNullable: true });
  readonly category = new FormControl<BacktestReliabilityDimensionName | ''>('', { nonNullable: true });
  readonly fold = new FormControl<number | ''>('', { nonNullable: true });
  readonly categories = Object.values(BacktestReliabilityDimensionName);
  readonly dimensions = Object.values(BacktestReliabilityDimensionName);
  private readonly stop = new Subject<void>();
  private readonly filterStop = new Subject<void>();
  private generation = 0;
  private disposed = false;

  ngOnInit(): void {
    merge(this.section.valueChanges, this.category.valueChanges, this.fold.valueChanges)
      .pipe(takeUntil(this.stop)).subscribe(() => this.reset());
    void this.load();
  }
  ngOnDestroy(): void { this.disposed = true; this.generation++; this.filterStop.next(); this.filterStop.complete(); this.stop.next(); this.stop.complete(); }
  close(): void { this.ref.close(); }
  reset(): void {
    this.generation++; this.filterStop.next(); this.findings.set([]); this.revisions.set([]); this.nextCursor.set(null);
    this.loading.set(false); void this.load();
  }
  async load(append = false): Promise<void> {
    if (this.disposed || this.loading()) return;
    const cursor = append ? this.nextCursor() ?? undefined : undefined;
    if (append && !cursor) return;
    const ticket = this.generation, section = this.section.value;
    this.loading.set(true); this.error.set('');
    try {
      const page = await firstValueFrom(this.api.getBacktestReliabilityReport(
        this.data.reportId, 20, cursor, section, this.category.value || undefined,
        this.fold.value === '' ? undefined : this.fold.value,
      ).pipe(takeUntil(this.stop), takeUntil(this.filterStop)));
      if (this.disposed || ticket !== this.generation) return;
      const previous = this.report();
      if (page.report.reportId !== this.data.reportId || page.section !== section
        || (previous && (page.report.reportHash !== previous.reportHash
          || page.report.version !== previous.version
          || page.report.snapshotReference.snapshotVersion !== previous.snapshotReference.snapshotVersion
          || page.report.snapshotReference.snapshotAt !== previous.snapshotReference.snapshotAt))) throw new Error('INVALID_REPORT_PAGE');
      this.report.set(page.report); this.warnings.set(page.currentWarnings); this.nextCursor.set(page.nextCursor);
      this.findings.set(append ? [...this.findings(), ...page.findings] : page.findings);
      this.revisions.set(append ? [...this.revisions(), ...page.revisionReferences] : page.revisionReferences);
      this.pageLoaded.emit(page);
    } catch (error) {
      if (this.disposed || ticket !== this.generation) return;
      this.error.set(safeError(error)); this.nextCursor.set(null);
      if (httpStatus(error) === 401) { this.sessionLost.emit(); this.ref.close(); }
    } finally { if (!this.disposed && ticket === this.generation) this.loading.set(false); }
  }
}
