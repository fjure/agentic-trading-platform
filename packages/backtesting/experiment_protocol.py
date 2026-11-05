"""Pure validation selection, frozen phase plans and conservative research history.

No storage, orchestration, simulator, training or OOS-result port exists here.
Validation inputs are trusted projections of immutable SEARCH result links; the
storage boundary must verify their values and fingerprints against those links.
"""

from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from uuid import UUID

from packages.domain.models import utc_time
from packages.research.models import _canonical

from .experiment import (
    CAPITAL_PRESERVATION,
    MAX_CANDIDATES,
    MAX_FOLDS,
    MAX_PUBLICATION_BYTES,
    MAX_SNAPSHOT_ROWS,
    PHASE_ROLES,
    REFERENCE_CONFIGURATIONS,
    SCHEMA_VERSION,
    SELECTION_VERSION,
    SELECTOR_FINGERPRINT,
    ExperimentDefinition,
    PhaseSeedBinding,
    PhaseWindow,
    StrategySelectionPolicy,
    selection_identity,
)
from .models import digest, fingerprint
from .strategies import FITTING_STATUS

GATES = (
    "COMPLETE_DATA",
    "RELIABILITY",
    "MINIMUM_VALIDATION_SESSIONS",
    "VALID_REQUIRED_METRICS",
    "MAX_DRAWDOWN",
    "MINIMUM_ENTRY_COUNT",
    "MINIMUM_INVESTED_BAR_RATIO",
)
_DECIMAL_FIELDS = ("netReturn", "maxDrawdown", "turnover", "investedBarRatio")
_PERFORMANCE_ROLES = tuple(role for _, role in PHASE_ROLES)
_REGISTERED_STATES = ("ACCEPTED", "RUNNING", "SUCCEEDED", "BLOCKED", "FAILED")


def exact_decimal(value: Decimal) -> str:
    """Canonical finite Decimal38, independent of the caller's Decimal context."""
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("finite Decimal required")
    if value == 0:
        return "0"
    if not -78 <= value.adjusted() < 80:
        raise ValueError("Decimal38 selection value outside wire bounds")
    digits = value.as_tuple().digits
    end = len(digits)
    while digits[end - 1] == 0:
        end -= 1
    if end > 38 or value.adjusted() - end + 1 < -78:
        raise ValueError("Decimal38 selection value outside wire bounds")
    text = format(Decimal((int(value.is_signed()), digits[:end], value.adjusted() - end + 1)), "f")
    if len(text) > 80:
        raise ValueError("Decimal38 canonical selection value required")
    return text


def display_decimal(value: Decimal) -> str:
    exact_decimal(value)
    with localcontext(Context(prec=100, rounding=ROUND_HALF_EVEN)):
        shown = value.quantize(Decimal("0.000000000001"))
        return format(abs(shown) if shown == 0 else shown, ".12f")


def decimal_median(values: tuple[Decimal, ...]) -> Decimal:
    if type(values) is not tuple or not values:
        raise ValueError("nonempty immutable Decimal values required")
    for value in values:
        exact_decimal(value)
    ordered = sorted(values)
    middle = len(ordered) // 2
    with localcontext(Context(prec=38, rounding=ROUND_HALF_EVEN)):
        return (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / Decimal(2)
        )


@dataclass(frozen=True)
class SelectionValues:
    net_return: Decimal
    max_drawdown: Decimal
    turnover: Decimal
    invested_bar_ratio: Decimal
    entry_count: int
    complete_validation_sessions: int

    def __post_init__(self):
        for value in (
            self.net_return,
            self.max_drawdown,
            self.turnover,
            self.invested_bar_ratio,
        ):
            exact_decimal(value)
        if not (
            -1 < self.net_return
            and 0 <= self.max_drawdown <= 1
            and self.turnover >= 0
            and 0 <= self.invested_bar_ratio <= 1
        ):
            raise ValueError("selection ratios inconsistent with positive equity")
        for value, maximum in (
            (self.entry_count, 100000),
            (self.complete_validation_sessions, 366),
        ):
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError("bounded exact integer required")

    def to_wire(self):
        return dict(
            zip(
                _DECIMAL_FIELDS,
                map(
                    exact_decimal,
                    (
                        self.net_return,
                        self.max_drawdown,
                        self.turnover,
                        self.invested_bar_ratio,
                    ),
                ),
                strict=True,
            )
        ) | {
            "entryCount": self.entry_count,
            "completeValidationSessions": self.complete_validation_sessions,
        }

    def display_values(self):
        return {
            key: display_decimal(Decimal(value)) if key in _DECIMAL_FIELDS else value
            for key, value in self.to_wire().items()
        }


