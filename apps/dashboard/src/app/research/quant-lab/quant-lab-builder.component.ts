import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormArray, FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import type {
  BacktestResearchMode, QuantLabExperimentCreateRequest, QuantLabInterval, QuantLabSearchSpace,
  QuantLabStrategyCatalog, QuantLabStrategyId, QuantLabStrategySelectionPolicy, ResearchInstrument,
} from '../../api/generated/control-api/model/models';

const UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:(?:00|15|30|45):00(?:\.0{1,6})?Z$/;
const UNIT = /^(?:0(?:\.\d{1,12})?|1(?:\.0{1,12})?)$/;
const DECIMAL = /^(?:0|[1-9]\d*)(?:\.\d{1,8})?$/;
const THRESHOLD = /^(?:(?:0|[1-9]\d?)(?:\.\d{1,4})?|100(?:\.0{1,4})?)$/;
const LIMIT = 32;
const control = (value = '') => new FormControl(value, { nonNullable: true });
const boundary = () => new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(UTC)] });
const interval = () => new FormGroup({ start: boundary(), end: boundary() });
const fold = () => new FormGroup({ training: interval(), validation: interval(), test: interval() });
const search = (strategyId: QuantLabStrategyId = 'ema-cross-v1') => new FormGroup({
  strategyId: new FormControl<QuantLabStrategyId>(strategyId, { nonNullable: true }),
  fast: control('5, 10, 15'),
  slow: control('30, 40, 50'),
  period: control('14'),
  entry: control('30'),
  exit: control('60'),
});

function parts(value: string): string[] {
  const items = value.split(',').map(item => item.trim());
  if (items.length > LIMIT || items.some(item => !item)) throw new Error('Jede Parameterliste braucht 1 bis 32 Werte.');
  return items;
}

function whole(value: string, min: number, max: number, label: string): number {
  if (!/^(?:0|[1-9]\d*)$/.test(value) || Number(value) < min || Number(value) > max) {
    throw new Error(label + ' muss eine ganze Zahl von ' + min + ' bis ' + max + ' sein.');
  }
  return Number(value);
}

function unique<T>(items: T[]): T[] {
  if (new Set(items).size !== items.length) throw new Error('Parameterlisten dürfen keine doppelten Werte enthalten.');
  return items;
}

function canonicalDecimal(value: string): string {
  return value.includes('.') ? value.replace(/0+$/, '').replace(/\.$/, '') : value;
}

function threshold(value: string): string[] {
  return unique(parts(value).map(item => {
    if (!THRESHOLD.test(item)) throw new Error('RSI-Schwellen müssen zwischen 0 und 100 liegen, mit höchstens vier Nachkommastellen.');
    return canonicalDecimal(item);
  }));
}

function parseGrid(grid: ReturnType<ReturnType<typeof search>['getRawValue']>): {
  space: QuantLabSearchSpace; count: number;
} {
  const { strategyId } = grid;
  if (strategyId === 'sma-cross-v1' || strategyId === 'ema-cross-v1') {
    const fast = unique(parts(grid.fast).map(value => whole(value, 2, 99, 'Fast')));
    const slow = unique(parts(grid.slow).map(value => whole(value, 3, 100, 'Slow')));
    if (Math.max(...fast) >= Math.min(...slow)) throw new Error('Jeder Fast-Wert muss kleiner als jeder Slow-Wert sein.');
    return { space: { strategyId, version: '1', parameterValues: { fast, slow } }, count: fast.length * slow.length };
  }
  if (strategyId === 'rsi-mean-reversion-v1') {
    const period = unique(parts(grid.period).map(value => whole(value, 2, 50, 'RSI-Periode')));
    const entry = threshold(grid.entry);
    const exit = threshold(grid.exit);
    if (Math.max(...entry.map(Number)) >= Math.min(...exit.map(Number))) {
      throw new Error('Jede RSI-Einstiegsschwelle muss kleiner als jede Ausstiegsschwelle sein.');
    }
    return { space: { strategyId, version: '1', parameterValues: { period, entry, exit } }, count: period.length * entry.length * exit.length };
  }
  return { space: { strategyId, version: '1', parameterValues: {} }, count: 1 };
}

