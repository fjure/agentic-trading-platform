"""Read and admit immutable Quant Lab experiments through the isolated research DB."""

from __future__ import annotations

import re
from contextlib import nullcontext
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from itertools import combinations
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from packages.agent_research.companion.testability import Assessment, Classification
from packages.agent_research.quant_approval import ApprovalBinding, ApprovalDenied
from packages.backtesting.calendar import (
    first_signal_knowledge,
    read_schedule_rows,
    schedule_windows,
)
from packages.backtesting.comparison import (
    BatchComparisonReference,
    ComparisonBinding,
    compare_batches,
)
from packages.backtesting.dataset import DatasetPort, quant_phase_history_start
from packages.backtesting.experiment import (
    MAX_CANDIDATES,
    MAX_FOLDS,
    MAX_PUBLICATION_BYTES,
    MAX_SIMULATION_BAR_STEPS,
    MAX_SNAPSHOT_ROWS,
    PHASE_ROLES,
    REFERENCE_CONFIGURATIONS,
    SCHEMA_VERSION,
    SELECTION_VERSION,
    SELECTOR_FINGERPRINT,
    StrategySelectionPolicy,
)
from packages.backtesting.experiment_storage import (
    PUBLIC_ROLES,
    ExperimentStorage,
    definition_domain,
)
from packages.backtesting.jobs import BacktestError
from packages.backtesting.models import BacktestBlocked, BacktestBudgetExceeded
from packages.backtesting.quant_lab_history import (
    HISTORY_LIMITATIONS,
    empty_counts,
    history_bound,
    project_history,
)
from packages.backtesting.retrospective import RetrospectiveTrustContext, retrospective_calendar
from packages.backtesting.strategies import (
    CATALOG_VERSION,
    FITTING_STATUS,
    QUANT_REGISTRY,
    resolve_strategy,
)
from packages.domain.models import utc_string
from packages.persistence.transaction import ScopedConnection
from packages.research.calendar import expected_windows
from packages.research.evidence_storage import EvidenceBudgetExceeded, read_trust_snapshot
from packages.research.models import BarInterval, content_fingerprint
from packages.research.registry import definitions
from services.control_api.backtest_service import BacktestService, identity, stamp

ROLES = dict(PHASE_ROLES)
STORAGE_ROLES = {public: internal for internal, public in PUBLIC_ROLES.items()}
TERMINAL = {"SUCCEEDED", "BLOCKED", "FAILED"}
LEASE_REASONS = {
    "ATTEMPTS_EXHAUSTED": "ATTEMPTS_EXHAUSTED",
    "BUDGET_EXHAUSTED": "BUDGET_EXCEEDED",
    "BUDGET_EXCEEDED": "BUDGET_EXCEEDED",
    "DEADLINE_EXCEEDED": "DEADLINE_EXCEEDED",
    "CODE_VERSION_MISMATCH": "VERSION_UNAVAILABLE",
    "VERSION_UNAVAILABLE": "VERSION_UNAVAILABLE",
    "MISSING_CALENDAR": "MISSING_CALENDAR",
    "BLOCKED_CALENDAR_UNKNOWN": "MISSING_CALENDAR",
    "SNAPSHOT_UNAVAILABLE": "SNAPSHOT_UNAVAILABLE",
    "RELIABILITY_BLOCKED": "RELIABILITY_BLOCKED",
    "INCOMPLETE_BAR_SERIES": "INCOMPLETE_BAR_SERIES",
    "DATA_UNAVAILABLE": "DATA_UNAVAILABLE",
}


def limitations(mode):
    values = [*HISTORY_LIMITATIONS, "DIVIDENDS_EXCLUDED", "NOT_TRAINABLE", "PHASE_RESETS"]
    if mode == "RETROSPECTIVE_EXPLORATORY":
        values.extend(("NOT_STRICT_PIT", "EX_POST_REVISIONS"))
    return values


def _camel(value):
    first, *rest = value.split("_")
    return first + "".join(item.title() for item in rest)


def strategy_catalog():
    items = []
    for definition in QUANT_REGISTRY:
        resolve_strategy(definition.strategy_id, definition.version, require_execution=True)
        row = asdict(definition)
        row["limitations"] = [
            "FINITE_SEED" if item == "FINITE_COMMON_SEED" else item for item in row["limitations"]
        ]
        row["parameter_definitions"] = [
            {_camel(key): value for key, value in item.items()}
            for item in row["parameter_definitions"]
        ]
        items.append(
            {
                _camel(key): list(value) if isinstance(value, tuple) else value
                for key, value in row.items()
            }
        )
    return dict(
        schemaVersion=SCHEMA_VERSION,
        catalogVersion=CATALOG_VERSION,
        items=items,
        limits=dict(
            maximumCandidates=MAX_CANDIDATES,
            referenceCount=2,
            maximumFolds=MAX_FOLDS,
            maximumSpanDays=366,
            maximumSimulationBarSteps=MAX_SIMULATION_BAR_STEPS,
            maximumSnapshotRows=MAX_SNAPSHOT_ROWS,
            maximumPublicationBytes=MAX_PUBLICATION_BYTES,
            attemptDeadlineSeconds=295,
            maximumAttempts=3,
            maximumActiveAttemptSeconds=885,
            queueSlots=100,
        ),
        limitations=HISTORY_LIMITATIONS,
    )


def _invalid_definition(payload):
    try:
        StrategySelectionPolicy.from_mapping(payload["strategySelectionPolicy"])
    except ValueError as error:
        raise BacktestError(422, "INVALID_POLICY") from error
    try:
        domain = definition_domain(payload)
    except ValueError as error:
        message = str(error)
        code = (
            "INVALID_PROTOCOL"
            if any(word in message for word in ("fold", "phase", "protocol", "UTC", "chronolog"))
            else "INVALID_PARAMETERS"
        )
        raise BacktestError(422, code) from error
    if payload["dataSelectionPolicyVersion"] != domain.data_selection_policy_version:
        raise BacktestError(422, "INVALID_POLICY")
    if (domain.instrument_id, domain.symbol) not in {
        (instrument.instrument_id, mapping.symbol) for instrument, mapping in definitions()
    }:
        raise BacktestError(422, "INVALID_PARAMETERS")
    return domain


