"""Real pure assessment/simulation behind fake SQL ports; current facts remain separate."""

import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from packages.backtesting.jobs import BacktestError
from packages.backtesting.models import BacktestBudgetExceeded
from packages.backtesting.retrospective_storage import RetrospectiveLease
from packages.domain.models import utc_string
from services.worker import retrospective_backtesting as worker_module
from services.worker.backtesting import BacktestWorker
from tests.contracts.test_retrospective_contracts import validator
from tests.unit.test_research_reliability import prepared
from tests.unit.test_retrospective_dataset import fixture


def inputs():
    value = prepared()
    config, dataset = value["config"], value["dataset"]
    rows = []
    for bar in dataset.revisions:
        row = {**vars(bar), **vars(bar.identity)}
        row.update(interval=bar.identity.interval.value, adjustment=bar.identity.adjustment.value)
        rows.append(("BAR", row))
    rows.extend(("CALENDAR", row) for row in value["calendar_rows"])
    request = dict(
        instrumentId=str(config.instrument_id),
        strategyId=config.strategy_id,
        version="1",
        parameters=dict(config.parameters),
        startCash="1000",
        feeUsd="0",
        slippageBps="0",
        quantityStep="0.000001",
        researchMode="RETROSPECTIVE_EXPLORATORY",
        selectionPolicyVersion="retrospective-v1",
        folds=[
            dict(
                trainingStart=utc_string(f.training_start),
                trainingEnd=utc_string(f.training_end),
                testStart=utc_string(f.test_start),
                testEnd=utc_string(f.test_end),
            )
            for f in config.folds
        ],
    )
    lease = RetrospectiveLease(
        UUID(int=700),
        UUID(int=800),
        1,
        dict(
            request=request,
            code_version="c" * 40,
            snapshot_version=dataset.snapshot.version,
            snapshot_at=dataset.snapshot.at,
        ),
    )
    receipt = value["license_receipt"]
    port = MemoryPort(
        dict(
            assessment_id=receipt.receipt_id,
            provider=receipt.provider,
            status=receipt.status,
            recorded_at=receipt.recorded_at,
            fingerprint=receipt.fingerprint,
        )
    )
    return config, lease, (dict(trustState=dict(version="b" * 64, records=[])), tuple(rows)), port


class MemoryPort:
    def __init__(self, receipt):
        self.receipt, self.preview, self.final, self.result = receipt, None, None, None
        self.gates, self.checkpoints, self.failure, self.current_calls = [], [], None, 0
        self.codes = []

    def checkpoint(self, lease):
        self.checkpoints.append(lease.fence)

    def license_receipt(self, _id):
        return self.receipt

    def publish_assessment(self, lease, report=None, *, error_code=None, **entries):
        self.preview = None if report is None else dict(body=report, **entries)
        self.failure = error_code

    def preview_report(self, _id):
        return self.preview

    def current_gate(self, lease):
        self.current_calls += 1
        return self.codes.pop(0) if self.codes else None

    def block_current_gate(self, lease, code):
        self.gates.append(code)

    def checkpoint_run(self, lease, index, projection):
        self.checkpoints.append(index)

    def publish_run(self, lease, result, report, **metadata):
        self.result, self.final = result, (report, metadata)

    def fail_run(self, lease, code):
        self.failure = code


def configured(monkeypatch):
    config, lease, data, port = inputs()
    monkeypatch.setattr(worker_module, "parse_request", lambda request, now: config)
    worker = worker_module.RetrospectiveWorker(
        None, owner=lease.owner, code_version="c" * 40, timer=lambda: 0
    )
    worker.storage = port
    monkeypatch.setattr(worker, "_read_inputs", lambda *args: data)
    return worker, lease, port


def test_preview_runs_pure_assessor_without_any_simulator_cpu(monkeypatch):
    worker, lease, port = configured(monkeypatch)
    monkeypatch.setattr(worker_module, "evaluate", lambda *a, **k: pytest.fail("preview simulated"))
    assert worker.process(lease)
    assert port.failure is None
    assert port.preview["body"]["overallStatus"] == "EXPLORATORY_WITH_LIMITATIONS"
    assert port.preview["body"]["licenseStatus"] == "UNKNOWN"
    assert len(port.preview["findings"]) == port.preview["body"]["findingsCount"]
    assert port.current_calls == 0
    validator("BacktestReliabilityReportV1").validate(port.preview["body"])


