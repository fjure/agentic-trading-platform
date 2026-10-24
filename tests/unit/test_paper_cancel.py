"""Synthetic fixed-host cancel adapter and independent service boundaries."""

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter, AlpacaPaperError
from packages.domain.models import OrderIntent
from services.execution.paper_cancel import CancelFenced, PaperCancelService

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
ACCOUNT = "10000000-0000-4000-8000-000000000001"
BROKER = "20000000-0000-4000-8000-000000000002"
WORKER = "30000000-0000-4000-8000-000000000003"
INTENT = OrderIntent(
    id="intent", proposal_id="proposal", risk_decision_id="risk", client_order_id="client",
    instrument_id="SPY", side="BUY", order_type="LIMIT", quantity=Decimal("2"),
    capability_version=1, created_at=NOW, trace_id="a" * 32, limit_price=Decimal("15"),
)


class Clock:
    def now(self):
        return NOW


class Ids:
    def new_id(self, namespace, seed=None):
        return namespace + ":" + str(seed)


class Provider:
    def __init__(self, status=204, *, terminal=False):
        self.status, self.terminal = status, terminal
        self.events = []
        self.account_reads = 0
        self.on_final_account = None
        self.client_id = "client"
        self.broker_id = BROKER
        self.checked_transaction = lambda: None

    def __call__(self, request):
        self.checked_transaction()
        assert request.url.host == "paper-api.alpaca.markets"
        self.events.append(request.method + " " + request.url.path)
        if request.url.path == "/v2/account":
            self.account_reads += 1
            if self.account_reads == 2 and self.on_final_account:
                self.on_final_account()
            return httpx.Response(200, json={
                "id": ACCOUNT, "status": "ACTIVE", "currency": "USD",
                "account_blocked": False, "trading_blocked": True,
                "transfers_blocked": False, "cash": "100000", "shorting_enabled": False,
            })
        if request.method == "GET":
            return httpx.Response(200, json={
                "id": self.broker_id, "client_order_id": self.client_id, "symbol": "SPY",
                "side": "buy", "type": "limit", "time_in_force": "day",
                "order_class": "simple", "qty": "2", "filled_qty": "2" if self.terminal else "0",
                "limit_price": "15", "status": "filled" if self.terminal else "new",
                "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
                "filled_avg_price": "15" if self.terminal else None,
            })
        assert request.method == "DELETE"
        if self.status == "timeout":
            raise httpx.ReadTimeout("private body must not escape", request=request)
        return httpx.Response(self.status, content=b"", headers={"Content-Type": "text/plain"})


def adapter(provider):
    broker = AlpacaPaperBrokerAdapter(
        Clock(), Ids(), api_key="synthetic-key", api_secret="synthetic-secret",
        paper_enabled=True, expected_account_id=ACCOUNT,
        transport=httpx.MockTransport(provider),
    )
    broker.bind_intent(INTENT)
    return broker


@pytest.mark.parametrize("status,kind,retry", [
    (204, "ACCEPTED", None), (429, "THROTTLED", 5), (422, "NOT_CANCELABLE", None),
    (500, "UNKNOWN", None), ("timeout", "UNKNOWN", None),
])
def test_structured_cancel_last_get_then_authorization_then_one_delete(status, kind, retry):
    provider = Provider(status)
    broker = adapter(provider)
    def observed(outcome):
        assert outcome.kind == "OBSERVED"
        assert outcome.as_record()["order"]["requestedQuantity"] == "2"
        provider.events.append("LOOKUP_COMMIT")

    def authorize():
        assert provider.account_reads == 2
        assert provider.events[-1] == "GET /v2/account"
        provider.events.append("DELETE_COMMIT")

    try:
        result = broker.cancel_with_outcome(
            "client", expected_broker_order_id=BROKER,
            on_lookup=observed, before_delete=authorize,
        )
        assert result.kind == kind
        assert result.delete_attempted is True
        assert result.retry_after_seconds == retry
        assert provider.events[-2:] == ["DELETE_COMMIT", "DELETE /v2/orders/" + BROKER]
        assert set(result.as_record()) == {
            "kind", "observedAt", "retryAfterSeconds", "deleteAttempted", "order",
        }
        assert "synthetic" not in repr(result)
    finally:
        broker.close()


