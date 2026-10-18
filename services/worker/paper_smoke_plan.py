"""One bounded, offline decision under the Worker DB role; never a broker client."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from packages.decisions.mock import MockDecisionEngine
from packages.domain import models as m
from packages.domain.paper_smoke import AUTH_TTL_SECONDS, SmokeRequest
from packages.observability.outbox import persist
from services.worker.planner import PlannerConfig, _insert_audited, plan_proposal


def plan_smoke(*, request: SmokeRequest, ledger, clock, ids):
    """Persist only the proposal bound to Execution's fresh immutable receipt."""
    now = m.utc_time(clock.now())
    with ledger.transaction() as tx:
        if tx.connection.execute(text("SELECT current_user")).scalar_one() != "atl_worker":
            raise ValueError("WORKER_ROLE_REQUIRED")
        # Serialize concurrent invocations of the same immutable run, without
        # granting Worker UPDATE rights on account/Execution-owned tables.
        tx.connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:run,0))"),
            {"run": request.entity_id},
        )
        receipts = tx.connection.execute(
            text("""
                SELECT id,reason_codes,occurred_at FROM public.paper_smoke_authorizations
                WHERE entity_id=:entity AND action='paper_smoke.authorize'
                AND actor_id='paper-smoke-execution' AND outcome='AUTHORIZED'
                AND trace_id=:trace AND occurred_at<=clock_timestamp()
                AND occurred_at>clock_timestamp()-make_interval(secs=>:ttl)
            """),
            {"entity": request.entity_id, "trace": request.trace_id, "ttl": AUTH_TTL_SECONDS},
        ).mappings().all()
        if len(receipts) != 1:
            raise ValueError("SMOKE_AUTHORIZATION_REQUIRED")
        receipt = receipts[0]
        if (
            tuple(receipt["reason_codes"]) != ("REQUEST_SHA256:" + request.digest(),)
            or not now - timedelta(seconds=AUTH_TTL_SECONDS) < receipt["occurred_at"] <= now
        ):
            raise ValueError("SMOKE_AUTHORIZATION_REQUIRED")
        state = tx.get(m.MarketState, request.market_state_id)
        if state is None or state.instrument_id != request.instrument_id:
            raise ValueError("SMOKE_STATE_MISMATCH")
        # No second decision after an abstention, and no mutation/repricing of a
        # persisted proposal. A retry only returns the original fresh result.
        existing = tx.get(m.DecisionResult, request.decision_id)
        if existing is not None:
            if existing.market_state_id != state.id or existing.valid_until <= now:
                raise ValueError("SMOKE_RUN_ALREADY_USED")
            prior = tx.get(m.TradeProposal, request.proposal_id)
            if prior is not None and (
                prior.limit_price != request.limit_price
                or prior.market_state_id != state.id
                or prior.valid_until <= now
            ):
                raise ValueError("SMOKE_RUN_ALREADY_USED")
            return prior
        decision = MockDecisionEngine(clock=clock, ids=ids).decide(state, "paper-smoke.v1")
        decision = replace(decision, id=request.decision_id)
        candidate = plan_proposal(
            decision=decision,
            market_state=state,
            config=PlannerConfig("paper-smoke-limit.v1", Decimal("1")),
            clock=clock,
            ids=ids,
        )
        # Never manufacture a LONG signal or close an existing position. The
        # existing baseline may abstain, in which case the smoke ends safely.
        proposal = None
        if candidate is not None and candidate.target is m.Target.LONG:
            if candidate.quantity != Decimal("1"):
                raise ValueError("SMOKE_QUANTITY_INVALID")
            proposal = replace(
                candidate, id=request.proposal_id, limit_price=request.limit_price,
                rationale_codes=(*candidate.rationale_codes, "PAPER_SMOKE_LIMIT"),
            )
        _insert_audited(tx, decision, "decision.persist", now, ids)
        if proposal is not None:
            _insert_audited(tx, proposal, "proposal.persist", now, ids)
        for entity, event_type in (
            (decision, "decision.completed.v1"), (proposal, "trade.proposed.v1")
        ):
            if entity is not None:
                persist(
                    tx.connection,
                    m.EventEnvelope(
                        ids.new_id("paper-smoke-event", entity.id), event_type, now,
                        entity.id, 1, entity.trace_id, entity,
                    ),
                )
        return proposal


def main(argv=None):
    parser = argparse.ArgumentParser(description="Human-only bounded Paper decision (no orders)")
    parser.add_argument("--paper-only", action="store_true")
    parser.add_argument("--operator-confirmed", action="store_true")
    for argument in (
        "run-id", "account-fingerprint", "instrument", "limit-price", "max-notional",
        "market-state-id",
    ):
        parser.add_argument("--" + argument, required=True)
    args = parser.parse_args(argv)
    engine = None
    try:
        if (
            not args.paper_only or not args.operator_confirmed or sys.platform != "linux"
            or not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr))
            or os.tcgetpgrp(sys.stdin.fileno()) != os.getpgrp()
        ):
            raise ValueError("INTERACTIVE_PAPER_OPERATOR_REQUIRED")
        request = SmokeRequest(
            args.run_id, "paper", args.account_fingerprint, args.instrument,
            args.limit_price, args.max_notional, args.market_state_id,
        )
        if input("Confirm offline Worker plan: type PLAN " + request.run_id + ": ") != (
            "PLAN " + request.run_id
        ):
            raise ValueError("OPERATOR_CONFIRMATION_REQUIRED")
        from packages.persistence import PostgresLedger
        from services.runtime.common import Clock, Ids
        from services.runtime.config import database_engine

        engine = database_engine("worker")
        proposal = plan_smoke(
            request=request, ledger=PostgresLedger(engine=engine), clock=Clock(), ids=Ids()
        )
        print(proposal.id if proposal is not None else "SMOKE_ABSTAIN_NO_PROPOSAL")
        return 0 if proposal is not None else 2
    except (Exception, KeyboardInterrupt):
        print("SMOKE_PLAN_ABORTED", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
