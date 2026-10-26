"""Whitelist projections of bound evidence and independent fold results."""

from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from packages.backtesting.models import BacktestBudgetExceeded
from packages.domain.models import decimal_string as ds
from packages.domain.models import utc_string as ts
from packages.research.models import content_fingerprint

ASSUMPTIONS = dict(
    simulation="LOCAL_SIMULATION",
    provider="ALPACA",
    feed="iex",
    interval="15Min",
    adjustment="raw",
    currency="USD",
    positionPolicy="LONG_FLAT",
    fillModel="next-regular-bar-open-v1",
    signalDelaySeconds=60,
    outcomeCutoffSeconds=300,
    decimalPrecision=38,
    rounding="ROUND_HALF_EVEN",
    moneyScale=8,
    returnScale=12,
    dividendsIncluded=False,
    forceLiquidation=False,
)
BUDGETS = dict(
    maximumFolds=12,
    maximumCalendarDays=366,
    maximumInputRows=100000,
    attemptSeconds=300,
    maximumAttempts=3,
    maximumActiveJobs=1,
)


def bar_identity(bar):
    i = bar.identity
    return dict(
        instrumentId=str(i.instrument_id),
        provider="ALPACA",
        feed=i.feed,
        interval=i.interval.value,
        start=ts(i.bar_start),
        end=ts(i.bar_end),
        adjustment=i.adjustment.value,
    )


def revision(bar):
    return dict(
        bar=bar_identity(bar),
        revisionId=str(bar.revision_id),
        localRevision=bar.local_revision,
        fingerprint=bar.fingerprint,
        ingestedAt=ts(bar.ingested_at),
        availableAt=ts(bar.available_at),
    )


def manifest_public(
    manifest, request, datasets, calendar_rows, quality_rows, proofs, checkpoint=lambda: None
):
    # Evidence metadata comes from the snapshot, never synthetic UUID placeholders.
    calendar_ids = {ref for dataset in datasets for ref in dataset.calendar_revision_ids}
    quality_ids = {ref for dataset in datasets for ref in dataset.quality_revision_ids}
    coverage_ids = {ref for dataset in datasets for ref in dataset.coverage_ids}

    def evidence(rows, id_key):
        return [
            dict(
                evidenceId=str(r[id_key]),
                fingerprint=content_fingerprint(dict(r)),
                revision=r["commit_version"],
                ingestedAt=ts(r["ingested_at"]),
            )
            for r in sorted(rows, key=lambda row: str(row[id_key]))
        ]

    calendars = [r for r in calendar_rows if r["version"] in calendar_ids]
    qualities = [r for r in quality_rows if str(r["observation_id"]) in quality_ids]
    if not qualities:
        valid_bars = {
            bar.revision_id: bar
            for dataset in datasets
            for step in dataset.steps
            for bar in step.input_bars
        }
        qualities = [
            dict(
                observation_id=b.revision_id,
                commit_version=b.commit_version,
                ingested_at=b.ingested_at,
                fingerprint=b.fingerprint,
                quality=b.quality,
            )
            for b in valid_bars.values()
        ]
    coverages = [
        dict(
            coverage_id=p.coverage_id,
            commit_version=p.commit_version,
            ingested_at=p.ingested_at,
            fingerprint=p.fingerprint,
        )
        for p in proofs
        if str(p.coverage_id) in coverage_ids
    ]
    folds, decisions, outcomes = [], [], []
    reference_count = 0
    for index, dataset in enumerate(datasets):
        binding = dataset.warmup_binding
        warm = {b.identity.bar_start: b for b in dataset.warmup}
        folds.append(
            dict(
                foldIndex=index,
                window=request["folds"][index],
                warmupStart=ts(binding.warmup_start),
                warmupEnd=ts(binding.warmup_end),
                warmupRuleVersion=binding.rule_version,
                expectedWarmupBars=[
                    bar_identity(warm[start]) for start in binding.expected_bar_starts
                ],
            )
        )
        for step in dataset.steps:
            checkpoint()
            reference_count += len(step.input_bars)
            # Detailed revision evidence is ~500 bytes per entry. This headroom
            # protects the 12MiB persisted envelope and bounded 16MiB transport.
            if reference_count > 10000:
                raise BacktestBudgetExceeded("manifest evidence byte budget exhausted")
            decisions.append(
                dict(
                    valuationAt=ts(step.signal_bar.identity.bar_end),
                    knowledgeAt=ts(step.knowledge_at),
                    inputHash=content_fingerprint(
                        tuple((bar.revision_id, bar.fingerprint) for bar in step.input_bars)
                    ),
                    revisions=[revision(bar) for bar in step.input_bars],
                )
            )
        cutoffs = dict(dataset.outcome_cutoffs)
        outcomes.extend(
            dict(revision=revision(bar), observationCutoff=ts(cutoffs[bar.revision_id]))
            for bar in dataset.outcome_bars
        )
    return dict(
        manifestHash=manifest.manifest_hash,
        inputHash=content_fingerprint(
            (
                manifest.config,
                manifest.decision_data,
                manifest.outcome_data,
                manifest.calendar_revisions,
                manifest.quality_revisions,
                manifest.coverage_revisions,
            )
        ),
        request=request,
        symbol=manifest.config.symbol,
        strategyVersion=manifest.config.strategy_version,
        simulatorVersion=manifest.simulator_version,
        metricsVersion=manifest.metrics_version,
        codeVersion=manifest.code_version,
        snapshot=dict(version=manifest.snapshot_version, committedAt=ts(manifest.snapshot_at)),
        assumptions=dict(ASSUMPTIONS),
        folds=folds,
        decisionInputs=decisions,
        outcomeInputs=outcomes,
        calendarRevisions=evidence(calendars, "session_id"),
        qualityRevisions=evidence(qualities, "observation_id"),
        corporateActionCoverage=evidence(coverages, "coverage_id"),
    )


