"""Isolated Paper composition; configuration and restart never authorize orders."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from threading import Lock

from sqlalchemy import text

from packages.domain import models as m
from packages.observability.outbox import Outbox
from packages.risk.engine import RiskLimits
from packages.risk.reservations import reserve
from services.control_api.paper_commands import PaperCommandError, PaperCommands

from .common import Clock, Ids
from .execution import ExecutionRuntime
from .paper_verification import PaperVerificationRuntime


@dataclass(frozen=True)
class PaperOwner:
    owner_id: str
    fencing_token: int


class PaperRuntimeCoordinator:
    # Reuse the existing normalized publication path without its eager legacy
    # constructor or static trading configuration.
    publish_market = ExecutionRuntime.publish_market
    _spread = staticmethod(ExecutionRuntime._spread)

    def __init__(self, config, ledger, *, clock=None, reader_factory=None,
                 broker_factory=None, market_factory=None, daily_factory=None):
        if config.deployment != "paper":
            raise ValueError("PAPER_RUNTIME_REQUIRED")
        self.config, self.ledger = config, ledger
        self.clock, self.ids, self.limits = clock or Clock(), Ids(), RiskLimits()
        self.commands = PaperCommands(ledger.engine)
        self.reader_factory, self.broker_factory = reader_factory, broker_factory
        self.market_factory, self.daily_factory = market_factory, daily_factory
        self.process = self.binding = self.gateway = self.broker = self.market_provider = None
        self._component_key, self._faulted = None, False
        self._owner_faulted = False
        self._manual_pending = None
        self._cycle = Lock()
        self.outbox = Outbox(ledger.engine)
        self.verifier = PaperVerificationRuntime(
            commands=self.commands, reader_factory=reader_factory, clock=self.clock,
            start_handler=self.start,
            manual_handler=self.manual_order,
        )

    def _read(self):
        return self.commands._call("SELECT public.paper_activation_read()", {})

    def _ensure_owner(self):
        if self.process is None:
            from services.execution.recovery_health import claim_execution_owner

            owner = str(uuid.uuid4())
            fence = claim_execution_owner(ledger=self.ledger, account_id="paper",
                                          owner_id=owner, trace_id=uuid.uuid4().hex)
            if self.commands._call("SELECT public.paper_restart_close(:owner,:fence)",
                                   {"owner": owner, "fence": fence}) is not True:
                raise ValueError("PAPER_RESTART_FENCED")
            self.process = PaperOwner(owner, fence)

    def heartbeat_tick(self):
        if self._owner_faulted:
            return "UNAVAILABLE"
        owner = self.process
        if owner is not None:
            from services.execution.recovery_health import heartbeat_execution_owner

            try:
                heartbeat_execution_owner(ledger=self.ledger, account_id="paper",
                                          owner_id=owner.owner_id,
                                          fencing_token=owner.fencing_token)
            except Exception:
                self._faulted = True
                self._owner_faulted = True
                raise
        return "UNAVAILABLE" if self._faulted else "READY"

    def command_tick(self):
        # Establish the restart fence before claiming a new command. Claiming
        # START first and then changing epoch would invalidate that same request.
        with self._cycle:
            if self._read() is not None:
                self._ensure_owner()
        return self.verifier.tick()

    def _components(self, context):
        self._ensure_owner()
        authority = context.get("recoveryFeeAuthority")
        fee = authority.get("profile") if isinstance(authority, dict) else None
        key = (context["credentialGeneration"], context["accountGeneration"],
               context["profileVersion"], fee)
        if self._component_key == key:
            return
        from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider
        from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
        from services.execution.broker_binding import AlpacaExecutionBinding
        from services.execution.broker_recovery import restore_alpaca_attempts
        from services.execution.market_gateway import MarketGateway
        from services.execution.paper_credentials import PaperGenerationReader

        reader = self.reader_factory() if self.reader_factory else PaperGenerationReader()
        private = reader.load(context["credentialGeneration"])
        if private.expected_account_id != context["pinnedAccountId"]:
            raise ValueError("ACCOUNT_IDENTITY_MISMATCH")
        market = (self.market_factory or AlpacaMarketDataProvider)(
            self.clock, self.ids, api_key=private.api_key, api_secret=private.api_secret,
            enabled=True, egress_proxy=self.market_factory is None, proxy_deployment="paper",
        )
        broker = (self.broker_factory or AlpacaPaperBrokerAdapter)(
            self.clock, self.ids, api_key=private.api_key, api_secret=private.api_secret,
            expected_account_id=private.expected_account_id, paper_enabled=True,
            egress_proxy=self.broker_factory is None, proxy_deployment="paper",
            reconciliation_fee_profile=fee,
        )
        del private
        binding = AlpacaExecutionBinding(self.ledger, "paper", broker, market)
        try:
            restore_alpaca_attempts(
                ledger=self.ledger, account_id="paper", broker=broker, binding=binding,
                owner_id=self.process.owner_id, fencing_token=self.process.fencing_token,
            )
        except Exception:
            broker.close()
            market.close()
            raise
        self._close_components()
        self.reader, self.broker = reader, broker
        self.market_provider, self.binding = market, binding
        self.gateway = MarketGateway(provider=market, clock=self.clock)
        self._component_key = key

    def _close_components(self):
        for component in (self.broker, self.market_provider):
            if component is not None:
                component.close()
        self.broker = self.market_provider = self.binding = self.gateway = None
        self._component_key = None

    def close(self):
        self._faulted = True
        self._owner_faulted = True
        if self.process is not None:
            try:
                self.commands._call("SELECT public.paper_restart_close(:owner,:fence)",
                                    {"owner": self.process.owner_id,
                                     "fence": self.process.fencing_token})
            except Exception:
                pass  # Fresh SQL submit gates and independent watchdog still fence.
        self._close_components()

    def _refresh(self, context):
        self._components(context)
        from services.execution.paper_daily_refresh import PaperDailyRefreshCollector
        from services.execution.paper_reconciliation import (
            collect_paper_reconciliation,
            reconcile_paper_account,
        )
        # Market failure cannot skip reconciliation. Both acquire their provider
        # observations before opening a financial SQL transaction.
        market_ok = True
        for instrument in self.config.instruments:
            try:
                self.binding.publish(instrument, uuid.uuid4().hex)
            except Exception:
                market_ok = False
        collected = collect_paper_reconciliation(
            ledger=self.ledger, account_id="paper", broker=self.broker, binding=self.binding,
        )
        receipt = reconcile_paper_account(
            ledger=self.ledger, account_id="paper", broker=self.broker, binding=self.binding,
            owner_id=self.process.owner_id, fencing_token=self.process.fencing_token,
            collected=collected,
        )
        if not receipt.ready:
            raise PaperCommandError(409, "RECONCILIATION_REQUIRED")
        reference = self.commands._call("SELECT public.paper_daily_reference()", {})
        collector = (self.daily_factory or PaperDailyRefreshCollector)(
            credentials=self.reader, clock=self.clock,
            expected_account_id=context["pinnedAccountId"],
            pinned_account_id=context["pinnedAccountId"],
        )
        observation = collector.collect(context["credentialGeneration"], reference)
        if self.commands._call(
            "SELECT public.paper_daily_append(:owner,:fence,:portfolio,CAST(:body AS jsonb))",
            {"owner": self.process.owner_id, "fence": self.process.fencing_token,
             "portfolio": receipt.portfolio_version, "body": json.dumps(observation)},
        ) is not True:
            raise PaperCommandError(409, "RECONCILIATION_REQUIRED")
        if not market_ok or not self.publish_market():
            raise PaperCommandError(409, "MARKET_DATA_UNAVAILABLE")
        return receipt

    def start(self, claim, command_owner):
        payload = claim["payload"]
        try:
            expiry = m.utc_time(datetime.fromisoformat(payload["expiresAt"].replace("Z", "+00:00")))
        except (ValueError, TypeError, KeyError):
            raise PaperCommandError(400, "INVALID_REQUEST") from None
        if self.clock.now() >= expiry:
            raise PaperCommandError(409, "APPROVAL_EXPIRED")
        with self._cycle:
            context = self._read()
            if not context or context["profileVersion"] < 1 or self._faulted:
                raise PaperCommandError(409, "LEDGER_NOT_PREPARED")
            receipt = self._refresh(context)
            result = self.commands._call(
                "SELECT public.paper_activation_begin(CAST(:id AS uuid),CAST(:owner AS uuid),"
                ":fence,CAST(:proof AS jsonb))",
                {"id": claim["operationId"], "owner": command_owner, "fence": claim["fence"],
                 "proof": json.dumps({"executionOwnerId": self.process.owner_id,
                                      "executionFencingToken": self.process.fencing_token,
                                      "reconciliationReceiptId": receipt.receipt_id})},
            )
            try:
                if (type(result) is not dict or set(result) != {"activationId", "expiresAt"}
                        or str(uuid.UUID(result["activationId"])) != result["activationId"]
                        or m.utc_time(datetime.fromisoformat(
                            result["expiresAt"].replace("Z", "+00:00"))) != expiry):
                    raise ValueError()
            except (ValueError, TypeError, KeyError, AttributeError):
                raise PaperCommandError(409, "RECONCILIATION_REQUIRED") from None

    def manual_order(self, claim, command_owner):
        from .paper_manual import run_manual_order

        return run_manual_order(self, claim, command_owner)

    def recovery_tick(self):
        with self._cycle:
            context = self._read()
            if context is None:
                # No credential read, adapter construction or provider request.
                return "READY"
            if self._owner_faulted:
                return "UNAVAILABLE"
            self._ensure_owner()
            if context["profileVersion"] < 1:
                return "READY"
            # Consume still-current work before publishing a new binding/quote
            # version. Otherwise every periodic refresh invalidates proposals
            # made by Worker between two Execution ticks before Risk sees them.
            if not self._faulted and context.get("activation") is not None:
                self._process_current_work()
            try:
                self._refresh(context)
            except Exception:
                # SQL watchdog and per-reservation/submit guards remain
                # independent, including while a provider is waiting or down.
                if context.get("activation") is not None:
                    self.commands._call("SELECT public.paper_restart_close(:owner,:fence)",
                                        {"owner": self.process.owner_id,
                                         "fence": self.process.fencing_token})
                # Recovery can establish an order outcome even when fresh
                # market data for new entries is unavailable afterwards.
                self._finish_manual_pending()
                return "DATA_UNAVAILABLE"
            current = self._read()
            if not self._faulted and current and current.get("activation") is not None:
                self._process_current_work()
            self._finish_manual_pending()
            for _ in range(100):
                if self.outbox.publish_one(lambda _event: None) is None:
                    break
            return "UNAVAILABLE" if self._faulted else "READY"

    def _finish_manual_pending(self):
        pending = self._manual_pending
        if pending is not None and self.commands._call(
            "SELECT public.paper_human_finish(CAST(:id AS uuid),CAST(:owner AS uuid),:fence)",
            pending,
        ) is True:
            self._manual_pending = None

    def _process_current_work(self):
        try:
            self._risk_and_submit()
        except Exception:
            self._faulted = True
            self.commands._call("SELECT public.paper_restart_close(:owner,:fence)",
                                {"owner": self.process.owner_id,
                                 "fence": self.process.fencing_token})
            # Keep the owner heartbeat and reconciliation running, while this
            # process permanently refuses further entry attempts.

    def _risk_and_submit(self):
        from services.execution.gates import SubmitDenied
        from services.execution.paper_submit import submit_paper_once

        with self.ledger.transaction() as tx:
            proposals = tx.connection.execute(text("""
                SELECT p.id FROM public.trade_proposals p WHERE p.valid_until>clock_timestamp()
                AND NOT EXISTS(SELECT 1 FROM public.risk_decisions r WHERE r.proposal_id=p.id)
                ORDER BY p.created_at,p.id LIMIT 20
            """)).scalars().all()
        for proposal in proposals:
            reserve(ledger=self.ledger, account_id="paper", proposal_id=proposal,
                    limits=self.limits)
        with self.ledger.transaction() as tx:
            intents = tx.connection.execute(text("""
                SELECT i.id FROM public.order_intents i
                JOIN public.risk_decisions r ON r.id=i.risk_decision_id
                WHERE i.account_id='paper' AND r.valid_until>clock_timestamp()
                AND NOT EXISTS(SELECT 1 FROM public.broker_orders b WHERE b.intent_id=i.id)
                ORDER BY i.created_at,i.id LIMIT 20
            """)).scalars().all()
        for intent in intents:
            try:
                submit_paper_once(
                    ledger=self.ledger, account_id="paper", intent_id=intent, limits=self.limits,
                    broker=self.broker, binding=self.binding, owner_id=self.process.owner_id,
                    fencing_token=self.process.fencing_token,
                )
            except SubmitDenied:
                pass
