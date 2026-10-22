"""Actual isolated Paper roles, synthetic quote proofs and immutable LIMIT policy."""

from pathlib import Path

MARKER = "PASS: fixed Paper LIMIT policy, quotes, planner inputs and exact privileges"


def test_paper_limit_policy_postgres():
    from tests.integration.test_paper_provision import host

    assert host(Path(__file__), MARKER) == MARKER


def guest():
    import json
    import tempfile
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta, timezone
    from types import SimpleNamespace
    from unittest.mock import patch

    import psycopg
    from sqlalchemy import URL, create_engine, event, text

    from packages.domain.models import MarketState, TradeProposal
    from packages.persistence.ledger import LedgerUnit, PostgresLedger
    from packages.risk.paper_limits import paper_proposal_reasons
    from services.runtime.provision_paper import (
        DATABASE,
        _verify_dormant_privileges,
        provision_paper,
    )
    from services.runtime.worker import WorkerRuntime
    from services.worker.paper_planning import read_planning_inputs, read_registered_plan

    common = dict(host="postgres", user="lab_migration", password="synthetic-unused")
    with psycopg.connect(dbname="lab", autocommit=True, **common) as c:
        for name in ("lab_runtime", "lab_demo"):
            c.execute(f"CREATE DATABASE {name}")
            c.execute(f"REVOKE ALL ON DATABASE {name} FROM PUBLIC")
    with tempfile.TemporaryDirectory() as directory:
        provision_paper(common, Path(directory))
        provision_paper(common, Path(directory))
    with psycopg.connect(dbname=DATABASE, **common) as c:
        _verify_dormant_privileges(c)
        assert c.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0015_paper_limit_policy",
        )
        assert c.execute("SELECT count(*) FROM accounts").fetchone() == (0,)
        # Fixture owner installs only synthetic prepared state; tested runtime
        # operations below run with the exact fixed login and group identities.
        fixture_time = c.execute("SELECT transaction_timestamp()").fetchone()[0]
        c.execute(_SEED)
        c.commit()
        now = datetime.now(UTC)
        quote = dict(
            sourceId="alpaca-market-data:iex",
            bid="14.99",
            ask="15",
            providerTime=(now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            observedAt=now.isoformat().replace("+00:00", "Z"),
            sourceCapabilityVersion=1,
        )

        def role(name):
            c.execute("SET SESSION AUTHORIZATION DEFAULT")
            c.execute("RESET ROLE")
            c.execute(f"SET SESSION AUTHORIZATION atl_paper_{name}_login")
            c.execute(f"SET ROLE atl_paper_{name}")

        def append(value=quote, owner="synthetic-owner", fence=1, context=1):
            return c.execute(
                "SELECT public.paper_quote_append('state',%s,%s,%s,%s::jsonb)",
                (context, owner, fence, json.dumps(value)),
            ).fetchone()[0]

        role("worker")
        assert c.execute("SELECT public.paper_planner_inputs('state')").fetchone() == (None,)
        assert c.execute("SELECT public.paper_limit_policy()").fetchone()[0]["version"] == 1
        for statement in (
            "SELECT * FROM broker_binding_proofs",
            "SELECT * FROM broker_account_bindings",
            "SELECT * FROM paper_quote_observations",
            "SELECT * FROM paper_limit_plans",
            "UPDATE positions SET quantity=0",
            "INSERT INTO accounts VALUES('x','USD',1)",
            "CREATE TABLE forbidden(id int)",
            "CREATE TEMP TABLE forbidden(id int)",
            "SELECT public.paper_quote_append('state',1,'x',1,'{}')",
        ):
            with c.transaction(force_rollback=True):
                try:
                    c.execute(statement)
                except psycopg.errors.InsufficientPrivilege:
                    pass
                else:
                    raise AssertionError("worker escaped scoped SQL authority")
        role("execution")
        for owner, fence in (
            (None, 1),
            ("synthetic-owner", None),
            ("other", 1),
            ("synthetic-owner", 2),
        ):
            assert append(owner=owner, fence=fence) == {"error": "VERSION_CONFLICT"}
        for change in (
            {"secret": "synthetic-canary"},
            {"bid": 14},
            {"bid": "NaN"},
            {"sourceCapabilityVersion": None},
            {"sourceCapabilityVersion": 2},
            {"sourceId": "alpaca-market-data:sip"},
            {"providerTime": (now - timedelta(seconds=31)).isoformat()},
            {"ask": "14.98"},
        ):
            assert "error" in append(quote | change)
        first = append()
        assert set(first) == {"quoteId", "quoteHash"} and len(first["quoteHash"]) == 64
        assert append() == first
        assert append(quote | {"bid": "15.99", "ask": "16"}) == {"error": "VERSION_CONFLICT"}
        c.commit()
        role("worker")
        raw = c.execute("SELECT public.paper_planner_inputs('state')").fetchone()[0]
        assert raw is not None
        assert raw["bid"] == "14.99" and raw["ask"] == "15"
        assert raw["held_exposure"] == "0" and raw["instrument_exposure"] == "0"
        assert raw["held_quantity"] == "0" and raw["remaining_cash"] == "100000"
        assert raw["risk_budget"] == "1000" and raw["budget_version"] == 1
        assert raw["quote_evidence_hash"] == first["quoteHash"]
        assert raw["capability"]["fractional"] == "CONFIRMED"
        assert "11111111-1111-4111-8111-111111111111" not in json.dumps(raw)
        assert "22222222-2222-4222-8222-222222222222" not in json.dumps(raw)
        engine = create_engine(
            URL.create(
                "postgresql+psycopg",
                **{
                    "host": "postgres",
                    "username": "atl_paper_worker_login",
                    "database": DATABASE,
                },
            )
        )

        @event.listens_for(engine, "connect")
        def set_worker_role(connection, _record):
            with connection.cursor() as cursor:
                cursor.execute("SET ROLE atl_paper_worker")
            connection.commit()

        with engine.connect() as connection:
            assert read_planning_inputs(connection, "state") is not None
        c.execute("SET SESSION AUTHORIZATION DEFAULT")
        c.execute("RESET ROLE")
        for statement in (
            "UPDATE paper_limit_policies SET version=2",
            "DELETE FROM paper_quote_observations",
            "TRUNCATE paper_quote_observations",
        ):
            with c.transaction(force_rollback=True):
                try:
                    c.execute(statement)
                except psycopg.errors.RaiseException:
                    pass
                else:
                    raise AssertionError("immutable policy/quote changed")
        for mutation in (
            "UPDATE risk_account_context SET reconciliation_current=false,version=2",
            "UPDATE paper_workflow_state SET pending_generation=2",
        ):
            with c.transaction(force_rollback=True):
                c.execute(
                    "INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,"
                    "entity_id,entity_version,trace_id) VALUES(gen_random_uuid()::text,"
                    "clock_timestamp(),'fixture','fixture','[\"SYNTHETIC_FIXTURE\"]',"
                    "'paper',2,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"
                )
                c.execute(mutation)
                role("worker")
                assert c.execute("SELECT public.paper_planner_inputs('state')").fetchone() == (
                    None,
                )
            c.execute("SET SESSION AUTHORIZATION DEFAULT")
            c.execute("RESET ROLE")
        _verify_dormant_privileges(c)
        assert c.execute("SELECT mode,safety_state FROM risk_account_context").fetchone() == (
            "DISABLED",
            "PAUSE_NEW",
        )
        # Synthetic venue timezone places DB UTC now at local noon; this tests
        # SQL/worker plumbing, not real exchange opening or calendar authority.
        synthetic_zone = timezone(
            timedelta(hours=12)
            - timedelta(
                hours=fixture_time.hour,
                minutes=fixture_time.minute,
                seconds=fixture_time.second,
                microseconds=fixture_time.microsecond,
            )
        )
        c.commit()
        runtime = WorkerRuntime(
            SimpleNamespace(
                deployment="paper",
                account_id="paper",
                instruments=("SPY",),
                decision_provider="mock",
            ),
            PostgresLedger(engine=engine),
        )

        def expose_synthetic_failure(*_args):
            import sys

            raise sys.exception()

        with (
            patch("services.worker.paper_limit._EASTERN", synthetic_zone),
            patch.object(
                runtime.jobs,
                "fail",
                side_effect=expose_synthetic_failure,
            ),
        ):
            assert runtime.tick() == "READY"
        assert c.execute("SELECT status FROM jobs").fetchall() == [("DONE",)]
        proposal = c.execute(
            "SELECT CASE WHEN limit_price IS NOT NULL THEN 'LIMIT' ELSE 'MARKET' END,"
            "limit_price,quantity,rationale_codes "
            "FROM trade_proposals"
        ).fetchone()
        assert proposal and proposal[0] == "LIMIT" and proposal[1] == 15
        assert proposal[2] > 0 and any(
            code.startswith("PAPER_INPUT_SHA256:") for code in proposal[3]
        )
        for name in ("order_intents", "broker_orders", "risk_reservations", "risk_decisions"):
            assert c.execute(f"SELECT count(*) FROM {name}").fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM outbox_events").fetchone() == (2,)
        proposal_id = c.execute("SELECT id FROM trade_proposals").fetchone()[0]
        registered = c.execute("SELECT input_hash,inputs FROM paper_limit_plans").fetchone()
        assert registered and registered[1]["quote_evidence_hash"] == first["quoteHash"]
        role("execution")
        original = c.execute("SELECT public.paper_limit_plan_read(%s)", (proposal_id,)).fetchone()[
            0
        ]
        assert original == {"inputHash": registered[0], "inputs": registered[1]}
        assert append() == first
        c.execute("SET SESSION AUTHORIZATION DEFAULT")
        c.execute("RESET ROLE")
        for statement in ("UPDATE paper_limit_plans SET input_hash=repeat('a',64)",
                          "DELETE FROM paper_limit_plans", "TRUNCATE paper_limit_plans"):
            with c.transaction(force_rollback=True):
                try:
                    c.execute(statement)
                except psycopg.errors.RaiseException:
                    pass
                else:
                    raise AssertionError("immutable original planner proof changed")
        role("worker")
        assert c.execute("SELECT public.paper_planner_record(%s,%s,'decision:state','other',1)",
                         (proposal_id, registered[0])).fetchone() == (False,)
        c.execute("SET SESSION AUTHORIZATION DEFAULT")
        c.execute("RESET ROLE")
        execution_engine = create_engine(
            URL.create(
                "postgresql+psycopg",
                **{
                    "host": "postgres",
                    "username": "atl_paper_execution_login",
                    "database": DATABASE,
                },
            )
        )

        @event.listens_for(execution_engine, "connect")
        def set_execution_role(connection, _record):
            with connection.cursor() as cursor:
                cursor.execute("SET ROLE atl_paper_execution")
            connection.commit()

        with execution_engine.connect() as connection:
            admitted = read_registered_plan(
                connection, SimpleNamespace(id=proposal_id, market_state_id="state")
            )
            assert admitted is not None and admitted.digest() == registered[0]
            tx = LedgerUnit(connection)
            actual_proposal = tx.get(TradeProposal, proposal_id)
            actual_state = tx.get(MarketState, "state")
            db_now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
            with patch("services.worker.paper_limit._EASTERN", synthetic_zone):
                assert paper_proposal_reasons(connection, tx, actual_proposal,
                                              actual_state, db_now) == ()
                assert paper_proposal_reasons(connection, tx, replace(actual_proposal,
                    quantity=actual_proposal.quantity+1), actual_state, db_now)
                assert paper_proposal_reasons(connection, tx, replace(actual_proposal,
                    rationale_codes=tuple(code for code in actual_proposal.rationale_codes
                        if not code.startswith("PAPER_INPUT_SHA256:"))), actual_state, db_now)
        execution_engine.dispose()
        # The immutable policy remains .10; a new tighter risk policy yields a
        # bounded effective projection without rewriting either durable policy.
        with c.transaction(force_rollback=True):
            c.execute(
                "INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,"
                "entity_id,entity_version,trace_id) VALUES('risk-2-audit',clock_timestamp(),"
                "'fixture','fixture','[\"SYNTHETIC_FIXTURE\"]','risk-2',2,"
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"
            )
            c.execute(
                "INSERT INTO risk_policies SELECT 'risk-2',2,currency,"
                "max_positions_per_instrument,max_positions_total,.05,max_daily_loss_fraction,"
                "clock_timestamp(),'fixture','risk-2-audit',simulation FROM risk_policies "
                "WHERE version=1"
            )
            role("worker")
            effective = c.execute("SELECT paper_planner_inputs('state')").fetchone()[0]
            assert effective["policy"]["max_position_fraction"] == "0.05"
            assert effective["policy_version"] == 2
            assert (
                c.execute("SELECT paper_limit_policy()").fetchone()[0]["max_position_fraction"]
                == "0.10"
            )
        c.execute("SET SESSION AUTHORIZATION DEFAULT")
        c.execute("RESET ROLE")
        # A new admitted decision state without an Execution quote must produce
        # decision evidence only, never a legacy MARKET fallback proposal.
        c.execute(
            "INSERT INTO market_states SELECT 'state-missing-quote',instrument_id,market,"
            "bar_id,evidence_id,portfolio_id,position_id,features,spread,event_time,received_at,"
            "available_at,clock_timestamp(),quality,trace_id,position_ledger_version "
            "FROM market_states WHERE id='state'"
        )
        c.commit()
        with (
            patch("services.worker.paper_limit._EASTERN", synthetic_zone),
            patch.object(
                runtime.jobs,
                "fail",
                side_effect=expose_synthetic_failure,
            ),
        ):
            assert runtime.tick() == "READY"
        assert c.execute("SELECT count(*) FROM decisions").fetchone() == (2,)
        assert c.execute("SELECT count(*) FROM trade_proposals").fetchone() == (1,)
        assert c.execute("SELECT count(*) FROM jobs WHERE status='DONE'").fetchone() == (2,)
        outcomes = c.execute(
            "SELECT reason_codes FROM audit_events WHERE action='paper.plan_outcome' "
            "ORDER BY occurred_at,id"
        ).fetchall()
        assert outcomes == [(["PAPER_LIMIT_PLANNED"],), (["PAPER_PROOF_MISSING"],)]
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT paper_planner_inputs('state-missing-quote')")
                ).scalar_one_or_none()
                is None
            )
        # Preserve conservative monetary evidence at the domain's 18-place
        # boundary even for legal q9 quantities multiplied by 18-place marks.
        with c.transaction(force_rollback=True):
            c.execute(_FRACTIONAL)
            role("execution")
            fractional_quote = quote | {"bid": "101.11", "ask": "101.12"}
            observed = c.execute(
                "SELECT paper_quote_append('state-fractional',1,'synthetic-owner',1,%s::jsonb)",
                (json.dumps(fractional_quote),),
            ).fetchone()[0]
            assert set(observed) == {"quoteId", "quoteHash"}
            raw_fractional = c.execute(
                "SELECT paper_planner_inputs('state-fractional')"
            ).fetchone()[0]
            assert raw_fractional["instrument_exposure"] == "12.484377267751714679"
            assert raw_fractional["held_exposure"] == "12.484377267751714679"
            from services.worker.paper_planning import decode_planning_inputs

            assert decode_planning_inputs(raw_fractional, "state-fractional") is not None
        c.execute("SET SESSION AUTHORIZATION DEFAULT")
        c.execute("RESET ROLE")
        engine.dispose()
    print(MARKER)