@dataclass(frozen=True)
class SelectionReliability:
    research_mode: str
    evidence_status: str
    rating: str
    eligibility: str = "EXPLORATORY_WITH_LIMITATIONS"

    def __post_init__(self):
        if self.research_mode not in ("STRICT_PIT", "RETROSPECTIVE_EXPLORATORY") or (
            self.evidence_status
            not in (
                "STRICT_PIT_EVIDENCE_COMPLETE",
                "EXPLORATORY_WITH_LIMITATIONS",
                "INCOMPLETE",
                "BLOCKED",
            )
            or self.rating
            not in (
                "PASS",
                "LIMITED",
                "UNKNOWN",
                "BLOCKED",
                "NOT_EVALUATED",
            )
            or self.eligibility
            not in (
                "EXPLORATORY_WITH_LIMITATIONS",
                "BLOCKED",
            )
        ):
            raise ValueError("closed reliability evidence required")

    @property
    def passed(self):
        expected = (
            "STRICT_PIT_EVIDENCE_COMPLETE"
            if self.research_mode == "STRICT_PIT"
            else "EXPLORATORY_WITH_LIMITATIONS"
        )
        return (
            self.eligibility == "EXPLORATORY_WITH_LIMITATIONS"
            and (self.evidence_status == expected)
            and self.rating
            in (
                ("PASS",)
                if self.research_mode == "STRICT_PIT"
                else (
                    "PASS",
                    "LIMITED",
                )
            )
        )


@dataclass(frozen=True)
class ValidationSelectionInput:
    """A typed projection of one Validation link in a shared bound manifest."""

    candidate_id: str
    fold_index: int
    input_hash: str
    manifest_hash: str
    values: SelectionValues | None
    complete_data: bool
    calendar_confirmed: bool
    snapshot_bound: bool
    reliability: SelectionReliability
    status: str = "SUCCEEDED"

    def __post_init__(self):
        digest(self.candidate_id)
        digest(self.input_hash)
        digest(self.manifest_hash)
        if (
            type(self.fold_index) is not int
            or not 0 <= self.fold_index < MAX_FOLDS
            or (self.values is not None and type(self.values) is not SelectionValues)
            or type(self.reliability) is not SelectionReliability
            or self.status
            not in (
                "SUCCEEDED",
                "BLOCKED",
                "FAILED",
                "INCOMPLETE",
            )
            or any(
                type(flag) is not bool
                for flag in (
                    self.complete_data,
                    self.calendar_confirmed,
                    self.snapshot_bound,
                )
            )
        ):
            raise ValueError("typed validation-only input required")


def _gate(name, passed, threshold, value, reason, *, disabled=False, incomplete=False):
    status = (
        "DISABLED"
        if disabled
        else "INCOMPLETE"
        if incomplete
        else ("PASSED" if passed else "FAILED")
    )
    reasons = list(reason) if isinstance(reason, tuple) else [reason]
    return {
        "gate": name,
        "status": status,
        "threshold": threshold,
        "value": value,
        "reasonCodes": [] if status in ("PASSED", "DISABLED") else reasons,
    }


