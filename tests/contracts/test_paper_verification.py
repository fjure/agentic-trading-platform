"""Execution verification via synthetic HTTP only; no broker submit port."""

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import httpx
import pytest

from services.execution.paper_credentials import (
    PaperCredentialsUnavailable,
    PrivatePaperCredentials,
)
from services.execution.paper_verification import PaperAccountVerifier

ACCOUNT = "11111111-2222-4333-8444-555555555555"
OTHER = "11111111-2222-4333-8444-555555555556"
CANARY = "synthetic-paper-verification-canary-never-a-provider-key"
NOW = datetime(2026, 10, 21, 10, tzinfo=UTC)  # Before the regular US session.
ACCOUNT_DATA = {
    "id": ACCOUNT, "status": "ACTIVE", "currency": "USD", "cash": "100000.000001",
    "account_blocked": False, "trading_blocked": False, "trade_suspended_by_user": False,
    "account_number": "synthetic-private-number", "untrusted_extra": CANARY,
}
QUOTES = {"quotes": {
    symbol: {"bp": 499.50, "ap": 500.25, "t": "2026-10-20T20:00:00Z"}
    for symbol in ["SPY", "QQQ"]
}}


def verifier(handler=None, *, account=None, quotes=None, pinned=ACCOUNT, clock=None):
    calls = []
    reader = Mock()
    reader.load.return_value = PrivatePaperCredentials(7, CANARY, CANARY + "-secret", ACCOUNT)

    def handle(request):
        calls.append(request)
        if handler:
            return handler(request)
        return httpx.Response(200, json=(
            ACCOUNT_DATA if account is None else account
        ) if request.url.path == "/v2/account" else (QUOTES if quotes is None else quotes))

    instance = PaperAccountVerifier(
        credentials=reader, clock=clock or Mock(now=Mock(return_value=NOW)),
        expected_account_id=ACCOUNT, pinned_account_id=pinned,
        transport=httpx.MockTransport(handle),
    )
    return instance, reader, calls


def assert_blocked(result, code):
    assert result.public.blockers == (code,)
    assert result.public.provider_verification == "UNVERIFIED"
    assert result.public.market_data_feed == "UNVERIFIED"
    assert result.public.cash is None and result.public.verified_at is None
    assert result.private is None
    assert CANARY not in repr(result) and ACCOUNT not in repr(result)


def test_connection_verification_is_get_only_sanitized_and_not_market_authorization(caplog):
    instance, reader, calls = verifier()
    result = instance.verify(7)
    reader.load.assert_called_once_with(7)
    assert result.public.cash == Decimal("100000.000001")
    assert result.public.observed_at == result.public.verified_at == NOW
    assert result.public.blockers == ()
    assert result.public.market_data_feed == "IEX"
    assert result.public.provider_verification == "CONFIRMED"
    assert result.public.account_label.startswith("paper-")
    assert result.private.provider_account_id == ACCOUNT
    assert result.private.credential_generation == result.public.credential_generation == 7
    assert [str(request.url) for request in calls] == [
        "https://paper-api.alpaca.markets/v2/account",
        "https://data.alpaca.markets/v2/stocks/quotes/latest?symbols=SPY%2CQQQ&feed=iex",
    ]
    assert all(request.method == "GET" and not request.content for request in calls)
    assert all(request.headers["APCA-API-KEY-ID"] == CANARY for request in calls)
    assert not any(
        key in asdict(result.public) for key in ["api_key", "provider_account_id", "orders"]
    )
    for public_output in [repr(result), repr(result.public), repr(result.private), caplog.text]:
        assert CANARY not in public_output and ACCOUNT not in public_output


@pytest.mark.parametrize("changes,code", [
    ({"id": OTHER}, "ACCOUNT_IDENTITY_MISMATCH"),
    ({"id": "bad-id"}, "ACCOUNT_IDENTITY_MISMATCH"),
    ({"currency": "EUR"}, "CURRENCY_UNSUPPORTED"),
    ({"status": "INACTIVE"}, "ACCOUNT_BLOCKED"),
    ({"account_blocked": True}, "ACCOUNT_BLOCKED"),
    ({"trading_blocked": True}, "ACCOUNT_BLOCKED"),
    ({"trade_suspended_by_user": True}, "ACCOUNT_BLOCKED"),
    ({"trading_blocked": 0}, "ACCOUNT_BLOCKED"),
    ({"account_blocked": None}, "ACCOUNT_BLOCKED"),
    ({"cash": "-1"}, "ACCOUNT_BLOCKED"),
    ({"cash": "NaN"}, "PROVIDER_UNAVAILABLE"),
    ({"cash": 100}, "PROVIDER_UNAVAILABLE"),
])
def test_account_checks_fail_closed_before_data_request(changes, code):
    instance, _, calls = verifier(account=ACCOUNT_DATA | changes)
    assert_blocked(instance.verify(7), code)
    assert len(calls) == 1


def test_pinned_identity_and_credential_generation_are_checked_before_io():
    instance, reader, calls = verifier(pinned=OTHER)
    assert_blocked(instance.verify(7), "ACCOUNT_IDENTITY_MISMATCH")
    assert calls == []
    instance, reader, calls = verifier()
    reader.load.return_value = PrivatePaperCredentials(8, CANARY, CANARY, ACCOUNT)
    assert_blocked(instance.verify(7), "CREDENTIALS_MISSING")
    assert calls == []
    reader.load.return_value = PrivatePaperCredentials(7, CANARY, CANARY, OTHER)
    assert_blocked(instance.verify(7), "ACCOUNT_IDENTITY_MISMATCH")
    assert calls == []
    reader.load.side_effect = PaperCredentialsUnavailable()
    assert_blocked(instance.verify(7), "CREDENTIALS_MISSING")
    assert calls == []