_SEED = """
INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
 entity_version,trace_id)
 SELECT 'fixture-audit-'||entity,transaction_timestamp(),'fixture','fixture',
 '["SYNTHETIC_FIXTURE"]',entity,1,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
 FROM unnest(ARRAY['paper','position','portfolio','risk-policy','paper:SPY',
 'paper:provider-account','paper:SPY:provider-asset','paper:SPY:proof']) entity;

INSERT INTO accounts VALUES('paper','USD',1);
INSERT INTO instruments(id,market,broker_symbol,asset_class,currency,version,session_name)
 VALUES('SPY','US_EQUITY','SPY','ETF','USD',1,'XNYS');
INSERT INTO positions VALUES('position','SPY','USD',0,NULL,0,1,'paper');
INSERT INTO portfolio_snapshots VALUES('portfolio','USD',100000,100000,0,
 transaction_timestamp(),1,'VALID','CURRENT');
INSERT INTO evidence_bundles
 VALUES('evidence','SPY','["alpaca-market-data:iex"]',
 transaction_timestamp(),'[]','VALID',true);
INSERT INTO market_bars VALUES('bar','SPY',transaction_timestamp()-interval '15 minutes',
 transaction_timestamp(),15,15,15,15,100,transaction_timestamp(),transaction_timestamp(),
 transaction_timestamp(),
 'VALID','PT15M',true,1);
INSERT INTO market_states VALUES('state','SPY','US_EQUITY','bar','evidence','portfolio','position',
 '{"previous_close":"14","last_close":"15","window_volume":"100"}',0.01,
 transaction_timestamp(),transaction_timestamp(),transaction_timestamp(),transaction_timestamp(),
 'VALID','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',1);
INSERT INTO risk_policies VALUES('risk-policy',1,'USD',1,2,.10,.01,transaction_timestamp(),
 'migration','paper-limit-policy-1-audit',true);
INSERT INTO risk_account_context VALUES('paper',1,'portfolio',1,'paper-risk-v1',
 transaction_timestamp(),100900,0,'DISABLED','PAUSE_NEW',true,transaction_timestamp()+interval
 '100 seconds',
 'fixture','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
INSERT INTO broker_account_bindings VALUES('paper','11111111-1111-4111-8111-111111111111',
 'fixture','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',transaction_timestamp());
INSERT INTO broker_asset_bindings VALUES('paper','SPY','22222222-2222-4222-8222-222222222222',
 'fixture','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',transaction_timestamp());
INSERT INTO broker_binding_proofs VALUES('paper','SPY',1,1,1,
 '11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222',
 transaction_timestamp()-interval '2 hours 30 minutes',transaction_timestamp()+interval '4 hours',
 transaction_timestamp(),transaction_timestamp(),transaction_timestamp()-interval '1 second',
 transaction_timestamp()-interval '1 second',transaction_timestamp()+interval '100 seconds',
 'fixture','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
INSERT INTO risk_instrument_context(account_id,instrument_id,version,capability_version,broker_id,
 paper_confirmed,regular_session_confirmed,continuous_session_confirmed,market_order_confirmed,
 limit_order_confirmed,fractional_confirmed,quantity_step,price_tick,session_open,session_version,
 session_valid_until,valid_until,actor_id,trace_id) VALUES('paper','SPY',1,1,'alpaca-paper',
 true,true,false,true,true,true,.000000001,.01,true,'session',
 transaction_timestamp()+interval '4 hours',transaction_timestamp()+interval '100 seconds',
 'fixture','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
INSERT INTO execution_owners
 VALUES('paper','synthetic-owner',1,transaction_timestamp(),transaction_timestamp(),
 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
UPDATE paper_workflow_state SET saved_generation=1,active_generation=1,account_generation=1,
 credential_counter=1,verified_at=transaction_timestamp();
INSERT INTO trading_profiles VALUES('paper',1000,'USD','neutral-baseline','neutral-baseline.v1',1,
 transaction_timestamp(),'fixture','paper-limit-policy-1-audit');
"""


