"""Research metrics reject arbitrary labels and never retain identifiers."""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from packages.observability.research import ResearchMetricError, ResearchMetrics, ResearchOperation
from packages.research.models import QualityStatus


def test_query_metrics_are_exact_numeric_aggregates_and_detached_snapshots():
    metrics = ResearchMetrics()
    metrics.observe_query(
        ResearchOperation.BARS, 0.25, rows=2, quality=QualityStatus.NO_OBSERVATION
    )
    metrics.observe_query(ResearchOperation.BARS, 0.75, error=ResearchMetricError.UNAVAILABLE)
    values = metrics.snapshot()
    assert values == {
        "research_query_bars_count": 2,
        "research_query_bars_duration_seconds": 1.0,
        "research_query_bars_rows": 2,
        "research_quality_no_observation_count": 1,
        "research_error_unavailable_count": 1,
    }
    values["research_query_bars_count"] = 999
    assert metrics.snapshot()["research_query_bars_count"] == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "synthetic-private-key"},
        {"duration_seconds": float("nan")},
        {"duration_seconds": float("inf")},
        {"duration_seconds": -1},
        {"duration_seconds": True},
        {"duration_seconds": 86401},
        {"rows": 201},
        {"rows": True},
        {"quality": "synthetic-private-payload"},
        {"error": "synthetic-private-cursor"},
    ],
)
def test_untrusted_values_rejected_before_any_observation(changes):
    metrics = ResearchMetrics()
    with pytest.raises(ValueError):
        metrics.observe_query(
            **{
                "operation": ResearchOperation.BARS,
                "duration_seconds": 1,
                **changes,
            }
        )
    assert metrics.snapshot() == {}


def test_metrics_are_thread_safe_without_unbounded_event_retention():
    metrics = ResearchMetrics()
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: metrics.observe_query(ResearchOperation.JOB, 0.5), range(1000)))
    assert metrics.snapshot() == {
        "research_query_job_count": 1000,
        "research_query_job_duration_seconds": 500,
        "research_query_job_rows": 0,
    }


def test_query_audit_is_trace_correlated_bounded_and_detached():
    metrics = ResearchMetrics()
    for value in range(1, 301):
        metrics.observe_query(
            ResearchOperation.BARS,
            0.5,
            rows=2,
            quality=QualityStatus.UNKNOWN_CALENDAR,
            error=ResearchMetricError.UNAVAILABLE,
            trace_id=f"{value:032x}",
        )
    records = metrics.audit_snapshot()
    assert len(records) == 256
    assert records[0]["trace"] == f"{45:032x}"
    assert records[-1] == {
        "trace": f"{300:032x}",
        "operation": "bars",
        "durationSeconds": 0.5,
        "rows": 2,
        "quality": "UNKNOWN_CALENDAR",
        "error": "UNAVAILABLE",
    }
    records[0]["trace"] = "changed"
    assert metrics.audit_snapshot()[0]["trace"] == f"{45:032x}"
    assert metrics.snapshot()["research_query_bars_count"] == 300


@pytest.mark.parametrize("trace", ["0" * 32, "a" * 31, "a" * 33, "A" * 32, 1, "private-key-canary"])
def test_invalid_trace_is_rejected_before_metrics_or_audit(trace):
    metrics = ResearchMetrics()
    with pytest.raises(ValueError, match="trace"):
        metrics.observe_query(ResearchOperation.BARS, 0.5, trace_id=trace)
    assert metrics.snapshot() == {} and metrics.audit_snapshot() == []


def test_export_contains_only_numeric_metrics_and_redacted_bounded_query_audit(tmp_path):
    metrics = ResearchMetrics()
    metrics.observe_query(
        ResearchOperation.EVIDENCE,
        0.25,
        rows=1,
        trace_id="a" * 32,
        error=ResearchMetricError.NOT_FOUND,
    )
    path = tmp_path / "research.json"
    assert metrics.export_local(path, aggregates={"research_jobs_count": 3})
    value = json.loads(path.read_text())
    assert value["metrics"]["research_jobs_count"] == 3
    assert all(type(item) in (int, float) for item in value["metrics"].values())
    assert value["queryAudit"] == metrics.audit_snapshot()
    assert set(value["queryAudit"][0]) == {
        "trace",
        "operation",
        "durationSeconds",
        "rows",
        "quality",
        "error",
    }
    assert value["checkedAt"].endswith("+00:00")
    assert "private" not in path.read_text() and "cursor" not in path.read_text()
    assert list(tmp_path.iterdir()) == [path]
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "aggregate",
    [
        {"research_private_key_canary": 1},
        {"jobId": 1},
        {"research_jobs_count": "private-cursor-canary"},
        {"research_jobs_count": True},
        {"research_jobs_count": -1},
        {"research_jobs_count": float("nan")},
        {"research_jobs_count": float("inf")},
    ],
)
def test_export_rejects_untrusted_labels_and_values_without_touching_previous_file(
    tmp_path, aggregate
):
    metrics = ResearchMetrics()
    path = tmp_path / "research.json"
    path.write_text("previous")
    assert not metrics.export_local(path, aggregates=aggregate)
    assert path.read_text() == "previous"
    assert list(tmp_path.iterdir()) == [path]


def test_export_failure_is_silent_and_cleans_private_temporary(tmp_path, monkeypatch):
    metrics = ResearchMetrics()

    def fail(*_):
        raise OSError("synthetic-private-export-canary")

    monkeypatch.setattr("packages.observability.research.os.replace", fail)
    assert not metrics.export_local(tmp_path / "research.json")
    assert list(tmp_path.iterdir()) == []


def test_concurrent_exports_are_complete_atomic_documents(tmp_path):
    metrics = ResearchMetrics()
    metrics.observe_query(ResearchOperation.JOB, 1, trace_id="b" * 32)
    path = tmp_path / "research.json"
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert all(executor.map(lambda _: metrics.export_local(path), range(20)))
    assert json.loads(path.read_text())["queryAudit"] == metrics.audit_snapshot()
    assert list(tmp_path.iterdir()) == [path]