def _validation_evidence(policy, fold, value):
    succeeded = value is not None and value.status == "SUCCEEDED"
    values = value.values if succeeded else None
    wire = values.to_wire() if values else None
    incomplete_data = ("INCOMPLETE_CANDIDATE_SET",)
    if succeeded:
        incomplete_data = tuple(
            reason
            for flag, reason in (
                (value.complete_data, "INCOMPLETE_BAR_SERIES"),
                (value.calendar_confirmed, "MISSING_CALENDAR"),
                (value.snapshot_bound, "SNAPSHOT_UNAVAILABLE"),
            )
            if not flag
        )
    complete = not incomplete_data
    reliable = bool(succeeded and value.reliability.passed)
    missing_reason = "INVALID_NUMERIC_INPUT" if succeeded else "INCOMPLETE_CANDIDATE_SET"
    required = values is not None
    gates = [
        _gate(
            GATES[0],
            complete,
            "1",
            "1" if complete else None,
            incomplete_data,
            incomplete=not complete,
        ),
        _gate(
            GATES[1],
            reliable,
            None,
            None,
            "RELIABILITY_BLOCKED" if succeeded else "INCOMPLETE_CANDIDATE_SET",
            incomplete=not reliable,
        ),
        _gate(
            GATES[2],
            bool(values and values.complete_validation_sessions >= policy.min_validation_sessions),
            policy.min_validation_sessions,
            values.complete_validation_sessions if values else None,
            "MINIMUM_SESSIONS_NOT_MET" if required else missing_reason,
            incomplete=not required,
        ),
        _gate(GATES[3], required, None, None, missing_reason, incomplete=not required),
        _gate(
            GATES[4],
            bool(values and values.max_drawdown <= Decimal(policy.max_drawdown)),
            policy.max_drawdown,
            wire["maxDrawdown"] if wire else None,
            "MAX_DRAWDOWN_EXCEEDED" if required else missing_reason,
            incomplete=not required,
        ),
        _gate(
            GATES[5],
            bool(
                values
                and (
                    policy.minimum_entry_count is None
                    or values.entry_count >= policy.minimum_entry_count
                )
            ),
            policy.minimum_entry_count,
            values.entry_count if values and policy.minimum_entry_count is not None else None,
            "MINIMUM_ENTRIES_NOT_MET" if required else missing_reason,
            disabled=policy.minimum_entry_count is None,
            incomplete=not required,
        ),
        _gate(
            GATES[6],
            bool(
                values
                and (
                    policy.minimum_invested_bar_ratio is None
                    or values.invested_bar_ratio >= Decimal(policy.minimum_invested_bar_ratio)
                )
            ),
            policy.minimum_invested_bar_ratio,
            wire["investedBarRatio"]
            if wire and policy.minimum_invested_bar_ratio is not None
            else None,
            "MINIMUM_INVESTED_BAR_RATIO_NOT_MET" if required else missing_reason,
            disabled=policy.minimum_invested_bar_ratio is None,
            incomplete=not required,
        ),
    ]
    return {"fold": fold, "selectionValues": wire, "gates": gates}


def canonical_selection_result(wire):
    """The shared domain/SQL selection-identity result, without runtime time/own hash."""
    if type(wire) is SelectionDecision:
        wire = wire.to_wire()
    return {key: value for key, value in wire.items() if key not in ("selectionHash", "frozenAt")}


@dataclass(frozen=True)
class SelectionDecision:
    policy: StrategySelectionPolicy
    scope: str
    validation_input_hashes: tuple[str, ...]
    _wire_json: str

    def to_wire(self):
        return json.loads(self._wire_json)

    @property
    def selection_hash(self):
        return self.to_wire()["selectionHash"]

    @property
    def selected_candidate_id(self):
        return self.to_wire()["selectedCandidateId"]

    @property
    def status(self):
        return self.to_wire()["status"]

    def display_values(self):
        """12-place projections alongside the exact values in to_wire()."""
        return {
            candidate["candidateId"]: {
                "validationValues": [
                    {
                        "fold": item["fold"],
                        "selectionValues": None
                        if not item["selectionValues"]
                        else {
                            key: display_decimal(Decimal(value))
                            if key in _DECIMAL_FIELDS
                            else value
                            for key, value in item["selectionValues"].items()
                        },
                    }
                    for item in candidate["validationValues"]
                ],
                "medianSelectionValues": None
                if not candidate["medianSelectionValues"]
                else {
                    key: display_decimal(Decimal(value))
                    for key, value in candidate["medianSelectionValues"].items()
                },
            }
            for candidate in self.to_wire()["candidates"]
        }


def canonical_selection_preimage(decision):
    if type(decision) is not SelectionDecision:
        raise ValueError("typed selection decision required")
    return _canonical(
        (
            SELECTION_VERSION,
            decision.policy.binding(),
            decision.scope,
            decision.validation_input_hashes,
            canonical_selection_result(decision),
        )
    )


