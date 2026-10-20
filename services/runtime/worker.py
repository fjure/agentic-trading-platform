"""Worker composition: durable decisions only, without broker capability."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from packages.domain import models as m
from packages.observability.outbox import persist
from packages.persistence.ledger import LedgerUnit
from services.worker.jobs import JobStore
from services.worker.planner import PlannerConfig, plan_proposal

from .common import Clock, Ids
from .config import secret_fields


class _JobLedger:
    def __init__(self, tx):
        self.tx = tx

    @contextmanager
    def transaction(self):
        yield self.tx


class WorkerRuntime:
    def __init__(self, config, ledger, telemetry=None):
        self.config, self.ledger, self.telemetry = config, ledger, telemetry
        self.clock, self.ids = Clock(), Ids()
        self.jobs = JobStore(ledger.engine)
        self.owner = uuid.uuid4().hex
        self.planner = PlannerConfig("runtime-planner-v1", Decimal("1"))
        if config.decision_provider == "jev":
            from packages.decisions.jev import JevDecisionEngine, load_config

            setup_path = Path("/run/provider/jev/credentials.json")
            value = secret_fields(
                setup_path if setup_path.exists() else Path("/run/secrets/jev"), {"api_key"})
            self.decision = JevDecisionEngine(
                clock=self.clock,
                ids=self.ids,
                api_key=value["api_key"],
                config=replace(load_config(), enabled=True),
                egress_proxy=True,
            )
        else:
            from packages.decisions.mock import MockDecisionEngine

            self.decision = MockDecisionEngine(clock=self.clock, ids=self.ids)

    def recovery(self, connection, job_id, input_ref):
        # Read-only proof that this deterministic input is durable and this job
        # cannot own any broker or reservation effects. No external call occurs.
        row = connection.execute(
            text("""
            SELECT j.type,s.id FROM public.jobs j
            JOIN public.market_states s ON s.id=j.input_ref
            WHERE j.id=:job AND j.input_ref=:input
            AND j.type='decision'
            AND NOT EXISTS(SELECT 1 FROM public.order_intents i WHERE i.id=j.id)
        """),
            {"job": job_id, "input": input_ref},
        ).first()
        return "market-state:" + input_ref if row else None

    def enqueue_latest(self):
        with self.ledger.transaction() as tx:
            tx.connection.execute(
                text("""
                INSERT INTO public.jobs(id,type,input_ref,trace_id)
                SELECT 'decision:' || s.id,'decision',s.id,s.trace_id
                FROM public.market_states s
                WHERE s.instrument_id=ANY(CAST(:instruments AS text[]))
                AND s.as_of > clock_timestamp()-interval '120 seconds'
                AND s.quality='VALID'
                AND NOT EXISTS(SELECT 1 FROM public.market_states n
                    WHERE n.instrument_id=s.instrument_id AND n.as_of>s.as_of)
                ON CONFLICT(id) DO NOTHING
            """),
                {"instruments": list(self.config.instruments)},
            )

    def tick(self):
        self.enqueue_latest()
        claim = self.jobs.claim(self.owner, verify_recovery=self.recovery)
        if claim is None:
            return "READY"
        try:
            if claim.job_type != "decision":
                self.jobs.fail(claim, "UNSUPPORTED_JOB_TYPE")
                return "DECISION_UNAVAILABLE"
            # External decisions execute outside the DB transaction. Heartbeat
            # and the final fenced transaction independently reject lease loss.
            with self.ledger.transaction() as tx:
                state = tx.get(m.MarketState, claim.input_ref)
                prior = tx.connection.execute(
                    text("""SELECT id FROM public.decisions
                    WHERE market_state_id=:state AND config_version=:config LIMIT 1"""),
                    {"state": claim.input_ref, "config": "runtime-decision-v1"},
                ).scalar_one_or_none()
            if state is None:
                raise ValueError("MARKET_STATE_MISSING")
            result = None if prior else self.decision.decide(state, "runtime-decision-v1")
            if not self.jobs.heartbeat(claim):
                raise ValueError("LEASE_LOST")
            with self.jobs.transaction(claim) as connection:
                tx = LedgerUnit(connection)
                if result is not None:
                    proposal = plan_proposal(
                        decision=result,
                        market_state=state,
                        config=self.planner,
                        clock=self.clock,
                        ids=self.ids,
                        ledger=_JobLedger(tx),
                    )
                    for entity, event_type in (
                        (result, "decision.completed.v1"),
                        (proposal, "trade.proposed.v1"),
                    ):
                        if entity is not None:
                            persist(
                                connection,
                                m.EventEnvelope(
                                    self.ids.new_id("event", entity.id),
                                    event_type,
                                    self.clock.now(),
                                    entity.id,
                                    1,
                                    entity.trace_id,
                                    entity,
                                ),
                            )
                    tx.insert(
                        m.AgentRun(
                            self.ids.new_id("run", claim.job_id),
                            result.input_hash,
                            "runtime-decision-v1",
                            state.id,
                            "DONE",
                            self.clock.now(),
                            result.id,
                            result.trace_id,
                        )
                    )
            if result is not None and result.reason_code == "ABSTAIN_UNAVAILABLE":
                if self.telemetry:
                    self.telemetry.update_health("DECISION", "UNAVAILABLE", "DECISION_UNAVAILABLE")
                    self.telemetry.observe("jev_error_count", 1, "DECISION", "UNAVAILABLE")
                return "DECISION_UNAVAILABLE"
            if result is not None and self.telemetry:
                self.telemetry.update_health("DECISION", "OK", "OK")
                self.telemetry.observe("jev_latency_seconds", result.latency_ms / 1000, "DECISION")
                if result.input_tokens is not None:
                    self.telemetry.observe("jev_usage", result.input_tokens, "DECISION")
            return "READY"
        except Exception:
            self.jobs.fail(claim, "DECISION_UNAVAILABLE")
            return "DECISION_UNAVAILABLE"
