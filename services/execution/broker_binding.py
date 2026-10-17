"""Execution-only, durable binding of Alpaca Paper facts to one risk context."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import text

from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider
from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
from packages.brokers.fake import FakeBrokerAdapter
from packages.brokers.proofs import BindingProof
from packages.domain import models as m
from packages.persistence.ledger import PostgresLedger
from services.execution.gates import SubmitDenied

_NAMESPACE = uuid.UUID("827cf475-b347-4ee9-993d-13e940773b7d")
_MAX_AGE = timedelta(seconds=120)
_ACTOR = "execution-broker-binding"


def adapter_id(broker: object) -> str:
    if type(broker) is FakeBrokerAdapter:
        return "fake"
    if type(broker) is AlpacaPaperBrokerAdapter:
        return "alpaca-paper"
    raise SubmitDenied("BROKER_ADAPTER_UNSUPPORTED")


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SubmitDenied(reason)


def _utc(value: datetime) -> datetime:
    try:
        return m.utc_time(value)
    except (TypeError, ValueError):
        raise SubmitDenied("BINDING_TIME_INVALID") from None


def _fresh(proof: BindingProof, now: datetime) -> bool:
    return (
        proof.session_start <= proof.provider_time < proof.session_end
        and proof.session_start <= proof.observed_at < proof.session_end
        and proof.session_start <= now < proof.session_end
        and -timedelta(seconds=5) <= proof.observed_at - proof.provider_time < _MAX_AGE
        and timedelta() <= now - proof.observed_at < _MAX_AGE
        and -timedelta(seconds=5) <= now - proof.provider_time < _MAX_AGE
    )


def _audit(tx, entity_id: str, version: int, trace_id: str, action: str, now: datetime) -> None:
    tx.insert(
        m.AuditEvent(
            id=str(uuid.uuid5(_NAMESPACE, f"{action}:{entity_id}:{version}:{trace_id}")),
            occurred_at=now,
            actor_id=_ACTOR,
            action=action,
            reason_codes=("PROVIDER_PROOF_CONFIRMED",),
            entity_id=entity_id,
            entity_version=version,
            trace_id=trace_id,
            outcome="CONFIRMED",
        )
    )


class AlpacaExecutionBinding:
    """The only trusted Alpaca route into the durable Paper risk context."""

    def __init__(
        self,
        ledger: PostgresLedger,
        account_id: str,
        broker: AlpacaPaperBrokerAdapter,
        market_provider: AlpacaMarketDataProvider,
    ) -> None:
        if (
            type(ledger) is not PostgresLedger
            or type(broker) is not AlpacaPaperBrokerAdapter
            or type(market_provider) is not AlpacaMarketDataProvider
            or not isinstance(account_id, str)
            or not account_id
        ):
            raise ValueError("exact execution-owned binding components required")
        self._ledger = ledger
        self._account_id = account_id
        self._broker = broker
        self._market_provider = market_provider
        self._restored = False

    @property
    def ledger(self) -> PostgresLedger:
        return self._ledger

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def broker(self) -> AlpacaPaperBrokerAdapter:
        return self._broker

    @property
    def restored(self) -> bool:
        return self._restored

    def mark_restored(self) -> None:
        """Called only after durable SQL attempt restore succeeds."""
        self._restored = True

    def _proofs(self, instrument_id: str):
        try:
            market, source_proof = self._market_provider.binding_proof(instrument_id)
            trading, trade_proof = self._broker.binding_proof(instrument_id)
        except Exception:
            raise SubmitDenied("BINDING_PROOF_UNAVAILABLE") from None
        _require(
            type(source_proof) is BindingProof and type(trade_proof) is BindingProof,
            "BINDING_PROOF_INVALID",
        )
        _require(
            type(trading) is m.BrokerCapabilities and trading.broker_id == "alpaca-paper",
            "BINDING_CAPABILITY_INVALID",
        )
        _require(
            source_proof.instrument_id == trade_proof.instrument_id == instrument_id
            and source_proof.provider_account_id == trade_proof.provider_account_id
            and source_proof.provider_asset_id == trade_proof.provider_asset_id
            and source_proof.session_start == trade_proof.session_start
            and source_proof.session_end == trade_proof.session_end
            and market.instrument.id == instrument_id
            and market.capability_version == source_proof.capability_version
            and market.source_capabilities.history is m.Capability.CONFIRMED
            and trading.instrument_id == instrument_id
            and trading.version == trade_proof.capability_version
            and trading.paper is m.Capability.CONFIRMED
            and trading.market_order is m.Capability.CONFIRMED
            and trading.limit_order is m.Capability.CONFIRMED
            and trading.quantity_step is not None
            and trading.price_tick is not None
            and (
                trading.continuous_session
                if instrument_id == "BTC/USD"
                else trading.regular_session
            )
            is m.Capability.CONFIRMED,
            "BINDING_PROOF_MISMATCH",
        )
        return market, source_proof, trading, trade_proof

    def publish(self, instrument_id: str, trace_id: str) -> m.BrokerCapabilities:
        """Refresh both independent providers, then publish audited SQL provenance."""
        with self._ledger.transaction() as tx:
            c = tx.connection
            _require(
                c.execute(text("SELECT current_user")).scalar_one() == "atl_execution",
                "EXECUTION_ROLE_REQUIRED",
            )
            tx.lock_account(self._account_id)
            account = c.execute(
                text("SELECT currency FROM public.accounts WHERE id=:account"),
                {"account": self._account_id},
            ).scalar_one()
            _require(
                account == "USD"
                and c.execute(text("SELECT count(*) FROM public.accounts")).scalar_one() == 1,
                "ACCOUNT_MISMATCH",
            )
            market, source, trading, trade = self._proofs(instrument_id)
            now = _utc(c.execute(text("SELECT clock_timestamp()")).scalar_one())
            _require(_fresh(source, now) and _fresh(trade, now), "BINDING_PROOF_STALE")
            account_pin = c.execute(
                text(
                    "SELECT provider_account_id FROM public.broker_account_bindings "
                    "WHERE account_id=:account"
                ),
                {"account": self._account_id},
            ).scalar_one_or_none()
            _require(
                account_pin is None or str(account_pin) == trade.provider_account_id,
                "PROVIDER_ACCOUNT_CHANGED",
            )
            if account_pin is None:
                _audit(
                    tx, f"{self._account_id}:provider-account", 1, trace_id, "binding.account", now
                )
                c.execute(
                    text("""
                    INSERT INTO public.broker_account_bindings
                      (account_id,provider_account_id,actor_id,trace_id)
                    VALUES (:account,:provider,:actor,:trace)
                """),
                    {
                        "account": self._account_id,
                        "provider": trade.provider_account_id,
                        "actor": _ACTOR,
                        "trace": trace_id,
                    },
                )
            asset_pin = c.execute(
                text("""
                SELECT provider_asset_id FROM public.broker_asset_bindings
                WHERE account_id=:account AND instrument_id=:instrument
            """),
                {"account": self._account_id, "instrument": instrument_id},
            ).scalar_one_or_none()
            _require(
                asset_pin is None or str(asset_pin) == trade.provider_asset_id,
                "PROVIDER_ASSET_CHANGED",
            )
            if asset_pin is None:
                _audit(
                    tx,
                    f"{self._account_id}:{instrument_id}:provider-asset",
                    1,
                    trace_id,
                    "binding.asset",
                    now,
                )
                c.execute(
                    text("""
                    INSERT INTO public.broker_asset_bindings
                      (account_id,instrument_id,provider_asset_id,actor_id,trace_id)
                    VALUES (:account,:instrument,:provider,:actor,:trace)
                """),
                    {
                        "account": self._account_id,
                        "instrument": instrument_id,
                        "provider": trade.provider_asset_id,
                        "actor": _ACTOR,
                        "trace": trace_id,
                    },
                )
            prior = (
                c.execute(
                    text("""
                SELECT version,broker_id,capability_version FROM public.risk_instrument_context
                WHERE account_id=:account AND instrument_id=:instrument FOR UPDATE
            """),
                    {"account": self._account_id, "instrument": instrument_id},
                )
                .mappings()
                .one_or_none()
            )
            previous_source = (
                None
                if prior is None
                else c.execute(
                    text("""
                SELECT source_capability_version FROM public.broker_binding_proofs
                WHERE account_id=:account AND instrument_id=:instrument
                  AND context_version=:version
            """),
                    {
                        "account": self._account_id,
                        "instrument": instrument_id,
                        "version": prior["version"],
                    },
                ).scalar_one_or_none()
            )
            invalidate_recovery = (
                account_pin is None
                or asset_pin is None
                or prior is None
                or prior["broker_id"] != "alpaca-paper"
                or prior["capability_version"] != trading.version
                or previous_source != source.capability_version
            )
            version = 1 if prior is None else prior["version"] + 1
            valid_until = min(
                market.valid_until,
                source.observed_at + _MAX_AGE,
                trade.observed_at + _MAX_AGE,
                source.provider_time + _MAX_AGE,
                trade.provider_time + _MAX_AGE,
                source.session_end,
            )
            _require(now < valid_until, "BINDING_PROOF_STALE")
            proof_id = f"{self._account_id}:{instrument_id}:proof"
            context_id = f"{self._account_id}:{instrument_id}"
            _audit(tx, proof_id, version, trace_id, "binding.proof", now)
            _audit(tx, context_id, version, trace_id, "binding.context", now)
            params = {
                "account": self._account_id,
                "instrument": instrument_id,
                "version": version,
                "trading": trading.version,
                "source": source.capability_version,
                "provider_account": trade.provider_account_id,
                "provider_asset": trade.provider_asset_id,
                "start": source.session_start,
                "end": source.session_end,
                "market_observed": source.observed_at,
                "trade_observed": trade.observed_at,
                "market_time": source.provider_time,
                "trade_time": trade.provider_time,
                "valid": valid_until,
                "actor": _ACTOR,
                "trace": trace_id,
            }
            c.execute(
                text("""
                INSERT INTO public.broker_binding_proofs
                  (account_id,instrument_id,context_version,trading_capability_version,
                   source_capability_version,provider_account_id,provider_asset_id,
                   session_start,session_end,market_observed_at,trade_observed_at,
                   market_provider_time,trade_provider_time,valid_until,actor_id,trace_id)
                VALUES (:account,:instrument,:version,:trading,:source,
                        :provider_account,:provider_asset,:start,:end,
                        :market_observed,:trade_observed,:market_time,:trade_time,
                        :valid,:actor,:trace)
            """),
                params,
            )
            c.execute(
                text("""
                INSERT INTO public.risk_instrument_context
                  (account_id,instrument_id,version,capability_version,broker_id,
                   paper_confirmed,regular_session_confirmed,continuous_session_confirmed,
                   market_order_confirmed,limit_order_confirmed,fractional_confirmed,
                   quantity_step,price_tick,session_open,session_version,
                   session_valid_until,valid_until,actor_id,trace_id)
                VALUES (:account,:instrument,:version,:trading,'alpaca-paper',
                        true,:regular,:continuous,:market_order,:limit_order,:fractional,
                        :step,:tick,true,:session_version,:end,:valid,:actor,:trace)
                ON CONFLICT (account_id,instrument_id) DO UPDATE SET
                  version=EXCLUDED.version,
                  capability_version=EXCLUDED.capability_version,
                  broker_id=EXCLUDED.broker_id,
                  paper_confirmed=EXCLUDED.paper_confirmed,
                  regular_session_confirmed=EXCLUDED.regular_session_confirmed,
                  continuous_session_confirmed=EXCLUDED.continuous_session_confirmed,
                  market_order_confirmed=EXCLUDED.market_order_confirmed,
                  limit_order_confirmed=EXCLUDED.limit_order_confirmed,
                  fractional_confirmed=EXCLUDED.fractional_confirmed,
                  quantity_step=EXCLUDED.quantity_step,
                  price_tick=EXCLUDED.price_tick,
                  session_open=EXCLUDED.session_open,
                  session_version=EXCLUDED.session_version,
                  session_valid_until=EXCLUDED.session_valid_until,
                  valid_until=EXCLUDED.valid_until,
                  actor_id=EXCLUDED.actor_id,
                  trace_id=EXCLUDED.trace_id
            """),
                {
                    **params,
                    "regular": trading.regular_session is m.Capability.CONFIRMED,
                    "continuous": trading.continuous_session is m.Capability.CONFIRMED,
                    "market_order": trading.market_order is m.Capability.CONFIRMED,
                    "limit_order": trading.limit_order is m.Capability.CONFIRMED,
                    "fractional": trading.fractional is m.Capability.CONFIRMED,
                    "step": trading.quantity_step,
                    "tick": trading.price_tick,
                    "session_version": source.session_start.isoformat(),
                },
            )
            if invalidate_recovery:
                # A prior Fake/inventory check is not authority for another provider
                # or capability. Reuse the audited latch and typed outbox atomically.
                c.execute(
                    text("""
                    SELECT public.safety_latch(
                        :account,'PAUSE_NEW',:actor,:trace,'HEALTH_CHECK_FAILED')
                """),
                    {"account": self._account_id, "actor": _ACTOR, "trace": trace_id},
                )
            return trading

    def validate_submit(
        self,
        tx,
        intent: m.OrderIntent,
        expected_capability: m.BrokerCapabilities,
        now: datetime,
    ) -> None:
        """Recheck every private identity and capability under caller's account lock."""
        c = tx.connection
        _require(
            c.execute(text("SELECT current_user")).scalar_one() == "atl_execution",
            "EXECUTION_ROLE_REQUIRED",
        )
        _require(
            type(intent) is m.OrderIntent and type(expected_capability) is m.BrokerCapabilities,
            "BINDING_INTENT_INVALID",
        )
        now = _utc(now)
        owner = c.execute(
            text("""
            SELECT account_id FROM public.order_intents WHERE id=:intent
        """),
            {"intent": intent.id},
        ).scalar_one_or_none()
        _require(owner == self._account_id, "ACCOUNT_MISMATCH")
        self._validate_context(tx, intent.instrument_id, expected_capability, now)

    def validate_recovery(self, tx, *, now: datetime) -> int:
        """Fresh dual-provider proof for every configured instrument under the gate."""
        c = tx.connection
        _require(
            c.execute(text("SELECT current_user")).scalar_one() == "atl_execution",
            "EXECUTION_ROLE_REQUIRED",
        )
        _require(self.restored, "BINDING_RESTORE_REQUIRED")
        tx.lock_account(self._account_id)
        instruments = (
            c.execute(
                text("""
            SELECT instrument_id FROM public.risk_instrument_context
            WHERE account_id=:a ORDER BY instrument_id FOR UPDATE
        """),
                {"a": self._account_id},
            )
            .scalars()
            .all()
        )
        _require(bool(instruments), "BINDING_CONTEXT_CHANGED")
        for instrument_id in instruments:
            try:
                capability = self._broker.capabilities(instrument_id)
            except Exception:
                raise SubmitDenied("BINDING_PROOF_UNAVAILABLE") from None
            self._validate_context(tx, instrument_id, capability, now)
        return len(instruments)

    def _validate_context(
        self, tx, instrument_id: str, expected_capability: m.BrokerCapabilities, now: datetime
    ) -> None:
        c = tx.connection
        context = (
            c.execute(
                text("""
            SELECT * FROM public.risk_instrument_context
            WHERE account_id=:account AND instrument_id=:instrument FOR UPDATE
        """),
                {"account": self._account_id, "instrument": instrument_id},
            )
            .mappings()
            .one_or_none()
        )
        _require(
            context is not None
            and context["broker_id"] == "alpaca-paper"
            and context["capability_version"] == expected_capability.version
            and context["valid_until"] > now,
            "BINDING_CONTEXT_CHANGED",
        )
        proof = (
            c.execute(
                text("""
            SELECT * FROM public.broker_binding_proofs
            WHERE account_id=:account AND instrument_id=:instrument
              AND context_version=:version
        """),
                {
                    "account": self._account_id,
                    "instrument": instrument_id,
                    "version": context["version"],
                },
            )
            .mappings()
            .one_or_none()
        )
        _require(proof is not None and proof["valid_until"] > now, "BINDING_PROOF_MISSING")
        account_pin = c.execute(
            text("""
            SELECT provider_account_id FROM public.broker_account_bindings
            WHERE account_id=:account
        """),
            {"account": self._account_id},
        ).scalar_one_or_none()
        asset_pin = c.execute(
            text("""
            SELECT provider_asset_id FROM public.broker_asset_bindings
            WHERE account_id=:account AND instrument_id=:instrument
        """),
            {"account": self._account_id, "instrument": instrument_id},
        ).scalar_one_or_none()
        _require(account_pin is not None and asset_pin is not None, "BINDING_PIN_MISSING")
        market, source, trading, trade = self._proofs(instrument_id)
        final_now = _utc(c.execute(text("SELECT clock_timestamp()")).scalar_one())
        _require(
            market.valid_until > final_now
            and _fresh(source, final_now)
            and _fresh(trade, final_now),
            "BINDING_PROOF_STALE",
        )
        _require(
            trading == expected_capability
            and trading.version == expected_capability.version
            and source.capability_version == proof["source_capability_version"]
            and trade.capability_version == proof["trading_capability_version"]
            and str(account_pin)
            == str(proof["provider_account_id"])
            == source.provider_account_id
            == trade.provider_account_id
            and str(asset_pin)
            == str(proof["provider_asset_id"])
            == source.provider_asset_id
            == trade.provider_asset_id
            and proof["session_start"] == source.session_start == trade.session_start
            and proof["session_end"] == source.session_end == trade.session_end
            and proof["valid_until"] > final_now
            and context["session_valid_until"] == source.session_end
            and context["session_open"] is True
            and context["paper_confirmed"] is True
            and context["regular_session_confirmed"]
            == (trading.regular_session is m.Capability.CONFIRMED)
            and context["continuous_session_confirmed"]
            == (trading.continuous_session is m.Capability.CONFIRMED)
            and context["market_order_confirmed"]
            == (trading.market_order is m.Capability.CONFIRMED)
            and context["limit_order_confirmed"] == (trading.limit_order is m.Capability.CONFIRMED)
            and context["fractional_confirmed"] == (trading.fractional is m.Capability.CONFIRMED)
            and context["quantity_step"] == trading.quantity_step
            and context["price_tick"] == trading.price_tick,
            "BINDING_PROOF_CHANGED",
        )
