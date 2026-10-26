"""Offline PIT oracle tests; real locking/SQL execution is certified separately in PG."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from packages.research.models import (
    BarIdentity,
    BarInterval,
    BarRevision,
    PointInTimeQuery,
    QualityStatus,
)
from packages.research.query import BarPosition, ResearchQuery, filter_fingerprint, latest_visible
from packages.research.storage import ResearchStore, bar_from_row, evidence_for_bar

INSTRUMENT = UUID("ce85d363-e845-4b4a-9b11-6de080bb43da")
START = datetime(2026, 10, 23, 14, tzinfo=UTC)
END = START + timedelta(minutes=15)
OBSERVED = END + timedelta(days=1)


def original():
    return BarRevision(
        uuid4(),
        BarIdentity(INSTRUMENT, "ALPACA", "iex", BarInterval.FIFTEEN_MINUTES, START, END),
        Decimal("10"),
        Decimal("12"),
        Decimal("9"),
        Decimal("11"),
        Decimal("2"),
        OBSERVED,
        OBSERVED,
        commit_version=2,
    )


def view(known=OBSERVED, version=2, snapshot=OBSERVED):
    return PointInTimeQuery(known, known, snapshot, version)


def test_old_event_does_not_make_late_backfill_historically_known():
    assert latest_visible((original(),), view(END, 2)) == ()


def test_late_correction_preserves_the_original_knowledge_view():
    old = original()
    corrected_at = OBSERVED + timedelta(hours=1)
    new = replace(
        old,
        revision_id=uuid4(),
        local_revision=2,
        predecessor_id=old.revision_id,
        close=Decimal("10"),
        ingested_at=corrected_at,
        available_at=corrected_at,
        commit_version=4,
    )
    assert latest_visible((new, old), view()) == (old,)
    assert latest_visible((old, new), view(corrected_at, 4, corrected_at)) == (new,)
    # A transaction committing after snapshot capture cannot leak through the timestamp filter.
    assert latest_visible((new, old), view(corrected_at, 2, corrected_at)) == (old,)


@pytest.mark.parametrize("delta,expected", [(-1, False), (0, True), (1, True)])
def test_microsecond_knowledge_boundary_is_inclusive(delta, expected):
    known = OBSERVED + timedelta(microseconds=delta)
    assert bool(latest_visible((original(),), view(known))) is expected


def test_snapshot_ingestion_bound_is_independent_of_knowledge():
    query = view(OBSERVED + timedelta(days=1), 9, OBSERVED - timedelta(microseconds=1))
    assert latest_visible((original(),), query) == ()


def test_invalid_correction_never_suppresses_last_known_valid_revision():
    old = original()
    invalid = replace(
        old,
        revision_id=uuid4(),
        local_revision=2,
        predecessor_id=old.revision_id,
        quality=QualityStatus.CONFLICT,
    )
    assert latest_visible((invalid, old), view()) == (old,)


def test_feed_and_provider_are_distinct_natural_keys():
    old = original()
    another_feed = replace(old, revision_id=uuid4(), identity=replace(old.identity, feed="sip"))
    another_provider = replace(
        old, revision_id=uuid4(), identity=replace(old.identity, provider="OTHER")
    )
    assert set(latest_visible((old, another_feed, another_provider), view())) == {
        old,
        another_feed,
        another_provider,
    }


def test_later_bar_is_not_included_before_its_end_even_if_malformed_input_is_attempted():
    old = original()
    with pytest.raises(ValueError, match="precede"):
        replace(
            old,
            identity=replace(
                old.identity, bar_start=OBSERVED, bar_end=OBSERVED + timedelta(minutes=15)
            ),
        )


def test_evidence_identity_is_deterministic_but_its_local_availability_is_not_backdated():
    bar = original()
    evidence = evidence_for_bar(bar)
    later = evidence_for_bar(bar, ingested_at=OBSERVED + timedelta(seconds=1), commit_version=3)
    assert evidence.evidence_id == later.evidence_id
    assert evidence.fingerprint == later.fingerprint
    assert view().includes(evidence)
    assert not view().includes(later)


def test_row_rehydration_preserves_exact_values_revision_and_first_observation():
    bar = original()
    row = {
        "revision_id": bar.revision_id,
        "instrument_id": INSTRUMENT,
        "provider": "ALPACA",
        "feed": "iex",
        "interval": "15Min",
        "adjustment": "raw",
        "bar_start": START,
        "bar_end": END,
        "open": Decimal("10.000000000000000000"),
        "high": Decimal("12"),
        "low": Decimal("9"),
        "close": Decimal("11"),
        "volume": Decimal("2"),
        "ingested_at": OBSERVED,
        "published_at": None,
        "local_revision": 1,
        "predecessor_id": None,
        "provider_revision": None,
        "commit_version": 2,
        "quality": "VALID",
    }
    restored = bar_from_row(row)
    assert restored == bar
    assert restored.fingerprint == bar.fingerprint
    assert restored.available_at == restored.ingested_at


@pytest.mark.parametrize("limit", [0, 201, True])
def test_query_rejects_unbounded_pages_before_database_access(limit):
    with pytest.raises(ValueError, match="limit"):
        ResearchQuery().list_bars(
            None, INSTRUMENT, START, END, BarInterval.FIFTEEN_MINUTES, view(), limit
        )


def test_query_range_and_keyset_types_reject_bad_inputs_without_database_access():
    with pytest.raises(ValueError, match="range"):
        ResearchQuery().list_bars(
            None,
            INSTRUMENT,
            START,
            START + timedelta(days=367),
            BarInterval.FIFTEEN_MINUTES,
            view(),
        )
    with pytest.raises(ValueError, match="position"):
        ResearchQuery().list_bars(
            None,
            INSTRUMENT,
            START,
            END,
            BarInterval.FIFTEEN_MINUTES,
            view(),
            after="provider-token",
        )
    with pytest.raises(ValueError):
        BarPosition(START.replace(tzinfo=None), END, "ALPACA", uuid4(), "0" * 64)


@pytest.mark.parametrize("symbol", ["", "spy", "SPY USD", "https://SPY", "A" * 17, None])
def test_bar_append_requires_valid_explicit_provider_symbol_before_database_access(symbol):
    with pytest.raises(ValueError, match="symbol"):
        ResearchStore().append_bar(None, original(), symbol=symbol)


def test_bar_append_cannot_implicitly_select_a_provider_symbol():
    with pytest.raises(TypeError, match="symbol"):
        ResearchStore().append_bar(None, original())


@pytest.mark.parametrize(
    "changed",
    [
        {"instrument_id": UUID("29f622d9-f40c-47b0-bb10-9ee199799712")},
        {"start": START - timedelta(minutes=15)},
        {"end": END + timedelta(minutes=15)},
        {"interval": BarInterval.ONE_DAY},
        {"query": replace(view(), valuation_at=OBSERVED + timedelta(seconds=1))},
        {"query": replace(view(), knowledge_at=OBSERVED - timedelta(seconds=1))},
        {"query": replace(view(), snapshot_at=OBSERVED + timedelta(seconds=1))},
        {"query": replace(view(), snapshot_version=3)},
    ],
)
def test_internal_position_rejects_each_changed_selection_boundary_before_sql(changed):
    inputs = dict(
        instrument_id=INSTRUMENT,
        start=START,
        end=END,
        interval=BarInterval.FIFTEEN_MINUTES,
        query=view(),
    )
    fingerprint = filter_fingerprint(**inputs)
    position = BarPosition(START, END, "ALPACA", uuid4(), fingerprint)
    changed_inputs = {**inputs, **changed}
    assert filter_fingerprint(**changed_inputs) != fingerprint
    with pytest.raises(ValueError, match="filters or snapshot"):
        ResearchQuery().list_bars(None, **changed_inputs, after=position)


def test_filter_fingerprint_is_stable_for_equivalent_query_values():
    first = filter_fingerprint(INSTRUMENT, START, END, BarInterval.FIFTEEN_MINUTES, view())
    second = filter_fingerprint(
        INSTRUMENT,
        START,
        END,
        BarInterval.FIFTEEN_MINUTES,
        PointInTimeQuery(OBSERVED, OBSERVED, OBSERVED, 2),
    )
    assert first == second