def _calendar_plan(rows, domain, snapshot):
    strict = domain.research_mode == "STRICT_PIT"
    retro_coverage = None if strict else retrospective_calendar(rows, snapshot)[0]
    starts, earliest = {}, domain.protocol.holdout.start
    for fold, phase, _, window in domain.protocol.phases:
        seed_start = quant_phase_history_start(
            snapshot, window, domain.warmup_count, rows, strict=strict
        )
        # A phase's first decision must be supported by the schedule actually visible
        # at that time, in addition to the bound snapshot's completed schedule.
        knowledge = (
            first_signal_knowledge(rows, window.start, window.end, snapshot)
            if strict
            else snapshot.at
        )
        if knowledge is None:
            raise BacktestBlocked("MISSING_CALENDAR")
        seed_span_start = seed_start if domain.warmup_count else window.start
        if strict:
            _, planned = schedule_windows(rows, seed_span_start, window.end, knowledge, snapshot)
            _, current = schedule_windows(rows, window.start, window.end, snapshot.at, snapshot)
        else:
            planned = expected_windows(
                retro_coverage, BarInterval.FIFTEEN_MINUTES, seed_span_start, window.end, window.end
            )
            current = expected_windows(
                retro_coverage, BarInterval.FIFTEEN_MINUTES, window.start, window.end, window.end
            )
        performance = tuple(start for start, _ in planned.windows if start >= window.start)
        if (
            planned.unknown_dates
            or current.unknown_dates
            or not performance
            or sum(end <= window.start for _, end in planned.windows) != domain.warmup_count
            or tuple(start for start, _ in current.windows) != performance
        ):
            raise BacktestBlocked("MISSING_CALENDAR")
        starts[(phase, fold)] = performance
        earliest = min(earliest, seed_start if domain.warmup_count else window.start)
    return starts, earliest


def _preflight_calendar(connection, domain, snapshot):
    rows = read_schedule_rows(connection, snapshot)
    imported = tuple(row for row in rows if row.get("evidence_id") is not None)
    if domain.research_mode != "RETROSPECTIVE_EXPLORATORY" or not imported:
        return rows
    try:
        state = read_trust_snapshot(connection, snapshot.at, snapshot.version)
    except EvidenceBudgetExceeded:
        raise BacktestBudgetExceeded() from None
    trust = RetrospectiveTrustContext.from_state(
        snapshot,
        state | {"version": content_fingerprint((state["version"], state["policy_version"]))},
    )
    verified = {row.reference for row in trust.records if row.content_verified}
    if any(str(row["evidence_id"]) not in verified for row in imported):
        raise BacktestBlocked("MISSING_CALENDAR")
    # Retrospective execution admits verified content with unproven historical
    # delivery time. Strict mode above keeps its independent temporal gate.
    return tuple(dict(row, managed_trust_state=None) for row in rows)


def _revision_count(connection, domain, snapshot, start, calendar_count):
    values = dict(
        instrument=domain.instrument_id,
        start=start,
        end=domain.protocol.holdout.end,
        version=snapshot.version,
        at=snapshot.at,
    )
    total = calendar_count + 1  # Existing license receipt is one bound metadata row.
    bar_rows = 0
    for table, criteria in (
        (
            "research.bar_revisions",
            "instrument_id=:instrument AND bar_start>=:start AND bar_end<=:end "
            "AND feed='iex' AND interval='15Min' AND adjustment='raw' AND ingested_at<=:at",
        ),
        ("research.quality_observations", "instrument_id=:instrument AND ingested_at<=:at"),
        ("backtest.corporate_action_coverage", "instrument_id=:instrument AND ingested_at<=:at"),
        (
            "research.evidence_revisions",
            "(instrument_id=:instrument OR kind='CALENDAR') AND ingested_at<=:at",
        ),
        ("research.trust_decisions", "recorded_at<=:at"),
    ):
        count = connection.execute(
            text(
                f"SELECT count(*) FROM (SELECT 1 FROM {table} "
                f"WHERE commit_version<=:version AND {criteria} LIMIT 100001) AS capped"
            ),
            values,
        ).scalar_one()
        total += count
        if table == "research.bar_revisions":
            bar_rows = count
        if total > MAX_SNAPSHOT_ROWS:
            break
    return total, total - bar_rows


def estimate_budget(connection, definition, domain, snapshot):
    """Bound feasibility with actual observed calendar. No price or provider access."""
    body = dict(
        definitionHash=definition["fingerprint"],
        calendarVerified=False,
        simulationBarSteps=None,
        snapshotRows=None,
        publicationBytes=None,
        seedBars=domain.warmup_count,
        reasons=[],
    )
    starts = {}
    try:
        rows = _preflight_calendar(connection, domain, snapshot)
        starts, earliest = _calendar_plan(rows, domain, snapshot)
    except BacktestBlocked:
        body["reasons"] = ["MISSING_CALENDAR"]
        return body, starts
    except BacktestBudgetExceeded:
        body["reasons"] = ["BUDGET_EXCEEDED"]
        return body, starts
    # Analysis and validation cover the entire search. Each later phase can
    # execute at most one selected search candidate and two fixed references.
    calls, steps = 0, 0
    for (phase, _fold), points in starts.items():
        executions = domain.queue_slots if phase in {"training", "validation"} else 3
        calls += executions
        steps += (len(points) + domain.warmup_count) * executions
    span_start = max(
        domain.protocol.holdout.end - timedelta(days=366),
        earliest if domain.warmup_count else earliest - timedelta(minutes=15),
    )
    rows_count, metadata_count = _revision_count(
        connection, domain, snapshot, span_start, len(rows)
    )
    # Allow a full set of non-price reliability references on each potential
    # execution, two price references per bar (decision/outcome), seed refs,
    # both curves, and fixed per-child/phase/selection envelopes. The estimate
    # does not reduce for protocol nonselection or deduplication.
    publication = 65536 + 1024 * steps + 1024 * rows_count + (16384 + 384 * metadata_count) * calls
    body.update(
        calendarVerified=True,
        simulationBarSteps=steps if steps <= MAX_SIMULATION_BAR_STEPS else None,
        snapshotRows=rows_count if rows_count <= MAX_SNAPSHOT_ROWS else None,
        publicationBytes=publication if publication <= MAX_PUBLICATION_BYTES else None,
    )
    if any(
        body[key] is None for key in ("simulationBarSteps", "snapshotRows", "publicationBytes")
    ) or (domain.protocol.holdout.end - earliest > timedelta(days=366)):
        body["reasons"] = ["BUDGET_EXCEEDED"]
    return body, starts


def _agent_data_reason(connection, domain, snapshot, context, deadline):
    from services.worker.quant_lab_data import MAX_ROWS, QuantTrustCap, read_quant_phase_inputs

    def checkpoint():
        if datetime.now(UTC) >= deadline:
            raise BacktestBudgetExceeded("quant preparation deadline reached")

    trust = connection.execute(text("""
        SELECT version,policy_version,(
          SELECT count(*) FROM (
            SELECT version FROM research.trust_decisions
            WHERE version<=:version LIMIT :limit
          ) bounded
        ) AS decisions
        FROM research.trust_version_state WHERE singleton
    """), {"version": context["trustVersion"], "limit": MAX_ROWS + 1}).one()
    if trust.version != context["trustVersion"]:
        raise BacktestError(409, "APPROVAL_STALE")
    cap = QuantTrustCap(trust.version, trust.policy_version, trust.decisions)
    try:
        checkpoint()
        read_quant_phase_inputs(
            lambda: nullcontext(connection), domain, snapshot, cap, checkpoint, checkpoint,
        )
        checkpoint()
    except (BacktestBudgetExceeded, EvidenceBudgetExceeded):
        return "BUDGET_EXCEEDED"
    except BacktestBlocked:
        return "RELIABILITY_BLOCKED"
    return None


