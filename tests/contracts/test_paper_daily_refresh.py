"""Daily refresh keeps initial reference and proves flows without order endpoints."""

from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import Mock

import httpx
import pytest

from services.execution.paper_credentials import PrivatePaperCredentials
from services.execution.paper_daily_refresh import PaperDailyRefreshCollector
from services.execution.paper_initial_evidence import InitialEvidenceUnavailable
from tests.contracts.test_paper_initial_evidence import ACCOUNT, ACCOUNT_DATA, CANARY, NOW


def fixture(*, expired=False, drift=False, unknown=False):
    calls, reads = [], 0
    start = NOW - timedelta(hours=1)
    reference = {
        "kind": "FIRST_DAY", "startsAt": start.isoformat().replace("+00:00", "Z"),
        "expiresAt": (NOW.replace(hour=0) + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "openingLower": "100000", "openingUpper": "100000", "capitalLower": "100000",
        "referenceHash": "a" * 64,
        "accountCreatedAt": (start-timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    }
    if expired:
        reference["startsAt"] = (start-timedelta(days=1)).isoformat()
        reference["expiresAt"] = NOW.replace(hour=0).isoformat()

    def handle(request):
        nonlocal reads
        calls.append(request)
        assert request.method == "GET" and request.url.host == "paper-api.alpaca.markets"
        if request.url.path == "/v2/clock":
            return httpx.Response(200, json={"timestamp": NOW.isoformat()})
        if request.url.path == "/v2/account":
            reads += 1
            raw = deepcopy(ACCOUNT_DATA)
            if drift and reads > 2:
                raw.update(equity="100501", portfolio_value="100501")
            return httpx.Response(200, json=raw)
        if request.url.path.endswith("/history"):
            midnight = NOW.replace(hour=0)
            return httpx.Response(200, json={"timeframe": "1Min", "equity": [100000],
                "timestamp": [int((midnight-timedelta(minutes=1)).timestamp())]})
        assert request.url.path == "/v2/account/activities"
        rows = [{"id": "20261021::funding", "activity_type": "CSD", "net_amount": "50",
                 "date": NOW.date().isoformat(), "created_at": NOW.isoformat()}]
        if unknown:
            rows[0]["activity_type"] = "UNKNOWN_TRANSFER"
        return httpx.Response(200, json=rows)

    reader = Mock(load=Mock(return_value=PrivatePaperCredentials(7, CANARY, CANARY, ACCOUNT)))
    return PaperDailyRefreshCollector(
        credentials=reader, clock=Mock(now=lambda: NOW), expected_account_id=ACCOUNT,
        pinned_account_id=ACCOUNT, transport=httpx.MockTransport(handle),
    ), reference, calls


def test_refresh_preserves_first_day_reference_and_proves_later_funding():
    collector, reference, calls = fixture()
    result = collector.collect(7, reference)
    assert all(result[k] == value for k, value in reference.items())
    assert result["currentEquity"] == "100500" and result["externalFunding"] == "50"
    assert len(result["observationHash"]) == 64
    assert CANARY not in repr(result) and ACCOUNT not in repr(result)
    queries = [r.url.params for r in calls if r.url.path.endswith("/activities")]
    assert sum(
        datetime.fromisoformat(p["after"]) == datetime.fromisoformat(reference["startsAt"])
        for p in queries if "after" in p
    ) == 2
    assert not any("orders" in r.url.path or "positions" in r.url.path for r in calls)


def test_expired_first_day_requires_real_utc_history_and_new_reference():
    collector, reference, calls = fixture(expired=True)
    result = collector.collect(7, reference)
    assert result["kind"] == "UTC_DAY" and result["referenceHash"] != reference["referenceHash"]
    assert result["openingLower"] == "99999.99" and result["capitalLower"] == "99999.99"
    assert all(result[key].endswith("Z") for key in ("startsAt", "expiresAt", "observedAt"))
    assert sum(r.url.path.endswith("/history") for r in calls) == 2


@pytest.mark.parametrize("options", [{"drift": True}, {"unknown": True}])
def test_changed_or_unproven_evidence_cannot_refresh(options):
    collector, reference, _ = fixture(**options)
    with pytest.raises(InitialEvidenceUnavailable):
        collector.collect(7, reference)