def test_run_uses_real_bound_engine_and_canonical_retrospective_projections(monkeypatch):
    worker, lease, port = configured(monkeypatch)
    assert worker.process(lease)
    before = port.preview["body"]["reportHash"]
    run = replace(lease, run_id=UUID(int=900))
    assert worker.process(run)
    assert port.failure is None
    assert port.current_calls == 3
    report, metadata = port.final
    assert report["reportHash"] == before
    validator("BacktestRetrospectiveManifest").validate(metadata["manifest"])
    validator("BacktestReliabilityReportV1").validate(report)
    for summary in port.result["summary"]:
        validator("BacktestRetrospectiveFoldSummary").validate(summary)
        assert summary["coverage"]["corporateActionStatus"] == "UNKNOWN"
    for fill in port.result["fills"]:
        validator("BacktestRetrospectiveFill").validate(fill)
        assert "observationCutoff" not in fill


@pytest.mark.parametrize(
    "code",
    (
        "LICENSE_DENIED",
        "TRUST_REVOKED",
        "TRUST_UNAVAILABLE",
        "STRUCTURAL_CORPORATE_ACTION",
        "CORPORATE_ACTION_CONFLICT",
    ),
)
def test_current_refusal_prevents_cpu_and_does_not_rewrite_snapshot(monkeypatch, code):
    worker, lease, port = configured(monkeypatch)
    assert worker.process(lease)
    before = dict(port.preview["body"])
    port.codes = [code]
    monkeypatch.setattr(worker_module, "evaluate", lambda *a, **k: pytest.fail("gate simulated"))
    assert worker.process(replace(lease, run_id=UUID(int=901)))
    assert port.preview["body"] == before
    assert port.receipt["status"] == "UNKNOWN"
    assert port.gates == [code]
    assert port.result is None


def test_revocation_between_simulation_and_publish_discards_result(monkeypatch):
    worker, lease, port = configured(monkeypatch)
    assert worker.process(lease)
    port.codes = [None, None, "LICENSE_DENIED"]
    assert worker.process(replace(lease, run_id=UUID(int=902)))
    assert port.gates == ["LICENSE_DENIED"]
    assert port.result is None
    assert port.preview["body"]["licenseStatus"] == "UNKNOWN"


def test_expired_cpu_budget_and_different_build_cannot_publish(monkeypatch):
    worker, lease, port = configured(monkeypatch)
    assert worker.process(replace(lease, row={**lease.row, "code_version": "e" * 40}))
    assert port.failure == "INTERNAL_FAILURE"
    times = iter((0, 295))
    worker.timer = lambda: next(times)
    assert worker.process(lease)
    assert port.failure == "BUDGET_EXHAUSTED"
    assert port.preview is None


def test_dispatch_default_disabled_never_claims(monkeypatch):
    worker = BacktestWorker(None)
    worker.retrospective_worker = SimpleNamespace(
        tick=lambda: pytest.fail("disabled worker claimed")
    )
    assert worker.tick() is False


def test_metadata_and_reference_budgets_do_not_reenter_the_locked_job_fence():
    config, lease, _data, _port = inputs()
    lease = replace(lease, row={**lease.row, "trust_version": 0, "trust_policy_version": "test-v1"})
    worker = worker_module.RetrospectiveWorker(None, owner=lease.owner, code_version="c" * 40)
    calls = []

    class MetadataPort:
        active = False
        content = None

        @contextmanager
        def transaction(self, **kwargs):
            assert kwargs == {"repeatable": True, "lease": lease}
            self.active = True
            try:
                yield SimpleNamespace(execute=lambda *args: SimpleNamespace(mappings=lambda: ()))
            finally:
                self.active = False

        def fenced(self, connection, current):
            assert self.active

        def load_dataset(self, identifier, *, checkpoint):
            if self.content is None:
                raise BacktestError(404, "NOT_FOUND")
            checkpoint()
            return {"body": {"content": self.content}}, ()

        def bind_dataset(self, current, content, references, *, checkpoint):
            self.active = True
            try:
                checkpoint()
                self.content = content
            finally:
                self.active = False

    worker.storage = MetadataPort()

    def checkpoint(*, fence=True):
        assert not (fence and worker.storage.active), "nested fence would self-lock"
        calls.append(fence)

    content, rows = worker._read_inputs(lease, config, checkpoint)
    assert content["trustState"]["records"] == []
    assert rows == ()
    assert False in calls and calls[-1] is True