@pytest.mark.parametrize("terminal,lookup_only", [(True, False), (False, True)])
def test_terminal_and_lookup_only_never_delete(terminal, lookup_only):
    provider = Provider(terminal=terminal)
    broker = adapter(provider)
    try:
        result = broker.cancel_with_outcome(
            "client", expected_broker_order_id=BROKER, on_lookup=lambda _: None,
            before_delete=lambda: pytest.fail("DELETE forbidden"), allow_delete=not lookup_only,
        )
        assert result.kind == "OBSERVED" and not result.delete_attempted
        assert all(not event.startswith("DELETE") for event in provider.events)
    finally:
        broker.close()


@pytest.mark.parametrize("error_type", [CancelFenced, AlpacaPaperError])
def test_callback_failure_cannot_be_swallowed_or_send_delete(error_type):
    provider = Provider()
    broker = adapter(provider)
    def reject():
        raise error_type() if error_type is CancelFenced else error_type("DENIED")
    try:
        with pytest.raises(error_type):
            broker.cancel_with_outcome("client", expected_broker_order_id=BROKER,
                                       on_lookup=lambda _: None, before_delete=reject)
        assert all(not event.startswith("DELETE") for event in provider.events)
    finally:
        broker.close()


@pytest.mark.parametrize("mismatch", ["client", "broker"])
def test_changed_provider_ownership_never_authorizes_delete(mismatch):
    provider = Provider()
    if mismatch == "client":
        provider.client_id = "foreign"
    else:
        provider.broker_id = "40000000-0000-4000-8000-000000000004"
    broker = adapter(provider)
    try:
        result = broker.cancel_with_outcome(
            "client", expected_broker_order_id=BROKER, on_lookup=lambda _: None,
            before_delete=lambda: pytest.fail("foreign order"),
        )
        assert result.kind == "UNKNOWN" and result.order is None
        assert all(not event.startswith("DELETE") for event in provider.events)
    finally:
        broker.close()


def test_final_identity_get_failure_cannot_commit_or_delete():
    provider = Provider()
    broker = adapter(provider)
    def unavailable():
        raise httpx.ReadTimeout("private", request=httpx.Request("GET", "https://invalid"))
    provider.on_final_account = unavailable
    try:
        result = broker.cancel_with_outcome(
            "client", expected_broker_order_id=BROKER, on_lookup=lambda _: None,
            before_delete=lambda: pytest.fail("identity not verified"),
        )
        assert result.kind == "NOT_SENT" and not result.delete_attempted
        assert all(not event.startswith("DELETE") for event in provider.events)
    finally:
        broker.close()


def test_crash_after_attempt_commit_sends_no_delete_and_propagates():
    provider = Provider()
    broker = adapter(provider)
    marker = []
    class Crash(BaseException):
        pass
    def uncertain_commit():
        marker.append("DURABLE_DELETE_ATTEMPT")
        raise Crash()
    try:
        with pytest.raises(Crash):
            broker.cancel_with_outcome(
                "client", expected_broker_order_id=BROKER, on_lookup=lambda _: None,
                before_delete=uncertain_commit,
            )
        assert marker == ["DURABLE_DELETE_ATTEMPT"]
        assert all(not event.startswith("DELETE") for event in provider.events)
    finally:
        broker.close()


class Ledger:
    depth = 0

    @contextmanager
    def transaction(self):
        self.depth += 1
        try:
            yield SimpleNamespace(get=lambda model, identity: INTENT)
        finally:
            self.depth -= 1


