"""Durable ledger, immutable references and separated runtime grants.

Revision ID: 0001_ledger
Revises:
"""

from alembic import op

from services.control_api.migrations.ledger_schema_v1 import TABLE_DDL, TABLE_NAMES

revision = "0001_ledger"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("SET LOCAL search_path = public, pg_catalog")
    for statement in TABLE_DDL:
        connection.exec_driver_sql(statement)

    # A migration owner is deliberately never made a member of an application role.
    op.execute("""
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atl_worker') THEN
        CREATE ROLE atl_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
      END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atl_execution') THEN
        CREATE ROLE atl_execution NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
      END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atl_control_api') THEN
        CREATE ROLE atl_control_api NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
      END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atl_watchdog') THEN
        CREATE ROLE atl_watchdog NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
      END IF;
      IF EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname IN ('atl_worker', 'atl_execution', 'atl_control_api', 'atl_watchdog')
          AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
               OR rolreplication OR rolbypassrls OR rolinherit)
      ) THEN
        RAISE EXCEPTION 'existing application group role has unsafe attributes';
      END IF;
    END $$;
    """)
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute(
        "GRANT USAGE ON SCHEMA public TO atl_worker, atl_execution, atl_control_api, atl_watchdog"
    )

    op.execute("""
    CREATE FUNCTION ledger_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'immutable ledger record';
    END $$;
    """)
    for table in (
        "audit_events",
        "instruments",
        "market_bars",
        "evidence_bundles",
        "market_states",
        "portfolio_snapshots",
        "risk_policies",
        "risk_decisions",
        "trade_proposals",
        "decisions",
        "execution_reports",
        "position_versions",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION ledger_reject_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION ledger_reject_mutation()"
        )

    # Audit events in the same transaction are mandatory for decision and ledger writes.
    op.execute("""
    CREATE FUNCTION public.ledger_require_audit() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
    DECLARE affected_id text;
    BEGIN
      IF TG_OP = 'DELETE' THEN affected_id := OLD.id;
      ELSE affected_id := NEW.id; END IF;
      IF NOT EXISTS (
        SELECT 1 FROM public.audit_events
        WHERE entity_id = affected_id AND transaction_id = txid_current()
      ) THEN
        RAISE EXCEPTION 'ledger mutation requires same-transaction audit';
      END IF;
      RETURN NULL;
    END $$;
    """)
    for table in (
        "risk_policies",
        "risk_decisions",
        "risk_reservations",
        "trade_proposals",
        "decisions",
        "order_intents",
        "broker_orders",
        "execution_reports",
        "positions",
        "portfolio_snapshots",
        "accounts",
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {table}_audit AFTER INSERT OR UPDATE OR DELETE "
            f"ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION ledger_require_audit()"
        )

    # Guard linkage fields even on mutable order and reservation rows.
    op.execute("""
    CREATE FUNCTION ledger_guard_references() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME = 'risk_reservations' THEN
        IF (NEW.proposal_id, NEW.risk_decision_id) IS DISTINCT FROM
           (OLD.proposal_id, OLD.risk_decision_id) THEN
          RAISE EXCEPTION 'reservation references are immutable';
        END IF;
      END IF;
      IF TG_TABLE_NAME = 'order_intents' THEN
        IF (NEW.proposal_id, NEW.risk_decision_id, NEW.client_order_id, NEW.instrument_id)
           IS DISTINCT FROM
           (OLD.proposal_id, OLD.risk_decision_id, OLD.client_order_id, OLD.instrument_id) THEN
          RAISE EXCEPTION 'intent references are immutable';
        END IF;
      END IF;
      IF TG_TABLE_NAME = 'broker_orders' THEN
        IF (NEW.intent_id, NEW.client_order_id, NEW.adapter)
           IS DISTINCT FROM (OLD.intent_id, OLD.client_order_id, OLD.adapter) THEN
          RAISE EXCEPTION 'order references are immutable';
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    """)
    for table in ("risk_reservations", "order_intents", "broker_orders"):
        op.execute(
            f"CREATE TRIGGER {table}_references BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION ledger_guard_references()"
        )

    op.execute("""
    CREATE FUNCTION public.ledger_snapshot_position() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $$
    BEGIN
      IF TG_RELID <> 'public.positions'::regclass THEN
        RAISE EXCEPTION 'invalid position snapshot trigger source';
      END IF;
      IF TG_OP = 'UPDATE' AND
         (NEW.instrument_id, NEW.currency, NEW.id) IS DISTINCT FROM
         (OLD.instrument_id, OLD.currency, OLD.id) THEN
        RAISE EXCEPTION 'position identity is immutable';
      END IF;
      IF TG_OP = 'UPDATE' AND NEW.ledger_version <= OLD.ledger_version THEN
        RAISE EXCEPTION 'position ledger version must increase';
      END IF;
      INSERT INTO public.position_versions (
        position_id, ledger_version, instrument_id, currency,
        quantity, average_price, realized_pnl
      ) VALUES (
        NEW.id, NEW.ledger_version, NEW.instrument_id, NEW.currency,
        NEW.quantity, NEW.average_price, NEW.realized_pnl
      );
      RETURN NEW;
    END $$;
    """)
    op.execute(
        "CREATE TRIGGER positions_snapshot AFTER INSERT OR UPDATE ON positions "
        "FOR EACH ROW EXECUTE FUNCTION ledger_snapshot_position()"
    )
    op.execute("REVOKE ALL ON FUNCTION public.ledger_snapshot_position() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.ledger_require_audit() FROM PUBLIC")

    op.execute(
        "GRANT SELECT ON instruments, market_bars, evidence_bundles, market_states, "
        "position_versions, positions, portfolio_snapshots, decisions, trade_proposals, "
        "agent_runs TO atl_worker"
    )
    op.execute(
        "GRANT SELECT ON instruments, market_bars, evidence_bundles, market_states, "
        "position_versions, positions, portfolio_snapshots, decisions, trade_proposals, "
        "risk_policies, risk_decisions, risk_reservations, order_intents, broker_orders, "
        "execution_reports, accounts, audit_events TO atl_execution"
    )
    op.execute(f"GRANT SELECT ON {', '.join(TABLE_NAMES)} TO atl_control_api")
    op.execute(
        "GRANT SELECT ON accounts, broker_orders, risk_reservations, audit_events TO atl_watchdog"
    )
    op.execute(
        "GRANT INSERT ON audit_events TO atl_worker, atl_execution, atl_control_api, atl_watchdog"
    )
    op.execute("GRANT INSERT, UPDATE ON agent_runs TO atl_worker")
    op.execute(
        "GRANT INSERT ON trade_proposals, decisions, market_bars, "
        "evidence_bundles, market_states, instruments TO atl_worker"
    )
    op.execute(
        "GRANT INSERT ON risk_policies, risk_decisions, risk_reservations, "
        "order_intents, broker_orders, execution_reports, accounts, positions, "
        "portfolio_snapshots TO atl_execution"
    )
    op.execute(
        "GRANT UPDATE ON risk_reservations, broker_orders, positions, accounts TO atl_execution"
    )
    # Control and watchdog commands are introduced with their own PTV tasks; no
    # table write is granted prematurely. Both can append audit evidence now.


def downgrade() -> None:
    raise RuntimeError(
        "ledger downgrade would destroy durable state; restore from a reviewed backup"
    )
