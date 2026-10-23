"""Submit orchestration boundaries with synthetic committed state and no provider I/O."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from packages.domain import models as m
from services.execution import paper_submit as submit
from services.execution.gates import SubmitDenied
from tests.contracts.test_alpaca_paper import BROKER_ID, NOW, intent


class Crash(BaseException):
    pass


@pytest.fixture
def world(monkeypatch):
    order_intent = intent()
    state = SimpleNamespace(in_tx=False, durable=None, latch="RUNNING", posts=0, now=NOW,
                            proofs=0, finalizes=0, events=[], commit_error=False, commit_at=None)
    proposal = SimpleNamespace(id="proposal-1", valid_until=NOW+timedelta(seconds=30))
    decision = SimpleNamespace(valid_until=NOW+timedelta(seconds=30))
    reservation = object()
    capability = object()
    proof_bundle = object()

    class Tx:
        connection = object()

        def get(self, model, identity):
            return order_intent if model is m.OrderIntent else state.durable

        def lock_account(self, account):
            assert state.in_tx and account == "paper"

    class Ledger:
        @contextmanager
        def transaction(self):
            assert not state.in_tx
            state.in_tx = True
            try:
                yield Tx()
            finally:
                state.in_tx = False
            if state.commit_error and state.durable is not None:
                raise RuntimeError("SYNTHETIC_COMMIT_UNCERTAIN")
            if state.commit_at is not None and state.durable is not None:
                state.now = state.commit_at

    def gate(**kwargs):
        assert state.in_tx
        if state.latch != "RUNNING":
            raise SubmitDenied("PAPER_ACTIVATION_INVALID")
        if state.durable is not None:
            raise SubmitDenied("RECONCILE_REQUIRED")
        return order_intent, proposal, decision, reservation, capability, NOW

    def start(tx, **kwargs):
        assert state.in_tx and kwargs["intent"] is order_intent
        state.durable = "SUBMITTING"
        state.events.append("attempt")

    def finish(tx, **kwargs):
        assert state.in_tx and state.durable == "SUBMITTING"
        state.finalizes += 1
        state.durable = kwargs["status"]
        state.events.append("reply")
        return kwargs["status"]

    class Binding:
        def collect_submit_proofs(self, symbol):
            assert not state.in_tx and symbol == "SPY"
            state.proofs += 1
            state.events.append("proofs")
            return proof_bundle

        def validate_submit(self, tx, current, cap, now, *, collected_proofs):
            assert state.in_tx and current is order_intent and cap is capability
            assert collected_proofs is proof_bundle

    class Broker:
        before_callback = None
        after_callback = None

        def _now(self):
            assert not state.in_tx
            return state.now

        def submit(self, current, *, before_io):
            assert not state.in_tx and current is order_intent
            state.events.append("preflight")
            if self.before_callback:
                self.before_callback()
            before_io()
            assert not state.in_tx and state.durable == "SUBMITTING"
            state.posts += 1
            state.events.append("post")
            if self.after_callback:
                return self.after_callback()
            return m.BrokerOrder(
                id="synthetic-order", intent_id=current.id, client_order_id=current.client_order_id,
                broker_order_id=BROKER_ID, status=m.OrderStatus.ACKNOWLEDGED,
                requested_quantity=current.quantity, filled_quantity=m.decimal_value("0"),
                version=1, updated_at=NOW, trace_id=current.trace_id,
            )

    monkeypatch.setattr(submit, "_adapter_id", lambda *_: "alpaca-paper")
    monkeypatch.setattr(submit, "is_isolated_paper", lambda _: True)
    monkeypatch.setattr(submit, "evaluate_submit_gate", gate)
    monkeypatch.setattr(submit, "read_activation", lambda *_, **__: (
        SimpleNamespace(expires_at=NOW+timedelta(seconds=30))
        if state.latch == "RUNNING" else None
    ))
    monkeypatch.setattr(submit, "_now", lambda _: NOW)
    monkeypatch.setattr(submit, "_start_attempt", start)
    monkeypatch.setattr(submit, "_finish", finish)
    monkeypatch.setattr(submit, "_assert_owner", lambda *_: None)
    state.broker = Broker()
    state.call = lambda: submit.submit_paper_once(
        ledger=Ledger(), account_id="paper", intent_id=order_intent.id, limits=object(),
        owner_id="owner", fencing_token=1, broker=state.broker, binding=Binding(),
    )
    state.proposal = proposal
    return state


def test_attempt_commits_before_post_and_all_network_work_runs_without_transaction(world):
    assert world.call() is m.OrderStatus.ACKNOWLEDGED
    assert world.events == ["proofs", "preflight", "attempt", "post", "reply"]
    assert world.posts == world.finalizes == 1


def test_kill_during_final_preflight_prevents_attempt_and_post(world):
    world.broker.before_callback = lambda: setattr(world, "latch", "HALTED")
    with pytest.raises(SubmitDenied, match="PAPER_ACTIVATION_INVALID"):
        world.call()
    assert world.durable is None and world.posts == world.finalizes == 0


def test_expired_proposal_after_fresh_preflight_is_not_attempted(world):
    world.proposal.valid_until = NOW
    with pytest.raises(SubmitDenied, match="PROPOSAL_EXPIRED"):
        world.call()
    assert world.durable is None and world.posts == 0


def test_kill_while_post_waits_does_not_hold_ledger_transaction(world):
    def halted_reply():
        assert not world.in_tx and world.durable == "SUBMITTING"
        world.latch = "HALTED"
        raise TimeoutError("synthetic network timeout")

    world.broker.after_callback = halted_reply
    assert world.call() is m.OrderStatus.UNKNOWN
    assert world.latch == "HALTED" and world.posts == 1


def test_crash_after_post_preserves_attempt_and_reentry_cannot_read_or_send_again(world):
    def crash():
        raise Crash()

    world.broker.after_callback = crash
    with pytest.raises(Crash):
        world.call()
    assert world.durable == "SUBMITTING" and world.finalizes == 0
    with pytest.raises(SubmitDenied, match="RECONCILE_REQUIRED"):
        world.call()
    assert world.proofs == world.posts == 1


def test_ambiguous_commit_prevents_post_and_durable_marker_blocks_reentry(world):
    world.commit_error = True
    with pytest.raises(RuntimeError, match="SYNTHETIC_COMMIT_UNCERTAIN"):
        world.call()
    assert world.durable == "SUBMITTING" and world.posts == 0
    with pytest.raises(SubmitDenied, match="RECONCILE_REQUIRED"):
        world.call()
    assert world.proofs == 1 and world.finalizes == 0


def test_existing_provider_order_without_callback_requires_reconciliation(world):
    world.broker.submit = lambda *_, **__: None
    with pytest.raises(SubmitDenied, match="RECONCILE_REQUIRED"):
        world.call()
    assert world.durable is None and world.posts == world.finalizes == 0


def test_reply_with_wrong_intent_cannot_be_acknowledged(world):
    original = world.broker.submit

    def changed(*args, **kwargs):
        return replace(original(*args, **kwargs), intent_id="other-intent")

    world.broker.submit = changed
    assert world.call() is m.OrderStatus.UNKNOWN
    assert world.posts == 1


def test_expiry_while_committing_attempt_prevents_post_and_preserves_marker(world):
    world.commit_at = NOW+timedelta(seconds=30)
    with pytest.raises(submit.PaperAttemptExpired, match="PAPER_ATTEMPT_EXPIRED"):
        world.call()
    assert world.durable == "SUBMITTING" and world.posts == world.finalizes == 0
    with pytest.raises(SubmitDenied, match="RECONCILE_REQUIRED"):
        world.call()
    assert world.proofs == 1
