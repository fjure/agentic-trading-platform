"""Execution-only broker composition and independently scheduled safety work."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

from sqlalchemy import text

from packages.domain import models as m
from packages.observability.outbox import Outbox, persist
from packages.risk.engine import RiskLimits
from packages.risk.reservations import reserve
from services.control_api.control import ControlDenied
from services.execution.intents import ExecutionProcess, SubmitDenied

from .common import Clock, Ids
from .config import secret_fields


class ExecutionRuntime:
    def __init__(self, config, ledger, telemetry=None):
        self.config, self.ledger, self.telemetry = config, ledger, telemetry
        self.clock, self.ids, self.limits = Clock(), Ids(), RiskLimits()
        self.gateway = self.binding = self.market_provider = None
        if config.broker_provider == "alpaca-paper":
            # This branch is reachable only from explicit operator configuration.
            # Neither startup validation nor tests invoke it with real secrets.
            from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider
            from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
            from services.execution.broker_binding import AlpacaExecutionBinding
            from services.execution.market_gateway import MarketGateway

            credentials = secret_fields(
                Path("/run/secrets/alpaca"), {"api_key", "api_secret", "expected_account_id"}
            )
            self.market_provider = AlpacaMarketDataProvider(
                self.clock,
                self.ids,
                api_key=credentials["api_key"],
                api_secret=credentials["api_secret"],
                enabled=True,
                egress_proxy=True,
            )
            broker = AlpacaPaperBrokerAdapter(
                self.clock,
                self.ids,
                api_key=credentials["api_key"],
                api_secret=credentials["api_secret"],
                expected_account_id=credentials["expected_account_id"],
                paper_enabled=True,
                egress_proxy=True,
                reconciliation_fee_profile=config.reconciliation_fee_profile,
            )
            self.binding = AlpacaExecutionBinding(
                ledger, config.account_id, broker, self.market_provider
            )
            self.gateway = MarketGateway(provider=self.market_provider, clock=self.clock)
        else:
            from packages.brokers.fake import FakeBrokerAdapter

            # No fake price, session, balance, capability or holdings is promoted
            # into production evidence. Offline fixtures live in tests only.
            broker = FakeBrokerAdapter(
                clock=self.clock,
                ids=self.ids,
                capabilities={},
                session_open=lambda _instrument, _moment: False,
            )
        self.process = ExecutionProcess(
            ledger=ledger,
            account_id=config.account_id,
            limits=self.limits,
            broker=broker,
            binding=self.binding,
            paper_smoke_enabled=config.paper_enabled,
        )
        self.outbox = Outbox(ledger.engine)

    def publish_market(self):
        if self.gateway is None:
            return False
        from services.worker.market_state import PaperMarketConfig, build_market_state

        config = PaperMarketConfig.load()
        published = 0
        for instrument in self.config.instruments:
            session = self.clock.now().astimezone(config.zone).date().isoformat()
            feed = self.gateway.read(instrument, session, require_spread=True)
            self.binding.publish(instrument, uuid.uuid4().hex)
            with self.ledger.transaction() as tx:
                snapshot_id = tx.connection.execute(
                    text("""
                    SELECT portfolio_snapshot_id FROM public.risk_account_context
                    WHERE account_id=:account AND reconciliation_current
                """),
                    {"account": self.config.account_id},
                ).scalar_one_or_none()
                position_id = tx.connection.execute(
                    text("""
                    SELECT id FROM public.positions
                    WHERE account_id=:account AND instrument_id=:instrument
                """),
                    {"account": self.config.account_id, "instrument": instrument},
                ).scalar_one_or_none()
                if not snapshot_id or not position_id:
                    continue
                portfolio = tx.get(m.PortfolioSnapshot, snapshot_id)
                position = tx.get(m.Position, position_id)
                source_config = replace(
                    config, source_id=feed.snapshot.source_capabilities.source_id
                )
                state = build_market_state(
                    config=source_config,
                    instrument_id=instrument,
                    bars=feed.snapshot.bars,
                    as_of=feed.snapshot.as_of,
                    # Gateway has no quote/spread authority. A missing spread
                    # MUST NOT be invented as zero or inferred from candles.
                    spread=self._spread(feed.snapshot),
                    portfolio=portfolio,
                    position=position,
                    trace_id=uuid.uuid4().hex,
                )
                for entity in (*feed.snapshot.bars, state.evidence, state):
                    if tx.get(type(entity), entity.id) is None:
                        tx.insert(entity)
                persist(
                    tx.connection,
                    m.EventEnvelope(
                        self.ids.new_id("market-event", state.id),
                        "market.state.v1",
                        self.clock.now(),
                        state.id,
                        1,
                        state.trace_id,
                        state,
                    ),
                )
                published += 1
        return published == len(self.config.instruments)

    @staticmethod
    def _spread(snapshot):
        if snapshot.spread is None or snapshot.quote_provider_time is None:
            raise SubmitDenied("MARKET_SPREAD_UNAVAILABLE")
        return snapshot.spread

    def recover_requests(self):
        with self.ledger.transaction() as tx:
            ids = (
                tx.connection.execute(
                    text("""
                SELECT r.id FROM public.safety_resume_requests r
                WHERE r.account_id=:account AND r.requested_at<=clock_timestamp()
                AND r.expires_at>clock_timestamp()
                AND NOT EXISTS(SELECT 1 FROM public.safety_health_evidence h
                    WHERE h.request_id=r.id)
                AND NOT EXISTS(SELECT 1 FROM public.audit_events a WHERE a.entity_id=r.id
                    AND a.action='safety.resume_denied')
                ORDER BY r.requested_at,r.id
            """),
                    {"account": self.config.account_id},
                )
                .scalars()
                .all()
            )
        for request_id in ids:
            try:
                self.process.recover(request_id)
            except ControlDenied:
                pass  # Existing recovery path durably audits one denial.

    def reconcile(self):
        if self.binding is not None:
            receipt = self.process.reconcile()
            if not receipt.ready:
                raise SubmitDenied("RECONCILIATION_REQUIRED")
            return receipt
        from services.execution.reconciliation import reconcile_order

        with self.ledger.transaction() as tx:
            ids = (
                tx.connection.execute(
                    text("""
                SELECT b.id FROM public.broker_orders b
                JOIN public.order_intents i ON i.id=b.intent_id
                WHERE i.account_id=:account AND b.adapter='fake'
                AND b.status NOT IN ('FILLED','REJECTED','CANCELED') ORDER BY b.id
            """),
                    {"account": self.config.account_id},
                )
                .scalars()
                .all()
            )
        for order_id in ids:
            result = reconcile_order(
                ledger=self.ledger,
                account_id=self.config.account_id,
                order_id=order_id,
                broker=self.process.broker,
            )
            if result.status is m.OrderStatus.UNKNOWN:
                raise SubmitDenied("BROKER_UNAVAILABLE")

    def cancel_after_halt(self):
        with self.ledger.transaction() as tx:
            halted = tx.connection.execute(
                text("""
                SELECT safety_state='HALTED' FROM public.risk_account_context
                WHERE account_id=:account
            """),
                {"account": self.config.account_id},
            ).scalar_one()
            clients = (
                tx.connection.execute(
                    text("""
                SELECT b.client_order_id FROM public.broker_orders b
                JOIN public.order_intents i ON i.id=b.intent_id
                WHERE i.account_id=:account
                AND b.status IN ('ACKNOWLEDGED','PARTIALLY_FILLED')
            """),
                    {"account": self.config.account_id},
                )
                .scalars()
                .all()
                if halted
                else []
            )
        if not halted:
            return
        if self.binding is None:
            from services.execution.recovery_health import cancel_known_after_halt

            cancel_known_after_halt(
                ledger=self.ledger,
                account_id=self.config.account_id,
                broker=self.process.broker,
                owner_id=self.process.owner_id,
                fencing_token=self.process.fencing_token,
            )
        else:
            for client_id in clients:
                try:
                    self.process.broker.cancel(client_id)
                except Exception:
                    pass  # A cancellation failure cannot undo the HALTED latch.

    def risk_and_submit(self):
        with self.ledger.transaction() as tx:
            proposals = (
                tx.connection.execute(
                    text("""
                SELECT p.id FROM public.trade_proposals p
                WHERE NOT EXISTS(SELECT 1 FROM public.risk_decisions r WHERE r.proposal_id=p.id)
                ORDER BY p.created_at,p.id LIMIT 20
            """)
                )
                .scalars()
                .all()
            )
        for proposal_id in proposals:
            reserve(
                ledger=self.ledger,
                account_id=self.config.account_id,
                proposal_id=proposal_id,
                limits=self.limits,
            )
        if not self.config.paper_enabled:
            return
        with self.ledger.transaction() as tx:
            intents = (
                tx.connection.execute(
                    text("""
                SELECT i.id FROM public.order_intents i
                JOIN public.risk_account_context c ON c.account_id=i.account_id
                WHERE i.account_id=:account AND c.mode='PAPER' AND c.safety_state='RUNNING'
                AND NOT EXISTS(SELECT 1 FROM public.broker_orders b WHERE b.intent_id=i.id)
                ORDER BY i.created_at,i.id LIMIT 20
            """),
                    {"account": self.config.account_id},
                )
                .scalars()
                .all()
            )
        for intent_id in intents:
            try:
                self.process.submit(intent_id)
            except SubmitDenied:
                pass  # Gate path records terminal pre-I/O denial; no retry loop.

    def tick(self):
        self.process.heartbeat()
        if getattr(self.process, "_faulted", False):
            self.pause_if_running("execution-storage-recovery")
        # Distinct stages: data/provider/decision failures cannot suppress
        # durable reconciliation, recovery checks or the independent watchdog.
        data_ok = False
        try:
            data_ok = self.publish_market()
        except Exception:
            if self.telemetry:
                self.telemetry.update_health("MARKET", "UNAVAILABLE", "MARKET_DATA_STALE")
        if not data_ok:
            self.pause_if_running("execution-market")
        self.cancel_after_halt()
        broker_ok = True
        try:
            self.reconcile()
        except Exception:
            broker_ok = False
            if self.telemetry:
                self.telemetry.update_health("BROKER", "UNAVAILABLE", "BROKER_UNAVAILABLE")
        # Even failed reconciliation must service explicit requests, whose own
        # independent checks durably deny unhealthy recovery.
        self.recover_requests()
        if broker_ok:
            self.risk_and_submit()
        for _ in range(100):
            # The public publish log is the delivery destination used by SSE.
            if self.outbox.publish_one(lambda _event: None) is None:
                break
        return "UNAVAILABLE" if not broker_ok else "READY" if data_ok else "DATA_UNAVAILABLE"

    def pause_if_running(self, actor):
        from services.control_api.control import pause

        with self.ledger.transaction() as tx:
            running = tx.connection.execute(
                text("""
                SELECT safety_state='RUNNING' FROM public.risk_account_context
                WHERE account_id=:account
            """),
                {"account": self.config.account_id},
            ).scalar_one()
        if running:
            pause(
                ledger=self.ledger,
                account_id=self.config.account_id,
                actor_id=actor,
                trace_id=uuid.uuid4().hex,
                reason_code="HEALTH_CHECK_FAILED",
            )