@pytest.mark.parametrize(
    ("field", "value"),
    (("interval", "1Day"), ("provider", "other"), ("feed", "sip"), ("adjustment", "split")),
)
def test_shared_research_series_are_excluded_before_reference_binding(monkeypatch, field, value):
    """Execute the worker's actual selection SQL; the real assessor sees only its series."""
    config, lease, (_, rows), receipt_port = inputs()
    lease = replace(lease, row={**lease.row, "trust_version": 0, "trust_policy_version": "test-v1"})
    bars = [row for kind, row in rows if kind == "BAR"]
    for row in bars:
        row["fingerprint"] = worker_module.bar_from_row(row).fingerprint
    unrelated = {**bars[0], "revision_id": UUID(int=9999), field: value}
    daily_quality = dict(
        observation_id=UUID(int=9998),
        instrument_id=config.instrument_id,
        interval="1Day",
        start_at=config.folds[0].training_start,
        end_at=config.folds[-1].test_end,
        commit_version=1,
        ingested_at=bars[0]["ingested_at"],
        fingerprint="f" * 64,
        quality="INVALID",
        rule_version="research-quality-v1",
        code="REJECTED_PROVIDER_RESPONSE",
    )
    by_id = {str(row["revision_id"]): row for row in (*bars, unrelated)}
    with sqlite3.connect(":memory:") as database:
        database.execute("ATTACH DATABASE ':memory:' AS research")
        columns = (
            "revision_id",
            "instrument_id",
            "provider",
            "feed",
            "interval",
            "adjustment",
            "bar_start",
            "bar_end",
            "commit_version",
            "ingested_at",
        )
        database.execute(
            "CREATE TABLE research.bar_revisions ("
            + ",".join(
                f"{name} {'INTEGER' if name == 'commit_version' else 'TEXT'}" for name in columns
            )
            + ")"
        )
        database.executemany(
            "INSERT INTO research.bar_revisions VALUES (" + ",".join("?" for _ in columns) + ")",
            [
                tuple(row[name] if name == "commit_version" else str(row[name]) for name in columns)
                for row in by_id.values()
            ],
        )
        quality_columns = (
            "observation_id",
            "instrument_id",
            "interval",
            "start_at",
            "end_at",
            "commit_version",
            "ingested_at",
        )
        database.execute(
            "CREATE TABLE research.quality_observations ("
            + ",".join(
                f"{name} {'INTEGER' if name == 'commit_version' else 'TEXT'}"
                for name in quality_columns
            )
            + ")"
        )
        database.execute(
            "INSERT INTO research.quality_observations VALUES ("
            + ",".join("?" for _ in quality_columns)
            + ")",
            tuple(
                daily_quality[name] if name == "commit_version" else str(daily_quality[name])
                for name in quality_columns
            ),
        )

        class SelectionPort(MemoryPort):
            content = None
            references = ()
            restored = ()

            def execute(self, statement, parameters):
                if "FROM research.bar_revisions" in str(statement):
                    bound = {
                        key: val if isinstance(val, int) else str(val)
                        for key, val in parameters.items()
                    }
                    selected = database.execute(str(statement), bound).fetchall()
                    result = [by_id[row[0]] for row in selected]
                elif "FROM research.quality_observations" in str(statement):
                    # Keep the actual selection predicate; only PostgreSQL's fingerprint
                    # projection is replaced for the local SQL execution engine.
                    predicate = str(statement).partition("WHERE ")[2]
                    bound = {
                        key: val if isinstance(val, int) else str(val)
                        for key, val in parameters.items()
                    }
                    selected = database.execute(
                        "SELECT observation_id FROM research.quality_observations WHERE "
                        + predicate,
                        bound,
                    ).fetchall()
                    result = [daily_quality for _ in selected]
                elif "FROM research.calendar_versions" in str(statement):
                    result = [row for kind, row in rows if kind == "CALENDAR"]
                else:
                    result = []
                return SimpleNamespace(mappings=lambda: result)

            @contextmanager
            def transaction(self, **kwargs):
                yield self

            def fenced(self, connection, current):
                assert current == lease

            def load_dataset(self, identifier, *, checkpoint):
                if self.content is None:
                    raise BacktestError(404, "NOT_FOUND")
                return {"body": {"content": self.content}}, self.restored

            def bind_dataset(self, current, content, references, *, checkpoint):
                self.content, self.references = content, references
                selected = {(kind, identifier) for kind, identifier, _ in references}
                id_fields = {
                    "BAR": "revision_id",
                    "CALENDAR": "session_id",
                    "QUALITY": "observation_id",
                }
                self.restored = tuple(
                    (kind, row)
                    for kind, row in (*rows, ("BAR", unrelated), ("QUALITY", daily_quality))
                    if (kind, row[id_fields[kind]]) in selected
                )

        port = SelectionPort(receipt_port.receipt)
        worker = worker_module.RetrospectiveWorker(
            None, owner=lease.owner, code_version="c" * 40, timer=lambda: 0
        )
        worker.storage = port
        monkeypatch.setattr(worker_module, "parse_request", lambda request, now: config)
        worker._read_inputs(lease, config, lambda **kwargs: None)
        assert worker.process(lease)
        assert port.failure is None
        assert port.preview["body"]["overallStatus"] == "EXPLORATORY_WITH_LIMITATIONS"
        assert {identifier for kind, identifier, _ in port.references if kind == "BAR"} == {
            row["revision_id"] for row in bars
        }
        assert not any(kind == "QUALITY" for kind, _, _ in port.references)


