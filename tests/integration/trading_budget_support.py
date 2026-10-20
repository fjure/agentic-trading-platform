"""Synthetic fixture extension; no provider/real data and no normal database writes."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal


def checks(admin, execution, engine, now):
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.exc import DBAPIError

    from packages.domain import models as m
    from packages.persistence import PostgresLedger
    from packages.risk.budget import read_cap, read_strategy
    from packages.risk.engine import RiskLimits
    from packages.risk.reservations import reserve
    from services.control_api.auth import AuthDenied
    from services.control_api.trading_profile import TradingProfileBackend
    from services.execution.gates import SubmitDenied, evaluate_submit_gate

    control_engine = create_engine(engine.url, hide_parameters=True)

    @event.listens_for(control_engine, "connect")
    def role(connection, _record):
        connection.execute("SET ROLE atl_control_api")
        connection.commit()

    control = PostgresLedger(engine=control_engine)
    profile = TradingProfileBackend(control, "paper")
    trace = "b" * 32
    scenario = sys.argv[2]

    def save(amount, key="budget-save-key-0001", strategy="mock-neutral"):
        return profile.update(
            {"budget": amount, "currency": "USD", "strategy": strategy}, "local-test", key, trace
        )

    def candidate(index):
        return reserve(
            ledger=execution, account_id="paper", proposal_id=f"p-{index}", limits=RiskLimits()
        )

    def denied(amount, key):
        try:
            save(amount, key)
        except AuthDenied as error:
            assert error.code == "RISK_LIMIT_EXCEEDED" and error.status == 409
        else:
            raise AssertionError("unsafe cap update accepted")

    def audit(tx, entity, version, label):
        tx.insert(m.AuditEvent(label, now, "fixture", "fixture", ("TEST",), entity, version, trace))

    if scenario == "legacy":
        assert profile.read() == {"budget": "100", "currency": "USD", "strategy": "mock-neutral"}
        with execution.transaction() as tx:
            assert read_cap(tx.connection, "paper") is None
            assert read_strategy(tx.connection, "paper") == ("mock-neutral", "mock-neutral.v1")
        # >100 USD including buffer proves display default isn't installed authority.
        assert candidate(0).approved
    elif scenario == "profile":
        before = {}
        with engine.connect() as c:
            for table in ("accounts", "portfolio_snapshots", "risk_account_context"):
                before[table] = c.execute(
                    text(f"SELECT md5(jsonb_agg(to_jsonb(t))::text) FROM public.{table} t")
                ).scalar_one()
        result = save("100.01", strategy="neutral-baseline")
        assert result == {"budget": "100.01", "currency": "USD", "strategy": "neutral-baseline"}
        assert profile.read() == result
        assert save("100.0100", strategy="neutral-baseline") == result
        # Actual authenticated transport must keep profile and audit readable.
        # This code belongs only to the isolated test authority, never the user lab.
        from fastapi.testclient import TestClient

        from services.control_api.app import create_app
        from services.control_api.auth import LocalAuth
        from services.control_api.snapshot import SnapshotReader

        origin = "http://127.0.0.1:8000"
        auth = LocalAuth()
        with TestClient(
            create_app(
                auth=auth, trading_profile=profile, supervisor=SnapshotReader(control, "paper")
            ),
            base_url=origin,
            client=("127.0.0.1", 46310),
        ) as http:
            reply = http.post(
                "/api/v1/session",
                json={"activationCode": auth.issue_activation_code()},
                headers={"Origin": origin, "Idempotency-Key": "profile-login-0001"},
            )
            assert reply.status_code == 204
            reply = http.get("/api/v1/trading-profile")
            assert reply.status_code == 200 and reply.json() == result
            reply = http.get("/api/v1/audit", params={"traceId": trace})
            assert reply.status_code == 200
            events = reply.json()["items"]
            assert len(events) == 1
            assert events[0]["action"] == "control.trading_profile_updated"
            assert events[0]["outcome"] == "ACCEPTED" and events[0]["reasonCode"] == "OK"
        try:
            save("101")
        except AuthDenied as error:
            assert error.code == "IDEMPOTENCY_CONFLICT"
        else:
            raise AssertionError("idempotency conflict ignored")
        with engine.connect() as c:
            assert (
                c.execute(
                    text(
                        "SELECT has_table_privilege('atl_control_api',"
                        "'public.trading_profiles','UPDATE')"
                    )
                ).scalar_one()
                is False
            )
            assert (
                c.execute(
                    text(
                        "SELECT has_table_privilege('atl_control_api',"
                        "'public.trading_profiles','INSERT')"
                    )
                ).scalar_one()
                is False
            )
            assert (
                c.execute(
                    text(
                        "SELECT has_function_privilege('atl_worker',"
                        "'public.control_update_trading_profile(varchar,varchar,"
                        "varchar,varchar,numeric,varchar)','EXECUTE')"
                    )
                ).scalar_one()
                is False
            )
            assert c.execute(text("SELECT version FROM public.trading_profiles")).scalar_one() == 1
            assert (
                c.execute(
                    text(
                        "SELECT count(*) FROM public.audit_events "
                        "WHERE action='control.trading_profile_updated'"
                    )
                ).scalar_one()
                == 1
            )
            assert (
                c.execute(text("SELECT count(*) FROM public.trading_profile_receipts")).scalar_one()
                == 1
            )
            for table, fingerprint in before.items():
                assert (
                    c.execute(
                        text(f"SELECT md5(jsonb_agg(to_jsonb(t))::text) FROM public.{table} t")
                    ).scalar_one()
                    == fingerprint
                )
        with execution.transaction() as tx:
            assert read_strategy(tx.connection, "paper") == (
                "neutral-baseline",
                "neutral-baseline.v1",
            )
        for statement in (
            "UPDATE public.trading_profiles SET budget=1",
            "INSERT INTO public.trading_profiles(account_id) VALUES('invalid')",
        ):
            try:
                with control.transaction() as tx:
                    tx.connection.execute(text(statement))
            except DBAPIError:
                pass
            else:
                raise AssertionError("Control role has unrestricted profile writes")
    elif scenario == "reserve-race":
        save("150")
        with ThreadPoolExecutor(max_workers=2) as pool:
            decisions = list(pool.map(candidate, (0, 1)))
        assert sum(decision.approved for decision in decisions) == 1
        rejected = next(decision for decision in decisions if not decision.approved)
        assert "CASH_LIMIT" in rejected.reason_codes
        with engine.connect() as c:
            assert c.execute(
                text("SELECT sum(reserved_notional)*1.01 FROM public.risk_reservations")
            ).scalar_one() <= Decimal("150")
    elif scenario == "update-exposure":
        save("1000")
        assert candidate(0).approved
        denied("100", "budget-save-key-0002")
        assert profile.read()["budget"] == "1000"
        assert save("101.0101", "budget-save-key-0003")["budget"] == "101.0101"
        # Actual held exposure is included independently from the open buy.
        with admin.transaction() as tx:
            tx.lock_account("paper")
            position = tx.get(m.Position, "pos2")
            audit(tx, "pos2", 2, "position-held-for-budget")
            tx.replace(
                replace(
                    position,
                    quantity=Decimal("0.5"),
                    average_price=Decimal("100"),
                    ledger_version=2,
                )
            )
        denied("150", "budget-save-key-0004")
    elif scenario == "unknown":
        save("1000")
        risk = candidate(0)
        assert risk.approved
        with execution.transaction() as tx:
            tx.lock_account("paper")
            reservation = tx.get(m.RiskReservation, risk.reservation_id)
            audit(tx, reservation.id, 2, "unknown-reservation-for-budget")
            tx.replace(replace(reservation, state=m.ReservationState.HELD_UNKNOWN, version=2))
        denied("1000000", "budget-save-key-0002")
        assert profile.read()["budget"] == "1000"
    elif scenario == "submit-recheck":
        save("150")
        risk = candidate(0)
        assert risk.approved
        with execution.transaction() as tx:
            intent = tx.connection.execute(
                text("SELECT id FROM public.order_intents WHERE proposal_id='p-0'")
            ).scalar_one()
            evaluate_submit_gate(
                tx=tx,
                account_id="paper",
                intent_id=intent,
                limits=RiskLimits(),
                owner_id="fixture",
                fencing_token=1,
            )
        # Additional held exposure appears after reservation; same fresh submit
        # gate must deny even though the original decision remains approved.
        with admin.transaction() as tx:
            tx.lock_account("paper")
            position = tx.get(m.Position, "pos2")
            audit(tx, "pos2", 2, "submit-held-for-budget")
            tx.replace(
                replace(
                    position,
                    quantity=Decimal("0.5"),
                    average_price=Decimal("100"),
                    ledger_version=2,
                )
            )
        try:
            with execution.transaction() as tx:
                evaluate_submit_gate(
                    tx=tx,
                    account_id="paper",
                    intent_id=intent,
                    limits=RiskLimits(),
                    owner_id="fixture",
                    fencing_token=1,
                )
        except SubmitDenied as error:
            assert error.reason_code == "CASH_LIMIT"
        else:
            raise AssertionError("Submit ignored fresh cap exposure")
        with engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM public.broker_orders")).scalar_one() == 0
    else:
        raise AssertionError("unknown synthetic scenario")
    control_engine.dispose()
    print("PASS: isolated exact USD profile/cap scenario " + scenario)


if __name__ == "__main__":
    from risk_support import guest

    guest(checks, uppercase_instruments=True)