@pytest.mark.parametrize("status,code", [
    (401, "CREDENTIALS_INVALID"), (403, "CREDENTIALS_INVALID"),
    (429, "PROVIDER_RATE_LIMITED"), (500, "PROVIDER_UNAVAILABLE"),
    (302, "PROVIDER_UNAVAILABLE"),
])
def test_account_http_failures_have_allowlisted_errors_and_no_redirect(status, code, caplog):
    instance, _, calls = verifier(lambda _r: httpx.Response(
        status, text=CANARY, headers={"Location": "https://invalid.example/"}
    ))
    assert_blocked(instance.verify(7), code)
    assert len(calls) == 1
    assert CANARY not in caplog.text


@pytest.mark.parametrize("status,code", [
    (401, "MARKET_DATA_UNAVAILABLE"), (403, "MARKET_DATA_UNAVAILABLE"),
    (429, "PROVIDER_RATE_LIMITED"), (503, "PROVIDER_UNAVAILABLE"),
])
def test_market_failure_does_not_claim_verified_account_or_feed(status, code):
    def handler(request):
        return httpx.Response(200, json=ACCOUNT_DATA) if request.url.path == "/v2/account" else (
            httpx.Response(status, text=CANARY)
        )
    instance, _, calls = verifier(handler)
    assert_blocked(instance.verify(7), code)
    assert len(calls) == 2


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=CANARY),
    httpx.Response(200, content=b'{"id":"first","id":"second"}',
                   headers={"content-type": "application/json"}),
    httpx.Response(200, content=b'{"cash":NaN}', headers={"content-type": "application/json"}),
    httpx.Response(200, content=b"{}", headers={
        "content-type": "application/json", "content-length": "65537",
    }),
    httpx.Response(200, content=b"x" * 65537, headers={"content-type": "application/json"}),
    httpx.Response(200, content=b"{}", headers={
        "content-type": "application/json", "content-encoding": "unexpected",
    }),
])
def test_invalid_or_unbounded_responses_are_redacted(response):
    instance, _, _ = verifier(lambda _r: response)
    assert_blocked(instance.verify(7), "PROVIDER_UNAVAILABLE")


@pytest.mark.parametrize("kind", ["missing-symbol", "zero-bid", "crossed", "future", "naive"])
def test_feed_probe_requires_valid_quotes_for_both_symbols(kind):
    quotes = deepcopy(QUOTES)
    if kind == "missing-symbol":
        del quotes["quotes"]["QQQ"]
    else:
        quotes["quotes"]["SPY"].update({
            "zero-bid": {"bp": 0}, "crossed": {"bp": 600},
            "future": {"t": "2026-10-22T10:00:00Z"},
            "naive": {"t": "2026-10-20T20:00:00"},
        }[kind])
    instance, _, _ = verifier(quotes=quotes)
    assert_blocked(instance.verify(7), "MARKET_DATA_UNAVAILABLE")


def test_transport_exception_and_clock_drift_do_not_leak(caplog):
    def fail(request):
        raise httpx.ReadTimeout(CANARY, request=request)
    instance, _, _ = verifier(fail)
    assert_blocked(instance.verify(7), "PROVIDER_UNAVAILABLE")
    assert CANARY not in caplog.text
    for offset in [-1, 30]:
        clock = Mock(now=Mock(side_effect=[NOW, NOW + timedelta(seconds=offset)]))
        instance, _, _ = verifier(clock=clock)
        assert_blocked(instance.verify(7), "PROVIDER_UNAVAILABLE")


def test_arbitrary_transports_are_refused():
    with pytest.raises(ValueError, match="exact MockTransport"):
        PaperAccountVerifier(credentials=Mock(), clock=Mock(), expected_account_id=ACCOUNT,
                             transport=Mock(spec=httpx.BaseTransport))


def test_production_client_uses_only_fixed_isolated_paper_proxy(monkeypatch):
    reader = Mock()
    reader.load.return_value = PrivatePaperCredentials(7, CANARY, CANARY, ACCOUNT)
    client = Mock(side_effect=RuntimeError("synthetic stop before creating any network client"))
    monkeypatch.setattr("services.execution.paper_verification.httpx.Client", client)
    instance = PaperAccountVerifier(
        credentials=reader, clock=Mock(now=Mock(return_value=NOW)),
        expected_account_id=ACCOUNT, pinned_account_id=ACCOUNT,
    )
    assert_blocked(instance.verify(7), "PROVIDER_UNAVAILABLE")
    kwargs = client.call_args.kwargs
    assert kwargs["proxy"] == "http://paper-execution-egress:8080"
    assert kwargs["transport"] is None
    assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
    assert kwargs["timeout"].connect == kwargs["timeout"].read == 5.0
    assert CANARY not in repr(kwargs) and ACCOUNT not in repr(kwargs)
    with pytest.raises(TypeError):
        PaperAccountVerifier(credentials=reader, clock=Mock(), expected_account_id=ACCOUNT,
                             proxy="http://invalid.example:8080")


def test_slow_chunk_stream_cannot_extend_verification_deadline(monkeypatch):
    monkeypatch.setattr("services.execution.paper_verification.monotonic",
                        Mock(side_effect=[0, 0, 31]))
    instance, _, calls = verifier()
    assert_blocked(instance.verify(7), "PROVIDER_UNAVAILABLE")
    assert len(calls) == 1


def test_streamed_response_without_content_length_is_bounded():
    class OversizedStream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(20):
                yield b"x" * 4096
    instance, _, calls = verifier(lambda _r: httpx.Response(
        200, stream=OversizedStream(), headers={"content-type": "application/json"},
    ))
    assert_blocked(instance.verify(7), "PROVIDER_UNAVAILABLE")
    assert len(calls) == 1