def select_validation(
    policy: StrategySelectionPolicy,
    candidate_ids: tuple[str, ...],
    inputs: tuple[ValidationSelectionInput, ...],
    *,
    scope: str,
    frozen_at: datetime,
    fold_indices: tuple[int, ...] | None = None,
) -> SelectionDecision:
    """Evaluate a complete closed validation search; never accepts phase/OOS outputs."""
    return _select(policy, candidate_ids, inputs, scope, frozen_at, fold_indices)


def _select(
    policy, candidate_ids, inputs, scope, frozen_at, fold_indices, *, batch_incomplete=False
):
    if (
        type(policy) is not StrategySelectionPolicy
        or type(candidate_ids) is not tuple
        or not (1 <= len(candidate_ids) <= MAX_CANDIDATES)
    ):
        raise ValueError("closed policy and unique immutable search candidates required")
    for candidate_id in candidate_ids:
        digest(candidate_id)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("duplicate candidate identity")
    if (
        type(inputs) is not tuple
        or len(inputs) > MAX_CANDIDATES * MAX_FOLDS
        or any(type(row) is not ValidationSelectionInput for row in inputs)
    ):
        raise ValueError("only typed validation inputs accepted")
    if (
        len({row.manifest_hash for row in inputs}) > 1
        or len({row.reliability.research_mode for row in inputs}) > 1
    ):
        raise ValueError("INCOMPARABLE_BINDINGS")
    frozen_at = utc_time(frozen_at)
    if type(scope) is not str:
        raise ValueError("closed selection scope required")
    if scope == "final":
        if (
            type(fold_indices) is not tuple
            or not 1 <= len(fold_indices) <= MAX_FOLDS
            or any(type(fold) is not int for fold in fold_indices)
            or (fold_indices != tuple(range(len(fold_indices))))
        ):
            raise ValueError("final selection requires the complete declared fold set")
        folds = fold_indices
    elif scope in tuple(f"fold:{i}" for i in range(MAX_FOLDS)):
        folds = (int(scope[-1]),)
        if fold_indices is not None:
            raise ValueError("scope/fold mismatch")
    else:
        raise ValueError("closed selection scope required")
    rows = {}
    for row in inputs:
        key = row.candidate_id, row.fold_index
        if key in rows or row.candidate_id not in candidate_ids or row.fold_index not in folds:
            raise ValueError("foreign or duplicate validation cell")
        rows[key] = row
    # A missing cell remains visible rather than removing a search candidate.
    candidates, keys = [], {}
    for candidate_id in sorted(candidate_ids):
        validation = [
            _validation_evidence(policy, fold, rows.get((candidate_id, fold))) for fold in folds
        ]
        gates = [gate for cell in validation for gate in cell["gates"]]
        incomplete = any(gate["status"] == "INCOMPLETE" for gate in gates)
        eligible = not incomplete and all(gate["status"] != "FAILED" for gate in gates)
        medians = None
        if not incomplete:
            medians = {
                key: exact_decimal(
                    decimal_median(
                        tuple(Decimal(cell["selectionValues"][key]) for cell in validation)
                    )
                )
                for key in _DECIMAL_FIELDS
            }
        if eligible:
            negative_return = Decimal(medians["netReturn"]).copy_negate()
            dd, turnover = Decimal(medians["maxDrawdown"]), Decimal(medians["turnover"])
            first, second = (
                (dd, negative_return)
                if policy.policy_id == CAPITAL_PRESERVATION
                else (negative_return, dd)
            )
            keys[candidate_id] = first, second, turnover, candidate_id
        candidates.append(
            {
                "schemaVersion": SCHEMA_VERSION,
                "candidateId": candidate_id,
                "eligibility": "INCOMPLETE"
                if incomplete
                else "ELIGIBLE"
                if eligible
                else "INELIGIBLE",
                "validationValues": validation,
                "medianSelectionValues": medians,
                "rankingKey": None,
                "rank": None,
                "reasons": sorted({reason for gate in gates for reason in gate["reasonCodes"]}),
            }
        )
    incomplete = batch_incomplete or any(
        candidate["eligibility"] == "INCOMPLETE" for candidate in candidates
    )
    order = sorted(keys, key=keys.get) if not incomplete else []
    status = "INCOMPLETE" if incomplete else "SELECTED" if order else "NO_ELIGIBLE_CANDIDATE"
    for candidate in candidates:
        candidate_id = candidate["candidateId"]
        if candidate_id in order:
            primary, secondary, turnover, _ = keys[candidate_id]
            candidate["rank"] = order.index(candidate_id) + 1
            candidate["rankingKey"] = {
                "primary": exact_decimal(primary),
                "secondary": exact_decimal(secondary),
                "turnover": exact_decimal(turnover),
                "candidateId": candidate_id,
            }
    hashes = tuple(sorted({row.input_hash for row in inputs}))
    # An entirely absent input set cannot produce a domain identity of a complete selection.
    if not hashes:
        raise ValueError("INCOMPLETE_CANDIDATE_SET: no bound validation result fingerprint")
    if len(hashes) != len(inputs):
        raise ValueError("one bound validation result fingerprint per cell required")
    reasons = (
        ["INCOMPLETE_CANDIDATE_SET"]
        if incomplete
        else ["NO_ELIGIBLE_CANDIDATE"]
        if not order
        else [
            "MIN_DRAWDOWN",
            "MAX_NET_RETURN",
            "MIN_TURNOVER",
            "CANONICAL_CANDIDATE_ID",
        ]
        if policy.policy_id == CAPITAL_PRESERVATION
        else [
            "MAX_NET_RETURN",
            "MIN_DRAWDOWN",
            "MIN_TURNOVER",
            "CANONICAL_CANDIDATE_ID",
        ]
    )
    if scope == "final":
        reasons += ["COMPONENTWISE_VALIDATION_MEDIANS", "ALL_VALIDATION_GATES"]
    wire = {
        "schemaVersion": SCHEMA_VERSION,
        "scope": "FINAL" if scope == "final" else "FOLD",
        "fold": None if scope == "final" else folds[0],
        "status": status,
        "selectedCandidateId": order[0] if order else None,
        "strategySelectionPolicy": dict(
            zip(
                (
                    "policyId",
                    "maxDrawdown",
                    "minValidationSessions",
                    "minimumEntryCount",
                    "minimumInvestedBarRatio",
                ),
                policy.binding()[:5],
                strict=True,
            )
        ),
        "strategySelectionPolicyHash": policy.policy_hash,
        "validationInputHash": fingerprint(hashes),
        "candidateIds": sorted(candidate_ids),
        "candidateCount": len(candidate_ids),
        "eligibleCount": len(keys),
        "candidates": candidates,
        "rankingReasons": reasons,
        "selectorVersion": SELECTION_VERSION,
        "selectorFingerprint": SELECTOR_FINGERPRINT,
    }
    wire["selectionHash"] = selection_identity(
        policy,
        scope=scope,
        validation_input_hashes=hashes,
        result=wire,
    )
    wire["frozenAt"] = frozen_at.isoformat().replace("+00:00", "Z")
    encoded = json.dumps(wire, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > MAX_PUBLICATION_BYTES:
        raise ValueError("selection publication budget exceeded")
    return SelectionDecision(policy, scope, hashes, encoded)


@dataclass(frozen=True)
class PlannedPhase:
    fold_index: int | None
    phase: str
    role: str
    window: PhaseWindow
    candidate_ids: tuple[str, ...]
    reference_ids: tuple[str, ...]
    not_evaluated: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class FrozenSelectionPlan:
    definition_hash: str
    decisions: tuple[SelectionDecision, ...]
    phases: tuple[PlannedPhase, ...]

    @property
    def complete(self):
        return all(decision.status != "INCOMPLETE" for decision in self.decisions)

    @property
    def fitting_status(self):
        return FITTING_STATUS

    @property
    def selection_hashes(self):
        return tuple(decision.selection_hash for decision in self.decisions)


def plan_analysis_validation(definition: ExperimentDefinition) -> tuple[PlannedPhase, ...]:
    if type(definition) is not ExperimentDefinition:
        raise ValueError("typed immutable definition required")
    candidates = tuple(c.candidate_id for c in definition.candidates)
    references = tuple(c.candidate_id for c in REFERENCE_CONFIGURATIONS)
    return tuple(
        PlannedPhase(fold, phase, role, window, candidates, references)
        for fold, phase, role, window in definition.protocol.phases
        if phase in ("training", "validation")
    )


def freeze_selection_plan(
    definition: ExperimentDefinition,
    inputs: tuple[ValidationSelectionInput, ...],
    *,
    frozen_at: datetime,
) -> FrozenSelectionPlan:
    """Build all scopes together. Persist this artifact before executing any OOS work."""
    phases = plan_analysis_validation(definition)
    candidates, references = phases[0].candidate_ids, phases[0].reference_ids
    policy = definition.strategy_selection_policy
    folds = tuple(range(len(definition.protocol.folds)))
    final = select_validation(
        policy, candidates, inputs, scope="final", frozen_at=frozen_at, fold_indices=folds
    )
    if any(row.reliability.research_mode != definition.research_mode for row in inputs):
        raise ValueError("INCOMPARABLE_BINDINGS: validation data mode differs from definition")
    complete = final.status != "INCOMPLETE"
    decisions = tuple(
        _select(
            policy,
            candidates,
            tuple(row for row in inputs if row.fold_index == fold),
            f"fold:{fold}",
            frozen_at,
            None,
            batch_incomplete=not complete,
        )
        for fold in folds
        if any(row.fold_index == fold for row in inputs)
    ) + (final,)
    if sum(len(decision._wire_json.encode()) for decision in decisions) > MAX_PUBLICATION_BYTES:
        raise ValueError("selection publication budget exceeded")
    if not complete:
        return FrozenSelectionPlan(definition.definition_hash, decisions, phases)
    late_phases = []
    for fold, phase, role, window in definition.protocol.phases:
        if phase not in ("test", "holdout"):
            continue
        winner = (final if fold is None else decisions[fold]).selected_candidate_id
        reason = "NOT_EVALUATED_NO_SELECTION" if winner is None else "NOT_EVALUATED_BY_PROTOCOL"
        late_phases.append(
            PlannedPhase(
                fold,
                phase,
                role,
                window,
                (winner,) if winner is not None else (),
                references,
                tuple(
                    (candidate_id, reason) for candidate_id in candidates if candidate_id != winner
                ),
            )
        )
    return FrozenSelectionPlan(definition.definition_hash, decisions, phases + tuple(late_phases))


@dataclass(frozen=True)
class ExposureMarket:
    instrument_id: UUID | None
    feed: str = "iex"
    interval: str = "15Min"
    adjustment: str = "raw"

    def __post_init__(self):
        if (self.instrument_id is not None and type(self.instrument_id) is not UUID) or any(
            type(part) is not str or not part
            for part in (self.feed, self.interval, self.adjustment)
        ):
            raise ValueError("market identity required")


@dataclass(frozen=True)
class ExposureInterval:
    """An actual UTC interval, including historical bounds off the QSL phase grid."""

    start: datetime
    end: datetime

    def __post_init__(self):
        if utc_time(self.start) >= utc_time(self.end):
            raise ValueError("nonempty exposure interval required")


@dataclass(frozen=True)
class ExposureWindow:
    phase_key: str
    role: str
    window: PhaseWindow | ExposureInterval | None
    bar_starts: tuple[datetime, ...] = ()
    warmup_starts: tuple[datetime, ...] = ()
    result_available: bool = False

    def __post_init__(self):
        if (
            type(self.phase_key) is not str
            or not self.phase_key
            or self.role
            not in (
                *_PERFORMANCE_ROLES,
                "UNKNOWN",
            )
            or (self.window is None and self.role != "UNKNOWN")
            or (
                self.window is not None and type(self.window) not in (PhaseWindow, ExposureInterval)
            )
            or type(self.result_available) is not bool
        ):
            raise ValueError("role and phase interval required, except unbound legacy UNKNOWN")
        for starts, maximum in ((self.bar_starts, MAX_SNAPSHOT_ROWS), (self.warmup_starts, 99)):
            if (
                type(starts) is not tuple
                or len(starts) > maximum
                or (tuple(sorted(set(starts))) != starts)
            ):
                raise ValueError("bounded chronological bar identities required")
            for at in starts:
                utc_time(at)
                if at.minute % 15 or at.second or at.microsecond:
                    raise ValueError("bar identity is off the 15-minute grid")
        if (self.bar_starts or self.warmup_starts) and self.window is None:
            raise ValueError("bar identities require a known phase interval")
        if self.bar_starts and not (
            self.window.start <= self.bar_starts[0] <= self.bar_starts[-1] < self.window.end
        ):
            raise ValueError("performance bars outside phase interval")
        if (
            self.warmup_starts
            and self.warmup_starts[-1] + timedelta(minutes=15) > self.window.start
        ):
            raise ValueError("warmup must precede performance")


def planned_exposure_windows(definition, bindings=()):
    """Use exact confirmed starts when provided, including seed identities across gaps."""
    if (
        type(definition) is not ExperimentDefinition
        or type(bindings) is not tuple
        or any(type(binding) is not PhaseSeedBinding for binding in bindings)
    ):
        raise ValueError("typed definition and phase seed bindings required")
    phases = definition.protocol.phases
    if bindings and (
        tuple((b.fold_index, b.phase, b.role, b.window) for b in bindings) != phases
        or any(len(b.expected_seed_starts) != definition.warmup_count for b in bindings)
    ):
        raise ValueError("phase seed bindings differ from protocol")
    return tuple(
        ExposureWindow(
            phase if fold is None else f"{fold}:{phase}",
            role,
            window,
            bindings[index].expected_bar_starts if bindings else (),
            bindings[index].expected_seed_starts if bindings else (),
        )
        for index, (fold, phase, role, window) in enumerate(phases)
    )


@dataclass(frozen=True)
class ResearchCounts:
    planned: int = 0
    started: int = 0
    complete: int = 0
    failed: int = 0
    blocked: int = 0
    not_evaluated: int = 0

    def __post_init__(self):
        counts = (
            self.planned,
            self.started,
            self.complete,
            self.failed,
            self.blocked,
            self.not_evaluated,
        )
        if (
            any(type(value) is not int or not 0 <= value <= MAX_CANDIDATES for value in counts)
            or (not sum(counts[2:5]) <= self.started <= self.planned)
            or sum(counts[2:]) > self.planned
        ):
            raise ValueError("inconsistent research counts")


@dataclass(frozen=True)
class ExposureRound:
    """Current projection at a bound register position, with one ID per submitted round."""

    source_id: UUID
    source_kind: str
    market: ExposureMarket
    status: str
    windows: tuple[ExposureWindow, ...]
    definition_hash: str | None = None
    supersedes: UUID | None = None
    candidate_counts: ResearchCounts = ResearchCounts()
    reference_counts: ResearchCounts = ResearchCounts()

    def __post_init__(self):
        if (
            type(self.source_id) is not UUID
            or self.source_kind
            not in (
                "RUN",
                "RETROSPECTIVE",
                "BATCH",
            )
            or type(self.market) is not ExposureMarket
            or self.status
            not in (
                "DRAFT",
                "VALIDATED",
                *_REGISTERED_STATES,
            )
            or type(self.windows) is not tuple
            or any(type(window) is not ExposureWindow for window in self.windows)
            or len({window.phase_key for window in self.windows}) != len(self.windows)
            or (self.status in _REGISTERED_STATES and not self.windows)
            or (
                self.supersedes is not None
                and (type(self.supersedes) is not UUID or self.supersedes == self.source_id)
            )
            or type(self.candidate_counts) is not ResearchCounts
            or (
                type(self.reference_counts) is not ResearchCounts
                or self.reference_counts.planned > 2
            )
        ):
            raise ValueError("closed exposure round projection required")
        if self.status in ("DRAFT", "VALIDATED") and any(w.result_available for w in self.windows):
            raise ValueError("draft rounds cannot have results")
        if self.definition_hash is not None:
            digest(self.definition_hash)


@dataclass(frozen=True)
class ExposureOverlap:
    current: ExposureWindow
    prior: ExposureWindow
    prior_round: ExposureRound
    interval: ExposureInterval
    overlap_bar_count: int

    def to_wire(self):
        def window_wire(window):
            return {"start": _canonical(window.start), "end": _canonical(window.end)}

        source = self.prior_round
        return {
            "schemaVersion": SCHEMA_VERSION,
            "currentRole": self.current.role,
            "priorRole": self.prior.role,
            "currentInterval": window_wire(self.current.window),
            "priorInterval": window_wire(self.prior.window),
            "overlapInterval": window_wire(self.interval),
            "priorBatchId": str(source.source_id) if source.source_kind == "BATCH" else None,
            "priorRunId": str(source.source_id) if source.source_kind != "BATCH" else None,
            "priorDefinitionHash": source.definition_hash,
            "priorStatus": source.status,
            "knowledgeStatus": "RESULT_AVAILABLE" if self.prior.result_available else "REQUESTED",
            "overlapBarCount": self.overlap_bar_count,
        }


@dataclass(frozen=True)
class WarmupOverlap:
    current_phase_key: str
    prior_phase_key: str
    prior_source_id: UUID
    seed_starts: tuple[datetime, ...]


@dataclass(frozen=True)
class ExposureComparison:
    registered_rounds: tuple[ExposureRound, ...]
    draft_rounds: tuple[ExposureRound, ...]
    overlaps: tuple[ExposureOverlap, ...]
    warmup_overlaps: tuple[WarmupOverlap, ...]
    unresolved_prior_rounds: tuple[ExposureRound, ...]

    @property
    def reuse_status(self):
        return "REGISTERED_REUSE" if self.overlaps else "NO_REGISTERED_PRIOR_USE"

    @property
    def external_knowledge_status(self):
        return "EXTERNAL_KNOWLEDGE_UNKNOWN"

    @property
    def prior_failed_round_count(self):
        return sum(r.status == "FAILED" for r in self.registered_rounds)

    @property
    def prior_blocked_round_count(self):
        return sum(r.status == "BLOCKED" for r in self.registered_rounds)

    @property
    def superseded_round_count(self):
        submitted = {r.source_id for r in self.registered_rounds}
        return len(submitted & {r.supersedes for r in self.registered_rounds})

    @property
    def repeated_validation_count(self):
        return sum(o.current.role == o.prior.role == "PARAMETER_SELECTION" for o in self.overlaps)


def compare_exposures(
    current: ExposureRound,
    prior_rounds: tuple[ExposureRound, ...],
) -> ExposureComparison:
    """Compare every performance role symmetrically at one caller-bound registry position.

    Null historical ranges/market identities stay unresolved, with no invented interval.
    Warmup intersections are separate and never cause performance reuse.
    """
    if (
        type(current) is not ExposureRound
        or current.market.instrument_id is None
        or not (
            current.windows and all(window.role in _PERFORMANCE_ROLES for window in current.windows)
        )
        or type(prior_rounds) is not tuple
        or any(type(row) is not ExposureRound for row in prior_rounds)
    ):
        raise ValueError("typed current phases and bound historical rounds required")
    prior_by_id = {}
    current_key = current.source_kind, current.source_id
    for row in prior_rounds:
        key = row.source_kind, row.source_id
        if key == current_key:
            continue
        existing = prior_by_id.setdefault(key, row)
        if existing != row:
            raise ValueError("conflicting projections for a single bound historical round")
    rows = tuple(prior_by_id[key] for key in sorted(prior_by_id))
    drafts = tuple(row for row in rows if row.status not in _REGISTERED_STATES)
    registered = tuple(row for row in rows if row.status in _REGISTERED_STATES)
    overlaps, warmup, unresolved = [], [], []
    for row in registered:
        if row.market != current.market:
            if row.market.instrument_id is None and (
                row.market.feed,
                row.market.interval,
                row.market.adjustment,
            ) == (current.market.feed, current.market.interval, current.market.adjustment):
                unresolved.append(row)
            continue
        if any(prior.window is None for prior in row.windows):
            unresolved.append(row)
        for prior in sorted(row.windows, key=lambda window: window.phase_key):
            if prior.window is None:
                continue
            for phase in current.windows:
                a, b = prior.window, phase.window
                start, end = max(a.start, b.start), min(a.end, b.end)
                if start < end:
                    starts = phase.bar_starts or prior.bar_starts
                    count = bisect_left(starts, end) - bisect_left(starts, start)
                    overlaps.append(
                        ExposureOverlap(phase, prior, row, ExposureInterval(start, end), count)
                    )
                seeds = tuple(at for at in phase.warmup_starts if a.start <= at < a.end)
                if seeds:
                    warmup.append(
                        WarmupOverlap(phase.phase_key, prior.phase_key, row.source_id, seeds)
                    )
    return ExposureComparison(registered, drafts, tuple(overlaps), tuple(warmup), tuple(unresolved))
