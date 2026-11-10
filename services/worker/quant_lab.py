"""One optional dispatcher for bound Quant Lab simulation batches."""

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, localcontext
from time import monotonic
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from packages.backtesting.comparison import (
    BoundPhaseAnalytics,
    ComparisonBinding,
    build_comparison_report,
    compare_phase,
)
from packages.backtesting.experiment import (
    EXPERIMENT_VERSION,
    PHASE_ROLES,
    REFERENCE_CONFIGURATIONS,
    SCHEMA_VERSION,
    SELECTION_VERSION,
    SELECTOR_FINGERPRINT,
)
from packages.backtesting.experiment_metrics import CONTEXT, Metric, PhaseAnalytics, analyze_phase
from packages.backtesting.experiment_protocol import (
    SelectionReliability,
    SelectionValues,
    ValidationSelectionInput,
    freeze_selection_plan,
    plan_analysis_validation,
)
from packages.backtesting.experiment_storage import ExperimentStorage, definition_domain
from packages.backtesting.jobs import BacktestError
from packages.backtesting.models import BacktestBlocked, BacktestBudgetExceeded
from packages.backtesting.simulator import QuantValidationContext, simulate_quant
from packages.domain.models import decimal_string, utc_string
from packages.research.evidence_storage import EvidenceBudgetExceeded
from packages.research.models import _canonical, content_fingerprint
from services.worker.quant_lab_data import load_quant_inputs


def _key(fold, phase):
    return "holdout" if fold is None else f"{fold}:{phase}"