@pytest.mark.parametrize("bar_count", (208, 780))
def test_manifest_budget_counts_canonical_bytes_not_repeated_revision_count(bar_count):
    def series(warmup):
        config, snapshot, bars, calendar = fixture()
        start = bars[0].identity.bar_start
        observed = start + timedelta(days=(bar_count + 25) // 26 + 1)
        snapshot = replace(snapshot, version=10000, at=observed + timedelta(hours=1))
        expanded = tuple(
            replace(
                bars[0],
                revision_id=UUID(int=100 + index),
                commit_version=index + 1,
                identity=replace(
                    bars[0].identity,
                    bar_start=start + timedelta(days=index // 26, minutes=15 * (index % 26)),
                    bar_end=start + timedelta(days=index // 26, minutes=15 * (index % 26 + 1)),
                ),
                ingested_at=observed,
                available_at=observed,
            )
            for index in range(bar_count)
        )
        fold = replace(
            config.folds[0],
            training_start=start,
            training_end=expanded[warmup].identity.bar_start,
            test_start=expanded[warmup].identity.bar_start,
            test_end=expanded[-1].identity.bar_end,
        )
        config = replace(
            config,
            strategy_id="sma-cross-v1",
            parameters=(("fast", 2), ("slow", warmup + 1)),
            folds=(fold,),
        )
        sessions = tuple(
            {
                **calendar[0],
                "session_id": UUID(int=10000 + day),
                "session_date": (start + timedelta(days=day)).date(),
                "opens_at": expanded[day * 26].identity.bar_start,
                "closes_at": expanded[min((day + 1) * 26, bar_count) - 1].identity.bar_end,
                "ingested_at": observed,
            }
            for day in range((bar_count + 25) // 26)
        )
        return config, snapshot, expanded, sessions

    value = prepared(warmup=99, fixture_factory=series)
    manifest = value["manifest"]
    assert manifest is not None
    references = sum(
        len(step.input_bars) for dataset in manifest.datasets for step in dataset.steps
    )
    assert references > 10000
    assessment = worker_module.assess_reliability(**value)
    report = assessment.to_dict(report_id=UUID(int=800), assessment_id=UUID(int=700))
    _, lease, _, _ = inputs()
    request = {
        **lease.row["request"],
        "strategyId": "sma-cross-v1",
        "parameters": {"fast": 2, "slow": 100},
        "folds": [
            {
                "trainingStart": utc_string(fold.training_start),
                "trainingEnd": utc_string(fold.training_end),
                "testStart": utc_string(fold.test_start),
                "testEnd": utc_string(fold.test_end),
            }
            for fold in manifest.config.folds
        ],
    }
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1

    if bar_count == 208:
        body = worker_module.public_manifest(
            manifest, request, report, value["license_receipt"], checkpoint
        )
        assert sum(len(step["revisions"]) for step in body["decisionInputs"]) == references
        assert len(worker_module.encoded(body).encode("utf-8")) < 12 * 1024 * 1024
    else:
        with pytest.raises(BacktestBudgetExceeded, match="byte budget"):
            worker_module.public_manifest(
                manifest, request, report, value["license_receipt"], checkpoint
            )
        assert calls < references, (
            "oversize evidence must stop before materializing every repeated reference"
        )
