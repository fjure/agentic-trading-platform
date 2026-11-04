"""Pure, bounded QSL definitions and snapshot bindings; no queue or execution I/O.

Legacy ExperimentConfig and data-selection policy preimages are intentionally
separate. A definition is a canonical immutable revision, never a fitted model.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import product
from uuid import UUID

from packages.domain.models import decimal_value, utc_time

from .models import DataReference, digest, fingerprint, selection_policy
from .strategies import (
    FITTING_STATUS,
    QUANT_WARMUP_VERSION,
    canonical_decimal,
    canonical_parameters,
    resolve_strategy,
    warmup_count,
)

SCHEMA_VERSION = "quant-lab-v1"
EXPERIMENT_VERSION = "quant-experiment-v1"
PROTOCOL_VERSION = "quant-research-protocol-v1"
SELECTION_VERSION = "quant-selection-v1"
QUANT_METRICS_VERSION = "quant-metrics-v1"
MAX_CANDIDATES = 32
MAX_FOLDS = 4
MAX_SNAPSHOT_ROWS = 100000
MAX_SIMULATION_BAR_STEPS = 100000
MAX_PUBLICATION_BYTES = 12 * 1024 * 1024
MAX_NET_RETURN = "MAX_NET_RETURN_WITH_DRAWDOWN_CONSTRAINT_V1"
CAPITAL_PRESERVATION = "CAPITAL_PRESERVATION_DRAWDOWN_FIRST_V1"
PHASE_ROLES = (
    ("training", "IN_SAMPLE_ANALYSIS"),
    ("validation", "PARAMETER_SELECTION"),
    ("test", "WALK_FORWARD_OOS"),
    ("holdout", "FINAL_HOLDOUT"),
)


def _selection_method():
    # A closed method declaration, subsequently bound to the executable selector
    # by QSL05 before the first persisted execution. No executable user content.
    return (
        SELECTION_VERSION, "Decimal38:ROUND_HALF_EVEN:no-intermediate-quantization",
        "complete-search-set;complete-data;min-sessions;inclusive-dd;optional-activity",
        (MAX_NET_RETURN, "-net_return,max_drawdown,turnover,candidate_id"),
        (CAPITAL_PRESERVATION, "max_drawdown,-net_return,turnover,candidate_id"),
        "final:all-fold-gates;componentwise-median;even:(lower+upper)/2",
        "validation-only;freeze-all-selections-before-any-oos;no-fallback",
    )


SELECTOR_FINGERPRINT = fingerprint(_selection_method())


@dataclass(frozen=True)
class StrategySelectionPolicy:
    policy_id: str
    max_drawdown: str
    min_validation_sessions: int
    minimum_entry_count: int | None = None
    minimum_invested_bar_ratio: str | None = None
    selector_fingerprint: str = field(init=False, default=SELECTOR_FINGERPRINT)

    def __post_init__(self):
        if self.policy_id not in (MAX_NET_RETURN, CAPITAL_PRESERVATION):
            raise ValueError("unsupported strategy selection policy")
        object.__setattr__(self, "max_drawdown", canonical_decimal(
            self.max_drawdown, places=12, maximum=1
        ))
        if type(self.min_validation_sessions) is not int or not (
            20 <= self.min_validation_sessions <= 252
        ):
            raise ValueError("minValidationSessions outside [20,252]")
        if self.minimum_entry_count is not None and (
            type(self.minimum_entry_count) is not int
            or not 0 <= self.minimum_entry_count <= 100000
        ):
            raise ValueError("minimumEntryCount outside [0,100000]")
        if self.minimum_invested_bar_ratio is not None:
            object.__setattr__(self, "minimum_invested_bar_ratio", canonical_decimal(
                self.minimum_invested_bar_ratio, places=12, maximum=1
            ))

    @classmethod
    def from_mapping(cls, value):
        fields = (
            "policyId", "maxDrawdown", "minValidationSessions", "minimumEntryCount",
            "minimumInvestedBarRatio",
        )
        if not isinstance(value, Mapping) or set(value) != set(fields):
            raise ValueError("exactly five strategy selection policy fields required")
        return cls(*(value[key] for key in fields))

    def binding(self):
        return (
            self.policy_id, self.max_drawdown, self.min_validation_sessions,
            self.minimum_entry_count, self.minimum_invested_bar_ratio,
            self.selector_fingerprint,
        )

    @property
    def policy_hash(self):
        return fingerprint(self.binding())


@dataclass(frozen=True)
class SearchSpace:
    strategy_id: str
    parameter_values: tuple[tuple[str, tuple[int | str, ...]], ...]
    strategy_version: str = "1"

    def __post_init__(self):
        definition = resolve_strategy(self.strategy_id, self.strategy_version)
        if type(self.parameter_values) is not tuple or any(
            type(pair) is not tuple or len(pair) != 2 for pair in self.parameter_values
        ):
            raise ValueError("immutable parameter value lists required")
        keys = tuple(k for k, _ in self.parameter_values)
        expected = tuple(p.name for p in definition.parameter_definitions)
        if any(type(k) is not str for k in keys) or sorted(keys) != sorted(expected):
            raise ValueError("unknown, missing or duplicate grid parameter")
        normalized = []
        specs = {p.name: p for p in definition.parameter_definitions}
        for key, values in self.parameter_values:
            if type(values) is not tuple or not 1 <= len(values) <= 32:
                raise ValueError("one to 32 explicit immutable values required")
            spec = specs[key]
            canonical = []
            for value in values:
                if spec.value_type == "INTEGER":
                    if type(value) is not int or not (
                        int(spec.minimum) <= value <= int(spec.maximum)
                    ):
                        raise ValueError("integer parameter outside bounds")
                else:
                    value = canonical_decimal(
                        value, places=spec.maximum_fractional_digits,
                        maximum=Decimal(spec.maximum),
                    )
                canonical.append(value)
            if len(set(canonical)) != len(canonical):
                raise ValueError("canonically duplicate parameter values")
            normalized.append((key, tuple(sorted(canonical, key=(
                Decimal if spec.value_type == "DECIMAL_STRING" else int
            )))))
        object.__setattr__(self, "parameter_values", tuple(sorted(normalized)))

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
            "strategyId", "version", "parameterValues"
        } or not isinstance(value["parameterValues"], Mapping):
            raise ValueError("closed search-space object required")
        pairs = []
        for key, values in value["parameterValues"].items():
            if type(values) is not list:
                raise ValueError("explicit parameter arrays required")
            pairs.append((key, tuple(values)))
        return cls(value["strategyId"], tuple(pairs), value["version"])

    @property
    def candidate_count(self):
        count = 1
        for _, values in self.parameter_values:
            # Saturation precedes Cartesian materialization.
            count = min(MAX_CANDIDATES + 1, count * len(values))
        return count


@dataclass(frozen=True)
class CandidateConfiguration:
    strategy_id: str
    parameters: tuple[tuple[str, int | str], ...]
    strategy_version: str = "1"
    implementation_fingerprint: str = field(init=False)

    def __post_init__(self):
        definition = resolve_strategy(self.strategy_id, self.strategy_version)
        object.__setattr__(
            self, "implementation_fingerprint", definition.implementation_fingerprint
        )
        canonical = canonical_parameters(self.strategy_id, self.parameters)
        object.__setattr__(self, "parameters", canonical)

    @property
    def candidate_id(self):
        return fingerprint((self.strategy_id, self.strategy_version, self.parameters))

    @property
    def warmup_count(self):
        return warmup_count(self.strategy_id, self.parameters)

def enumerate_candidates(search_spaces):
    if type(search_spaces) is not tuple or not 1 <= len(search_spaces) <= 32 or any(
        type(space) is not SearchSpace for space in search_spaces
    ):
        raise ValueError("one to 32 immutable typed search spaces required")
    if len({(s.strategy_id, s.strategy_version) for s in search_spaces}) != len(search_spaces):
        raise ValueError("duplicate strategy definition")
    total = 0
    for space in search_spaces:
        total = min(MAX_CANDIDATES + 1, total + space.candidate_count)
        if total > MAX_CANDIDATES:
            raise ValueError("candidate product exceeds 32")
    candidates = {}
    for space in search_spaces:
        keys = tuple(k for k, _ in space.parameter_values)
        for values in product(*(v for _, v in space.parameter_values)):
            candidate = CandidateConfiguration(
                space.strategy_id, tuple(zip(keys, values, strict=True)), space.strategy_version
            )
            if candidate.candidate_id in candidates:
                raise ValueError("duplicate canonical candidate")
            candidates[candidate.candidate_id] = candidate
    return tuple(candidates[key] for key in sorted(candidates))


REFERENCE_CONFIGURATIONS = (
    CandidateConfiguration("flat-v1", ()), CandidateConfiguration("buy-hold-v1", ()),
)


@dataclass(frozen=True)
class PhaseWindow:
    start: datetime
    end: datetime

    def __post_init__(self):
        for value in (self.start, self.end):
            utc_time(value)
            if value.minute % 15 or value.second or value.microsecond:
                raise ValueError("15-minute UTC phase boundaries required")
        if self.start >= self.end:
            raise ValueError("nonempty phase required")


@dataclass(frozen=True)
class ExperimentFold:
    training: PhaseWindow
    validation: PhaseWindow
    test: PhaseWindow

    def __post_init__(self):
        if any(type(p) is not PhaseWindow for p in (self.training, self.validation, self.test)):
            raise ValueError("typed phases required")
        if not self.training.end <= self.validation.start or not (
            self.validation.end <= self.test.start
        ):
            raise ValueError("training, validation and test must be chronological/disjoint")


@dataclass(frozen=True)
class PhaseProtocol:
    folds: tuple[ExperimentFold, ...]
    holdout: PhaseWindow

    def __post_init__(self):
        if type(self.folds) is not tuple or not 1 <= len(self.folds) <= MAX_FOLDS or any(
            type(f) is not ExperimentFold for f in self.folds
        ) or type(self.holdout) is not PhaseWindow:
            raise ValueError("one to four folds and final holdout required")
        if any(a.test.end > b.training.start for a, b in zip(
            self.folds, self.folds[1:], strict=False
        )) or self.folds[-1].test.end > self.holdout.start:
            raise ValueError("globally disjoint chronological phases required")
        if self.holdout.end - self.folds[0].training.start > timedelta(days=366):
            raise ValueError("protocol exceeds 366 days")

    @property
    def phases(self):
        rows = tuple(
            (index, key, role, getattr(fold, key))
            for index, fold in enumerate(self.folds)
            for key, role in PHASE_ROLES[:3]
        )
        return rows + ((None, "holdout", "FINAL_HOLDOUT", self.holdout),)

    @property
    def protocol_hash(self):
        return fingerprint((PROTOCOL_VERSION, FITTING_STATUS, self))


@dataclass(frozen=True)
class ExperimentDefinition:
    instrument_id: UUID
    symbol: str
    hypothesis: str
    search_spaces: tuple[SearchSpace, ...]
    protocol: PhaseProtocol
    strategy_selection_policy: StrategySelectionPolicy
    start_cash: Decimal = Decimal("10000")
    fee_usd: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    quantity_step: Decimal = Decimal("0.000001")
    research_mode: str = "STRICT_PIT"
    definition_version: int = 1
    predecessor_hash: str | None = None
    feed: str = "iex"
    interval: str = "15Min"
    adjustment: str = "raw"
    currency: str = "USD"
    candidates: tuple[CandidateConfiguration, ...] = field(init=False)

    def __post_init__(self):
        if type(self.instrument_id) is not UUID or self.symbol not in ("SPY", "QQQ"):
            raise ValueError("SPY/QQQ instrument required")
        if type(self.hypothesis) is not str or not 1 <= len(self.hypothesis) <= 2000 or (
            not self.hypothesis.strip()
        ) or any(
            ord(c) < 32 and c not in "\n\t" for c in self.hypothesis
        ):
            raise ValueError("redacted nonempty hypothesis up to 2000 characters required")
        if type(self.protocol) is not PhaseProtocol or type(
            self.strategy_selection_policy
        ) is not StrategySelectionPolicy:
            raise ValueError("typed protocol and strategy selection policy required")
        policy_version = {
            "STRICT_PIT": "strict-pit-v1", "RETROSPECTIVE_EXPLORATORY": "retrospective-v1"
        }.get(self.research_mode)
        selection_policy(self.research_mode, policy_version)
        if self.research_mode not in ("STRICT_PIT", "RETROSPECTIVE_EXPLORATORY"):
            raise ValueError("explicit closed research mode required")
        if (self.feed, self.interval, self.adjustment, self.currency) != (
            "iex", "15Min", "raw", "USD"
        ):
            raise ValueError("unsupported research market")
        if type(self.definition_version) is not int or self.definition_version < 1:
            raise ValueError("positive definition revision required")
        if self.predecessor_hash is not None:
            digest(self.predecessor_hash)
        if (self.definition_version == 1) != (self.predecessor_hash is None):
            raise ValueError("revisions must bind their predecessor")
        for value in (self.start_cash, self.fee_usd, self.slippage_bps, self.quantity_step):
            if type(value) is not Decimal:
                raise ValueError("Decimal costs/cash required")
            decimal_value(value)
        if not 0 < self.start_cash <= Decimal("1000000000") or not (
            0 <= self.fee_usd <= 1000 and 0 <= self.slippage_bps <= 100
        ) or self.quantity_step != Decimal("0.000001"):
            raise ValueError("invalid simulation assumptions")
        candidates = enumerate_candidates(self.search_spaces)
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "search_spaces", tuple(sorted(
            self.search_spaces, key=lambda s: fingerprint(s)
        )))

    @property
    def warmup_count(self):
        return max(c.warmup_count for c in self.candidates)

    @property
    def queue_slots(self):
        return len(self.candidates) + 2

    @property
    def data_selection_policy_version(self):
        return "strict-pit-v1" if self.research_mode == "STRICT_PIT" else "retrospective-v1"

    @property
    def fitting_status(self):
        return FITTING_STATUS

    @property
    def definition_hash(self):
        return fingerprint(self.binding())

    def binding(self):
        return (
            EXPERIMENT_VERSION, self, PROTOCOL_VERSION, QUANT_WARMUP_VERSION,
            self.warmup_count, self.strategy_selection_policy.binding(),
            tuple((c.candidate_id, c.implementation_fingerprint) for c in self.candidates),
            self.data_selection_policy_version,
        )


def canonical_definition_preimage(definition):
    """JSON-compatible domain identity preimage shared with the SQL boundary."""
    from packages.research.models import _canonical

    if type(definition) is not ExperimentDefinition:
        raise ValueError("typed definition required")
    return _canonical(definition.binding())


def canonical_policy_preimage(policy):
    from packages.research.models import _canonical

    if type(policy) is not StrategySelectionPolicy:
        raise ValueError("typed strategy selection policy required")
    return _canonical(policy.binding())


@dataclass(frozen=True)
class PhaseSeedBinding:
    fold_index: int | None
    phase: str
    window: PhaseWindow
    expected_seed_starts: tuple[datetime, ...]
    expected_bar_starts: tuple[datetime, ...]

    def __post_init__(self):
        if self.phase not in dict(PHASE_ROLES) or type(self.window) is not PhaseWindow:
            raise ValueError("closed phase and typed window required")
        if (self.phase == "holdout") != (self.fold_index is None) or (
            self.fold_index is not None and (
                type(self.fold_index) is not int or not 0 <= self.fold_index < MAX_FOLDS
            )
        ):
            raise ValueError("invalid phase/fold binding")
        for starts in (self.expected_seed_starts, self.expected_bar_starts):
            if type(starts) is not tuple or tuple(sorted(set(starts))) != starts:
                raise ValueError("immutable unique chronological bar starts required")
            for start in starts:
                utc_time(start)
                if start.minute % 15 or start.second or start.microsecond:
                    raise ValueError("15-minute bar identity required")
        if len(self.expected_seed_starts) > 99 or not self.expected_bar_starts or any(
            start + timedelta(minutes=15) > self.window.start
            for start in self.expected_seed_starts
        ) or any(
            not self.window.start <= start < self.window.end for start in self.expected_bar_starts
        ):
            raise ValueError("seed/phase identities outside binding")

    @property
    def role(self):
        return dict(PHASE_ROLES)[self.phase]


@dataclass(frozen=True)
class QuantExperimentManifest:
    definition: ExperimentDefinition
    snapshot_version: int
    snapshot_at: datetime
    phase_bindings: tuple[PhaseSeedBinding, ...]
    decision_data: tuple[DataReference, ...]
    outcome_data: tuple[DataReference, ...]
    calendar_revisions: tuple[str, ...]
    quality_revisions: tuple[str, ...]
    coverage_revisions: tuple[str, ...]
    code_version: str
    trust_fingerprint: str
    license_receipt_fingerprint: str
    simulator_version: str = "backtest-simulator-v1"
    metrics_version: str = QUANT_METRICS_VERSION
    fill_version: str = "next-regular-open-v1"
    evidence_policy_version: str | None = None

    def __post_init__(self):
        if type(self.definition) is not ExperimentDefinition:
            raise ValueError("typed immutable definition required")
        if type(self.snapshot_version) is not int or self.snapshot_version < 1:
            raise ValueError("committed snapshot required")
        utc_time(self.snapshot_at)
        if self.definition.protocol.holdout.end > self.snapshot_at:
            raise ValueError("future evaluation window")
        if type(self.code_version) is not str or not re.fullmatch(
            r"[0-9a-f]{40}", self.code_version
        ):
            raise ValueError("build SHA required")
        for value in (self.trust_fingerprint, self.license_receipt_fingerprint):
            digest(value)
        if (self.simulator_version, self.metrics_version, self.fill_version) != (
            "backtest-simulator-v1", QUANT_METRICS_VERSION, "next-regular-open-v1"
        ):
            raise ValueError("unsupported simulator/metrics/fill version")
        expected_evidence_policy = (
            "quant-strict-pit-evidence-v1"
            if self.definition.research_mode == "STRICT_PIT"
            else "quant-retrospective-snapshot-evidence-v1"
        )
        if self.evidence_policy_version not in (None, expected_evidence_policy):
            raise ValueError("research mode/evidence policy mismatch")
        object.__setattr__(self, "evidence_policy_version", expected_evidence_policy)
        for candidate in self.definition.candidates:
            resolve_strategy(
                candidate.strategy_id, candidate.strategy_version,
                candidate.implementation_fingerprint,
            )
        if type(self.phase_bindings) is not tuple or any(
            type(b) is not PhaseSeedBinding for b in self.phase_bindings
        ):
            raise ValueError("typed phase bindings required")
        expected = tuple((i, key, window) for i, key, _, window in self.definition.protocol.phases)
        if tuple((b.fold_index, b.phase, b.window) for b in self.phase_bindings) != expected or any(
            len(b.expected_seed_starts) != self.definition.warmup_count for b in self.phase_bindings
        ):
            raise ValueError("all phases require the same bound seed count")
        seed_start = min(
            b.expected_seed_starts[0] if b.expected_seed_starts else b.window.start
            for b in self.phase_bindings
        )
        if self.definition.protocol.holdout.end - seed_start > timedelta(days=366):
            raise ValueError("seed-inclusive span exceeds 366 days")
        for refs in (self.decision_data, self.outcome_data):
            if type(refs) is not tuple or not refs or any(
                type(r) is not DataReference for r in refs
            ):
                raise ValueError("nonempty immutable revision bindings required")
            if tuple(sorted(set(refs))) != refs or any(r.cutoff > self.snapshot_at for r in refs):
                raise ValueError("invalid revision order/cutoff")
        for refs in (self.calendar_revisions, self.quality_revisions, self.coverage_revisions):
            if type(refs) is not tuple or any(type(r) is not str for r in refs) or (
                tuple(sorted(set(refs))) != refs
            ):
                raise ValueError("immutable metadata bindings required")
            from .models import label

            for ref in refs:
                label(ref)
        # Strict quality_at() binds even the known-empty findings state by hash.
        # Retrospective structural-action gates are bound by its snapshot/trust
        # policy and do not claim historical Strict corporate-action proof.
        if not self.calendar_revisions or (
            self.definition.research_mode == "STRICT_PIT"
            and (not self.quality_revisions or not self.coverage_revisions)
        ):
            raise ValueError("calendar and mode-required quality/coverage evidence required")
        revisions = {}
        for ref in self.decision_data + self.outcome_data:
            if ref.revision_id in revisions and revisions[ref.revision_id] != ref.fingerprint:
                raise ValueError("conflicting revision fingerprints")
            revisions[ref.revision_id] = ref.fingerprint
        if len(revisions) + sum(map(len, (
            self.calendar_revisions, self.quality_revisions, self.coverage_revisions,
        ))) > MAX_SNAPSHOT_ROWS:
            raise ValueError("snapshot row budget exceeded")

    @classmethod
    def from_phase_datasets(
        cls, definition, phases, snapshot, *, code_version, trust_fingerprint,
        license_receipt_fingerprint, quality_revisions=(), coverage_revisions=(),
    ):
        """Bind actual phase-port output without inventing metadata references."""
        from packages.research.models import BarRevision

        from .dataset import (
            FoldDataset,
            QuantPhaseDataset,
            Snapshot,
            validate_quant_phase_dataset,
        )
        from .retrospective import RetrospectiveFoldDataset

        if type(definition) is not ExperimentDefinition or type(snapshot) is not Snapshot or (
            type(phases) is not tuple or not phases
        ) or any(
            type(p) is not QuantPhaseDataset for p in phases
        ):
            raise ValueError("typed common snapshot and phase datasets required")
        decisions, outcomes, calendars, qualities, coverages = set(), set(), set(), set(), set()

        def validate_bar(bar):
            if type(bar) is not BarRevision or (
                bar.identity.instrument_id, bar.identity.provider, bar.identity.feed,
                bar.identity.interval, bar.identity.adjustment.value,
            ) != (
                definition.instrument_id, "alpaca", definition.feed, definition.interval,
                definition.adjustment,
            ):
                raise ValueError("revision instrument/market differs from bound definition")
            if bar.commit_version > snapshot.version or bar.ingested_at > snapshot.at:
                raise ValueError("revision outside bound common snapshot")
            if bar.identity.bar_end - bar.identity.bar_start != timedelta(minutes=15):
                raise ValueError("complete 15-minute revision interval required")

        for phase in phases:
            data = phase.dataset
            if definition.research_mode == "STRICT_PIT":
                if type(data) is not FoldDataset or not data.seed_once:
                    raise ValueError("Strict single-seed dataset required")
                validate_quant_phase_dataset(data, phase.binding, snapshot)
                calendars.update(data.calendar_revision_ids)
                qualities.update(data.quality_revision_ids)
                coverages.update(data.coverage_ids)
                cutoffs = dict(data.outcome_cutoffs)
            else:
                if type(data) is not RetrospectiveFoldDataset or not data.seed_once or (
                    data.snapshot != snapshot
                ):
                    raise ValueError("retrospective common-snapshot single-seed dataset required")
                data.validate()
                calendars.update(data.calendar_revision_ids)
                cutoffs = {bar.revision_id: snapshot.at for bar in data.outcome_bars}
            if data.warmup_binding.expected_bar_starts != phase.binding.expected_seed_starts or (
                data.expected_bar_starts != phase.binding.expected_bar_starts
            ) or (
                (data.fold.test_start, data.fold.test_end)
                != (phase.binding.window.start, phase.binding.window.end)
            ):
                raise ValueError("phase/seed dataset binding mismatch")
            if not data.steps or data.warmup != data.steps[0].input_bars[:-1]:
                raise ValueError("seed differs from first immutable input prefix")
            for step in data.steps:
                for bar in step.input_bars:
                    validate_bar(bar)
                    decisions.add(DataReference(
                        bar.revision_id, bar.fingerprint,
                        step.knowledge_at if definition.research_mode == "STRICT_PIT"
                        else snapshot.at,
                    ))
            for bar in data.outcome_bars:
                validate_bar(bar)
                if bar.revision_id not in cutoffs:
                    raise ValueError("missing outcome cutoff")
                outcomes.add(DataReference(
                    bar.revision_id, bar.fingerprint, cutoffs[bar.revision_id]
                ))
        # Snapshot-bound retrospective evidence can be supplied by its existing
        # quality/action ports. Strict references come only from the PIT sweep.
        if definition.research_mode == "STRICT_PIT" and (quality_revisions or coverage_revisions):
            raise ValueError("Strict evidence must come from the materialized dataset")
        qualities.update(quality_revisions)
        coverages.update(coverage_revisions)
        return cls(
            definition, snapshot.version, snapshot.at, tuple(p.binding for p in phases),
            tuple(sorted(decisions)), tuple(sorted(outcomes)), tuple(sorted(calendars)),
            tuple(sorted(qualities)), tuple(sorted(coverages)), code_version,
            trust_fingerprint, license_receipt_fingerprint,
        )

    @property
    def manifest_hash(self):
        return fingerprint((
            EXPERIMENT_VERSION, self, self.definition.definition_hash,
            self.definition.strategy_selection_policy.binding(), PROTOCOL_VERSION,
            QUANT_WARMUP_VERSION,
        ))

    @property
    def snapshot_union(self):
        """Unique expected seed/performance identities from the one bound snapshot."""
        return tuple(sorted({
            start for binding in self.phase_bindings
            for start in binding.expected_seed_starts + binding.expected_bar_starts
        }))


def selection_identity(policy, *, scope, validation_input_hashes, result):
    """Identity port for QSL05: OOS values have no argument or implicit input."""
    if type(policy) is not StrategySelectionPolicy or type(scope) is not str or not re.fullmatch(
        r"final|fold:[0-3]", scope
    ):
        raise ValueError("closed selection scope and policy required")
    if type(validation_input_hashes) is not tuple or not validation_input_hashes or (
        tuple(sorted(set(validation_input_hashes))) != validation_input_hashes
    ):
        raise ValueError("sorted unique validation input hashes required")
    for value in validation_input_hashes:
        digest(value)
    return fingerprint((
        SELECTION_VERSION, policy.binding(), scope, validation_input_hashes, result
    ))