@Component({
  selector: 'atl-quant-lab-builder',
  imports: [ReactiveFormsModule, MatButtonModule, MatCardModule, MatFormFieldModule, MatInputModule, MatSelectModule],
  templateUrl: './quant-lab-builder.component.html',
  styleUrl: './quant-lab-builder.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class QuantLabBuilderComponent {
  readonly catalog = input<QuantLabStrategyCatalog | null>(null);
  readonly instruments = input<readonly ResearchInstrument[]>([]);
  readonly definition = input<QuantLabExperimentCreateRequest | null>(null);
  readonly busy = input(false);
  readonly saveDraft = output<QuantLabExperimentCreateRequest>();
  readonly edited = output<void>();
  readonly error = signal('');
  readonly phases = [
    { key: 'training', label: 'Training', role: 'IN_SAMPLE_ANALYSIS' },
    { key: 'validation', label: 'Validation', role: 'PARAMETER_SELECTION' },
    { key: 'test', label: 'Test', role: 'WALK_FORWARD_OOS' },
  ] as const;
  readonly form = new FormGroup({
    hypothesis: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.maxLength(2000)] }),
    instrumentId: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    researchMode: new FormControl<BacktestResearchMode>('STRICT_PIT', { nonNullable: true }),
    policyId: new FormControl<QuantLabStrategySelectionPolicy['policyId']>('MAX_NET_RETURN_WITH_DRAWDOWN_CONSTRAINT_V1', { nonNullable: true }),
    maxDrawdown: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.pattern(UNIT)] }),
    minValidationSessions: control('20'),
    minimumEntryCount: control(),
    minimumInvestedBarRatio: control(),
    startCash: control('1000'),
    feeUsd: control('0'),
    slippageBps: control('0'),
    grids: new FormArray([search()]),
    folds: new FormArray([fold()]),
    holdout: interval(),
  });
  readonly grids = this.form.controls.grids;
  readonly folds = this.form.controls.folds;
  private readonly revision = signal(0);
  readonly allowedInstruments = computed(() => this.instruments().filter(
    instrument => (instrument.baseUnit === 'SPY' || instrument.baseUnit === 'QQQ') && instrument.currency === 'USD',
  ));
  readonly preview = computed(() => {
    this.revision();
    try {
      const grids = this.grids.getRawValue().map(parseGrid);
      const keys = grids.map(grid => grid.space.strategyId);
      if (new Set(keys).size !== keys.length) throw new Error('Eine Strategie darf nur einmal im Raster stehen.');
      const count = grids.reduce((total, grid) => total + grid.count, 0);
      if (count > (this.catalog()?.limits.maximumCandidates ?? LIMIT)) throw new Error('Das Suchraster umfasst mehr als 32 Kandidaten.');
      return { count, spaces: grids.map(grid => grid.space), reason: '' };
    } catch (error) {
      return { count: null, spaces: [], reason: error instanceof Error ? error.message : 'Suchraster prüfen.' };
    }
  });

  constructor() {
    effect(() => this.hydrate(this.definition()));
    this.form.valueChanges.pipe(takeUntilDestroyed(inject(DestroyRef))).subscribe(() => {
      this.revision.update(value => value + 1);
      this.error.set('');
      this.edited.emit();
    });
  }

  private hydrate(definition: QuantLabExperimentCreateRequest | null): void {
    const quiet = { emitEvent: false };
    this.grids.clear(quiet);
    if (definition) {
      for (const space of definition.searchSpaces) {
        const grid = search(space.strategyId);
        if (space.strategyId === 'ema-cross-v1' || space.strategyId === 'sma-cross-v1') {
          grid.patchValue({ fast: space.parameterValues.fast.join(', '), slow: space.parameterValues.slow.join(', ') }, quiet);
        } else if (space.strategyId === 'rsi-mean-reversion-v1') {
          grid.patchValue({
            period: space.parameterValues.period.join(', '),
            entry: space.parameterValues.entry.join(', '),
            exit: space.parameterValues.exit.join(', '),
          }, quiet);
        }
        this.grids.push(grid, quiet);
      }
    } else {
      this.grids.push(search(), quiet);
    }
    this.folds.clear(quiet);
    for (let index = 0; index < (definition?.protocol.folds.length ?? 1); index++) this.folds.push(fold(), quiet);
    const policy = definition?.strategySelectionPolicy;
    this.form.reset({
      hypothesis: definition?.hypothesis,
      instrumentId: definition?.instrumentId,
      researchMode: definition?.researchMode,
      policyId: policy?.policyId,
      maxDrawdown: policy?.maxDrawdown,
      minValidationSessions: policy ? String(policy.minValidationSessions) : undefined,
      minimumEntryCount: String(policy?.minimumEntryCount ?? ''),
      minimumInvestedBarRatio: policy?.minimumInvestedBarRatio ?? '',
      startCash: definition?.startCash,
      feeUsd: definition?.feeUsd,
      slippageBps: definition?.slippageBps,
      grids: this.grids.getRawValue(),
      folds: definition?.protocol.folds,
      holdout: definition?.protocol.holdout,
    }, quiet);
    this.error.set('');
    this.revision.update(value => value + 1);
  }

  addGrid(): void {
    const used = new Set(this.grids.getRawValue().map(grid => grid.strategyId));
    const available = this.catalog()?.items.find(item => !used.has(item.strategyId));
    if (available) this.grids.push(search(available.strategyId));
  }

  removeGrid(index: number): void {
    if (this.grids.length > 1) this.grids.removeAt(index);
  }

  addFold(): void {
    if (this.folds.length < (this.catalog()?.limits.maximumFolds ?? 4)) this.folds.push(fold());
  }

  removeFold(index: number): void {
    if (this.folds.length > 1) this.folds.removeAt(index);
  }

  submit(): void {
    this.form.markAllAsTouched();
    if (this.busy()) return;
    try {
      this.saveDraft.emit(this.request());
    } catch (error) {
      this.error.set(error instanceof Error ? error.message : 'Entwurf prüfen.');
    }
  }

  private request(): QuantLabExperimentCreateRequest {
    const value = this.form.getRawValue();
    const catalog = this.catalog();
    if (!catalog) throw new Error('Der Strategiekatalog ist noch nicht verfügbar.');
    if (!value.hypothesis.trim()) throw new Error('Eine Forschungshypothese ist erforderlich.');
    const symbol = this.allowedInstruments().find(item => item.instrumentId === value.instrumentId)?.baseUnit;
    if (symbol !== 'SPY' && symbol !== 'QQQ') throw new Error('Ein registriertes Researchinstrument auswählen.');
    const preview = this.preview();
    if (preview.count === null) throw new Error(preview.reason);
    if (preview.spaces.some(space => !catalog.items.some(item => item.strategyId === space.strategyId
      && item.version === space.version && item.symbols.includes(symbol)
      && item.researchModes.includes(value.researchMode)))) {
      throw new Error('Eine Strategie ist für Instrument oder Forschungsmodus nicht verfügbar.');
    }
    if (!UNIT.test(value.maxDrawdown)) throw new Error('Maximalen Drawdown ausdrücklich zwischen 0 und 1 angeben.');
    if (value.minimumInvestedBarRatio && !UNIT.test(value.minimumInvestedBarRatio)) {
      throw new Error('Die minimale Investiertheitsquote muss zwischen 0 und 1 liegen.');
    }
    const minValidationSessions = whole(value.minValidationSessions, 20, 252, 'Minimale Validation-Sitzungen');
    const minimumEntryCount = value.minimumEntryCount === '' ? null : whole(value.minimumEntryCount, 0, 100000, 'Minimale Entries');
    for (const [label, amount, max] of [
      ['Startkapital', value.startCash, 1000000000],
      ['Gebühr', value.feeUsd, 1000],
      ['Slippage', value.slippageBps, 100],
    ] as const) {
      if (!DECIMAL.test(amount) || Number(amount) > max || (label === 'Startkapital' && Number(amount) <= 0)) {
        throw new Error(label + ' als gültigen Dezimalwert mit Punkt angeben.');
      }
    }
    const protocol = value.folds.map((item, index) => ({ fold: index, ...item }));
    const windows = [...protocol.flatMap(item => [item.training, item.validation, item.test]), value.holdout];
    this.checkWindows(windows, catalog.limits.maximumSpanDays);
    if (this.form.invalid) throw new Error('Die markierten Pflichtfelder prüfen.');
    return {
      schemaVersion: 'quant-lab-v1',
      hypothesis: value.hypothesis.trim(),
      predecessorDefinitionId: null,
      supersedesBatchId: null,
      searchSpaces: preview.spaces,
      protocol: { schemaVersion: 'quant-lab-v1', protocolVersion: 'quant-protocol-v1', folds: protocol, holdout: value.holdout, fittingStatus: 'NOT_TRAINABLE' },
      strategySelectionPolicy: {
        policyId: value.policyId,
        maxDrawdown: canonicalDecimal(value.maxDrawdown),
        minValidationSessions,
        minimumEntryCount,
        minimumInvestedBarRatio: value.minimumInvestedBarRatio ? canonicalDecimal(value.minimumInvestedBarRatio) : null,
      },
      instrumentId: value.instrumentId, symbol, currency: 'USD', feed: 'IEX', interval: '15Min', adjustment: 'raw',
      researchMode: value.researchMode,
      dataSelectionPolicyVersion: value.researchMode === 'STRICT_PIT' ? 'strict-pit-v1' : 'retrospective-v1',
      startCash: canonicalDecimal(value.startCash),
      feeUsd: canonicalDecimal(value.feeUsd),
      slippageBps: canonicalDecimal(value.slippageBps),
      quantityStep: '0.000001',
    };
  }

  private checkWindows(windows: QuantLabInterval[], maximumDays: number): void {
    const utc = windows.flatMap(window => [window.start, window.end]);
    const times = utc.map(value => Date.parse(value));
    if (utc.some((value, index) => !UTC.test(value) || !Number.isFinite(times[index])
      || new Date(times[index]).toISOString().slice(0, 19) !== value.slice(0, 19))
      || times.some((value, index) => index > 0 && (value < times[index - 1] || (index % 2 === 1 && value === times[index - 1])))) {
      throw new Error('Phasen brauchen gültige UTC-Grenzen im 15-Minuten-Raster und müssen zeitlich geordnet und disjunkt sein.');
    }
    if (times[times.length - 1] - times[0] > maximumDays * 86400000) {
      throw new Error('Die Phasen überschreiten die maximal zulässige Zeitspanne.');
    }
  }
}