def _one(connection, query, parameters):
    row = connection.execute(text(query), parameters).mappings().one_or_none()
    if row is None:
        raise BacktestError(404, "NOT_FOUND")
    return dict(row)


def _phase_key(phase, fold):
    if phase == "holdout" and fold is None:
        return phase
    if phase in {"training", "validation", "test"} and type(fold) is int and 0 <= fold < MAX_FOLDS:
        return f"{fold}:{phase}"
    raise BacktestError(400, "INVALID_REQUEST")


def _reason(status, reason):
    if not reason:
        return []
    return [
        LEASE_REASONS.get(
            reason, "DATA_UNAVAILABLE" if status == "BLOCKED" else "INCOMPLETE_CANDIDATE_SET"
        )
    ]


def _encode_schedule(starts):
    """Persist only regular-session runs needed to replay history pagination."""
    schedule = []
    step = timedelta(minutes=15)
    for (phase, fold), values in starts.items():
        runs = []
        previous = None
        for start in values:
            if previous is not None and start - previous == step:
                runs[-1][1] += 1
            else:
                runs.append([utc_string(start), 1])
            previous = start
        schedule.append(dict(phase=phase, fold=fold, runs=runs))
    return schedule


def _decode_schedule(schedule):
    step = timedelta(minutes=15)
    return {
        (row["phase"], row["fold"]): tuple(
            start + step * index
            for encoded, count in row["runs"]
            for start in (stamp(encoded),)
            for index in range(count)
        )
        for row in schedule
    }


