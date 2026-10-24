"""Actual isolated Paper roles, synthetic quote proofs and immutable LIMIT policy."""

from pathlib import Path

MARKER = "PASS: fixed Paper LIMIT policy, quotes, planner inputs and exact privileges"


def test_paper_limit_policy_postgres():
    from tests.integration.test_paper_provision import host

    assert (
        host(
            Path(__file__),
            MARKER,
            support_paths=(Path(__file__).with_name("test_paper_lifecycle.py"),),
        )
        == MARKER
    )


def guest():
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import patch

    import psycopg
    from sqlalchemy import text
    from test_paper_lifecycle import PaperFixture

    from packages.domain.models import MarketState, TradeProposal
    from packages.persistence.ledger import LedgerUnit, PostgresLedger
    from packages.risk.paper_limits import paper_proposal_reasons
    from services.runtime.provision_paper import _verify_dormant_privileges
    from services.runtime.worker import WorkerRuntime
    from services.worker.paper_planning import read_planning_inputs, read_registered_plan

    f = PaperFixture()
    f.prepare()
    f.profile()
    f.refresh()
    assert not f.sql("worker", "SELECT paper_planning_active()")
    _, _, _, activation = f.start()
    assert "activationId" in activation
    with psycopg.connect(dbname=f.database, **f.common) as c:
        _verify_dormant_privileges(c)
        assert c.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0020_paper_read_models",
        )
        for name in ("paper_limit_policies", "paper_quote_observations"):
            for mutation in (
                f"UPDATE {name} SET created_at=clock_timestamp()",
                f"DELETE FROM {name}",
                f"TRUNCATE {name}",
            ):
                with c.transaction(force_rollback=True):
                    try:
                        c.execute(mutation)
                    except psycopg.errors.RaiseException:
                        pass
                    else:
                        raise AssertionError("immutable policy or quote changed")
        c.commit()
    # Worker may read public models, but cannot read private provenance or write finance.
    for statement in (
        "SELECT * FROM broker_binding_proofs",
        "SELECT * FROM broker_account_bindings",
        "SELECT * FROM paper_quote_observations",
        "SELECT * FROM paper_limit_plans",
        "UPDATE positions SET quantity=0",
        "CREATE TABLE forbidden(id int)",
        "CREATE TEMP TABLE forbidden(id int)",
    ):
        with f.engines["worker"].begin() as c:
            try:
                c.execute(text(statement))
            except psycopg.errors.InsufficientPrivilege:
                pass
            except Exception as error:
                if not isinstance(
                    getattr(error, "orig", None), psycopg.errors.InsufficientPrivilege
                ):
                    raise
            else:
                raise AssertionError("worker escaped bounded privileges")
    import json

    quote = f.sql("execution", "SELECT paper_planner_inputs('SPY-state')")
    assert quote["bid"] == "14.99" and quote["ask"] == "15"
    assert quote["held_exposure"] == quote["instrument_exposure"] == "0"
    assert quote["held_quantity"] == "0" and quote["remaining_cash"] == "100000"
    assert quote["risk_budget"] == "1000" and quote["budget_version"] == 1
    assert quote["capability"]["fractional"] == "CONFIRMED"
    assert f.account not in json.dumps(quote)
    assert "22222222-2222-4222-8222-222222222222" not in json.dumps(quote)
    original_quote = dict(
        sourceId=quote["quote_source_id"],
        bid=quote["bid"],
        ask=quote["ask"],
        providerTime=quote["quote_provider_time"],
        observedAt=quote["quote_observed_at"],
        sourceCapabilityVersion=1,
    )

    def append(value=original_quote, owner=f.owner, fence=f.fence, context=1):
        return f.sql(
            "execution",
            "SELECT paper_quote_append('SPY-state',:context,:owner,:fence,CAST(:body AS jsonb))",
            context=context,
            owner=owner,
            fence=fence,
            body=json.dumps(value),
        )

    expected = dict(quoteId=quote["quote_evidence_id"], quoteHash=quote["quote_evidence_hash"])
    assert append() == expected
    for owner, fence in (
        (None, f.fence),
        (f.owner, None),
        ("other", f.fence),
        (f.owner, f.fence + 1),
    ):
        assert append(owner=owner, fence=fence) == {"error": "VERSION_CONFLICT"}
    for mutation in (
        {"secret": "synthetic-canary"},
        {"bid": 14},
        {"bid": "NaN"},
        {"sourceCapabilityVersion": None},
        {"sourceCapabilityVersion": 2},
        {"sourceId": "alpaca-market-data:sip"},
        {"ask": "14.98"},
    ):
        assert "error" in append(original_quote | mutation)
    from datetime import timedelta

    assert "error" in append(
        original_quote
        | {
            "providerTime": (f.clock.now() - timedelta(seconds=31))
            .isoformat()
            .replace("+00:00", "Z")
        }
    )
    assert append(original_quote | {"bid": "15.99", "ask": "16"}) == {"error": "VERSION_CONFLICT"}
    runtime = WorkerRuntime(
        SimpleNamespace(
            deployment="paper", account_id="paper", instruments=("SPY",), decision_provider="mock"
        ),
        PostgresLedger(engine=f.engines["worker"]),
    )
    with patch("services.worker.paper_limit._EASTERN", f.zone):
        assert runtime.tick() == "READY"
    with f.engines["execution"].begin() as c:
        tx = LedgerUnit(c)
        proposal_id = c.execute(text("SELECT id FROM trade_proposals")).scalar_one()
        proposal, state = tx.get(TradeProposal, proposal_id), tx.get(MarketState, "SPY-state")
        original = read_registered_plan(c, proposal)
        assert original is not None
        assert original.quote_evidence_hash == expected["quoteHash"]
        assert read_planning_inputs(c, state.id, execution=True) is not None
        now = c.execute(text("SELECT clock_timestamp()")).scalar_one()
        with patch("services.worker.paper_limit._EASTERN", f.zone):
            assert paper_proposal_reasons(c, tx, proposal, state, now) == ()
            assert paper_proposal_reasons(
                c, tx, replace(proposal, quantity=proposal.quantity + 1), state, now
            )
            assert paper_proposal_reasons(c, tx, replace(proposal, rationale_codes=()), state, now)
        for name in ("order_intents", "broker_orders", "risk_reservations", "risk_decisions"):
            assert c.execute(text(f"SELECT count(*) FROM {name}")).scalar_one() == 0
        assert f.sql("worker", "SELECT count(*) FROM jobs WHERE status='DONE'") == 1
        assert c.execute(text("SELECT mode,safety_state FROM risk_account_context")).one() == (
            "PAPER",
            "RUNNING",
        )
    assert (
        f.sql(
            "worker",
            "SELECT paper_planner_record(:proposal,:hash,'decision:SPY-state','wrong',1)",
            proposal=proposal_id,
            hash=original.digest(),
        )
        is False
    )
    with psycopg.connect(dbname=f.database, **f.common) as c:
        for mutation in (
            "UPDATE risk_account_context SET "
            "reconciliation_current=false,version=version+1,actor_id='fixture'",
            "UPDATE paper_workflow_state SET pending_generation=2",
        ):
            with c.transaction(force_rollback=True):
                c.execute(
                    "INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,"
                    "entity_id,entity_version,trace_id) SELECT gen_random_uuid()::text,"
                    "clock_timestamp(),'fixture','fixture','[\"SYNTHETIC_FIXTURE\"]','paper',"
                    "version+1,trace_id FROM risk_account_context WHERE account_id='paper'"
                )
                c.execute(mutation)
                c.execute("SET SESSION AUTHORIZATION atl_paper_worker_login")
                c.execute("SET ROLE atl_paper_worker")
                assert c.execute("SELECT paper_planner_inputs('SPY-state')").fetchone() == (None,)
            c.execute("SET SESSION AUTHORIZATION DEFAULT")
            c.execute("RESET ROLE")
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
                "clock_timestamp(),'fixture','risk-2-audit',simulation FROM "
                "risk_policies WHERE version=1"
            )
            c.execute("SET SESSION AUTHORIZATION atl_paper_worker_login")
            c.execute("SET ROLE atl_paper_worker")
            tighter = c.execute("SELECT paper_planner_inputs('SPY-state')").fetchone()[0]
            assert tighter["policy"]["max_position_fraction"] == "0.05"
            assert tighter["policy_version"] == 2
            assert (
                c.execute("SELECT paper_limit_policy()").fetchone()[0]["max_position_fraction"]
                == "0.10"
            )
            assert c.execute("SELECT paper_planning_active()").fetchone() == (False,)
        c.execute("SET SESSION AUTHORIZATION DEFAULT")
        c.execute("RESET ROLE")
        for statement in (
            "UPDATE paper_limit_plans SET input_hash=repeat('a',64)",
            "DELETE FROM paper_limit_plans",
            "TRUNCATE paper_limit_plans",
        ):
            with c.transaction(force_rollback=True):
                try:
                    c.execute(statement)
                except psycopg.errors.RaiseException:
                    pass
                else:
                    raise AssertionError("registered original proof changed")
        # Latest unquoted input revokes readiness; worker must not create a legacy MARKET fallback.
        c.execute(
            "INSERT INTO market_states SELECT 'state-missing-quote',instrument_id,market,"
            "bar_id,evidence_id,portfolio_id,position_id,features,spread,event_time,received_at,"
            "available_at,clock_timestamp(),quality,trace_id,position_ledger_version "
            "FROM market_states WHERE id='SPY-state'"
        )
        c.commit()
        assert not f.sql("worker", "SELECT paper_planning_active()")
        assert runtime.tick() == "READY"
        assert c.execute("SELECT count(*) FROM decisions").fetchone() == (1,)
        assert c.execute("SELECT count(*) FROM trade_proposals").fetchone() == (1,)
        with c.transaction(force_rollback=True):
            c.execute(_FRACTIONAL)
            c.execute("SET SESSION AUTHORIZATION atl_paper_execution_login")
            c.execute("SET ROLE atl_paper_execution")
            value = original_quote | {"bid": "101.11", "ask": "101.12"}
            assert set(
                c.execute(
                    "SELECT paper_quote_append('state-fractional',1,%s,%s,%s::jsonb)",
                    (f.owner, f.fence, json.dumps(value)),
                ).fetchone()[0]
            ) == {"quoteId", "quoteHash"}
            raw = c.execute("SELECT paper_planner_inputs('state-fractional')").fetchone()[0]
            assert raw["instrument_exposure"] == raw["held_exposure"] == "12.484377267751714679"
            from services.worker.paper_planning import decode_planning_inputs

            assert decode_planning_inputs(raw, "state-fractional") is not None
        c.execute("SET SESSION AUTHORIZATION DEFAULT")
        c.execute("RESET ROLE")
        _verify_dormant_privileges(c)
    print(MARKER)