_FRACTIONAL = """
INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
 entity_version,trace_id)
 SELECT 'fraction-audit-'||entity,transaction_timestamp(),'fixture','fixture',
 '["SYNTHETIC_FIXTURE"]',entity,2,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
 FROM unnest(ARRAY['paper','position','fractional-portfolio']) entity;
UPDATE positions SET quantity=.123456789,average_price=101.12,ledger_version=2
 WHERE id='position';
UPDATE accounts SET ledger_version=2 WHERE id='paper';
INSERT INTO portfolio_snapshots VALUES('fractional-portfolio','USD',
 100012.484377267751714679,100000,12.484377267751714679,
 transaction_timestamp(),2,'VALID','CURRENT');
UPDATE risk_account_context SET version=2,portfolio_snapshot_id='fractional-portfolio',
 portfolio_version=2,actor_id='fixture' WHERE account_id='paper';
INSERT INTO market_bars VALUES('fractional-bar','SPY',transaction_timestamp()-interval '15 minutes',
 transaction_timestamp(),101.123456789012345678,101.123456789012345678,
 101.123456789012345678,101.123456789012345678,100,transaction_timestamp(),
 transaction_timestamp(),transaction_timestamp(),'VALID','PT15M',true,1);
INSERT INTO market_states VALUES('state-fractional','SPY','US_EQUITY','fractional-bar','evidence',
 'fractional-portfolio','position','{}',.01,transaction_timestamp(),transaction_timestamp(),
 transaction_timestamp(),transaction_timestamp(),'VALID','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',2);
"""


if __name__ == "__main__":
    import sys
    import traceback

    if sys.argv[1:] == ["guest"]:
        try:
            guest()
        except Exception as error:
            import re

            frame = traceback.extract_tb(error.__traceback__)[-1]
            root_error = getattr(error, "orig", error)
            diagnostic = getattr(getattr(root_error, "diag", None), "message_primary", "") or ""
            missing = re.search(r'(?:column|relation|table) "([a-z_]+)"', diagnostic)
            detail = ":" + missing[1].upper() if missing else ""
            print(
                f"APW02_FAIL:LIMIT_POLICY:{type(root_error).__name__}{detail}:"
                f"{Path(frame.filename).name}:{frame.lineno}"
            )
            raise SystemExit(1) from None
    else:
        from tests.integration.test_paper_provision import host

        print(host(Path(__file__), MARKER))