class QuantLabService:
    _sign = BacktestService._sign
    _verify = BacktestService._verify
    _binding = staticmethod(BacktestService._binding)

    def __init__(
        self, engine, *, code_version, cursor_key=None, approval_engine=None,
        authorize_operator=None,
    ):
        if not isinstance(code_version, str) or not re.fullmatch(r"[0-9a-f]{40}", code_version):
            raise ValueError("verified code version required")
        self.storage, self.code_version = ExperimentStorage(engine), code_version
        self._approval_storage = None if approval_engine is None else ExperimentStorage(
            approval_engine,
        )
        self._authorize_operator = authorize_operator
        if cursor_key is None:
            with self.storage.transaction() as connection:
                cursor_key = connection.execute(
                    text("SELECT signing_key FROM backtest.cursor_state WHERE singleton")
                ).scalar_one()
        if not isinstance(cursor_key, bytes) or len(cursor_key) < 32:
            raise ValueError("cursor signing material required")
        self._cursor_key = cursor_key

    def get_strategies(self):
        return strategy_catalog()

    def _cursor(self, kind, scope, query, **context):
        if not isinstance(scope, str) or not scope or len(scope) > 128:
            raise BacktestError(403, "FORBIDDEN")
        limit = query.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise BacktestError(400, "INVALID_REQUEST")
        binding = self._binding(
            "quant:" + kind,
            dict(
                scope=scope,
                **context,
                query={key: value for key, value in query.items() if key != "cursor"},
            ),
        )
        token = None
        if "cursor" in query:
            try:
                token = self._verify(query["cursor"], binding)
            except BacktestError as error:
                if error.status == 409:
                    raise BacktestError(400, "CURSOR_INVALID") from None
                raise
            snapshot = token["snapshot"]
            if not isinstance(snapshot, dict) or set(snapshot) != {"expiresAt", "value"}:
                raise BacktestError(400, "CURSOR_INVALID")
            if datetime.now(UTC) >= stamp(snapshot["expiresAt"]):
                raise BacktestError(400, "CURSOR_INVALID")
            token["snapshot"] = snapshot["value"]
        return binding, limit, token

    def _next(self, binding, snapshot, position):
        expiration = utc_string(datetime.now(UTC) + timedelta(minutes=15))
        return self._sign(
            dict(
                binding=binding,
                snapshot=dict(expiresAt=expiration, value=snapshot),
                position=position,
            )
        )

    @staticmethod
    def _write(actor, key, trace):
        if actor != "local-operator":
            raise BacktestError(403, "FORBIDDEN")
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", key)
            or (not isinstance(trace, str) or not re.fullmatch(r"[0-9a-f]{32}", trace))
        ):
            raise BacktestError(400, "INVALID_REQUEST")

    def create_experiment(
        self, payload, *, actor, key, trace, scope="creation", _agent_origin=None,
        _transaction: ScopedConnection | None = None,
    ):
        self._write(actor, key, trace)
        domain = _invalid_definition(payload)
        candidates = [
            dict(
                role=role,
                configuration=dict(
                    strategyId=configuration.strategy_id,
                    version=configuration.strategy_version,
                    parameters=dict(configuration.parameters),
                ),
            )
            for role, configuration in [
                *(("SEARCH", c) for c in domain.candidates),
                *zip(
                    ("FLAT_REFERENCE", "BUY_HOLD_REFERENCE"), REFERENCE_CONFIGURATIONS, strict=True
                ),
            ]
        ]
        namespace = "create" if _agent_origin is None else f"agent-create:{_agent_origin[0]}"
        identifier = uuid5(NAMESPACE_URL, f"quant-lab-v1:{namespace}:{actor}:{key}")
        origin = {} if _agent_origin is None else {"agent_origin": _agent_origin}
        row = self.storage.create_definition(
            payload, candidates, actor=actor, identifier=identifier, scope=_transaction, **origin,
        )
        return self._definition_wire(
            row, {}, scope=scope, force_draft=True, connection=_transaction,
        )

    def create_agent_experiment(
        self, payload, *, actor, key, trace, run_id, idea_id, idea_version,
        transaction: ScopedConnection | None = None,
    ):
        return self.create_experiment(
            payload, actor=actor, key=key, trace=trace,
            _agent_origin=(identity(run_id), idea_id, idea_version),
            _transaction=transaction,
        )

    @staticmethod
    def _version(row, payload):
        if (row["definition_version"], row["fingerprint"]) != (
            payload["definitionVersion"],
            payload["definitionHash"],
        ):
            raise BacktestError(409, "DEFINITION_CONFLICT")

    def validate_experiment(
        self, id, payload, *, actor, key, trace, scope="validation", _agent_preflight=False,
        _transaction: ScopedConnection | None = None,
        _input_deadline: datetime | None = None,
    ):
        self._write(actor, key, trace)
        identifier = identity(id)
        definition = self.storage.definition(identifier, scope=_transaction)
        self._version(definition, payload)
        saved = self.storage.validation_request(identifier, key, scope=_transaction)
        if saved is None:
            domain = definition_domain(
                definition["body"],
                definition_version=definition["definition_version"],
                predecessor_hash=self._predecessor_hash(definition, transaction=_transaction),
            )
            with self.storage.transaction(scope=_transaction) as connection:
                snapshot = DatasetPort().snapshot(connection)
                body, starts = estimate_budget(connection, definition, domain, snapshot)
                if _agent_preflight:
                    context = connection.execute(
                        text("SELECT backtest.agent_validation_context(:version)"),
                        {"version": snapshot.version},
                    ).scalar_one()
                    if context is None:
                        raise BacktestError(409, "APPROVAL_STALE")
                    body["agentValidationContext"] = context
                    if _input_deadline is not None and not body["reasons"]:
                        reason = _agent_data_reason(
                            connection, domain, snapshot, context, _input_deadline,
                        )
                        if reason is None:
                            body["agentDataVerified"] = True
                        else:
                            body["reasons"].append(reason)
                body["validatedAt"] = utc_string(snapshot.at)
                receipt = dict(history_bound(connection))
                body["registryWatermark"] = receipt["mark"]
                body["historyPhaseRuns"] = _encode_schedule(starts)
                body["researchHistory"] = project_history(
                    connection, definition, phase_bar_starts=starts, **receipt
                )
            saved = self.storage.record_validation_request(
                identifier, key, body, valid=not body["reasons"], scope=_transaction,
            )
        result = self._validation_wire(definition, saved)
        history = result["researchHistory"]
        if history["overlapCount"] > len(history["overlaps"]):
            selected = result["id"]
            query = {"validationId": selected, "limit": 200}
            binding, _, _ = self._cursor("history", scope, query, resource=("definition", id))
            receipt = dict(
                mark=saved["body"]["registryWatermark"],
                registry_hash=history["registryHash"],
                bound_at=history["boundAt"],
            )
            result["researchHistory"] = dict(
                history, nextOverlapCursor=self._next(binding, receipt, len(history["overlaps"]))
            )
        return result

    def validate_agent_experiment(
        self, id, payload, *, actor, key, trace, scope="validation",
        transaction: ScopedConnection | None = None,
        input_deadline: datetime | None = None,
    ):
        return self.validate_experiment(
            id, payload, actor=actor, key=key, trace=trace, scope=scope, _agent_preflight=True,
            _transaction=transaction, _input_deadline=input_deadline,
        )

    def _predecessor_hash(self, definition, *, transaction=None):
        predecessor = definition["predecessor_id"]
        if predecessor is None:
            return None
        return self.storage.definition(predecessor, scope=transaction)["fingerprint"]

    @staticmethod
    def _validation_wire(definition, validation):
        body = validation["body"]
        valid = validation["valid"]
        count = definition["candidate_count"]
        return dict(
            schemaVersion=SCHEMA_VERSION,
            id=str(validation["validation_id"]),
            definitionId=str(definition["definition_id"]),
            definitionVersion=definition["definition_version"],
            definitionHash=definition["fingerprint"],
            validationHash=validation["fingerprint"],
            validatedAt=body["validatedAt"],
            status="VALIDATED" if valid else "INVALID",
            strategySelectionPolicy=definition["body"]["strategySelectionPolicy"],
            strategySelectionPolicyHash=definition["policy_hash"],
            budgetEstimate=dict(
                schemaVersion=SCHEMA_VERSION,
                candidateCount=count,
                reservedQueueSlots=count + 2,
                snapshotRows=body["snapshotRows"],
                simulationBarSteps=body["simulationBarSteps"],
                publicationBytes=body["publicationBytes"],
                calendarConfirmed=body["calendarVerified"],
                withinLimits=valid,
            ),
            researchHistory=body["researchHistory"],
            reasons=body["reasons"],
            limitations=limitations(definition["body"]["researchMode"]),
        )

    def start_experiment_batch(self, id, payload, *, actor, key, trace, scope="admission"):
        self._write(actor, key, trace)
        identifier = identity(id)
        definition = self.storage.definition(identifier)
        self._version(definition, payload)
        try:
            scoped_key = content_fingerprint(
                ("quant-start-v1", identifier, payload["definitionVersion"], key)
            )
            batch = self.storage.enqueue(
                identifier,
                payload["definitionHash"],
                actor,
                scoped_key,
                trace,
                self.code_version,
                definition_version=payload["definitionVersion"],
                validation_id=identity(payload["validationId"]),
                validation_hash=payload["validationHash"],
            )
        except BacktestError as error:
            if error.status == 429:
                raise BacktestError(429, "ADMISSION_LIMIT") from None
            raise
        return self._batch_detail(batch["batch_id"], {}, scope=scope)

    def _require_approval_operator(self, actor, key, trace):
        self._write(actor, key, trace)
        if self._approval_storage is None or self._authorize_operator is None:
            raise BacktestError(403, "APPROVAL_REQUIRED")
        if self._authorize_operator(actor) is not True:
            raise BacktestError(403, "APPROVAL_REQUIRED")

    def prepare_agent_approval(self, binding_document, *, actor, key, trace):
        self._require_approval_operator(actor, key, trace)
        try:
            binding = ApprovalBinding.parse(binding_document)
        except ApprovalDenied:
            raise BacktestError(409, "APPROVAL_STALE") from None
        if binding.to_document()["operatorId"] != actor:
            raise BacktestError(403, "APPROVAL_REQUIRED")
        return self._approval_storage.prepare_approval(binding, key), binding.binding_hash

    def accept_agent_approximation(
        self, *, actor, key, trace, idea_id, idea_version, decision_id, revision,
        proposal_id, proposal_hash,
    ):
        self._require_approval_operator(actor, key, trace)
        if (
            any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value)
                for value in (idea_id, decision_id, proposal_id))
            or any(type(value) is not int or not 1 <= value <= 2**53 - 1
                   for value in (idea_version, revision))
            or not isinstance(proposal_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", proposal_hash)
        ):
            raise BacktestError(400, "INVALID_REQUEST")
        return self._approval_storage.record_approximation(
            actor, idea_id, idea_version, decision_id, revision, proposal_id, proposal_hash,
        )

    def register_agent_approximation(self, definition_id, proposal_id, assessment):
        if (
            not isinstance(assessment, Assessment)
            or assessment.classification is not Classification.TESTABLE_WITH_DECLARED_APPROXIMATION
            or assessment.approximation is None
            or assessment.mapping != assessment.approximation.mapping
        ):
            raise BacktestError(409, "APPROVAL_STALE")
        identifier = identity(definition_id)
        definition = self.storage.definition(identifier)
        domain = definition_domain(
            definition["body"], definition_version=definition["definition_version"],
            predecessor_hash=self._predecessor_hash(definition),
        )
        mapping = assessment.approximation.mapping
        if len(domain.candidates) != 1 or domain.research_mode != mapping.research_mode:
            raise BacktestError(409, "APPROVAL_STALE")
        candidate = domain.candidates[0]
        if (candidate.strategy_id, candidate.strategy_version, candidate.parameters) != (
            mapping.strategy_id, mapping.strategy_version, mapping.parameters,
        ):
            raise BacktestError(409, "APPROVAL_STALE")
        self.storage.register_approximation(identifier, proposal_id, assessment.approximation)

    def start_approved_experiment_batch(
        self, approval_id, binding_hash, *, actor, key, trace, scope="admission",
    ):
        self._require_approval_operator(actor, key, trace)
        if not isinstance(binding_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", binding_hash):
            raise BacktestError(409, "APPROVAL_STALE")
        approval_id = identity(approval_id)
        try:
            batch_id = self._approval_storage.enqueue_approved(
                approval_id, binding_hash, actor, key, trace, self.code_version,
            )
        except BacktestError as error:
            code = "QUANT_CAPACITY_EXHAUSTED" if error.status == 429 else error.code
            if code in {
                "APPROVAL_STALE", "QUANT_CAPACITY_EXHAUSTED",
                "BUDGET_EXHAUSTED", "IDEMPOTENCY_CONFLICT",
            }:
                try:
                    self._approval_storage.record_approval_denial(approval_id, actor, trace, code)
                except BacktestError:
                    pass
            if error.status == 429:
                raise BacktestError(429, code) from None
            raise
        return self._batch_detail(batch_id, {}, scope=scope)

    def _history(
        self,
        connection,
        definition,
        query,
        scope,
        resource,
        *,
        receipt=None,
        counts=None,
        saved=None,
        starts=None,
        seeds=None,
    ):
        binding, limit, token = self._cursor("history", scope, query, resource=resource)
        if receipt is None:
            receipt = token["snapshot"] if token else dict(history_bound(connection))
        mark, digest, at = receipt["mark"], receipt["registry_hash"], receipt["bound_at"]
        if isinstance(at, str):
            at = stamp(at)
        snapshot = dict(mark=mark, registry_hash=digest, bound_at=utc_string(at))
        if token and token["snapshot"] != snapshot:
            raise BacktestError(400, "CURSOR_INVALID")
        offset = token["position"] if token else 0
        if type(offset) is not int or offset < 0:
            raise BacktestError(400, "CURSOR_INVALID")
        if saved is not None and offset + limit <= len(saved["overlaps"]):
            result = dict(saved, overlaps=saved["overlaps"][offset : offset + limit])
        elif saved is not None and offset >= saved["overlapCount"]:
            result = dict(saved, overlaps=[])
        else:
            result = project_history(
                connection,
                definition,
                mark=mark,
                registry_hash=digest,
                bound_at=at,
                candidate_counts=counts[0] if counts else None,
                reference_counts=counts[1] if counts else None,
                phase_bar_starts=starts,
                seed_bar_starts=seeds,
                offset=offset,
                limit=limit,
            )
        if counts:
            result.update(candidateCounts=counts[0], referenceCounts=counts[1])
        end = offset + len(result["overlaps"])
        result["nextOverlapCursor"] = (
            self._next(binding, snapshot, end) if end < result["overlapCount"] else None
        )
        return result

    def _definition_wire(self, definition, query, *, scope, force_draft=False, connection=None):
        if connection is None:
            with self.storage.transaction() as connection:
                return self._definition_wire(
                    definition, query, scope=scope, force_draft=force_draft, connection=connection
                )
        identifier = definition["definition_id"]
        selected = query.get("validationId")
        if not force_draft:
            validation = (
                connection.execute(
                    text("SELECT * FROM backtest.experiment_validations WHERE definition_id=:id"),
                    {"id": identifier},
                )
                .mappings()
                .one_or_none()
            )
        else:
            validation = None
        matches_approval = validation is not None and (
            selected is None or str(validation["validation_id"]) == selected
        )
        valid = matches_approval
        if (
            matches_approval
            and not {"validatedAt", "reasons", "researchHistory", "registryWatermark"}
            <= validation["body"].keys()
        ):
            # An approval produced by 0035 may have only its admission
            # estimate. Reuse the first complete projection recorded by 0036.
            validation = (
                connection.execute(
                    text("""
                    SELECT body FROM backtest.experiment_validation_requests
                    WHERE definition_id=:definition AND approval_id=:approval AND valid
                    ORDER BY validated_at,key_hash COLLATE "C" LIMIT 1
                    """),
                    {"definition": identifier, "approval": validation["validation_id"]},
                )
                .mappings()
                .one_or_none()
            )
            valid = validation is not None
        if selected is not None and not matches_approval:
            # Query this through its existing narrow read port without holding
            # the size-one connection pool while opening another transaction.
            validation = self.storage._validation_request(
                connection, identifier, invalid_id=identity(selected)
            )
            if validation is None:
                raise BacktestError(404, "NOT_FOUND")
        body = definition["body"]
        saved = validation["body"] if validation else {}
        history = saved.get("researchHistory")
        receipt = (
            dict(history_bound(connection, at=definition["created_at"])) if force_draft else None
        )
        if history is not None:
            receipt = dict(
                mark=saved["registryWatermark"],
                registry_hash=history["registryHash"],
                bound_at=history["boundAt"],
            )
        return dict(
            schemaVersion=SCHEMA_VERSION,
            id=str(identifier),
            definitionVersion=definition["definition_version"],
            definitionHash=definition["fingerprint"],
            createdAt=utc_string(definition["created_at"]),
            state="VALIDATED" if valid else "DRAFT",
            definition=body,
            strategySelectionPolicyHash=definition["policy_hash"],
            candidateCount=definition["candidate_count"],
            fittingStatus=FITTING_STATUS,
            researchHistory=self._history(
                connection,
                definition,
                query,
                scope,
                ("definition", str(identifier)),
                receipt=receipt,
                saved=history,
                starts=_decode_schedule(saved.get("historyPhaseRuns", ())),
            ),
            limitations=limitations(body["researchMode"]),
            selectorVersion=SELECTION_VERSION,
            selectorFingerprint=SELECTOR_FINGERPRINT,
        )

    def get_experiment(self, id, query, *, scope):
        identifier = identity(id)
        with self.storage.transaction() as connection:
            definition = self.storage._definition(connection, identifier)
            return self._definition_wire(definition, query, scope=scope, connection=connection)

    def _dated_rows(
        self, connection, table, column, identifier_column, kind, scope, query, **filters
    ):
        binding, limit, token = self._cursor(
            kind, scope, query, **{k: str(v) for k, v in filters.items()}
        )
        snapshot = (
            token["snapshot"]
            if token
            else utc_string(connection.execute(text("SELECT clock_timestamp()")).scalar_one())
        )
        clauses = [f"{column}<=:snapshot"] + [f"{key}=:{key}" for key in filters]
        params = {**filters, "snapshot": stamp(snapshot), "limit": limit + 1}
        if token:
            previous_at, previous_id = token["position"]
            clauses.append(f"({column},{identifier_column})<(:previous_at,:previous_id)")
            params.update(previous_at=stamp(previous_at), previous_id=identity(previous_id))
        rows = (
            connection.execute(
                text(
                    f"SELECT * FROM {table} WHERE "
                    + " AND ".join(clauses)
                    + f" ORDER BY {column} DESC,{identifier_column} DESC LIMIT :limit"
                ),
                params,
            )
            .mappings()
            .all()
        )
        cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            cursor = self._next(
                binding, snapshot, [utc_string(last[column]), str(last[identifier_column])]
            )
        return rows[:limit], cursor

    def list_experiments(self, query, *, scope):
        with self.storage.transaction() as connection:
            rows, cursor = self._dated_rows(
                connection,
                "backtest.experiment_definitions",
                "created_at",
                "definition_id",
                "definitions",
                scope,
                query,
            )
            return dict(
                schemaVersion=SCHEMA_VERSION,
                items=[
                    self._definition_wire(row, {}, scope=scope, connection=connection)
                    for row in rows
                ],
                nextCursor=cursor,
            )

    @staticmethod
    def _batch_row(connection, identifier):
        return _one(
            connection,
            """
            SELECT b.*,s.body AS snapshot_body,s.fingerprint AS manifest_hash,
                   (s.canonical_text::jsonb)->1->'phase_bindings' AS snapshot_phases,
                   v.body->'historyPhaseRuns' AS validation_schedule,
                   r.body AS report_body,r.fingerprint AS physical_report_hash
            FROM backtest.experiment_batches b
            JOIN backtest.experiment_validations v USING(validation_id)
            LEFT JOIN backtest.experiment_snapshots s USING(batch_id)
            LEFT JOIN backtest.experiment_reports r USING(batch_id)
            WHERE b.batch_id=:id
        """,
            {"id": identifier},
        )

    @staticmethod
    def _candidate_rows(connection, batch):
        return (
            connection.execute(
                text("""
            SELECT c.candidate_id,c.role,c.body,q.implementation_fingerprint,
                   coalesce(array_agg(l.status ORDER BY l.phase_key)
                     FILTER(WHERE l.phase_key IS NOT NULL),'{}') AS states,
                   max(l.body->>'errorCode') FILTER (WHERE l.status IN ('FAILED','BLOCKED'))
                     AS error_code,
                   coalesce(bool_or(l.status='COMPLETE'
                     AND (l.phase_key='holdout' OR split_part(l.phase_key,chr(58),2)='test')),
                     false) AS test_complete
            FROM backtest.experiment_candidates c
            JOIN backtest.quant_strategy_revisions q ON q.strategy_id=c.body->>'strategyId'
               AND q.version=c.body->>'version' AND q.execution_available
            LEFT JOIN backtest.experiment_result_links l ON l.batch_id=:batch
               AND l.candidate_id=c.candidate_id AND l.role=c.role
            WHERE c.definition_id=:definition
            GROUP BY c.candidate_id,c.role,c.body,q.implementation_fingerprint
            ORDER BY c.role COLLATE "C",c.candidate_id COLLATE "C"
        """),
                dict(batch=batch["batch_id"], definition=batch["definition_id"]),
            )
            .mappings()
            .all()
        )

    @staticmethod
    def _candidate_state(batch, row):
        states = row["states"]
        if "FAILED" in states:
            return "FAILED"
        if "BLOCKED" in states:
            return "BLOCKED"
        expected = len(batch.get("snapshot_phases") or ())
        if batch["status"] != "SUCCEEDED" and not (expected and len(states) == expected):
            return "RUNNING" if batch["status"] == "RUNNING" and states else "PENDING"
        if row["test_complete"]:
            return "SUCCEEDED"
        if "NOT_EVALUATED_BY_PROTOCOL" in states:
            return "NOT_EVALUATED_BY_PROTOCOL"
        return "NOT_EVALUATED_NO_SELECTION"

    @classmethod
    def _counts(cls, batch, rows):
        search, references = empty_counts(0), empty_counts(0)
        categories = {
            "SUCCEEDED": "complete",
            "FAILED": "failed",
            "BLOCKED": "blocked",
            "NOT_EVALUATED_BY_PROTOCOL": "notEvaluated",
            "NOT_EVALUATED_NO_SELECTION": "notEvaluated",
        }
        for row in rows:
            counts = search if row["role"] == "SEARCH" else references
            counts["planned"] += 1
            if row["states"]:
                counts["started"] += 1
            state = cls._candidate_state(batch, row)
            if batch["status"] == "SUCCEEDED" and "COMPLETE" in row["states"]:
                # A search may complete all required work even when its OOS
                # phase is explicitly not evaluated. Phase state stays separate.
                state = "SUCCEEDED"
            if state in categories:
                counts[categories[state]] += 1
        return search, references

    @staticmethod
    def _snapshot_starts(batch):
        phases = batch.get("snapshot_phases") or []
        if not phases:
            return _decode_schedule(batch.get("validation_schedule") or ()), {}
        return (
            {
                (row["phase"], row["fold_index"]): tuple(map(stamp, row["expected_bar_starts"]))
                for row in phases
            },
            {
                (row["phase"], row["fold_index"]): tuple(map(stamp, row["expected_seed_starts"]))
                for row in phases
            },
        )

    @staticmethod
    def _receipt(batch):
        return dict(
            mark=batch["registry_watermark"],
            registry_hash=batch["registry_hash"],
            bound_at=batch["accepted_at"],
        )

    @staticmethod
    def _warnings(connection, definition, batch):
        current = []
        if batch.get("snapshot_body") is not None:
            gate = connection.execute(
                text("SELECT backtest.current_quant_gate(:batch)"), dict(batch=batch["batch_id"])
            ).scalar_one()
            if gate is not None:
                now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
                current.append(
                    dict(
                        observedAt=utc_string(now),
                        code="CURRENT_RELIABILITY_BLOCK",
                        relatedBatchId=None,
                    )
                )
        later = connection.execute(
            text("""
            SELECT e.source_kind,e.source_id,min(e.observed_at) AS first_seen
            FROM backtest.test_exposures e
            WHERE e.exposure_id>:watermark AND e.instrument_id=CAST(:instrument AS uuid)
              AND e.feed='iex' AND e.interval='15Min' AND e.adjustment='raw'
              AND NOT (e.source_kind='BATCH' AND e.source_id=:batch)
              AND NOT EXISTS (SELECT 1 FROM backtest.test_exposures prior
                WHERE prior.exposure_id<=:watermark
                AND prior.source_kind=e.source_kind AND prior.source_id=e.source_id)
              AND EXISTS (SELECT 1 FROM backtest.test_exposures own
                WHERE own.source_kind='BATCH' AND own.source_id=:batch
                AND own.knowledge_status='REQUESTED' AND own.status='ACCEPTED'
                AND e.start_at<own.end_at AND own.start_at<e.end_at)
            GROUP BY e.source_kind,e.source_id
            ORDER BY first_seen,e.source_kind,e.source_id LIMIT 31
        """),
            dict(
                batch=batch["batch_id"],
                watermark=batch["registry_watermark"],
                instrument=definition["body"]["instrumentId"],
            ),
        ).mappings()
        current.extend(
            dict(
                observedAt=utc_string(row["first_seen"]),
                code="LATER_REGISTERED_OVERLAP",
                relatedBatchId=str(row["source_id"]) if row["source_kind"] == "BATCH" else None,
            )
            for row in later
        )
        return current

    def _batch_wire(self, connection, batch, query, scope):
        definition = self.storage._definition(connection, batch["definition_id"])
        counts = self._counts(batch, self._candidate_rows(connection, batch))
        body = definition["body"]
        snapshot = batch.get("snapshot_body") or {}
        comparison = (batch.get("report_body") or {}).get("comparison")
        starts, seeds = self._snapshot_starts(batch)
        history = self._history(
            connection,
            definition,
            query,
            scope,
            ("batch", str(batch["batch_id"])),
            receipt=self._receipt(batch),
            counts=counts,
            starts=starts,
            seeds=seeds,
            saved=comparison["researchHistory"] if comparison else None,
        )
        return dict(
            schemaVersion=SCHEMA_VERSION,
            id=str(batch["batch_id"]),
            definitionId=str(batch["definition_id"]),
            definitionVersion=definition["definition_version"],
            definitionHash=definition["fingerprint"],
            state=batch["status"],
            createdAt=utc_string(batch["accepted_at"]),
            updatedAt=utc_string(batch["updated_at"]),
            supersedesBatchId=body["supersedesBatchId"],
            strategySelectionPolicy=body["strategySelectionPolicy"],
            strategySelectionPolicyHash=definition["policy_hash"],
            manifest=snapshot.get("manifestReference"),
            reportId=comparison["id"] if comparison else None,
            reportHash=comparison["reportHash"] if comparison else None,
            candidateCounts=counts[0],
            referenceCounts=counts[1],
            attemptCount=batch["attempts"],
            researchHistory=history,
            currentWarnings=self._warnings(connection, definition, batch),
            reasons=_reason(batch["status"], batch["error_code"]),
            limitations=limitations(body["researchMode"]),
        )

    def _batch_detail(self, identifier, query, *, scope):
        with self.storage.transaction() as connection:
            return self._batch_wire(
                connection, self._batch_row(connection, identifier), query, scope
            )

    def get_experiment_batch(self, id, query, *, scope):
        return self._batch_detail(identity(id), query, scope=scope)

    def list_experiment_batches(self, id, query, *, scope):
        identifier = identity(id)
        with self.storage.transaction() as connection:
            self.storage._definition(connection, identifier)
            rows, cursor = self._dated_rows(
                connection,
                "backtest.experiment_batches",
                "accepted_at",
                "batch_id",
                "batches",
                scope,
                query,
                definition_id=identifier,
            )
            return dict(
                schemaVersion=SCHEMA_VERSION,
                items=[
                    self._batch_wire(
                        connection, self._batch_row(connection, row["batch_id"]), {}, scope
                    )
                    for row in rows
                ],
                nextCursor=cursor,
            )

    def _page(self, query, scope, kind, rows, *, snapshot, **context):
        binding, limit, token = self._cursor(kind, scope, query, **context)
        if token and token["snapshot"] != snapshot:
            raise BacktestError(400, "CURSOR_INVALID")
        offset = token["position"] if token else 0
        if type(offset) is not int or offset < 0:
            raise BacktestError(400, "CURSOR_INVALID")
        end = offset + limit
        return dict(
            schemaVersion=SCHEMA_VERSION,
            items=rows[offset:end],
            nextCursor=self._next(binding, snapshot, end) if end < len(rows) else None,
        )

    def list_candidates(self, id, query, *, scope):
        with self.storage.transaction() as connection:
            batch = self._batch_row(connection, identity(id))
            rows = []
            for row in self._candidate_rows(connection, batch):
                state = self._candidate_state(batch, row)
                reasons = (
                    [state]
                    if state.startswith("NOT_EVALUATED")
                    else _reason(state, row["error_code"])
                    if state in ("FAILED", "BLOCKED")
                    else []
                )
                rows.append(
                    dict(
                        schemaVersion=SCHEMA_VERSION,
                        candidateId=row["candidate_id"],
                        role=PUBLIC_ROLES[row["role"]],
                        configuration=row["body"],
                        implementationFingerprint=row["implementation_fingerprint"],
                        state=state,
                        fittingStatus=FITTING_STATUS,
                        reasons=reasons,
                    )
                )
            return self._page(
                query, scope, "candidates", rows, snapshot=utc_string(batch["updated_at"]), batch=id
            )

    def list_results(self, id, query, *, scope):
        with self.storage.transaction() as connection:
            batch = self._batch_row(connection, identity(id))
            definition = self.storage._definition(connection, batch["definition_id"])
            binding, limit, token = self._cursor("results", scope, query, batch=id)
            snapshot = utc_string(batch["updated_at"])
            if token and token["snapshot"] != snapshot:
                raise BacktestError(400, "CURSOR_INVALID")
            offset = token["position"] if token else 0
            if type(offset) is not int or offset < 0:
                raise BacktestError(400, "CURSOR_INVALID")
            params = dict(
                batch=batch["batch_id"],
                phase=query.get("phase"),
                fold=query.get("fold"),
                candidate=query.get("candidateId"),
                offset=offset,
                limit=limit + 1,
            )
            if params["phase"] == "holdout" and params["fold"] is not None:
                raise BacktestError(400, "INVALID_REQUEST")
            rows = (
                connection.execute(
                    text("""
                SELECT l.candidate_id,l.role,l.status,l.body,p.phase,p.fold_index,
                       r.body->'metrics' AS metrics,r.body->'selectionValues' AS selection_values,
                       r.body->'reliability' AS reliability
                FROM backtest.experiment_result_links l
                JOIN backtest.experiment_phase_bindings p USING(batch_id,phase_key)
                LEFT JOIN backtest.results r USING(run_id)
                WHERE l.batch_id=:batch AND (CAST(:phase AS text) IS NULL OR p.phase=:phase)
                  AND (CAST(:fold AS integer) IS NULL OR p.fold_index=:fold)
                  AND (CAST(:candidate AS text) IS NULL OR l.candidate_id=:candidate)
                ORDER BY p.fold_index NULLS LAST,
                         CASE p.phase WHEN 'training' THEN 0 WHEN 'validation' THEN 1 ELSE 2 END,
                         l.role COLLATE "C",l.candidate_id COLLATE "C"
                LIMIT :limit OFFSET :offset
            """),
                    params,
                )
                .mappings()
                .all()
            )
            items = [
                dict(
                    schemaVersion=SCHEMA_VERSION,
                    batchId=id,
                    candidateId=row["candidate_id"],
                    role=PUBLIC_ROLES[row["role"]],
                    phase=row["phase"],
                    researchRole=ROLES[row["phase"]],
                    fold=row["fold_index"],
                    fittingStatus=FITTING_STATUS,
                    evaluationStatus=row["status"],
                    metrics=row["metrics"],
                    selectionValues=row["selection_values"]
                    if row["phase"] == "validation" and row["status"] == "COMPLETE"
                    else None,
                    reliability=row["reliability"],
                    reasonCodes=row["body"].get("reasonCodes")
                    or (
                        [row["status"]]
                        if row["status"].startswith("NOT_EVALUATED")
                        else _reason(row["status"], row["body"].get("errorCode"))
                    ),
                    limitations=limitations(definition["body"]["researchMode"]),
                )
                for row in rows[:limit]
            ]
            cursor = self._next(binding, snapshot, offset + limit) if len(rows) > limit else None
            return dict(schemaVersion=SCHEMA_VERSION, items=items, nextCursor=cursor)

    @staticmethod
    def _comparison(batch):
        report = (batch.get("report_body") or {}).get("comparison")
        if report is None:
            raise BacktestError(409, "REPORT_NOT_READY")
        return report

    def get_comparison(self, id, query, *, scope):
        with self.storage.transaction() as connection:
            batch = self._batch_row(connection, identity(id))
            report = self._comparison(batch)
            definition = self.storage._definition(connection, batch["definition_id"])
            starts, seeds = self._snapshot_starts(batch)
            history = self._history(
                connection,
                definition,
                query,
                scope,
                ("comparison", id, report["reportHash"]),
                receipt=self._receipt(batch),
                saved=report["researchHistory"],
                starts=starts,
                seeds=seeds,
                counts=(
                    report["researchHistory"]["candidateCounts"],
                    report["researchHistory"]["referenceCounts"],
                ),
            )
            return dict(
                report,
                researchHistory=history,
                currentWarnings=self._warnings(connection, definition, batch),
            )

    def _curve(self, id, candidate_id, query, scope, kind):
        phase, fold = query.get("phase"), query.get("fold")
        phase_key = _phase_key(phase, fold)
        with self.storage.transaction() as connection:
            batch = self._batch_row(connection, identity(id))
            report = self._comparison(batch)
            requested_role = query.get("role")
            if requested_role is not None and requested_role not in STORAGE_ROLES:
                raise BacktestError(400, "INVALID_REQUEST")
            candidate = _one(
                connection,
                """SELECT role FROM backtest.experiment_candidates
                WHERE definition_id=:definition AND candidate_id=:candidate
                  AND (CAST(:role AS text) IS NULL OR role=:role)
                ORDER BY (role='SEARCH') DESC,role COLLATE "C" LIMIT 1""",
                dict(
                    definition=batch["definition_id"],
                    candidate=candidate_id,
                    role=STORAGE_ROLES.get(requested_role),
                ),
            )
            role = candidate["role"]
            binding, limit, token = self._cursor(
                kind,
                scope,
                query,
                batch=id,
                candidate=candidate_id,
                role=role,
                report=report["reportHash"],
            )
            offset = token["position"] if token else 0
            if type(offset) is not int or offset < 0:
                raise BacktestError(400, "CURSOR_INVALID")
            # Resolve the configuration role before inspecting the result so a
            # non-evaluated search cannot resolve to the same-ID reference.
            row = _one(
                connection,
                f"""
                SELECT jsonb_array_length(r.body->'{kind}') AS total,
                  coalesce((SELECT jsonb_agg(value ORDER BY ordinal)
                    FROM jsonb_array_elements(r.body->'{kind}') WITH ORDINALITY item(value,ordinal)
                    WHERE ordinal>:offset AND ordinal<=:end),'[]'::jsonb) AS items
                FROM backtest.experiment_result_links l JOIN backtest.results r USING(run_id)
                WHERE l.batch_id=:batch AND l.candidate_id=:candidate AND l.phase_key=:phase
                  AND l.role=:role AND l.status='COMPLETE'
            """,
                dict(
                    batch=batch["batch_id"],
                    candidate=candidate_id,
                    phase=phase_key,
                    role=role,
                    offset=offset,
                    end=offset + limit,
                ),
            )
            end = offset + len(row["items"])
            cursor = self._next(binding, report["reportHash"], end) if end < row["total"] else None
            return dict(
                schemaVersion=SCHEMA_VERSION,
                batchId=id,
                candidateId=candidate_id,
                role=PUBLIC_ROLES[role],
                reportHash=report["reportHash"],
                phase=phase,
                researchRole=ROLES[phase],
                fold=fold,
                items=row["items"],
                nextCursor=cursor,
                sampling="EXACT_BAR_CLOSE",
                totalPointCount=row["total"],
                limitations=report["limitations"],
            )

    def get_equity(self, id, candidate_id, query, *, scope):
        return self._curve(id, candidate_id, query, scope, "equity")

    def get_drawdown(self, id, candidate_id, query, *, scope):
        return self._curve(id, candidate_id, query, scope, "drawdown")

    def compare_experiments(self, query, *, scope):
        ids = tuple(map(identity, query["batchId"]))
        if not scope or not 1 <= len(ids) <= 4 or len(ids) != len(set(ids)):
            raise BacktestError(400, "INVALID_REQUEST")
        batches = []
        with self.storage.transaction() as connection:
            for identifier in ids:
                batch = self._batch_row(connection, identifier)
                report = (batch["report_body"] or {}).get("comparison")
                snapshot = batch["snapshot_body"] or {}
                saved = snapshot.get("comparisonDimensions")
                if report is not None and report["status"] == "COMPLETE" and saved is not None:
                    binding = ComparisonBinding(tuple((key, value) for key, value in saved))
                    if binding.fingerprint != report["comparisonFingerprint"]:
                        raise BacktestError(503, "UNAVAILABLE")
                    batches.append(
                        BatchComparisonReference(
                            identifier, identity(report["id"]), binding, report["status"]
                        )
                    )
        # Incomplete data does not establish differences between research
        # bindings. Preserve request order and make this reason explicit.
        completed = {row.batch_id for row in batches}
        result = compare_batches(
            tuple(batches)
            or (BatchComparisonReference(ids[0], None, ComparisonBinding(()), "INCOMPLETE"),)
        )
        result["batchIds"] = list(map(str, ids))
        result["incomparabilities"].extend(
            dict(leftBatchId=str(left), rightBatchId=str(right), reasons=["INCOMPLETE_RESULTS"])
            for left, right in combinations(ids, 2)
            if {left, right} - completed
        )
        return result

    def get_reliability_report(self, id, report_id, query, *, scope):
        batch_id, identifier = identity(id), identity(report_id)
        section = query.get("section", "FINDINGS")
        binding, limit, token = self._cursor(
            "reliability", scope, query, batch=id, report=report_id, section=section
        )
        offset = token["position"] if token else 0
        if type(offset) is not int or offset < 0:
            raise BacktestError(400, "CURSOR_INVALID")
        result = self.storage.reliability_report(
            batch_id, identifier, section=section, offset=offset, limit=limit
        )
        report, items = result["report"], result["items"]
        if token and token["snapshot"] != report["reportHash"]:
            raise BacktestError(400, "CURSOR_INVALID")
        end = offset + len(items)
        finding = section == "FINDINGS"
        total = report["findingsCount" if finding else "revisionReferenceCount"]
        cursor = self._next(binding, report["reportHash"], end) if end < total else None
        with self.storage.transaction() as connection:
            batch = self._batch_row(connection, batch_id)
            definition = self.storage._definition(connection, batch["definition_id"])
            warnings = self._warnings(connection, definition, batch)
        return dict(
            report=report,
            section=section,
            findings=items if finding else [],
            revisionReferences=[] if finding else items,
            nextCursor=cursor,
            currentWarnings=warnings,
        )