_FRACTIONAL = """
INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
 entity_version,trace_id)
 SELECT 'fraction-audit-'||entity,transaction_timestamp(),'fixture','fixture',
 '["SYNTHETIC_FIXTURE"]',entity,2,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
 FROM unnest(ARRAY['paper','paper-position-SPY','fractional-portfolio']) entity;
INSERT INTO
 audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,entity_version,trace_id)
 SELECT
 'fraction-context-audit',transaction_timestamp(),'fixture','fixture','["SYNTHETIC_FIXTURE"]',
 'paper',version+1,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' FROM risk_account_context WHERE
 account_id='paper';
UPDATE positions SET quantity=.123456789,average_price=101.12,ledger_version=2
 WHERE id='paper-position-SPY';
UPDATE accounts SET ledger_version=2 WHERE id='paper';
INSERT INTO portfolio_snapshots VALUES('fractional-portfolio','USD',
 100012.484377267751714679,100000,12.484377267751714679,
 transaction_timestamp(),2,'VALID','CURRENT');
UPDATE risk_account_context SET version=version+1,portfolio_snapshot_id='fractional-portfolio',
 portfolio_version=2,actor_id='fixture' WHERE account_id='paper';
INSERT INTO market_bars VALUES('fractional-bar','SPY',transaction_timestamp()-interval '15 minutes',
 transaction_timestamp(),101.123456789012345678,101.123456789012345678,
 101.123456789012345678,101.123456789012345678,100,transaction_timestamp(),
 transaction_timestamp(),transaction_timestamp(),'VALID','PT15M',true,1);
INSERT INTO market_states
 VALUES('state-fractional','SPY','US_EQUITY','fractional-bar','SPY-evidence',
 'fractional-portfolio','paper-position-SPY','{}',.01,transaction_timestamp(),transaction_timestamp(),
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
            missing = re.search(
                r'(?:column|relation|table|function|schema) "?([a-z_]+)', diagnostic
            )
            detail = ":" + missing[1].upper() if missing else ""
            print(
                f"APW02_FAIL:LIMIT_POLICY:{type(root_error).__name__}{detail}:"
                f"{Path(frame.filename).name}:{frame.lineno}"
            )
            raise SystemExit(1) from None
    else:
        from tests.integration.test_paper_provision import host

        print(
            host(
                Path(__file__),
                MARKER,
                support_paths=(Path(__file__).with_name("test_paper_lifecycle.py"),),
            )
        )