class _CandidateFailure(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def _period(start, end):
    return dict(start=utc_string(start), end=utc_string(end))


def _phase_wire(phase):
    binding, data = phase.binding, phase.dataset
    seed = data.warmup
    return dict(
        schemaVersion=SCHEMA_VERSION,
        phase=binding.phase,
        researchRole=dict(PHASE_ROLES)[binding.phase],
        fold=binding.fold_index,
        interval=_period(binding.window.start, binding.window.end),
        seedInterval=_period(seed[0].identity.bar_start, seed[-1].identity.bar_end)
        if seed
        else None,
        seedBarCount=len(seed),
        fittingStatus="NOT_TRAINABLE",
    )


def _reliability(inputs):
    strict = inputs.manifest.definition.research_mode == "STRICT_PIT"

    def dim(rating, *reasons):
        return dict(rating=rating, reasonCodes=list(reasons))

    license_ok = inputs.license_status in ("CONFIRMED", "DECLARED_CONFIRMED")
    return dict(
        overallStatus="EXPLORATORY_WITH_LIMITATIONS",
        evidenceStatus="STRICT_PIT_EVIDENCE_COMPLETE" if strict else "EXPLORATORY_WITH_LIMITATIONS",
        rating="PASS" if strict else "LIMITED",
        dimensions=dict(
            INTEGRITY=dim("PASS"),
            CALENDAR=dim("PASS"),
            CORPORATE_ACTIONS=dim("PASS")
            if strict
            else dim("UNKNOWN", "CORPORATE_ACTIONS_UNKNOWN"),
            HISTORICAL_AVAILABILITY=dim("PASS")
            if strict
            else dim("LIMITED", "HISTORICAL_AVAILABILITY_NOT_PROVEN"),
            FEED=dim("LIMITED", "IEX_PARTIAL_MARKET"),
            LICENSE=dim("PASS") if license_ok else dim("UNKNOWN", "LICENSE_UNKNOWN"),
        ),
        historicalAvailabilityStatus="VERIFIED_FOR_DECISION_CUTOFFS" if strict else "NOT_PROVEN",
        resultLimitations=[
            "PRICE_ONLY",
            "DIVIDENDS_EXCLUDED",
            "IEX_PARTIAL_MARKET",
            "SURVIVORSHIP_BIAS",
            "NO_TRADING_AUTHORIZATION",
            *(() if strict else ("NOT_STRICT_PIT", "EX_POST_REVISIONS")),
        ],
    )


def _check_approved_budget(budget, lease, definition, receipt):
    counts = ("candidateCount", "reservedQueueSlots", "snapshotRows", "simulationBarSteps",
              "publicationBytes")
    if (
        budget.get("calendarConfirmed") is not True or budget.get("withinLimits") is not True
        or any(type(budget.get(key)) is not int or budget[key] <= 0 for key in counts)
        or budget["candidateCount"] != len(definition.candidates)
        or budget["reservedQueueSlots"] != definition.queue_slots
        or budget["reservedQueueSlots"] != lease.row["slots"]
        or lease.row["bar_steps"] > budget["simulationBarSteps"]
        or lease.row["publication_bytes"] > budget["publicationBytes"]
    ):
        raise BacktestBudgetExceeded("approved quant budget unavailable or exhausted")
    license_status = receipt["license_body"]["status"]
    if license_status in ("DENIED", "BLOCKED"):
        raise BacktestBlocked("LICENSE_DENIED")
    if license_status not in ("CONFIRMED", "DECLARED_CONFIRMED"):
        raise BacktestBlocked("LICENSE_UNKNOWN")


def _check_approved_inputs(budget, definition, inputs, receipt):
    steps = sum(
        (len(phase.dataset.steps) + len(phase.dataset.warmup))
        * (definition.queue_slots if phase.binding.phase in ("training", "validation")
           else len(REFERENCE_CONFIGURATIONS) + 1)
        for phase in inputs.phases
    )
    # Admission bounds the complete read set. These immutable bindings provide an
    # independent lower bound without counting repeated projections of a source.
    rows = len(inputs.references) + len(receipt["trust_body"]["decisions"]) + 1
    if steps > budget["simulationBarSteps"] or rows > budget["snapshotRows"]:
        raise BacktestBudgetExceeded("materialized quant inputs exceed approved budget")


def _findings(reliability, phase):
    b = phase.binding
    period = _period(b.window.start, b.window.end)
    return tuple(
        dict(
            findingId=str(
                uuid5(
                    NAMESPACE_URL,
                    "quant-reliability-finding-v1/"
                    + content_fingerprint((b.fold_index, b.phase, period, category, code)),
                )
            ),
            category=category,
            code=code,
            severity="LIMITATION",
            foldIndex=b.fold_index,
            period=period,
            references=[],
        )
        for category, dimension in reliability["dimensions"].items()
        for code in dimension["reasonCodes"]
    )


def _curves(result, checkpoint):
    equity, drawdown = [], []
    high = result.start_cash
    with localcontext(CONTEXT):
        for point in result.equity:
            checkpoint()
            at = utc_string(point.at)
            high = max(high, point.equity)
            drawdown.append(
                dict(
                    at=at,
                    drawdown=format(
                        ((high - point.equity) / high).quantize(Decimal("0.000000000001")), "f"
                    ),
                )
            )
            equity.append(
                dict(
                    at=at,
                    equity=format(point.equity, ".8f"),
                    cash=format(point.cash, ".8f"),
                    close=decimal_string(point.close),
                    quantity=decimal_string(point.quantity),
                )
            )
    return equity, drawdown


def _analytics_state(analytics):
    return dict(
        valuationMethod=analytics.valuation_method,
        dailyMean=str(analytics.daily_mean) if analytics.daily_mean is not None else None,
        exactMetrics={
            name: str(metric.value)
            for name, metric in analytics.metrics.items()
            if isinstance(metric.value, Decimal)
        },
    )


def _restore_analytics(body, definition, candidate, phase, calendar):
    wire, state = body["metrics"], body["analyticsInputs"]
    raw = {**wire, **wire["activity"], **wire["exposure"]}
    exact = state["exactMetrics"]
    values = body["selectionValues"]
    return PhaseAnalytics(
        candidate.strategy_id,
        state["valuationMethod"],
        phase.binding.window,
        calendar,
        definition.start_cash,
        wire["expectedBarCount"],
        wire["observedBarCount"],
        wire["completeValidationSessions"],
        {
            name: Metric(
                Decimal(exact[name]) if name in exact else metric["value"],
                metric["observationCount"],
                metric["frequency"],
                metric["status"],
                metric["reasonCode"],
            )
            for name, metric in raw.items()
            if isinstance(metric, dict) and "frequency" in metric
        },
        SelectionValues(
            *(
                Decimal(values[k])
                for k in ("netReturn", "maxDrawdown", "turnover", "investedBarRatio")
            ),
            values["entryCount"],
            values["completeValidationSessions"],
        )
        if values
        else None,
        Decimal(state["dailyMean"]) if state["dailyMean"] is not None else None,
    )


class QuantLabWorker:
    def __init__(self, engine, *, owner, code_version, timer=monotonic):
        self.storage = ExperimentStorage(engine)
        self.owner, self.code_version, self.timer = owner, code_version, timer

    def tick(self):
        lease = self.storage.claim(self.owner)
        return None if lease is None else self.process(lease)

    def _gate(self, lease):
        with self.storage.transaction(lease) as connection:
            return connection.execute(
                text("SELECT backtest.current_quant_gate(:batch)"), {"batch": lease.batch_id}
            ).scalar_one()

    def _bind(self, lease, row, inputs):
        manifest = inputs.manifest
        definition = manifest.definition
        binding = ComparisonBinding.from_manifest(manifest)
        common = dict(
            definitionHash=row["fingerprint"], strategySelectionPolicyHash=row["policy_hash"]
        )
        snap = dict(
            snapshotVersion=manifest.snapshot_version, snapshotAt=utc_string(manifest.snapshot_at)
        )
        projection = dict(
            schemaVersion=SCHEMA_VERSION,
            manifestVersion=EXPERIMENT_VERSION,
            manifestHash=manifest.manifest_hash,
            snapshotHash=manifest.manifest_hash,
            comparisonFingerprint=binding.fingerprint,
            strategySelectionPolicyHash=row["policy_hash"],
            researchProtocolHash=definition.protocol.protocol_hash,
            implementationFingerprint=content_fingerprint(
                tuple(
                    c.implementation_fingerprint
                    for c in definition.candidates + REFERENCE_CONFIGURATIONS
                )
            ),
            buildSha=self.code_version,
            metricsVersion=manifest.metrics_version,
            simulatorVersion=manifest.simulator_version,
            commonWarmupBars=definition.warmup_count,
            phaseBindings=[_phase_wire(p) for p in inputs.phases],
            snapshotReference=snap,
            selectorVersion=SELECTION_VERSION,
            selectorFingerprint=SELECTOR_FINGERPRINT,
        )
        body = dict(
            common,
            **snap,
            manifestVersion=EXPERIMENT_VERSION,
            strategySelectionPolicy=row["body"]["strategySelectionPolicy"],
            protocol=row["body"]["protocol"],
            registryHash=lease.row["registry_hash"],
            manifestReference=projection,
            comparisonDimensions=_canonical(binding.dimensions),
        )
        phases = []
        for p in inputs.phases:
            b, d = p.binding, p.dataset
            seed = [bar.revision_id for bar in d.warmup]
            phases.append(
                dict(
                    phaseKey=_key(b.fold_index, b.phase),
                    phase=b.phase,
                    fold=b.fold_index,
                    start=b.window.start,
                    end=b.window.end,
                    seedRevisionIds=seed,
                    body=dict(
                        common,
                        seedRevisionIds=list(map(str, seed)),
                        outcomeRevisionIds=[str(bar.revision_id) for bar in d.outcome_bars],
                    ),
                )
            )
        self.storage.bind_snapshot(lease, body, tuple(inputs.references), phases, manifest=manifest)
        return common

    def _saved(self, lease, phase_key):
        with self.storage.transaction(lease) as connection:
            rows = connection.execute(
                text(
                    "SELECT l.candidate_id,l.role,l.status,l.fingerprint,"
                    "l.body->>'errorCode' AS error_code,"
                    "CASE WHEN r.run_id IS NOT NULL THEN jsonb_build_object("
                    "'metrics',r.body->'metrics','analyticsInputs',r.body->'analyticsInputs',"
                    "'selectionValues',r.body->'selectionValues') END AS result FROM "
                    "backtest.experiment_result_links l LEFT JOIN backtest.results r USING(run_id) "
                    "WHERE l.batch_id=:id AND l.phase_key=:phase"
                ),
                dict(id=lease.batch_id, phase=phase_key),
            ).mappings()
            return {(r["candidate_id"], r["role"]): dict(r) for r in rows}

    @contextmanager
    def _candidate_attempt(self, lease, common, **slot):
        try:
            yield
            return
        except BacktestBlocked as error:
            failure = _CandidateFailure("BLOCKED", error.code)
        except (BacktestBudgetExceeded, EvidenceBudgetExceeded):
            failure = _CandidateFailure("FAILED", "BUDGET_EXHAUSTED")
        except BacktestError as error:
            if error.code == "LEASE_LOST":
                raise
            if error.code == "BUDGET_EXHAUSTED":
                failure = _CandidateFailure("FAILED", error.code)
            else:
                gate = self._gate(lease)
                failure = (
                    _CandidateFailure("BLOCKED", gate)
                    if gate
                    else _CandidateFailure("FAILED", "INTERNAL_FAILURE")
                )
        except Exception:
            failure = _CandidateFailure("FAILED", "INTERNAL_FAILURE")
        try:
            self.storage.record_result(
                lease, dict(common, errorCode=failure.code), status=failure.status, **slot
            )
        except BacktestError as error:
            if error.code == "LEASE_LOST":
                raise
        raise failure

    def _run_phase(
        self,
        lease,
        context,
        inputs,
        planned,
        common,
        checkpoint,
        *,
        validation_rows=None,
        selection=None,
    ):
        phase_key = _key(planned.fold_index, planned.phase)
        phase, calendar = next(
            (p, c)
            for p, c in zip(inputs.phases, inputs.calendars, strict=True)
            if _key(p.binding.fold_index, p.binding.phase) == phase_key
        )
        definition = context.manifest.definition
        configs = {c.candidate_id: c for c in definition.candidates}
        saved = self._saved(lease, phase_key)
        result_rows = {}
        bar_ids = {str(b.revision_id) for b in phase.dataset.outcome_bars}
        bar_ids.update(str(b.revision_id) for step in phase.dataset.steps for b in step.input_bars)
        references = tuple(
            ref
            for _, ref in sorted(inputs.references.items())
            if ref["referenceId"] in bar_ids or ref["kind"] != "BAR"
        )
        reliability = _reliability(inputs)
        findings = _findings(reliability, phase)
        rows = (
            *zip(REFERENCE_CONFIGURATIONS, ("FLAT_REFERENCE", "BUY_HOLD_REFERENCE"), strict=True),
            *((configs[key], "SEARCH") for key in planned.candidate_ids),
        )
        for candidate, role in rows:
            checkpoint()
            candidate_id = candidate.candidate_id
            prior = saved.get((candidate_id, role))
            if prior is not None:
                if prior["status"] in ("FAILED", "BLOCKED"):
                    raise _CandidateFailure(
                        prior["status"], prior["error_code"] or "INTERNAL_FAILURE"
                    )
                if prior["status"] != "COMPLETE" or prior["result"] is None:
                    raise BacktestBlocked("INCOMPLETE_RESULT")
                analytics = _restore_analytics(
                    prior["result"], definition, candidate, phase, calendar
                )
                link_hash = prior["fingerprint"]
            else:
                gate = self._gate(lease)
                if gate:
                    raise BacktestBlocked(gate)
                self.storage.checkpoint(
                    lease, additional_bar_steps=len(phase.dataset.steps) + len(phase.dataset.warmup)
                )
                with self._candidate_attempt(
                    lease, common, candidate_id=candidate_id, role=role, phase_key=phase_key
                ):
                    result = simulate_quant(
                        definition,
                        phase,
                        candidate,
                        checkpoint=checkpoint,
                        validation_context=context,
                    )
                    checkpoint()
                    analytics = analyze_phase(result, calendar, phase.binding.window)
                    checkpoint()
                    if analytics.selection_values is None:
                        raise BacktestBlocked("INCOMPLETE_RESULT")
                    raw = analytics
                    bound = BoundPhaseAnalytics(
                        context.manifest, planned.fold_index, planned.phase, candidate, raw
                    )
                    analytics = compare_phase(
                        bound,
                        flat=result_rows.get("FLAT_REFERENCE"),
                        buy_hold=result_rows.get("BUY_HOLD_REFERENCE"),
                        paired_validation=(validation_rows or {}).get(
                            (planned.fold_index, candidate_id)
                        ),
                        selection=selection,
                    )
                    equity, drawdown = _curves(result, checkpoint)
                    request = dict(common, candidateId=candidate_id, role=role, phaseKey=phase_key)
                    child = dict(
                        request, batchId=str(lease.batch_id), snapshotHash=context.manifest_hash
                    )
                    link_hash = self.storage.publish_child(
                        lease,
                        request,
                        child,
                        dict(
                            child,
                            metrics=analytics.to_wire(),
                            analyticsInputs=_analytics_state(analytics),
                            selectionValues=raw.selection_values.to_wire(),
                            equity=equity,
                            drawdown=drawdown,
                        ),
                        link=dict(dataComplete=True, requiredMetricsValid=True),
                        reliability=reliability,
                        findings=findings,
                        revision_references=references,
                    )["link_hash"]
            bound = BoundPhaseAnalytics(
                context.manifest,
                planned.fold_index,
                planned.phase,
                candidate,
                analytics,
                validation_input_hash=link_hash
                if role == "SEARCH" and planned.phase == "validation"
                else None,
            )
            result_rows[candidate_id if role == "SEARCH" else role] = bound
        for candidate_id, status in planned.not_evaluated:
            checkpoint()
            self.storage.record_result(
                lease,
                common,
                candidate_id=candidate_id,
                role="SEARCH",
                phase_key=phase_key,
                status=status,
            )
        return {key: result_rows[key] for key in planned.candidate_ids}

    def process(self, lease):
        started = self.timer()
        last_fence = started

        def deadline():
            if self.timer() - started >= 295:
                raise BacktestBudgetExceeded("quant attempt deadline exhausted")

        def checkpoint():
            nonlocal last_fence
            deadline()
            now = self.timer()
            if now - last_fence >= 1:
                with self.storage.transaction(lease):
                    pass
                last_fence = now

        try:
            self._process(lease, checkpoint, deadline=deadline)
            return True
        except _CandidateFailure as error:
            return self._finish(lease, error.status, error.code)
        except (BacktestBudgetExceeded, EvidenceBudgetExceeded):
            return self._finish(lease, "FAILED", "BUDGET_EXHAUSTED")
        except BacktestBlocked as error:
            return self._finish(lease, "BLOCKED", error.code)
        except BacktestError as error:
            if error.code == "LEASE_LOST":
                return False
            if error.code == "BUDGET_EXHAUSTED":
                return self._finish(lease, "FAILED", error.code)
            try:
                gate = self._gate(lease)
            except BacktestError:
                return False
            return self._finish(lease, "BLOCKED" if gate else "FAILED", gate or "INTERNAL_FAILURE")
        except Exception:
            return self._finish(lease, "FAILED", "INTERNAL_FAILURE")

    def _finish(self, lease, status, code):
        try:
            self.storage.publish(lease, status=status, error_code=code)
            return True
        except BacktestError:
            return False

    def _process(self, lease, checkpoint, *, deadline=None):
        if lease.row["code_version"] != self.code_version:
            raise ValueError("build version mismatch")
        gate = self._gate(lease)
        if gate:
            raise BacktestBlocked(gate)
        row = self.storage.definition(lease.row["definition_id"])
        predecessor = row["predecessor_id"]
        definition = definition_domain(
            row["body"],
            definition_version=row["definition_version"],
            predecessor_hash=self.storage.definition(predecessor)["fingerprint"]
            if predecessor
            else None,
        )
        receipt = self.storage.source_receipt(lease.batch_id)
        budget = self.storage.approval_budget(lease.batch_id)
        if budget is not None:
            _check_approved_budget(budget, lease, definition, receipt)
        inputs = load_quant_inputs(
            self.storage, lease, definition, receipt, checkpoint, deadline or checkpoint
        )
        context = QuantValidationContext(inputs.manifest, inputs.phases)
        if budget is not None:
            _check_approved_inputs(budget, definition, inputs, receipt)
        common = self._bind(lease, row, inputs)
        if budget is not None:
            gate = self._gate(lease)
            if gate:
                raise BacktestBlocked(gate)
        validation_rows = {}
        for planned in plan_analysis_validation(definition):
            rows = self._run_phase(lease, context, inputs, planned, common, checkpoint)
            if planned.phase == "validation":
                validation_rows.update(
                    {(planned.fold_index, key): value for key, value in rows.items()}
                )
        report = _reliability(inputs)
        reliability = SelectionReliability(
            definition.research_mode, report["evidenceStatus"], report["rating"]
        )
        selection_inputs = tuple(
            ValidationSelectionInput(
                candidate,
                fold,
                item.validation_input_hash,
                context.manifest_hash,
                item.analytics.selection_values,
                True,
                True,
                True,
                reliability,
            )
            for (fold, candidate), item in validation_rows.items()
        )
        with self.storage.transaction(lease) as connection:
            existing = connection.execute(
                text(
                    "SELECT body->>'frozenAt' FROM backtest.experiment_selections "
                    "WHERE batch_id=:id AND scope='final'"
                ),
                {"id": lease.batch_id},
            ).scalar_one_or_none()
            frozen_at = (
                datetime.fromisoformat(existing)
                if existing
                else connection.execute(text("SELECT clock_timestamp()")).scalar_one()
            )
        plan = freeze_selection_plan(definition, selection_inputs, frozen_at=frozen_at)
        if not plan.complete:
            raise BacktestBlocked("INCOMPLETE_RESULT")
        self.storage.freeze_selections(lease, tuple(d.to_wire() for d in plan.decisions))
        selected = {d.scope: d for d in plan.decisions}
        test_rows = []
        for planned in plan.phases:
            if planned.phase not in ("test", "holdout"):
                continue
            selection = selected[
                "final" if planned.fold_index is None else f"fold:{planned.fold_index}"
            ]
            rows = self._run_phase(
                lease,
                context,
                inputs,
                planned,
                common,
                checkpoint,
                validation_rows=validation_rows,
                selection=selection,
            )
            if planned.phase == "test":
                test_rows.extend(rows.values())
        self._publish(
            lease, row, inputs, plan, validation_rows, tuple(test_rows), common, checkpoint
        )

    def _publish(self, lease, row, inputs, plan, validation_rows, test_rows, common, checkpoint):
        from packages.backtesting.quant_lab_history import empty_counts, project_history

        checkpoint()
        if gate := self._gate(lease):
            raise BacktestBlocked(gate)
        with self.storage.transaction(lease) as connection:
            created_at = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
            candidates = row["candidate_count"]
            history = project_history(
                connection,
                row,
                mark=lease.row["registry_watermark"],
                registry_hash=lease.row["registry_hash"],
                bound_at=lease.row["accepted_at"],
                candidate_counts=empty_counts(candidates)
                | dict(started=candidates, complete=candidates),
                reference_counts=empty_counts(2) | dict(started=2, complete=2),
                phase_bar_starts={
                    (p.binding.phase, p.binding.fold_index): p.binding.expected_bar_starts
                    for p in inputs.phases
                },
                seed_bar_starts={
                    (p.binding.phase, p.binding.fold_index): p.binding.expected_seed_starts
                    for p in inputs.phases
                },
            )
            links = tuple(
                connection.execute(
                    text(
                        "SELECT fingerprint,reliability_report_id FROM "
                        "backtest.experiment_result_links WHERE batch_id=:id "
                        "ORDER BY candidate_id,role,phase_key"
                    ),
                    {"id": lease.batch_id},
                ).mappings()
            )
            selections = tuple(
                connection.execute(
                    text(
                        "SELECT fingerprint FROM backtest.experiment_selections "
                        "WHERE batch_id=:id ORDER BY scope"
                    ),
                    {"id": lease.batch_id},
                ).scalars()
            )
        comparison = build_comparison_report(
            inputs.manifest,
            report_id=uuid5(lease.batch_id, "quant-comparison-v1"),
            batch_id=lease.batch_id,
            created_at=created_at,
            selections=plan.decisions,
            validation_rows=tuple(validation_rows.values()),
            test_rows=test_rows,
            research_history=history,
            complete=True,
        )
        if comparison["status"] != "COMPLETE":
            raise BacktestBlocked("INCOMPLETE_RESULT")
        self.storage.publish(
            lease,
            dict(
                common,
                comparison=comparison,
                snapshotHash=inputs.manifest.manifest_hash,
                registryHash=lease.row["registry_hash"],
                reliabilityStatus=_reliability(inputs)["evidenceStatus"],
                selectionHashes=list(selections),
                resultHashes=[link["fingerprint"] for link in links],
                reliabilityReferences=[
                    str(link["reliability_report_id"])
                    for link in links
                    if link["reliability_report_id"]
                ],
            ),
        )