def metrics_public(evaluated):
    m = evaluated.metrics
    return dict(
        startCash=ds(evaluated.simulation.start_cash),
        endEquity=ds(m.end_equity),
        netReturn=ds(m.net_return),
        maxDrawdown=ds(m.max_drawdown),
        turnover=ds(m.turnover),
        feeTotalUsd=ds(m.fees_usd),
        slippageTotalUsd=ds(m.slippage_usd),
        endCash=ds(m.end_cash),
        endQuantity=ds(m.end_quantity),
        valuation="MARK_TO_MARKET",
        decisionCount=m.decision_count,
        fillCount=m.fill_count,
    )


def fold_public(evaluation, dataset, window):
    with localcontext() as context:
        context.prec = 38
        context.rounding = ROUND_HALF_EVEN
        return _fold_public(evaluation, dataset, window)


def _fold_public(evaluation, dataset, window):
    index = evaluation.fold_index
    summary = dict(
        foldIndex=index,
        resultHash=content_fingerprint(evaluation),
        window=window,
        metrics=metrics_public(evaluation.strategy),
        references=[
            dict(strategyId=key, metrics=metrics_public(value))
            for key, value in [("flat-v1", evaluation.flat), ("buy-hold-v1", evaluation.buy_hold)]
        ],
        coverage=dict(
            expectedBars=len(dataset.expected_bar_starts),
            observedBars=len(dataset.outcome_bars),
            warmupExpectedBars=len(dataset.warmup_binding.expected_bar_starts),
            warmupObservedBars=len(dataset.warmup),
            complete=True,
            corporateActions="CERTIFIED_ABSENT",
        ),
    )
    equity, fills = [], []
    bars = {b.revision_id: b for b in dataset.outcome_bars}
    for value in (evaluation.strategy, evaluation.flat, evaluation.buy_hold):
        sim = value.simulation
        # The selected strategy may itself be a benchmark. Emit each identity once.
        if any(p["strategyId"] == sim.strategy_id for p in equity):
            continue
        equity.extend(
            dict(
                foldIndex=index,
                strategyId=sim.strategy_id,
                at=ts(p.at),
                cash=ds(p.cash),
                quantity=ds(p.quantity),
                close=ds(p.close),
                equity=ds(p.equity),
                revisionId=str(p.outcome_revision_id),
            )
            for p in sim.equity
        )
        cash, quantity = sim.start_cash, Decimal("0")
        for sequence, fill in enumerate(sim.fills, start=1):
            if fill.side == "BUY":
                cash -= fill.notional + fill.fee
                quantity += fill.quantity
            else:
                cash += fill.notional - fill.fee
                quantity -= fill.quantity
            signal = next(
                d
                for d in reversed(sim.decisions)
                if d.fill_target == fill.at and d.signal_at <= fill.at
            )
            fills.append(
                dict(
                    foldIndex=index,
                    strategyId=sim.strategy_id,
                    sequence=sequence,
                    signalAt=ts(signal.signal_at),
                    executedAt=ts(fill.at),
                    side=fill.side,
                    quantity=ds(fill.quantity),
                    rawOpen=ds(bars[fill.outcome_revision_id].open),
                    price=ds(fill.price),
                    notionalUsd=ds(fill.notional),
                    feeUsd=ds(fill.fee),
                    slippageUsd=ds(fill.slippage_cost),
                    cashAfter=ds(cash),
                    quantityAfter=ds(quantity),
                    revisionId=str(fill.outcome_revision_id),
                    observationCutoff=ts(fill.outcome_cutoff),
                )
            )
    return dict(summary=summary, equity=equity, fills=fills)