class Service(PaperCancelService):
    def __init__(self, provider, *, claim=True, deny_delete=False):
        ledger = Ledger()
        self.calls, self.reader_loads, self.instances = [], [], []
        self.has_claim, self.deny_delete = claim, deny_delete
        def factory(*args, **kwargs):
            assert ledger.depth == 0
            broker = AlpacaPaperBrokerAdapter(*args, **kwargs,
                                             transport=httpx.MockTransport(provider))
            self.instances.append(broker)
            return broker
        def load(generation):
            self.reader_loads.append(generation)
            assert "paper_cancel_begin_lookup" in self.calls
            return SimpleNamespace(api_key="synthetic", api_secret="synthetic",
                                   expected_account_id=ACCOUNT)
        super().__init__(ledger, reader_factory=lambda: SimpleNamespace(load=load),
                         broker_factory=factory, clock=Clock(), worker_id=WORKER)
        provider.checked_transaction = lambda: self._no_transaction()

    def _no_transaction(self):
        assert self.ledger.depth == 0

    def _call(self, statement, params):
        name = statement.split("public.")[1].split("(")[0]
        self.calls.append(name)
        if name == "paper_cancel_claim":
            return ({"cancelId": WORKER, "leaseFence": 1, "intentId": "intent",
                     "clientOrderId": "client", "brokerOrderId": BROKER, "lookupOnly": False}
                    if self.has_claim else None)
        if name == "paper_cancel_begin_lookup":
            return {"attemptId": "lookup", "credentialGeneration": 1, "pinnedAccountId": ACCOUNT}
        if name == "paper_cancel_begin_delete":
            return {"error": "DENIED"} if self.deny_delete else {"attemptId": "delete"}
        if name == "paper_cancel_record":
            self.calls.append(params["outcome"])
            return True
        assert name == "paper_cancel_finish"
        return False


def test_empty_queue_reads_no_credentials_and_builds_no_adapter():
    service = Service(Provider(), claim=False)
    assert service.tick(owner_id="owner", fencing_token=1) == "READY"
    assert service.calls == ["paper_cancel_claim"]
    assert not service.reader_loads and not service.instances


def test_service_commits_lookup_and_attempt_before_http_and_retains_pending():
    provider = Provider()
    service = Service(provider)
    assert service.tick(owner_id="owner", fencing_token=1) == "PENDING"
    assert service.calls.index("paper_cancel_begin_lookup") < service.calls.index(
        "paper_cancel_record") < service.calls.index("paper_cancel_begin_delete")
    assert any('"kind": "ACCEPTED"' in item for item in service.calls)
    assert service.instances[0]._client.is_closed


def test_delete_gate_denial_keeps_lookup_and_never_sends_delete():
    provider = Provider()
    service = Service(provider, deny_delete=True)
    assert service.tick(owner_id="owner", fencing_token=1) == "UNAVAILABLE"
    assert not any(item.startswith("DELETE") for item in provider.events)
    assert service.instances[0]._client.is_closed


def test_existing_authoritative_terminal_proof_finishes_without_credentials():
    class Finished(Service):
        def _call(self, statement, params):
            if "paper_cancel_finish(" in statement:
                self.calls.append("paper_cancel_finish")
                return True
            return super()._call(statement, params)
    provider = Provider()
    service = Finished(provider)
    assert service.tick(owner_id="owner", fencing_token=1) == "READY"
    assert service.calls == ["paper_cancel_claim", "paper_cancel_finish"]
    assert not service.reader_loads and not service.instances and not provider.events


def test_lookup_record_failure_prevents_remaining_io():
    class FailedRecord(Service):
        def _call(self, statement, params):
            if "paper_cancel_record(" in statement:
                return False
            return super()._call(statement, params)
    provider = Provider()
    service = FailedRecord(provider)
    assert service.tick(owner_id="owner", fencing_token=1) == "UNAVAILABLE"
    assert provider.account_reads == 1
    assert not any(item.startswith("DELETE") for item in provider.events)
    assert "paper_cancel_begin_delete" not in service.calls


def test_ambiguous_delete_commit_is_not_overwritten_with_a_safe_outcome():
    class Ambiguous(Service):
        def _call(self, statement, params):
            if "paper_cancel_begin_delete(" in statement:
                self.calls.append("DURABLE_DELETE_ATTEMPT")
                raise RuntimeError("commit reply lost")
            return super()._call(statement, params)
    provider = Provider()
    service = Ambiguous(provider)
    assert service.tick(owner_id="owner", fencing_token=1) == "UNAVAILABLE"
    assert service.calls[-1] == "DURABLE_DELETE_ATTEMPT"
    assert not any(item.startswith("DELETE") for item in provider.events)
    assert service.instances[0]._client.is_closed
